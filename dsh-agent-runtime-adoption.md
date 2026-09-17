# DSH 作为 FinClaw 代理运行时的采用建议

**日期:** 2026-09-15
**状态:** 架构建议，不是已批准的实现计划
**范围:** `deepseek-harness` 的插件化代理运行时设计如何用于 `finclaw-ng`，以及哪些部分不应该迁移。

## 1. TL;DR

建议把 DSH 放在 FinClaw 外层，作为**代理控制平面**；FinClaw 继续作为**确定性财务数据平面**。

```text
人类 / SDK / ACP / Web
        |
        v
DSH runtime
  - Cordis 插件组合
  - profile / bundle / patch
  - typed tools / skills / approvals
  - session log / replay
  - MCP / subagent / workflow
        |
        | 通过本地 stdio MCP 或子进程调用
        v
FinClaw command interface and proposed MCP adapter
  - project + mapping
  - Frictionless pipeline
  - allocation
  - Gold / analysis
  - catalog / lineage / retention
  - OTC / excelize / maybeai-sheet
```

核心判断：

1. **直接复用 DSH，不要复刻 DSH。** DSH 已经提供插件生命周期、能力缝、工具注册、会话日志、技能、审批、子代理、工作流和 MCP 桥接。FinClaw 不应再维护一套自己的通用代理循环。
2. **FinClaw 的确定性引擎不进入 DSH。** 映射编译、Frictionless 执行、精确算术、SQL allowlist、合约校验、Gold publication、lineage 和 retention 仍由 `finc` 拥有。
3. **模型只做 DSH 擅长的事。** 模型负责理解业务问题、选择工具和技能、请求澄清、解释差异、生成非权威诊断和 proposal。模型不负责计算公式、验证账本或静默推进权威版本。
4. **DSH 的 session log 是模型可见动作的 replay 源，不是财务 provenance。** FinClaw 的 lineage/provenance/retention 必须继续由 FinClaw 自己的不可变版本和证据链证明。

## 2. 为什么用 DSH，而不是在 FinClaw 里再做一个 agent harness

DSH 的基础是 Cordis：所有能力都是插件，插件注册是可逆 effect。这带来的收益对 FinClaw 特别有价值：

| DSH 能力 | FinClaw 收益 |
|---|---|
| profile + bundle + `cordis.patch.yml` | 一个客户或一个 vertical 是一组可叠加配置，不需要新 CLI 或复制主流程 |
| capability seam: Service Definition / Provider / Consumer | `finc` CLI、OTC、excelize、maybeai-sheet 可以独立替换 provider，不污染核心工具 |
| `ctx.tools.register(defineTool(...))` | 把 `finc` 命令变成强类型工具，参数在进执行体前校验 |
| session log + replay | 每次模型动作可回放，适合审计、排障和快照测试 |
| skills + `tool-skill` | 复用 `finclaw-ng/skills/*/SKILL.md`，按需加载，不长期占用上下文 |
| interaction / approval / questions | 把 `needs_review`、Gold promotion、mapping correction 等映射成失败关闭的人工决策 |
| MCP client | 不重写 OTC、Excel、Sheet 连接器，用 MCP 接入外部工具 |
| subagent / workflow | 并行跑独立 vertical、独立 source 检查或独立诊断，再回到主 agent |
| hooks | 复用 Claude Code / Codex 的已有 hooks，不另建 hook 框架 |
| credentials | 不把 token 放进 YAML 或 FinClaw package |

这些能力如果只在 FinClaw 内模仿，等于维护一个轻量版 DSH，长期成本高于直接集成。

## 3. 关键边界

| 关注点 | 应由 DSH 拥有 | 应由 FinClaw 拥有 | 共享协议 |
|---|---|---|---|
| Agent loop, model adapter, turn/step | 是 | 否 | DSH 调用 FinClaw tool/MCP |
| Tool schema, 参数校验, model-facing result | 是 | 否 | `finc.result/v1` 被 DSH 包装 |
| Session transcript, replay, compaction | 是 | 否 | DSH 记录 tool call/result |
| 财务 provenance, lineage, immutable version | 否 | 是 | DSH 只保存 FinClaw 返回的 URI/hash |
| Contracts, mappings, metrics, SQL allowlist | 否 | 是 | DSH 通过 typed tool 传递参数 |
| Business approval / review | 流程编排可在 DSH | 决策记录和权威状态在 FinClaw | `needs_review` 返回结构化 review |
| Source inspection, diagnosis, proposal | DSH skill/tool 可辅助 | FinClaw 拥有 diagnostic 和 proposal contract | `finc diagnose ...` |
| OTC / excelize / maybeai-sheet | DSH MCP client 接入 | FinClaw binding 拥有 readback 和 receipt | MCP tool name / JSON result |

