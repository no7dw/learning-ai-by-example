# FinClaw-NG `src/` 简化计划

**日期：** 2026-09-22  
**范围：** `src/finc/` 中与 Excel/CSV intake、pipeline build、mapping validation 和 publication 相关的代码  
**目标：** 在不改变 public behavior、data contract、hash、lineage 和 review gate 的前提下，减少重复 orchestration 和隐含状态

## 结论

当前最值得简化的不是 domain semantics，而是执行路径的重复表达：

1. `src/finc/cli.py::_run` 同时承担 parser dispatch、path resolution、业务调用、错误分类和 output rendering。
2. `src/finc/pipeline/plan.py::_validate_pipeline` 与 `src/finc/pipeline/runner.py::_run_pipeline` 分别实现了相似的 source/stage/binding/resource 解析逻辑。
3. provenance、diagnostic、input resource 和 proposal drift 检查分散在多个小函数中，规则容易重复或发生轻微漂移。
4. best-practise guide 要求使用多个稳定 stage，但 `cmd-history.md` 显示用户需要手工重复 `generate -> validate -> plan -> run -> inspect -> project -> import`。

建议采用 `code-simplifier` 的原则：先读现状，做 behavior-preserving refactor，优先提取小而清晰的 helper；不以“统一”为理由创建新的复杂 framework。

## 简化边界

### 可以简化

- command dispatch 的组织方式；
- stage binding、source path、resource lookup 的重复代码；
- diagnostic metadata 的重复声明；
- provenance verification 的重复 try/except 和字段校验；
- public workflow 的重复命令输入。

### 不能简化掉

- `needs_review` 阻止 Silver/Gold 的 gate；
- immutable run、content hash、target hash、mapping hash 和 transformer hash；
- allocation 的 amount conservation、rounding 和 residual 规则；
- input-row disposition、source coordinates 和 lineage evidence；
- failed attempt 与 published release 的区别；
- `validate`、`plan`、`run`、`inspect` 各自留下的可审计 artifact。

## 优先级总表

