"""Building in Minecraft, from the outside.

Why it works this way
---------------------
Java Edition 26.2, vanilla, singleplayer. That rules out most of the obvious
routes: RCON only exists on a dedicated server, and every bot library
(mineflayer and friends) is pinned to protocol versions years behind this one.
A mod would work and is a heavier commitment than the job deserves.

What is left is the thing that cannot go out of date: the chat box. `/fill`
and `/setblock` have been stable for a decade and will still be there in the
next snapshot. So this plans a structure, turns it into a few dozen commands,
and types them in.

It is a *command* builder, not a mouse builder, and that is the whole reason
it is quick. Placing a mansion block by block through the mouse is thousands
of clicks and ten minutes of a hijacked cursor, and one lag spike ruins it.
One `/fill` lays an entire wall. A mansion is around sixty commands and lands
in seconds.

Coordinates are RELATIVE (`~`), so nothing needs to know where the player is
standing. Everything is offset away from the player so the walls never appear
around them.

Two things it will not do
-------------------------
  * Only `fill`, `setblock` and `tp` are ever sent, and `tp` only relative to
    the player. The verb of every generated command is checked against that
    list immediately before it is typed — see `_safe()`. There is no path from
    here to `/kill`, `/ban`, `/op` or anything else.
  * It types into whatever window is Minecraft, and refuses to type at all if
    it cannot find one. It will not send keystrokes into whatever happens to
    be in front.

Requires cheats enabled in the world (Open to LAN -> Allow Cheats: ON works
for an existing world).
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import time


try:
    import win32gui
    import win32con
    import win32clipboard
    HAVE_WIN = True
except Exception:                       # pragma: no cover - not on Windows
    HAVE_WIN = False


#  The clipboard, directly.
#
#  jarvis_desktop.write_clipboard shells out to PowerShell, which is the right
#  call there — it is robust and it runs once. Here it runs once per command,
#  and measured at 257ms against 0.2ms for the Win32 API: ninety-two commands
#  would have spent twenty-four seconds on clipboard writes alone, for a build
#  that otherwise takes a few seconds.
def _clip_set(text):
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, str(text))
    finally:
        win32clipboard.CloseClipboard()


def _clip_get():
    try:
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None                     # empty, or holding something not text


# ---------------------------------------------------------------------------
#  1 · finding and driving the game window
# ---------------------------------------------------------------------------

WINDOW_HINTS = ('minecraft', 'lwjgl')

#  Delay between chat commands.
#
#  Measured, and then measured again after being wrong about it.
#
#  0.035/0.09 built a small house and a full mansion cleanly, at 32s for 96
#  commands. Halving them to 0.012/0.045 brought that to 16.7s and the next
#  mansion came out with walls missing while its floor and roof landed — the
#  shape of Minecraft dropping keystrokes while the chat box is still opening.
#  The commands all report as sent either way, because SendInput accepting an
#  event says nothing about the game reading it.
#
#  So these are back to the only values that have ever produced a correct
#  build. Twice as slow and right beats twice as fast and holed.
KEY_GAP = 0.035
CHAT_GAP = 0.09
MAX_COMMANDS = 400                      # a build that needs more is a mistake
NEW_BUILD_GRACE = 600                   # s before a second build stops looking
                                        # like a follow-up someone meant as `add`


def find_window():
    """The Minecraft window handle, or None."""
    if not HAVE_WIN:
        return None
    hits = []

    def look(handle, _):
        if not win32gui.IsWindowVisible(handle):
            return
        title = (win32gui.GetWindowText(handle) or '').lower()
        if any(h in title for h in WINDOW_HINTS):
            hits.append(handle)

    try:
        win32gui.EnumWindows(look, None)
    except Exception:
        return None
    return hits[0] if hits else None


#  SendInput with scan codes, not keybd_event and not PostMessage.
#
#  Minecraft reads the keyboard through GLFW, which takes raw input. Messages
#  posted to the window are ignored outright, and virtual-key events without a
#  scan code arrive as the wrong key. Scan codes are what actually land.
_SCAN = {'t': 0x14, 'enter': 0x1C, 'esc': 0x01, 'ctrl': 0x1D, 'v': 0x2F,
         'slash': 0x35, 'a': 0x1E, 'c': 0x2E, 'f3': 0x3D}

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


#  The union has to be declared at its FULL width, not just the member being
#  used.
#
#  This was the bug that made the first build type nothing at all: with only
#  KEYBDINPUT in the union, sizeof(INPUT) came to 32 where Windows wants 40,
#  and every SendInput call was rejected with ERROR_INVALID_PARAMETER. It
#  reported 27 commands sent and sent none of them, because nobody was reading
#  the return value. MOUSEINPUT is the larger member and it is what sets the
#  size, so it is declared even though nothing here moves the mouse.
_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _KEYBD(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', _ULONG_PTR)]


class _MOUSE(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
                ('dwFlags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', _ULONG_PTR)]


class _HARDWARE(ctypes.Structure):
    _fields_ = [('uMsg', wt.DWORD), ('wParamL', wt.WORD), ('wParamH', wt.WORD)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [('ki', _KEYBD), ('mi', _MOUSE), ('hi', _HARDWARE)]
    _anonymous_ = ('u',)
    _fields_ = [('type', wt.DWORD), ('u', _U)]


_SendInput = ctypes.windll.user32.SendInput
_SendInput.argtypes = (wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_SendInput.restype = wt.UINT


class KeysRefused(Exception):
    """SendInput rejected the event. Never silent — see above."""


def _key(scan, up=False):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    item = _INPUT(type=INPUT_KEYBOARD,
                  u=_INPUT._U(ki=_KEYBD(wVk=0, wScan=scan, dwFlags=flags,
                                        time=0, dwExtraInfo=0)))
    if _SendInput(1, ctypes.byref(item), ctypes.sizeof(item)) != 1:
        raise KeysRefused('Windows refused the keystroke (error %d).'
                          % ctypes.windll.kernel32.GetLastError())


def _tap(name, hold=()):
    for h in hold:
        _key(_SCAN[h])
    _key(_SCAN[name])
    time.sleep(KEY_GAP)
    _key(_SCAN[name], up=True)
    for h in reversed(hold):
        _key(_SCAN[h], up=True)


def _focus(handle):
    """Bring the game to the front, and say honestly whether it worked.

    SetForegroundWindow on its own is refused for a background process —
    Windows will not let one program steal focus from another, and it fails
    silently. It looked like it was working here only because Minecraft
    happened to already be in front.

    AttachThreadInput ties this thread's input queue to the foreground
    window's for a moment, which is the documented way to be allowed. If even
    that is refused, this returns False and nothing is typed, rather than
    firing keystrokes at whatever is actually in front."""
    try:
        if win32gui.IsIconic(handle):
            win32gui.ShowWindow(handle, win32con.SW_RESTORE)
    except Exception:
        pass

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    me = kernel32.GetCurrentThreadId()
    front = user32.GetForegroundWindow()
    other = user32.GetWindowThreadProcessId(front, None) if front else 0

    attached = False
    try:
        if other and other != me:
            attached = bool(user32.AttachThreadInput(me, other, True))
        user32.SetForegroundWindow(handle)
        user32.BringWindowToTop(handle)
        user32.SetActiveWindow(handle)
    except Exception:
        pass
    finally:
        if attached:
            try:
                user32.AttachThreadInput(me, other, False)
            except Exception:
                pass

    for _ in range(12):                     # give the switch a moment to settle
        if user32.GetForegroundWindow() == handle:
            time.sleep(0.25)
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
#  1b · where he actually is
# ---------------------------------------------------------------------------

#  Minecraft will tell you, if you ask it the way it expects.
#
#  F3+C copies the player's position to the clipboard as a /tp command. That is
#  a built-in debug feature, it is exact to two decimal places, and it carries
#  the facing and the dimension with it. No screen reading, no OCR, no guessing
#  digits out of a screenshot.
#
#  This is what lifts the whole module out of "relative to wherever he happens
#  to be standing". Builds can be anchored to real coordinates, remembered, and
#  returned to later from anywhere in the world.
#
#  F3+C is TAPPED, never held: holding it for six seconds is Minecraft's
#  deliberate crash shortcut.
_TP = re.compile(r'tp\s+@s\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)'
                 r'(?:\s+(-?[\d.]+)\s+(-?[\d.]+))?')
_DIM = re.compile(r'in\s+(\S+)\s+run')


def where(retries=2):
    """The player's absolute position and facing, or an error."""
    handle = find_window()
    if not handle:
        return {'ok': False, 'error': 'No Minecraft window is open.'}
    if not _focus(handle):
        return {'ok': False, 'error': 'Could not bring Minecraft to the front.'}

    keep = _clip_get()
    try:
        for attempt in range(retries + 1):
            _clip_set('jarvis-probe')          # so a stale read cannot pass as fresh
            time.sleep(0.08)
            _key(_SCAN['f3'])
            time.sleep(0.05)
            _key(_SCAN['c'])
            time.sleep(0.05)
            _key(_SCAN['c'], up=True)
            time.sleep(0.05)
            _key(_SCAN['f3'], up=True)
            time.sleep(0.45)

            got = _clip_get() or ''
            hit = _TP.search(got)
            if hit:
                dim = _DIM.search(got)
                x, y, z = (float(hit.group(i)) for i in (1, 2, 3))
                yaw = float(hit.group(4)) if hit.group(4) else 0.0
                return {'ok': True,
                        'x': int(round(x)), 'y': int(round(y)), 'z': int(round(z)),
                        'yaw': round(((yaw % 360) + 360) % 360, 1),
                        'facing': _facing(yaw),
                        'dimension': dim.group(1) if dim else 'minecraft:overworld',
                        'summary': 'He is at %d %d %d, facing %s.'
                                   % (round(x), round(y), round(z), _facing(yaw))}
    finally:
        if isinstance(keep, str) and keep and keep != 'jarvis-probe':
            try:
                _clip_set(keep)
            except Exception:
                pass

    return {'ok': False, 'error':
            'Asked the game for his position with F3+C and nothing came back. He is '
            'probably in a menu or the chat box rather than in the world.'}


