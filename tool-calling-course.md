# 第五步：学习工具调用与函数调用

本章学习如何让 LLM 调用函数、API、插件和 MCP 工具，并把一个“能调用”的 demo 做成生产上可控的 Agent 能力。

本文使用两个真实项目作为案例：

- `fastestai-api`：Agent、LLM function calling、结构化输出、MCP 工具加载和执行。
- `omnimcp-be`：MCP Server 管理、工具元数据、工具知识索引、向量检索、缓存、状态和权限配置。

源码路径：

```text
/Users/dengwei/work/ai/maybeai-uni/fastestai-api
/Users/dengwei/work/ai/maybeai-uni/omnimcp-be
```

## 1. 正确的心智模型

LLM 不会直接执行 Python 函数。它只会根据工具描述生成一个结构化的调用请求，真正执行函数的是你的应用程序。

```text
用户意图
   |
   v
LLM + 工具描述
   |
   | 生成 function/tool call
   v
应用运行时
   |
   | 校验参数、鉴权、限流、审计
   v
真实函数 / HTTP API / MCP Server
   |
   v
工具结果
   |
   v
LLM 继续推理或生成最终答案
```

最常见的错误是把下面两件事混在一起：

```text
Function calling = LLM 选择函数并生成参数
Tool execution   = 应用真正执行函数并处理结果
```

工具调用的安全边界必须放在应用运行时。不能因为模型生成了合法 JSON，就直接执行其中的副作用操作。

## 2. 最小例子：调用本地函数

先定义一个天气函数。函数本身不需要知道 LLM 的存在。

```python
from typing import Any


def get_weather(city: str, unit: str = "celsius") -> dict[str, Any]:
    # 教学示例。生产环境这里应调用真实天气 API。
    return {
        "city": city,
        "temperature": 24,
        "unit": unit,
        "condition": "sunny",
    }
```

再把函数描述给模型：

```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name, for example Tokyo",
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                    },
                },
                "required": ["city"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }
]
```

模型返回的内容不是天气，而可能是：

```json
{
  "tool_calls": [
    {
      "id": "call_123",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"city\":\"Tokyo\",\"unit\":\"celsius\"}"
      }
    }
  ]
}
```

注意 `arguments` 常常是 JSON 字符串，而不是已经解析好的对象。应用要自己解析和校验。

## 3. 完整的 function calling loop

一次完整对话通常至少需要两次 LLM 请求：第一次决定调用什么函数，第二次根据函数结果组织答案。

```text
+--------+
| User   |
+---+----+
    |
    v
+---+-------------------------+
| LLM request                 |
| messages + tool definitions |
+---+-------------------------+
    |
    v
+---+--------------------------+
| finish_reason=tool_calls?   |
+---+--------------------------+
    | yes                         no
    |                            |
    v                            v
+---+---------------------+   +--+----------------+
| Parse call              |   | Return text      |
| Validate name/arguments |   | to user          |
+---+---------------------+   +-------------------+
    |
    v
+---+-----------------------------+
| auth / approval / rate / timeout|
+---+-----------------------------+
    |
    +---- denied -------------------------> safe error
    |
    v
+---+----------------+
| Execute tool       |
| local/API/MCP      |
+---+----------------+
    |
    v
+---+-----------------------------+
| Append tool result to messages  |
+---+-----------------------------+
    |
    v
+---+-------------------------+
| LLM request again           |
| messages + tool result      |
+---+-------------------------+
    |
    v
 final answer
```

一个简化的 Python loop：

```python
import json
from openai import AsyncOpenAI


FUNCTIONS = {"get_weather": get_weather}


async def chat_with_tools(client: AsyncOpenAI, question: str) -> str:
    messages = [{"role": "user", "content": question}]

    for _ in range(6):
        response = await client.chat.completions.create(
            model="gpt-5.2",
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        message = response.choices[0].message

        if not message.tool_calls:
            return message.content or ""

        messages.append(message)
        for call in message.tool_calls:
            function = FUNCTIONS.get(call.function.name)
            try:
                arguments = json.loads(call.function.arguments)
                result = await function(**arguments) if function else {
                    "ok": False,
                    "error": "unknown_tool",
                }
            except Exception as error:
                result = {"ok": False, "error": str(error)}

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    raise RuntimeError("tool loop exceeded maximum rounds")
```

生产代码必须补上参数模型校验、工具白名单、用户权限、幂等键、超时、重试规则、审计和循环上限。`for _ in range(6)` 防止模型在工具失败时无限调用。

## 4. Function calling 和结构化输出

这两个能力都返回结构化数据，但用途不同。

| 能力 | 目的 | 典型结果 |
| --- | --- | --- |
| Function calling | 让模型请求应用执行动作 | `get_weather(city="Tokyo")` |
| Structured output | 让模型返回符合 schema 的数据 | `{"intent": "weather", "city": "Tokyo"}` |

function calling 的函数是可执行能力。structured output 的 schema 是数据契约。

