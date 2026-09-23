# Session Compaction 教学：长会话如何压缩，以及搜索结果如何保留

这篇教程以 `deepseek-harness` 为例，解释一个长会话超过 Context Window 后如何继续工作：

- 压缩是否会重新调用搜索工具；
- 搜索结果、URL、snippet 和 fetch 内容到底保存在哪里；
- Compaction 如何选择历史范围；
- 它如何避免破坏工具调用与工具结果的配对；
- 为什么原始历史仍然可以回放，但模型不一定继续看到每个旧细节；
- 如何从源码定位整个实现。

## 1. 先记住一个核心区分

Session 中同时存在两种历史：

```text
完整 Session Log                 当前 Model Surface
----------------                 ------------------
所有原始事件                       模型下一次请求看到的消息
搜索结果原文                       压缩后的 checkpoint
旧工具输出                         最近保留的消息
compaction 事件                    当前系统提示词和工具 schema
失败尝试和元数据
```

Compaction 只改变右侧的 Model Surface，不删除左侧的 append-only log。

因此：

- 对模型来说，旧内容可能被一个摘要替代；
- 对持久化、回放、审计和 UI 来说，原始事件仍然存在；
- `deriveMessages()` 从当前 surface 推导模型历史，而不是直接把完整 log 全部发送给模型。

## 2. Compaction 会不会使用搜索历史工具？

不会。

默认 Compaction 的流程是：

1. 从 Session 的当前 surface 读取需要压缩的消息；
2. 把这些消息交给一次专用 summarization LLM call；
3. 让 summarizer 生成结构化 checkpoint；
4. 用 checkpoint 替换旧的 surface range。

它不会：

- 自动调用 `web_search`；
- 自动调用 `web_fetch`；
- 根据旧 URL 重新抓取网页；
- 在摘要失败时自动重新搜索原始资料。

如果未来模型需要旧搜索资料，但资料已经不在保留的最近 tail 中，它只能依靠 checkpoint，或者重新调用当前仍然可用的 `web_search` / `web_fetch` 工具。

## 3. 总体流程图

```text
User / assistant / tool events
              |
              v
      Append-only Session log
              |
              +-----------------------------+
              |                             |
              v                             v
       Current surface                Complete durable log
       (model-visible)                 (replay / audit / UI)
              |
              v
       Token meter measures
       the routed request
              |
              v
   Pressure or context overflow?
              |
              v
   Optional tool-result pruning
   - retain head
   - insert middle marker
   - retain tail
              |
              v
   Re-measure the surface
              |
       +------+------+
       |             |
       v             v
  Below threshold  Still too large
  stop here        select old balanced range
                        |
                        v
              Build summarizer input:
              system + tools + old messages
                        |
                        v
              Direct ctx.llm.stream()
              with compaction instruction
                        |
                        v
              Structured summary checkpoint
                        |
                        v
              Append durable records:
              compaction/start
              compaction/summary
              user/message replacement
              compaction/end
                        |
                        v
              deriveMessages()
              returns checkpoint + recent tail
```

## 4. When does automatic compaction start?

With `dsh-compaction-basic` enabled, automatic compaction is checked at the `agent/pre-step` waterfall, before the next model request is derived.

The normal pressure path is approximately:

```text
latest durable route
        |
        v
ctx.tokenMeter.measure(session)
        |
        v
compare against routed model threshold
        |
        v
optional tool-result pruning
        |
        v
measure again
        |
        v
compact oldest safe range while retaining recent tail
```

The documented defaults are:

| Setting | Default | Meaning |
|---|---:|---|
| `thresholdRatio` | `0.8` | Start compaction at 80% of the routed context window |
| `retainRatio` | `0.16` | Keep the newest 16% verbatim |
| `maxTokens` | `8192` | Summarizer output cap |
| `compactionRetries` | `1` | Extra attempts if pressure remains |
| `maxOverflowRetries` | `1` | Retry budget after confirmed context overflow |

The confirmed overflow path is more aggressive. After a provider reports `CONTEXT_WINDOW_EXCEEDED`, the backend attempts a useful balanced reduction and retries only if the session surface actually advanced.

## 5. How is the old range selected?

The selector walks backward from the newest surface node and accumulates tokens until the configured recent-tail budget is reached. The range before that tail becomes the candidate for summarization.

It has important constraints:

```text
surface node 0: system/message
        |
        +--> never selected as the old range head

old messages ---------------- recent tail
       compact this part       keep verbatim
```

The selector also moves the boundary until tool-call/result pairing is balanced:

```text
assistant tool call A
tool result A
assistant tool call B
tool result B
assistant answer
```

It must not produce a history like this:

```text
assistant tool call A
<compaction summary>
tool result A
```

