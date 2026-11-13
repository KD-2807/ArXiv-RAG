from collections import defaultdict
from pathlib import Path
import hashlib
import json
import math
import time
from .domain import checksum
from .limits import MAX_SEARCH_RESULTS


def ranking_metrics(ranking: list[str], relevance: dict[str, int], k: int) -> dict[str, float]:
    if k < 1 or not relevance or any(not isinstance(grade, int) or not 0 <= grade <= 3 for grade in relevance.values()):
        raise ValueError('Metrics require positive K and relevance grades from 0 to 3.')
    relevant = {key for key, grade in relevance.items() if grade > 0}
    if not relevant:
        raise ValueError('Each query needs at least one relevant document.')
    unique = list(dict.fromkeys(ranking))[:k]
    recall = len(set(unique) & relevant) / len(relevant)
    reciprocal = next((1 / rank for rank, key in enumerate(unique, 1) if key in relevant), 0.0)
    gain = sum((2 ** relevance.get(key, 0) - 1) / math.log2(rank + 1) for rank, key in enumerate(unique, 1))
    ideal = sum((2 ** grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(sorted(relevance.values(), reverse=True)[:k], 1))
    return {'recall_at_k': recall, 'mrr_at_k': reciprocal, 'ndcg_at_k': gain / ideal}


class RetrievalEvaluator:
    def __init__(self, repository, search):
        self.repository, self.search = repository, search

    def _documents(self, dataset: dict) -> dict[str, str]:
        root = Path(self.repository.get('root'))
        digests: dict[str, str] = {}
        for item in dataset['documents']:
            relative = Path(item['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Benchmark paths must be relative to the indexed root.')
            path = (root / relative).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError('Benchmark PDF resolved outside the indexed root.')
            digest = checksum(path)
            if item.get('sha256') and item['sha256'] != digest:
                raise ValueError(f'Benchmark corpus hash mismatch: {relative}')
            row = self.repository.db.execute('SELECT hash FROM files WHERE path=?', (str(path),)).fetchone()
            if row is None or row[0] != digest or not self.repository.has(digest):
                raise ValueError(f'Benchmark PDF is missing, changed, or not indexed: {relative}')
            if item['id'] in digests or digest in digests.values():
                raise ValueError('Benchmark document IDs and contents must be unique.')
            digests[item['id']] = digest
        return digests

    def evaluate(self, path: Path, k: int, candidate_depth: int = 4096) -> dict:
        if not 1 <= k <= MAX_SEARCH_RESULTS:
            raise ValueError('Evaluation K must be 1–100.')
        data = path.read_bytes()
        dataset = json.loads(data)
        if dataset.get('version') != 1 or not dataset.get('queries'):
            raise ValueError('Use benchmark format version 1 with at least one query.')
        digests = self._documents(dataset)
        reverse = {digest: identifier for identifier, digest in digests.items()}
        results, groups, query_ids = [], defaultdict(list), set()
        for query in dataset['queries']:
            if query['id'] in query_ids:
                raise ValueError('Benchmark query IDs must be unique.')
            query_ids.add(query['id'])
            if any(key not in digests for key in query['relevance']):
                raise ValueError('Every relevance label must reference a benchmark document.')
            ranking_metrics([], query['relevance'], k)
            subject = query.get('subject', '')
            if subject:
                for identifier, grade in query['relevance'].items():
                    if grade and not self.repository.sources(digests[identifier], subject):
                        raise ValueError('A relevant benchmark document is excluded by its subject filter.')
            start = time.perf_counter()
            hits = self.search.search_documents(query['query'], k, subject, candidate_depth)
            elapsed = time.perf_counter() - start
            ranking = list(dict.fromkeys(reverse.get(hit['document_id'], hit['document_id']) for hit in hits))
            scores = ranking_metrics(ranking, query['relevance'], k)
            row = {'id': query['id'], 'subject': subject, 'ranking': ranking[:k], 'seconds': elapsed, 'retrieval': dict(self.search.last_search), **scores}
            results.append(row)
            groups[subject or '(all subjects)'].append(scores)
        def average(rows):
            return {key: sum(row[key] for row in rows) / len(rows) for key in ('recall_at_k', 'mrr_at_k', 'ndcg_at_k')}
        return {'dataset': dataset.get('name', path.name), 'dataset_sha256': hashlib.sha256(data).hexdigest(),
                'corpus_sha256': digests, 'profile': json.loads(self.repository.get('profile')),
                'k': k, 'ranking_unit': 'document', 'document_score': 'maximum chunk cosine similarity',
                'ann_candidate_depth': candidate_depth, 'queries': results,
                'macro': average(results), 'by_subject': {key: average(rows) for key, rows in groups.items()},
                'latency_mean_seconds': sum(row['seconds'] for row in results) / len(results),
                'judgments': dataset.get('judgments', 'User supplied; not independently validated.')}
