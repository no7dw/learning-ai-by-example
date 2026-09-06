# Agent Pattern 课程：根据场景选择 Single Agent 还是 Multi-Agent

这门课解决一个实际问题：同一个 LLM，为什么有时只需要一个 Agent，有时需要 ReAct、Plan-and-Execute、Graph/Workflow，甚至多个 Agent 协作？

答案不在于“哪个框架更先进”，而在于任务的路径是否已知、状态是否复杂、角色是否真的不同，以及失败的代价有多高。

本文使用两组案例：

- 电商：商品问答、商品搜索、订单售后、销售分析、促销活动和客服团队。
- `fastestai-api`：当前 `/v2/chat/streaming` 的 single-agent、动态工具、Plan、Team 和流式运行路径。

已有 presentation 中的内容：

- [基础版 Agent Team presentation](presentations/ai-agent-dev-team-presentation-basic.html)
- [高级版 Agent Team presentation](presentations/ai-agent-dev-team-presentation-advanced.html)
- [Chat Agent 完整链路](chat-agent.md)
- [工具调用与函数调用](tool-calling-course.md)

## 1. 先理解 Pattern 是什么

Agent pattern 不是一个类名，也不是某个框架的专属功能。它描述的是：

```text
谁决定下一步？
状态保存在哪里？
步骤能不能并行？
失败后从哪里恢复？
什么时候结束？
```

可以从两个维度看模式：

```text
认知能力:  感知 -> 推理 -> 规划 -> 行动 -> 反思 -> 协作

执行拓扑:  单节点 -> 循环 -> 顺序 -> 分支 -> 并行 -> 层级
```

例如：

- ReAct 是“推理和行动交替”的循环。
- Plan-and-Execute 是“先生成计划，再执行计划”的顺序和重规划。
- Graph/Workflow 是“状态和节点由显式边连接”的控制流。
- Multi-Agent 是“多个角色按消息或共享状态协作”的分布式执行。

模式可以组合。一个真实系统可能是：

```text
Supervisor Multi-Agent
        |
        +--> Researcher: ReAct + tools
        +--> Analyst: Plan-and-Execute
        +--> Approval: Graph / Workflow
        +--> Finalizer: deterministic validation + LLM synthesis
```

## 2. 先看总比较表

| 模式 | 控制谁负责 | 状态形态 | 适合场景 | 优点 | 缺点 | 电商例子 |
| --- | --- | --- | --- | --- | --- | --- |
| Direct LLM / Single Agent | 一个模型或一个 Agent | 对话上下文 | 问答、简单分类、一次工具调用 | 低延迟、低成本、容易调试 | 复杂任务容易漏步骤 | “这双鞋是什么材质？” |
| ReAct | Agent 在每一轮决定下一步 | 隐式消息历史 | 开放式搜索、工具调用、故障排查 | 自适应，遇到新信息能调整 | 路径不可预测，可能循环和浪费 token | “帮我找适合雨天通勤的鞋并比较库存” |
| Plan-and-Execute | Planner 生成计划，Executor 执行 | 计划 + step results | 目标明确、步骤较多、依赖可描述 | 全局视角强，进度清楚，可并行 | 计划可能过时，先规划会增加延迟 | “分析本月销售、退货和广告效果并给出建议” |
| Graph / Workflow | 代码或图状态决定下一节点 | 显式 state | 业务规则、审批、重试、可恢复流程 | 可测试、可追踪、边界清楚 | 分支多时维护成本高，灵活性较低 | “退货审核 -> 风险判断 -> 人审 -> 退款” |
| Supervisor Multi-Agent | Manager 选择角色和交接 | 消息 + 共享任务状态 | 专业角色确实不同，任务可拆分 | 专业化、可并行、团队边界清晰 | 通信成本高，容易重复工作 | “商品、广告、库存、客服分别分析活动” |
| Peer-to-Peer Multi-Agent | Agent 之间直接协商 | 消息网络 | 角色平等、协作关系动态 | 灵活，少一个中心调度器 | 难以预测和停止，排查困难 | 多个供应商 Agent 协商方案 |
| Hybrid | 代码控制关键边界，Agent 处理开放部分 | state + messages + artifacts | 生产系统和复杂任务 | 兼顾确定性和适应性 | 设计和观测要求最高 | Workflow 管订单，ReAct 做商品研究，Team 做活动方案 |

默认选择规则很简单：

