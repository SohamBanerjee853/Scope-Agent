# Recording sources

These are the scripts and narration used to make the checked-in two-minute
terminal screencast. The recording used Scope revision
`cd8f4209eaf6a4b7ea4991b0aa4dd045ccbb51e8` on macOS, with tmux at
`/usr/local/bin/tmux` and the system Menlo/Arial fonts. The scripts are standalone
artifact tooling, not package runtime dependencies or live coding-host drivers.

To regenerate on a matching Mac, work from the repository root and copy these
three source files into a new, ignored local workspace. For example, when
`.tools/submission-video` does not already exist:

```sh
mkdir -p .tools/submission-video
cp submission/recording/record_demo.py submission/recording/render_video.py submission/recording/narration.json .tools/submission-video/
uv venv .tools/submission-video/.venv
uv pip install --python .tools/submission-video/.venv/bin/python . pytest==9.1.1 pillow==12.3.0 pyte==0.8.2 imageio-ffmpeg==0.6.0
.tools/submission-video/.venv/bin/python .tools/submission-video/record_demo.py
```

The recorder prints its new `take-*` directory after it verifies the workflow.
Pass that actual directory to `render_video.py`. The renderer writes clean and
captioned MP4s, a timed speaking script, subtitles, stills and a manifest under
the local workspace's `deliverables/` directory. The checked-in landing page and
ZIP bundle are packaging around those generated files.

Recording creates a fresh demo identity and isolated homes. It drives the real
Scope review interface with labeled fixture inputs and executes only the bundled
local payment fixture and its exact repair. It never launches a live coding model
or performs the synthetic publishing command. No recordings, runtime homes,
virtual environments or ffmpeg binaries should be staged from the local workspace.

The committed videos and fixture receipt preserve their original bytes and
displayed demo paths. The manifest and validation report describe the original
capture; they are not evidence that a later regeneration ran identically.
