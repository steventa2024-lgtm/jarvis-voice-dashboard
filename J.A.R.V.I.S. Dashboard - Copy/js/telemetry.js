/* ==========================================================================
   telemetry.js — clock, power, network, load, weather
   Every reading is feature-detected. A capability the browser lacks shows an
   explicit "n/a" rather than a blank widget or a console error.
   ========================================================================== */

(function (J) {

  const started = Date.now();
  let turns = 0, searches = 0;

  /* ================================================================== clock */

  function tickClock() {
    const now = new Date();
    const c = J.$('#clock'), d = J.$('#date'), u = J.$('#uptimeVal');
    if (c) c.textContent = now.toLocaleTimeString('en-GB', { hour12: false });
    if (d) d.textContent = now.toLocaleDateString(undefined, {
      weekday: 'short', day: '2-digit', month: 'short', year: 'numeric'
    });
    if (u) {
      const s = Math.floor((Date.now() - started) / 1000);
      u.textContent = [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60]
        .map(n => String(n).padStart(2, '0')).join(':');
    }
  }

  /* ================================================================ battery */

  const gauge = J.$('#batteryGauge');
  let batteryLevel = null, batteryCharging = false;

  function drawGauge(pct, charging) {
    if (!gauge) return;
    const box = J.fitCanvas(gauge);
    const g = gauge.getContext('2d');
    g.setTransform(box.s, 0, 0, box.s, 0, 0);
    g.clearRect(0, 0, box.w, box.h);

    if (box.w < 20 || box.h < 20) return;    // panel hidden — nothing to draw
    const cx = box.w / 2, cy = box.h / 2, r = Math.min(box.w, box.h) / 2 - 7;
    const START = Math.PI * 0.75, SPAN = Math.PI * 1.5;

    g.lineCap = 'round';

    g.beginPath();
    g.arc(cx, cy, r, START, START + SPAN);
    g.strokeStyle = 'rgba(255,255,255,0.08)';
    g.lineWidth = 5;
    g.stroke();

    if (pct === null) return;

    const colour = charging ? '#3ddc97' : pct <= 15 ? '#ff5f6b' : pct <= 35 ? '#ffb454' : J.settings.accent;
    g.beginPath();
    g.arc(cx, cy, r, START, START + SPAN * (pct / 100));
    g.strokeStyle = colour;
    g.lineWidth = 5;
    g.shadowBlur = 12;
    g.shadowColor = colour;
    g.stroke();
  }

  function paintBattery() {
    const pctEl = J.$('#batteryPct'), stEl = J.$('#batteryState'), etaEl = J.$('#batteryEta');
    if (batteryLevel === null) {
      if (pctEl) pctEl.innerHTML = 'n/a';
      if (stEl)  stEl.textContent = 'not exposed';
      if (etaEl) etaEl.textContent = 'browser has no Battery API';
      drawGauge(null, false);
      return;
    }
    const pct = Math.round(batteryLevel * 100);
    if (pctEl) pctEl.innerHTML = pct + '<small>%</small>';
    if (stEl)  stEl.textContent = batteryCharging ? 'charging' : pct <= 15 ? 'critical' : 'discharging';
    drawGauge(pct, batteryCharging);
  }

  async function initBattery() {
    if (!('getBattery' in navigator)) { paintBattery(); return; }
    try {
      const b = await navigator.getBattery();
      const sync = () => {
        batteryLevel = b.level;
        batteryCharging = b.charging;
        const etaEl = J.$('#batteryEta');
        const secs = b.charging ? b.chargingTime : b.dischargingTime;
        if (etaEl) {
          if (secs && isFinite(secs) && secs > 0) {
            const h = Math.floor(secs / 3600), m = Math.round((secs % 3600) / 60);
            etaEl.textContent = (b.charging ? 'full in ' : 'remaining ') + (h ? h + 'h ' : '') + m + 'm';
          } else etaEl.textContent = ' ';
        }
        paintBattery();
      };
      ['levelchange', 'chargingchange', 'chargingtimechange', 'dischargingtimechange']
        .forEach(ev => b.addEventListener(ev, sync));
      sync();
      J.log('Power cell online', 'ok', 'sys');
    } catch (e) {
      paintBattery();
    }
  }

  /* ================================================================ network */

  const netSpark = J.$('#netSpark');
  const netHist = [];

  function paintNetwork() {
    const on = navigator.onLine;
    const st = J.$('#netStatus');
    if (st) { st.textContent = on ? 'online' : 'offline'; st.className = 'pill ' + (on ? 'ok' : 'crit'); }

    const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    const type = J.$('#netType'), down = J.$('#netDown'), rtt = J.$('#netRtt');
    if (c) {
      if (type) type.textContent = c.effectiveType || '--';
      if (down) down.textContent = c.downlink ? c.downlink.toFixed(1) + ' Mb/s' : '--';
      if (rtt)  rtt.textContent  = c.rtt ? c.rtt + ' ms' : '--';
      netHist.push(J.clamp((c.downlink || 0) / 12, 0.02, 1));
    } else {
      if (type) type.textContent = 'n/a';
      if (down) down.textContent = 'n/a';
      if (rtt)  rtt.textContent  = 'n/a';
      netHist.push(on ? 0.55 : 0.02);
    }
    while (netHist.length > 60) netHist.shift();
    sparkline(netSpark, netHist, navigator.onLine ? J.settings.accent : '#ff5f6b');
  }

  /* =============================================================== sparkline */

  function sparkline(canvas, data, colour) {
    if (!canvas || !data.length) return;
    const box = J.fitCanvas(canvas);
    const g = canvas.getContext('2d');
    g.setTransform(box.s, 0, 0, box.s, 0, 0);
    g.clearRect(0, 0, box.w, box.h);

    const pad = 3, w = box.w, h = box.h - pad * 2;
    const step = w / Math.max(1, data.length - 1);
    const y = v => pad + h - J.clamp(v, 0, 1) * h;

    g.beginPath();
    data.forEach((v, i) => (i ? g.lineTo(i * step, y(v)) : g.moveTo(0, y(v))));

    const line = new Path2D();
    data.forEach((v, i) => (i ? line.lineTo(i * step, y(v)) : line.moveTo(0, y(v))));

    g.lineTo(w, box.h); g.lineTo(0, box.h); g.closePath();
    const [r, gg, b] = J.hexToRgb(colour);
    const fill = g.createLinearGradient(0, 0, 0, box.h);
    fill.addColorStop(0, 'rgba(' + r + ',' + gg + ',' + b + ',0.26)');
    fill.addColorStop(1, 'rgba(' + r + ',' + gg + ',' + b + ',0)');
    g.fillStyle = fill;
    g.fill();

    g.strokeStyle = colour;
    g.lineWidth = 1.4;
    g.stroke(line);
  }

  /* ================================================================== load */

  const loadSpark = J.$('#loadSpark');
  const loadHist = [];
  let frames = 0, lastSample = performance.now(), lastFrame = performance.now(), frameMs = 16;

  function loadFrame() {
    const now = performance.now();
    frameMs += ((now - lastFrame) - frameMs) * 0.1;
    lastFrame = now;
    frames++;

    if (now - lastSample >= 1000) {
      const fps = Math.round((frames * 1000) / (now - lastSample));
      frames = 0; lastSample = now;

      const fpsEl = J.$('#fpsVal'), frEl = J.$('#frameVal');
      if (fpsEl) fpsEl.textContent = fps;
      if (frEl)  frEl.textContent  = frameMs.toFixed(1);

      // "load" reads as the share of a 60fps budget the frame is consuming
      loadHist.push(J.clamp(frameMs / 33, 0.02, 1));
      while (loadHist.length > 60) loadHist.shift();
      sparkline(loadSpark, loadHist, J.settings.accent);

      const heap = J.$('#heapVal');
      if (heap) {
        heap.textContent = performance.memory
          ? (performance.memory.usedJSHeapSize / 1048576).toFixed(0) + 'M'
          : 'n/a';
      }
    }
    requestAnimationFrame(loadFrame);
  }

  /* ================================================================ weather */

  const WX = {
    0:  ['Clear',            '☀'],
    1:  ['Mainly clear',     '☀'],
    2:  ['Partly cloudy',    '⛅'],
    3:  ['Overcast',         '☁'],
    45: ['Fog',              '░'],
    48: ['Rime fog',         '░'],
    51: ['Light drizzle',    '☔'],
    53: ['Drizzle',          '☔'],
    55: ['Heavy drizzle',    '☔'],
    56: ['Freezing drizzle', '❄'],
    57: ['Freezing drizzle', '❄'],
    61: ['Light rain',       '☔'],
    63: ['Rain',             '☔'],
    65: ['Heavy rain',       '☔'],
    66: ['Freezing rain',    '❄'],
    67: ['Freezing rain',    '❄'],
    71: ['Light snow',       '❄'],
    73: ['Snow',             '❄'],
    75: ['Heavy snow',       '❄'],
    77: ['Snow grains',      '❄'],
    80: ['Rain showers',     '☔'],
    81: ['Rain showers',     '☔'],
    82: ['Violent showers',  '⛈'],
    85: ['Snow showers',     '❄'],
    86: ['Snow showers',     '❄'],
    95: ['Thunderstorm',     '⚡'],
    96: ['Thunderstorm',     '⚡'],
    99: ['Thunderstorm',     '⚡']
  };

  const describe = code => (WX[code] || ['Unknown', '◌'])[0];
  const iconFor  = code => (WX[code] || ['Unknown', '◌'])[1];

  /* Latest snapshot, handed to the model as conversation context. */
  const state = { weather: null, place: null, coords: null };

  function locate() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) return reject(new Error('no geolocation'));
      navigator.geolocation.getCurrentPosition(
        p => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }),
        e => reject(e),
        { timeout: 12000, maximumAge: 900000 }
      );
    });
  }

  async function fetchWeather(coords) {
    const imperial = J.settings.units === 'imperial';
    const url = 'https://api.open-meteo.com/v1/forecast'
      + '?latitude=' + coords.lat + '&longitude=' + coords.lon
      + '&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day'
      + '&hourly=temperature_2m,weather_code'
      + '&forecast_days=2&timezone=auto'
      + (imperial ? '&temperature_unit=fahrenheit&wind_speed_unit=mph' : '');

    const res = await fetch(url);
    if (!res.ok) throw new Error('weather http ' + res.status);
    return res.json();
  }

  /* Keyless reverse geocode, with the IANA timezone as a guaranteed fallback
     so the place line is never empty once we have coordinates. */
  async function reverseGeocode(coords) {
    try {
      const res = await fetch('https://api.bigdatacloud.net/data/reverse-geocode-client'
        + '?latitude=' + coords.lat + '&longitude=' + coords.lon + '&localityLanguage=en');
      if (res.ok) {
        const d = await res.json();
        const name = [d.city || d.locality, d.principalSubdivision, d.countryName].filter(Boolean).join(', ');
        if (name) return name;
      }
    } catch (e) { /* fall through */ }

    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    return tz ? tz.split('/').pop().replace(/_/g, ' ') : null;
  }

  function paintWeather(data, place) {
    const c = data.current;
    const unit = J.settings.units === 'imperial' ? 'F' : 'C';
    const spd  = J.settings.units === 'imperial' ? 'mph' : 'km/h';

    const set = (id, html) => { const el = J.$(id); if (el) el.innerHTML = html; };
    set('#wxTemp',  Math.round(c.temperature_2m) + '<small>°' + unit + '</small>');
    set('#wxIcon',  iconFor(c.weather_code));
    set('#wxDesc',  J.esc(describe(c.weather_code)));
    set('#wxFeels', Math.round(c.apparent_temperature) + '°');
    set('#wxWind',  Math.round(c.wind_speed_10m) + ' ' + spd);
    set('#wxHum',   Math.round(c.relative_humidity_2m) + '%');
    set('#wxPlace', place ? J.esc(place) : ' ');

    // next five 3-hour steps
    const strip = J.$('#wxStrip');
    if (strip && data.hourly) {
      const times = data.hourly.time, temps = data.hourly.temperature_2m, codes = data.hourly.weather_code;
      const now = new Date();
      let idx = times.findIndex(ts => new Date(ts) > now);
      if (idx < 0) idx = 0;

      strip.innerHTML = '';
      for (let n = 0; n < 5; n++) {
        const i = idx + n * 3;
        if (i >= times.length) break;
        const when = new Date(times[i]);
        const slot = document.createElement('div');
        slot.className = 'wx-slot';
        slot.innerHTML =
          '<span>' + String(when.getHours()).padStart(2, '0') + '</span>' +
          '<i>' + iconFor(codes[i]) + '</i>' +
          '<b>' + Math.round(temps[i]) + '°</b>';
        strip.appendChild(slot);
      }
    }

    state.weather = {
      temperature: Math.round(c.temperature_2m),
      feelsLike: Math.round(c.apparent_temperature),
      humidity: Math.round(c.relative_humidity_2m),
      wind: Math.round(c.wind_speed_10m) + ' ' + spd,
      conditions: describe(c.weather_code),
      unit: '°' + unit,
      isDay: !!c.is_day
    };
  }

  async function refreshWeather(silent) {
    try {
      const coords = state.coords || await locate();
      state.coords = coords;

      const data = await fetchWeather(coords);
      if (!state.place) state.place = await reverseGeocode(coords);
      paintWeather(data, state.place);
      if (!silent) J.log('Local conditions acquired' + (state.place ? ' — ' + state.place : ''), 'ok', 'net');
    } catch (err) {
      const desc = J.$('#wxDesc'), place = J.$('#wxPlace');
      const denied = err && (err.code === 1 || /denied/i.test(err.message || ''));
      if (desc)  desc.textContent = denied ? 'location denied' : 'unavailable';
      if (place) place.textContent = denied
        ? 'allow location access to enable this panel'
        : 'could not reach the weather service';
      if (!silent) J.log('Weather unavailable: ' + (denied ? 'location permission denied' : (err.message || 'error')), 'warn', 'net');
    }
  }

  /* ================================================================ context */
  /* The compact snapshot handed to the model with each message, so it can
     answer questions about the machine and the local environment. */

  function snapshot() {
    const now = new Date();
    const c = navigator.connection || {};
    return {
      localTime: now.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'medium' }),
      isoTime: now.toISOString(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      battery: batteryLevel === null ? 'unavailable'
        : Math.round(batteryLevel * 100) + '%' + (batteryCharging ? ' (charging)' : ''),
      network: navigator.onLine
        ? 'online' + (c.effectiveType ? ' (' + c.effectiveType + (c.downlink ? ', ' + c.downlink + ' Mb/s' : '') + ')' : '')
        : 'offline',
      location: state.place || (state.coords
        ? state.coords.lat.toFixed(3) + ', ' + state.coords.lon.toFixed(3)
        : 'unknown (location not shared)'),
      coords: state.coords || null,
      weather: state.weather || 'unavailable',
      platform: navigator.platform || 'unknown',
      language: navigator.language,
      screen: window.screen.width + 'x' + window.screen.height,
      units: J.settings.units
    };
  }

  /* ================================================================ counters */

  function bump(which) {
    if (which === 'turn')   { turns++;    const el = J.$('#turnsVal');  if (el) el.textContent = turns; }
    if (which === 'search') { searches++; const el = J.$('#searchVal'); if (el) el.textContent = searches; }
  }

  /* ================================================================== boot */

  function init() {
    const cores = J.$('#coresVal');
    if (cores) cores.textContent = navigator.hardwareConcurrency || 'n/a';

    tickClock();
    setInterval(tickClock, 1000);

    initBattery();

    paintNetwork();
    setInterval(paintNetwork, 5000);
    window.addEventListener('online',  () => { paintNetwork(); J.log('Uplink restored', 'ok', 'net'); });
    window.addEventListener('offline', () => { paintNetwork(); J.log('Uplink lost', 'crit', 'net'); J.toast('Network connection lost', 'crit'); });

    requestAnimationFrame(loadFrame);

    refreshWeather(true);
    setInterval(() => refreshWeather(true), 15 * 60 * 1000);

    window.addEventListener('resize', () => {
      paintBattery();
      sparkline(loadSpark, loadHist, J.settings.accent);
      sparkline(netSpark, netHist, navigator.onLine ? J.settings.accent : '#ff5f6b');
    });

    J.on('settings', () => { paintBattery(); refreshWeather(true); });
  }

  J.telemetry = { init, snapshot, refreshWeather, bump, describe, state };

})(window.J);