```text
用户输入
   |
   v
结构化意图识别
   |
   +--> intent=answer_question -> 直接回答
   |
   +--> intent=call_tool -> 工具候选 -> function calling
```

复杂系统常常两者都用：前者控制路由，后者控制执行参数。

## 5. 如何让 LLM 返回结构化 JSON

### 5.1 四层约束

不要只在 prompt 中写“请返回 JSON”。应同时做四层约束：

```text
1. Prompt: 说明业务含义和禁止项
2. API: 使用 response_format=json_schema 或 function parameters
3. Runtime: json.loads / model_validate_json
4. Business: 校验字段之间的业务关系
```

在 `fastestai-api` 中，`llm_parse_model` 使用 `response_format`：

```python
response_format={
    "type": "json_schema",
    "json_schema": {
        "name": response_model.__name__,
        "schema": response_model.model_json_schema(),
    },
}
```

收到文本后再执行：

```python
return response_model.model_validate_json(resp)
```

源码：

```text
fastestai-api/src/fastestai/core/llm/__init__.py:808-833
```

provider 的 JSON 约束不能替代服务端验证。模型、网关或 provider 仍可能返回空内容、错误 schema 或截断数据。

### 5.2 用 Pydantic 生成 schema

```python
from typing import Literal
from pydantic import BaseModel, Field


class ToolIntent(BaseModel):
    intent: Literal["weather", "search", "none"]
    city: str | None = Field(default=None, max_length=100)
    confidence: float = Field(ge=0, le=1)


schema = ToolIntent.model_json_schema()
```

推荐的 schema 规则：

- 工具名使用稳定、短、可读的标识符。
- 字段写清 description、类型、范围、枚举和单位。
- 对开放文本设置长度上限。
- 对关键字段设置 required。
- 不接受未声明字段，使用 `additionalProperties: false`。
- 需要区分“没有值”和“模型忘了填值”，显式使用 nullable 字段。
- 对日期、金额、时区、ID 使用明确格式，不要只写 string。
- schema 版本要可追踪，变更要有兼容策略。

### 5.3 `strict` 不等于业务正确

```json
{
  "city": "Tokyo",
  "unit": "celsius"
}
```

可能满足 JSON schema，但仍然业务错误：

- 城市不存在。
- 用户没有授权访问该城市的私有数据。
- 参数触发了高风险操作。

因此至少要有两种验证：

```text
结构验证: 类型、必填项、枚举、长度、格式
业务验证: 资源存在、用户可访问、状态允许、金额/范围合理
```

### 5.4 `fastestai-api` 的结构化输出范例

`structure_output` 采用一个名为 `structureOutput` 的函数工具，强制模型调用：

```python
tools = [{
    "type": "function",
    "function": {
        "name": "structureOutput",
        "description": "Save the final structure output",
        "parameters": response_model.model_json_schema(),
        "strict": True,
    },
}]

tool_choice = "required"
```

然后解析 function call 的 arguments：

```python
return response_model.model_validate_json(
    tool_calls[0].function.arguments
)
```

如果 Pydantic 校验失败，当前实现会把错误和上一次输出发回模型，再尝试一次修正。这是“模型修复”和“服务端最终裁决”结合的例子。

源码：

```text
fastestai-api/src/fastestai/core/llm/__init__.py:836-945
```

其中有一个生产注意点：`llm_parse_model` 和 `structure_output` 是两条不同实现。前者使用 `response_format`，后者使用强制 function call。接入新模型时要分别验证 provider 对两种接口的支持。

## 6. 工具描述应该包含什么

模型能否正确选择工具，取决于工具描述是否能表达“什么时候用”和“什么时候不要用”。一个生产工具至少需要：

```text
ToolDefinition
├── name
├── description
├── input_schema
├── output_schema
├── use_case
├── limitation
├── failure_cases
├── auth / config requirements
├── need_approve
├── timeout / async capability
├── idempotency behavior
└── version
```

`omnimcp-be` 的 `ToolMetaDTO` 和 `ToolMeta` 保存了这些信息的一部分，包括：

```text
input_schema
output_schema
samples
status
need_approve
app_scope
use_case
limitation
failure_cases
dependent_tools
alternative_tools
```

源码：

```text
omnimcp-be/src/omnimcp_be/mcp/tool/models.py:134-217
```

描述工具时不要只写：

```text
"Search data"
```

应该写成：

```text
Search the user's connected Google Sheet rows by natural-language criteria.
Use this for reading existing rows. Do not use it to update or delete rows.
Requires the user to have connected a Google account. Results may be stale for
up to 60 seconds. The returned row IDs can be passed to the update tool.
```

工具描述本质上是模型的 API 文档，也是工具路由的训练数据。描述越模糊，工具选择越不稳定。

## 7. 工具元数据到可执行工具

真实 Agent 中，工具一般经历四个状态：

