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
  { label: 'σ marker chip colors', text: '0σ = green (sole Yes-bet target), ±2σ = yellow, ±3σ = red (No-bet targets) — operator-specified single points, per WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md context.' },
  { label: 'σ marker gold ★', text: 'Dynamic (permanent rule as of 2026-07-17, supersedes an earlier same-night fixed-set revert): a marker gets a star exactly when the Sigma-Marker Performance panel\'s pooled "All Cities" row shows positive NET DOLLARS for it — not ROI%, and not a fixed set. Updates automatically on the panel\'s 5-min refresh as trades settle; a marker can gain or lose its star on its own. Star = historically profitable in the pooled data, NOT live trading eligibility — a starred marker can still fall inside cp4_kelly_sizer.py\'s live |z|&lt;1.0 no-trade exclusion zone and be untradeable right now (hover a star for this note).' },
  { label: 'Sigma-Marker Performance panel', text: 'Live, recomputed on every load from every settled trade (weather_position_exits_clean) across all 3 cities — grows as more trades settle, not a snapshot. Each trade\'s entry-time signed z-score is rounded to the nearest integer marker (-4..+4), so these numbers will NOT exactly match WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md\'s custom zone-ranges — different binning method, same underlying trades. Every marker (including 0σ) uses the plain recorded No-side outcome — no Yes-side resimulation. Cell color: green = positive ROI (any sample size), everything else neutral.' },
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
  // market_ticker -> {station, target_date, bucket_label, bucket_floor, bucket_cap,
  //   market_ticker, noPrice, noInvestment, yesPrice, yesInvestment}. Keyed by
  // ticker (not bucket_label, which collides across cities/dates -- e.g. "T90"
  // exists for every station) so rows from multiple cities can coexist in the
  // same session (2026-07-17, see onCityOrDateChanged()). Never overwritten
  // by a refresh once seeded.
  rowInputs: {},
  // stationDateKey(station, date) -> bucket_label currently marked as the
  // winning outcome for THAT city/date. Replaces the old single global
  // winningBucket string (2026-07-17) -- each city has its own real-world
  // outcome, so one shared value couldn't represent multiple cities' results
  // at once.
  winningBuckets: {},
  journal: [],
  probChart: null,
  countdownSec: PRICE_REFRESH_MS / 1000,
  sigmaPerfPooled: null,  // /api/sigma-performance's pooled row, keyed by marker -- feeds BOTH the live performance panel AND isStarredMarker() (positive net $), same source of truth so they stay in sync automatically
};

// Browser-local calendar date, NOT UTC (toISOString()/setUTCDate() give the
// UTC calendar day -- wrong for a human clicking "Today": any time after
// ~8PM EDT, UTC has already rolled to the next day, so the old
// toISOString()-based version silently selected tomorrow's contract instead
// of today's. Fixed 2026-07-17 -- same today/tomorrow confusion family as
// the settlement-clock bug fixed in cp4_kelly_sizer.py tonight, but this is
// an independent bug in a different codebase (dashboard date-picker
// defaults), not the same code path.
function localIso(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}
function todayIso() { return localIso(new Date()); }
function tomorrowIso() {
  const d = new Date(); d.setDate(d.getDate() + 1);
  return localIso(d);
}

