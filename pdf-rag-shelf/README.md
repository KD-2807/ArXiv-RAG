# PDF RAG Shelf

A local PDF embedding and retrieval application for Ubuntu, written in Python with a native Tcl/Tk interface. It uses your installed Ollama models, preserves subject folders, and returns passages with PDF paths and physical page numbers. The worker exits after each operation. The application opens no web server or LAN listener.

This is the retrieval part of RAG. Pass retrieved passages to your preferred LLM, Hermes, OpenClaw, or n8n workflow for answer generation. No agent framework is required.

## Features

- SHA-256 deduplication, subject filters, adaptive embedding batches, resumable batch checkpoints, and atomic document publication.
- Automatic retry eligibility for transient failures and a circuit breaker for Ollama outages.
- Preview-first cleanup with confirmation tokens, root identity checks, and persisted nested mount identities.
- Atomic staged root relocation that reuses existing vectors.
- Exact search by default; optional FAISS HNSW caches for approximate search.
- Optional Bubblewrap filesystem/process/network isolation and optional OCR of empty pages.
- Every source alias is selectable in the existing results table; supported viewers open the selected page.
- Document-level retrieval and Recall@K, MRR@K, nDCG@K without the old 100-chunk evaluation ceiling.
- Measured 10K/100K/1M synthetic FAISS benchmarks and opt-in live Ollama/GUI integration checks.

See [CHANGELOG.md](CHANGELOG.md), [REVIEW.md](REVIEW.md), [VALIDATION.md](VALIDATION.md), and [SECURITY.md](SECURITY.md).

## Install and run on Ubuntu

Target: Ubuntu 24.04 or newer, Python 3.11 or newer, and a graphical desktop for the GUI.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-tk tk poppler-utils util-linux xdg-utils
cd /path/to/arxiv-rag/pdf-rag-shelf
python3 -m venv .venv
.venv/bin/python -m pip install -r python/requirements.txt
bash scripts/run-python.sh
```

The baseline Python dependency is `numpy==2.3.5`. The GUI uses system `wish`; extraction uses `pdftotext` and `prlimit`. No model weights are included or downloaded by the application.

Check your existing Ollama installation with `ollama list`. Start its service if needed with `sudo systemctl start ollama`, or use `ollama serve` if you do not use the service. Avoid starting a second server. Configure Ollama itself to listen on loopback if you want the entire stack unavailable to the LAN; the application never edits the shared service configuration.

Optional application-menu launcher:

```bash
bash scripts/install-launcher.sh
```

Keep the project folder in its final location after installing the launcher.

## Desktop workflow

1. Choose your PDF root, for example `~/Downloads/ArxivShelf`.
2. Choose an index database. The default is `~/.local/share/pdf-rag-shelf-python/index-v3.sqlite3`.
3. Start with `nomic-embed-text-v2-moe:latest`, chunk `600`, overlap `80`, and batch `16`.
4. Click **Index PDFs**, then enter a question and click **Search**.
5. Select a row to read the passage and all available source paths. Identical content with several source aliases has one selectable row per alias.
6. **Open selected PDF at page** uses Evince, Atril, or Okular if available. Otherwise it opens the default viewer and reports the page to navigate to manually.

Pages are one-based physical PDF pages, not journal page labels. Subject names come from the first folder below the root: `ArxivShelf/Sound/paper.pdf` has subject `Sound`. Root-level PDFs are `Uncategorized`. Filters are exact and case-sensitive.

The existing scrollable layout is retained. **Stop** terminates the worker group, escalating after five seconds. Completed documents and committed embedding batches are preserved. **Retry failed PDFs** retries cached document errors; transient errors do not need it. **Use new root** stages relocation. **Clean index** previews counts and requires approval before deleting index records. It never deletes PDFs.

Settings are stored in `~/.config/pdf-rag-shelf-python/settings.tcldata`, respecting `XDG_CONFIG_HOME`. The search model comes from the index profile, not the current dropdown value.

## Upgrade to: corrected PDF reading order

Close all jobs, back up the database, and replace the program files. Poppler now uses its default reading-order extraction instead of `-layout`. This prevents interleaved columns in the included regression PDF. Complex layouts, widely spaced paragraphs, tables, equations, and unusual PDF text encoding can still defeat Poppler's reading-order heuristics; inspect representative passages from your collection.

**Create a new database to benefit from the fix.** Pipeline version 3 records the changed extraction behavior. Existing pipeline-v1/v2 databases remain searchable, but cannot receive newly extracted documents under v3. Old embeddings already contain the old text order; merely upgrading code cannot repair them. Existing checkpoints also belong to their old profile and are not reused in a new database.

In the GUI, choose a new database such as `~/.local/share/pdf-rag-shelf-python/index-v3.sqlite3`, then index your PDFs again. Saved settings may still point to the old database; change the database field explicitly. Keep the old database until the new one is complete. The UI layout is unchanged; only the default filename for fresh settings changes.

```bash
bash scripts/python-cli.sh index \
  --root "$HOME/Downloads/ArxivShelf" \
  --db "$HOME/.local/share/pdf-rag-shelf-python/index-v3.sqlite3" \
  --model 'nomic-embed-text-v2-moe:latest' --chunk 600 --overlap 80
