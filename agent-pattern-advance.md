# Agent Framework & Multi-Agent Framework

## 课程目标

这门课程不以学习某一个 Agent SDK 的 API 为目标，而是回答三个问题：

1. **Agent Framework 到底解决什么问题？**
2. **多个 Agent 为什么需要 Orchestration？**
3. **OpenAI Agents SDK、Microsoft Agent Framework、LangGraph 在 Multi-Agent 上到底有什么区别？**

最终希望建立这样一套认知：

```text
LLM
 ↓
Agent
 ↓
Agent Loop
 ↓
Multi-Agent
 ↓
Orchestration Pattern
 ↓
Agent Framework
 ↓
Production Agent System
```

---

# 01. Agent Framework 到底解决什么问题？

## 1.1 从 LLM 到 Agent

最开始我们直接调用 LLM：

```python
response = llm("分析这个商品为什么销量下降")
```

LLM 只能产生文本。

但是电商业务中，一个真正的任务往往需要：

* 查询商品销量
* 查询广告数据
* 查询库存
* 查询竞品
* 分析数据
* 修改 Listing
* 最后生成报告

于是变成：

```text
用户：
分析 SKU A123 为什么最近销量下降
        ↓
Agent
        ↓
思考下一步需要什么
        ↓
查询销量
        ↓
查询广告
        ↓
查询库存
        ↓
分析
        ↓
输出结论
```

这就是 Agent。

---

# 1.2 Agent 最核心的东西：Agent Loop

一个最简单的 Agent 可以理解成：

```text
User
 ↓
LLM
 ↓
需要工具？
 ├── No → Final Answer
 │
 └── Yes
       ↓
     Tool
       ↓
   Observation
       ↓
      LLM
       ↓
      ...
```

例如：

```python
from agents import Agent, Runner

agent = Agent(
    name="Ecommerce Analyst",
    instructions="""
    Analyze ecommerce performance.
    Use available tools when you need actual business data.
    Do not guess data.
    """
)

result = await Runner.run(
    agent,
    "为什么 SKU A123 最近销量下降？"
)

print(result.final_output)
```

这里真正运行 Agent 的不是：

```python
Agent(...)
```

而是：

```python
Runner.run(...)
```

可以把它理解成：

```text
Agent
=
定义“谁”

Runner
=
负责“怎么跑”
```

---

# 1.3 为什么没有看到 ReAct？

很多人第一次学习 Agent Framework 会问：

> 为什么代码里面没有 `ReAct`？

因为 ReAct 更像是一种 **Agent execution pattern**，而不是必须暴露出来的 API。

典型 ReAct：

```text
Thought
  ↓
Action
  ↓
Observation
  ↓
Thought
  ↓
Action
  ↓
Observation
  ↓
Final
```

现代 Agent Framework 通常把这个 Loop 封装起来。

所以代码不会写：

```python
agent = Agent(mode="react")
```

而是：

```python
Runner.run(agent, task)
```

Framework 内部负责：

```text
LLM
 ↓
Tool Call
 ↓
Tool Result
 ↓
LLM
 ↓
Tool Call
 ↓
...
```

---

# 1.4 Agent Framework 解决什么？

如果自己实现 Agent Loop，大致需要：

```python
while True:

    response = llm(messages, tools=tools)

    if response.is_final:
        return response

    if response.tool_call:
        result = execute_tool(response.tool_call)

        messages.append(response)
        messages.append(result)
```

实际生产环境还需要处理：

* tool execution
* parallel tool calls
* retries
* errors
* handoff
* guardrails
* tracing
* approvals
* max turns
* cancellation

所以 Agent Framework 的价值之一就是：

> **把 Agent Runtime 从应用代码中抽出来。**

---

# 02. Agent Runtime：ReAct、Loop、Iteration、Reflection

这一部分非常重要，因为这些概念经常被混在一起。

---

## 2.1 ReAct

ReAct 最简单的理解：

> Agent 在执行过程中不断根据观察结果决定下一步行动。

电商例子：

```text
用户：
分析 SKU A123 销量下降原因

Agent
 ↓
查询销量
 ↓
销量下降 30%
 ↓
查询广告
 ↓
广告 CTR 没变
 ↓
查询库存
 ↓
库存为 0
 ↓
结论：
主要原因是缺货
```

这是典型的：

```text
Reason
 ↓
Act
 ↓
Observe
 ↓
Reason
 ↓
Act
```

