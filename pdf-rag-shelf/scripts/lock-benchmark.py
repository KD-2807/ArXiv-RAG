from pathlib import Path
import argparse
import hashlib
import json
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve(strict=True)
    dataset = json.loads(args.dataset.read_text())
    for document in dataset['documents']:
        relative = Path(document['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Benchmark paths must be relative to the corpus root.')
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError('PDF resolves outside the corpus root.')
        with path.open('rb') as stream:
            document['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(dataset, output, indent=2)
        output.write('\n')
    print(args.output)


if __name__ == '__main__':
    main()
