"""
Durable memory and reminders for J.A.R.V.I.S.

Why this moved off the browser
------------------------------
Both of these lived in localStorage, which has two problems. It is per-origin
and per-browser, so what he knew about you vanished if you opened the dashboard
somewhere else or cleared site data. And a timer held in a page is not a
reminder at all — it dies the moment the tab reloads, which is exactly when you
most need it to survive.

So the server keeps them, in a single JSON file next to itself. The page still
does the announcing, because the page is the thing that can speak; it just asks
what is due rather than trying to remember on its own.

Memory is stored with timestamps and searched rather than recited. Dumping
every fact into the system prompt works while there are ten of them and stops
working somewhere before a hundred.
"""

import json
import math
import os
import re
import struct
import threading
import time

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jarvis_state.json')

_lock = threading.Lock()

# Facts beyond this many go in but stop being volunteered wholesale; the model
# has to search for them instead.
PROMPT_BUDGET = 40


def _read():
    try:
        with open(STORE, 'r', encoding='utf-8') as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    d.setdefault('memories', [])
    d.setdefault('reminders', [])
    return d


def _write(d):
    tmp = STORE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, STORE)


def _next_id(rows):
    return max([r.get('id', 0) for r in rows] or [0]) + 1


# ------------------------------------------------------------------ memories

def remember(fact):
    fact = (fact or '').strip()
    if not fact:
        return {'ok': False, 'error': 'No fact supplied.'}

    with _lock:
        d = _read()
        low = fact.lower()
        for m in d['memories']:
            if m['text'].lower() == low:
                return {'ok': True, 'duplicate': True,
                        'summary': 'Already remembered.'}

    # Word-for-word matching only catches the easy half. "He lives in LA" and
    # "Zero is based in Los Angeles" are the same fact, and storing both means
    # reciting both forever.
    sim, near_id, near_text = _closest(fact)
    if sim >= SIM_SAME:
        return {'ok': True, 'duplicate': True, 'similarity': round(sim, 3),
                'summary': 'Already remembered, in different words: "%s"' % near_text}

    with _lock:
        d = _read()
        row = {'id': _next_id(d['memories']), 'text': fact, 'at': time.time()}
        d['memories'].append(row)
        _write(d)
        n = len(d['memories'])

    _index_fact(row['id'], fact)

    # Related but not the same. Often that is simply more detail; sometimes it
    # is a correction, and only the user knows which. Say so rather than
    # quietly keeping both or quietly dropping one.
    if sim >= SIM_NEAR:
        return {'ok': True, 'related': near_text, 'similarity': round(sim, 3),
                'summary': 'Stored. Note that it sits close to something already '
                           'remembered: "%s". If the new one replaces it rather than '
                           'adding to it, forget the old one.' % near_text}

    return {'ok': True, 'summary': 'Stored. %d facts now remembered.' % n}


def forget(match):
    match = (match or '').strip().lower()
    if not match:
        return {'ok': False, 'error': 'Nothing to match on.'}
    with _lock:
        d = _read()
        before = len(d['memories'])
        gone = [m['id'] for m in d['memories'] if match in m['text'].lower()]
        d['memories'] = [m for m in d['memories'] if match not in m['text'].lower()]
        removed = before - len(d['memories'])
        _write(d)
    # An orphaned vector would keep surfacing a fact that no longer exists.
    _drop_vectors(gone)
    return {'ok': True, 'summary': ('Forgotten (%d removed).' % removed) if removed
                                   else 'Nothing matched that.'}