---

## 2.2 Iterative

Iteration 是更外层的循环。

例如：

```text
生成 Listing
      ↓
检查 Listing
      ↓
不合格？
      ↓
重新生成
      ↓
重新检查
      ↓
合格
```

代码可以非常简单：

```python
for i in range(3):

    result = await Runner.run(
        listing_agent,
        product_info
    )

    review = await Runner.run(
        reviewer,
        result.final_output
    )

    if review.final_output == "PASS":
        break
```

这里不是一个 Agent 内部不断调用工具。

而是：

```text
Agent Run
   ↓
Evaluator
   ↓
再一次 Agent Run
   ↓
Evaluator
```

---

## 2.3 Reflection

Reflection 是：

> Agent 对自己的结果进行 Critique，然后改进。

例如：

```text
Generate Listing
       ↓
      Critic
       ↓
发现：
- 标题关键词不足
- 五点描述重复
       ↓
Improve
       ↓
Final Listing
```

代码：

```python
draft = await Runner.run(
    listing_agent,
    product
)

critique = await Runner.run(
    critic_agent,
    draft.final_output
)

final = await Runner.run(
    listing_agent,
    f"""
    Original draft:
    {draft.final_output}

    Critique:
    {critique.final_output}

    Improve the listing.
    """
)
```

---

## 2.4 三者不要混淆

| 概念                     | 核心问题                |
| ---------------------- | ------------------- |
| **ReAct / Agent Loop** | Agent 在一次执行过程中如何行动？ |
| **Iteration**          | 是否需要再次执行？           |
| **Reflection**         | 是否先评价/批评结果，再改进？     |

可以记成：

```text
Agent Runtime
     │
     └── ReAct / Agent Loop
              │
              ↓
         Tool → Observation


Workflow Level
     │
     └── Iteration
              │
              ↓
        Evaluate → Retry


Reasoning Technique
     │
     └── Reflection
              │
              ↓
        Critique → Improve
```

---

# 03. 为什么需要 Multi-Agent？

## 3.1 一个 Agent 什么都做会发生什么？

假设我们做一个电商 AI Assistant：

> “帮我分析这个商品销量下降，并优化 Listing，再检查广告。”

一个 Agent 可能拥有：

```text
Tools
├── Sales API
├── Inventory API
├── Ads API
├── Amazon API
├── Competitor API
├── Listing API
├── Database
├── Search
└── ...
```

Prompt 也越来越长：

```text
你是销售分析专家……
你也是广告专家……
你也是 SEO 专家……
你也是 Listing 专家……
你还需要……
```

最终：

```text
One Agent
│
├── Sales
├── Ads
├── SEO
├── Inventory
├── Competitor
├── Listing
└── Reporting
```

这会导致：

* Tool 太多
* Prompt 太复杂
* 职责不清晰
* Agent 很难判断应该做什么
* 不同任务之间容易互相干扰

---

# 3.2 Multi-Agent 的基本思想

把一个大 Agent 拆成多个专业 Agent：

```text
                  Ecommerce Manager
                  /       |       \
                 ↓        ↓        ↓
             Sales      Ads      Listing
             Agent      Agent     Agent
```

例如：

### Sales Agent

负责：

```text
销量
订单
转化率
库存
```

### Ads Agent

负责：

```text
CTR
CPC
ACOS
ROAS
Campaign
```

### Listing Agent

负责：

```text
Title
Bullet Points
Description
Keywords
```

---

# 3.3 Multi-Agent 不等于“多个 Agent 就完事”

真正的问题变成：

> **这些 Agent 怎么协作？**

例如：

```text
Sales Agent
     ↓
发现销量下降
     ↓
Ads Agent
     ↓
发现广告没有问题
     ↓
Inventory Agent
     ↓
发现库存不足
     ↓
Manager
     ↓
生成最终结论
```

这就是：

> **Agent Orchestration**

而这正是 Multi-Agent Framework 最核心的问题。

---

# 04. Multi-Agent 核心 Patterns

这一章是课程的核心。

不要把几十种“Agent Pattern”全部讲一遍。

真正需要掌握的是下面这些。

---

## 4.1 Agent-as-Tool / Manager

### 核心思想

Manager 不把控制权交给 Specialist。

而是：

```text
Manager
 ├──→ Sales Agent
 ├──→ Ads Agent
 └──→ Listing Agent
```

Specialist 完成任务之后：