| 优先级 | 简化项 | 主要代码位置 | 风险 | 建议 |
| --- | --- | --- | --- | --- |
| P0 | 拆分 CLI dispatch，保留统一 error/output boundary | [`src/finc/cli.py`](../../src/finc/cli.py#L283-L1214) | 低 | 先做，收益立即可见 |
| P0 | 抽取 shared stage/binding helpers，减少 validation/run 漂移 | [`src/finc/pipeline/plan.py`](../../src/finc/pipeline/plan.py#L833-L1049)、[`src/finc/pipeline/runner.py`](../../src/finc/pipeline/runner.py#L1488-L1813) | 中 | 先补 parity tests，再逐步抽取 |
| P1 | 增加一个高层 pipeline orchestration command，隐藏重复 CLI 步骤 | [`src/finc/cli.py`](../../src/finc/cli.py)、pipeline modules | 中 | 不合并内部 stage，只组合已有 artifact |
| P1 | 集中 diagnostic metadata 和 error classification | [`src/finc/diagnostics.py`](../../src/finc/diagnostics.py#L8-L72) | 低 | 用 module-level registry 替代每次实例化时创建 dict/set |
| P1 | 简化 auxiliary input 和 workbook resource loading | [`src/finc/transformer/input_resources.py`](../../src/finc/transformer/input_resources.py#L31-L110) | 低 | 提取 path validation、workbook read、row projection helper |
| P2 | 简化 Silver provenance verification 和 proposal drift check | [`src/finc/gold/silver.py`](../../src/finc/gold/silver.py#L361-L441)、[`src/finc/transformer/proposals.py`](../../src/finc/transformer/proposals.py#L306-L334) | 中 | 最后做，必须保持 failure code 不变 |

## P0-1：拆分 CLI dispatch

### 当前复杂度

`_run` 从 `cli.py:283` 延伸到 `cli.py:1214`，一个函数同时处理：

- interactive help、version、completion、doctor；
- allocation、transformer、pipeline、source、config、lineage、gold、metrics 等 family；
- path normalization 和 config resolution；
- `SyncError`、`CompilationFailure` 和通用 exception 到 `CompilerDiagnostic` 的转换；
- JSON/text output。

这使得增加一个 command 时容易影响不相关的 family，也让每个 family 的测试需要经过
同一个大型函数。

### 简化方案

保留 `_run` 作为薄的 top-level boundary，只做四件事：

1. 处理 `help/version/completion/doctor`；
2. 根据 `args.family` 选择 handler；
3. 统一捕获并标准化 exception；
4. 调用 `_emit`。

将 family 逻辑拆为局部 handler，例如：

```text
_handle_transformer(args, command)
_handle_pipeline(args, command)
_handle_source(args, command)
_handle_allocation(args, command)
_handle_gold(args, command)
_handle_metrics(args, command)
```

不要立即引入动态 plugin registry。当前静态 mapping 已足够，重点是减少 nesting 和
保持每个 handler 的输入/输出边界清晰。

### 验收标准

- 所有现有 CLI command 的 exit code、JSON schema、diagnostic code 和 stderr behavior 不变。
- 每个 family 可以独立通过 focused tests。
- `_run` 不再包含具体的 mapping、pipeline 或 Gold business logic。
- `--debug` 仍然保留原始 exception 行为。

## P0-2：共享 `validate` 和 `run` 的 stage/binding 解析

### 当前复杂度

`_validate_pipeline` 与 `_run_pipeline` 都需要重复完成以下工作：

- 遍历 `plan.selected_sources`；
- 判断 Raw/Silver stage 是否 selected；
- 解析 `PIPELINE_STAGES_KEY` 和 stage binding；
- 找到 source input、pipeline descriptor、schema 和 output；
- 解析 prepared output、driver input 和 mapping resources；
- 创建 Frictionless `Resource` 并执行 schema validation。

其中 validation 版本使用 temporary output 和 bounded rows，runner 版本使用 staged
output 和 full rows。这两个行为差异必须保留，但 source/binding 解析不应复制两套。

### 简化方案

分三层提取 helper：

1. **纯解析层**
   - `iter_selected_stage_bindings(plan, stage)`
   - `resolve_source_input(plan, source, binding)`
   - `resolve_stage_descriptor(plan, files, binding)`
   - `resolve_stage_schema(binding, target_schema)`

2. **resource/context 层**
   - `build_stage_context(...)`
   - `resolve_named_resource(...)`
   - `resolve_driver_inputs(...)`

3. **执行策略层**
   - validation 使用 `sample_rows` 和 `TemporaryDirectory`；
   - runner 使用 full rows、staging 和 publication evidence。

也就是说，只共享“如何找到和解释 stage”，不强行让 validation 和 runner 共用同一个
执行函数。这样既减少重复，也避免把 bounded validation 和 full publication 混成一个难以
调试的 abstraction。

### 实施顺序

1. 先为现有 `plan` 和 `runner` 记录相同 input 下的 stage/binding snapshot。
2. 提取只读 helper，不改变执行代码。
3. 对 raw、prepared、driver、silver 四类 stage 分别迁移。
4. 每迁移一类，运行 validation/run parity tests。
5. 最后删除原有重复的 binding 解析代码。

### 验收标准

- 同一 compilation manifest 在 `validate` 和 `run` 中得到相同的 source/stage/binding identity。
- `sample` 和 `full` 只在 row limit、staging root 和 publication 行为上不同。
- `needs_review`、review artifact、driver barrier 和 Silver coverage behavior 不变。
- 错误 code 和错误所在 stage 不发生无意改变。

## P1-1：增加高层 Pipeline Orchestration Command

### 背景

best-practise guide 要求：

```text
transformer generate
pipeline validate
pipeline plan
pipeline run
pipeline inspect
projection
Maybe import
```

但 `cmd-history.md` 记录了多个 temporary input root、`v1` 到 `v4` 的手工 CSV，以及
多次 worksheet delete/create。问题不是这些 stage 没有价值，而是用户必须手工复制和
传递大量 artifact path。

### 简化方案

增加一个薄的高层入口，例如：

```bash
uv run finc pipeline release PROJECT.yaml \
  --input-root reference/finclaw-ecommerce/input \
  --output-format json
```

该 command 只负责 orchestration：

1. 调用现有 `generate`；
2. 保存 compilation manifest path/hash；
3. 调用 `validate` 和 `plan`；
4. 调用 `run`；
5. 对 immutable version 执行 `inspect`；
6. 生成 release candidate metadata 和 compact projection artifact；
7. 输出下一步 Maybe import 所需的明确 artifact。

第一版不应自动执行危险的 worksheet delete/create。Maybe publication 仍然是显式 command，
但它消费 release candidate，而不是消费用户手工挑选的 `/tmp/*.csv`。

### 验收标准

- 单次 command 生成可重放的 compilation、run、inspect 和 projection artifact。
- 所有内部 stage 仍然可以单独调用，兼容现有 users 和 tests。
- output 明确区分 `candidate`、`validated` 和 `published`。
- command failure 能指出失败 stage 和对应 artifact path。

## P1-2：集中 Diagnostic Metadata

### 当前复杂度

`CompilerDiagnostic.__post_init__` 每次实例化都创建 `phases` dict 和 reviewable code set。
这些规则实际是 static metadata，但目前和对象初始化逻辑混在一起。

### 简化方案

将其改为 module-level constants：

```python
DIAGNOSTIC_PHASES = {...}
REVIEWABLE_CODES = frozenset({...})
```

`__post_init__` 只做 lookup 和 default assignment。进一步可以提供：

```python
def diagnostic_phase(code: str) -> str: ...
def is_reviewable_code(code: str) -> bool: ...
```

这样新增 error code 时，metadata 变更位置单一，也更容易测试覆盖。

### 验收标准

- 所有现有 diagnostic code、phase 和 reviewable behavior 完全一致。
- 新增 code 只需更新一个 registry。
- 诊断排序和 serialized output 不变。

## P1-3：简化 Auxiliary Input 和 Workbook Resource Loading

### 当前复杂度

`input_resources.py` 同时处理 path security、file existence、byte/row limits、workbook
opening、worksheet header 校验、field projection、fingerprint 和 source metadata。
这些职责都合理，但现在主要集中在一个连续流程中，导致 reader mode 或 source profile
问题不容易单独测试。

### 简化方案

拆成三个小边界：

1. `validate_input_declarations(config) -> tuple[ResolvedInput, ...]`
2. `read_workbook_surface(resolved_input) -> WorkbookSurface`
3. `project_surface(surface, declaration) -> MappingResource`

`load_input_resources` 只负责 orchestration 和 limits。所有 helper 都应保留当前的
`CompilationFailure` 和 `INPUT_RESOURCE_INVALID` behavior。

这一步直接服务于稳定性计划的 source intake：未来 intake manifest 可以复用
`ResolvedInput` 和 `WorkbookSurface`，不再重新实现一套 profiling。

### 验收标准

- path traversal、migration path、missing file、duplicate header、missing field 和 size limit 的 error behavior 不变。
- workbook parse failure 和 worksheet iteration failure 仍然返回相同 failure category。
- source fingerprint 和 `__finclaw_source_*` metadata 不变。
- 可以针对错误 sheet、错误 header row 和 reader ambiguity 写小型 tests。

## P2-1：集中 Silver Provenance Verification

### 当前复杂度

`resolve_verified_silver_run` 当前在一个函数中依次完成 run record、compilation manifest、
target contract、selected outputs、output hashes、content schema 和 mapping/transformer
hash 的验证，并用多个相似的 `try/except ValueError` 转成不同的
`VerifiedSilverFailure` category。

### 简化方案

保留每个 failure category，但提取纯 helper：

```text
load_verified_run_record(run_id, state_root)
verify_compilation_manifest(record)
verify_target_contract(record, manifest, expected_contract)
load_verified_fragments(record, manifest, contract_id)
build_verified_silver_run(record, fragments)
```

不要删掉任何 hash check。目标是让每一段校验只负责一个 provenance boundary，便于测试
和定位失败。

### 验收标准

- `_RUN_INVALID`、`_PROVENANCE_INVALID`、`_CONTRACT_INVALID` 和 `_OUTPUT_INVALID` 分类不变。
- 同一损坏 fixture 仍然产生相同 failure category 和核心 message。
- verified run 的返回 data shape 不变。

## P2-2：简化 Proposal Input Drift Check

### 当前复杂度

`_comparison_input_state` 同时解析 dependency key、选择 root、处理 mapping override、
执行 hash check、执行 file identity check 和构造 failure。它的正确性很重要，但
`json.loads(key)`、path resolution 和 stat identity 细节混在一个 loop 中。

### 简化方案

提取：

- `parse_dependency_key(key)`；
- `resolve_dependency_path(manifest, dependency)`；
- `file_identity(path)`；
- `assert_dependency_unchanged(path, expected_hash, expected_identity)`。

保留“same bytes but restored file identity changed”这一保护；这不是可以删除的冗余，而是
proposal comparison 的并发安全约束。

## 不建议现在简化的部分

### `silver_store` publication locking

publication recovery、head chain 和 FileLock 看起来复杂，但它们直接保护 immutable release
和 failed/concurrent run behavior。应先补测试和文档，不能为了短代码而合并状态转换。

### Gold `run_gold` orchestration

`run_gold` 有较长的 orchestration，但它同时绑定 pins、input frames、runtime identity、
lineage、registry registration、conformance 和 replay。除非先定义清楚 release candidate
boundary，否则不应把它与 Silver pipeline 合并。

### Mapping semantics 和 allocation policy

mapping classification、allocation、rounding 和 residual handling 是业务 authority，不是
普通的重复代码。它们可以改善命名和测试 fixture，但不能为了“统一”抽成会隐藏
`amount_category`、`amount_subcategory`、`event_type` 变化的通用 rule engine。

## 交付顺序

### Phase 1：低风险局部 refactor

完成 CLI handler 拆分、diagnostic registry 和 input resource helper。每一步都保持现有
CLI output 和 error contract，并运行 focused tests。

### Phase 2：Pipeline parity refactor

为 `validate` 和 `run` 增加 stage/binding snapshot 与 parity tests，再抽取 shared resolver。
禁止在没有 parity evidence 时直接重写 `_validate_pipeline` 或 `_run_pipeline`。

### Phase 3：Public workflow simplification

增加高层 `pipeline release` orchestration，使实际 ingest 不再依赖大量 `/tmp` 文件和手工
命令复制。保持现有低层 commands 作为 debug 和 recovery tools。

### Phase 4：Provenance 和 proposal cleanup

最后拆分 `resolve_verified_silver_run` 与 `_comparison_input_state`。这两个区域的目标是
可读性和 failure localization，不改变 verification strength。

## 验证要求

每个 phase 至少验证：

- 现有 unit tests 和 CLI contract tests；
- July input 的 source/stage/row count parity；
- `needs_review` 仍然阻止 Silver/Gold；
- same input + same definitions 的 replay hash；
- deliberate wrong mapping 的 discrepancy；
- failed run、concurrent publication 和 stale candidate 的 failure behavior；
- `cmd-history.md` 中 forwarder correction、purchase input、purchase cost 和 finance handling 的代表性路径。

## 成功标准

简化完成后，代码和使用方式应达到：

1. 新增一个 pipeline family 时，不需要修改一个千行级 CLI dispatcher。
2. `validate` 与 `run` 对同一 manifest 的 stage/binding 解释一致。
3. 普通 ingest 从多个手工 command 变成一个可追踪的 high-level command，同时保留低层 debug command。
4. provenance、review gate、lineage 和 hash 保护没有减少。
5. 简化后的错误信息更容易回答：哪个 source、哪个 stage、哪个 artifact、哪个 contract 失败。

## 参考资料

- [FinClaw-NG 稳定性改进计划](./2026-09-22-finclaw-stability-improvement-plan.md)
- [cmd-history.md](../../cmd-history.md)
- [best-practise-guide-ingest-pipeline.md](../../best-practise-guide-ingest-pipeline.md)
- [code-simplifier skill](/Users/dengwei/.codex/skills/code-simplifier/SKILL.md)

