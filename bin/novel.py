#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
BASE = HERE.parent.parent
for candidate in (BASE / "runtime", BASE / "src"):
    if (candidate / "novel_agent_framework").is_dir():
        sys.path.insert(0, str(candidate))
        break
else:
    raise SystemExit("novel runtime package not found beside CLI")

from novel_agent_framework.cli import main

raise SystemExit(main())
