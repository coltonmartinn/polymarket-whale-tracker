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

const TREND_LABELS = { new: "New", increasing: "Piling on", decreasing: "Backing off", stable: "No change" };

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

function setStatus(message, isError = false, isLoading = false) {
  statusEl.textContent = message;
  statusEl.classList.remove("hidden");
  statusEl.classList.toggle("error", isError);
  statusEl.classList.toggle("loading", isLoading);
}

function clearStatus() {
  statusEl.classList.add("hidden");
  statusEl.classList.remove("loading");
}

async function runScan() {
  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning…";
  emptyStateEl.classList.add("hidden");
  setStatus("Fetching active markets and whale positions from Polymarket… this can take 15–30 seconds.", false, true);

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

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
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
    node.querySelector(".stat-total-holders").textContent =
      s.total_holders != null ? `${s.total_holders}${s.total_holders_capped ? "+" : ""}` : "—";
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

    wireDemoBetForm(node, s);

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
  mapping: document.getElementById("tab-mapping"),
  demo: document.getElementById("tab-demo"),
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
      if (tab === "track-record") {
        fetchTrackRecord();
        fetchWalletLeaderboard();
      }
      if (tab === "demo") fetchDemoPortfolios();
    }
  });
});

// ---------- Momentum ----------

const momentumStatusEl = document.getElementById("momentum-status");
const momentumGroupsEl = document.getElementById("momentum-groups");
const momentumRowTemplate = document.getElementById("momentum-row-template");
const MOMENTUM_GROUP_CAP = 50; // per bucket; keeps a very busy scan from turning into an endless scroll

document.getElementById("momentum-refresh-btn").addEventListener("click", fetchMomentum);

async function fetchMomentum() {
  momentumStatusEl.classList.remove("hidden");
  momentumStatusEl.classList.remove("error");
  momentumStatusEl.classList.add("loading");
  momentumStatusEl.textContent = "Loading…";
  try {
    const resp = await fetch("/api/momentum");
    const data = await resp.json();
    renderMomentum(data);
  } catch (err) {
    momentumStatusEl.textContent = `Failed to load momentum: ${err.message}`;
    momentumStatusEl.classList.add("error");
  } finally {
    momentumStatusEl.classList.remove("loading");
  }
}

// Four buckets the user thinks in, in plain short words. "exited" isn't
// one of the four they asked for, but dropping a position to zero is too
// different from trimming it to fold into "Backing off" -- kept as its
// own small group instead of hidden.
const MOMENTUM_LABELS = {
  new_entry: "New",
  increased: "Piling on",
  decreased: "Backing off",
  exited: "Exited",
};

function renderMomentumRow(container, e) {
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

  container.appendChild(node);
}

function renderMomentum(data) {
  if (!data.has_previous) {
    momentumGroupsEl.classList.add("hidden");
    momentumStatusEl.textContent = "Only one scan recorded so far — momentum needs at least two scans to diff against each other. Run another scan, or wait for the scheduled background scan (every 30 minutes).";
    momentumStatusEl.classList.remove("hidden");
    return;
  }

  momentumStatusEl.classList.add("hidden");
  momentumGroupsEl.classList.remove("hidden");

  const byType = { new_entry: [], increased: [], decreased: [], exited: [] };
  for (const e of data.events) {
    (byType[e.type] || byType.exited).push(e);
  }

  for (const groupEl of momentumGroupsEl.querySelectorAll(".momentum-group[data-group]")) {
    const type = groupEl.dataset.group;
    if (type === "unchanged") continue; // handled separately below

    const items = byType[type] || [];
    const listEl = groupEl.querySelector(".momentum-group-list");
    listEl.innerHTML = "";

    if (items.length === 0) {
      groupEl.classList.add("hidden");
      continue;
    }
    groupEl.classList.remove("hidden");

    for (const e of items.slice(0, MOMENTUM_GROUP_CAP)) {
      renderMomentumRow(listEl, e);
    }
    if (items.length > MOMENTUM_GROUP_CAP) {
      const more = document.createElement("p");
      more.className = "momentum-group-note";
      more.textContent = `+ ${items.length - MOMENTUM_GROUP_CAP} more`;
      listEl.appendChild(more);
    }
  }

  const unchangedEl = momentumGroupsEl.querySelector('.momentum-group[data-group="unchanged"]');
  const count = data.unchanged_count || 0;
  unchangedEl.querySelector(".momentum-group-note").textContent =
    count > 0
      ? `${count.toLocaleString()} whale position${count === 1 ? "" : "s"} held roughly steady — no notable change.`
      : "No positions held steady between these two scans.";
}

// ---------- Calibration backtest ----------

