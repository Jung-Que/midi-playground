# noinspection PyUnresolvedReferences
from typing import Union, Optional, BinaryIO
# noinspection PyUnresolvedReferences
from errors import *
from config import Config, get_colors
from translations import TRANSLATIONS
# noinspection PyUnresolvedReferences
from time import perf_counter as get_current_time  # monotonic, sub-microsecond
from time import sleep
import moderngl
import mido
import pygame
from sys import platform
import subprocess
from enum import Enum
from os.path import join
from math import sin, pi
from sys import setrecursionlimit
setrecursionlimit(10000)  # increase if more notes


# vsync framerate if on windows, else 60
if platform == "win32":
    try:
        # noinspection PyPackageRequirements
        import win32api
        FRAMERATE = win32api.EnumDisplaySettings(win32api.EnumDisplayDevices().DeviceName, -1).DisplayFrequency
    except ModuleNotFoundError:
        print("The win32api python module is not found! Install it with \"pip install pywin32\"")
        FRAMERATE = 60
    except Exception:  # e.g. pywintypes.error in a background map-worker process: it never draws, 60 is fine
        FRAMERATE = 60
elif "linux" in platform:
    # noinspection PyPackageRequirements
    from Xlib import display
    # noinspection PyPackageRequirements
    from Xlib.ext import randr
    d = display.Display()
    default_screen = d.get_default_screen()
    info = d.screen(default_screen)

    resources = randr.get_screen_resources(info.root)
    active_modes = set()
    for crtc in resources.crtcs:
        crtc_info = randr.get_crtc_info(info.root, crtc, resources.config_timestamp)
        if crtc_info.mode:
            active_modes.add(crtc_info.mode)

    for mode in resources.modes:
        if mode.id in active_modes:
            FRAMERATE = round(mode.dot_clock / (mode.h_total * mode.v_total))
            break
else:
    FRAMERATE = 60


# camera follow modes
class CameraFollow(Enum):
    Center = 0  # center the square
    Lazy = 1  # lazy camera, used by Crazy Nutter 101
    Smoothed = 2  # smoothed camera, interpolates camera a little bit between current and previous every frame
    Predictive = 3  # smoothed camera, but you can see where the square will bounce better
    TargetLead = 4  # frame the square and its immediate target together


def get_camera_follow(value, default=CameraFollow.TargetLead) -> CameraFollow:
    """Load old or invalid saved camera values without crashing startup."""
    try:
        return CameraFollow(int(value))
    except (TypeError, ValueError):
        return default


def read_osu_file(filedata: bytes):
    filedata = filedata.decode("utf-8")
    filelines = filedata.splitlines(False)
    started_counting = False
    timestamps = []
    for line in filelines:
        if not started_counting:
            if "HitObjects" in line:
                started_counting = True
            continue
        if len(line) < 3:
            continue
        args = line.split(",")
        if (int(args[3]) & 3) == 0:
            continue
        timestamps.append(int(args[2])/1000)
    return timestamps


def surf_to_texture(in_surface: pygame.Surface) -> moderngl.Texture:
    tex = Config.ctx.texture(in_surface.get_size(), 4)
    smooth = getattr(Config, "texture_smooth", False)
    tex.filter = (moderngl.LINEAR, moderngl.LINEAR) if smooth else (moderngl.NEAREST, moderngl.NEAREST)
    tex.swizzle = 'BGRA'
    tex.write(in_surface.get_view('1'))
    return tex


def update_screen(screen: pygame.Surface, glsl_program: moderngl.Program, render_object: moderngl.VertexArray):
    frame_tex = surf_to_texture(screen)
    frame_tex.use(0)
    glsl_program['tex'] = 0
    if "ascii.glsl" in Config.shader_file_name:
        if Config.ascii_tex is None:
            Config.ascii_tex = surf_to_texture(pygame.image.load('./assets/shaders/ascii.png').convert_alpha())
            Config.ascii_tex.use(1)
        try:
            glsl_program['asciipng'] = 1
        except KeyError:
            pass
    render_object.render(mode=moderngl.TRIANGLE_STRIP)

    pygame.display.flip()

    frame_tex.release()