// Ladder title ("Highest temperature in {City} {Today|Tomorrow}? {date}") --
// parses state.date's Y-M-D components directly rather than `new Date(iso)`
// + toLocaleString, since that round-trips through UTC midnight and would
// shift the displayed date backward in negative-UTC-offset zones (the same
// bug class fixed in todayIso()/tomorrowIso() above) -- a pure calendar
// date like this has no time-of-day, so there's nothing to convert.
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];
function ordinalSuffix(n) {
  if (n % 100 >= 11 && n % 100 <= 13) return 'th';
  switch (n % 10) {
    case 1: return 'st';
    case 2: return 'nd';
    case 3: return 'rd';
    default: return 'th';
  }
}
function formatLongDate(isoDateStr) {
  const [y, m, d] = isoDateStr.split('-').map(Number);
  return `${MONTH_NAMES[m - 1]} ${d}${ordinalSuffix(d)}, ${y}`;
}
function renderLadderTitle() {
  const el = document.getElementById('ladderTitleMain');
  if (!el) return;
  const cityName = (state.cities.find(c => c.station_code === state.station) || {}).city || state.station;
  const whenLabel = state.date === todayIso() ? 'Today' : 'Tomorrow';
  el.textContent = `Highest temperature in ${cityName} ${whenLabel}? ${formatLongDate(state.date)}`;
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
  renderPaperPositionCityFilter();
  initPaperPositionColumns();
  document.getElementById('journalForm').addEventListener('submit', onJournalSubmit);

  // Sigma-performance fetched BEFORE the first ladder render -- it drives
  // which sigma markers get a star, so state.sigmaPerfPooled needs to be
  // populated before renderReferenceStrip/renderLadderTable's first paint,
  // not after.
  await refreshSigmaPerformance();
  await refreshAll(true);
  await refreshJournal();
  await refreshNotepad();
  await refreshPaperPositions();

  setInterval(() => refreshLadder(false), PRICE_REFRESH_MS);
  setInterval(tickCountdown, 1000);
  // Global, city-agnostic view (all 3 cities' settled trades) -- trades
  // settle once a day via the nightly recon job, so the 5-min orchestrator
  // cadence is more than fast enough here, no need for the 20s poll.
  setInterval(refreshSigmaPerformance, MODEL_REFRESH_MS);
  // Same rationale as sigma-performance's cadence above -- real paper
  // trades settle once a day, not every 20s.
  setInterval(refreshPaperPositions, MODEL_REFRESH_MS);
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
  // rowInputs/winningBuckets are deliberately NOT reset here (2026-07-17) --
  // both are keyed by market_ticker / stationDateKey() respectively, so they
  // no longer collide across cities/dates, and simulation rows entered on
  // one city tab should persist when switching to another (multi-city
  // sessions), not silently vanish.
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

    // Seed rowInputs defaults only for tickers we haven't seen yet — never
    // touch an existing entry, so a user mid-edit never gets reset by a poll.
    // Keyed by market_ticker (unique per station+date+bucket), NOT
    // bucket_label -- bucket_label alone collides across cities/dates (e.g.
    // "T90" exists for every station), which would silently merge unrelated
    // rows' simulation inputs together once rowInputs stopped being wiped on
    // every city/date switch (see onCityOrDateChanged()). station/target_date/
    // bucket_floor/bucket_cap/bucket_label are carried alongside the numeric
    // inputs so renderSimulation() can render/resolve a row without needing
    // state.ladder to still be showing that row's city.
    for (const b of data.buckets) {
      if (!state.rowInputs[b.market_ticker]) {
        state.rowInputs[b.market_ticker] = {
          station: state.station, target_date: state.date,
          bucket_label: b.bucket_label, bucket_floor: b.bucket_floor, bucket_cap: b.bucket_cap,
          market_ticker: b.market_ticker,
          noPrice: b.no_ask_cents ?? 50,
          noInvestment: 0,
          yesPrice: b.yes_ask_cents ?? 50,
          yesInvestment: 0,
        };
      }
    }

    renderLadderTitle();
    renderReferenceStrip(data);
    renderMarketTimes(data);
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
      const isStarred = isStarredMarker(m.n);
      const colorClass = markerColorClass(m.n);
      div.className = 'sigma-marker' + (colorClass ? ' ' + colorClass : '') + (isStarred ? ' starred' : '');
      const star = isStarred ? ` <span title="${starTitle()}">&#9733;</span>` : '';
      div.innerHTML = `<div class="n">${m.n > 0 ? '+' : ''}${m.n}&sigma;${star}</div><div class="temp">${m.temp_f}&deg;F</div>`;
      strip.appendChild(div);
    }
  }

  const badges = document.getElementById('sourceBadges');
  badges.innerHTML = '';
  badges.appendChild(sourceBadge('&mu;'.replace('&mu;', 'μ'), data.mu_source));
  badges.appendChild(sourceBadge('σ', data.sigma_source));
}

