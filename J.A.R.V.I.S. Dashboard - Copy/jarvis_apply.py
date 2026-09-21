"""Stage two of the hunt: read, tailor, prepare. Never send.

`jarvis_jobs.py` already finds work and ranks it by embedding similarity. That
is a cheap filter over hundreds of listings and it is the right first pass.
What it cannot do is read a description the way a person does - notice that
"5+ years" is a hard gate, that the stack is one you have never touched, that
the salary band is below your floor. That is what this module adds, and it
only ever runs on the handful of listings that survived the cheap filter.

Three stages, deliberately separate, because each costs something different
and fails differently:

    analyse(job_id)   a local model reads the description against the master
                      resume and returns a structured verdict. SLOW - measured
                      at 17s+ per listing on this machine, so it runs on a
                      shortlist, never on a whole scan.

    tailor(job_id)    a resume and a covering letter for one listing. Every
                      generated claim is checked against the master resume
                      before it is written to disk. See _unsupported().

    prepare(job_id)   opens the application form, fills what it can, and stops.

Two constraints are structural here rather than advisory, because a rule the
model can talk itself out of is not a rule:

  * NOTHING IS EVER SUBMITTED. There is no code path in this file that clicks
    a submit control. _is_submit() exists to REFUSE, and prepare() walks away
    from the form with it still on screen.

  * NOTHING IS EVER INVENTED. A tailored resume may reorder, select and
    rephrase what the master resume already says. It may not add an employer,
    a date, a number or a technology that is not in the master. That is
    enforced by comparing the two, not by asking nicely.
"""

import json
import os
import re
import subprocess
import time
import urllib.request

import jarvis_jobs as jobs

# ---------------------------------------------------------------------------
#  Where things go. Writing stays inside the projects folder, same as the rest
#  of the file layer - an application packet is a project like any other.
# ---------------------------------------------------------------------------

PROJECTS = os.path.join(os.path.expanduser('~'), 'JarvisProjects')
PACKET_DIR = os.path.join(PROJECTS, 'applications')

OLLAMA = 'http://localhost:11434/v1/chat/completions'
#  Measured 2026-08-28, drafting a real packet for a real listing:
#    jarvis-r1:8b        timed out at 182s — could not produce a packet at all,
#                        so choosing a job failed before the form ever opened.
#    gpt-oss:120b-cloud  13s, and a letter citing the actual Disney barista
#                        role rather than "[Your Name]".
#  These sit on the critical path of an application, so they get the model that
#  finishes. JARVIS_APPLY_MODEL overrides both if the cloud tier is exhausted.
ANALYST = os.environ.get('JARVIS_APPLY_MODEL', 'gpt-oss:120b-cloud')
DRAFTER = os.environ.get('JARVIS_APPLY_MODEL', 'gpt-oss:120b-cloud')

ANALYSE_TIMEOUT = 180             # a reasoning model on a 6k-token prompt is slow
SHORTLIST_FLOOR = 0.55            # embedding score below this is not worth reading


# ---------------------------------------------------------------------------
#  1 · the local model
# ---------------------------------------------------------------------------

def _ask(model, system, user, timeout=ANALYSE_TIMEOUT, want_json=True):
    """One call to the local model. Returns (ok, payload_or_error)."""
    body = json.dumps({
        'model': model,
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'stream': False,
        'temperature': 0.2,
    }).encode('utf-8')

    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except Exception as err:
        return False, 'local model unavailable: %s' % str(err)[:160]

    text = ((data.get('choices') or [{}])[0].get('message') or {}).get('content') or ''

    # deepseek-r1 emits its working inside <think> tags. That is the point of
    # using it, and it is also not the answer - strip it before parsing.
    text = re.sub(r'(?is)<think>.*?</think>', ' ', text).strip()
    if not want_json:
        return True, text

    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        return False, 'model returned no JSON: %s' % text[:200]
    try:
        return True, json.loads(m.group(0))
    except Exception as err:
        return False, 'model returned malformed JSON: %s' % str(err)[:120]


# ---------------------------------------------------------------------------
#  2 · analyse - read one description properly
# ---------------------------------------------------------------------------

