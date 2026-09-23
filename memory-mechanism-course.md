# Hermes Agent Memory Mechanism Course

> 面向课程讲解的源码导读：Hermes Agent 如何完成短期记忆、长期记忆，以及记忆的提取、写入、检索、更新、去重、冲突消解、衰减和遗忘。
>
> 代码基线：2026-09-23 工作区。行号以当前工作区文件为准；代码继续变化后应重新运行 `nl -ba` 校准行号。

## 1. 先给结论

Hermes 的记忆不是一个单一模块，而是四层组合：

1. **短期记忆：当前会话上下文**。消息列表、工具结果、当前 turn 的临时召回内容和压缩摘要构成模型眼前的上下文。
2. **内置长期记忆：`MEMORY.md` 与 `USER.md`**。这是人工/模型共同维护的高信号事实卡片，启动会话时读取并冻结到 system prompt。
3. **会话历史长期存档：SQLite + FTS5**。全部会话消息保存在 state DB 中，需要时通过 `session_search` 按关键词检索，而不是把全部历史常驻 prompt。
4. **外部长期记忆 Provider**。Honcho、Mem0、OpenViking、Hindsight、Holographic、RetainDB、ByteRover、Supermemory 等通过 `MemoryProvider` ABC 接入；同一时刻最多激活一个外部 Provider，但它与内置记忆并存。

最重要的设计约束是：**system prompt 中的内置记忆是 session-start snapshot，不能在普通 turn 中原地更新**。新写入会立即落盘，但下一次 session 才进入 system prompt。这样可以保持长会话的 prompt prefix byte-stable，避免每次记忆变化都破坏缓存。

## 2. “短期”和“长期”在 Hermes 中分别是什么

| 层级 | 载体 | 生命周期 | 主要用途 | 是否自动压缩/遗忘 |
|---|---|---|---|---|
| 当前 turn | `messages`、当前 user row 的 `api_content` | 一次 turn | 让模型完成当前任务 | 会随 turn 结束进入历史 |
| 当前 session | SQLite active messages、上下文压缩 summary | `/new`、reset、压缩边界之前 | 保持连续对话 | 压缩会归档旧消息，但仍可搜索 |
| 内置长期事实 | `$HERMES_HOME/memories/MEMORY.md`、`USER.md` | 跨 session | 常驻事实、偏好、环境约定 | 容量拒绝 + 显式 replace/remove；无统一时间衰减 |
| 全量历史 | `$HERMES_HOME/state.db` + FTS5 | 长期 | “上周讨论过什么”式回溯 | 通过 session 边界/归档过滤，内容不自动变成事实记忆 |
| 外部长期记忆 | Provider 自己的云/本地后端 | 由 Provider 决定 | 语义检索、自动抽取、用户建模、知识图谱等 | Provider-specific；不是核心统一策略 |

## 3. 一次完整 turn 的记忆时序

```text
Agent 初始化
  ├─ 读取 MEMORY.md / USER.md，生成冻结 system-prompt snapshot
  └─ 按 memory.provider 激活一个外部 Provider

新 turn 开始
  ├─ on_turn_start()
  ├─ 对非 trivial prompt 做 prefetch(query)
  ├─ 把外部召回包进 <memory-context>，追加到当前 user API 内容
  ├─ 只更新 api_content sidecar，不改历史 message 的 durable content
  └─ 调用 LLM / 工具循环

turn 成功结束
  ├─ 异步 sync_turn(user, assistant)
  ├─ 异步 queue_prefetch(user)，为下一 turn 预热
  └─ 按 nudge interval 启动后台 memory review，决定是否写入内置 memory

session 边界（/new、reset、压缩轮转、进程关闭）
  ├─ on_session_end(messages)：Provider 可做最终提取/flush
  ├─ on_session_switch(...)：切换 Provider 的 session binding
  └─ shutdown()：有界等待后台写入，再关闭 Provider
```

核心代码：

- 初始化：[`agent/agent_init.py:1232-1295`](agent/agent_init.py:1232)
- turn-start 召回：[`agent/turn_context.py:762-790`](agent/turn_context.py:762)
- turn context 编排：[`agent/turn_context.py:857-1017`](agent/turn_context.py:857)
- turn-end 同步：[`run_agent.py:875-906`](run_agent.py:875)
- turn-finalizer 调用顺序：[`agent/turn_finalizer.py:604-624`](agent/turn_finalizer.py:604)
- session-end：[`run_agent.py:854-873`](run_agent.py:854)

