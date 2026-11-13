# Validation

Validated on 2026-11-10 in a Debian 13 x86-64 execution environment with Python 3.12 and Rust 1.99.1. The documented installation target is Ubuntu 24.04 or newer; Ubuntu itself was not available for an end-to-end desktop installation in this environment.

| Check | Result |
|---|---|
| Python unit/regression suite | 16 passed |
| Rust unit/regression suite | 12 passed |
| Rust live integration test | Passed, explicitly invoked; ignored in offline CI |
| Python live metadata request | Passed: `1706.03762v7`, Attention Is All You Need |
| Python live category/date query | Passed: `cs.CL`, 2017-06-12, 17 results parsed |
| Python live PDF download | Passed: 2,215,244 bytes; header/end-marker verified |
| Rust live metadata and PDF download | Passed for the same paper and byte count |
| Python GUI adapter | Init, Save, configuration persistence, and Quit passed with a stand-in UI process |
| Rust GUI adapter | Same protocol checks passed with the release binary |
| Rust optimized build | Passed |
| Rust Clippy | Passed with warnings treated as errors |
| Rust formatting | Passed |
| Shell launcher/timer scripts | Bash syntax checks passed |
| Generated systemd unit syntax | Passed `systemd-analyze --user verify` with a harmless stand-in executable |
| Python modules and helper scripts | Bytecode compilation passed |
| Tcl source | Syntax completeness check passed |
| Visual Tk window / virtual-display smoke test | Not executed successfully: this environment denies local display-server sockets |
| Actual systemd scheduling | Not exercised; install and inspect on Ubuntu with the supplied scripts |
| Whole-category download | Not performed; traversal and mode semantics covered by fixture tests |
| Full corpus download | Not implemented; official bulk-access route documented in README |
| RAM comparison | Measurement helper supplied; no measured desktop RAM claim |

The live checks retrieved one public PDF per implementation. No downloaded paper, personal configuration, database, credentials, or compiled binary is included in the source archive. Rust uses the system certificate trust store with verification enabled.

The GUI adapter tests exercise message transport and configuration persistence; they do not verify visual layout or physical clicks. Run `xvfb-run -a python3 tests/ui_smoke.py` on Ubuntu for the native widget test, then open the app normally to inspect the layout.

The GitHub workflow is included but has not been executed by GitHub Actions in this session. Its local equivalent checks are reported above.
