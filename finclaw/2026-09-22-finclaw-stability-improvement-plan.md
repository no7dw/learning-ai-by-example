# FinClaw-NG 稳定性改进计划

**日期：** 2026-09-22  
**范围：** Excel/CSV intake、Bronze -> Silver build、mapping correctness、Maybe publication  
**目标：** 优先完成三项能提升稳定性、pipeline build 和 mapping correctness 的改进

## 结论摘要

当前最主要的不稳定性不在单个 transformation，而在以下边界之间：

`source inspection -> controlled input -> pipeline compilation -> pipeline run -> Maybe publication`

July session 中，核心 pipeline 最终能够生成 immutable successful run，但经历了多次
补充和重跑：

- forwarder workbook 第一次读取时使用了不可靠的读取方式，被误判为几乎没有数据；之后才发现有 405 行有效数据。
- Maybe worksheet 在增加 source family 和 derived input 后多次 delete-and-create，gid 从 4 变为 7。
- controlled input 由临时 Python heredoc 生成，同时 project 又有意排除部分文件，无法通过机器规则区分“未适用”“未准备好”和“遗漏”。
- 最终结果是成功的，但依赖多次人工组装，而不是一次可重放的 intake-to-publication transaction。

这些问题与 `improve-proposal.md` 中的 Harness 原则一致：稳定性需要
feedforward context、deterministic feedback、explicit orchestration 和 evidence-driven iteration。
它们也说明 best-practise guide 中的 source selection、sealed compilation、review gate
和 pinned revision 还需要进一步变成可执行的系统约束。

## Top 3 改进项

| 优先级 | 改进项 | 主要降低的风险 | 预期结果 |
| --- | --- | --- | --- |
| 1 | Deterministic source intake 和 coverage manifest | 错 sheet、错 reader mode、遗漏或重复 input、晚发现 source | 在 mapping 开始前，所有 source 都完成 fingerprint、分类和 disposition |
| 2 | Sealed build 和 transactional publication | project、manifest、input tree 不一致；重复手工重跑；worksheet 状态过期 | 一个 release candidate 完成 compile、validate、inspect、projection 和 publication |
| 3 | Independent mapping correctness 和 reconciliation loop | 静默误分类、错误 allocation、总额抵消造成的 false success | mapping 变更由 expected rows、amounts、lineage 和 fault cases 共同证明 |

## 使用的 POV：来自 `improve-proposal.md`

本计划不是只把现有流程再自动化一遍，而是把 `improve-proposal.md` 中关于
Agent reliability、Harness Engineering 和 business correctness 的观点，映射到当前
FinClaw pipeline 的具体问题上。

| `improve-proposal.md` 的 POV | 在本计划中的应用 | 对应改进 |
| --- | --- | --- |
| **Harness = Steer + Constrain + Integrate**：系统要能引导执行、限制危险动作，并把 tools、state、logs、hooks 和 workflow 组合起来 | intake manifest 负责 `Steer`；fail-closed gate、review gate 和 rollback 负责 `Constrain`；release candidate 负责 `Integrate` | Top 1、Top 2 |
| **Feedforward**：Agent 或 pipeline 开始前必须看到正确的目标、规则、环境和能力 | 在 pipeline 开始前固定 raw file、sheet、reader mode、source family、mapping selector 和 expected source coverage | Top 1 |
| **Feedback**：执行后必须返回可纠正的错误，而不是只返回成功或错误码 | 通过 source coverage report、discrepancy report、row disposition、amount delta 和 evidence reference 返回具体纠正信息 | Top 1、Top 3 |
| **Deterministic control 优先于推断型判断**：能用程序验证的内容，不应交给模型或人工猜测 | 用 hash、row count、schema、enum、allocation conservation、replay hash 和 full recomputation 做确定性 gate | Top 1、Top 2、Top 3 |
| **长任务需要显式状态和 session handoff**：避免一次承担过多、上下文遗忘和过早判断完成 | 用 intake manifest、release candidate、run status、publication status 和 evidence record 保存跨 stage、跨 session 状态 | Top 1、Top 2 |
| **执行与验收应分离**：Generator 自检不等于独立验收 | expected-output layer 独立于 mapping implementation；finance reviewer 可以在不阅读 candidate implementation 的情况下验证结果 | Top 3 |
| **业务正确性不能只靠工程检查**：测试与实现可能共享同一个误解，aggregate total 也可能掩盖错误 | 加入 reviewed fixtures、independent expected outputs、lineage、source disposition 和 offsetting-error cases | Top 3 |
| **高风险业务任务需要降低自治程度**：缺少可靠验收标准时应 escalation，而不是猜测 | `needs_review`、`unresolved`、semantic ambiguity 和 correction budget 都必须阻止自动 publication | Top 1、Top 2、Top 3 |
| **Loop Engineering 需要明确触发、状态、验证、重试和停止条件** | release candidate 使用显式 lifecycle；discrepancy correction 使用 bounded attempts；失败时保留可诊断状态 | Top 2、Top 3 |
| **Entropy management**：持续清理规则、代码、状态和文档漂移 | 通过 manifest hash、definition hash、source-profile disagreement、replay check 和稳定性指标持续发现 drift | Top 1、Top 2 |
| **Agent = Model + Harness**：评估的不只是模型，还包括工具、上下文、错误回传和执行环境 | 评估对象从单个 mapping 扩展为完整的 intake、compile、run、inspect、reconcile 和 publish workflow | 三项共同 |