```text
能用普通函数解决？       -> 普通函数
需要一次 LLM 判断？       -> Direct LLM
需要边走边发现信息？       -> Single Agent / ReAct
步骤和目标已经清楚？       -> Plan-and-Execute
规则、审批和状态很重要？   -> Graph / Workflow
专业角色真的不同？         -> Multi-Agent
```

不要因为任务“听起来很复杂”就直接使用 Multi-Agent。先证明一个 Agent 不够，再增加拓扑。

## 3. 场景决策树

```text
                          +------------------+
                          | 用户任务          |
                          +--------+---------+
                                   |
                                   v
                    +--------------+--------------+
                    | 路径是否已经完全知道？      |
                    +--------------+--------------+
                         | yes                | no
                         v                    v
                 Graph / Workflow       +-----+----------------+
                                        | 需要连续使用工具、   |
                                        | 根据观察调整下一步？ |
                                        +-----+----------------+
                                          | yes            | no
                                          v                v
                                       ReAct       +------+---------+
                                                   | 步骤多且目标、  |
                                                   | 依赖较清楚？    |
                                                   +------+---------+
                                                      | yes      | no
                                                      v          v
                                               Plan-and-Execute  Single Agent

无论上面选哪一个，都再问：

         是否存在真正不同的专业责任？
                    |
             yes    v    no
              +--> Multi-Agent
              |
              +--> 保持单 Agent，必要时增加工具或 Workflow 节点
```

这棵树不是绝对规则。它的作用是强迫我们先描述任务的结构，再决定架构。

## 4. Single Agent：从一个 Agent 开始

### 4.1 什么是 Single Agent

Single Agent 是一个 Agent 持有任务上下文、工具和目标，并独立完成任务：

```text
User request
      |
      v
Single Agent
      |
      +--> think
      +--> call tool
      +--> observe result
      +--> answer or continue
```

它不等于“只能调用一次工具”。一个 Single Agent 可以内部运行多个 ReAct loop，也可以动态发现和加载工具。

### 4.2 适合的电商场景

```text
用户: “查一下订单 1001 现在到哪里了。”

Single Agent
    |
    +--> get_order_status(order_id=1001)
    +--> 根据结果回答
```

即使订单状态查询需要鉴权、超时和重试，任务仍然只有一个主要责任：查询并解释状态。增加客服 Agent、物流 Agent 和经理 Agent 只会增加交接成本。

### 4.3 优点和缺点

| 优点 | 缺点 |
| --- | --- |
| 上下文只有一份，交接少 | 任务很长时上下文会膨胀 |
| token、延迟和日志成本较低 | 一个 Agent 可能在多个责任之间迷失 |
| 最容易复现和调试 | 很难对每个步骤设置不同的权限 |
| 工具调用链路直接 | 失败恢复通常依赖一个 loop |

### 4.4 `fastestai-api` 的对应路径

`fastestai-api` 在复杂聊天路径中可以根据已有 Agent 数量决定 single-agent 或 workflow：

```text
get_context_by_agents(agents, plan)
        |
        +--> len(agents) == 1 and no plan
        |       |
        |       +--> single_agent_loop
        |
        +--> multiple agents + plan
                |
                +--> IterativeWorkflow
```

当前真实代码中的单 Agent 仍可能是一个配置了工具的 `ReActAgent`，所以“single”描述的是角色数量，不是执行步数。

源码：

```text
fastestai-api/src/fastestai/chat/agent.py:234-300
fastestai-api/src/fastestai/agents/react.py:27-120
```

## 5. ReAct：边想边做，适合探索型任务

### 5.1 ReAct 的核心循环

ReAct 把推理、行动和观察放在一个循环里。模型不需要一开始就知道完整路径：

```text
        +----------------------+
        |                      |
        v                      |
      Thought                 |
        |                      |
        v                      |
      Action -----------------+
        |
        v
    Observation
        |
        +--> objective complete? -- yes --> Final answer
        |
        no
        +--------------------------> Thought
```

在生产实现中，Thought 不一定应该发送给客户端。系统只需要记录可审计的 action、tool call、result 和状态变化。

### 5.2 电商例子：商品研究

用户请求：

```text
“帮我找一双适合新加坡雨季通勤的黑色运动鞋，预算 150 新币，比较三个可购买选项。”
```

ReAct Agent 可能这样推进：

