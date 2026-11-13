import argparse
from pathlib import Path
import subprocess
import time


def descendants(pid):
    result = {pid}
    for status in Path('/proc').glob('[0-9]*/status'):
        try:
            fields = dict(line.split(':', 1) for line in status.read_text().splitlines() if ':' in line)
            parent = int(fields['PPid'])
            if parent in result:
                result.add(int(status.parent.name))
        except (OSError, KeyError, ValueError):
            continue
    previous = set()
    while result != previous:
        previous = result.copy()
        for parent in previous:
            try:
                children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
                result.update(map(int, children))
            except OSError:
                pass
    return result


def memory(pid):
    totals = {'Rss': 0, 'Pss': 0}
    for member in descendants(pid):
        try:
            lines = Path(f'/proc/{member}/smaps_rollup').read_text().splitlines()
            for line in lines:
                key = line.split(':')[0]
                if key in totals:
                    totals[key] += int(line.split()[1])
        except OSError:
            pass
    return totals


def main():
    parser = argparse.ArgumentParser(description='Measure the entire process tree; close the app to finish.')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('Provide a command after --')
    process = subprocess.Popen(command)
    peak_rss = peak_pss = 0
    try:
        while process.poll() is None:
            values = memory(process.pid)
            peak_rss = max(peak_rss, values['Rss'])
            peak_pss = max(peak_pss, values['Pss'])
            print(f"RSS {values['Rss'] / 1024:.1f} MiB | PSS {values['Pss'] / 1024:.1f} MiB", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
    print(f'Peak sampled RSS: {peak_rss / 1024:.1f} MiB; PSS: {peak_pss / 1024:.1f} MiB')


if __name__ == '__main__':
    main()
