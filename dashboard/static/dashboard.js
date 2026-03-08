/**
 * dashboard.js — Frontend logic for the Autoresearch monitoring dashboard.
 * Plotly.js + Socket.IO loaded from CDN at end of body.
 */

"use strict";

// ═══════════════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════════════

const state = {
  experiments: [],
  branchData: {},
  branches: [],
  currentBranch: null,
  overlayMode: false,
  agentStatus: "idle",
  paused: false,
  sortKey: "experiment_number",
  sortAsc: true,
  chartInited: false,
};

const BRANCH_COLORS = ["#58a6ff", "#3fb950", "#d29922", "#bc8cff", "#79c0ff", "#f78166"];

// ═══════════════════════════════════════════════════════════
// Boot — runs after all scripts (CDN + dashboard.js) are loaded
// ═══════════════════════════════════════════════════════════

window.addEventListener("load", async () => {
  console.log("[dashboard] window.load — booting");

  // Wire Socket.IO (io() is now available since scripts are at end of body)
  initSocketIO();

  // Fetch initial data via REST (reliable fallback independent of Socket.IO)
  await Promise.all([loadBranches(), loadExperiments(), loadStatus()]);
  await loadGitCommits();

  // Init Plotly chart — wrapped so a CDN failure doesn't block the table
  tryInitChart();
  renderAll();

  setInterval(loadStatus, 15000);
  console.log("[dashboard] boot complete, experiments:", state.experiments.length);
});

// ═══════════════════════════════════════════════════════════
// Socket.IO
// ═══════════════════════════════════════════════════════════

function initSocketIO() {
  if (typeof io === "undefined") {
    console.warn("[dashboard] Socket.IO CDN not loaded — live updates disabled");
    return;
  }

  const socket = window._socket = io({ reconnectionDelay: 2000 });

  socket.on("connect", () => {
    console.log("[dashboard] WS connected:", socket.id);
    socket.emit("request_state");
  });

  socket.on("disconnect", () => setStatus("idle"));

  socket.on("full_state", (data) => {
    console.log("[dashboard] full_state received:", data.experiments?.length, "experiments");
    if (Array.isArray(data.experiments)) {
      state.experiments = data.experiments;
      tryInitChart();
      renderAll();
    }
  });

  socket.on("git_history", (data) => {
    if (Array.isArray(data.commits)) renderGitFeed(data.commits);
  });

  socket.on("new_experiment", (exp) => {
    state.experiments.push(exp);
    renderChart();
    renderTable();
    updateStats();
    toast(`Exp #${exp.experiment_number}: val_bpb=${fmtBpb(exp.val_bpb)} (${exp.status})`,
          exp.status === "improved" ? "success" : exp.status === "crashed" ? "error" : "warn");
  });

  socket.on("new_commit", (commit) => prependCommit(commit));
  socket.on("progress_updated", refreshProgressImage);
  socket.on("run_log_update", (data) => updateLiveLog(data.tail || []));
  socket.on("agent_status", (data) => setStatus(data.status));
}

// ═══════════════════════════════════════════════════════════
// Data loaders
// ═══════════════════════════════════════════════════════════

async function loadBranches() {
  try {
    const r = await fetch("/api/git/branches");
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    state.branches = d.branches || [];
    state.currentBranch = d.current || null;
    console.log("[dashboard] branches:", state.branches);
    renderBranchSelect();
  } catch (e) {
    console.warn("[dashboard] loadBranches failed:", e);
    // Show current branch fallback
    state.branches = ["main"];
    state.currentBranch = "main";
    renderBranchSelect();
  }
}

async function loadExperiments(branch) {
  const url = branch
    ? `/api/experiments?branch=${encodeURIComponent(branch)}`
    : "/api/experiments";
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    console.log("[dashboard] loadExperiments:", d.total, "experiments");
    state.experiments = d.experiments || [];
    if (branch) state.branchData[branch] = state.experiments;
  } catch (e) {
    console.warn("[dashboard] loadExperiments failed:", e);
  }
}

async function loadGitCommits(branch) {
  const url = branch
    ? `/api/git/commits?branch=${encodeURIComponent(branch)}`
    : "/api/git/commits";
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    console.log("[dashboard] loadGitCommits:", d.commits?.length, "commits");
    renderGitFeed(d.commits || []);
  } catch (e) {
    console.warn("[dashboard] loadGitCommits failed:", e);
  }
}