最重要的约束：DSH 的 `cordis.patch.yml` 控制**运行期插件组合**，FinClaw 的 `config/catalog.yaml` 和 package 控制**业务配置权威**。两者不能合并成一个配置权威。

## 4. FinClaw 概念到 DSH 构造的映射

### 4.1 命令到工具

FinClaw 当前以 `finc` CLI 和 `finc.result/v1` 输出为主。最稳的接入方式不是逐条复刻 Python，而是把 FinClaw 暴露成一个 typed tool set 或一个本地 stdio MCP server。

建议第一批 DSH tools 只覆盖可组合的最小闭环：

| 当前支持的 FinClaw 命令 | 建议的 DSH tool | 主要参数 | 推荐 render intent |
|---|---|---|---|
| `finc pipeline run PROJECT` | `finclaw.pipeline.run` | `projectPath`, `outputFormat` | `terminal` 或 `generic` |
| `finc pipeline inspect PATH` | `finclaw.pipeline.inspect` | `manifestOrAttempt` | `generic` |
| `finc pipeline run --from-run ATTEMPT` | `finclaw.pipeline.retry` | `attempt`, `approvedOverrides` | `generic` |
| `finc allocation preview PROJECT` | `finclaw.allocation.preview` | `projectPath`, `policy` | `generic` |
| `finc allocation validate PROJECT` | `finclaw.allocation.validate` | `projectPath`, `policy` | `generic` |
| `finc allocation explain PROJECT --run RUN_ID` | `finclaw.allocation.explain` | `projectPath`, `runId`, `policy` | `generic` |
| `finc analyze run REQUEST` | `finclaw.analyze.run` | `requestPath`, `paramsPath`, `goldVersion` | `generic` |
| `finc analyze present RESULT_REF --to markdown` | `finclaw.analyze.present` | `resultRef`, `format` | `generic` |
| `finc gold inspect GOLD_VERSION` | `finclaw.gold.inspect` | `version` | `generic` |
| `finc gold conformance SUBMISSION` | `finclaw.gold.conformance` | `submissionPath` | `generic` |
| `finc diagnose from-run RUN_ID` | `finclaw.diagnose.from_run` | `runId` | `generic` |
| `finc diagnose from-result RESULT.json` | `finclaw.diagnose.from_result` | `resultPath` | `generic` |
| `finc diagnose validate PROPOSAL` | `finclaw.diagnose.validate` | `proposalPath` | `generic` |
| `finc catalog validate --catalog config/catalog.yaml` | `finclaw.catalog.validate` | `catalogPath`, `repositoryRoot` | `generic` |

`finclaw.analyze.plan`、`finclaw.analyze.export`、Gold intake/verify 和 retention cleanup 只是建议的 DSH 工具能力；当前 `finc` CLI 没有这些可执行命令。在 CLI 合约落地前，它们不能作为 adapter 调用目标。

这些工具应返回结构化 JSON 给模型，同时保存 FinClaw 原生的 run ID、version manifest、artifact path、diagnostics 和 review。DSH 的 UI 展示只负责如何渲染结果，不改变模型看到的权威事实。

### 4.2 Skills

FinClaw 已经有 `skills/*/SKILL.md`。DSH 可以直接通过 `skill-filesystem` 发现它们，并通过 `tool-skill` 把 catalog 暴露给模型。不要把这些 Markdown 复制进 DSH bundle，避免出现两套 skill 源。

以下是概念示意，字段以 DSH 当前 `skill-filesystem` 配置 catalog 为准：

```yaml
- id: skill-filesystem
  name: '@deepseek-ai/dsh-skill-filesystem'
  config:
    # 指向 finclaw-ng/skills 或部署后的技能目录
    customSkillDirs:
      - /path/to/finclaw-ng/skills
      - /path/to/finclaw-ng/verticals/ecommerce/skills
```

`$transformer-pipeline`、`$analyze`、`$diagnose-variance`、`$analysis-output-governance` 和 `$customer-decisions` 的既有边界应保留为 DSH skill 内容，不改写它们的权威逻辑。

### 4.3 Verticals 和 customer profiles

FinClaw 的 vertical 包和 customer profile 是业务配置，不应该在 DSH 里重新建模。DSH 只负责选择哪个 FinClaw 配置包、哪些工具和哪些技能被挂载。

