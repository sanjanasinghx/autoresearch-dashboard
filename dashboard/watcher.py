"""
watcher.py — Filesystem + Git monitoring for the autoresearch dashboard.
Pure observer: reads only, never writes to the autoresearch repo.
"""

import os
import re
import time
import threading
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
import git
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_results(tsv_path: str) -> pd.DataFrame:
    """Parse results.tsv into a clean DataFrame with derived columns."""
    path = Path(tsv_path)
    if not path.exists():
        return _empty_results_df()

    try:
        df = pd.read_csv(
            path,
            sep="\t",
            on_bad_lines="warn",
            dtype=str,
        )
    except Exception as e:
        logger.warning(f"Failed to read {tsv_path}: {e}")
        return _empty_results_df()

    if df.empty:
        return _empty_results_df()

    # Normalize column names (strip whitespace)
    df.columns = [c.strip() for c in df.columns]

    # Ensure required columns exist with defaults
    required = {
        "experiment_id": None,
        "val_bpb": None,
        "peak_vram_mb": None,
        "commit_sha": "",
        "timestamp": None,
        "description": "",
    }
    for col, default in required.items():
        if col not in df.columns:
            df[col] = default

    # Coerce numeric types
    df["val_bpb"] = pd.to_numeric(df["val_bpb"], errors="coerce")
    df["peak_vram_mb"] = pd.to_numeric(df["peak_vram_mb"], errors="coerce")

    # Coerce timestamps
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    # Assign sequential experiment number (1-based)
    df = df.reset_index(drop=True)
    df["experiment_number"] = df.index + 1

    # Derived columns
    df["cumulative_best"] = df["val_bpb"].expanding().min()
    prev_best = df["cumulative_best"].shift(1)
    df["delta_bpb"] = df["val_bpb"] - prev_best
    df.loc[df.index == 0, "delta_bpb"] = 0.0  # first experiment has no delta

    # Status classification
    def classify(row):
        if pd.isna(row["val_bpb"]):
            return "crashed"
        if row["delta_bpb"] < -1e-6:
            return "improved"
        return "no_gain"

    df["status"] = df.apply(classify, axis=1)

    return df


def _empty_results_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "experiment_id", "val_bpb", "peak_vram_mb", "commit_sha",
        "timestamp", "description", "experiment_number",
        "cumulative_best", "delta_bpb", "status",
    ])


def parse_run_log(log_path: str) -> dict:
    """Parse run.log to extract metrics, detect crashes, and pull timing info."""
    path = Path(log_path)
    result = {
        "val_bpb": None,
        "peak_vram_mb": None,
        "crashed": False,
        "error_snippet": None,
        "wall_clock_seconds": None,
        "tail": [],
    }

    if not path.exists():
        return result

    try:
        text = path.read_text(errors="replace")
    except Exception as e:
        logger.warning(f"Failed to read {log_path}: {e}")
        return result

    lines = text.splitlines()

    # Extract last occurrence of val_bpb
    for line in reversed(lines):
        m = re.search(r"val_bpb[:\s=]+([0-9]+\.[0-9]+)", line)
        if m:
            result["val_bpb"] = float(m.group(1))
            break

    # Extract last occurrence of peak_vram_mb
    for line in reversed(lines):
        m = re.search(r"peak_vram_mb[:\s=]+([0-9]+(?:\.[0-9]+)?)", line)
        if m:
            result["peak_vram_mb"] = float(m.group(1))
            break

    # Detect crashes (Python tracebacks)
    if "Traceback (most recent call last)" in text or "Error:" in text:
        result["crashed"] = True
        # Find the last error block
        error_lines = []
        capture = False
        for line in reversed(lines):
            if "Error:" in line or "Exception:" in line:
                capture = True
            if capture:
                error_lines.append(line)
            if len(error_lines) >= 10:
                break
        result["error_snippet"] = "\n".join(reversed(error_lines))

    # Wall clock from common patterns: "Training done in 300.2s" or similar
    for line in reversed(lines):
        m = re.search(r"(?:done|elapsed|time)[^\d]*([0-9]+(?:\.[0-9]+)?)\s*s", line, re.I)
        if m:
            result["wall_clock_seconds"] = float(m.group(1))
            break

    # Last 20 lines as tail
    result["tail"] = lines[-20:] if len(lines) >= 20 else lines

    return result


# ---------------------------------------------------------------------------
# Git Tracker
# ---------------------------------------------------------------------------

