# Retrieval evaluation

`research-papers-v1.json` is a fixed starter with 16 queries about Attention, LoRA, CLIP, and Whisper. Questions and document-level relevance grades were manually authored from the papers' topics. They are not independently adjudicated judgments. Four subject-filter cases check filter behavior; single-document subjects cannot establish ranking quality. Use the unfiltered cases and expand the corpus with judged distractors before drawing conclusions.

## Prepare the corpus

Download the four papers from the primary sources recorded in the manifest, or use existing local copies. The app does not download them. Arrange them under a separate root:

| Relative path | Paper |
|---|---|
| `NLP/1706.03762.pdf` | Attention Is All You Need |
| `NLP/2106.09685.pdf` | LoRA: Low-Rank Adaptation of Large Language Models |
| `Vision/2103.00020.pdf` | Learning Transferable Visual Models From Natural Language Supervision |
| `Sound/2212.04356.pdf` | Robust Speech Recognition via Large-Scale Weak Supervision |

ArXiv revisions can differ. Fix the actual files by producing a hash-locked manifest, retain those files unchanged, and share the manifest with any comparison results:

```bash
.venv/bin/python scripts/lock-benchmark.py \
  --dataset benchmarks/research-papers-v1.json \
  --root "$HOME/Documents/rag-benchmark" \
  --output benchmarks/research-papers-locked.json

bash scripts/python-cli.sh index \
  --root "$HOME/Documents/rag-benchmark" \
  --db "$HOME/.local/share/pdf-rag-shelf-python/benchmark.sqlite3" \
  --model 'nomic-embed-text-v2-moe:latest'

bash scripts/python-cli.sh evaluate \
  --db "$HOME/.local/share/pdf-rag-shelf-python/benchmark.sqlite3" \
  --dataset benchmarks/research-papers-locked.json --k 3 > exact-report.json
```

The lock command refuses to overwrite an existing output. Inspect the paper identities before locking: a hash proves the bytes match subsequent runs, not that an arbitrary renamed file is the intended paper. The evaluator verifies the indexed aliases still match those bytes. No embeddings or relevance scores are supplied with this package.

For FAISS comparison, build caches for the empty subject and each used subject (`NLP`, `Vision`, `Sound`), then repeat `evaluate` with `--backend faiss`. Keep model digest, extraction/OCR profile, corpus hashes, dataset, K, and hardware constant. Native ANN load/verification is included in the first applicable query's time; subsequent queries reuse the latest loaded subject cache. Report cold and repeated timings separately when studying deployment latency.

## Metrics

Rankings are document-level. Exact search scans all eligible vectors, using each document's maximum chunk cosine similarity and deduplicating before selecting K. There is no 100-chunk ceiling. FAISS expands candidates up to `--candidate-depth` (default 4096) and falls back to exact ranking if it cannot find K available documents. Approximate rankings can still miss relevant documents even when K results are returned. Set `--ann-ef-search` to tune HNSW search effort and inspect per-query method/candidate diagnostics. Ties use descending representative chunk ID, matching passage search.

- Recall@K: relevant documents retrieved divided by all positively judged documents for that query.
- MRR@K: reciprocal of the first relevant document rank, or zero if absent; macro-averaged over queries.
- nDCG@K: discounted gain using `(2^grade - 1) / log2(rank + 1)`, normalized by the ideal ranking from all query judgments.
- Grades are integers 0–3. Unjudged retrieved documents count as nonrelevant; duplicate hits cannot inflate scores.
- Reports include each query, subject-group means, overall macro means, mean query time, corpus hashes, dataset hash, and model profile.

For a meaningful study, add queries that require distinguishing similar papers, independent relevance judgments (including multiple relevant documents), distractors, and a held-out query set. Document-level relevance does not prove that a specific passage supports an answer. This starter measures neither generated-answer faithfulness nor OCR accuracy.

## Synthetic speed, RAM, and nearest-neighbor recall

[RESULTS.md](RESULTS.md) records executed 10K, 100K, and 1M chunk measurements with raw JSON reports. These use random vectors, not inferred research-paper embeddings; nearest-neighbor recall against exact search is distinct from labeled relevance.

```bash
PYTHONPATH=python .venv/bin/python scripts/benchmark-search.py \
  --sizes 10000 100000 1000000 --dimensions 128 --queries 8 \
  --efforts 96 512 2048 --output search-benchmark.json
```

Use `--dimensions 768` and more queries for a more expensive follow-up appropriate to a chosen model. `--unit chunks` measures passage retrieval; the default is documents. Build and query phases use separate processes. The temporary corpus is removed after each size. The memory budget defaults to 4096 MiB for this opt-in benchmark, not the app's 512 MiB default. Larger dimensions/corpora require more disk, RAM, and build time.
