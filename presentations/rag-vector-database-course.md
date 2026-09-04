# 通过 RAG 与向量数据库学习 AI Agent

## 课程草案：从 Embedding 到生产级检索

本课程使用两个真实服务作为学习实验室：

- `fastestai-api`：基于 FastAPI 的 AI 服务，包含通用 Qdrant 检索器、私有知识搜索、Agent Memory，以及外部 Saky RAG 代理。
- `omnimcp-be`：MCP 后端，负责生成可搜索的工具知识、将工具索引到 Qdrant，并为 AI Agent 检索候选工具。

课程目标不是记住某个厂商的 API，而是理解从用户问题到检索上下文，再到 Agent 决策的完整链路。

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

在 `omnimcp-be` 中，工具向量使用 Named Vector `openai:text-embedding-ada-002`，维度为 `1536`。索引代码向 OpenAI Embedding 客户端传入未加前缀的模型名 `text-embedding-ada-002`，然后用 Named Vector 名称保存结果。相关代码位于 `src/omnimcp_be/mcp/tool/tool_index.py`。

在 `fastestai-api` 中，`OpenAIEmbedding` 提供 `embed_text` 和 `embed_texts`，`QdrantRetriever` 在单条和批量检索中都使用这两个方法。相关代码位于 `src/fastestai/core/embeddings/openai.py` 和 `src/fastestai/core/qdrant/__init__.py`。

### 相似度分数不是通用概率

只有在距离函数、Embedding 模型、Collection 和数据分布都相同的情况下，分数阈值才具有可比性。不要把 `0.8` 当作通用的相关性标准。应该使用带标签的查询样本来调节阈值。

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

### `search` 与 `scroll` 的区别

代码库使用两种不同的 Qdrant 操作：

- `search`：根据查询向量检索最近的 Point。
- `scroll`：根据 Payload 过滤器遍历 Point，常用于重新获取某个父文档的所有文本块，或者检查 Collection 内容。

这个区别是理解 RAG 的关键。最近邻搜索可能只返回几个匹配文本块，而 Full Text 响应可能需要再次执行 `scroll`，加载属于选中文档的所有文本块。

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

## 7. RAG 与 Agent Memory 是不同的系统

RAG 和 Memory 经常都使用 Embedding 与 Qdrant，但它们回答的是不同的问题。

| 系统 | 要回答的问题 | 典型生命周期 |
|---|---|---|
| 知识 RAG | 哪些源文档可以回答这个问题？ | 入库、索引、检索、引用或提供依据 |
| Agent Memory | Agent 应该记住哪些与用户或历史交互有关的事实？ | 提取、保存、检索、更新、遗忘 |
| 工具检索 | 哪些工具描述与当前任务匹配？ | 索引工具知识、检索候选工具、执行选中的工具 |

### `fastestai-api` 中的 Agent Memory

`fastestai-api` 使用异步 `Memory` 类包装 `mem0.Memory`。Memory 配置使用 Qdrant，并将 Collection 设置为 `mem0`。由于 mem0 的操作是同步的，包装类会把它们放入 Executor 中执行，避免阻塞异步事件循环。

Memory API 提供：

- `POST /memory/add`：使用 `agent_id`、可选 `user_id` 和元数据保存内容；
- `POST /memory/search`：检索相关记忆；
- `POST /memory/list`：按时间范围和可选元数据过滤列出记忆。

在 Auto Chat 流程中，可以分别检索用户 Memory 和 GPT Memory。检索结果会被格式化后放入 Prompt 的用户记忆区和助手记忆区。响应完成后，流程还可以使用本次交互更新 Memory。

这与建立文档知识库索引不是一回事。Memory 是选择性的、与行为相关的系统，应该重点考虑隐私、保留期限、更正和删除。

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

这样做的目的，是让用户表达和工具名称不完全一致时仍然可以找到正确工具。例如，用户可能说"把文档保存到 Drive"，但工具名称实际是 `create_google_doc`。

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

它的失败结果不只是"回答错误"。检索质量较差时，Agent 可能调用错误工具、没有调用必要工具，或者暴露不应该被当前用户使用的工具。

