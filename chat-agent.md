# Chat Agent 开发课程示例：`/v2/chat/streaming`

本文以 `fastestai-api` 当前代码为例，追踪一次用户输入从 HTTP 请求进入，到 Agent、Memory、工具和 SSE 响应返回的完整过程。

目标不是把实现描述成一个“调用 LLM”的函数，而是看清一个生产型 Chat Agent 通常包含的几个阶段：

1. 接收并整理会话输入。
2. 判断是否可以走低延迟快路径。
3. 为复杂任务准备上下文、Memory 和知识。
4. 创建 Agent，并让 Agent 自己决定是否检索工具、调用工具或调度其他 Agent。
5. 把内部事件转成客户端可以消费的流式消息。
6. 在回答完成后更新长期 Memory。

## 1. 先确定入口

目标路由定义在：

```text
src/fastestai/api/routers/chat.py:197-289
```

代码使用 `with_version("/chat/streaming", version="v2")`，所以本例入口是：

```text
POST /v2/chat/streaming
Content-Type: application/json
Accept: text/event-stream
```

> `chat.py:204` 的审计日志把入口写成了 `/api/v2/chat/streaming`，这是日志字段；实际路由装饰器生成的是 `/v2/chat/streaming`。

### 请求模型

请求体的核心模型是 `ChatRequestV2`：

```json
{
  "messages": [
    {
      "role": "user",
      "content": "帮我查一下本周的销售数据",
      "timestamp": 1760000000
    }
  ],
  "gpt_id": "gpt-123",
  "user_id": "user-456",
  "memory": true,
  "omni": true,
  "task_id": "task-789"
}
```

`messages` 会按 `timestamp` 排序，最后一条作为当前问题，其余消息作为历史。因此“最后一条数组元素”不一定是当前问题。

请求模型还支持 `team`、`plan`、`app_context`、`user_context`、`extra_context` 和 `dispatch_mode`，它们会影响 Agent 编排。

当前 v2 handler 有三个容易误读的实现细节：

- `images` 在 v2 handler 中没有传给 `streaming_chat_auto`，当前路径不会处理图片。
- `use_agent` 也没有传给 `streaming_chat_auto`，当前路径实际由 `omni`、`team`、`plan` 和相似度分类决定是否进入 Agent。
- `model` 字段虽然存在于请求模型中，但 handler 没有向 `streaming_chat_auto` 传递；复杂路径使用默认模型，快路径使用 `SETTINGS.llm.model.simple_chat`。

这是课程中很重要的工程经验：请求模型上的字段，不等于该字段已经接入执行链路。

## 2. 全链路总图

下面的图只画调用关系，后面再拆每个阶段。

```text
                         用户 / Chat Client
                                |
                                | POST /v2/chat/streaming
                                v
+------------------------------------------------------------------+
| FastAPI: streaming_chat_v2(request)                             |
|                                                                  |
|  1. 绑定 task_id / user_id / gpt_id / request_id                 |
|  2. split_messages(messages)                                     |
|  3. 立即发送 System Log: Processing Request                     |
|  4. 相似度分类: enable_nlu                                       |
|  5. setup_translator(last_message)                               |
|  6. 组装 UserContext，并调用 streaming_chat_auto                 |
+------------------------------------------------------------------+
                                |
                                v
                    +---------------------------+
                    | is_simple_task ?          |
                    +-------------+-------------+
                                  |
                 +----------------+----------------+
                 |                                 |
                yes                               no
                 |                                 |
                 v                                 v
+-----------------------------+   +-------------------------------+
| Ultra-fast path             |   | Complex / Agent path          |
| fast_simple_chat            |   | prepare_chat                  |
|                             |   |   - optional Memory search    |
| direct LLM stream           |   | Context.prepare                |
| simple_chat model           |   |   - domain                    |
|                             |   |   - NLU                       |
|                             |   |   - history summary           |
|                             |   |   - app/domain knowledge     |
|                             |   | omnify                        |
|                             |   |   - OmniAgent                 |
|                             |   |   - team / plan / single     |
+--------------+--------------+   +---------------+---------------+
               |                                  |
               |                                  v
               |                 +-------------------------------+
               |                 | Agent loop                    |
               |                 | ReActAgent / TaskAgent         |
               |                 |                               |
               |                 | - reason about the task       |
               |                 | - call built-in tools         |
               |                 | - search dynamic tools        |
               |                 | - load MCP tools              |
               |                 | - call MCP server             |
               |                 | - dispatch agents/apps        |
               |                 +---------------+---------------+
               |                                 |
               +----------------+----------------+
                                v
                 +-------------------------------+
                 | Content / Log chunk processing |
                 | optional response translation  |
                 | agent result or LLM fallback   |
                 | optional Memory update         |
                 +---------------+---------------+
                                 |
                                 v
                    EventSourceResponse / SSE
                                 |
                                 v
                           Chat Client
```

