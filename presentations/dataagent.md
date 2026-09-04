# 企业数据 Agent：Ontology、Graph 与 FinClaw 实践模式

**状态：** 企业数据 Agent 实践架构指南  
**案例仓库：** `/Users/dengwei/work/ai/maybeai-uni/finclaw`  
**更新时间：** 2026-08-27

本文说明企业应该如何使用 Data Agent，同时避免把大语言模型变成一段
未经审计的 ETL 脚本。重点包括：Ontology（本体）与 Graph（图）的边界、
企业数据 Agent 的典型架构、FinClaw 电商财务场景，以及如何处理企业定制
的费用分摊、账务事件映射和指标定义。

核心结论如下：

> 用版本化的本体和策略层保存业务含义，用编译后的图保存可执行依赖、
> 数据产品和证据。让 Agent 负责理解意图、提出映射、请求审批和解释结果；
> 让确定性服务负责检查、编译、执行、门禁和发布。

如果把这些职责全部交给模型，系统很容易生成“看起来合理”的数字，却
无法证明数字来自哪一行、哪条规则和哪一次运行。

## 1. 企业 Data Agent 是什么

Data Agent 是面向对话或 API 的数据工作控制面。它把下面这样的业务问题：

> “按店铺核对 6 月平台收入和共同物流成本，并解释差异。”

转换成一组有类型、有证据的操作：

1. 识别业务主体、期间、平台和所需维度；
2. 只读检查用户选择的来源；
3. 解析来源角色和业务词汇；
4. 对会改变财务行为的选择请求审批；
5. 编译不可变的执行计划；
6. 通过批准的 Connector 和 Adapter 执行计划；
7. 校验结构、类型、总额、分类和血缘；
8. 返回结果、证据、缺口和下一步。

Agent 不是数据库、语义层、调度器或事实系统。它不应该因为提示词听起来
很确定，就自行发明列名、选择汇率、写任意 SQL，或发布未经审核的分摊规则。

### 概率性职责与确定性职责

| 关注点 | Agent 可以做什么 | 确定性系统必须做什么 |
| --- | --- | --- |
| 意图 | 理解问题，识别可能的度量和维度 | 校验请求的度量和维度是否存在 |
| 来源发现 | 提议某张表可能是订单或结算表 | 检查表头、类型、键、指纹和行数 |
| 映射 | 根据证据提议 `海外代发运费 -> last_mile_cost` | 只执行已批准、版本化的规则 |
| 工作流 | 选择下一条引擎命令并说明原因 | 管理顺序、重试、幂等性和检查点 |
| 计算 | 选择已声明的分析配方 | 执行准确的 SQL、公式或关系算子 |
| 异常 | 总结缺口并提出问题 | 保留原行、原因、负责人和影响 |
| 发布 | 请求用户确认写入或切换所有权 | 暂存、回读、执行门禁并原子接受版本 |
| 叙述 | 用业务语言解释结果 | 提供事实、血缘和可复现记录 |

FinClaw 对这一边界有明确表述：引擎拥有顺序、门禁、运行状态和工作簿
变更；Skill 与 Agent 拥有业务意图、路由和叙述。企业系统也应采用类似边界。

## 2. Ontology 与 Graph 的区别

### Ontology（本体）

Ontology 是经过约定的业务词汇和语义模型。它定义：

- 概念或类别，例如 `Order`、`Shop`、`Settlement`、`FinancialEvent` 和
  `Metric`；
- 属性和类型，例如 `Order.created_at: datetime`、
  `FinancialEvent.amount_reporting: decimal`；
- 允许值和关系，例如 `Shop belongs_to Platform`；
- 约束、定义、同义词和有效期；
- 所有者、审批状态和版本信息。

Ontology 回答的是 **“这个东西是什么意思？”**。它可以用 OWL/RDF、
JSON Schema、YAML Contract、语义层，或这些形式的组合来实现。Ontology
本身不会说明先读哪个文件，也不会自动证明某条 SQL 可以安全执行。

### Graph（图）

Graph 是节点和边组成的具体关系集合。企业 Data Agent 通常需要多个相互
关联、但生命周期不同的图：

1. **语义图：** 表示概念关系，例如 `last_mile_cost is_a product_cost`；
2. **数据集/依赖图：** 表示处理节点和输入，例如
   `S_financial_event_ledger depends_on B_amount_component`；