```

Source identity guards and atomic relocation remain in place. Existing v2 cleanup is permitted only when its recorded mount baseline and completed-scan protections are valid. The new extractor does not silently adopt, rewrite, or delete old vectors. Changing model/digest, chunk settings, or OCR language also requires a separate database; batch size does not.

## Command line

```bash
bash scripts/python-cli.sh index \
  --root "$HOME/Downloads/ArxivShelf" \
  --db "$HOME/.local/share/pdf-rag-shelf-python/index-v3.sqlite3" \
  --model 'nomic-embed-text-v2-moe:latest' --batch-size 16

bash scripts/python-cli.sh search \
  --db "$HOME/.local/share/pdf-rag-shelf-python/index-v3.sqlite3" \
  --query 'How is speech synthesis evaluated?' --subject Sound --k 8

bash scripts/python-cli.sh stats --db /path/to/index-v3.sqlite3
```

Search writes JSON Lines to stdout; logs/errors go to stderr:

```json
{"chunk_id":42,"document_id":"sha256-of-pdf","score":0.81,"page":7,"text":"Retrieved passage...","sources":[{"path":"/home/user/papers/Sound/paper.pdf","subject":"Sound"}]}
```

`document_id` identifies PDF content by SHA-256. Similarity is not a probability. Empty searches produce no result lines. One operation per database is allowed by an advisory lock.

| Exit | Meaning |
|---|---|
| 0 | Successful operation; previously known document errors may have been skipped |
| 1 | Fatal error, circuit breaker, invalid service configuration, or refused maintenance |
| 2 | New PDF/scan failures or incomplete relocation; argument parsing can also return 2 |
| 130 | Interrupted worker; committed checkpoints remain reusable |

`--ui` and `--encoded` remain internal pipe options. Existing index/search/stats/prune/cancel-relocation command names and default exact-search behavior are retained.

## Document search and embedding checkpoints

The GUI keeps its passage-oriented behavior. To return one best passage per unique PDF content:

```bash
bash scripts/python-cli.sh search --db /path/to/index-v3.sqlite3 \
  --query 'How is speech synthesis evaluated?' --documents --k 8

bash scripts/python-cli.sh search --db /path/to/index-v3.sqlite3 \
  --backend faiss --documents --candidate-depth 8192 --ann-ef-search 512 \
  --query 'How is speech synthesis evaluated?' --k 8