```text
1. 提取约束：黑色、雨季、通勤、预算、可购买
2. 搜索商品目录
3. 发现商品结果缺少防水信息，调用商品详情工具
4. 发现库存需要按地区查询，调用区域库存工具
5. 比较价格、评分、防水和库存
6. 生成三个候选和理由
```

这里很难提前写死所有工具调用，因为商品详情和库存结果会改变下一步。ReAct 的价值就在于根据 Observation 调整路径。

### 5.3 `fastestai-api` 的真实例子

`ReActAgent` 在 `dynamic_toolset=True` 时增加两个工具：

```text
get_avaliable_tools(queries, limit)
apply_selected_tools(tools)
```

实际含义是：

```text
Agent 判断已有工具不够
        |
        v
搜索工具注册表
        |
        v
返回候选工具描述
        |
        v
Agent 选择 tool id
        |
        v
加载并加入当前 Agent
        |
        v
继续 ReAct 执行
```

这是一个很实用的 Single Agent 设计：不把所有工具一次性塞进 prompt，而是让 Agent 按需扩展自己的工具集。

源码：

```text
fastestai-api/src/fastestai/agents/react.py:42-120
fastestai-api/src/fastestai/tools/tool_adapter/mcp/omni.py:460-693
```

### 5.4 ReAct 什么时候不合适

以下情况不应该只依赖 ReAct：

- 退款、支付、删除等动作必须经过固定审批。
- 一定要按顺序执行，跳过一步会造成业务错误。
- 需要恢复到明确的中间状态。
- 需要证明某个步骤一定执行过。
- 多个专业角色之间有明确的输入输出契约。

这些场景应把 ReAct 放进受控的 Workflow 节点，或升级到 Plan-and-Execute / Multi-Agent。

## 6. Plan-and-Execute：先规划，再执行

### 6.1 模式结构

```text
Goal
 |
 v
Planner
 |
 v
Plan: [S1, S2, S3]
       |   |   |
       +---+---+----> Execute steps
              |
              v
        Replan if needed
              |
              v
          Synthesize
```

Plan-and-Execute 比 ReAct 多了一份显式的全局计划。计划可以让每一步的目标、责任 Agent、依赖和结果更清楚。

### 6.2 电商例子：月度经营分析

用户请求：

```text
“分析 4 月销售表现，结合广告、库存和退货，给出 5 月促销建议。”
```

可以先生成：

```text
Step 1: 获取 4 月订单、GMV、客单价和品类数据
Step 2: 获取广告消耗、点击、转化和归因数据
Step 3: 获取库存、缺货和补货周期
Step 4: 获取退货率和退货原因
Step 5: 合并结果，识别问题和机会
Step 6: 生成 5 月促销建议和风险说明
```

Step 1 到 Step 4 如果互不依赖，可以并行；Step 5 必须等待它们完成。

```text
                         +--> Sales data
                        /
Goal -> Plan -> Fan-out +--> Ads data ----+
                        \                 |
                         +--> Inventory --+--> Analysis -> Recommendation
                        /                  |
                         +--> Returns ----+
```

### 6.3 重规划不是无限重试

执行后如果广告 API 失败，Planner 可以把剩余计划改成：

```text
已完成: sales, inventory, returns
失败: ads
替代: 使用订单侧归因数据，并标记广告结论置信度降低
```

重规划要有上限：

```text
max_iterations
max_plan_steps
max_tool_calls
deadline
```

否则 Plan-and-Execute 会变成一个没有停止条件的 ReAct loop。

### 6.4 `fastestai-api` 的对应实现

`IterativeWorkflow` 接收 `members` 和 `plan`。每轮执行计划后，使用结构化 JSON 生成 `ReplanResult`，判断目标是否完成或生成剩余步骤，最后再用一个 synthesis prompt 合并结果。

```text
IterativeWorkflow
       |
       +--> extract objective
       +--> replan
       +--> execute current steps
       +--> collect StepResult
       +--> replan until complete or max_iterations
       +--> synthesize final result
```

源码：

```text
fastestai-api/src/fastestai/agents/team/workflow.py:113-280
fastestai-api/src/fastestai/chat/agent.py:212-300
```

### 6.5 优点和缺点

| 优点 | 缺点 |
| --- | --- |
| 先看全局，适合多步骤目标 | 规划本身增加一次或多次 LLM 调用 |
| 计划和结果便于展示进度 | 外部世界变化会让计划过时 |
| 独立步骤可以并行 | 步骤依赖判断错误会导致等待或失败 |
| 可以在每轮重规划 | Planner 可能生成过细、过长或重复计划 |