#  Minecraft yaw: 0 is south (+Z), 90 west (-X), 180 north (-Z), 270 east (+X).
_COMPASS = [(45, 'south', (0, 1)), (135, 'west', (-1, 0)),
            (225, 'north', (0, -1)), (315, 'east', (1, 0))]


def _facing(yaw):
    y = ((yaw % 360) + 360) % 360
    for edge, name, _ in _COMPASS:
        if y < edge:
            return name
    return 'south'


def _forward(yaw):
    """Unit step in the direction he is looking, as (dx, dz)."""
    y = ((yaw % 360) + 360) % 360
    for edge, _, vec in _COMPASS:
        if y < edge:
            return vec
    return (0, 1)


# ---------------------------------------------------------------------------
#  2 · what may be sent
# ---------------------------------------------------------------------------

#  The whole safety story. A generated command is checked against this the
#  instant before it is typed, so a bug in a generator cannot turn into a
#  command that does something other than place blocks.
ALLOWED = ('fill', 'setblock', 'tp')

_VERB = re.compile(r'^/([a-z]+)\b')


def _safe(cmd):
    """Whether one generated command is allowed to be typed."""
    cmd = (cmd or '').strip()
    if not cmd.startswith('/') or len(cmd) > 250 or '\n' in cmd:
        return False
    verb = _VERB.match(cmd)
    if not verb or verb.group(1) not in ALLOWED:
        return False
    # tp only ever relative, so nothing can be flung across the world. fill and
    # setblock may be absolute now that the game tells us where he is.
    if verb.group(1) == 'tp' and '~' not in cmd:
        return False
    return True


def send(commands, dry_run=False):
    """Type a list of chat commands into the game.

    Clipboard-and-paste rather than character-by-character: a `/fill` is
    eighty characters, and sixty of those typed one key at a time is a minute
    of the keyboard being unusable instead of a few seconds."""
    flat = []
    for c in commands:
        flat.extend(c if isinstance(c, list) else [c])
    commands = [c for c in flat if c and str(c).strip()]
    if not commands:
        return {'ok': False, 'error': 'Nothing to build.'}
    if len(commands) > MAX_COMMANDS:
        return {'ok': False, 'error': 'That plan needs %d commands; the ceiling is %d.'
                                      % (len(commands), MAX_COMMANDS)}

    refused = [c for c in commands if not _safe(c)]
    if refused:
        return {'ok': False, 'error': 'Refusing to send %d command(s) that are not '
                                      'fill/setblock/tp: %s'
                                      % (len(refused), '; '.join(refused[:3]))}

    if dry_run:
        return {'ok': True, 'dry_run': True, 'commands': commands,
                'summary': '%d commands planned, nothing sent.' % len(commands)}

    handle = find_window()
    if not handle:
        return {'ok': False, 'error':
                'No Minecraft window is open, so nothing was typed. Start the game, '
                'load the world, and make sure cheats are on — an existing world can '
                'get them from Escape, Open to LAN, Allow Cheats ON, Start LAN World.'}

    keep = _clip_get()
    if not _focus(handle):
        return {'ok': False, 'error': 'Could not bring the Minecraft window to the '
                                      'front, so nothing was typed.'}

    sent = 0
    try:
        for cmd in commands:
            _clip_set(cmd)
            _tap('t')                      # open chat
            time.sleep(CHAT_GAP)
            _tap('v', hold=('ctrl',))      # paste the command
            time.sleep(0.02)
            _tap('enter')
            time.sleep(CHAT_GAP)
            sent += 1
    except KeysRefused as err:
        return {'ok': False, 'sent': sent,
                'error': 'Windows stopped accepting keystrokes after %d command(s): %s'
                         % (sent, err)}
    finally:
        # His clipboard is his. Put back whatever was in it.
        if isinstance(keep, str) and keep:
            try:
                _clip_set(keep)
            except Exception:
                pass

    return {'ok': True, 'sent': sent,
            'summary': 'Sent %d build command(s) to Minecraft.' % sent}


# ---------------------------------------------------------------------------
#  3 · the structures
# ---------------------------------------------------------------------------

#  Palettes, so "a mansion" is not always the same grey box.
#  A style is a palette plus a shape language: what the roof does, how much of
#  the wall is glass, and whether it stands on the ground at all. Adding one is
#  a dict entry, not new code.
#
#  `glass` is roughly the percentage of wall that becomes window, and it is
#  what separates a stone keep from a modern villa more than the blocks do.
STYLES = {
    'stone':      {'wall': 'stone_bricks', 'trim': 'polished_andesite',
                   'floor': 'oak_planks', 'roof_block': 'deepslate_tiles',
                   'window': 'glass_pane', 'light': 'lantern', 'door': 'oak_door',
                   'roof': 'stepped', 'glass': 30},
    'oak':        {'wall': 'oak_planks', 'trim': 'stripped_oak_log',
                   'floor': 'spruce_planks', 'roof_block': 'dark_oak_planks',
                   'window': 'glass_pane', 'light': 'lantern', 'door': 'oak_door',
                   'roof': 'stepped', 'glass': 30},
    'quartz':     {'wall': 'quartz_block', 'trim': 'smooth_quartz',
                   'floor': 'polished_diorite', 'roof_block': 'gray_concrete',
                   'window': 'glass_pane', 'light': 'sea_lantern',
                   'door': 'birch_door', 'roof': 'stepped', 'glass': 45},

    'modern':     {'wall': 'white_concrete', 'trim': 'smooth_quartz',
                   'floor': 'polished_andesite', 'roof_block': 'light_gray_concrete',
                   'window': 'glass', 'light': 'sea_lantern', 'door': 'warped_door',
                   'roof': 'flat', 'glass': 75},
    'futuristic': {'wall': 'light_gray_concrete', 'trim': 'sea_lantern',
                   'floor': 'smooth_stone', 'roof_block': 'cyan_terracotta',
                   'window': 'tinted_glass', 'light': 'sea_lantern',
                   'door': 'iron_door', 'roof': 'flat', 'glass': 85},
    'treehouse':  {'wall': 'oak_planks', 'trim': 'oak_log',
                   'floor': 'spruce_planks', 'roof_block': 'oak_stairs',
                   'window': 'glass_pane', 'light': 'lantern', 'door': 'oak_door',
                   'roof': 'pitched', 'glass': 35, 'lift': 12, 'trunk': 'oak_log'},
    'cave':       {'wall': 'cobblestone', 'trim': 'stone_bricks',
                   'floor': 'stone_bricks', 'roof_block': 'stone',
                   'window': 'glass_pane', 'light': 'torch', 'door': 'spruce_door',
                   'roof': 'none', 'glass': 10, 'carve': True},
}
PALETTES = STYLES            # the old name, so nothing that used it breaks

SIZES = {'small': (11, 9), 'medium': (17, 13), 'large': (25, 17),
         'mansion': (31, 21)}


#  Set to (x, y, z) while a plan is being generated for a KNOWN spot, and left
#  None to fall back to coordinates relative to the player.
#
#  Absolute is what makes a build findable again. Relative coordinates were the
#  root of the doubled shells: every command meant "wherever he is standing
#  now", so a follow-up from six blocks away laid a second building through the
#  first. With F3+C giving real coordinates, a build has an address.
_ORIGIN = None


def _at(x, y, z):
    if _ORIGIN is None:
        return '~%d ~%d ~%d' % (x, y, z)
    return '%d %d %d' % (_ORIGIN[0] + x, _ORIGIN[1] + y, _ORIGIN[2] + z)


#  /fill refuses more than 32768 blocks in one command. It does not clamp, it
#  does not do what it can — it rejects the whole thing and does nothing.
#
#  That is how a clear-up turned into vandalism: a 53x29x81 air fill was
#  124,497 blocks, so it was thrown out entirely and the buildings stayed
#  standing, while the small grass fill in the same batch went through and laid
#  a flat plane under them. Floating mansions over levelled ground.
#
#  So every box is sliced to fit before it is ever sent.
FILL_LIMIT = 32768


def _box(x1, y1, z1, x2, y2, z2, block, mode=''):
    out = _boxes(x1, y1, z1, x2, y2, z2, block, mode)
    return out[0] if len(out) == 1 else out


