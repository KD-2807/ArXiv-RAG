# Changelog

## 1.4.1

- Removed `pdftotext -layout`; added a real two-column PDF regression comparing old and new reading order.
- Advanced extraction pipeline to v3, retaining old-index search while refusing mixed extraction profiles. New GUI settings default to index-v3.sqlite3; existing saved paths are preserved.
- Split Repository into persistence, RootManager, and CleanupService using composition and compatibility delegates; retained transactional cleanup/relocation safeguards.
- Reorganized version-named tests into functional modules with shared fixtures.
- Replaced dynamic count-field mutation with IndexOutcome and explicit counters; removed redundant initialization.

## 1.4.0

- Kept Python-only source, the existing native UI, and existing passage-search defaults.
- Added document-level search with bounded top-K state, adaptive ANN candidate depth, configurable HNSW search effort, and document-level exact fallback.
- Removed the evaluator's 100-chunk ceiling; included per-query retrieval diagnostics.
- Added per-batch embedding checkpoints with PDF/profile/text-prefix validation and atomic document publication; added checkpoint status/reset commands.
- Closed repositories on initialization failures and context exit, fixed test connection ownership, closed extraction iterators, and killed/reaped PDF child groups after timeout or interruption.
- Added opt-in live GUI and combined Ollama/FAISS/OCR/Bubblewrap checks.
- Recorded actual 10K/100K/1M synthetic benchmarks, including poor ANN recall at low search effort; exact remains default.
- Added project-wide gradual type checking and strict checking for four core modules.

## 1.3.0

- Preserved the native interface layout and existing CLI defaults.
- Added mounted-source identity baselines, nested/bind-mount cleanup guards, legacy baseline adoption, and verified relocation recovery for legitimate mount changes.
- Added optional FAISS HNSW caches, SQLite revision invalidation, subject-specific caches, integrity verification, memory estimates, and exact fallback for insufficient live candidates.
- Added optional Bubblewrap process/filesystem/network isolation that fails closed, and optional Tesseract fallback for empty PDF pages.
- Exposed every source alias in the existing result table and added supported-viewer page navigation.
- Added document-level retrieval metrics, a fixed research-paper starter dataset, corpus hash locking, and a separate live Ollama integration script.
- Extended regression coverage with real FAISS, image-only PDF OCR, loopback HTTP contract, mount consistency, evaluation, and UI alias tests.
- Kept pipeline-v2 text indexes reusable; OCR settings explicitly belong to the index profile.

## 1.2.0

- Made the project Python-only, including source, launchers, documentation, and CI.
- Limited persistent failure skipping to typed deterministic document errors. Transient/unknown failures retry normally.
- Added a circuit breaker after two consecutive final Ollama failures; unattempted documents remain untouched.
- Changed cleanup to preview-only by default, with exact-plan confirmation tokens and root/scan/relocation guards.
- Added cleanup of orphan error records independently of source aliases.
- Replaced immediate root switching with staged aliases and one final verified transaction; added cancellation of staging.
- Introduced pipeline version 2 and a new default database filename. Legacy indexes remain searchable.
- Increased bounded search batches and added partial top-k selection with missing-source fallback and deterministic ties.
- Split scanning, per-document indexing, and CLI maintenance handlers; retained dimension cache after successful commits.
- Removed process-global umask mutation and improved missing-executable and OLLAMA_HOST diagnostics.
- Added focused correctness lint and dependency-audit CI jobs, plus safety regression tests.

## 1.1.0

Added embedding batches, adaptive chunk splitting, source caching, private file permissions, extraction resource limits, and maintenance controls. The retry and maintenance behavior from this version is superseded by 1.2.0.

## 1.0.0

Initial local PDF indexing, retrieval, native desktop UI, and CLI.