```text
工具元数据
   |
   v
检索候选
   |
   v
加载 schema 和连接信息
   |
   v
FunctionTool / MCP adapter
   |
   v
可执行工具
```

在 `fastestai-api` 中，`ReActAgent` 会把工具包装成 `FunctionTool`。动态工具模式下还会增加：

```text
get_avaliable_tools(queries, limit)
apply_selected_tools(tools)
```

源码：

```text
fastestai-api/src/fastestai/agents/react.py:42-120
```

### 7.1 工具检索

`get_avaliable_tools` 调用 `search_tools`，再通过 `omnimcp-be` 批量查询候选：

```text
用户问题
   |
   v
Agent 生成一个或多个检索 query
   |
   v
POST /api/v1/tool/query/batch
   |
   v
omnimcp-be ToolSelector
   |
   +--> tool ID / pattern search
   +--> private_knowledge 向量搜索
   +--> 多空间并发搜索
   +--> Redis cache-first 查询
   +--> inactive / disabled 过滤
   +--> 去重和排序
   v
工具候选及其 metadata
```

工具服务 router 的 `/query` 和 `/query/batch` 定义在：

```text
omnimcp-be/src/omnimcp_be/mcp/tool/router.py:162-301
```

### 7.2 `omnimcp-be` 的批量检索

`ToolSelector.query_tool_batch` 的设计重点是一次请求处理多个 query：

```text
queries=[q1, q2, q3]
       |
       +--> tool ID queries
       |
       +--> regular natural-language queries
                    |
                    +--> _batch_cache_search
                    |
                    +--> MultiSpaceQueryService
                            |
                            +--> browserscraper
                            +--> ai_field_template
                            +--> dataframe
                            +--> Others
```

缓存搜索和多空间向量搜索并发执行。`MultiSpaceQueryService` 会对四个空间并发调用 `_batch_private_search`，查询 `private_knowledge` 相关数据，再由 `ToolSelector` 合并结果。

源码：

```text
omnimcp-be/src/omnimcp_be/mcp/tool/tool_selector.py:1394-1682
omnimcp-be/src/omnimcp_be/mcp/tool/multi_space_query_service.py:39-223
```

单个查询的合并关系：

```text
                 +------------------+
                 | User query       |
                 +--------+---------+
                          |
             +------------+------------+
             |                         |
             v                         v
       Redis cache                Vector search
       tool/server data           private_knowledge
             |                         |
             +------------+------------+
                          v
                deduplicate / rank
                          |
                          v
                active + filter check
                          |
                          v
                     ToolMeta
```

`private_knowledge` 搜索结果只是候选，不代表用户已经拥有执行权限。候选结果里可能包含 `need_config_key`、`need_approve`、`active` 等执行前需要处理的元数据。

## 8. 工具索引不是请求时检索

工具系统应该把“工具变化后的索引链路”和“用户请求时的检索链路”分开。

### 8.1 工具变化后的链路

当 MCP Server 的工具列表或 schema 发生变化时，`refresh_tool_list` 会重新连接 MCP Server 并调用 `list_tools()`，保存最新工具列表，然后异步触发 hook：

```text
MCP Server 变化
      |
      v
refresh_tool_list
      |
      +--> 连接 server
      +--> initialize()
      +--> list_tools()
      +--> 保存 MCPTool name/schema
      |
      v
MCPServerHooks.after_server_change
      |
      +--> generate_tool_knowledge_for_servers
      |       |
      |       +--> 生成 use_case / limitation / failure_cases
      |       +--> 生成检索 query 文本
      |
      +--> ToolIndex.index_tool(force_update=True)
              |
              +--> 文本 + payload metadata
              +--> embedding
              +--> Qdrant private_knowledge
              |
              v
      刷新 Redis server/tool metadata cache
```

相关源码：

```text
omnimcp-be/src/omnimcp_be/mcp/server/mcp_server_manager.py:719-789
omnimcp-be/src/omnimcp_be/mcp/server/mcp_hooks.py:14-70
omnimcp-be/src/omnimcp_be/mcp/tool/tool_index.py:510-690, 716-835
```

`ToolIndex` 会为每个工具生成文档，使用 embedding 写入 Qdrant。索引任务有固定次数重试，但这只解决索引过程的瞬时错误，不等于用户请求执行时也可以无限重试。

### 8.2 用户请求时的链路

```text
用户输入
   |
   v
Agent 产生 query / queries
   |
   v
ToolSelector.query_tool_batch
   |
   +--> cache-first metadata
   +--> private vector search
   +--> filter inactive/disabled
   +--> merge and rank
   v
返回 MCPToolMeta
   |
   v
Agent 选择 tool_id
   |
   v
再加载实际工具并执行
```

索引是写侧，检索是读侧。写侧失败应该报警并保留旧索引，读侧失败可以降级到缓存、pattern search 或无工具回答，但不能把旧索引当成永久正确。

## 9. 从工具 ID 到 MCP 调用