```

Identical PDF copies count as one document and retain every available alias. Exact mode scans all matching vectors regardless of candidate depth. ANN candidate depth bounds expansion when many chunks belong to the same document; it is not a recall guarantee. See [measured results](benchmarks/RESULTS.md) before choosing ANN settings.

Rerun the same indexing command after interruption. Each successful batch commits pending vectors plus a verified text-prefix cursor. Complete documents publish atomically. Changed extraction invalidates that checkpoint and restarts the document. Deterministic document failures discard pending work; transient failures retain it. Changing batch size is allowed. Existing completed embeddings remain searchable in their original database; v3 extraction requires the new database described above.

```bash
bash scripts/python-cli.sh checkpoint-status --db /path/to/index-v3.sqlite3
bash scripts/python-cli.sh reset-checkpoints --db /path/to/index-v3.sqlite3
```

`checkpoint-status` reports pending documents/chunks. `reset-checkpoints` explicitly deletes all unfinished embedding work, preserving completed documents. Pending work for a PDF whose bytes changed or disappeared stays until reset; normal cleanup does not silently remove these potentially expensive checkpoints. Checkpoints contain text and vectors and have the same privacy requirements as the index.

## Models and recovery

Use your installed `nomic-embed-text-v2-moe:latest` initially. `dengcao/Qwen3-Embedding-8B:Q4_K_M` is an optional larger preset and needs a separate database. Rerankers and chat/coder models are not interchangeable with embedding models. The editable model field must name an installed model supporting `/api/embed`.

Nomic passages/queries receive the document/query prefixes. Qwen3 embedding queries receive a retrieval instruction. Requests use `truncate=false`; recognized context errors split batches, then oversized individual chunks. Chunk size counts characters, not tokens. Very long queries must be shortened manually.

On your RTX 3050 8 GB and 32 GB system RAM, start with Nomic, batch 16, and one indexing job at a time. Ollama controls GPU placement. Inspect actual memory with `ollama ps` and `nvidia-smi`; model download size is not runtime memory.

Only typed deterministic document errors are skip-cached by file hash. Connection errors, GPU failures, malformed responses, and unknown extraction/resource errors retry on the next run without `--retry-failed`. Two consecutive final Ollama failures stop the job without marking unattempted PDFs broken. Restore Ollama and rerun. Incomplete PDFs reuse committed embedding batches automatically after verifying the unchanged PDF hash, profile, and extracted-text prefix. The current uncommitted batch is repeated. Extraction and OCR run again to validate the prefix; their output is not checkpointed.

Address precedence is `--ollama-url`, `OLLAMA_HOST`, then `http://127.0.0.1:11434`. Only HTTP numeric loopback/localhost is accepted; proxies and redirects are disabled. A server bind value such as `OLLAMA_HOST=0.0.0.0` is not a client destination. Override it with `--ollama-url http://127.0.0.1:11434`, or launch the GUI with `env -u OLLAMA_HOST bash scripts/run-python.sh`.

`--timeout` is 1–600 seconds per attempt, default 180, with three attempts for retryable failures. Indexing keeps the model alive for ten minutes; individual searches request one minute. Existing Ollama residency can avoid reloading even though the client is a new process.

## Cleanup and mounted sources

```bash
bash scripts/python-cli.sh prune --db /path/to/index-v3.sqlite3 --dry-run
bash scripts/python-cli.sh prune --db /path/to/index-v3.sqlite3 --apply-prune TOKEN_FROM_PREVIEW
```

Plain `prune` is preview-only. Review the root and file/document/chunk/error counts before applying its exact token. Applying recomputes the plan in a transaction and refuses stale tokens.

A successful scan records the containing filesystem and mounted sources at or below the root from Linux `/proc/self/mountinfo`, including bind mounts on the same device. Mountpoint, filesystem root/type/source, device, and mountpoint inode are checked. Unavailable sources, changed identities/topology, unreadable mount information, incomplete scans, and pending relocation block cleanup. Indexing also validates the prior baseline before scanning, so an ordinary rescan cannot silently accept a vanished nested mount.

Checks run again at scan completion and during cleanup. Restoring the original sources is the normal recovery. If storage identities legitimately changed, use a verified relocation as below; all previously indexed document contents must be present. Network storage can hang until the OS returns an error. These checks do not constitute a filesystem snapshot or an adversarial race-proof guarantee. Keep storage stable during maintenance. See [SECURITY.md](SECURITY.md).