const calibrationStatusEl = document.getElementById("calibration-status");
const calibrationContentEl = document.getElementById("calibration-content");

document.getElementById("calibration-run-btn").addEventListener("click", runCalibrationBacktest);

async function fetchCalibration() {
  calibrationStatusEl.classList.remove("hidden");
  calibrationStatusEl.classList.remove("error");
  calibrationStatusEl.classList.add("loading");
  calibrationStatusEl.textContent = "Loading…";
  try {
    const resp = await fetch("/api/calibration");
    const data = await resp.json();
    renderCalibration(data);
  } catch (err) {
    calibrationStatusEl.textContent = `Failed to load calibration data: ${err.message}`;
    calibrationStatusEl.classList.add("error");
  } finally {
    calibrationStatusEl.classList.remove("loading");
  }
}

async function runCalibrationBacktest() {
  const btn = document.getElementById("calibration-run-btn");
  btn.disabled = true;
  btn.textContent = "Running backtest…";
  calibrationStatusEl.classList.remove("hidden");
  calibrationStatusEl.classList.remove("error");
  calibrationStatusEl.classList.add("loading");
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
    calibrationStatusEl.classList.remove("loading");
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
  trackRecordStatusEl.classList.add("loading");
  trackRecordStatusEl.textContent = "Loading…";
  try {
    const resp = await fetch("/api/track_record");
    const data = await resp.json();
    renderTrackRecord(data);
  } catch (err) {
    trackRecordStatusEl.textContent = `Failed to load track record: ${err.message}`;
    trackRecordStatusEl.classList.add("error");
  } finally {
    trackRecordStatusEl.classList.remove("loading");
  }
}

async function refreshTrackRecord() {
  const btn = document.getElementById("track-record-refresh-btn");
  btn.disabled = true;
  btn.textContent = "Checking…";
  trackRecordStatusEl.classList.remove("hidden");
  trackRecordStatusEl.classList.remove("error");
  trackRecordStatusEl.classList.add("loading");
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
    fetchWalletLeaderboard();
  } catch (err) {
    trackRecordStatusEl.textContent = `Refresh failed: ${err.message}`;
    trackRecordStatusEl.classList.add("error");
  } finally {
    trackRecordStatusEl.classList.remove("loading");
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

// ---------- Whale leaderboard ----------

const walletLeaderboardStatusEl = document.getElementById("wallet-leaderboard-status");
const walletLeaderboardContentEl = document.getElementById("wallet-leaderboard-content");

async function fetchWalletLeaderboard() {
  walletLeaderboardStatusEl.classList.remove("hidden");
  walletLeaderboardStatusEl.classList.remove("error");
  walletLeaderboardStatusEl.classList.add("loading");
  walletLeaderboardStatusEl.textContent = "Loading…";
  try {
    const resp = await fetch("/api/wallet_leaderboard");
    const data = await resp.json();
    renderWalletLeaderboard(data);
  } catch (err) {
    walletLeaderboardStatusEl.textContent = `Failed to load leaderboard: ${err.message}`;
    walletLeaderboardStatusEl.classList.add("error");
  } finally {
    walletLeaderboardStatusEl.classList.remove("loading");
  }
}

function renderWalletLeaderboard(data) {
  const rows = data.leaderboard || [];
  if (!rows.length) {
    walletLeaderboardContentEl.innerHTML = "";
    const short = (data.wallets_tracked || 0) > 0
      ? ` ${data.wallets_tracked} wallet(s) tracked so far, but none have reached ${data.min_calls || 3} resolved calls yet.`
      : "";
    walletLeaderboardStatusEl.textContent = `No wallets qualify yet.${short}`;
    walletLeaderboardStatusEl.classList.remove("hidden");
    return;
  }

  walletLeaderboardStatusEl.classList.add("hidden");

  let html = `<p class="pending-note">${data.wallets_qualifying} of ${data.wallets_tracked} tracked wallets have &ge;${data.min_calls} resolved calls and are ranked below.</p>`;
  rows.forEach((w, i) => {
    const label = w.wallet_name ? `${shortWallet(w.wallet)} (${escapeHtml(w.wallet_name)})` : shortWallet(w.wallet);
    const pct = Math.max(w.win_rate * 100, 0.5);
    html += `
      <div class="calib-bar-row wallet-row">
        <span class="calib-bar-label wallet-rank">#${i + 1} ${label}</span>
        <div class="calib-bar-track">
          <div class="calib-bar-fill favorite" style="width: ${pct}%"></div>
        </div>
        <span class="calib-bar-meta">${(w.win_rate * 100).toFixed(0)}% (n=${w.n}) &middot; avg ${formatUsd(w.avg_usd)}</span>
      </div>`;
  });

  walletLeaderboardContentEl.innerHTML = html;
}

// ---------- Market mapping ----------

const mappingStatusEl = document.getElementById("mapping-status");
const mappingContentEl = document.getElementById("mapping-content");

document.getElementById("mapping-run-btn").addEventListener("click", runMarketMapping);

async function runMarketMapping() {
  const btn = document.getElementById("mapping-run-btn");
  btn.disabled = true;
  btn.textContent = "Searching Polymarket US…";
  mappingStatusEl.classList.remove("hidden");
  mappingStatusEl.classList.remove("error");
  mappingStatusEl.classList.add("loading");
  mappingStatusEl.textContent = "Fetching Polymarket US's non-sports markets and comparing against your last scan — this can take up to 15 seconds.";
  try {
    const resp = await fetch("/api/market_mapping");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.message || `Request failed (${resp.status})`);
    renderMapping(data.markets || []);
  } catch (err) {
    mappingContentEl.innerHTML = "";
    mappingStatusEl.textContent = err.message;
    mappingStatusEl.classList.add("error");
    mappingStatusEl.classList.remove("hidden");
  } finally {
    mappingStatusEl.classList.remove("loading");
    btn.disabled = false;
    btn.textContent = "Find candidate matches (from last scan)";
  }
}