async function loadStatus() {
  try {
    const r = await fetch("/api/status");
    if (!r.ok) return;
    const d = await r.json();
    setStatus(d.status);
    state.paused = d.paused;
    const btn = document.getElementById("pause-btn");
    btn.textContent = d.paused ? "Resume" : "Pause";
    btn.className = d.paused ? "btn success" : "btn danger";
  } catch (e) {}
}

// ═══════════════════════════════════════════════════════════
// Branch selector
// ═══════════════════════════════════════════════════════════

function renderBranchSelect() {
  const sel = document.getElementById("branch-select");
  sel.innerHTML = "";
  const branches = state.branches.length ? state.branches : ["(none)"];
  branches.forEach((b) => {
    const opt = document.createElement("option");
    opt.value = b;
    opt.textContent = b;
    if (b === state.currentBranch) opt.selected = true;
    sel.appendChild(opt);
  });
}

document.getElementById("branch-select").addEventListener("change", async (e) => {
  state.currentBranch = e.target.value;
  await loadExperiments(state.currentBranch);
  await loadGitCommits(state.currentBranch);
  renderAll();
});

document.getElementById("overlay-mode").addEventListener("change", async (e) => {
  state.overlayMode = e.target.checked;
  if (state.overlayMode) {
    for (const b of state.branches) {
      if (!state.branchData[b]) await loadExperiments(b);
    }
  }
  renderChart();
  toggleBranchLegend();
});

function toggleBranchLegend() {
  const legend = document.getElementById("branch-legend");
  if (!state.overlayMode || state.branches.length < 2) {
    legend.style.display = "none";
    return;
  }
  legend.style.display = "flex";
  legend.innerHTML = state.branches.map((b, i) =>
    `<div class="legend-item">
       <div class="legend-dot" style="background:${BRANCH_COLORS[i % BRANCH_COLORS.length]};"></div>
       <span>${b}</span>
     </div>`
  ).join("");
}

// ═══════════════════════════════════════════════════════════
// Chart (Plotly) — lazy init, guarded against CDN failure
// ═══════════════════════════════════════════════════════════

function tryInitChart() {
  if (state.chartInited) return true;
  if (typeof Plotly === "undefined") {
    console.warn("[dashboard] Plotly not loaded — chart disabled");
    document.getElementById("main-chart").innerHTML =
      `<div class="empty-state" style="height:280px;">
         <h3>Chart unavailable</h3>
         <p>Plotly CDN could not be loaded. Data is still visible in the table below.</p>
       </div>`;
    return false;
  }
  try {
    Plotly.newPlot("main-chart", [], buildLayout(), {
      responsive: true,
      displayModeBar: true,
      modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"],
      displaylogo: false,
    });
    state.chartInited = true;
    console.log("[dashboard] Plotly chart initialized");
    return true;
  } catch (err) {
    console.error("[dashboard] Plotly.newPlot failed:", err);
    return false;
  }
}

function buildLayout() {
  return {
    paper_bgcolor: "transparent",
    plot_bgcolor: "#0d1117",
    font: { family: "JetBrains Mono, monospace", color: "#8b949e", size: 11 },
    margin: { l: 55, r: 20, t: 10, b: 45 },
    xaxis: {
      title: "Experiment #",
      gridcolor: "#21262d",
      zerolinecolor: "#30363d",
      color: "#8b949e",
    },
    yaxis: {
      title: "val_bpb (lower = better)",
      gridcolor: "#21262d",
      zerolinecolor: "#30363d",
      color: "#8b949e",
      autorange: "reversed",
    },
    legend: { bgcolor: "transparent", font: { color: "#e6edf3" } },
    hoverlabel: {
      bgcolor: "#161b22",
      bordercolor: "#30363d",
      font: { family: "JetBrains Mono, monospace", color: "#e6edf3" },
    },
  };
}