def search_memory(q):
    """Meaning first, words second.

    Word overlap cannot find "where does he live" from "Zero is based in Los
    Angeles" — there is not a word in common. Vectors can. The overlap search
    below stays as the fallback for when the embedding model is unreachable."""
    hits = relevant(q, 12)
    if hits:
        return {'ok': True, 'summary': chr(10).join('- ' + t for _, _, t in hits)}

    d = _read()
    terms = [w for w in re.findall(r'[a-z0-9]+', (q or '').lower()) if len(w) > 2]
    if not terms:
        return {'ok': True, 'summary': _recite(d['memories'])}

    scored = []
    for m in d['memories']:
        text = m['text'].lower()
        hits = sum(1 for t in terms if t in text)
        if hits:
            scored.append((hits, m))
    scored.sort(key=lambda x: -x[0])
    rows = [m for _, m in scored[:12]]
    if not rows:
        # "What do you know about me" is a question about the memory itself and
        # matches no individual fact in it. Answering a deliberate search with
        # silence is worse than showing what is actually held.
        if not d['memories']:
            return {'ok': True, 'summary': 'Nothing remembered yet.'}
        return {'ok': True, 'summary': 'Nothing matched that closely. Everything '
                                       'remembered:\n' + _recite(d['memories'])}
    return {'ok': True, 'summary': '\n'.join('- ' + m['text'] for m in rows)}


def _recite(rows):
    if not rows:
        return 'Nothing remembered yet.'
    return '\n'.join('- ' + m['text'] for m in rows[-PROMPT_BUDGET:])


def memories_for_prompt():
    """What gets folded into the system prompt each turn."""
    d = _read()
    rows = d['memories']
    text = '\n'.join('- ' + m['text'] for m in rows[-PROMPT_BUDGET:])
    return {'count': len(rows), 'text': text,
            'truncated': len(rows) > PROMPT_BUDGET}


def clear_memories():
    with _lock:
        d = _read()
        gone = [m['id'] for m in d['memories']]
        d['memories'] = []
        _write(d)
    _drop_vectors(gone)
    return {'ok': True, 'summary': 'Memory cleared.'}


# ----------------------------------------------------------------- reminders

_UNITS = {'second': 1, 'seconds': 1, 'sec': 1, 'secs': 1,
          'minute': 60, 'minutes': 60, 'min': 60, 'mins': 60,
          'hour': 3600, 'hours': 3600, 'hr': 3600, 'hrs': 3600,
          'day': 86400, 'days': 86400, 'week': 604800, 'weeks': 604800}


def parse_when(text):
    """Turn 'in 10 minutes', 'at 7pm', 'tomorrow at 9' into an epoch time.

    Deliberately small: these are the shapes people actually say out loud, and
    a full natural-language date parser is a dependency this project does not
    need.
    """
    t = (text or '').lower().strip()
    now = time.time()

    m = re.search(r'in\s+(\d+(?:\.\d+)?)\s*([a-z]+)', t)
    if m and m.group(2) in _UNITS:
        return now + float(m.group(1)) * _UNITS[m.group(2)], None

    m = re.search(r'(\d+(?:\.\d+)?)\s*([a-z]+)\s+from\s+now', t)
    if m and m.group(2) in _UNITS:
        return now + float(m.group(1)) * _UNITS[m.group(2)], None

    m = re.search(r'at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == 'pm' and hour < 12:
            hour += 12
        if ampm == 'am' and hour == 12:
            hour = 0
        lt = time.localtime(now)
        target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0,
                              0, 0, -1))
        if 'tomorrow' in t or target <= now:
            target += 86400
        return target, None

    if 'tomorrow' in t:
        lt = time.localtime(now + 86400)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1)), None

    return None, ('Could not read a time from "%s". Try "in 20 minutes", "at 7pm", '
                  'or "tomorrow at 9".' % text)


def add_reminder(text, when):
    due, err = parse_when(when)
    if err:
        return {'ok': False, 'error': err}
    body = (text or '').strip() or 'reminder'
    with _lock:
        d = _read()
        row = {'id': _next_id(d['reminders']), 'text': body, 'due': due,
               'created': time.time(), 'fired': False}
        d['reminders'].append(row)
        _write(d)
    return {'ok': True, 'id': row['id'], 'due': due,
            'summary': 'Reminder set for %s: %s'
                       % (time.strftime('%a %H:%M', time.localtime(due)), body)}


