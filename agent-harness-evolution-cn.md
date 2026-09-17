# 从 Claude Code 到 OpenClaw 与 Hermes Agent：Agent Harness 的演化

## 摘要

Agent 产品正在从“用户输入一次，模型回答一次”，转向“模型可以在真实计算机环境中持续完成任务”。这类系统的关键不再只是模型本身，而是包围模型的一层运行时：它负责组织上下文、提供和调度工具、管理权限、维持会话状态、处理超时与失败、压缩历史、恢复中断任务，并在没有新用户输入时主动唤醒 Agent。

这层运行时可以称为 **Agent Harness**。本文基于当前工作区中的三个代码库进行源码阅读：

| 系统 | 代码库定位 | 主要观察点 |
|---|---|---|
| Claude Code | `claude-code-leak` 源码快照 | 单轮 Agent Loop、工具权限、并发调度、上下文恢复 |
| OpenClaw | `openclaw` | Gateway、可替换 Harness、Heartbeat、隔离会话、消息投递 |
| Hermes Agent | `hermes-agent` | 分阶段 Turn Loop、持久化状态、Memory/Skill、Cron 和租约 |

分析日期：2026-09-15。

这里的 `claude-code-leak` 仅表示当前本地目录的项目名称和源码快照，不对其来源、版本发布关系或生产行为作额外判断。本文结论以代码结构为准，不把 README 中未经源码验证的宣传性描述当作实现事实。

## 一、什么是 Agent Harness

一个能够长期运行的 Agent，至少需要处理下面六类问题：

1. **执行循环**：模型输出文本，还是继续发起工具调用；工具结果回来后如何继续下一轮。
2. **计算机接口**：文件、终端、浏览器、代码执行、消息发送和外部 API 如何暴露给模型。
3. **安全边界**：工具参数是否符合 Schema，调用是否需要用户批准，危险操作如何阻断。
4. **上下文管理**：历史太长时如何裁剪、摘要、压缩，同时保留任务进度和关键标识符。
5. **状态与恢复**：进程被杀、网络中断、模型切换或会话切换后，如何继续而不是从内存状态猜测。
6. **外部驱动**：Heartbeat、Cron、后台任务完成事件如何唤醒 Agent，并把结果投递回正确的会话。

可以用下面的抽象表示：

```text
用户 / 消息平台 / Cron / Heartbeat
              |
       Gateway / Session Router
              |
       Agent Harness Runtime
   ┌──────────┼───────────┐
   |          |           |
上下文      Tool       State
组装        调度       持久化
   |          |           |
   └────── Model API ─────┘
              |
        文本或工具调用
```

因此，Agent Harness 不是某一个“提示词模板”，也不等同于某个模型 SDK。它是模型输出和真实执行环境之间的控制平面。

## 二、Claude Code：把工具调用变成可靠的单轮循环

### 2.1 主链路

Claude Code 的核心结构集中在 `src/QueryEngine.ts` 和 `src/query.ts`。可以把一次用户请求概括为：

```text
CLI / SDK 输入
  -> QueryEngine.submitMessage()
  -> 构造 system prompt、工具、上下文和权限回调
  -> queryLoop()
  -> 流式调用模型
  -> 收集 assistant 文本和 tool_use
  -> 执行工具并生成 tool_result
  -> 若需要继续，则再次调用模型
  -> 最终文本、错误或中断结果
```

`QueryEngine` 是会话级对象，持有可变消息、AbortController、权限拒绝记录、usage 和已发现的技能等状态。`submitMessage()` 会先整理系统提示和运行时信息，并在真正调用模型前把用户消息写入 transcript。这样，进程在请求中途退出时，恢复逻辑至少能看见用户已经发起过什么请求。

`query.ts` 中的 `queryLoop()` 是显式的异步生成器循环。它每轮会：