That would leave the next model request with an invalid or ambiguous tool history.

## 6. What does the summarizer receive?

The default summarizer constructs a direct `ctx.llm.stream()` request containing:

1. The current system head, when present;
2. The latest request header's tool schemas;
3. The selected old messages in surface order;
4. A final compaction instruction.

```text
[current system prompt]
[tool schemas]
[old user / assistant / tool messages]
[compaction instruction]
```

The system prompt and tool schemas are replayed before the selected history so the auxiliary call resembles a prefix of the normal conversation request. This can preserve provider prefix-cache reuse.

The compaction instruction asks for these sections:

```text
## Primary Request and Intent
## Key Technical Concepts
## Files and Code
## Errors and Fixes
## Pending Jobs
## Current Work
## Next Step
## Critical Context
```

It also instructs the summarizer to preserve exact file paths, commands, identifiers, numeric values, syntax fragments, user feedback, and corrections.

The output is wrapped in:

```text
<compacted-summary>
...
</compacted-summary>
```

Only text blocks are accepted. Image output from the summarizer fails with `UNSUPPORTED_CONTENT` rather than silently disappearing.

## 7. What happens to a web search result?

A `web_search` call produces two useful representations:

```text
web_search
   |
   +--> model-facing tool/result content
   |    answer, URLs, snippets, published dates
   |
   +--> tool/result.meta
        structured sources for replayable UI cards
```

The model-facing result is persisted as a `tool/result` event and projects into a user message containing a `tool-result` block:

```text
tool/result  --->  user message with tool-result block
```

The private `meta` field can preserve structured source data such as:

```json
{
  "sources": [
    {
      "url": "https://example.com/article",
      "title": "Example article",
      "snippet": "Relevant excerpt",
      "publishedAt": "2026-09-23"
    }
  ],
  "truncated": false
}
```

The core session treats `meta` as opaque JSON. The web tool owns its schema and uses it to rebuild the search card during replay.

### During compaction

If the search result is inside the selected range:

```text
old search tool/result
          |
          v
summarizer sees the projected result text
          |
          v
summary should preserve important URLs and facts
          |
          v
old surface node is shadowed by checkpoint
```

The complete original `tool/result` event remains in the append-only log, but the next model request normally sees the summary rather than the original result.

This is the important limitation:

```text
durable preservation != model-context preservation
```

Durable history is lossless. The generated summary is not a formal guarantee that every URL, snippet, quote, or search detail will survive into future model context.

## 8. Oversized tool-result pruning

The optional `dsh-compaction-tool-result-pruner` runs after a compaction trigger qualifies and before the summary range is selected.

Its default policy is:

```text
if text length > 8192 code points:

first 4096 code points
    + "[... tool result middle pruned ...]"
    + last 1024 code points
```

The original tool result is not overwritten. Instead, the pruner appends:

```text
compaction/prune       log-only shadow price
tool/result replacement surface replacement
```

The replacement preserves the tool call, step, error state, metadata, and non-text block order. Only the oversized text content is shortened.

This can avoid a model summarization call entirely:

```text
pressure qualifies
        |
        v
prune huge tool output
        |
        v
measure again
        |
        +--> below threshold: stop, no summary call
        |
        +--> still above threshold: summarize pruned surface
```

For search results, this means a very large `web_fetch` or tool output may retain only its beginning, a marker, and its ending before the summarizer sees it.

## 9. The durable compaction transaction

Compaction is recorded as a bracketed transaction:

```text
compaction/start
        |
        v
prepare selected range
        |
        v
summarize asynchronously
        |
        v
re-check surface stability
        |
        v
compaction/summary
        |
        v
user/message with surfaceOp = replace
        |
        v
compaction/end
```

The replacement message cites the source events that it shadows. Conceptually:

```ts
session.append('user/message', checkpointMessage, {
  surfaceOp: { op: 'replace', startSeq, endSeq },
  sourceEventSeqs: [startSeq, summarySeq, ...shadowedSeqs],
})
```

If the process fails after `compaction/start` but before `compaction/end`, the unmatched start remains detectable as a durable lock instead of pretending that compaction completed.

If the surface changes while summarization is running, the summary is rejected rather than applied to stale history.

## 10. Why does `deriveMessages()` matter?

`Session.deriveMessages()` is the bridge between the raw event log and the model request.

```text
Session events
    |
    v
surface operations
    |
    v
deriveEventMessage()
    |
    v
deriveMessages()
    |
    v
LLM request history
```

The surface contains message-producing events such as:

- `system/message`;
- `user/message`;
- `assistant/message`;
- `tool/result`.

Structural events such as `turn/start`, `step/end`, `assistant/attempt`, and `compaction/*` do not become ordinary model messages.