// Market open / close (Last Trading Time) / average daily-high time, for
// the currently selected city. Open/close come back as UTC ISO from the
// backend; formatted here into that city's own local time via the
// browser's Intl API using the timezone already in state.cities.
function renderMarketTimes(data) {
  const row = document.getElementById('marketTimesRow');
  const cityTz = (state.cities.find(c => c.station_code === state.station) || {}).timezone;
  const fmt = (iso) => {
    if (!iso || !cityTz) return '—';
    return new Date(iso).toLocaleString('en-US', {
      timeZone: cityTz, month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', hour12: true, timeZoneName: 'short',
    });
  };
  const items = [
    { label: 'Market open', value: fmt(data.market_open_time) },
    { label: 'Market close (Last Trading Time)', value: fmt(data.market_close_time) },
    { label: 'Avg. daily-high time', value: data.avg_dailyhigh_time_local
        ? `${data.avg_dailyhigh_time_local.slice(0, 5)} ${data.avg_dailyhigh_timezone || ''}`
        + (data.avg_dailyhigh_source_note ? ' ⚠' : '')
        : '—' },
  ];
  row.innerHTML = items.map(i =>
    `<div class="time-chip" ${i.label.includes('daily-high') && data.avg_dailyhigh_source_note ? `title="${data.avg_dailyhigh_source_note}"` : ''}>
       <div class="time-chip-label">${i.label}</div><div class="time-chip-value">${i.value}</div>
     </div>`
  ).join('');
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

// Mirrors Kalshi's real ticker convention: a "between" bucket's ticker
// suffix is B{midpoint} (e.g. B96.5 for 96-97), a threshold bucket's is
// T{strike} (e.g. T97). The T-suffix is the raw stored strike (matches the
// real contract_ticker), but the strike is an EXCLUSIVE boundary -- Kalshi's
// rule is strictly ">strike"/"<strike", confirmed 2026-07-18 against raw
// contract text (KXHIGHMIA-26JUL16-T97: rules "is greater than 97",
// subtitle "98 or above"; -T90: rules "is less than 90", subtitle "89 or
// below"). So the human-readable phrase is strike+-1 (matching Kalshi's own
// subtitle), NOT the bare strike value -- same fix applied to the
// exit_audit_logger.py settlement bug this same night.
function bucketRangeLabel(b) {
  if (b.bucket_floor != null && b.bucket_cap != null) {
    const mid = (b.bucket_floor + b.bucket_cap) / 2;
    return `${b.bucket_floor}–${b.bucket_cap}°, ${mid}`;
  }
  if (b.bucket_floor != null) return `${b.bucket_floor + 1}° or above, T${b.bucket_floor}`;
  if (b.bucket_cap != null) return `${b.bucket_cap - 1}° or below, T${b.bucket_cap}`;
  return b.bucket_label;
}

// Dynamic again as of 2026-07-17, superseding the same-night fixed-set
// revert -- deliberate reversal, operator direction, not a bug being
// reintroduced. New permanent rule: a marker gets a star exactly when the
// Sigma-Marker Performance panel's pooled "All Cities" row shows positive
// NET DOLLARS (cell.pnl > 0) for it -- NOT positive ROI% (tagClass()/the
// panel's own cell coloring stays ROI-based, unchanged; the star criterion
// and the panel's green-cell criterion are deliberately different now, so
// don't assume they always agree). Same source of truth as the panel
// (state.sigmaPerfPooled), so both stay in sync automatically as trades
// settle -- no separate maintenance. Stars gain/lose on their own on the
// next 5-min refreshSigmaPerformance() cycle; no manual intervention.
// Known, accepted consequence: a marker can show starred here (historically
// profitable in the pooled data) while still being excluded from live
// signals by cp4_kelly_sizer.py's |z|<1.0 no-trade zone -- e.g. -1sigma can
// be starred while untradeable under the current live rule. Star = historical
// performance, not live trading eligibility -- see starTitle()'s tooltip.
function isStarredMarker(n) {
  const cell = state.sigmaPerfPooled && state.sigmaPerfPooled[n];
  return !!cell && cell.pnl != null && cell.pnl > 0;
}

// Shared tooltip text for the star glyph itself, both call sites -- keeps
// the clarification from drifting between the reference strip and ladder.
function starTitle() {
  return "Historically profitable (positive net $) in the pooled live-trade data — may not currently be tradeable (e.g. inside cp4_kelly_sizer.py's |z|<1.0 no-trade exclusion zone). Star = historical performance, not live trading eligibility.";
}

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

// station+date -> the single key state.winningBuckets is keyed by. Shared
// so every reader/writer of winningBuckets agrees on the exact key format.
function stationDateKey(station, date) { return `${station}::${date}`; }

function renderLadderTable(data) {
  const tbody = document.getElementById('ladderBody');
  tbody.innerHTML = '';
  const wbKey = stationDateKey(state.station, state.date);
  for (const b of data.buckets) {
    const inputs = state.rowInputs[b.market_ticker];
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
        const star = isStarredMarker(n) ? ` <span title="${starTitle()}">&#9733;</span>` : '';
        // Same markerColorClass() shared with the reference strip -- the
        // two must always agree. Star is the dynamic pooled-net-$ rule
        // (isStarredMarker), a separate signal layered on top, not tied
        // to the fixed colors.
        const colorClass = markerColorClass(n);
        return `<div class="sigma-chip${colorClass ? ' ' + colorClass : ''}${star ? ' starred' : ''}">${n > 0 ? '+' : ''}${n}&sigma;${tempStr}${star}</div>`;
      }).join('');

    const edgeClass = b.edge_pct == null ? '' : (b.edge_pct >= 0 ? 'edge-pos' : 'edge-neg');
    const edgeStr = b.edge_pct == null ? '—' : `${b.edge_pct >= 0 ? '+' : ''}${b.edge_pct}%`;

    tr.innerHTML = `
      <td><input type="checkbox" class="win-checkbox" ${state.winningBuckets[wbKey] === b.bucket_label ? 'checked' : ''}></td>
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
      state.winningBuckets[wbKey] = e.target.checked ? b.bucket_label : null;
      renderLadderTable(state.ladder); // re-render so only one checkbox is checked (for THIS city/date)
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
// Simulation summary — resolves mixed Yes/No positions across EVERY city/date
// a row has been entered for (2026-07-17: no longer scoped to state.ladder,
// the currently-displayed city only), each against ITS OWN city/date's
// selected winning bucket via state.winningBuckets:
//   - Selected winning bucket: its YES position wins, its NO position loses.
//   - Every other bucket (same city/date): its NO position wins, its YES
//     position loses.
//   - A city/date with no winning bucket selected yet: all its rows show
//     "pending", same as before, just per-city now instead of all-or-nothing.
// ---------------------------------------------------------------------------
function renderSimulation() {
  const tbody = document.getElementById('simBody');
  const totalsEl = document.getElementById('simTotals');
  tbody.innerHTML = '';

  // Fee bug fix (2026-07-17): totalInvested now accumulates PURE contract
  // cost only (contracts * price), NOT totalCost (which bundles in the fee)
  // -- previously "Total invested" silently included the fee, so it read as
  // if fees were embedded in "invested" rather than shown as their own
  // deduction. Net P&L's actual DOLLAR VALUE is unchanged by this (Net =
  // Gross - PureInvested - Fees is algebraically identical to the old
  // Gross - (PureInvested+Fees), since fees were always subtracted exactly
  // once via totalCost, per-row, for both wins and losses) -- this fixes
  // what "Total invested" honestly represents, not a double-counted fee.
  let grossTotal = 0, netTotal = 0, totalInvested = 0, totalFees = 0;
  let anyRows = false, anyResolved = false;

  for (const ticker of Object.keys(state.rowInputs)) {
    const inputs = state.rowInputs[ticker];
    const wbKey = stationDateKey(inputs.station, inputs.target_date);
    const winningBucket = state.winningBuckets[wbKey];
    const hasResolution = winningBucket != null;
    const isWinningBucket = hasResolution && winningBucket === inputs.bucket_label;
    if (hasResolution) anyResolved = true;

    for (const side of ['no', 'yes']) {
      const investKey = side === 'no' ? 'noInvestment' : 'yesInvestment';
      const priceKey = side === 'no' ? 'noPrice' : 'yesPrice';
      const investment = inputs[investKey];
      if (!investment || investment <= 0) continue;
      anyRows = true;

      const result = calcSide(inputs[priceKey], investment);
      const sideWins = (side === 'yes') ? isWinningBucket : !isWinningBucket;
      const pnl = !hasResolution
        ? null
        : (sideWins ? result.profitIfWin : -result.totalCost);
      const pureCost = round2(result.totalCost - result.fee);
      const roiPct = pnl != null && pureCost > 0 ? round2((pnl / pureCost) * 100) : null;

      totalInvested += pureCost;
      totalFees += result.fee;
      if (pnl != null) {
        grossTotal += sideWins ? result.payoutIfWin : 0;
        netTotal += pnl;
      }

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${inputs.station}</td>
        <td>${bucketRangeLabel(inputs)}</td>
        <td class="ticker-cell">${inputs.market_ticker}</td>
        <td>${side.toUpperCase()}</td>
        <td>$${pureCost.toFixed(2)}</td>
        <td>$${result.fee.toFixed(2)}</td>
        <td>${!hasResolution ? 'pending' : (sideWins ? '<span class="row-win">WIN</span>' : '<span class="row-loss">LOSS</span>')}</td>
        <td class="${pnl == null ? '' : (pnl >= 0 ? 'row-win' : 'row-loss')}">${pnl == null ? '—' : '$' + pnl.toFixed(2)}</td>
        <td class="${roiPct == null ? '' : (roiPct >= 0 ? 'row-win' : 'row-loss')}">${roiPct == null ? '—' : (roiPct >= 0 ? '+' : '') + roiPct.toFixed(1) + '%'}</td>`;
      tbody.appendChild(tr);
    }
  }

  if (!anyRows) {
    tbody.innerHTML = '<tr><td colspan="9" class="hint">No investment entered on any row yet.</td></tr>';
  }

  totalsEl.innerHTML = !anyResolved
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