3. **血缘图：** 表示某次运行中的来源、转换、输出、决策和证据边；
4. **知识/实体图：** 表示业务实例，例如订单经过某仓库并由某平台结算。

Graph 回答的是 **“什么和什么相连，方向是什么？”**。数据集图还可以回答
**“为了得到这个输出，哪些节点必须先运行？”**。Graph 不会自动定义
“有效订单”或“成本”的正确含义。

### 对比表

| 维度 | Ontology | Graph |
| --- | --- | --- |
| 首要职责 | 定义含义和约束 | 表示实例、依赖和证据 |
| 典型内容 | 类别、属性、域、值集、规则 | 节点、边、键、版本、时间、来源 |
| 变化方式 | 有意且受治理地变更 | 随来源、运行和数据版本变化 |
| 典型问题 | 什么是有效订单？ | 哪些行和规则产生了这个比率？ |
| 校验方式 | 类型、基数、允许值、逻辑一致性 | 无环、可达性、所有权、边有效性、对账 |
| 查询方式 | 语义查找和分类 | 遍历、闭包、影响分析、血缘、依赖排序 |
| 单独使用的风险 | 词汇正确但没有执行路径 | 关系完整但业务含义含糊 |
| FinClaw 对应物 | Package Contract、分类、策略、Profile | 编译后的 `DatasetSpec`、`PipelinePlan`、运行血缘 |

### 推荐关系

Ontology 和 Graph 应该一起使用，但必须分开管理：

```text
业务词汇 + 策略 + Contract
              |
              v
       编译器解析并校验
              |
              v
       可执行数据集/依赖图
              |
              v
   运行状态 + 门禁 + 血缘 + 输出
```

不要构建一个“企业超级图”，然后要求它同时充当本体、工作流引擎、数仓、
血缘库和聊天记忆。它们的更新频率、权限和正确性要求不同。使用共享 ID
和显式链接连接这些图即可。

## 3. 企业参考架构

```text
用户 / API 请求
        |
        v
Agent 路由与策略守卫
        |
        +--> 只读发现与来源检查
        |
        +--> 提议与决策服务
        |        |
        |        +--> 已批准的本体术语、映射、策略
        |
        v
Package/Profile Loader --> Compiler --> 不可变 PipelinePlan
                                               |
                                               v
                                      确定性图执行器
                                      |       |        |
                                      v       v        v
                                  Connector SQL/Sheet Dataset Store
                                      |       |        |
                                      +-------+--------+
                                               |
                                  门禁、回读、血缘、运行状态
                                               |
                                  已接受的 Silver 或 Analysis Result
                                               |
                            Dashboard / Workbook / 报告 / 叙述
```

Agent 应调用窄而有类型的能力，例如：

- `inspect(source_item)`
- `propose_source_role(inspected_evidence)`
- `resolve_decisions(proposals)`
- `compile(selection, decision_set)`
- `execute(prepared_plan)`
- `verify(run_id, output)`
- `explain(run_id, evidence_filter)`

所有会变更状态的能力都应接收 Compiler 签发的对象或计划句柄，而不是模型
拼出的自由文本。可以通过受保护的 Wrapper 暴露命令，但不能允许模型绕过
Wrapper 直接写入底层系统。

### 实际控制回路

```text
请求
  -> 意图与范围
  -> 只读检查
  -> 本体解析
  -> 决策包
  -> 编译并校验图
  -> Dry-run 计划审阅
  -> 执行准确的 Prepared Step
  -> 暂存 + 回读 + 门禁
  -> 接受或隔离
  -> 基于证据解释结果、缺口和证据
```

Dry-run 很重要：用户可以在任何业务数据被修改前看到输入、输出目标、
转换方式和审批状态。

## 4. FinClaw 的具体模式

FinClaw 是面向异构财务文件和 MaybeSheet 工作簿的引擎优先 ETL 系统。其
活动数据路径是：

```text
Source -> Raw -> Bronze -> Silver -> 可选 Analysis/Gold -> Presentation
```

受治理的处理产品是 Silver。电商场景的两个核心输出是：

- `S_order_details`：标准化订单事实；
- `S_financial_event_ledger`：带有分类、金额、店铺、期间、币种和血缘的
  标准财务事件。

### 企业概念与 FinClaw 概念映射