在 `fastestai-api` 中，选中的 tool ID 会经过 `load_tools`：

```text
tool_ids
   |
   v
get_tool_info_by_id / batch
   |
   +--> mcp_sse_url
   +--> tool name
   +--> inputSchema
   +--> outputSchema
   +--> need_approve
   v
按 mcp_id 分组
   |
   v
连接 MCP SSE Server
   |
   +--> initialize()
   +--> list_tools()
   +--> 找到目标 tool
   +--> 创建 SseAdapter
   v
FunctionTool.invoke(kwargs)
```

真正调用时：

```text
LLM function call
       |
       v
FunctionTool
       |
       v
SseAdapter.run(args)
       |
       v
MCP Client Session
       |
       v
session.call_tool(name, arguments)
       |
       v
structuredContent / content / isError
```

源码：

```text
fastestai-api/src/fastestai/tools/tool_adapter/mcp/omni.py:460-693
fastestai-api/src/fastestai/tools/tool_adapter/mcp/base.py:139-204
```

这里的 `McpTool` 是描述，`FunctionTool` 才是 Agent framework 能够调用的对象。两者之间需要 adapter 层，因为远程 MCP 的连接、认证、超时和返回格式都不是模型能处理的事情。

## 10. 一个基础 Agent 如何选择正确工具

不要让模型在几千个工具里直接猜。推荐分层：

```text
用户问题
   |
   v
意图提取
   |
   +--> 任务类型
   +--> 资源 / 实体
   +--> 动作: read / write / delete
   +--> 时间范围
   +--> 权限需求
   v
候选召回
   |
   +--> exact tool ID
   +--> keyword/pattern
   +--> vector search
   +--> featured/frequently-used cache
   v
权限和状态过滤
   |
   +--> server active?
   +--> tool active?
   +--> user configured?
   +--> app scope allowed?
   +--> approval required?
   v
候选排序
   |
   +--> intent fit
   +--> schema fit
   +--> freshness
   +--> reliability
   +--> cost / latency
   v
LLM 选择一个或多个工具
   |
   v
参数校验和执行策略
```

基础版本可以只保留三个工具：

```python
candidate_tools = [weather_tool, search_tool, calculator_tool]

selected = choose_by_intent(
    question="东京今天的天气如何？",
    candidates=candidate_tools,
)
```

`choose_by_intent` 不能只看名字，还要比较：

```text
问题: “东京今天的天气如何？”

weather_tool:
  capability = current weather
  input = city
  freshness = real-time
  side_effect = none
  match = high

search_tool:
  capability = web search
  input = query
  freshness = variable
  side_effect = none
  match = medium

calculator_tool:
  capability = arithmetic
  input = expression
  match = none
```

### 10.1 基础选择器伪代码

```python
async def select_tool(question: str, user: User) -> Tool | None:
    candidates = await retrieve_tools(question, limit=20)
    candidates = [tool for tool in candidates if tool.active]
    candidates = [
        tool for tool in candidates
        if await is_authorized(user, tool)
    ]

    if not candidates:
        return None

    ranked = await rank_tools(question, candidates)
    top = ranked[0]
    if top.need_approve:
        return await request_user_approval(top)
    return top
```

生产版本应把 `is_authorized` 放在候选召回后和最终执行前各做一次。原因是元数据可能过期，用户权限也可能在两次检查之间变化。

## 11. 权限怎么决定

权限不能只由“模型想调用什么”决定。一个实用的决策矩阵至少考虑五个维度：

```text
风险 = side_effect
     + data_sensitivity
     + user_scope
     + financial_impact
     + reversibility
```

### 11.1 建议的权限等级

| 等级 | 示例 | 默认策略 |
| --- | --- | --- |
| L0 | 计算、格式转换、公开知识查询 | 自动执行 |
| L1 | 读取用户已授权的私有数据 | 登录和资源授权后自动执行 |
| L2 | 发送消息、创建任务、写入文档 | 明确用户意图，必要时确认 |
| L3 | 删除、支付、发布、修改权限 | 每次确认，强幂等和审计 |
| L4 | 管理员操作、批量删除、跨租户访问 | 禁止由普通 Agent 直接调用 |

判断一个工具是否需要确认，可以问：

```text
1. 是否产生外部副作用？
2. 是否写入、删除或发送数据？
3. 是否涉及金钱、账号、权限或隐私？
4. 操作是否容易撤销？
5. 用户是否已经明确表达了最终动作？
6. 是否允许批量执行？
```

### 11.2 `omnimcp-be` 中的权限相关信息

`MCPToolMeta` 和 `ToolMetaDTO` 会携带：

```text
need_approve
need_config_key
app_scope
mcp_activestatus
tool.status
```

`need_config_key` 表示工具所在 Server 需要用户或公开配置。`need_approve` 表示调用前需要确认。它们是工具治理的输入，但最终执行层仍要验证：

```text
当前 user_id
当前 app
当前 tool_id
当前参数
当前 server 状态
当前授权配置
```

