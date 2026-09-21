"""
The job hunt for J.A.R.V.I.S.

Where this looks, and why not where you expected
------------------------------------------------
Indeed answers automated requests with HTTP 403, and its terms forbid
scraping. LinkedIn is the same behind a login wall. Getting past either means
CAPTCHA evasion and disguised traffic, which is not built here and will not
be.

It turns out not to cost much, because **Indeed is an index, not the
destination**. The application itself almost always happens on an applicant
tracking system — Greenhouse, Lever, Ashby, Workable — and those publish their
boards as open JSON with no key and no ceremony. One company's Greenhouse
board returns nearly six hundred live postings with the full description and
the real apply URL. So this goes where the jobs actually are rather than to
the directory that points at them.

What it will not do
-------------------
It does not press submit. It finds, scores, asks, and prepares — and then the
form is opened for you with the answers ready. An application cannot be
unsent: a letter addressed to the wrong company, or an invented year of
experience, lands in a real recruiter's inbox under your name and stays
there. The cost of a mistake here is asymmetric and permanent, so the last
click is yours.

For the same reason nothing on an application is ever invented. A field that
cannot be answered from the stored profile is left blank and flagged.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, 'jarvis_jobs.json')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/140.0 Safari/537.36')

# Deliberately unhurried. These boards are free and open; hammering them is
# how open things stop being open.
POLL_SECONDS = 900          # a full sweep at most every fifteen minutes
BOARD_GAP = 1.5             # pause between boards inside a sweep
KEEP_JOBS = 400
EMBED_CAP = 120             # postings scored per board per sweep

#  Job posts are mostly boilerplate — the same benefits paragraph, the same
#  equal-opportunity block, the same "you will collaborate cross-functionally".
#  Measured on one board, everything eligible scored between 0.69 and 0.75, so
#  an absolute threshold either lets all of it through or none of it. Ranking
#  is what carries the signal here, not the number.
#
#  So the floor only removes the obviously unrelated, and the cap decides how
#  many actually reach you. Eighty-three cards in the inbox is not a shortlist.
MAX_NEW_PER_SWEEP = 12

_lock = threading.Lock()
_worker = {'thread': None, 'running': False, 'stop': False,
           'last': 0, 'scanning': False, 'checked': 0, 'found': 0, 'error': ''}


# ------------------------------------------------------------------- storage

def _read():
    try:
        with open(STORE, 'r', encoding='utf-8') as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    # The matching half of the profile, unchanged.
    d.setdefault('profile', {'resume': '', 'skills': '', 'wants': '',
                             'titles': '', 'locations': '', 'remote': True,
                             'salary_floor': '', 'exclude': ''})

    # The applicant half. Separate concern, same record: scoring a listing needs
    # a resume, filling a form needs a name and an email, and neither should
    # have to be typed twice. Added rather than replacing - setdefault leaves an
    # existing profile alone, so nothing already stored is touched.
    for field in ('first_name', 'last_name', 'full_name', 'email', 'phone',
                  'location', 'linkedin', 'github'):
        d['profile'].setdefault(field, '')
    d.setdefault('watch', list(SEED_BOARDS))
    d.setdefault('jobs', [])
    d.setdefault('seen', [])
    d.setdefault('enabled', False)
    return d


def _write(d):
    tmp = STORE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, STORE)


# --------------------------------------------------------------------- boards

#  A starting watchlist. Every one of these was reachable when written; a slug
#  that dies is reported rather than retried forever.
SEED_BOARDS = [
    {'company': 'Stripe', 'ats': 'greenhouse', 'slug': 'stripe'},
    {'company': 'Anthropic', 'ats': 'greenhouse', 'slug': 'anthropic'},
    {'company': 'Databricks', 'ats': 'greenhouse', 'slug': 'databricks'},
    {'company': 'Figma', 'ats': 'greenhouse', 'slug': 'figma'},
    {'company': 'Discord', 'ats': 'greenhouse', 'slug': 'discord'},
    {'company': 'Reddit', 'ats': 'greenhouse', 'slug': 'reddit'},
    {'company': 'Cloudflare', 'ats': 'greenhouse', 'slug': 'cloudflare'},
    {'company': 'Ramp', 'ats': 'ashby', 'slug': 'ramp'},
    {'company': 'Linear', 'ats': 'ashby', 'slug': 'linear'},
]

ATS_LABEL = {'greenhouse': 'Greenhouse', 'ashby': 'Ashby',
             'lever': 'Lever', 'workable': 'Workable'}


def _get(url, timeout=20):
    req = urllib.request.Request(url)
    req.add_header('User-Agent', UA)
    req.add_header('Accept', 'application/json, text/plain, */*')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _strip(html):
    """Descriptions arrive as HTML. The matcher wants words."""
    text = re.sub(r'(?is)<(script|style).*?</\1>', ' ', html or '')
    text = re.sub(r'(?s)<[^>]+>', ' ', text)
    text = (text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                .replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"'))
    return re.sub(r'\s+', ' ', text).strip()


def _fetch_greenhouse(slug):
    d = _get('https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true'
             % urllib.parse.quote(slug))
    out = []
    for j in d.get('jobs') or []:
        out.append({
            'id': 'gh:%s:%s' % (slug, j.get('id')),
            'title': j.get('title') or '',
            'location': ((j.get('location') or {}).get('name') or ''),
            'url': j.get('absolute_url') or '',
            'posted': j.get('first_published') or j.get('updated_at') or '',
            'description': _strip(j.get('content'))[:6000],
        })
    return out


def _fetch_ashby(slug):
    d = _get('https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true'
             % urllib.parse.quote(slug))
    out = []
    for j in d.get('jobs') or []:
        out.append({
            'id': 'ash:%s:%s' % (slug, j.get('id')),
            'title': j.get('title') or '',
            'location': j.get('location') or '',
            'url': j.get('jobUrl') or j.get('applyUrl') or '',
            'posted': j.get('publishedAt') or '',
            'description': _strip(j.get('descriptionHtml') or j.get('descriptionPlain'))[:6000],
            'pay': (j.get('compensationTierSummary') or ''),
        })
    return out


def _fetch_lever(slug):
    d = _get('https://api.lever.co/v0/postings/%s?mode=json' % urllib.parse.quote(slug))
    out = []
    for j in (d if isinstance(d, list) else []):
        cat = j.get('categories') or {}
        out.append({
            'id': 'lev:%s:%s' % (slug, j.get('id')),
            'title': j.get('text') or '',
            'location': cat.get('location') or '',
            'url': j.get('hostedUrl') or j.get('applyUrl') or '',
            'posted': '',
            'description': _strip(j.get('descriptionPlain') or j.get('description'))[:6000],
        })
    return out


def _fetch_workable(slug):
    d = _get('https://apply.workable.com/api/v1/widget/accounts/%s'
             % urllib.parse.quote(slug))
    out = []
    for j in d.get('jobs') or []:
        out.append({
            'id': 'wk:%s:%s' % (slug, j.get('shortcode') or j.get('id')),
            'title': j.get('title') or '',
            'location': ', '.join(x for x in (j.get('city'), j.get('country')) if x),
            'url': j.get('url') or j.get('application_url') or '',
            'posted': j.get('published_on') or '',
            'description': _strip(j.get('description'))[:6000],
        })
    return out


FETCHERS = {'greenhouse': _fetch_greenhouse, 'ashby': _fetch_ashby,
            'lever': _fetch_lever, 'workable': _fetch_workable}


def resolve(company):
    """Find which applicant tracking system a company publishes through.

    The slug is nearly always the name, lowercased and stripped. Try each
    board and keep whichever answers with postings — cheaper and more reliable
    than maintaining a directory by hand.
    """
    name = (company or '').strip()
    if not name:
        return {'ok': False, 'error': 'No company named.'}
    slug = re.sub(r'[^a-z0-9]', '', name.lower())
    if not slug:
        return {'ok': False, 'error': 'That is not a usable company name.'}

    tried = []
    for ats, fn in FETCHERS.items():
        try:
            jobs = fn(slug)
        except Exception:
            tried.append(ats)
            continue
        if jobs:
            return {'ok': True, 'company': name, 'ats': ats, 'slug': slug,
                    'count': len(jobs),
                    'summary': 'Found %s on %s with %d open postings.'
                               % (name, ATS_LABEL[ats], len(jobs))}
        tried.append(ats)

    return {'ok': False, 'error': 'No public job board found for "%s". Tried %s. '
                                  'Some companies use a system with no open '
                                  'listing, and those cannot be watched from '
                                  'here.' % (name, ', '.join(tried))}


# -------------------------------------------------------------------- profile

def get_profile():
    return {'ok': True, 'profile': _read()['profile']}


def set_profile(patch):
    with _lock:
        d = _read()
        for k, v in (patch or {}).items():
            if k in d['profile']:
                d['profile'][k] = v
        _write(d)
    return {'ok': True, 'summary': 'Profile saved.'}


def _profile_text(p):
    return ' '.join(x for x in (p.get('titles'), p.get('skills'),
                                p.get('wants'), p.get('resume')) if x)[:8000]


# ------------------------------------------------------------------- matching

def _embed(text):
    try:
        import jarvis_recall as recall
        return recall.embed(text) or None
    except Exception:
        return None


def _embed_many(texts):
    """Embed a whole board in one request.

    One at a time is roughly two seconds each here, which is fine for a single
    lookup and hopeless for a board: a hundred and fifty postings took longer
    than the scan's own timeout. The batch endpoint amortises the per-call
    overhead — the same reason the document indexer uses it."""
    if not texts:
        return []
    try:
        import jarvis_recall as recall
        got = recall.embed_many(texts)
        return got if len(got) == len(texts) else []
    except Exception:
        return []


def _cos(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sa = sb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        sa += x * x
        sb += y * y
    if sa <= 0 or sb <= 0:
        return 0.0
    return dot / ((sa ** 0.5) * (sb ** 0.5))


def _eligible(job, p):
    """Hard filters, applied before scoring.

    A ninety-percent match for a role in another state that I cannot take is
    not a good match, it is noise wearing a high number.
    """
    loc = (job.get('location') or '').lower()
    title = (job.get('title') or '').lower()

    for word in re.split(r'[,\n]+', (p.get('exclude') or '').lower()):
        word = word.strip()
        if word and (word in title or word in loc):
            return False, 'excluded by "%s"' % word

    wants = [w.strip().lower() for w in
             re.split(r'[,\n]+', p.get('locations') or '') if w.strip()]
    if wants:
        remote_ok = p.get('remote') and ('remote' in loc or 'anywhere' in loc)
        if not remote_ok and not any(w in loc for w in wants):
            return False, 'location'
    return True, ''


def score_job(job, profile, pvec=None):
    ok, why = _eligible(job, profile)
    if not ok:
        return 0.0, why

    pv = pvec if pvec is not None else _embed(_profile_text(profile))
    if not pv:
        return 0.0, 'no embedding available'

    jv = _embed((job.get('title') or '') + '. ' + (job.get('description') or '')[:2500])
    if not jv:
        return 0.0, 'no embedding available'
    return round(_cos(pv, jv), 3), ''


# --------------------------------------------------------------------- sweep

def scan(limit_boards=None):
    """One pass over the watchlist. Returns what was newly matched."""
    d = _read()
    p = d['profile']
    if not _profile_text(p).strip():
        return {'ok': False, 'error': 'No profile yet — he has nothing to match '
                                      'against. Fill in at least the titles and '
                                      'skills in Configuration.'}

    pvec = _embed(_profile_text(p))
    if not pvec:
        return {'ok': False, 'error': 'The embedding model is unreachable, so '
                                      'nothing can be scored. Is Ollama running?'}

    seen = set(d['seen'])
    boards = d['watch'][:limit_boards] if limit_boards else d['watch']
    threshold = float(p.get('threshold') or 0.55)

    fresh, checked, errors = [], 0, []
    # Why things were dropped. A bare "0 matches" is indistinguishable from a
    # broken scan; "94 wrong location, 56 excluded by your keywords" is a
    # result you can act on.
    rejected = {'location': 0, 'excluded': 0, 'below': 0, 'seen': 0}
    looked = 0
    _worker['scanning'] = True
    try:
        for board in boards:
            if _worker['stop']:
                break
            fn = FETCHERS.get(board.get('ats'))
            if not fn:
                continue
            try:
                postings = fn(board['slug'])
            except Exception as err:
                errors.append('%s: %s' % (board.get('company'), str(err)[:60]))
                continue
            checked += 1

            # Filter first, embed second. Everything the hard filters remove is
            # a posting we never pay to embed, and on a large board that is
            # most of them.
            candidates = []
            for job in postings:
                looked += 1
                if job['id'] in seen:
                    rejected['seen'] += 1
                    continue
                seen.add(job['id'])
                ok, why = _eligible(job, p)
                if not ok:
                    rejected['location' if why == 'location' else 'excluded'] += 1
                    continue
                candidates.append(job)

            candidates = candidates[:EMBED_CAP]
            if not candidates:
                continue          # nothing survived the filters; not a failure

            vecs = _embed_many([(j['title'] or '') + '. ' + (j['description'] or '')[:2000]
                                for j in candidates])
            if not vecs:
                errors.append('%s: could not embed' % board.get('company'))
                continue

            for job, jv in zip(candidates, vecs):
                sim = round(_cos(pvec, jv), 3)
                if sim < threshold:
                    rejected['below'] += 1
                    continue
                job.update({'company': board.get('company'), 'ats': board.get('ats'),
                            'score': sim, 'state': 'new', 'found': time.time()})
                job['description'] = job['description'][:1500]
                fresh.append(job)

            time.sleep(BOARD_GAP)
    finally:
        _worker['scanning'] = False

    # Best first, then only the best few. The rest stay in `seen` so they are
    # not offered again on the next sweep.
    fresh.sort(key=lambda j: -j['score'])
    dropped = max(0, len(fresh) - MAX_NEW_PER_SWEEP)
    fresh = fresh[:MAX_NEW_PER_SWEEP]

    with _lock:
        d = _read()
        have = {j['id'] for j in d['jobs']}
        d['jobs'] = [j for j in fresh if j['id'] not in have] + d['jobs']
        d['jobs'] = d['jobs'][:KEEP_JOBS]
        d['seen'] = list(seen)[-6000:]
        _write(d)

    _worker['last'] = time.time()
    _worker['checked'] = checked
    _worker['found'] = len(fresh)
    _worker['error'] = '; '.join(errors[:3])

    bits = []
    if rejected['location']:
        bits.append('%d in the wrong place' % rejected['location'])
    if rejected['excluded']:
        bits.append('%d excluded by your keywords' % rejected['excluded'])
    if rejected['below']:
        bits.append('%d too far from your profile' % rejected['below'])
    if rejected['seen']:
        bits.append('%d already seen' % rejected['seen'])

    line = 'Checked %d board(s), %d posting(s): %d new match(es).' % (
        checked, looked, len(fresh))
    if dropped:
        line += ' (%d more cleared the bar; kept the best.)' % dropped
    if bits:
        line += ' Skipped ' + ', '.join(bits) + '.'

    return {'ok': True, 'checked': checked, 'looked': looked, 'new': len(fresh),
            'rejected': rejected, 'errors': errors[:3], 'summary': line}


# -------------------------------------------------------------------- worker

def _loop():
    """The background hunt.

    Runs on its own thread precisely so it never touches a conversation. The
    page polls for what it found; it never announces anything itself.
    """
    while not _worker['stop']:
        try:
            scan()
        except Exception as err:
            _worker['error'] = str(err)[:120]
        for _ in range(POLL_SECONDS):
            if _worker['stop']:
                break
            time.sleep(1)
    _worker['running'] = False


def start():
    if _worker['running']:
        return {'ok': True, 'summary': 'Already hunting.'}
    d = _read()
    if not _profile_text(d['profile']).strip():
        return {'ok': False, 'error': 'Fill in the job profile first — without it '
                                      'there is nothing to match against.'}
    _worker['stop'] = False
    _worker['running'] = True
    t = threading.Thread(target=_loop, daemon=True)
    _worker['thread'] = t
    t.start()
    with _lock:
        d = _read()
        d['enabled'] = True
        _write(d)
    return {'ok': True, 'summary': 'Hunting. Matches will queue quietly.'}


def stop():
    _worker['stop'] = True
    _worker['running'] = False
    with _lock:
        d = _read()
        d['enabled'] = False
        _write(d)
    return {'ok': True, 'summary': 'Stopped. No further requests will be made.'}


def status():
    d = _read()
    counts = {}
    for j in d['jobs']:
        counts[j.get('state', 'new')] = counts.get(j.get('state', 'new'), 0) + 1
    return {'ok': True, 'running': _worker['running'], 'scanning': _worker['scanning'],
            'enabled': d['enabled'], 'last': _worker['last'],
            'checked': _worker['checked'], 'error': _worker['error'],
            'watching': len(d['watch']), 'counts': counts,
            'has_profile': bool(_profile_text(d['profile']).strip())}


# --------------------------------------------------------------------- jobs

def list_jobs(state=None, limit=60):
    d = _read()
    rows = [j for j in d['jobs'] if not state or j.get('state') == state]
    rows.sort(key=lambda j: (-j.get('score', 0), -j.get('found', 0)))
    return {'ok': True, 'count': len(rows), 'jobs': rows[:limit]}


def decide(job_id, verdict):
    """Interested, or pass. A pass is a labelled example, not a deletion."""
    if verdict not in ('interested', 'passed', 'applied', 'new'):
        return {'ok': False, 'error': 'Unknown verdict "%s".' % verdict}
    with _lock:
        d = _read()
        hit = None
        for j in d['jobs']:
            if j['id'] == job_id:
                j['state'] = verdict
                j['decided'] = time.time()
                hit = j
                break
        if not hit:
            return {'ok': False, 'error': 'No such job in the list.'}
        _write(d)
    return {'ok': True, 'job': hit,
            'summary': '%s — %s at %s.' % (verdict.title(), hit['title'], hit['company'])}


def watchlist():
    return {'ok': True, 'watch': _read()['watch']}


def watch_add(company):
    found = resolve(company)
    if not found.get('ok'):
        return found
    with _lock:
        d = _read()
        if any(w['slug'] == found['slug'] and w['ats'] == found['ats'] for w in d['watch']):
            return {'ok': True, 'summary': '%s is already on the list.' % found['company']}
        d['watch'].append({'company': found['company'], 'ats': found['ats'],
                           'slug': found['slug']})
        _write(d)
    return {'ok': True, 'summary': found['summary'] + ' Added to the watchlist.'}


def watch_remove(company):
    key = (company or '').strip().lower()
    with _lock:
        d = _read()
        before = len(d['watch'])
        d['watch'] = [w for w in d['watch']
                      if w['company'].lower() != key and w['slug'] != key]
        _write(d)
    return {'ok': True, 'summary': 'Removed.' if before != len(d['watch'])
                                   else 'Nothing on the list by that name.'}


def command(action, **kw):
    if action == 'status':
        return status()
    if action == 'start':
        return start()
    if action == 'stop':
        return stop()
    if action == 'scan':
        return scan()
    if action == 'list':
        return list_jobs(kw.get('state'))
    if action == 'decide':
        return decide(kw.get('id'), kw.get('verdict'))
    if action == 'profile':
        return get_profile()
    if action == 'set_profile':
        return set_profile(kw.get('profile') or {})
    if action == 'watchlist':
        return watchlist()
    if action == 'watch_add':
        return watch_add(kw.get('company'))
    if action == 'watch_remove':
        return watch_remove(kw.get('company'))
    if action == 'resolve':
        return resolve(kw.get('company'))
    return {'ok': False, 'error': 'Unknown jobs action "%s".' % action}