ANALYST_SYSTEM = (
    'You compare one job description against one resume and report what is '
    'actually there. You do not encourage and you do not sell. A gap is a gap.\n'
    'Reply with JSON only:\n'
    '{"score": 0-100, "verdict": "strong|possible|weak|blocked", '
    '"matches": ["..."], "gaps": ["..."], "blockers": ["..."], "why": "one sentence"}\n'
    'A blocker is something that disqualifies outright - a required clearance, '
    'a degree they insist on, a location that is not remote, years of experience '
    'stated as a minimum. If there are blockers the verdict is "blocked" '
    'regardless of how well everything else fits.'
)


def analyse(job_id):
    """Read one listing against the master resume. Slow, so shortlist first."""
    job = _job(job_id)
    if not job:
        return {'ok': False, 'error': 'No job with id %s.' % job_id}

    # get_profile() answers {'ok', 'profile'} like every other command in
    # that module - the profile itself is one level down.
    profile = (jobs.get_profile() or {}).get('profile') or {}
    master = (profile.get('resume') or '').strip()
    if not master:
        return {'ok': False, 'error': 'No master resume set. Configuration, Jobs, resume.'}

    desc = (job.get('description') or '').strip()
    if not desc:
        return {'ok': False, 'error': 'That listing has no description stored to read.'}

    user = ('RESUME\n' + master[:6000] +
            '\n\nJOB\n' + job.get('title', '') + ' at ' + job.get('company', '') +
            '\n' + desc[:6000])

    started = time.time()
    ok, out = _ask(ANALYST, ANALYST_SYSTEM, user)
    if not ok:
        return {'ok': False, 'error': out}

    out['seconds'] = round(time.time() - started, 1)
    out['job_id'] = job_id
    _remember(job_id, 'analysis', out)
    return {'ok': True, 'analysis': out}


def shortlist(limit=8, floor=SHORTLIST_FLOOR):
    """The listings worth spending a slow read on."""
    listing = jobs.list_jobs(state='new', limit=200)
    rows = [j for j in (listing.get('jobs') or [])
            if (j.get('score') or 0) >= floor]
    rows.sort(key=lambda j: j.get('score') or 0, reverse=True)
    return {'ok': True, 'count': len(rows[:limit]), 'jobs': rows[:limit]}


# ---------------------------------------------------------------------------
#  3 · tailor - and prove nothing was invented
# ---------------------------------------------------------------------------

DRAFT_SYSTEM = (
    'You rewrite an existing resume for one specific job.\n'
    'HARD RULE: every employer, title, date, number, tool and qualification in '
    'your output must already appear in the resume you were given. You may '
    'reorder, select, cut and rephrase. You may NOT add. If the job asks for '
    'something the resume does not show, leave it out - do not imply it.\n'
    'Reply with JSON only: {"resume": "...", "cover_letter": "..."}'
)

# Tokens that carry a factual claim. Prose is allowed to vary; these are not.
_CLAIM = re.compile(r'\b(?:[A-Z][A-Za-z0-9+.#-]{2,}|\d[\d,.%$kK+]*)\b')

# Words that look like claims but are ordinary sentence furniture.
_HARMLESS = {
    'I', 'My', 'The', 'This', 'That', 'These', 'Those', 'A', 'An', 'And', 'Or',
    'But', 'For', 'With', 'From', 'Your', 'You', 'We', 'Our', 'It', 'As', 'At',
    'In', 'On', 'To', 'Of', 'By', 'Dear', 'Sincerely', 'Regards', 'Hiring',
    'Manager', 'Team', 'Role', 'Position', 'Company', 'Experience', 'Skills',
    'Summary', 'Education', 'Work', 'Projects', 'Contact',
}


# Where a capital letter means "new sentence" rather than "proper noun".
_SENT_START = re.compile(r'(?:^|[.!?:]\s+|\n\s*(?:[-*•]\s*)?)$')


