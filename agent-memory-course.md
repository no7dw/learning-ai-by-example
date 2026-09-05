# Agent Memory 教学：从会话记录到可控的长期记忆

这篇教程回答一个实际问题：一个 Agent 如何在新的会话里记住有用的事情，同时不把临时聊天、错误推断和过期信息带回来。

本文会从零建立一个简单的 Memory 方案，再比较 Mem0、Letta、Zep/Graphiti 和 LangGraph Store 等外置框架，最后用 Hermes Agent、OpenClaw、Codex 和 Claude Code 解释不同类型 Agent 的记忆设计。

## 1. 先区分四种“记住”

Memory 不是把全部聊天记录复制到下一个 Prompt。一个 Agent 通常同时拥有以下四层状态：

| 层次 | 例子 | 默认处理方式 | 主要问题 |
|---|---|---|---|
| 当前上下文 | 本轮问题、最近几条消息、工具结果 | 直接放入 Prompt | Context Window 不够大 |
| 会话状态 | 当前任务、待确认事项、工具调用结果 | 按 `thread_id` 保存和恢复 | 会话结束后是否仍然有用 |
| 长期 Memory | 用户偏好、项目决策、稳定事实 | 提取、审核、检索、更新 | 错误、过期和冲突 |
| 知识 RAG | 产品文档、代码、政策、网页 | 建索引并按问题检索 | 来源、权限和新鲜度 |

可以用一句话判断边界：

- “这次对话正在做什么”是会话状态。
- “以后与这个人或 Agent 相关的稳定事实”是长期 Memory。
- “外部资料里写了什么”是知识 RAG。
- “没有权限就不能做”是权限和策略，不应该只依赖 Memory。

典型链路如下：

```text
用户消息
    -> 读取当前会话状态
    -> 检索相关长期 Memory
    -> 检索有权限的知识和工具
    -> 组装 Prompt
    -> Agent 执行并回答
    -> 从本轮结果提取候选 Memory
    -> 校验、去重、解决冲突、保存
```

向量数据库只是其中一种存储或检索组件。它可以回答“这条记忆和当前问题像不像”，但不能单独回答“这条记忆是否真实、是否已经过期、是否拥有更高权限”。

## 2. 什么内容应该塞进 Memory

好的 Memory 是未来会改变 Agent 行为、并且值得跨会话保留的短事实。可以按下面几类组织：

| 类型 | 适合保存的内容 | 示例 |
|---|---|---|
| 用户资料 | 身份、长期偏好、沟通方式 | 用户希望默认使用中文，回答先给结论 |
| 项目事实 | 稳定的项目背景和当前约束 | 这个服务使用 PostgreSQL，不允许直接改生产库 |
| 决策 | 已经确认的技术或产品选择 | 本项目选择 Qdrant，原因是已有运维能力 |
| 反馈 | 用户明确纠正过的行为 | 用户说不要把未验证的推断写进 Memory |
| 过程记忆 | 下次继续工作需要的短摘要 | 迁移脚本已生成，尚未在 staging 执行 |
| 程序性知识 | 可重复的工作方法 | 发布前先运行迁移检查，再创建 PR |

下面这些内容通常不应该直接进入长期 Memory：

- 原始聊天全文、完整日志、大段代码和临时输出；
- 一次性的情绪、猜测和未经确认的用户画像；
- API Key、Cookie、密码、支付信息和其他秘密；
- 没有来源的外部说法，尤其是会影响资金、权限或安全的说法；
- 已经失效的任务状态，例如“正在修复”但任务其实已经完成；
- 把“用户问过某件事”误写成“用户认同某个结论”。

### 写入前的准入检查

可以把每一条候选 Memory 过下面五个问题：

1. 未来的另一个会话会用到它吗？
2. 它会改变回答、检索或行动吗？
3. 它是事实、偏好、决策，还是仅仅是当前对话内容？
4. 它有来源、时间和适用范围吗？
5. 它是否需要过期、用户确认或更高权限才能使用？

如果只能回答“这是刚刚聊过的”，就先留在会话状态或每日笔记中，不要提升为长期 Memory。