function buildTraces(experiments, prefix, colorIdx) {
  if (!experiments || experiments.length === 0) return [];

  const color = BRANCH_COLORS[colorIdx % BRANCH_COLORS.length];
  const name = prefix || "Experiments";

  // Filter to valid (non-crashed) for the best line
  const xs = experiments.map((e) => e.experiment_number);

  const dotColors = experiments.map((e) => {
    if (e.status === "crashed") return "#d29922";
    if (e.status === "improved") return "#3fb950";
    return "#484f58";
  });

  const scatterTrace = {
    type: "scatter",
    mode: "markers",
    name,
    x: xs,
    y: experiments.map((e) => e.val_bpb),
    marker: {
      color: dotColors,
      size: 9,
      symbol: experiments.map((e) => e.status === "crashed" ? "x" : "circle"),
      line: { color: "#21262d", width: 1 },
    },
    text: experiments.map((e) =>
      `#${e.experiment_number} — ${e.description || ""}<br>` +
      `val_bpb: ${fmtBpb(e.val_bpb)}<br>` +
      `Δ: ${fmtDelta(e.delta_bpb)}<br>` +
      `VRAM: ${e.peak_vram_mb ? Math.round(e.peak_vram_mb) + " MB" : "N/A"}<br>` +
      `Status: ${e.status}`
    ),
    hovertemplate: "%{text}<extra></extra>",
    showlegend: state.overlayMode,
  };

  const bestTrace = {
    type: "scatter",
    mode: "lines",
    name: `${name} best`,
    x: xs,
    y: experiments.map((e) => e.cumulative_best),
    line: { color, width: 2, dash: "dash" },
    hovertemplate: "Best so far: %{y:.4f}<extra></extra>",
    showlegend: state.overlayMode,
  };

  return [scatterTrace, bestTrace];
}

function renderChart() {
  if (!tryInitChart()) return;
  try {
    let traces = [];
    if (state.overlayMode && state.branches.length > 1) {
      state.branches.forEach((b, i) => {
        traces = traces.concat(buildTraces(state.branchData[b] || [], b, i));
      });
    } else {
      traces = buildTraces(state.experiments, null, 0);
    }
    Plotly.react("main-chart", traces, buildLayout(), { responsive: true, displaylogo: false });
  } catch (err) {
    console.error("[dashboard] renderChart error:", err);
  }
}

function appendChartDot() {
  renderChart(); // re-render full chart with updated state.experiments
}

function resetZoom() {
  if (!state.chartInited) return;
  Plotly.relayout("main-chart", { "xaxis.autorange": true, "yaxis.autorange": "reversed" });
}

// ═══════════════════════════════════════════════════════════
// Stats bar
// ═══════════════════════════════════════════════════════════

function updateStats() {
  const exps = state.experiments;
  const total = exps.length;
  const valid = exps.filter((e) => e.val_bpb != null && !isNaN(e.val_bpb));
  const improved = exps.filter((e) => e.status === "improved");
  const crashed = exps.filter((e) => e.status === "crashed");

  document.getElementById("stat-total").textContent = total || "0";

  const bestVal = valid.length ? Math.min(...valid.map((e) => Number(e.val_bpb))) : null;
  document.getElementById("stat-best").textContent = bestVal != null ? bestVal.toFixed(4) : "—";

  const improveRate = total ? ((improved.length / total) * 100).toFixed(1) + "%" : "—";
  const rateEl = document.getElementById("stat-impr-rate");
  rateEl.textContent = improveRate;
  rateEl.className = "stat-value " + (parseFloat(improveRate) > 30 ? "good" : "warn");

  document.getElementById("stat-crashes").textContent = crashed.length;

  const deltas = improved.map((e) => Number(e.delta_bpb)).filter((d) => !isNaN(d) && d < 0);
  const avgDelta = deltas.length ? deltas.reduce((a, b) => a + b, 0) / deltas.length : null;
  document.getElementById("stat-avg-delta").textContent =
    avgDelta != null ? avgDelta.toFixed(4) : "—";

  // Runtime
  const times = exps
    .filter((e) => e.timestamp)
    .map((e) => new Date(e.timestamp).getTime())
    .filter((t) => !isNaN(t))
    .sort((a, b) => a - b);

  if (times.length >= 2) {
    document.getElementById("stat-runtime").textContent =
      msToHuman(times[times.length - 1] - times[0]);
  } else {
    document.getElementById("stat-runtime").textContent = "—";
  }

  // ETA to next run (~5 min budget)
  const eta = document.getElementById("eta-label");
  if (times.length > 0) {
    const remaining = times[times.length - 1] + 5 * 60 * 1000 - Date.now();
    eta.textContent = remaining > 0 && remaining < 7 * 60 * 1000
      ? `next ~${Math.ceil(remaining / 60000)}m`
      : "";
  }
}

// ═══════════════════════════════════════════════════════════
// Experiment table
// ═══════════════════════════════════════════════════════════