入口函数本身不直接生成最终答案。它负责请求级工作和流式包装，真正的路径选择和 Agent 执行在 `fastestai.chat.auto.streaming_chat` 中完成。

## 3. 第一段：请求接入和早期判断

### 3.1 绑定请求上下文

路由首先绑定审计和结构化日志上下文：

```text
request.task_id       -> runtime audit context
request.user_id       -> runtime audit context
request.app_context.id -> audit app
request.gpt_id        -> structlog context
request_id            -> structlog context
```

这样后续 Memory、LLM、Agent 和工具日志可以用相同的 `task_id` 串起来。对 Agent 系统来说，`task_id` 不只是一个业务字段，也是排查一次复杂执行的主关联键。

### 3.2 取出当前问题

```text
messages
   |
   +-- 按 timestamp 排序
   |
   +-- last_message    当前用户问题
   |
   +-- chat_history    之前的会话消息
```

如果 `messages` 为空，`split_messages` 会抛出 `LastMessageIsNotUserError`，请求不会进入 Agent 流程。

### 3.3 先发一条状态事件

路由在分类之前就发送：

```json
{
  "type": "log",
  "data": {
    "scope": "system",
    "log": {
      "header": "Processing Request",
      "content": "Processing your request..."
    }
  }
}
```

这是“首字节体验”和“最终答案”分离的例子：客户端先知道请求已经被接收，后续才等待检索或 Agent 执行。

### 3.4 相似度分类

`classify_message_similarity` 使用 OpenAI embedding 查询 Qdrant collection：

```text
last_message.content
        |
        v
OpenAIEmbedding
        |
        v
Qdrant: MAYBEAI_similarity_dispatcher_examples
        |
        +-- limit=10
        +-- score_threshold=0.85
        v
有足够相似的简单问题示例吗？
        |
        +-- 有: enable_nlu=False -> is_simple_task=True
        +-- 无: enable_nlu=True  -> 进入完整处理
```

分类失败时默认走完整处理。这是一个合理的降级方向：宁愿多做一次 NLU，也不要因为分类服务失败而把复杂任务当成普通聊天。

## 4. 第二段：两条执行路径

### 4.1 快路径：直接聊天

只有以下条件同时满足时才会进入 `fast_simple_chat`：

```text
is_simple_task
and no explicit agents
and no agent_ids
and no plan
and omni is true
```

执行流程：

```text
last_message.content
        |
        v
fast_simple_chat
        |
        +-- 使用 SETTINGS.llm.model.simple_chat
        +-- system prompt: helpful assistant / OmniMCP
        +-- 将 chat_history 转成 OpenAI message
        +-- _chat_stream(...)
        +-- 收集底层文本片段
        v
一次性生成 ContentChunk(完整回答)
```

这里有一个容易和“streaming”混淆的点：底层 LLM 是流式读取的，但 `fast_simple_chat` 会先把文本片段收集起来，最后只 yield 一个完整的 `ContentChunk`。因此客户端看到的是 SSE 传输，未必是逐 token 的内容事件。

快路径跳过：

- Memory 检索。
- `Context.prepare()`。
- Agent 创建和工具发现。
- Agent 日志流。

### 4.2 复杂路径：Agent 编排

复杂路径进入 `streaming_chat`：

```text
prepare_chat
    |
    +-- optional Memory search
    +-- default model / system prompt
    +-- last_message / chat_history
    |
    v
Context(...)
    |
    +-- 非 simple task 时 Context.prepare()
    |
    v
准备 agents / plan
    |
    v
omnify(...)
    |
    v
OmniAgent.run(...)
```