| 企业概念 | FinClaw 实现 |
| --- | --- |
| Source Item | 一个可独立处理的文件、Sheet、Base 表或 Table URI |
| Raw 保留 | `R_*` 表，保存来源行和来源证据 |
| 结构标准化 | `B_*` Bronze Contract 与 Source Projection |
| 业务事实 | 满足严格 Contract 的 `S_*` Silver 数据集 |
| 语义词汇 | Package 分类 Profile、Rule Relation、字段字典 |
| 业务策略 | 分摊策略、路由、FX 策略、Company Profile、Batch Overlay |
| 可执行图 | 展开后的 `DatasetSpec` Catalog 和 `PipelinePlan/v2` |
| Agent 决策 | 带审批状态的版本化 Decision 或 Review Bundle |
| 运行恢复 | `outputs/runs/<run_id>/state.json` 和事件日志 |
| 血缘 | OpenLineage 加 FinClaw 计划、来源、发布和异常 Facet |
| 用户输出 | Analysis Result、可选 Gold、工作簿、Dashboard 或叙述 |

### Package 负责什么

Ecommerce Package 是通用行业模板。`package.yaml` 注册：

- 数据集和数据 Contract；
- Financial Event Producer；
- 分类 Profile；
- 宽表和长表 Amount Shape Template；
- Allocation Policy 和 Route；
- Identity 与 Compatibility Recipe；
- FX Quote Bundle 与 Consumer Binding。

Package 不应放入某个客户某个月的答案，也不应藏入一段提示词。客户决策
应进入 Company Profile、Batch 配置或经过审核的运行证据。

### Compiler 和 Engine 负责什么

Compiler 将 Package 声明与已批准决策转换成普通数据集节点，并校验：

- 输入缺失和输出所有权重复；
- Raw/Bronze/Silver 层方向是否合法；
- 输入 Alias 是否一致；
- 图闭包和环；
- Contract 与门禁定义；
- Executor 能力和准确的 Resolved SQL 依赖；
- 计划、节点、Contract、Transform 和 Decision Hash。

Executor 执行不可变计划、暂存输出、回读结果、执行门禁，并且只在检查通过
后接受数据集版本。失败或阻塞的节点不会抹掉不依赖它的成功分支。

## 5. 电商财务 Agent 的 Ontology

下面是一套可落地的最小本体。它是概念模型，不是要求所有概念放进同一张表。

### 核心概念

| 概念 | 含义 | 重要属性 |
| --- | --- | --- |
| `Company` | 报告主体和策略所有者 | `company_id`、时区、财务期间约定 |
| `Platform` | 电商平台 | 标准 ID、来源标签 |
| `Shop` | 具体店铺账号 | `shop_id`、平台、生效期间 |
| `Period` | 报告或服务期间 | 起止时间、日历、确认规则 |
| `SourceItem` | 不可变输入身份 | URI/定位、媒体类型、内容 Hash |
| `SourceTable` | 来源中的 Sheet 或关系表 | 坐标、表头证据、结构 Hash |
| `Order` / `OrderLine` | 订单和 SKU 明细 | 订单键、SKU、数量、状态、日期 |
| `Settlement` | 平台结算事实 | 账单类型、结算日期、金额列 |
| `AmountComponent` | 分类前的标准金额组件 | Slot、金额、原币/报告币、来源引用 |
| `FinancialEvent` | 发布后的标准财务事件 | 事件类型、类别、子类、店铺、期间、金额 |
| `AllocationPolicy` | 共同金额分配规则 | 直接、加权、均分、舍入、尾差 |
| `Driver` | 分配使用的驱动值 | 有效订单数、销售额、数量、权重 |
| `Metric` | 基于已接受事实的指标定义 | 粒度、度量、维度、分母、截止规则 |
| `Decision` | 对语义选择的治理审批 | 范围、状态、审批人、时间、证据 |
| `Evidence` | 来源、转换、回读或异常证据 | Hash、坐标、Artifact、覆盖率 |
| `DatasetVersion` | 不可变数据产品版本 | 数据集 ID、版本、Contract Hash、血缘 |
| `Run` | 一次执行及其状态 | Plan Hash、步骤状态、证据路径 |

### 标准关系

