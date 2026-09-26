from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_project", ROOT / "scripts" / "install_project.py")
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_install_copies_executable_runtime_and_preserves_existing_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            subprocess.run(["git", "init", "-q", str(target)], check=True)
            (target / "AGENTS.md").write_text("# Existing\n\nKeep me.\n", encoding="utf-8")
            (target / ".codex").mkdir()
            (target / ".codex" / "config.toml").write_text(
                '[agents]\nenabled = true\nmax_depth = 1\n', encoding="utf-8"
            )
            result = installer.install(target)
            self.assertFalse(result["dry_run"])
            self.assertTrue((target / ".novel" / "bin" / "novel.py").is_file())
            self.assertTrue((target / ".novel" / "config" / "retrieval.json").is_file())
            self.assertTrue(
                (target / ".novel" / "runtime" / "novel_agent_framework" / "runtime.py").is_file()
            )
            self.assertEqual(len(list((target / ".codex" / "agents").glob("novel-*.toml"))), 13)
            agents_text = (target / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("Keep me.", agents_text)
            self.assertIn("Novel Agent Framework", agents_text)

    def test_non_git_target_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):
                installer.install(Path(temp))


if __name__ == "__main__":
    unittest.main()
