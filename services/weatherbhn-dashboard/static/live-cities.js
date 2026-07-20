// WeatherBHN — Live Trajectory (All Cities). Standalone page, deliberately
// NOT sharing app.js/its `state` object (that object is built around a
// single selected city/date tab) -- this page shows all 3 tradeable cities
// at once, each with its own independent Chart.js instance. Chart-building
// logic here is a parameterized duplicate of app.js's
// renderTrajectoryChart()/renderTrajectoryBadges(), same intentional-
// duplication pattern app/model_math.py already uses in this codebase
// ("kept as a duplicate... since this service deploys independently" --
// verify these stay in sync if the source formula/shape ever changes).
//
// Read-only, same as every other page in this service: only ever calls
// GET /api/config and GET /api/live-trajectory.

const REFRESH_MS = 30000;

// Operator-requested display order (2026-07-20) -- Los Angeles, Denver,
// Miami -- NOT app/main.py's CITIES list order (KDEN first). Panels for
// any station not present/enabled in /api/config are hidden rather than
// left broken, so this page degrades gracefully if a city's `enabled` flag
// ever changes.
const CITY_DISPLAY_ORDER = ['KLAX', 'KDEN', 'KMIA'];

const charts = {};       // station_code -> Chart.js instance
let cityMeta = {};        // station_code -> {city, timezone}
let countdownSec = REFRESH_MS / 1000;

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function localIso(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}
function todayIso() { return localIso(new Date()); }

function localHHMM(iso, tz) {
  return new Date(iso).toLocaleString('en-US', {
    timeZone: tz, hour: 'numeric', minute: '2-digit', hour12: false,
  });
}

function renderChart(station, data) {
  const ctx = document.getElementById(`chart-${station}`);
  if (!ctx) return;
  const tz = (cityMeta[station] || {}).timezone;

  const emptyId = `empty-${station}`;
  document.getElementById(emptyId)?.remove();
  if (!data.is_today || data.observations.length === 0) {
    if (charts[station]) { charts[station].destroy(); charts[station] = null; }
    const msg = document.createElement('div');
    msg.id = emptyId;
    msg.className = 'hint';
    msg.style.padding = '40px 0';
    msg.style.textAlign = 'center';
    msg.textContent = 'No live ASOS observations yet today for this station.';
    ctx.parentElement.appendChild(msg);
    return;
  }

  const labels = data.observations.map(o => localHHMM(o.observed_at, tz));
  const liveTemp = data.observations.map(o => o.air_temp_f);
  const flat = (v) => data.observations.map(() => v);

  const datasets = [
    {
      type: 'line', label: 'Live temp (ASOS)', data: liveTemp,
      borderColor: '#4d8dff', backgroundColor: '#4d8dff',
      tension: 0.2, pointRadius: 0, borderWidth: 2,
    },
  ];
  if (data.mu != null) {
    datasets.push({
      type: 'line', label: `Model μ (${data.mu_source})`, data: flat(data.mu),
      borderColor: '#c9a94d', borderDash: [6, 4], pointRadius: 0, borderWidth: 1.5,
    });
  }
  if (data.mu_plus_1sigma != null) {
    datasets.push({
      type: 'line', label: 'μ +1σ', data: flat(data.mu_plus_1sigma),
      borderColor: 'rgba(201, 169, 77, 0.45)', borderDash: [2, 3], pointRadius: 0, borderWidth: 1,
    });
    datasets.push({
      type: 'line', label: 'μ −1σ', data: flat(data.mu_minus_1sigma),
      borderColor: 'rgba(201, 169, 77, 0.45)', borderDash: [2, 3], pointRadius: 0, borderWidth: 1,
    });
  }
  if (data.nws_forecast_tmax_f != null) {
    datasets.push({
      type: 'line', label: 'NWS forecast high', data: flat(data.nws_forecast_tmax_f),
      borderColor: '#17c964', borderDash: [4, 4], pointRadius: 0, borderWidth: 1.5,
    });
  }
  if (data.gfs_forecast_tmax_f != null) {
    datasets.push({
      type: 'line', label: 'GFS forecast high', data: flat(data.gfs_forecast_tmax_f),
      borderColor: '#e0578f', borderDash: [4, 4], pointRadius: 0, borderWidth: 1.5,
    });
  }

  if (charts[station]) charts[station].destroy();
  charts[station] = new Chart(ctx, {
    data: { labels, datasets },
    options: {
      responsive: true,
      animation: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: { ticks: { color: '#8891a3', maxTicksLimit: 8 }, grid: { color: '#232937' } },
        y: {
          ticks: { color: '#8891a3' }, grid: { color: '#232937' },
          title: { display: true, text: '°F', color: '#8891a3' },
        },
      },
      plugins: { legend: { labels: { color: '#e6e9ef', boxWidth: 12, font: { size: 10 } } } },
    },
  });
}

function renderBadges(station, data) {
  const el = document.getElementById(`badges-${station}`);
  if (!el) return;
  el.innerHTML = '';
  const chip = (label, value) => {
    const span = document.createElement('span');
    span.className = 'badge live';
    span.textContent = value == null ? `${label}: —` : `${label}: ${value}°F`;
    return span;
  };
  if (data.running_high_so_far != null) el.appendChild(chip('High so far', data.running_high_so_far));
  el.appendChild(chip('NWS', data.nws_forecast_tmax_f));
  el.appendChild(chip('GFS', data.gfs_forecast_tmax_f));
  el.appendChild(chip('Model μ', data.mu));
}

async function refreshCity(station) {
  try {
    const data = await fetchJSON(`/api/live-trajectory?station=${station}&target_date=${todayIso()}`);
    renderChart(station, data);
    renderBadges(station, data);
  } catch (e) {
    console.error(`live-trajectory fetch failed for ${station}:`, e);
    const el = document.getElementById(`badges-${station}`);
    if (el) el.innerHTML = '<span class="badge none">refresh failed</span>';
  }
}

async function refreshAllCities() {
  const indicator = document.getElementById('refreshIndicator');
  await Promise.all(CITY_DISPLAY_ORDER.filter(s => cityMeta[s]).map(refreshCity));
  indicator.classList.remove('stale');
  countdownSec = REFRESH_MS / 1000;
}

function tick() {
  countdownSec = Math.max(0, countdownSec - 1);
  const el = document.getElementById('liveCountdown');
  if (el) el.textContent = `(${countdownSec}s)`;
}

async function init() {
  const cfg = await fetchJSON('/api/config');
  cityMeta = {};
  for (const c of cfg.cities) {
    if (c.enabled) cityMeta[c.station_code] = { city: c.city, timezone: c.timezone };
  }

  // Hide/relabel panels for any station not actually enabled, and skip it
  // entirely in the poll loop -- graceful degradation if CITIES ever
  // changes in app/main.py without this page being updated in lockstep.
  for (const station of CITY_DISPLAY_ORDER) {
    const panel = document.getElementById(`panel-${station}`);
    if (!panel) continue;
    if (!cityMeta[station]) {
      panel.style.display = 'none';
      continue;
    }
    const titleEl = document.getElementById(`title-${station}`);
    if (titleEl) titleEl.textContent = `${cityMeta[station].city} (${station})`;
  }

  await refreshAllCities();
  setInterval(refreshAllCities, REFRESH_MS);
  setInterval(tick, 1000);
}

init();
