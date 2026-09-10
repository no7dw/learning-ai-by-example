# 防止 LLM Hallucination：从 RAG 到 Grounding、Verification 和 Repair

LLM 的幻觉不是一个单独的 Prompt 问题，而是一个系统设计问题。

如果系统只有这一条链路：

```text
User -> LLM -> Answer
```

模型只能依赖参数记忆和当前上下文。它可能生成语法正确、表达流畅、但没有事实依据的内容。

更可靠的系统需要把“获取事实”“生成答案”“检查答案”和“处理失败”拆开：

```text
User
  |
  v
Retrieve / Tool / MCP / DB
  |
  v
Evidence / Facts
  |
  v
LLM Generator
  |
  v
Draft Answer
  |
  v
Verifier
  |
  +-- PASS ------------------------------> Final Answer
  |
  +-- FAIL -> Repair / Re-retrieve / Abstain
```

本文用 Self-RAG、CRAG、Grounding Check 和 Agent Harness 统一解释这条路线。

## 1. 先建立正确的目标

防止幻觉不等于让模型“永远说真话”。工程上更可操作的目标是：

1. 事实类问题尽量使用权威工具或数据源，而不是让模型凭记忆回答。
2. 让每个重要结论都能追溯到具体证据。
3. 把“证据不存在”“证据冲突”“推理超出证据”和“数据计算错误”区分开。
4. 检查失败时，选择重新检索、修改答案、降低表述强度或拒答，而不是让模型继续猜。
5. 让确定性的业务规则和计算由代码执行，而不是由 LLM 自由推理。

最重要的一句原则是：

```text
让 LLM 决定下一步做什么，但不要让 LLM 单独决定事实是什么。
```

## 2. 传统 RAG 的问题

传统 RAG 通常是：

```text
Question
   |
   v
Retriever
   |
   v
Retrieved Documents
   |
   v
Question + Documents
   |
   v
LLM
   |
   v
Answer
```

它比纯参数记忆更可靠，但仍然有三个问题：

- 不需要检索的问题也可能被强行塞入文档。
- Retriever 可能返回无关、过时或误导性的文档。
- LLM 可能只使用文档的一部分，或者生成超出文档支持范围的结论。

因此，不能把“检索到了文档”直接等同于“答案已经有依据”。

```text
Retrieved document exists
              !=
Every answer claim is supported
```

## 3. Self-RAG：让模型控制检索和自我反思

论文：

