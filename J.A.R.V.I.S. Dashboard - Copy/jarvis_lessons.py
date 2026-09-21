"""
Language learning for J.A.R.V.I.S.

The split
---------
The model teaches. This module remembers.

That division is the whole design. A model is very good at explaining why
Spanish puts the pronoun where it does, and completely incapable of knowing
that you learned *aunque* eleven days ago and are about to forget it. So the
conversation stays with the model and the schedule lives here, on disk, in a
file you can read.

Scheduling is SM-2, the algorithm behind every spaced-repetition system worth
using. An item you get right moves further away; an item you get wrong comes
back tomorrow and loses the ground it had gained. Nothing about it is clever —
it is just arithmetic that has been tested on millions of learners, which is
worth more than anything invented here would be.

Any language
------------
Nothing below knows what a language is. It stores items with a term, a
meaning and a note, so it holds Spanish vocabulary, Japanese characters,
German cases and ASL handshapes equally well. What differs between them is
what the model puts in the note, not the machinery.
"""

import json
import os
import re
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, 'jarvis_lessons.json')

DAY = 86400.0
NEW_PER_LESSON = 7          # new items introduced in one sitting
REVIEW_CAP = 20             # due items shown before the new ones

_lock = threading.Lock()


def _read():
    try:
        with open(STORE, 'r', encoding='utf-8') as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    d.setdefault('items', [])
    d.setdefault('sessions', [])
    return d


def _write(d):
    tmp = STORE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, STORE)


def _key(lang):
    return re.sub(r'[^a-z ]', '', (lang or '').strip().lower()) or 'unknown'


def _next_id(rows):
    return max([r.get('id', 0) for r in rows] or [0]) + 1


# ------------------------------------------------------------------ schedule

def _schedule(item, grade):
    """SM-2. `grade` is 0-5; below 3 is a failure.

    The ease factor is where the algorithm earns its keep: an item you keep
    getting wrong does not merely repeat, it becomes permanently more frequent
    than one you find easy. Floor it at 1.3 or a single bad day can condemn a
    word to appearing forever.
    """
    ease = float(item.get('ease', 2.5))
    reps = int(item.get('reps', 0))
    interval = float(item.get('interval', 0))

    if grade < 3:
        reps = 0
        interval = 1.0
        item['lapses'] = int(item.get('lapses', 0)) + 1
    else:
        if reps == 0:
            interval = 1.0
        elif reps == 1:
            interval = 6.0
        else:
            interval = round(interval * ease, 2)
        reps += 1

    ease = ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    item['ease'] = round(max(1.3, ease), 3)
    item['reps'] = reps
    item['interval'] = interval
    item['due'] = time.time() + interval * DAY
    item['seen'] = int(item.get('seen', 0)) + 1
    item['last'] = time.time()
    return item


# --------------------------------------------------------------------- items

def add(language, items):
    """Teach him what he just taught you.

    Called by the model after it introduces something, so the schedule knows
    the item exists. Duplicates by term are updated rather than added — the
    same word taught twice is one word.
    """
    lang = _key(language)
    rows = items if isinstance(items, list) else []
    if not rows:
        return {'ok': False, 'error': 'No items to add.'}

    added, updated = 0, 0
    with _lock:
        d = _read()
        by_term = {(i['lang'], i['term'].strip().lower()): i for i in d['items']}
        for r in rows:
            term = str(r.get('term') or '').strip()
            if not term:
                continue
            hit = by_term.get((lang, term.lower()))
            if hit:
                hit['meaning'] = str(r.get('meaning') or hit.get('meaning') or '')
                if r.get('note'):
                    hit['note'] = str(r['note'])
                updated += 1
                continue
            row = {'id': _next_id(d['items']), 'lang': lang, 'term': term,
                   'meaning': str(r.get('meaning') or ''),
                   'note': str(r.get('note') or ''),
                   'ease': 2.5, 'reps': 0, 'interval': 0.0,
                   'due': time.time(), 'seen': 0, 'lapses': 0,
                   'added': time.time()}
            d['items'].append(row)
            by_term[(lang, term.lower())] = row
            added += 1
        _write(d)
        total = sum(1 for i in d['items'] if i['lang'] == lang)

    return {'ok': True, 'added': added, 'updated': updated, 'total': total,
            'summary': 'Added %d and refreshed %d. You now have %d %s items.'
                       % (added, updated, total, lang)}