def open_file(filename):
    if platform == "win32":
        from os import startfile
        startfile(filename)
    else:
        opener = "open" if platform == "darwin" else "xdg-open"
        subprocess.call([opener, filename])


# noinspection PyUnresolvedReferences
def read_midi_file(file):
    midi_file = mido.MidiFile(file=file)

    notes = []
    current_time = 0

    for msg in midi_file:
        if msg.type == 'note_on' and msg.velocity != 0:
            timestamp = current_time + msg.time
            notes.append(round(timestamp*1000)/1000)
        current_time += msg.time
    return notes


# (time in seconds, pitch or None, duration in seconds)
MapNote = tuple[float, Optional[int], float]


def read_midi_notes(file, skip_channels=()) -> list[MapNote]:
    """Like read_midi_file, but keeps each onset's pitch and length. Simultaneous notes collapse to the highest one."""
    open_notes: dict[tuple[int, int], list[float]] = {}
    raw: list[list] = []  # [time, pitch, duration]
    current_time = 0.0
    for msg in mido.MidiFile(file=file):
        current_time += msg.time
        if getattr(msg, "channel", None) in skip_channels:
            continue
        if msg.type == "note_on" and msg.velocity != 0:
            timestamp = round(current_time * 1000) / 1000
            raw.append([timestamp, msg.note, 0.0])
            open_notes.setdefault((msg.channel, msg.note), []).append(len(raw) - 1)
        elif msg.type in ("note_off", "note_on"):
            stack = open_notes.get((msg.channel, msg.note))
            if stack:
                index = stack.pop(0)
                raw[index][2] = max(current_time - raw[index][0], 0.0)

    notes: list[MapNote] = []
    for timestamp, pitch, duration in raw:
        if notes and notes[-1][0] == timestamp:
            _, other_pitch, other_duration = notes[-1]
            notes[-1] = (timestamp, max(pitch, other_pitch), max(duration, other_duration))
        else:
            notes.append((timestamp, pitch, duration))
    return notes


def thin_notes(notes: list[MapNote], threshold=30) -> list[MapNote]:
    """remove_too_close_values for MapNotes, with ornaments resolved onto the note they lead into.

    A note within `threshold` ms of the last kept note is normally dropped. But if the kept note is a short ornament
    (it has stopped sounding by the time the next note starts and is shorter than the threshold), the later note takes
    its place, so the peg lands on the main note's beat. A note that already replaced one is not replaced again,
    so a trill thins out instead of sliding to its last note."""
    threshold_seconds = threshold / 1000
    kept: list[MapNote] = []
    replaced_last = False
    for note in notes:
        if not kept or kept[-1][0] + threshold_seconds <= note[0]:
            kept.append(note)
            replaced_last = False
            continue
        last_time, _, last_duration = kept[-1]
        is_ornament = 0 < last_duration < threshold_seconds and last_time + last_duration <= note[0] + 1e-6
        if is_ornament and not replaced_last:
            kept[-1] = note
            replaced_last = True
    return kept


# remove values that are too close to each other
def remove_too_close_values(lst: list[float], threshold=30) -> list[float]:
    """Assumes the list is sorted"""
    new = []
    before = None
    for _ in lst:
        if before is None:
            before = _
            new.append(_)
            continue
        if before+threshold/1000 > _:
            continue
        before = _
        new.append(_)
    return new