function renderTable() {
  const tbody = document.getElementById("exp-tbody");
  const exps = [...state.experiments];

  if (exps.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7">
      <div class="empty-state">
        <h3>No experiments yet</h3>
        <p>Generate mock data: <code>curl -X POST http://localhost:5050/api/mock/generate</code></p>
      </div>
    </td></tr>`;
    return;
  }

  exps.sort((a, b) => {
    let av = a[state.sortKey], bv = b[state.sortKey];
    if (av == null) av = state.sortAsc ? Infinity : -Infinity;
    if (bv == null) bv = state.sortAsc ? Infinity : -Infinity;
    return state.sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
  });

  const validBpbs = exps.filter((e) => e.val_bpb != null).map((e) => Number(e.val_bpb));
  const bestBpb = validBpbs.length ? Math.min(...validBpbs) : null;

  tbody.innerHTML = exps.map((e) => buildRow(e, bestBpb)).join("");

  tbody.querySelectorAll("tr[data-sha]").forEach((row) => {
    row.addEventListener("click", () => {
      document.querySelectorAll("#exp-tbody tr.selected").forEach((r) => r.classList.remove("selected"));
      row.classList.add("selected");
      if (row.dataset.sha) loadDiff(row.dataset.sha);
    });
  });
}

function buildRow(e, bestBpb) {
  const bpb = e.val_bpb != null ? Number(e.val_bpb) : null;
  const isBest = bpb != null && bpb === bestBpb;
  const sha = e.commit_sha || "";
  const shaShort = sha.slice(0, 8);
  const delta = e.delta_bpb != null ? Number(e.delta_bpb) : null;
  const deltaStr = delta == null || isNaN(delta) || delta === 0
    ? "—"
    : (delta > 0 ? "+" : "") + delta.toFixed(4);
  const deltaCls = delta != null && delta < -1e-6 ? "delta-neg" : delta != null && delta > 1e-6 ? "delta-pos" : "";
  const status = e.status || "no_gain";

  return `<tr class="${isBest ? "best-row" : ""}" data-sha="${sha}">
    <td>${e.experiment_number ?? "—"}</td>
    <td>${bpb != null ? bpb.toFixed(4) : "—"}${isBest ? ' <span style="color:var(--green)">★</span>' : ""}</td>
    <td class="${deltaCls}">${deltaStr}</td>
    <td>${e.peak_vram_mb != null ? Math.round(Number(e.peak_vram_mb)) : "—"}</td>
    <td><span class="badge ${status}">${status}</span></td>
    <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;" title="${escHtml(e.description || "")}">${escHtml((e.description || "").slice(0, 60))}</td>
    <td>${shaShort ? `<a class="sha-link" href="#" onclick="loadDiff('${sha}');return false;">${shaShort}</a>` : "—"}</td>
  </tr>`;
}

function prependTableRow() {
  renderTable();
}

function sortTable(key) {
  state.sortKey === key ? (state.sortAsc = !state.sortAsc) : (state.sortKey = key, state.sortAsc = true);
  renderTable();
}

// ═══════════════════════════════════════════════════════════
// Diff viewer
// ═══════════════════════════════════════════════════════════

async function loadDiff(sha) {
  switchTab("diff", document.querySelectorAll(".tab-btn")[2]);
  document.getElementById("diff-label").textContent = `Diff for ${sha.slice(0, 8)}…`;
  document.getElementById("diff-content").innerHTML = '<span style="color:var(--text-muted)">Loading…</span>';
  try {
    const r = await fetch(`/api/git/diff/${sha}`);
    const d = await r.json();
    renderDiff(d.diff || "(empty diff)");
  } catch (e) {
    document.getElementById("diff-content").textContent = `Error: ${e}`;
  }
}

async function loadCurrentDiff() {
  document.getElementById("diff-label").textContent = "Current uncommitted changes";
  document.getElementById("diff-content").innerHTML = '<span style="color:var(--text-muted)">Loading…</span>';
  try {
    const r = await fetch("/api/git/current-diff");
    const d = await r.json();
    renderDiff(d.diff || "(no uncommitted changes)");
  } catch (e) {
    document.getElementById("diff-content").textContent = `Error: ${e}`;
  }
}

function renderDiff(diff) {
  const el = document.getElementById("diff-content");
  el.innerHTML = diff.split("\n").map((line) => {
    let cls = "diff-ctx";
    if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) cls = "diff-hdr";
    else if (line.startsWith("+")) cls = "diff-add";
    else if (line.startsWith("-")) cls = "diff-del";
    return `<span class="${cls}">${escHtml(line)}</span>`;
  }).join("\n");
}

// ═══════════════════════════════════════════════════════════
// Analytics
// ═══════════════════════════════════════════════════════════

function renderAnalytics() {
  const exps = state.experiments;
  if (!exps.length) return;

  // Strategy distribution
  const cats = {};
  const keywords = {
    "learning rate": ["learning_rate", "lr", "learning rate"],
    "dropout": ["dropout"],
    "scheduler": ["cosine", "warmup", "schedule"],
    "batch size": ["batch"],
    "weight decay": ["weight_decay", "decay"],
    "activation": ["activation", "relu", "gelu", "swish"],
    "optimizer": ["adam", "sgd", "optimizer", "muon"],
    "architecture": ["layer", "depth", "head", "embed", "residual", "skip"],
  };

  exps.forEach((e) => {
    const desc = (e.description || "").toLowerCase();
    let found = false;
    for (const [cat, words] of Object.entries(keywords)) {
      if (words.some((w) => desc.includes(w))) { cats[cat] = (cats[cat] || 0) + 1; found = true; break; }
    }
    if (!found) cats["other"] = (cats["other"] || 0) + 1;
  });

  const baseLayout = {
    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    font: { family: "JetBrains Mono, monospace", color: "#8b949e", size: 10 },
    margin: { l: 10, r: 10, t: 10, b: 10 }, showlegend: false,
  };

  if (typeof Plotly !== "undefined") {
    Plotly.react("chart-strategy", [{
      type: "bar", x: Object.values(cats), y: Object.keys(cats), orientation: "h",
      marker: { color: "#58a6ff" }, text: Object.values(cats), textposition: "outside",
      hovertemplate: "%{y}: %{x}<extra></extra>",
    }], { ...baseLayout, xaxis: { gridcolor: "#21262d", color: "#8b949e" }, yaxis: { color: "#8b949e" } },
    { responsive: true, displaylogo: false });

    const vramData = exps.filter((e) => e.peak_vram_mb != null);
    Plotly.react("chart-vram", [{
      type: "scatter", mode: "lines+markers",
      x: vramData.map((e) => e.experiment_number),
      y: vramData.map((e) => Number(e.peak_vram_mb)),
      line: { color: "#bc8cff", width: 1.5 }, marker: { color: "#bc8cff", size: 4 },
      hovertemplate: "Exp #%{x}: %{y} MB<extra></extra>",
    }], { ...baseLayout, xaxis: { title: "Exp #", gridcolor: "#21262d", color: "#8b949e" },
          yaxis: { title: "MB", gridcolor: "#21262d", color: "#8b949e" } },
    { responsive: true, displaylogo: false });
  }

  // Insights
  const times = exps.filter((e) => e.timestamp).map((e) => new Date(e.timestamp).getTime()).sort();
  const rateStr = times.length >= 2
    ? (exps.length / ((times[times.length-1] - times[0]) / 3600000)).toFixed(1)
    : "—";
  document.getElementById("ins-rate").textContent = rateStr;

  const revIdx = [...exps].reverse().findIndex((e) => e.status === "improved");
  document.getElementById("ins-since-improve").textContent =
    revIdx < 0 ? "never" : revIdx === 0 ? "just now" : `${revIdx} ago`;

  let maxStreak = 0, streak = 0;
  exps.forEach((e) => { e.status !== "improved" ? streak++ : (streak = 0); maxStreak = Math.max(maxStreak, streak); });
  document.getElementById("ins-streak").textContent = maxStreak;

  const vramVals = exps.filter((e) => e.peak_vram_mb != null).map((e) => Number(e.peak_vram_mb));
  const avgVram = vramVals.length ? Math.round(vramVals.reduce((a, b) => a + b, 0) / vramVals.length) : null;
  document.getElementById("ins-vram").textContent = avgVram ? `${avgVram} MB` : "—";
}

// ═══════════════════════════════════════════════════════════
// Git feed
// ═══════════════════════════════════════════════════════════

function renderGitFeed(commits) {
  const feed = document.getElementById("git-feed");
  if (!commits || commits.length === 0) {
    // Fall back to showing experiments as pseudo-commits if no real git history
    if (state.experiments.length > 0) {
      const pseudoCommits = [...state.experiments]
        .reverse()
        .slice(0, 20)
        .map((e) => ({
          sha: (e.commit_sha || "").slice(0, 8) || "--------",
          full_sha: e.commit_sha || "",
          message: `Exp #${e.experiment_number}: ${e.description || ""}`,
          timestamp: e.timestamp || "",
          val_bpb: e.val_bpb,
          diff_stat: { files_changed: 1, insertions: 0, deletions: 0 },
        }));
      feed.innerHTML = pseudoCommits.map(buildCommitItem).join("");
      return;
    }
    feed.innerHTML = '<div class="empty-state" style="padding:20px;"><p>No commits found.</p></div>';
    return;
  }
  feed.innerHTML = commits.map(buildCommitItem).join("");
}

