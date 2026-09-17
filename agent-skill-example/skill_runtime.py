"""A tiny filesystem-backed skill registry used by every framework example."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse the deliberately small YAML subset used by these examples."""
    if not text.startswith("---\n"):
        return {}, text.strip()

    _, header, body = text.split("---\n", 2)
    metadata: dict[str, str] = {}
    for line in header.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = value.strip().strip('"\'')
    return metadata, body.strip()


def discover_skills(skills_dir: Path) -> list[Skill]:
    skills: list[Skill] = []
    for path in sorted(skills_dir.glob("*/SKILL.md")):
        metadata, body = _frontmatter(path.read_text(encoding="utf-8"))
        name = metadata.get("name") or path.parent.name
        description = metadata.get("description", "")
        skills.append(Skill(name=name, description=description, body=body, path=path))
    return skills


def skill_index(skills: list[Skill]) -> str:
    if not skills:
        return "(no skills are installed)"
    return "\n".join(f"- {skill.name}: {skill.description}" for skill in skills)


def get_skill(skills: list[Skill], name: str) -> Skill:
    normalized = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    for skill in skills:
        if skill.name == normalized:
            return skill
    available = ", ".join(skill.name for skill in skills) or "none"
    raise ValueError(f"Unknown skill {name!r}. Available skills: {available}")


def system_prompt(skills: list[Skill]) -> str:
    return f"""You are an agent that uses filesystem-backed skills.

Skills are procedures, not executable code. Do not claim to have used a skill
until you call load_skill. Start by choosing the most relevant skill from this
index, then call load_skill with its exact name. Follow the loaded procedure.

Available skill index:
{skill_index(skills)}
""".strip()


def dry_run(skills_dir: Path) -> None:
    skills = discover_skills(skills_dir)
    print("SYSTEM PROMPT")
    print(system_prompt(skills))
    print("\nTOOL CONTRACT")
    print("- list_skills() -> skill names and short descriptions")
    print("- load_skill(name) -> full SKILL.md procedure")
    print("\nDISCOVERED FILES")
    for skill in skills:
        print(f"- {skill.name}: {skill.path}")