### 这些 POV 如何改变当前方案

1. **从“命令顺序”改为“可验证状态机”。** 当前 guide 已经定义了命令顺序；本计划进一步要求每个 stage 生成带 hash 的 artifact，并明确 candidate、validated、published、rejected 和 rolled-back 状态。
2. **从“pipeline succeeded”改为“evidence-backed release”。** 成功执行不再等于业务正确。publication 必须同时满足 source coverage、contract、reconciliation、lineage 和 rollback evidence。
3. **从“人工发现问题”改为“反馈驱动纠正”。** forwarder 误读、source omission、unknown classification 和 allocation discrepancy 都必须在下一次 stage 前变成结构化反馈，而不是依赖 session history 才被发现。
4. **从“总额对得上”改为“多 grain correctness”。** source、component、event、allocation 和 compact projection 都要有检查，避免 offsetting errors 通过 grand total。
5. **从“扩大自治”改为“按可验证性分级自治”。** 对明确、可计算、可回滚的步骤自动化；对 semantic ambiguity、missing evidence 和 unresolved mapping 保留人工 review。

## 1. Deterministic Source Intake 和 Coverage Manifest

### 问题

best-practise guide 要求在编写 mapping 前先完成 source profiling，并要求所有 expected
source 出现在 `selection.population` 中。但 July session 仍然允许 source 被错误读取，
直到第一次 publication 后才发现。当前历史记录也主要依赖 shell history、临时脚本和
`/tmp` 文件名，没有机器可读的 source disposition。

关键证据：

