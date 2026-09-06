# 通过 RAG 与向量数据库学习 AI Agent

## 课程草案：从 Embedding 到生产级检索

本课程使用两个真实服务作为学习实验室：

- `fastestai-api`：基于 FastAPI 的 AI 服务，包含通用 Qdrant 检索器、私有知识搜索、Agent Memory，以及外部 Saky RAG 代理。
- `omnimcp-be`：MCP 后端，负责生成可搜索的工具知识、将工具索引到 Qdrant，并为 AI Agent 检索候选工具。

课程目标不是记住某个厂商的 API，而是理解从用户问题到检索上下文，再到 Agent 决策的完整链路。

完成基础检索链路后，继续阅读 [`RAG-performance.md`](RAG-performance.md)，学习企业级 RAG 的全链路耗时预算、六层优化、缓存和预打分快速通道。

## 1. 学习目标

完成课程后，你应该能够：

1. 解释为什么即使 LLM 具备很强的通用知识，仍然需要检索系统。
2. 将文本转换为 Embedding，并解释向量相似度的含义。
3. 将 Qdrant 中的一个 Point 理解为 ID、向量和 Payload 的组合。
4. 构建包含切分、元数据、Embedding 和 Upsert 的数据入库流程。
5. 构建包含查询向量、过滤器、分数阈值和 Top-K 的检索流程。
6. 区分知识 RAG、Agent Memory 和工具检索。
7. 追踪请求在 `fastestai-api` 和 `omnimcp-be` 中的实际执行路径。
8. 诊断空结果、切分不合理、Embedding 不匹配、索引过期和向量缺失等问题。
9. 为检索质量和系统运行一致性设计评估实验。

### 可提升点

为每个学习目标增加一个可验证的产出，例如调用图、测试报告或 Recall@K 数据。这样可以把“看懂概念”升级为“证明自己能运行和排查”。

## 2. 核心心智模型

LLM 负责生成文本。它不会自动搜索你的私有数据库，不会自动读取今天新增的文档，也不知道你的应用刚刚安装了哪些工具。

检索增强生成，即 Retrieval-Augmented Generation，简称 RAG，在生成之前增加了一个检索步骤：

```text
源数据
    -> 规范化和切分
    -> 创建 Embedding
    -> 保存向量和元数据

用户问题
    -> 创建查询 Embedding
    -> 搜索最近邻向量
    -> 应用元数据过滤和分数过滤
    -> 组装上下文
    -> 要求 LLM 基于上下文生成回答
```

也可以把这个过程拆成更适合调试的 ASCII 链路：

```text
+-------------+    +-------------+    +-------------+    +-------------+
| 用户问题    | -> | 查询处理    | -> | 向量检索    | -> | 过滤/排序   |
+-------------+    +-------------+    +-------------+    +-------------+
                                                               |
                                                               v
+-------------+    +-------------+    +-------------+    +-------------+
| 最终回答    | <- | LLM 生成    | <- | Prompt 组装  | <- | 上下文拼接  |
+-------------+    +-------------+    +-------------+    +-------------+
```

最重要的设计原则是：

> 生成回答的质量受检索上下文的质量和相关性限制。

LLM 可以基于不相关的上下文写出流畅的回答，但这仍然是一次检索失败。

### RAG 不等于模型微调

微调通过训练样本改变模型的行为或表达风格。RAG 通常不改变模型本身，而是在请求时提供相关信息。

当问题包含以下特征时，通常应该先考虑 RAG：

- 企业内部或组织专属信息；
- 经常变化的信息；
- 需要来源、证据和可检查上下文；
- 文档规模较大，无法在每次请求中全部放入 Prompt。

### 可提升点

在基础向量检索前增加 Query Rewrite、关键词检索或 HyDE，并通过实验比较增强前后的 Recall@K，而不是只凭回答是否流畅来判断效果。

## 3. Embedding 与相似度

Embedding 模型把文本映射为固定长度的数字向量：

```text
"如何创建一个 Google Doc？"
    -> [0.012, -0.224, ..., 0.091]
```

语义相关的文本通常会在向量空间中更接近。向量数据库通过索引高效地搜索附近的向量。

### Embedding 契约

每个被索引的向量都遵守一个隐含契约：

| 属性 | 必须兼容的内容 |
|---|---|
| 模型 | 文档和查询使用的模型应该兼容 |
| 维度 | 一个 Collection 中的向量必须符合配置的维度 |
| 距离函数 | Cosine、Dot Product 和 Euclidean 会影响分数含义 |
| 向量名称 | Named Vector Collection 必须使用正确的向量名称 |
| 文本表示 | 建立索引时使用的文本应匹配实际检索意图 |

在 `omnimcp-be` 中，工具向量使用 Named Vector `openai:text-embedding-ada-002`，维度为 `1536`。索引代码向 OpenAI Embedding 客户端传入未加前缀的模型名 `text-embedding-ada-002`，然后用 Named Vector 名称保存结果。相关代码位于 `src/omnimcp_be/mcp/tool/tool_index.py`（L120:L365）。

在 `fastestai-api` 中，`OpenAIEmbedding` 提供 `embed_text` 和 `embed_texts`，`QdrantRetriever` 在单条和批量检索中都使用这两个方法。相关代码位于 `src/fastestai/core/embeddings/openai.py`（L8:L54）和 `src/fastestai/core/qdrant/__init__.py`（L76:L412）。

### 相似度分数不是通用概率

只有在距离函数、Embedding 模型、Collection 和数据分布都相同的情况下，分数阈值才具有可比性。不要把 `0.8` 当作通用的相关性标准。应该使用带标签的查询样本来调节阈值。

### 可提升点

建立一组固定的“问题 -> 相关文档”评估集，同时测试多个 Embedding 模型、距离函数和阈值，记录 Recall@K、延迟和成本。

## 4. 使用 Qdrant 作为向量数据库

可以把 Qdrant 中的一个 Point 抽象为：

```json
{
  "id": "point-id",
  "vector": {
    "openai:text-embedding-ada-002": [0.01, -0.22, 0.09]
  },
  "payload": {
    "id": "chunk-id",
    "parent_id": "document-id",
    "gpt_id": "assistant-or-tenant-id",
    "content": "可用于搜索的文本块",
    "headline": "文档标题",
    "created_at": 1720000000
  }
}
```

向量用于语义搜索，Payload 用于过滤、展示、分组、权限控制，以及重新组装原始文档。

### 搜索通常需要同时使用向量和 Payload

语义相似度回答的是："哪些内容在概念上与这个问题相关？"

Payload 过滤回答的是："哪些租户、用户、GPT、网站、时间范围或服务器的数据可以返回？"

生产环境的查询通常会把两者结合起来。在 `fastestai-api` 中，`QdrantRetriever._create_filters` 支持 Match、Text Match、Any-Value Match、Range、Null Check 和 Empty Check。它会先区分 `must`、`must_not` 和 `should` 条件，再创建 Qdrant Filter。

### 可提升点

为常用 Payload 字段建立索引，并分别记录向量检索、过滤和全文重建的延迟。不要只优化向量搜索，却忽略过滤和上下文重建造成的耗时。

## 5. 数据入库：建立向量索引

入库流程负责把源文档转换为可搜索的 Point。

### 第一步：规范化源数据

提取文本，同时保留后续会用到的元数据：

