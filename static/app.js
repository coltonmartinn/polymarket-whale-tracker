const scanBtn = document.getElementById("scan-btn");
const statusEl = document.getElementById("status");
const summaryEl = document.getElementById("summary");
const resultsEl = document.getElementById("results");
const emptyStateEl = document.getElementById("empty-state");
const cardTemplate = document.getElementById("signal-card-template");

const priceThresholdInput = document.getElementById("price-threshold");
const priceThresholdVal = document.getElementById("price-threshold-val");
const minWhaleInput = document.getElementById("min-whale");
const maxMarketsInput = document.getElementById("max-markets");
const sortBySelect = document.getElementById("sort-by");
const typeFilterSelect = document.getElementById("type-filter");
const minProfitabilityInput = document.getElementById("min-profitability");
const minProfitabilityVal = document.getElementById("min-profitability-val");
const searchInput = document.getElementById("search");

const TREND_LABELS = { new: "New", increasing: "Increasing", decreasing: "Decreasing", stable: "Stable" };

let currentSignals = [];
const PAGE_SIZE = 30;
let visibleCount = PAGE_SIZE;

priceThresholdInput.addEventListener("input", () => {
  priceThresholdVal.textContent = `≥ ${priceThresholdInput.value}%`;
});

function resetPagingAndRender() {
  visibleCount = PAGE_SIZE;
  renderResults();
}

sortBySelect.addEventListener("change", resetPagingAndRender);
typeFilterSelect.addEventListener("change", resetPagingAndRender);
searchInput.addEventListener("input", resetPagingAndRender);
minProfitabilityInput.addEventListener("input", () => {
  const val = Math.max(Number(minProfitabilityInput.value) || 0, 0);
  minProfitabilityVal.textContent = `≥ ${val}%`;
  resetPagingAndRender();
});

scanBtn.addEventListener("click", runScan);

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.remove("hidden");
  statusEl.classList.toggle("error", isError);
}

function clearStatus() {
  statusEl.classList.add("hidden");
}

async function runScan() {
  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning…";
  emptyStateEl.classList.add("hidden");
  setStatus("Fetching active markets and whale positions from Polymarket… this can take 15–30 seconds.");

  const params = new URLSearchParams({
    price_threshold: (Number(priceThresholdInput.value) / 100).toFixed(2),
    min_whale_usd: minWhaleInput.value,
    max_markets: maxMarketsInput.value,
    holders_per_market: 10,
  });

  const startedAt = performance.now();
  try {
    const resp = await fetch(`/api/scan?${params.toString()}`);
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.error || `Request failed (${resp.status})`);
    }
    const data = await resp.json();
    currentSignals = data.signals;
    visibleCount = PAGE_SIZE;
    const elapsed = ((performance.now() - startedAt) / 1000).toFixed(1);
    renderSummary(data.meta, elapsed);
    renderResults();
    clearStatus();
  } catch (err) {
    setStatus(`Scan failed: ${err.message}`, true);
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan Market";
  }
}

function renderSummary(meta, elapsed) {
  summaryEl.classList.remove("hidden");
  summaryEl.innerHTML = "";
  const metrics = [
    ["Markets scanned", meta.markets_scanned],
    ["Near-certain markets", meta.markets_flagged],
    ["Whale signals found", meta.signals_found],
    ["Scan time", `${elapsed}s`],
  ];
  for (const [label, value] of metrics) {
    const wrap = document.createElement("div");
    wrap.className = "metric";
    wrap.innerHTML = `<span class="metric-value">${value}</span><span class="metric-label">${label}</span>`;
    summaryEl.appendChild(wrap);
  }
}

function strengthClass(strength) {
  return "strength-" + strength.toLowerCase().replace(/\s+/g, "");
}