```text
Specialist
 ↓
Result
 ↓
Manager
```

控制权始终在 Manager。

---

### OpenAI Agents SDK

```python
from agents import Agent, Runner

sales_agent = Agent(
    name="Sales Analyst",
    instructions="""
    Analyze ecommerce sales performance.
    """
)

ads_agent = Agent(
    name="Ads Analyst",
    instructions="""
    Analyze advertising performance.
    """
)

manager = Agent(
    name="Ecommerce Manager",
    instructions="""
    You manage ecommerce analysis.

    Use the sales analyst for sales questions.
    Use the ads analyst for advertising questions.
    Combine their findings into one conclusion.
    """,
    tools=[
        sales_agent.as_tool(
            tool_name="analyze_sales",
            tool_description="Analyze product sales performance"
        ),
        ads_agent.as_tool(
            tool_name="analyze_ads",
            tool_description="Analyze advertising performance"
        ),
    ],
)

result = await Runner.run(
    manager,
    "为什么 SKU A123 最近销量下降？"
)
```

这里：

```text
Manager
   │
   ├── analyze_sales()
   │
   └── analyze_ads()
```

Agent 本身被包装成 Tool。

---

## 4.2 Handoff / Routing

Handoff 完全不同。

这里是：

```text
                 Triage
                /  |  \
               ↓   ↓   ↓
            Sales Ads Listing
```

一旦选择：

```text
Triage
 ↓
Sales Agent
```

控制权就转移给 Sales Agent。

---

### OpenAI

```python
sales_agent = Agent(
    name="Sales Agent",
    instructions="Handle ecommerce sales questions."
)

ads_agent = Agent(
    name="Ads Agent",
    instructions="Handle ecommerce advertising questions."
)

triage = Agent(
    name="Triage",
    instructions="""
    Route the user to the appropriate specialist.
    """,
    handoffs=[
        sales_agent,
        ads_agent,
    ],
)
```

---

## 4.3 Agent-as-Tool vs Handoff

这是 Multi-Agent 最重要的区别之一。

|                  | Agent-as-Tool  | Handoff           |
| ---------------- | -------------- | ----------------- |
| 控制权              | Manager 保留     | 转移                |
| Specialist 完成后   | 回到 Manager     | 不一定回来             |
| Manager 是否负责最终答案 | 通常是            | 不一定               |
| 适合               | 专业子任务          | 路由/转交             |
| 典型例子             | Research Agent | Support → Billing |

简单记：

```text
Agent-as-Tool

Manager
 ↓
Specialist
 ↓
Manager
 ↓
Final
```

```text
Handoff

Router
 ↓
Specialist
 ↓
Final
```

---

# 4.4 Sequential Workflow

如果 Agent 有明确顺序：

```text
Product Research
       ↓
Sales Analysis
       ↓
Listing Optimization
```

就不需要让 LLM 自由决定流程。

---

### OpenAI

OpenAI 更偏向代码编排：

```python
research = await Runner.run(
    research_agent,
    product
)

analysis = await Runner.run(
    sales_agent,
    research.final_output
)

listing = await Runner.run(
    listing_agent,
    analysis.final_output
)

print(listing.final_output)
```

也就是：

```text
Python Code
 ↓
Agent 1
 ↓
Agent 2
 ↓
Agent 3
```

---

### Microsoft Agent Framework

MAF 提供明确的 Sequential Workflow：

```python
from agent_framework import SequentialBuilder

workflow = SequentialBuilder(
    participants=[
        research_agent,
        sales_agent,
        listing_agent,
    ]
).build()

result = await workflow.run(product)
```

因此：

```text
OpenAI
→ Code-driven orchestration

MAF
→ Workflow-driven orchestration
```

---

# 4.5 Concurrent / Fan-out

如果三个分析互相独立：

```text
              ┌→ Sales Agent
              │
Product ──────┼→ Ads Agent
              │
              └→ Competitor Agent
```

应该并行。

---

### OpenAI

```python
import asyncio

sales_task = Runner.run(
    sales_agent,
    product
)

ads_task = Runner.run(
    ads_agent,
    product
)

competitor_task = Runner.run(
    competitor_agent,
    product
)

sales, ads, competitor = await asyncio.gather(
    sales_task,
    ads_task,
    competitor_task
)
```

OpenAI 中通常直接用 Python orchestration。

---

### MAF

