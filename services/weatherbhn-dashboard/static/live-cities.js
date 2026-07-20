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

// Big rolling-72h single-city chart, added 2026-07-20 -- separate Chart.js
// instance/state from the small per-city panels above (charts{}), own
// dropdown-selected city, but polled on the same shared 30s tick so
// everything on the page refreshes together.
const BIG_WINDOW_HOURS = 72;
let bigChart = null;
let bigChartStation = null;  // which station bigChart's current instance was built for
let bigSelectedStation = null;

// chartjs-plugin-zoom (loaded via CDN script tag in live-cities.html) --
// drag to zoom into an x/y range, wheel/pinch to zoom, shift+drag to pan,
// double-click or the "Reset zoom" button to widen back out. Added
// 2026-07-20 per operator request ("lock and drag to narrow and widen the
// x and y axis"). "Lock" is handled by NOT destroying/recreating the chart
// on every 30s poll (see renderBigChart below) -- only updating its data
// in place, so whatever zoom/pan the user has applied survives each
// refresh instead of snapping back to full-range every 30s.
if (typeof Chart !== 'undefined' && typeof ChartZoom !== 'undefined') {
  Chart.register(ChartZoom);
}
const ZOOM_PLUGIN_OPTS = {
  pan: { enabled: true, mode: 'xy', modifierKey: 'shift' },
  zoom: { drag: { enabled: true }, wheel: { enabled: true }, pinch: { enabled: true }, mode: 'xy' },
};

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

// Includes the date (not just HH:MM like the small panels' localHHMM) --
// a 72h window spans multiple calendar days, so a bare time would be
// ambiguous about which day a point falls on.
function localDayTime(iso, tz) {
  return new Date(iso).toLocaleString('en-US', {
    timeZone: tz, month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', hour12: false,
  });
}