- 稳定的文档 ID；
- 父文档 ID；
- 标题或 Headline；
- 来源 URL 或文件名；
- 租户、用户、GPT 或服务器 ID；
- 创建时间和更新时间；
- 权限和可见性；
- 数据源类型和版本。

不要因为某个元数据没有参与 Embedding 就直接丢弃它。后续的过滤和权限校验可能仍然依赖这些字段。

### 第二步：切分文本

Chunking 是信息检索设计，而不只是字符串长度处理。一个好的文本块应该包含足够的信息来回答一个小问题，同时足够小，不会因为加入过多无关内容而稀释匹配结果。

需要考虑的决策包括：

- 最大字符数或 Token 数；
- 相邻文本块之间的重叠；
- 是否保留标题、列表、表格和代码块的结构；
- 文本块脱离父标题后是否仍然能够独立理解；
- 是否为每个文本块生成稳定的 `split_id`，便于更新和排查。

私有知识 API 暴露了 `split_limit`，当前默认值是 `500`。这是当前私有数据实现的接口行为，不应被当作通用的 Chunking 推荐值。

### 第三步：创建 Embedding

批量生成 Embedding 通常比每个文本块单独请求更高效。应该根据吞吐量、限流、内存和成本控制批次大小与并发数。

`fastestai-api` 将批量索引交给 `vecutils.qdrant.batch_index_vectors`。它的 Retriever 暴露了 `embedding_batch_size` 和 `embedding_concurrency`，明确体现了这个工程权衡。

`omnimcp-be` 使用基于输入文本 Hash 的 Embedding 缓存。缓存有大小上限，超过上限后会删除较早的条目。

### 第四步：Upsert Point

Upsert 应该具备幂等性。对相同源数据重复建立索引时，不应该无限创建重复 Point。

`QdrantRetriever.add_documents` 可以先收集已存在的 Point ID，只为尚不存在的文档创建索引。MCP 工具索引器构造 `PointStruct`，然后调用 Qdrant 的 `upsert`，并设置 `wait=True`。

### 第五步：重新索引和删除过期内容

当源数据发生变化时，系统需要明确的更新策略：

- 使用稳定 Point ID 更新；
- 删除一个父文档的全部文本块后重新创建；
- 为源数据增加版本字段，只检索当前有效版本；
- 对大规模迁移重建整个 Collection。

`omnimcp-be` 中记录了一个重新索引路径：根据 `website_profile` 和 `gpt_id` 过滤并删除现有 Qdrant 文档，然后重新生成 Embedding。

完整入库流程可以用 ASCII 图表示：

```text
+----------+    +----------+    +----------+    +----------+
| 源文档    | -> | 规范化   | -> | 切分     | -> | Embedding|
+----------+    +----------+    +----------+    +----------+
                                                       |
                                                       v
+----------+    +----------+    +----------+    +----------+
| 可检索库  | <- | Upsert   | <- | Point    | <- | 元数据   |
+----------+    +----------+    +----------+    +----------+
```

### 可提升点

把索引过程设计成可恢复的增量任务：记录源版本、处理状态、失败原因和重试次数，并支持失败后从上一个 checkpoint 继续。

## 6. 检索：找到正确的上下文

`fastestai-api` 中可复用的检索流程如下：

```text
查询文本
    -> OpenAIEmbedding.embed_text(query)
    -> 可选的 Named Vector
    -> Qdrant Search
    -> Payload Filter
    -> Limit 和 Score Threshold
    -> Typed 或 Untyped Search Result
```

实现位于 `QdrantRetriever.retrieve_documents`。它请求 Payload，并将 Qdrant 的 Scored Point 转换为通用字典或经过校验的 Pydantic 文档模型。

### 私有知识检索

接口 `/v1/gpt/private/search` 接收如下请求：

```json
{
  "gpt_id": "assistant-id",
  "query": "如何创建一个文档？",
  "limit": 10,
  "score_threshold": 0.8,
  "deduplicate": true,
  "with_vector": false
}
```

请求会查询 `private_knowledge` Collection。实现会在搜索前加入 `gpt_id` Match Filter，以及可选的时间范围、网站和自定义 Qdrant Filter。

API 也支持 `/v1/gpt/private/search_batch`。它会为多个查询创建 Embedding，为每个查询构造一个 Qdrant `SearchRequest`，然后调用 Qdrant 的批量搜索。返回结果与输入查询保持相同顺序。

### 文本块结果与全文结果

私有知识实现会根据 `parent_id` 对命中的文本块进行分组，然后选择命中次数最多的父文档，同时保留分数和可选的向量。

当前有两种有用的输出模式：

- `Chunk`：返回命中的文本块。
- `FullText`：对每个选中的父文档使用 `scroll`，加载该文档的所有文本块。

这种分组方式是一个实用的折中：检索先找到相关文本块，但生成回答可能需要同一文档的周边内容。同时，它也带来了新的评估问题：排名应该使用最佳文本块分数、平均文本块分数，还是匹配文本块的数量？

### 外部 RAG 作为服务边界

`fastestai-api` 还在 `/v1/knowledge/saky` 下提供 Saky RAG 代理。这是一个很有价值的架构模式：索引和检索可以由独立服务负责，而业务 API 只负责代理、校验和统一错误处理。

这个代理把职责分成三类：

- `GET /health`：检查外部服务和索引状态；
- `POST /retrieve`：返回包含 `chunk_id`、`source_file`、`heading_path`、文本和分数的结构化命中结果；
- `POST /query` 或 `POST /query/stream`：要求外部服务生成回答，并可选择使用缓存。

对于教学和排查问题，`retrieve` 尤其重要，因为它允许在生成之前检查证据。`query` 对调用方更方便，但会隐藏更多检索和 Prompt 组装细节。代理还会把上游失败和超时转换为明确的 HTTP 错误或 SSE 错误事件。

### 去重行为

当前私有数据格式化逻辑可以基于 Headline 去重。这是针对当前数据质量问题的实用修复，但不是通用的身份判断规则。两个文档可能合法地共享同一个标题。生产系统应该优先使用稳定的源 ID 或父文档 ID 去重。

### 可提升点

增加 Reranker 或混合检索，把向量分数、关键词分数、时间新鲜度和业务优先级合并排序，再决定哪些上下文进入 Prompt。

## 7. Agent Memory

Agent Memory 已单独整理为一篇教学文档，覆盖记忆分层、外置 Memory 框架、写入内容、过期与冲突处理，以及 Hermes Agent、OpenClaw、Codex 和 Claude Code 的案例：

请阅读 [`agent-memory-course.md`](agent-memory-course.md)。

## 8. 工具检索：面向 Agent 的 RAG

AI Agent 往往需要先检索工具，才能决定调用哪个工具。这可以看作对工具描述执行 RAG。

### `omnimcp-be` 索引的内容

MCP 工具索引器会从以下信息生成可搜索文档：

- MCP Server 和工具名称；
- 工具描述；
- 标签；
- 样例描述以及输入输出示例；
- 自动生成的自然语言查询；
- `website_profile`、`gpt_id` 和 `split_id` 等源数据元信息。

`omnimcp-be` 的 README 描述了七类查询，包括直接功能、使用场景、集成、问题解决、工作流、特性和通用功能。文档中设定的目标是每个工具生成 35 个不同查询，即七类查询，每类五个。

这样做的目的，是让用户表达和工具名称不完全一致时仍然可以找到正确工具。例如，用户可能说“把文档保存到 Drive”，但工具名称实际是 `create_google_doc`。

### 可提升点

