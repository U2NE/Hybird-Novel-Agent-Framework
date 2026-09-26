#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROLE_NAMES = [
    "story-director",
    "plot-architect",
    "world-builder",
    "character-agent",
    "chapter-planner",
    "writer",
    "dialogue-specialist",
    "continuity-reviewer",
    "character-consistency-reviewer",
    "foreshadowing-tracker",
    "style-critic",
    "reader-simulator",
    "state-scanner",
]
CONFIG_START = "# novel-agent-framework:agents:start"
CONFIG_END = "# novel-agent-framework:agents:end"
AGENTS_START = "<!-- novel-agent-framework:start -->"
AGENTS_END = "<!-- novel-agent-framework:end -->"


def role_name(name: str) -> str:
    return f"novel-{name}"


def read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def remove_managed_block(text: str, start: str, end: str) -> str:
    pattern = re.compile(
        rf"(?:\n|^){re.escape(start)}.*?{re.escape(end)}(?:\n|$)",
        re.DOTALL,
    )
    return pattern.sub("\n", text).strip()


def ensure_agents_root(text: str) -> str:
    if re.search(r"(?m)^\s*\[agents\]\s*$", text):
        if re.search(r"(?ms)^\s*\[agents\]\s*$.*?^\s*enabled\s*=\s*false\s*$", text):
            raise RuntimeError(
                "target .codex/config.toml explicitly disables agents; refusing silent override"
            )
        return text
    prefix = text.rstrip()
    if prefix:
        prefix += "\n\n"
    return prefix + "[agents]\nenabled = true\nmax_concurrent_threads_per_session = 8\n"


def merge_config(old: str) -> str:
    cleaned = remove_managed_block(old, CONFIG_START, CONFIG_END)
    cleaned = ensure_agents_root(cleaned)
    parsed = tomllib.loads(cleaned) if cleaned.strip() else {}
    agents = parsed.get("agents", {})
    for name in ROLE_NAMES:
        reserved = role_name(name)
        if reserved in agents:
            raise RuntimeError(f"target already defines reserved Codex role: {reserved}")
    lines = [cleaned.rstrip(), "", CONFIG_START]
    for name in ROLE_NAMES:
        reserved = role_name(name)
        lines.extend(
            [
                f'[agents."{reserved}"]',
                f'description = "Novel framework role: {name}"',
                f'config_file = "agents/{reserved}.toml"',
                "",
            ]
        )
    lines.append(CONFIG_END)
    merged = "\n".join(lines).strip() + "\n"
    tomllib.loads(merged)
    return merged


def merge_agents_md(old: str) -> str:
    cleaned = remove_managed_block(old, AGENTS_START, AGENTS_END)
    block = f"""{AGENTS_START}
## Novel Agent Framework

- For Korean long-form fiction planning/writing/review, invoke $novel.
- .novel/state/story.sqlite3 is canonical runtime state; never edit it directly.
- Start every operation with workflow-next/orchestrate-next; use python3 .novel/bin/novel.py for every state transition.
- Keep dispatch flat: only the lead spawns runtime-issued novel-* sibling tasks.
- Every framework-controlled subagent call must use the explicit model and reasoning effort from .novel/config/model-routing.json. Never inherit a session/default route.
- Chapter is the canonical prose/revision unit. Internal scenes/beats are planning subunits only.
- Generate exactly three A/B/C chapter candidates.
- Reviewers independently inspect the same frozen draft and never see one another's reviews.
- Publishing prose stages memory proposals only; Canon changes require explicit human memory approval even in autonomous mode.
- Reader Simulator must not receive future plot, hidden secrets, AuthorIntent, AuthorPreference, or future foreshadow plans.
- Use resume/status after interruption; never bypass checkpoints/idempotency by editing the SQLite file.
{AGENTS_END}
"""
    return (cleaned.rstrip() + "\n\n" + block).lstrip()


def copy_file(src: Path, dst: Path, dry_run: bool, ops: list[str]) -> None:
    ops.append(f"copy {dst}")
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree(src: Path, dst: Path, dry_run: bool, ops: list[str]) -> None:
    for item in src.rglob("*"):
        if item.is_file() and "__pycache__" not in item.parts:
            copy_file(item, dst / item.relative_to(src), dry_run, ops)