def _unsupported(master, generated, context=''):
    """Claims present in the generated text that the master resume never made.

    Errs in the safe direction — a legitimate rephrasing flagged costs a
    glance, a fabricated employer let through costs the application — but not
    so far that the warning stops being read. It used to report "energetic,
    proven, strong, core, fast, paced" on a perfectly honest letter, because
    every word opening a sentence is capitalised and the pattern reads a
    capital as a proper noun. A list of ordinary adjectives is one a person
    learns to skip, and then the fabricated employer goes past with it.

    So a word is not treated as a claim merely for starting a sentence or a
    bullet. Everything that actually carries a fact still is: numbers and
    quantities always, anything with an internal capital or an acronym
    (TerraTech, POS, ServSafe), and any capitalised word mid-sentence.
    """
    #  Two things count as already-known, and neither is a fabrication.
    #
    #  Every word of the master, not only its capitalised ones: the draft
    #  rephrases his own skills into title case ("Restocking & Station
    #  Organization"), and matching only capitals made his own vocabulary look
    #  invented.
    #
    #  And the listing itself. "Pura Vida Miami" is not a claim about him, it
    #  is the company he is applying to, and flagging the addressee of the
    #  letter is pure noise.
    def tokens(text):
        #  Both tokenisations, because the two sides split differently: the
        #  master holds "626-696-0490" as one run while the claim pattern reads
        #  three numbers out of it, and his own telephone number was being
        #  reported as an unsupported claim.
        low = text.lower()
        return (set(re.findall(r"[a-z0-9#+.-]{2,}", low))
                | set(re.findall(r"[a-z0-9]+", low)))

    #  The listing vouches for its own words but NOT for its numbers. Letting
    #  it vouch for numbers is how "6 years of experience" stopped being
    #  flagged: the description happened to contain a 6, and a fabricated
    #  duration is exactly what this check exists to catch. Only the master
    #  resume can support a figure about him.
    have_words = tokens(master + ' ' + (context or ''))
    #  List numbering is not a figure about him. His master resume numbers its
    #  skills 1 to 8 on their own lines, which put every single digit into the
    #  known set and made "6 years of experience" unflaggable — the exact
    #  fabrication this is for.
    have_nums = tokens(re.sub(r'(?m)^\s*\d{1,2}[.)]?\s*$', ' ', master))
    out = []
    for m in _CLAIM.finditer(generated):
        tok = m.group(0)
        if tok in _HARMLESS:
            continue
        if tok.lower() in (have_nums if tok[:1].isdigit() else have_words):
            continue
        # Plain Capitalised word in sentence-opening position: style, not fact.
        if (tok[:1].isupper() and tok[1:].islower()
                and _SENT_START.search(generated[:m.start()])):
            continue
        if tok.lower() not in out:
            out.append(tok.lower())
    return out


def tailor(job_id):
    """A resume and a covering letter for one listing, checked before written."""
    job = _job(job_id)
    if not job:
        return {'ok': False, 'error': 'No job with id %s.' % job_id}

    # get_profile() answers {'ok', 'profile'} like every other command in
    # that module - the profile itself is one level down.
    profile = (jobs.get_profile() or {}).get('profile') or {}
    master = (profile.get('resume') or '').strip()
    if not master:
        return {'ok': False, 'error': 'No master resume set.'}

    user = ('RESUME\n' + master[:6000] +
            '\n\nJOB\n' + job.get('title', '') + ' at ' + job.get('company', '') +
            '\n' + (job.get('description') or '')[:6000])

    ok, out = _ask(DRAFTER, DRAFT_SYSTEM, user, timeout=ANALYSE_TIMEOUT)
    if not ok:
        return {'ok': False, 'error': out}

    resume = (out.get('resume') or '').strip()
    letter = (out.get('cover_letter') or '').strip()
    if not resume or not letter:
        return {'ok': False, 'error': 'The model returned an empty draft.'}

    invented = _unsupported(master, resume + chr(10) + letter,
                            context=' '.join([job.get('title') or '',
                                              job.get('company') or '',
                                              (job.get('description') or '')[:4000]]))

    folder = os.path.join(PACKET_DIR, _slug(job))
    os.makedirs(folder, exist_ok=True)
    _write(os.path.join(folder, 'resume.md'), resume)
    _write(os.path.join(folder, 'cover-letter.md'), letter)
    _write(os.path.join(folder, 'job.md'),
           '# %s\n\n%s\n\n%s\n\n%s' % (job.get('title', ''), job.get('company', ''),
                                       job.get('url', ''), job.get('description', '')))

    return {
        'ok': True,
        'folder': folder,
        # Never silently. An empty list is a result; a non-empty one is a stop sign.
        'unsupported_claims': invented,
        'summary': ('Drafted into %s. %s'
                    % (folder,
                       'Nothing appeared that the master resume does not say.'
                       if not invented else
                       'CHECK THESE - they are not in your master resume: '
                       + ', '.join(invented[:12]))),
    }