## 9. 真实代码链路阅读

下面两条链路可以作为课程的主要代码阅读练习。

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

- `src/fastestai/gpt/private/api.py`
- `src/fastestai/tools/private_data/private_data.py`
- `src/fastestai/core/text_search.py`
- `src/fastestai/core/qdrant/__init__.py`

### 链路 B：工具检索

1. MCP Server 被部署或发生变化。
2. Server Hook 调用工具索引器。
3. 工具元数据和生成的查询文本被转换为文档。
4. Embedding 客户端创建 1536 维向量。
5. Qdrant 在 `private_knowledge` 中保存 Named Vector 和 Payload 元数据。
6. 客户端发送一个或多个自然语言工具查询。
7. Tool Selector 执行批量私有搜索和可选的缓存补充。
8. 结果被映射为工具元数据并返回给 Agent。

建议首先阅读：

- `src/omnimcp_be/mcp/tool/tool_index.py`
- `src/omnimcp_be/mcp/tool/tool_selector.py`
- `src/omnimcp_be/mcp/tool/multi_space_query_service.py`
- `src/omnimcp_be/mcp/tool/router.py`
- `src/omnimcp_be/mcp/tool/models.py`

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

代码库中包含 `tests/qdrant/test_batch_search_private_data.py`。它会重复调用私有批量搜索，并使用 `with_vector: true` 检查：

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

概念：记忆提取、用户 Memory 与 Agent Memory、生命周期、隐私。

练习：使用 Memory API，并检查 Memory 如何进入聊天 Prompt。

### 模块 7：工具检索

概念：工具知识、查询扩展、候选工具排序、多空间检索、Schema 补充。

练习：追踪 `omnimcp-be` 中的 `/api/v1/tool/query/batch`。

### 模块 8：评估与生产运维

概念：检索指标、Grounded Generation 指标、可观测性、一致性测试、安全。

练习：为结果数量、Filter、分数分布和向量字段行为设计回归测试。

## 15. 源码地图

### `fastestai-api`

- `src/fastestai/core/embeddings/base.py`：Embedding 接口。
- `src/fastestai/core/embeddings/openai.py`：OpenAI Embedding 实现。
- `src/fastestai/core/qdrant/__init__.py`：Qdrant 客户端包装、过滤、搜索、批量搜索、Scroll、索引和删除。
- `src/fastestai/core/text_search.py`：语义搜索编排。
- `src/fastestai/tools/private_data/private_data.py`：私有知识过滤、父文档分组、Chunk/Full Text 格式化和批量检索。
- `src/fastestai/gpt/private/api.py`：私有搜索和批量搜索的请求响应契约。
- `src/fastestai/memory/memory.py`：基于 mem0 和 Qdrant 的 Agent Memory 包装器。
- `src/fastestai/memory/api.py`：Memory 的添加、搜索和列表接口。
- `src/fastestai/chat/auto.py`：Auto Chat 中的 Memory 检索和更新。
- `tests/qdrant/test_batch_search_private_data.py`：重复批量搜索一致性测试。

### `omnimcp-be`

- `src/omnimcp_be/mcp/tool/models.py`：私有 Collection 和工具查询模型定义。
- `src/omnimcp_be/mcp/tool/embedding.py`：工具索引使用的 Embedding 客户端。
- `src/omnimcp_be/mcp/tool/tool_index.py`：工具文档格式化、Embedding 缓存、Qdrant Point 创建、Upsert 和重新索引行为。
- `src/omnimcp_be/mcp/tool/tool_selector.py`：工具检索、批量编排、元数据补充和可选向量检索。
- `src/omnimcp_be/mcp/tool/multi_space_query_service.py`：跨工具空间的并发检索。
- `src/omnimcp_be/mcp/tool/router.py`：工具查询 API 路由。
- `src/omnimcp_be/mcp/tool/qdrant_scanner.py`：Collection 和文档数量诊断工具。
- `tests/test_tool_vector_query.py`：`with_vector` 行为的 API 级检查。
- `README.md` 中的 "Vector Indexing & Search" 小节：向量索引和搜索的架构概览。

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

