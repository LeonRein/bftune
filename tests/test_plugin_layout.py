"""The Claude Code plugin layer: skill/agent frontmatter, cross-references, launcher, manifests."""

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USER_SKILLS = {"tune", "feedback", "flight-plan", "review"}


def _front(p: Path) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", p.read_text(), re.S)
    assert m, f"{p}: missing frontmatter"
    out, key = {}, None
    for line in m.group(1).splitlines():
        if re.match(r"^\s+- ", line) and key:
            out.setdefault(key, []).append(line.split("- ", 1)[1].strip())
        elif ":" in line:
            key, v = line.split(":", 1)
            key, v = key.strip(), v.strip().strip('"')
            out[key] = v if v else []
    return out


def test_skills_frontmatter():
    skills = {p.parent.name: _front(p) for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert USER_SKILLS <= set(skills)
    for name, fm in skills.items():
        assert fm.get("name") == name
        assert len(fm.get("description", "")) > 60
        if name in USER_SKILLS:
            assert fm.get("user-invocable", "true") != "false"
        else:
            assert fm.get("user-invocable") == "false", name


def test_skill_cross_references_exist():
    names = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    for p in list((ROOT / "skills").glob("*/SKILL.md")) + list((ROOT / "agents").glob("*.md")):
        for ref in re.findall(r"`bftune:([a-z-]+)`", p.read_text()):
            assert ref in names, f"{p}: unknown skill bftune:{ref}"


def test_agent_preloads_existing_skills():
    names = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    fm = _front(ROOT / "agents" / "tuning-engineer.md")
    assert fm["name"] == "tuning-engineer" and set(fm["skills"]) <= names


def test_manifests_and_launcher():
    pj = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    mk = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    ver = re.search(r'^version = "(.+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    assert pj["name"] == "bftune" and pj["version"] == ver and mk["plugins"][0]["name"] == "bftune"
    assert os.access(ROOT / "bin" / "bftune", os.X_OK)


def test_evals_have_prompt_case_and_graders():
    cases = [p for p in (ROOT / "evals").iterdir() if p.is_dir() and p.name != "results"]
    assert cases
    for c in cases:
        assert (c / "prompt.md").exists() and (c / "case.yaml").exists() and list((c / "graders").glob("*.md"))
        assert os.access(c / "fixture.sh", os.X_OK)
