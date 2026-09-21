"""
Desktop awareness for J.A.R.V.I.S. — clipboard and foreground window.

Why the server and not the page
-------------------------------
A browser can read the clipboard only inside a user gesture and only after a
permission prompt, which is unusable for "what did I just copy?" — by the time
the model asks, the gesture is long gone. And no web API can tell you which
application is in front. Both are ordinary questions for a desktop process.

Everything here is read-mostly and cheap. `active_window` in particular is worth
calling before answering an ambiguous question: "how do I fix this" means
something different in a code editor than in a music player.
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess

IS_WINDOWS = os.name == 'nt'

CLIP_LIMIT = 20_000        # characters returned; a clipboard can hold a novel


# --------------------------------------------------------------- clipboard

def read_clipboard():
    """Text currently on the clipboard.

    PowerShell rather than the Win32 clipboard API: opening the clipboard from
    another process can fail if any application is holding it, and Get-Clipboard
    already handles the retry and the unicode.
    """
    if not IS_WINDOWS:
        return {'ok': False, 'error': 'Clipboard reading is implemented for Windows only.'}
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command', 'Get-Clipboard -Raw'],
            capture_output=True, text=True, timeout=15,
            encoding='utf-8', errors='replace')
        text = (r.stdout or '').rstrip('\r\n')
        if not text.strip():
            return {'ok': True, 'empty': True,
                    'summary': 'The clipboard is empty, or holds something that is not text '
                               '(an image or a file, for instance).'}
        clipped = len(text) > CLIP_LIMIT
        return {'ok': True, 'chars': len(text),
                'summary': text[:CLIP_LIMIT] + ('\n\n[truncated]' if clipped else '')}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'Reading the clipboard timed out — something is '
                                      'holding it open.'}
    except Exception as err:
        return {'ok': False, 'error': 'Could not read the clipboard: %s' % err}


def write_clipboard(text):
    if not IS_WINDOWS:
        return {'ok': False, 'error': 'Clipboard writing is implemented for Windows only.'}
    body = str(text or '')
    if not body:
        return {'ok': False, 'error': 'Nothing to copy.'}
    try:
        # through stdin, so the text never has to survive shell quoting
        r = subprocess.run(
            ['powershell', '-NoProfile', '-Command', 'Set-Clipboard -Value ([Console]::In.ReadToEnd())'],
            input=body, capture_output=True, text=True, timeout=15,
            encoding='utf-8', errors='replace')
        if r.returncode != 0:
            return {'ok': False, 'error': (r.stderr or 'Set-Clipboard failed')[:160]}
        return {'ok': True, 'chars': len(body),
                'summary': 'Copied %d characters to the clipboard.' % len(body)}
    except Exception as err:
        return {'ok': False, 'error': 'Could not write the clipboard: %s' % err}


# ----------------------------------------------------------- active window

def _process_name(pid):
    try:
        PROCESS_QUERY_LIMITED = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
        if not h:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wt.DWORD(512)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        pass
    return ''


def active_window():
    """Which application is in front, and what its window is called."""
    if not IS_WINDOWS:
        return {'ok': False, 'error': 'Window inspection is implemented for Windows only.'}
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {'ok': True, 'summary': 'Nothing appears to be in the foreground.'}

        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ''

        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = _process_name(pid.value)

        app = os.path.splitext(exe)[0] if exe else 'unknown'
        return {'ok': True, 'app': app, 'exe': exe, 'title': title,
                'summary': ('%s — "%s"' % (app, title)) if title else app}
    except Exception as err:
        return {'ok': False, 'error': 'Could not read the foreground window: %s' % err}


def command(action, text=None):
    if action == 'clipboard':
        return read_clipboard()
    if action == 'copy':
        return write_clipboard(text)
    if action == 'active_window':
        return active_window()
    return {'ok': False, 'error': 'Unknown desktop action "%s".' % action}