## 4. 八类记忆能力如何实现

### 4.1 记忆提取（Extraction）

Hermes 有三种提取入口：

1. **后台 memory review 提取内置事实**。每隔配置的 nudge interval，`run_agent.py` 在回复交付后 fork 一个 review agent；prompt 要求它识别用户的身份、偏好、行为期待和稳定事实，并使用 `memory` tool 保存。见 [`agent/background_review.py:298-309`](agent/background_review.py:298) 和 [`run_agent.py:740-807`](run_agent.py:740)。
2. **外部 Provider 按 turn 提取**。turn 成功后，`MemoryManager.sync_all()` 把 user/assistant 内容异步交给 Provider；例如 Mem0 在 [`plugins/memory/mem0/__init__.py:302-323`](plugins/memory/mem0/__init__.py:302) 调 backend 的 `add(..., infer=True)` 做服务端事实提取。
3. **session-end 提取**。`on_session_end()` 只在真实 session 边界运行。OpenViking 会在 [`plugins/memory/openviking/__init__.py:2341-2344`](plugins/memory/openviking/__init__.py:2341) 提交 session，从而触发 profile、preferences、entities、events、cases、patterns 等抽取；Honcho 在 [`plugins/memory/honcho/__init__.py:869-872`](plugins/memory/honcho/__init__.py:869) flush pending messages。

注意：普通 Hermes 核心不会把每个 user message 自动写进 `MEMORY.md`。内置事实记忆需要模型工具调用或后台 review；全量会话则会进入 SQLite 历史。

### 4.2 记忆写入（Write）

内置写入通过一个 `memory` tool 完成，支持单操作和原子 batch：

```text
add       新增一条 entry
replace   用 old_text 唯一定位后替换整条 entry
remove    用 old_text 唯一定位后删除 entry
batch     多个 add/replace/remove，按最终状态一次性校验并提交
```

入口在 [`tools/memory_tool.py:172-205`](tools/memory_tool.py:172)，底层文件事务在 [`tools/memory_tool_store.py:213-235`](tools/memory_tool_store.py:213)。写入具备：profile-scoped 路径、文件锁、重新读取、原子 temp-file + rename、外部 drift 检测和不可读文件保护。

外部 Provider 的写入由 `MemoryProvider.sync_turn()`、Provider-specific tools 和 `on_memory_write()` 完成；`MemoryManager` 只负责生命周期、路由、异步化和失败隔离，不替 Provider 决定事实模型。

### 4.3 记忆检索（Retrieval）

内置记忆的检索是“启动时常驻”：`MemoryStore.load_from_disk()` 读取后生成 `_system_prompt_snapshot`，system prompt 通过 [`agent/system_prompt.py:487-512`](agent/system_prompt.py:487) 注入。

外部 Provider 的检索是“按 query 召回”：

- turn-start 调用 `MemoryManager.prefetch_all()`：[`agent/memory_manager.py:394-448`](agent/memory_manager.py:394)。
- 当前 query 的召回内容被包装成 `<memory-context>`，由 [`agent/memory_manager.py:266-280`](agent/memory_manager.py:266) 生成。
- 注入发生在当前 user API message，而不是中途改 system prompt：[`agent/turn_context.py:1086-1107`](agent/turn_context.py:1086)。
- 外部 provider 慢或卡住时，默认有 8 秒边界；单 Provider 超时不阻塞主对话：[`agent/memory_manager.py:404-448`](agent/memory_manager.py:404)。

独立的历史检索由 `session_search` 完成。它使用 SQLite FTS5，返回真实消息，不调用 LLM 做二次摘要；搜索入口见 [`hermes_state_search.py:1010-1085`](hermes_state_search.py:1010)，工具层还会按 lineage 去重并排除仍在当前 live context 的会话，见 [`tools/session_search_tool.py:261-310`](tools/session_search_tool.py:261)。

### 4.4 记忆更新（Update）

内置更新不是数据库 row update，而是对整条 entry 的 substring 定位：

- 唯一匹配：[`tools/memory_tool_store.py:58-64`](tools/memory_tool_store.py:58)
- replace/remove：[`tools/memory_tool_store.py:259-297`](tools/memory_tool_store.py:259)
- batch update：[`tools/memory_tool_store.py:322-361`](tools/memory_tool_store.py:322)