## 7. Graph / Workflow：让控制流显式化

### 7.1 Graph 和 Workflow 的共同点

Graph/Workflow 把 Agent 执行拆成节点和状态：

```text
START
  |
  v
[Load order]
  |
  v
[Check policy] ---- rejected ----> [Explain]
  |
 approved
  v
[Risk check] ------ high --------> [Human approval]
  |
 low
  v
[Refund]
  |
  v
 END
```

节点可以是普通函数、工具、LLM 或 Agent。关键在于下一步由显式状态和边决定，而不是只存在于一段 prompt 中。

### 7.2 电商例子：退货和退款

退款流程适合 Workflow，因为规则和审计比自由探索更重要：

```text
订单号
  |
  v
读取订单和支付状态
  |
  v
检查是否在退货期限内
  +--> no  -> 拒绝并解释政策
  |
 yes
  v
检查商品类别和退货原因
  +--> 高风险 -> 人工审核
  |
 normal
  v
创建退货单
  |
  v
仓库确认收货
  +--> timeout -> 查询状态 / 重试
  |
 received
  v
发起退款
  |
  v
记录审计并通知用户
```

这里可以在“退货原因解释”节点使用 LLM，但不能让模型自行跳过期限检查或直接决定退款金额。

### 7.3 Workflow 和 Plan-and-Execute 的区别

| 问题 | Plan-and-Execute | Graph / Workflow |
| --- | --- | --- |
| 谁生成路径 | Planner 可以动态生成 | 通常由代码或图定义 |
| 路径是否稳定 | 中等，可能重规划 | 高，边和条件明确 |
| 适合什么 | 研究、分析、开放目标 | 业务流程、审批、状态机 |
| 失败恢复 | 重新生成计划 | 从 checkpoint 节点恢复 |
| 可测试性 | 需要评估计划质量 | 可以对节点和边做确定性测试 |
| 变更成本 | prompt/Planner 易改 | 图结构和状态契约需要维护 |

两者可以组合：用 Workflow 控制大的业务边界，在某个“研究”节点里运行 Plan-and-Execute。

### 7.4 `fastestai-api` 中的对应关系

`fastestai-api` 的 `IterativeWorkflow` 是一个真实的 workflow 编排实现：它有成员、Step、StepResult、重规划和最大迭代次数。它不是一个通用的可视化 Graph 引擎，但足以展示“状态、步骤、Agent 责任和终止条件”如何落地。

```text
plan: Sequence[Step]
Step: agent_name + description
members: list[ChatAgent]
PlanResult: objective + step_results + is_complete
ReplanResult: steps + is_complete
```

源码：

```text
fastestai-api/src/fastestai/agents/team/models.py
fastestai-api/src/fastestai/agents/team/workflow.py:113-280
```

## 8. Multi-Agent：什么时候真的需要多个 Agent

### 8.1 Multi-Agent 不是“多开几个 prompt”

多个 Agent 有自己的职责、工具、上下文和交接协议：

```text
                         +-------------+
                         | Manager     |
                         +------+------+
                                |
                  +-------------+-------------+
                  |             |             |
                  v             v             v
             Product       Inventory       Marketing
              Agent           Agent          Agent
                  \             |             /
                   +------------+------------+
                                v
                         Reviewer / Deliver
```

如果三个 Agent 都能访问同样的工具、读取同样的上下文、完成同样的工作，那么它们不是专业分工，而是重复调用。

### 8.2 电商例子：大促活动方案

用户请求：

```text
“为下个月的大促设计一套活动方案，兼顾商品组合、库存、广告预算、客服话术和风险。”
```

可以拆为：

| Agent | 责任 | 输入 | 输出 |
| --- | --- | --- | --- |
| Product Agent | 商品组合、价格和毛利 | 商品目录、历史销售 | 商品候选和毛利约束 |
| Inventory Agent | 库存、补货和履约风险 | 库存、供应商、仓库 | 可售量和风险 |
| Marketing Agent | 投放渠道、预算和素材方向 | 广告历史、受众数据 | 投放建议 |
| Customer Agent | FAQ、客服脚本和升级规则 | 历史咨询、政策 | 话术和人工升级条件 |
| Reviewer | 检查冲突和完整性 | 上述结果 | 最终方案和缺口 |

```text
User goal
    |
    v
Manager / Planner
    |
    +--> Product Agent ----+
    +--> Inventory Agent -+--> conflict check -> Reviewer -> Plan
    +--> Marketing Agent -+
    +--> Customer Agent ---+
```

