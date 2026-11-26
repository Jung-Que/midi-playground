"""YouTube live mode: endless category playlist, chat commands, top prompt panel and test captions.

Every on-screen word comes from live.json (texts / labels / captions). The defaults are bracketed placeholders
only; the streamer writes the real wording.

Chat commands are collected only while an input window is open (it opens every `window_every_seconds` for
`window_open_seconds`). When a window closes:
  * song requests fill the empty queue slots in the order they arrived (one per viewer per window),
  * slots still empty are filled automatically with random songs from the category,
  * the most-voted theme (ties: the one voted first) is applied.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import queue
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pygame

from config import Config
from paths import user_path, resource_path

logger = logging.getLogger("midi_playground.live")

PLACEHOLDER_SETTINGS = {
    # behaviour
    "category": "",                  # "" = every song; "a,b" = songs in a or b
    "video_id": "",                  # YouTube video id or watch/live URL
    "chat_source": "auto",           # auto | pytchat | chat-downloader | file | none
    "test_chat_file": "live_test_chat.txt",  # file source: one "name: message" per line, appended while running
    "song_command": "!song",
    "color_command": "!color",
    "help_command": "!help",
    "help_cooldown_seconds": 30,     # one reply per 30 s at most (the chat API allows ~200 messages a day)
    # !help reply, split into chat messages of at most 200 characters; {songs} / {themes} are filled in
    "help_messages": ["!song <title or number>  !color <theme>", "Songs: {songs}", "Themes: {themes}"],
    "queue_size": 3,
    "window_every_seconds": 60,
    "window_open_seconds": 20,
    "recent_song_memory": 3,         # auto-fill avoids the last N songs played
    "themes": ["dark_modern", "dark", "light", "rainbow", "autumn", "winter", "spring", "magenta", "monochromatic"],
    # layout (fractions of the screen height)
    "width": 1080,
    "height": 1920,
    "window_height": 0,              # on-screen window height in px; 0 = 1280, or 90% of a smaller monitor
    "window_pos": [],                # [x, y] of the window; [] = right side of the primary monitor
    "zoom": 1.8,                     # playfield scale: square 50 px -> 90 px in the 1080-wide frame
    "top_panel_ratio": 0.24,
    "bottom_reserve_ratio": 0.16,    # kept clear for the Shorts player UI (title, buttons)
    "font_file": "C:/Windows/Fonts/malgunbd.ttf",
    "caption_seconds": 5,
    "show_hud": False,               # performance numbers in the corner (debug)
    # settings.json values used while live (not saved), so every PC streams the same look
    "game_settings": {},
    "label_color": "#A5B9CD",        # Now / Next / Queue headings               # performance numbers in the corner (debug)
    # every visible word - placeholders only, the streamer writes them
    "texts": [],
    "captions": ["!song <title>      !color <theme>"],
    "labels": {
        "now": "Now",
        "next": "Next",
        "queue": "Queue",
        "empty_slot": "Empty",
        "auto_slot": "Auto",
        "window_open": "Open {seconds}",
        "window_closed": "Next input in {seconds}",
        "color_vote": "Theme vote: {theme} {votes}",
        "requested_by": "{user}",
    },
}


def path_key(path) -> str:
    """One spelling per pack file, so packs found by a folder scan match the songs already loaded."""
    return os.path.normcase(os.path.abspath(str(path)))


def live_settings_path() -> Path:
    return user_path("live.json")


def load_live_settings(path: Path | None = None) -> dict:
    """live.json merged over the placeholder defaults; the file is created (with placeholders) when missing."""
    path = Path(path) if path is not None else live_settings_path()
    data = {}
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("live.json unreadable, defaults used: %s", exc)
    if not isinstance(data, dict):
        data = {}
    merged = json.loads(json.dumps(PLACEHOLDER_SETTINGS))
    for key, value in data.items():
        if key == "labels" and isinstance(value, dict):
            merged["labels"].update({k: v for k, v in value.items() if isinstance(v, str)})
        elif key in merged and isinstance(value, type(merged[key])):
            merged[key] = value
        elif key in merged and isinstance(merged[key], (int, float)) and isinstance(value, (int, float)):
            merged[key] = value
    if not path.is_file():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=4), encoding="utf-8")
        except OSError:
            pass
    merged["queue_size"] = max(1, min(int(merged["queue_size"]), 10))
    merged["window_open_seconds"] = max(1.0, float(merged["window_open_seconds"]))
    merged["window_every_seconds"] = max(merged["window_open_seconds"], float(merged["window_every_seconds"]))
    return merged


# ---------------------------------------------------------------------------------------------- categories

def normalize_category(value: str) -> str:
    return str(value).strip().lower()


def categories_from_metadata(metadata: dict) -> list[str]:
    """`category` (string or list) and `categories` (list) are both accepted; a song may be in several."""
    raw = []
    for key in ("category", "categories"):
        value = metadata.get(key)
        if isinstance(value, str):
            raw.extend(value.split(","))
        elif isinstance(value, (list, tuple)):
            raw.extend(item for item in value if isinstance(item, str))
    result = []
    for item in raw:
        name = normalize_category(item)
        if name and name not in result:
            result.append(name)
    return result


def songs_in_category(songs: list, category: str) -> list:
    wanted = [normalize_category(part) for part in str(category or "").split(",") if part.strip()]
    if not wanted or wanted == ["all"]:
        return list(songs)
    return [song for song in songs if set(getattr(song, "categories", [])) & set(wanted)]


# ---------------------------------------------------------------------------------------------- matching

def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text).lower())


def match_choice(query: str, names: list[str]) -> Optional[int]:
    """Index into names for a chat query: a 1-based number, exact, prefix, substring, then fuzzy."""
    query = query.strip()
    if not query or not names:
        return None
    if query.isdigit():
        number = int(query)
        return number - 1 if 1 <= number <= len(names) else None
    q = _norm(query)
    if not q:
        return None
    normalized = [_norm(name) for name in names]
    for test in (lambda n: n == q, lambda n: n.startswith(q), lambda n: q in n):
        hits = [i for i, n in enumerate(normalized) if n and test(n)]
        if hits:
            return min(hits, key=lambda i: len(normalized[i]))
    close = difflib.get_close_matches(q, normalized, n=1, cutoff=0.6)
    return normalized.index(close[0]) if close else None


# ---------------------------------------------------------------------------------------------- queue

@dataclass
class QueueEntry:
    index: int
    requested_by: str = ""  # "" = filled automatically


@dataclass
class CommandWindow:
    every: float
    open_for: float
    started_at: float = 0.0

    def state(self, now: float) -> tuple[bool, float]:
        """(open, seconds until it closes/opens) - the first window opens right away."""
        phase = (now - self.started_at) % self.every
        if phase < self.open_for:
            return True, self.open_for - phase
        return False, self.every - phase

    def cycle(self, now: float) -> int:
        return int((now - self.started_at) // self.every)


class LiveQueue:
    def __init__(self, songs: list, size: int, recent_memory: int = 3, rng: Optional[random.Random] = None):
        self.songs = list(songs)
        self.size = size
        self.entries: list[QueueEntry] = []
        self.pending: list[QueueEntry] = []  # requests of the open window, in arrival order
        self.recent: list[int] = []
        self.recent_memory = recent_memory
        self.rng = rng or random.Random()
        self.locked: list[int] = []  # songs already handed to the player (playing / preparing)
        self.disabled: set[int] = set()  # pack removed from disk or no longer in the category

    def names(self) -> list[str]:
        return [getattr(song, "name", str(song)) for song in self.songs]

    def _taken(self) -> set[int]:
        return {entry.index for entry in self.entries} | {entry.index for entry in self.pending} | set(self.locked)

    def request(self, user: str, query: str) -> Optional[int]:
        """Record a request for the open window. Returns the song index, or None when it was not accepted."""
        index = match_choice(query, self.names())
        if index is None or index in self.disabled:
            return None
        self.pending = [entry for entry in self.pending if entry.requested_by != user]  # latest request counts
        if index in self._taken():
            return None
        self.pending.append(QueueEntry(index, user))
        return index

    def _random_index(self) -> int:
        if len(self.songs) == 1:
            return 0
        avoid = set(self.recent[-min(self.recent_memory, len(self.songs) - 1):]) | self._taken() | self.disabled
        choices = [i for i in range(len(self.songs)) if i not in avoid]
        # small categories: give up the recent-song rule first, then repeats inside the queue
        queued = {entry.index for entry in self.entries} | {entry.index for entry in self.pending}
        for allowed in (lambda i: i not in queued and i not in self.recent[-1:], lambda i: i not in self.recent[-1:],
                        lambda i: True):
            if choices:
                break
            choices = [i for i in range(len(self.songs)) if allowed(i) and i not in self.disabled] or                 [i for i in range(len(self.songs)) if allowed(i)]
        return self.rng.choice(choices)

    def close_window(self):
        """Requests fill the empty slots in arrival order; whatever is still empty is filled automatically."""
        for entry in self.pending:
            if len(self.entries) >= self.size:
                break
            if entry.index not in {e.index for e in self.entries} | set(self.locked):
                self.entries.append(entry)
        self.pending = []
        while len(self.entries) < self.size:
            self.entries.append(QueueEntry(self._random_index()))

    def pop_next(self, _base_index: int = -1) -> int:
        """Next song for the player (the playlist's next-index provider)."""
        self.entries = [entry for entry in self.entries if entry.index not in self.disabled]
        if self.entries:
            index = self.entries.pop(0).index
        else:
            index = self._random_index()
        self.recent.append(index)
        self.recent = self.recent[-20:]
        return index

    def slots_view(self) -> list[tuple[str, Optional[QueueEntry], bool]]:
        """For drawing: (kind, entry, tentative) per slot - filled, pending (tentative) or empty."""
        view = [("filled", entry, False) for entry in self.entries]
        free = self.size - len(view)
        view += [("pending", entry, True) for entry in self.pending[:max(free, 0)]]
        view += [("empty", None, False)] * max(self.size - len(view), 0)
        return view


class ThemeVote:
    def __init__(self, themes: list[str]):
        self.themes = list(themes)
        self.votes: dict[str, str] = {}  # user -> theme
        self.first_seen: dict[str, int] = {}
        self._counter = 0

    def vote(self, user: str, query: str) -> Optional[str]:
        index = match_choice(query, self.themes)
        if index is None:
            return None
        theme = self.themes[index]
        self.votes[user] = theme
        if theme not in self.first_seen:
            self.first_seen[theme] = self._counter
            self._counter += 1
        return theme

    def leader(self) -> Optional[tuple[str, int]]:
        if not self.votes:
            return None
        counts: dict[str, int] = {}
        for theme in self.votes.values():
            counts[theme] = counts.get(theme, 0) + 1
        theme = max(counts, key=lambda t: (counts[t], -self.first_seen.get(t, 0)))
        return theme, counts[theme]

    def close(self) -> Optional[str]:
        leader = self.leader()
        self.votes.clear()
        self.first_seen.clear()
        return leader[0] if leader else None


# ---------------------------------------------------------------------------------------------- chat sources

@dataclass
class ChatMessage:
    user: str
    text: str


def extract_video_id(value: str) -> str:
    """11-character video id from an id or any YouTube URL (watch?v=, youtu.be/, /live/, /shorts/)."""
    value = (value or "").strip()
    match = re.search(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", value)
    if match:
        return match.group(1)
    return value if re.fullmatch(r"[A-Za-z0-9_-]{11}", value) else value


class ChatSource:
    """Background reader; messages arrive in `self.messages`. Reconnects with back-off when the reader fails."""

    def __init__(self, kind: str, video_id: str, test_file: Path):
        self.kind = kind
        self.video_id = extract_video_id(video_id)
        self.test_file = test_file
        self.messages: "queue.Queue[ChatMessage]" = queue.Queue()
        self.status = "idle"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self.kind == "none":
            self.status = "off"
            return
        self._thread = threading.Thread(target=self._run, name="live-chat", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def drain(self) -> list[ChatMessage]:
        items = []
        while True:
            try:
                items.append(self.messages.get_nowait())
            except queue.Empty:
                return items

    def _kinds(self) -> list[str]:
        if self.kind != "auto":
            return [self.kind]
        return ["pytchat", "chat-downloader"] if self.video_id else ["file"]

    def _run(self):
        backoff = 2.0
        while not self._stop.is_set():
            for kind in self._kinds():
                if self._stop.is_set():
                    return
                try:
                    self.status = f"{kind}: connecting"
                    getattr(self, "_read_" + kind.replace("-", "_"))()
                    backoff = 2.0
                except Exception as exc:  # library missing, stream offline, YouTube changed...
                    self.status = f"{kind}: {type(exc).__name__}: {exc}"[:160]
                    logger.warning("live chat %s failed: %s", kind, exc)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60.0)

    def _push(self, user, text):
        if text:
            self.messages.put(ChatMessage(str(user or "?"), str(text)))

    def _read_pytchat(self):
        import pytchat
        chat = pytchat.create(video_id=self.video_id, interruptable=False)
        self.status = "pytchat: connected"
        try:
            while chat.is_alive() and not self._stop.is_set():
                for item in chat.get().sync_items():
                    self._push(item.author.name, item.message)
                self._stop.wait(0.5)
            if self._stop.is_set():
                return
            chat.raise_for_status()
        finally:
            chat.terminate()
        raise RuntimeError("chat ended")

    def _read_chat_downloader(self):
        from chat_downloader import ChatDownloader
        url = self.video_id if "://" in self.video_id else f"https://www.youtube.com/watch?v={self.video_id}"
        chat = ChatDownloader().get_chat(url)
        self.status = "chat-downloader: connected"
        for item in chat:
            if self._stop.is_set():
                return
            self._push((item.get("author") or {}).get("name"), item.get("message"))
        raise RuntimeError("chat ended")

    def _read_file(self):
        """Test source: lines appended to the file ("name: message") are read as chat."""
        path = self.test_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        self.status = f"file: {path.name}"
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, 2)
            while not self._stop.is_set():
                line = handle.readline()
                if not line:
                    self._stop.wait(0.2)
                    continue
                user, sep, text = line.rstrip("\r\n").partition(":")
                if sep:
                    self._push(user.strip(), text.strip())
                else:
                    self._push("test", user.strip())


# ---------------------------------------------------------------------------------------------- session

class LiveSession:
    def __init__(self, settings: dict, songs: list, apply_theme: Callable[[str], None],
                 chat: Optional[ChatSource] = None, clock: Callable[[], float] = time.monotonic,
                 themes: Optional[list[str]] = None):
        self.settings = settings
        self.songs = list(songs)
        self.clock = clock
        self.queue = LiveQueue(self.songs, settings["queue_size"], settings["recent_song_memory"])
        self.themes = ThemeVote(themes or list(Config.color_themes))
        self.window = CommandWindow(settings["window_every_seconds"], settings["window_open_seconds"], clock())
        self._last_cycle = 0
        self._was_open = True
        self.apply_theme = apply_theme
        self.chat = chat
        self.started_at = clock()
        self.handled = 0
        self.outbox: list[str] = []  # chat replies waiting for a sender
        self._last_status = ""
        self.path_index = {path_key(getattr(song, "fp", "")): i for i, song in enumerate(self.songs)}
        self._last_help = float("-inf")

    def start(self):
        if self.chat is not None:
            self.chat.start()

    def stop(self):
        if self.chat is not None:
            self.chat.stop()

    def help_messages(self) -> list[str]:
        names = [None if i in self.queue.disabled else name for i, name in enumerate(self.queue.names())]
        return build_help_messages(self.settings["help_messages"], names, self.themes.themes)

    def refresh_library(self, found: dict) -> tuple[list[tuple[int, object]], list[tuple[int, object]]]:
        """found: every pack on disk that is in the category, path -> Song.
        New packs are appended (indices never move, so queue / player indices stay valid); a pack that is
        gone or left the category is disabled, and enabled again when it comes back.
        Returns (added, replaced) as (index, song) for the player's list."""
        added, replaced = [], []
        found = {path_key(path): song for path, song in found.items()}
        for path, song in found.items():
            index = self.path_index.get(path)
            if index is None:
                index = len(self.songs)
                self.path_index[path] = index
                self.songs.append(song)
                self.queue.songs.append(song)
                added.append((index, song))
            elif self.songs[index] is not song:  # pack rebuilt: new title / metadata
                self.songs[index] = song
                self.queue.songs[index] = song
                replaced.append((index, song))
        self.queue.disabled = {index for path, index in self.path_index.items() if path not in found}
        return added, replaced

    def handle_message(self, user: str, text: str) -> bool:
        """Apply one chat line. Outside an input window commands are ignored (!help always answers)."""
        word, _, rest = text.strip().partition(" ")
        word = word.lower()
        if word == self.settings["help_command"].lower():
            now = self.clock()
            if now - self._last_help < float(self.settings["help_cooldown_seconds"]):
                return False
            self._last_help = now
            # no chat sender yet (needs the official API): replies wait in the outbox and go to the log
            replies = self.help_messages()
            self.outbox.extend(replies)
            for reply in replies:
                logger.info("!help reply: %s", reply)
            return True
        open_now, _ = self.window.state(self.clock())
        if not open_now:
            return False
        if word == self.settings["song_command"].lower():
            return self.queue.request(user, rest) is not None
        if word == self.settings["color_command"].lower():
            return self.themes.vote(user, rest) is not None
        return False

    def describe(self, user: str, text: str, accepted: bool) -> str:
        """One console line per command, for testing the chat link."""
        word = text.strip().partition(" ")[0].lower()
        open_now, seconds = self.window.state(self.clock())
        if accepted:
            result = "OK"
        elif word != self.settings["help_command"].lower() and not open_now:
            result = f"ignored: input closed (opens in {int(seconds) + 1}s)"
        elif word == self.settings["help_command"].lower():
            result = "ignored: !help cooldown"
        else:
            result = "ignored: no match / already queued / queue full"
        return f"[chat] {user}: {text}  ->  {result}"

    def update(self):
        if self.chat is not None:
            if self.chat.status != self._last_status:
                self._last_status = self.chat.status
                print(f"[chat] {self.chat.status}", flush=True)
            for message in self.chat.drain():
                accepted = self.handle_message(message.user, message.text)
                if accepted:
                    self.handled += 1
                if message.text.strip().startswith("!"):
                    print(self.describe(message.user, message.text, accepted), flush=True)
        now = self.clock()
        open_now, _ = self.window.state(now)
        cycle = self.window.cycle(now)
        if self._was_open and (not open_now or cycle != self._last_cycle):
            self.close_window()
        self._was_open = open_now
        self._last_cycle = cycle

    def close_window(self):
        self.queue.close_window()
        theme = self.themes.close()
        if theme:
            self.apply_theme(theme)
        names = self.queue.names()
        queue_text = ", ".join(f"{names[e.index]}" + (f" ({e.requested_by})" if e.requested_by else "")
                               for e in self.queue.entries)
        print(f"[live] input closed - queue: {queue_text}" + (f" - theme: {theme}" if theme else ""), flush=True)

    def next_index(self, base_index: int = -1) -> int:
        return self.queue.pop_next(base_index)

    def caption(self) -> str:
        captions = self.settings["captions"]
        if not captions:
            return ""
        seconds = max(float(self.settings["caption_seconds"]), 0.5)
        return captions[int((self.clock() - self.started_at) // seconds) % len(captions)]


# ---------------------------------------------------------------------------------------------- overlay

class LiveOverlay:
    """Top panel: prompt texts, test caption, input-window timer, now/next/queue. Words come from live.json."""

    def __init__(self, settings: dict):
        self.settings = settings
        self._fonts: dict[int, pygame.font.Font] = {}

    def font(self, size: int) -> pygame.font.Font:
        if size not in self._fonts:
            path = self.settings.get("font_file") or ""
            try:
                self._fonts[size] = pygame.font.Font(path, size) if path and Path(path).is_file() else \
                    pygame.font.SysFont("malgungothic,applesdgothicneo,notosanscjkkr,arial", size)
            except Exception:
                self._fonts[size] = pygame.font.SysFont("arial", size)
        return self._fonts[size]

    def panel_rect(self, screen: pygame.Surface) -> pygame.Rect:
        return pygame.Rect(0, 0, screen.get_width(), int(screen.get_height() * float(self.settings["top_panel_ratio"])))

    def _ellipsize(self, text: str, size: int, width: int) -> str:
        """Cut a long queue entry to the width with '...' (the font size stays)."""
        font = self.font(size)
        if font.size(text)[0] <= width:
            return text
        while text and font.size(text + "...")[0] > width:
            text = text[:-1]
        return text.rstrip() + "..."

    def _outlined(self, text: str, size: int, color) -> pygame.Surface:
        return self._fit(text, size, 10 ** 6, color)

    def _fit(self, text: str, size: int, width: int, color, bold_shadow=True) -> pygame.Surface:
        font = self.font(size)
        while size > 14 and font.size(text)[0] > width:
            size -= 2
            font = self.font(size)
        surface = font.render(text, True, color)
        if not bold_shadow:
            return surface
        # dark outline + drop shadow: readable without a backdrop
        edge = max(size // 20, 1)
        dark = font.render(text, True, (0, 0, 0))
        out = pygame.Surface((surface.get_width() + edge * 2 + 2, surface.get_height() + edge * 2 + 2), pygame.SRCALPHA)
        out.blit(dark, (edge + 2, edge + 2))
        for dx in (-edge, 0, edge):
            for dy in (-edge, 0, edge):
                if dx or dy:
                    out.blit(dark, (edge + dx, edge + dy))
        out.blit(surface, (edge, edge))
        return out

    def draw(self, screen: pygame.Surface, session: LiveSession, now_name: str, next_name: str, accent):
        """No backdrop: every text is outlined so it reads on any theme.
        Top-down: caption (centre), prompt texts, theme vote, then the song list on the right."""
        labels = self.settings["labels"]
        panel = self.panel_rect(screen)
        w = panel.width
        pad = int(w * 0.04)
        unit = max(panel.height // 12, 12)
        y = pad // 2

        caption = session.caption()
        if caption:
            caption_surface = self._fit(caption, int(unit * 1.1), w - pad * 2, (255, 255, 255))
            screen.blit(caption_surface, caption_surface.get_rect(midtop=(w // 2, y)))
            y += caption_surface.get_height()

        for text in self.settings["texts"]:
            surface = self._fit(text, int(unit * 1.25), w - pad * 2, (255, 255, 255))
            screen.blit(surface, surface.get_rect(midtop=(w // 2, y)))
            y += surface.get_height()

        open_now, _ = session.window.state(session.clock())
        leader = session.themes.leader()
        if open_now and leader:
            vote = labels["color_vote"].format(theme=leader[0], votes=leader[1])
            vote_surface = self._fit(vote, int(unit * 0.7), w - pad * 2, (235, 235, 235))
            screen.blit(vote_surface, vote_surface.get_rect(midtop=(w // 2, y)))
            y += vote_surface.get_height()

        # now / next / queue: small, on the right, under the caption
        try:
            label_color = pygame.Color(self.settings["label_color"])
        except (TypeError, ValueError):
            label_color = pygame.Color(200, 200, 200)
        small = max(int(unit * 0.5), 14)
        rows = [(labels["now"], now_name, (255, 255, 255)), (labels["next"], next_name, (225, 225, 225))]
        names = session.queue.names()
        for number, (kind, entry, tentative) in enumerate(session.queue.slots_view()):
            label = ""  # the queue simply continues under "Next"
            if entry is None:
                rows.append((label, labels["empty_slot"], (170, 170, 170)))
                continue
            who = labels["requested_by"].format(user=entry.requested_by) if entry.requested_by else labels["auto_slot"]
            color = (200, 200, 200) if tentative else (240, 240, 240)
            rows.append((label, f"{names[entry.index]} · {who}", color))
        max_block = int(w * 0.36)
        label_room = max(self.font(small).size(label)[0] for label, _, _ in rows if label)
        value_room = max_block - label_room - small // 2
        rendered = [
            (self._outlined(label, small, label_color) if label else None,
             self._outlined(self._ellipsize(value, small, value_room), small, color))
            for label, value, color in rows
        ]
        label_width = max((item[0].get_width() for item in rendered if item[0] is not None), default=0)
        block_width = min(max(label_width + small // 2 + item[1].get_width() for item in rendered), max_block)
        x0 = w - pad - block_width
        y += unit
        for label_surface, value_surface in rendered:
            if label_surface is not None:
                screen.blit(label_surface, (x0, y))
            screen.blit(value_surface, (x0 + label_width + small // 2, y))
            y += value_surface.get_height()


CHAT_MESSAGE_LIMIT = 200


def build_help_messages(templates: list[str], songs: list[str], themes: list[str],
                        limit: int = CHAT_MESSAGE_LIMIT) -> list[str]:
    """Fill {songs} (numbered, as !song accepts the number) and {themes}; a list that does not fit one chat
    message continues in the next message with the same prefix."""
    lists = {
        "{songs}": [f"{number}. {name}" for number, name in enumerate(songs, start=1) if name is not None],
        "{themes}": list(themes),
    }
    messages = []
    for template in templates:
        key = next((k for k in lists if k in template), None)
        if key is None:
            messages.append(template[:limit])
            continue
        prefix, _, suffix = template.partition(key)
        current = []
        for item in lists[key]:
            candidate = prefix + ", ".join(current + [item]) + suffix
            if current and len(candidate) > limit:
                messages.append(prefix + ", ".join(current) + suffix)
                current = [item]
            else:
                current.append(item)
        messages.append((prefix + ", ".join(current) + suffix)[:limit])
    return messages


class LibraryWatcher:
    """Re-reads the song folders every few seconds so packs can be added, rebuilt or removed during a stream.
    A pack is only (re)loaded when its size or modification time changed; a pack that is still being copied
    fails to load and is simply tried again on the next scan."""

    def __init__(self, directories: list[Path], category: str, loader: Callable[[str], object],
                 interval: float = 5.0, clock: Callable[[], float] = time.monotonic):
        self.directories = [Path(d) for d in directories]
        self.category = category
        self.loader = loader
        self.interval = interval
        self.clock = clock
        self._next = clock() + interval
        self._cache: dict[str, tuple[tuple[int, int], object]] = {}

    def poll(self) -> Optional[dict]:
        """None between scans; otherwise path -> Song for every loadable pack in the category."""
        if self.clock() < self._next:
            return None
        self._next = self.clock() + self.interval
        return self.scan()

    def scan(self) -> dict:
        seen = {}
        for directory in self.directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in (".zip", ".midiplayground") or not path.is_file():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                key = path_key(path)
                stamp = (stat.st_size, stat.st_mtime_ns)
                cached = self._cache.get(key)
                if cached is None or cached[0] != stamp:
                    try:
                        song = self.loader(key)
                    except Exception as exc:  # half-copied / broken pack: try again next scan
                        logger.info("live library: %s not loadable yet: %s", path.name, exc)
                        self._cache.pop(key, None)
                        continue
                    self._cache[key] = (stamp, song)
                seen[key] = self._cache[key][1]
        for key in set(self._cache) - set(seen):
            del self._cache[key]
        return {key: song for key, song in seen.items() if songs_in_category([song], self.category)}


def apply_cli_overrides(settings: dict, argv: list[str]) -> dict:
    """--category X --video ID --chat KIND override live.json for this run only."""
    settings = dict(settings)
    for flag, key in (("--category", "category"), ("--video", "video_id"), ("--chat", "chat_source")):
        if flag in argv:
            position = argv.index(flag)
            if position + 1 < len(argv):
                settings[key] = argv[position + 1]
    return settings


def make_chat_source(settings: dict) -> ChatSource:
    test_file = Path(settings["test_chat_file"])
    if not test_file.is_absolute():
        test_file = user_path(settings["test_chat_file"])
    return ChatSource(settings["chat_source"], settings["video_id"].strip(), test_file)


def view_center_offset(settings: dict, height: int) -> float:
    """Shift of the gameplay centre so the square sits in the band between the top panel and the Shorts UI."""
    top = float(settings["top_panel_ratio"]) * height
    bottom = float(settings["bottom_reserve_ratio"]) * height
    return (top - bottom) / 2