```text
Company --owns_policy--> AllocationPolicy / Decision
Platform --contains--> Shop
SourceItem --contains--> SourceTable --produces--> Raw row
Order --has_line--> OrderLine --references--> SKU
Settlement --contains--> AmountComponent
AmountComponent --classified_as--> FinancialEvent
FinancialEvent --allocated_to--> Shop
FinancialEvent --belongs_to--> Period
DatasetVersion --derived_from--> DatasetVersion / SourceItem
Run --executes--> PipelinePlan --contains--> Dataset node
Metric --reads--> accepted Silver DatasetVersion
Every published fact --has--> Evidence
```

### 开放词汇与封闭协议

平台名称、店铺 ID、来源标签、类别和子类别应作为版本化数据保存。只有真正
的协议概念才应在代码中封闭，例如图层（`raw`、`bronze`、`silver`）、节点
状态和 Executor Capability。

这样，客户增加新的费用子类别时不需要发布新的引擎版本，也能避免模型把
业务字符串误当成指令或 SQL 标识符。

## 6. FinClaw 电商典型场景

### 场景 A：按店铺生成月度利润并核对结算

**问题：**“按三个店铺展示 6 月收入、商品成本、物流成本、经营费用和利润，
与平台结算核对，并列出未解决项。”

**来源族：**订单导出、取消/退款、平台结算、应付账款、采购账单、费用/报销、
FX 观察值，以及可选的库存数据。

**图闭包：**

```text
来源文件
   -> Raw 来源保留关系
   -> Bronze 订单行 / 结算落地 / 金额组件 / FX 证据
   -> S_order_details
   -> S_financial_event_ledger
   -> 分析结果：按期间/店铺计算收入 - 成本 - 费用
```

Agent 可以识别问题并提出范围，但不能从答案工作簿计算利润，也不能因为
文件名包含“6 月”就自行确定期间。Engine 必须证明来源身份、期间、键、币种
和 Contract 覆盖率。

### 场景 B：共同成本分摊

FinClaw 提供两类代表性策略：

- **Direct：** 来源行已经指定具体 `shop_id`；
- **Weighted：** 共享行通过驱动关系（例如有效订单数）分配给所有符合条件的
  具体店铺。

`direct-or-weighted.yaml` 中的 Route 在 `shop_id != all` 时走 Direct，
在 `shop_id == all` 时走 Weighted。Weighted Policy 要求正权重，使用 scale 2
和 half-even 舍入，并把尾差放到最大目标。发布结果不能保留 `shop_id=all`。

这是一条可执行策略。它与工作簿中的一句 `各店平均分` 或 `按有效订单比例`
不是同一个权威层级。

### 场景 C：结算与账务事件映射

平台账单类型只有经过审核的映射才能成为标准事件。Ecommerce Package 中的
示例包括：

| 来源证据 | 标准 Tuple |
| --- | --- |
| `订单销售收入-订单收入` + `product_price_amount` | `settlement / revenue / gross_sales` |
| `退货退款-订单调整` + `product_price_amount` | `settlement / contra_revenue / return_refund` |
| `service_fee_amount` | `settlement / settlement_fee / settlement_service_fee` |
| `fulfillment_fee_amount`，排除零值 | `settlement / settlement_fee / fulfillment_fee` |
| `奖惩及其他-运费补贴` + `receivable_amount` | `settlement / incentive_penalty / shipping_subsidy` |

原始标签和坐标必须保留为证据。下游规则和 Contract 使用标准英文 ID。

### 场景 D：指标和经营问题

`customize-metrics.md` 有意只保存定义，不保存计算后的金额，也没有公式单元格。
其中包括：

- 库存周转率：`(海外仓 + 在途 + 采购 + 出单) / 3家销量件数`；
- 实际货成本支出：1688、运费、应季品样品、公司账单和线下采购的组合；
- 店铺有效订单占比：去重总订单减去去重的发货前取消订单，再除以去重总订单；
- 销量：标准化 SKU 乘以数量；
- 发货数量：当月采购数量，但仍需确认它是采购、入库还是实际发货粒度。

Agent 可以把问题转换成 `AnalysisRequest`，但在输出数字之前，指标必须明确
粒度、期间截止、估值、分母、币种和零值处理。

### 场景 E：异常和对账

如果应付账款行缺少服务期间或 FX 证据，正确结果是可见缺口，而不是猜一个
月份或汇率。系统应保留：

- 来源文件/表和物理行或 Base 坐标；
- 原始标签和原始值证据；
- 已执行的标准化值（如有）；
- 使用的规则或 Decision；
- 受影响的数据集、指标和金额；
- 负责人、修正方式、重跑状态和影响。

