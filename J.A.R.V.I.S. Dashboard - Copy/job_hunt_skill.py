"""
job_hunt_skill.py — the guided job hunt.

What this adds that was not already here
----------------------------------------
`jarvis_jobs.py` watches nine Greenhouse/Ashby/Lever/Workable boards. Those are
engineering ATSs and the stored profile targets barista and food service work,
so they will never carry a single relevant listing. That is the gap this fills:
`python-jobspy` reaches LinkedIn, Indeed, Google Jobs and ZipRecruiter, which is
where those roles actually are.

The second thing it adds is a *session*: three matches at a time, chosen by
number, and a fresh three ten minutes later if none of them are wanted.

What it deliberately does NOT add
---------------------------------
A second job store, a second dedup list, a second profile, or a second way to
fill a form. All four already exist and work:

  * `jarvis_jobs.json` holds `profile`, `jobs[]` and `seen[]` — 2,939 ids
    already rejected or applied. Scraped rows join that list, so a job passed
    over in the dashboard cannot resurface here, and vice versa.
  * `jarvis_apply.prepare()` fills a form and stops. Its submit refusal is
    deterministic — `FIELD_MAP` is an allowlist of fields it will type into and
    `_is_submit()` a denylist of controls it will not click — and it has been
    verified against a live form: 5 filled, 3 refused, 4 left for a human.

The apply step therefore routes through `prepare()` by default. `APPLY_ENGINE`
is the seam if you want something else there; see `_engine_prepare` for the
contract. Whatever goes in that slot inherits one rule that is not negotiable:
it opens the form and it stops. Nothing here presses submit.

Noise budget
------------
The retry loop runs on a daemon thread and never speaks. It queues a batch and
`pending()` reports it when asked, exactly like the existing hunt loop. Nothing
in this module announces anything on its own.
"""

import hashlib
import json
import os
import re
import threading
import time

import jarvis_jobs as jobs
import jarvis_apply as apply_stage

# The scoring model.
#
# Measured on two real barista listings and one Kubernetes control, against the
# stored resume:
#
#   phi3:mini           30 / 30 / 30   flat - scores a Kubernetes job the same
#                                      as a barista job. Useless for ranking.
#   llama3.2:3b         80 / 40 /  0   two near-identical barista jobs scored
#                                      80 and 40, and it justified the 40 with
#                                      "lack of prior specialty coffee
#                                      experience" for a resume whose second
#                                      line is a barista post at Disneyland.
#                                      It reads the resume and then contradicts
#                                      it, which is worse than not reading it.
#   gpt-oss:120b-cloud  92 / 85 /  5   correct, well spread, and its reasoning
#                                      cites the Disney barista role and the
#                                      commute rather than guessing.
#
# So the cloud model scores and the small local one is the offline fallback.
# The whole point of a match percentage is the ordering, and a model that
# returns 40% for everything makes the top three arbitrary.
MATCH_MODEL = os.environ.get('JARVIS_MATCH_MODEL', 'gpt-oss:120b-cloud')
MATCH_FALLBACK = os.environ.get('JARVIS_MATCH_FALLBACK', 'llama3.2:3b')
MATCH_TIMEOUT = 90

BATCH_SIZE = 3
RETRY_SECONDS = 600               # ten minutes, as asked
SCRAPE_TARGET = 40                # rows to pull before scoring; scoring is the slow half
DESC_CHARS = 4000                 # of a description worth reading

# How many survive the cheap pre-rank and get read by the scoring model.
# Ollama Cloud caps a session, so asking it about eighty listings would spend
# the allowance on rows that a free local embedding can already tell apart.
# Same two-stage shape as jarvis_apply.shortlist().
LLM_SHORTLIST = 10

# How long start() holds the call waiting for the first batch. A refill is
# around 40s once scoring runs in parallel; this leaves generous headroom.
START_WAIT = 150

SITES = ['linkedin', 'indeed', 'google', 'zip_recruiter']


# ---------------------------------------------------------------------------
#  1 · the profile
# ---------------------------------------------------------------------------