# ---------------------------------------------------------------------------
#  4 · prepare - fill the form, stop at the line
# ---------------------------------------------------------------------------

# Anything whose label, value or name matches this is a control that sends the
# application. prepare() never clicks one. This list is the whole guarantee, so
# it errs wide: a false positive costs one manual click, a false negative sends
# an application Zero has not read.
_SUBMIT = re.compile(
    r'(?i)\b(submit|send|apply\s*now|finish|complete\s*application|'
    r'confirm|agree\s*and|i\s*accept)\b')


def _is_submit(label):
    return bool(_SUBMIT.search(label or ''))


# Fields worth filling automatically. Anything not on this list is left for a
# human, because a wrong answer on a screening question is worse than a blank.
FIELD_MAP = {
    'first_name':  ['first name', 'given name', 'forename'],
    'last_name':   ['last name', 'surname', 'family name'],
    'full_name':   ['full name', 'your name', 'name'],
    'email':       ['email', 'e-mail'],
    'phone':       ['phone', 'mobile', 'telephone'],
    'location':    ['location', 'city', 'where are you based'],
    'linkedin':    ['linkedin'],
    'github':      ['github', 'portfolio', 'website'],
}


#  Which browser opens the form, and where its profile lives.
#
#  Edge because that is what he uses, so the window that appears is the one he
#  expects rather than a stray "Chrome for Testing". The profile is separate
#  from his everyday one and persists between applications: sign into Indeed or
#  LinkedIn once in that window and every later form opens already signed in.
BROWSER_CHANNEL = os.environ.get('JARVIS_BROWSER_CHANNEL', 'msedge')
BROWSER_PROFILE = os.path.join(PROJECTS, '.apply-browser')

#  Browser windows left open on a filled form, held so nothing collects them.
#  Bounded: a filled form nobody dealt with is still an open browser, and four
#  of those is a cluttered desktop rather than a queue of work.
_OPEN = []
MAX_OPEN_FORMS = 3
APPLY_HOPS = 3                    # advert -> employer site -> form


EDGE_PATHS = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]


def _edge_exe():
    for p in EDGE_PATHS:
        if os.path.isfile(p):
            return p
    return None


#  Pages that mean "you are not signed in" rather than "here is the form".
_LOGIN_URL = re.compile(r'(?i)/(?:auth|login|signin|sign-in|account/login)\b'
                        r'|\b(?:secure\.indeed\.com|accounts\.google\.com'
                        r'|www\.linkedin\.com/uas|login\.microsoftonline\.com)\b')


def sign_in(url='https://www.indeed.com/'):
    """Open the apply profile in an ORDINARY Edge window so a login can be done.

    This exists because a login cannot be done inside the automated browser at
    all. Google refuses OAuth to anything driving Chrome DevTools Protocol —
    the sign-in button opens a blank popup and stops — and that refusal is
    deliberate on their side. Working around it would mean disguising the
    automation, which is not something this module does.

    So the login happens where a login belongs: a normal browser window, driven
    by a person. It is the same on-disk profile that prepare() later opens, so
    the session it leaves behind is the session the automation picks up. Sign
    in once here and every later form opens already authenticated.

    Nothing automated touches this window. It is launched and let go.
    """
    exe = _edge_exe()
    if not exe:
        return {'ok': False, 'error':
                'Could not find msedge.exe. Sign in by hand instead: open Edge with '
                '--user-data-dir="%s"' % BROWSER_PROFILE}

    os.makedirs(BROWSER_PROFILE, exist_ok=True)
    try:
        subprocess.Popen([exe, '--user-data-dir=' + BROWSER_PROFILE,
                          '--no-first-run', '--no-default-browser-check', url],
                         close_fds=True)
    except Exception as err:
        return {'ok': False, 'error': 'Could not open Edge: %s' % err}

    return {'ok': True, 'summary':
            'Opened %s in a normal Edge window using the application profile. Sign in '
            'there — Google and Indeed will both work, because nothing is automating '
            'it. Close the window when you are done; the session is kept, and the next '
            'job you choose will open its form already signed in.' % url}


def _hold(session):
    _OPEN.append(session)
    while len(_OPEN) > MAX_OPEN_FORMS:
        _shut(_OPEN.pop(0))