用真实历史查询建立工具检索数据集，持续评估 Recall@K，并记录“检索到正确工具但执行失败”和“根本没有检索到正确工具”这两类不同问题。

### 工具索引流程

```text
MCP Server 发生变化
    -> Hook 触发 ToolIndex
    -> 格式化工具文档
    -> 生成多样化查询
    -> 创建 Embedding
    -> 将 Named Vector 和 Payload 写入 private_knowledge
```

`omnimcp-be` 的 Server Lifecycle README 展示了这种关系：MongoDB 保存 MCP Server 和工具元数据，Qdrant 保存语义搜索向量，Redis 用于分布式更新锁。

### 工具查询流程

批量接口 `/api/v1/tool/query/batch` 接收多个查询、结果数量上限、分数阈值和 `with_vector`。

Selector 会执行以下工作：

1. 识别直接的工具 ID 查询。
2. 搜索普通的自然语言查询。
3. 使用缓存和数据库元数据丰富结果。
4. 可选地检索 Browser Scraper、AI Field Template、DataFrame 和其他工具空间。
5. 在配置允许时，为特殊情况生成或恢复工具结果。
6. 按查询顺序返回结果。

Multi-Space Service 会并发执行选定空间的检索，并把结果映射回对应空间。这是一个很好的案例：真正的检索系统往往不只是调用一次向量数据库，还需要在其周围进行检索编排。

工具检索的执行过程可以抽象为：

```text
+-----------+    +-----------+    +-----------+    +-----------+
| 用户意图   | -> | 查询分类   | -> | 向量搜索   | -> | 候选合并  |
+-----------+    +-----------+    +-----------+    +-----------+
                                                           |
                                                           v
+-----------+    +-----------+    +-----------+    +-----------+
| 工具执行   | <- | LLM 选择   | <- | Schema    | <- | 过滤排序  |
+-----------+    +-----------+    +-----------+    +-----------+
```

### 为什么工具检索对 Agent 很重要

工具检索是 Agent 控制循环的一部分：

```text
用户意图
    -> 检索候选工具
    -> 过滤和排序
    -> 将工具 Schema 提供给 LLM
    -> 模型选择工具和参数
    -> 执行工具
    -> 观察工具结果
    -> 继续执行或回答
```

它的失败结果不只是“回答错误”。检索质量较差时，Agent 可能调用错误工具、没有调用必要工具，或者暴露不应该被当前用户使用的工具。

### 可提升点

在向量检索后增加工具 Schema 校验和权限校验，并设置“没有足够证据时不调用工具”的拒绝路径。

## 9. 真实代码链路阅读

下面的链路可以作为课程的主要代码阅读练习。

### 链路 A：私有知识搜索

1. 客户端向 `fastestai-api` 的 `/v1/gpt/private/search` 发送查询。
2. Router 使用 `SearchPrivateRequest` 校验请求。
3. 请求调用 `PrivateKnowledge.run`。
4. `PrivateKnowledge._create_filters` 加入 GPT ID，以及可选的时间、网站和自定义过滤器。
5. `semantic_search_documents` 调用 `QdrantRetriever.batch_retrieve_documents`，即使当前只有一个查询也是如此。
6. Retriever 为查询创建 Embedding，并携带 Payload 向 Qdrant 发送 Search Request。
7. 返回结果被校验为 `TextPoint`。
8. 命中文本块按照 `parent_id` 分组。
9. Formatter 返回文本块，或者通过 Scroll 获取父文档的全文。
10. Router 返回 `PrivateItem`，其中包含内容、Headline、来源 URL、分数和可选向量。

建议首先阅读：

- `src/fastestai/gpt/private/api.py`（L12:L163）
- `src/fastestai/tools/private_data/private_data.py`（L70:L701）
- `src/fastestai/core/text_search.py`（L129:L262）
- `src/fastestai/core/qdrant/__init__.py`（L76:L412）

私有知识搜索也可以压缩成下面的 ASCII 链路：

```text
+--------+    +---------+    +----------+    +----------+
| Client | -> | Router  | -> | Private  | -> | Filters  |
+--------+    +---------+    |Knowledge|    +----------+
                             +----------+          |
                                                    v
+---------+    +---------+    +----------+    +----------+
| API     | <- | Format  | <- | Group by | <- | Qdrant   |
| Response|    | Output  |    | parent_id|    | Search   |
+---------+    +---------+    +----------+    +----------+
```

### 可提升点

要求学习者为每一个调用节点补充输入、输出、错误和耗时字段，形成一份可用于 Debug 的 Trace Contract。

### 链路 B：工具检索

工具检索要拆成两个时机：MCP Server 变化后的索引构建，以及 Agent 发起请求时的候选工具检索。前者是写入链路，后者是读取链路；它们的触发者、延迟目标和故障模式都不同。

#### 链路 B1：MCP Server 变化后的工具索引

这是一个由 Server 生命周期事件触发的异步或后台写入流程：

1. MCP Server 被部署或发生变化。
2. Server Hook 调用工具索引器。
3. 工具名称、描述、标签、示例和生成的自然语言查询被格式化为可检索文档。
4. Embedding 客户端为这些文档创建 1536 维向量。
5. 索引器构造带 Named Vector 和 Payload 的 Point。
6. Point 被 Upsert 到 `private_knowledge` Collection，Payload 保存工具和服务元数据。

```text
+------------+    +----------+    +------------+    +-----------+
| MCP 变化   | -> | Server   | -> | Tool Index | -> | Embedding |
+------------+    | Hook     |    +------------+    +-----------+
                   +----------+             |
                                             v
                                      +-------------+
                                      | Qdrant      |
                                      | private_    |
                                      | knowledge   |
                                      +-------------+
```

这条链路的结果不是“找到一个工具”，而是把当前可用的工具目录转换成后续可以检索的索引。需要关注幂等 Upsert、重复索引、Server 删除、Embedding 失败和索引新鲜度。

#### 链路 B2：Agent 请求时的工具检索

这是由客户端查询触发的在线读取流程：

1. 客户端发送一个或多个自然语言工具查询，或者发送一个直接工具 ID 查询。
2. `router.py` 校验批量查询请求，并交给 Tool Selector。
3. Tool Selector 根据查询类型执行直接匹配、批量私有知识搜索，或调用 Multi-Space Service 检索多个工具空间。
4. Selector 使用缓存和数据库元数据补充候选工具，并保留结果与输入查询的对应关系。
5. 候选结果被映射为工具元数据和 Schema，返回给 Agent 做下一步选择。

```text
+--------+    +--------+    +----------+    +----------------+
| Client | -> | Router | -> | Selector | -> | Qdrant Search  |
+--------+    +--------+    +----------+    +----------------+
                                  |                |
                                  |                v
                                  |         +--------------+
                                  +-------> | Cache/DB     |
                                            | metadata     |
                                            +------+-------+
                                                   |
                                                   v
                                            +--------------+
                                            | Tool Schema  |
                                            | -> Agent     |
                                            +--------------+
```

这条链路的结果是“给当前 Agent 的候选工具”，不是执行工具本身。需要单独评估查询召回、批量顺序、跨空间结果合并、缓存一致性、Schema 完整性和权限过滤。

建议按链路阅读源码：

