// WeatherBHN Trading Dashboard — frontend logic.
// Read-only planning/simulation tool. Never calls any order-placement API.

const PRICE_REFRESH_MS = 20000;   // live Kalshi price/volume/chance
const MODEL_REFRESH_MS = 5 * 60000; // mu/sigma — matches orchestrator cadence

// ---------------------------------------------------------------------------
// Footer content — edit these two arrays directly, no markup changes needed.
// Rendered once on load by renderFooter(). KNOWN_ISSUES is meant to be a
// living log: add an entry the moment something's found broken/degraded,
// change its status to 'resolved' (or delete it) once actually fixed and
// verified live — not a one-time snapshot.
// ---------------------------------------------------------------------------
const DATA_SOURCES = [
  { label: 'Market prices / Yes¢ / No¢ / Chance%', text: 'weather_bronze_kalshi_market_snapshots (live Kalshi collector), refreshes every ~20-30s poll from this page' },
  { label: 'Volume / Open Interest', text: 'same snapshot table — volume reflects real intraday cumulative trades and is legitimately near-zero early in a trading day (99-100% of rows are nonzero by the time a contract settles); low volume on today\'s/tomorrow\'s buckets is real market thinness, not missing data' },
  { label: 'Model prediction (μ)', text: 'CP3 XGBoost via weather_position_exits_clean (entry-frozen or live) when a bucket has qualified as a trade; falls back to weather_gold_contract_ledger (nws_forecast_f + model_delta_f, logged for every evaluated bucket including SKIP) otherwise' },
  { label: 'Uncertainty (σ)', text: 'entry-frozen entry_sigma_used for a bucket that actually qualified as a trade; live sigma_used for a still-open qualified position; computed fresh (same calculate_time_decayed_sigma formula CP4 itself uses) when nothing has qualified yet' },
  { label: 'Sigma markers (-4σ..+4σ)', text: 'μ ± n·σ, recomputed on every refresh from that day\'s actual values — never fixed/hardcoded' },
  { label: 'Fee calculation', text: 'maker rate by default: ceil(0.0175 × price × (1−price) × contracts × 100) / 100, matches scripts/trading/fee_calculator.py exactly' },
  { label: 'Liquidity guard', text: 'is_liquid = volume > 100, same threshold CP4 itself uses (EDGE_THRESHOLD_LIQ/ILL split)' },
  { label: 'Bucket set', text: 'latest single snapshot batch only (MAX(retrieved_at), 45-min staleness cutoff) — matches CP4\'s own query, so removed/stale buckets drop out instead of lingering' },
  { label: 'Model % / Edge columns', text: 'Model % = exact Gaussian CDF mass between the bucket\'s (already threshold-opened) floor/cap given today\'s μ/σ (Python math.erf, not the chart\'s JS approximation). Edge = Model % − market Chance%.' },
  { label: 'σ marker chip colors', text: '0σ = green (sole Yes-bet target), ±2σ = yellow, ±3σ = red (No-bet targets) — operator-specified single points, per WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md context. Gold ★ on −2σ/−3σ/+3σ only is a separate, more specific marker layered on top.' },
  { label: 'Sigma-Marker Performance panel', text: 'Live, recomputed on every load from every settled trade (weather_position_exits_clean) across all 3 cities — grows as more trades settle, not a snapshot. Each trade\'s entry-time signed z-score is rounded to the nearest integer marker (-4..+4), so these numbers will NOT exactly match WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md\'s custom zone-ranges — different binning method, same underlying trades. Cell color: green/red requires n≥8 (the same bar that doc used to call a zone "robust"); anything thinner stays yellow/gray regardless of ROI sign.' },
];

const KNOWN_ISSUES = [
  // Resolved 2026-07-17: NOT a bug. Compared today/tomorrow (0.9%/4.5% of
  // snapshot rows nonzero) against the 3 prior settled days (99.4%/100%/100%
  // nonzero) -- these weather markets genuinely have near-zero trading
  // activity in the early hours of a trading day; volume accumulates as
  // the day progresses. The collector is reporting reality correctly. A
  // bucket showing "illiquid" early in today's session is an accurate
  // read of the market, not stale/broken data.
];