如果请求带了 `plan`，但没有 `agents` 或 `agent_ids`，代码直接报错：计划必须有执行它的 Agent。

## 5. Memory：回答前检索，回答后更新

### 5.1 回答前检索

`prepare_chat` 只有在 `memory=True` 且同时存在 `gpt_id`、`user_id` 时才会启用 Memory：

```text
                  当前问题
                     |
          +----------+----------+
          |                     |
          v                     v
   User Memory             GPT Memory
   agent_id=gpt_id         agent_id=gpt_id
   user_id=user_id         user_id=gpt_id + "_" + user_id
          |                     |
          +----------+----------+
                     v
              mem0 Memory.search
                     |
                     v
              Qdrant collection: mem0
```

两次搜索通过 `asyncio.gather` 并行执行。单个搜索失败时，该侧被替换为空列表，另一侧仍可以继续使用。

这两类 Memory 的语义不同：

- User Memory：以 `user_id` 隔离，当前实现把用户问题作为待记忆内容。
- GPT Memory：以 `gpt_id_user_id` 作为 session 维度，并给 mem0 一个“只提取助手自身事实”的自定义 prompt。

在 Agent 路径里，Memory 查询结果不会直接修改 `messages`，而是保存在 `user_memory`、`gpt_memory`，供最终 fallback prompt 使用。

### 5.2 回答后更新

答案确定后，若 Memory 条件仍满足，则并行写入两份记录：

```text
当前问题 + 最终回答
          |
          +--> User Memory: 只写入 query
          |
          +--> GPT Memory: 写入
               User: query
               Assistant: response
```

写入失败只记录 warning，不让已经生成的回答失败。这体现了 Memory 在此实现中属于增强能力，而不是回答可用性的硬依赖。

需要特别注意：快路径在 `streaming_chat` 的最前面直接 return，因此不会执行前置 Memory search，也不会执行后置 Memory update。

## 6. Context：把分散信息组装成 Agent 可用上下文

复杂路径会创建：

```text
Context
├── AppContext
├── UserContext
├── SessionContext(messages)
├── CurrentContext
├── RunContext
└── extra_context
```

`Context.prepare()` 的调用关系如下：

```text
Context.prepare()
        |
        +--> get_domain()
        |       |
        |       +--> LLM 从 Domain 枚举中选择领域
        |
        +--> SessionContext.prepare(domain)
        |       |
        |       +--> NLU(last_message, chat_history, domain)
        |       +--> 历史消息摘要
        |       +--> 最近消息和长度限制
        |
        +--> AppContext.prepare(session, domain)
                |
                +--> domain knowledge search
                +--> app knowledge search
```

`AppContext.prepare` 的两类知识查询也是并行的，并将结果按 `(title, content, author)` 去重后放进上下文。最终 `context.xml()` 会把当前时间、应用、用户、会话、运行结果和额外上下文组合成 XML 风格的文本，供 Agent prompt 使用。

`UserContext.prepare()` 和 `RunContext.prepare()` 当前为空实现，但它们保留了未来扩展点。

## 7. OmniAgent：编排入口

`omnify` 根据传入的 Agent、团队、计划和 `omni` 标志选择运行形态：

```text
omnify
  |
  +-- 没有 agents
  |     +--> 创建 OmniAgent leader
  |
  +-- 一个 agent
  |     +--> 包装成 OmniAgent
  |
  +-- 多个 agents
  |     +--> 包装成带 team 的 OmniAgent
  |
  +-- omni=True
        +--> 注入 dispatch / team / tool 等内置能力
```

`OmniAgent.run` 先尝试预取 Agent 和 App 的目录信息，然后把以下内容拼入 `TASK_TEMPLATE`：

```text
<context>...</context>
<agent_apps>...</agent_apps>
User input: <rephrased latest message>
```

默认 `omni=True` 时，实际执行入口是：

```text
OmniAgent.run
    -> single_agent_loop
       -> build_agent(dynamic_toolset=True)
          -> ReActAgent
             -> agent.run_stream(task)
```

OmniAgent 的系统提示词要求它先判断策略，再决定是直接回答、调用工具、调度现有 Agent、创建团队，还是综合多个结果。

