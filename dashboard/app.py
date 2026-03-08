"""
app.py — Flask + Socket.IO server for the autoresearch monitoring dashboard.
Pure observer: reads filesystem state, never modifies autoresearch files.

Usage:
    python app.py --repo /path/to/autoresearch --port 5050
"""

import os
import sys
import argparse
import logging
import threading
import time
from pathlib import Path
from datetime import datetime

from flask import Flask, jsonify, render_template, send_file, abort, request
from flask_socketio import SocketIO, emit as ws_emit

from watcher import (
    parse_results,
    parse_run_log,
    GitTracker,
    ExperimentWatcher,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser(description="Autoresearch monitoring dashboard")
parser.add_argument(
    "--repo",
    default=os.environ.get("AUTORESEARCH_REPO", str(Path(__file__).parent.parent)),
    help="Path to the autoresearch repo root",
)
parser.add_argument("--port", type=int, default=5050, help="Port to serve on")
parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
args, _ = parser.parse_known_args()

REPO_PATH = os.path.abspath(args.repo)
logger.info(f"Watching repo: {REPO_PATH}")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = "autoresearch-dashboard-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Lazily-started watcher
_watcher: ExperimentWatcher | None = None
_watcher_lock = threading.Lock()


def get_watcher() -> ExperimentWatcher:
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = ExperimentWatcher(REPO_PATH, socketio)
            _watcher.start()
    return _watcher


def get_tracker() -> GitTracker:
    return GitTracker(REPO_PATH)


# ---------------------------------------------------------------------------
# Helper: safe path resolution inside repo
# ---------------------------------------------------------------------------

def repo_file(name: str) -> str:
    return os.path.join(REPO_PATH, name)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# -- Experiments --

@app.route("/api/experiments")
def api_experiments():
    branch = request.args.get("branch")
    df = parse_results(repo_file("results.tsv"))
    if df.empty:
        return jsonify({"experiments": [], "total": 0})
    records = df.where(df.notna(), None).to_dict(orient="records")
    # Convert timestamps
    for r in records:
        if r.get("timestamp") and hasattr(r["timestamp"], "isoformat"):
            r["timestamp"] = r["timestamp"].isoformat()
    return jsonify({"experiments": records, "total": len(records)})


@app.route("/api/experiments/latest")
def api_experiments_latest():
    df = parse_results(repo_file("results.tsv"))
    if df.empty:
        return jsonify({"experiment": None})
    row = df.iloc[-1].where(df.iloc[-1].notna(), None).to_dict()
    if row.get("timestamp") and hasattr(row["timestamp"], "isoformat"):
        row["timestamp"] = row["timestamp"].isoformat()
    return jsonify({"experiment": row})


@app.route("/api/experiments/best")
def api_experiments_best():
    df = parse_results(repo_file("results.tsv"))
    if df.empty or df["val_bpb"].isna().all():
        return jsonify({"best": None})
    idx = df["val_bpb"].idxmin()
    row = df.loc[idx].where(df.loc[idx].notna(), None).to_dict()
    if row.get("timestamp") and hasattr(row["timestamp"], "isoformat"):
        row["timestamp"] = row["timestamp"].isoformat()
    return jsonify({"best": row})


# -- Progress image --

@app.route("/api/progress-image")
def api_progress_image():
    png_path = repo_file("progress.png")
    if not os.path.exists(png_path):
        abort(404)
    return send_file(png_path, mimetype="image/png")


# -- Git --

@app.route("/api/git/commits")
def api_git_commits():
    branch = request.args.get("branch")
    limit = int(request.args.get("limit", 50))
    tracker = get_tracker()
    commits = tracker.get_commits(branch=branch, limit=limit)
    return jsonify({"commits": commits})


@app.route("/api/git/diff/<sha>")
def api_git_diff(sha):
    tracker = get_tracker()
    diff = tracker.get_commit_diff(sha)
    return jsonify({"sha": sha, "diff": diff})


@app.route("/api/git/current-diff")
def api_git_current_diff():
    tracker = get_tracker()
    diff = tracker.get_current_diff()
    return jsonify({"diff": diff})


@app.route("/api/git/branches")
def api_git_branches():
    tracker = get_tracker()
    branches = tracker.get_experiment_branches()
    current = tracker.get_current_branch()
    return jsonify({"branches": branches, "current": current})


# -- Agent status --

@app.route("/api/status")
def api_status():
    pause_file = repo_file(".dashboard_pause")
    log_path = repo_file("run.log")
    if os.path.exists(pause_file):
        status = "paused"
    else:
        try:
            mtime = os.path.getmtime(log_path)
            if time.time() - mtime < 300:
                status = "running"
            else:
                status = "idle"
        except FileNotFoundError:
            status = "idle"

    df = parse_results(repo_file("results.tsv"))
    total = len(df)
    best_bpb = float(df["val_bpb"].min()) if not df.empty and not df["val_bpb"].isna().all() else None

    return jsonify({
        "status": status,
        "total_experiments": total,
        "best_val_bpb": best_bpb,
        "repo_path": REPO_PATH,
        "paused": os.path.exists(pause_file),
    })


# -- Controls --

@app.route("/api/control/pause", methods=["POST"])
def api_control_pause():
    pause_file = repo_file(".dashboard_pause")
    Path(pause_file).touch()
    socketio.emit("agent_status", {"status": "paused"})
    return jsonify({"ok": True, "status": "paused"})


@app.route("/api/control/resume", methods=["POST"])
def api_control_resume():
    pause_file = repo_file(".dashboard_pause")
    try:
        os.remove(pause_file)
    except FileNotFoundError:
        pass
    socketio.emit("agent_status", {"status": "running"})
    return jsonify({"ok": True, "status": "running"})


# -- Train source --

@app.route("/api/train-source")
def api_train_source():
    train_path = repo_file("train.py")
    if not os.path.exists(train_path):
        abort(404)
    content = Path(train_path).read_text(errors="replace")
    return jsonify({"content": content, "path": train_path})


# -- Run log --

@app.route("/api/run-log")
def api_run_log():
    log_path = repo_file("run.log")
    data = parse_run_log(log_path)
    return jsonify(data)


# -- Mock data (for testing with no real results.tsv) --

@app.route("/api/mock/generate", methods=["POST"])
def api_mock_generate():
    """Generate a sample results.tsv for testing."""
    import random, math
    lines = ["experiment_id\tval_bpb\tpeak_vram_mb\tcommit_sha\ttimestamp\tdescription"]
    bpb = 1.85
    for i in range(1, 21):
        delta = random.uniform(-0.05, 0.08)
        bpb = max(1.0, bpb + delta)
        vram = random.randint(8000, 16000)
        sha = "".join(random.choices("0123456789abcdef", k=8))
        ts = f"2025-03-{i:02d}T12:{i:02d}:00"
        descs = [
            "increase learning rate", "add dropout", "try cosine schedule",
            "reduce batch size", "add weight decay", "change activation",
            "try different optimizer", "increase model depth", "add skip connections",
            "tune warmup steps",
        ]
        desc = descs[i % len(descs)]
        lines.append(f"{i}\t{bpb:.4f}\t{vram}\t{sha}\t{ts}\t{desc}")
    tsv_path = repo_file("results.tsv")
    Path(tsv_path).write_text("\n".join(lines))
    return jsonify({"ok": True, "rows": len(lines) - 1, "path": tsv_path})


# ---------------------------------------------------------------------------
# Socket.IO events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    # Trigger watcher start on first connection
    get_watcher()
    _send_full_state()


def _send_full_state():
    """Send current full state to the requesting client (use ws_emit inside socket context)."""
    df = parse_results(repo_file("results.tsv"))
    records = []
    if not df.empty:
        records = df.where(df.notna(), None).to_dict(orient="records")
        for r in records:
            if r.get("timestamp") and hasattr(r["timestamp"], "isoformat"):
                r["timestamp"] = r["timestamp"].isoformat()
            # Ensure all numeric fields are plain Python types
            for k, v in r.items():
                if hasattr(v, "item"):  # numpy scalar
                    r[k] = v.item()
    ws_emit("full_state", {"experiments": records})

    tracker = get_tracker()
    commits = tracker.get_commits(limit=30)
    ws_emit("git_history", {"commits": commits})


@socketio.on("request_state")
def on_request_state(data=None):
    """Client requests full refresh (e.g., after reconnect)."""
    _send_full_state()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info(f"Starting dashboard on http://localhost:{args.port}")
    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)
