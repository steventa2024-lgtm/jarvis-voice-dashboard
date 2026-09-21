"""
Live data sources for J.A.R.V.I.S.

Web search answers open questions, but badly for things that have an exact,
structured answer. "What is a dollar worth in yen" through a search engine
means reading a snippet off a page that may be months stale; through the ECB's
own feed it is a number, current, in 200ms.

So this is a set of narrow sources that return facts rather than prose. Every
one of them was reachable and keyless at the time of writing — no signup, no
token, no rate-limit ceremony. They are proxied through the server for the same
reason search is: a browser cannot call most of them directly because of CORS.

If a source dies, it dies alone: each is a separate function and a failure in
one is reported as itself rather than taking the tool down.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Kept current on purpose. Some endpoints refuse an out-of-date Chrome
# outright — ESPN answers Chrome/140 and returns 403 to Chrome/126, with no
# hint that the version is what it objects to. If a source starts failing for
# no visible reason, bump this first.
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/140.0 Safari/537.36')

# Sensible starting feeds. Deliberately mainstream and low-volume; the point is
# a briefing, not a firehose.
DEFAULT_FEEDS = [
    ('BBC World', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
    ('Ars Technica', 'https://feeds.arstechnica.com/arstechnica/index'),
    ('NPR', 'https://feeds.npr.org/1001/rss.xml'),
]


def _get_json(url, timeout=12):
    req = urllib.request.Request(url)
    req.add_header('User-Agent', UA)
    req.add_header('Accept', 'application/json')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _get_text(url, timeout=12):
    req = urllib.request.Request(url)
    req.add_header('User-Agent', UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


# ------------------------------------------------------------------ currency

def currency(q):
    """'100 usd to eur', 'usd jpy', 'eur' — all resolve to a rate."""
    text = (q or 'USD to EUR').upper()
    codes = re.findall(r'\b([A-Z]{3})\b', text)
    amount = 1.0
    m = re.search(r'(\d+(?:\.\d+)?)', text)
    if m:
        amount = float(m.group(1))

    base = codes[0] if codes else 'USD'
    targets = codes[1:] or ['EUR', 'GBP', 'JPY']

    data = _get_json('https://api.frankfurter.app/latest?' + urllib.parse.urlencode(
        {'from': base, 'to': ','.join(targets)}))
    rates = data.get('rates') or {}
    if not rates:
        return {'ok': False, 'error': 'No rate for %s -> %s.' % (base, ', '.join(targets))}

    lines = ['%s %s = %s %s' % (_fmt(amount), base, _fmt(amount * v), k)
             for k, v in rates.items()]
    return {'ok': True, 'source': 'European Central Bank via Frankfurter',
            'as_of': data.get('date', ''), 'summary': '; '.join(lines)}


def _fmt(v):
    return ('%.2f' % v).rstrip('0').rstrip('.') if v < 1000 else '%,.2f' % v


# -------------------------------------------------------------------- crypto

_COIN_ALIASES = {
    'btc': 'bitcoin', 'eth': 'ethereum', 'sol': 'solana', 'ada': 'cardano',
    'doge': 'dogecoin', 'xrp': 'ripple', 'ltc': 'litecoin', 'dot': 'polkadot',
}


def crypto(q):
    words = re.findall(r'[a-z0-9-]+', (q or 'bitcoin').lower())
    ids = []
    for w in words:
        if w in ('price', 'of', 'the', 'is', 'what', 'usd', 'in', 'now', 'today'):
            continue
        ids.append(_COIN_ALIASES.get(w, w))
    if not ids:
        ids = ['bitcoin']

    data = _get_json('https://api.coingecko.com/api/v3/simple/price?'
                     + urllib.parse.urlencode({
                         'ids': ','.join(ids[:5]),
                         'vs_currencies': 'usd',
                         'include_24hr_change': 'true'}))
    if not data:
        return {'ok': False, 'error': 'CoinGecko knows nothing called "%s". Use the full '
                                      'name, e.g. bitcoin.' % ', '.join(ids)}

    parts = []
    for name, v in data.items():
        chg = v.get('usd_24h_change')
        parts.append('%s $%s%s' % (
            name, '{:,.2f}'.format(v.get('usd', 0)),
            (' (%+.1f%% in 24h)' % chg) if chg is not None else ''))
    return {'ok': True, 'source': 'CoinGecko', 'summary': '; '.join(parts)}


# ----------------------------------------------------------------- wikipedia

def wikipedia(q):
    if not q:
        return {'ok': False, 'error': 'No subject supplied.'}
    title = urllib.parse.quote(q.strip().replace(' ', '_'), safe='')
    try:
        data = _get_json('https://en.wikipedia.org/api/rest_v1/page/summary/' + title)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            # fall back to search, since the title guess is usually the problem
            hits = _get_json('https://en.wikipedia.org/w/api.php?' + urllib.parse.urlencode({
                'action': 'query', 'list': 'search', 'srsearch': q,
                'format': 'json', 'srlimit': 3}))
            names = [h['title'] for h in
                     (hits.get('query', {}).get('search') or [])]
            if not names:
                return {'ok': False, 'error': 'Wikipedia has no article for "%s".' % q}
            return wikipedia(names[0])
        raise

    extract = data.get('extract', '')
    if not extract:
        return {'ok': False, 'error': 'No summary for "%s".' % q}
    return {'ok': True, 'source': 'Wikipedia', 'title': data.get('title', q),
            'summary': extract[:1200],
            'url': (data.get('content_urls', {}).get('desktop', {}) or {}).get('page', '')}


# ---------------------------------------------------------------- tech news

def hackernews(_q=None, n=8):
    ids = _get_json('https://hacker-news.firebaseio.com/v0/topstories.json')[:n]
    out = []
    for i in ids:
        try:
            item = _get_json('https://hacker-news.firebaseio.com/v0/item/%d.json' % i)
            if item and item.get('title'):
                out.append({'title': item['title'], 'url': item.get('url', ''),
                            'score': item.get('score', 0)})
        except Exception:
            continue
    if not out:
        return {'ok': False, 'error': 'Hacker News returned nothing.'}
    return {'ok': True, 'source': 'Hacker News',
            'summary': '\n'.join('%d. %s (%s points)' % (k + 1, s['title'], s['score'])
                                 for k, s in enumerate(out)),
            'items': out}


# ---------------------------------------------------------------------- news

def _parse_feed(xml_text, limit=5):
    root = ET.fromstring(xml_text)
    items = root.findall('.//item') or root.findall(
        './/{http://www.w3.org/2005/Atom}entry')
    out = []
    for it in items[:limit]:
        title = it.findtext('title') or it.findtext(
            '{http://www.w3.org/2005/Atom}title') or ''
        if title.strip():
            out.append(title.strip())
    return out


def news(q=None, feeds=None):
    """A short briefing across a handful of feeds.

    RSS needs no key and no account, which makes it the cheapest possible way
    to give him a current view of the world.
    """
    use = feeds or DEFAULT_FEEDS
    blocks, failed = [], []
    for name, url in use:
        try:
            heads = _parse_feed(_get_text(url, timeout=10))
            if heads:
                blocks.append('%s: %s' % (name, '; '.join(heads)))
        except Exception as err:
            failed.append('%s (%s)' % (name, str(err)[:40]))

    if not blocks:
        return {'ok': False, 'error': 'No feed responded. ' + '; '.join(failed)}
    return {'ok': True, 'source': 'RSS', 'summary': '\n\n'.join(blocks),
            'failed': failed}


# ------------------------------------------------------------------ tv guide

def tv(q=None):
    country = 'US'
    m = re.search(r'\b([A-Z]{2})\b', (q or '').upper())
    if m:
        country = m.group(1)
    data = _get_json('https://api.tvmaze.com/schedule?' + urllib.parse.urlencode(
        {'country': country}))
    now = time.strftime('%H:%M')
    rows = []
    for ep in data[:40]:
        show = (ep.get('show') or {}).get('name', '')
        when = (ep.get('airtime') or '')
        if show and when >= now:
            rows.append('%s %s' % (when, show))
        if len(rows) >= 10:
            break
    if not rows:
        rows = ['%s %s' % (ep.get('airtime', ''), (ep.get('show') or {}).get('name', ''))
                for ep in data[:8]]
    return {'ok': True, 'source': 'TVMaze', 'summary': '; '.join(rows)}


# ------------------------------------------------------------------- daylight

def daylight(q=None, lat=None, lon=None):
    if lat is None or lon is None:
        nums = re.findall(r'-?\d+\.?\d*', q or '')
        if len(nums) >= 2:
            lat, lon = nums[0], nums[1]
        else:
            return {'ok': False, 'error': 'No coordinates. The dashboard already knows the '
                                          'user location; pass it as "lat,lon".'}
    data = _get_json('https://api.sunrise-sunset.org/json?' + urllib.parse.urlencode(
        {'lat': lat, 'lng': lon, 'formatted': 1}))
    r = data.get('results') or {}
    if not r:
        return {'ok': False, 'error': 'No daylight data for that location.'}
    return {'ok': True, 'source': 'sunrise-sunset.org',
            'summary': 'Sunrise %s UTC, sunset %s UTC, day length %s'
                       % (r.get('sunrise'), r.get('sunset'), r.get('day_length'))}


# ------------------------------------------------------------------- markets

# Spoken company names, since nobody says "ticker A A P L" out loud.
_TICKERS = {
    'apple': 'AAPL', 'microsoft': 'MSFT', 'google': 'GOOGL', 'alphabet': 'GOOGL',
    'amazon': 'AMZN', 'meta': 'META', 'facebook': 'META', 'tesla': 'TSLA',
    'nvidia': 'NVDA', 'netflix': 'NFLX', 'amd': 'AMD', 'intel': 'INTC',
    'palantir': 'PLTR', 'coinbase': 'COIN', 'disney': 'DIS', 'boeing': 'BA',
    's&p': '^GSPC', 'sp500': '^GSPC', 's&p 500': '^GSPC', 'spx': '^GSPC',
    'dow': '^DJI', 'dow jones': '^DJI', 'nasdaq': '^IXIC',
    'russell': '^RUT', 'vix': '^VIX', 'ftse': '^FTSE', 'nikkei': '^N225',
}

_INDEX_SET = ['^GSPC', '^DJI', '^IXIC']


def _quote(symbol):
    data = _get_json(
        'https://query1.finance.yahoo.com/v8/finance/chart/'
        + urllib.parse.quote(symbol) + '?range=5d&interval=1d')
    result = ((data.get('chart') or {}).get('result') or [None])[0]
    if not result:
        return None
    m = result.get('meta') or {}
    price = m.get('regularMarketPrice')
    prev = m.get('chartPreviousClose') or m.get('previousClose')
    if price is None:
        return None
    change = None
    if prev:
        change = (price - prev) / prev * 100.0
    return {
        'symbol': m.get('symbol', symbol),
        'name': m.get('shortName') or m.get('longName') or symbol,
        'price': price,
        'currency': m.get('currency', ''),
        'change_pct': change,
        'day_low': m.get('regularMarketDayLow'),
        'day_high': m.get('regularMarketDayHigh'),
        'year_low': m.get('fiftyTwoWeekLow'),
        'year_high': m.get('fiftyTwoWeekHigh'),
    }


def _resolve(word):
    w = word.strip().lower()
    if w in _TICKERS:
        return _TICKERS[w]
    if re.fullmatch(r'\^?[A-Za-z.\-]{1,6}', word.strip()):
        return word.strip().upper()
    return None


def stocks(q):
    """Quotes for named companies, tickers, or the major indices.

    Yahoo's chart endpoint is used because it still answers without a key —
    their v7 quote endpoint now returns 401. This reports numbers only; what
    they mean is a separate question and not one a price feed can answer.
    """
    text = (q or '').strip()
    if not text or re.search(r'\b(market|markets|indices|indexes|overall)\b', text, re.I):
        symbols = _INDEX_SET
    else:
        symbols = []
        low = text.lower()
        for phrase, sym in _TICKERS.items():             # multi-word names first
            if ' ' in phrase and phrase in low:
                symbols.append(sym)
                low = low.replace(phrase, ' ')
        for word in re.findall(r'[\^A-Za-z.&\-]+', low):
            if word in ('and', 'the', 'price', 'of', 'stock', 'stocks', 'is',
                        'what', 'how', 'doing', 'today', 'now', 'at'):
                continue
            sym = _resolve(word)
            if sym and sym not in symbols:
                symbols.append(sym)
        symbols = symbols[:5] or _INDEX_SET

    rows, missed = [], []
    for sym in symbols:
        try:
            qd = _quote(sym)
        except Exception:
            qd = None
        if not qd:
            missed.append(sym)
            continue
        chg = ('%+.2f%%' % qd['change_pct']) if qd['change_pct'] is not None else ''
        rows.append('%s (%s) %s%s %s' % (
            qd['name'], qd['symbol'],
            '{:,.2f}'.format(qd['price']), (' ' + qd['currency']) if qd['currency'] else '',
            chg))

    if not rows:
        return {'ok': False,
                'error': 'No quote for %s. Use a ticker such as AAPL, or a company name.'
                         % ', '.join(missed or symbols)}
    return {'ok': True, 'source': 'Yahoo Finance',
            'summary': '; '.join(rows)
                       + (' (no data for %s)' % ', '.join(missed) if missed else ''),
            'note': 'Prices may be delayed and are indicative, not trade-quality data.'}




# -------------------------------------------------------------------- sports

#  Scores and fixtures.
#
#  This exists because the alternative was watching him spend twelve web
#  lookups scraping a schedule page and still come back without a start time.
#  A scoreboard is four structured fields; asking a search engine for it in
#  English and reading the answer back out of prose is the wrong shape of
#  question entirely.
#
#  ESPN publishes these as plain JSON with no key and no registration. It is
#  undocumented rather than private — the same endpoint their own scoreboard
#  pages call — so it can change without warning, which is why every field
#  below is read defensively and a missing one simply does not appear.

SPORT_PATHS = {
    'mlb':     'baseball/mlb',
    'nfl':     'football/nfl',
    'nba':     'basketball/nba',
    'nhl':     'hockey/nhl',
    'ncaaf':   'football/college-football',
    'ncaab':   'basketball/mens-college-basketball',
    'mls':     'soccer/usa.1',
    'epl':     'soccer/eng.1',
    'laliga':  'soccer/esp.1',
    'ucl':     'soccer/uefa.champions',
    'wnba':    'basketball/wnba',
}

# Words that tell us which league without being part of a team name.
LEAGUE_WORDS = {
    'mlb': 'mlb', 'baseball': 'mlb',
    'nfl': 'nfl', 'football': 'nfl',
    'nba': 'nba', 'basketball': 'nba',
    'nhl': 'nhl', 'hockey': 'nhl',
    'wnba': 'wnba',
    'mls': 'mls',
    'premier': 'epl', 'epl': 'epl',
    'liga': 'laliga',
    'champions': 'ucl',
    'ncaaf': 'ncaaf', 'ncaab': 'ncaab', 'college': 'ncaaf',
}


def _espn(sport_path, when=None):
    """ESPN's own scoreboard endpoint, called the way their site calls it.

    It refuses a bare browser User-Agent with a 403 while accepting the same
    UA alongside an Accept and a Referer — measured, not guessed. Sending the
    full set is what keeps this working."""
    url = ('https://site.api.espn.com/apis/site/v2/sports/%s/scoreboard'
           % sport_path)
    if when:
        url += '?dates=' + when

    req = urllib.request.Request(url)
    req.add_header('User-Agent', UA)
    req.add_header('Accept', 'application/json, text/plain, */*')
    req.add_header('Referer', 'https://www.espn.com/')
    req.add_header('Origin', 'https://www.espn.com')
    with urllib.request.urlopen(req, timeout=14) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _competitors(game):
    """Home and away, each reduced to what a scoreboard actually shows."""
    comp = (game.get('competitions') or [{}])[0]
    out = {}
    for c in comp.get('competitors') or []:
        team = c.get('team') or {}
        out[c.get('homeAway') or 'home'] = {
            'name': team.get('shortDisplayName') or team.get('displayName') or '?',
            'full': team.get('displayName') or '',
            'abbr': team.get('abbreviation') or '',
            'logo': team.get('logo') or '',
            'score': c.get('score'),
            'record': ((c.get('records') or [{}])[0].get('summary') or ''),
        }
    return comp, out


def _one_game(game):
    comp, sides = _competitors(game)
    status = (game.get('status') or {})
    st = (status.get('type') or {})
    home, away = sides.get('home', {}), sides.get('away', {})

    broadcast = ''
    for b in (comp.get('broadcasts') or []):
        names = b.get('names') or []
        if names:
            broadcast = names[0]
            break

    return {
        'id': game.get('id'),
        'away': away, 'home': home,
        'state': st.get('state') or '',              # pre | in | post
        'detail': st.get('shortDetail') or st.get('detail') or '',
        'completed': bool(st.get('completed')),
        'start': game.get('date') or '',
        'venue': ((comp.get('venue') or {}).get('fullName') or ''),
        'broadcast': broadcast,
    }


def _describe(g):
    """One line, and it must not be ambiguous about whether the game is over.

    "Dodgers 3, Braves 3 (Bot 4th)" reads as a final score to anything that is
    not paying close attention — he reported a live tie game as having ended.
    The state is the most important word in the sentence, so it goes first.
    """
    a, h = g['away'], g['home']
    if g['state'] == 'pre':
        line = 'UPCOMING: %s at %s — %s' % (a['name'], h['name'], g['detail'])
        if g['broadcast']:
            line += ' on ' + g['broadcast']
        return line

    score = '%s %s, %s %s' % (a['name'], a.get('score') or '0',
                              h['name'], h.get('score') or '0')
    if g['state'] == 'in':
        return 'IN PROGRESS (not finished): %s — currently %s' % (score, g['detail'])
    return 'FINAL: %s' % score


def sports(q=None):
    """Fixtures and scores. Ask for a team, a league, or nothing at all."""
    text = (q or '').strip()
    low = text.lower()

    # Which league. A named one wins; otherwise try the popular ones in turn
    # and keep whichever actually mentions the team.
    league = None
    for word, key in LEAGUE_WORDS.items():
        if re.search(r'\b%s\b' % re.escape(word), low):
            league = key
            break

    # Strip the league words back out so what is left is the team.
    team = re.sub(r'\b(%s)\b' % '|'.join(map(re.escape, LEAGUE_WORDS)), ' ', low)
    team = re.sub(r'\b(score|scores|game|games|play|playing|played|today|tonight|'
                  r'yesterday|result|results|the|do|does|did|is|are|who|what|when|'
                  r'time|vs|versus|against|a|an)\b', ' ', team)
    team = re.sub(r'[^a-z0-9 ]+', ' ', team)
    team = re.sub(r'\s+', ' ', team).strip()

    order = [league] if league else ['mlb', 'nba', 'nfl', 'nhl', 'epl', 'wnba']
    tried, best = [], None

    for key in order:
        path = SPORT_PATHS.get(key)
        if not path:
            continue
        tried.append(key)
        try:
            data = _espn(path)
        except Exception:
            continue

        games = [_one_game(g) for g in (data.get('events') or [])]
        if not games:
            continue

        if team:
            hit = [g for g in games
                   if team in (g['home']['full'] + ' ' + g['home']['name']
                               + ' ' + g['away']['full'] + ' ' + g['away']['name']).lower()
                   or any(w in (g['home']['full'] + ' ' + g['away']['full']).lower()
                          for w in team.split() if len(w) > 3)]
            if hit:
                best = (key, hit)
                break
            # keep looking in the other leagues before giving up
            continue

        best = (key, games)
        break

    if not best:
        if team:
            return {'ok': True, 'source': 'ESPN',
                    'summary': 'No game today for "%s" in %s. They may be off, or '
                               'the name did not match a team.'
                               % (text.strip(), ', '.join(tried).upper())}
        return {'ok': True, 'source': 'ESPN',
                'summary': 'Nothing scheduled in %s today.' % ', '.join(tried).upper()}

    key, games = best
    lines = [_describe(g) for g in games[:8]]

    # The card carries the structure; the summary is what he reads out. Two
    # different jobs, and deriving one from the other is what made cards
    # fragile everywhere else in this project.
    cards = []
    for g in games[:6]:
        a, h = g['away'], g['home']
        cards.append({
            'title': '%s at %s' % (a['name'], h['name']),
            'subtitle': key.upper(),
            'state': g['state'],
            'status': g['detail'],
            'on': g['broadcast'],
            'venue': g['venue'],
            'teams': {
                'away': {'name': a['name'], 'abbr': a['abbr'],
                         'logo': a['logo'], 'score': a.get('score'),
                         'record': a.get('record')},
                'home': {'name': h['name'], 'abbr': h['abbr'],
                         'logo': h['logo'], 'score': h.get('score'),
                         'record': h.get('record')},
            },
            'accent': 'live' if g['state'] == 'in' else ('ok' if g['completed'] else None),
        })

    return {'ok': True, 'source': 'ESPN', 'league': key,
            'card': {'kind': 'game', 'items': cards},
            'summary': '\n'.join(lines)}


# ---------------------------------------------------------------------------
#  The markets strip
#
#  stocks() answers a question in prose, which is right for something spoken
#  aloud and wrong for a panel that has to draw a line chart. This returns
#  structure instead: a price, a change, and the intraday series behind it.
#
#  Futures rather than cash indices, deliberately. The dashboard runs all
#  night and "Market closed" for sixteen hours a day is not a market watch —
#  futures trade nearly around the clock, which is exactly why a real ticker
#  shows them.
# ---------------------------------------------------------------------------

MARKET_STRIP = [
    ('YM=F',    'Dow Futures'),
    ('NQ=F',    'Nasdaq Futures'),
    ('RTY=F',   'Russell 2000'),
    ('^VIX',    'VIX'),
    ('GC=F',    'Gold'),
    ('BTC-USD', 'Bitcoin'),
    ('CL=F',    'Crude Oil'),
]

SPARK_POINTS = 40          # a 90px sparkline cannot show 260 of them


def _series(symbol):
    """One symbol: current price, change against the previous close, and a
    downsampled intraday series for the sparkline."""
    data = _get_json(
        'https://query1.finance.yahoo.com/v8/finance/chart/'
        + urllib.parse.quote(symbol) + '?range=1d&interval=5m')
    result = ((data.get('chart') or {}).get('result') or [None])[0]
    if not result:
        return None

    meta = result.get('meta') or {}
    price = meta.get('regularMarketPrice')
    prev = meta.get('chartPreviousClose') or meta.get('previousClose')
    if price is None:
        return None

    quote = ((result.get('indicators') or {}).get('quote') or [{}])[0]
    closes = [c for c in (quote.get('close') or []) if c is not None]

    # Evenly spaced rather than the last N: the shape of the whole session is
    # the point, and the tail alone reads as a flat line on a quiet day.
    if len(closes) > SPARK_POINTS:
        step = len(closes) / float(SPARK_POINTS)
        closes = [closes[int(i * step)] for i in range(SPARK_POINTS)]

    change = (price - prev) if prev else None
    pct = ((price - prev) / prev * 100.0) if prev else None
    return {
        'symbol': symbol,
        'price': price,
        'change': change,
        'pct': pct,
        'spark': [round(c, 4) for c in closes],
    }


def markets(q=None):
    """The whole strip in one call, so the panel makes one request per refresh."""
    rows, failed = [], []
    for symbol, label in MARKET_STRIP:
        try:
            row = _series(symbol)
            if not row:
                failed.append(symbol)
                continue
            row['name'] = label
            rows.append(row)
        except Exception as err:
            failed.append('%s (%s)' % (symbol, str(err)[:40]))

    if not rows:
        return {'ok': False, 'error': 'No market data came back (%s).'
                                      % '; '.join(failed)}
    out = {'ok': True, 'source': 'Yahoo Finance', 'rows': rows,
           'note': 'Delayed and indicative, not trade-quality data.'}
    if failed:
        out['missing'] = failed
    return out


SOURCES = {
    'stocks': stocks,
    'markets': markets,
    'currency': currency,
    'crypto': crypto,
    'wikipedia': wikipedia,
    'tech_news': hackernews,
    'news': news,
    'tv_tonight': tv,
    'daylight': daylight,
    'sports': sports,
}


def lookup(source, q=None):
    fn = SOURCES.get(source)
    if not fn:
        return {'ok': False, 'error': 'Unknown source "%s". Known: %s'
                                      % (source, ', '.join(sorted(SOURCES)))}
    try:
        return fn(q)
    except urllib.error.HTTPError as err:
        return {'ok': False, 'error': '%s returned HTTP %s.' % (source, err.code)}
    except Exception as err:
        return {'ok': False, 'error': '%s failed: %s' % (source, str(err)[:120])}