function renderMapping(markets) {
  if (!markets.length) {
    mappingContentEl.innerHTML = "";
    mappingStatusEl.textContent = "No flagged markets from the last scan.";
    mappingStatusEl.classList.remove("hidden");
    return;
  }
  mappingStatusEl.classList.add("hidden");

  let html = "";
  for (const m of markets) {
    html += `<div class="mapping-card">
      <div class="mapping-global">
        <span class="mapping-label">Global</span>
        <a href="${m.global_url || "#"}" target="_blank" rel="noopener">${escapeHtml(m.global_question)}</a>
      </div>`;

    if (m.confirmed) {
      html += `<div class="mapping-row mapping-confirmed">
        <span class="mapping-label">Confirmed US match</span>
        <span>${escapeHtml(m.confirmed.us_question)}</span>
        <button class="link-btn mapping-clear-btn" data-condition="${escapeHtml(m.condition_id)}">Clear</button>
      </div>`;
    } else if (m.candidates.length) {
      for (const c of m.candidates) {
        html += `<div class="mapping-row">
          <span class="mapping-candidate-text">${escapeHtml(c.us_question)} <span class="mapping-sim">${(c.similarity * 100).toFixed(0)}% match${c.date_gap_days != null ? " &middot; " + c.date_gap_days + "d apart" : ""}</span></span>
          <button class="btn-secondary mapping-confirm-btn"
            data-condition="${escapeHtml(m.condition_id)}"
            data-global-question="${escapeHtml(m.global_question)}"
            data-global-slug="${escapeHtml(m.global_slug || "")}"
            data-us-id="${escapeHtml(c.us_market_id)}"
            data-us-question="${escapeHtml(c.us_question)}"
            data-us-slug="${escapeHtml(c.us_slug || "")}"
            data-similarity="${c.similarity}">Confirm</button>
        </div>`;
      }
    } else {
      html += `<p class="mapping-none">No candidate found on Polymarket US.</p>`;
    }
    html += `</div>`;
  }
  mappingContentEl.innerHTML = html;

  mappingContentEl.querySelectorAll(".mapping-confirm-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await fetch("/api/market_mapping/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            condition_id: btn.dataset.condition,
            global_question: btn.dataset.globalQuestion,
            global_slug: btn.dataset.globalSlug,
            us_market_id: btn.dataset.usId,
            us_question: btn.dataset.usQuestion,
            us_slug: btn.dataset.usSlug,
            similarity: Number(btn.dataset.similarity),
          }),
        });
        renderMapping(await (await fetch("/api/market_mapping")).json().then((d) => d.markets || []));
      } catch (err) {
        btn.disabled = false;
      }
    });
  });

  mappingContentEl.querySelectorAll(".mapping-clear-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await fetch("/api/market_mapping/clear", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ condition_id: btn.dataset.condition }),
        });
        renderMapping(await (await fetch("/api/market_mapping")).json().then((d) => d.markets || []));
      } catch (err) {
        btn.disabled = false;
      }
    });
  });
}

// ---------- Demo portfolio ----------

document.getElementById("demo-refresh-btn").addEventListener("click", fetchDemoPortfolios);

