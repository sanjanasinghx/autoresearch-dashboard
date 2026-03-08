"""
run_mock_agent.py — Simulates an autoresearch agent loop for dashboard testing.

Appends one row to results.tsv every 8 seconds, mimicking the agent's behavior.
Checks for .dashboard_pause sentinel (mirrors the pause/resume mechanism).

Usage:
    python dashboard/run_mock_agent.py --repo /path/to/autoresearch
"""

import os
import sys
import time
import random
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--repo", default=str(Path(__file__).parent.parent))
parser.add_argument("--interval", type=float, default=8.0, help="Seconds between experiments")
parser.add_argument("--count", type=int, default=50, help="Number of experiments to run")
args = parser.parse_args()

REPO = os.path.abspath(args.repo)
RESULTS_TSV = os.path.join(REPO, "results.tsv")
RUN_LOG = os.path.join(REPO, "run.log")
PAUSE_FILE = os.path.join(REPO, ".dashboard_pause")

DESCRIPTIONS = [
    "increase learning rate", "add dropout", "try cosine schedule",
    "reduce batch size", "add weight decay", "change activation to gelu",
    "try adamw optimizer", "increase model depth by 2 layers",
    "add skip connections", "tune warmup steps to 200",
    "reduce head dimension", "add layer norm before attention",
    "try muon optimizer", "increase embedding dim",
    "add value embeddings", "reduce weight decay",
    "try sliding window attention", "increase batch size",
    "add gradient clipping", "reduce dropout rate",
]

def ensure_header():
    p = Path(RESULTS_TSV)
    if not p.exists() or p.stat().st_size == 0:
        p.write_text("experiment_id\tval_bpb\tpeak_vram_mb\tcommit_sha\ttimestamp\tdescription\n")

def get_next_id():
    p = Path(RESULTS_TSV)
    if not p.exists():
        return 1
    lines = p.read_text().strip().splitlines()
    return max(1, len(lines))  # subtract header

def append_result(exp_id, val_bpb, vram, sha, desc):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"{exp_id}\t{val_bpb:.4f}\t{vram}\t{sha}\t{ts}\t{desc}"
    with open(RESULTS_TSV, "a") as f:
        f.write(line + "\n")
    print(f"[mock] exp {exp_id}: val_bpb={val_bpb:.4f} vram={vram}MB  '{desc}'")

def write_log(exp_id, val_bpb, vram, crashed=False):
    with open(RUN_LOG, "w") as f:
        if crashed:
            f.write(f"Experiment {exp_id} starting...\n")
            f.write("Traceback (most recent call last):\n")
            f.write("  File 'train.py', line 42, in forward\n")
            f.write("RuntimeError: CUDA out of memory\n")
        else:
            f.write(f"Experiment {exp_id} starting...\n")
            f.write(f"Training for 300 seconds...\n")
            f.write(f"step 0100 (10.0%) | loss: 2.345678 | lrm: 0.20\n")
            f.write(f"step 0500 (50.0%) | loss: 1.923456 | lrm: 1.00\n")
            f.write(f"step 0900 (90.0%) | loss: 1.789012 | lrm: 0.20\n")
            f.write(f"---\n")
            f.write(f"val_bpb:          {val_bpb:.6f}\n")
            f.write(f"peak_vram_mb:     {vram:.1f}\n")
            f.write(f"training_seconds: 300.0\n")

ensure_header()
bpb = 1.85
best_bpb = bpb

print(f"[mock] Starting mock agent loop: {args.count} experiments, {args.interval}s interval")
print(f"[mock] Repo: {REPO}")
print(f"[mock] results.tsv: {RESULTS_TSV}")
print()

for i in range(args.count):
    exp_id = get_next_id()

    # Check pause sentinel
    while os.path.exists(PAUSE_FILE):
        print(f"[mock] Paused — waiting for .dashboard_pause to be removed...")
        time.sleep(3)

    desc = DESCRIPTIONS[i % len(DESCRIPTIONS)]
    crashed = random.random() < 0.08  # 8% crash rate

    if crashed:
        write_log(exp_id, 0, 0, crashed=True)
        time.sleep(args.interval * 0.3)
        sha = "".join(random.choices("0123456789abcdef", k=8))
        # Write crashed row (no val_bpb)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(RESULTS_TSV, "a") as f:
            f.write(f"{exp_id}\t\t\t{sha}\t{ts}\t{desc} [CRASHED]\n")
        print(f"[mock] exp {exp_id}: CRASHED  '{desc}'")
    else:
        # Simulate training progress
        delta = random.gauss(-0.01, 0.06)  # slight bias toward improvement
        bpb = max(1.0, bpb + delta)
        vram = random.randint(8000, 16000)
        sha = "".join(random.choices("0123456789abcdef", k=8))

        # Write partial log while training
        with open(RUN_LOG, "w") as f:
            f.write(f"Experiment {exp_id} starting...\n")
            f.write(f"Training... (this will update every few seconds)\n")

        time.sleep(args.interval * 0.8)

        write_log(exp_id, bpb, vram)
        time.sleep(args.interval * 0.2)
        append_result(exp_id, bpb, vram, sha, desc)

        if bpb < best_bpb:
            best_bpb = bpb
            print(f"[mock]   *** NEW BEST: {best_bpb:.4f} ***")

print(f"\n[mock] Done. Ran {args.count} experiments. Best val_bpb: {best_bpb:.4f}")