### 11.3 public/private 和认证

`omnimcp-be` 将用户 Server 配置分为 `private` 和 `public`，并根据 `user_id` 查找用户私有配置、外部授权配置或公开配置。

```text
请求 user_id
    |
    v
优先读取 private user config
    |
    +--> 没有 -> external auth config
    |
    +--> 仍没有 -> public config
    |
    v
检查 server.config_fields 是否全部满足
```

源码：

```text
omnimcp-be/src/omnimcp_be/mcp/server/user_server_config.py:153-174, 218-313
omnimcp-be/src/omnimcp_be/mcp/models.py:188-217
```

### 11.4 一个真实的安全边界

`fastestai-api` 的直接工具接口支持无会话执行，但要求 `run-task-token` 与服务端 token 使用常量时间比较：

```python
if not request.requires_chat_session:
    expected_token = SETTINGS.run_task_token
    if not (
        expected_token
        and run_task_token
        and hmac.compare_digest(run_task_token, expected_token)
    ):
        raise HTTPException(status_code=403)
```

源码：

```text
fastestai-api/src/fastestai/tools/call_tool/router.py:297-321
```

这类 internal token 只能用于服务间调用，不能当作面向用户的通用授权方案。面向用户的调用应使用用户身份、资源权限和工具 scope 做授权。

## 12. 健康检查和可用性

工具系统至少需要三种健康状态：

```text
Process health       进程是否活着
Dependency readiness  Redis/Mongo/Qdrant/LLM 是否可用
Tool health          某个 MCP Server / tool 是否可调用
```

```text
GET /health
   |
   +--> liveness: API 进程可以响应

readiness
   |
   +--> Redis ping
   +--> Mongo ping
   +--> Qdrant collection 可访问
   +--> LLM provider 配置存在

per-tool health
   |
   +--> MCP Server connect
   +--> initialize
   +--> list_tools
   +--> 可选的安全 read-only probe
```

`fastestai-api` 提供简单的 `/health`，返回 `{"message": "ok"}`。这只能说明 HTTP 进程还活着，不能说明每一个 MCP Server 都正常。

`omnimcp-be` 维护 Server active、internal/external active、tool metadata status 和工具状态统计。检索时会过滤 disabled server 和 inactive tool，避免把明显不可用的工具交给 Agent。

生产健康检查要注意：

- 不要在 health probe 中调用有副作用的工具。
- 不要因为一个低优先级工具挂掉，就让整个 API readiness 失败。
- 区分“暂时不可用”和“配置不完整”。
- 记录最近成功时间、失败次数、延迟和错误类型。
- 对长任务使用异步状态，不要让健康检查等待业务任务完成。

## 13. 重试：什么时候可以重试

重试不是“失败就再来一次”。先判断错误是否具有瞬时性，以及重复执行是否安全。

| 错误 | 通常重试？ | 条件 |
| --- | --- | --- |
| 连接超时、DNS 短暂失败 | 是 | 指数退避，有上限 |
| HTTP 408、429 | 是 | 读取 `Retry-After`，遵守限流 |
| HTTP 500、502、503、504 | 是 | provider 明确是暂时故障 |
| 认证失败 401 | 否 | 刷新 token 后只允许受控重试 |
| 权限失败 403 | 否 | 应重新授权，不要重复请求 |
| 参数错误 400/422 | 否 | 修正参数或换工具 |
| schema 校验失败 | 最多一次 | 把具体错误反馈给模型再校验 |
| 工具业务拒绝 | 通常否 | 除非错误明确要求等待或刷新 |
| MCP Server 不存在 | 否 | 更新元数据或选择替代工具 |

### 13.1 读操作和写操作

```text
read/search
   +--> 大多数 transient error 可以重试

create/send/update/delete/payment
   +--> 只有有幂等键、状态查询或 provider 保证幂等时才重试
```

例如“发送邮件”第一次请求可能已经成功，但响应在网络中丢失。直接重试可能发送两封邮件。正确做法是：

```text
生成 idempotency_key
       |
       v
发送请求带 key
       |
       +--> 超时
       |
       v
用同一个 key 查询或重试
       |
       v
确保只产生一次副作用
```

### 13.2 两个项目里的重试例子

`omnimcp-be` 的工具索引中，`request_index_api` 使用固定次数、固定间隔的 retry。索引写入失败不会把用户正在执行的工具重复运行，因此风险较低。

`fastestai-api` 的 MCP adapter 对单次工具调用设置 timeout，并把错误封装为 `McpToolCallResult(is_error=True)`。这让 Agent 可以看到工具失败并决定修正或换工具，但不应该由 Agent 自由决定无限重试。

课程实现时应给每一次工具调用增加：

```text
max_attempts
deadline
retryable_errors
idempotency_key
attempt_number
```

## 14. 缓存：什么时候应该缓存

缓存最适合放在工具目录和工具描述层，而不是默认放在所有工具结果上。

