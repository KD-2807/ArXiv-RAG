from pathlib import Path
import os
import subprocess
import sys
import tempfile

project = Path(__file__).resolve().parents[1]
fake_wish = '''#!/usr/bin/env python3
import base64
import sys
fields = sys.stdin.readline().rstrip('\\n').split('\\t')
assert fields[0] == 'init'
assert base64.b64decode(fields[6]).decode() in ('Python', 'Rust')
print('save\\t' + '\\t'.join(fields[1:6] + fields[7:]), flush=True)
while True:
    line = sys.stdin.readline()
    if not line:
        break
    if line.startswith('log\\t') and 'Settings saved.' in base64.b64decode(line.split('\\t')[1]).decode():
        print('quit', flush=True)
        sys.stdin.read()
        break
'''
for variant, command in [('python', [sys.executable, '-m', 'arxiv_shelf']), ('rust', [str(project / 'rust/target/release/arxiv-shelf')])]:
    if variant == 'rust' and not Path(command[0]).exists():
        raise SystemExit('Build the Rust release binary before running this test.')
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        wish = root / 'wish'
        wish.write_text(fake_wish)
        wish.chmod(0o755)
        env = dict(os.environ, PATH=directory + ':' + os.environ['PATH'], PYTHONPATH=str(project / 'python'), XDG_CONFIG_HOME=str(root / 'config'))
        subprocess.run(command, env=env, check=True, timeout=15)
        config = root / 'config' / f'arxiv-shelf-{variant}' / 'settings.json'
        assert config.exists(), config
        print(f'{variant}: GUI adapter init, save, settings persistence and quit passed.')