- B1 索引构建：`src/omnimcp_be/mcp/tool/tool_index.py`（L120:L365, L510:L650）
- B2 查询入口和路由：`src/omnimcp_be/mcp/tool/router.py`（L163:L301）和 `src/omnimcp_be/mcp/tool/models.py`（L17:L23, L244:L305）
- B2 候选工具选择：`src/omnimcp_be/mcp/tool/tool_selector.py`（L1394:L1682, L2644:L2960）
- B2 跨空间检索：`src/omnimcp_be/mcp/tool/multi_space_query_service.py`（L52:L245）

### 可提升点

将 B1 和 B2 画成一张对照图，明确哪些数据存在 MongoDB、哪些数据存在 Qdrant，以及哪个服务负责最终权限判断。

## 10. 动手实验

### 实验 1：理解检索抽象

目标：理解哪些能力可以跨应用复用，哪些能力属于具体业务领域。

任务：

1. 阅读 `QdrantRetriever.retrieve_documents`。
2. 找出查询在哪一行被转换为 Embedding。
3. 找出 Filter、Limit 和 Score Threshold 在哪里应用。
4. 找出 Qdrant 结果如何转换为 Pydantic Model。
5. 画出从私有搜索 Router 到 Qdrant 的调用图。

交付物：一张单页调用图，并解释 `Document`、`SearchResult` 和 `TypedSearchResult` 的作用。

### 实验 2：解释检索结果缺失实验

目标：学会区分检索质量问题和响应数据整形问题。

代码库中包含 `tests/qdrant/test_batch_search_private_data.py`（L1:L584）。它会重复调用私有批量搜索，并使用 `with_vector: true` 检查：

- 结果数量；
- 返回向量的条目和不返回向量的条目；
- 空向量条目；
- 同一查询在重复运行中的一致性。

任务：

1. 只在明确配置的测试环境中运行测试。
2. 在查看汇总统计前，先检查原始响应。
3. 比较 `with_vector: true` 和 `with_vector: false`。
4. 追踪差异发生在 Qdrant、结果转换、分组、去重还是序列化阶段。

不要因为向量字段出现波动，就直接判断最近邻搜索本身发生了变化。Qdrant 返回结果之后，响应可能还经过多层数据整形。

### 实验 3：构建一个小型文档 RAG 服务

目标：实现一个最小但完整的 RAG 循环。

使用五个关于虚构产品的短 Markdown 文档。对每个文档：

1. 分配稳定的 `parent_id`。
2. 将文档切分为文本块。
3. 使用同一个模型创建 Embedding。
4. 将向量和 Payload 保存到独立的测试 Collection。
5. 使用三个问题进行搜索。
6. 打印前五个文本块、分数和来源元数据。

验收标准：

- 文档和查询 Embedding 使用兼容的模型和维度；
- 每个 Point 都有稳定 ID 和来源 Payload；
- 可以通过租户或 Collection Filter 排除某个文档；
- 空结果和 Qdrant 连接失败可以明确区分。

### 实验 4：将检索结果用于有依据的生成

目标：先让检索结果可检查，再接入 LLM。

创建如下 Prompt：

```text
你必须基于提供的上下文回答问题。
如果上下文无法支持答案，请明确说明信息不可用。

上下文：
{retrieved_chunks}

问题：
{question}
```

记录检索到的文本块 ID、分数和来源 ID。将回答质量与检索质量分开评估。如果回答碰巧正确，但没有使用相关上下文，也不应被统计为一次成功的 RAG 检索。

### 实验 5：构建 Agent 工具路由器

目标：理解工具检索如何成为 Agent 能力的一部分。

1. 创建十个虚拟工具描述。
2. 为每个工具添加示例和不同的自然语言查询。
3. 使用 Named Vector 建立索引。
4. 使用不包含精确工具名称的用户意图进行搜索。
5. 只把排名靠前的候选工具及其 Schema 提供给 LLM。
6. 单独测试直接工具 ID 查询和语义查询。

需要测量：

- Recall@K：正确工具是否出现在前 K 个结果中；
- Precision@K：返回的工具中有多少真正有用；
- Schema 完整性：选中的工具是否拥有安全调用所需的元数据；
- 权限正确性：被禁止的工具是否可能出现在结果中。

### 可提升点

为每个实验增加一个失败用例和一个回归断言。例如，故意使用错误的向量名称、过高阈值或错误租户 ID，并验证系统返回的是可解释错误而不是静默空结果。

## 11. 评估与可观测性

RAG 至少应该被评估为两个系统：

### 检索指标

- Recall@K：相关文本块是否出现在结果中；
- Precision@K：返回上下文中有多少内容真正相关；
- Mean Reciprocal Rank：第一个相关结果排在多高的位置；
- 父文档覆盖率：是否从正确来源检索到足够的文本块；
- Filter 正确性：是否排除了未授权数据或错误租户数据。

### 生成指标

- Faithfulness：回答是否有检索上下文支持；
- 完整性：是否使用了所有必要事实；
- 引用或来源正确性：引用是否指向真正使用的证据；
- 拒答质量：缺少证据时是否能够拒绝编造答案。

### 运行指标

- Embedding 延迟和失败率；
- Qdrant Search 延迟；
- 缓存命中率；
- 批量处理吞吐量；
- 空结果比例；
- 不同查询类型的分数分布；
- 索引新鲜度；
- 向量维度和 Named Vector 一致性；
- `with_vector` 等可选字段变化时的响应结构一致性。

代码库在 Embedding 和批量搜索操作周围使用计时装饰器和结构化日志。应该使用这些日志定位延迟，而不是凭感觉猜测瓶颈组件。

### 可提升点

建立离线评估集和线上反馈闭环：把查询、候选结果、最终选择、回答质量和用户反馈关联到同一个 Trace ID，支持版本之间的对比。

## 12. 必须明确讲解的故障模式

### 空结果

可能原因包括：

- Collection 不存在；
- 没有 Point 满足租户或 GPT Filter；
- 查询 Embedding 失败；
- Score Threshold 设置过高；
- 文档从未被索引；
- 建立索引时的文本没有表达与查询相同的意图；
- 查询使用了不同的 Embedding 模型或向量名称。

### 错误结果

可能原因包括：

- 文本块过大或过小；
- Embedding 文本中没有包含标题和关键元数据；
- Filter 没有应用，或者使用了错误的 Payload Key；
- 通用工具描述造成语义冲突；
- 阈值过滤掉了正确结果，却保留了一个勉强相似的结果；
- 父文档分组改变了表面上的排名。

### 响应中的向量缺失或不一致

应该先检查完整链路：

```text
Qdrant with_vector 参数
    -> 客户端结果对象
    -> 转换为 SearchResult
    -> 提取向量
    -> 父文档分组
    -> 输出格式化
    -> Pydantic 校验
    -> JSON 序列化
```

存储的向量、Qdrant 搜索响应中的向量和 API 响应字段中的向量，是三个不同层次的观察结果。应该分别测试每个边界。

### 索引过期或重复

使用稳定 ID、明确的删除和重新索引策略、源数据版本元信息，以及 Collection 级别的检查。返回看似合理但已经过期的内容，通常比直接返回空结果更危险。

### 阻塞异步事件循环

`fastestai-api` 中的 mem0 包装器会把同步 Memory 操作放入 Executor。外部 Embedding、Qdrant 和 LLM 操作也应该被当作 I/O 边界处理。需要谨慎测量并发数：增加 Embedding 并发可能提高吞吐量，但最终会受到限流、连接池或内存的限制。

### 可提升点

为外部调用增加超时、有限重试、熔断和降级策略，并把“服务不可用”和“没有相关数据”定义成不同的状态。