def write_text(path: Path, content: str, dry_run: bool, ops: list[str]) -> None:
    ops.append(f"write {path}")
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def install(target: Path, dry_run: bool = False) -> dict[str, object]:
    target = target.resolve()
    if not target.is_dir():
        raise RuntimeError(f"target is not a directory: {target}")
    if not (target / ".git").exists():
        raise RuntimeError("target must be a Git repository")

    manifest_path = target / ".novel" / "manifest.json"
    prior_install = manifest_path.exists()
    ops: list[str] = []

    for name in ROLE_NAMES:
        dst = target / ".codex" / "agents" / f"{role_name(name)}.toml"
        if dst.exists() and not prior_install:
            raise RuntimeError(f"refusing to overwrite pre-existing reserved role file: {dst}")

    copy_tree(
        SOURCE_ROOT / "src" / "novel_agent_framework",
        target / ".novel" / "runtime" / "novel_agent_framework",
        dry_run,
        ops,
    )
    copy_file(
        SOURCE_ROOT / "bin" / "novel.py",
        target / ".novel" / "bin" / "novel.py",
        dry_run,
        ops,
    )
    copy_file(
        SOURCE_ROOT / "config" / "model-routing.json",
        target / ".novel" / "config" / "model-routing.json",
        dry_run,
        ops,
    )
    copy_file(
        SOURCE_ROOT / "config" / "retrieval.json",
        target / ".novel" / "config" / "retrieval.json",
        dry_run,
        ops,
    )
    for name in ROLE_NAMES:
        copy_file(
            SOURCE_ROOT / ".codex" / "agents" / f"{role_name(name)}.toml",
            target / ".codex" / "agents" / f"{role_name(name)}.toml",
            dry_run,
            ops,
        )
    copy_file(
        SOURCE_ROOT / "skills" / "novel" / "SKILL.md",
        target / ".agents" / "skills" / "novel" / "SKILL.md",
        dry_run,
        ops,
    )
    copy_file(
        SOURCE_ROOT / "skills" / "korean-fiction-craft" / "SKILL.md",
        target / ".agents" / "skills" / "korean-fiction-craft" / "SKILL.md",
        dry_run,
        ops,
    )
    copy_file(SOURCE_ROOT / "LICENSE", target / ".novel" / "LICENSE", dry_run, ops)

    merged_config = merge_config(read_optional(target / ".codex" / "config.toml"))
    write_text(target / ".codex" / "config.toml", merged_config, dry_run, ops)
    merged_agents = merge_agents_md(read_optional(target / "AGENTS.md"))
    write_text(target / "AGENTS.md", merged_agents, dry_run, ops)

    manifest = {
        "name": "novel-agent-framework",
        "version": "0.1.0",
        "runtime": ".novel/runtime/novel_agent_framework",
        "cli": ".novel/bin/novel.py",
        "database": ".novel/state/story.sqlite3",
        "skill": ".agents/skills/novel/SKILL.md",
        "roles": [role_name(name) for name in ROLE_NAMES],
        "canonical_language": "ko",
        "canonical_unit": "chapter",
        "memory_authority": "human-approval-required",
        "model_routing": ".novel/config/model-routing.json",
        "retrieval_config": ".novel/config/retrieval.json",
    }
    write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        dry_run,
        ops,
    )

    validation: dict[str, object] = {"config": "not-run", "runtime": "not-run"}
    if not dry_run:
        tomllib.loads((target / ".codex" / "config.toml").read_text(encoding="utf-8"))
        validation["config"] = "parsed"
        result = subprocess.run(
            [
                sys.executable,
                str(target / ".novel" / "bin" / "novel.py"),
                "--project-root",
                str(target),
                "doctor",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        validation["runtime"] = "doctor-ran"
        validation["doctor_exit"] = result.returncode
        validation["doctor_stdout"] = result.stdout.strip()
        validation["doctor_stderr"] = result.stderr.strip()

    return {
        "target": str(target),
        "dry_run": dry_run,
        "operations": ops,
        "prior_install": prior_install,
        "validation": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Novel Agent Framework into a Git repo")
    parser.add_argument("target")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = install(Path(args.target), args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, OSError, tomllib.TOMLDecodeError, subprocess.SubprocessError) as exc:
        print(f"install-project: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