def due(language, limit=REVIEW_CAP):
    """What has come back round, hardest first."""
    lang = _key(language)
    now = time.time()
    d = _read()
    rows = [i for i in d['items'] if i['lang'] == lang and i.get('due', 0) <= now]
    # Lowest ease first: the ones being forgotten matter more than the ones
    # that are merely due.
    rows.sort(key=lambda i: (i.get('ease', 2.5), i.get('due', 0)))
    return rows[:limit]


def start_lesson(language):
    """Everything the model needs to run one sitting."""
    lang = _key(language)
    d = _read()
    mine = [i for i in d['items'] if i['lang'] == lang]
    review = due(lang)
    fresh = [i for i in mine if i.get('seen', 0) == 0][:NEW_PER_LESSON]

    known = sum(1 for i in mine if i.get('reps', 0) >= 2)
    streak = _streak(d, lang)

    if not mine:
        head = ('Nothing stored for %s yet. Teach a first handful and add them '
                'so they can be scheduled.' % lang)
    elif review:
        head = ('%d item(s) are due for review. Test those first, then introduce '
                'up to %d new ones.' % (len(review), NEW_PER_LESSON))
    else:
        head = ('Nothing is due. Introduce up to %d new items, or practise '
                'freely.' % NEW_PER_LESSON)

    return {'ok': True, 'language': lang, 'due': review, 'new': fresh,
            'total': len(mine), 'known': known, 'streak': streak,
            'summary': head}


def record(item_id, correct, grade=None, term=None, language=None):
    """Score one answer. Right pushes it away, wrong pulls it back.

    Accepts the term as well as the id, because the model knows the word and
    has to be told the number — and it was guessing at numbers, failing, then
    fetching the list to try again. Matching on what it already knows removes
    a wasted round trip per answer.
    """
    try:
        g = int(grade) if grade is not None else (4 if correct else 1)
    except (TypeError, ValueError):
        g = 4 if correct else 1
    g = max(0, min(5, g))

    with _lock:
        d = _read()
        hit = None
        if item_id is not None and str(item_id) not in ('', 'None', 'undefined'):
            for i in d['items']:
                if str(i['id']) == str(item_id):
                    hit = i
                    break
        if not hit and term:
            key = str(term).strip().lower()
            lang = _key(language) if language else None
            for i in d['items']:
                if i['term'].strip().lower() == key and (not lang or i['lang'] == lang):
                    hit = i
                    break
        if not hit:
            return {'ok': False, 'error': 'No such item. Pass either the id from a '
                                          'lesson listing, or the exact term with its '
                                          'language.'}
        _schedule(hit, g)
        _touch_session(d, hit['lang'])
        _write(d)

    when = ('tomorrow' if hit['interval'] <= 1
            else 'in %g days' % round(hit['interval'], 1))
    return {'ok': True, 'item': hit['term'], 'interval': hit['interval'],
            'summary': '%s — next in view %s.' % (hit['term'], when)}


def _touch_session(d, lang):
    today = time.strftime('%Y-%m-%d')
    for s in d['sessions']:
        if s['date'] == today and s['lang'] == lang:
            s['answers'] = s.get('answers', 0) + 1
            return
    d['sessions'].append({'date': today, 'lang': lang, 'answers': 1})
    d['sessions'] = d['sessions'][-800:]


def _streak(d, lang):
    """Consecutive days ending today or yesterday.

    Yesterday counts as still alive — a streak that dies at midnight punishes
    you for sleeping, which is not what a streak is for.
    """
    days = {s['date'] for s in d['sessions'] if s['lang'] == lang}
    if not days:
        return 0
    today = time.strftime('%Y-%m-%d')
    yday = time.strftime('%Y-%m-%d', time.localtime(time.time() - DAY))
    if today not in days and yday not in days:
        return 0
    n, cursor = 0, time.time() if today in days else time.time() - DAY
    while time.strftime('%Y-%m-%d', time.localtime(cursor)) in days:
        n += 1
        cursor -= DAY
    return n