function renderBigChart(station, data) {
  const ctx = document.getElementById('bigTrajChart');
  if (!ctx) return;
  const tz = (cityMeta[station] || {}).timezone;

  const emptyId = 'bigTrajEmptyMsg';
  document.getElementById(emptyId)?.remove();
  if (!data.observations.length) {
    if (bigChart) { bigChart.destroy(); bigChart = null; bigChartStation = null; }
    const msg = document.createElement('div');
    msg.id = emptyId;
    msg.className = 'hint';
    msg.style.padding = '60px 0';
    msg.style.textAlign = 'center';
    msg.textContent = `No live ASOS observations in the last ${BIG_WINDOW_HOURS}h for this station.`;
    ctx.parentElement.appendChild(msg);
    return;
  }

  const labels = data.observations.map(o => localDayTime(o.observed_at, tz));
  const liveTemp = data.observations.map(o => o.air_temp_f);
  const flat = (v) => data.observations.map(() => v);
  // null-through, not 0 -- a gap (pre-deploy history, or a station with no
  // reading this cycle) should break the line, not draw a false value.
  const nullable = (key) => data.observations.map(o => (o[key] == null ? null : o[key]));

  // Synoptic reports cloud_layer_1_condition as a lowercase description,
  // NOT a METAR code (confirmed against live data 2026-07-20: 'clear',
  // 'scattered', 'thin scattered' seen so far) -- mapped to the METAR
  // sky-cover band it corresponds to, midpoint %. Falls back to matching
  // on a substring since Synoptic's exact wording for broken/overcast/few
  // hasn't been observed yet locally; unrecognized text -> null (skip),
  // never a guessed number.
  const CLOUD_PCT_BANDS = [
    [/clear|sky clear/, 0],
    [/few/, 12],
    [/thin scattered|scattered/, 37],
    [/broken/, 69],
    [/overcast/, 100],
  ];
  const cloudPctFor = (cond) => {
    if (!cond) return null;
    const lc = cond.toLowerCase();
    const hit = CLOUD_PCT_BANDS.find(([re]) => re.test(lc));
    return hit ? hit[1] : null;
  };
  const cloudPct = data.observations.map(o => cloudPctFor(o.cloud_layer_1_condition));

  const datasets = [
    {
      type: 'line', label: 'Live temp (ASOS)', data: liveTemp, yAxisID: 'y',
      borderColor: '#4d8dff', backgroundColor: '#4d8dff',
      tension: 0.15, pointRadius: 0, borderWidth: 2,
    },
    {
      type: 'line', label: 'Dew point', data: nullable('dew_point_f'), yAxisID: 'y',
      borderColor: '#5ad1c9', backgroundColor: '#5ad1c9', spanGaps: false,
      tension: 0.15, pointRadius: 0, borderWidth: 1.5,
    },
    {
      type: 'line', label: 'Wind speed (mph)', data: nullable('wind_speed_mph'), yAxisID: 'yWind',
      borderColor: '#f0a63a', backgroundColor: '#f0a63a', spanGaps: false,
      tension: 0.15, pointRadius: 0, borderWidth: 1.5,
    },
    {
      type: 'line', label: 'Wind direction (°)', data: nullable('wind_direction_deg'), yAxisID: 'yDir',
      borderColor: '#a97cd6', backgroundColor: '#a97cd6', spanGaps: false,
      tension: 0, pointRadius: 0, borderWidth: 1, borderDash: [1, 2],
    },
    {
      // Segmented/highlighted look per operator request -- filled area
      // (not a sharp line) so each reported band (30%, 40%, etc.) reads as
      // a light shaded region rather than competing visually with the
      // actual temperature/wind lines.
      type: 'line', label: 'Cloud cover (%, METAR band)', data: cloudPct, yAxisID: 'yCloud',
      borderColor: 'rgba(150, 160, 175, 0.5)', backgroundColor: 'rgba(150, 160, 175, 0.18)',
      fill: true, stepped: true, spanGaps: false, tension: 0, pointRadius: 0, borderWidth: 1,
    },
  ];
  // Reference lines (today's model mu/sigma, NWS/GFS forecast highs) are
  // inherently per-contract/per-target_date values -- they're plotted flat
  // across the whole 72h window as a reference backdrop for TODAY's
  // contract specifically, not a separate value per historical day.
  if (data.mu != null) {
    datasets.push({
      type: 'line', label: `Model μ, today (${data.mu_source})`, data: flat(data.mu),
      borderColor: '#c9a94d', borderDash: [6, 4], pointRadius: 0, borderWidth: 1.5,
    });
  }
  if (data.mu_plus_1sigma != null) {
    datasets.push({
      type: 'line', label: 'μ +1σ, today', data: flat(data.mu_plus_1sigma),
      borderColor: 'rgba(201, 169, 77, 0.45)', borderDash: [2, 3], pointRadius: 0, borderWidth: 1,
    });
    datasets.push({
      type: 'line', label: 'μ −1σ, today', data: flat(data.mu_minus_1sigma),
      borderColor: 'rgba(201, 169, 77, 0.45)', borderDash: [2, 3], pointRadius: 0, borderWidth: 1,
    });
  }
  if (data.nws_forecast_tmax_f != null) {
    datasets.push({
      type: 'line', label: 'NWS forecast high, today', data: flat(data.nws_forecast_tmax_f),
      borderColor: '#17c964', borderDash: [4, 4], pointRadius: 0, borderWidth: 1.5,
    });
  }
  if (data.gfs_forecast_tmax_f != null) {
    datasets.push({
      type: 'line', label: 'GFS forecast high, today', data: flat(data.gfs_forecast_tmax_f),
      borderColor: '#e0578f', borderDash: [4, 4], pointRadius: 0, borderWidth: 1.5,
    });
  }

  // Same station as the existing chart instance -- update data in place
  // (chart.update()) instead of destroying/recreating, so any zoom/pan the
  // user has applied stays exactly where it is across this 30s refresh.
  // Only a city switch (or first render) tears down and rebuilds.
  if (bigChart && bigChartStation === station) {
    bigChart.data.labels = labels;
    bigChart.data.datasets.forEach((ds, i) => { if (datasets[i]) ds.data = datasets[i].data; });
    // Dataset COUNT can change run to run (e.g. mu/sigma go from unresolved
    // to resolved) -- if the shape changed, fall through to a full rebuild
    // rather than silently dropping/misaligning datasets.
    if (bigChart.data.datasets.length !== datasets.length) {
      bigChart.destroy();
      bigChart = null;
      bigChartStation = null;
    } else {
      bigChart.update('none');
      return;
    }
  } else if (bigChart) {
    bigChart.destroy();
    bigChart = null;
    bigChartStation = null;
  }

  bigChartStation = station;
  bigChart = new Chart(ctx, {
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: { ticks: { color: '#8891a3', maxTicksLimit: 12 }, grid: { color: '#232937' } },
        y: {
          position: 'left',
          ticks: { color: '#8891a3' }, grid: { color: '#232937' },
          title: { display: true, text: '°F (temp / dew point)', color: '#8891a3' },
        },
        yWind: {
          position: 'right', min: 0,
          ticks: { color: '#f0a63a' }, grid: { drawOnChartArea: false },
          title: { display: true, text: 'mph (wind)', color: '#f0a63a' },
        },
        yDir: {
          position: 'right', min: 0, max: 360,
          ticks: { color: '#a97cd6', stepSize: 90 }, grid: { drawOnChartArea: false },
          title: { display: true, text: '° (wind dir)', color: '#a97cd6' },
        },
        yCloud: {
          position: 'right', min: 0, max: 100,
          ticks: { color: '#8891a3' }, grid: { drawOnChartArea: false },
          title: { display: true, text: '% cloud cover', color: '#8891a3' },
        },
      },
      plugins: {
        legend: { labels: { color: '#e6e9ef', boxWidth: 12, font: { size: 11 } } },
        // mode: 'x' only, not 'xy' -- with 4 differently-scaled y-axes now,
        // dragging one zoom gesture across all of them at once (the
        // single-axis chart's original 'xy' mode) doesn't make sense; each
        // y-axis auto-fits to whatever time range is currently zoomed in.
        zoom: {
          pan: { ...ZOOM_PLUGIN_OPTS.pan, mode: 'x' },
          zoom: { ...ZOOM_PLUGIN_OPTS.zoom, mode: 'x' },
        },
      },
    },
  });
}