function formatUsd(value) {
  return "$" + Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function shortWallet(addr) {
  if (!addr || addr.length < 10) return addr || "unknown";
  return addr.slice(0, 6) + "…" + addr.slice(-4);
}

function renderResults() {
  const sortKey = sortBySelect.value;
  const typeFilter = typeFilterSelect.value;
  const query = searchInput.value.trim().toLowerCase();
  // Percent return if the bet hits (profit per $100 staked) -- a payout
  // percentage, never a dollar amount, so it stays meaningful regardless of
  // how big or small a whale's position is.
  const minProfitability = Math.max(Number(minProfitabilityInput.value) || 0, 0);

  let filtered = currentSignals.filter((s) => {
    if (typeFilter !== "all" && s.outcome_type !== typeFilter) return false;
    if (query && !s.market.toLowerCase().includes(query) && !s.outcome.toLowerCase().includes(query)) return false;
    if ((s.payout_per_100 ?? 0) < minProfitability) return false;
    return true;
  });

  filtered = [...filtered].sort((a, b) => b[sortKey] - a[sortKey]);

  resultsEl.innerHTML = "";

  if (currentSignals.length === 0) {
    emptyStateEl.classList.remove("hidden");
    emptyStateEl.querySelector("p").textContent = "No scan yet. Click Scan Market to pull live data from Polymarket.";
    return;
  }

  if (filtered.length === 0) {
    emptyStateEl.classList.remove("hidden");
    emptyStateEl.querySelector("p").textContent = "No signals match the current filters.";
    return;
  }

  emptyStateEl.classList.add("hidden");

  const toShow = filtered.slice(0, visibleCount);

  for (const s of toShow) {
    const node = cardTemplate.content.cloneNode(true);

    const typeBadge = node.querySelector(".badge-type");
    typeBadge.textContent = s.outcome_type === "favorite" ? "Favorite" : "Longshot";
    typeBadge.classList.add(s.outcome_type);

    const strengthBadge = node.querySelector(".badge-strength");
    strengthBadge.textContent = s.signal_strength + " signal";
    strengthBadge.classList.add(strengthClass(s.signal_strength));
    if (s.momentum_shifted) {
      const arrow = s.momentum_shifted === "up" ? " ▲" : " ▼";
      strengthBadge.textContent += arrow;
      strengthBadge.title = `Shifted ${s.momentum_shifted} from ${s.base_signal_strength} on whale momentum since the last scan (${s.momentum_pct >= 0 ? "+" : ""}${(s.momentum_pct * 100).toFixed(0)}%).`;
    }

    node.querySelector(".card-question").textContent = s.market;
    node.querySelector(".card-outcome").innerHTML = `Betting on <strong>${s.outcome}</strong>`;

    node.querySelector(".stat-prob").textContent = `${(s.implied_probability * 100).toFixed(1)}%`;
    node.querySelector(".stat-payout").textContent = s.payout_per_100 != null ? `$${100 + s.payout_per_100}` : "—";
    node.querySelector(".stat-whale-usd").textContent = formatUsd(s.whale_usd_total);
    node.querySelector(".stat-whale-count").textContent = s.whale_count;
    node.querySelector(".stat-liq-pct").textContent = `${s.pct_of_liquidity}%`;
    node.querySelector(".stat-top-wallet").textContent =
      `${shortWallet(s.top_whale.wallet)} (${formatUsd(s.top_whale.usd_value)})`;
    const trendEl = node.querySelector(".stat-top-wallet-trend");
    if (s.top_whale.trend) {
      trendEl.textContent = TREND_LABELS[s.top_whale.trend] || s.top_whale.trend;
      trendEl.className = "trend-tag trend-" + s.top_whale.trend;
    } else {
      trendEl.classList.add("hidden");
    }

    node.querySelector(".card-recommendation").textContent = s.recommendation;

    const link = node.querySelector(".card-link");
    if (s.polymarket_url) {
      link.href = s.polymarket_url;
    } else {
      link.classList.add("hidden");
    }

    resultsEl.appendChild(node);
  }

  if (filtered.length > toShow.length) {
    const loadMoreWrap = document.createElement("div");
    loadMoreWrap.className = "load-more-wrap";
    const remaining = filtered.length - toShow.length;
    loadMoreWrap.innerHTML = `<button class="btn-secondary" id="load-more-btn">Show ${Math.min(PAGE_SIZE, remaining)} more (${remaining} left)</button>`;
    resultsEl.appendChild(loadMoreWrap);
    document.getElementById("load-more-btn").addEventListener("click", () => {
      visibleCount += PAGE_SIZE;
      renderResults();
    });
  }
}

// ---------- Tabs ----------

const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanels = {
  scanner: document.getElementById("tab-scanner"),
  momentum: document.getElementById("tab-momentum"),
  calibration: document.getElementById("tab-calibration"),
  "track-record": document.getElementById("tab-track-record"),
};
const loadedTabs = new Set(["scanner"]);

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    tabButtons.forEach((b) => b.classList.toggle("active", b === btn));
    Object.entries(tabPanels).forEach(([name, panel]) => panel.classList.toggle("hidden", name !== tab));

    if (!loadedTabs.has(tab)) {
      loadedTabs.add(tab);
      if (tab === "momentum") fetchMomentum();
      if (tab === "calibration") fetchCalibration();
      if (tab === "track-record") fetchTrackRecord();
    }
  });
});