### 把自然语言变成结构化事实

不要只保存一段没有身份的字符串。至少保留这些字段：

```json
{
  "id": "memory-01",
  "scope": "user:alice",
  "kind": "preference",
  "key": "answer_language",
  "content": "用户偏好中文回答",
  "status": "active",
  "confidence": 0.98,
  "source": "explicit_user_statement",
  "observed_at": "2026-09-05T08:00:00Z",
  "expires_at": null,
  "supersedes_id": null
}
```

`key` 很重要。没有 `answer_language` 这个规范化键，系统很容易把“用中文回答”与“以后请用英文回答”当成两条都有效的相似文本。

## 3. Memory 的读写架构

一个可控的外置 Memory 一般包含六个部分：

```text
                 +------------------+
                 | 当前会话和工具结果 |
                 +---------+--------+
                           |
                    候选记忆提取
                           |
                 +---------v--------+
                 | 准入策略和权限检查 |
                 +---------+--------+
                           |
                 +---------v--------+
                 | 规范化/去重/冲突处理 |
                 +---------+--------+
                           |
                 +---------v--------+
                 | 外置 Store         |
                 | SQL/文件/向量/图    |
                 +---------+--------+
                           |
                       相关记忆检索
                           |
                 +---------v--------+
                 | Prompt 组装与引用   |
                 +------------------+
```

写路径和读路径应该分开考虑：

- 写路径追求准确、可审计和可更正。宁可少写，也不要把每一句话都变成事实。
- 读路径追求相关、低延迟和范围正确。只读取当前用户、当前项目和当前 Agent 有权使用的 Memory。
- 策略层决定“能不能使用”，Memory 层只提供“可能相关的内容”。

### 一个实用的 Memory 记录模型

```text
MemoryRecord
  id                 稳定记录 ID
  scope              user:alice / project:demo / agent:assistant
  key                规范化事实键，可用于冲突检测
  kind               profile / preference / decision / task / procedure
  content            给 Agent 看的短内容
  status             active / superseded / expired / deleted
  source             用户、工具、文档、模型推断或人工审核
  confidence         置信度，不等于真实性证明
  observed_at        事实被观察或确认的时间
  expires_at         时间过期点，可为空
  supersedes_id      它替代的旧记录
  source_ref         对话、工单、文档或工具结果的引用
```

`confidence` 只能帮助排序或触发审核，不能取代来源。高风险场景应要求明确来源或用户确认。

## 4. 典型外置 Memory 框架

“外置 Memory 框架”通常替你实现了部分提取、存储、检索或更新流程，但它不会替你决定业务事实。选型时先问：你缺的是数据结构、召回能力、Agent 状态管理，还是用户建模？

