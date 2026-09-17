"""LangGraph: the model/tool loop is represented explicitly as a graph."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skill_runtime import discover_skills, dry_run, get_skill, system_prompt


SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def build_graph():
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode, tools_condition

    skills = discover_skills(SKILLS_DIR)

    @tool
    def list_skills() -> str:
        """List the names and descriptions of installed skills."""
        return json.dumps(
            [{"name": skill.name, "description": skill.description} for skill in skills],
            indent=2,
        )

    @tool
    def load_skill(name: str) -> str:
        """Load the complete SKILL.md procedure for an exact skill name."""
        return get_skill(skills, name).body

    model = ChatOpenAI(model=__import__("os").environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    model_with_tools = model.bind_tools([list_skills, load_skill])

    def call_model(state: MessagesState):
        messages = state["messages"]
        if not messages or messages[0].type != "system":
            from langchain_core.messages import SystemMessage

            messages = [SystemMessage(content=system_prompt(skills)), *messages]
        return {"messages": [model_with_tools.invoke(messages)]}

    builder = StateGraph(MessagesState)
    builder.add_node("model", call_model)
    builder.add_node("tools", ToolNode([list_skills, load_skill]))
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "model")
    return builder.compile()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*", help="Task for the agent")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        dry_run(SKILLS_DIR)
        return

    graph = build_graph()
    result = graph.invoke({"messages": [{"role": "user", "content": " ".join(args.prompt)}]})
    print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