def list_reminders(include_done=False):
    d = _read()
    rows = [r for r in d['reminders'] if include_done or not r.get('fired')]
    rows.sort(key=lambda r: r['due'])
    if not rows:
        return {'ok': True, 'summary': 'No reminders set.', 'items': []}
    return {'ok': True, 'items': rows,
            'summary': '\n'.join(
                '#%d %s — %s' % (r['id'],
                                 time.strftime('%a %H:%M', time.localtime(r['due'])),
                                 r['text']) for r in rows)}


def cancel_reminder(which):
    """Cancel by id, or by a distinctive phrase from the text."""
    key = str(which or '').strip().lower()
    if not key:
        return {'ok': False, 'error': 'Nothing to cancel.'}
    with _lock:
        d = _read()
        before = len(d['reminders'])
        if key.lstrip('#').isdigit():
            wanted = int(key.lstrip('#'))
            d['reminders'] = [r for r in d['reminders'] if r['id'] != wanted]
        else:
            d['reminders'] = [r for r in d['reminders'] if key not in r['text'].lower()]
        removed = before - len(d['reminders'])
        _write(d)
    return {'ok': True, 'summary': ('Cancelled %d.' % removed) if removed
                                   else 'No reminder matched that.'}


def due_reminders():
    """Reminders whose time has passed, marked fired so they announce once.

    The page polls this. Anything that came due while the dashboard was closed
    still surfaces on the next poll, which is the whole point of moving them
    off the browser.
    """
    now = time.time()
    with _lock:
        d = _read()
        ready = [r for r in d['reminders'] if not r.get('fired') and r['due'] <= now]
        for r in ready:
            r['fired'] = True
            r['fired_at'] = now
        if ready:
            # keep the file from growing without bound
            d['reminders'] = [r for r in d['reminders']
                              if not r.get('fired') or now - r.get('fired_at', now) < 86400]
            _write(d)
    return {'ok': True, 'due': [{'id': r['id'], 'text': r['text'],
                                 'late_by': int(now - r['due'])} for r in ready]}




# ==========================================================================
#  Semantic memory
#
#  Reciting every fact into the system prompt works at ten and stops working
#  somewhere before a hundred. The cost is not only tokens: a model given
#  forty unrelated facts attends to all of them badly, and the prompt cache
#  breaks every time one is added.
#
#  So facts are embedded and retrieved against what was actually said. The
#  JSON file stays the record of truth — readable, editable, backed up by
#  copying one file — and the vectors live beside the document index because
#  that is where the embedding machinery already is.
#
#  Everything here degrades rather than fails. If Ollama is not running there
#  are no vectors, retrieval falls back to reciting, and nothing breaks.
# ==========================================================================

VEC_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jarvis_recall.db')

#  Thresholds, measured against nomic-embed-text on this machine rather than
#  guessed. The measurement is worth writing down because it changes the
#  design:
#
#      "lives in Los Angeles"  vs "based in LA"        0.916   same fact
#      "runs on port 8123"     vs "now runs on 9000"   0.887   CONTRADICTION
#
#  Three hundredths apart. Similarity cannot tell a restatement from a
#  correction, and anything that merges on similarity alone will eventually
#  delete something true. So merging happens only where the texts are all but
#  identical, and everything else is surfaced for a judgement rather than
#  acted on. Deciding which of two plausible statements is current is a job
#  for the model or the user, not for a cosine.
#
#  Unrelated pairs measured 0.31-0.37, genuinely related ones 0.57 and up,
#  which is what makes the retrieval floor safe to set where it is.

SIM_SAME = 0.95        # near-identical wording; safe to collapse
SIM_NEAR = 0.80        # close enough to mention when storing
SIM_CAND = 0.70        # worth putting in front of someone during a sweep
SIM_FLOOR = 0.45       # below this it is not about the same subject at all
RECALL_N = 8


_vec_ready = None      # None = not tried yet; True/False once known


