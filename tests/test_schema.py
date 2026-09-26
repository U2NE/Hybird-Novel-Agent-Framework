from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from novel_agent_framework.errors import SchemaError
from novel_agent_framework.runtime import NovelRuntime


class SchemaTests(unittest.TestCase):
    def test_unsupported_schema_version_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = NovelRuntime(root)
            runtime.init("schema")
            con = sqlite3.connect(root / ".novel" / "state" / "story.sqlite3")
            try:
                con.execute(
                    "UPDATE schema_meta SET value='999' WHERE key='schema_version'"
                )
                con.commit()
            finally:
                con.close()
            with self.assertRaises(SchemaError):
                runtime.status()


if __name__ == "__main__":
    unittest.main()