```python
from agent_framework import ConcurrentBuilder

workflow = ConcurrentBuilder(
    participants=[
        sales_agent,
        ads_agent,
        competitor_agent,
    ]
).build()

result = await workflow.run(product)
```

这里 Concurrent 是 Framework 的显式 workflow primitive。

---

# 4.6 Fan-in / Aggregation

Fan-out 后面通常就是 Fan-in：

```text
          ┌→ Sales ─────┐
          │              │
Input ────┼→ Ads ────────┼→ Aggregator
          │              │
          └→ Competitor ─┘
```

Aggregator 负责：

> 把三个 Agent 的结果组合起来。

例如：

```python
prompt = f"""
Analyze this ecommerce product.

Sales:
{sales.final_output}

Advertising:
{ads.final_output}

Competitor:
{competitor.final_output}

Give me one unified diagnosis.
"""

final = await Runner.run(
    manager,
    prompt
)
```

这就是：

```text
Fan-out
   ↓
Parallel Agents
   ↓
Fan-in
   ↓
Aggregator
```

---

# 4.7 Orchestrator → Workers

这个 Pattern 比固定的 Fan-out 更进一步。

Orchestrator 不提前知道需要几个 Worker。

例如用户说：

> “分析我们 100 个 SKU 最近销量下降的原因。”

Orchestrator 可以动态拆：

```text
Orchestrator

SKU 1   → Worker
SKU 2   → Worker
SKU 3   → Worker
...
SKU 100 → Worker
```

最后：

```text
Workers
   ↓
Aggregator
   ↓
Orchestrator
```

---

### LangGraph

LangGraph 对这个 Pattern 支持非常明确：

```text
             Orchestrator
                  ↓
          动态产生 Workers
        /    /    |    \    \
       ↓    ↓     ↓     ↓    ↓
     SKU1 SKU2  SKU3  SKU4  SKU5
        \    \   |    /    /
         └──── Aggregator ──┘
```

LangGraph 使用 Graph + `Send` 这种机制可以动态产生 worker execution。

---

### MAF

MAF 对应的是更高级的：

```text
Magentic
```

其核心思想也是：

```text
Manager
 ↓
动态分解任务
 ↓
Specialized Agents
 ↓
Manager
```

---

### OpenAI

OpenAI 可以实现：

```python
for sku in skus:
    tasks.append(
        Runner.run(
            worker_agent,
            f"Analyze SKU {sku}"
        )
    )

results = await asyncio.gather(*tasks)
```

但是这里是：

> **应用代码实现 Orchestrator-Worker**

而不是一个专门的 `OrchestratorWorkerBuilder`。

---

# 4.8 Group Chat

另一种模式是：

> 多个 Agent 在一个共享讨论中协作。

例如：

```text
               Manager
              /   |   \
             ↓    ↓    ↓
          Sales  Ads  SEO
             ↖   ↑   ↗
               ↖ ↑ ↗
```

例如：

```text
Sales Agent:
销量下降主要发生在 US 市场。

Ads Agent:
广告 CTR 没有下降。

SEO Agent:
核心关键词排名下降了 12 位。

Manager:
综合来看，主要原因是自然搜索流量下降。
```

这种模式特别适合：

* 多专家讨论
* Debate
* Complex diagnosis
* Collaborative planning

MAF 有明确的 GroupChat orchestration。

OpenAI 可以通过 Agent-as-tool 或自己编排实现，但没有同等级的 GroupChat Builder primitive。

---

# 4.9 Human-in-the-loop

电商系统中，很多 Agent 行为不能直接执行。

例如：

```text
Agent：
准备把广告预算从 $500/day 调整到 $2,000/day
       ↓
风险操作
       ↓
Human Approval
       ↓
Approved
       ↓
Execute
```

因此：

```text
Analyze
 ↓
Recommend
 ↓
Approve?
 ├── No → Stop
 └── Yes
       ↓
     Execute
```

这是生产级 Agent 非常重要的 Pattern。

---

# 05. OpenAI vs Microsoft Agent Framework vs LangGraph

这一章不要变成 API 罗列，而是回答：

> **三个框架看待 Agent Orchestration 的方式有什么不同？**

---

## 5.1 核心差异

```text
OpenAI Agents SDK

Agent
 ↓
Agent
 ↓
Python orchestration
```

```text
Microsoft Agent Framework

Agent
 ↓
Workflow
 ↓
Sequential / Concurrent / Handoff
```