Cleanup removes missing aliases, unreferenced documents/chunks, and obsolete errors independently. SQLite reuses released pages; `VACUUM` is a separate optional operation with the app closed. ANN caches become stale when their indexed data changes and must be rebuilt.

## Relocate the PDF root

```bash
bash scripts/python-cli.sh index --root /path/to/moved/ArxivShelf \
  --db /path/to/index-v3.sqlite3 --relocate-root
```

Use the existing model/chunk/overlap/OCR settings. Aliases are staged while existing vectors are reused by hash. Every previously indexed document needs a matching copy; staged PDFs are rehashed and mounted sources revalidated before the alias/root/baseline switch commits atomically. Failed/interrupted relocation preserves the original aliases and blocks cleanup. Resume the command, or cancel staging:

```bash
bash scripts/python-cli.sh cancel-relocation --db /path/to/index-v3.sqlite3
```

The same relocation flag can verify a legitimate source-identity change at the same root path. It does not permit an empty replacement to erase existing documents. If PDFs were physically moved, old paths are naturally unavailable until relocation completes or the old directory is restored.

## Optional FAISS retrieval

Exact NumPy/SQLite search remains the low-memory default. Install the optional CPU dependency and build a cache:

```bash
.venv/bin/python -m pip install -r python/requirements-ann.txt
bash scripts/python-cli.sh build-search-index --db /path/to/index-v3.sqlite3
bash scripts/python-cli.sh search --db /path/to/index-v3.sqlite3 \
  --backend faiss --query 'robot motion' --k 8
```

Subject filters require their own cache:

```bash
bash scripts/python-cli.sh build-search-index --db /path/to/index-v3.sqlite3 --subject Sound
bash scripts/python-cli.sh search --db /path/to/index-v3.sqlite3 \
  --backend faiss --subject Sound --query 'speech recognition'
```

Enable it for the existing GUI with `RAG_SEARCH_BACKEND=faiss bash scripts/run-python.sh`. Build every subject cache you intend to use. Rebuild after additions, changes, cleanup, or relocation. A stale/missing cache produces an actionable error; it is not silently used. Unchanged scans do not invalidate caches.

SQLite remains authoritative. FAISS caches contain HNSW vectors and a chunk-ID map. Publication commits the manifest after private cache files are written. File digests, ownership, permissions, dimensions, and SQLite revision are checked before loading. Do not import untrusted databases/caches; native FAISS deserialization is not a hostile-file sandbox.

HNSW uses M=16, construction ef=80, search ef=96 by default, and two CPU threads. CLI `--ann-ef-search` accepts 16–65536; increasing it usually trades latency for recall but does not guarantee exact results. Candidates are rescored against SQLite vectors and checked for live sources. Insufficient available candidates trigger exact fallback. Approximate rankings can miss relevant neighbors; compare relevance metrics with exact mode.

`--ann-memory-mib` defaults to 512 for build/search. A conservative estimate rejects oversized caches before allocation. This is an estimate, not an OS memory cap; native allocator overhead varies. HNSW stores full float vectors in RAM, so it is not the minimum-memory option. A new CLI process verifies and loads the cache from disk each time, including a private verified copy before native deserialization. Cold-start I/O remains proportional to cache size; no end-to-end speedup is claimed. Repeated evaluation queries reuse a loaded cache within their process. Interrupted builds can leave unreferenced files in the `.ann` directory; with the app closed, that disposable directory can be removed and rebuilt.

## Optional PDF isolation and OCR

Install stronger isolation:

```bash
sudo apt install -y bubblewrap
RAG_PDF_SANDBOX=bubblewrap bash scripts/run-python.sh
```

For CLI indexing, use `--pdf-sandbox bubblewrap`. The sandbox exposes readonly system runtime/fonts and only the selected input PDF, plus a private writable work directory. It unshares network/process/user/IPC namespaces, drops capabilities, clears the environment, and keeps the extraction resource limits. Home, the database, and the rest of the PDF library are not mounted. Ubuntu user-namespace/AppArmor policy may block Bubblewrap; the job fails explicitly and never falls back to unisolated processing. `limits` remains the compatibility default. This is namespace/filesystem isolation, not a custom syscall allowlist or a virtual machine.