这样 Agent 可以回答“缺什么、影响什么”，而不会把 partial 结果说成完整结果。

## 7. 四个定制 Artifact 的权威边界

`verticals/ecommerce/package/` 下的四个文件体现了企业定制，但它们的权威
等级不同。

### `customize-expense-allocation.md`

这是客户配置工作簿的只读捕获。它记录了均分、有效订单比例、来源提示和限定条件。
文件明确指出，工作簿中的可见金额和三分之一份额只是配置上下文，不是账务输入。

它可以用于：

- 提议一条 Policy Decision；
- 确定所需的 Driver 和来源证据；
- 展示需要确认的项目；
- 为未来的 Company Profile 更新准备暂存数据。

它不能用于：

- 没有具体有效店铺集合时直接发布默认三等分；
- 把工作簿金额当作交易；
- 自动解决手续费规则冲突；
- 把未支付账单确认为本期费用。

### `customize-ledger-event-mapping.md`

这是把来源标签映射到标准事件 Tuple 的暂存证据，记录工作簿坐标、状态和未决决策。
其中有工资、租金、运费、服务费、结算和采购支出的已确认示例，也明确把
`period-last-mile`、0.3% 手续费和 2% 小规模税标为待确认或需复核。

它可以作为 Rule Relation 或 Decision 转换的证据，但不是第二套执行映射，
不能绕过 Package Compiler。

### `customize-metrics.md`

这是指标定义目录，没有指标金额和公式单元格。Agent 应使用它提出分析配方，
并补齐期间、估值、币种、截止时间和零销售处理等缺失语义。

### `customize-misc.md`

这是来源接入和排除决策的记录，包括：

- 订单数据使用 `正确SKU` 和 `组合倍数`；
- 使用 `售后状态` 确认最终状态和有效订单比例；
- 将 `海外代发运费` 解释为尾程成本；
- 区分办公 `采购费用` 与商品采购成本；
- 保留银行账单描述；
- 忽略 `互转-唐钲杰私户互转-珏简` 等内部转账行。

这些是来源权威和解释决策。审批后应进入客户 Profile 或 Batch Overlay，不能只
存在 Agent 的上下文记忆中。

### 推荐权威顺序

```text
Engine 不变量和 Contract
  > 已批准的 Company Profile / Batch Decision
  > 已校验的 Package Rule 和 Policy
  > 暂存 Artifact 和来源观察
  > Agent 提议和叙述
```

当两个层级冲突时，保留较低层级的原始证据，并暂停或请求较高层级的决策。不要
静默重写来源 Artifact。

## 8. 如何构建企业 Data Agent

### 第一步：定义业务边界

明确业务主体、行业、报告期间、币种、目标输出和 Agent 可以执行的动作。建议
先选择一个完整但有限的工作流，例如“把电商财务资料处理到已接受 Silver”。

同时写出明确的非目标。例如 FinClaw 的数据准备和 Ledger Skill 不负责法定凭证
过账，也不负责报告渲染。

### 第二步：建立 Ontology 和 Contract

为每个概念定义：

- 标准 ID 和展示名称；
- 粒度和主键；
- 必填和可空字段；
- 类型、单位、币种和正负号约定；
- 允许值、同义词和有效期；
- 所有者和审批状态；
- 血缘与异常要求。

不要一开始就建设庞大的知识图。一个能被校验的小 Contract 通常比一个没人
维护的大词表更有价值。

### 第三步：保留并检查来源

根据内容、表头、样例值、键、粒度、期间、币种和金额证据识别文件。文件名只能
作为提示。每一行可用证据都应进入带来源身份和坐标的 Raw 关系；排除、损坏和
含糊记录也要可见。

在 FinClaw 中，读取结果是带来源证据的不可变 `InspectedTable`；Raw 层在标准化
之前保存来源。Source Adapter 不得猜同义词，也不能回退到“看起来相似”的列。

### 第四步：让 Agent 产生 Proposal，而不是 Action

Proposal 应该是结构化、可审核的对象，例如：

```json
{
  "proposal_type": "classification",
  "source_reference": "settlement-row-01842",
  "source_label": "奖惩及其他-运费补贴",
  "canonical_category": "incentive_penalty",
  "canonical_subcategory": "shipping_subsidy",
  "evidence": ["source-sheet=Settlement", "row=1842"],
  "confidence": 0.94,
  "requires_approval": false,
  "reason": "匹配已批准的结算账单类型规则"
}
```