- 根据当前状态生成发送给模型的消息副本；
- 注入用户上下文、工具 Schema 和运行时系统提示；
- 估算上下文压力，并在必要时触发自动压缩；
- 流式接收模型响应；
- 从 assistant 消息中提取 `tool_use`；
- 让工具执行器产生 `tool_result`；
- 判断是否需要下一轮，或者进入终止、恢复、fallback 和错误路径。

这里的要点是：**工具调用不是模型回答旁边的附加功能，而是主循环的一个状态转移**。

### 2.2 工具执行：验证、权限和并发

`src/services/tools/toolExecution.ts` 把工具调用拆成多个阶段：

1. 找到工具并检查调用是否已被中止；
2. 用工具的输入 Schema 验证参数；
3. 执行输入修正、Hook 和权限决策；
4. 允许、拒绝或等待用户批准；
5. 调用真正的工具实现；
6. 记录进度、结果、telemetry 和错误；
7. 返回符合 API 协议的 `tool_result`。

工具调度在 `src/services/tools/toolOrchestration.ts` 中进一步区分安全等级。连续的只读工具可以并发执行，默认并发上限由 `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` 控制；可能产生副作用的工具则串行执行。并发工具产生的上下文修改不会任意竞态地写入，而是按稳定顺序合并。

这体现了一个重要原则：

> Agent 可以并发读取环境，但改变环境的动作必须有明确的顺序和权限边界。

### 2.3 上下文压缩不是简单截断

Claude Code 的上下文路径包含 microcompact、自动 compact、上下文 collapse 和 prompt-too-long 恢复。`src/services/compact/autoCompact.ts` 会根据模型上下文窗口、摘要输出预留和安全缓冲计算自动压缩阈值；连续压缩失败达到上限后，会通过 circuit breaker 停止无效重试。

`query.ts` 对 prompt-too-long、媒体过大、输出 token 达上限等情况有专门恢复路径。例如：

- 先尝试排空已经暂存的上下文折叠；
- 再尝试 reactive compact；
- 仍失败时才把真实错误暴露给用户；
- 模型 fallback 时清理旧的 assistant/tool 状态，避免旧的 tool ID 泄漏到新请求；
- 流式中断时补齐缺失的 `tool_result`，保持协议配对。

这说明上下文压缩本质上是一个**可恢复的状态转换**，而不是把字符串从前面剪掉。

### 2.4 Claude Code 的 Harness 特征

从源码看，Claude Code 最强的部分是“单次交互内部的可靠执行”：

- 以流式 API 为中心组织明确的工具循环；
- 工具 Schema、权限、Hook 和错误处理位于模型调用之外；
- 只读并发和副作用串行被区分；
- transcript、resume、interrupt 和 compact 共同构成恢复机制。

它的主要关注点仍然是一个用户请求如何安全地完成。相比之下，跨消息平台的 Gateway、周期性唤醒和长期后台任务并不是这套代码最突出的抽象。

## 三、OpenClaw：把 Agent Loop 放进长生命周期运行时

### 3.1 Gateway 是外层控制面

OpenClaw 的架构不只处理一次 CLI 请求。Gateway 负责长期运行，承接消息平台、会话路由、Cron、Heartbeat、后台任务和 outbound delivery。一个 Agent 运行通常经过：

```text
消息 / Cron / Heartbeat
  -> Gateway session key
  -> session lane / command queue
  -> runEmbeddedPiAgent()
  -> runEmbeddedAttempt()
  -> createAgentSession()
  -> activeSession.prompt()
  -> 订阅流式事件、工具结果和投递事件
```

`src/agents/pi-embedded-runner/run.ts` 负责解析会话、工作区、模型、认证、fallback 和运行队列；`src/agents/pi-embedded-runner/run/attempt.ts` 负责一次具体执行：准备 workspace/sandbox、技能快照、Bootstrap 文件、工具、系统提示、SessionManager 和上下文引擎，然后创建 `@mariozechner/pi-coding-agent` 会话并提交 prompt。