建议使用三个 DSH profile 族：

| DSH profile | 用途 | 特点 |
|---|---|---|
| `finclaw-web` | 人类审阅和交互式分析 | 开启 approval、questions、skills、MCP、web |
| `finclaw-headless` | CI、批量运行、one-shot | startup-only patch，审批失败关闭 |
| `finclaw-sdk` / `finclaw-acp` | SDK 或自动化 | stdout/stdio 协议，权限更窄 |

客户 overlay 只替换 customer 相关的 FinClaw 路径、默认垂直、审批策略和可见工具。客户专属 mapping、rules、decisions 仍留在 `input-data/<company>/<batch>/custom_rules/`，不能进入 DSH 插件。

### 4.4 人工审批

FinClaw 的以下状态适合映射到 DSH `user-approval` 和 `user-questions`：

| FinClaw 状态 | DSH 交互 |
|---|---|
| `needs_review` | 通过 `ask_user_question` 或专用 tool 请求结构化决策 |
| mapping correction proposal | 请求 approve/reject/edit，未回答则失败关闭 |
| Gold promotion | 单独的 post-grade 人工决策或预存在 promotion policy |
| variance response `candidate` | 每次 isolated apply 前 fresh human approval |
| `bounded_auto` | 只能由显式 FinClaw autonomy policy 授权，不能被 DSH 默认放宽 |

DSH 的 permission preset 适合做人类可选择的沙盒与审批组合：

```yaml
- id: permission
  name: '@deepseek-ai/dsh-permission-presets'
  config:
    presets:
      finclaw-read-only:
        sandbox: read-only
        approval: ask
      finclaw-review:
        sandbox: workspace-write
        approval: ask
      finclaw-danger:
        sandbox: danger-full-access
        approval: never
```

但 `finclaw-danger` 应只用于非权威演示，不能成为财务数据 mutation 的默认授权。

### 4.5 MCP 和外部数据面

OTC、excelize、maybeai-sheet、Google Sheets 和 ERP 都可以通过 DSH `mcp-client` 接入。MCP 工具的 namespace 可以保持稳定，且不会进入 FinClaw 的业务配置。

示例：

```yaml
- id: mcp-finclaw-otc
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: finclaw-otc
    transport: stdio
    command: uv
    args: ['run', 'finc', 'mcp']
    failOnStartupError: true
```

如果 `finc` 还没有独立 MCP server，可以先做一个最小 stdio server，内部继续调用现有 `finc` 命令。这个 MCP server 只做协议适配，不复制 domain logic。

### 4.6 Subagent 和 workflow

适合用 DSH subagent/workflow 的 FinClaw 任务：

- 并行执行多个 independent source preflight；
- 并行运行不同 vertical 的只读 catalog/source inspection；
- 并行生成不同 diagnosis proposal，再由主 agent 汇总；
- 对同一结果跑只读 evaluation 或 mutation test；
- 将长任务切分为独立 worker，再回到主会话。

不适合用 workflow 的 FinClaw 任务：

- `needs_review` 之后的自动推进；
- 有依赖顺序的 pipeline stage；
- Gold publication；
- 需要单一人审批的 mutation；
- 财务账本计算和 promotion。

## 5. 建议的 bundle 结构

建议新建一个 out-of-tree DSH bundle，而不是改 `deepseek-harness` 的核心包。

```text
finclaw-dsh/
  packages/
    dsh-finclaw-base/        # patch carrier: tools, skills, approval, MCP
    dsh-finclaw-tools/       # typed finc CLI tools
    dsh-finclaw-skills/      # FinClaw skill discovery/labels
    dsh-finclaw-vertical-ecommerce/
    dsh-finclaw-vertical-reference-finance/
    dsh-finclaw-web/
    dsh-finclaw-headless/
  profiles/
    finclaw-web/package.json
    finclaw-headless/package.json
    finclaw-sdk/package.json
```

其中 `dsh-finclaw-base` 的第一层可以是 `@deepseek-ai/dsh-base`，然后插入 FinClaw rows。每个 FinClaw vertical 只贡献 skills、工具限制和默认路径，不贡献财务计算。

### 5.1 工具适配示意

以下是概念代码，不是可直接运行的 package。工具执行体通过 `finc --output-format json` 获取结构化结果，再转成 DSH 可回放的 `ToolResult`。