服务端应按 Schema 校验对象、检查决策范围，然后接受、提出问题或生成异常。
置信度不能代替证据和必需的审批。

### 第五步：分开分类和行为

新标签可能只是分类变化；会改变金额、符号、币种、FX、分摊、确认期间、身份
或粒度的选择属于行为变化。

FinClaw 只有在金额及其他行为输入已经确定时，才允许使用显式 `unclassified`
回退。可能改变财务行为的选择必须暂停或等待审批。这样可以保留不确定性，
又不会为了“完成”而编造金额。

### 第六步：先编译图，再进行变更

Compiler 应按下面顺序工作：

1. 加载 Package、Contract、Profile、Policy 和 Decision；
2. 解析来源绑定和分区；
3. 将声明式 Producer 展开为普通数据集节点；
4. 校验 Contract、Alias、层级、Scope 和输出所有权；
5. 选择目标闭包和拓扑顺序；
6. 通过准确的 Executor Artifact 做准备；
7. 冻结 Plan、Node、Contract、Transform 和 Decision Hash。

结果必须是不可变计划。模型不能自己拼 Graph JSON，然后直接发送给数据库。

### 第七步：使用门禁和可恢复状态执行

每个节点从调用者角度都应幂等。至少持久化：

- 计划和节点身份；
- 输入数据集版本；
- Executor 和 Artifact 签名；
- 阶段输出和回读路径；
- 门禁结果和异常；
- 接受或隔离的发布状态。

FinClaw 将这些内容保存到 `outputs/runs/<run_id>/state.json`。Resume 会重用已
通过的步骤，只重跑失败或待处理步骤。

### 第八步：从证据校验并生成叙述

验证顺序应先结构、后数值：

- Schema 和字段类型；
- 粒度、键和重复；
- 行数和空值；
- 分类覆盖率和未映射标签；
- 金额总额、符号、单位、币种和 FX；
- SQL/公式文本以及计算值；
- 血缘和来源坐标；
- 每个缺口的原因、负责人、修正和影响。

Agent 的叙述应从 Run Manifest 和已接受输出生成，而不是从对话记忆生成。

## 9. FinClaw 操作方式

公共引擎入口是 `finclaw`：

```bash
# 执行 package-copy -> map -> preview 组合流程。
finclaw run \
  --doc-id <DOC_ID> \
  --raw-readback <passed-readback.json> \
  --profile input-data/<company>/<batch>/custom_rules

# 查看持久化步骤，并在失败后恢复。
finclaw status
finclaw resume
finclaw resume --raw-readback <passed-readback.json>

# 执行聚焦命令。
finclaw map -- --help
finclaw build -- --explain-package
finclaw verify section revenue -- --doc-id <DOC_ID>
finclaw profile list
```

电商 Data Agent 的对话应接近下面的流程：

1. 用户提供文件夹、工作簿或业务问题；
2. Agent 报告发现的来源族、期间、店铺和缺口；
3. Agent 针对含糊映射或策略展示一张简洁确认卡；
4. Engine 编译以 `S_order_details`、`S_financial_event_ledger` 或两者为目标的计划；
5. 用户确认写入或所有权切换；
6. Engine 执行、回读、运行门禁并记录证据；
7. Agent 返回 `passed`、`partial`、`blocked` 或 `failed`，说明影响范围和下一步。

FinClaw 支持通过 MaybeSheet 访问边界做离线录制和重放：

```bash
FINCLAW_MBS_RECORD=outputs/cassettes/run.jsonl finclaw run ...
FINCLAW_MBS_REPLAY=outputs/cassettes/run.jsonl finclaw run ...
```

Replay 必须重现已录制的成功和门禁失败。Replay 未命中时必须报错，不能悄悄
退回线上系统。

## 10. Guardrails 与批判性思考

### Agent 每次接受结果前要问什么

- 精确来源和内容指纹是什么？
- 行粒度和业务键是什么？
- 适用哪个期间和确认规则？
- 所有金额的币种和正负号是否一致？
- 分母是否完整且已批准？
- 这是分类变化还是行为变化？
- 哪些行被排除、重复、损坏或未解析？
- 能否从记录的计划和输入版本复现？