def progress(language=None):
    d = _read()
    langs = {}
    for i in d['items']:
        s = langs.setdefault(i['lang'], {'total': 0, 'known': 0, 'due': 0, 'learning': 0})
        s['total'] += 1
        if i.get('reps', 0) >= 2:
            s['known'] += 1
        elif i.get('seen', 0) > 0:
            s['learning'] += 1
        if i.get('due', 0) <= time.time():
            s['due'] += 1

    for name in langs:
        langs[name]['streak'] = _streak(d, name)

    if language:
        lang = _key(language)
        s = langs.get(lang)
        if not s:
            return {'ok': True, 'language': lang, 'total': 0,
                    'summary': 'Nothing stored for %s yet.' % lang}
        return {'ok': True, 'language': lang, **s,
                'summary': '%s: %d known, %d still learning, %d due now, %d day streak.'
                           % (lang, s['known'], s['learning'], s['due'], s['streak'])}

    if not langs:
        return {'ok': True, 'languages': {}, 'summary': 'No languages started yet.'}
    lines = ['%s: %d known of %d, %d due, %d day streak'
             % (k, v['known'], v['total'], v['due'], v['streak'])
             for k, v in sorted(langs.items())]
    return {'ok': True, 'languages': langs, 'summary': '\n'.join(lines)}


def forget(language, term=None):
    lang = _key(language)
    with _lock:
        d = _read()
        before = len(d['items'])
        if term:
            key = term.strip().lower()
            d['items'] = [i for i in d['items']
                          if not (i['lang'] == lang and i['term'].strip().lower() == key)]
        else:
            d['items'] = [i for i in d['items'] if i['lang'] != lang]
        gone = before - len(d['items'])
        _write(d)
    return {'ok': True, 'removed': gone,
            'summary': 'Removed %d item(s).' % gone if gone else 'Nothing matched.'}


# ----------------------------------------------------------------------- ASL

#  A deliberate limitation, stated rather than papered over.
#
#  Most signs are movement. A still photograph of a handshape shows one frame
#  of something whose meaning is often carried entirely by direction,
#  repetition or facial expression — the difference between many sign pairs is
#  invisible in a photograph. Fingerspelling is the exception: the 26
#  handshapes are static, which is exactly why it is the right place to start.
#
#  So this ships the fingerspelling curriculum and describes movement in words
#  for everything else, and points at a real dictionary rather than pretending
#  a still is a lesson. Bundling a photo set would need one that is actually
#  licensed for it, which is a decision for Zero and not something to quietly
#  assume.

ASL_NOTE = (
    'ASL is its own language with its own grammar. It is not English in the '
    'hands, and word-for-word translation teaches something that is not ASL. '
    'Most signs are movement, so a still image cannot carry them; fingerspelling '
    'is the exception and is where to begin.'
)

ASL_REFERENCE = 'https://www.lifeprint.com/asl101/fingerspelling/'


def asl_info():
    return {'ok': True, 'note': ASL_NOTE, 'reference': ASL_REFERENCE,
            'summary': ASL_NOTE + ' Reference: ' + ASL_REFERENCE}


def command(action, **kw):
    if action == 'start':
        return start_lesson(kw.get('language'))
    if action == 'add':
        return add(kw.get('language'), kw.get('items'))
    if action == 'record':
        return record(kw.get('id'), bool(kw.get('correct')), kw.get('grade'),
                      kw.get('term'), kw.get('language'))
    if action == 'progress':
        return progress(kw.get('language'))
    if action == 'due':
        return {'ok': True, 'items': due(kw.get('language'))}
    if action == 'forget':
        return forget(kw.get('language'), kw.get('term'))
    if action == 'asl':
        return asl_info()
    return {'ok': False, 'error': 'Unknown lessons action "%s".' % action}
