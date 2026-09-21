/* Markets and teams — the two things worth glancing at without asking.

   Both feeds already existed as `lookup` sources the model could call. What
   was missing was the ambient half: numbers that are simply there, the way
   the weather is, rather than the answer to a question.

   ---------------------------------------------------------------------------
   Futures, not cash indices.

   "Market closed" for sixteen hours a day is not a market watch. Futures
   trade nearly around the clock, which is exactly why every real ticker shows
   them, and why this dashboard — which runs all night on a television — shows
   them too.

   ---------------------------------------------------------------------------
   How often this asks, because it is the whole design.

   J.A.R.V.I.S. runs unattended overnight. A panel refreshing every thirty
   seconds is roughly 1,300 requests across an empty night, and the wake word
   already taught us what that earns: restarting a cloud recogniser every
   seven seconds all night got the speech service to refuse us for most of a
   day.

   The tab being visible is not enough of a guard here, because on a wall
   display it is visible at four in the morning. So the cadence follows the
   session instead:

     * hidden tab            — nothing at all
     * US cash session open  — every 60s, one request for the whole strip
     * futures only          — every 5 minutes
     * weekend gap           — every 15 minutes
     * a team's game live    — that team polled every 60s, else half-hourly */

(function () {
  'use strict';

  const TEAMS = [
    { label: 'Lakers',  q: 'lakers nba' },
    { label: 'Braves',  q: 'atlanta braves mlb' },
    { label: 'Raiders', q: 'las vegas raiders nfl' }
  ];

  const EVERY = {
    cash:      60000,
    extended: 300000,
    closed:   900000,
    liveGame:  60000,
    idleGame: 1800000
  };

  let timer = null;
  let lastTeamsAt = 0;
  let anyGameLive = false;

  /* ------------------------------------------------------------- helpers */

  /* Worked out in New York rather than by guessing an offset from here.
     Intl knows about daylight saving; arithmetic on UTC does not, and gets it
     wrong twice a year. */
  function nyNow() {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false
    }).formatToParts(new Date());
    const get = t => (parts.find(p => p.type === t) || {}).value;
    return {
      day: get('weekday'),
      mins: parseInt(get('hour'), 10) * 60 + parseInt(get('minute'), 10)
    };
  }

  function sessionPhase() {
    const { day, mins } = nyNow();
    const weekday = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].indexOf(day) !== -1;

    if (weekday && mins >= 570 && mins < 960) return 'cash';        // 09:30-16:00
    // Futures rest Friday evening until Sunday evening.
    if (day === 'Sat') return 'closed';
    if (day === 'Fri' && mins >= 1020) return 'closed';             // after 17:00
    if (day === 'Sun' && mins < 1080) return 'closed';              // before 18:00
    return 'extended';
  }

  function visible() { return document.visibilityState === 'visible'; }

  async function ask(source, q) {
    const res = await fetch('api/knowledge?' +
      new URLSearchParams({ source: source, q: q || '' }));
    if (!res.ok) throw new Error('lookup ' + res.status);
    return res.json();
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function fill(id, nodes, emptyText) {
    const list = J.$('#' + id);
    if (!list) return;
    list.innerHTML = '';
    if (!nodes.length) { list.appendChild(el('li', 'watch-note', emptyText)); return; }
    nodes.forEach(n => list.appendChild(n));
  }

  function num(v, dp) {
    return Number(v).toLocaleString(undefined,
      { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }

  /* ---------------------------------------------------------- sparkline

     Inline SVG rather than a canvas: there are seven of these, they redraw
     once a minute at most, and an SVG survives a re-render without needing a
     device-pixel-ratio dance. */
  const SPARK_W = 76, SPARK_H = 20;

  function sparkline(series, dir) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'mk-spark ' + dir);
    svg.setAttribute('viewBox', '0 0 ' + SPARK_W + ' ' + SPARK_H);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-hidden', 'true');

    const pts = (series || []).filter(n => typeof n === 'number');
    if (pts.length < 2) return svg;

    const lo = Math.min.apply(null, pts);
    const hi = Math.max.apply(null, pts);
    const span = (hi - lo) || 1;
    const pad = 2;

    const coords = pts.map((v, i) => {
      const x = (i / (pts.length - 1)) * SPARK_W;
      const y = SPARK_H - pad - ((v - lo) / span) * (SPARK_H - pad * 2);
      return x.toFixed(1) + ',' + y.toFixed(1);
    });

    const line = document.createElementNS(svg.namespaceURI, 'polyline');
    line.setAttribute('points', coords.join(' '));
    svg.appendChild(line);
    return svg;
  }

  /* ------------------------------------------------------------- markets */

  function marketRow(r) {
    const dir = r.pct > 0 ? 'watch-up' : r.pct < 0 ? 'watch-down' : 'watch-flat';
    const li = el('li', 'mk');

    const top = el('div', 'mk-top');
    top.appendChild(el('span', 'mk-name', r.name));
    top.appendChild(sparkline(r.spark, dir));
    li.appendChild(top);

    // Two decimals reads wrong on an index and right on oil, so scale it.
    const dp = Math.abs(r.price) >= 1000 ? 2 : Math.abs(r.price) >= 10 ? 2 : 4;

    const bot = el('div', 'mk-bot');
    bot.appendChild(el('b', 'mk-price', num(r.price, dp)));
    const chg = el('b', 'mk-chg ' + dir);
    chg.textContent = (r.change > 0 ? '+' : '') + num(r.change, dp)
                    + '  ' + (r.pct > 0 ? '+' : '') + r.pct.toFixed(2) + '%';
    bot.appendChild(chg);
    li.appendChild(bot);
    return li;
  }

  async function refreshMarkets() {
    try {
      const d = await ask('markets', '');
      if (!d.ok) { fill('marketList', [], d.error || 'no data'); return; }
      fill('marketList', (d.rows || []).map(marketRow), 'no data');
    } catch (e) {
      J.log('Markets lookup failed: ' + e.message, 'warn', 'net');
    }
  }

  /* --------------------------------------------------------------- teams */

  /* Same treatment as the score pop-up: load it immediately, and if it fails,
     remove it rather than leave a broken-picture icon in a scoreboard. */
  function badge(t) {
    const wrap = el('span', 'tm-side');
    if (t.logo) {
      const img = document.createElement('img');
      img.src = t.logo;
      img.alt = '';
      img.addEventListener('error', () => img.remove());
      wrap.appendChild(img);
    }
    wrap.appendChild(el('em', null, t.abbr || t.name || '?'));
    return wrap;
  }

  function teamRow(team, data) {
    const item = ((data && data.card) || {}).items;
    const game = item && item[0];

    if (!game) {
      const li = el('li', 'tm');
      li.appendChild(el('span', 'tm-quiet', team.label));
      li.appendChild(el('b', 'watch-flat', 'no game'));
      return { node: li, live: false };
    }

    const t = game.teams || {};
    const home = t.home || {}, away = t.away || {};
    const live = game.state === 'in';

    const li = el('li', 'tm' + (live ? ' is-live' : ''));
    const matchup = el('span', 'tm-match');
    matchup.appendChild(badge(away));
    matchup.appendChild(el('i', 'tm-at', '@'));
    matchup.appendChild(badge(home));
    li.appendChild(matchup);

    const b = el('b');
    if (game.state === 'pre') {
      b.className = 'watch-flat';
      b.textContent = game.status || 'scheduled';
    } else {
      const mine = (home.name || '').toLowerCase()
        .indexOf(team.label.toLowerCase()) !== -1 ? home : away;
      const them = mine === home ? away : home;
      const ms = parseInt(mine.score, 10), ts = parseInt(them.score, 10);
      b.className = isNaN(ms) || isNaN(ts) ? 'watch-flat'
                  : ms > ts ? 'watch-up' : ms < ts ? 'watch-down' : 'watch-flat';
      b.textContent = (away.score || '0') + '–' + (home.score || '0')
                    + '  ' + (game.status || '');
    }
    li.appendChild(b);
    return { node: li, live: live };
  }

  async function refreshTeams() {
    const nodes = [];
    let live = false;
    for (const team of TEAMS) {
      try {
        const built = teamRow(team, await ask('sports', team.q));
        nodes.push(built.node);
        if (built.live) live = true;
      } catch (e) {
        J.log('Sports lookup failed for ' + team.label + ': ' + e.message, 'warn', 'net');
      }
    }
    anyGameLive = live;
    lastTeamsAt = Date.now();
    fill('teamList', nodes, 'no data');
  }

  /* ----------------------------------------------------------- scheduling */

  async function tick() {
    if (!visible()) return schedule();          // hidden: ask for nothing at all

    await refreshMarkets();

    const due = anyGameLive ? EVERY.liveGame : EVERY.idleGame;
    if (Date.now() - lastTeamsAt > due) await refreshTeams();

    schedule();
  }

  function schedule() {
    clearTimeout(timer);
    const next = !visible() ? EVERY.idleGame
               : anyGameLive ? EVERY.liveGame
               : EVERY[sessionPhase()];
    timer = setTimeout(tick, next);
  }

  document.addEventListener('visibilitychange', () => {
    if (visible()) { clearTimeout(timer); tick(); }      // catch up on return
  });

  J.watch = { refresh: tick, phase: sessionPhase };

  // Let the rest of the boot finish before adding network work to it.
  setTimeout(tick, 1500);
})();
