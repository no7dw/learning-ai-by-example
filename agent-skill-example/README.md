# Agent Skills: LangChain, LangGraph, MAF, and FastestAI

This small example shows how an application can implement a `SKILL.md` convention
on top of three agent runtimes:

| Runtime | What the example makes explicit |
| --- | --- |
| LangChain | A normal tool-calling agent discovers and loads a skill. |
| LangGraph | The model -> tool -> model loop is represented as graph edges. |
| MAF | Microsoft Agent Framework's native `SkillsProvider` scans the same files. |
| FastestAI | The repository's `build_agent()` path receives local and registry tools. |

For LangChain, LangGraph, and FastestAI, the application owns the skill registry
and exposes two tools:

1. `list_skills()` returns cheap metadata that can be included in the prompt.
2. `load_skill(name)` reads the full procedure only when the model needs it.

That is progressive disclosure. The skill explains **how to work**; Python tools
implement **what the agent can do**.

MAF has this protocol built in: `SkillsProvider.from_paths(...)` discovers the
frontmatter, injects the skill index into the run context, and registers
`load_skill` plus resource/script tools when applicable. The MAF example uses
that native provider instead of reimplementing the registry.

## Layout

```text
agent-skill-example/
├── skills/
│   ├── incident-triage/SKILL.md
│   └── release-notes/SKILL.md
├── skill_runtime.py          # shared registry and tool behavior
├── examples/
│   ├── langchain_agent.py
│   ├── langgraph_agent.py
│   ├── maf_agent.py
│   └── fastestai_agent.py
├── tests/test_skill_runtime.py
└── pyproject.toml
```

## Run

The examples require an OpenAI-compatible API key. The default model is
`gpt-4o-mini`; set `OPENAI_MODEL` to use another model.

```bash
uv sync
export OPENAI_API_KEY=...

uv run python examples/langchain_agent.py \
  "Triage this incident: checkout returns HTTP 500 after a deploy"

uv run python examples/langgraph_agent.py \
  "Write release notes for the new CSV export"

uv run python examples/maf_agent.py \
  "Triage this incident: checkout returns HTTP 500 after a deploy"
```

### FastestAI

The FastestAI example uses the same initialization path as
`fastestai-api/src/fastestai/agents/utils.py`:

1. Build an `Agent` config with tool names.
2. Put application-owned Python tools in `extra_tools`.
3. `build_agent()` sends remaining tool IDs to `load_tools()` for MCP registry
   resolution.
4. FastestAI creates a `TaskAgent` or `ReActAgent` with the resolved tools.

Skills are still ordinary application tools. `list_skills` exposes metadata and
`load_skill` reads the selected `SKILL.md`; neither is a special model feature.
The example also includes a small `lookup_incident_logs` tool so the difference
between procedural knowledge and executable capability is visible.

Run it from the fastestai-api checkout so its dependencies and package are
available:

```bash
cd /Users/dengwei/work/ai/maybeai-uni/fastestai-api
PYTHONPATH=src uv run python \
  /Users/dengwei/work/no7dw/learning-ai-by-example/agent-skill-example/examples/fastestai_agent.py \
  "Triage this incident: checkout returns HTTP 500 after a deploy"
```

The live example needs the fastestai-api model settings and an API key. To add
a real MCP registry tool, set `FASTESTAI_TOOL_ID` to its tool ID; the script
leaves that ID out of `extra_tools`, so FastestAI resolves it through
`prepare_tools_for_agent()` and `load_tools()`.

Use `--dry-run` without starting services or calling a model:

```bash
uv run python examples/fastestai_agent.py --dry-run
```

All examples have `--dry-run`, which prints the prompt/tool contract without
calling a model:

```bash
uv run python examples/langchain_agent.py --dry-run
```

MAF means **Microsoft Agent Framework** in this example. Its package and API are
still evolving, so keep the MAF dependency separate from the LangChain examples.