```ts
import { defineTool } from '@deepseek-ai/dsh-tools'

ctx.tools.register(defineTool({
  name: 'finclaw_pipeline_run',
  description: 'Run a finc pipeline project and return the immutable run manifest and review state.',
  parameters: {
    projectPath: { type: 'string', required: true, description: 'Absolute path to project.yaml' },
    policy: { type: 'string', description: 'Optional allocation policy id' },
  },
  output: {
    schema: { type: 'object' },
    render: (_args, value) => [{
      type: 'text',
      text: `Pipeline status: ${value.status}\n${JSON.stringify(value.data ?? {})}`,
    }],
  },
  async execute(args, exec) {
    const output = await runFinCJson(
      ['pipeline', 'run', args.projectPath],
      { signal: exec.signal },
    )
    if (output.review?.id) {
      return {
        status: output.status,
        data: output.data,
        diagnostics: output.diagnostics,
        review: output.review,
      }
    }
    return output
  },
}))
```

模型看到 `review.id` 后应停止自动推进，并调用审批或问题工具。不能因为模型判断“看起来可以通过”就直接重跑或 promote。

## 6. 直接复用 DSH 的收益

### 6.1 减少通用层重复建设

FinClaw 现在需要维护 CLI、skills、diagnostics、governance、runtime state 和未来可能的 UI/SDK。DSH 已经覆盖其中通用部分：

- 多 provider/model 路由和请求重试；
- 会话持久化、标题、replay、compaction；
- tool schema 校验和 `tools/pre-execute` 等拦截点；
- 文件系统沙盒和权限策略；
- skill catalog；
- MCP、subagent、workflow；
- Web / SDK / ACP surface。

FinClaw 只需维护业务面，不需要自建 agent harness。

### 6.2 可审计的模型行为

DSH 的 `model-visible <=> logged` 约束意味着模型看到的 FinClaw tool schema、调用的 command、返回结果都进入 session log。这样可以让产品行为可回放，但不能把 session log 当成财务 provenance。财务证据仍由 FinClaw 的 version manifest、contract hash、run evidence 和 OTC receipt 承担。

### 6.3 可替换 provider 与 vertical

DSH 的 capability seam 可以让 FinClaw 的不同 provider 更清晰地解耦：

| Seam | Service Definition | Provider | Consumer |
|---|---|---|---|
| FinClaw runtime | `ctx.finClaw` | stdio `finc` MCP adapter | typed tools / skills |
| external table / OTC | MCP tool namespace | `finclaw-otc` MCP server | FinClaw commands + agent |
| analysis workbook / Excel | MCP tool namespace | `excelize-mcp` / `maybeai-sheet` | FinClaw readback tool |
| diagnostics | `ctx.finClawDiagnostics` | current `finc diagnose` adapter | diagnosis skill |

这样不会把 FinClaw 核心逻辑拆散，只会让外部依赖可替换。

## 7. 不能做的事

| 风险 | 原因 |
|---|---|
| 把 `finc` 的业务引擎重写成 DSH TypeScript 插件 | 失去现有 Frictionless、精确算术、contract、lineage 和测试 oracle |
| 用 `run_code` 或 arbitrary Python 计算财务指标 | 违反 FinClaw 的确定性控制原则，也无法形成财务 provenance |
| 把 DSH session log 当作 FinClaw lineage | DSH 记录 agent 动作，FinClaw 记录数据血缘和不可变版本，语义不同 |
| 用 DSH profile/patch 取代 FinClaw `config/catalog.yaml` | 一个控制运行期组合，一个控制业务配置权威 |
| 让 `needs_review` 被模型自动 resolve | 破坏 independent approval 和 promotion gate |
| 把 customer rules 放回 vertical/DSH plugin | 与 “customer decisions live in input-data/custom_rules” 冲突 |
| 用 workflow 并行化有依赖的 pipeline stage | 发布顺序和 accepted root 可能错乱 |

## 8. 推荐迁移顺序

### Phase 0: Read-only MCP spike

只做一个 FinClaw stdio MCP server，让 DSH 通过 `mcp-client` 调用 `finc gold inspect`、`finc pipeline inspect` 和 `finc diagnose from-run`。不修改 FinClaw 领域代码。

验收：

- 一个 DSH profile 能发现 FinClaw MCP tools；
- 一次只读 inspect 能写入 DSH session log；
- FinClaw 返回的 hash/run id/artifact path 不被 DSH 改写。

### Phase 1: Typed tools 和 skills

把第一批命令包成 DSH tools，接入现有 `skills/*/SKILL.md`。加入 `ask_user_question` 审批流，确保 `needs_review` 失败关闭。

验收：