def fix_overlap(rects: list[pygame.Rect], callback=None):
    if callback is None:
        callback = lambda _: None
    xvs = set()
    yvs = set()
    for rect in rects:
        xvs.add(rect.right)
        xvs.add(rect.left)
        yvs.add(rect.top)
        yvs.add(rect.bottom)
    xvs = sorted(list(xvs))
    yvs = sorted(list(yvs))

    outputs = []
    for xv1 in range(len(xvs)-1):
        xv2 = xv1+1
        for yv1 in range(len(yvs)-1):
            yv2 = yv1+1
            r = pygame.Rect(xvs[xv1], yvs[yv1], xvs[xv2]-xvs[xv1], yvs[yv2]-yvs[yv1])
            if r.collidelist(rects)+1:
                outputs.append(r)

        if callback(f"Checking minirectangles ({int(100*xv1*len(yvs)/(len(xvs)*len(yvs)))}% done)"):
            raise UserCancelsLoadingError()

    callback("Merging adjacent minirectangles")

    for ai in range(len(outputs)-1):
        a = outputs[ai+1]
        b = outputs[ai]
        if a.width == 0 or a.height == 0 or b.width == 0 or b.height == 0:
            continue
        if not (a.right == b.left or a.left == b.right or a.top == b.bottom or b.bottom == a.top):
            continue
        if a.x == b.x and a.width == b.width:
            a.y = min(a.y, b.y)
            a.height = a.height+b.height
            b.height = 0
            continue

    outputs = [out for out in outputs if out.width > 0 and out.height > 0]

    outputs.sort(key=lambda _: _.y*100000+_.x)

    for ai in range(len(outputs)-1):
        a = outputs[ai+1]
        b = outputs[ai]
        if a.width == 0 or a.height == 0 or b.width == 0 or b.height == 0:
            continue
        if not (a.right == b.left or a.left == b.right or a.top == b.bottom or b.bottom == a.top):
            continue
        if a.y == b.y and a.height == b.height:
            a.x = min(a.x, b.x)
            a.width = a.width+b.width
            b.width = 0
            continue

    callback("Finished loading")

    return [out for out in outputs if out.width > 0 and out.height > 0]


_font_registry: dict[str, pygame.font.Font] = {}


def lang_key(key: str):
    """
    useful for different languages, so everyone can be able to read the text.
    translations are found in translations.py btw
    """
    english_language = TRANSLATIONS["english"]
    my_language = TRANSLATIONS.get(Config.language, {})
    return my_language.get(key, english_language.get(key, "<missing>"))


def get_font(size: int = 24, font_file_name: str = None) -> pygame.font.Font:
    if font_file_name is None:
        font_file_name = lang_key("font")
    font_path = f'./assets/fonts/{font_file_name}'
    fn_id = f"[{font_path}/{size}]"
    if fn_id not in _font_registry:
        try:
            _font_registry[fn_id] = pygame.font.Font(font_path, size)
        except FileNotFoundError:
            print(f"Font {fn_id} not found!")
            _font_registry[fn_id] = pygame.font.SysFont("Arial", size)
    return _font_registry[fn_id]


_channels = []
_sound_registry: dict[str, pygame.mixer.Sound] = {}


def play_sound(snd_name: str, vol: float = 0.5):
    if len(_channels) == 0:
        pygame.mixer.set_num_channels(40)
        for _ in range(1, 20):
            _channels.append(pygame.mixer.Channel(_))

    if snd_name not in _sound_registry:
        _sound_registry[snd_name] = pygame.mixer.Sound(join("assets", snd_name))

    chan = pygame.mixer.find_channel(False)
    if not chan:
        return False
    if chan.get_busy():
        return False
    chan.set_volume(vol)
    chan.play(_sound_registry[snd_name])
    return True


def interpolate_fn(n):
    """Interpolate sigmoidally from 0-1"""
    n = min(max(n, 0), 1)

    return sin(pi * (n - 0.5)) / 2 + 0.5


