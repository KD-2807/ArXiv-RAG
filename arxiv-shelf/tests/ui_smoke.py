from pathlib import Path
import base64
import os
import subprocess
import tempfile

project = Path(__file__).resolve().parents[1]
source = (project / 'ui/shelf.tcl').read_text()
probe = '''
after 300 {
    if {[winfo manager .body.keywords] ne $expectKeywordManager} {exit 6}
    if {$keywordsEnabled && [.body.keywords get 1.0 end-1c] ne "connectome=Connectome"} {exit 7}
    if {$root ne "/tmp/arxiv-test"} {puts stderr "INIT FAILED"; exit 2}
    if {[.body.subjects get 1.0 end-1c] ne "cs.SD=Sound"} {exit 3}
    .body.actions.save invoke
    setBusy 1
    if {[.body.actions.start cget -state] ne "disabled"} {exit 4}
    setBusy 0
    appendLog {Unicode: Robotics → Sound; [exit] $root}
    if {[.body.actions.start cget -state] ne "normal"} {exit 5}
    exit 0
}
'''
cases = [
    ('Rust-style init without keywords', ['/tmp/arxiv-test', 'cs.SD=Sound', 'daily', '100', '', 'Test'], '', 5),
    ('Python-style init with keywords', ['/tmp/arxiv-test', 'cs.SD=Sound', 'daily', '100', '', 'Test', 'connectome=Connectome'], 'grid', 6),
]
for name, values, manager, fields in cases:
    line = 'init\t' + '\t'.join(base64.b64encode(value.encode()).decode() for value in values) + '\n'
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / 'probe.tcl'
        script.write_text(f'set expectKeywordManager {{{manager}}}\n' + source + probe)
        child = subprocess.Popen([os.environ.get('WISH', 'wish'), str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child.stdin.write(line)
        child.stdin.flush()
        child.wait(timeout=10)
        output = child.stdout.read()
        error = child.stderr.read()
        assert child.returncode == 0, (name, child.returncode, error)
        assert output.startswith('save\t') and len(output.splitlines()[0].split('\t')) == fields + 1, (name, output)
        child.stdin.close()
        print(f'Tk UI ({name}): init, Unicode, save protocol, busy controls and exit passed.')
