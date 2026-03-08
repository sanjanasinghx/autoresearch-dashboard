# Autoresearch Dashboard

A live web dashboard for [Karpathy's autoresearch](https://github.com/karpathy/autoresearch). It plots experiment progress, shows git history, and can pause/resume the agent. It doesn't touch the core loop.

## What's in it

- Progress chart: Plotly scatter of val_bpb vs experiment #, green = improvement, gray = no gain, orange = crash, with a dashed line for the best so far. Updates live over Socket.IO.
- Stats bar: total experiments, best val_bpb, improvement rate, crash count, avg delta bpb, total runtime.
- Experiment log: sortable table; click a row to see its git diff in the Diff Viewer.
- Analytics tab: strategy breakdown, VRAM trend, experiments/hour, time since last improvement, longest no-gain streak.
- Diff viewer: green/red diff of `train.py` per experiment, plus a "Current Working Diff" button.
- Git activity feed: recent commits with SHA, message, time and diff stats.
- Live `run.log`: last 20 lines of the current experiment's output.
- `progress.png`: reloads when the file changes.
- Pause/Resume: creates/removes a `.dashboard_pause` file (see the `program.md` note below).
- Multiple branches: pick any `autoresearch/*` branch from the header dropdown, or turn on Overlay to compare branches on one chart.

## Setup

Install dependencies:

```bash
cd dashboard
pip install -r requirements.txt
```

Run it:

```bash
# From the repo root (uses the parent directory as the repo path)
python dashboard/app.py

# Or set it explicitly:
python dashboard/app.py --repo /path/to/autoresearch --port 5050
```

Then open http://localhost:5050.

If you don't have real experiments yet, generate some mock data:

```bash
curl -X POST http://localhost:5050/api/mock/generate
```

This writes a sample `results.tsv` with 20 experiments.

## Docker

```bash
cd dashboard
docker build -t autoresearch-dashboard .
docker run -p 5050:5050 -v /path/to/autoresearch:/repo autoresearch-dashboard
```

## Pause/Resume (optional)

For the Pause button to actually do anything, add this to the top of `program.md`:

```markdown
Before starting each new experiment, check if a file `.dashboard_pause` exists
in the repo root. If it does, wait and check again every 30 seconds until it's removed.
```

Pause creates `.dashboard_pause` and Resume removes it. Nothing else in the autoresearch loop changes.

## API

| Endpoint | Method | Description |
|---|---|---|
| `/api/experiments` | GET | Full experiment history JSON |
| `/api/experiments/latest` | GET | Most recent result |
| `/api/experiments/best` | GET | Best val_bpb so far |
| `/api/progress-image` | GET | Current `progress.png` (binary) |
| `/api/git/commits` | GET | Recent commits (optional `?branch=`) |
| `/api/git/diff/<sha>` | GET | Diff for a commit |
| `/api/git/current-diff` | GET | Uncommitted changes |
| `/api/git/branches` | GET | All branches |
| `/api/status` | GET | Agent status: idle/running/paused |
| `/api/control/pause` | POST | Create `.dashboard_pause` |
| `/api/control/resume` | POST | Remove `.dashboard_pause` |
| `/api/train-source` | GET | Current `train.py` contents |
| `/api/run-log` | GET | Parsed run.log data |
| `/api/mock/generate` | POST | Write a sample results.tsv for testing |

## Layout

```
autoresearch/          <- Karpathy's repo (not modified)
|-- train.py
|-- program.md
|-- results.tsv        <- read by dashboard
|-- run.log            <- read by dashboard
|-- progress.png       <- read by dashboard
`-- dashboard/         <- this project
    |-- app.py         Flask + Socket.IO server
    |-- watcher.py     file + git watcher
    |-- requirements.txt
    |-- Dockerfile
    |-- static/
    |   |-- dashboard.js
    |   `-- dashboard.css
    `-- templates/
        `-- index.html
```

The watcher uses `watchdog` for file events and also polls every 10 seconds in case an event gets missed.

## Socket.IO events

| Event | Direction | Payload |
|---|---|---|
| `new_experiment` | server to client | `{experiment_number, val_bpb, delta_bpb, ...}` |
| `new_commit` | server to client | `{sha, message, timestamp, diff_stat}` |
| `progress_updated` | server to client | `{timestamp}` |
| `run_log_update` | server to client | `{tail: [...lines], crashed: bool}` |
| `agent_status` | server to client | `{status: "running"\|"paused"\|"idle"}` |
| `full_state` | server to client | `{experiments: [...]}` (on connect) |
| `request_state` | client to server | asks the server to resend full_state |