```text
LangGraph

Graph
 ├── Node
 ├── Edge
 ├── State
 └── Conditional routing
```

---

## 5.2 Pattern 对比

| Pattern               | OpenAI Agents SDK | Microsoft Agent Framework | LangGraph |
| --------------------- | :---------------: | :-----------------------: | :-------: |
| Agent Loop            |         ✅         |             ✅             |     ✅     |
| Agent-as-Tool         |         ✅         |             ◐             |     ✅     |
| Handoff               |         ✅         |             ✅             |     ✅     |
| Sequential            |       ◐ Code      |             ✅             |     ✅     |
| Concurrent            |       ◐ Code      |             ✅             |     ✅     |
| Fan-out / Fan-in      |       ◐ Code      |             ✅             |     ✅     |
| Router                |         ◐         |             ◐             |     ✅     |
| Orchestrator-Worker   |         ◐         |         ✅ Magentic        |     ✅     |
| Group Chat            |         ◐         |             ✅             |     ◐     |
| Iterative Loop        |       ◐ Code      |             ✅             |     ✅     |
| Reflection            |         ◐         |             ◐             |     ◐     |
| HITL                  |         ✅         |             ✅             |     ✅     |
| Conditional Branching |       ◐ Code      |             ✅             |     ✅     |
| Durable Workflow      |         ◐         |             ✅             |   **✅**   |

这里最重要的是：

> **不要把“可以实现”和“Framework 明确支持”混为一谈。**

---

## 5.3 OpenAI：Agent-centric

OpenAI Agents SDK 最自然的思路：

```python
manager = Agent(
    name="Ecommerce Manager",
    tools=[
        sales_agent.as_tool(...),
        ads_agent.as_tool(...),
    ]
)
```

然后：

```python
Runner.run(manager, task)
```

它的核心抽象是：

> **Agent**

Workflow 很多时候由 Python 控制。

---

## 5.4 MAF：Workflow-centric

MAF 更强调：

```python
workflow = SequentialBuilder(
    participants=[...]
).build()
```

或者：

```python
workflow = ConcurrentBuilder(
    participants=[...]
).build()
```

所以它的核心抽象更接近：

> **Agent + Workflow**

---

## 5.5 LangGraph：Graph-centric

LangGraph 的核心思想：

```text
Graph
│
├── Node
│
├── Edge
│
├── Conditional Edge
│
└── State
```

电商分析：

```text
START
  ↓
Classify
  ↓
 ┌───────────────┐
 ↓               ↓
Sales         Advertising
 ↓               ↓
 └───────┬───────┘
         ↓
     Aggregator
         ↓
       Report
         ↓
        END
```

所以：

> **LangGraph 不是简单的 Multi-Agent SDK，而是一个 Agent/Workflow Graph runtime。**

---

# 06. Agent Factory：Agent 是固定的还是动态产生的？

这一章解决一个非常重要的问题：

> “我们的系统到底需要提前定义多少 Agent？”

---

## 6.1 固定 Agent

最简单：

```text
Ecommerce System
│
├── Sales Agent
├── Ads Agent
├── Listing Agent
└── Customer Service Agent
```

启动系统的时候这些 Agent 就已经存在。

---

## 6.2 Dynamic Agent

更进一步：

```text
User Task
    ↓
Planner
    ↓
需要：
Research
Competitor Analysis
SEO
    ↓
Dynamic Workers
```

也就是说：

> Agent 的角色不是完全提前定义，而是在运行时产生。

---

## 6.3 但是 Dynamic Worker ≠ Agent Factory

这是一个非常重要的区别。

例如 Hermes：

```text
Main Agent
    ↓
delegate_task()
    ↓
Generic Child Agent
```

它更像：

> Dynamic Task Delegation

而不是：

```text
AgentSpec
    ↓
Agent Factory
    ↓
Coding Agent
Research Agent
Review Agent
```

真正的 Agent Factory 应该可以动态定义：

```text
AgentSpec
├── role
├── prompt
├── model
├── tools
├── skills
├── MCP
├── permissions
└── environment
```

然后：

```python
agent = AgentFactory.create(
    role="listing_optimizer",
    model="gpt-5",
    tools=[...],
    instructions="..."
)
```

---

## 6.4 为什么 Agent Factory 很重要？

假设电商任务：

> “帮我优化 Amazon US 的 100 个 SKU。”

系统可能动态生成：