function prependCommit(commit) {
  const feed = document.getElementById("git-feed");
  const div = document.createElement("div");
  div.innerHTML = buildCommitItem(commit);
  if (div.firstChild) feed.insertBefore(div.firstChild, feed.firstChild);
  toast(`New commit: ${commit.sha}`, "success");
}

function buildCommitItem(c) {
  const stat = c.diff_stat || {};
  const ts = c.timestamp ? new Date(c.timestamp).toLocaleString() : "";
  const msg = (c.message || "").split("\n")[0].slice(0, 80);
  const sha = c.full_sha || c.sha || "";
  return `<div class="commit-item" onclick="if('${sha}')loadDiff('${sha}')">
    <div class="commit-header">
      <span class="commit-sha">${c.sha || "?"}</span>
      <span class="commit-time">${ts}</span>
    </div>
    <div class="commit-msg">${escHtml(msg)}</div>
    ${c.val_bpb != null ? `<div style="font-size:10px;color:var(--green);margin-top:2px;">val_bpb: ${Number(c.val_bpb).toFixed(4)}</div>` : ""}
    <div class="commit-stats">
      <span>${stat.files_changed || 0} files</span>
      <span class="ins">+${stat.insertions || 0}</span>
      <span class="del">-${stat.deletions || 0}</span>
    </div>
  </div>`;
}