class GitTracker:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        try:
            self.repo = git.Repo(repo_path)
        except git.InvalidGitRepositoryError:
            self.repo = None
            logger.warning(f"Not a git repo: {repo_path}")

    def _check_repo(self) -> bool:
        return self.repo is not None

    def get_experiment_branches(self) -> list[str]:
        """Return all branches matching autoresearch/* pattern."""
        if not self._check_repo():
            return []
        branches = []
        try:
            for ref in self.repo.references:
                name = ref.name
                if "autoresearch/" in name or name.startswith("autoresearch-"):
                    branches.append(name.replace("origin/", ""))
            # Deduplicate
            seen = set()
            unique = []
            for b in branches:
                if b not in seen:
                    seen.add(b)
                    unique.append(b)
            return unique if unique else self._all_branches()
        except Exception as e:
            logger.warning(f"get_experiment_branches error: {e}")
            return self._all_branches()

    def _all_branches(self) -> list[str]:
        """Fallback: return all local branches."""
        try:
            return [h.name for h in self.repo.heads]
        except Exception:
            return []

    def get_commits(self, branch: str = None, limit: int = 200) -> list[dict]:
        """Return recent commits as list of dicts."""
        if not self._check_repo():
            return []
        try:
            if branch:
                # Try local branch first, then origin/branch
                try:
                    commits_iter = self.repo.iter_commits(branch, max_count=limit)
                except Exception:
                    commits_iter = self.repo.iter_commits(f"origin/{branch}", max_count=limit)
            else:
                commits_iter = self.repo.iter_commits(max_count=limit)

            results = []
            for commit in commits_iter:
                # Parse val_bpb from commit message
                val_bpb = None
                m = re.search(r"val_bpb[:\s=]+([0-9]+\.[0-9]+)", commit.message)
                if m:
                    val_bpb = float(m.group(1))

                # Diff stats — use gitpython's built-in stats (accurate +/- counts)
                diff_stat = {"files_changed": 0, "insertions": 0, "deletions": 0}
                try:
                    stats = commit.stats
                    diff_stat["files_changed"] = len(stats.files)
                    diff_stat["insertions"] = stats.total["insertions"]
                    diff_stat["deletions"] = stats.total["deletions"]
                except Exception:
                    pass

                results.append({
                    "sha": commit.hexsha[:8],
                    "full_sha": commit.hexsha,
                    "message": commit.message.strip(),
                    "timestamp": datetime.fromtimestamp(commit.committed_date).isoformat(),
                    "author": commit.author.name,
                    "diff_stat": diff_stat,
                    "val_bpb": val_bpb,
                })
            return results
        except Exception as e:
            logger.warning(f"get_commits error: {e}")
            return []

    def get_commit_diff(self, sha: str) -> str:
        """Return the diff for a specific commit."""
        if not self._check_repo():
            return ""
        try:
            commit = self.repo.commit(sha)
            if commit.parents:
                diff = commit.parents[0].diff(commit, create_patch=True)
                parts = []
                for d in diff:
                    try:
                        patch = d.diff
                        if isinstance(patch, bytes):
                            patch = patch.decode("utf-8", errors="replace")
                        parts.append(f"--- {d.a_path}\n+++ {d.b_path}\n{patch}")
                    except Exception:
                        pass
                return "\n".join(parts)
            return "(initial commit, no diff)"
        except Exception as e:
            return f"Error getting diff: {e}"

    def get_current_diff(self) -> str:
        """Return uncommitted changes (what the agent is working on right now)."""
        if not self._check_repo():
            return ""
        try:
            diff = self.repo.git.diff()
            return diff if diff else "(no uncommitted changes)"
        except Exception as e:
            return f"Error: {e}"

    def get_current_branch(self) -> str:
        if not self._check_repo():
            return "unknown"
        try:
            return self.repo.active_branch.name
        except Exception:
            return "detached HEAD"


# ---------------------------------------------------------------------------
# Filesystem Watcher
# ---------------------------------------------------------------------------