这带来一个边界：OpenClaw 自己强力控制的是运行前后的编排，模型内部的基础 Agent Loop 主要由 PI Coding Agent 承担。OpenClaw 的 Harness 不是重新实现一个模型循环，而是把这个循环嵌入一个可恢复、可投递、可替换的宿主环境。

### 3.2 Harness 抽象和 fallback

`src/agents/harness/types.ts` 定义了 `AgentHarness`，核心能力包括：

- 判断当前 provider/model/session 是否支持该 Harness；
- 执行一次 attempt；
- 可选的 compact、reset 和 dispose。

`src/agents/harness/selection.ts` 会根据运行策略选择插件 Harness 或内置 PI Harness。运行策略可以是 session 级、配置级或环境变量级。当插件 Harness 在自动模式下失败时，代码可以 fallback 到内置 PI backend；如果明确关闭 fallback，则直接抛错。

这个设计把“模型路由”和“执行运行时”分离开来：同一套 Gateway 可以根据 provider、模型和会话策略选择不同的 Agent Harness。

### 3.3 一次 Embedded Attempt 做了什么

`runEmbeddedAttempt()` 的职责比普通的 API wrapper 更宽：

- 解析真实工作区和 sandbox 访问模式；
- 加载技能和技能环境变量快照；
- 取得 session write lock；
- 读取 Bootstrap、Agents、Soul、Identity、User、Tools、Memory 等上下文文件；
- 构造 OpenClaw coding tools、客户端工具和自定义工具；
- 创建 SessionManager 和 `@mariozechner/pi-coding-agent` session；
- 安装流式传输、provider 适配、prompt cache、超时和 malformed tool-call 保护；
- 订阅 `subscribeEmbeddedPiSession()`，统一处理 assistant 文本、tool lifecycle、compaction、消息投递和 liveness；
- 在提交 prompt 前检查上下文是否需要预压缩或工具结果截断；
- 在结束时执行 context-engine after-turn、hooks、usage 和资源清理。

因此，OpenClaw 的“长期运行能力”主要来自一组包围 `activeSession.prompt()` 的机制，而不是来自某一条更长的 system prompt。

### 3.4 Heartbeat：没有用户输入时继续工作

Heartbeat 实现位于 `src/infra/heartbeat-runner.ts` 和 `src/auto-reply/heartbeat.ts`。

默认 Heartbeat 指令要求 Agent 读取工作区中的 `HEARTBEAT.md`，只执行其中应该关注的任务；没有需要处理的事情时返回 `HEARTBEAT_OK`。代码还支持：

- 可配置周期和 active hours；
- `HEARTBEAT.md` 中按 interval 判断是否到期的 tasks；
- 对空文件或无到期任务的跳过，避免无意义的模型调用；
- 忽略正在执行的主会话 lane，避免打断用户交互；
- 将 Heartbeat prompt 和回复中的确认 token 从历史中清理，减少上下文污染；
- 识别 Exec completion、Cron event 和 wake event，并选择不同的 prompt；
- `isolatedSession` 模式，为每次 Heartbeat 使用新的轻量会话，避免重复发送完整历史；
- 通过 delivery target 把告警发送到最后活动的频道、线程或账号。

这与普通“定时调用模型”不同：Heartbeat 是带有会话路由、空闲判断、静默确认和投递策略的 Agent 运行模式。

### 3.5 Cron 和事件唤醒

`src/cron/service.ts` 对外提供 `wake({ mode: "now" | "next-heartbeat", text })`。Cron 任务可以：

- 立即触发一次 Heartbeat 运行；
- 把事件放入系统事件队列，等待下一次 Heartbeat 合并处理；
- 运行独立的 isolated session；
- 在任务完成后通过 outbound delivery 把结果送回目标会话。

OpenClaw 还会检查 session lane 是否繁忙，必要时跳过并稍后重试。这种调度方式把“模型什么时候再次获得控制权”变成了基础设施能力。

### 3.6 Memory、上下文和压缩