```text
Orchestrator
│
├── US Listing Worker
├── US Keyword Worker
├── Competitor Worker
├── Pricing Worker
├── Review Worker
└── QA Worker
```

不同市场又可能变成：

```text
US
├── Amazon Agent
└── Walmart Agent

EU
├── Amazon Agent
└── eBay Agent
```

因此：

> Multi-Agent 的下一步不是“增加更多固定 Agent”，而是 **Agent Composition / Agent Factory**。

---

# 07. Production Multi-Agent Architecture

到了生产环境，Pattern 不能单独存在。

一个真实的电商 AI Assistant 可以是：

```text
                         User
                          ↓
                    AI Assistant
                          ↓
                    Orchestrator
                          ↓
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
    Sales Agent        Ads Agent       Listing Agent
        ↓                 ↓                 ↓
        └─────────────────┼─────────────────┘
                          ↓
                     Aggregator
                          ↓
                       Review
                          ↓
                    Human Approval
                          ↓
                       Execute
                          ↓
                    Business System
```

---

## 7.1 一个真实任务

用户：

> “帮我分析 SKU A123 销量下降，如果确认是广告问题，把预算提高 20%。”

这类任务不一定需要 Multi-Agent。

如果销量、广告、库存、价格等数据已经整理成一个宽表，分析逻辑也可以通过几个明确的 Skill 提供，那么一个 Agent 就可能足够完成任务：它读取宽表，调用相关 Skill 分析销量下降原因，在满足条件时请求人工审批，最后执行预算调整。

是否拆成多个 Agent，主要取决于问题的复杂度，而不是任务名称中是否同时出现了“分析”和“执行”。可以重点考虑：

- 是否需要访问多个异构数据源，且数据获取和处理流程彼此独立；
- 是否包含多个相对独立的专业领域，需要不同的上下文、工具或权限；
- 是否存在可以并行执行的子任务，以及是否需要将它们的结果汇总；
- 是否需要通过独立 Agent 隔离风险、权限或责任边界。

因此，同一个业务需求可能有两种合理实现：数据已经标准化、流程主要是串行决策时，使用单 Agent + Skills 更简单；数据分散、领域复杂、需要并行协作或权限隔离时，再考虑拆成多个 Agent。

系统：

```text
1. Sales Agent
       ↓
   判断销量变化

2. Ads Agent
       ↓
   判断广告变化

3. Diagnosis Agent
       ↓
   判断是否为广告问题

4. Decision
       ↓
   是否需要修改预算？

5. Human Approval
       ↓
   Approve

6. Ads Agent
       ↓
   修改预算
```

上面的复杂任务可以用一个 Manager Agent 编排多个 Specialist Agent 来实现。每个 Specialist 负责一个清晰的职责边界，Manager 通过 `as_tool()` 调用它们，并负责整合结果、处理矛盾和输出最终结论：

```python
from agents import Agent, Runner, WebSearchTool, function_tool


# -------------------------
# 1. Specialist: Web Research
# -------------------------

web_researcher = Agent(
    name="Web Researcher",
    instructions="""
    You are a web research specialist.

    Find reliable and recent information.
    Return:
    - key facts
    - source URLs
    - important evidence

    Do not make recommendations.
    """,
    tools=[
        WebSearchTool()
    ],
)


# -------------------------
# 2. Specialist: Data Analyst
# -------------------------

@function_tool
def query_sales_db(sql: str) -> str:
    """Query the company's sales database."""
    # real application would execute SQL here
    return "Revenue: $12.4M, +18% YoY"


data_analyst = Agent(
    name="Data Analyst",
    instructions="""
    You are a data analyst.

    Use the database tool to obtain quantitative evidence.
    Check the numbers before making conclusions.
    Return the relevant metrics and calculations.
    """,
    tools=[
        query_sales_db
    ],
)


# -------------------------
# 3. Specialist: Business Analyst
# -------------------------

business_analyst = Agent(
    name="Business Analyst",
    instructions="""
    You are a business analyst.

    Analyze information supplied by the manager.
    Identify:
    - business drivers
    - risks
    - opportunities

    Do not invent data.
    Clearly separate facts from inference.
    """,
)


# -------------------------
# 4. Manager / Orchestrator
# -------------------------

manager = Agent(
    name="Research Manager",

    instructions="""
    You are the lead research manager.

    Your job is to answer the user's question.

    When external facts are needed:
        use web_researcher.

    When quantitative company data is needed:
        use data_analyst.

    When interpretation or business reasoning is needed:
        use business_analyst.

    You are responsible for combining the specialists'
    outputs into one final answer.

    Do not blindly trust specialist results.
    Resolve contradictions before answering.
    """,

    tools=[
        web_researcher.as_tool(
            tool_name="web_research",
            tool_description=(
                "Research current information from the web. "
                "Use this when external facts or sources are needed."
            ),
        ),

        data_analyst.as_tool(
            tool_name="data_analysis",
            tool_description=(
                "Query and analyze company quantitative data."
            ),
        ),

        business_analyst.as_tool(
            tool_name="business_analysis",
            tool_description=(
                "Analyze business implications, risks and opportunities."
            ),
        ),
    ],
)


# -------------------------
# 5. Run
# -------------------------

result = Runner.run_sync(
    manager,
    """
    Analyze whether Company X is a good growth opportunity.
    Research its latest market position, analyze our sales data,
    and give me your recommendation.
    """
)

print(result.final_output)
```