When a replacement operation shadows old surface nodes, `deriveMessages()` omits those nodes and returns the replacement checkpoint in their position.

## 11. What prevents important information from being forgotten?

The design uses several partial safeguards, not one perfect guarantee:

### Recent-tail retention

The newest messages remain verbatim, so active work, recent tool results, and the immediate next step are less likely to be summarized.

### Structured checkpoint sections

The summarizer is explicitly asked to retain intent, files, errors, pending jobs, current work, next step, and critical context.

### Exact-value preservation instructions

The prompt asks for exact paths, commands, identifiers, numbers, syntax fragments, and user corrections.

### Balanced tool boundaries

Compaction does not split tool call/result pairs.

### Shrink validation

The summary must be smaller than the replaced content. Otherwise the compaction attempt fails.

### Durable original log

The original events remain available for replay, inspection, export, and future recovery tooling.

### Search can be repeated

The current `web_search` and `web_fetch` tools remain available after compaction, so the agent can retrieve details again when the checkpoint indicates that more evidence is needed.

## 12. The real limitation

Compaction is not a database index and not a search engine over every old detail.

It is a controlled projection:

```text
raw durable history
        |
        +--> exact replay and inspection
        |
        +--> model-visible projection
                  |
                  +--> recent messages verbatim
                  +--> old messages summarized
                  +--> huge tool output pruned when needed
```

If a product requires guaranteed future access to every search source, it should add a separate durable evidence layer, for example:

- store search results as structured evidence records;
- preserve URL, title, snippet, fetch timestamp, and source hash;
- provide a model-facing `search_session_history` or evidence lookup tool;
- require citations to resolve against that evidence store;
- keep compaction summaries as pointers to evidence IDs instead of relying only on generated prose.

That would be a separate capability from the current compaction implementation.

## 13. Source-code map

The following paths refer to the repository:
`/Users/dengwei/work/ai/github/deepseek-harness`.

| Responsibility | File and key location |
|---|---|
| Automatic pressure trigger | `packages/compaction/compaction-basic/src/index.ts:144` |
| Context-overflow recovery and retry | `packages/compaction/compaction-basic/src/index.ts:176` |
| Pressure measurement and retention policy | `packages/compaction/compaction-basic/src/index.ts:255` |
| Select old range and retain recent tail | `packages/compaction/compaction-basic/src/region.ts:118` |
| Tool-call/result boundary checks | `packages/compaction/compaction-basic/src/region.ts:144` |
| Compaction transaction and lock | `packages/compaction/compaction-basic/src/region.ts:174` |
| Build summarizer input | `packages/compaction/compaction-basic/src/region.ts:545` |
| Append summary and surface replacement | `packages/compaction/compaction-basic/src/region.ts:471` |
| Default summarizer LLM call | `packages/compaction/compaction-basic/src/summarizer.ts:119` |
| Structured compaction prompt | `packages/compaction/compaction-basic/src/summarizer.ts:31` |
| Summary checkpoint framing | `packages/compaction/compaction-basic/src/summarizer.ts:186` |
| Model-visible history projection | `packages/core/session/src/index.ts:823` |
| Session event append and surface validation | `packages/core/session/src/index.ts:682` |
| `tool/result` projection rules | `docs/subsystems/session.md:648` |
| Compaction event vocabulary | `packages/compaction/compaction/src/types.ts:17` |
| Web search output formatting | `packages/web/tool-web/src/search.ts:73` |
| Structured search metadata | `packages/web/tool-web/src/search.ts:108` |
| Web search tool registration | `packages/web/tool-web/src/search.ts:296` |
| Tool-result pruning | `packages/compaction/compaction-tool-result-pruner/src/index.ts:124` |
| Pruning defaults | `packages/compaction/compaction-tool-result-pruner/src/config.ts:6` |
| Compaction subsystem overview | `docs/subsystems/compaction.md:75` |

## 14. Recommended reading order in the codebase

```text
1. docs/subsystems/compaction.md
2. packages/compaction/compaction-basic/README.md
3. packages/compaction/compaction-basic/src/index.ts
4. packages/compaction/compaction-basic/src/region.ts
5. packages/compaction/compaction-basic/src/summarizer.ts
6. packages/core/session/src/index.ts
7. packages/web/tool-web/src/search.ts
8. packages/compaction/compaction-tool-result-pruner/src/index.ts
```

Read `region.ts` and `session/src/index.ts` together. The compaction backend decides which nodes to replace; the Session surface decides what the next model request actually receives.

## 15. One-sentence summary

`deepseek-harness` preserves complete search and tool events in the append-only Session log, but it does not re-search old history during compaction; it uses a structured LLM summary plus a verbatim recent tail to build a smaller model-visible surface, with optional deterministic pruning for oversized tool results.