## 13. 安全与数据边界

向量搜索本身不是权限系统。

安全设计应该：

- 在每条检索路径中应用租户和用户过滤器；
- 校验调用方提供的 ID 是否有权限访问；
- 在每个 Point 中保存权限相关元数据；
- 除非客户端确实需要，否则不要返回原始向量；
- 不要在源代码或课程示例中放置密钥；
- 定义 Memory 的保留、删除和更正策略；
- 将检索文本视为不可信输入，因为其中可能包含 Prompt Injection。

对于工具检索，工具不仅要相关，还必须被允许使用。LLM 不应该收到当前用户或 Agent 无权执行的工具 Schema。

### 可提升点

将权限过滤放在检索服务或数据访问层，而不是只依赖 Prompt 提示；同时增加 Prompt Injection 测试，验证恶意文档不能改变工具权限或系统指令。

## 14. 推荐课程顺序

### 模块 1：LLM 的限制与 RAG

概念：Context Window、私有数据、数据新鲜度、Grounding、RAG 与微调的区别。

练习：识别一个聊天产品中哪些功能需要检索。

### 模块 2：Embedding

概念：向量表示、维度、距离函数、分数解释。

练习：比较语义相关但字面不同的查询。

### 模块 3：Qdrant 基础

概念：Collection、Point、Payload、Named Vector、Search、Scroll、Filter。

练习：创建测试 Collection 并检查 Point。

### 模块 4：文档索引

概念：规范化、Chunking、稳定 ID、批处理、Upsert、重新索引。

练习：为一组小型 Markdown 文档建立索引。

### 模块 5：检索管道

概念：Top-K、Score Threshold、元数据过滤、分组、去重、批量查询。

练习：追踪 `/v1/gpt/private/search` 和 `/search_batch`。

### 模块 6：Agent Memory

概念：记忆提取、用户 Memory 与 Agent Memory、生命周期、隐私。详见 [`agent-memory-course.md`](agent-memory-course.md)。

练习：使用 Memory API，并检查 Memory 如何进入聊天 Prompt。

### 模块 7：工具检索

概念：工具知识、查询扩展、候选工具排序、多空间检索、Schema 补充。

练习：追踪 `omnimcp-be` 中的 `/api/v1/tool/query/batch`。

### 模块 8：评估与生产运维

概念：检索指标、Grounded Generation 指标、可观测性、一致性测试、安全。

练习：为结果数量、Filter、分数分布和向量字段行为设计回归测试。

整个课程可以沿着一条纵向路线推进：

```text
[概念]
   |
   v
[Embedding] -> [Qdrant] -> [文档索引] -> [检索 API]
                                              |
                                              v
[生产运维] <- [评估] <- [Agent 工具] <- [Agent Memory]
```

### 可提升点

把八个模块安排成一条可运行的纵向项目线：每个模块都在上一个模块的代码上增加一个能力，最终形成可演示的 Agent，而不是八个互相独立的理论练习。

## 15. 源码地图

### `fastestai-api`

- `src/fastestai/core/embeddings/base.py`（L1:L15）：Embedding 接口。
- `src/fastestai/core/embeddings/openai.py`（L8:L54）：OpenAI Embedding 实现。
- `src/fastestai/core/qdrant/__init__.py`（L76:L412）：Qdrant 客户端包装、过滤、搜索、批量搜索、Scroll、索引和删除。
- `src/fastestai/core/text_search.py`（L129:L262）：语义搜索编排。
- `src/fastestai/tools/private_data/private_data.py`（L70:L701）：私有知识过滤、父文档分组、Chunk/Full Text 格式化和批量检索。
- `src/fastestai/gpt/private/api.py`（L12:L163）：私有搜索和批量搜索的请求响应契约。
- `src/fastestai/memory/memory.py`（L53:L168）：基于 mem0 和 Qdrant 的 Agent Memory 包装器。
- `src/fastestai/memory/api.py`（L1:L124）：Memory 的添加、搜索和列表接口。
- `src/fastestai/chat/auto.py`（L104:L176, L267:L312）：Auto Chat 中的 Memory 检索和更新。
- `tests/qdrant/test_batch_search_private_data.py`（L1:L584）：重复批量搜索一致性测试。
- `src/fastestai/tools/private_data/saky_rag.py`（L19:L224）：Saky RAG 代理的数据模型、检索、查询和流式调用。
- `src/fastestai/api/routers/knowledge.py`（L18:L145）：Saky RAG 的 Knowledge API 路由。

### `omnimcp-be`

- `src/omnimcp_be/mcp/tool/models.py`（L17:L23, L244:L305）：私有 Collection 和工具查询模型定义。
- `src/omnimcp_be/mcp/tool/embedding.py`（L1:L75）：工具索引使用的 Embedding 客户端。
- `src/omnimcp_be/mcp/tool/tool_index.py`（L120:L365, L510:L650, L1083:L1115）：工具文档格式化、Embedding 缓存、Qdrant Point 创建、Upsert 和重新索引行为。
- `src/omnimcp_be/mcp/tool/tool_selector.py`（L1394:L1682, L2644:L2960）：工具检索、批量编排、元数据补充和可选向量检索。
- `src/omnimcp_be/mcp/tool/multi_space_query_service.py`（L52:L245）：跨工具空间的并发检索。
- `src/omnimcp_be/mcp/tool/router.py`（L163:L301）：工具查询 API 路由。
- `src/omnimcp_be/mcp/tool/qdrant_scanner.py`（L33:L257）：Collection 和文档数量诊断工具。
- `tests/test_tool_vector_query.py`（L1:L532）：`with_vector` 行为的 API 级检查。
- `README.md`（L217:L410）中的 "Vector Indexing & Search" 小节：向量索引和搜索的架构概览。

### 可提升点

在源码地图中补充关键函数的行号、请求样例和依赖关系，让学习者可以从 API 入口快速跳到核心实现。

## 16. 综合项目

构建一个 Agent：它能够回答一组小型产品文档中的问题，并从多个产品相关工具中选择合适的工具。

项目必须包含：

1. 一个文档入库任务。
2. 一个包含 Payload 元数据和稳定 ID 的 Qdrant Collection。
3. 一个支持租户过滤和批量查询的检索 API。
4. 一个带证据 ID 的 Grounded Answer Prompt。
5. 一个支持自然语言查询扩展的工具索引。
6. 一个 Agent 循环：检索工具、选择工具、校验参数、处理工具输出。
7. 检索、生成、延迟和一致性测试。
8. 一份故障报告：故意修改阈值、过滤器或 Chunking 配置，并记录其影响。

当其他学习者能够检查回答、找到对应的源文本块、复现检索请求，并解释被选工具为什么相关且有权限使用时，综合项目才算完成。

### 可提升点

增加可回滚的索引版本、自动化质量门禁和成本预算。发布新 Embedding 模型或 Chunking 策略时，只有离线评估、权限测试和延迟测试全部通过，才能切换到新版本。

## 17. 简化代码示例

本节用短小的教学代码，模拟两个仓库中的真实能力。代码故意没有复制生产实现中的日志、异常处理、兼容逻辑、缓存和复杂编排，因此适合用来理解核心流程，不应该直接替换生产代码。

### 17.1 Embedding 接口

真实代码把 Embedding 封装成可替换的接口。教学版本只保留单条和批量两个方法：