// Binary now, per operator simplification: only a positive-ROI cell gets
// highlighted, everything else stays neutral (no separate red/thin/
// no_data states).
function tagClass(tag) {
  return tag === 'positive' ? 'perf-green' : '';
}

// Empty cells (n===0, e.g. -4sigma/+4sigma with no settled trades yet) now
// render through the SAME .perf-cell wrapper as populated cells (styling
// fix 2026-07-17) -- previously a bare <span>, which had no border and no
// enforced size, so it rendered smaller/undefined next to the five-line
// populated cells instead of matching their fixed dimensions. .perf-empty
// just mutes the color; sizing/border come from the shared .perf-cell rule.
function cellHtml(cell) {
  if (cell.n === 0) {
    return '<div class="perf-cell perf-empty" title="No settled trades yet">—</div>';
  }
  const netClass = cell.pnl >= 0 ? 'profit-pos' : 'profit-neg';
  const tooltip = `n=${cell.n}, staked $${cell.staked}, pnl $${cell.pnl}`;
  return `<div class="perf-cell ${tagClass(cell.tag)}" title="${tooltip}">`
    + `<div class="perf-pct">${cell.win_pct}% win</div>`
    + `<div class="perf-roi">${cell.roi_pct == null ? '—' : (cell.roi_pct >= 0 ? '+' : '') + cell.roi_pct + '% ROI'}</div>`
    + `<div class="perf-n">n=${cell.n}</div>`
    + `<div class="perf-dollars">$${cell.staked.toFixed(0)} staked</div>`
    + `<div class="perf-dollars ${netClass}">${cell.pnl >= 0 ? '+' : ''}$${cell.pnl.toFixed(0)} net</div></div>`;
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

  // Star markers (reference strip + ladder) are driven by this pooled row's
  // net-$ per marker (isStarredMarker()), not the green/tag coloring shown
  // here -- re-render both if a ladder view is already on screen so a star
  // change (this refreshes on its own 5-min cadence, independent of the
  // ladder's 20s poll) shows up immediately, not just on the next ladder poll.
  state.sigmaPerfPooled = data.pooled;
  if (state.ladder) {
    renderReferenceStrip(state.ladder);
    renderLadderTable(state.ladder);
  }
}