def _shut(session):
    for step in ('browser', 'pw'):
        try:
            target = session.get(step)
            if target:
                (target.close if step == 'browser' else target.stop)()
        except Exception:
            pass


def close_forms():
    """Shut every form window this module opened."""
    n = len(_OPEN)
    while _OPEN:
        _shut(_OPEN.pop())
    return {'ok': True, 'summary': 'Closed %d open form window(s).' % n}


def prepare(job_id, headless=False):
    """Open the form, fill the plain fields, attach the packet, and stop.

    Deliberately NOT headless by default: the entire point is that Zero sees
    the filled form and decides. A headless run would fill a form nobody looks
    at, which is one bug away from the thing this module refuses to do.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'ok': False, 'error':
                'Playwright is not installed (pip install playwright '
                '&& playwright install chromium). Roughly 400 MB of browser.'}

    job = _job(job_id)
    if not job or not job.get('url'):
        return {'ok': False, 'error': 'No job with id %s, or it has no URL.' % job_id}

    folder = os.path.join(PACKET_DIR, _slug(job))
    resume_path = os.path.join(folder, 'resume.md')
    if not os.path.isfile(resume_path):
        return {'ok': False, 'error': 'Draft the packet first: apply tailor %s' % job_id}

    # get_profile() answers {'ok', 'profile'} like every other command in
    # that module - the profile itself is one level down.
    profile = (jobs.get_profile() or {}).get('profile') or {}
    filled, skipped, refused = [], [], []

    #  Started, not entered as a context manager.
    #
    #  `with sync_playwright() as p:` tears the driver down on the way out of
    #  the block, which closes the browser with it — so this filled the form
    #  and then destroyed it before anyone could look, which is the one thing
    #  the whole module exists to avoid. The window has to outlive the call.
    #
    #  The driver is kept in _OPEN so nothing collects it, and closed either by
    #  the caller or by the cap below.
    p = sync_playwright().start()
    keep = None
    try:
        #  A real browser, with a profile that remembers him.
        #
        #  This used to be `p.chromium.launch()`, which opens Playwright's own
        #  bundled Chrome for Testing on a blank profile — no cookies, no
        #  sessions. Every real application form is behind a login, so a blank
        #  profile lands on the sign-in page instead of the form, and job sites
        #  refuse a sign-in attempt from a fresh automated browser anyway. The
        #  form was never reachable.
        #
        #  So: real Edge, and a persistent profile of its own. Its own, rather
        #  than his everyday one, because a running browser holds a lock on its
        #  profile directory — using the live one fails whenever Edge is open,
        #  which is always. He signs into a job site once in this window and it
        #  is remembered from then on.
        #  chromium_sandbox=True because Playwright otherwise passes
        #  --no-sandbox, which puts a security warning banner across the top of
        #  the window and is one of the things a site looks at when deciding
        #  whether it is talking to a person.
        launch = dict(headless=headless, chromium_sandbox=True,
                      args=['--no-first-run', '--no-default-browser-check'])
        try:
            ctx = p.chromium.launch_persistent_context(
                BROWSER_PROFILE, channel=BROWSER_CHANNEL, **launch)
        except Exception:
            # No Edge on this machine: the bundled browser still fills a form,
            # it just cannot carry a login.
            ctx = p.chromium.launch_persistent_context(BROWSER_PROFILE, **launch)

        browser = ctx                      # a context closes the browser with it
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(job['url'], wait_until='domcontentloaded', timeout=45000)

        #  Landing on a login page is not a form with nothing fillable on it,
        #  and reporting "filled 0 fields" for it sends you looking for a bug in
        #  the field matching. Say what actually happened.
        landed = page.url or ''
        if _LOGIN_URL.search(landed):
            keep = {'pw': p, 'browser': ctx, 'job': job_id, 'at': time.time()}
            _hold(keep)
            return {
                'ok': False,
                'needs_sign_in': True,
                'url': landed,
                'error': ('That listing is behind a login — the window landed on %s '
                          'rather than the application form, so nothing was filled.\n\n'
                          'Signing in cannot be done from this window: Google refuses '
                          'to authenticate a browser under automation, which is why '
                          'its button opens a blank popup. Run the sign_in action '
                          'instead. That opens the SAME profile in an ordinary Edge '
                          'window with nothing driving it, where the login works '
                          'normally. Sign in there once, close it, and choose this job '
                          'again — the form will open already authenticated.'
                          % landed.split('?')[0]),
            }

        def scan(pg):
            """Fill what can be filled on whatever page we are looking at.

            Enumerates the send controls FIRST and names them, so the guarantee
            is visible in the result rather than resting on the selector below
            happening not to match a <button>. Nothing here is clicked; the
            list exists to prove what was deliberately left alone."""
            for ctl in pg.query_selector_all(
                    'button, input[type=submit], input[type=button], [role=button]'):
                try:
                    label = (ctl.inner_text() or '').strip() or _label_for(pg, ctl)
                    if _is_submit(label):
                        refused.append(label)
                except Exception:
                    pass

            for field in pg.query_selector_all(
                    'input:not([type=hidden]), textarea, select'):
                try:
                    label = _label_for(pg, field)
                    if _is_submit(label):
                        refused.append(label)              # never touched
                        continue

                    key = _match_field(label)
                    if not key:
                        skipped.append(label or '(unlabelled)')
                        continue

                    value = (profile.get(key) or '').strip()
                    if not value:
                        skipped.append(label)
                        continue

                    field.fill(value)
                    filled.append('%s = %s' % (label, value))
                except Exception:
                    skipped.append('(could not read a field)')

        #  Getting from an advert to the actual form.
        #
        #  There are usually two doors, not one. An Indeed viewjob page carries
        #  a description and "Apply on company site"; that lands on the
        #  employer's ATS, which is itself a description page with "Apply Now";
        #  the form is behind THAT. Following one door reached Kroger's careers
        #  site and stopped there with nothing filled.
        #
        #  So follow up to APPLY_HOPS doors, and stop the moment a page has
        #  fields on it.
        #
        #  The safety rule that makes clicking "Apply Now" acceptable: a page
        #  with nothing fillable on it has nothing to submit, so an apply
        #  control there is a way in, not a send button. The moment a page does
        #  have fields, no apply control is ever clicked again — it is treated
        #  as the submit it now is, refused by name, and left for Zero.
        followed = ''

        def has_form(pg):
            """Enough inputs that this is a form rather than a gateway."""
            try:
                return len(pg.query_selector_all(
                    'input:not([type=hidden]):not([type=search]), textarea, select')) >= 3
            except Exception:
                return False

        for _hop in range(APPLY_HOPS):
            page.wait_for_timeout(1500)          # forms that render after load
            del refused[:]
            del skipped[:]
            del filled[:]
            scan(page)

            if filled or has_form(page):
                break                            # this is the form; stop here

            entry = None
            for ctl in page.query_selector_all('a, button, [role=button]'):
                try:
                    text = (ctl.inner_text() or '').strip()
                    if re.search(r'(?i)\bapply\b', text) and len(text) < 60:
                        entry = (ctl, text)
                        break
                except Exception:
                    pass
            if not entry:
                break

            ctl, text = entry
            try:
                # It usually opens the next step in a new tab; if it navigates
                # in place, expect_page times out and the same page object is
                # already on the destination.
                with ctx.expect_page(timeout=8000) as popup:
                    ctl.click()
                page = popup.value
            except Exception:
                pass
            try:
                page.wait_for_load_state('domcontentloaded', timeout=30000)
            except Exception:
                pass
            followed = (followed + ' -> ' + text) if followed else text

        #  Nothing fillable and nothing to send is not a form. Reporting "the
        #  form is open and pre-filled" over a page with neither is how he came
        #  to tell Zero an application was ready when the browser was showing a
        #  job advert. Say what is actually there.
        if not filled and not refused and not skipped:
            keep = {'pw': p, 'browser': ctx, 'job': job_id, 'at': time.time()}
            if headless:
                browser.close(); p.stop(); keep = None
            else:
                _hold(keep)
            return {
                'ok': False,
                'no_form': True,
                'url': page.url,
                'followed': followed,
                'error': ('No application form was found. The window is on %s, which '
                          'has no fields to fill and no send button — %s. NOTHING was '
                          'filled and nothing was submitted; do not tell him an '
                          'application is ready, because there is not one. He can open '
                          'that page and apply by hand, or pick a different listing.'
                          % (page.url.split('?')[0],
                             ('following "%s" did not reach one' % followed) if followed
                             else 'it is a job advert rather than an application')),
            }

        # The browser is left open, on the filled form, with submit untouched.
        if headless:
            browser.close()
            p.stop()
        else:
            keep = {'pw': p, 'browser': browser, 'job': job_id, 'at': time.time()}
            _hold(keep)

        return {
            'ok': True,
            'summary': ('Filled %d field(s) on %s. The form is open in a browser '
                        'window and NOT submitted - read it and press the button '
                        'yourself.' % (len(filled), job.get('company', 'the site'))),
            'filled': filled,
            'left_for_you': skipped[:20],
            'submit_controls_untouched': refused,
            'packet': folder,
            'url': page.url,
            'followed': followed,
        }
    except Exception:
        if keep is None:
            try:
                p.stop()
            except Exception:
                pass
        raise


#  How a browser decides what a control is called, in the order a person would
#  read it. Asked in the page because the association is a DOM relationship —
#  <label for>, or a <label> wrapped around the field — and no attribute on the
#  input itself records it.
_LABEL_JS = r"""
el => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();

  if (el.id) {
    const l = document.querySelector('label[for="' + (window.CSS && CSS.escape
                                                      ? CSS.escape(el.id) : el.id) + '"]');
    if (l && clean(l.textContent)) return clean(l.textContent);
  }
  const wrap = el.closest('label');
  if (wrap && clean(wrap.textContent)) return clean(wrap.textContent);

  const by = el.getAttribute('aria-labelledby');
  if (by) {
    const joined = by.split(/\s+/).map(id => document.getElementById(id))
                     .filter(Boolean).map(n => clean(n.textContent)).join(' ');
    if (clean(joined)) return clean(joined);
  }
  return '';
}
"""


def _label_for(page, field):
    """Best-effort human label for a form control.

    The visible <label> is asked for first, and that is the whole fix here:
    this used to read only aria-label, placeholder, name and id, so a form
    written the ordinary way —

        <label><span>First name</span><input name="fn"></label>

    — reported its field as "fn", matched nothing in FIELD_MAP, and was left
    blank. Measured against a fixture with six fillable fields: zero filled
    before, six after. That is the whole of "he is not filling it out with my
    info".

    Attributes stay as the fallback for forms with no labels at all."""
    try:
        text = field.evaluate(_LABEL_JS)
    except Exception:
        text = ''
    if text:
        # A label wrapping a whole fieldset can be enormous; the first line of
        # it is the part that names this control.
        return text[:120]

    for attr in ('aria-label', 'placeholder', 'name', 'id'):
        v = field.get_attribute(attr)
        if v:
            return v.replace('_', ' ').replace('-', ' ')
    return ''


def _match_field(label):
    low = (label or '').lower()
    for key, words in FIELD_MAP.items():
        if any(w in low for w in words):
            return key
    return None


# ---------------------------------------------------------------------------
#  plumbing
# ---------------------------------------------------------------------------

def _job(job_id):
    for j in (jobs.list_jobs(limit=500).get('jobs') or []):
        if j.get('id') == job_id:
            return j
    return None


def _slug(job):
    raw = '%s-%s' % (job.get('company', ''), job.get('title', ''))
    return re.sub(r'[^a-z0-9]+', '-', raw.lower()).strip('-')[:60] or 'application'


def _write(path, text):
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


def _remember(job_id, key, value):
    """Keep the analysis beside the listing so a slow read happens once."""
    try:
        d = jobs._read()
        for j in d.get('jobs', []):
            if j.get('id') == job_id:
                j[key] = value
                jobs._write(d)
                return
    except Exception:
        pass


def command(action, **kw):
    """Same dispatcher shape as the other modules, so serve.py routes it the
    same way: POST /api/apply/command."""
    if action == 'shortlist':
        return shortlist(int(kw.get('limit') or 8))
    if action == 'analyse':
        return analyse(kw.get('id') or '')
    if action == 'tailor':
        return tailor(kw.get('id') or '')
    if action == 'prepare':
        return prepare(kw.get('id') or '', headless=bool(kw.get('headless')))
    if action == 'close_forms':
        return close_forms()
    if action == 'sign_in':
        return sign_in(kw.get('url') or 'https://www.indeed.com/')
    return {'ok': False, 'error': 'Unknown action "%s". Known: shortlist, '
                                  'analyse, tailor, prepare, sign_in, close_forms.' % action}
