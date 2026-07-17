// WeatherBHN Trading Dashboard — frontend logic.
// Read-only planning/simulation tool. Never calls any order-placement API.

const PRICE_REFRESH_MS = 20000;   // live Kalshi price/volume/chance
const MODEL_REFRESH_MS = 5 * 60000; // mu/sigma — matches orchestrator cadence

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
  document.getElementById('journalForm').addEventListener('submit', onJournalSubmit);

  await refreshAll(true);
  await refreshJournal();

  setInterval(() => refreshLadder(false), PRICE_REFRESH_MS);
  setInterval(tickCountdown, 1000);
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
      div.className = 'sigma-marker' + (m.n === 0 ? ' zero' : '');
      div.innerHTML = `<div class="n">${m.n > 0 ? '+' : ''}${m.n}&sigma;</div><div class="temp">${m.temp_f}&deg;F</div>`;
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

  // Model probability per bucket: Gaussian mass between [floor,cap] given
  // (mu, sigma) — same distribution CP4 uses for center buckets. Tail
  // (Student-t) buckets are not re-derived here (display-only chart); the
  // Gaussian approximation is clearly a different curve than CP4's live
  // number and is for visual context, not a trading input.
  const modelProb = data.buckets.map(b => {
    if (data.mu == null || data.sigma == null) return null;
    const lo = b.bucket_floor == null ? -Infinity : b.bucket_floor;
    const hi = b.bucket_cap == null ? Infinity : b.bucket_cap;
    return round2((normalCdf(hi, data.mu, data.sigma) - normalCdf(lo, data.mu, data.sigma)) * 100);
  });

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
          label: 'Model probability % (Gaussian, display-only)',
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
        y: { ticks: { color: '#8891a3' }, grid: { color: '#232937' }, beginAtZero: true },
      },
      plugins: { legend: { labels: { color: '#e6e9ef' } } },
    },
  });
}

function normalCdf(x, mu, sigma) {
  if (!isFinite(x)) return x > 0 ? 1 : 0;
  return 0.5 * (1 + erf((x - mu) / (sigma * Math.SQRT2)));
}
function erf(x) {
  // Abramowitz-Stegun 7.1.26 approximation — adequate for a display chart.
  const sign = x < 0 ? -1 : 1; x = Math.abs(x);
  const a1=0.254829592,a2=-0.284496736,a3=1.421413741,a4=-1.453152027,a5=1.061405429,p=0.3275911;
  const t = 1/(1+p*x);
  const y = 1-(((((a5*t+a4)*t)+a3)*t+a2)*t+a1)*t*Math.exp(-x*x);
  return sign*y;
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

function renderLadderTable(data) {
  const tbody = document.getElementById('ladderBody');
  tbody.innerHTML = '';
  for (const b of data.buckets) {
    const inputs = state.rowInputs[b.bucket_label];
    const tr = document.createElement('tr');
    tr.dataset.bucket = b.bucket_label;

    const sigmaChips = b.sigma_markers_in_bucket
      .map(n => `<span class="sigma-chip">${n > 0 ? '+' : ''}${n}&sigma;</span>`).join('');

    tr.innerHTML = `
      <td><input type="checkbox" class="win-checkbox" ${state.winningBucket === b.bucket_label ? 'checked' : ''}></td>
      <td class="col-bucket"><span class="bucket-range">${bucketRangeLabel(b)}</span></td>
      <td class="col-sigma">${sigmaChips || '&nbsp;'}</td>
      <td class="col-chance">${b.chance_pct != null ? b.chance_pct + '%' : '—'}</td>
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

function buildCalcCell(cell, bucket, side, inputs) {
  const priceKey = side === 'no' ? 'noPrice' : 'yesPrice';
  const investKey = side === 'no' ? 'noInvestment' : 'yesInvestment';
  const result = calcSide(inputs[priceKey], inputs[investKey]);

  cell.innerHTML = `
    <div class="calc-grid">
      <label>Price &cent;</label><label>Invest $</label>
      <input type="number" class="price-input" step="0.5" min="0.5" max="99.5" value="${inputs[priceKey]}">
      <input type="number" class="invest-input" step="1" min="0" value="${inputs[investKey]}">
      <div class="calc-summary"><span>Contracts</span><span>${result.contracts}</span></div>
      <div class="calc-summary"><span>Fee (maker)</span><span>$${result.fee.toFixed(2)}</span></div>
      <div class="calc-summary"><span>Payout if win</span><span>$${result.payoutIfWin.toFixed(2)}</span></div>
      <div class="calc-summary"><span>Profit if win</span><span class="${result.profitIfWin >= 0 ? 'profit-pos' : 'profit-neg'}">$${result.profitIfWin.toFixed(2)}</span></div>
      ${bucket.is_liquid === false ? '<div class="calc-summary illiquid-flag" style="grid-column:span 2">volume ≤ 100 — illiquid</div>' : ''}
    </div>`;

  cell.querySelector('.price-input').addEventListener('input', (e) => {
    inputs[priceKey] = parseFloat(e.target.value) || 0;
    buildCalcCell(cell, bucket, side, inputs);
    renderSimulation();
  });
  cell.querySelector('.invest-input').addEventListener('input', (e) => {
    inputs[investKey] = parseFloat(e.target.value) || 0;
    buildCalcCell(cell, bucket, side, inputs);
    renderSimulation();
  });
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
  let grossTotal = 0, netTotal = 0, totalInvested = 0;
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
      if (pnl != null) {
        grossTotal += sideWins ? result.payoutIfWin : 0;
        netTotal += pnl;
      }

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${bucketRangeLabel(b)}</td>
        <td>${side.toUpperCase()}</td>
        <td>$${result.totalCost.toFixed(2)}</td>
        <td>${state.winningBucket == null ? 'pending' : (sideWins ? '<span class="row-win">WIN</span>' : '<span class="row-loss">LOSS</span>')}</td>
        <td class="${pnl == null ? '' : (pnl >= 0 ? 'row-win' : 'row-loss')}">${pnl == null ? '—' : '$' + pnl.toFixed(2)}</td>`;
      tbody.appendChild(tr);
    }
  }

  if (!anyRows) {
    tbody.innerHTML = '<tr><td colspan="5" class="hint">No investment entered on any row yet.</td></tr>';
  }

  totalsEl.innerHTML = state.winningBucket == null
    ? `<div class="hint">Select a winning bucket above to see P&amp;L.</div>`
    : `<div><span class="total-label">Total invested</span><span class="total-value">$${totalInvested.toFixed(2)}</span></div>
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

init();
