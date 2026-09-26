.PHONY: check test smoke optional-lint

check:
	python3 -m compileall -q src scripts bin
	PYTHONPATH=src python3 -m unittest discover -s tests -v

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

smoke:
	python3 scripts/runtime_smoke.py

optional-lint:
	ruff check src tests scripts bin
	mypy src/novel_agent_framework