OpenClaw 的 memory search 配置位于 `src/agents/memory-search.ts`，支持 SQLite 存储、全文检索、向量检索、混合排序、分块、同步、watch、缓存和可选的 session memory。它不只是把一个 `MEMORY.md` 文件拼接进 prompt，而是可以建立独立的检索存储，并按 agent、来源和查询策略召回。

上下文压缩在 `src/agents/compaction.ts` 中实现。代码会：

- 排除 `toolResult.details` 等不应进入摘要的内容；
- 按 token 估算把历史分块；
- 避免把 assistant tool call 与对应 tool result 拆开；
- 对过大的消息使用 progressive fallback；
- 分阶段生成和合并摘要；
- 明确要求保留任务进度、当前请求、决策、TODO、约束和 opaque identifiers。

因此，OpenClaw 在 Harness 层面补齐的是“长时间运行所需的外围机制”：周期唤醒、消息投递、会话隔离、可插拔执行后端和面向长期记忆的上下文处理。

## 四、Hermes Agent：显式分阶段的执行控制面

### 4.1 从 Facade 到 Turn Loop

Hermes 用 Python 把 Agent 运行拆成多个明确阶段。公开入口由 `run_agent.py` 提供，真正的循环在 `agent/conversation_loop.py`，并进一步拆分为多个 `turn_*.py` 阶段。

核心循环可以概括为：

```text
run_conversation()
  -> build_turn_context()
  -> begin_iteration()
  -> prepare_iteration()
  -> assemble_api_request()
  -> run_preflight_gate()
  -> perform_api_call()
  -> normalize_model_response()
  -> run_tool_round() 或 finish_text_response()
  -> finalize_turn()
```

循环条件同时受 `max_iterations` 和 `iteration_budget` 约束，并处理 API retry、fallback、interrupt、compression timeout、persistence failure 和最终响应格式化。

Hermes 的特点不是把所有逻辑放在一个巨大函数里，而是让每个 turn 阶段有清晰契约。`run_tool_round()` 的返回值会告诉外层循环继续、结束，还是直接返回一个结构化结果。

### 4.2 工具注册、发现和执行

`model_tools.py` 是工具 Schema 和调度的薄编排层；`tools/registry.py` 负责注册、发现、可用性检查和 dispatch；`toolsets.py` 定义按场景组合的工具集合。

工具进入模型请求前需要经过：

- toolset 选择和禁用集合过滤；
- `check_fn` 可用性检查；
- 动态 Schema 重写；
- provider 或平台能力过滤；
- tool definition 缓存。

工具调用后则由 `agent/tool_executor.py` 和相关 inline executor 处理。Hermes 也区分并发安全工具与需要串行的工具，并为异步工具维护长期 event loop，避免每次调用都创建和销毁客户端运行环境。

### 4.3 Persist-before-execute：先记录，再产生副作用

Hermes 的 `agent/turn_tool_round.py` 有一个非常关键的顺序：

```text
模型产生 tool call
  -> 把 assistant tool-call 消息写入 SessionDB
  -> 确认持久化成功
  -> 执行工具副作用
  -> 把 tool result 写回 SessionDB
  -> 继续下一轮模型调用
```

如果工具调用消息无法持久化，Hermes 不会继续执行可能产生破坏性副作用的工具。原因很直接：进程重启后，恢复系统必须知道工具已经被模型发起，不能让数据库和真实环境处于不可解释的状态。

这是从“能跑起来”走向“可恢复 Agent”时必须建立的持久化不变量。

### 4.4 SQLite StateDB 是运行时的一部分

`hermes_state.py` 和 `hermes_state_common.py` 显示，Hermes 把很多运行事实放入 SQLite，而不是只依赖内存：

