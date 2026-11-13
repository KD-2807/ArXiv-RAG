# arXiv Shelf

Two independent Ubuntu desktop applications for collecting arXiv papers by subject: a Python version and a Rust version. Both use the same lightweight native Tk interface, separate settings and databases, and a single background download worker. No browser, web server, listening port, account, or LAN access is required.

The application supports **all papers in selected categories**, a chosen number of **new downloads per category**, and **daily catch-up**. Downloaded papers are checked and skipped on later runs.

## Contents

- [Features](#features)
- [Choose a version](#choose-a-version)
- [Install and run on Ubuntu](#install-and-run-on-ubuntu)
- [Configure subjects and download modes](#configure-subjects-and-download-modes)
- [Automatic daily downloads](#automatic-daily-downloads)
- [Files and duplicate handling](#files-and-duplicate-handling)
- [Whole-arXiv bulk access](#whole-arxiv-bulk-access)
- [Architecture](#architecture)
- [Tests and memory comparison](#tests-and-memory-comparison)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Contributing and license](#contributing-and-license)

## Features

- Editable category-to-folder mapping, including `cs.SD=Sound` and `cs.RO=Robotics`.
- Python version only: keyword downloads that behave like the arXiv website search, for example `connectome=Connectome`.
- HTTPS requests, one download at a time, at least 3.2 seconds between application requests.
- Retries for temporary failures and HTTP 429/5xx; respects `Retry-After`.
- Streamed PDFs with a 64 KiB buffer, bounded metadata pages, and a bounded UI log.
- SQLite catalogue with titles, identifiers, categories, original downloaded versions, and SHA-256 hashes.
- Atomic completed-file replacement; incomplete transfers never become completed records.
- Restart a stopped job to skip verified files and continue finding missing ones.
- Cross-listed papers appear in multiple subject folders using hard links where possible.
- Desktop Start, Stop, Save settings, Browse, Open folder, activity indicator, and live log.
- Headless mode and optional Ubuntu systemd user timers.
- Python classes/protocols and Rust structs/traits separate domain, transport, storage, orchestration, and UI.
- Source code contains no explanatory comments; documentation lives in Markdown.

## Choose a version

| Property | Python | Rust |
|---|---|---|
| Runtime | Python 3.11+ and Tk | Compiled binary and Tk |
| Python packages | None; standard library only | Python is not required to run the app |
| GUI | Native Tk via `wish` | Same native Tk via `wish` |
| Setup | No compilation | Build once with Cargo |
| Network | `urllib` | `reqwest` with Rustls and system trust roots |
| Database | Standard-library SQLite | Bundled SQLite through `rusqlite` |
| Download directory | `~/Downloads/ArxivShelf-Python` | `~/Downloads/ArxivShelf-Rust` |
| Settings | `~/.config/arxiv-shelf-python/settings.json` | `~/.config/arxiv-shelf-rust/settings.json` |

Start with Python for the easiest setup. Compare measured memory on your Ubuntu machine before choosing a winner. Rust is not automatically lower-memory once the GUI and networking libraries are included. Both deliberately use the same small native UI for a useful comparison; this is not a claim of the absolute lowest possible RAM consumption.

## Install and run on Ubuntu

Target: Ubuntu 24.04 LTS or newer, with a graphical desktop. The project also works on compatible Linux desktops. Use a local filesystem owned by your user. Internet access is needed for downloads and the first Rust build.

### 1. Open the project

The project is the `arxiv-shelf/` folder inside `arxiv-rag/`. Keep `arxiv-rag/` somewhere permanent, then enter the folder:

```bash
cd /path/to/arxiv-rag/arxiv-shelf
```

If you publish the project on GitHub, users can clone that repository instead. Keep the `python`, `rust`, `ui`, `scripts`, and `tests` directories together.

### 2. Python version

```bash
sudo apt install -y python3 tk xdg-utils
python3 --version
bash scripts/run-python.sh
```

Python must be 3.11 or newer. Ubuntu 24.04's system Python meets this requirement. No `pip install`, virtual environment, browser, or service setup is needed.

Optional application-menu launcher:

```bash
bash scripts/install-launcher.sh python
```

Open **arXiv Shelf (python)** from the Ubuntu application menu. The installed launcher points to this project folder; rerun the installer if you move the project.

### 3. Rust version

Install build dependencies and Tk:

```bash
sudo apt install -y build-essential curl ca-certificates tk xdg-utils
```

If Rust is not installed, download the official installer, review it, then run it:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/arxiv-rustup.sh
less /tmp/arxiv-rustup.sh
sh /tmp/arxiv-rustup.sh -y --profile minimal
source "$HOME/.cargo/env"
```

If Rust is already installed, update the stable toolchain. Then build and run:

```bash
rustup update stable
cargo build --release --locked --manifest-path rust/Cargo.toml
bash scripts/run-rust.sh
```

The executable is `rust/target/release/arxiv-shelf`. The first build downloads dependencies and can use much more RAM than the running application. On a constrained machine, add `-j 1` to the build command. `Cargo.lock` is included for dependency reproducibility.

Optional application-menu launcher:

```bash
bash scripts/install-launcher.sh rust
```

After building, Rust runs without Python. Its Tk interface is embedded in the binary and written to its own configuration directory at launch.

## Configure subjects and download modes

1. Choose a **Download folder**, or keep the separate default directory.
2. Enter one `category=Folder` mapping per line.
3. Select a mode and, for limited mode, the number of new papers.
4. Optionally enter a contact email or project URL for the HTTP User-Agent.
5. Click **Save settings**, then **Start downloads**.

Example:

```text
cs.SD=Sound
cs.RO=Robotics
cs.AI=Artificial Intelligence
cs.LG=Machine Learning
cs.CV=Computer Vision
cs.CL=Language Processing
cs.MM=Multimedia
cs.IR=Information Retrieval
```

`categories.example.txt` contains more mappings you can copy selectively. Exact non-CS categories, such as `math.NA=Numerical Analysis`, also work. Wildcards are intentionally not accepted. The arXiv API validates whether a syntactically valid category actually exists; an unknown category can return zero results.

### Download by keyword (Python version)

The **Keywords → folders** box accepts one entry per line, alongside or instead of categories:

```text
connectome=Connectome
brain connectome=Brain Connectome
"graph theory"=Graphs
https://arxiv.org/search/?query=connectome&searchtype=all&source=header
https://arxiv.org/search/?query=connectome&searchtype=title Connectome Titles
```

- `keyword=Folder` searches all fields, like the website's default **All fields** search. Several words must all match; wrap words in quotes for an exact phrase.
- A pasted `arxiv.org/search` URL is converted to the equivalent API query. The `query` and `searchtype` parameters are used (`all`, `title`, `abstract`, `author`, `comments`, `journal_ref`, `report_num`). Add a folder name after a space; otherwise the folder is named after the query.
- Keywords use the same modes, the same skip-if-already-downloaded check, and their own daily checkpoint. Keyword searches scan the whole archive history in adaptively split ranges, newest first, so a rare keyword needs only a few requests.
- The Rust version does not yet have this box and ignores keywords.

| Mode | Behavior |
|---|---|
| New papers per category | Scans newest first until it has saved N missing papers in each category, or exhausted that category. Existing papers do not consume N. |
| Daily catch-up | Retrieves new submissions since the previous successful daily checkpoint, with seven days of overlap. First run checks the preceding seven days. |
| All in selected categories | Scans category history back to 1991, newest first. No application paper-count limit. |

For `cs.SD` and `cs.RO`, count 100 means up to 100 new files per category. A paper already saved while processing the first category is skipped and linked in the second; it does not count as a second download.

Daily means **new submissions**, not every revision to older papers. The API's date filter is `submittedDate`. Previously saved base identifiers are kept even if a newer version appears. For example, after saving `2611.00001v1`, a later `v2` result is skipped. The database records which version you actually downloaded. Automatic revision replacement is not implemented.

Daily catch-up is not a mathematical guarantee against arbitrarily late arXiv indexing. Seven days of overlap reduces ordinary announcement/indexing delays. Run All occasionally if you need to reconcile older omissions. API results are not a transactional snapshot: changing page totals are treated as an error, and rerunning reconciles known papers.

Large date ranges are divided by month, then recursively split when a range exceeds 1,000 matches. Metadata pages contain at most 100 records, so categories exceeding the API's per-query result ceiling can be traversed without silently stopping at that ceiling. An unpageable one-minute range raises an explicit error.

### Terminal operation

Use the last saved GUI configuration:

```bash
bash scripts/run-python.sh --headless --mode limited --limit 100
bash scripts/run-python.sh --headless --mode daily
bash scripts/run-python.sh --headless --mode all
```

Rust accepts the same arguments:

```bash
bash scripts/run-rust.sh --headless --mode limited --limit 100
bash scripts/run-rust.sh --headless --mode daily
bash scripts/run-rust.sh --headless --mode all
```

Run one download job at a time. The two implementations share only an advisory download lock, so their API traffic is serialized. Their PDFs, catalogues, caches, and settings remain separate. Other arXiv clients on your devices are not coordinated by this lock.

`--config /absolute/path/settings.json` selects a different settings file. Command-line `--mode` and `--limit` overrides apply to headless runs only and do not rewrite saved GUI settings.

## Automatic daily downloads

First launch the chosen version, configure subjects and the directory, and click **Save settings**. Install its launcher as described above.

Choose one timer:

```bash
bash scripts/install-timer.sh python
```

or:

```bash
bash scripts/install-timer.sh rust
```

The timer runs **daily catch-up** around 09:00 in the computer's local timezone, with up to 15 minutes of randomized delay. It uses the saved subjects and path but overrides the GUI mode with `daily`. A missed run is caught up when the user timer becomes active again. The app window can remain closed. Nothing downloads while the computer is powered off.

To download up to **100 additional papers per category each day**, install the limited-mode timer instead:

```bash
bash scripts/install-timer.sh python limited 100
```

Replace `python` with `rust` for the Rust version. Reinstalling replaces only that version's timer configuration.

Inspect or stop a timer:

```bash
systemctl --user list-timers 'arxiv-shelf-*'
journalctl --user -u arxiv-shelf-python.service -n 50
systemctl --user disable --now arxiv-shelf-python.timer
systemctl --user stop arxiv-shelf-python.service
```

A failed scheduled job retries after 15 minutes; completed files are skipped. A user timer normally runs while you are logged in. To allow your user services to remain active after logout on an always-on computer, optionally enable lingering:

```bash
loginctl enable-linger "$USER"
```

This may require administrator authorization on your Ubuntu installation. The application does not change this setting automatically.

## Files and duplicate handling

For the Python version, a typical layout is:

```text
~/Downloads/ArxivShelf-Python/
    .variant
    .objects/
        2611.00001.pdf
    catalog.sqlite3
    Sound/
        2611.00001.pdf
    Robotics/
        2611.00001.pdf
```

The Rust version has the same structure under its own root. `.variant` prevents accidentally mixing the two databases in one directory. XDG configuration and cache environment variables are respected.

- Canonical filenames use stable arXiv base identifiers, including safe filenames for old IDs.
- The catalogue stores the title and original metadata; filenames remain stable if titles change.
- A valid canonical file with a matching stored SHA-256 is skipped. Missing or changed files are downloaded again when encountered.
- A complete canonical file left after a crash before database commit is adopted on the next encounter.
- A file must pass PDF header and end-marker checks. HTTP content length is checked when provided. These checks are not a full structural PDF validator.
- New PDFs are streamed into `.part` files, flushed to disk, checked, then atomically renamed. Stop or failure removes the in-progress file. Restart resumes at the paper level, not the byte offset.
- Subject files normally hard-link to the canonical file, so cross-listing costs no additional PDF storage. Filesystems without hard-link support use a copy fallback.
- Editing a hard-linked subject PDF also changes the canonical PDF; use copies for annotations.
- Deleting only a subject link does not remove the canonical copy. A later encounter recreates that link. Renaming a configured folder creates links in the new folder; old folders are not automatically deleted.
- Existing PDFs outside the managed `.objects` layout are not automatically imported or deduplicated.
- Keep the entire root together when backing up or moving it; then update Download folder in settings. Close the app and stop its timer during the move.

All mode restarts its metadata scan after interruption and skips completed PDFs. It does not persist an exact metadata-page cursor. Metadata responses are cached for up to 24 hours; expired entries are removed when a new job starts. Daily checkpoints advance only after a category is fully processed successfully.

Maximum accepted single PDF size is 512 MiB; maximum Atom response is 16 MiB. Larger responses fail explicitly. Stop is checked between streamed chunks and during waits; an active network call may take up to its timeout before stopping. DNS resolution may add platform-dependent delay.

## Whole-arXiv bulk access

The desktop All option means **selected categories**, not a full-arXiv website mirror. arXiv directs complete-corpus PDF downloads to its bulk distribution services. This release does **not** include an S3/Kaggle downloader or bulk-tar subject importer.

For literally every paper in the archive, use [arXiv Bulk Data Access](https://info.arxiv.org/help/bulk_data.html) and [Full Text via S3](https://info.arxiv.org/help/bulk_data_s3.html). S3 bulk access has its own account, transfer-cost, storage, archive-format, and licensing considerations. Read those instructions before starting a multi-terabyte download. Use this application for your category collections and subsequent catch-up.

Requests follow the [arXiv API Terms](https://info.arxiv.org/help/api/tou.html) and [API manual](https://info.arxiv.org/help/api/user-manual.html): serialized requests, a conservative delay, small pages, and backoff. Do not run independent clients in parallel to increase throughput. PDFs are kept locally; the project does not redistribute them.

## Architecture

| Layer | Python | Rust | Responsibility |
|---|---|---|---|
| Domain and configuration | `domain.py` | `domain.rs` | Paper identity, settings validation, catalogue abstraction |
| Transport | `network.py` | `network.rs` | HTTPS, delay, retry, Atom parsing, cancellation, streaming |
| Persistence | `storage.py` | `storage.rs` | Store abstraction, SQLite, hashes, atomic subject links |
| Application service | `service.py` | `service.rs` | Partitioned traversal, mode semantics, checkpoints, process lock |
| Composition and UI adapter | `app.py` | `main.rs` | Wiring, CLI, worker lifecycle, pipe protocol |
| Presentation | `ui/shelf.tcl` | Same source, embedded at compile time | Native Tk window and controls |

The service depends on narrow catalogue and storage interfaces. Concrete network and SQLite implementations are injected at the composition boundary. Tests replace the catalogue without opening network connections. Rust uses composition and traits rather than inheritance; Python uses classes and structural protocols. The UI has no database or HTTP logic.

UI fields and log messages travel through child-process pipes as tab-separated base64 fields. They are treated as data, not executable Tcl. A bounded log keeps long runs from accumulating text widgets indefinitely. The engine, UI, and optional headless timer never bind a TCP listener.

## Tests and memory comparison

Python tests:

```bash
PYTHONPATH=python python3 -m unittest discover -s python/tests -v
```

Rust tests and static checks:

```bash
rustup component add rustfmt clippy
cargo test --locked --manifest-path rust/Cargo.toml
cargo fmt --manifest-path rust/Cargo.toml -- --check
cargo clippy --locked --manifest-path rust/Cargo.toml -- -D warnings
```

GUI adapter tests, after building Rust (no display needed):

```bash
python3 tests/gui_adapter_smoke.py
```

UI smoke test, using a virtual display:

```bash
sudo apt install -y xvfb
xvfb-run -a python3 tests/ui_smoke.py
```

The suite covers feed parsing, legacy IDs, input validation, duplicates, missing/corrupt files, paper-count semantics, cross-listing, cancellation, incomplete pagination, checkpoints, path protection, and date windows. Python additionally exercises retry timing, partition splitting, partial-file cleanup, and locking. CI runs offline tests on Ubuntu; it does not download live papers.

Measure each complete desktop process tree separately:

```bash
python3 scripts/measure-memory.py -- bash scripts/run-python.sh
python3 scripts/measure-memory.py -- bash scripts/run-rust.sh
```

For a fair comparison, use the Rust release build, the same settings, and separate initially empty download roots. Measure idle for 30 seconds, then the same small download workload. Close the window to print peak sampled usage. Do not run both download jobs simultaneously.

The script reports RSS and PSS in MiB for the parent and descendants. Summed RSS can double-count shared pages; PSS allocates shared pages proportionally and is better for comparing these two-process applications. Peaks are sampled once per second, so very brief spikes may be missed. The benchmark helper itself uses Python; it is optional and is not part of the Rust application's runtime.

See [VALIDATION.md](VALIDATION.md) for the checks actually performed on this delivery. No Ubuntu-specific performance claim should be inferred from a different test environment.

## Troubleshooting

| Problem | Action |
|---|---|
| `wish` not found | Install `tk`: `sudo apt install tk`. |
| No `$DISPLAY` / cannot open display | Run in your Ubuntu graphical session, or use `--headless`. |
| Python is older than 3.11 | Use Ubuntu 24.04+ or an independently installed Python 3.11+. Do not replace Ubuntu's system interpreter. |
| Rust binary missing | Run the release build command from the project root. |
| HTTP 429 / 503 | Let backoff complete, stop other arXiv clients, and retry later. |
| TLS certificate failure | Install/update `ca-certificates`. Do not disable verification. |
| Another download job is running | Stop the other window's job or its systemd service, then retry. |
| Zero matches | Check the exact category against the [category taxonomy](https://arxiv.org/category_taxonomy), and try All if the daily window is empty. |
| PDF is unavailable or withdrawn | The job reports the error and keeps its checkpoint unchanged. Already completed files remain; investigate that identifier and rerun later. There is no silent ignore-failures mode. |
| Disk full or permission denied | Free space or choose a writable local folder, then restart the job. |
| All mode seems slow | Requests are intentionally serialized. Large collections can take days; progress survives interruption. |
| Timer not running | Check `systemctl --user list-timers` and the service journal. Save GUI settings before enabling it. |
| UI looks small on HiDPI | Adjust your desktop's scaling settings; Tk follows the local display environment. |

Open a repository issue with Ubuntu version, Python/Rust version, mode, selected categories, and the relevant error text. Do not include personal contact information or downloaded PDFs.

## Uninstall

For each installed variant, stop and remove its timer and launcher. Example for Python:

```bash
systemctl --user disable --now arxiv-shelf-python.timer
systemctl --user stop arxiv-shelf-python.service
rm -f ~/.config/systemd/user/arxiv-shelf-python.{timer,service}
rm -f ~/.local/bin/arxiv-shelf-python
rm -f ~/.local/share/applications/arxiv-shelf-python.desktop
systemctl --user daemon-reload
```

Replace `python` with `rust` for the other version. Removing launchers does not remove your PDFs. Delete project files, configuration directories, or paper directories manually only when you no longer need them.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [MIT license](LICENSE). The software license does not grant rights to downloaded papers. The repository owner maintains their published copy. This project is independent and is not affiliated with or endorsed by arXiv.

The README follows GitHub's guidance to explain purpose, usefulness, installation, help, and contribution paths: [About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes).