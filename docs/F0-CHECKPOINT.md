# F0 foundation checkpoint

Completed September 12, 2026 in `/Users/soham/Scope-Agent`, on `main`, for the
public repository `SohamBanerjee853/Scope-Agent`. The configured remote was empty
before implementation. Existing local files were preserved.

Implemented: Python 3.11+ package and console entry point; lazy routes for all
frozen commands; side-effect-free path resolution and safe session filenames;
UTF-8 JSONL append/read with malformed/truncated-record tolerance; contributor
ownership rules; frozen interfaces and baseline hook contract documentation.
The event shape and module dispatch conventions are in INTERFACES.md.

Validation on macOS with CPython 3.11.16 and uv 0.12.13:

- `uv sync --python 3.11`: succeeded and generated `uv.lock`.
- `uv run pytest -q`: 59 passed, 0 skipped.
- `uv build`: source distribution and wheel both built; wheel built from sdist.
- Noneditable wheel installed in a separate environment: 59 passed, 0 skipped.
- Installed imports resolved to site-packages, and all three reserved resource
  directories were readable using importlib.resources.
- CLI help/version, exact wire contract, maintained Markdown links/UTF-8
  and wheel contents were checked.

The commands above used `.tools/bin/uv`, installed in an ignored local tooling
environment. On this checkout use that executable, or place `.tools/bin` on PATH.
The noneditable check environment is `.tools/wheel-check`.

Resource packaging includes the whole `src/scope` package, including data files.
F0 contains resource README placeholders only. Actual YAML rules, skills and demo
assets await S1/S3/A3 and need their own wheel checks once implemented.

No permission engine, watcher, understanding workflow, receipt, demo or launcher
exists yet. Missing feature commands exit 1 with a clear diagnostic; they are not
usable hooks. No live host session, global hook installation, or model call was
performed. Windows execution and shell classifier behavior are unverified; the
portable path tests include both POSIX and Windows-shaped inputs on this Mac.
There is no classifier to exercise in F0.

The baseline hook contract has not been verified against a current host.

Next: review this shared foundation. Soham then starts S1 on
`work/soham-permissions`; Arjun starts A1 from the same foundation SHA in a separate
checkout on `work/arjun-understanding`. F0 does not create either feature branch.