#  Not a new file. This is the shape of the `profile` block already inside
#  jarvis_jobs.json, written out so it can be checked and filled in. Anything
#  missing here weakens matching rather than breaking it, except `resume`,
#  which is the entire basis of the comparison.
PROFILE_TEMPLATE = {
    "resume": "Paste the full plain-text resume here. This is what every job "
              "description is compared against.",
    "titles": "barista, café attendant, food service, front of house",
    "locations": "Lakewood CA, Long Beach CA, Los Angeles CA",
    "remote": False,
    "skills": "espresso, milk steaming, POS, cash handling, inventory, opening/closing",
    "wants": "part-time or full-time, morning shifts, within 15 miles",
    "salary_floor": "",
    "exclude": "unpaid, commission-only, door-to-door",

    "first_name": "",
    "last_name": "",
    "full_name": "",
    "email": "",
    "phone": "",
    "location": "",
    "linkedin": "",
    "github": ""
}


def profile_template():
    """The schema, for filling in by hand. Writes nothing."""
    return {'ok': True, 'store': jobs.STORE, 'key': 'profile',
            'template': PROFILE_TEMPLATE,
            'summary': 'This is the profile block inside %s. Edit it there — a '
                       'separate file would be a second source of truth.'
                       % os.path.basename(jobs.STORE)}


def _profile():
    got = jobs.get_profile()
    return got.get('profile') or {} if isinstance(got, dict) else {}


# ---------------------------------------------------------------------------
#  2 · scraping — the part that is genuinely new
# ---------------------------------------------------------------------------

def _search_terms(profile):
    """What to actually type into four job boards.

    The profile stores titles as a comma-separated line because a human types
    it that way. jobspy wants one search term per call, so the line becomes a
    list and each is searched separately."""
    raw = (profile.get('titles') or '').strip()
    terms = [t.strip() for t in raw.split(',') if t.strip()]
    return terms[:4] or ['barista']


def _locations(profile):
    raw = (profile.get('locations') or '').strip()
    locs = [l.strip() for l in raw.split(',') if l.strip()]
    return locs[:2] or ['Los Angeles, CA']


SITE_TAG = {'linkedin': 'li', 'indeed': 'ind', 'google': 'gj',
            'zip_recruiter': 'zr', 'ziprecruiter': 'zr', 'glassdoor': 'gd'}


def _row_id(row):
    """A stable id in the same namespace as the existing store.

    `jarvis_jobs` uses "gh:reddit:8163425" — source, company, native id. Keeping
    that shape means the shared seen[] list stays readable and a scraped row can
    never collide with a board row."""
    raw = str(row.get('site') or 'web').lower()
    site = SITE_TAG.get(raw) or re.sub(r'[^a-z]', '', raw)[:3] or 'web'
    company = re.sub(r'[^a-z0-9]+', '-', str(row.get('company') or '').lower()).strip('-')[:24]
    native = str(row.get('id') or row.get('job_url') or '')
    if not native:
        return None
    native = re.sub(r'[^A-Za-z0-9]+', '', native)[-18:]
    return '%s:%s:%s' % (site, company or 'unknown', native)