- guide 要求 one-based sheet index、显式 `sourceProfiles` 和 controlled CSV materialization：
  [best-practise-guide-ingest-pipeline.md](../../best-practise-guide-ingest-pipeline.md#step-0-profile-并读取-workbook)。
- forwarder correction 证明第一次 source qualification 不可靠：
  [cmd-history.md](../../cmd-history.md#6-correction-july-forwarder-workbook-did-contain-data)。
- guide 要求在进入 Gold 前检查完整 source selection、row counts 和 review rows：
  [best-practise-guide-ingest-pipeline.md](../../best-practise-guide-ingest-pipeline.md#step-4-通过-inspect-做-quality-check)。

### 改进方案

引入 versioned intake manifest，作为 Raw/source inspection 到 FinC project 的唯一 handoff。
manifest 至少记录：

- raw file path、content hash、workbook sheet index/name、reader mode、header row、detected shape 和 source fingerprint；
- controlled output path、adapter version、output schema、row count 和 amount totals；
- disposition：`selected`、`excluded_with_reason`、`not_applicable`、`needs_review` 或 `failed`；
- expected source family 和 mapping selector；
- raw workbook 与 profiling result 的 evidence link。

intake 必须 fail closed：required source 缺失、workbook surface 有歧义、不同 reader mode
导致 row count 有明显差异，或 output 没有注册时，都不能进入 pipeline。应提供
`--check` 模式，只执行读取和检查，不写入 governed input。

### 实施步骤

1. 定义 `ingest-manifest/v1` 和 period-level manifest。
2. 包装现有 adapter、FX generator 和 order-detail export，使它们输出 manifest record，而不是只依赖 shell history 和 `/tmp` 文件。
3. 对 merged cells、formula、hidden rows 或已知 reader ambiguity 增加 dual-read check；row count 差异超过阈值时直接停止。
4. 让 `transformer generate` 消费 manifest，并拒绝未注册的 input。
5. 在 mapping 执行前生成 source coverage report，列出 selected、excluded、unresolved 和 missing source family。

### 验收标准

- 没有完整 intake manifest 时，pipeline 不能启动。
- 每个 raw file 都有稳定 hash，每个 workbook surface 都有 profile 记录。
- project 排除的 source 必须有明确理由；意外遗漏的 source 必须使 validation 失败。
- 对未变化的 raw input 重跑 intake，controlled CSV hash 和 row count 必须一致。
- forwarder case 必须成为 regression fixture：系统必须发现错误读取，不能发布一个不完整但 status 为 successful 的 release。

## 2. Sealed Build 和 Transactional Publication

### 问题

guide 正确地把 `generate`、`validate`、`plan`、`run`、`inspect`、projection 和 Maybe
import 分开。但 July session 使用了多个不同的 temporary input root，并手工合并了
`v1` 到 `v4` 的 CSV。每次修正都 delete-and-create Maybe worksheet。

这种方式即使每个单独命令都成功，整体仍然容易出现：

- compile 使用的 input 与 run 使用的 input 不同；
- inspect 的 version 与实际 import 的 projection 不同；
- failed attempt 被误当成当前 accepted release；
- worksheet 已经变化，但 run evidence 没有同步更新。

improvement roadmap 已经指出相同缺口：full-output validation 必须成为 publication gate，
failed attempt 不能冒充 prior release 或 accepted release。

### 改进方案

建立一个 `release candidate` 对象，绑定以下内容：

- intake manifest hash；
- sealed compilation manifest hash；
- project、mapping、contract、policy 和 resource hash；
- immutable Silver version 和 inspect result；
- compact projection hash、schema hash、row counts 和 amount totals；
- Maybe workbook、worksheet name、当前 revision 和 intended replacement；
- publication status：`candidate`、`validated`、`published`、`rejected` 或 `rolled_back`。

publication 必须是 full validation 之后的独立显式 transition。删除旧 worksheet 前，
必须先通过 candidate checks 并写入 rollback record。import wrapper 需要在 postflight 中
按 worksheet name、schema、row count、period、entity、hash 和 key totals 验证新状态。

### 实施步骤

1. 从 immutable run 和 projection 生成 `release-candidate.json`，记录所有 hash 和 expected count。
2. 把 projection 变成 deterministic FinC step，消除未记录的手工 CSV merge。
3. 增加 pre-publication gate，检查 source coverage、zero review rows、enum validity、allocation conservation、projection schema 和 expected totals。
4. 增加 Maybe publication wrapper，包含 preflight、delete/create、postflight 和 rollback evidence。
5. 将 failed attempt 与 published release 分开持久化。import 失败或 evidence write 失败时，不能把失败结果报告为当前 accepted release。

### 验收标准

- 同一个 candidate 可以 inspect 和 publish，不需要从另一个 input tree 重新 build。
- input、mapping、contract 或 policy 任何一个变化，candidate hash 都变化，并阻止 stale publication。
- failed run 保留 previous accepted release，并清楚记录失败 stage。
- 使用相同 manifest 和 definition version replay 时，Silver hash 和 projection hash 一致。
- worksheet replacement 可以根据 prior revision 和 release-candidate metadata 恢复。

## 3. Independent Mapping Correctness 和 Reconciliation Loop

### 问题

结构 validation 和 successful execution 不能单独证明 classification 或 business meaning
正确。`improve-proposal.md` 已指出：实现和测试可能共享同一个误解，aggregate total
也可能掩盖相互抵消的错误。

July analysis 中已经出现类似风险：

- `purchase_payment` 和 `purchase_cost` 是不同 business fact，不能混用；
- sales 增长时 product cost 下降，需要进一步检查 SKU matching 和 cancellation；
- `purchase-cost` 中有 166 行 `missing_cancellation_source`。

因此，pipeline status 为 `succeeded` 或 grand total 对得上，都不足以证明 mapping semantics 正确。

### 改进方案

建立小规模但独立维护的 verification corpus，让 mapping 变更遵循：

`inspect -> compare -> explain -> approve`

expected answer 必须独立于被测试的 mapping implementation，并覆盖：

- source-row disposition：processed、explicitly excluded 或 unresolved；
- mapping selector coverage 和 exclusivity；
- 只能从 governed resource 产生 classification tuple；
- event expansion 和 deduplication；
- allocation 的 amount conservation、rounding、residual assignment 和 zero-denominator behavior；
- sign、currency、date-period 和 grain semantics；
- source-to-Silver lineage 与 excluded-row explanation；
- deliberately wrong mapping，包括总额不变但 attribution 错误的 case。

### 实施步骤

1. 建立 reviewed fixtures：normal period、missing source、duplicate source、wrong sheet、unknown classification、refund、cancellation、mixed currency、allocation residual 和 late-arriving correction。
2. 建立独立 expected-output layer，保存 reviewed row identity、classification、totals 和 lineage assertion；不能用同一套 SQL 或 adapter 生成 expected result。
3. 增加 mapping graph checks：每个 selector 必须恰好匹配一次；每个 emitted taxonomy value 必须存在于 contract enum 和 confirmed resource；unresolved row 必须 hold back，不能进入 Silver/Gold。
4. 在 source、component、event、allocation 和 compact projection 多个 grain 生成 reconciliation report，不能只看 grand total。
5. 输出 machine-readable discrepancy：baseline、candidate、changed rows、changed fields、amount delta 和 evidence reference。超过限定 correction attempt 后必须 escalation。

### 验收标准

- deliberately incorrect mapping 必须被拒绝，或产生可定位的 discrepancy；不能因为另一个错误抵消而通过。
- 每个 published row 都有 source coordinates、mapping/resource version 和清晰的 disposition path。
- `needs_review` 不能 advance Silver，也不能 feed Gold。
- allocation test 必须在声明的 precision 内证明 exact amount conservation。
- candidate preview 必须与同一 candidate 的 clean full recomputation 一致。
- finance reviewer 可以在不阅读 candidate implementation 的情况下独立确认 expected output。

## 交付顺序

### Phase 1：先加固 Intake

先实现 manifest 和 source coverage check。把 July forwarder case 以及 excluded-source
case 作为强制 regression test。这一步优先级最高，因为错误或不完整的 input 会使所有
下游 comparison 失去意义。

### Phase 2：建立 Release Candidate Path

把 manifest、compilation、run、projection 和 Maybe publication 绑定到一个 release record。
先保留现有 CLI stage，但要求每个 stage 的 artifact 显式存在并通过 hash 关联，再考虑
性能优化或增加 connector。

### Phase 3：建立 Correctness Corpus 和 Feedback Loop

加入独立 expected output、mapping graph validation、reconciliation 和 discrepancy report。
然后把这些结果反馈到 transformer skill 和 session handoff，形成：

`inspect -> calculate -> compare -> trace -> propose -> execute -> review -> publish`

## 稳定性指标

按 period、provider 和 source family 统计：

- first-pass intake completeness rate；
- 需要 manual rerun 或 late source addition 的 run 比例；
- source-profile disagreement 数量；
- failed run 被误报为 published release 的次数；
- deterministic replay hash match rate；
- 各 stage 的 unresolved/review row 数量；
- mapping discrepancy detection 和 localization rate；
- allocation conservation failure 数量；
- Maybe publication rollback 次数；
- 每个 accepted release 的 reviewer minutes。

第一阶段目标不是增加更多 automation，而是做到：

1. 零 silent omission；
2. 零 stale 或 false publication；
3. 在支持的 source scope 内，mapping evidence 可重复、可解释、可审查。

## 本计划不包含

- 在当前 source scope 尚未稳定前继续增加更多 ecommerce domain。
- 通过 training 或 fine-tuning 弥补 source control 缺失。
- 在没有明确 correctness 或 performance gap 前替换 Frictionless 或现有 compiler。
- 在 local release 和 evidence behavior 还不可信前建设 hosted multi-tenant workflow。