## 8. 工具发现和 MCP 执行

这部分要拆成“检索工具”和“真正调用工具”两段。工具检索本身不会执行工具。

### 8.1 工具检索：自然语言 -> 工具候选

当 `dynamic_toolset=True` 时，`ReActAgent` 会额外注入两个函数工具：

```text
get_avaliable_tools(queries, limit)
apply_selected_tools(tools)
```

实际检索流程：

```text
ReActAgent 判断当前工具不够
              |
              v
get_avaliable_tools(queries, limit)
              |
              v
search_tools(query, queries, rerank=False)
              |
              v
batch_search_tools
              |
              | POST {SETTINGS.tool.url}/api/v1/tool/query/batch
              | 多个自然语言 query 一起提交
              v
工具服务返回每个 query 的候选和 score
              |
              +-- 过滤 inactive tool
              +-- 合并多个 query 的候选
              +-- 统计出现次数和平均 score
              +-- 计算 query / tool description 的 embedding 相关度
              +-- 按 rank 选出候选
              v
format_tools(candidates)
              |
              v
返回给 ReActAgent，仍然只是工具描述
```

当前 Agent 动态检索使用的是工具服务的 `/api/v1/tool/query/batch`，而不是在 Chat API 进程内直接访问 Qdrant。向量化和 Qdrant 的具体实现位于工具服务边界之后。

代码中还存在 `get_omni_mcp_tools` 辅助函数，它会生成 8 个搜索 query，并再让 LLM 从候选工具中选择；但当前 checkout 中没有发现它被 `OmniAgent.run` 或 `single_agent_loop` 调用。因此阅读实际链路时，应以 `ReActAgent.get_avaliable_tools -> search_tools` 为准，不应把未被调用的 helper 画成必经路径。

### 8.2 工具加载：工具 ID -> 可执行 FunctionTool

Agent 根据候选描述选择工具 ID 后调用 `apply_selected_tools`：

```text
apply_selected_tools(tool_ids)
              |
              v
load_tools(tool_ids, agent_name, task_id)
              |
              +--> 校验 tool id 长度
              |
              +--> 并行获取工具信息
              |       POST /api/v1/tool/query/info2
              |
              +--> 跳过 async tool
              |
              +--> 按 mcp_id 分组
              |
              +--> 连接每个 MCP SSE server
              |       initialize()
              |       list_tools()
              |
              +--> 为选中的工具创建 SseAdapter
              |
              +--> 包装为 FunctionTool
              |
              +--> 尝试补充工具示例
              v
       self.add_tools(selected)
```

这里发生了一次重要的“描述到能力”的转换：

```text
McpTool metadata
       |
       v
FunctionTool(name, description, input_model, invoke)
       |
       v
LLM 可以发起的 function/tool call
```

### 8.3 工具真正执行

当模型发出工具调用后，执行链路大致是：

```text
Agent framework
       |
       v
FunctionTool.invoke(kwargs)
       |
       v
SseAdapter.run(kwargs)
       |
       v
MCP SSE session
       |
       +--> initialize
       +--> call_tool(name, arguments)
       v
MCP Server
       |
       v
structuredContent / content / isError
       |
       v
返回 Agent loop，继续推理或结束任务
```

如果 MCP 调用出错，适配器会把结果标记为 error，Agent framework 可以将错误作为当前循环的反馈；OmniAgent 的系统提示词要求分析参数、重试或寻找替代工具。

## 9. Agent 事件如何变成 SSE

Agent framework 的原始事件可能包括：

```text
ToolCallRequestEvent
ToolCallExecutionEvent
ThoughtEvent
普通 Agent message
Response
TaskResult
Stats
```

`streaming_handle_stream` 会把原始事件解析成 `AgentLog`、`Response`、`TaskResult` 和统计信息。`single_agent_loop` 再映射为 Chat API chunk：

```text
原始 Agent event
       |
       v
streaming_handle_stream
       |
       +-- AgentLog
       +-- Response / TaskResult
       v
single_agent_loop
       |
       +-- LogChunk(scope=agent, log=AgentLog)
       +-- ContentChunk(content=agent result)
       v
streaming_chat
       |
       +-- 过滤 user / task terminator 日志
       +-- 合并 sub-task log queue
       +-- 检测回答语言
       +-- 必要时翻译
       v
chunk.model_dump_json()
       |
       v
EventSourceResponse(media_type="text/event-stream")
```