- [Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection](https://arxiv.org/abs/2310.11511)
- arXiv:2310.11511，提交于 2023 年 10 月 17 日

Self-RAG 解决的是：

```text
什么时候应该检索？
检索到的 passage 是否有用？
生成的内容是否被 passage 支持？
多个候选答案中哪个更好？
```

论文的核心机制是 reflection tokens。模型不只生成普通文本，也学习生成用于控制和评价的特殊 token。

### 3.1 Self-RAG 的数据流

```text
                         +----------------+
                         |     Query      |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         |   Self-RAG LM  |
                         | Should retrieve?|
                         +--------+-------+
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
              [NO RETRIEVE]                [RETRIEVE]
                    |                           |
                    |                           v
                    |                   +---------------+
                    |                   |   Retriever   |
                    |                   +-------+-------+
                    |                           |
                    |                    d1, d2, ..., dk
                    |                           |
                    |                           v
                    |                   +---------------+
                    |                   | Generate /    |
                    |                   | critique each |
                    |                   | passage       |
                    |                   +-------+-------+
                    |                           |
                    |                           v
                    |                   +---------------+
                    |                   | Reflection    |
                    |                   | tokens        |
                    |                   | relevance     |
                    |                   | support       |
                    |                   | utility       |
                    |                   +-------+-------+
                    |                           |
                    +-------------+-------------+
                                  |
                                  v
                         +----------------+
                         | Select / rank  |
                         | candidate      |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         | Continue       |
                         | generation     |
                         +----------------+
```

### 3.2 Reflection token 的作用

Self-RAG 把“检索和评价”放进模型可以预测的 token 序列中：

```text
Question
   |
   v
[Retrieve: YES]
   |
   v
Retriever -> d1, d2, d3
   |
   v
d1 -> [Relevant]
d2 -> [Irrelevant]
d3 -> [Relevant]
   |
   v
Generate candidate answer
   |
   v
[Supported: Fully]
   |
   v
[Utility: High]
   |
   v
Select candidate and continue
```

它不是简单的：

```text
Retrieve -> Generate
```

而更像：

```text
Retrieve?
   -> Retrieve
   -> Generate
   -> Critique
   -> Select
   -> Continue generation
   -> Critique again
```

Self-RAG 的关键特点：

- 一个模型学习检索控制、生成和反思。
- 检索不是固定发生，而是按需发生。
- passage relevance、generation support 和整体 utility 都进入推理控制。
- 重点是生成过程中的自我反思，而不是在生成结束后才做一次外部检查。

Self-RAG 仍然不能保证事实一定正确。它可以改善检索和引用行为，但它依赖训练出来的反思能力，以及 Retriever 提供的候选证据质量。

## 4. CRAG：先评估 Retriever，错误时修正检索

论文：

- [Corrective Retrieval Augmented Generation](https://arxiv.org/abs/2401.15884)
- arXiv:2401.15884，初次提交于 2024 年 1 月 29 日，后续版本于 2024 年 10 月修订

CRAG 解决的是：

```text
Retriever 找错了怎么办？
```

传统 RAG 常常默认：

```text
Retriever 返回了文档 -> 文档应该可信 -> LLM 使用文档
```

CRAG 在 Retriever 和 Generator 之间增加了 Retrieval Evaluator，并根据检索质量触发不同动作。

### 4.1 CRAG 总体数据流

```text
                         +----------------+
                         |     Query      |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         |   Retriever    |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         | Retrieved docs |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         | Retrieval      |
                         | Evaluator      |
                         +--------+-------+
                                  |
              +-------------------+-------------------+
              |                   |                   |
              v                   v                   v
          CORRECT            AMBIGUOUS            INCORRECT
              |                   |                   |
              v                   v                   v
       Refine documents    Refine documents      Discard docs
              |            + web search                |
              |                   |                    v
              |                   |             Query rewrite
              |                   |                    |
              |                   |                    v
              |                   |             External search
              +-------------------+--------------------+
                                  |
                                  v
                         +----------------+
                         | Corrected      |
                         | knowledge      |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         |      LLM       |
                         +--------+-------+
                                  |
                                  v
                               Answer
```

### 4.2 三种检索结果状态

#### CORRECT

当前检索结果具有较高置信度，但 CRAG 仍然不会盲目把完整文档直接塞给模型：

```text
Retrieved document
        |
        v
Decompose into knowledge strips
        |
        v
Filter irrelevant strips
        |
        v
Recompose useful knowledge
        |
        v
LLM generation
```

#### INCORRECT

检索结果置信度很低时：

```text
Bad retrieved docs
        |
        v
Discard
        |
        v
Rewrite query
        |
        v
Web search / external retrieval
        |
        v
LLM generation
```

这里的关键是：**错误的 context 不应该继续被当作事实使用。**

#### AMBIGUOUS

结果不够好，但也不能确定完全错误时：

```text
Internal retrieved knowledge
             +
External web knowledge
             |
             v
      Combined knowledge
             |
             v
            LLM
```

CRAG 的关键特点：

- Retrieval Evaluator 是独立的检索质量判断层。
- 错误检索会触发修正，而不是继续信任原始结果。
- 可以通过 Web Search 扩展静态知识库。
- Decompose -> Filter -> Recompose 减少无关上下文。
- 它是 plug-and-play 的 RAG 增强层，不要求重新设计整个生成模型。

## 5. Self-RAG 和 CRAG 的区别

| 维度 | Self-RAG | CRAG |
| --- | --- | --- |
| 主要问题 | 什么时候检索、答案是否被支持 | Retriever 错了怎么办 |
| 检索控制 | 模型内生决定 | 外部 evaluator 评估结果 |
| 生成批评 | 核心机制 | 不是核心机制 |
| Reflection tokens | 核心机制 | 不使用 |
| 错误恢复 | 继续检索或选择候选生成 | 丢弃、改写 query、Web Search |
| 文档处理 | passage relevance filtering | decompose -> filter -> recompose |
| 结构 | 集成式模型 | 可插拔的 RAG 流程层 |
| 关注点 | Generation control | Retrieval recovery |

可以把两者放在一条演进线上：

```text
Traditional RAG
    |
    | blindly retrieve and generate
    v
Self-RAG
    |
    | decide whether to retrieve and critique generation
    v
CRAG
    |
    | evaluate retrieval quality and recover from bad context
    v
Production Agent Harness
    |
    | retrieve, evaluate, generate, verify, recover
    v
Grounded answer
```

## 6. Grounding Check：验证答案是否被 facts 支持

Grounding 和 Retrieval Evaluation 是两个不同的检查点。

```text
Retrieval Evaluation:
检索出来的 documents 对 query 有用吗？

Grounding Check:
已经生成的 answer 中，每个 claim 是否被提供的 facts 支持？
```

Grounding Check 的典型输入输出可以抽象成：

```text
answerCandidate + facts
          |
          v
   Grounding verifier
          |
          v
   Split into claims
          |
          +-- Claim 1 -> Fact 2 -> supported
          |
          +-- Claim 2 -> Fact 1 -> supported
          |
          +-- Claim 3 -> no fact -> unsupported
          |
          v
 support score + citations
```

它判断的是：

```text
Claim is supported by supplied evidence
```

而不是：

```text
Claim is certainly true in the real world
```

如果 RAG 本身拿到了错误事实，Grounding 可能仍然给出高分，因为答案确实匹配了错误事实。

因此，生产系统至少需要两层检查：

```text
Source correctness / retrieval quality
              |
              v
Answer grounding
```

## 7. Generate -> Verify -> Recover

Grounding API 或 Verifier 通常只负责指出问题，不自动完成业务层面的修复。

完整的 Agent 流程应该由上层 Harness 编排：

```text
                         +----------------+
                         | Retrieve / Tool|
                         | / MCP / DB     |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         | Facts /        |
                         | Evidence       |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         | LLM Generator  |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         | Draft answer   |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         | Grounding /    |
                         | Citation check |
                         +--------+-------+
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
                 PASS                         FAIL
                    |                           |
                    v                           v
               Return answer            Failure classifier
                                                |
                              +-----------------+-----------------+
                              |                 |                 |
                              v                 v                 v
                         Rewrite         Retrieve again        Abstain
```

失败后不能只把 `score = 0.54` 交给模型。Repair LLM 需要看到具体问题：

```json
{
  "original_answer": "广告成本增加导致利润下降 20%。",
  "facts": [
    "广告成本同期增加 30%。",
    "利润同期下降 20%。"
  ],
  "grounding_result": {
    "support_score": 0.54,
    "claims": [
      {
        "claim": "广告成本增加导致利润下降",
        "supported": false,
        "citations": []
      }
    ]
  }
}
```

Repair 指令应该限制模型：

```text
Rewrite the answer so every claim is supported by the provided facts.
Do not introduce new facts.
Remove unsupported claims rather than guessing.
Preserve uncertainty when the evidence is incomplete.
```

可能的修复结果是：

```text
广告成本同期增加 30%，利润同期下降 20%。
现有数据支持这两个指标的变化，但不足以单独证明广告成本是利润下降的直接原因。
```

这里发生的不是“模型重新猜一个原因”，而是：

```text
保留被支持的 observation
删除没有证据的 causality
降低过强的表述
显式表达不确定性
```

## 8. 修答案，还是修证据？

这是生产系统里最容易混淆的地方。

### 8.1 Citation 失效，例如 HTTP 404

```text
Answer + Citation
        |
        v
URL validation
        |
        +-- 200 and relevant -> keep citation
        |
        +-- 404 / inaccessible -> search replacement source
```

404 只说明来源链接无效，不直接证明 claim 是假的。正确动作可能是：

```text
Citation 404
     |
     v
Search another authoritative source
     |
     +-- found -> replace citation, keep claim if supported
     |
     +-- not found -> qualify, remove, or abstain
```

### 8.2 Evidence 不支持 claim

```text
Claim
  |
  v
No supporting fact
  |
  +-- important and retrievable -> retrieve again
  |
  +-- minor wording issue -> rewrite
  |
  +-- no reliable evidence -> remove / abstain
```

因此更完整的名字应该是：

```text
Generate -> Verify -> Recover / Repair
```

而不是把所有失败都叫作 Rewrite。

## 9. Failure Taxonomy

Verifier 的输出应该转换成可路由的 failure type，而不是只返回一个总分。

| Failure type | 典型含义 | 推荐动作 |
| --- | --- | --- |
| `unsupported_fact` | claim 没有证据 | 重新检索、删除或拒答 |
| `partial_support` | 数字或实体正确，但条件不完整 | 限定范围、补充条件 |
| `unsupported_causality` | 相关性被写成因果性 | 降低表述强度或重新检索 |
| `invalid_citation` | URL 404、被拒绝或不是直接来源 | 替换来源 |
| `conflicting_evidence` | 多个来源结论冲突 | 展示冲突并说明不确定性 |
| `retrieval_failure` | 当前 context 与 query 不相关 | 丢弃并改写 query |
| `calculation_error` | 数字计算或单位错误 | 重新调用计算器或确定性代码 |
| `business_rule_violation` | 违反业务规则 | 交给业务 validator |
| `missing_evidence` | 结论可能合理但证据不足 | retrieve again 或 abstain |

路由可以表示为：

```text
Grounding result
      |
      v
Failure classifier
      |
      +-- wording issue ------------> Rewrite
      |
      +-- missing data -------------> Retrieve again
      |
      +-- bad retrieval ------------> Discard + query rewrite
      |
      +-- invalid citation ---------> Source replacement
      |
      +-- calculation error --------> Deterministic recalculation
      |
      +-- no recoverable evidence --> Abstain
```

## 10. 电商 Agent 的推荐架构

事实型电商问题应优先调用工具：

| 问题 | 首选能力 |
| --- | --- |
| 昨天销售额是多少？ | 数据库或销售 API |
| 当前库存多少？ | 库存 API 或数据库 |
| Amazon 广告花费多少？ | 广告平台 API |
| GMV 如何计算？ | SQL、计算器或确定性函数 |
| 为什么利润下降？ | 多个业务工具 + 分析模型 |
| 公司产品规则是什么？ | 权限过滤后的 RAG |
| 什么是 Transformer？ | LLM，必要时再检索 |

一个“为什么利润下降”的安全链路：

```text
User: 为什么本月利润下降？
                 |
                 v
        +--------------------+
        | Agent planner      |
        +---------+----------+
                  |
       +----------+----------+
       |          |          |
       v          v          v
   Sales tool  Ads tool  Returns tool
       |          |          |
       +----------+----------+
                  |
                  v
              Facts
                  |
                  v
          LLM draft analysis
                  |
                  v
          Grounding verifier
                  |
       +----------+-----------+
       |                      |
       v                      v
  Observations supported   Causal claim unsupported
       |                      |
       |                      v
       |                Rewrite / retrieve
       |                      |
       +----------+-----------+
                  |
                  v
          Business validator
                  |
                  v
              Final answer
```

不安全的答案：

```text
广告成本增加导致利润下降 20%。
```

如果系统只有这些事实：

```text
广告成本增加 30%。
利润下降 20%。
```

更安全的答案是：

```text
本月广告成本增加 30%，同时利润下降 20%。
现有数据支持这两个变化，但不足以单独确认广告成本是利润下降的直接原因。
```

## 11. Deterministic Validator 和 Grounding Validator 要分工

Grounding 解决：

```text
答案中的 claim 是否有证据？
```

确定性校验解决：

```text
数字是否算对？
业务规则是否满足？
单位和时间范围是否一致？
```

推荐分层：

```text
                      Draft answer
                           |
              +------------+------------+
              |                         |
              v                         v
       Grounding check          Deterministic validator
       claim <-> evidence       numbers / units / formulas
              |                         |
              +------------+------------+
                           |
                           v
                    Business validator
                           |
                           v
                    Final answer
```

不要让 LLM 负责本来可以由代码可靠完成的工作：

```text
LLM: 选择工具、解释结果、提出建议
Tool: 查询真实数据
Code: 计算指标、执行规则、检查约束
Verifier: 检查回答与证据的对应关系
Harness: 决定重试、修复、审批或拒答
```

## 12. 如何设计 Evidence 对象

不要只把一大段字符串塞进 prompt。建议把证据规范化：

```json
{
  "id": "fact_001",
  "text": "SKU A 在 2026-09-07 的可用库存为 327 件。",
  "source": {
    "type": "inventory_api",
    "name": "warehouse_inventory",
    "record_id": "inventory-2026-09-07-SKU-A"
  },
  "timestamp": "2026-09-07T15:20:00+08:00",
  "scope": {
    "sku": "SKU-A",
    "warehouse": "SG-01"
  }
}
```

规范化 evidence 可以支持：

- claim 到 fact 的明确引用。
- 时间范围、租户、SKU 和仓库等范围检查。
- 冲突证据检测。
- citation 可追溯性。
- 失败后只重新检索缺失的事实。

## 13. 最小可用的 Agent Harness

下面是概念性伪代码：

```python
async def answer_with_guardrails(user_query):
    evidence = await retrieve_or_call_tools(user_query)

    for attempt in range(3):
        draft = await generate_answer(user_query, evidence)
        grounding = await check_grounding(draft, evidence)
        deterministic = validate_numbers_and_rules(draft, evidence)

        if grounding.pass_ and deterministic.pass_:
            return draft

        failure = classify_failure(grounding, deterministic)

        if failure.kind == "retrieval_failure":
            evidence = await corrective_retrieval(user_query, failure)
        elif failure.kind == "missing_evidence":
            evidence = await retrieve_missing_facts(user_query, failure)
        elif failure.kind in {"unsupported_fact", "unsupported_causality"}:
            draft = await repair_answer(draft, evidence, grounding)
            # 下一轮重新检查修复后的答案
        elif failure.kind == "invalid_citation":
            evidence = await replace_invalid_sources(evidence, failure)
        else:
            return abstain_with_explanation(failure)

    return abstain_with_explanation("The answer could not be grounded reliably.")
```

这个 Harness 的关键不是“多调用几次 LLM”，而是每次失败都有明确的恢复动作和终止条件。

## 14. 评估指标

不能只看最终答案是否流畅。至少应分别评估：

| 维度 | 要回答的问题 |
| --- | --- |
| Retrieval relevance | 检索结果是否与 query 相关？ |
| Evidence coverage | 关键 claim 是否有证据？ |
| Faithfulness / grounding | 答案是否超出 evidence？ |
| Citation correctness | citation 是否真的支持对应 claim？ |
| Citation validity | URL 是否可访问、是否是正确来源？ |
| Calculation correctness | 数字、公式和单位是否正确？ |
| Business correctness | 是否符合业务规则和时间范围？ |
| Abstention quality | 证据不足时是否能正确拒答？ |
| Recovery success | 失败后重试是否真的改善？ |
| Cost and latency | 多轮检索和验证是否可接受？ |

一个可观测的请求应该记录：

```text
trace_id
query
retrieval_attempts
evidence_ids
draft_answer
grounding_result
deterministic_validation_result
repair_action
final_answer
abstained
latency
cost
```

## 15. 最终心智模型

把几种机制放在一起：

```text
                         User query
                             |
                             v
                    +------------------+
                    | Retrieval policy |
                    | Should retrieve? |
                    +--------+---------+
                             |
                             v
                    +------------------+
                    | Retriever / Tool |
                    +--------+---------+
                             |
                             v
                    +------------------+
                    | Retrieval        |
                    | evaluator        |
                    +--------+---------+
                             |
                 +-----------+-----------+
                 |           |           |
                 v           v           v
              Correct     Ambiguous    Incorrect
                 |           |           |
                 v           v           v
              Refine    Refine + web   Discard + rewrite
                 |           |           |
                 +-----------+-----------+
                             |
                             v
                        Clean facts
                             |
                             v
                         LLM draft
                             |
                             v
                    Claim-level grounding
                             |
                 +-----------+-----------+
                 |                       |
                 v                       v
                Pass                    Fail
                 |                       |
                 v                       v
              Return             Recover / repair
                                         |
                         +---------------+---------------+
                         |               |               |
                         v               v               v
                      Rewrite       Retrieve again    Abstain
```

可以用一句话总结：

```text
Self-RAG 控制“要不要检索以及生成时如何反思”。
CRAG 控制“检索错了如何修正证据”。
Grounding 控制“答案中的 claim 是否被 facts 支持”。
Agent Harness 控制“检查失败后重试、修答案、修证据还是拒答”。
```

最终目标不是让模型表现得更自信，而是让系统在证据不足时表现得更诚实、更可追溯、更容易恢复。