### 14.1 适合缓存的内容

```text
适合缓存
├── MCP Server metadata
├── Tool metadata
├── tool schema
├── featured/frequently-used tool list
├── 工具搜索候选
├── 静态文档和工具知识
└── 短时间的只读结果
```

`omnimcp-be` 的 `MCPSimpleToolCacheManager` 使用 Redis 缓存 Server、tool metadata、disabled server 和 inactive tool keys，默认 TTL 是一个月；Server 变化后会主动刷新对应缓存。

```text
初始化缓存
   |
   +--> all_servers
   +--> all_tool_meta
   +--> disabled_servers
   +--> inactive_tool_meta

MCP Server 更新
   |
   v
刷新该 Server 和它的工具 metadata
```

源码：

```text
omnimcp-be/src/omnimcp_be/mcp/server/mcp_simple_tool_cache_manager.py:23-77, 402-518
```

### 14.2 不要默认缓存的内容

```text
不应默认缓存
├── 发送消息、写入、删除、支付等副作用结果
├── 当前余额、库存、价格、权限状态
├── 包含用户 secret 的请求或结果
├── 未按 user/tenant 隔离的私有数据
├── 一次性 token
└── 依赖当前时间且过期代价高的数据
```

搜索结果是否可缓存，要看问题：

```text
“如何使用 Google Sheets API？” -> 可以缓存较久
“我的账户现在余额多少？”     -> 短 TTL 或不缓存
“发送这封邮件”                -> 不缓存执行结果
```

### 14.3 Cache key 必须隔离用户

```text
cache_key = hash(
    tenant_id,
    user_id,
    app_scope,
    tool_id,
    tool_version,
    normalized_arguments,
    permission_version,
)
```

不要把 access token、refresh token 或完整敏感参数直接放进 key。需要考虑：

- 用户和租户隔离。
- 参数排序和默认值归一化。
- schema/version 变化自动失效。
- 权限变化主动失效。
- Server/tool disable 时主动删除。

### 14.4 stale-while-revalidate

工具元数据适合使用旧值快速返回，同时后台刷新：

```text
请求工具 metadata
       |
       +--> fresh cache -> 立即返回
       |
       +--> stale cache -> 返回旧值 + 后台刷新
       |
       +--> miss -> DB/MCP 查询 -> 写缓存
```

但执行前应再次确认工具 active、权限和配置。缓存只能减少查询延迟，不能成为授权事实源。

## 15. 工具执行返回值

不要只返回一个字符串。推荐统一 envelope：

```json
{
  "ok": true,
  "tool_id": "weather__get_current",
  "request_id": "req_123",
  "data": {"city": "Tokyo", "temperature": 24},
  "error": null,
  "retryable": false,
  "partial": false,
  "next_action": null
}
```

失败也返回同样的结构：

```json
{
  "ok": false,
  "tool_id": "mail__send",
  "request_id": "req_123",
  "data": null,
  "error": {
    "code": "AUTH_REQUIRED",
    "message": "The user must connect an email account."
  },
  "retryable": false,
  "partial": false,
  "next_action": "request_authorization"
}
```

至少区分：

```text
成功
可重试失败
不可重试失败
需要用户授权
需要用户确认
部分成功
已接受但异步处理中
```

如果所有失败都变成普通文本，Agent 很难决定是修参数、重试、换工具还是向用户提问。

## 16. Streaming 场景下的工具调用

工具 Agent 通常有两条流：

```text
内部流: LLM / Agent framework -> tool call -> tool result -> final answer
外部流: API server -> SSE -> client
```

```text
LLM event
   |
   +--> tool_call request
   +--> tool_call result
   +--> reasoning/log
   +--> final content
   v
Stream adapter
   |
   v
SSE JSON chunk
```

`fastestai-api` 用 `MafAgent.run_stream` 读取 Agent framework 的 stream，再转换成 `AgentLog`、`ContentChunk` 和 `TaskResult`。`streaming_handle_stream` 会把 function call 和 function result 转成 Agent 日志。

源码：

```text
fastestai-api/src/fastestai/agents/maf_runtime.py:171-213, 281-307
fastestai-api/src/fastestai/agents/utils.py:238-378, 787-822
```

客户端应当：

- 按 `type` 区分 `log`、`content`、`data`。
- 用 `tool_call_id` 关联调用和结果。
- 不把内部思维内容当成最终答案。
- 处理连接中断、重复 chunk 和结束事件。
- 对工具执行中的敏感参数做脱敏。

## 17. 生产总流程图