- `sessions`：会话元数据、模型配置、压缩 lineage 和生命周期；
- `messages`：消息、tool call、tool result、reasoning、压缩状态和展示元数据；
- `system_prompts`：可复用的系统提示和 prompt cache 相关信息；
- `gateway_routing`：平台到会话的路由映射；
- `gateway_heartbeats`：Gateway backend 的进程存活信息；
- `compression_locks`：压缩操作的跨进程锁；
- `session_turn_leases`：同一会话 turn 的跨进程租约；
- `messages_fts` / trigram FTS：历史消息搜索。

Hermes 还利用 WAL、读连接池、压缩父子 session lineage、过期锁回收和启动时 orphan sweep 来应对长生命周期 Gateway 的并发与恢复问题。

这意味着 Hermes 的 session 不只是“发给模型的 messages 数组”，而是一个可以被恢复、搜索、压缩、迁移和诊断的持久化对象。

### 4.5 Prompt cache 和消息协议不变量

Hermes 的 `agent/AGENTS.md` 和源码都强调两个约束：

1. prompt cache 的稳定前缀不能被不必要地重写；
2. OpenAI 风格的 system/user/assistant/tool 消息角色交替和 tool-call/result 配对必须保持合法。

因此，Hermes 会区分 canonical transcript 和每次 API 请求的 send copy。很多规范化、空消息修复、工具参数 canonicalization 和 cache marker 注入只修改发送副本，避免改变持久化历史的字节内容。

这是一个容易被忽视的事实：**上下文管理不仅要让模型“看懂”，还要让 provider 协议、恢复逻辑和缓存系统都能接受同一份历史。**

### 4.6 Memory 和 Skill：长期上下文的两个方向

Hermes 的 Memory provider 接口位于 `agent/memory_provider.py`，由 `agent/memory_manager.py` 统一管理。生命周期包括：

- 初始化；
- 生成 system prompt block；
- 为下一轮预取 recall context；
- 在 turn 完成后异步同步；
- 在压缩前提取信息；
- 会话结束或切换时 flush；
- 暴露 provider 自己的 memory tools。

MemoryManager 对外部 provider 设置超时和后台线程，单独的写入 worker 保证 turn N 的写入先于 turn N+1；外部 provider 出错通常不会阻塞主 Agent。

`agent/agent_init.py` 还区分内置 MemoryStore 和外部 memory provider。`skip_memory` 并不简单等于“所有 memory 都不存在”，因为某些 flush、cron 或工具集仍可能需要内置 memory 工具。

Skill 则是一种更偏“可复用工作方法”的持久化上下文。它通过工具集、技能文档、技能快照和 system prompt 指令影响 Agent 的行为。Memory 更像事实和经验的召回，Skill 更像程序化的流程和领域规范。

### 4.7 Cron、Gateway 和会话恢复

Hermes 的 `cron/scheduler.py` 由 Gateway 定期 tick，维护 due jobs、运行 claim、锁、超时、inactivity watchdog、脚本执行和 Cron session。Cron 运行通常拥有独立 session，并在结束时写入标题、生命周期和输出。

`gateway/wake.py` 负责把后台完成事件重新送回原会话：支持 push-capable adapter 的内部事件，也支持 API server 通过原始 session id self-post；异步 delegation 完成则可以作为 durable delivery row 写入，而不是未经授权地伪装成新的用户消息。

`gateway/session_recovery.py` 和 `gateway/run_heartbeat_restore.py` 会从持久化路由与 Heartbeat 状态重建内存映射。这样，Gateway 重启后仍有机会恢复正确会话，而不是把消息发送到一个新建的平行会话。

## 五、横向比较