这里使用 Multi-Agent 的理由是责任不同、工具不同、输出可以并行，而且每个领域可以独立评估。不是因为“一个模型不够聪明”。

### 8.3 Supervisor、Sequential 和 Parallel

| 拓扑 | ASCII | 适合 |
| --- | --- | --- |
| Supervisor | Manager -> selected Agent -> Manager | 任务需要动态分派 |
| Sequential | A -> B -> C | 前一步输出是后一步输入 |
| Parallel / Fan-out | A -> {B, C, D} -> Gather | 子任务互相独立 |
| Hierarchical | Manager -> Team Manager -> specialists | 大组织、多层责任 |
| Peer-to-peer | A <-> B <-> C | 平等协商，但要有终止条件 |

电商大促分析通常是 `Supervisor + Parallel + Gather`，退款流程通常是 `Workflow`，不要为了统一而全部 Multi-Agent 化。

### 8.4 Multi-Agent 的通信契约

每个 Agent 之间不要只传自然语言长文本。定义最小消息结构：

```json
{
  "task_id": "campaign-2026-05",
  "sender": "inventory_agent",
  "recipient": "reviewer",
  "type": "evidence",
  "payload": {
    "available_sku_count": 24,
    "risk_items": ["SKU-9"]
  },
  "evidence": ["inventory_snapshot_2026_04_30"],
  "created_at": "2026-04-30T10:00:00Z"
}
```

还要明确：

- 谁拥有并修改共享状态。
- 哪些结果是事实，哪些只是建议。
- Agent 失败时由谁重试、替代或终止。
- 何时停止协作，最大轮次是多少。
- 哪些工具权限只能由特定 Agent 使用。

### 8.5 `fastestai-api` 的真实 Multi-Agent 路径

`fastestai-api` 有几种相关能力：

```text
dispatch
   -> 根据用户意图选择 agent 或 app

build_agent_team
   -> 把 Agent 配置构造成 ChatAgent members

MafTeam
   -> 没有显式 plan 时，由 selector 决定下一位成员

IterativeWorkflow
   -> 有显式 plan 时，按 Step 和 Agent 执行并重规划
```

在 `chat/auto.py` 中，`build_agent_team` 没有 plan 时创建 `MafTeam`，并使用 `max_rounds=50`；有 plan 时创建 `IterativeWorkflow`。在 `chat/agent.py` 中，多 Agent workflow 则要求 plan 与 members 匹配，这也是阅读不同运行入口时需要留意的实现边界。

源码：

```text
fastestai-api/src/fastestai/chat/dispatch.py:224-454
fastestai-api/src/fastestai/chat/auto.py:196-221
fastestai-api/src/fastestai/chat/agent.py:212-300
fastestai-api/src/fastestai/agents/runtime.py:19-220
```

## 9. `fastestai-api` 原始 Chat Agent 用例

### 9.1 从 `/v2/chat/streaming` 进入

这个项目的原始使用方式不是一个单独的“pattern demo”，而是一个根据请求和任务复杂度选择路径的 Chat Agent：

```text
POST /v2/chat/streaming
          |
          v
streaming_chat_v2
          |
          +--> split messages
          +--> classify simple / complex
          +--> prepare context, memory, agents, plan
          +--> streaming_chat_auto
```

源码：

```text
fastestai-api/src/fastestai/api/routers/chat.py:197-289
fastestai-api/src/fastestai/chat/auto.py:329-447
```

### 9.2 当前路径和 Pattern 的映射

| 请求或运行状态 | 主要模式 | 解释 |
| --- | --- | --- |
| Simple task，没有显式 Agent、Team、Plan | Direct LLM / fast path | 低延迟，跳过复杂 Agent 准备 |
| 一个 Agent，没有 plan | Single Agent，通常由 ReAct loop 执行 | 一个角色自己判断工具和下一步 |
| 一个 Agent，但从其描述中提取出明确 workflow | Plan / Workflow | `extract_workflow_from_agent` 可以生成 Step |
| 多个 Agent，有 plan | IterativeWorkflow / Plan-and-Execute | Step 指定由哪个 Agent 执行 |
| 多个成员，没有显式 plan 的 team path | MafTeam / selector-driven coordination | 由 selector 选择下一位成员 |
| `omni=True` 且需要发现能力 | Supervisor-like OmniAgent | 可 dispatch agent/app、建立 team、加载工具 |