- 一个端到端 `pipeline run -> needs_review -> human decision -> retry` 流程；
- 无人工回答时不会 promote；
- 有 keyless snapshot 覆盖工具参数校验和 review 分支。

### Phase 2: Profiles, bundles 和 customer overlays

建立 `finclaw-web`、`finclaw-headless`、`finclaw-sdk` 三个 profile。用 bundle/patch 组合不同 vertical 和 customer，不把 customer 规则混入通用插件。

验收：

- 不同 profile 的工具可见集合不同；
- customer overlay 只替换路径、默认配置和审批策略；
- `dsh --profile finclaw-headless --dump-config` 可以审计最终组合。

### Phase 3: Subagent/workflow 和外部 MCP

把独立的 source preflight、vertical inspection、diagnosis proposal 等并行任务放到 subagent/workflow。接入 OTC、excelize、maybeai-sheet 等外部 MCP，只用于工具交互和 readback，不复制 FinClaw 引擎。

验收：

- 并行任务有 parent/child 可见性；
- child 不能直接 promote Gold；
- 外部 MCP failure 不会静默产生财务结果。

### Phase 4: SDK/ACP/Web 产品面

在稳定后暴露 SDK/ACP 或 Web，复用 DSH 的 transport。产品仍以 FinClaw 的 accepted root、Gold version 和 lineage 为权威。

验收：

- SDK/ACP 能启动 `finclaw-sdk` profile；
- 自动化模式无法绕过审批；
- 财务 artifact 由 FinClaw store 管理，DSH 只保存引用。

## 9. 如果只借鉴 DSH 设计，不引入 DSH runtime

如果决策是不引入 Node/DSH runtime，也应该把 DSH 的以下约束提炼进 FinClaw：

1. **插件注册是 effect**：所有 tool、provider、skill 注册返回 disposer，生命周期可回滚。
2. **能力缝完整**：一个 FinClaw capability 由 Service Definition、Provider、Consumer 组成，不允许只有 provider 或只有 consumer。
3. **模型可见即日志**：模型看到的 command、参数和结果必须能从 run/session log 重构。
4. **显式边界，不隐藏默认**：provider resolve 由 owner 显式完成，不在 `run()` 内部静默 `?? default`。
5. **错误失败得大声**：缺失 contract、mapping、policy、provider 时在可解析点失败，不能静默跳过。
6. **配置不硬编码**：垂直、customer、超时、root、approval 都来自 validated config。
7. **UI presenter 纯函数**：只依赖 args/result，不读取时钟、session 或执行 I/O，保证 replay。
8. **人工审批失败关闭**：所有 candidate/mutation/promotion 默认 deny。

这套约束与 FinClaw 的 configuration authority consolidation、immutable Silver/Gold、lineage、retention 和 diagnosis 设计方向一致。但如果没有外部运行时需求，直接采用 DSH 比在 FinClaw 中再造这些通用机制更省成本。

## 10. 需要决策的问题

1. DSH 是作为 FinClaw 的唯一 agent runtime，还是仅作为 Web/SDK/ACP 的其中一种 surface。
2. `finc` 与 DSH 的边界优先采用 stdio MCP，还是 typed TS tool adapter。
3. 是否允许 DSH 直接触发 `pipeline run`、`retry` 和 `analyze run`，还是先只开放只读 inspection 和 diagnosis。
4. FinClaw authority contract 与 DSH tool schema 的版本如何同步，尤其是 `finc.project/v1`、`finc.analysis-request/v6`、`finc.metric/v1`。
5. 哪些审批必须由 FinClaw 自己的 review record 证明，哪些可由 DSH approval transcript 证明。

## 11. 相关文件

- deepseek-harness 外部源码参考：`docs/architecture.md`
- deepseek-harness 外部源码参考：`docs/capability-seams.md`
- deepseek-harness 外部源码参考：`packages/core/tools/README.md`
- deepseek-harness 外部源码参考：`packages/skill/README.md`
- deepseek-harness 外部源码参考：`packages/interaction/README.md`
- deepseek-harness 外部源码参考：`packages/mcp/README.md`
- deepseek-harness 外部源码参考：`packages/subagent/README.md`
- deepseek-harness 外部源码参考：`packages/workflow/README.md`
- [finclaw-ng README](../README.md)
- [finclaw-ng overview](research/finclaw-overview.md)
- [FinClaw configuration authority consolidation design](superpowers/specs/2026-09-15-configuration-authority-consolidation-design.md)
- [FinClaw configuration consolidation analysis plan](superpowers/plans/2026-09-15-configuration-consolidation-analysis.md)