如果 `old_text` 匹配多个不同 entry，操作失败并要求更具体；如果没有匹配，也不会静默新增。这保证了“更新事实”不会意外创建第二份事实。

外部 Provider 自己暴露 update API。例如 Mem0 用 `memory_id` 更新或删除，工具 schema 和路由在 [`plugins/memory/mem0/__init__.py:108-117`](plugins/memory/mem0/__init__.py:108) 与 [`plugins/memory/mem0/__init__.py:348-376`](plugins/memory/mem0/__init__.py:348)。

### 4.5 去重（Deduplication）

内置去重分三层：

1. load 时 `dict.fromkeys()` 做保序 exact dedup：[`tools/memory_tool_store.py:128-141`](tools/memory_tool_store.py:128)。
2. add 时 exact content 已存在则返回成功但不追加：[`tools/memory_tool_store.py:245-257`](tools/memory_tool_store.py:245)。
3. batch add 同样跳过 exact duplicate，并且只在最终状态通过后提交：[`tools/memory_tool_store.py:300-360`](tools/memory_tool_store.py:300)。

会话搜索的去重不是内容去重，而是 **lineage 去重**：同一会话 lineage 只保留一个搜索结果，避免压缩轮转或子 session 重复淹没结果，见 [`tools/session_search_tool.py:282-310`](tools/session_search_tool.py:282)。

外部 Provider 可能做语义去重。例如 RetainDB 的 overlay 会归一化文本并排除与本地 entries 重复的结果，见 [`plugins/memory/retaindb/__init__.py:276-293`](plugins/memory/retaindb/__init__.py:276)。这类语义去重不由核心统一规定。

### 4.6 冲突消解（Conflict Resolution）

内置 memory 的冲突消解是**模型驱动的显式 replace**，不是自动事实推理：当用户说“把深色模式改成浅色模式”时，review agent 应搜索旧 entry 并 replace；容量不足时，tool 返回 current entries，要求模型先合并旧事实再重试。

工程层的冲突保护包括：

- 多匹配拒绝，避免错误覆盖：[`tools/memory_tool_store.py:276-286`](tools/memory_tool_store.py:276)
- batch all-or-nothing：[`tools/memory_tool_store.py:322-361`](tools/memory_tool_store.py:322)
- 外部文件 drift 拒绝写入并生成备份：[`tools/memory_tool_store.py:426-440`](tools/memory_tool_store.py:426)
- 后台 review 的 replace/remove 默认 staged，不能无人值守删除：[`tools/memory_tool.py:129-169`](tools/memory_tool.py:129)

Holographic 是一个明确实现了冲突候选发现的外部 Provider：它通过共享实体 + 低内容相似度计算 contradiction score，见 [`plugins/memory/holographic/retrieval.py:120-155`](plugins/memory/holographic/retrieval.py:120)。但该实现负责“发现疑似冲突”，最终如何选择新事实仍由 Provider/模型/用户流程决定。

### 4.7 衰减（Decay）

这里必须区分核心和 Provider：

- **内置 `MEMORY.md` / `USER.md`：没有时间衰减分数，也不会因为时间自动删除。它通过固定字符预算（默认 `MEMORY.md` 2200、`USER.md` 1375）控制长期记忆规模；超过预算时拒绝写入并要求 consolidation，见 [`tools/memory_tool_store.py:237-257`](tools/memory_tool_store.py:237)。
- **会话历史：没有按时间衰减排序的事实记忆模型**，但 active/compacted/rewound 状态影响是否进入 live context 或搜索结果。压缩会 soft-archive 旧行，仍保留可搜索性，见 [`hermes_state_messages.py:567-578`](hermes_state_messages.py:567)。
- **Holographic Provider：明确支持 temporal decay**。`score *= 0.5 ** (age_days / half_life)`，默认 half-life 为 0 表示关闭，见 [`plugins/memory/holographic/retrieval.py:36-44`](plugins/memory/holographic/retrieval.py:36) 与 [`plugins/memory/holographic/retrieval.py:52-75`](plugins/memory/holographic/retrieval.py:52)。

因此，课程中应把“衰减”讲成 Provider-level retrieval policy，而不是 Hermes core 的统一生命周期操作。

### 4.8 遗忘（Forgetting）

内置遗忘是显式的：`memory(action="remove", old_text=...)`，或使用 `/journey delete` 删除 memory chunk。普通 batch 不允许无意中把非空 memory 清成空文件；最后一条需要单独、明确的 remove，见 [`tools/memory_tool_store.py:343-353`](tools/memory_tool_store.py:343)。

