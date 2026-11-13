# Review resolution for 1.4.1

| Requested item | Change | Verification |
|---|---|---|
| 1: Two-column extraction | Remove `-layout`; use Poppler reading-order mode; pipeline v3 prevents silently mixing old/new embeddings | Synthetic two-column PDF: 0/6 adjacent within-column line pairs preserved before, 6/6 after; page numbering retained |
| 3: Oversized Repository | Compose RootManager and CleanupService; Repository retains persistence plus public delegation methods | Existing relocation, mount, cleanup preview, mid-operation rollback and checkpoint tests retained |
| 4: Version-named tests | Functional modules for mounts, FAISS, evaluation, OCR, isolation/presentation, checkpoints, document search and lifecycle; shared support fixtures | Original 122 tests retained; three new regression tests added |
| 5: Dynamic counters / redundant initialization | IndexOutcome enum with explicit counter updates; initialize consumed/empty/saved once | All outcomes and invalid input exercised |

Poppler reading order remains heuristic: wide paragraph gaps, complex layouts, tables and equations may still be misordered. The extraction regression measures line adjacency on a known fixture, not live embedding relevance. Item 2 (real-model retrieval evaluation) was not requested in this patch and remains unverified.

Existing v1/v2 indexes remain searchable. A new v3 database and re-embedding are required to benefit from corrected extraction. No old index is automatically rebuilt or deleted. The native UI layout is retained; fresh settings use a v3 database filename.