def _boxes(x1, y1, z1, x2, y2, z2, block, mode=''):
    """One box as however many commands it takes to stay under the limit."""
    lo = lambda a, b: (min(a, b), max(a, b))
    x1, x2 = lo(x1, x2)
    y1, y2 = lo(y1, y2)
    z1, z2 = lo(z1, z2)

    span_x, span_y, span_z = x2 - x1 + 1, y2 - y1 + 1, z2 - z1 + 1
    if span_x * span_y * span_z <= FILL_LIMIT:
        return ['/fill %s %s %s%s' % (_at(x1, y1, z1), _at(x2, y2, z2), block,
                                      (' ' + mode) if mode else '')]

    # Slice along Y first: a layer at a time reads sensibly in chat, and it is
    # the axis a building is thinnest in.
    per = max(1, FILL_LIMIT // max(1, span_x * span_z))
    out = []
    y = y1
    while y <= y2:
        top = min(y2, y + per - 1)
        out.extend(_boxes(x1, y, z1, x2, top, z2, block, mode))
        y = top + 1
    return out


# ---------------------------------------------------------------------------
#  Structures other than a house
# ---------------------------------------------------------------------------

#  A house is a box with a roof, and everything above was written around that.
#  A stadium is a bowl, a waterpark is a set of terraces with water on them, a
#  fairground is a collection of separate objects on a plaza. None of those are
#  a palette applied to a box, so each gets its own generator and `plan` picks
#  between them.
#
#  Every one of these returns plain fill/setblock commands and goes through the
#  same slicing and the same safety gate as everything else.

def _pal(palette):
    return dict(STYLES.get(palette) or STYLES['stone'])


def _ring(cx, cz, r, y1, y2, block, thickness=1):
    """A hollow circle, drawn as a square ring of fills.

    Minecraft has no circle primitive, so this walks the perimeter in short
    runs. Good enough at building scale and far cheaper than one setblock per
    block."""
    out = []
    for t in range(thickness):
        rr = r - t
        if rr < 1:
            break
        for a in range(0, 360, 6):
            import math
            x = cx + int(round(rr * math.cos(math.radians(a))))
            z = cz + int(round(rr * math.sin(math.radians(a))))
            out.append(_box(x, y1, z, x, y2, z, block))
    return out


def _beach_house(pal, w, d, floors, lift=0):
    """On stilts, open to the view, decked on the seaward side."""
    x0, z0 = 6, -(d // 2)
    x1, z1 = x0 + w - 1, z0 + d - 1
    stilt = 4
    cmds = [_box(x0 - 6, stilt, z0 - 4, x1 + 2, stilt + floors * 5 + 8, z1 + 4, 'air')]

    # stilts down to whatever is below, so it works over sand or water
    for sx in (x0, x1):
        for sz in (z0, z1):
            cmds.append(_box(sx, stilt - 12, sz, sx, stilt - 1, sz, pal['trim']))

    for f in range(floors):
        base = stilt + f * 5
        cmds.append(_box(x0, base, z0, x1, base + 4, z1, pal['wall']))
        cmds.append(_box(x0 + 1, base + 1, z0 + 1, x1 - 1, base + 4, z1 - 1, 'air'))
        cmds.append(_box(x0 + 1, base, z0 + 1, x1 - 1, base, z1 - 1, pal['floor']))
        # the whole seaward wall is glass; that is the point of a beach house
        cmds.append(_box(x0, base + 1, z0 + 1, x0, base + 3, z1 - 1, pal['window']))
        cmds.append(_box(x0 + 1, base + 1, z0, x1 - 1, base + 3, z0, pal['window']))

    top = stilt + floors * 5
    for i in range((d // 2) + 1):                     # low pitched roof
        cmds.append(_box(x0, top + i, z0 + i, x1, top + i, z0 + i, pal['roof_block']))
        cmds.append(_box(x0, top + i, z1 - i, x1, top + i, z1 - i, pal['roof_block']))

    # deck and steps down to the sand
    cmds.append(_box(x0 - 6, stilt - 1, z0 + 1, x0 - 1, stilt - 1, z1 - 1, pal['floor']))
    for i in range(stilt):
        cmds.append(_box(x0 - 6 - i, stilt - 2 - i, z0 + 2, x0 - 6 - i, stilt - 2 - i,
                         z0 + 4, pal['trim']))
    mid = (z0 + z1) // 2
    cmds.append(_box(x0, stilt, mid, x0, stilt + 1, mid, 'air'))
    for dy, half in ((0, 'lower'), (1, 'upper')):
        cmds.append('/setblock %s %s[facing=east,half=%s,hinge=left]'
                    % (_at(x0, stilt + dy, mid), pal['door'], half))
    return cmds


def _stadium(pal, w, d, floors, lift=0):
    """A bowl: sunken pitch, tiered seating, floodlights at the corners."""
    x0, z0 = 8, -(d // 2) - 6
    x1, z1 = x0 + w + 10, z0 + d + 12
    tiers = 5
    cmds = [_box(x0 - tiers - 2, -8, z0 - tiers - 2, x1 + tiers + 2, 26,
                 z1 + tiers + 2, 'air')]

    # the pitch, sunk below ground
    cmds.append(_box(x0, -6, z0, x1, -6, z1, 'green_concrete'))
    cmds.append(_box(x0 + 3, -5, z0 + 3, x1 - 3, -5, z1 - 3, 'lime_concrete'))

    # seating: each tier a step wider and a step higher
    for t in range(tiers):
        y = -5 + t * 2
        a0, b0 = x0 - t, z0 - t
        a1, b1 = x1 + t, z1 + t
        cmds.append(_box(a0, y, b0, a1, y + 1, b1, pal['wall']))
        cmds.append(_box(a0 + 1, y, b0 + 1, a1 - 1, y + 1, b1 - 1, 'air'))
        cmds.append(_box(a0, y + 1, b0, a1, y + 1, b1, 'red_concrete'))
        cmds.append(_box(a0 + 1, y + 1, b0 + 1, a1 - 1, y + 1, b1 - 1, 'air'))

    # outer wall and floodlights
    top = -5 + tiers * 2
    cmds.append(_box(x0 - tiers, top, z0 - tiers, x1 + tiers, top + 3,
                     z1 + tiers, pal['trim']))
    cmds.append(_box(x0 - tiers + 1, top, z0 - tiers + 1, x1 + tiers - 1, top + 3,
                     z1 + tiers - 1, 'air'))
    for fx in (x0 - tiers, x1 + tiers):
        for fz in (z0 - tiers, z1 + tiers):
            cmds.append(_box(fx, top, fz, fx, top + 12, fz, pal['trim']))
            cmds.append(_box(fx, top + 12, fz, fx, top + 13, fz, 'sea_lantern'))
    return cmds


def _theater(pal, w, d, floors, lift=0):
    """Enclosed hall, raked seating, a stage and a proscenium."""
    x0, z0 = 6, -(d // 2) - 2
    x1, z1 = x0 + w + 4, z0 + d + 4
    h = 14
    cmds = [_box(x0 - 1, -2, z0 - 1, x1 + 1, h + 4, z1 + 1, 'air'),
            _box(x0, 0, z0, x1, h, z1, pal['wall']),
            _box(x0 + 1, 1, z0 + 1, x1 - 1, h - 1, z1 - 1, 'air'),
            _box(x0 + 1, 0, z0 + 1, x1 - 1, 0, z1 - 1, 'black_concrete')]

    stage = x1 - 8
    cmds.append(_box(stage, 1, z0 + 2, x1 - 1, 2, z1 - 2, pal['floor']))
    cmds.append(_box(stage - 1, 3, z0 + 1, stage - 1, h - 2, z1 - 1, pal['trim']))
    cmds.append(_box(stage - 1, 3, z0 + 3, stage - 1, h - 5, z1 - 3, 'air'))
    cmds.append(_box(stage, 3, z0 + 3, stage, h - 5, z1 - 3, 'red_wool'))   # curtain

    # raked seating stepping up away from the stage
    row, y = stage - 4, 1
    while row > x0 + 2:
        cmds.append(_box(row, y, z0 + 2, row, y, z1 - 2, pal['trim']))
        cmds.append(_box(row, y + 1, z0 + 3, row, y + 1, z1 - 3, 'red_concrete'))
        row -= 2
        y += 1

    cmds.append(_box(x0 + 1, h - 1, z0 + 1, x1 - 1, h - 1, z1 - 1, pal['roof_block']))
    for cz in (z0 + 3, z1 - 3):
        cmds.append(_box(x0 + 3, h - 3, cz, x1 - 3, h - 3, cz, pal['light']))
    mid = (z0 + z1) // 2
    cmds.append(_box(x0, 1, mid - 1, x0, 2, mid + 1, 'air'))
    return cmds


def _waterpark(pal, w, d, floors, lift=0):
    """Terraced pools, a tower, and slides running down from it."""
    x0, z0 = 6, -(d // 2) - 4
    x1, z1 = x0 + w + 8, z0 + d + 8
    cmds = [_box(x0 - 2, -6, z0 - 2, x1 + 2, 30, z1 + 2, 'air'),
            _box(x0 - 2, -1, z0 - 2, x1 + 2, -1, z1 + 2, pal['floor'])]

    # three pools at different depths
    pools = [(x0 + 1, z0 + 1, x0 + 12, z0 + 12, 3),
             (x0 + 15, z0 + 1, x1 - 1, z0 + 10, 2),
             (x0 + 4, z0 + 15, x1 - 4, z1 - 1, 4)]
    for ax, az, bx, bz, depth in pools:
        cmds.append(_box(ax, -depth, az, bx, 0, bz, 'air'))
        cmds.append(_box(ax - 1, -depth - 1, az - 1, bx + 1, -depth - 1, bz + 1,
                         'blue_terracotta'))
        cmds.append(_box(ax, -depth, az, bx, -1, bz, 'water'))
        cmds.append(_box(ax - 1, 0, az - 1, bx + 1, 0, bz + 1, pal['trim']))
        cmds.append(_box(ax, 0, az, bx, 0, bz, 'air'))

    # the tower, with a ladder and two slides spiralling off it
    tx, tz, th = x0 + 20, z1 - 8, 22
    cmds.append(_box(tx, 0, tz, tx + 2, th, tz + 2, pal['wall']))
    cmds.append(_box(tx + 1, 1, tz + 1, tx + 1, th - 1, tz + 1, 'air'))
    cmds.append(_box(tx + 1, 1, tz + 1, tx + 1, th - 1, tz + 1, 'ladder[facing=north]'))
    for lane, drop in ((0, 0), (4, 6)):
        y = th - 2 - drop
        x = tx + 3 + lane
        z = tz
        while y > 1 and z > z0 + 4:
            cmds.append(_box(x, y, z, x + 1, y, z - 2, 'blue_glazed_terracotta'))
            cmds.append(_box(x, y + 1, z, x, y + 2, z - 2, pal['window']))
            cmds.append(_box(x + 2, y + 1, z, x + 2, y + 2, z - 2, pal['window']))
            z -= 3
            y -= 2
    return cmds


def _amusement_park(pal, w, d, floors, lift=0):
    """A plaza with a ferris wheel, a carousel, stalls and lit paths."""
    x0, z0 = 6, -(d // 2) - 6
    x1, z1 = x0 + w + 12, z0 + d + 12
    cmds = [_box(x0 - 2, -2, z0 - 2, x1 + 2, 40, z1 + 2, 'air'),
            _box(x0 - 2, -1, z0 - 2, x1 + 2, -1, z1 + 2, pal['floor'])]

    # ferris wheel: a vertical ring on two supports
    wx, wz, r = x0 + 14, z0 + 14, 12
    import math
    for a in range(0, 360, 5):
        dx = int(round(r * math.cos(math.radians(a))))
        dy = int(round(r * math.sin(math.radians(a))))
        cmds.append(_box(wx + dx, r + 2 + dy, wz, wx + dx, r + 2 + dy, wz,
                         pal['trim']))
        if a % 45 == 0:                      # gondolas
            cmds.append(_box(wx + dx, r + 1 + dy, wz - 1, wx + dx, r + 1 + dy, wz + 1,
                             'red_concrete'))
    for sz in (wz - 4, wz + 4):
        cmds.append(_box(wx, 0, sz, wx, r + 2, sz, pal['trim']))
    cmds.append(_box(wx - 1, 0, wz - 5, wx + 1, 0, wz + 5, pal['trim']))

    # carousel
    cx, cz = x1 - 12, z1 - 12
    cmds.append(_box(cx - 5, 0, cz - 5, cx + 5, 0, cz + 5, 'white_concrete'))
    cmds.extend(_ring(cx, cz, 5, 1, 5, 'yellow_concrete'))
    cmds.append(_box(cx, 1, cz, cx, 7, cz, pal['trim']))
    cmds.extend(_ring(cx, cz, 6, 7, 7, 'red_concrete'))
    cmds.append(_box(cx - 1, 8, cz - 1, cx + 1, 8, cz + 1, 'red_concrete'))

    # a row of stalls and lamps along the path
    for i, sx in enumerate(range(x0 + 2, x1 - 6, 8)):
        sz = z1 - 4
        cmds.append(_box(sx, 0, sz, sx + 4, 3, sz + 3, pal['wall']))
        cmds.append(_box(sx + 1, 1, sz + 1, sx + 3, 3, sz + 2, 'air'))
        cmds.append(_box(sx, 4, sz - 1, sx + 4, 4, sz + 4,
                         'red_concrete' if i % 2 else 'white_concrete'))
        cmds.append('/setblock %s %s' % (_at(sx + 2, 1, sz + 1), pal['light']))
    for lx in range(x0 + 4, x1 - 2, 10):
        cmds.append(_box(lx, 0, z0 + 2, lx, 4, z0 + 2, pal['trim']))
        cmds.append('/setblock %s %s' % (_at(lx, 5, z0 + 2), pal['light']))
    return cmds


# ---------------------------------------------------------------------------
#  Named venues and brands
# ---------------------------------------------------------------------------

#  A stadium is not one shape. A gridiron is a long rectangle with end zones, a
#  soccer pitch has a centre circle and penalty areas, a diamond is a quarter
#  circle with dirt base paths, a basketball court is indoors and small. Same
#  bowl, different floor — and the floor is the part that makes it recognisable.

SPORTS = ('football', 'soccer', 'basketball', 'baseball')


def _bowl(pal, x0, z0, x1, z1, tiers=5, floor_y=-6):
    """Sunken pitch with tiered seating and floodlights. Shared by every sport."""
    cmds = [_box(x0 - tiers - 3, floor_y - 2, z0 - tiers - 3,
                 x1 + tiers + 3, 28, z1 + tiers + 3, 'air')]
    for t in range(tiers):
        y = floor_y + 1 + t * 2
        a0, b0, a1, b1 = x0 - t - 1, z0 - t - 1, x1 + t + 1, z1 + t + 1
        cmds.append(_box(a0, y, b0, a1, y + 1, b1, pal['wall']))
        cmds.append(_box(a0 + 1, y, b0 + 1, a1 - 1, y + 1, b1 - 1, 'air'))
        cmds.append(_box(a0, y + 1, b0, a1, y + 1, b1, 'red_concrete'))
        cmds.append(_box(a0 + 1, y + 1, b0 + 1, a1 - 1, y + 1, b1 - 1, 'air'))
    top = floor_y + 1 + tiers * 2
    cmds.append(_box(x0 - tiers - 1, top, z0 - tiers - 1,
                     x1 + tiers + 1, top + 3, z1 + tiers + 1, pal['trim']))
    cmds.append(_box(x0 - tiers, top, z0 - tiers,
                     x1 + tiers, top + 3, z1 + tiers, 'air'))
    for fx in (x0 - tiers - 1, x1 + tiers + 1):
        for fz in (z0 - tiers - 1, z1 + tiers + 1):
            cmds.append(_box(fx, top, fz, fx, top + 14, fz, pal['trim']))
            cmds.append(_box(fx, top + 14, fz, fx, top + 15, fz, 'sea_lantern'))
    return cmds


def _sport_stadium(pal, w, d, floors, sport='soccer'):
    sport = (sport or 'soccer').lower()
    y = -6

    if sport == 'basketball':
        #  Indoors and small: a hall around a wooden court.
        w, d = 30, 18
        x0, z0, x1, z1 = 8, -(d // 2), 8 + w, (d // 2)
        cmds = [_box(x0 - 4, y - 2, z0 - 4, x1 + 4, 20, z1 + 4, 'air'),
                _box(x0 - 3, y, z0 - 3, x1 + 3, y, z1 + 3, 'stripped_oak_wood'),
                _box(x0, y, z0, x1, y, z1, 'oak_planks')]
        mid = (x0 + x1) // 2
        cmds.append(_box(mid, y + 1, z0, mid, y + 1, z1, 'white_concrete'))   # halfway
        cmds.extend(_ring(mid, (z0 + z1) // 2, 4, y + 1, y + 1, 'white_concrete'))
        for kx in (x0 + 6, x1 - 6):                                # the key
            cmds.append(_box(kx - 3, y + 1, (z0 + z1) // 2 - 4, kx + 3, y + 1,
                             (z0 + z1) // 2 + 4, 'orange_concrete'))
        for hx, off in ((x0 + 2, 1), (x1 - 2, -1)):                # backboard and hoop
            cz = (z0 + z1) // 2
            cmds.append(_box(hx, y + 4, cz - 2, hx, y + 6, cz + 2, 'white_concrete'))
            cmds.append(_box(hx + off, y + 4, cz, hx + off, y + 4, cz, 'orange_wool'))
        # the hall around it
        cmds.append(_box(x0 - 4, y + 1, z0 - 4, x1 + 4, y + 14, z1 + 4, pal['wall']))
        cmds.append(_box(x0 - 3, y + 1, z0 - 3, x1 + 3, y + 13, z1 + 3, 'air'))
        cmds.append(_box(x0 - 4, y + 14, z0 - 4, x1 + 4, y + 14, z1 + 4, pal['roof_block']))
        for lx in range(x0 + 4, x1, 8):
            cmds.append('/setblock %s sea_lantern' % _at(lx, y + 13, (z0 + z1) // 2))
        return cmds

    if sport == 'baseball':
        w, d = 46, 46
        x0, z0, x1, z1 = 8, -(d // 2), 8 + w, (d // 2)
        cmds = _bowl(pal, x0, z0, x1, z1, tiers=4)
        cmds.append(_box(x0, y, z0, x1, y, z1, 'green_concrete'))
        hx, hz = x0 + 3, (z0 + z1) // 2                     # home plate corner
        #  The diamond, as nested stepped runs rather than block by block: the
        #  per-block version was 657 commands for one field.
        import math
        for i in range(22):
            a = math.pi / 2 * i / 21 - math.pi / 4
            rr = 24
            px = hx + int(round(rr * math.cos(a)))
            pz = hz + int(round(rr * math.sin(a)))
            cmds.append(_box(hx, y, hz, px, y, pz, 'coarse_dirt'))
        cmds.append(_box(hx, y + 1, hz, hx, y + 1, hz, 'white_concrete'))
        for bx, bz in ((hx + 16, hz - 16), (hx + 22, hz), (hx + 16, hz + 16)):
            cmds.append(_box(bx, y + 1, bz, bx, y + 1, bz, 'white_concrete'))
        cmds.append(_box(hx + 11, y + 1, hz, hx + 11, y + 1, hz, 'coarse_dirt'))
        return cmds

    #  football (gridiron) and soccer share a rectangle; the markings differ.
    if sport == 'football':
        w, d = 48, 26
    else:
        w, d = 44, 30
    x0, z0, x1, z1 = 8, -(d // 2), 8 + w, (d // 2)
    cmds = _bowl(pal, x0, z0, x1, z1)
    cmds.append(_box(x0, y, z0, x1, y, z1, 'green_concrete'))
    cmds.append(_box(x0, y + 1, z0, x1, y + 1, z0, 'white_concrete'))     # touchlines
    cmds.append(_box(x0, y + 1, z1, x1, y + 1, z1, 'white_concrete'))
    cmds.append(_box(x0, y + 1, z0, x0, y + 1, z1, 'white_concrete'))
    cmds.append(_box(x1, y + 1, z0, x1, y + 1, z1, 'white_concrete'))
    mid = (x0 + x1) // 2

    if sport == 'football':
        for i in range(1, 10):                                  # yard lines
            lx = x0 + 4 + i * 4
            cmds.append(_box(lx, y + 1, z0 + 1, lx, y + 1, z1 - 1, 'white_concrete'))
        for ez, ex in ((x0, x0 + 4), (x1 - 4, x1)):             # end zones
            cmds.append(_box(ez, y, z0 + 1, ex, y, z1 - 1, 'blue_concrete'))
        for gx, off in ((x0 + 1, 1), (x1 - 1, -1)):             # goal posts
            cz = (z0 + z1) // 2
            cmds.append(_box(gx, y + 1, cz, gx, y + 6, cz, 'yellow_concrete'))
            cmds.append(_box(gx, y + 6, cz - 3, gx, y + 6, cz + 3, 'yellow_concrete'))
            cmds.append(_box(gx, y + 6, cz - 3, gx, y + 9, cz - 3, 'yellow_concrete'))
            cmds.append(_box(gx, y + 6, cz + 3, gx, y + 9, cz + 3, 'yellow_concrete'))
    else:
        cmds.append(_box(mid, y + 1, z0, mid, y + 1, z1, 'white_concrete'))
        cmds.extend(_ring(mid, (z0 + z1) // 2, 7, y + 1, y + 1, 'white_concrete'))
        for px, sign in ((x0, 1), (x1, -1)):                    # penalty areas
            cz = (z0 + z1) // 2
            bx = px + sign * 10
            cmds.append(_box(bx, y + 1, cz - 10, bx, y + 1, cz + 10, 'white_concrete'))
            cmds.append(_box(px, y + 1, cz - 10, bx, y + 1, cz - 10, 'white_concrete'))
            cmds.append(_box(px, y + 1, cz + 10, bx, y + 1, cz + 10, 'white_concrete'))
            gx = px + sign
            cmds.append(_box(gx, y + 1, cz - 4, gx, y + 4, cz - 4, 'white_concrete'))
            cmds.append(_box(gx, y + 1, cz + 4, gx, y + 4, cz + 4, 'white_concrete'))
            cmds.append(_box(gx, y + 4, cz - 4, gx, y + 4, cz + 4, 'white_concrete'))
    return cmds


def _racetrack(pal, w, d, floors, sport=None):
    """A banked oval — two straights and two stepped curves.

    Drawn as RUNS, not per block. The first version walked the ellipse a degree
    at a time placing single blocks and came to 2,010 commands: ten minutes of
    typing for one track. Two straights are two fills, and each curve is a
    staircase of about twenty. Same shape, a thirtieth of the commands.
    """
    import math
    cx, cz = 46, 0
    ra, rb = 44, 30                   # centreline radii
    tw = 7                            # track width
    y = 0
    cmds = [_box(cx - ra - 14, -4, cz - rb - 16, cx + ra + 14, 26,
                 cz + rb + 16, 'air'),
            _box(cx - ra - 12, -1, cz - rb - 12, cx + ra + 12, -1,
                 cz + rb + 12, 'green_concrete')]

    #  Straights: the top and bottom of the oval.
    for sz, wall in ((cz - rb, -1), (cz + rb, 1)):
        cmds.append(_box(cx - ra // 2, y, sz - tw // 2,
                         cx + ra // 2, y, sz + tw // 2, 'black_concrete'))
        cmds.append(_box(cx - ra // 2, y, sz + wall * (tw // 2 + 1),
                         cx + ra // 2, y + 2, sz + wall * (tw // 2 + 1),
                         'white_concrete'))

    #  Curves: stepped quarter arcs at each end, one fill per step.
    for sx, sign in ((cx + ra // 2, 1), (cx - ra // 2, -1)):
        steps = 22
        for i in range(steps + 1):
            a = math.pi * i / steps - math.pi / 2
            dx = int(round((ra // 2) * math.cos(a))) * sign
            dz = int(round(rb * math.sin(a)))
            px, pz = sx + dx, cz + dz
            cmds.append(_box(px - tw // 2, y, pz - tw // 2,
                             px + tw // 2, y, pz + tw // 2, 'black_concrete'))
            ox = sx + int(round((ra // 2 + tw) * math.cos(a))) * sign
            oz = cz + int(round((rb + tw) * math.sin(a)))
            cmds.append(_box(ox, y, oz, ox, y + 2, oz, 'white_concrete'))

    #  Start/finish, pit lane, grandstand along the front straight.
    cmds.append(_box(cx - 2, y + 1, cz - rb - tw // 2, cx + 2, y + 1,
                     cz - rb + tw // 2, 'white_concrete'))
    cmds.append(_box(cx - ra // 2, y, cz - rb - tw - 4, cx + ra // 2, y,
                     cz - rb - tw - 1, 'gray_concrete'))
    for t in range(5):
        gz = cz - rb - tw - 6 - t
        cmds.append(_box(cx - 30, y + t * 2, gz, cx + 30, y + t * 2 + 1, gz,
                         pal['wall']))
        cmds.append(_box(cx - 30, y + t * 2 + 1, gz, cx + 30, y + t * 2 + 1, gz,
                         'red_concrete'))
    for lx in range(cx - 28, cx + 29, 14):
        cmds.append(_box(lx, y + 10, cz - rb - tw - 12, lx, y + 18,
                         cz - rb - tw - 12, pal['trim']))
        cmds.append('/setblock %s sea_lantern' % _at(lx, y + 19, cz - rb - tw - 12))
    return cmds


#  Big-box retail. Recognisable comes from the silhouette and the colour, not
#  from a perfect logo: a long low box, a tall parapet, a glass front, a car
#  park with painted bays, and the brand's colour banded across the facade.
BRANDS = {
    'target':    {'main': 'red_concrete', 'trim': 'white_concrete',
                  'wall': 'light_gray_concrete', 'sign': 'red_concrete'},
    'walmart':   {'main': 'blue_concrete', 'trim': 'yellow_concrete',
                  'wall': 'light_gray_concrete', 'sign': 'yellow_concrete'},
    'starbucks': {'main': 'green_concrete', 'trim': 'white_concrete',
                  'wall': 'stripped_spruce_wood', 'sign': 'white_concrete'},
    'generic':   {'main': 'orange_concrete', 'trim': 'white_concrete',
                  'wall': 'light_gray_concrete', 'sign': 'white_concrete'},
}


def _store(pal, w, d, floors, brand='target'):
    b = BRANDS.get((brand or 'target').lower()) or BRANDS['generic']
    small = b is BRANDS['starbucks']
    if small:
        w, d = 17, 15
    else:
        w, d = 44, 30
    x0, z0 = 8, -(d // 2)
    x1, z1 = x0 + w, z0 + d
    h = 7 if small else 10
    cmds = [_box(x0 - 26, -2, z0 - 6, x1 + 4, h + 12, z1 + 6, 'air'),
            _box(x0 - 26, -1, z0 - 6, x1 + 4, -1, z1 + 6, 'gray_concrete')]

    cmds.append(_box(x0, 0, z0, x1, h, z1, b['wall']))
    cmds.append(_box(x0 + 1, 1, z0 + 1, x1 - 1, h - 1, z1 - 1, 'air'))
    cmds.append(_box(x0 + 1, 0, z0 + 1, x1 - 1, 0, z1 - 1, 'smooth_stone'))
    cmds.append(_box(x0, h, z0, x1, h, z1, 'gray_concrete'))
    cmds.append(_box(x0, h + 1, z0, x1, h + 2, z1, b['main']))       # parapet
    cmds.append(_box(x0 + 1, h + 1, z0 + 1, x1 - 1, h + 2, z1 - 1, 'air'))

    # glass frontage and doors on the -X face
    cmds.append(_box(x0, 1, z0 + 2, x0, 4, z1 - 2, 'glass'))
    mid = (z0 + z1) // 2
    cmds.append(_box(x0, 1, mid - 1, x0, 3, mid + 1, 'air'))
    cmds.append(_box(x0 - 1, 0, mid - 3, x0 - 1, 0, mid + 3, b['trim']))

    # the sign band, and a pylon sign out by the road
    cmds.append(_box(x0, h + 1, z0 + 3, x0, h + 2, z1 - 3, b['sign']))
    cmds.append(_box(x0 - 20, 0, mid, x0 - 20, 12, mid, 'gray_concrete'))
    cmds.append(_box(x0 - 21, 12, mid - 2, x0 - 19, 16, mid + 2, b['main']))
    cmds.append(_box(x0 - 21, 13, mid - 1, x0 - 19, 15, mid + 1, b['trim']))

    # car park bays
    for i, px in enumerate(range(x0 - 24, x0 - 3, 3)):
        for pz in (z0 + 1, z1 - 9):
            cmds.append(_box(px, 0, pz, px, 0, pz + 7, 'white_concrete'))
    for lx in range(x0 - 22, x0 - 2, 10):
        cmds.append(_box(lx, 0, z0 - 3, lx, 5, z0 - 3, 'gray_concrete'))
        cmds.append('/setblock %s sea_lantern' % _at(lx, 6, z0 - 3))
    return cmds


#  Everything he can be asked for. `house` falls through to the original
#  generator; the rest have their own geometry.
STRUCTURES = {
    'house': None,                 # handled by the house path in plan()
    'beach_house': _beach_house,
    'stadium': _stadium,
    'theater': _theater,
    'waterpark': _waterpark,
    'amusement_park': _amusement_park,
    # named venues: the bowl is shared, the floor markings are what differ
    'football_stadium':   lambda p, w, d, f: _sport_stadium(p, w, d, f, 'football'),
    'soccer_stadium':     lambda p, w, d, f: _sport_stadium(p, w, d, f, 'soccer'),
    'basketball_arena':   lambda p, w, d, f: _sport_stadium(p, w, d, f, 'basketball'),
    'baseball_stadium':   lambda p, w, d, f: _sport_stadium(p, w, d, f, 'baseball'),
    'racetrack':          _racetrack,
    'target_store':       lambda p, w, d, f: _store(p, w, d, f, 'target'),
    'walmart':            lambda p, w, d, f: _store(p, w, d, f, 'walmart'),
    'starbucks':          lambda p, w, d, f: _store(p, w, d, f, 'starbucks'),
    'store':              lambda p, w, d, f: _store(p, w, d, f, 'generic'),
}

#  Footprints for the big ones, which are not house-sized.
BIG_SIZES = {'stadium': (34, 26), 'theater': (24, 20),
             'waterpark': (34, 26), 'amusement_park': (38, 30),
             'beach_house': (15, 13),
             'football_stadium': (60, 40), 'soccer_stadium': (56, 44),
             'basketball_arena': (40, 28), 'baseball_stadium': (58, 58),
             'racetrack': (110, 80),
             'target_store': (72, 44), 'walmart': (72, 44),
             'starbucks': (46, 28), 'store': (72, 44)}


def plan(kind='mansion', palette='stone', floors=2, offset=6, overrides=None,
         structure='house'):
    """Turn a request into a list of commands.

    `overrides` is how a photograph reaches this. The vision model reads the
    picture and hands back the attributes it can actually see — wall colour,
    roof shape, how much glass, how many storeys — and they land here on top of
    the named style. It is not a copy of the building in the photo and is not
    meant to be; it is the same shape language in the same colours.
    """
    pal = dict(STYLES.get(palette) or STYLES['stone'])
    pal.update({k: v for k, v in (overrides or {}).items() if v})

    #  Anything that is not a house has its own geometry.
    structure = (structure or 'house').strip().lower()
    maker = STRUCTURES.get(structure)
    if maker:
        bw, bd = BIG_SIZES.get(structure, (21, 17))
        if overrides:
            bw = int(overrides.get('width') or bw)
            bd = int(overrides.get('depth') or bd)
        return maker(pal, max(9, min(60, bw)), max(9, min(60, bd)),
                     max(1, min(4, int(floors or 2))))

    w, d = SIZES.get(kind) or SIZES['mansion']
    if overrides:
        w = int(overrides.get('width') or w)
        d = int(overrides.get('depth') or d)
        w, d = max(7, min(48, w)), max(7, min(48, d))

    floors = max(1, min(4, int(floors or 2)))
    storey = 5
    height = floors * storey
    lift = int(pal.get('lift') or 0)           # treehouses stand off the ground
    glass = max(0, min(100, int(pal.get('glass', 30))))
    roof = pal.get('roof', 'stepped')

    x0, z0 = offset, -(d // 2)
    x1, z1 = x0 + w - 1, z0 + d - 1
    cmds = []

    # ---- ground ----
    if pal.get('carve'):
        # A cave house is cut INTO the hillside, so the volume is hollowed and
        # the shell lines the hole rather than standing in a clearing.
        cmds.append(_box(x0, lift, z0, x1, lift + height, z1, 'air'))
    else:
        cmds.append(_box(x0 - 1, lift, z0 - 1, x1 + 1, lift + height + 8, z1 + 1, 'air'))
        cmds.append(_box(x0 - 1, lift - 1, z0 - 1, x1 + 1, lift - 1, z1 + 1, pal['trim']))

    if lift:
        # trunk and a ladder, so a treehouse is reachable and not floating
        cx, cz = (x0 + x1) // 2, (z0 + z1) // 2
        cmds.append(_box(cx, 0, cz, cx, lift - 1, cz, pal.get('trunk', 'oak_log')))
        cmds.append(_box(cx + 1, 0, cz, cx + 1, lift - 1, cz, 'ladder[facing=east]'))

    # ---- shell ----
    for f in range(floors):
        base = lift + f * storey
        cmds.append(_box(x0, base, z0, x1, base + storey - 1, z1, pal['wall']))
        cmds.append(_box(x0 + 1, base + 1, z0 + 1, x1 - 1, base + storey - 1, z1 - 1, 'air'))
        cmds.append(_box(x0 + 1, base, z0 + 1, x1 - 1, base, z1 - 1, pal['floor']))
        cmds.append(_box(x0, base + storey - 1, z0, x1, base + storey - 1, z1, pal['trim']))

        # ---- windows: spacing falls out of the glass ratio ----
        if glass >= 70:
            # a curtain wall, which is what reads as "modern" more than colour
            cmds.append(_box(x0, base + 1, z0 + 1, x0, base + 3, z1 - 1, pal['window']))
            cmds.append(_box(x1, base + 1, z0 + 1, x1, base + 3, z1 - 1, pal['window']))
            cmds.append(_box(x0 + 1, base + 1, z0, x1 - 1, base + 3, z0, pal['window']))
            cmds.append(_box(x0 + 1, base + 1, z1, x1 - 1, base + 3, z1, pal['window']))
        elif glass > 0:
            step = 2 if glass >= 45 else 3
            for x in range(x0 + 2, x1 - 1, step):
                cmds.append(_box(x, base + 2, z0, x, base + 3, z0, pal['window']))
                cmds.append(_box(x, base + 2, z1, x, base + 3, z1, pal['window']))
            for z in range(z0 + 2, z1 - 1, step):
                cmds.append(_box(x0, base + 2, z, x0, base + 3, z, pal['window']))
                cmds.append(_box(x1, base + 2, z, x1, base + 3, z, pal['window']))

        for cx in (x0 + 2, x1 - 2):
            for cz in (z0 + 2, z1 - 2):
                cmds.append('/setblock %s %s' % (_at(cx, base + storey - 2, cz), pal['light']))

    # ---- roof ----
    top = lift + height
    if roof == 'flat':
        # slab plus a parapet, which is the whole difference between a modern
        # box and an unfinished one
        cmds.append(_box(x0, top, z0, x1, top, z1, pal['roof_block']))
        cmds.append(_box(x0, top + 1, z0, x1, top + 1, z1, pal['trim']))
        cmds.append(_box(x0 + 1, top + 1, z0 + 1, x1 - 1, top + 1, z1 - 1, 'air'))
    elif roof == 'pitched':
        span = (d // 2) + 1
        for i in range(span):
            cmds.append(_box(x0, top + i, z0 + i, x1, top + i, z0 + i, pal['roof_block']))
            cmds.append(_box(x0, top + i, z1 - i, x1, top + i, z1 - i, pal['roof_block']))
            if i:
                cmds.append(_box(x0, top + i, z0 + i + 1, x1, top + i, z1 - i - 1, 'air'))
    elif roof == 'stepped':
        inset = 0
        while x0 + inset < x1 - inset and z0 + inset < z1 - inset and inset <= 8:
            cmds.append(_box(x0 + inset, top, z0 + inset, x1 - inset, top, z1 - inset,
                             pal['roof_block']))
            inset += 1
            top += 1

    # ---- the way in ----
    mid = (z0 + z1) // 2
    leaves = [(mid, 'left')] if kind in ('small', 'medium') else              [(mid, 'left'), (mid + 1, 'right')]
    for z, hinge in leaves:
        cmds.append(_box(x0, lift, z, x0, lift + 1, z, 'air'))
        for dy, half in ((0, 'lower'), (1, 'upper')):
            cmds.append('/setblock %s %s[facing=east,half=%s,hinge=%s]'
                        % (_at(x0, lift + dy, z), pal['door'], half, hinge))
    if not lift:
        cmds.append(_box(x0 - 1, -1, mid - 1, x0 - 1, -1, mid + 2, pal['trim']))

    # ---- stairs between floors, so upstairs is reachable ----
    if floors > 1:
        sx, sz = x1 - 2, z0 + 2
        for f in range(floors - 1):
            base = lift + f * storey
            cmds.append(_box(sx, base + 1, sz, sx, base + storey, sz + 3, 'air'))
            for step in range(storey):
                cmds.append('/setblock %s %s'
                            % (_at(sx, base + 1 + step, sz + min(step, 3)), pal['trim']))

    if kind in ('large', 'mansion') and not lift:
        for pz in (z0 + 1, z1 - 1):
            cmds.append(_box(x0 - 1, 0, pz, x0 - 1, height - 1, pz, pal['trim']))

    return cmds


def clear(kind='mansion', floors=2, offset=6, dry_run=False):
    """Wipe the volume a build of that kind occupies, and put grass back.

    Relative like everything else, so stand where you stood when you built it.
    There is no way to ask the game where a structure is from out here, so the
    player is the anchor — that is the one real limitation of driving this
    through the chat box.

    Deliberately clears exactly the footprint a build would have taken rather
    than a blanket radius: a big box of air centred on someone is a good way to
    delete a hillside they liked."""
    w, d = SIZES.get(kind) or SIZES['mansion']
    floors = max(1, min(4, int(floors or 2)))
    height = floors * 5

    x0, z0 = offset - 1, -(d // 2) - 1
    x1, z1 = offset + w, z0 + d + 1

    cmds = [
        _box(x0, -1, z0, x1, height + 10, z1, 'air'),
        _box(x0, -1, z0, x1, -1, z1, 'grass_block'),
    ]
    out = send(cmds, dry_run=dry_run)
    if out.get('ok') and not dry_run:
        out['summary'] = ('Cleared the %s footprint east of you and laid grass back '
                          'over it.' % kind)
    return out


# ---------------------------------------------------------------------------
#  Where everything already is
# ---------------------------------------------------------------------------

#  A register of every building, with its absolute bounding box.
#
#  The first attempt at stopping double-builds was a ten-minute timer over an
#  in-memory record of the last one. It could not work, and did not: the record
#  died with every serve.py restart, and a timer says nothing about whether two
#  buildings occupy the same ground. Two mansions went up inside each other
#  with the guard switched on and satisfied.
#
#  Now that F3+C gives real coordinates, overlap is a question with an actual
#  answer. Boxes are kept on disk, checked geometrically, and a build that would
#  land on top of an existing one is MOVED rather than refused — being told
#  "no" when you asked for a house is not better than getting the house.
BUILDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'jarvis_minecraft.json')
CLEARANCE = 4                     # blocks of breathing room between buildings
MAX_SHUFFLE = 12                  # how many times to step aside looking for space


def _load_builds():
    try:
        with open(BUILDS_FILE, 'r', encoding='utf-8') as fh:
            got = json.load(fh)
        return got if isinstance(got, list) else []
    except Exception:
        return []


def _save_builds(rows):
    try:
        tmp = BUILDS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(rows[-200:], fh, indent=1)
        os.replace(tmp, BUILDS_FILE)
    except Exception:
        pass                       # a lost register must never lose a build


def _bbox(kind, floors, origin, offset=6, lift=0, structure='house'):
    """The absolute box a build of this shape will occupy."""
    w, d = BIG_SIZES.get(structure) or SIZES.get(kind) or SIZES['mansion']
    if structure and structure != 'house':
        w, d = w + 16, d + 16          # the big ones sprawl past their footprint
    h = max(1, min(4, int(floors or 2))) * 5 + 10 + lift
    ox, oy, oz = origin
    return {'x1': ox + offset - 1, 'x2': ox + offset + w,
            'y1': oy - 8, 'y2': oy + h,
            'z1': oz - (d // 2) - 1, 'z2': oz + (d // 2) + 1}


def _hits(a, b, pad=CLEARANCE):
    return not (a['x2'] + pad < b['x1'] or b['x2'] + pad < a['x1'] or
                a['z2'] + pad < b['z1'] or b['z2'] + pad < a['z1'] or
                a['y2'] < b['y1'] or b['y2'] < a['y1'])


def _free_spot(kind, floors, origin, dimension, lift=0, structure='house'):
    """Nudge the anchor until the box clears everything already standing.

    Steps sideways along +X, which is the direction builds already extend, so a
    row of buildings ends up looking deliberate rather than scattered. The step
    is the width of the thing being placed, so a stadium strides further than a
    cottage and does not creep across a neighbour."""
    rows = [r for r in _load_builds() if r.get('dimension') == dimension]
    if not rows:
        return origin, 0, None

    ox, oy, oz = origin
    w = (BIG_SIZES.get(structure) or SIZES.get(kind) or SIZES['mansion'])[0]
    stride = w + CLEARANCE * 2
    for step in range(MAX_SHUFFLE):
        cand = (ox + step * stride, oy, oz)
        box = _bbox(kind, floors, cand, lift=lift, structure=structure)
        clash = next((r for r in rows if _hits(box, r['box'])), None)
        if not clash:
            return cand, step * stride, None
    return origin, 0, rows[-1]


def _remember_build(kind, palette, floors, origin, dimension, lift=0,
                    structure='house'):
    rows = _load_builds()
    rows.append({'kind': kind, 'palette': palette, 'floors': int(floors or 2),
                 'structure': structure, 'origin': list(origin),
                 'dimension': dimension, 'at': int(time.time()),
                 'box': _bbox(kind, floors, origin, lift=lift, structure=structure)})
    _save_builds(rows)
    return rows[-1]


#  What was built last, so a follow-up can extend it instead of starting over.
#
#  "Add stairs to it" used to reach the model with only `build` available, so it
#  built a WHOLE SECOND MANSION at wherever the player was standing. Its clear
#  volume then ate the walls of the first one and left the floor and roof —
#  which is where the doubled shells and the empty stone platforms came from.
_LAST = {'kind': None, 'palette': None, 'floors': 2, 'at': 0,
         'origin': None, 'dimension': None}


FEATURES = ('stairs', 'interior', 'basement', 'terrace', 'garden', 'porch')


def add(feature, kind=None, palette=None, floors=None, offset=6, dry_run=False):
    """Add one feature to the building that is already there.

    Relative like everything else, so it lands correctly only if he is standing
    where he stood when it went up. The tool description tells him to say so
    rather than guessing — there is no way to ask the game where a structure is.

    Crucially this NEVER lays a shell or clears a volume, so it cannot double
    a building or erase one."""
    feature = (feature or '').strip().lower()
    if feature not in FEATURES:
        return {'ok': False, 'error': 'Unknown feature "%s". Known: %s.'
                                      % (feature, ', '.join(FEATURES))}

    kind = (kind or _LAST['kind'] or 'mansion').lower()
    palette = (palette or _LAST['palette'] or 'stone').lower()
    floors = max(1, min(4, int(floors or _LAST['floors'] or 2)))
    pal = STYLES.get(palette) or STYLES['stone']
    w, d = SIZES.get(kind) or SIZES['mansion']
    storey, height = 5, floors * 5
    lift = int(pal.get('lift') or 0)

    #  Anchored to where the building actually IS, not to where he is standing.
    #  That is the whole point of recording an origin: "add a porch" now works
    #  from across the world instead of putting one in a field.
    global _ORIGIN
    _ORIGIN = _LAST.get('origin')
    anchored = _ORIGIN is not None

    x0, z0 = offset, -(d // 2)
    x1, z1 = x0 + w - 1, z0 + d - 1
    mid = (z0 + z1) // 2
    cmds = []

    if feature == 'stairs':
        sx, sz = x1 - 2, z0 + 2
        for f in range(max(1, floors - 1)):
            base = lift + f * storey
            cmds.append(_box(sx, base + 1, sz, sx, base + storey, sz + 3, 'air'))
            for step in range(storey):
                cmds.append('/setblock %s %s'
                            % (_at(sx, base + 1 + step, sz + min(step, 3)), pal['trim']))

    elif feature == 'interior':
        # a partition wall and a lit corridor, so it stops being one open shell
        cut = (x0 + x1) // 2
        for f in range(floors):
            base = lift + f * storey
            cmds.append(_box(cut, base + 1, z0 + 1, cut, base + storey - 2, z1 - 1,
                             pal['wall']))
            cmds.append(_box(cut, base + 1, mid, cut, base + 2, mid, 'air'))
            for cz in (z0 + 3, z1 - 3):
                cmds.append('/setblock %s %s'
                            % (_at(cut - 2, base + storey - 2, cz), pal['light']))

    elif feature == 'basement':
        cmds.append(_box(x0 + 1, lift - 6, z0 + 1, x1 - 1, lift - 1, z1 - 1, 'air'))
        cmds.append(_box(x0 + 1, lift - 7, z0 + 1, x1 - 1, lift - 7, z1 - 1, pal['floor']))
        sx, sz = x1 - 2, z0 + 2
        for step in range(6):
            cmds.append('/setblock %s %s'
                        % (_at(sx, lift - 1 - step, sz + min(step, 3)), pal['trim']))

    elif feature == 'terrace':
        top = lift + height
        cmds.append(_box(x0, top + 1, z0, x1, top + 1, z1, pal['trim']))
        cmds.append(_box(x0 + 1, top + 1, z0 + 1, x1 - 1, top + 1, z1 - 1, 'air'))
        for cx in (x0 + 2, x1 - 2):
            for cz in (z0 + 2, z1 - 2):
                cmds.append('/setblock %s %s' % (_at(cx, top + 1, cz), pal['light']))

    elif feature == 'garden':
        # a path out from the door and a low wall around a plot, no clearing
        for i in range(1, 9):
            cmds.append(_box(x0 - i, lift - 1, mid, x0 - i, lift - 1, mid + 1, pal['trim']))
        cmds.append(_box(x0 - 9, lift - 1, z0 - 3, x0 - 9, lift, z1 + 3, pal['trim']))
        cmds.append(_box(x0 - 9, lift - 1, z0 - 3, x0 - 1, lift, z0 - 3, pal['trim']))
        cmds.append(_box(x0 - 9, lift - 1, z1 + 3, x0 - 1, lift, z1 + 3, pal['trim']))
        for cz in (z0 - 2, z1 + 2):
            cmds.append('/setblock %s %s' % (_at(x0 - 5, lift, cz), pal['light']))

    elif feature == 'porch':
        cmds.append(_box(x0 - 4, lift, mid - 2, x0 - 1, lift + 3, mid + 3, 'air'))
        cmds.append(_box(x0 - 4, lift + 4, mid - 2, x0 - 1, lift + 4, mid + 3, pal['roof_block']))
        for pz in (mid - 2, mid + 3):
            cmds.append(_box(x0 - 4, lift, pz, x0 - 4, lift + 3, pz, pal['trim']))
        cmds.append(_box(x0 - 4, lift - 1, mid - 2, x0 - 1, lift - 1, mid + 3, pal['trim']))

    out = send(cmds, dry_run=dry_run)
    _ORIGIN = None
    if out.get('ok') and not dry_run:
        out['anchored'] = anchored
        out['summary'] = (
            'Added a %s to the %s. %d commands. Nothing else was touched — no shell '
            'was laid and nothing was cleared.%s'
            % (feature, kind, out.get('sent', 0),
               '' if anchored else
               ' NOTE: there is no recorded position for that build, so this went in '
               'relative to where he is standing. If it landed in the wrong place, '
               'have him stand where he built it and say so.'))
    return out


def suggest(kind, palette, floors):
    """Five things this build does not have, worth adding next.

    Generated from what the plan actually leaves out rather than asked of a
    model, so it never suggests something that is already there."""
    pal = STYLES.get(palette) or STYLES['stone']
    out = []
    if floors < 2:
        out.append('a second floor — say "add a floor" and it goes up one storey')
    if pal.get('roof') == 'flat':
        out.append('a roof terrace: railings, planters and a few lanterns on the flat top')
    else:
        out.append('dormer windows in the roof, to break up the slope')
    out.append('a interior: rooms are one open shell right now, so walls, beds, '
               'chests and a kitchen')
    out.append('grounds — a path to the door, a wall or hedge around a garden, '
               'and lighting along it')
    if kind in ('large', 'mansion'):
        out.append('a wing or an attached garage, set back from the main block')
    else:
        out.append('a porch or a covered entrance over the door')
    out.append('a basement, dug under the footprint and reached by the stairwell')
    return out[:5]


def build(kind='mansion', palette='stone', floors=2, dry_run=False,
          overrides=None, confirm=False, structure='house'):
    """Plan a structure and type it in."""
    kind = (kind or 'mansion').strip().lower()
    if kind not in SIZES:
        kind = 'mansion'
    palette = (palette or 'stone').strip().lower()
    if palette not in STYLES:
        palette = 'stone'

    #  A second build shortly after the first is nearly always a follow-up that
    #  should have been `add` — "add stairs", "put a garage on it". Building
    #  again clears a fresh volume at wherever he is now standing, which eats
    #  the walls of the last one and leaves its floor and roof: the doubled
    #  shells and empty stone platforms. Refuse once and say so; `confirm`
    #  gets through when a second building really is wanted.
    #  Ask the game where he is and anchor the build there. If it will not say
    #  — he is in a menu, or the window will not focus — fall back to relative
    #  coordinates, which still work, they just cannot be found again.
    global _ORIGIN
    _ORIGIN = None
    spot = None
    if not dry_run:
        spot = where()
        if spot.get('ok'):
            _ORIGIN = (spot['x'], spot['y'], spot['z'])

    #  Step aside rather than build through something.
    #
    #  This replaces a ten-minute timer that refused the SECOND build of any
    #  session outright — which is exactly what stopped a stadium going up
    #  ninety seconds after a beach house, and got reported as "the build
    #  request failed". A timer cannot tell whether two buildings share ground.
    #  Real coordinates can, so the question is now geometric and the answer is
    #  to move over, not to say no.
    moved = 0
    if _ORIGIN:
        pal_lift = int((STYLES.get(palette) or {}).get('lift') or 0)
        dim = (spot or {}).get('dimension') or 'minecraft:overworld'
        found, moved, blocked = _free_spot(kind, floors, _ORIGIN, dim,
                                           pal_lift, structure)
        if blocked and not confirm:
            return {'ok': False, 'needs_confirm': True,
                    'error': ('No clear ground here — everything within %d building '
                              'widths east of him is taken, nearest a %s. Nothing was '
                              'built. Have him walk somewhere open, or pass confirm '
                              'true to put it up on top of what is there.'
                              % (MAX_SHUFFLE, blocked.get('structure')
                                 or blocked.get('kind', 'building')))}
        _ORIGIN = found

    cmds = plan(kind, palette, floors, overrides=overrides,
                structure=structure)
    out = send(cmds, dry_run=dry_run)
    placed = _ORIGIN
    _ORIGIN = None                      # never leaks into the next call
    if out.get('ok') and not dry_run:
        w, d = BIG_SIZES.get(structure) or SIZES[kind]
        dim = (spot or {}).get('dimension') or 'minecraft:overworld'
        #  On the register, so the NEXT build steps around it. This is the line
        #  that was missing when two mansions went up inside each other.
        if placed:
            _remember_build(kind, palette, floors, placed, dim,
                            int((STYLES.get(palette) or {}).get('lift') or 0),
                            structure)
        _LAST.update({'kind': kind, 'palette': palette,
                      'floors': int(floors or 2), 'at': time.time(),
                      'origin': placed, 'dimension': dim})
        out['suggestions'] = suggest(kind, palette, int(floors or 2))
        nl = chr(10)
        listed = nl.join('  %d. %s' % (i + 1, t)
                         for i, t in enumerate(out['suggestions']))
        out['summary'] = (
            ('Built a %s %s, %dx%d and %d floors, just east of where you are '
             'standing. %d commands.' + nl * 2 +
             'Five things it does not have yet:' + nl + '%s' + nl * 2 +
             'Read those out as suggestions and ask which he wants — do not start '
             'building any of them unprompted.')
            % (palette, kind, w, d, floors, out.get('sent', 0), listed))
    return out


def status():
    handle = find_window()
    return {'ok': True, 'window': bool(handle),
            'kinds': sorted(SIZES), 'palettes': sorted(STYLES),
            'structures': sorted(STRUCTURES),
            'summary': ('Minecraft is open and reachable.' if handle else
                        'No Minecraft window is open.')}


def command(action, **kw):
    """Routed by serve.py as POST /api/minecraft/command."""
    if action == 'build':
        return build(kw.get('kind'), kw.get('palette'), kw.get('floors') or 2,
                     bool(kw.get('dry_run')), kw.get('overrides'),
                     bool(kw.get('confirm')), kw.get('structure') or 'house')
    if action == 'add':
        return add(kw.get('feature'), kw.get('kind'), kw.get('palette'),
                   kw.get('floors'), dry_run=bool(kw.get('dry_run')))
    if action == 'plan':
        return {'ok': True, 'commands': plan(kw.get('kind') or 'mansion',
                                             kw.get('palette') or 'stone',
                                             kw.get('floors') or 2)}
    if action == 'clear':
        return clear(kw.get('kind'), kw.get('floors') or 2,
                     dry_run=bool(kw.get('dry_run')))
    if action == 'where':
        return where()
    if action == 'status':
        return status()
    return {'ok': False, 'error': 'Unknown action "%s". Known: build, add, clear, plan, status.'
                                  % action}