只要有一个答案未知，就应明确说明；必要时将结果标为 `partial` 或 `blocked`。

### 常见但危险的捷径

| 诱惑 | 错误原因 | 正确处理 |
| --- | --- | --- |
| 把“答案”工作簿作为输入 | 计算会被预期结果污染 | 只把它作为验证证据 |
| 把文件名或 Sheet 名当权威 | 提供方会重命名文件和标签 | 按内容和结构证据识别 |
| 正数银行流水直接算收入 | 现金流不等于会计含义 | 通过批准的来源规则映射 |
| 用最新汇率填补缺失 FX | 会静默改变财务金额 | 暂停或生成 FX 异常 |
| 发布 `shop_id=all` | 下游指标需要具体归属 | 分配给批准的目标集合，或保持未分配 |
| 自动把 0.3% 变成手续费 | 客户可能有冲突规则 | 暂存候选并请求确认 |
| 删除不符合 Contract 的行 | 隐藏数据损失和偏差 | 保留行并记录异常处置 |
| 每个类别添加一份 SQL | 造成漂移和引擎分支 | 使用封闭算子或审核后的 Source Projection |
| 相信高置信度分数 | 置信度不是审计轨迹 | 要求证据、Schema 校验和门禁 |

### 不确定性状态机

```text
observed -> proposed -> approved -> executable -> verified -> accepted
                    \-> review_required
                    \-> rejected / exception
```

不要把 `review_required`、`partial` 和 `blocked` 合并成笼统的“尽力而为”。
它们对应不同的用户动作和发布权限。

### 安全与租户边界

企业 Data Agent 还应强制执行：

- 每个来源、决策和数据集的租户与公司范围；
- inspect、propose、approve、execute、publish 的角色权限；
- 凭据只注入 Connector，不写入 URI、Plan 或血缘；
- Agent 看到敏感数据前先通过行级和列级策略；
- 审批和所有权切换的不可变审计记录；
- 防止任意代码和原始写入的 Prompt/Tool Allowlist。

FinClaw 的 TableURI 与 Connector 设计可作为参考：核心只看到不含凭据的身份，
Connector 负责私有客户端参数。

## 11. 评估与上线

评估 Agent 时，应把它当成数据系统，而不只是聊天系统。

### 测试分层

1. **静态 Contract 测试：** Package 引用、命令面、命名、Schema 和依赖方向；
2. **Ontology 测试：** 标准 ID、允许值、同义词、有效期和冲突检测；
3. **Compiler 测试：** 图闭包、环拒绝、层方向、Alias、输出所有权、Hash 和能力；
4. **确定性阶段测试：** Raw/Bronze/Silver 转换、门禁、分摊守恒、FX 和回读；
5. **Agent Proposal 测试：** 来源角色识别、映射、澄清和拒绝编造；
6. **Replay E2E 测试：** 录制 Connector 响应并注入表头变更、FX 缺失等故障；
7. **人工验收：** 财务人员审核证据，而不是只看最终数字。

### 建议指标

| 维度 | 示例度量 |
| --- | --- |
| 覆盖率 | 每个来源、行、字段和预期输出都有处置状态 |
| 语义准确性 | 类别、符号、币种、期间和分母符合批准规则 |
| 数值准确性 | 按指标检查对账总额和容差，零基数场景做精确检查 |
| 可追溯性 | 用户可以从结果跳转到来源坐标和规则 |
| 恢复能力 | 中断后恢复不会重复写入 |
| 安全性 | 未批准的行为选择被阻止，不能绕过原始写入口 |
| 实用性 | 得到已验证答案的时间，以及缺口解释质量 |

建议先上线一个高价值工作流。起步阶段将 Agent 限制为只读或 Preview，等来源
身份、决策复用、门禁和 Replay 稳定后，再开放物化写入。只有当用户能解释“为什么
这个结果可以接受”时，才应允许自动发布。

## 12. 参考实现清单

### 企业 Data Agent 最小闭环

- [ ] 一个有边界的业务工作流和明确输出 Contract
- [ ] 覆盖实体、度量、维度、策略和证据的 Ontology
- [ ] 带内容指纹和坐标的来源检查
- [ ] Raw 保留和异常处置
- [ ] 经过 Schema 校验的结构化 Agent Proposal
- [ ] 带范围和版本的 Decision/Approval 存储
- [ ] 输出一个不可变可执行图的 Compiler
- [ ] 受保护的 inspect、compile、execute、verify、explain 工具
- [ ] 阶段门禁、回读、血缘和可恢复运行状态
- [ ] `partial`、`blocked`、`failed` 状态语义
- [ ] 离线 Replay 和注入故障测试
- [ ] 角色与租户控制
- [ ] 用户可访问的证据和 Caveat 链接

