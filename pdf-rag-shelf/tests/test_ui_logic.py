from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipIf
try:
    import tkinter
except ImportError:
    tkinter = None


@skipIf(tkinter is None, 'Python Tcl binding is unavailable')
class UiLogicTests(TestCase):
    def setUp(self):
        source = (Path(__file__).resolve().parents[1] / 'ui/app.tcl').read_text()
        self.tcl = tkinter.Tcl()
        self.assertTrue(self.tcl.call('info', 'complete', source))
        self.tcl.eval(source[source.index('proc b64'):source.index('wm title .')])
        self.tcl.eval('''
            proc tk_messageBox {args} {set ::message $args; return ok}
            array set cfg {root /tmp db /tmp/index.sqlite3 model nomic chunk 600 overlap 80 batch 16 k 8}
            set channel ""
            set message ""
        ''')

    def test_range_validation_precedes_worker_start(self):
        for key, value in [('chunk', -1), ('overlap', -1), ('overlap', 300), ('batch', 0), ('batch', 65), ('k', 101)]:
            old = self.tcl.eval(f'set cfg({key})')
            self.tcl.call('set', f'cfg({key})', value)
            self.tcl.call('startJob', 'index')
            self.assertIn('Chunk:', self.tcl.eval('set message'))
            self.tcl.call('set', f'cfg({key})', old)

    def test_save_settings_is_private(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'settings.tcldata'
            self.tcl.call('set', 'cfgfile', str(path))
            self.tcl.call('saveSettings')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_stop_escalates_only_current_job(self):
        self.tcl.eval('''
            rename exec real_exec
            proc exec {args} {lappend ::calls $args}
            rename pid real_pid
            proc pid {args} {return 12345}
            set calls {}
            set channel fake
            set cancelled 0
            set killTimer ""
            set generation 7
        ''')
        self.tcl.call('stopJob')
        self.assertIn('-TERM', self.tcl.eval('set calls'))
        self.tcl.call('forceStop', 6, '12345')
        self.assertNotIn('-KILL', self.tcl.eval('set calls'))
        self.tcl.call('forceStop', 7, '12345')
        self.assertIn('-KILL', self.tcl.eval('set calls'))

    def test_cleanup_confirmation_displays_counts_and_root(self):
        self.tcl.eval('''
            rename tk_messageBox old_message
            proc tk_messageBox {args} {set ::message $args; return yes}
            rename startJob old_start
            proc startJob {action} {set ::started $action}
            set prunePlan {3 2 200 1 abc123 /papers}
            set applyToken ""
        ''')
        self.tcl.call('confirmPrune')
        self.assertIn('/papers', self.tcl.eval('set message'))
        self.assertIn('200 vector chunks', self.tcl.eval('set message'))
        self.assertEqual(self.tcl.eval('set applyToken'), 'abc123')
        self.assertEqual(self.tcl.eval('set started'), 'prune_apply')

    def test_cleanup_decline_does_not_apply(self):
        self.tcl.eval('''
            rename tk_messageBox old_message
            proc tk_messageBox {args} {return no}
            rename startJob old_start
            proc startJob {action} {error "Cleanup must not start"}
            set prunePlan {3 2 200 1 abc123 /papers}
        ''')
        self.tcl.call('confirmPrune')

    def test_all_aliases_have_selectable_rows(self):
        self.tcl.eval('''
            set rows {}
            proc .main.results {args} {lappend ::rows $args; return [llength $::rows]}
            array set hits {}
        ''')
        self.tcl.call('addHit', ('/papers/a.pdf', '4', '0.8', 'A passage', 'Sound', '/papers/b {copy}.pdf', 'Robotics'))
        self.assertEqual(int(self.tcl.eval('array size hits')), 2)
        self.assertEqual(self.tcl.eval('lindex $hits(2) 0'), '/papers/b {copy}.pdf')
        self.assertEqual(self.tcl.eval('lindex $hits(2) 1'), '4')
        self.assertEqual(int(self.tcl.eval('llength [lindex $hits(1) 3]')), 2)