function wireDemoBetForm(node, signal) {
  const openBtn = node.querySelector(".card-demo-bet-btn");
  const form = node.querySelector(".demo-bet-form");
  const input = node.querySelector(".demo-bet-input");
  const confirmBtn = node.querySelector(".demo-bet-confirm-btn");
  const cancelBtn = node.querySelector(".demo-bet-cancel-btn");
  const msg = node.querySelector(".demo-bet-msg");

  openBtn.addEventListener("click", () => {
    form.classList.remove("hidden");
    openBtn.classList.add("hidden");
    msg.textContent = "";
    msg.classList.remove("error");
    input.focus();
    input.select();
  });

  cancelBtn.addEventListener("click", () => {
    form.classList.add("hidden");
    openBtn.classList.remove("hidden");
  });

  const submit = async () => {
    const stake = Number(input.value);
    if (!Number.isFinite(stake) || stake <= 0) {
      msg.textContent = "Enter a positive amount.";
      msg.classList.add("error");
      return;
    }
    confirmBtn.disabled = true;
    msg.classList.remove("error");
    msg.textContent = "Placing…";
    try {
      const resp = await fetch("/api/demo/manual/bet", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          condition_id: signal.condition_id,
          clob_token_id: signal.clob_token_id,
          market_question: signal.market,
          slug: signal.slug,
          outcome_name: signal.outcome,
          entry_price: signal.implied_probability,
          stake_usd: stake,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error(data.error || `Request failed (${resp.status})`);
      msg.textContent = `Placed $${stake.toLocaleString()}.`;
      confirmBtn.disabled = false;
      setTimeout(() => {
        form.classList.add("hidden");
        openBtn.classList.remove("hidden");
      }, 1200);
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("error");
      confirmBtn.disabled = false;
    }
  };

  confirmBtn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });
}

async function fetchDemoPortfolios() {
  await Promise.all([fetchDemoPortfolio("manual"), fetchDemoPortfolio("auto")]);
}

async function fetchDemoPortfolio(mode) {
  const statsEl = document.getElementById(`demo-${mode}-stats`);
  const openEl = document.getElementById(`demo-${mode}-open`);
  const resolvedEl = document.getElementById(`demo-${mode}-resolved`);
  try {
    const resp = await fetch(`/api/demo/${mode}`);
    const data = await resp.json();
    renderDemoPortfolio(mode, data, statsEl, openEl, resolvedEl);
  } catch (err) {
    statsEl.innerHTML = `<p class="mapping-none">Failed to load: ${err.message}</p>`;
  }
}

function renderDemoPortfolio(mode, data, statsEl, openEl, resolvedEl) {
  const pnlClass = data.realized_pnl > 0 ? "positive" : data.realized_pnl < 0 ? "negative" : "";
  statsEl.innerHTML = `
    <div class="metric"><span class="metric-value">${formatUsd(data.net_worth_at_cost)}</span><span class="metric-label">Net worth (at cost)</span></div>
    <div class="metric"><span class="metric-value ${pnlClass}">${data.realized_pnl >= 0 ? "+" : ""}${formatUsd(data.realized_pnl)}</span><span class="metric-label">Realized P&amp;L</span></div>
    <div class="metric"><span class="metric-value">${formatUsd(data.cash_available)}</span><span class="metric-label">Cash available</span></div>
    <div class="metric"><span class="metric-value">${formatUsd(data.open_stake_total)}</span><span class="metric-label">At risk (open)</span></div>
    <div class="metric"><span class="metric-value">${data.win_rate != null ? (data.win_rate * 100).toFixed(0) + "%" : "—"}</span><span class="metric-label">Win rate (n=${data.resolved_count})</span></div>
  `;

  if (!data.open_positions.length) {
    openEl.innerHTML = `<p class="mapping-none">No open positions.</p>`;
  } else {
    openEl.innerHTML = data.open_positions.map((p) => `
      <div class="mapping-row">
        <span class="mapping-candidate-text">${escapeHtml(p.market_question)} &mdash; <strong>${escapeHtml(p.outcome_name)}</strong> <span class="mapping-sim">@ ${(p.entry_price * 100).toFixed(1)}%</span></span>
        <span class="mapping-sim">${formatUsd(p.stake_usd)} staked</span>
      </div>`).join("");
  }

  if (!data.resolved_positions.length) {
    resolvedEl.innerHTML = `<p class="mapping-none">No resolved positions yet.</p>`;
  } else {
    resolvedEl.innerHTML = data.resolved_positions.map((p) => {
      const net = p.payout_usd - p.stake_usd;
      return `<div class="mapping-row">
        <span class="mapping-candidate-text">${escapeHtml(p.market_question)} &mdash; <strong>${escapeHtml(p.outcome_name)}</strong> <span class="mapping-sim">${p.resolved_won ? "won" : "lost"}</span></span>
        <span class="${net >= 0 ? "positive" : "negative"}">${net >= 0 ? "+" : ""}${formatUsd(net)}</span>
      </div>`;
    }).join("");
  }
}