function renderBigBadges(station, data) {
  const el = document.getElementById('bigTrajBadges');
  if (!el) return;
  el.innerHTML = '';
  const chip = (label, value) => {
    const span = document.createElement('span');
    span.className = 'badge live';
    span.textContent = value == null ? `${label}: —` : `${label}: ${value}°F`;
    return span;
  };
  if (data.running_high_so_far != null) el.appendChild(chip("High so far (today)", data.running_high_so_far));
  el.appendChild(chip('NWS (today)', data.nws_forecast_tmax_f));
  el.appendChild(chip('GFS (today)', data.gfs_forecast_tmax_f));
  el.appendChild(chip('Model μ (today)', data.mu));
}

async function refreshBigChart() {
  if (!bigSelectedStation) return;
  try {
    const data = await fetchJSON(
      `/api/live-trajectory?station=${bigSelectedStation}&target_date=${todayIso()}&window_hours=${BIG_WINDOW_HOURS}`
    );
    renderBigChart(bigSelectedStation, data);
    renderBigBadges(bigSelectedStation, data);
  } catch (e) {
    console.error('big trajectory fetch failed:', e);
    const el = document.getElementById('bigTrajBadges');
    if (el) el.innerHTML = '<span class="badge none">refresh failed</span>';
  }
}

function initBigCitySelect() {
  const sel = document.getElementById('bigCitySelect');
  if (!sel) return;
  sel.innerHTML = '';
  for (const station of CITY_DISPLAY_ORDER) {
    if (!cityMeta[station]) continue;
    const opt = document.createElement('option');
    opt.value = station;
    opt.textContent = `${cityMeta[station].city} (${station})`;
    sel.appendChild(opt);
  }
  bigSelectedStation = CITY_DISPLAY_ORDER.find(s => cityMeta[s]) || null;
  if (bigSelectedStation) sel.value = bigSelectedStation;
  sel.addEventListener('change', () => {
    bigSelectedStation = sel.value;
    refreshBigChart();
  });

  const resetBtn = document.getElementById('bigTrajResetZoom');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => { bigChart?.resetZoom(); });
  }

  // Native Fullscreen API on the panel element (not just the canvas) so the
  // dropdown/reset/badges stay visible and usable in fullscreen too. Chart.js
  // is responsive, so it auto-resizes to the panel's new fullscreen
  // dimensions -- just needs a resize() nudge since the fullscreen
  // transition doesn't always fire a window resize event reliably.
  const fsBtn = document.getElementById('bigTrajFullscreen');
  const panel = document.querySelector('.big-traj-panel');
  if (fsBtn && panel) {
    fsBtn.addEventListener('click', () => {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        panel.requestFullscreen?.();
      }
    });
    document.addEventListener('fullscreenchange', () => {
      fsBtn.textContent = document.fullscreenElement ? '⛶ Exit fullscreen' : '⛶ Fullscreen';
      setTimeout(() => bigChart?.resize(), 50);
    });
  }
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
  await Promise.all([
    ...CITY_DISPLAY_ORDER.filter(s => cityMeta[s]).map(refreshCity),
    refreshBigChart(),
  ]);
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

  initBigCitySelect();
  await refreshAllCities();
  setInterval(refreshAllCities, REFRESH_MS);
  setInterval(tick, 1000);
}

init();
