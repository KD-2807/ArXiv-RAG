# Contributing

Report reproducible issues with the app version, Ubuntu/Python versions, exact embedding-model name, operation, and relevant log lines. Remove private document text and paths before posting logs.

Keep domain logic, inference, PDF extraction, persistence, services, CLI, and UI separate. Inject embedding/extraction dependencies so failure paths can be tested without live models. Add regression coverage for behavior changes, especially destructive maintenance and recovery.

Run the unit tests and native UI smoke test from README. GitHub Actions also configures focused Ruff checks and a dependency audit. Do not commit real private PDFs, indexes, model weights, credentials, virtual environments, or caches. The included PDFs are synthetic test data.

The owner of a published repository maintains that copy; this archive does not imply an existing repository or issue tracker.

Install development checks with `python -m pip install -r python/requirements-dev.txt`.

```bash
PYTHONPATH=python python -m mypy
PYTHONPATH=python python -m mypy --strict python/rag_shelf/domain.py python/rag_shelf/ranking.py python/rag_shelf/filesystem.py python/rag_shelf/lifecycle.py
PYTHONPATH=python python -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
ruff check --select E9,F63,F7,F82 python tests scripts
```

Whole-project typing is gradual (`check_untyped_defs`), not strict. New core modules should use explicit annotations. Resource warnings are errors in the test process; Python versions differ in which SQLite lifecycle warnings they emit, so exception-path ownership is also tested directly.