```python
from openai import AsyncOpenAI


class SimpleEmbedding:
    def __init__(self, model: str = "text-embedding-ada-002") -> None:
        self.client = AsyncOpenAI()
        self.model = model

    async def embed_text(self, text: str) -> list[float]:
        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
        )
        return response.data[0].embedding

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [item.embedding for item in response.data]
```

对应关系：`fastestai-api` 的 `OpenAIEmbedding` 提供同样的两个核心操作。生产实现还需要处理超时、重试、限流、空输入和成本统计。

### 17.2 将文本块写入 Qdrant

下面的示例把每个文本块变成一个带 Named Vector 的 Point：

```python
from dataclasses import asdict, dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct


@dataclass
class Chunk:
    point_id: int
    chunk_id: str
    parent_id: str
    tenant_id: str
    text: str


async def index_chunks(
    client: AsyncQdrantClient,
    embedder: SimpleEmbedding,
    collection: str,
    chunks: list[Chunk],
) -> None:
    vectors = await embedder.embed_texts([chunk.text for chunk in chunks])
    points = [
        PointStruct(
            id=chunk.point_id,
            vector={"text": vector},
            payload={**asdict(chunk), "id": chunk.chunk_id},
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    await client.upsert(collection_name=collection, points=points, wait=True)
```

这里的 `"text"` 是向量名称。查询时必须使用同一个名称。真实的 `omnimcp-be` 工具索引流程也会把工具描述、示例和元数据整理成文档，再生成向量并 Upsert 到 `private_knowledge`。

### 17.3 使用 Payload Filter 检索

向量相似度只能说明语义相关。下面的 Filter 额外限制租户：

```python
from qdrant_client.models import FieldCondition, Filter, MatchValue


async def retrieve_chunks(
    client: AsyncQdrantClient,
    embedder: SimpleEmbedding,
    collection: str,
    query: str,
    tenant_id: str,
    limit: int = 5,
) -> list[dict]:
    query_vector = await embedder.embed_text(query)
    tenant_filter = Filter(
        must=[
            FieldCondition(
                key="tenant_id",
                match=MatchValue(value=tenant_id),
            )
        ]
    )

    hits = await client.search(
        collection_name=collection,
        query_vector=("text", query_vector),
        query_filter=tenant_filter,
        limit=limit,
        with_payload=True,
    )
    return [
        {
            "id": hit.id,
            "score": hit.score,
            "text": hit.payload["text"],
            "parent_id": hit.payload["parent_id"],
        }
        for hit in hits
    ]
```

这个示例对应 `QdrantRetriever.retrieve_documents` 的核心行为：查询向量、Named Vector、Payload Filter、Limit 和返回 Payload。生产代码还需要校验 Payload 结构，并根据业务需要设置 Score Threshold。

### 17.4 从文本块生成有依据的回答

RAG 的关键不是把搜索和 LLM 调用写在一起，而是把检索到的证据显式放进 Prompt：

```python
async def answer_question(
    question: str,
    retriever,
    llm,
) -> str:
    hits = await retriever(question)
    context = "\n\n".join(
        f"[证据 {item['id']}] {item['text']}" for item in hits
    )

    prompt = f"""
请只根据下面的证据回答问题。
如果证据不足，请回答“没有足够信息”。

证据：
{context}

问题：
{question}
"""

    response = await llm.chat.completions.create(
        model="your-chat-model",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""
```

教学时应先打印 `hits`，再调用 LLM。这样可以确认错误来自检索，还是来自回答生成。真实服务还应该返回来源 ID、分数和必要的引用信息。

### 17.5 批量检索多个问题

批量检索的重点是：一次批量生成查询向量，并保持查询与结果的顺序：

```python
from qdrant_client import models


async def batch_retrieve(
    client: AsyncQdrantClient,
    embedder: SimpleEmbedding,
    collection: str,
    queries: list[str],
    limit: int = 5,
) -> list[list[dict]]:
    vectors = await embedder.embed_texts(queries)
    requests = [
        models.SearchRequest(
            vector=("text", vector),
            limit=limit,
            with_payload=True,
        )
        for vector in vectors
    ]
    batch_hits = await client.search_batch(
        collection_name=collection,
        requests=requests,
    )
    return [
        [
            {"id": hit.id, "score": hit.score, "payload": hit.payload}
            for hit in hits
        ]
        for hits in batch_hits
    ]
```

返回值的外层列表对应输入查询，内层列表对应该查询的命中结果。这个顺序契约是 `fastestai-api` 私有批量搜索和 `omnimcp-be` 工具批量查询都必须保护的行为。

### 17.6 将工具描述作为可检索文档

工具检索可以复用同一套索引和搜索抽象：

```python
tools = [
    {
        "id": "create_doc",
        "name": "create_google_doc",
        "description": "创建一个 Google Doc 并设置标题",
    },
    {
        "id": "send_message",
        "name": "send_telegram_message",
        "description": "向 Telegram 用户发送消息",
    },
]

tool_chunks = [
    Chunk(
        point_id=index,
        chunk_id=tool["id"],
        parent_id=tool["id"],
        tenant_id="tool-catalog",
        text=f"{tool['name']}：{tool['description']}",
    )
    for index, tool in enumerate(tools, start=1)
]

await index_chunks(client, embedder, "tool_catalog", tool_chunks)
matches = await retrieve_chunks(
    client,
    embedder,
    "tool_catalog",
    query="帮我新建一份 Google 文档",
    tenant_id="tool-catalog",
)
```

这个简化例子只索引工具名称和描述。真实的 `omnimcp-be` 还会加入标签、样例、生成的自然语言查询和服务元数据，从而提升用户意图与工具名称不一致时的召回率。

### 17.7 一个最小的检索回归测试

检索测试不应该只断言接口返回了 `200`，还应该验证结果内容和数据边界：

```python
async def test_retrieval_respects_tenant(client, embedder) -> None:
    await index_chunks(
        client,
        embedder,
        "test_docs",
        [
            Chunk(1, "a-1", "doc-a", "tenant-a", "甲公司的退款规则"),
            Chunk(2, "b-1", "doc-b", "tenant-b", "乙公司的退款规则"),
        ],
    )

    results = await retrieve_chunks(
        client,
        embedder,
        "test_docs",
        "退款规则是什么？",
        tenant_id="tenant-a",
    )

    assert results
    assert all(item["parent_id"] == "doc-a" for item in results)
```

这个测试使用固定数据验证租户过滤。课程后续可以继续增加：空结果、错误向量名称、过高阈值、批量顺序、重复索引和 `with_vector` 响应一致性测试。

### 17.8 简化代码与生产代码的对应关系

```text
+----------------------+       +----------------------------------+
| 教学代码             |  ---> | 真实仓库实现                     |
+----------------------+       +----------------------------------+
| SimpleEmbedding      |  ---> | OpenAIEmbedding                 |
| index_chunks         |  ---> | QdrantRetriever.add_documents   |
| retrieve_chunks      |  ---> | retrieve_documents / private    |
| batch_retrieve       |  ---> | batch_retrieve_documents         |
| tool_chunks          |  ---> | ToolIndex + ToolSelector         |
| memory 教学          |  ---> | Agent Memory + auto chat         |
+----------------------+       +----------------------------------+
```

学习顺序建议是：先运行或手动推演教学代码，再回到真实源码，逐一寻找日志、异常处理、并发控制、缓存、权限和数据整形是如何补上的。

### 可提升点