class _FileEventHandler(FileSystemEventHandler):
    def __init__(self, watcher):
        self.watcher = watcher

    def on_modified(self, event):
        if event.is_directory:
            return
        self.watcher._handle_file_change(event.src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        self.watcher._handle_file_change(event.src_path)


class ExperimentWatcher:
    def __init__(self, repo_path: str, socketio):
        self.repo_path = repo_path
        self.socketio = socketio
        self.last_log_mtime = 0
        self.last_png_mtime = 0
        self.observer = None
        self._stop_event = threading.Event()
        self._poll_thread = None
        # Initialize to current count so we don't re-emit old experiments on startup
        tsv_path = os.path.join(repo_path, "results.tsv")
        self.last_experiment_count = len(parse_results(tsv_path))
        logger.info(f"ExperimentWatcher initialized with {self.last_experiment_count} existing experiments")

    def start(self):
        """Start both the watchdog observer and fallback polling thread."""
        # watchdog observer
        try:
            self.observer = Observer()
            handler = _FileEventHandler(self)
            self.observer.schedule(handler, self.repo_path, recursive=False)
            self.observer.start()
            logger.info(f"Watchdog observer started on {self.repo_path}")
        except Exception as e:
            logger.warning(f"Failed to start watchdog: {e}")

        # Fallback polling thread
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self):
        self._stop_event.set()
        if self.observer:
            self.observer.stop()
            self.observer.join()

    def _handle_file_change(self, path: str):
        basename = os.path.basename(path)
        if basename == "results.tsv":
            self._check_results()
        elif basename == "run.log":
            self._check_log()
        elif basename == "progress.png":
            self._emit_progress_updated()

    def _poll_loop(self):
        """Fallback: poll every 10 seconds regardless of watchdog."""
        while not self._stop_event.wait(10):
            try:
                self._check_results()
                self._check_log()
                self._check_png()
                self._check_new_commits()
                self._emit_agent_status()
            except Exception as e:
                logger.warning(f"Poll loop error: {e}")

    def _check_results(self):
        tsv_path = os.path.join(self.repo_path, "results.tsv")
        df = parse_results(tsv_path)
        if len(df) > self.last_experiment_count:
            new_rows = df.iloc[self.last_experiment_count:]
            for _, row in new_rows.iterrows():
                payload = self._row_to_dict(row)
                self.socketio.emit("new_experiment", payload)
            self.last_experiment_count = len(df)

    def _row_to_dict(self, row) -> dict:
        d = {}
        for col in row.index:
            val = row[col]
            # Check for NA/NaN (only for non-strings — isna("") raises nothing but is False)
            if not isinstance(val, str) and pd.isna(val):
                d[col] = None
            elif hasattr(val, "isoformat"):
                d[col] = val.isoformat()
            elif hasattr(val, "item"):
                # numpy scalar (int64, float64, bool_) → native Python type
                d[col] = val.item()
            else:
                try:
                    d[col] = float(val) if isinstance(val, (int, float)) else str(val)
                except Exception:
                    d[col] = str(val)
        return d

    def _check_log(self):
        log_path = os.path.join(self.repo_path, "run.log")
        try:
            mtime = os.path.getmtime(log_path)
            if mtime > self.last_log_mtime:
                self.last_log_mtime = mtime
                data = parse_run_log(log_path)
                self.socketio.emit("run_log_update", {
                    "tail": data["tail"],
                    "crashed": data["crashed"],
                })
        except FileNotFoundError:
            pass

    def _check_png(self):
        png_path = os.path.join(self.repo_path, "progress.png")
        try:
            mtime = os.path.getmtime(png_path)
            if mtime > self.last_png_mtime:
                self.last_png_mtime = mtime
                self._emit_progress_updated()
        except FileNotFoundError:
            pass

    def _emit_progress_updated(self):
        self.socketio.emit("progress_updated", {"timestamp": datetime.now().isoformat()})

    _last_commit_count = 0

    def _check_new_commits(self):
        tracker = GitTracker(self.repo_path)
        commits = tracker.get_commits(limit=5)
        if commits:
            count = len(commits)
            if count != self._last_commit_count:
                self._last_commit_count = count
                self.socketio.emit("new_commit", commits[0])

    def _emit_agent_status(self):
        pause_file = os.path.join(self.repo_path, ".dashboard_pause")
        log_path = os.path.join(self.repo_path, "run.log")
        if os.path.exists(pause_file):
            status = "paused"
        else:
            # Running if run.log was modified in last 5 minutes
            try:
                mtime = os.path.getmtime(log_path)
                if time.time() - mtime < 300:
                    status = "running"
                else:
                    status = "idle"
            except FileNotFoundError:
                status = "idle"
        self.socketio.emit("agent_status", {"status": status})