// ---------------------------------------------------------------------------
// Paper Position Summary -- REAL system-placed paper trades from
// weather_position_exits, via /api/position-exits. Deliberately separate
// from renderSimulation() above: that panel is manual what-if entries,
// never reads or writes this table. Full history, no date filter (operator
// direction 2026-07-18: "full paper-trading history... not scoped to the
// current city/date tab or any rolling window") -- the city dropdown here
// is an optional display filter only, independent of the ladder's city
// tabs/state.station.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Paper Position Summary -- column hide/show + width resize. View-only,
// never adds or removes underlying data/columns -- operator's explicit
// 2026-07-19 scope. State persisted in localStorage so it survives a
// reload. Order MUST match the hardcoded <td> order in
// renderPaperPositionTable() exactly -- this array only drives the
// header row/picker panel/resize handles, not row content itself.
// ---------------------------------------------------------------------------

const PP_COLUMNS = [
  { key: 'station',    label: 'Station' },
  { key: 'exec_time',  label: 'Execution Time (PST)' },
  { key: 'ticker',     label: 'Ticker' },
  { key: 'bucket',     label: 'Bucket' },
  { key: 'actual_temp', label: 'Final Actual Temp' },
  { key: 'pred_temp',  label: 'Model Predicted Temp', divider: true, group: 'entry-start' },
  { key: 'nws',        label: 'NWS Forecast' },
  { key: 'gfs',        label: 'GFS Forecast' },
  { key: 'model_raw',  label: 'Model Raw' },
  { key: 'entry_delta', label: 'Entry Delta' },
  { key: 'entry_edge', label: 'Entry Edge', group: 'entry-end' },
  { key: 'side',       label: 'Side', divider: true },
  { key: 'investment', label: 'Investment' },
  { key: 'price',      label: 'Price' },
  { key: 'contracts',  label: 'Contracts' },
  { key: 'sigma',      label: 'Sigma' },
  { key: 'fee',        label: 'Fee' },
  { key: 'result',     label: 'Result' },
  { key: 'pnl',        label: 'P&L' },
  { key: 'roi',        label: 'ROI%' },
];