为每个简化示例增加一个“从教学版到生产版”的任务。例如给 `retrieve_chunks` 增加 Score Threshold、时间 Filter、超时和结构化日志，再与 `fastestai-api` 的 `QdrantRetriever` 对照。

## 18. 进阶课程：复杂文档处理与多 Retriever 架构

基础 RAG 通常假设文档是干净的纯文本。但真实业务文档可能同时包含：

- 普通段落和标题；
- 多级表头、合并单元格和跨页表格；
- 扫描图片和 OCR 文本；
- ERP 或财务系统生成的非规则 PDF；
- 旋转文本、缺失边框、很小的字体和嵌套表格；
- 图片、脚注、页眉页脚和附录。

此时，单一的文本切分器或单一的 Dense Retriever 往往不够。进阶架构需要先理解文档，再根据页面和查询类型选择多个检索器。

### 18.1 TATR 的正确定位

`microsoft/table-transformer-structure-recognition`，通常称为 TATR，是很有价值的表格结构检测模型，但不应该被理解为“完整的 PDF 语义解析器”。

TATR 更适合回答：

```text
表格在哪里？
行和列在哪里？
单元格的几何边界是什么？
哪些区域属于表头、行或列？
```

它不应该单独负责回答：

```text
这一列的业务含义是什么？
这个数字代表收入、税额还是余额？
跨页表格应该如何合并？
这个单元格应该继承哪一级表头？
```

一个适合教学的分工是：

```text
+-------------+    +-------------+    +-------------+
| PDF 页面    | -> | TATR        | -> | 几何结构    |
+-------------+    +-------------+    +-------------+
                                             |
                                             v
+-------------+    +-------------+    +-------------+
| 业务含义    | <- | OCR/文本    | <- | 单元格区域  |
+-------------+    +-------------+    +-------------+
```

官方 TATR-v1.0 在 PubTables-1M 上报告了很高的检测和结构指标，例如 AP50 `97.0%`、AP75 `94.1%`、AP `90.2%`，以及约 `98.5%` 的 GriTS Top/Con。GriTS 比单纯的目标检测 AP 更接近“表格网格是否重建正确”。

但是，基准数据集和任意业务 PDF 不是同一个问题。PubTables-1M 主要来自科学论文，复杂财务表、中文 ERP PDF、扫描件、超大表格和嵌套表格可能明显偏离训练分布。课程中应把这些数字当作基准参考，而不是任意 PDF 的成功率保证。

还应该注意模型版本：评估 TATR 时，应比较原始 v1.0 和后续的 TATR-v1.1-Pub、TATR-v1.1-Fin、TATR-v1.1-All，而不是只测一个旧模型。

### 可提升点

建立自己的业务 PDF 小型评估集，按“普通表格、合并单元格、多级表头、扫描表格、超大表格、嵌套表格”分层统计，不要只引用 PubTables-1M 的总分。

### 18.2 复杂文档处理流程

复杂 PDF 不应该直接进入一个统一的文本切分器。先对页面进行分类，再选择解析路径：

```text
+---------+
| PDF     |
+---------+
     |
     v
+---------+      +------------------+
| 页面分析 | ---> | 文本/版面信号    |
+---------+      +------------------+
     |
     +------------------+------------------+------------------+
     |                  |                  |                  |
     v                  v                  v                  v
+---------+        +---------+        +---------+        +---------+
| 纯文本页 |        | 表格页  |        | 扫描页  |        | 复杂页  |
+---------+        ++---------+       ++---------+       ++---------+
     |                  |                  |                  |
     v                  v                  v                  v
文本解析器          TATR + OCR          OCR           VLM/版面分析
     |                  |                  |                  |
     +------------------+------------------+------------------+
                        |
                        v
              +------------------+
              | 统一证据对象      |
              +------------------+
                        |
                        v
              +------------------+
              | 多 Retriever 检索 |
              +------------------+
```

页面分类可以使用简单的启发式规则开始，再逐步替换为分类模型。关键是让每一页的处理结果都进入统一数据结构，而不是让每条解析路径返回不同格式。

### 18.3 简化的页面路由代码

下面的代码不是 PDF 解析器，而是展示“先分类，再选择解析器”的接口设计：

```python
from dataclasses import dataclass
from enum import StrEnum


class PageKind(StrEnum):
    TEXT = "text"
    TABLE = "table"
    SCAN = "scan"
    COMPLEX = "complex"


@dataclass
class PageSignals:
    text_chars: int
    image_count: int
    table_score: float
    has_rotated_text: bool = False


def classify_page(signals: PageSignals) -> PageKind:
    if signals.has_rotated_text or signals.table_score > 0.8:
        return PageKind.COMPLEX
    if signals.table_score > 0.5:
        return PageKind.TABLE
    if signals.text_chars < 80 and signals.image_count > 0:
        return PageKind.SCAN
    return PageKind.TEXT


def parser_for(kind: PageKind) -> str:
    return {
        PageKind.TEXT: "text-parser",
        PageKind.TABLE: "tatr-plus-ocr",
        PageKind.SCAN: "ocr",
        PageKind.COMPLEX: "vlm-layout-parser",
    }[kind]
```

教学重点不是阈值本身，而是路由接口。真实系统可以把 `parser_for` 替换为可配置的策略，并记录每页的路由原因和解析版本。

### 18.4 统一证据对象

不同解析器必须输出统一的证据对象，才能被同一套 Retriever 和 Reranker 使用：

```python
from dataclasses import dataclass, field


@dataclass
class Evidence:
    id: str
    text: str
    source_file: str
    page: int
    kind: str
    score: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)


def table_cell_evidence(
    source_file: str,
    page: int,
    table_id: str,
    row: int,
    column: int,
    header: str,
    value: str,
) -> Evidence:
    return Evidence(
        id=f"{table_id}:r{row}:c{column}",
        text=f"{header}: {value}",
        source_file=source_file,
        page=page,
        kind="table-cell",
        metadata={"table_id": table_id},
    )
```

这样，段落文本、表格单元格、OCR 行和 VLM 识别的区域都可以成为检索证据。`kind` 和 `metadata` 让后续逻辑知道证据来自哪一种结构。

### 18.5 表格结构解析的简化分工

表格解析建议分成三层：

```text
+-------------+    +-------------+    +-------------+
| 结构检测    | -> | OCR/文本定位 | -> | 语义重建    |
| TATR        |    | words       |    | headers     |
+-------------+    +-------------+    +-------------+
        |                   |                   |
        v                   v                   v
      boxes              cell text          business fields
```

一个教学级的结构模型可以只表示区域和文本：

```python
from dataclasses import dataclass


@dataclass
class Box:
    left: int
    top: int
    right: int
    bottom: int


@dataclass
class Cell:
    row: int
    column: int
    box: Box
    text: str = ""


def assign_words_to_cells(cells: list[Cell], words: list[tuple[Box, str]]) -> list[Cell]:
    for cell in cells:
        cell_words = [
            text for word_box, text in words if overlaps(cell.box, word_box)
        ]
        cell.text = " ".join(cell_words)
    return cells


def overlaps(left: Box, right: Box) -> bool:
    return not (
        left.right < right.left
        or right.right < left.left
        or left.bottom < right.top
        or right.bottom < left.top
    )
```

这里的 `cells` 可以来自 TATR，`words` 可以来自 OCR 或 PDF 文本层。真实系统还要处理坐标系缩放、阅读顺序、重叠词、多行文本、合并单元格和跨页表格。

### 可提升点