---

## 7.2 这里其实组合了多个 Pattern

```text
                Orchestrator
                     │
              ┌──────┴──────┐
              ↓             ↓
          Sales Agent    Ads Agent
              │             │
              └──────┬──────┘
                     ↓
                  Fan-in
                     ↓
                 Diagnosis
                     ↓
                  Decision
                     ↓
                 HITL
                     ↓
                  Execute
```

不是一个 Pattern。

而是：

```text
Concurrent
+
Fan-in
+
Agent-as-Tool
+
Conditional Routing
+
HITL
```

这才是实际生产中的 Multi-Agent。

---

# 08. 最终实战：电商 Multi-Agent Assistant

## 8.1 需求

构建一个：

> **E-commerce Performance Analyst**

用户输入：

```text
分析 SKU A123 最近 30 天销量下降的原因，
并给出下一步运营建议。
```

系统需要：

* 销量分析
* 广告分析
* 竞品分析
* Listing 分析
* 综合判断
* 输出建议

---

## 8.2 Agent 设计

```text
Ecommerce Manager
│
├── Sales Agent
├── Ads Agent
├── Competitor Agent
├── Listing Agent
└── Report Agent
```

---

## 8.3 OpenAI Agents SDK 实现

如果四个分析可以并行：

```python
import asyncio
from agents import Agent, Runner


sales_agent = Agent(
    name="Sales Analyst",
    instructions="""
    Analyze ecommerce sales data.
    Focus on orders, revenue, conversion and trends.
    """
)

ads_agent = Agent(
    name="Ads Analyst",
    instructions="""
    Analyze ecommerce advertising performance.
    Focus on CTR, CPC, ACOS and ROAS.
    """
)

competitor_agent = Agent(
    name="Competitor Analyst",
    instructions="""
    Analyze competitor pricing and market changes.
    """
)

listing_agent = Agent(
    name="Listing Analyst",
    instructions="""
    Analyze product listing quality,
    keywords and ranking.
    """
)


async def analyze_product(sku):

    task = f"""
    Analyze ecommerce SKU {sku}.
    Identify the likely reasons for the recent sales decline.
    """

    results = await asyncio.gather(
        Runner.run(sales_agent, task),
        Runner.run(ads_agent, task),
        Runner.run(competitor_agent, task),
        Runner.run(listing_agent, task),
    )

    synthesis = f"""
    SKU: {sku}

    Sales:
    {results[0].final_output}

    Ads:
    {results[1].final_output}

    Competitor:
    {results[2].final_output}

    Listing:
    {results[3].final_output}

    Provide:
    1. Root cause
    2. Evidence
    3. Recommended actions
    """

    report_agent = Agent(
        name="Report Agent",
        instructions="""
        Synthesize ecommerce analysis into
        a concise executive report.
        """
    )

    final = await Runner.run(
        report_agent,
        synthesis
    )

    return final.final_output
```

这里非常典型：

```text
                 SKU
                  ↓
        ┌─────────┼─────────┐
        ↓         ↓         ↓
      Sales      Ads    Competitor ...
        │         │         │
        └─────────┼─────────┘
                  ↓
             Report Agent
                  ↓
               Result
```

---

# 8.4 MAF 实现思路

MAF 可以直接表达成 Concurrent Workflow：

