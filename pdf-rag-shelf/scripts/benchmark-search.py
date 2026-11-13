from pathlib import Path
import argparse
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'python'))


def worker(args):
    import numpy as np
    from rag_shelf.domain import Profile
    from rag_shelf.storage import Repository
    from rag_shelf.service import SearchService
    from rag_shelf.retrieval import FaissBackend
    base = Path(args.workspace)
    database = base / 'benchmark.sqlite3'
    start = time.perf_counter()
    repository = Repository(database)
    try:
        if args.phase == 'prepare':
            root = base / 'pdfs'
            root.mkdir()
            repository.bind(Profile('synthetic-benchmark', 'seed-' + str(args.seed)), root)
            generator = np.random.default_rng(args.seed)
            for offset in range(0, args.size, 8192):
                count = min(8192, args.size - offset)
                vectors = generator.normal(size=(count, args.dimensions)).astype('<f4')
                vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
                with repository.transaction():
                    for row, vector in enumerate(vectors):
                        identifier = offset + row
                        document = identifier // 100
                        digest = f'{document:064x}'
                        if identifier % 100 == 0:
                            path = root / f'{document}.pdf'
                            path.write_bytes(b'Synthetic source availability placeholder')
                            repository.finish(digest)
                            repository.attach(path, digest, 'Synthetic')
                        repository.add(digest, identifier % 100 + 1, 'Synthetic benchmark vector', vector.tobytes())
            return {'seconds': time.perf_counter() - start, 'database_bytes': database.stat().st_size,
                    'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
        if args.phase == 'build':
            result = FaissBackend(repository, args.memory_mib).build()
            return {**result, 'seconds': time.perf_counter() - start,
                    'cache_bytes': sum(path.stat().st_size for path in Path(str(database) + '.ann').iterdir()),
                    'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
        vectors = np.random.default_rng(args.seed + 100).normal(size=(args.queries, args.dimensions)).astype('<f4')
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        class QueryVectors:
            def embed(self, texts):
                return [vectors[int(text)].tolist() for text in texts]
        def query_run(effort):
            backend = FaissBackend(repository, args.memory_mib, effort) if args.phase == 'ann' else None
            service = SearchService(QueryVectors(), repository, backend)
            timings, rankings, diagnostics = [], [], []
            for query in range(args.queries):
                begin = time.perf_counter()
                if args.unit == 'documents':
                    hits = service.search_documents(str(query), args.k, candidate_depth=args.candidate_depth)
                    identifiers = [hit['document_id'] for hit in hits]
                else:
                    hits = service.search(str(query), args.k)
                    identifiers = [hit['chunk_id'] for hit in hits]
                timings.append(time.perf_counter() - begin)
                rankings.append(identifiers)
                diagnostics.append(dict(service.last_search))
            repeated = timings[1:] or timings
            return {'ef_search': effort if backend else None,
                    'first_query_seconds': timings[0], 'repeated_median_seconds': statistics.median(repeated),
                    'repeated_p95_seconds': float(np.percentile(repeated, 95)), 'query_seconds': timings,
                    'rankings': rankings, 'diagnostics': diagnostics,
                    'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
        result = query_run(96)
        if args.phase == 'ann':
            result['effort_sweep'] = [query_run(effort) for effort in args.efforts if effort != 96]
        return result
    finally:
        repository.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sizes', type=int, nargs='+', default=[10000, 100000, 1000000])
    parser.add_argument('--dimensions', type=int, default=128)
    parser.add_argument('--queries', type=int, default=8)
    parser.add_argument('--k', type=int, default=10)
    parser.add_argument('--unit', choices=['documents', 'chunks'], default='documents')
    parser.add_argument('--seed', type=int, default=1400)
    parser.add_argument('--memory-mib', type=int, default=4096)
    parser.add_argument('--candidate-depth', type=int, default=4096)
    parser.add_argument('--efforts', type=int, nargs='+', default=[96, 512, 2048])
    parser.add_argument('--output', type=Path, default=Path('search-benchmark.json'))
    parser.add_argument('--phase', choices=['prepare', 'build', 'exact', 'ann'])
    parser.add_argument('--workspace')
    parser.add_argument('--size', type=int)
    args = parser.parse_args()
    if args.phase:
        print(json.dumps(worker(args)))
        return
    if min(args.sizes) < args.k * 100 or args.queries < 2 or not 1 <= args.k <= 100 or not 2 <= args.dimensions <= 4096:
        parser.error('Use at least K*100 chunks, 2 queries, K in 1–100, and dimensions in 2–4096.')
    import numpy as np
    import faiss
    report = {'format': 1, 'data': 'Seeded independent isotropic unit vectors; 100 chunks per synthetic document. No PDF inference or semantic relevance labels.',
              'platform': platform.platform(), 'python': platform.python_version(), 'cpu_count': os.cpu_count(),
              'numpy': np.__version__, 'faiss': faiss.__version__, 'faiss_threads': 2,
              'hnsw': {'M': 16, 'efConstruction': 80, 'efSearch': 96},
              'dimensions': args.dimensions, 'queries': args.queries, 'k': args.k, 'seed': args.seed,
              'unit': args.unit, 'candidate_depth': args.candidate_depth, 'results': []}
    cpuinfo = Path('/proc/cpuinfo').read_text()
    report['cpu_model'] = next((line.split(':', 1)[1].strip() for line in cpuinfo.splitlines() if line.startswith('model name')), 'unknown')
    report['memory_total'] = Path('/proc/meminfo').read_text().splitlines()[0]
    for size in args.sizes:
        result = {'chunks': size}
        with tempfile.TemporaryDirectory(prefix='rag-benchmark-') as temporary:
            for phase in ('prepare', 'build', 'exact', 'ann'):
                print(f'{size} chunks: {phase}', file=sys.stderr, flush=True)
                command = [sys.executable, str(Path(__file__).resolve()), '--phase', phase, '--workspace', temporary,
                           '--size', str(size), '--dimensions', str(args.dimensions), '--queries', str(args.queries),
                           '--k', str(args.k), '--unit', args.unit, '--seed', str(args.seed), '--memory-mib', str(args.memory_mib),
                           '--candidate-depth', str(args.candidate_depth), '--efforts', *map(str, args.efforts)]
                execution = subprocess.run(command, check=True, capture_output=True, text=True,
                                           env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '2'})
                result[phase] = json.loads(execution.stdout)
        recalls = [len(set(actual) & set(expected)) / len(expected)
                   for actual, expected in zip(result['ann']['rankings'], result['exact']['rankings'])]
        result['ann_recall_against_exact_at_k'] = statistics.mean(recalls)
        result['recall_by_query'] = recalls
        for setting in result['ann'].get('effort_sweep', []):
            setting['recall_against_exact_at_k'] = statistics.mean(
                len(set(actual) & set(expected)) / len(expected)
                for actual, expected in zip(setting['rankings'], result['exact']['rankings']))
        report['results'].append(result)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(f'{size} chunks: recall={result["ann_recall_against_exact_at_k"]:.4f}', file=sys.stderr, flush=True)
    print(args.output)


if __name__ == '__main__':
    main()