### FinClaw 起始文件

- 阅读仓库规则：[`AGENTS.md`](/Users/dengwei/work/ai/maybeai-uni/finclaw/AGENTS.md)。
- 阅读入口说明：[`README.md`](/Users/dengwei/work/ai/maybeai-uni/finclaw/README.md)。
- 阅读数据集图规范：
  [`dataset-graph-engine-and-stage-refactoring.md`](/Users/dengwei/work/ai/maybeai-uni/finclaw/docs/spec/dataset-graph-engine-and-stage-refactoring.md)。
- 阅读 Ecommerce Package：
  [`package/README.md`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/README.md)
  和 [`package.yaml`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/package.yaml)。
- 阅读四个定制证据：
  [`customize-expense-allocation.md`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/customize-expense-allocation.md)、
  [`customize-ledger-event-mapping.md`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/customize-ledger-event-mapping.md)、
  [`customize-metrics.md`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/customize-metrics.md)
  和 [`customize-misc.md`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/customize-misc.md)。
- 阅读最终事件 Contract：
  [`financial-ledger.schema.yaml`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/contracts/financial-ledger.schema.yaml)。
- 阅读分摊策略和路由：
  [`direct.yaml`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/allocation_policies/direct.yaml)、
  [`weighted-amount.yaml`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/allocation_policies/weighted-amount.yaml)
  和 [`direct-or-weighted.yaml`](/Users/dengwei/work/ai/maybeai-uni/finclaw/verticals/ecommerce/package/allocation_routes/direct-or-weighted.yaml)。
- 阅读血缘规范：[`data-lineage.md`](/Users/dengwei/work/ai/maybeai-uni/finclaw/docs/spec/data-lineage.md)。

## 13. 外部参考与研究说明

以下资料用于校准术语和边界：

- [W3C OWL 2 Web Ontology Language Primer](https://www.w3.org/TR/owl2-primer/)，
  了解本体类别、属性和约束；
- [W3C RDF 1.1 Concepts and Abstract Syntax](https://www.w3.org/TR/rdf11-concepts/)，
  了解图三元组和标识符；
- [W3C PROV-Overview](https://www.w3.org/TR/prov-overview/)，了解来源和证明关系；
- [OpenLineage 文档](https://openlineage.io/docs/)，了解标准血缘事件模型；
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/)，了解图检索与普通向量检索的区别；
- [dbt Semantic Layer](https://docs.getdbt.com/docs/use-dbt-semantic-layer/dbt-sl)，
  了解受治理的指标定义和可复用度量。

用户提供的七个微信公众号链接保留如下：

1. https://mp.weixin.qq.com/s/8DTNJkKOcgcsuo6h8f9rKg
2. https://mp.weixin.qq.com/s/5nS0ndTip3TeH5yGeA-ZlA
3. https://mp.weixin.qq.com/s/TRCjXb34DtjT25pJ1n8ZJw
4. https://mp.weixin.qq.com/s/73j4qHbmZJIoF901cLa3xg
5. https://mp.weixin.qq.com/s/xmrO2R7kW5RY1iSi7N97XQ
6. https://mp.weixin.qq.com/s/uV7k4b7AHF3UhDfryb040Q
7. https://mp.weixin.qq.com/s/TQJBGq2TUDmyeqss1Itefw

写作时这些页面返回的是环境验证页，没有返回文章正文。因此本文没有把任何
具体观点或引文归给这些文章。具备访问权限的人工审阅者应在使用前核对文章中的
术语和案例；本文关于 FinClaw 的结论以仓库中的代码、Contract、规范和测试为准。

## 结论

企业 Data Agent 的标准不是“听起来有多自主”，而是面对杂乱输入时能否给出
正确、可复现、可审核的答案。Ontology 负责含义，Graph 负责路径和证据，确定性
Contract 与门禁负责停止条件。Agent 最有价值的位置是在边界上：把人的问题转换
为经过批准、可解释的工作，并让不确定性及时暴露、及时解决。