外部 Provider 通过自己的 forget/delete 工具实现，例如 Mem0 的 `mem0_delete`、OpenViking 的 `viking_forget`、Supermemory 的 forget handler。核心只通过 `handle_tool_call()` 路由：[`agent/memory_manager.py:590-599`](agent/memory_manager.py:590)。

这意味着“忘记”不是删除 SQLite 全部会话历史的同义词：

- 从内置事实卡片 remove，只删除该事实。
- 从外部 Provider delete，删除 Provider 中的对象。
- `session_search` 仍可能找到原始会话，因为会话历史是独立存档。
- 若用户要求完整删除，需要同时处理 facts、Provider backend、session DB 和备份/导出。

## 5. 内置记忆的写入安全模型

`MemoryStore` 是一个小型、边界清晰的 read-modify-write 存储层：

```text
memory_tool()
  -> 校验 target/action/old_text
  -> write_approval：允许、阻止或 staged
  -> MemoryStore.add/replace/remove/apply_batch
  -> strict threat scan（防 prompt injection / exfiltration）
  -> 独立 .lock 文件加锁
  -> 重新读取并检查 drift
  -> 最终容量校验
  -> atomic_write_text(temp + rename)
  -> 返回 usage、entry_count、done
```

安全扫描入口：[`tools/memory_tool_store.py:26-29`](tools/memory_tool_store.py:26)；approval 入口：[`tools/memory_tool.py:64-78`](tools/memory_tool.py:64)。因为记忆会跨 session 进入 system prompt，写入安全比普通文本文件更严格。

## 6. 外部 Provider 的统一接口

Provider contract 在 [`agent/memory_provider.py:75-192`](agent/memory_provider.py:75)，核心生命周期方法是：

| 方法 | 作用 |
|---|---|
| `initialize` | 建立 backend/session/user identity |
| `system_prompt_block` | 注入静态 Provider 使用说明或基础上下文 |
| `prefetch` | 为当前 query 返回召回上下文 |
| `queue_prefetch` | 在后台预热下一次 query |
| `sync_turn` | 保存完成的 user/assistant turn，可能触发自动提取 |
| `get_tool_schemas` / `handle_tool_call` | 提供 search/add/update/delete/forget 等操作 |
| `on_session_end` | session 边界最终 flush / extraction |
| `on_pre_compress` | 压缩前 checkpoint 或提取摘要 |
| `on_memory_write` | 镜像内置 memory tool 的已提交写入 |
| `shutdown` | 有界 drain 和资源释放 |

`MemoryManager` 在 [`agent/memory_manager.py:283-836`](agent/memory_manager.py:283) 负责 provider fan-out、单外部 Provider 限制、超时、后台 FIFO、schema 去重、tool routing、session boundary 和 graceful shutdown。

## 7. 记忆写入不会破坏 prompt cache 的原因

内置 memory 写入落盘后，`MemoryStore.format_for_system_prompt()` 仍返回 load-time snapshot：[`tools/memory_tool_store.py:363-366`](tools/memory_tool_store.py:363)。当前 turn 如果有外部召回，只写入当前 user message 的 `api_content` sidecar；历史 replay 复用相同 bytes：[`agent/turn_context.py:1043-1052`](agent/turn_context.py:1043)。

这解决了一个核心矛盾：

- 记忆可以在 turn 中更新；
- 当前回答不会因为 system prompt 被重建而破坏 cache；
- 新记忆在下一 session 以新的 snapshot 生效；
- 只有 context compression 是被允许的 cache break。

## 8. 当前实现的边界与课程中的准确表述

不要把 Hermes 说成已经提供一个统一的“自动遗忘大脑”。更准确的表述是：

1. Hermes Core 提供生命周期、存储边界、写入安全、session persistence、FTS search、Provider ABC 和异步编排。
2. 内置长期事实是小而稳定的 curated memory，不做自动时间衰减。
3. SQLite session history 是可检索的 episodic memory，不自动等价于 durable fact。
4. 自动抽取、语义去重、冲突发现、时间衰减、知识图谱和云端 forget 由具体 Provider 提供。
5. “冲突消解”在内置实现中主要通过模型调用 `replace`、容量 consolidation、用户 approval 和精确匹配来完成。

## 9. 相关代码 LOC 索引