```text
                              +----------------+
                              | User request   |
                              +-------+--------+
                                      |
                                      v
                         +------------+-------------+
                         | Normalize and authenticate|
                         +------------+-------------+
                                      |
                                      v
                         +------------+-------------+
                         | Intent classification    |
                         +------------+-------------+
                                      |
                 +--------------------+--------------------+
                 |                                         |
                 v                                         v
        direct answer path                         tool-needed path
                 |                                         |
                 |                            +------------+-------------+
                 |                            | Retrieve candidates     |
                 |                            | exact / keyword / vector|
                 |                            +------------+-------------+
                 |                                         |
                 |                                         v
                 |                            +------------+-------------+
                 |                            | Filter state and access |
                 |                            +------------+-------------+
                 |                                         |
                 |                                         v
                 |                            +------------+-------------+
                 |                            | LLM picks tool + args   |
                 |                            | schema constrained JSON |
                 |                            +------------+-------------+
                 |                                         |
                 |                                         v
                 |                            +------------+-------------+
                 |                            | Validate again          |
                 |                            | auth / approval / quota |
                 |                            +------+------------------+
                 |                                   |
                 |                    +--------------+--------------+
                 |                    |                             |
                 |                    v                             v
                 |             confirmation needed              execute
                 |                    |                             |
                 |                    v                             v
                 |             user approval             timeout / retry policy
                 |                                                  |
                 |                                                  v
                 |                                      local / API / MCP call
                 |                                                  |
                 |                                                  v
                 |                                      result envelope + audit
                 |                                                  |
                 |                                                  v
                 |                                    LLM final synthesis
                 +--------------------------+-----------------------+
                                            |
                                            v
                                  SSE / HTTP response
```

## 18. 从 basic 到 advanced 的课程路线

建议按下面的顺序实现，每一级都能运行和测试，再进入下一级：

```text
Level 1: 本地函数
  一个 get_weather 函数
  手动写 schema
  完成 tool_call -> 执行 -> tool 结果 -> 最终回答

Level 2: HTTP API
  把本地函数换成 HTTP client
  加 timeout、错误 envelope、参数校验和日志

Level 3: 动态选工具
  工具注册表 + 工具元数据
  根据用户意图召回候选工具
  让 LLM 在候选集合中选择并生成参数

Level 4: MCP
  从 MCP Server list_tools 获取 schema
  把 MCP 元数据转换为 FunctionTool
  通过 SSE / MCP call 执行并统一返回值

Level 5: 生产治理
  权限、审批、配置、配额、重试、缓存、审计、观测
  处理版本漂移、并发、长任务、取消和数据隔离
```

在 `fastestai-api` 中，Level 3 对应 `ReActAgent` 的 dynamic toolset：先调用
`get_avaliable_tools` 搜索候选，再调用 `apply_selected_tools` 加载实际工具。
Level 4 对应 `load_tools` 创建 MCP 连接和 `FunctionTool` 包装器。

## 19. 生产中还会遇到的典型问题

### 19.1 选错工具怎么办

不能只看 top-1。保留 top-k 候选，并设置最低分数；候选太接近时，让 Agent
澄清问题或拒绝执行。线上应记录：用户意图、候选列表、最终选择、选择理由、
工具结果和最终结果，才能分析是召回错、排序错还是 schema 描述不清。

### 19.2 参数是合法 JSON，但业务上不合法

JSON Schema 只能保证类型和形状，不能代替业务校验。例如金额可以是数字，
但不能是负数；日期格式正确，也可能早于当前账期。执行前必须再做：

```text
LLM arguments
      |
      v
JSON parse -> Pydantic/schema validation -> business validation
                                      |
                                      v
                              permission / approval
                                      |
                                      v
                                  execute
```

### 19.3 工具结果包含 prompt injection

网页、邮件、文档和第三方 API 的内容都是不可信数据。工具返回值只能作为
`tool` 数据交给模型，不能把其中的“新指令”直接升级为系统指令。建议：

- 明确区分 trusted instruction 和 untrusted tool data。
- 对外部文本做长度限制、字段过滤和敏感信息脱敏。
- 对“发送、删除、付款、修改权限”等动作再次要求用户确认。

### 19.4 工具 schema 发生漂移

MCP Server 更新了参数名，但索引、缓存或 Agent 仍使用旧 schema，会导致持续
失败。为工具保存 `schema_hash` 或版本；发现变化时重新拉取 `list_tools`、
更新索引、刷新缓存，并在短时间内避免重复触发同一失败任务。

### 19.5 同一请求重复调用工具

流式重连、模型重试、网络超时都可能让服务端收到两次请求。读操作通常问题较小，
写操作必须使用幂等键：

```text
request_id / idempotency_key
          |
          v
  +-------+--------+
  | 已成功执行？   |-- yes --> 返回原结果
  +-------+--------+
          |
          no
          v
      执行一次并持久化结果
```

### 19.6 工具结果太大或工具执行太久

结果应有最大字节数、最大行数和字段白名单。大结果先存对象存储，只把摘要、
引用 ID 或分页数据返回给模型。长任务要返回 `task_id`，提供查询、取消和进度
事件，避免占用一次 HTTP 请求直到完成。

### 19.7 成本、速率和并发怎么控制