// ---------- Momentum ----------

const momentumStatusEl = document.getElementById("momentum-status");
const momentumListEl = document.getElementById("momentum-list");
const momentumRowTemplate = document.getElementById("momentum-row-template");

document.getElementById("momentum-refresh-btn").addEventListener("click", fetchMomentum);

async function fetchMomentum() {
  momentumStatusEl.classList.remove("hidden");
  momentumStatusEl.classList.remove("error");
  momentumStatusEl.textContent = "Loading…";
  try {
    const resp = await fetch("/api/momentum");
    const data = await resp.json();
    renderMomentum(data);
  } catch (err) {
    momentumStatusEl.textContent = `Failed to load momentum: ${err.message}`;
    momentumStatusEl.classList.add("error");
  }
}

const MOMENTUM_LABELS = {
  new_entry: "New position",
  increased: "Added",
  decreased: "Reduced",
  exited: "Exited",
};

function renderMomentum(data) {
  momentumListEl.innerHTML = "";

  if (!data.has_previous) {
    momentumStatusEl.textContent = "Only one scan recorded so far — momentum needs at least two scans to diff against each other. Run another scan, or wait for the scheduled background scan (every 30 minutes).";
    momentumStatusEl.classList.remove("hidden");
    return;
  }

  if (data.events.length === 0) {
    momentumStatusEl.textContent = "No meaningful position changes ($250+) between the two most recent scans.";
    momentumStatusEl.classList.remove("hidden");
    return;
  }

  momentumStatusEl.classList.add("hidden");

  for (const e of data.events.slice(0, 100)) {
    const node = momentumRowTemplate.content.cloneNode(true);
    const badge = node.querySelector(".momentum-type");
    badge.textContent = MOMENTUM_LABELS[e.type] || e.type;
    badge.classList.add("type-" + e.type);

    node.querySelector(".momentum-market").textContent = e.market;
    node.querySelector(".momentum-detail").textContent =
      `${e.outcome} · ${shortWallet(e.wallet)}${e.wallet_name ? " (" + e.wallet_name + ")" : ""} · now ${formatUsd(e.usd_value)}`;

    const deltaEl = node.querySelector(".momentum-delta");
    const sign = e.delta_usd >= 0 ? "+" : "-";
    deltaEl.textContent = `${sign}${formatUsd(Math.abs(e.delta_usd))}`;
    deltaEl.classList.add(e.delta_usd >= 0 ? "positive" : "negative");

    momentumListEl.appendChild(node);
  }
}

// ---------- Calibration backtest ----------

const calibrationStatusEl = document.getElementById("calibration-status");
const calibrationContentEl = document.getElementById("calibration-content");

document.getElementById("calibration-run-btn").addEventListener("click", runCalibrationBacktest);

async function fetchCalibration() {
  calibrationStatusEl.classList.remove("hidden");
  calibrationStatusEl.classList.remove("error");
  calibrationStatusEl.textContent = "Loading…";
  try {
    const resp = await fetch("/api/calibration");
    const data = await resp.json();
    renderCalibration(data);
  } catch (err) {
    calibrationStatusEl.textContent = `Failed to load calibration data: ${err.message}`;
    calibrationStatusEl.classList.add("error");
  }
}