| 框架 | 核心模型 | 适合的场景 | 需要自己补上的部分 |
|---|---|---|---|
| [Mem0](https://github.com/mem0ai/mem0) | 从对话提取用户、会话和 Agent 级 Memory，再通过 API 写入和检索；具体版本可能采用 add-only 累积或提供更新/删除流程 | 个性化聊天、客服、需要快速接入的助手 | 事实准入、权限、TTL、审计和高风险冲突确认 |
| [Letta](https://docs.letta.com/) | Agent 管理可持续状态；常见分层是始终可见的 Core Memory 与按需召回的 Archival Memory | 长时间运行、需要自己维护状态的 Agent | 防止 Agent 自己写入错误事实，限制 Memory 块和工具权限 |
| [Zep / Graphiti](https://github.com/getzep/graphiti) | 以实体、关系、事件和时间有效性组织 Temporal Knowledge Graph | 用户关系、事件演化、多跳事实和“当时/现在”的问题 | 图模型、实体合并、权限、删除和成本控制 |
| [LangGraph Store](https://docs.langchain.com/oss/python/langgraph/persistence) | Checkpointer 保存线程状态，Store 保存跨线程的长期键值数据 | 已经使用 LangGraph 的工作流和多 Agent 系统 | Memory 提取、字段规范、冲突处理和过期任务 |
| [Honcho](https://github.com/plastic-labs/honcho) | 用户模型和跨会话的个性化推断 | 想把“用户是谁、怎样协作”作为独立服务的助手 | 推断的可解释性、纠错入口、敏感画像限制 |

这些框架有不同的抽象，不能只用“是否支持向量搜索”比较：

- Mem0 偏向 Memory 层的自动提取和召回。
- Letta 偏向 Agent 如何持有、修改和使用自己的状态。
- Zep/Graphiti 偏向带时间的实体关系和事件记忆。
- LangGraph Store 偏向把持久化能力接入图工作流，明确区分线程状态与跨线程数据。
- Honcho 偏向用户建模，不应该被当作权限或事实来源。

如果只是一个单用户、单 Agent、低数据量的个人工具，Markdown 或 SQLite 往往比一开始引入完整框架更容易调试。只有当记忆数量、召回质量、跨 Agent 共享或时间关系成为主要问题时，才引入向量索引、图存储或托管服务。

## 5. 过期怎么处理

过期不是“把旧文本从向量库删掉”这么简单。至少有四种过期条件：

| 过期类型 | 示例 | 推荐做法 |
|---|---|---|
| 时间过期 | “本周临时使用 staging” | `expires_at`，读取时过滤，后台清理 |
| 事件过期 | “迁移尚未完成”在发布后失效 | 发布事件触发 supersede 或更新 |
| 状态过期 | 任务从 `pending` 变成 `done` | 同一 `key` 写入新版本，旧记录标记 `superseded` |
| 权限过期 | 用户离开团队或项目权限被撤销 | 权限系统立即阻断，Memory 异步删除或隔离 |

推荐的生命周期是：

```text
候选 -> active -> superseded
                 |
                 +-> expired
                 |
                 +-> deleted
```

实践建议：

1. 每条 Memory 都保存 `created_at`、`observed_at`、`expires_at` 和 `last_accessed_at`。
2. 读取时始终过滤 `status != active` 和已经过期的记录，不能只依赖定时清理。
3. 定时任务负责物理删除、归档、压缩和索引清理；它不是正确性的唯一保障。
4. 需要保留审计的场景先标记 `deleted` 或写入 tombstone，再按保留策略物理删除。
5. 把 TTL 按类型设置，而不是全库统一设置：用户偏好可能长期有效，临时部署约束可能只有几小时。
6. 用户要求“忘记这件事”时，按 `scope`、`key`、来源和派生索引一起删除，并记录删除结果。

向量存储尤其要注意：删除数据库中的原始记录后，还可能残留向量索引、缓存、摘要或导出的 Prompt。删除策略必须覆盖所有副本。

### 默认 TTL 可以这样设计

```text
当前会话状态       会话结束或 24 小时
临时操作约束       事件完成后，最长 7 天兜底
项目进行中摘要     30 天后复核，不一定自动删除
稳定用户偏好       无固定 TTL，但发生新确认时 supersede
敏感信息           默认不保存，必要时使用最短 TTL
```

这是起点，不是通用答案。应结合数据敏感度、复用价值和用户期望调整。

## 6. 冲突怎么处理

冲突的根源通常不是“两个向量距离太近”，而是同一个事实在不同时间、范围或来源下发生了变化。

例如：

```text
2026-08-01  用户偏好中文回答       key=answer_language
2026-09-01  用户说以后使用英文      key=answer_language
```

正确做法不是把两条都放进 Prompt，也不是简单地选择向量分数更高的一条，而是：

1. 用 `key` 找到同一事实族。
2. 检查 scope：用户偏好、项目约束和本轮临时要求不是同一个范围。
3. 检查来源：明确用户指令通常高于模型推断；权威系统数据高于聊天猜测。
4. 检查时间：较新的确认通常替代旧事实，但不能覆盖一个仍然有效的历史事实。
5. 新事实写入后，把旧事实标记为 `superseded`，保留 `supersedes_id` 和来源。
6. 如果冲突会影响资金、删除、权限或安全操作，暂停行动并向用户确认。

### 推荐的权威顺序

```text
硬策略/权限系统
    > 用户当前明确指令
    > 领域权威系统或已确认工具结果
    > 项目中的明确决策记录
    > 近期有来源的观察
    > 模型推断和相似度排序
```

这不是让 Memory 绕过权限的理由。即使一条 Memory 说“用户允许发送邮件”，发送邮件前仍然应该检查当前工具权限和产品确认规则。

### 冲突记录示例

```json
{
  "key": "deployment_environment",
  "old": {
    "content": "可以直接部署到 production",
    "source": "conversation",
    "status": "superseded"
  },
  "new": {
    "content": "部署必须先经过 staging 验证",
    "source": "project_policy.md",
    "status": "active"
  },
  "resolution": "权威项目策略覆盖聊天中的临时说法"
}
```

## 7. 一个简单的 Memory 方案如何实现

先实现一个不依赖 LLM、Embedding 或外部服务的版本，目的是看清生命周期和冲突处理。下面的实现使用 Python 标准库 SQLite，支持：

- 按 `scope + key` 保存事实；
- 同一个键的新事实自动 supersede 旧事实；
- TTL 过期；
- 只检索 active 且未过期的内容；
- 基于词重叠的简单搜索。

```python
from __future__ import annotations

import re
import sqlite3
import time
import uuid


def now() -> int:
    return int(time.time())


def terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


class MemoryStore:
    def __init__(self, path: str = "memory.db") -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER,
                supersedes_id TEXT
            );
            CREATE INDEX IF NOT EXISTS memories_scope_status
                ON memories(scope, status, expires_at);
            CREATE INDEX IF NOT EXISTS memories_scope_key
                ON memories(scope, key, status);
            """
        )

    def remember(
        self,
        *,
        scope: str,
        kind: str,
        key: str,
        content: str,
        source: str,
        confidence: float = 0.8,
        ttl_days: int | None = None,
    ) -> str:
        created_at = now()
        expires_at = (
            created_at + ttl_days * 86400 if ttl_days is not None else None
        )

        with self.db:
            active = self.db.execute(
                """
                SELECT id, content FROM memories
                WHERE scope = ? AND key = ? AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC
                """,
                (scope, key, created_at),
            ).fetchall()

            for old in active:
                if old["content"] == content:
                    return str(old["id"])
                self.db.execute(
                    "UPDATE memories SET status = 'superseded' WHERE id = ?",
                    (old["id"],),
                )

            memory_id = uuid.uuid4().hex
            self.db.execute(
                """
                INSERT INTO memories (
                    id, scope, kind, key, content, status, confidence,
                    source, created_at, expires_at, supersedes_id
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    scope,
                    kind,
                    key,
                    content,
                    confidence,
                    source,
                    created_at,
                    expires_at,
                    active[0]["id"] if active else None,
                ),
            )
            return memory_id

    def expire(self) -> int:
        with self.db:
            result = self.db.execute(
                """
                UPDATE memories SET status = 'expired'
                WHERE status = 'active' AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (now(),),
            )
        return result.rowcount

    def search(self, *, scope: str, query: str, limit: int = 5) -> list[dict]:
        self.expire()
        query_terms = terms(query)
        if not query_terms:
            return []

        rows = self.db.execute(
            """
            SELECT * FROM memories
            WHERE scope = ? AND status = 'active'
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (scope, now()),
        ).fetchall()

        def score(row: sqlite3.Row) -> float:
            overlap = len(query_terms & terms(row["content"]))
            age_days = max(0, (now() - row["created_at"]) / 86400)
            recency = 1 / (1 + age_days / 30)
            return overlap + 0.25 * recency + 0.1 * row["confidence"]

        ranked = [(score(row), row) for row in rows]
        ranked = [(value, row) for value, row in ranked if value > 0.1]
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [dict(row) for _, row in ranked[:limit]]

    def forget(self, *, scope: str, key: str) -> int:
        with self.db:
            result = self.db.execute(
                """
                UPDATE memories SET status = 'deleted'
                WHERE scope = ? AND key = ? AND status = 'active'
                """,
                (scope, key),
            )
        return result.rowcount


store = MemoryStore(":memory:")
store.remember(
    scope="user:alice",
    kind="preference",
    key="answer_language",
    content="Alice prefers Chinese answers",
    source="explicit_user_statement",
)
store.remember(
    scope="user:alice",
    kind="task",
    key="current_project",
    content="Alice is migrating the billing service",
    source="conversation_summary",
    ttl_days=30,
)

for item in store.search(scope="user:alice", query="preferred answer language"):
    print(item["key"], item["content"])
```

### 这个简单实现还缺什么

它故意没有假装解决所有问题：

- `terms()` 只适合英文和带空格的标识符，中文应增加分词或 Embedding 检索；
- 它假设调用方已经提取出结构化事实，生产系统需要让 LLM 输出受约束的 JSON，再由代码校验；
- 它没有用户确认 UI、权限校验、加密、审计导出和跨服务同步；
- 它只保留同一个 `scope + key` 的一个 active 值，真正的事件型 Memory 应允许多个时间有效的事实共存；
- 它没有全文索引和向量索引，数据量变大后应使用 SQLite FTS、专用检索服务或向量/图数据库。

### 加上 LLM 提取时的正确边界

让模型做“候选提取”，让程序做“最终写入”：

```text
对话
  -> LLM: 提议 {kind, key, content, ttl, evidence}
  -> 程序: 校验字段、过滤秘密、检查权限和来源
  -> 程序: 按 scope + key 查冲突
  -> 用户或策略: 高风险事实确认
  -> Store: 写入 active 或 pending
```

不要让 LLM 直接获得一个无约束的 `save_memory(text)` 工具，并据此把所有模型判断升级为系统事实。

## 8. Hermes Agent 和 OpenClaw：个人助手如何处理

个人助手的特点是跨很多渠道、跨很多天持续工作。它们通常需要“少量始终可见的个人资料”加上“可按需检索的历史记录”，否则每次都把所有聊天放入 Prompt，成本和隐私风险都会快速上升。

### Hermes Agent

Hermes 的内置方案是两个有界的 Markdown 文件：

- `MEMORY.md`：Agent 自己的环境事实、约定和经验；
- `USER.md`：用户偏好、沟通方式和期望。

两者位于 `~/.hermes/memories/`，在会话开始时作为固定快照注入；当前文档给出的默认字符上限分别是 2,200 和 1,375。内置 Memory 工具可以 `add`、`replace` 和删除条目。超过容量时不会静默压缩，而是返回错误，要求先合并或删减。

Hermes 还把会话搜索与长期 Memory 分开：历史会话存入 SQLite FTS5，通过 `session_search` 按需查找，适合回答“上周我们讨论过什么”，不适合把所有历史自动放进每个 Prompt。Hermes 的外部 Provider 还包括 Mem0、Honcho、Hindsight 等，可与内置文件并行使用。

Hermes 的分层可以这样理解：

```text
USER.md       -> 每次会话都需要的用户画像和偏好
MEMORY.md     -> 每次会话都需要的 Agent 经验和环境事实
session_search-> 不常用但可能需要追溯的历史细节
外部 Provider  -> 更大的语义、图关系或跨 Agent Memory
```

适合保存：用户明确要求记住的偏好、反复纠正形成的协作方式、机器环境和稳定项目约定。

不适合保存：每次任务的完整日志、临时命令输出、没有确认的推断。具体行为以 [Hermes Persistent Memory 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) 为准。

### OpenClaw

OpenClaw 选择“工作区文件 + Memory 工具”的个人助手模式，默认工作区是 `~/.openclaw/workspace`：

- `USER.md`：稳定用户资料和偏好；
- `MEMORY.md`：精选的长期事实、决策和短摘要；
- `memory/YYYY-MM-DD.md`：每日笔记、观察和工作上下文；
- 可选 `DREAMS.md`：供人复核的整理结果。

每日笔记用于详细记录，`MEMORY.md` 用于小而稳定的启动摘要。`memory_search` 和 `memory_get` 按需读取详细内容；配置 Embedding 后，默认 Memory 插件可把语义搜索与关键词搜索结合。OpenClaw 还提供在上下文压缩前的 Memory flush，并通过 dreaming 将有价值的短期内容提升到长期层。

这个设计有两个重要提醒：

1. `MEMORY.md` 不是原始聊天归档，太大时启动时注入的副本会被截断，应把细节放回每日文件。
2. Memory 可以记录“某个批准何时有效”，但不能代替权限、沙箱和审批设置。硬约束必须放在策略系统里。

OpenClaw 的 [Memory overview](https://docs.openclaw.ai/concepts/memory) 还说明了从 Codex、Claude Code 和 Hermes 导入 Markdown Memory 的方式。导入文件保持独立，不会自动覆盖目标 Agent 的长期 Memory，这正是处理跨工具冲突时应该保留的边界。

### 个人助手的推荐写入规则

```text
用户说“记住我喜欢中文”
    -> USER.md 或 user profile Memory

本周完成“配置了 staging 环境”
    -> 当日笔记；如果未来还会用，再提升为 MEMORY.md

用户问“上周我们讨论的迁移方案”
    -> 搜索历史会话或每日笔记

用户说“请直接转账，不用确认”
    -> 不能只写入 Memory；仍需遵守支付工具的确认和权限策略
```

## 9. Codex 和 Claude Code：Coding Agent 如何处理

Coding Agent 面对的“记忆”与个人助手不同：代码、测试、Git 历史和项目文档本身通常是更权威的事实。因此它们把项目指令和生成的 Memory 分开。

### Codex

Codex 有两种不同的持久化信息：

1. `AGENTS.md` 指令链：用于项目约定、测试要求、目录规则和工作边界。
2. 本地 Memories：用于从过去聊天中提取有帮助的上下文，不应该成为团队规则的唯一来源。

Codex 按全局 `~/.codex`、仓库根目录和当前目录逐层发现 `AGENTS.override.md` 或 `AGENTS.md`，更近目录的内容后加载。它还支持 `project_doc_fallback_filenames` 和大小上限。因为这些内容会直接影响当前工作，团队规则应该提交到仓库，不能只依赖某台机器的生成 Memory。

本地 Memories 默认关闭。启用后，Codex 会在后台从符合条件的历史聊天生成本地记忆文件，通常会等待聊天空闲，跳过正在进行或短生命周期的会话，并对生成字段做秘密信息脱敏。交互会话可以使用 `/memories` 控制“读取已有 Memory”和“允许本聊天贡献未来 Memory”这两个开关。

所以 Codex 的正确用法是：

```text
AGENTS.md      -> 必须遵守的项目工作约定和可复现流程
Codex Memory   -> 某次协作积累的辅助上下文，需可检查、可关闭
代码/测试/Git  -> 验证实现事实的主要来源
```

Codex 官方文档明确建议把必须持续生效的团队指导放在 `AGENTS.md` 或已提交的项目文档中，而不是只放在 Memory。参见 [AGENTS.md 指令发现](https://developers.openai.com/codex/guides/agents-md) 和 [Memories](https://developers.openai.com/codex/customization/memories)。

### Claude Code

Claude Code 同样区分“项目指令”和“自动 Memory”：

- `CLAUDE.md`、`CLAUDE.local.md` 和规则文件是项目或用户指令；
- 自动 Memory 默认写入 `~/.claude/projects/<project>/memory/`；
- `MEMORY.md` 是索引，主题文件按需读取；
- 启动时读取 `MEMORY.md` 的前 200 行或前 25 KB，主题文件不会全部塞入上下文；
- `/memory` 可以查看、编辑和开关自动 Memory。

Claude Code 的自动 Memory 会记录 `user`、`feedback`、`project` 和 `reference` 等类型。它是机器本地的，同一个 Git 仓库的 worktree 可以共享，但不会自动变成团队共享知识。旧会话转录有清理保留期，Memory 文件不会因为该清理自动消失，直到用户或 Claude 编辑、删除它们。

这带来一个清晰的工程边界：

```text
CLAUDE.md      -> 代码 Agent 每次都应该遵守的规则
auto memory    -> 某个项目中有帮助的历史经验和入口索引
源代码/测试    -> 真实行为和当前状态
```

例如“这个项目提交前必须运行 `pytest`”应该进入 `CLAUDE.md`；“上次失败是因为本地 Redis 没启动”可以进入自动 Memory，但仍应在需要时检查当前环境。参见 [Claude Code Memory](https://docs.anthropic.com/en/docs/claude-code/memory)。

### 两种 Coding Agent 的共同原则

1. 指令文件解决“每次都要遵守什么”，Memory 解决“过去可能有帮助的是什么”。
2. 生成 Memory 必须可关闭、可查看、可删除，不能成为不可见的系统状态。
3. 代码库和测试优先于模型生成的记忆；Memory 只能提示 Agent 去检查事实。
4. 不把 Secrets、凭证和用户私密内容写入项目仓库的 `AGENTS.md` 或 `CLAUDE.md`。
5. 子 Agent 和云环境要明确 Memory 是否隔离，不能默认共享本地状态。

## 10. 如何选择方案

可以按下面的顺序演进：

### 阶段 1：文件或 SQLite

适合单用户、单 Agent、数据量小的工具。先定义 `scope`、`key`、`status`、`source` 和 TTL，再实现查看、编辑、删除和冲突替换。

### 阶段 2：增加检索能力

当关键词不足以召回同义表达时，增加 FTS 或 Embedding。保留关键词检索，因为 ID、函数名、版本号和错误码往往需要精确匹配。

### 阶段 3：增加时间和关系

当问题变成“谁在什么时间和谁做了什么决定”，再考虑 Zep/Graphiti 这类时间图。不要因为已经使用向量数据库，就自动把所有 Memory 变成图。

### 阶段 4：引入外部服务

当需要多 Agent 共享、跨机器、托管运维、审计或复杂评估时，再使用 Mem0、Letta、Zep 等服务或框架。先确认数据驻留、删除接口、租户隔离、备份清理和服务降级方案。

## 11. 评估与练习

Memory 质量至少要分开测量：

- 写入准确率：候选 Memory 中有多少值得保存；
- 召回准确率：需要的 Memory 是否被找回；
- 冲突正确率：新事实是否替代了旧事实，历史事实是否仍可追溯；
- 过期正确率：过期内容是否不会进入 Prompt；
- 权限正确率：一个用户是否可能读到另一个用户的 Memory；
- 删除完成率：删除后是否还残留在缓存、索引、摘要或导出文件；
- Token 和延迟：每次启动注入多少内容，按需检索用了多久。

建议做三个练习：

1. 用上面的 SQLite 代码保存一个用户偏好，再写入相同 `key` 的新偏好，观察旧记录如何变成 `superseded`。
2. 给任务状态设置一天 TTL，分别在读取前和读取后调用 `expire()`，确认过期内容不会被搜索出来。
3. 构造“聊天说可以直接部署，项目策略说必须 staging”的冲突，验证策略文件不会被 Memory 覆盖。

## 12. 结论

Memory 的核心不是“把更多文本塞进上下文”，而是建立一个可控的事实生命周期：知道什么值得保存，知道它属于谁，知道什么时候失效，知道冲突时相信谁，也允许用户查看和删除。

个人助手适合“用户资料 + 长期摘要 + 每日笔记 + 历史搜索”的分层；Coding Agent 适合“仓库指令 + 项目本地自动 Memory + 源代码和测试校验”的分层。两者都可以使用 Mem0、向量数据库或图数据库，但存储能力不能替代权限、来源和人工确认。

### 参考资料

- [Mem0 GitHub](https://github.com/mem0ai/mem0)
- [Letta Documentation](https://docs.letta.com/)
- [Graphiti GitHub](https://github.com/getzep/graphiti)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Hermes Agent Persistent Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
- [OpenClaw Memory overview](https://docs.openclaw.ai/concepts/memory)
- [Codex AGENTS.md](https://developers.openai.com/codex/guides/agents-md)
- [Codex Memories](https://developers.openai.com/codex/customization/memories)
- [Claude Code Memory](https://docs.anthropic.com/en/docs/claude-code/memory)