Optional OCR:

```bash
sudo apt install -y tesseract-ocr tesseract-ocr-eng
bash scripts/python-cli.sh index --root /path/to/papers \
  --db /path/to/index-ocr-eng.sqlite3 --ocr-language eng --pdf-sandbox bubblewrap
```

For the desktop, set `RAG_OCR_LANGUAGE=eng` and choose a new database. OCR language belongs to the index profile; do not mix text-only and OCR extraction in one existing index. Additional installed Tesseract language packs can be selected with codes such as `eng+vie`.

Only pages whose extracted text is empty receive OCR. Existing text/page numbers are retained. Rendering handles one page at a time, limited to a 2400-pixel long side. It does not detect poor existing OCR, sparse text over an image, or recognize equations reliably. No OCR correction UI is included.

Every parser/OCR process retains 2 GiB virtual-address, 180-second CPU/wall, and 128 MiB per-file output limits. Text is capped at 4 MiB per page. Limits are per process/output file, not a complete aggregate disk quota; a long scanned document can require many bounded calls. Forced termination can leave private temporary files. Large valid PDFs may exceed limits and remain eligible for retry.

## Retrieval benchmark and live integration

See [benchmarks/README.md](benchmarks/README.md). The included fixed starter dataset has 16 questions about four research papers, document-level grades, and subject-filter cases. The papers/model weights are not redistributed. You supply the PDFs locally, lock their hashes, index them, then run:

```bash
bash scripts/python-cli.sh evaluate --db /path/to/benchmark.sqlite3 \
  --dataset /path/to/locked-benchmark.json --k 3 > evaluation.json
```

The report includes document-level Recall@K, MRR@K, nDCG@K, per-subject means, elapsed query time, corpus hashes, and model profile. Exact evaluation scans all eligible chunks and ranks each unique PDF by its maximum chunk cosine similarity before choosing K. FAISS expands candidates up to `--candidate-depth` (default 4096), deduplicates before K, and falls back to exact document retrieval when too few available documents are found. Per-query diagnostics report the actual method and candidate counts. This fallback does not detect every approximate ranking miss. The small starter is not an independently adjudicated research benchmark, and no live relevance score is claimed in this release.

A separate test uses your existing live Ollama server without downloading models or changing your real index:

```bash
PYTHONPATH=python .venv/bin/python tests/live_ollama.py \
  --model 'nomic-embed-text-v2-moe:latest' --timeout 60
```

It checks batched inference, real PDF extraction, indexing, deduplication, and retrieval in a temporary database. To exercise installed optional features together:

```bash
PYTHONPATH=python .venv/bin/python tests/live_ollama.py \
  --model 'nomic-embed-text-v2-moe:latest' --faiss --ocr-language eng \
  --pdf-sandbox bubblewrap --timeout 60

xvfb-run -a .venv/bin/python tests/ui_smoke.py \
  --live-model 'nomic-embed-text-v2-moe:latest' --timeout 300
```

The GUI check uses exact search and resource limits; the first command checks the optional backend/OCR/sandbox combination. Failures are reported without silently disabling requested features. Both use temporary test data and leave your real index unchanged. No model is downloaded. A passing result establishes integration, not research-paper relevance or GPU memory suitability.

## Agents and automation

Hermes, OpenClaw, or a host-local n8n runner can invoke the CLI and parse JSON Lines. Use argument arrays for dynamic queries; never concatenate untrusted questions into shell commands. Treat retrieved passages as untrusted data, cite path/page, and do not execute instructions found in PDFs.

For an n8n Execute Command node running on the Ubuntu host, invoke the absolute wrapper path. A Code node can parse stdout:

```javascript
const lines = $json.stdout.trim();
return lines ? lines.split('\n').map(line => ({ json: JSON.parse(line) })) : [];
```