### 典型事件类型

客户端至少应该按 `type` 区分两类事件：

```json
{
  "type": "log",
  "data": {
    "scope": "system",
    "log": {
      "header": "Preparing agents and tools",
      "content": "Finding specialized agents and tools..."
    }
  }
}
```

```json
{
  "type": "log",
  "data": {
    "scope": "agent",
    "log": {
      "source": "OmniAgent",
      "content": {
        "tool_calls": [
          {"name": "search_weather", "args": {"city": "Tokyo"}}
        ]
      }
    }
  }
}
```

```json
{
  "type": "content",
  "data": {
    "content": "东京今天是晴天。"
  }
}
```

在 Agent 路径中，`streaming_chat` 收到第一个 `ContentChunk` 后，会把它作为 `agent_context`，停止等待更多 Agent 内容，并继续后处理。若没有得到 `ContentChunk`，则使用 `format_query(...)` 组装上下文、Memory 和 Agent 结果，再走一次普通 `_chat` 作为 fallback。

## 10. 翻译发生在哪里

入口会基于用户问题调用 `setup_translator`：

```text
用户问题
   |
   v
检测输入语言和期望输出语言
   |
   +-- 相同语言或置信度低 -> translator=None
   |
   +-- 不同语言且置信度足够 -> 创建 StreamingTranslator
```

Agent 返回 `ContentChunk` 后，`streaming_chat` 再检测回答语言：

```text
Agent content
   |
   v
detect_response_language
   |
   +-- 不需要翻译 -> 原文
   |
   +-- 需要翻译 -> translator.translate_text(content)
   v
agent_context / final response
```

当前 Agent 结果在 `streaming_chat` 中是按 `ContentChunk` 处理的，不是把一个连续文本流交给 `translate_streaming` 做句子级缓冲。因此课程实现“真正逐 token 翻译”时，需要重新设计 chunk 生命周期和翻译缓冲区。

## 11. 一次复杂请求的时序图

```text
Client        FastAPI        auto          Memory       Context       Agent       Tool Service     MCP Server
  |              |             |              |            |            |              |               |
  |-- POST ------>|             |              |            |            |              |               |
  |              |-- status -->|              |            |            |              |               |
  |              |             |-- classify -> Qdrant      |            |              |               |
  |              |             |-- prepare_chat           |            |              |               |
  |              |             |-------------->|            |            |              |               |
  |              |             |<--------------|            |            |              |               |
  |              |             |--------------------------->|            |              |               |
  |              |             |                            |-- NLU ---> LLM            |               |
  |              |             |                            |-- knowledge search ------>|               |
  |              |             |                            |<-------------------------|               |
  |              |             |-- omnify --------------------------------->|              |               |
  |              |             |                                             |-- search -->|               |
  |              |             |                                             |<------------|               |
  |              |             |                                             |-- tool info>|               |
  |              |             |                                             |             |-- MCP list --->|
  |              |             |                                             |             |<---------------|
  |              |             |                                             |-- call tool|-------------->|
  |              |             |                                             |<-----------|<---------------|
  |<-------------|<------------|-- LogChunk / ContentChunk ------------------|              |               |
  |              |             |-- update_memory ---------------------------->|              |               |
  |<-------------|<------------|-- final ContentChunk                         |              |               |
```

这张图里有两种不同的“流”：

- 外部流：FastAPI 到客户端的 SSE。
- 内部流：Agent framework 到 `streaming_chat` 的 Agent event stream。

两者不是同一个协议，`streaming_chat` 是适配层。

## 12. 开发时应该怎样拆模块

从这个案例可以抽象出一个可复用的 Chat Agent 结构：

```text
Chat API
├── Request normalization
├── Early routing / cheap classifier
├── Context builder
├── Memory provider
├── Agent runtime
│   ├── planner / dispatcher
│   ├── tool selector
│   ├── tool loader
│   └── tool executor
├── Response post-processing
│   ├── translation
│   ├── fallback synthesis
│   └── memory write-back
└── Stream adapter
```

