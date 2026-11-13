# Executed synthetic retrieval benchmarks

Measured during v1.4.0 development on 12–13 November 2026. Exact search remains the default: the low-effort ANN configuration missed many exact neighbors on this dataset.

## Method

- Linux container, Intel Xeon Platinum 8573C, 9 reported CPUs, approximately 9.7 GiB visible RAM; Python 3.12.14, NumPy 2.3.5, FAISS CPU 1.13.0. This is not the user's i7/RTX 3050 machine.
- Independent isotropic random unit vectors, 128 dimensions, seed 1400; 100 chunks per synthetic document. These are deliberately structure-free vectors, not model embeddings or a realistic semantic corpus.
- Eight queries per size, document K=10. Exact reference scans all vectors and uses maximum chunk cosine per document. Recall is the mean overlap with the exact top ten documents, not relevance recall against human judgments.
- HNSW M=16, efConstruction=80, two native threads; BLAS uses one thread. Candidate depth is 4096. The benchmark explicitly allows a 4096 MiB cache budget; the app default remains 512 MiB.
- Separate processes for preparation, index build, exact queries, and ANN queries. First-query time includes cache verification/copy/load for ANN but excludes Python startup and Ollama inference. The seven later queries use different query vectors and reuse the loaded index. OS filesystem caches were not flushed.
- Peak RSS is the worker process high-water mark, including Python and native allocations. It excludes the parent, page cache, Ollama, and GPU memory. Effort-sweep RSS is cumulative within its ANN worker, not isolated per setting.
- Eight queries are too few for stable tail-latency estimates. Raw query timings and rankings are retained; p95 is descriptive, not an SLO. Two-thread HNSW build scheduling and shared-host load can change recall/timing across runs.

## Main run: default efSearch=96

| Chunks | Build (s) | Build peak MiB | Exact first (ms) | Exact warm median / p95 (ms) | ANN first (ms) | ANN warm median / p95 (ms) | Exact / ANN peak MiB | Recall@10 vs exact |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10,000 | 1.89 | 53.6 | 35.9 | 30.38 / 34.76 | 126.4 | 2.32 / 3.60 | 37.5 / 49.2 | 0.8625 |
| 100,000 | 34.14 | 130.1 | 252.0 | 253.63 / 259.64 | 347.8 | 2.19 / 3.57 | 37.7 / 107.0 | 0.2125 |
| 1,000,000 | 465.61 | 762.9 | 3289.4 | 3180.83 / 3420.17 | 3823.7 | 4.43 / 7.79 | 37.8 / 678.6 | 0.1000 |

Raw report: [synthetic-128d.json](results/synthetic-128d.json). Its `prepare.database_bytes` measures only the main SQLite file at that point, excluding any WAL; do not interpret it as total disk usage.

## Search-effort sweep

The 10K and 100K corpora were rebuilt in a second run to add the effort sweep. The 1M sweep reused the main run's cache. These rows are not all from the same build as the main table. Within each size, effort settings share the same cache and query vectors.

| Chunks | efSearch | Warm median (ms) | Warm p95 (ms) | Recall@10 vs exact |
|---:|---:|---:|---:|---:|
| 10,000 | 96 | 1.32 | 1.53 | 0.8375 |
| 10,000 | 512 | 1.62 | 2.25 | 0.9875 |
| 10,000 | 2048 | 3.11 | 3.47 | 1.0000 |
| 100,000 | 96 | 1.66 | 1.88 | 0.2500 |
| 100,000 | 512 | 21.73 | 48.10 | 0.7625 |
| 100,000 | 2048 | 11.28 | 12.38 | 0.9750 |
| 1,000,000 | 96 | 4.43 | 7.79 | 0.1000 |
| 1,000,000 | 512 | 9.09 | 38.34 | 0.2750 |
| 1,000,000 | 2048 | 11.73 | 13.49 | 0.6000 |

Additional raw report: [synthetic-128d-effort.json](results/synthetic-128d-effort.json). All reported comparisons can be recomputed from the stored rankings. The 100K ef=512 timing was slower than ef=2048 in this short run; host noise makes these timings unsuitable for a monotonic latency claim.

## Interpretation

ANN has much lower repeated-query latency, but that speed comes with substantial recall loss at ef=96. At 1M chunks, increasing ef to 2048 improved overlap from 0.10 to 0.60, still far from exact. First ANN query latency at 1M was higher than the first exact query because cache loading dominates. The desktop launches a new worker per search, so warm in-process timings do not describe its full click-to-result latency.

Use exact mode when complete nearest-neighbor ranking matters. Tune and evaluate ANN on the actual embedding distribution before enabling it. These results do not establish semantic relevance, answer quality, GPU suitability, or performance at 768/4096 dimensions. No such live-model claim is made.

Reproduction commands and the separate human-labeled starter evaluation are in [README.md](README.md).