Containerized n8n runs commands inside its container. This app supplies no LAN API or container bridge. The runner needs local access to the PDFs, database, runtime, and loopback Ollama endpoint.

## Architecture and verification

| Module | Responsibility |
|---|---|
| `domain.py`, `limits.py` | Profiles, interfaces, normalization, chunking, errors, limits |
| `adapters.py` | Loopback Ollama HTTP and streamed Poppler text |
| `processes.py`, `ocr.py` | Resource limits, optional isolation, optional OCR |
| `filesystem.py` | Mounted-source capture and validation |
| `storage.py` | SQLite persistence, transactions, metadata, cache revisions; compatibility delegates |
| `root_manager.py`, `cleanup.py` | Root binding/relocation and guarded cleanup planning/application |
| `service.py`, `ranking.py` | Index orchestration, bounded document ranking, exact retrieval |
| `checkpoints.py`, `lifecycle.py` | Batch recovery, atomic publication, iterator cleanup |
| `retrieval.py` | Optional disposable FAISS cache and candidate retrieval |
| `evaluation.py` | Document ranking metrics and reproducible reports |
| `viewer.py` | Safe PDF viewer argument construction and page navigation |
| `__main__.py`, `ui/app.tcl` | CLI commands and existing native UI |

The default exact search scans matching vectors in batches of up to 2048, capped to an 8 MiB matrix. Other Python/SQLite objects use additional memory. Raw vectors occupy roughly `chunks × dimensions × 4 bytes`, before text/database overhead. There is no resident search server or automatic reranker. Pending embeddings are stored separately and become searchable only when the document is complete.

```bash
PYTHONPATH=python .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
sudo apt install -y xvfb
xvfb-run -a .venv/bin/python tests/ui_smoke.py
python3 scripts/measure-memory.py -- bash scripts/run-python.sh
```

FAISS/OCR tests skip when their optional dependencies are absent; CI installs them. The memory helper measures app/child RSS and PSS, excluding the independent Ollama server and GPU memory. See [VALIDATION.md](VALIDATION.md) for actual results and remaining unverified integrations.

## Troubleshooting

| Message/symptom | Action |
|---|---|
| Missing mounted-source baseline | Restore all storage and perform the documented one-time adoption |
| Mounted sources changed | Restore mounts, or deliberately relocate after making all indexed content available |
| Circuit breaker opened | Restore Ollama/GPU availability; rerun without a retry flag |
| Profile differs | Match the existing configuration or choose a fresh database |
| ANN cache missing/stale | Rebuild for the same subject, or choose exact mode |
| ANN budget exceeded | Use exact mode or explicitly increase the budget |
| Bubblewrap unavailable | Resolve host namespace policy; no automatic downgrade occurs |
| OCR language missing | Install the matching Tesseract language pack |
| No text | Enable OCR in a separate index, or OCR the original PDF externally |
| Index in use | Wait for the current job or stop it cleanly |
| Missing wish/prlimit/pdftotext | Install tk/util-linux/poppler-utils respectively |
| No display | Use the CLI |

To uninstall, close jobs and remove the `pdf-rag-shelf` program directory and optional launcher files `~/.local/bin/pdf-rag-shelf-python` and `~/.local/share/applications/pdf-rag-shelf-python.desktop`. PDFs, indexes, settings, and Ollama models are separate.

## Contributing, license, and references

See [CONTRIBUTING.md](CONTRIBUTING.md) and [LICENSE](LICENSE). Software licensing does not change PDF or model licenses. This is an independent project.

- [Ollama embedding API](https://docs.ollama.com/api/embed)
- [FAISS index selection](https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index)
- [FAISS index I/O and trust warning](https://github.com/facebookresearch/faiss/wiki/Index-IO%2C-cloning-and-hyper-parameter-tuning)
- [Bubblewrap design and limitations](https://github.com/containers/bubblewrap)
- Research-paper sources are linked in [the benchmark dataset](benchmarks/research-papers-v1.json).
