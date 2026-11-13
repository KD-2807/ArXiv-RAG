# Validation for 1.4.1

## Patch verification

The original 122 tests were reorganized without dropping tests. Three new cases cover real two-column extraction, old-profile search/mixed-profile rejection, and explicit index outcomes. The old `-layout` command preserved 0 of 6 expected within-column adjacent line pairs in the synthetic fixture; the patched extractor preserved all 6. This is extraction correctness evidence, not a semantic search benchmark. The fixture needs no ReportLab dependency to run.

The current suite, Ruff, project-wide gradual mypy, four-module strict mypy, compilation and shell checks were rerun. The synthetic FAISS measurements and dependency audit listed below were executed for v1.4.0; unchanged dependencies and retrieval algorithms were not benchmarked/audited again for this refactor.

## Executed

- **125 tests passed, zero skipped**, Python 3.12.14, NumPy 2.3.5, FAISS CPU 1.13.0. The suite ran with `-W error::ResourceWarning`.
- Real FAISS build/serialization/load/query tests passed, including cache integrity, revision invalidation, subject filtering, budgets, missing sources, document deduplication, adaptive candidate expansion, and exact fallback.
- New recovery tests covered committed batches surviving transient failure/interruption, reopening the database, changing batch size, adaptive chunk splitting, changed/shorter extraction prefixes, changed PDF bytes, reset isolation, and publication rollback. Partial documents stayed out of search.
- A 450-chunk dominant document did not hide other documents in exact retrieval or evaluation. Bounded document ranking also matched a randomized reference with ties.
- Repository closure was asserted after context exit and initialization failure. Test connection ownership was corrected. PDF child termination/reaping was exercised with a real timed-out process and an interrupted wait; extractor iterators were closed on early exit.
- Existing mount safety, staged relocation, cleanup preview, deterministic/transient failure classification, permissions, Unicode-path handling, HTTP contract, headless Tcl, and viewer-argument regression tests passed.
- Real Poppler extraction and Poppler/Tesseract OCR of the included synthetic PDFs passed. HTTP fixture vectors were deterministic test data, not live Ollama inference.
- Actual 10K, 100K, and 1M chunk SQLite/FAISS synthetic benchmarks completed. [Measured results and raw reports](benchmarks/RESULTS.md) include latency, build/query peak RSS, nearest-neighbor recall, and an effort sweep. Low-effort ANN recall was poor; exact remains default.
- Ruff correctness checks passed. Mypy checked all application modules with `check_untyped_defs`; strict checks passed for domain, ranking, filesystem, and lifecycle modules. This is not whole-project strict typing.

- `pip-audit -r python/requirements-ann.txt` reported no known vulnerabilities at validation time. This covers resolved Python runtime/ANN dependencies, not OS packages or a permanent security guarantee.
- Python compilation, shell syntax, CLI help, and relative documentation links passed. The Tcl/Tk layout is unchanged; the fresh-settings default database filename advances to index-v3.sqlite3.

## Environment limits

Xvfb and Tk were made available, but Xvfb could not create its local display sockets in this container (`Cannot establish any listening sockets`). Full current GUI rendering/interaction was therefore not executed. Headless Tcl tests passed. `tests/ui_smoke.py` supports the existing fixture worker and a new opt-in `--live-model` mode for the Ubuntu desktop or Xvfb.

Bubblewrap was present, but the host denied namespace setup. Fail-closed startup and command construction were checked; successful sandboxed extraction still requires validation under the target Ubuntu policy.

No live Ollama server/model or user GPU was available. `tests/live_ollama.py` now accepts `--faiss`, `--ocr-language eng`, and `--pdf-sandbox bubblewrap` to validate installed optional features together. These opt-in paths were syntax-checked but not executed against live inference here. The same applies to the live-model GUI path.

## What the results do not establish

- Research-paper relevance: the supplied 16-query/four-paper starter was not run with a real embedding model. It requires local corpus hashes and independently expanded judgments.
- Native Ubuntu GPU placement, VRAM consumption, app/desktop memory, actual PDF viewer behavior, or Hermes/OpenClaw/n8n deployment.
- Large-library performance at the user's model dimensions: the executed synthetic benchmark uses 128-dimensional random vectors.
- Uninterruptible kernel I/O or SIGKILL cleanup guarantees. Graceful cancellation closes owned resources; forcibly killed processes can leave temporary files, and the current uncommitted batch must be repeated.
- Filesystem snapshot semantics, hostile-file safety certification, or production readiness.

GitHub Actions is configured to install optional dependencies, run tests, type checks, the fixture GUI smoke test, and a dependency audit. Configuration is not an observed CI run. Python versions differ in SQLite ResourceWarning behavior; explicit ownership assertions supplement warning checks. Dependency pins are not a platform-wide hash lock.