async function runCalibrationBacktest() {
  const btn = document.getElementById("calibration-run-btn");
  btn.disabled = true;
  btn.textContent = "Running backtest…";
  calibrationStatusEl.classList.remove("hidden");
  calibrationStatusEl.classList.remove("error");
  calibrationStatusEl.textContent = "Sampling resolved markets and pulling price history — this takes 30–60 seconds.";
  try {
    const resp = await fetch("/api/calibration/run?max_markets=250&min_volume=5000", { method: "POST" });
    if (!resp.ok) throw new Error(`Request failed (${resp.status})`);
    const data = await resp.json();
    renderCalibration(data.summary);
  } catch (err) {
    calibrationStatusEl.textContent = `Backtest failed: ${err.message}`;
    calibrationStatusEl.classList.add("error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Run new backtest (~30–60s)";
  }
}

function bucketMidpoint(label) {
  const [lo, hi] = label.replace("%", "").split("-").map(Number);
  return (lo + hi) / 2;
}

function renderBarSection(title, buckets, side) {
  let html = `<div class="calib-title">${title}</div>`;
  for (const b of buckets) {
    const target = bucketMidpoint(b.label);
    const fillPct = Math.max(b.observed_win_rate * 100, 0.5);
    html += `
      <div class="calib-bar-row">
        <span class="calib-bar-label">${b.label}</span>
        <div class="calib-bar-track">
          <div class="calib-bar-fill ${side}" style="width: ${fillPct}%"></div>
          <div class="calib-bar-target" style="left: ${target}%"></div>
        </div>
        <span class="calib-bar-meta">${(b.observed_win_rate * 100).toFixed(1)}% hit &middot; n=${b.n}</span>
      </div>`;
  }
  return html;
}

function renderCalibration(data) {
  if (!data || !data.observations) {
    calibrationContentEl.innerHTML = "";
    calibrationStatusEl.textContent = "No backtest data yet. Click \"Run new backtest\" to sample resolved markets.";
    calibrationStatusEl.classList.remove("hidden");
    return;
  }

  calibrationStatusEl.classList.add("hidden");

  const cl = data.call_level;
  const favBuckets = data.buckets.filter((b) => bucketMidpoint(b.label) >= 50);
  const longBuckets = data.buckets.filter((b) => bucketMidpoint(b.label) < 50);

  let html = `<div class="calib-stats-row">
    <div class="metric"><span class="metric-value">${(cl.favorite_accuracy * 100).toFixed(1)}%</span><span class="metric-label">Favorite accuracy (n=${cl.favorite_n} calls)</span></div>
    <div class="metric"><span class="metric-value">${(cl.longshot_hit_rate * 100).toFixed(1)}%</span><span class="metric-label">Longshot hit rate (n=${cl.longshot_n} calls)</span></div>
    <div class="metric"><span class="metric-value">${cl.brier_score}</span><span class="metric-label">Brier score (call-level, 0=perfect)</span></div>
    <div class="metric"><span class="metric-value">${data.markets}</span><span class="metric-label">Distinct resolved markets</span></div>
  </div>
  <p class="calib-note">${cl.note} Raw point-level stats (below, ${data.observations} observations, Brier ${data.brier_score}) include every sampled price tick and will look more confident than the call-level numbers above — the call-level numbers are the fairer read.</p>`;

  html += renderBarSection("Favorites (priced ≥95%) by bucket", favBuckets, "favorite");
  html += renderBarSection("Longshots (priced ≤5%) by bucket", longBuckets, "longshot");
  html += `<div class="calib-legend"><span class="tick-sample"></span>tick = bucket midpoint (perfect calibration); bar = actual observed win rate</div>`;

  html += `<div class="calib-title">Accuracy by lead time before resolution</div>`;
  for (const lt of data.by_lead_time) {
    const shortLabel = lt.label.replace(" days before resolution", "d");
    html += `
      <div class="calib-bar-row">
        <span class="calib-bar-label">${shortLabel}</span>
        <div class="calib-bar-track">
          <div class="calib-bar-fill favorite" style="width: ${lt.accuracy * 100}%"></div>
        </div>
        <span class="calib-bar-meta">${(lt.accuracy * 100).toFixed(1)}% &middot; n=${lt.n}</span>
      </div>`;
  }

  calibrationContentEl.innerHTML = html;
}

// ---------- Live track record ----------

const trackRecordStatusEl = document.getElementById("track-record-status");
const trackRecordContentEl = document.getElementById("track-record-content");

document.getElementById("track-record-refresh-btn").addEventListener("click", refreshTrackRecord);

async function fetchTrackRecord() {
  trackRecordStatusEl.classList.remove("hidden");
  trackRecordStatusEl.classList.remove("error");
  trackRecordStatusEl.textContent = "Loading…";
  try {
    const resp = await fetch("/api/track_record");
    const data = await resp.json();
    renderTrackRecord(data);
  } catch (err) {
    trackRecordStatusEl.textContent = `Failed to load track record: ${err.message}`;
    trackRecordStatusEl.classList.add("error");
  }
}

async function refreshTrackRecord() {
  const btn = document.getElementById("track-record-refresh-btn");
  btn.disabled = true;
  btn.textContent = "Checking…";
  trackRecordStatusEl.classList.remove("hidden");
  trackRecordStatusEl.classList.remove("error");
  trackRecordStatusEl.textContent = "Checking flagged markets for new resolutions…";
  try {
    const resp = await fetch("/api/track_record/refresh", { method: "POST" });
    if (!resp.ok) throw new Error(`Request failed (${resp.status})`);
    const data = await resp.json();
    renderTrackRecord(data.summary ? { ...data.summary, tracking_status: data.summary.tracking_status } : data);
    if (data.newly_resolved !== undefined) {
      trackRecordStatusEl.textContent = `Checked ${data.checked} flagged markets — ${data.newly_resolved} newly resolved.`;
      trackRecordStatusEl.classList.remove("hidden");
    }
  } catch (err) {
    trackRecordStatusEl.textContent = `Refresh failed: ${err.message}`;
    trackRecordStatusEl.classList.add("error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Check for new resolutions";
  }
}

function renderTrackRecord(data) {
  const status = data.tracking_status || {};
  let html = `<p class="pending-note">Tracking since ${status.tracking_since ? new Date(status.tracking_since).toLocaleString() : "—"} &middot; <strong>${status.resolved || 0}</strong> resolved &middot; <strong>${status.pending || 0}</strong> still pending.</p>`;

  if (!data.observations) {
    trackRecordContentEl.innerHTML = html;
    if (!trackRecordStatusEl.textContent || trackRecordStatusEl.textContent === "Loading…") {
      trackRecordStatusEl.textContent = "No flagged markets have resolved yet — check back after some time has passed, or click \"Check for new resolutions.\"";
      trackRecordStatusEl.classList.remove("hidden");
    }
    return;
  }

  trackRecordStatusEl.classList.add("hidden");

  const cl = data.call_level;
  html += `<div class="calib-stats-row">
    <div class="metric"><span class="metric-value">${cl.favorite_accuracy != null ? (cl.favorite_accuracy * 100).toFixed(1) + "%" : "—"}</span><span class="metric-label">Favorite accuracy (n=${cl.favorite_n})</span></div>
    <div class="metric"><span class="metric-value">${cl.longshot_hit_rate != null ? (cl.longshot_hit_rate * 100).toFixed(1) + "%" : "—"}</span><span class="metric-label">Longshot hit rate (n=${cl.longshot_n})</span></div>
    <div class="metric"><span class="metric-value">${data.brier_score}</span><span class="metric-label">Brier score</span></div>
    <div class="metric"><span class="metric-value">${data.observations}</span><span class="metric-label">Resolved calls tracked</span></div>
  </div>`;

  const favBuckets = data.buckets.filter((b) => bucketMidpoint(b.label) >= 50);
  const longBuckets = data.buckets.filter((b) => bucketMidpoint(b.label) < 50);
  if (favBuckets.length) html += renderBarSection("Favorites we flagged, by bucket", favBuckets, "favorite");
  if (longBuckets.length) html += renderBarSection("Longshots we flagged, by bucket", longBuckets, "longshot");

  trackRecordContentEl.innerHTML = html;
}