const PP_STORAGE_KEY = 'ppColumnState.v1';

function _ppLoadState() {
  try {
    const raw = localStorage.getItem(PP_STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (e) { return {}; }
}
function _ppSaveState(s) {
  try { localStorage.setItem(PP_STORAGE_KEY, JSON.stringify(s)); } catch (e) {}
}

const ppState = _ppLoadState();
if (!ppState.hidden) ppState.hidden = {};
if (!ppState.widths) ppState.widths = {};

function _ppApplyVisibilityCSS() {
  let style = document.getElementById('ppVisibilityStyle');
  if (!style) {
    style = document.createElement('style');
    style.id = 'ppVisibilityStyle';
    document.head.appendChild(style);
  }
  style.textContent = PP_COLUMNS.map((c, i) => {
    if (!ppState.hidden[c.key]) return '';
    const n = i + 1;
    return `#paperPositionTable #paperPositionHeaderRow th:nth-child(${n}), ` +
           `#paperPositionTable tbody td:nth-child(${n}) { display: none; }`;
  }).join('\n');
}

function _ppApplyWidthsCSS() {
  let style = document.getElementById('ppWidthStyle');
  if (!style) {
    style = document.createElement('style');
    style.id = 'ppWidthStyle';
    document.head.appendChild(style);
  }
  style.textContent = PP_COLUMNS.map((c, i) => {
    const w = ppState.widths[c.key];
    if (!w) return '';
    const n = i + 1;
    return `#paperPositionTable #paperPositionHeaderRow th:nth-child(${n}), ` +
           `#paperPositionTable tbody td:nth-child(${n}) ` +
           `{ width: ${w}px; max-width: ${w}px; overflow: hidden; text-overflow: ellipsis; }`;
  }).join('\n');
}

function _ppUpdateGroupSpans() {
  const startIdx = PP_COLUMNS.findIndex(c => c.group === 'entry-start');
  const endIdx = PP_COLUMNS.findIndex(c => c.group === 'entry-end');
  let a = 0, entry = 0, b = 0;
  PP_COLUMNS.forEach((c, i) => {
    if (ppState.hidden[c.key]) return;
    if (i < startIdx) a++;
    else if (i <= endIdx) entry++;
    else b++;
  });
  const row = document.getElementById('paperPositionGroupRow');
  row.innerHTML = `<th colspan="${a}"></th>` +
    (entry > 0 ? `<th colspan="${entry}" class="col-divider-start" style="text-align:center">Entry</th>` : '') +
    `<th colspan="${b}"></th>`;
}

function _ppWireResize() {
  document.querySelectorAll('#paperPositionHeaderRow th').forEach((th, i) => {
    const handle = th.querySelector('.col-resize-handle');
    if (!handle) return;
    let startX, startW;
    const onMove = (e) => {
      const w = Math.max(30, startW + (e.clientX - startX));
      th.style.width = w + 'px';
      th.style.maxWidth = w + 'px';
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      ppState.widths[PP_COLUMNS[i].key] = th.offsetWidth;
      _ppSaveState(ppState);
      _ppApplyWidthsCSS();
    };
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startX = e.clientX;
      startW = th.offsetWidth;
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });
}

function buildPaperPositionHeader() {
  const row = document.getElementById('paperPositionHeaderRow');
  row.innerHTML = PP_COLUMNS.map(c =>
    `<th class="${c.divider ? 'col-divider-start' : ''}">` +
      `<span class="col-th-label">${c.label}</span>` +
      `<span class="col-resize-handle"></span>` +
    `</th>`
  ).join('');
  _ppUpdateGroupSpans();
  _ppWireResize();
}

function buildPaperPositionColsPanel() {
  const panel = document.getElementById('paperPositionColsPanel');
  panel.innerHTML = PP_COLUMNS.map(c =>
    `<label class="col-picker-item">` +
      `<input type="checkbox" data-col-key="${c.key}" ${ppState.hidden[c.key] ? '' : 'checked'}> ${c.label}` +
    `</label>`
  ).join('');
  panel.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener('change', () => {
      const key = cb.dataset.colKey;
      if (cb.checked) delete ppState.hidden[key]; else ppState.hidden[key] = true;
      _ppSaveState(ppState);
      _ppApplyVisibilityCSS();
      _ppUpdateGroupSpans();
    });
  });

  const btn = document.getElementById('paperPositionColsBtn');
  btn.addEventListener('click', (e) => { e.stopPropagation(); panel.hidden = !panel.hidden; });
  document.addEventListener('click', (e) => {
    if (!panel.hidden && !panel.contains(e.target) && e.target !== btn) panel.hidden = true;
  });
}