def scrape(profile=None, want=SCRAPE_TARGET, hours=72):
    """Pull live postings from the four consumer boards.

    Returns rows in the same shape `jarvis_jobs` stores, so everything
    downstream — scoring, the dashboard list, decide() — works unchanged."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        return {'ok': False, 'error':
                'python-jobspy is not installed. pip install python-jobspy'}

    profile = profile or _profile()
    terms, locs = _search_terms(profile), _locations(profile)
    remote = bool(profile.get('remote'))

    rows, errors, notes = [], [], []
    per_call = max(5, want // max(1, len(terms) * len(locs)))

    def pull(term, loc, want_remote):
        try:
            return scrape_jobs(site_name=SITES, search_term=term, location=loc,
                               results_wanted=per_call, hours_old=hours,
                               is_remote=want_remote, country_indeed='USA',
                               description_format='markdown', verbose=0), None
        except Exception as err:
            # One board refusing is normal — LinkedIn and Indeed both
            # rate-limit aggressively. It is not a reason to lose the results
            # the others returned.
            return None, '%s/%s: %s' % (term, loc, str(err)[:100])

    for term in terms:
        for loc in locs:
            df, err = pull(term, loc, remote)
            if err:
                errors.append(err)
                continue

            if remote and (df is None or not len(df)):
                # `remote` defaults to True in the stored profile and stays
                # true long after it stops being true of the search. A barista
                # shift is not a remote job, so a remote-only filter returns an
                # empty page and looks exactly like "there are no jobs". Retry
                # once without it and say so, rather than reporting nothing.
                df, err = pull(term, loc, False)
                if df is not None and len(df):
                    notes.append('"%s" in %s returned nothing as remote-only, so '
                                 'the remote filter was dropped for it' % (term, loc))
            if df is None or not len(df):
                continue
            for _, r in df.iterrows():
                row = {k: (None if _isnan(v) else v) for k, v in r.to_dict().items()}
                jid = _row_id(row)
                if not jid:
                    continue
                rows.append({
                    'id': jid,
                    'title': str(row.get('title') or '').strip(),
                    'company': str(row.get('company') or '').strip(),
                    'location': str(row.get('location') or loc).strip(),
                    'url': str(row.get('job_url') or '').strip(),
                    'posted': str(row.get('date_posted') or ''),
                    'description': str(row.get('description') or '')[:20000],
                    'ats': str(row.get('site') or 'web'),
                    'found': int(time.time()),
                    'state': 'new',
                })

    unique = {}
    for r in rows:
        unique.setdefault(r['id'], r)
    return {'ok': True, 'rows': list(unique.values()), 'errors': errors,
            'notes': notes, 'searched': len(terms) * len(locs)}


def _isnan(v):
    try:
        return v != v            # pandas NaN, without importing pandas
    except Exception:
        return False


# ---------------------------------------------------------------------------
#  3 · matching — one number per listing, from the local model
# ---------------------------------------------------------------------------

MATCH_SYSTEM = (
    'You score one job posting against one resume. You are blunt: a gap is a '
    'gap and an unrelated job scores low no matter how pleasant it sounds.\n'
    'Reply with JSON only: {"score": 0-100, "why": "one short sentence"}\n'
    'Score on whether this person could actually do this job and would be '
    'called for an interview. Wrong field, required licence they lack, or a '
    'location they cannot reach all mean a score below 25.'
)


def score(job, profile=None, model=None):
    """0-100 for one listing. Falls back to the embedding score already in
    jarvis_jobs if the local model is unreachable, because a hunt that stops
    dead because Ollama is down is worse than one ranked slightly worse."""
    profile = profile or _profile()
    resume = (profile.get('resume') or '').strip()
    if not resume:
        return 0, 'no resume in the profile to compare against'

    prompt = ('RESUME\n%s\n\nJOB\n%s at %s (%s)\n\n%s'
              % (resume[:6000], job.get('title', ''), job.get('company', ''),
                 job.get('location', ''), (job.get('description') or '')[:DESC_CHARS]))

    tried = [m for m in (model or MATCH_MODEL, None if model else MATCH_FALLBACK) if m]
    out = 'not attempted'
    for candidate in tried:
        ok, out = apply_stage._ask(candidate, MATCH_SYSTEM, prompt,
                                   timeout=MATCH_TIMEOUT, want_json=True)
        if ok and isinstance(out, dict):
            try:
                return max(0, min(100, int(float(out.get('score', 0))))), \
                       str(out.get('why') or '')[:200]
            except Exception:
                pass
        # The cloud model is the one that runs out; dropping to the local one
        # ranks worse but keeps the hunt moving.

    # score_job returns (score, why) — a tuple, not a number.
    try:
        val, why = jobs.score_job(job, profile)
        return int(round(float(val) * 100)), \
               (why or 'scored by embedding; no scoring model answered')
    except Exception:
        return 0, 'could not be scored: %s' % str(out)[:120]


# ---------------------------------------------------------------------------
#  4 · the session — three at a time, ten minutes apart
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_state = {
    'running': False, 'stop': False, 'thread': None,
    'batch': [], 'batch_at': 0, 'round': 0,
    'pool': [],                 # scored, unseen, not yet offered
    'next_at': 0, 'error': '', 'note': '', 'chosen': None,
    # Never resets. `round` restarts at 1 with every session, so a second
    # hunt's first batch collided with the first hunt's card still on
    # screen and the pop-up was suppressed as a duplicate. The page keys
    # off this instead; `round` stays per-session for what he is told.
    'seq': 0,
}


def _face_key(job):
    """What a listing looks like to a person, as a storable key.

    Dedup by id alone is not enough. A board re-posts the same role and gives
    it a new id, so "Starbucks Barista, Ralphs, Los Angeles" comes back a week
    after it was turned down and reads as a fresh match. Turning one down has
    to mean turning that job down, not that copy of it.

    Prefixed so it can live in the same seen[] list as the ids without ever
    being mistaken for one."""
    face = '|'.join(str(job.get(k) or '').strip().lower()
                    for k in ('title', 'company', 'location'))
    return 'face:' + hashlib.sha1(face.encode('utf-8')).hexdigest()[:16]


def _seen_ids():
    d = jobs._read()
    return set(d.get('seen') or []) | {j.get('id') for j in (d.get('jobs') or [])}


def _remember(rows, as_seen=True):
    """Fold rows into the shared store. Offered-and-declined goes to seen[] so
    it can never come round again; that is the whole point of sharing the file
    rather than keeping a private one."""
    with _lock:
        d = jobs._read()
        seen = set(d.get('seen') or [])
        known = {j.get('id') for j in d.get('jobs') or []}
        for r in rows:
            if as_seen:
                seen.add(r['id'])
                seen.add(_face_key(r))      # and every future copy of it
            elif r['id'] not in known:
                d.setdefault('jobs', []).append(r)
        d['seen'] = sorted(seen)
        jobs._write(d)


def _refill(profile):
    """Scrape, drop anything already seen, score what is left."""
    got = scrape(profile)
    if not got.get('ok'):
        _state['error'] = got.get('error') or 'scrape failed'
        return []
    _state['error'] = ''
    if got.get('errors'):
        _state['note'] = '%d of %d searches were refused by the board' % (
            len(got['errors']), got.get('searched') or 0)

    seen = _seen_ids()
    fresh = [r for r in got['rows']
             if r['id'] not in seen and _face_key(r) not in seen]
    if not fresh:
        return []

    #  Two stages, for the same reason jarvis_apply.shortlist() has two.
    #
    #  Stage one is a local embedding over everything scraped: one batched call
    #  to nomic-embed-text, free, and easily good enough to tell a barista job
    #  from an assistant-manager job. Stage two is the scoring model, which is
    #  accurate, capped by Ollama Cloud, and worth spending only on rows that
    #  already look plausible.
    #
    #  Deliberately NOT jobs.score_job here: its _eligible() gate hard-matches
    #  the profile's `locations` line as a substring, and that line reads
    #  "los angeles". jobspy searches a radius, so Pasadena and Costa Mesa come
    #  back legitimately and would all be discarded as "location". The scoring
    #  model weighs distance properly — it cited proximity unprompted.
    ranked = fresh
    try:
        pvec = jobs._embed(jobs._profile_text(profile))
        vecs = jobs._embed_many([(r.get('title') or '') + '. '
                                 + (r.get('description') or '')[:2500] for r in fresh])
        if pvec and vecs:
            for r, v in zip(fresh, vecs):
                r['_near'] = jobs._cos(pvec, v)
            ranked = sorted(fresh, key=lambda r: -r.get('_near', 0))
    except Exception:
        pass                     # no embeddings: fall through in scrape order

    #  Scored in parallel. Ten listings at four seconds each is forty seconds
    #  of a person waiting for three lines of text, and the calls are entirely
    #  independent — the only reason it was sequential is that it was written
    #  that way first. Four workers rather than ten because the cloud tier
    #  rate-limits, and the gain from four to ten is small next to the risk of
    #  being throttled mid-hunt.
    short = ranked[:LLM_SHORTLIST]
    scored = []

    def rate(row):
        if _state['stop']:
            return None
        pct, why = score(row, profile)
        #  Stored 0-1, like every other score in jarvis_jobs.json.
        #
        #  The scoring model answers 0-100 and that went straight into the
        #  record, but the dashboard's jobs pane renders a score as
        #  Math.round(score * 100) because score_job() returns a cosine. An
        #  88% match was therefore displayed as 8800%. One scale, everywhere;
        #  _card() converts back for the pop-up.
        row['score'] = round(pct / 100.0, 3)
        row['why'] = why
        row.pop('_near', None)
        return row

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            scored = [r for r in pool.map(rate, short) if r]
    except Exception:
        scored = [r for r in (rate(row) for row in short) if r]

    scored.sort(key=lambda r: -r.get('score', 0))
    return scored


def _face(job):
    """What a listing looks like to a person reading three lines."""
    return (str(job.get('title') or '').strip().lower(),
            str(job.get('company') or '').strip().lower(),
            str(job.get('location') or '').strip().lower())


def _take_batch():
    """Three, none of which read as the same job.

    Big boards carry the same role posted several times — "Starbucks Barista,
    Ralphs, Los Angeles" came back twice in one batch, which spends a third of
    the choice on something already offered. They keep different ids and may be
    different stores, so a look-alike is not dropped, only held back for a
    later batch where it stands on its own."""
    batch, held, faces = [], [], set()
    for job in _state['pool']:
        if len(batch) >= BATCH_SIZE:
            held.append(job)
            continue
        face = _face(job)
        if face in faces:
            held.append(job)
            continue
        faces.add(face)
        batch.append(job)

    _state['pool'] = held
    _state['batch'] = batch
    _state['batch_at'] = int(time.time())
    _state['round'] += 1
    _state['seq'] += 1
    return batch


def _loop(profile):
    """The retry loop. Its own thread, so a ten-minute wait never blocks the
    dashboard or a conversation."""
    while not _state['stop']:
        if not _state['pool']:
            _state['pool'] = _refill(profile)
        if not _state['pool']:
            _state['note'] = (_state['error']
                              or 'nothing unseen came back from any board')
            break
        _take_batch()

        # Wait for a choice, or for the retry to come round.
        _state['next_at'] = int(time.time()) + RETRY_SECONDS
        for _ in range(RETRY_SECONDS):
            if _state['stop'] or not _state['batch']:
                break
            time.sleep(1)
        if _state['stop']:
            break
        if _state['batch']:
            # Nobody answered. Treat silence as a decline so the same three do
            # not come back for ever.
            decline_all(_auto=True)
    _state['running'] = False


def start_job_hunt_workflow(blocking=False):
    """Entry point. Jarvis calls this on "start my job hunt".

    Returns as soon as the first batch is ready, so the caller can show it.
    `blocking=True` runs the console version instead, for a terminal.
    """
    profile = _profile()
    if not (profile.get('resume') or '').strip():
        return {'ok': False, 'error':
                'There is no resume in the profile, so nothing can be matched. '
                'Fill the profile block in %s first.' % os.path.basename(jobs.STORE)}

    #  A second start while one is running joins the first rather than
    #  returning early.
    #
    #  Observed live: gpt-oss fired three start calls in parallel. The first
    #  blocked for the scrape; the other two returned instantly with an empty
    #  batch, the last empty one won, and he asked "Option 1, 2, or 3?" without
    #  ever listing the three. Every caller has to wait for the same batch, or
    #  a duplicate call silently erases the answer.
    already = _state['running']

    if not already:
        _state.update({'stop': False, 'running': True, 'round': 0,
                       'batch': [], 'pool': [], 'error': '', 'note': '',
                       'chosen': None})

        if blocking:
            return _console(profile)

        t = threading.Thread(target=_loop, args=(profile,), daemon=True)
        _state['thread'] = t
        t.start()
    elif blocking:
        return pending()

    #  Hold the call until the first batch exists.
    #
    #  Returning early is worse than waiting: the caller gets "Hunting." with
    #  nothing to read out, and has to be told to ask again — which it will not
    #  reliably do. A scrape plus a scored shortlist is around forty seconds,
    #  so this waits comfortably past that and only gives up on something
    #  genuinely stuck.
    deadline = time.time() + START_WAIT
    while time.time() < deadline:
        if _state['batch'] or not _state['running']:
            break
        time.sleep(0.5)

    out = pending()
    if not out['batch'] and _state['running']:
        out['summary'] = ('Still searching — the boards were slow. Call pending in a '
                          'few seconds for the three matches; do not start again, '
                          'the search is already running.')
    return out


def pending():
    """What is on the table right now. Polled; never announced.

    Reports the LAST OUTCOME as well as the current batch, because the two are
    not distinguishable otherwise and that cost a real application. Choosing a
    job ends the hunt by design, so pending afterwards returns an empty batch —
    which read as a dead session, and he told Zero "I'm unable to proceed"
    about a form that was open in front of him with his details already in it.
    An empty batch means nothing on its own; what happened to the last one is
    the part worth saying."""
    out = {'ok': True, 'running': _state['running'], 'round': _state['round'],
           'seq': _state['seq'],
           'batch': [_card(j) for j in _state['batch']],
           'waiting': max(0, _state['next_at'] - int(time.time())) if _state['batch'] else 0,
           'pool': len(_state['pool']), 'error': _state['error'],
           'note': _state['note'],
           'summary': _summarise()}
    if _state.get('chosen'):
        out['chosen'] = _state['chosen']
        out['summary'] = (
            'The hunt is finished because a job was already chosen: %s at %s. Its '
            'application form was opened and filled. That is a COMPLETED job hunt, '
            'not a failed one — do not report being unable to proceed. If he wants '
            'more listings, start a new hunt.'
            % (_state['chosen'].get('title') or 'a listing',
               _state['chosen'].get('company') or 'the employer'))
    return out


def _card(j):
    return {'id': j['id'], 'title': j.get('title', ''), 'company': j.get('company', ''),
            'location': j.get('location', ''),
            # stored 0-1; the card and the model both want a percentage
            'score': int(round((j.get('score') or 0) * 100)),
            'why': j.get('why', ''), 'url': j.get('url', ''), 'source': j.get('ats', '')}


def _summarise():
    if not _state['batch']:
        return _state['note'] or ('Hunting.' if _state['running'] else 'Not running.')
    lines = ['Batch %d — three matches:' % _state['round']]
    for i, j in enumerate(_state['batch'], 1):
        lines.append('  %d. %s — %s, %s (%d%%)'
                     % (i, j.get('title', ''), j.get('company', ''),
                        j.get('location', ''), j.get('score', 0)))
    lines.append('Option 1, 2, 3, or decline all?')
    return '\n'.join(lines)


def decline_all(_auto=False):
    """Bank these three as seen and hand back the next three.

    Returning the moment the old batch is banked has the same fault `start`
    had: the caller gets "fetching three more" with nothing to show, and has to
    know to ask again. So a decline from outside waits for the replacements and
    returns them, which makes one call one complete exchange.

    The automatic decline must NOT wait — it is called from inside the loop
    thread, and waiting there would be waiting on itself."""
    batch = _state['batch']
    if not batch:
        return {'ok': False, 'error': 'There is no batch on the table.'}
    _remember(batch, as_seen=True)
    _state['batch'] = []
    declined = [j['id'] for j in batch]
    _state['note'] = ('No answer within ten minutes, so those three were set aside.'
                      if _auto else 'Set aside.')

    if _auto:
        return {'ok': True, 'summary': _state['note'], 'declined': declined}

    deadline = time.time() + START_WAIT
    while time.time() < deadline:
        if _state['batch'] or not _state['running']:
            break
        time.sleep(0.5)

    out = pending()
    out['declined'] = declined
    if not out['batch']:
        out['summary'] = _state['note'] + ' ' + (
            'Still fetching the next three — call pending shortly.'
            if _state['running'] else 'Nothing unseen is left to offer.')
    return out


def choose(option):
    """Pick 1, 2 or 3 and hand it to the form engine."""
    batch = _state['batch']
    if not batch:
        return {'ok': False, 'error': 'There is no batch on the table.'}
    try:
        idx = int(str(option).strip()) - 1
    except Exception:
        return {'ok': False, 'error': 'Say 1, 2, 3, or decline all.'}
    if idx < 0 or idx >= len(batch):
        return {'ok': False, 'error': 'There are only %d on the table.' % len(batch)}

    picked = batch[idx]
    rest = [j for k, j in enumerate(batch) if k != idx]

    # The chosen one joins jobs[] so the dashboard can see it; the other two go
    # straight to seen[].
    _remember([picked], as_seen=False)
    _remember(rest, as_seen=True)
    _state['batch'] = []

    #  Choosing ends the hunt.
    #
    #  Without this the loop treats an emptied batch as a decline and offers
    #  three more, so picking a job would be answered by another batch while
    #  the form is still opening. The brief is explicit: every ten minutes
    #  until he picks one or says stop.
    _state['stop'] = True
    _state['running'] = False
    _state['note'] = 'Hunt ended — you picked one.'
    _state['chosen'] = _card(picked)

    out = APPLY_ENGINE(picked)
    out.setdefault('job', _card(picked))
    return out


def stop():
    _state['stop'] = True
    _state['running'] = False
    return {'ok': True, 'summary': 'Hunt stopped. Nothing further will be fetched.'}


# ---------------------------------------------------------------------------
#  5 · the apply engine — the one seam
# ---------------------------------------------------------------------------

#  The contract: take a job record, open its form, fill what can be filled
#  safely, and STOP with the browser open. Return {'ok', 'summary', ...}.
#
#  Whatever sits here must not click submit. The default does not, structurally
#  rather than by instruction: jarvis_apply fills only the fields named in
#  FIELD_MAP and refuses any control whose label matches _is_submit(), so there
#  is no code path from here to a submitted application. An LLM-driven agent
#  told "please stop before submitting" is a different and weaker promise, and
#  swapping one in is a decision to make deliberately, not by default.

def _engine_prepare(job):
    """Default: draft the packet if there isn't one, then open and fill the form.

    Both steps, because prepare() refuses to open anything without a packet on
    disk — it wants a resume.md to attach, and returns "Draft the packet first"
    otherwise. Wiring choose() straight to prepare() therefore failed on every
    freshly scraped job, and the model, handed that refusal, improvised: it
    offered to write a cover letter itself and produced one addressed from
    "[Your Name]" at "[Phone]".

    Choosing a job has to mean the form opens filled. Anything less hands the
    work back to a model that will invent the parts it does not have."""
    d = jobs._read()
    if not any(j.get('id') == job['id'] for j in d.get('jobs') or []):
        _remember([job], as_seen=False)

    notes, invented = [], []

    packet = os.path.join(apply_stage.PACKET_DIR, apply_stage._slug(job))
    if not os.path.isfile(os.path.join(packet, 'resume.md')):
        drafted = apply_stage.tailor(job['id'])
        if not drafted.get('ok'):
            return {'ok': False, 'error':
                    'Could not draft the application packet, so the form was not '
                    'opened: %s' % drafted.get('error')}
        notes.append(drafted.get('summary') or '')
        invented = drafted.get('unsupported_claims') or []

    out = apply_stage.prepare(job['id'], headless=False)
    if not isinstance(out, dict):
        return {'ok': False, 'error': str(out)}
    if not out.get('ok'):
        return out

    out['packet_notes'] = notes
    out['unsupported_claims'] = invented
    out['summary'] = '\n'.join(filter(None, [
        '\n'.join(notes),
        out.get('summary') or '',
        'The form is open and filled as far as it safely can be. Nothing was '
        'submitted — read it and send it yourself.',
        # Never quietly. The standing rule is that nothing on an application is
        # invented, and a claim the master resume does not support is exactly
        # the thing a person skims past on a form that looks finished.
        ('BEFORE YOU SEND IT — these appear in the draft and are NOT in the '
         'master resume: ' + ', '.join(invented[:12])) if invented else '',
    ]))
    return out


APPLY_ENGINE = _engine_prepare


# ---------------------------------------------------------------------------
#  6 · console runner, for driving it from a terminal
# ---------------------------------------------------------------------------

def _console(profile):
    print('\n  Job hunt — Ctrl+C to stop.\n')
    try:
        while True:
            if not _state['pool']:
                print('  scraping…')
                _state['pool'] = _refill(profile)
            if not _state['pool']:
                print('  nothing unseen came back. ' + (_state['error'] or ''))
                return {'ok': True, 'summary': 'No unseen matches.'}
            _take_batch()
            print('\n' + _summarise() + '\n')
            answer = input('  > ').strip().lower()
            if answer in ('1', '2', '3'):
                out = choose(answer)
                print('  ' + (out.get('summary') or out.get('error') or ''))
                return out
            if answer in ('q', 'quit', 'stop'):
                return stop()
            decline_all()
            print('  ten minutes…')
            time.sleep(RETRY_SECONDS)
    except KeyboardInterrupt:
        return stop()


# ---------------------------------------------------------------------------
#  7 · dispatcher — same shape as every other module here
# ---------------------------------------------------------------------------

def command(action, **kw):
    """Routed by serve.py as POST /api/hunt/command."""
    if action == 'start':
        return start_job_hunt_workflow()
    if action == 'pending':
        return pending()
    if action == 'choose':
        return choose(kw.get('option'))
    if action in ('decline', 'decline_all', 'next_batch'):
        return decline_all()
    if action == 'stop':
        return stop()
    if action == 'profile_template':
        return profile_template()
    if action == 'scrape':
        return scrape()
    return {'ok': False, 'error': 'Unknown action "%s". Known: start, pending, '
                                  'choose, decline, stop, profile_template, '
                                  'scrape.' % action}


if __name__ == '__main__':
    start_job_hunt_workflow(blocking=True)