对每个用户、Agent、工具和 MCP Server 分别设置 timeout、并发上限、速率限制、
每日预算和最大 tool loop 次数。模型可以连续调用工具，但必须有总步数上限，
否则一个错误的计划会无限循环。

### 19.8 如何排查“工具调用成功但用户体验失败”

把一次请求串成同一个 trace：`request_id`、`conversation_id`、`tool_call_id`、
`mcp_id`、`tool_id`、schema 版本、重试次数、缓存命中、耗时、结果大小和错误类型。
日志中不要直接写 API key、用户私密配置或完整敏感 payload。

## 20. 测试应该覆盖什么

至少需要下面四类测试：

| 类型 | 重点 |
| --- | --- |
| Schema | 缺字段、额外字段、错误类型、空值、超长字符串、枚举值 |
| Selection | 同义表达、多个候选、无候选、低分拒绝、工具不可用、权限过滤 |
| Execution | timeout、5xx、429、连接断开、畸形返回、重复请求、部分成功 |
| Security | 未登录、跨用户配置、越权 tool_id、审批绕过、敏感结果泄漏 |

关键用例应固定模型输出或使用 mock，避免测试结果被模型随机性影响。模型本身
另做离线评测：工具选择准确率、参数正确率、无工具时的拒答率和危险操作拦截率。

## 21. 源码阅读顺序

### 21.1 fastestai-api

1. `src/fastestai/core/llm/__init__.py`
   - `llm_parse_model`：JSON Schema structured output。
   - `structure_output`：strict function tool、参数解析和纠错重试。
2. `src/fastestai/agents/react.py`
   - 动态工具搜索和工具加载。
3. `src/fastestai/tools/tool_adapter/mcp/omni.py`
   - MCP server 连接、`list_tools`、MCP call 和 `FunctionTool` 适配。
4. `src/fastestai/tools/call_tool/router.py`
   - 直接工具调用、内部 token 和 HMAC 校验。
5. `src/fastestai/agents/maf_runtime.py`
   - Agent 流式事件和 tool-call 事件如何返回给上层。

### 21.2 omnimcp-be

1. `src/omnimcp_be/mcp/tool/models.py`
   - 工具元数据、输入输出 schema、配置和审批字段。
2. `src/omnimcp_be/mcp/tool/router.py`
   - 单次和批量工具查询 API。
3. `src/omnimcp_be/mcp/tool/tool_selector.py`
   - 精确 ID、模式搜索、向量搜索、缓存补充、过滤和去重。
4. `src/omnimcp_be/mcp/tool/multi_space_query_service.py`
   - 多空间并行检索。
5. `src/omnimcp_be/mcp/server/mcp_server_manager.py` 与 `mcp_hooks.py`
   - MCP 变化、拉取工具列表、刷新缓存和重建索引。
6. `src/omnimcp_be/mcp/tool/tool_index.py`
   - 工具文档、查询文本、Embedding、Qdrant 和索引重试。

## 22. 章节总结

工具调用的核心不是“让模型调用一个函数”，而是建立一个可验证的闭环：

```text
自然语言意图
    -> 工具发现
    -> 候选过滤
    -> schema 选择和参数生成
    -> JSON / 业务 / 权限校验
    -> 可控执行
    -> 结构化结果
    -> 最终回答
```

需要牢记四个区别：

- Function calling 是模型提出调用意图；真正执行仍由应用负责。
- Structured output 是约束模型返回数据；它不等于执行工具。
- 工具检索解决“选哪个工具”；权限系统解决“能不能执行”。
- Retry 解决临时失败；Cache 解决可复用结果；二者都不能掩盖业务错误。

完成本章后，应该能够从零实现一个安全的基础 Agent，并能沿着两个真实项目
定位问题：在 `fastestai-api` 看执行和编排，在 `omnimcp-be` 看工具发现、索引、
缓存与元数据治理。

## 源码索引

| 文件 | 在本章中的用途 |
| --- | --- |
| `fastestai-api/src/fastestai/core/llm/__init__.py` | structured output 和 function calling |
| `fastestai-api/src/fastestai/agents/react.py` | 动态工具发现与选择 |
| `fastestai-api/src/fastestai/tools/tool_adapter/mcp/omni.py` | MCP 工具适配与执行 |
| `fastestai-api/src/fastestai/tools/call_tool/router.py` | 直接调用和内部鉴权 |
| `omnimcp-be/src/omnimcp_be/mcp/tool/models.py` | 工具能力、配置、审批元数据 |
| `omnimcp-be/src/omnimcp_be/mcp/tool/tool_selector.py` | 单次/批量检索、缓存、过滤 |
| `omnimcp-be/src/omnimcp_be/mcp/tool/tool_index.py` | 工具索引和向量化 |
| `omnimcp-be/src/omnimcp_be/mcp/server/mcp_hooks.py` | Server 变化后的索引与缓存链路 |

学习顺序从一个本地函数开始，逐步扩展到远程 API、MCP、工具搜索、权限、重试、缓存和生产治理。