function initPaperPositionColumns() {
  buildPaperPositionHeader();
  buildPaperPositionColsPanel();
  _ppApplyVisibilityCSS();
  _ppApplyWidthsCSS();
}

function renderPaperPositionCityFilter() {
  const el = document.getElementById('paperPositionCityFilter');
  for (const c of state.cities) {
    const opt = document.createElement('option');
    opt.value = c.station_code;
    opt.textContent = c.city;
    el.appendChild(opt);
  }
  el.addEventListener('change', refreshPaperPositions);
}

async function refreshPaperPositions() {
  const stationFilter = document.getElementById('paperPositionCityFilter').value;
  const url = stationFilter
    ? `/api/position-exits?station=${stationFilter}`
    : '/api/position-exits';
  let data;
  try {
    data = await fetchJSON(url);
  } catch (e) {
    console.error(e);
    return;
  }
  renderPaperPositionTable(data.positions);
}

function resultClass(result) {
  if (result === 'WIN') return 'row-win';
  if (result === 'LOSS') return 'row-loss';
  return '';
}

// PST, always -- not the traded city's own local time (that's what the
// market-times panel is for). Operator's own reference timezone for "when
// did this trade actually fire," same for every row regardless of station.
function _fmtPST(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    timeZone: 'America/Los_Angeles', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true,
  });
}

