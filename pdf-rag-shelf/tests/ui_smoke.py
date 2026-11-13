import argparse
import shutil
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[1]


def tcl_value(value):
    encoded = base64.b64encode(str(value).encode()).decode()
    return f'[encoding convertfrom utf-8 [binary decode base64 {{{encoded}}}]]'


def check(variant, live_model=None, ollama_url="http://127.0.0.1:11434", timeout=300):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        calls = root / 'calls.jsonl'
        engine = root / 'fake_engine.py'
        path = root / 'Sound' / 'speech.pdf'
        query = 'Unicode âm thanh | > & ; $value [exec false]'
        engine.write_text(
            'import base64, json, sys\n'
            f'with open({str(calls)!r}, "a") as output: output.write(json.dumps(sys.argv[1:]) + "\\n")\n'
            'def emit(event, *values):\n'
            '    print("\\t".join([event] + [base64.b64encode(str(v).encode()).decode() for v in values]), flush=True)\n'
            'emit("log", "Worker ready")\n'
            'if sys.argv[1] == "search":\n'
            f'    emit("hit", {str(path)!r}, 2, "0.85", "Speech and âm thanh", "Sound", {str(root / "Robotics" / "alias.pdf")!r}, "Robotics")\n',
            encoding='utf-8',
        )
        if live_model:
            for target in (path, root / 'Robotics' / 'alias.pdf'):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(PROJECT / 'tests/fixture.pdf', target)
        worker = [sys.executable, '-m', 'rag_shelf'] if live_model else [sys.executable, str(engine)]
        worker_tcl = ' '.join(tcl_value(value) for value in worker)
        deadline = timeout if live_model else 10
        preview_pattern = '*Page *All sources:*' if live_model else '*Page 2*Speech*'
        probe = root / 'probe.tcl'
        probe.write_text(
            f'set argv [list {variant} {worker_tcl}]\n'
            f'source {tcl_value(PROJECT / "ui/app.tcl")}\n'
            f'set cfg(root) {tcl_value(root)}\n'
            f'set cfg(db) {tcl_value(root / "index.sqlite3")}\n'
            f'set cfg(query) {tcl_value(query)}\n'
            f'set cfg(model) {tcl_value(live_model or "nomic-embed-text-v2-moe:latest")}\n'
            'set cfg(k) 1\n'
            'proc bgerror {message} {puts stderr $message; exit 1}\n'
            'proc assert {condition message} {if {![uplevel 1 [list expr $condition]]} {puts stderr $message; exit 1}}\n'
            'set phase 0\n'
            'proc verify {} {\n'
            '    global channel phase status hits cfgfile\n'
            '    if {$channel ne ""} {after 50 verify; return}\n'
            '    assert {$status eq "Finished successfully."} "Worker did not finish successfully"\n'
            '    assert {[file exists $cfgfile]} "Settings were not saved"\n'
            '    assert {[.main.buttons.stop cget -state] eq "disabled"} "Stop remained active"\n'
            '    if {$phase == 0} {set phase 1; startJob search; after 50 verify; return}\n'
            '    set ids [.main.results children {}]\n'
            '    assert {[llength $ids] == 2} "Expected both source aliases"\n'
            '    .main.results selection set [lindex $ids 0]\n'
            '    showHit\n'
            f'    assert {{[string match {{{preview_pattern}}} [.main.preview get 1.0 end]]}} "Preview missing page or passage"\n'
            '    puts "UI smoke test passed"\n'
            '    exit 0\n'
            '}\n'
            'after 0 {startJob index; after 50 verify}\n'
            f'after {deadline * 1000} {{puts stderr "UI smoke test timed out"; exit 1}}\n',
            encoding='utf-8',
        )
        environment = dict(os.environ, XDG_CONFIG_HOME=str(root / 'config'),
                           PYTHONPATH=str(PROJECT / 'python') + os.pathsep + os.environ.get('PYTHONPATH', ''),
                           OLLAMA_HOST=ollama_url, RAG_SEARCH_BACKEND='exact', RAG_OCR_LANGUAGE='', RAG_PDF_SANDBOX='limits')
        subprocess.run(['wish', str(probe)], env=environment, check=True, timeout=deadline + 5)
        if live_model:
            print('Live GUI indexing/search integration passed; relevance and sandboxing were not evaluated.')
            return
        arguments = [json.loads(line) for line in calls.read_text().splitlines()]
        assert [item[0] for item in arguments] == ['index', 'search']
        search = arguments[1]
        assert base64.b64decode(search[search.index('--query') + 1]).decode() == query
        assert '--encoded' in search
        assert '--ui' in search


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live-model')
    parser.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    check('python', args.live_model, args.ollama_url, args.timeout)