简化图：

```text
                         +------------------+
                         | User message     |
                         +--------+---------+
                                  |
                                  v
                    +-------------+-------------+
                    | simple task classification|
                    +-------------+-------------+
                         | yes               | no
                         v                   v
                    Fast path        Context + Memory + NLU
                                             |
                                             v
                                      prepare agents
                                             |
                       +---------------------+---------------------+
                       |                                           |
                       v                                           v
                 no explicit agents                         agents provided
                       |                                           |
                       v                                           v
                 OmniAgent dispatch                     +----------+----------+
                                                        |                     |
                                                        v                     v
                                                one agent/no plan       team or plan
                                                        |                     |
                                                        v                     v
                                                  ReAct/single       MafTeam/Workflow
```

完整的 `/v2/chat/streaming` 入口说明见 [chat-agent.md](chat-agent.md)。本课程只关注它如何体现不同 Agent pattern。

### 9.3 一个重要的工程判断

请求模型上有 `team`、`plan`、`omni` 等字段，不代表每次请求都会使用 Multi-Agent。实际运行还会经过 simple-task 分类、Agent 准备、`omnify` 和运行时条件。阅读代码时要区分：

```text
请求配置
    !=
最终执行拓扑
```

这是选择 pattern 时最容易忽略的一层：用户想要一个团队，不等于当前系统真的建立了一个团队；代码必须验证最终构建出的 runtime。

## 10. 一个完整电商场景的组合方式

### 10.1 场景

用户说：

```text
“帮我策划 618 活动：找出高潜商品，检查库存，估算广告预算，生成客服话术，最后给我一份可执行方案。”
```

不要一上来让五个 Agent 自由聊天。更可控的组合是：

```text
Workflow: 建立任务、权限和停止条件
    |
    v
Plan-and-Execute: 生成可检查的阶段计划
    |
    v
Multi-Agent fan-out
    +--> Product Agent: 商品和毛利
    +--> Inventory Agent: 库存和履约
    +--> Marketing Agent: 广告和预算
    +--> Customer Agent: 话术和升级规则
    |
    v
Graph node: conflict / policy / approval check
    |
    v
Reviewer Agent + deterministic validation
    |
    v
Final answer or human approval
```

为什么这样组合：

- 大边界是固定的，所以用 Workflow。
- 子任务结构清晰，所以用 Plan-and-Execute。
- 子任务角色真的不同，所以用 Multi-Agent。
- 预算、库存和客服政策存在业务约束，所以用确定性校验和人审。

### 10.2 哪些部分不要交给 LLM

```text
LLM 可以做：
  商品卖点提取、候选比较、广告文案草拟、客服话术草拟

代码必须做：
  库存扣减、价格和金额计算、权限检查、预算上限、审批、幂等、最终发布
```

Pattern 决定“谁来思考和交接”，不改变工具执行的安全边界。工具调用的 Auth、Approval、Rate Limit、Timeout、Retry、Cache 仍然要放在 Tool Gateway middleware 中。

## 11. 选择 Single Agent 还是 Multi-Agent

### 11.1 用能力差异，而不是数量差异

需要 Multi-Agent 的信号：

```text
专业知识明显不同
工具权限明显不同
上下文需要隔离
子任务可以并行
每个角色有独立质量标准
需要不同模型或不同成本配置
```

不需要 Multi-Agent 的信号：

```text
只是想让回答更“聪明”
所有角色都访问同样的工具
所有角色都读完整上下文
没有明确的交接结果
没有停止和仲裁机制
```

### 11.2 量化一个初步选择

可以用一个粗略的设计评分帮助讨论：

```text
Multi-Agent 倾向分数 =
  responsibility_difference
  + tool_permission_difference
  + context_isolation_need
  + parallelism_value
  + independent_eval_need
```

只要“角色差异”和“独立评估”都是低，先选 Single Agent。分数不是算法输出，而是让团队把争论从框架偏好转成任务事实。

### 11.3 模式对比和电商场景总表