// Gross P&L - Gross Fee = Net P&L, scoped to whatever the city dropdown
// currently shows (positions is already filtered server-side by station
// before this runs). Settled trades only (result !== 'OPEN') -- an open
// position's fee was charged at entry but its P&L isn't known yet, so
// including one without the other would make "Net" inconsistent. Matches
// the same scoping used for the operator's portfolio P&L reconciliation
// earlier tonight (scored_at IS NOT NULL).
function renderPaperPositionTotals(positions) {
  const el = document.getElementById('paperPositionTotals');
  if (!el) return;
  const settled = positions.filter(p => p.result !== 'OPEN');
  if (!settled.length) {
    el.innerHTML = '<span class="hint">No settled trades yet for this filter.</span>';
    return;
  }
  const grossPnl = settled.reduce((sum, p) => sum + (p.pnl_usd || 0), 0);
  const grossFee = settled.reduce((sum, p) => sum + (p.fee_usd || 0), 0);
  const netPnl = grossPnl - grossFee;
  const cls = v => (v >= 0 ? 'row-win' : 'row-loss');
  const openCount = positions.length - settled.length;
  el.innerHTML =
    `<span class="${cls(grossPnl)}">Gross P&amp;L: $${grossPnl.toFixed(2)}</span>` +
    ` &minus; Gross Fee: $${grossFee.toFixed(2)}` +
    ` = <strong class="${cls(netPnl)}">Net P&amp;L: $${netPnl.toFixed(2)}</strong>` +
    ` <span class="hint">(${settled.length} settled${openCount ? `, ${openCount} open` : ''})</span>`;
}

function renderPaperPositionTable(positions) {
  renderPaperPositionTotals(positions);
  const tbody = document.getElementById('paperPositionBody');
  if (!positions.length) {
    tbody.innerHTML = '<tr><td colspan="20" class="hint">No paper trades placed yet.</td></tr>';
    return;
  }
  tbody.innerHTML = positions.map(p => {
    const pnlClass = p.pnl_usd == null ? '' : (p.pnl_usd >= 0 ? 'row-win' : 'row-loss');
    const roiClass = p.roi_pct == null ? '' : (p.roi_pct >= 0 ? 'row-win' : 'row-loss');
    const sigmaTxt = p.entry_sigma_distance == null ? '—'
      : (p.entry_sigma_distance >= 0 ? '+' : '') + p.entry_sigma_distance.toFixed(1) + 'σ';
    return `<tr>
      <td>${p.station_code}</td>
      <td>${_fmtPST(p.entry_captured_at)}</td>
      <td class="ticker-cell">${p.contract_ticker}</td>
      <td class="bucket-cell">${bucketRangeLabel(p)}</td>
      <td>${p.actual_tmax_f == null ? '—' : p.actual_tmax_f.toFixed(1) + '°'}</td>
      <td class="col-divider-start">${p.predicted_tmax_f == null ? '—' : p.predicted_tmax_f.toFixed(1) + '°'}</td>
      <td>${p.nws_maxt_f == null ? '—' : p.nws_maxt_f.toFixed(1) + '°'}</td>
      <td>${p.gfs_maxt_f == null ? '—' : p.gfs_maxt_f.toFixed(1) + '°'}</td>
      <td>${p.model_maxt_f == null ? '—' : p.model_maxt_f.toFixed(1) + '°'}</td>
      <td>${p.entry_delta_f == null ? '—' : p.entry_delta_f.toFixed(1) + '°'}</td>
      <td>${p.entry_edge_cents == null ? '—' : p.entry_edge_cents.toFixed(1) + '¢'}</td>
      <td class="col-divider-start">${p.side}</td>
      <td>${p.investment_usd == null ? '—' : '$' + p.investment_usd.toFixed(2)}</td>
      <td>${p.entry_price_cents == null ? '—' : p.entry_price_cents.toFixed(1) + '¢'}</td>
      <td>${p.contracts == null ? '—' : p.contracts}</td>
      <td>${sigmaTxt}</td>
      <td>${'$' + p.fee_usd.toFixed(2)}</td>
      <td class="${resultClass(p.result)}">${p.result}</td>
      <td class="${pnlClass}">${p.pnl_usd == null ? '—' : '$' + p.pnl_usd.toFixed(2)}</td>
      <td class="${roiClass}">${p.roi_pct == null ? '—' : (p.roi_pct >= 0 ? '+' : '') + p.roi_pct.toFixed(1) + '%'}</td>
    </tr>`;
  }).join('');
}

init();