其中最值得保留的边界是：

1. **工具检索不等于工具执行**：检索只返回候选和描述，加载后才形成可调用工具，模型发起调用后才执行。
2. **Memory 不是聊天历史**：历史用于当前会话上下文，Memory 是跨会话检索的事实或偏好。
3. **内部 Agent event 不等于外部 SSE**：需要一个稳定的 chunk schema 把内部事件映射给客户端。
4. **快路径必须显式定义跳过什么**：低延迟来自跳过 NLU、Memory、工具和上下文准备，而不是来自某个神奇的“更快 Agent”。
5. **请求字段要追踪到消费者**：字段是否有效，应沿着 route -> service -> runtime 逐层确认。

## 13. 阅读源码的推荐顺序

```text
1. src/fastestai/api/routers/chat.py
   - ChatRequestV2
   - streaming_chat_v2

2. src/fastestai/chat/auto.py
   - streaming_chat
   - fast_simple_chat
   - prepare_chat

3. src/fastestai/tools/context/__init__.py
   - Context.prepare
   - Context.xml

4. src/fastestai/chat/omni.py
   - omnify
   - OmniAgent.run

5. src/fastestai/agents/utils.py
   - single_agent_loop
   - streaming_handle_stream
   - prepare_tools_for_agent

6. src/fastestai/agents/react.py
   - get_avaliable_tools
   - apply_selected_tools

7. src/fastestai/agents/team/plan/tools.py
   - generate_tool_search_queries
   - search_tools

8. src/fastestai/tools/tool_adapter/mcp/omni.py
   - batch_search_tools
   - load_tools
   - MCP server adapter

9. src/fastestai/message.py
   - ContentChunk
   - LogChunk
   - SystemLog
```

## 14. 练习题

### 练习一：画出快路径

给定：

```json
{
  "messages": [{"role": "user", "content": "你好", "timestamp": 1}],
  "omni": true,
  "memory": true
}
```

回答：为什么即使 `memory=true`，也可能没有 Memory search？

答案：因为入口的相似度分类可能把问题识别为 simple task，`auto.streaming_chat` 直接进入 `fast_simple_chat` 并 return，前后置 Memory 都被跳过。

### 练习二：工具搜索和调用

回答：`get_avaliable_tools` 返回的结果为什么还不能直接执行？

答案：它返回的是格式化后的工具描述。Agent 需要选出工具 ID，再通过 `apply_selected_tools -> load_tools` 获取工具信息、连接 MCP SSE server、创建 `FunctionTool`，之后模型才能发出真正的 tool call。

### 练习三：定位一次请求

回答：一次任务为什么应该优先用 `task_id` 查日志，而不是只查 `user_id`？

答案：一个用户可以并发发起多个任务，`task_id` 能把请求级 audit、Agent log、sub-task queue 和 MCP 工具调用关联到同一次执行。

## 15. 源码索引

以下路径相对于 `fastestai-api`：

| 关注点 | 文件 |
| --- | --- |
| v2 请求和 SSE 入口 | `src/fastestai/api/routers/chat.py:78-95, 197-289` |
| 快路径和主编排 | `src/fastestai/chat/auto.py:87-182, 224-253, 318-533` |
| Context、NLU、领域和知识 | `src/fastestai/tools/context/__init__.py:24-171, 206-283, 401-529` |
| OmniAgent 和策略 | `src/fastestai/chat/omni.py:472-542, 607-823` |
| Agent stream 转换 | `src/fastestai/agents/utils.py:238-378, 787-822` |
| 动态工具能力 | `src/fastestai/agents/react.py:42-120` |
| 工具检索和候选排序 | `src/fastestai/agents/team/plan/tools.py:56-96, 349-496` |
| MCP 工具加载和适配 | `src/fastestai/tools/tool_adapter/mcp/omni.py:260-377, 380-693` |
| Memory 读写 | `src/fastestai/chat/__init__.py:87-182, 221-246` |
| mem0 + Qdrant 封装 | `src/fastestai/memory/memory.py:17-168` |
| 对外 chunk schema | `src/fastestai/message.py:9-141` |
| LLM 普通/流式调用 | `src/fastestai/core/llm/__init__.py:190-240, 599-690` |