// ═══════════════════════════════════════════════════════════
// Live log & progress image
// ═══════════════════════════════════════════════════════════

function updateLiveLog(lines) {
  const el = document.getElementById("live-log");
  if (!lines.length) return;
  el.innerHTML = lines.map((line) =>
    `<div class="log-line${/error|traceback|exception/i.test(line) ? " err" : ""}">${escHtml(line)}</div>`
  ).join("");
  el.scrollTop = el.scrollHeight;
}

function refreshProgressImage() {
  const img = document.getElementById("progress-img");
  const placeholder = document.getElementById("img-placeholder");
  img.src = `/api/progress-image?t=${Date.now()}`;
  img.style.display = "block";
  placeholder.style.display = "none";
  img.onerror = () => { img.style.display = "none"; placeholder.style.display = "flex"; };
}

// ═══════════════════════════════════════════════════════════
// Status
// ═══════════════════════════════════════════════════════════

function setStatus(status) {
  state.agentStatus = status;
  document.getElementById("status-dot").className = `status-dot ${status}`;
  document.getElementById("status-label").textContent =
    status.charAt(0).toUpperCase() + status.slice(1);
}

// ═══════════════════════════════════════════════════════════
// Pause / Resume
// ═══════════════════════════════════════════════════════════

async function togglePause() {
  const endpoint = state.paused ? "/api/control/resume" : "/api/control/pause";
  try {
    const r = await fetch(endpoint, { method: "POST" });
    const d = await r.json();
    state.paused = d.status === "paused";
    document.getElementById("pause-btn").textContent = state.paused ? "Resume" : "Pause";
    document.getElementById("pause-btn").className = state.paused ? "btn success" : "btn danger";
    toast(state.paused ? "Agent paused" : "Agent resumed", state.paused ? "warn" : "success");
  } catch (e) {
    toast(`Error: ${e}`, "error");
  }
}

// ═══════════════════════════════════════════════════════════
// Tabs
// ═══════════════════════════════════════════════════════════

function switchTab(name, btn) {
  document.querySelectorAll(".tab-content").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
  document.getElementById(`tab-${name}`).classList.add("active");
  if (btn) btn.classList.add("active");
  if (name === "analytics") renderAnalytics();
}

// ═══════════════════════════════════════════════════════════
// Render all
// ═══════════════════════════════════════════════════════════

function renderAll() {
  renderChart();
  renderTable();
  updateStats();
}

// ═══════════════════════════════════════════════════════════
// Toast
// ═══════════════════════════════════════════════════════════

function toast(msg, type = "success") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ═══════════════════════════════════════════════════════════
// Utils
// ═══════════════════════════════════════════════════════════

function fmtBpb(v) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toFixed(4);
}

function fmtDelta(v) {
  if (v == null || isNaN(v) || v === 0) return "—";
  return (Number(v) > 0 ? "+" : "") + Number(v).toFixed(4);
}

function msToHuman(ms) {
  const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
