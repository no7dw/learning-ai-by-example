"""FastestAI: initialize an agent with skills and executable tools.

This example intentionally uses the application adapter from fastestai-api:

    Agent config -> build_agent() -> prepare_tools_for_agent()
    -> local extra_tools and/or MCP load_tools() -> TaskAgent/ReActAgent

The filesystem skills are exposed as tools because SKILL.md is procedural
knowledge, not executable code.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

skill_runtime = importlib.import_module("skill_runtime")


SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def build_skill_tools():
    """Create local FastestAI FunctionTools backed by the skill directory."""
    from fastestai.agents.maf_runtime import FunctionTool

    skills = skill_runtime.discover_skills(SKILLS_DIR)

    def list_skills() -> str:
        """List installed skills and their short descriptions."""
        return json.dumps(
            [
                {"name": skill.name, "description": skill.description}
                for skill in skills
            ],
            indent=2,
        )

    def load_skill(name: str) -> str:
        """Load the complete SKILL.md procedure for an exact skill name."""
        return skill_runtime.get_skill(skills, name).body

    def lookup_incident_logs(service: str) -> str:
        """Return example evidence for a service during incident triage."""
        evidence = {
            "service": service,
            "window": "2026-09-10T09:00:00Z to 2026-09-10T09:15:00Z",
            "confirmed_facts": [
                f"{service} returned HTTP 500 for checkout requests.",
                "The errors began shortly after a deployment.",
            ],
            "not_confirmed": [
                "No root cause has been established from these sample logs.",
            ],
        }
        return json.dumps(evidence, indent=2)

    tools = [
        FunctionTool(
            name="list_skills",
            description="List the names and descriptions of installed skills.",
            func=list_skills,
        ),
        FunctionTool(
            name="load_skill",
            description="Load the complete SKILL.md procedure for an exact skill name.",
            func=load_skill,
        ),
        FunctionTool(
            name="lookup_incident_logs",
            description="Look up incident evidence for a named service.",
            func=lookup_incident_logs,
        ),
    ]
    return {tool.name: tool for tool in tools}


async def initialize_agent():
    """Initialize a FastestAI agent using the repository's real builder."""
    from fastestai.agents.models import Agent
    from fastestai.agents.utils import build_agent
    from fastestai.message import LogChunk

    local_tools = build_skill_tools()
    configured_tool_ids = list(local_tools)

    # Any ID not present in local_tools is resolved by FastestAI's MCP loader.
    registry_tool_id = os.getenv("FASTESTAI_TOOL_ID")
    if registry_tool_id:
        configured_tool_ids.append(registry_tool_id)

    config = Agent(
        name="incident_triage_agent",
        description="Triage incidents using an explicit procedure and evidence tools.",
        system_message=(
            f"{skill_runtime.system_prompt(skill_runtime.discover_skills(SKILLS_DIR))}\n\n"
            "For incident tasks, load incident-triage before forming conclusions. "
            "Use lookup_incident_logs for evidence and never invent a root cause."
        ),
        model=os.getenv("FASTESTAI_MODEL", "gpt-4o-mini"),
        tools=configured_tool_ids,
    )

    agent = None
    async for item in build_agent(
        agent_config=config,
        task_id=str(uuid4()),
        reasoning=False,
        extra_tools=local_tools,
    ):
        if isinstance(item, LogChunk):
            print(f"[setup] {item.data.log.content or item.data.log.header}")
        else:
            agent = item

    if agent is None:
        raise RuntimeError("FastestAI failed to initialize the agent")

    print(f"[setup] agent={agent.name} tools={configured_tool_ids}")
    return agent


async def run(prompt: str) -> None:
    from fastestai.agents.maf_runtime import (
        Response,
        TaskResult,
        ThoughtEvent,
        ToolCallExecutionEvent,
        ToolCallRequestEvent,
    )

    agent = await initialize_agent()
    async for event in agent.run_stream(task=prompt):
        if isinstance(event, TaskResult):
            if event.messages:
                print(f"\nFINAL\n{event.messages[-1].to_text()}")
        elif isinstance(event, ToolCallRequestEvent):
            print(f"[tool call] {event.to_text()}")
        elif isinstance(event, ToolCallExecutionEvent):
            print(f"[tool result] {event.to_text()}")
        elif isinstance(event, ThoughtEvent):
            print(f"[thought] {event.to_text()}")
        elif isinstance(event, Response):
            print(event.chat_message.to_text())
        else:
            print(event.to_text())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*", help="Task for the agent")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        skill_runtime.dry_run(SKILLS_DIR)
        print("\nFASTESTAI INITIALIZATION")
        print("- Agent.tools contains local skill tools and optional registry tool IDs")
        print("- extra_tools supplies local FunctionTool instances")
        print("- remaining IDs are loaded by FastestAI through MCP")
        return

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        parser.error("a prompt is required unless --dry-run is used")
    asyncio.run(run(prompt))


if __name__ == "__main__":
    main()
