# Autoresearch Monitoring Dashboard

A real-time web dashboard for [Karpathy's autoresearch](https://github.com/karpathy/autoresearch) that visualizes experiment progress, tracks git history, and lets you pause/resume the agent — all without touching the core loop.

## Features

- **Live progress chart** — Plotly scatter plot of val_bpb vs experiment #, color-coded (green = improvement, gray = no gain, orange = crash), with a dashed cumulative-best line. Auto-updates via Socket.IO.
- **Stats bar** — total experiments, best val_bpb, improvement rate, crash count, avg Δ bpb, total runtime.
- **Experiment log table** — sortable, clickable rows; click a row to load its git diff in the Diff Viewer.
- **Analytics tab** — strategy distribution bar chart, VRAM trend, experiments/hour, time since last improvement, longest no-gain streak.
- **Diff viewer** — syntax-highlighted green/red diff of `train.py` changes per experiment, plus a "Current Working Diff" button.
- **Git activity feed** — scrolling commit feed with SHA, message, timestamp, diff stats.
- **Live run.log tail** — last 20 lines of the current experiment's stdout/stderr.
- **progress.png viewer** — auto-refreshes when the file changes.
- **Pause / Resume** — creates/removes a `.dashboard_pause` sentinel file (see optional `program.md` integration below).
- **Multi-branch overlay** — select any `autoresearch/*` branch from the header dropdown; enable Overlay to compare branches on the same chart.

## Quick Start

### 1. Install dependencies

```bash
cd dashboard
pip install -r requirements.txt
```

### 2. Run

```bash
# From the repo root (auto-detects parent directory as repo path)
python dashboard/app.py

# Or specify explicitly:
python dashboard/app.py --repo /path/to/autoresearch --port 5050
```

Open http://localhost:5050 in your browser.

### 3. Generate mock data (if no real experiments yet)

```bash
curl -X POST http://localhost:5050/api/mock/generate
```

This writes a sample `results.tsv` with 20 experiments so you can explore the dashboard UI.

## Docker

```bash
cd dashboard
docker build -t autoresearch-dashboard .
docker run -p 5050:5050 -v /path/to/autoresearch:/repo autoresearch-dashboard
```

## Optional: Pause/Resume integration

Add this single line to the top of `program.md` to enable the pause/resume button:

```markdown
Before starting each new experiment, check if a file `.dashboard_pause` exists
in the repo root. If it does, wait and check again every 30 seconds until it's removed.
```

The dashboard's Pause button creates `.dashboard_pause`; Resume removes it. The autoresearch loop is otherwise untouched.

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/experiments` | GET | Full experiment history JSON |
| `/api/experiments/latest` | GET | Most recent result |
| `/api/experiments/best` | GET | Best val_bpb achieved |
| `/api/progress-image` | GET | Current `progress.png` (binary) |
| `/api/git/commits` | GET | Recent commits (optional `?branch=`) |
| `/api/git/diff/<sha>` | GET | Diff for a specific commit |
| `/api/git/current-diff` | GET | Uncommitted changes |
| `/api/git/branches` | GET | All branches |
| `/api/status` | GET | Agent status: idle/running/paused |
| `/api/control/pause` | POST | Create `.dashboard_pause` |
| `/api/control/resume` | POST | Remove `.dashboard_pause` |
| `/api/train-source` | GET | Current `train.py` contents |
| `/api/run-log` | GET | Parsed run.log data |
| `/api/mock/generate` | POST | Write sample results.tsv for testing |

## Architecture

```
autoresearch/          ← Karpathy's repo (UNTOUCHED)
├── train.py
├── program.md
├── results.tsv        ← read by dashboard
├── run.log            ← read by dashboard
├── progress.png       ← read by dashboard
└── dashboard/         ← this project
    ├── app.py         Flask + Socket.IO server
    ├── watcher.py     Filesystem + git monitoring daemon
    ├── requirements.txt
    ├── Dockerfile
    ├── static/
    │   ├── dashboard.js
    │   └── dashboard.css
    └── templates/
        └── index.html
```

The watcher daemon uses `watchdog` for immediate filesystem events plus a 10-second fallback poll — so no experiment result is ever missed.

## Socket.IO Events

| Event | Direction | Payload |
|---|---|---|
| `new_experiment` | server→client | `{experiment_number, val_bpb, delta_bpb, ...}` |
| `new_commit` | server→client | `{sha, message, timestamp, diff_stat}` |
| `progress_updated` | server→client | `{timestamp}` |
| `run_log_update` | server→client | `{tail: [...lines], crashed: bool}` |
| `agent_status` | server→client | `{status: "running"|"paused"|"idle"}` |
| `full_state` | server→client | `{experiments: [...]}` (on connect) |
| `request_state` | client→server | triggers full_state resend |
