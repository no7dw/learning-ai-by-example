"""LangChain: skill discovery and loading are ordinary tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skill_runtime import discover_skills, dry_run, get_skill, system_prompt


SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def build_agent():
    from langchain.agents import create_agent
    from langchain.tools import tool

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

    model = __import__("os").environ.get("OPENAI_MODEL", "openai:gpt-4o-mini")
    return create_agent(model=model, tools=[list_skills, load_skill], system_prompt=system_prompt(skills))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*", help="Task for the agent")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        dry_run(SKILLS_DIR)
        return

    agent = build_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": " ".join(args.prompt)}]})
    print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