| 维度 | Claude Code | OpenClaw | Hermes Agent |
|---|---|---|---|
| 核心关注 | 单轮任务如何通过工具完成 | 长生命周期、多渠道 Agent runtime | 可恢复、可治理的 turn 控制面 |
| Loop 归属 | `query.ts` 自己维护流式循环 | 外层编排 + PI Coding Agent 内部循环 | `conversation_loop.py` + 分阶段 turn modules |
| 工具执行 | Schema、权限、Hook、并发安全分类 | coding tools、插件 tools、session subscription | registry、toolset、check_fn、executor、guardrail |
| 上下文压缩 | auto compact、collapse、reactive recovery | preflight compact、分阶段摘要、context engine | preflight/post-tool compression、压缩 lineage 和锁 |
| 持久化重点 | transcript、resume、assistant/tool 配对 | session files、SessionManager、Gateway routing | SQLite sessions/messages/leases/locks/FTS |
| 主动唤醒 | 不是最突出的中心抽象 | Heartbeat、Cron、wake event 是核心能力 | Cron、Gateway wake、后台完成事件 |
| Memory | 侧重会话上下文和技能机制 | SQLite/FTS/vector/hybrid memory search | 内置 MemoryStore + 可插拔 MemoryProvider |
| 多渠道 | CLI、SDK、remote 等入口 | Gateway 统一消息平台和 delivery | CLI、TUI、Electron、Gateway、API 等表面 |
| Harness 可替换性 | 以固定运行时为主 | `AgentHarness` 插件选择和 PI fallback | 通过 facade、tool registry、provider/plugin 扩展 |
| 典型风险 | 上下文爆炸、工具协议错误、权限误判 | 队列/会话/投递/心跳状态复杂 | 数据库一致性、租约、prompt cache 和恢复复杂 |

## 六、从三个系统看到的演化路径

### 第一阶段：模型输出接入真实工具

Claude Code 代表的第一步，是把模型输出可靠地变成文件读写、终端命令和其他工具调用。关键能力是：

- 明确的 tool-call loop；
- 参数验证；
- 权限批准；
- 只读并发和副作用串行；
- 错误、fallback 和中断恢复。

### 第二阶段：Agent 成为长生命周期进程

OpenClaw 代表第二步。Agent 不再只等待用户输入，而是拥有 Gateway、session lane、Heartbeat、Cron、isolated session、消息投递和后台事件。模型只是其中一个执行部件，Harness 开始承担“何时运行、在哪个会话运行、结果投递到哪里”的责任。

### 第三阶段：运行时成为可恢复控制平面

Hermes 进一步把 session、message、prompt、压缩、turn lease、Gateway heartbeat 和 memory sync 做成持久化状态机。长期运行 Agent 面临的核心问题变成：

- 一个副作用是否已经被记录；
- 一个会话是否被两个进程同时推进；
- 压缩是否正在发生；
- Gateway 是否还活着；
- resume 时应该恢复哪条 lineage；
- prompt cache 是否因无意义重建而失效。

因此，下一代 Agent Harness 的形态更接近：

```text
模型推理
 + 工具运行时
 + 权限与沙箱
 + 上下文引擎
 + 持久化会话
 + 记忆与技能
 + 调度与唤醒
 + 失败恢复
 + 可观测性与投递
```

这也对应图片中“Agents need computers”的观点：Agent 要完成长期任务，除了 token 生成，还需要一个轻量、可组合、可恢复、接近 Unix 心智的计算环境。

## 七、可复用的工程原则

### 1. 把 Agent Loop 写成显式状态机

不要把“调用模型、执行工具、继续调用”散落在多个 callback 中。应能回答：当前 turn 在哪个阶段、下一步为什么继续、什么条件会终止。

### 2. 工具执行要有两条边界

第一条是模型协议边界：Schema、tool ID、tool result 配对。第二条是真实环境边界：权限、沙箱、危险命令、审批和超时。两者不能只靠 prompt 维持。

### 3. 副作用前先建立可恢复记录

至少要先持久化“准备执行哪个工具”，再执行文件修改、命令、网络写入等副作用。否则进程重启后无法判断真实世界已经发生了什么。

### 4. 上下文压缩必须保留任务状态

摘要不应只保留“讨论过什么”，还要保留当前请求、完成进度、决定及其理由、未解决问题、约束、承诺和关键 ID。压缩失败也要有明确的终止或恢复策略。

### 5. 心跳必须低噪声且可幂等