class FramePacer:
    """Frames at exact 1/fps intervals, presented just before a display refresh.

    pygame's Clock.tick waits whole milliseconds (60 fps comes out as ~61.3), which on a 180 Hz screen shows
    some frames for 2 refreshes instead of 3: a visible hitch several times a second. Here each frame has a
    present deadline on a fixed 1/fps grid; on Windows the deadline is snapped to the compositor's vblank so
    every frame stays exactly N refreshes. Usage per frame: plan() -> draw (state for that time) -> wait() ->
    flip; dt is the time between two presents.
    """

    PRESENT_MARGIN = 0.0025  # present this long before the refresh, so the compositor takes it for that refresh

    def __init__(self):
        self.target = None
        self.dt = 1 / 60
        self._dwm = None
        self._freq = None
        if platform == "win32":
            try:
                import ctypes
                self._ctypes = ctypes
                self._dwm = ctypes.windll.dwmapi
                freq = ctypes.c_int64()
                ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
                self._freq = float(freq.value)
                self._info = _dwm_timing_info_type(ctypes)()
            except Exception:
                self._dwm = None

    def _vblank_grid(self):
        """(time of the last vblank, refresh period) in perf_counter seconds, or None."""
        if self._dwm is None:
            return None
        info = self._info
        info.cbSize = self._ctypes.sizeof(info)
        if self._dwm.DwmGetCompositionTimingInfo(None, self._ctypes.byref(info)) != 0 or not info.qpcRefreshPeriod:
            return None
        return info.qpcVBlank / self._freq, info.qpcRefreshPeriod / self._freq

    def plan(self, fps: float) -> float:
        """Present time of the frame about to be drawn (draw the world as it is at this moment)."""
        period = 1.0 / max(float(fps), 1.0)
        now = get_current_time()
        previous = self.target
        target = previous + period if previous is not None else now + period
        if target < now + 0.001:  # fell behind (a stall): restart the grid from now instead of rushing
            target = now + period
        grid = self._vblank_grid()
        if grid is not None and abs(period / grid[1] - round(period / grid[1])) > 0.05:
            grid = None
        if grid is not None:
            vblank, refresh = grid
            n = round((target + self.PRESENT_MARGIN - vblank) / refresh)
            snapped = vblank + n * refresh - self.PRESENT_MARGIN
            if snapped < now + 0.001:
                snapped += refresh * max(1, round(period / refresh))
            target = snapped
        self.dt = (target - previous) if previous is not None else period
        self.target = target
        return target

    def wait(self):
        """Sleep, then spin the last moment, until the planned present time."""
        if self.target is None:
            return
        while True:
            remaining = self.target - get_current_time()
            if remaining <= 0:
                return
            if remaining > 0.002:
                sleep(remaining - 0.0015)


def _dwm_timing_info_type(ctypes):
    u32, u64 = ctypes.c_uint32, ctypes.c_uint64
    fields = [("cbSize", u32), ("rateRefreshNum", u32), ("rateRefreshDen", u32), ("qpcRefreshPeriod", u64),
              ("rateComposeNum", u32), ("rateComposeDen", u32), ("qpcVBlank", u64), ("cRefresh", u64),
              ("cDXRefresh", u32), ("qpcCompose", u64), ("cFrame", u64), ("cDXPresent", u32),
              ("cRefreshFrame", u64), ("cFrameSubmitted", u64), ("cDXPresentSubmitted", u32),
              ("cFrameConfirmed", u64), ("cDXPresentConfirmed", u32), ("cRefreshConfirmed", u64),
              ("cDXRefreshConfirmed", u32), ("cFramesLate", u64), ("cFramesOutstanding", u32),
              ("cFrameDisplayed", u64), ("qpcFrameDisplayed", u64), ("cRefreshFrameDisplayed", u64),
              ("cFrameComplete", u64), ("qpcFrameComplete", u64), ("cFramePending", u64), ("qpcFramePending", u64),
              ("cFramesDisplayed", u64), ("cFramesComplete", u64), ("cFramesPending", u64),
              ("cFramesAvailable", u64), ("cFramesDropped", u64), ("cFramesMissed", u64),
              ("cRefreshNextDisplayed", u64), ("cRefreshNextPresented", u64), ("cRefreshesDisplayed", u64),
              ("cRefreshesPresented", u64), ("cRefreshStarted", u64), ("cPixelsReceived", u64),
              ("cPixelsDrawn", u64), ("cBuffersEmpty", u64)]
    return type("DWM_TIMING_INFO", (ctypes.Structure,), {"_pack_": 1, "_fields_": fields})
