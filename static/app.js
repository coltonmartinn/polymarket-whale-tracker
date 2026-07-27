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
const searchInput = document.getElementById("search");

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

  let filtered = currentSignals.filter((s) => {
    if (typeFilter !== "all" && s.outcome_type !== typeFilter) return false;
    if (query && !s.market.toLowerCase().includes(query) && !s.outcome.toLowerCase().includes(query)) return false;
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

    node.querySelector(".card-question").textContent = s.market;
    node.querySelector(".card-outcome").innerHTML = `Betting on <strong>${s.outcome}</strong>`;

    node.querySelector(".stat-prob").textContent = `${(s.implied_probability * 100).toFixed(1)}%`;
    node.querySelector(".stat-payout").textContent = s.payout_per_100 != null ? `$${100 + s.payout_per_100}` : "—";
    node.querySelector(".stat-whale-usd").textContent = formatUsd(s.whale_usd_total);
    node.querySelector(".stat-whale-count").textContent = s.whale_count;
    node.querySelector(".stat-liq-pct").textContent = `${s.pct_of_liquidity}%`;
    node.querySelector(".stat-top-wallet").textContent =
      `${shortWallet(s.top_whale.wallet)} (${formatUsd(s.top_whale.usd_value)})`;

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