def _vdb():
    import sqlite3
    con = sqlite3.connect(VEC_DB, timeout=30)
    con.execute('PRAGMA busy_timeout=15000')
    con.execute("""CREATE TABLE IF NOT EXISTS facts (
        fact_id INTEGER PRIMARY KEY,
        text TEXT,
        vec BLOB,
        at REAL
    )""")
    con.commit()
    return con


def _embed(text):
    """A vector, or None. Never raises — memory must survive Ollama being off."""
    global _vec_ready
    try:
        import jarvis_recall as recall
        v = recall.embed(text)
        _vec_ready = bool(v)
        return v or None
    except Exception:
        _vec_ready = False
        return None


def _pack(vec):
    return struct.pack('%df' % len(vec), *vec)


def _unpack(blob):
    return list(struct.unpack('%df' % (len(blob) // 4), blob))


def _cos(a, b):
    """Pure Python on purpose. A few hundred facts is microseconds, and it
    keeps this module free of numpy, which the rest of it does not need."""
    if len(a) != len(b):
        return 0.0                      # embedded with a different model
    dot = sa = sb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        sa += x * x
        sb += y * y
    if sa <= 0 or sb <= 0:
        return 0.0
    return dot / math.sqrt(sa * sb)


def _index_fact(fact_id, text):
    vec = _embed(text)
    if not vec:
        return False
    con = _vdb()
    try:
        con.execute('REPLACE INTO facts (fact_id, text, vec, at) VALUES (?,?,?,?)',
                    (fact_id, text, _pack(vec), time.time()))
        con.commit()
    finally:
        con.close()
    return True


def _drop_vectors(fact_ids):
    if not fact_ids:
        return
    con = _vdb()
    try:
        con.executemany('DELETE FROM facts WHERE fact_id=?', [(i,) for i in fact_ids])
        con.commit()
    finally:
        con.close()


def _stored_vectors():
    con = _vdb()
    try:
        return [(fid, txt, _unpack(blob))
                for fid, txt, blob in con.execute('SELECT fact_id, text, vec FROM facts')]
    except Exception:
        return []
    finally:
        con.close()


def _closest(text, exclude_id=None):
    """The most similar fact already known. Returns (similarity, id, text)."""
    vec = _embed(text)
    if not vec:
        return (0.0, None, None)
    best = (0.0, None, None)
    for fid, txt, other in _stored_vectors():
        if exclude_id is not None and fid == exclude_id:
            continue
        sim = _cos(vec, other)
        if sim > best[0]:
            best = (sim, fid, txt)
    return best


def reindex():
    """Embed anything that has no vector yet. Safe to run repeatedly."""
    d = _read()
    con = _vdb()
    try:
        have = {row[0] for row in con.execute('SELECT fact_id FROM facts')}
    finally:
        con.close()

    missing = [m for m in d['memories'] if m['id'] not in have]
    done = 0
    for m in missing:
        if _index_fact(m['id'], m['text']):
            done += 1
        else:
            break                      # embedding is unavailable; stop trying
    stale = have - {m['id'] for m in d['memories']}
    _drop_vectors(sorted(stale))
    return {'ok': True, 'embedded': done, 'pending': len(missing) - done,
            'dropped': len(stale),
            'summary': 'Embedded %d fact(s); %d still waiting on the embedding model.'
                       % (done, len(missing) - done)}


def relevant(query, n=RECALL_N):
    """The facts that bear on what was just said, best first."""
    query = (query or '').strip()
    if not query:
        return []
    vec = _embed(query)
    if not vec:
        return []
    scored = []
    for fid, txt, other in _stored_vectors():
        sim = _cos(vec, other)
        if sim >= SIM_FLOOR:
            scored.append((sim, fid, txt))
    scored.sort(key=lambda r: -r[0])
    return scored[:max(1, int(n or RECALL_N))]


def context_for(text, n=RECALL_N):
    """What to put in front of him this turn.

    Retrieval when it is available, the old wholesale recital when it is not.
    A memory system that goes silent because a background service is down is
    worse than one that was never clever."""
    d = _read()
    if not d['memories']:
        return {'ok': True, 'mode': 'empty', 'count': 0, 'text': ''}

    hits = relevant(text, n)
    if hits:
        return {'ok': True, 'mode': 'retrieved', 'count': len(hits),
                'total': len(d['memories']),
                'text': '\n'.join('- ' + t for _, _, t in hits)}

    if _vec_ready is False:
        return {'ok': True, 'mode': 'recited', 'count': min(len(d['memories']), PROMPT_BUDGET),
                'total': len(d['memories']), 'text': _recite(d['memories'])}

    # Vectors work and nothing matched. That is an answer, not a failure —
    # he does not need your address to say good morning.
    return {'ok': True, 'mode': 'none', 'count': 0, 'total': len(d['memories']), 'text': ''}


def consolidate(apply=True):
    """Merge facts that say the same thing, and report ones that disagree.

    Merging is safe: the newest wording of an identical fact survives.
    Contradictions are NOT resolved automatically — deciding which of two
    plausible statements is true is exactly the judgement a machine should
    not make silently."""
    d = _read()
    rows = list(d['memories'])
    if len(rows) < 2:
        return {'ok': True, 'merged': 0, 'conflicts': [],
                'summary': 'Nothing to consolidate yet.'}

    vecs = {fid: v for fid, _, v in _stored_vectors()}
    if not vecs:
        return {'ok': False, 'error': 'No fact vectors yet. Run reindex first '
                                      '(the embedding model must be reachable).'}

    rows.sort(key=lambda m: m.get('at', 0))       # oldest first; newest wording wins
    dropped, conflicts = [], []
    for i, a in enumerate(rows):
        if a['id'] in dropped or a['id'] not in vecs:
            continue
        for b in rows[i + 1:]:
            if b['id'] in dropped or b['id'] not in vecs:
                continue
            sim = _cos(vecs[a['id']], vecs[b['id']])
            if sim >= SIM_SAME:
                dropped.append(a['id'])           # keep b, the later phrasing
                break
            if sim >= SIM_CAND:
                conflicts.append({'a': a['text'], 'b': b['text'],
                                  'similarity': round(sim, 3)})

    if apply and dropped:
        with _lock:
            d2 = _read()
            d2['memories'] = [m for m in d2['memories'] if m['id'] not in dropped]
            _write(d2)
        _drop_vectors(dropped)

    lines = []
    if dropped:
        lines.append('Merged %d duplicate fact(s).' % len(dropped))
    if conflicts:
        lines.append('These pairs are close enough that one may have replaced the '
                     'other. I have changed nothing — similarity cannot tell a '
                     'rewording from a correction, so this needs a judgement:')
        for c in conflicts[:8]:
            lines.append('  * "%s"\n    vs "%s"' % (c['a'], c['b']))
        lines.append('Tell me which is right and I will forget the other.')
    if not lines:
        lines.append('Nothing to merge and nothing that looks contradictory.')

    return {'ok': True, 'merged': len(dropped), 'conflicts': conflicts,
            'summary': '\n'.join(lines)}




# ==========================================================================
#  Episodic memory — what worked last time
#
#  Facts are what he knows. Episodes are what he has done: the request, the
#  sequence of tools that satisfied it, and whether it came out right.
#
#  The point is not nostalgia. An assistant that has built forty landing pages
#  and starts the forty-first from nothing is not learning, and the user can
#  tell. Retrieving the shape of a run that worked puts him on the path he
#  already found rather than the one he is about to rediscover.
#
#  Only successful episodes are ever retrieved. A record of how something went
#  wrong is worth keeping for a post-mortem and worth nothing as a template.
# ==========================================================================

EP_MATCH = 0.62        # how alike two requests must be before precedent helps
EP_SAME = 0.93         # the same request again; update rather than accumulate
EP_KEEP = 400          # episodes retained; the oldest fall off the end


def _edb():
    import sqlite3
    con = sqlite3.connect(VEC_DB, timeout=30)
    con.execute('PRAGMA busy_timeout=15000')
    con.execute("""CREATE TABLE IF NOT EXISTS episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request TEXT,
        actions TEXT,
        outcome TEXT,
        detail TEXT,
        vec BLOB,
        at REAL
    )""")
    con.commit()
    return con


def record_episode(request, actions=None, outcome='ok', detail=''):
    """Log a completed piece of work.

    Called after the turn, never during it — an episode is only meaningful
    once it has an outcome."""
    request = (request or '').strip()
    if not request:
        return {'ok': False, 'error': 'No request to record.'}

    acts = actions if isinstance(actions, list) else []
    vec = _embed(request)
    if not vec:
        return {'ok': False, 'error': 'No embedding available; episode not stored.'}

    blob = _pack(vec)
    con = _edb()
    try:
        # The same request asked twice is one lesson, not two. Replace the older
        # record so the table reflects the latest way it was actually done.
        best_id, best_sim = None, 0.0
        for eid, other in con.execute('SELECT id, vec FROM episodes'):
            sim = _cos(vec, _unpack(other))
            if sim > best_sim:
                best_id, best_sim = eid, sim

        row = (request, json.dumps(acts)[:4000], outcome, (detail or '')[:600],
               blob, time.time())
        if best_id is not None and best_sim >= EP_SAME:
            con.execute('UPDATE episodes SET request=?, actions=?, outcome=?, '
                        'detail=?, vec=?, at=? WHERE id=?', row + (best_id,))
        else:
            con.execute('INSERT INTO episodes (request, actions, outcome, detail, '
                        'vec, at) VALUES (?,?,?,?,?,?)', row)

        con.execute('DELETE FROM episodes WHERE id NOT IN '
                    '(SELECT id FROM episodes ORDER BY at DESC LIMIT ?)', (EP_KEEP,))
        con.commit()
    finally:
        con.close()
    return {'ok': True, 'summary': 'Episode recorded.'}


def precedent(text, n=2):
    """How work like this went before. Successful runs only."""
    text = (text or '').strip()
    if not text:
        return {'ok': True, 'count': 0, 'text': ''}
    vec = _embed(text)
    if not vec:
        return {'ok': True, 'count': 0, 'text': ''}

    con = _edb()
    try:
        rows = con.execute('SELECT request, actions, detail, vec, at FROM episodes '
                           "WHERE outcome='ok'").fetchall()
    except Exception:
        return {'ok': True, 'count': 0, 'text': ''}
    finally:
        con.close()

    scored = []
    for request, actions, detail, blob, at in rows:
        sim = _cos(vec, _unpack(blob))
        if sim >= EP_MATCH:
            scored.append((sim, request, actions, detail, at))
    scored.sort(key=lambda r: -r[0])
    top = scored[:max(1, int(n or 2))]
    if not top:
        return {'ok': True, 'count': 0, 'text': ''}

    out = []
    for sim, request, actions, detail, at in top:
        try:
            steps = ' -> '.join(json.loads(actions))
        except Exception:
            steps = ''
        when = time.strftime('%d %b', time.localtime(at))
        line = '- "%s" (%s)' % (request[:140], when)
        if steps:
            line += '\n  what worked: ' + steps[:300]
        if detail:
            line += '\n  result: ' + detail[:200]
        out.append(line)

    return {'ok': True, 'count': len(top), 'text': chr(10).join(out)}


def episode_stats():
    con = _edb()
    try:
        total = con.execute('SELECT COUNT(*) FROM episodes').fetchone()[0]
        good = con.execute("SELECT COUNT(*) FROM episodes WHERE outcome='ok'").fetchone()[0]
    except Exception:
        total = good = 0
    finally:
        con.close()
    return {'ok': True, 'total': total, 'ok_count': good,
            'summary': '%d episodes recorded, %d of them successful.' % (total, good)}


# ==========================================================================
#  The notice queue
#
#  Standing decision, made before any of this was built: **nothing speaks
#  unprompted.** Anything he notices waits here until Zero looks at it.
#
#  That constraint is the whole design. There is no announce path, no toast,
#  no voice call — a notice is written to disk and a number changes in the
#  corner of the screen. The difference between an assistant that is present
#  and one that is insufferable is entirely a question of who chooses the
#  moment, and it is not him.
#
#  Kept in the same JSON file as reminders, so it survives a restart for the
#  same reason they do.
# ==========================================================================

NOTICE_KEEP = 200


def add_notice(text, kind='note', source=''):
    text = (text or '').strip()
    if not text:
        return {'ok': False, 'error': 'A notice needs something to say.'}
    with _lock:
        d = _read()
        d.setdefault('notices', [])
        # The same observation arriving twice is one thing worth knowing, not
        # two. A watcher that fires every minute must not fill the queue.
        for n in d['notices']:
            if n['text'] == text and not n.get('seen'):
                n['at'] = time.time()
                n['repeats'] = n.get('repeats', 1) + 1
                _write(d)
                return {'ok': True, 'duplicate': True, 'id': n['id'],
                        'summary': 'Already queued; timestamp refreshed.'}

        row = {'id': _next_id(d['notices']), 'text': text, 'kind': kind,
               'source': source or '', 'at': time.time(), 'seen': False}
        d['notices'].append(row)
        d['notices'] = d['notices'][-NOTICE_KEEP:]
        _write(d)
        unseen = sum(1 for n in d['notices'] if not n.get('seen'))
    return {'ok': True, 'id': row['id'], 'unseen': unseen,
            'summary': 'Queued. %d waiting.' % unseen}


def list_notices(include_seen=True, limit=60):
    d = _read()
    rows = d.get('notices', [])
    if not include_seen:
        rows = [n for n in rows if not n.get('seen')]
    rows = sorted(rows, key=lambda n: -n['at'])[:limit]
    unseen = sum(1 for n in d.get('notices', []) if not n.get('seen'))
    return {'ok': True, 'unseen': unseen, 'notices': rows,
            'summary': ('\n'.join('%s%s — %s'
                                  % ('' if n.get('seen') else '* ',
                                     time.strftime('%d %b %H:%M', time.localtime(n['at'])),
                                     n['text']) for n in rows)
                        or 'Nothing has been noticed.')}


def mark_seen(which=None):
    """Mark one notice read, or all of them."""
    with _lock:
        d = _read()
        d.setdefault('notices', [])
        hit = 0
        for n in d['notices']:
            if n.get('seen'):
                continue
            if which in (None, '', 'all') or str(n['id']) == str(which):
                n['seen'] = True
                n['seen_at'] = time.time()
                hit += 1
        _write(d)
        unseen = sum(1 for n in d['notices'] if not n.get('seen'))
    return {'ok': True, 'marked': hit, 'unseen': unseen,
            'summary': 'Marked %d read.' % hit}


def clear_notices():
    with _lock:
        d = _read()
        d['notices'] = []
        _write(d)
    return {'ok': True, 'unseen': 0, 'summary': 'Queue cleared.'}


def command(action, text=None, when=None):
    if action == 'set':
        return add_reminder(text, when)
    if action == 'list':
        return list_reminders()
    if action == 'cancel':
        return cancel_reminder(text)
    if action == 'remember':
        return remember(text)
    if action == 'forget':
        return forget(text)
    if action == 'recall':
        return search_memory(text)
    if action == 'relevant':
        return context_for(text)
    if action == 'consolidate':
        return consolidate()
    if action == 'reindex':
        return reindex()
    if action == 'precedent':
        return precedent(text)
    if action == 'episodes':
        return episode_stats()
    if action == 'notice':
        return add_notice(text)
    if action == 'notices':
        return list_notices()
    if action == 'notices_seen':
        return mark_seen(text)
    if action == 'notices_clear':
        return clear_notices()
    return {'ok': False, 'error': 'Unknown action "%s".' % action}
