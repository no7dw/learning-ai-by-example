# Coding Agent 架构课程：OpenAI Agents SDK、Codex app-server、Pi Agent 与 OpenCode

> 本文是一篇架构理解课程，不是四个项目的 API 参考手册。
> 文中把“Pi agent”理解为 pi.dev / badlogic/pi-mono 这一套开源 coding agent。
> 不同版本的命令、目录和协议可能变化，落地时应以对应版本的官方文档为准。

相关项目：

- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [Codex developer documentation](https://developers.openai.com/codex/)
- [Pi](https://pi.dev/)
- [Pi monorepo](https://github.com/badlogic/pi-mono)
- [OpenCode](https://opencode.ai/)
- [OpenCode repository](https://github.com/anomalyco/opencode)

## 1. 先建立正确的比较方式

这四个名字不在同一个抽象层。

~~~text
Agent-building framework
    ├── OpenAI Agents SDK
    └── Pi agent-core / Pi extensions

Ready-made coding agent runtime or application
    ├── Codex app-server
    └── OpenCode server
~~~

更准确地说：

- OpenAI Agents SDK 给应用开发者 Agent、Runner、tools、handoffs、guardrails 和 session 等编排原语。
- Codex app-server 把已经具备 coding loop 的 Codex 暴露给外部客户端或平台。
- Pi 同时包含可复用的 agent core 和完整的 coding agent 应用，强调小型、可读、可扩展的 harness。
- OpenCode 是一个开源 coding agent 应用，也可以运行成 server，供 TUI、Web UI 或其他客户端使用。

因此，不能简单问“谁的 Agent 更强”。应该问：

> 谁拥有 Agent loop？谁拥有工具执行？谁拥有 session？谁负责 Skill、权限和 workspace？

## 2. Coding agent 的六层结构

一个完整 coding agent 通常包含六层：

~~~text
┌────────────────────────────────────────────┐
│ 1. Client / UI                             │
│    CLI、TUI、Web、IDE、HTTP API             │
├────────────────────────────────────────────┤
│ 2. Agent loop                              │
│    模型调用 → tool call → tool result → ... │
├────────────────────────────────────────────┤
│ 3. Context / Session                       │
│    history、compaction、branch、resume      │
├────────────────────────────────────────────┤
│ 4. Tools                                   │
│    read、write、edit、shell、MCP、browser    │
├────────────────────────────────────────────┤
│ 5. Policy / Runtime                        │
│    approval、sandbox、network、filesystem    │
├────────────────────────────────────────────┤
│ 6. Extension / Skill                       │
│    prompt、reference、script、plugin、MCP    │
└────────────────────────────────────────────┘
~~~

四个系统的主要区别，是这六层由谁持有。

| 系统 | Agent loop | Session | Tools | Policy | Skill/Extension |
|---|---|---|---|---|---|
| Agents SDK | 应用或 SDK Runner | 应用可控制 | 应用注册 | 应用负责较多 | 需要自己设计或组装 |
| Codex app-server | Codex runtime | Codex thread/session | Codex runtime | Codex config/runtime | Codex filesystem Skill |
| Pi agent | Pi core 或 coding harness | Pi session/tree | 内置 tools + extensions | harness/宿主配置 | skills、prompt templates、extensions |
| OpenCode | OpenCode server | OpenCode session | 内置 tools + MCP/plugins | OpenCode permissions/宿主环境 | commands、skills、plugins、config |

## 3. OpenAI Agents SDK

### 3.1 它解决什么问题？

Agents SDK 更像一个“自己构建 Agent 产品”的框架。

典型流程是：

~~~text
你的应用
  ↓
Runner.run(agent, input)
  ↓
模型返回文本或 tool call
  ↓
你的 tool 执行器执行工具
  ↓
工具结果回到 Runner
  ↓
继续下一轮或返回最终答案
~~~

一个简化模型：

~~~python
agent = Agent(
    name="Order Assistant",
    instructions="帮助用户处理订单。",
    tools=[lookup_order, cancel_order],
)

result = await Runner.run(agent, user_input)
~~~

这里 instructions 是 Agent 的基础行为说明；tools 是应用明确注册的能力；业务代码通常拥有工具执行、数据库访问、权限校验和持久化。

### 3.2 优势

- 控制力强：应用可以决定每一轮做什么。
- 工具类型清晰：可以为参数、返回值和错误定义 schema。
- 适合业务 workflow：订单、审批、客服、销售、数据分析等。
- 容易做单元测试：模型和工具可以分别 mock。
- 适合动态 Agent composition：根据用户、租户或任务生成不同 Agent。
- 可以把 Agent 变成后端服务中的一个普通模块，而不是长期运行的独立 coding 进程。

### 3.3 代价

Agents SDK 本身不会自动变成一个完整的 Codex。你通常需要自己补上：

- workspace 和文件操作；
- shell 执行；
- sandbox 和网络限制；
- session 持久化；
- streaming protocol；
- interrupt、timeout、retry；
- Skill discovery 和 progressive disclosure；
- script/reference 的安全执行模型。

如果把它用于 coding agent，最终常常会自己实现一套小型 coding harness。

### 3.4 适合的场景

~~~text
业务 API + typed tools + 明确 workflow + 强权限控制
~~~

不太适合直接承担“自由浏览代码、修改多个文件、运行测试、反复修复”的全部运行时工作，除非你愿意自己建设这部分能力。

## 4. Codex app-server

### 4.1 它解决什么问题？

Codex app-server 更像一个已经实现好的 coding agent engine。

外部应用通过 SDK 或协议连接它：

~~~text
你的 Gateway / Web UI
        ↓
Codex SDK client
        ↓
Codex app-server
        ↓
Codex agent loop
        ├── 模型调用
        ├── file/shell/MCP/tool 执行
        ├── Skill discovery
        ├── workspace policy
        └── thread/session state
~~~

当前项目使用的就是这种边界：

~~~text
POST /runs
  ↓
RunManager
  ↓
CodexRuntime.stream_run()
  ↓
thread_start / thread_resume
  ↓
session.turn(message)
~~~

Gateway 不实现内部的“模型 → 工具 → 模型”循环，而是消费 app-server 的事件并转换成自己的 SSE 协议。理解这一区别时，可以把 Gateway 看成外部控制面，把 app-server 看成内部 coding data plane。

### 4.2 Skill 模型

在当前 Gateway 设计中，Skill 是 Agent home 下的文件系统资源：

~~~text
<agent_home>/skills/<name>/
├── SKILL.md
├── references/
├── scripts/
└── assets/
~~~

Gateway 负责：

- 从 SkillHub 或 HTTPS zip 安装；
- 校验归档内容；
- 原子替换目录；
- 记录来源和 fingerprint；
- 给 Codex runtime 提供只读访问。

Codex runtime 负责：

- 发现可用 Skill；
- 监听 Skill 目录变化；
- 根据任务按需加载；
- 读取 reference 或执行 script。

这和 Agents SDK 的差别是：Gateway 不把所有 SKILL.md 内容拼到 instructions，也不把每个 script 转成 Python function tool。

### 4.3 优势

- coding loop 已经存在，不需要重复实现。
- workspace、shell、文件和 session 语义较完整。
- Skill 可以保持为可复用 filesystem artifact。
- app-server 适合被 Web UI、IDE 或企业 Gateway 包装。
- Gateway 可以把用户隔离、SSE、审计和业务 API 放在外层。
- 适合长期 thread 和交互式 coding 任务。

### 4.4 代价

- 内部 Agent loop 的控制权较弱。
- 应用不一定能准确知道某个 Skill 是否真的被激活。
- runtime、协议和配置会绑定 Codex 版本。
- 每个 Agent 独立进程、home 和 workspace，资源成本更高。
- 业务 workflow 不如 Agents SDK 直观。
- 对模型决策和工具执行的测试更依赖集成测试。

### 4.5 适合的场景

~~~text
需要 Codex 行为、workspace、shell、Skill 和长期 coding session
~~~

当前 Agent Gateway 的设计就是：Gateway 拥有控制面，Codex app-server 拥有 coding data plane。

## 5. Pi Agent

### 5.1 它解决什么问题？

Pi 更接近一个开源、可读、可改造的 coding agent harness。它不是只有一个“远程 server”概念，而是把多个层放在同一个 monorepo 中：

~~~text
pi-ai             模型/provider 抽象
pi-agent-core     Agent loop 和状态
pi-coding-agent   coding harness / CLI
pi-tui            终端界面
extensions        自定义工具和行为
~~~

概念上的调用链是：

~~~text
Pi CLI / TUI / 自定义应用
        ↓
Pi coding agent
        ↓
Pi agent core
        ↓
model adapter + tools + extensions
~~~

### 5.2 它和 Agents SDK 的相似点

- Agent loop 更靠近应用代码。
- 可以编写扩展、工具和自定义行为。
- 对 provider 和运行时有较强的改造空间。
- 更容易阅读和修改内部 harness。
- 可以选择把 Pi 当完整应用，也可以使用其中的 core 能力。

### 5.3 它和 Codex app-server 的差别

Pi 通常更强调：

- 开源代码可读性；
- 本地运行和自托管；
- 多 provider；
- TypeScript 扩展；
- 轻量、可改的 coding harness。

Codex app-server 更强调：

- Codex 统一运行时；
- app-server 协议；
- Codex 的 workspace、权限和 Skill 机制；
- 将 Codex 核心能力暴露给外部应用。

所以 Pi 的问题是“我想拥有并改造一个 coding harness”，Codex app-server 的问题是“我想把 Codex coding engine 集成到我的产品”。

### 5.4 Pi 的优势

- 开源，适合学习 Agent loop 的实现。
- 可以直接阅读上下文压缩、session tree、工具执行和扩展机制。
- provider 和扩展自由度通常更高。
- 适合个人开发工具、研究型 Agent、定制 CLI。
- 比完整企业 Gateway 更容易 fork 和修改。

### 5.5 Pi 的代价

- 你需要自己负责更多部署和安全边界。
- 不同扩展可能改变 Agent 行为，治理成本需要自行承担。
- 企业级多租户、权限、审计和远程 session 需要额外建设。
- 生态和协议稳定性依赖项目版本和社区。

## 6. OpenCode

### 6.1 它解决什么问题？

OpenCode 是一个开源 coding agent 应用，同时提供 server mode，使不同客户端可以连接到同一个 coding agent 服务。

概念上的结构是：

~~~text
OpenCode TUI / Web / custom client
        ↓
OpenCode server
        ↓
OpenCode agent loop
        ├── provider/model
        ├── file/shell tools
        ├── MCP
        ├── plugins
        ├── permissions
        └── sessions/events
~~~

它和 Codex app-server 最像，因为两者都提供“coding agent server + 外部客户端”这种集成方式。

### 6.2 和 Codex app-server 的相似点

- 都有独立的 coding agent loop。
- 都可以被 TUI、Web 或自定义 Gateway 使用。
- 都有 session、消息和工具事件。
- 都围绕 workspace 中的代码工作。
- 都支持 MCP 或类似的扩展能力。
- 都可以把 Skill、command、plugin 等能力放到 Agent 外部，而不只是一个长 prompt。

### 6.3 和 Codex app-server 的差别

| 维度 | Codex app-server | OpenCode server |
|---|---|---|
| 主要定位 | Codex runtime 的集成协议 | OpenCode 应用的 server mode |
| 代码开放性 | 由 Codex 产品和 SDK 边界决定 | 开源，可直接研究和修改 |
| Provider | 更偏 Codex/OpenAI runtime 体系 | 通常更强调 provider 灵活性 |
| 扩展方式 | Codex config、Skill、MCP 等 | config、commands、skills、plugins、MCP 等 |
| 外部集成 | app-server protocol | HTTP/server API 和事件流 |
| 权限边界 | Codex runtime 的 sandbox/config | OpenCode 和部署环境共同决定 |
| 适合角色 | 企业 Codex 平台底座 | 可自托管的开源 coding app/server |

两者不能只按“都能返回 SSE”来判断是否相同。真正的差别在于：

- 谁定义 session 和 item 的语义；
- 谁管理 sandbox；
- 谁负责 provider；
- 谁定义 Skill 的发现和加载协议；
- 谁保证版本兼容；
- 谁拥有 runtime 的源代码。

### 6.4 OpenCode 的优势

- 开源和自托管友好。
- 多 provider 和扩展空间大。
- server、TUI、Web 的组合自然。
- 社区更容易直接修改 Agent 行为。
- 适合想拥有完整 coding agent 产品而不是只调用一个 Agent SDK 的团队。

### 6.5 OpenCode 的代价

- 需要自己验证多租户和 sandbox 边界。
- Server API、事件模型和插件契约需要跟随版本。
- 企业审计、资源配额、隔离和密钥管理不能只依赖默认配置。
- 如果 Gateway 已经围绕 Codex thread/turn/item 建模，接入 OpenCode 需要写一套完整 adapter。

## 7. 四者的核心对比

| 维度 | OpenAI Agents SDK | Codex app-server | Pi Agent | OpenCode |
|---|---|---|---|---|
| 抽象层 | Agent 编排框架 | Coding runtime server | 可改造的 coding harness | Coding agent application/server |
| 谁持有 loop | 应用/Runner | Codex | Pi core/harness | OpenCode server |
| 谁执行工具 | 应用注册的 tools | Codex runtime | Pi tools/extensions | OpenCode tools/plugins/MCP |
| Skill 是否天然存在 | 需要自己实现 | 原生 filesystem Skill | 项目/扩展机制 | 自己的 skills/config/plugins 机制 |
| Provider 灵活度 | 应用可组合 | 受 Codex runtime 约束 | 较高 | 较高 |
| Session 控制权 | 应用较强 | runtime 较强 | core/harness 较强 | server 较强 |
| Coding 能力 | 需要自行建设 | 开箱即用 | 开源可改 | 开箱即用 |
| Sandbox | 应用负责 | runtime/config 负责较多 | 宿主和 harness 负责 | runtime/宿主共同负责 |
| 可观察性 | 应用最容易控制 | 内部 loop 较黑盒 | 可读代码较多 | 可通过 server/events 观察 |
| 多租户难度 | 应用自己建设 | Gateway + runtime 可分层 | 需要自己补 | 需要自己补 |
| 最适合 | 业务 Agent | Codex 平台 | 可改的个人/研究型 coding agent | 开源自托管 coding agent |

## 7.1 从 Harness 角度重新分类

### Harness 是什么？

Harness 可以翻译成“运行支架”或“执行框架”。它不是单纯的 prompt，也不是单纯的模型 SDK，而是把模型变成一个可以持续完成任务的 Agent 所需要的那一层。

一个 coding harness 通常至少包含：

~~~text
Coding Harness
  ├── Agent loop
  ├── context/session history
  ├── compaction 或上下文恢复
  ├── coding tools
  │   ├── read
  │   ├── write/edit
  │   ├── shell
  │   └── test/build
  ├── workspace/cwd
  ├── permission/approval/sandbox
  ├── Skill/extension loading
  ├── error/timeout/retry
  └── UI 或 machine-facing protocol
~~~

因此，Harness 关注的不只是“模型能不能调用工具”，而是：

> 一个 Agent 如何在真实工作环境里持续工作、失败恢复、维护上下文并最终交付结果。

### 不是所有 Runtime 都是完整 Harness

“Runtime”经常被宽泛地使用，但可以分成三层：

| 层次 | 负责什么 | 例子 |
|---|---|---|
| Model runtime | 调用模型、生成 response/tool call | model SDK、Responses API |
| Agent runtime | loop、tool call、history、handoff | Agents SDK Runner、Pi agent-core |
| Coding harness | Agent runtime + workspace、编辑、shell、测试、权限、恢复 | Codex、Pi coding-agent、OpenCode |

Server 也不是第四种 Harness。Server 只是暴露 Harness 的方式：

~~~text
Coding Harness
  ├── local CLI/TUI
  ├── library embedding
  └── app-server / HTTP server
~~~

### 四个系统在 Harness 中的位置

| 系统 | 是否属于 Harness | 更准确的判断 |
|---|---|---|
| OpenAI Agents SDK | 部分属于 | 它提供通用 Agent runtime 和编排能力，但不是完整 coding harness |
| Codex app-server | 是，但名称指的是集成入口 | app-server 背后是完整 Codex coding harness；app-server 本身是协议边界 |
| Pi agent | 分层看 | Pi agent-core 是部分 harness；Pi coding-agent 是较完整的 coding harness |
| OpenCode | 是 | OpenCode 应用本身是完整 coding harness；server mode 是它的外部接入面 |

### OpenAI Agents SDK：通用 Agent Harness 的一部分

Agents SDK 已经包含一些 harness 能力：

- Agent loop；
- tool call 和 tool result；
- handoff；
- guardrails；
- session 或上下文接口；
- tracing；
- streaming。

所以把它说成“只有 prompt + tools”并不准确。它已经是通用 Agent runtime，甚至可以叫通用 Agent harness 的基础层。

但它通常不直接提供完整 coding harness：

- 不替你定义 coding workspace；
- 不自动提供完整 read/write/edit/shell/test 工具链；
- 不替你决定 shell sandbox；
- 不替你实现 Skill filesystem discovery；
- 不替你实现代码编辑后的验证和恢复流程。

因此更准确的写法是：

~~~text
Agents SDK
  = 通用 Agent harness primitives
  ≠ 完整 coding harness
~~~

如果应用自己补齐 workspace、shell、patch、测试、权限和 Skill loader，那么它当然可以用 Agents SDK 构建出完整 coding harness。

### Codex app-server：完整 Coding Harness 的协议入口

Codex app-server 不是一个“只负责转发模型请求的 server”。它背后已经有完整的 coding harness：

~~~text
Codex coding harness
  ├── Codex agent loop
  ├── thread/turn/item state
  ├── coding tools
  ├── workspace/cwd
  ├── permissions/sandbox
  ├── Skills
  └── interactive control
          ↑
     app-server protocol
~~~

所以在当前项目中，CodexRuntime 更准确的含义是：

~~~text
CodexRuntime
  = Gateway 对 Codex coding harness 的 runtime adapter
~~~

Gateway 没有自己实现 coding harness，而是把 Codex harness 包装成统一的 Session → Run → Item API。

### Pi：一个分层的 Harness

Pi 不能只归类为“框架”或“应用”。需要看使用的是哪一层：

~~~text
pi-agent-core
  = 通用 Agent runtime / harness 基础层

pi-coding-agent
  = 在 core 之上加入 coding tools、session、上下文管理和 CLI 的 coding harness

Pi TUI / CLI
  = harness 的客户端和交互面
~~~

Pi 的重要特点是这些层通常在同一个开源代码库中，开发者可以继续修改和组合它们。它既可以拿来使用，也适合拿来学习 harness 的内部实现。

### OpenCode：完整 Coding Application Harness

OpenCode 更适合看成一个完整的 coding application harness：

~~~text
OpenCode
  ├── model/provider layer
  ├── Agent loop
  ├── coding tools
  ├── session/context
  ├── permissions
  ├── MCP/plugins/skills
  ├── TUI/Web client
  └── server mode
~~~

因此 OpenCode server 不是独立于 OpenCode 的另一个 Agent。它是 OpenCode harness 提供给外部客户端的服务入口。

### Harness 完整度排序

如果只按照“现成 coding harness 的完整度”排序，可以粗略理解为：

~~~text
Agents SDK
  → 通用 Agent harness 基础层，需要自行补 coding 能力

Pi agent-core
  → 可组合 Agent harness 基础层

Pi coding-agent
  → 较完整、可修改的 coding harness

OpenCode
  → 完整开源 coding application harness

Codex app-server
  → 完整 Codex coding harness 的产品化集成入口
~~~

这不是质量排名，而是“开箱即用的 coding 能力”和“应用控制权”之间的区别：

~~~text
应用控制权高  ←────────────────────→  开箱即用能力高
Agents SDK / Pi core                  Codex / OpenCode
                 Pi coding-agent
~~~

### 对 Agent Gateway 的意义

如果 Gateway 只是接入一个已经完成的 coding harness，它的职责应是：

~~~text
Gateway
  ├── 用户、Agent 和租户隔离
  ├── runtime 生命周期
  ├── session/run 公共协议
  ├── SSE/event translation
  ├── Skill 安装和来源管理
  ├── 配额、审计和业务权限
  └── 选择 Codex/Pi/OpenCode runtime
~~~

Gateway 不需要再实现第二套 coding harness。

如果 Gateway 改用 Agents SDK，则职责会变成：

~~~text
Gateway + Agents SDK
  ├── Agents SDK loop
  ├── coding tools
  ├── workspace
  ├── shell sandbox
  ├── Skill loader
  ├── session persistence
  ├── recovery/compaction
  └── public API
~~~

这会让 Gateway 从“runtime adapter”升级成“coding harness owner”，灵活性更高，但需要维护的代码和安全边界也更多。

## 8. Skill 在四种系统里的不同落地方式

最重要的概念是：Skill 不是“一个 prompt 字符串”，而是一套可复用的工作方法。

~~~text
Skill
  ├── instructions
  ├── references
  ├── scripts
  ├── assets
  └── permissions / metadata
~~~

四种实现可以这样理解：

### Agents SDK

需要应用自己定义 Skill layer：

~~~text
skill.md → instructions
references → read_reference tool 或按需读取
scripts → typed tool / sandbox subprocess
metadata → Skill registry
~~~

优点是完全可控，缺点是工作量最大。

### Codex app-server

~~~text
Skill directory → Codex native filesystem discovery
SKILL.md        → runtime 按需读取
references      → runtime 按需读取
scripts         → Codex tool loop 中执行
~~~

Gateway 负责安装和隔离，Codex 负责使用。

### Pi

通常由 Pi 的 skills、prompt template、extension 或 package 机制组合完成。Pi 的优势在于可以直接修改加载器、上下文策略和扩展执行逻辑。

### OpenCode

通常由 skills、commands、plugins、MCP 和项目配置共同完成。它提供的是 OpenCode 自己的扩展契约，不应假设与 Codex 或 Pi 完全兼容。

## 9. 如何选型

### 选择 OpenAI Agents SDK

当你的核心问题是：

~~~text
我需要一个可以严格控制的业务 Agent workflow。
~~~

优先考虑 Agents SDK。

### 选择 Codex app-server

当你的核心问题是：

~~~text
我需要把 Codex 的 coding 能力、workspace、Skill 和 session 集成到产品中。
~~~

优先考虑 Codex app-server。

### 选择 Pi

当你的核心问题是：

~~~text
我想学习、修改或 fork 一个简洁的 coding agent harness。
~~~

优先考虑 Pi。

### 选择 OpenCode

当你的核心问题是：

~~~text
我需要一个开源、可自托管、多 provider、带 server/UI 生态的 coding agent。
~~~

优先考虑 OpenCode。

## 10. 一个实用的混合架构

真实产品通常不需要四选一，可以让不同系统承担不同角色：

~~~text
业务 Orchestrator / Agents SDK
        │
        ├── 查询业务数据
        ├── 权限检查
        ├── 审批和审计
        └── 选择 coding sub-agent
                    │
                    ├── Codex app-server
                    ├── Pi agent
                    └── OpenCode server
~~~

例如用户提出“修复这个线上 bug”：

1. 业务 Agent 查询 issue、项目和用户权限。
2. 业务 Agent 选择一个 coding runtime。
3. Coding runtime 在隔离 workspace 中读取代码、修改文件、运行测试。
4. 业务 Agent 检查结果并决定是否创建 PR。
5. GitHub、部署和审批仍由业务层的 typed tools 负责。

这样可以把“开放式 coding”与“强约束业务流程”分开。

## 11. 对当前 Agent Gateway 的启发

当前 Gateway 已经采用了一个合理的分层：

~~~text
Gateway-owned
  ├── Agent CRUD
  ├── user/agent isolation
  ├── Skill installation
  ├── run/session/SSE
  ├── timeout/interrupt/steer
  └── public API contract

Codex-owned
  ├── model loop
  ├── tool execution
  ├── Skill discovery
  ├── thread execution
  └── coding behavior
~~~

如果未来需要支持 Pi 或 OpenCode，建议不要把公共 API 改成 Codex 专属 API，而是保持：

~~~text
AgentRuntime
  ├── CodexRuntime
  ├── PiRuntime
  └── OpenCodeRuntime
~~~

公共层只定义 runtime-neutral 的概念：

~~~text
Session → Run → Item → Tool Call → Event
~~~

每个 adapter 自己处理：

- session id 如何映射；
- event 如何转换；
- Skill 如何安装和发现；
- permission 如何落地；
- interrupt/steer 如何实现；
- runtime 进程如何启动和关闭。

## 12. 最终记忆模型

~~~text
OpenAI Agents SDK
    = 给你积木，让你造 Agent 产品

Codex app-server
    = 把 Codex coding engine 暴露给你的产品

Pi agent
    = 一个可以阅读、修改和 fork 的 coding agent harness

OpenCode
    = 一个开源、可自托管、可通过 server 使用的 coding agent 应用
~~~

最重要的不是记住哪个名字更强，而是记住这条判断：

> 如果你要控制 Agent，使用 Agent framework。  
> 如果你要复用一个已经完成的 coding Agent，使用 coding runtime/server。  
> 如果你既要业务控制又要 coding 能力，就用业务 Agent 编排 coding sub-agent。

## 13. 课程练习

### 练习一：判断抽象层

下面哪个问题更适合 Agents SDK？

~~~text
A. 给一个仓库运行测试并自动修复失败用例
B. 根据客户权限决定是否允许退款，并调用订单 API
~~~

答案：B。它需要强业务约束、typed tools 和明确流程。

### 练习二：判断 runtime

~~~text
A. 让用户在浏览器中选择文件、修改代码、运行测试
B. 让客服 Agent 查询订单并生成退款申请
~~~

答案：A 更适合 Codex app-server、Pi 或 OpenCode；B 更适合 Agents SDK。

### 练习三：设计混合系统

请画出下面流程的边界：

~~~text
用户报告一个 bug
→ 查询 issue
→ 检查权限
→ 修改代码
→ 运行测试
→ 创建 PR
→ 请求人工审批
~~~

一个合理答案是：

- 查询 issue、检查权限、创建 PR、人工审批：业务 Agent / typed tools。
- 修改代码、运行测试：Codex、Pi 或 OpenCode coding runtime。