| 场景 | 推荐模式 | 不优先选择 | 原因 |
| --- | --- | --- | --- |
| 商品属性问答 | Direct LLM 或 Single Agent | Multi-Agent | 没有任务分解价值 |
| 商品搜索和比较 | ReAct | 固定 Graph | 搜索结果会改变下一步 |
| 订单状态查询 | Single Agent + tool | Team | 单一责任、单一结果 |
| 退货退款 | Graph / Workflow | 自由 ReAct | 审批、政策、幂等和审计重要 |
| 月度经营分析 | Plan-and-Execute | 完全自由聊天 | 目标明确，步骤可并行 |
| 大促活动方案 | Hybrid Multi-Agent | 一个 Agent 包办一切 | 专业角色不同且需要汇总 |
| 供应商协商 | Peer-to-peer 或 Supervisor | 线性 Workflow | 多轮协商和动态反馈 |
| 批量上架商品 | Workflow + bounded workers | 无限制 Multi-Agent | 需要限速、重试、幂等和进度 |

## 12. 生产落地时每个 Pattern 都要补的能力

模式表只说明控制流，不代表生产可用。每种模式都必须补这些横向能力：

```text
Identity / Auth
Approval and policy
Rate limit / quota
Timeout / cancellation
Retry / idempotency
Cache isolation
State checkpoint
Trace / audit
Evaluation
Termination condition
```

### 12.1 ReAct 的生产边界

```text
max_steps
max_tool_calls
allowed_tools
tool timeout
same-action detection
final answer fallback
```

### 12.2 Plan-and-Execute 的生产边界

```text
plan schema
step owner
dependency declaration
parallelism limit
replan budget
partial result policy
```

### 12.3 Graph / Workflow 的生产边界

```text
state schema
checkpoint version
transition condition
human approval node
resume / cancel behavior
compensation action
```

### 12.4 Multi-Agent 的生产边界

```text
message schema
state ownership
role permission
max rounds
duplicate message handling
reviewer / arbitrator
```

如果这些内容没有被设计，Multi-Agent 不是“更高级”，而是把一个难调试的 loop 变成多个难调试的 loop。

## 13. 流程、状态和消息怎么选

```text
只有一次调用？
  -> request / response

需要连续工具调用？
  -> Agent message history

需要可恢复的长流程？
  -> persisted state + checkpoint

需要多个角色异步协作？
  -> message contract + event log

需要大数据或文件传递？
  -> artifact_id / dataset_id + metadata
```

不要把大表格和长文档直接塞进每个 Agent 的消息。传递摘要、schema、sample、`dataset_id` 或 artifact ID，让需要详情的 Agent 再调用 detail tool。这个原则与 [工具调用课程](tool-calling-course.md) 中的结构化结果和大数据二次检索一致。

## 14. 评估不同 Pattern

不要只比较最终回答是否正确。至少按下面维度评估：

| 维度 | Single / ReAct | Plan-and-Execute | Graph / Workflow | Multi-Agent |
| --- | --- | --- | --- | --- |
| 最终正确率 | answer accuracy | plan + answer accuracy | transition + answer accuracy | team + answer accuracy |
| 工具选择 | wrong tool rate | step tool fit | node tool fit | role tool fit |
| 过程成本 | steps / tokens | planning + execution | node count | messages + agents |
| 稳定性 | loop variance | plan variance | path determinism | coordination variance |
| 恢复能力 | restart loop | replan | checkpoint resume | message/state recovery |
| 安全性 | tool policy | step policy | edge and node policy | role and delegation policy |

建议建立同一组电商评测集：

```text
商品问答
商品比较
订单查询
退款判断
销售分析
大促策划
异常库存处理
```

对每个任务分别跑 Single、ReAct、Plan、Workflow 和 Multi-Agent，比较质量、延迟、token、工具调用次数和危险动作拦截率。不要以 demo 中“看起来会协作”作为选型证据。

## 15. 典型失败和排查顺序

### 15.1 Agent 选错模式

症状：一个简单订单查询创建了多个 Agent，延迟和成本都很高。

排查：

```text
请求
  -> simple classification
  -> actual agents
  -> actual plan
  -> actual runtime type
  -> tool calls
```

先看最终 runtime，不要只看请求里的 `team` 或 presentation 图。

### 15.2 ReAct 无限循环

检查：

- 是否重复调用相同工具和相同参数。
- 工具失败是否被当成普通文本，导致 Agent 继续猜。
- 是否有 `max_steps` 和 deadline。
- 是否把“没有找到数据”误判为“继续搜索”。

### 15.3 Plan 生成了不可执行步骤

检查：

- 每个 Step 是否有明确 Agent owner。
- 依赖数据是否在前置 Step 产出。
- Step 是否能转成工具调用或可验证动作。
- 是否支持部分失败和重规划。

### 15.4 Workflow 分支越来越多