以下是课程讲解最值得打开的文件和当前总行数；括号内是重点代码区间，不是整个文件都属于 memory。

| 文件 | 当前 LOC | 重点区间 | 课程角色 |
|---|---:|---|---|
| `agent/memory_provider.py` | 192 | 75-192 | Provider ABC、prefetch/sync/session hooks |
| `agent/memory_manager.py` | 836 | 283-836 | 外部 Provider 编排、超时、异步、路由 |
| `agent/agent_init.py` | 2336 | 1232-1295 | 内置 store 与 Provider 初始化 |
| `agent/turn_context.py` | 1168 | 762-790, 857-1017, 1086-1107 | turn-start recall 与 API 注入 |
| `agent/turn_finalizer.py` | 644 | 604-624 | turn-end sync 与 background review |
| `agent/background_review.py` | 1317 | 298-309, 869-1033 | 自动记忆提取 review fork |
| `agent/system_prompt.py` | 790 | 487-512 | frozen memory snapshot 注入 |
| `tools/memory_tool.py` | 397 | 64-205, 228-259, 262-365 | memory tool、approval、schema |
| `tools/memory_tool_store.py` | 440 | 58-141, 213-440 | 文件存储、锁、容量、去重、原子写 |
| `tools/session_search_tool.py` | 654 | 261-340, 500-654 | 历史会话 discovery/scroll/read |
| `hermes_state_search.py` | 1297 | 1010-1085 | SQLite FTS5 message search |
| `hermes_state_messages.py` | 1313 | 567-625 | compaction archive / active rows |
| `run_agent.py` | 1560 | 854-906 | session end、turn sync、next prefetch |
| `plugins/memory/mem0/__init__.py` | 472 | 244-376 | 语义 search、server extraction、CRUD |
| `plugins/memory/honcho/__init__.py` | 1280 | 554-779, 869-1008 | context/dialectic recall、flush、tools |
| `plugins/memory/openviking/__init__.py` | 2698 | 1541-1565, 2014-2040, 2341-2456 | hierarchical recall、sync、session extraction |
| `plugins/memory/holographic/retrieval.py` | 215 | 36-75, 120-155, 206-215 | trust、矢量检索、冲突候选、时间衰减 |
| `plugins/memory/retaindb/__init__.py` | 626 | 276-293, 356-430 | overlay 去重、后台 prefetch、queue ingest |

精确总行数可在仓库根目录运行：

```bash
wc -l agent/memory_provider.py agent/memory_manager.py agent/background_review.py \
  agent/turn_context.py agent/turn_finalizer.py agent/agent_init.py \
  agent/system_prompt.py tools/memory_tool.py tools/memory_tool_store.py \
  tools/session_search_tool.py plugins/memory/*/*.py
```

## 10. 建议的课程实验

### 实验 A：观察 frozen snapshot

1. 启动 Hermes，确认 `MEMORY.md` 中有一条 entry。
2. 在同一 session 调用 `memory add`。
3. 观察 tool result 的 live usage 已变化，但当前 system prompt 不重建。
4. 执行 `/new`，确认新 entry 出现在新 session 的 memory block。

### 实验 B：观察容量 consolidation

1. 将 `memory_char_limit` 设为很小。
2. 添加超出预算的 entry，观察 `current_entries` 和失败原因。
3. 用一个 batch 同时 `replace` 旧 entry、`add` 新 entry。
4. 检查 batch 是全有或全无，而不是半提交。

### 实验 C：观察外部 Provider 生命周期

1. 配置 Mem0 或其他 Provider。
2. 打开 debug log，观察 `on_turn_start -> prefetch -> sync_turn -> queue_prefetch`。
3. 中断一个 turn，确认 `run_agent.py` 不把 partial assistant output 写入外部 durable memory。
4. 执行 `/new`，观察 `on_session_end` 在 session switch 前运行。

### 实验 D：区分 fact recall 与 episodic recall

1. 把稳定偏好写入 `USER.md`。
2. 把一次性讨论留在 session history，不写入 memory。
3. 新 session 中用偏好问题观察常驻 memory。
4. 再用 `session_search` 找回一次性讨论。

## 11. 一句话教学总结

**Hermes 用“当前上下文 + 压缩摘要”解决短期记忆，用“受限事实卡片 + 全量会话搜索 + 可插拔 Provider”解决长期记忆；核心负责边界与可靠性，Provider 负责语义抽取、检索、冲突和衰减策略。**