没有事情时应跳过模型调用或静默确认；有事情时才投递告警。Heartbeat prompt、确认 token、事件队列和历史清理必须设计成重复执行不会造成消息污染。

### 6. 会话路由必须持久化

多渠道系统不能只依赖内存中的 `sessionKey -> session` 映射。Gateway 重启、压缩换代、profile 切换和后台事件都要求路由能从持久化状态恢复。

### 7. 把扩展点放在边缘，保持核心窄腰

模型适配、工具、Memory、Skill、Gateway adapter 和 Harness 都可以插件化，但核心 turn loop 需要少而稳定的契约。Hermes 的 tool registry/provider 接口和 OpenClaw 的 `AgentHarness` 都体现了这一点。

## 结论

Claude Code、OpenClaw 和 Hermes Agent 并不是三个互相替代的“聊天机器人实现”，而是 Agent Harness 演化的三个观察面：

- **Claude Code** 说明如何把模型输出变成安全、可恢复的工具执行循环；
- **OpenClaw** 说明如何把 Agent 嵌入 Gateway，使其拥有 Heartbeat、Cron、多渠道和后台运行能力；
- **Hermes Agent** 说明如何把 turn、memory、prompt cache、session、lease 和 compression 组织成持久化控制面。

最终的竞争点会从“谁的模型回答更像人”逐渐扩展为“谁能在权限可控的前提下，持续、可观测、可恢复地完成真实任务”。模型提供推理能力，Agent Harness 则决定这种能力能否稳定地作用于计算机世界。

## 八、源码索引

### Claude Code

- `src/QueryEngine.ts`：会话级入口、消息持久化、system prompt、query 结果。
- `src/query.ts`：流式模型调用、tool_use 收集、fallback、恢复和 follow-up loop。
- `src/services/tools/toolExecution.ts`：Schema、权限、Hook、工具调用和 tool_result。
- `src/services/tools/toolOrchestration.ts`：只读并发和副作用串行。
- `src/services/compact/autoCompact.ts`：自动压缩阈值、失败保护和压缩触发。

### OpenClaw

- `src/agents/harness/types.ts`：Agent Harness 契约。
- `src/agents/harness/selection.ts`：Harness 选择和 PI fallback。
- `src/agents/pi-embedded-runner/run.ts`：Embedded Agent 运行入口和队列。
- `src/agents/pi-embedded-runner/run/attempt.ts`：workspace、tools、session、prompt 和运行清理。
- `src/agents/pi-embedded-subscribe.ts`：流式事件、工具生命周期、压缩和消息投递订阅。
- `src/infra/heartbeat-runner.ts`：Heartbeat preflight、任务、会话和投递。
- `src/auto-reply/heartbeat.ts`：Heartbeat prompt、任务解析和确认 token。
- `src/cron/service.ts`：Cron API 和 wake 模式。
- `src/agents/compaction.ts`：分块摘要、tool pairing 和 progressive fallback。
- `src/agents/memory-search.ts`：SQLite、FTS、vector、hybrid memory search 配置。

### Hermes Agent

- `run_agent.py`：AIAgent facade 和生命周期。
- `agent/conversation_loop.py`：分阶段 turn loop。
- `agent/turn_tool_round.py`：persist-before-execute 和工具轮次。
- `model_tools.py`：工具 Schema、toolset 和 dispatch 编排。
- `tools/registry.py`：工具注册、发现、可用性检查和 dispatch。
- `toolsets.py`：工具集合和平台 posture。
- `agent/memory_provider.py`：Memory provider 接口。
- `agent/memory_manager.py`：Memory 生命周期、prefetch、sync 和插件工具。
- `hermes_state.py` / `hermes_state_common.py`：SQLite state、消息、路由、FTS、锁和租约。
- `cron/scheduler.py`：Cron tick、claim、watchdog 和独立运行。
- `gateway/wake.py`：后台完成事件到原会话的唤醒与投递。
- `gateway/session_recovery.py`：Gateway 会话路由恢复。