不要让表格检测器直接输出最终业务 JSON。先保存原始框、OCR 词、单元格映射和重建后的表格，保留中间证据，才能在结构错误时定位是检测、OCR 还是语义映射出了问题。

### 18.6 为什么需要 Multi-Retriever

不同 Retriever 擅长不同问题：

| Retriever | 擅长内容 | 典型失败 |
|---|---|---|
| Dense Retriever | 同义表达、自然语言意图 | 精确数字、短 ID、代码字段 |
| Keyword Retriever | 精确词、编号、产品型号 | 同义改写、跨语言表达 |
| Metadata Retriever | 日期、租户、文档类型、页码 | 未建索引或字段缺失 |
| Table Retriever | 行列、表头、数值关系 | 普通段落和跨表语义 |
| Image/VLM Retriever | 图表、扫描页、版面关系 | 成本、延迟和结果稳定性 |

多 Retriever 不表示每次都把所有系统无条件调用一遍。更合理的做法是：

```text
+---------+    +-----------+    +----------------+
| Query   | -> | Query 类型 | -> | Retriever 路由 |
+---------+    +-----------+    +----------------+
                                      |
                 +--------------------+--------------------+
                 |                    |                    |
                 v                    v                    v
           Dense Search        Keyword Search        Table Search
                 |                    |                    |
                 +--------------------+--------------------+
                                      |
                                      v
                              结果融合 + 重排
                                      |
                                      v
                                 最终证据
```

### 18.7 简化的 Retriever 接口

让所有检索器返回相同的 `Evidence` 类型：

```python
import asyncio

from typing import Protocol


class Retriever(Protocol):
    name: str

    async def search(self, query: str, top_k: int) -> list[Evidence]:
        ...


class MultiRetriever:
    def __init__(self, retrievers: list[Retriever]) -> None:
        self.retrievers = retrievers

    async def search(self, query: str, top_k: int = 5) -> list[Evidence]:
        results = await asyncio.gather(
            *(retriever.search(query, top_k) for retriever in self.retrievers)
        )

        merged: dict[str, Evidence] = {}
        for retriever_results in results:
            for rank, evidence in enumerate(retriever_results, start=1):
                item = merged.setdefault(evidence.id, evidence)
                item.score += 1 / (60 + rank)

        return sorted(
            merged.values(),
            key=lambda item: item.score,
            reverse=True,
        )[:top_k]
```

这里使用的是简化版 Reciprocal Rank Fusion 思路：不同 Retriever 的原始分数可能不可直接比较，因此先使用排名融合。生产系统可以加入 Retriever 权重、查询类型权重、权限过滤和专门的 Reranker。

### 18.8 加入 Query Router

可以在调用 Multi-Retriever 前，根据查询和文档元数据选择策略：

```python
def retriever_names(query: str, has_tables: bool) -> list[str]:
    names = ["dense", "keyword"]

    if any(token in query for token in ["金额", "合计", "同比", "第几行"]):
        names.append("table")
    if has_tables and "表格" in query:
        names.append("table")
    if "页码" in query or "原文" in query:
        names.append("metadata")

    return list(dict.fromkeys(names))
```

更成熟的 Router 可以使用一个轻量分类模型，但必须保留规则和分类结果，便于解释为什么某个 Retriever 被调用或跳过。

### 18.9 复杂文档的端到端架构

适用于财务、ERP、中文业务 PDF 的一个候选架构是：

```text
                         +----------------+
                         | 原始 PDF       |
                         +----------------+
                                  |
                                  v
                         +----------------+
                         | 页面/版面分类  |
                         +----------------+
                                  |
              +-------------------+-------------------+
              |                   |                   |
              v                   v                   v
       +-------------+     +-------------+     +-------------+
       | 文本层      |     | 表格区域    |     | 页面图像    |
       | PDF/OCR     |     | TATR        |     | VLM/OCR     |
       +-------------+     +-------------+     +-------------+
              |                   |                   |
              v                   v                   v
       +-------------+     +-------------+     +-------------+
       | 段落证据    |     | 单元格证据  |     | 图像证据    |
       +-------------+     +-------------+     +-------------+
              \                  |                  /
               \                 |                 /
                +----------------+----------------+
                                 |
                                 v
                         +----------------+
                         | 统一 Evidence  |
                         +----------------+
                                 |
                                 v
                         +----------------+
                         | Query Router   |
                         +----------------+
                                 |
                 +---------------+---------------+
                 |               |               |
                 v               v               v
             Dense           Keyword           Table
                 |               |               |
                 +---------------+---------------+
                                 |
                                 v
                         +----------------+
                         | 融合/重排      |
                         +----------------+
                                 |
                                 v
                         +----------------+
                         | Grounded LLM   |
                         +----------------+
```

### 18.10 进阶评估方法

复杂文档不能只评估“回答是否正确”，需要分别测量：

1. 页面路由是否正确；
2. 表格检测是否找到正确区域；
3. 行列和单元格结构是否正确；
4. OCR 文本是否被放入正确单元格；
5. 表头是否正确继承到数据单元格；
6. 相关证据是否被正确 Retriever 找到；
7. 多 Retriever 融合后是否提高 Recall@K；
8. 最终回答是否忠实于证据。

建议为每种文档类型建立一个评估矩阵：

| 文档类型 | 路由正确率 | 表格结构 | 检索 Recall@K | 回答忠实度 |
|---|---:|---:|---:|---:|
| 科学论文 |  |  |  |  |
| 普通数字 PDF |  |  |  |  |
| 财务报告 |  |  |  |  |
| 中文 ERP PDF |  |  |  |  |
| 扫描文档 |  |  |  |  |
| 合并单元格表格 |  |  |  |  |
| 超大或嵌套表格 |  |  |  |  |

### 18.11 与当前仓库的结合练习

这个进阶 Session 可以和当前两个仓库这样结合：

1. 使用 `fastestai-api` 的 `QdrantRetriever` 作为统一 Dense Retriever 外壳。
2. 将表格单元格转成 `TextPoint` 或等价 Payload，保留 `source_file`、`page`、`table_id`、`row` 和 `column`。
3. 为表格证据增加一个 Metadata Filter，例如只搜索指定文档、页码或表格。
4. 使用 `batch_retrieve_documents` 同时检索多个查询变体。
5. 在 `omnimcp-be` 的工具检索思路上，为不同文档类型建立不同的 Retriever Space。
6. 用统一 `Evidence` 结构把文本检索、表格检索和工具检索结果合并。
7. 在送入 Agent 或 LLM 前，记录每条证据来自哪个 Retriever 以及为什么被选中。

### 可提升点

把 Retriever 路由从硬编码规则升级为“规则 + 轻量分类器 + 失败回退”：当表格 Retriever 结果为空或置信度不足时，自动补充 Dense 和 Keyword Search，并记录本次回退原因。

### 18.12 延伸阅读

- TATR 官方仓库：[microsoft/table-transformer](https://github.com/microsoft/table-transformer)
- Hugging Face 文档 AI 介绍：[Document AI](https://github.com/huggingface/blog/blob/main/document-ai.md)
- TATR v1.1-Pub：[bsmock/TATR-v1.1-Pub](https://huggingface.co/bsmock/TATR-v1.1-Pub)
- 表格结构数据集对齐研究：[Aligning benchmark datasets for table structure recognition](https://www.researchgate.net/publication/368923317_Aligning_benchmark_datasets_for_table_structure_recognition)