```python
from agent_framework import ConcurrentBuilder

workflow = ConcurrentBuilder(
    participants=[
        sales_agent,
        ads_agent,
        competitor_agent,
        listing_agent,
    ]
).build()

result = await workflow.run(
    "Analyze SKU A123"
)
```

然后接一个 Aggregator / Report Agent：

```text
Concurrent Workflow
        ↓
  Sales / Ads / Competitor / Listing
        ↓
       Fan-in
        ↓
    Report Agent
```

这里最大的优势不是代码少多少，而是：

> **Workflow 的结构成为 Framework 中显式的一等对象。**

---

# 8.5 LangGraph 实现思路

LangGraph 更自然地表达成：

```text
                   START
                     ↓
                Orchestrator
                     ↓
       ┌─────────────┼─────────────┐
       ↓             ↓             ↓
     Sales           Ads       Competitor
       ↓             ↓             ↓
       └─────────────┼─────────────┘
                     ↓
                  Report
                     ↓
                    END
```

代码结构：

```python
from langgraph.graph import StateGraph, START, END


def sales_analysis(state):
    return {
        "sales": analyze_sales(state["sku"])
    }


def ads_analysis(state):
    return {
        "ads": analyze_ads(state["sku"])
    }


def competitor_analysis(state):
    return {
        "competitor": analyze_competitor(state["sku"])
    }


def report(state):
    return {
        "report": generate_report(
            state["sales"],
            state["ads"],
            state["competitor"],
        )
    }


graph = StateGraph(dict)

graph.add_node("sales", sales_analysis)
graph.add_node("ads", ads_analysis)
graph.add_node("competitor", competitor_analysis)
graph.add_node("report", report)

graph.add_edge(START, "sales")
graph.add_edge(START, "ads")
graph.add_edge(START, "competitor")

graph.add_edge("sales", "report")
graph.add_edge("ads", "report")
graph.add_edge("competitor", "report")

graph.add_edge("report", END)

app = graph.compile()
```

这里非常直观：

> **Workflow 本身就是 Graph。**

---

# 最终总结：三种 Framework 怎么选择？

```text
                 Multi-Agent Application
                          │
             ┌────────────┼────────────┐
             ↓            ↓            ↓
          OpenAI         MAF       LangGraph
             │            │            │
          Agent         Workflow       Graph
             │            │            │
       Agent-as-tool   Sequential     Node
       Handoff         Concurrent     Edge
       Python Code     Handoff        State
                       Magentic       Routing
```

## OpenAI Agents SDK

适合：

> **Agent-centric application**

典型：

```text
Manager
 ↓
Agent-as-Tool
 ↓
Specialist
```

优点是简单、Agent 抽象清晰，复杂 orchestration 通常交给 Python。

---

## Microsoft Agent Framework

适合：

> **Agent + Workflow**

典型：

```text
Workflow
├── Sequential
├── Concurrent
├── Handoff
├── Group Chat
└── Magentic
```

当业务流程本身比较明确时，很自然。

---

## LangGraph

适合：

> **复杂、状态化、条件化的 Agent Workflow**

典型：

```text
Graph
├── Node
├── Edge
├── Conditional Branch
├── Parallel
├── Loop
└── Dynamic Worker
```

尤其适合复杂 Agent Workflow / Orchestration。

---

# 课程最终认知框架

学完以后，学生应该能够看到一个 Agent 系统时，快速回答：

```text
1. Agent 怎么运行？
        ↓
   Agent Loop / ReAct

2. 为什么需要多个 Agent？
        ↓
   专业化 / 并行 / Context 隔离 / 权限

3. Agent 怎么协作？
        ↓
   Agent-as-Tool
   Handoff
   Sequential
   Concurrent
   Fan-out / Fan-in
   Orchestrator-Worker

4. Framework 怎么表达这些？
        ↓
   OpenAI → Agent + Code
   MAF    → Agent + Workflow
   LangGraph → Graph + State

5. Agent 是固定还是动态？
        ↓
   Static Agent
   Dynamic Worker
   Agent Factory

6. 怎么进入生产？
        ↓
   HITL
   Guardrails
   Retry
   Observability
   Audit
   Evaluation
```

最终不是记住：

> “OpenAI 有哪个 API，MAF 有哪个 Builder，LangGraph 有哪个函数。”

而是形成：

> **Agent Runtime → Multi-Agent Orchestration → Pattern → Framework → Production Architecture**

这条完整的技术认知链。