不要把所有自然语言变化都硬编码成边。固定业务不变量放在 Workflow，开放问题交给一个受限 Agent 节点；否则 Graph 会变成不可维护的 prompt 状态机。

### 15.5 Multi-Agent 输出互相冲突

建立 Reviewer 或 deterministic validator，并规定来源优先级：

```text
业务数据库事实
    > 工具结构化结果
    > Agent 推断
    > Agent 自我评价
```

冲突不能靠最后一个 Agent “感觉哪个更对”来解决。

## 16. 学习和实现路线

```text
Step 1: Direct LLM
  商品属性问答

Step 2: Single Agent + one tool
  订单状态查询

Step 3: ReAct + multiple tools
  商品搜索、详情、库存和价格比较

Step 4: Plan-and-Execute
  月度销售和广告分析

Step 5: Graph / Workflow
  退货、退款、审批和异步履约

Step 6: Multi-Agent
  大促活动的商品、库存、营销、客服协作

Step 7: Hybrid production system
  Workflow boundary + Agent nodes + middleware + evaluation
```

每一步都先记录：输入、选择、工具调用、状态、结果、耗时和成本。这样升级模式时，才能证明复杂度真的换来了业务收益。

## 17. 源码阅读顺序

### 17.1 先读 Chat 入口

```text
fastestai-api/src/fastestai/api/routers/chat.py:197-289
fastestai-api/src/fastestai/chat/auto.py:261-447
```

关注：simple task 分支、context、memory、agents、plan、`omnify` 和 stream。

### 17.2 再读 Single Agent / ReAct

```text
fastestai-api/src/fastestai/chat/agent.py:234-300
fastestai-api/src/fastestai/agents/react.py:27-120
fastestai-api/src/fastestai/agents/maf_runtime.py:1-240
```

关注：工具列表、动态工具搜索、MCP adapter、tool call event 和最终响应。

### 17.3 再读 Plan / Workflow

```text
fastestai-api/src/fastestai/agents/team/models.py
fastestai-api/src/fastestai/agents/team/workflow.py:113-280
fastestai-api/src/fastestai/agents/runtime.py:19-220
```

关注：Step、PlanResult、ReplanResult、selector、max iterations 和 termination。

### 17.4 最后读 Multi-Agent 编排

```text
fastestai-api/src/fastestai/chat/dispatch.py:224-454
fastestai-api/src/fastestai/chat/omni.py:90-430
fastestai-api/src/fastestai/chat/auto.py:196-221
```

关注：agent/app dispatch、team members、MafTeam、IterativeWorkflow 和结果 synthesis。

## 18. 课程总结

```text
简单任务       -> Single Agent
探索任务       -> ReAct
目标明确多步骤 -> Plan-and-Execute
规则和状态强   -> Graph / Workflow
角色真正不同   -> Multi-Agent
生产系统       -> Hybrid
```

选择模式时记住三句话：

1. 先看任务的路径、状态、责任和失败代价，再选框架。
2. Multi-Agent 解决专业分工和并行规模，不是默认的“更强模型”。
3. 生产系统最终依赖状态契约、权限、停止条件、观测和评估，而不是一张漂亮的 Agent 拓扑图。

## 源码和材料索引

| 材料 | 用途 |
| --- | --- |
| [ai-agent-dev-team-presentation-basic.html](presentations/ai-agent-dev-team-presentation-basic.html) | 模式决策树、模式框架和基础 Multi-Agent 介绍 |
| [ai-agent-dev-team-presentation-advanced.html](presentations/ai-agent-dev-team-presentation-advanced.html) | ReAct、Plan-and-Execute、Graph/Workflow、Multi-Agent 高阶图示 |
| `fastestai-api/src/fastestai/api/routers/chat.py` | `/v2/chat/streaming` 入口 |
| `fastestai-api/src/fastestai/chat/auto.py` | simple path、Agent path、MafTeam 和 Workflow 选择 |
| `fastestai-api/src/fastestai/chat/agent.py` | single-agent 和多 Agent workflow 运行 |
| `fastestai-api/src/fastestai/agents/react.py` | ReAct、动态工具发现和工具加载 |
| `fastestai-api/src/fastestai/agents/team/workflow.py` | Plan、重规划和结果合成 |
| `fastestai-api/src/fastestai/agents/runtime.py` | Team selector、成员选择和终止 |
| `fastestai-api/src/fastestai/chat/dispatch.py` | Agent / App dispatch |