function renderFooter() {
  const srcEl = document.getElementById('dataSourceList');
  srcEl.innerHTML = DATA_SOURCES.map(d => `<li><strong>${d.label}:</strong> ${d.text}</li>`).join('');

  const issuesEl = document.getElementById('knownIssuesList');
  issuesEl.innerHTML = KNOWN_ISSUES.length
    ? KNOWN_ISSUES.map(i => `<li class="issue-${i.status}">${i.text}</li>`).join('')
    : '<li class="issue-resolved">No known issues.</li>';
}

const state = {
  station: null,
  date: null,
  cities: [],
  ladder: null,          // last /api/ladder response
  rowInputs: {},          // bucket_label -> {noPrice, noInvestment, yesPrice, yesInvestment} (never overwritten by refresh)
  winningBucket: null,    // bucket_label currently marked as the winning outcome
  journal: [],
  probChart: null,
  countdownSec: PRICE_REFRESH_MS / 1000,
};

function todayIso() { return new Date().toISOString().slice(0, 10); }
function tomorrowIso() {
  const d = new Date(); d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// Fee math — mirrors app/fees.py / scripts/trading/fee_calculator.py exactly.
// Maker is the default everywhere (live trading architecture is maker-only
// resting limit orders); taker is not exposed as a default anywhere in the UI.
// ---------------------------------------------------------------------------
function makerFee(p, n) { return Math.ceil(0.0175 * p * (1 - p) * n * 100) / 100; }
function takerFee(p, n) { return Math.ceil(0.07 * p * (1 - p) * n * 100) / 100; }

function calcSide(priceCents, investmentUsd) {
  const p = Math.max(0, Math.min(1, priceCents / 100));
  const costPerContract = priceCents / 100;
  const contracts = costPerContract > 0 ? Math.floor(investmentUsd / costPerContract) : 0;
  const fee = makerFee(p, contracts);
  const totalCost = round2(contracts * costPerContract + fee);
  const payoutIfWin = contracts * 1.0;
  const profitIfWin = round2(payoutIfWin - totalCost);
  return { contracts, fee, totalCost, payoutIfWin, profitIfWin };
}

function round2(x) { return Math.round(x * 100) / 100; }

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
async function init() {
  const cfg = await fetchJSON('/api/config');
  state.cities = cfg.cities.filter(c => c.enabled);
  state.station = state.cities[0].station_code;
  state.date = todayIso();

  renderCityTabs();
  renderDayToggle();
  renderFooter();
  initNotepad();
  document.getElementById('journalForm').addEventListener('submit', onJournalSubmit);

  await refreshAll(true);
  await refreshJournal();
  await refreshNotepad();
  await refreshSigmaPerformance();

  setInterval(() => refreshLadder(false), PRICE_REFRESH_MS);
  setInterval(tickCountdown, 1000);
  // Global, city-agnostic view (all 3 cities' settled trades) -- trades
  // settle once a day via the nightly recon job, so the 5-min orchestrator
  // cadence is more than fast enough here, no need for the 20s poll.
  setInterval(refreshSigmaPerformance, MODEL_REFRESH_MS);
}

// Only today/tomorrow -- matches Kalshi's own UI (currently-open markets
// only). Deliberately no historical/past-date browsing.
function renderDayToggle() {
  const el = document.getElementById('dayToggle');
  el.innerHTML = '';
  const options = [
    { label: 'Today', date: todayIso() },
    { label: 'Tomorrow', date: tomorrowIso() },
  ];
  for (const o of options) {
    const btn = document.createElement('button');
    btn.className = 'city-tab' + (o.date === state.date ? ' active' : '');
    btn.textContent = o.label;
    btn.addEventListener('click', () => {
      state.date = o.date;
      renderDayToggle();
      onCityOrDateChanged();
    });
    el.appendChild(btn);
  }
}

function onCityOrDateChanged() {
  state.rowInputs = {};      // new station/date = genuinely different contracts, safe to reset
  state.winningBucket = null;
  refreshAll(true);
  refreshJournal();
}

function tickCountdown() {
  state.countdownSec = Math.max(0, state.countdownSec - 1);
  const el = document.getElementById('refreshCountdown');
  if (el) el.textContent = `${state.countdownSec}s`;
}

function renderCityTabs() {
  const el = document.getElementById('cityTabs');
  el.innerHTML = '';
  for (const c of state.cities) {
    const btn = document.createElement('button');
    btn.className = 'city-tab' + (c.station_code === state.station ? ' active' : '');
    btn.textContent = c.city;
    btn.addEventListener('click', () => {
      state.station = c.station_code;
      renderCityTabs();
      onCityOrDateChanged();
      refreshNotepad(); // notepad is per-city, not per-date -- reload on city switch only
    });
    el.appendChild(btn);
  }
}

async function refreshAll(isFullRefresh) {
  await refreshLadder(isFullRefresh);
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Ladder fetch + render (preserves in-progress user inputs across refresh)
// ---------------------------------------------------------------------------
async function refreshLadder(isFullRefresh) {
  const indicator = document.getElementById('refreshIndicator');
  try {
    const data = await fetchJSON(`/api/ladder?station=${state.station}&target_date=${state.date}`);
    state.ladder = data;

    // Seed rowInputs defaults only for buckets we haven't seen yet — never
    // touch an existing entry, so a user mid-edit never gets reset by a poll.
    for (const b of data.buckets) {
      if (!state.rowInputs[b.bucket_label]) {
        state.rowInputs[b.bucket_label] = {
          noPrice: b.no_ask_cents ?? 50,
          noInvestment: 0,
          yesPrice: b.yes_ask_cents ?? 50,
          yesInvestment: 0,
        };
      }
    }

    renderReferenceStrip(data);
    renderProbabilityChart(data);
    renderVolumeTable(data);
    renderLadderTable(data);
    renderSimulation();

    const staleBanner = document.getElementById('staleBanner');
    staleBanner.style.display = data.data_stale ? '' : 'none';

    indicator.classList.remove('stale');
    indicator.textContent = 'live';

    // Reset the countdown on every successful load, full or poll-triggered.
    state.countdownSec = PRICE_REFRESH_MS / 1000;
    const cd = document.getElementById('refreshCountdown');
    if (cd) {
      cd.textContent = `${state.countdownSec}s`;
      cd.classList.add('reset');
      setTimeout(() => cd.classList.remove('reset'), 400);
    }
  } catch (e) {
    indicator.classList.add('stale');
    indicator.textContent = 'refresh failed';
    console.error(e);
  }
}

const SOURCE_LABELS = {
  entry_frozen: 'entry-frozen',
  live: 'live (entry-frozen unavailable)',
  ledger_skip: 'ledger (no bucket qualified yet)',
  computed_fresh: 'computed fresh (CP4 formula, no signal row yet)',
  none: 'no data',
};
function sourceBadge(label, source) {
  const span = document.createElement('span');
  span.className = 'badge ' + source;
  span.textContent = `${label}: ${SOURCE_LABELS[source] || source}`;
  return span;
}

function renderReferenceStrip(data) {
  document.getElementById('refStripSubtitle').textContent =
    `${state.station} ${state.date}` + (data.hours_to_settle != null ? ` — ${data.hours_to_settle.toFixed(1)}h to settle` : '');

  const strip = document.getElementById('sigmaMarkerStrip');
  strip.innerHTML = '';
  if (data.mu == null || data.sigma == null) {
    strip.innerHTML = '<div class="hint">No model prediction available yet for this station/date.</div>';
  } else {
    for (const m of data.sigma_markers) {
      const div = document.createElement('div');
      const isStarred = STAR_MARKERS.has(m.n);
      const colorClass = markerColorClass(m.n);
      div.className = 'sigma-marker' + (colorClass ? ' ' + colorClass : '') + (isStarred ? ' starred' : '');
      const star = isStarred ? ' &#9733;' : '';
      div.innerHTML = `<div class="n">${m.n > 0 ? '+' : ''}${m.n}&sigma;${star}</div><div class="temp">${m.temp_f}&deg;F</div>`;
      strip.appendChild(div);
    }
  }

  const badges = document.getElementById('sourceBadges');
  badges.innerHTML = '';
  badges.appendChild(sourceBadge('&mu;'.replace('&mu;', 'μ'), data.mu_source));
  badges.appendChild(sourceBadge('σ', data.sigma_source));
}

function renderProbabilityChart(data) {
  const ctx = document.getElementById('probabilityChart');
  const emptyMsgId = 'probChartEmptyMsg';
  document.getElementById(emptyMsgId)?.remove();
  if (data.mu == null || data.sigma == null) {
    if (state.probChart) { state.probChart.destroy(); state.probChart = null; }
    const msg = document.createElement('div');
    msg.id = emptyMsgId;
    msg.className = 'hint';
    msg.style.padding = '40px 0';
    msg.style.textAlign = 'center';
    msg.textContent = 'No model prediction available yet for this station/date — market bars will appear once buckets are quoted.';
    ctx.parentElement.appendChild(msg);
    // Still nothing to draw a model curve against, but market bars alone
    // aren't useful without it either — skip the chart entirely rather
    // than draw a bars-only chart that looks like a rendering failure.
    return;
  }

  const labels = data.buckets.map(b => b.bucket_label);
  const marketChance = data.buckets.map(b => b.chance_pct);
  // Reuse the backend's exact model_prob_pct (Python math.erf) instead of
  // re-deriving it here -- one source of truth, matches the ladder's
  // Model % column exactly instead of a second, JS-approximated curve.
  const modelProb = data.buckets.map(b => b.model_prob_pct);

  if (state.probChart) state.probChart.destroy();
  state.probChart = new Chart(ctx, {
    data: {
      labels,
      datasets: [
        {
          type: 'bar',
          label: 'Market chance %',
          data: marketChance,
          backgroundColor: 'rgba(23, 201, 100, 0.35)',
          borderColor: '#17c964',
          borderWidth: 1,
        },
        {
          type: 'line',
          label: 'Model probability %',
          data: modelProb,
          borderColor: '#4d8dff',
          backgroundColor: '#4d8dff',
          tension: 0.3,
          pointRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      scales: {
        x: { ticks: { color: '#8891a3' }, grid: { color: '#232937' } },
        y: {
          beginAtZero: true, max: 100,
          ticks: { color: '#8891a3' }, grid: { color: '#232937' },
          title: { display: true, text: '%', color: '#8891a3' },
        },
      },
      plugins: { legend: { labels: { color: '#e6e9ef' } } },
    },
  });
}

function renderVolumeTable(data) {
  const tbody = document.querySelector('#volumeTable tbody');
  tbody.innerHTML = '';
  for (const b of data.buckets) {
    const tr = document.createElement('tr');
    const liquid = b.is_liquid == null ? '—' : (b.is_liquid ? 'yes' : 'no');
    const liquidClass = b.is_liquid === false ? 'illiquid-flag' : '';
    tr.innerHTML = `
      <td>${b.bucket_label}</td>
      <td>${b.volume ?? '—'}</td>
      <td>${b.open_interest ?? '—'}</td>
      <td class="${liquidClass}">${liquid}</td>`;
    tbody.appendChild(tr);
  }
}

function bucketRangeLabel(b) {
  if (b.bucket_floor != null && b.bucket_cap != null) return `${b.bucket_floor}–${b.bucket_cap}°`;
  if (b.bucket_floor != null) return `${b.bucket_floor}° or above`;
  if (b.bucket_cap != null) return `${b.bucket_cap}° or below`;
  return b.bucket_label;
}

// Exactly these three sigma points get a star -- operator-specified,
// not derived from any rule (explicitly NOT the naive -2/+2 symmetry:
// +2sigma was a confirmed losing zone at -11.1% ROI and stays unstarred).
const STAR_MARKERS = new Set([-2, -3, 3]);

// Shared color rule for BOTH the top reference strip and the ladder's
// sigma-chip column -- they must always agree, same city, same day:
// 0sigma = green (sole Yes-bet target), +-2sigma = yellow, +-3sigma = red,
// everything else uncolored.
function markerColorClass(n) {
  if (n === 0) return 'chip-green';
  if (n === 2 || n === -2) return 'chip-yellow';
  if (n === 3 || n === -3) return 'chip-red';
  return '';
}

function renderLadderTable(data) {
  const tbody = document.getElementById('ladderBody');
  tbody.innerHTML = '';
  for (const b of data.buckets) {
    const inputs = state.rowInputs[b.bucket_label];
    const tr = document.createElement('tr');
    tr.dataset.bucket = b.bucket_label;


    // Same format as the reference strip (n-sigma / temp) so each row is
    // self-contained -- no need to cross-reference the top strip. Stacked
    // vertically (not wrapped inline) in bell-curve order: -4sigma at top
    // down through -1, then 0, then +1 up through +4sigma at the bottom --
    // sigma_markers_in_bucket is already ascending by construction
    // (backend builds it from range(-4,5)), so no re-sort needed here.
    const sigmaChips = b.sigma_markers_in_bucket
      .map(n => {
        const marker = data.sigma_markers.find(m => m.n === n);
        const tempStr = marker ? ` / ${marker.temp_f}&deg;F` : '';
        const star = STAR_MARKERS.has(n) ? ' &#9733;' : '';
        // Same markerColorClass() shared with the reference strip -- the
        // two must always agree. Star (-2/-3/+3 only) is a separate, more
        // specific marker layered on top, not the same set as the colors.
        const colorClass = markerColorClass(n);
        return `<div class="sigma-chip${colorClass ? ' ' + colorClass : ''}${star ? ' starred' : ''}">${n > 0 ? '+' : ''}${n}&sigma;${tempStr}${star}</div>`;
      }).join('');

    const edgeClass = b.edge_pct == null ? '' : (b.edge_pct >= 0 ? 'edge-pos' : 'edge-neg');
    const edgeStr = b.edge_pct == null ? '—' : `${b.edge_pct >= 0 ? '+' : ''}${b.edge_pct}%`;

    tr.innerHTML = `
      <td><input type="checkbox" class="win-checkbox" ${state.winningBucket === b.bucket_label ? 'checked' : ''}></td>
      <td class="col-bucket"><span class="bucket-range">${bucketRangeLabel(b)}</span></td>
      <td class="col-sigma">${sigmaChips || '&nbsp;'}</td>
      <td class="col-chance">${b.chance_pct != null ? b.chance_pct + '%' : '—'}</td>
      <td class="col-model">${b.model_prob_pct != null ? b.model_prob_pct + '%' : '—'}</td>
      <td class="col-edge ${edgeClass}">${edgeStr}</td>
      <td class="col-yesno">
        <span class="pill yes">Yes ${fmtC(b.yes_ask_cents)}</span><br>
        <span class="pill no">No ${fmtC(b.no_ask_cents)}</span>
      </td>
      <td class="calc-cell no-side"></td>
      <td class="calc-cell yes-side"></td>
    `;
    tbody.appendChild(tr);

    tr.querySelector('.win-checkbox').addEventListener('change', (e) => {
      state.winningBucket = e.target.checked ? b.bucket_label : null;
      renderLadderTable(state.ladder); // re-render so only one checkbox is checked
      renderSimulation();
    });

    buildCalcCell(tr.querySelector('.calc-cell.no-side'), b, 'no', inputs);
    buildCalcCell(tr.querySelector('.calc-cell.yes-side'), b, 'yes', inputs);
  }
}

function fmtC(cents) { return cents == null ? '—' : `${cents}¢`; }

// Builds the cell's DOM ONCE (labels + inputs). Deliberately does NOT get
// called again on every keystroke -- rebuilding the inputs' innerHTML on
// each 'input' event was destroying and recreating the focused element,
// which killed keyboard focus/cursor position after every character (the
// "can't type 0" bug). Only updateCalcSummary() runs on input events now,
// touching just the read-only summary lines below the inputs.
function buildCalcCell(cell, bucket, side, inputs) {
  const priceKey = side === 'no' ? 'noPrice' : 'yesPrice';
  const investKey = side === 'no' ? 'noInvestment' : 'yesInvestment';

  cell.innerHTML = `
    <div class="calc-grid">
      <div class="calc-field">
        <label for="price-${side}-${bucket.bucket_label}">Price &cent;</label>
        <input id="price-${side}-${bucket.bucket_label}" type="number" class="price-input" step="0.5" min="0" max="99.5" value="${inputs[priceKey]}">
      </div>
      <div class="calc-field">
        <label for="pos-${side}-${bucket.bucket_label}">Position $</label>
        <input id="pos-${side}-${bucket.bucket_label}" type="number" class="invest-input" step="1" min="0" value="${inputs[investKey]}">
      </div>
      <div class="calc-summary-block"></div>
    </div>`;

  cell.querySelector('.price-input').addEventListener('input', (e) => {
    inputs[priceKey] = parseFloat(e.target.value) || 0;
    updateCalcSummary(cell, bucket, side, inputs);
    renderSimulation();
  });
  cell.querySelector('.invest-input').addEventListener('input', (e) => {
    inputs[investKey] = parseFloat(e.target.value) || 0;
    updateCalcSummary(cell, bucket, side, inputs);
    renderSimulation();
  });

  updateCalcSummary(cell, bucket, side, inputs);
}

function updateCalcSummary(cell, bucket, side, inputs) {
  const priceKey = side === 'no' ? 'noPrice' : 'yesPrice';
  const investKey = side === 'no' ? 'noInvestment' : 'yesInvestment';
  const result = calcSide(inputs[priceKey], inputs[investKey]);

  cell.querySelector('.calc-summary-block').innerHTML = `
    <div class="calc-summary"><span>Contracts</span><span>${result.contracts}</span></div>
    <div class="calc-summary"><span>Fee (maker)</span><span>$${result.fee.toFixed(2)}</span></div>
    <div class="calc-summary"><span>Payout if win</span><span>$${result.payoutIfWin.toFixed(2)}</span></div>
    <div class="calc-summary"><span>Profit if win</span><span class="${result.profitIfWin >= 0 ? 'profit-pos' : 'profit-neg'}">$${result.profitIfWin.toFixed(2)}</span></div>
    ${bucket.is_liquid === false ? '<div class="calc-summary illiquid-flag">volume &le; 100 — illiquid</div>' : ''}
  `;
}

// ---------------------------------------------------------------------------
// Simulation summary — resolves mixed Yes/No positions across the whole
// ladder against the single selected winning bucket.
//   - Selected winning bucket: its YES position wins, its NO position loses.
//   - Every other bucket: its NO position wins, its YES position loses.
// ---------------------------------------------------------------------------
function renderSimulation() {
  const tbody = document.getElementById('simBody');
  const totalsEl = document.getElementById('simTotals');
  tbody.innerHTML = '';

  if (!state.ladder) return;
  let grossTotal = 0, netTotal = 0, totalInvested = 0, totalFees = 0;
  let anyRows = false;

  for (const b of state.ladder.buckets) {
    const inputs = state.rowInputs[b.bucket_label];
    if (!inputs) continue;
    const isWinningBucket = state.winningBucket === b.bucket_label;

    for (const side of ['no', 'yes']) {
      const investKey = side === 'no' ? 'noInvestment' : 'yesInvestment';
      const priceKey = side === 'no' ? 'noPrice' : 'yesPrice';
      const investment = inputs[investKey];
      if (!investment || investment <= 0) continue;
      anyRows = true;

      const result = calcSide(inputs[priceKey], investment);
      const sideWins = (side === 'yes') ? isWinningBucket : !isWinningBucket;
      const pnl = state.winningBucket == null
        ? null
        : (sideWins ? result.profitIfWin : -result.totalCost);

      totalInvested += result.totalCost;
      totalFees += result.fee;
      if (pnl != null) {
        grossTotal += sideWins ? result.payoutIfWin : 0;
        netTotal += pnl;
      }

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${bucketRangeLabel(b)}</td>
        <td>${side.toUpperCase()}</td>
        <td>$${result.totalCost.toFixed(2)}</td>
        <td>$${result.fee.toFixed(2)}</td>
        <td>${state.winningBucket == null ? 'pending' : (sideWins ? '<span class="row-win">WIN</span>' : '<span class="row-loss">LOSS</span>')}</td>
        <td class="${pnl == null ? '' : (pnl >= 0 ? 'row-win' : 'row-loss')}">${pnl == null ? '—' : '$' + pnl.toFixed(2)}</td>`;
      tbody.appendChild(tr);
    }
  }

  if (!anyRows) {
    tbody.innerHTML = '<tr><td colspan="6" class="hint">No investment entered on any row yet.</td></tr>';
  }

  totalsEl.innerHTML = state.winningBucket == null
    ? `<div class="hint">Select a winning bucket above to see P&amp;L.</div>`
    : `<div><span class="total-label">Total invested</span><span class="total-value">$${totalInvested.toFixed(2)}</span></div>
       <div><span class="total-label">Total fees</span><span class="total-value">$${totalFees.toFixed(2)}</span></div>
       <div><span class="total-label">Gross payout</span><span class="total-value">$${grossTotal.toFixed(2)}</span></div>
       <div><span class="total-label">Net P&amp;L</span><span class="total-value ${netTotal >= 0 ? 'pos' : 'neg'}">$${netTotal.toFixed(2)}</span></div>`;
}

// ---------------------------------------------------------------------------
// Manual journal
// ---------------------------------------------------------------------------
async function refreshJournal() {
  const data = await fetchJSON(`/api/journal?station=${state.station}`);
  state.journal = data.entries;
  renderJournal();
}

function renderJournal() {
  const tbody = document.getElementById('journalBody');
  tbody.innerHTML = '';
  for (const e of state.journal) {
    const tr = document.createElement('tr');
    const pnlClass = e.pnl_usd == null ? '' : (e.pnl_usd >= 0 ? 'row-win' : 'row-loss');
    tr.innerHTML = `
      <td>${e.target_date}</td><td>${e.bucket_label}</td><td>${e.side}</td>
      <td>${e.price_cents}&cent;</td><td>$${Number(e.investment_usd).toFixed(2)}</td>
      <td>${e.contracts}</td><td>${e.outcome}</td>
      <td class="${pnlClass}">${e.pnl_usd == null ? '—' : '$' + Number(e.pnl_usd).toFixed(2)}</td>
      <td>${e.notes ?? ''}</td>
      <td><button class="journal-delete" data-id="${e.id}">delete</button></td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll('.journal-delete').forEach(btn => {
    btn.addEventListener('click', async () => {
      await fetch(`/api/journal/${btn.dataset.id}`, { method: 'DELETE' });
      refreshJournal();
    });
  });
}

async function onJournalSubmit(e) {
  e.preventDefault();
  const form = e.target;
  const body = {
    station_code: state.station,
    target_date: state.date,
    bucket_label: form.bucket_label.value,
    side: form.side.value,
    price_cents: parseFloat(form.price_cents.value),
    investment_usd: parseFloat(form.investment_usd.value),
    contracts: parseInt(form.contracts.value, 10),
    outcome: form.outcome.value,
    notes: form.notes.value || null,
  };
  await fetch('/api/journal', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  form.reset();
  refreshJournal();
}

// ---------------------------------------------------------------------------
// Per-city scratch notepad -- freeform, separate from the structured
// journal above. One overwritable note per station, debounced autosave.
// ---------------------------------------------------------------------------
let notepadSaveTimer = null;
let notepadLoadedStation = null; // guards against saving stale content over a station we've since switched away from

function initNotepad() {
  const textarea = document.getElementById('notepadText');
  textarea.addEventListener('input', () => {
    setSaveStatus('unsaved');
    clearTimeout(notepadSaveTimer);
    const stationAtEdit = state.station;
    notepadSaveTimer = setTimeout(() => saveNotepad(stationAtEdit, textarea.value), 800);
  });
  document.getElementById('notepadCollapse').addEventListener('click', () => {
    const widget = document.getElementById('notepadWidget');
    const collapsed = widget.classList.toggle('collapsed');
    document.getElementById('notepadCollapse').textContent = collapsed ? '+' : '−';
  });
}

async function refreshNotepad() {
  const cityName = (state.cities.find(c => c.station_code === state.station) || {}).city || state.station;
  document.getElementById('notepadCityLabel').textContent = `Notes — ${cityName}`;
  notepadLoadedStation = state.station;
  const data = await fetchJSON(`/api/notes/${state.station}`);
  // Guard: if the city changed again while this fetch was in flight, don't
  // clobber the textarea with a now-stale response.
  if (notepadLoadedStation !== state.station) return;
  document.getElementById('notepadText').value = data.note_text || '';
  setSaveStatus(data.updated_at ? `saved` : '');
}

async function saveNotepad(station, text) {
  setSaveStatus('saving…');
  try {
    await fetch(`/api/notes/${station}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note_text: text }),
    });
    if (state.station === station) setSaveStatus('saved');
  } catch (e) {
    if (state.station === station) setSaveStatus('save failed');
    console.error(e);
  }
}

function setSaveStatus(text) {
  const el = document.getElementById('notepadSaveStatus');
  if (el) el.textContent = text;
}

// ---------------------------------------------------------------------------
// Sigma-marker performance -- live, growing computation (all settled trades
// across all 3 cities), pooled table + per-city cross-tab grid. Global/
// city-agnostic -- doesn't depend on state.station or state.date.
// ---------------------------------------------------------------------------
function markerLabel(n) { return `${n > 0 ? '+' : ''}${n}σ`; }

function tagClass(tag) {
  return { green: 'perf-green', red: 'perf-red', thin: 'perf-thin', no_data: 'perf-nodata' }[tag] || '';
}

function cellHtml(cell) {
  if (cell.n === 0) return '<span class="hint">—</span>';
  return `<div class="perf-cell ${tagClass(cell.tag)}" title="n=${cell.n}, staked $${cell.staked}, pnl $${cell.pnl}">`
    + `<div class="perf-pct">${cell.win_pct}% win</div>`
    + `<div class="perf-roi">${cell.roi_pct == null ? '—' : (cell.roi_pct >= 0 ? '+' : '') + cell.roi_pct + '% ROI'}</div>`
    + `<div class="perf-n">n=${cell.n}</div></div>`;
}

async function refreshSigmaPerformance() {
  let data;
  try {
    data = await fetchJSON('/api/sigma-performance');
  } catch (e) {
    console.error(e);
    return;
  }

  const pooledHead = document.getElementById('sigmaPerfPooledHead');
  pooledHead.innerHTML = '<th>All cities</th>' + data.markers.map(m => `<th>${markerLabel(m)}</th>`).join('');
  document.getElementById('sigmaPerfPooledBody').innerHTML =
    `<tr><td>Pooled</td>${data.markers.map(m => `<td>${cellHtml(data.pooled[m])}</td>`).join('')}</tr>`;

  const cityHead = document.getElementById('sigmaPerfCityHead');
  cityHead.innerHTML = '<th>City</th>' + data.markers.map(m => `<th>${markerLabel(m)}</th>`).join('');
  const cityBody = document.getElementById('sigmaPerfCityBody');
  cityBody.innerHTML = Object.keys(data.by_city).sort().map(city => {
    const cityName = (state.cities.find(c => c.station_code === city) || {}).city || city;
    return `<tr><td>${cityName}</td>${data.markers.map(m => `<td>${cellHtml(data.by_city[city][m])}</td>`).join('')}</tr>`;
  }).join('');
}

init();
