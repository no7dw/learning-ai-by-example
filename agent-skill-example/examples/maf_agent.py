"""Microsoft Agent Framework (MAF): use its native SkillsProvider."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skill_runtime import dry_run


SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def build_agent():
    from agent_framework import Agent, SkillsProvider
    from agent_framework.openai import OpenAIChatClient

    client = OpenAIChatClient(
        model=__import__("os").environ.get("OPENAI_MODEL", "gpt-4o-mini")
    )
    skills = SkillsProvider.from_paths(
        SKILLS_DIR,
        # These are local, read-only example files. Keep approval enabled for
        # untrusted skills or when skill scripts/resources are executable.
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
    )
    return Agent(
        client=client,
        instructions=(
            "Use the installed skills when relevant. Load a skill before claiming "
            "to have followed its procedure."
        ),
        context_providers=[skills],
    )


async def run(prompt: str) -> None:
    agent = build_agent()
    result = await agent.run(prompt)
    print(result.text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*", help="Task for the agent")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        dry_run(SKILLS_DIR)
        return
    asyncio.run(run(" ".join(args.prompt)))


if __name__ == "__main__":
    main()
