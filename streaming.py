from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from collections import deque
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from math import floor
from threading import Lock
from time import perf_counter
from typing import Optional, Sequence
from uuid import uuid4
from zipfile import ZipFile

import pygame

from bounce import Bounce
from config import Config
from errors import MapLoadingFailureError
from routeplanner import plan_route
from utils import MapNote, read_midi_notes, read_osu_file, thin_notes


@dataclass(frozen=True)
class SongSpec:
    name: str
    filepath: str
    song_file_name: str
    audio_file_name: str
    music_offset: int
    is_from_osu_file: bool
    min_spacing_ms: Optional[float] = None  # per-song floor for bounce_min_spacing (metadata "bounce_min_spacing")


@dataclass
class MapChunk:
    start_time: float
    end_time: float
    bounces: list[tuple[list[float], list[int], float, int]]
    start_pos: list[float] = field(default_factory=list)
    start_dir: list[int] = field(default_factory=list)


@dataclass
class PreparedMap:
    bounces: list[tuple[list[float], list[int], float, int]]
    unhit_notes: list[float]
    start_pos: list[float]
    start_dir: list[int]
    chunks: list[MapChunk] = field(default_factory=list)
    generation_ms: float = 0.0
    used_fallback: bool = False
    warning: str = ""
    complete: bool = True
    stream_slot: object = field(default=None, repr=False, compare=False)
    start_time: float = 0.0  # the route leaves start_pos at this song time (< 0: it continues the previous song)

    @property
    def end_state(self) -> tuple[list[float], list[int]]:
        if not self.bounces:
            return self.start_pos.copy(), self.start_dir.copy()
        pos, direction, _, _ = self.bounces[-1]
        return pos.copy(), direction.copy()


def song_to_spec(song) -> SongSpec:
    return SongSpec(
        name=song.name,
        filepath=song.fp,
        song_file_name=song.song_file_name,
        audio_file_name=song.audio_file_name,
        music_offset=song.music_offset,
        is_from_osu_file=song.is_from_osu_file,
        min_spacing_ms=getattr(song, "min_spacing_ms", None),
    )


def map_settings_snapshot() -> dict:
    names = (
        "seed",
        "max_notes",
        "bounce_min_spacing",
        "square_speed",
        "direction_change_chance",
        "backtrack_chance",
        "backtrack_amount",
        "map_retention_seconds",
        "map_chunk_seconds",
        "spatial_cell_size",
    )
    return {name: getattr(Config, name) for name in names}


def read_song_audio(spec: SongSpec) -> tuple[bytes, str]:
    with ZipFile(spec.filepath) as archive:
        audio = archive.read(spec.audio_file_name)
    return audio, Path(spec.audio_file_name).suffix.lower()


MAP_TAIL_TOLERANCE = 2.0  # seconds of audio notes after the map's last note before the map is extended


def _read_notes(spec: SongSpec) -> list[MapNote]:
    with ZipFile(spec.filepath) as archive:
        if spec.is_from_osu_file:
            return [(timestamp, None, 0.0) for timestamp in read_osu_file(archive.read(spec.song_file_name))]
        notes = read_midi_notes(BytesIO(archive.read(spec.song_file_name)))
        audio_name = spec.audio_file_name
        if audio_name != spec.song_file_name and audio_name.lower().endswith((".mid", ".midi")):
            audio_notes = read_midi_notes(BytesIO(archive.read(audio_name)), skip_channels=(9,))
            notes = _extend_to_audio_end(notes, audio_notes)
        return notes


def _extend_to_audio_end(notes: list[MapNote], audio_notes: list[MapNote]) -> list[MapNote]:
    """A map MIDI that stops before its audio MIDI (e.g. a truncated melody file) continues with the audio's top line,
    so the map lasts until the song does."""
    if not audio_notes:
        return notes
    last = notes[-1][0] if notes else float("-inf")
    if audio_notes[-1][0] - last <= MAP_TAIL_TOLERANCE:
        return notes
    return notes + [note for note in audio_notes if note[0] > last + 0.25]


def _as_map_notes(notes) -> list[MapNote]:
    """Accept plain timestamps (live regeneration, osu) as well as MapNotes."""
    return [note if isinstance(note, tuple) else (note, None, 0.0) for note in notes]


def _filter_map_notes(notes: list[MapNote], min_spacing_ms: Optional[float] = None) -> list[MapNote]:
    notes = notes[:Config.max_notes] if Config.max_notes is not None else notes
    spacing = max(float(Config.bounce_min_spacing), float(min_spacing_ms or 0))
    return thin_notes(notes, spacing)


def _serialize(bounce: Bounce) -> tuple[list[float], list[int], float, int]:
    return bounce.square_pos.copy(), bounce.square_dir.copy(), bounce.time, bounce.bounce_dir


def _conflict_warning(conflicts: int) -> str:
    return f"Route overlap unavoidable at {conflicts} bounces" if conflicts else ""


@dataclass
class StreamChunkResult:
    session_id: str
    chunk: MapChunk
    all_notes: list[float]
    start_pos: list[float]
    start_dir: list[int]
    complete: bool
    end_pos: list[float]
    end_dir: list[int]
    elapsed_ms: float
    collision_conflicts: int = 0
    start_time: float = 0.0


class _WorkerMapSession:
    """Bakes the whole song's route up front (so the planner can backtrack across it), then hands it out by chunk."""

    def __init__(
            self,
            notes: list[MapNote],
            settings: dict,
            start_pos: list[float],
            start_dir: list[int],
            min_spacing_ms: Optional[float] = None,
            obstacles: Sequence[tuple[tuple[float, float, float, float], float]] = (),
            obstacle_segments: Sequence[tuple[float, float, float, float]] = (),
            start_time: float = 0.0,
    ):
        started_at = perf_counter()
        for name, value in settings.items():
            setattr(Config, name, value)
        self.notes = _filter_map_notes(_as_map_notes(notes), min_spacing_ms)
        if not self.notes:
            raise MapLoadingFailureError("The map does not contain any playable notes")
        self.start_time = float(start_time)
        bounces, stats = plan_route(
            [note[0] for note in self.notes], start_pos, start_dir, start_time=self.start_time,
            obstacles=[(pygame.Rect(rect), timestamp) for rect, timestamp in obstacles],
            obstacle_segments=obstacle_segments,
        )
        self.bounces = [_serialize(bounce) for bounce in bounces]
        self.conflicts = stats.conflicts
        self.bake_ms = (perf_counter() - started_at) * 1000
        self.index = 0
        self.chunk_seconds = max(float(Config.map_chunk_seconds), 1.0)
        self.initial_pos = start_pos.copy()
        self.initial_dir = start_dir.copy()

    def next_chunk(self, session_id: str, include_notes: bool) -> StreamChunkResult:
        started_at = perf_counter()
        chunk_start = floor(self.bounces[self.index][2] / self.chunk_seconds) * self.chunk_seconds
        chunk_end = chunk_start + self.chunk_seconds
        if self.index:
            chunk_start_pos, chunk_start_dir = self.bounces[self.index - 1][0].copy(), self.bounces[self.index - 1][1].copy()
        else:
            chunk_start_pos, chunk_start_dir = self.initial_pos.copy(), self.initial_dir.copy()
        output = []
        while self.index < len(self.bounces) and self.bounces[self.index][2] < chunk_end:
            output.append(self.bounces[self.index])
            self.index += 1

        last_pos, last_dir, _, _ = output[-1]
        elapsed_ms = (perf_counter() - started_at) * 1000
        if include_notes:
            elapsed_ms += self.bake_ms
        return StreamChunkResult(
            session_id=session_id,
            chunk=MapChunk(
                start_time=chunk_start,
                end_time=chunk_end,
                bounces=output,
                start_pos=chunk_start_pos,
                start_dir=chunk_start_dir,
            ),
            all_notes=[note[0] for note in self.notes] if include_notes else [],
            start_pos=self.initial_pos.copy(),
            start_dir=self.initial_dir.copy(),
            complete=self.index >= len(self.bounces),
            end_pos=last_pos.copy(),
            end_dir=last_dir.copy(),
            elapsed_ms=elapsed_ms,
            collision_conflicts=self.conflicts,
            start_time=self.start_time,
        )


    def tail_obstacles(self) -> list[tuple[tuple[int, int, int, int], float]]:
        """The last pegs of this song, re-timed so the final one is at 0 s of the next song.

        The final peg itself is left out: the next song starts touching it, just as a route leaves its own last peg."""
        if not self.bounces:
            return []
        end = self.bounces[-1][2]
        tail = []
        for position, direction, timestamp, axis in reversed(self.bounces[:-1]):
            rect = Bounce(position, direction, timestamp, axis).get_collision_rect()
            tail.append((tuple(rect), timestamp - end))
        return tail

    def route_segments(self) -> list[tuple[float, float, float, float]]:
        """Every travelled segment of this song, so the next song's pegs are never placed on it."""
        points = [self.initial_pos] + [bounce[0] for bounce in self.bounces]
        return [(*a, *b) for a, b in zip(points, points[1:])]


_WORKER_MAP_SESSIONS: dict[str, _WorkerMapSession] = {}
_WORKER_SESSION_TAILS: dict[str, list] = {}  # finished sessions -> their last pegs (for the next song's planner)


def _finish_session(session_id: str):
    session = _WORKER_MAP_SESSIONS.pop(session_id, None)
    if session is not None:
        _WORKER_SESSION_TAILS[session_id] = (session.tail_obstacles(), session.route_segments())
        while len(_WORKER_SESSION_TAILS) > 4:
            _WORKER_SESSION_TAILS.pop(next(iter(_WORKER_SESSION_TAILS)))


def start_song_map_stream(
        session_id: str,
        spec: SongSpec,
        settings: dict,
        start_pos: list[float],
        start_dir: list[int],
        previous_session_id: str = "",
        start_time: float = 0.0,
) -> StreamChunkResult:
    obstacles, segments = _WORKER_SESSION_TAILS.get(previous_session_id, ([], [])) if previous_session_id else ([], [])
    session = _WorkerMapSession(
        _read_notes(spec), settings, start_pos, start_dir, spec.min_spacing_ms, obstacles, segments, start_time,
    )
    _WORKER_MAP_SESSIONS[session_id] = session
    result = session.next_chunk(session_id, include_notes=True)
    if result.complete:
        _finish_session(session_id)
    return result


def continue_song_map_stream(session_id: str) -> StreamChunkResult:
    session = _WORKER_MAP_SESSIONS.get(session_id)
    if session is None:
        raise RuntimeError(f"Map stream session was lost: {session_id}")
    result = session.next_chunk(session_id, include_notes=False)
    if result.complete:
        _finish_session(session_id)
    return result


def close_song_map_stream(session_id: str):
    _WORKER_MAP_SESSIONS.pop(session_id, None)


def prepare_song_map(
        spec: SongSpec,
        settings: dict,
        start_pos: list[float],
        start_dir: list[int],
) -> PreparedMap:
    notes = _read_notes(spec)
    return prepare_notes_map(notes, settings, start_pos, start_dir, min_spacing_ms=spec.min_spacing_ms)


def prepare_notes_map(
        notes,
        settings: dict,
        start_pos: list[float],
        start_dir: list[int],
        start_time: float = 0.0,
        min_spacing_ms: Optional[float] = None,
        obstacles: Sequence[tuple[pygame.Rect, float]] = (),
) -> PreparedMap:
    """notes: MapNotes or plain timestamps. obstacles: (peg rect, time) already on screen (live regeneration)."""
    started_at = perf_counter()
    for name, value in settings.items():
        setattr(Config, name, value)

    map_notes = _filter_map_notes(_as_map_notes(notes), min_spacing_ms)
    if not map_notes:
        raise MapLoadingFailureError("The map does not contain any playable notes")
    bounces, stats = plan_route(
        [note[0] for note in map_notes], start_pos, start_dir, start_time, obstacles=obstacles
    )

    serialized = [_serialize(bounce) for bounce in bounces]
    chunk_seconds = max(float(settings.get("map_chunk_seconds", 15.0)), 1.0)
    chunks: list[MapChunk] = []
    for bounce in serialized:
        chunk_start = int(bounce[2] // chunk_seconds) * chunk_seconds
        if not chunks or chunks[-1].start_time != chunk_start:
            chunks.append(MapChunk(chunk_start, chunk_start + chunk_seconds, []))
        chunks[-1].bounces.append(bounce)

    return PreparedMap(
        bounces=serialized,
        unhit_notes=[note[0] for note in map_notes],
        start_pos=start_pos.copy(),
        start_dir=start_dir.copy(),
        chunks=chunks,
        generation_ms=(perf_counter() - started_at) * 1000,
        used_fallback=False,
        warning=_conflict_warning(stats.conflicts),
    )


@dataclass
class TimedAudio:
    data: bytes
    extension: str
    elapsed_ms: float
    duration: float = 0.0  # playback length in seconds, 0 when unknown

    def __getitem__(self, index: int):
        return (self.data, self.extension)[index]

    def __iter__(self):
        return iter((self.data, self.extension))


@dataclass
class PrefetchSlot:
    index: int
    song: object
    spec: SongSpec
    map_future: Future
    audio_future: Future
    submitted_at: float
    audio_claimed: bool = False
    map_ms: float = 0.0
    audio_ms: float = 0.0
    complete_future: Future = field(default_factory=Future)
    chunk_queue: deque[MapChunk] = field(default_factory=deque)
    session_id: str = ""
    start_pos: list[float] = field(default_factory=list)
    start_dir: list[int] = field(default_factory=list)
    end_pos: list[float] = field(default_factory=list)
    end_dir: list[int] = field(default_factory=list)
    all_notes: list[float] = field(default_factory=list)
    map_started: bool = False
    map_inflight: bool = False
    map_complete: bool = False
    error: str = ""
    cancelled: bool = False  # dropped from the queue; late worker results are ignored
    last_bounce_time: Optional[float] = None

def _ogg_duration(data: bytes) -> float:
    """Length from the last page's granule position (no decoding)."""
    head = data.find(b"\x01vorbis")
    if head >= 0:
        rate, pre_skip = int.from_bytes(data[head + 12:head + 16], "little"), 0
    else:
        head = data.find(b"OpusHead")
        if head < 0:
            return 0.0
        rate, pre_skip = 48_000, int.from_bytes(data[head + 10:head + 12], "little")
    last = data.rfind(b"OggS")
    if last < 0 or rate <= 0:
        return 0.0
    granule = int.from_bytes(data[last + 6:last + 14], "little", signed=True)
    return max(granule - pre_skip, 0) / rate


def audio_duration(data: bytes, extension: str) -> float:
    """Playback length of a song's audio in seconds; 0.0 when it cannot be told."""
    try:
        extension = extension.lower()
        if extension in (".mid", ".midi"):
            import mido
            return float(mido.MidiFile(file=BytesIO(data)).length)
        if extension == ".ogg":
            return _ogg_duration(data)
        if pygame.mixer.get_init():
            return float(pygame.mixer.Sound(file=BytesIO(data)).get_length())
    except Exception:
        pass
    return 0.0


def read_song_audio_timed(spec: SongSpec) -> TimedAudio:
    started_at = perf_counter()
    data, extension = read_song_audio(spec)
    duration = audio_duration(data, extension)
    return TimedAudio(data, extension, (perf_counter() - started_at) * 1000, duration)


class PlaylistController:
    """Streams map chunks from one worker and preloads the next two audio tracks."""

    def __init__(self):
        self.songs = []
        self.current_index = -1
        self.active_slot: Optional[PrefetchSlot] = None
        self.slots: deque[PrefetchSlot] = deque()
        self.last_error = ""
        self.last_skipped_song = ""
        self.last_map_ms = 0.0
        self.last_audio_ms = 0.0
        self._generation = 0
        self._map_executor: Optional[ProcessPoolExecutor] = None
        self._audio_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio-preload")
        self._lock = Lock()
        # optional: picks the song after base_index (live mode queue); default is the next song in the list
        self.next_index_provider = None
        self._drop_on_rebuild = 0

    @property
    def next_index(self) -> int:
        return self.slots[0].index if self.slots else -1

    @property
    def next_spec(self) -> Optional[SongSpec]:
        return self.slots[0].spec if self.slots else None

    @property
    def map_future(self) -> Optional[Future]:
        return self.slots[0].map_future if self.slots else None

    @property
    def audio_future(self) -> Optional[Future]:
        return self.slots[0].audio_future if self.slots else None

    def configure(self, songs: list, current_index: int):
        self.songs = list(songs)
        self.current_index = current_index

    def _ensure_map_executor(self):
        if self._map_executor is None:
            self._map_executor = ProcessPoolExecutor(max_workers=1)

    def _invalidate_streams(self, keep_active: bool = False):
        """Drop queued streams. keep_active leaves the playing song's stream running."""
        if keep_active:
            old_slots = list(self.slots)
        else:
            old_slots = [slot for slot in [self.active_slot, *self.slots] if slot is not None]
            self._generation += 1
        for slot in old_slots:
            slot.cancelled = True
            if self._map_executor is not None and slot.map_started and not slot.map_complete:
                try:
                    self._map_executor.submit(close_song_map_stream, slot.session_id)
                except Exception:  # a crashed worker pool must not stop shutdown (settings are saved after it)
                    pass

    def _new_slot(self, index: int) -> PrefetchSlot:
        spec = song_to_spec(self.songs[index])
        return PrefetchSlot(
            index=index,
            song=self.songs[index],
            spec=spec,
            map_future=Future(),
            audio_future=self._audio_executor.submit(read_song_audio_timed, spec),
            submitted_at=perf_counter(),
            session_id=uuid4().hex,
        )

    def _candidate_indices(self, base_index: int, count: int, reuse: tuple = ()) -> list[int]:
        if not self.songs:
            return []
        if self.next_index_provider is not None:
            # songs already taken from the provider keep their place when the queue is rebuilt
            picked = [index for index in reuse if 0 <= index < len(self.songs)][:count]
            while len(picked) < count:
                picked.append(self._pick_after(picked[-1] if picked else base_index))
            return picked
        count = min(count, len(self.songs))
        return [(base_index + offset) % len(self.songs) for offset in range(1, count + 1)]

    def _pick_after(self, base_index: int) -> int:
        if self.next_index_provider is not None:
            index = int(self.next_index_provider(base_index))
            if 0 <= index < len(self.songs):
                return index
        return (base_index + 1) % len(self.songs)

    def _set_stream_error(self, slot: PrefetchSlot, exc: Exception):
        slot.error = str(exc)
        slot.map_inflight = False
        if not slot.map_future.done():
            slot.map_future.set_exception(exc)
        if not slot.complete_future.done():
            slot.complete_future.set_exception(exc)

    def _submit_stream_start(
            self,
            slot: PrefetchSlot,
            start_pos: list[float],
            start_dir: list[int],
            previous: Optional[PrefetchSlot] = None,
            start_time: Optional[float] = None,
    ):
        if slot.map_started or slot.cancelled:
            return
        if start_time is None:
            start_time = self._continuation_start_time(previous)
        self._ensure_map_executor()
        slot.map_started = True
        slot.map_inflight = True
        slot.start_pos = start_pos.copy()
        slot.start_dir = start_dir.copy()
        generation = self._generation
        future = self._map_executor.submit(
            start_song_map_stream,
            slot.session_id,
            slot.spec,
            map_settings_snapshot(),
            start_pos.copy(),
            start_dir.copy(),
            previous.session_id if previous is not None else "",
            start_time,
        )
        future.add_done_callback(
            lambda completed: self._finish_stream_chunk(slot, completed, generation, first=True)
        )

    def _submit_stream_continue(self, slot: PrefetchSlot):
        if slot.map_complete or slot.map_inflight or slot.error:
            return
        if len(slot.chunk_queue) >= max(int(Config.map_stream_buffer_chunks), 1):
            return
        self._ensure_map_executor()
        slot.map_inflight = True
        generation = self._generation
        future = self._map_executor.submit(continue_song_map_stream, slot.session_id)
        future.add_done_callback(
            lambda completed: self._finish_stream_chunk(slot, completed, generation, first=False)
        )

    def _finish_stream_chunk(
            self,
            slot: PrefetchSlot,
            future: Future,
            generation: int,
            first: bool,
    ):
        if generation != self._generation or slot.cancelled:
            return
        try:
            result: StreamChunkResult = future.result()
        except Exception as exc:
            self._set_stream_error(slot, exc)
            return

        with self._lock:
            slot.map_inflight = False
            slot.map_ms += result.elapsed_ms
            slot.end_pos = result.end_pos.copy()
            slot.end_dir = result.end_dir.copy()
            if result.chunk.bounces:
                slot.last_bounce_time = result.chunk.bounces[-1][2]
            if first:
                slot.all_notes = result.all_notes.copy()
                warning = _conflict_warning(result.collision_conflicts)
                prepared = PreparedMap(
                    bounces=list(result.chunk.bounces),
                    unhit_notes=slot.all_notes.copy(),
                    start_pos=result.start_pos.copy(),
                    start_dir=result.start_dir.copy(),
                    chunks=[result.chunk],
                    generation_ms=result.elapsed_ms,
                    warning=warning,
                    complete=result.complete,
                    stream_slot=slot,
                    start_time=result.start_time,
                )
                slot.map_future.set_result(prepared)
            else:
                slot.chunk_queue.append(result.chunk)

            if result.complete:
                slot.map_complete = True
                if not slot.complete_future.done():
                    slot.complete_future.set_result((slot.end_pos.copy(), slot.end_dir.copy()))

        if result.complete:
            self._start_next_waiting_slot(slot)
        else:
            self._submit_stream_continue(slot)

    @staticmethod
    def _continuation_start_time(previous: Optional[PrefetchSlot]) -> float:
        """The next song's route starts this long before its own 0 s: the time between the previous song's last
        bounce and the end of its audio, which the square spends gliding on instead of waiting."""
        if previous is None or not previous.end_pos or not previous.audio_future.done():
            return 0.0
        try:
            duration = float(getattr(previous.audio_future.result(), "duration", 0.0) or 0.0)
        except Exception:
            return 0.0
        last_time = previous.last_bounce_time
        if duration <= 0.0 or last_time is None:
            return 0.0
        return -max(duration - last_time, 0.0)

    def _start_next_waiting_slot(self, completed_slot: PrefetchSlot):
        slots = list(self.slots)
        if completed_slot is self.active_slot:
            candidate = slots[0] if slots else None
        elif completed_slot in slots:
            index = slots.index(completed_slot) + 1
            candidate = slots[index] if index < len(slots) else None
        else:
            candidate = None
        if candidate is not None and not candidate.map_started:
            self._submit_stream_start(candidate, completed_slot.end_pos, completed_slot.end_dir, completed_slot)

    def prepare_current(self, start_pos: list[float], start_dir: list[int]) -> PreparedMap:
        self._invalidate_streams()
        self.slots.clear()
        slot = self._new_slot(self.current_index)
        self.active_slot = slot
        self._submit_stream_start(slot, start_pos, start_dir)
        prepared: PreparedMap = slot.map_future.result(timeout=30)
        # The first track is loaded synchronously elsewhere, so discard the duplicate preload.
        return prepared

    def prepare_ahead(
            self,
            start_pos: list[float],
            start_dir: list[int],
            keep_active: bool = False,
            start_time: float = 0.0,
    ):
        """Rebuild the queue after current_index. keep_active keeps the playing song streaming; the next map then
        starts where the playing song's finished map ends (right away if it is already complete)."""
        keep_active = keep_active and self.active_slot is not None and not self.active_slot.error
        reuse = tuple(slot.index for slot in list(self.slots)[self._drop_on_rebuild:])
        self._drop_on_rebuild = 0
        self._invalidate_streams(keep_active=keep_active)
        if not keep_active:
            self.active_slot = None
        self.slots.clear()
        self.last_error = ""
        count = max(int(Config.playlist_prefetch_count), 1)
        for index in self._candidate_indices(self.current_index, count, reuse):
            self.slots.append(self._new_slot(index))
        if not self.slots:
            return
        if keep_active:
            active = self.active_slot
            if active.map_complete and active.complete_future.done():
                end_pos, end_dir = active.complete_future.result()
                self._submit_stream_start(self.slots[0], end_pos, end_dir, active)
            # otherwise _start_next_waiting_slot starts it when the playing song's stream completes
            return
        self._submit_stream_start(self.slots[0], start_pos, start_dir, start_time=start_time)

    def prepare_next(self, start_pos: list[float], start_dir: list[int]):
        self.prepare_ahead(start_pos, start_dir)

    @staticmethod
    def _future_error(future: Future, prefix: str) -> str:
        if not future.done():
            return ""
        try:
            future.result()
        except Exception as exc:
            return f"{prefix}: {exc}"
        return ""

    def first_error(self) -> str:
        if not self.slots:
            return "No prefetched track"
        slot = self.slots[0]
        error = slot.error
        if error:
            error = f"Map generation failed: {error}"
        if not error:
            error = self._future_error(slot.map_future, "Map generation failed")
        if not error:
            error = self._future_error(slot.audio_future, "Audio preload failed")
        if error:
            self.last_error = error
        return error

    def first_ready(self) -> bool:
        if not self.slots or self.first_error():
            return False
        slot = self.slots[0]
        return slot.map_future.done() and slot.audio_future.done()

    def ready_count(self) -> int:
        count = 0
        for slot in self.slots:
            if self._future_error(slot.map_future, "") or self._future_error(slot.audio_future, ""):
                break
            if not slot.map_future.done() or not slot.audio_future.done():
                break
            count += 1
        return count

    def request_more(self, slot: Optional[PrefetchSlot]):
        if slot is not None:
            self._submit_stream_continue(slot)

    def claim_chunks_until(self, slot: Optional[PrefetchSlot], horizon: float) -> list[MapChunk]:
        if slot is None:
            return []
        claimed = []
        with self._lock:
            while slot.chunk_queue and slot.chunk_queue[0].start_time <= horizon:
                claimed.append(slot.chunk_queue.popleft())
        self.request_more(slot)
        return claimed

    @staticmethod
    def stream_drained(slot: Optional[PrefetchSlot]) -> bool:
        return bool(slot and slot.map_complete and not slot.chunk_queue and not slot.map_inflight)

    def claim_audio(self) -> Optional[tuple[bytes, str]]:
        if not self.first_ready():
            return None
        slot = self.slots[0]
        timed_audio: TimedAudio = slot.audio_future.result()
        slot.audio_ms = timed_audio.elapsed_ms
        self.last_audio_ms = timed_audio.elapsed_ms
        slot.audio_claimed = True
        return timed_audio.data, timed_audio.extension

    def consume_prepared_map(self) -> Optional[tuple[int, object, PreparedMap]]:
        if not self.first_ready():
            return None
        slot = self.slots.popleft()
        prepared: PreparedMap = slot.map_future.result()
        prepared.stream_slot = slot
        timed_audio: TimedAudio = slot.audio_future.result()
        self.last_map_ms = slot.map_ms or prepared.generation_ms
        self.last_audio_ms = timed_audio.elapsed_ms
        self.current_index = slot.index
        self.active_slot = slot
        self.last_error = ""
        return slot.index, slot.song, prepared

    def release_active_audio(self):
        slot = self.active_slot
        if slot is None or not slot.audio_future.done():
            return
        if self._future_error(slot.audio_future, ""):
            return
        timed_audio: TimedAudio = slot.audio_future.result()
        released = Future()
        released.set_result(TimedAudio(b"", timed_audio.extension, timed_audio.elapsed_ms, timed_audio.duration))
        slot.audio_future = released

    def ensure_prefetch(self, current_end_pos: list[float], current_end_dir: list[int]):
        if not self.songs:
            return
        target = min(max(int(Config.playlist_prefetch_count), 1), len(self.songs))
        anchor = self.slots[-1] if self.slots else self.active_slot
        base_index = anchor.index if anchor is not None else self.current_index
        while len(self.slots) < target:
            index = self._pick_after(base_index)
            slot = self._new_slot(index)
            self.slots.append(slot)
            base_index = index

        first = self.slots[0] if self.slots else None
        if first is None or first.map_started:
            return
        # the next map starts where the playing song's map ends, so wait for that map to finish streaming
        previous = self.active_slot
        if previous is not None and previous.map_started:
            if not previous.complete_future.done() or self._future_error(previous.complete_future, ""):
                return
            start_pos, start_dir = previous.complete_future.result()
        else:
            previous = None
            start_pos, start_dir = current_end_pos, current_end_dir
        self._submit_stream_start(first, start_pos, start_dir, previous)

    def refresh_next_map(self, start_pos: list[float], start_dir: list[int], start_time: float = 0.0):
        self.prepare_ahead(start_pos, start_dir, start_time=start_time)

    def active_duration(self) -> float:
        """Audio length of the playing song (0.0 when unknown)."""
        slot = self.active_slot
        if slot is None or not slot.audio_future.done():
            return 0.0
        try:
            return float(getattr(slot.audio_future.result(), "duration", 0.0) or 0.0)
        except Exception:
            return 0.0

    def peek_next_map(self) -> Optional[tuple[PreparedMap, list[MapChunk]]]:
        """The next song's map so far (first chunk + queued chunks), without consuming anything."""
        if not self.slots or not self.slots[0].map_future.done() or self.first_error():
            return None
        slot = self.slots[0]
        with self._lock:
            queued = list(slot.chunk_queue)
        return slot.map_future.result(), queued

    def skip_failed(
            self,
            start_pos: list[float],
            start_dir: list[int],
            restart_map_worker: bool = False,
    ) -> Optional[object]:
        if not self.slots:
            return None
        failed = self.slots[0]
        self.last_skipped_song = getattr(failed.song, "name", str(failed.song))
        self.current_index = failed.index
        self._drop_on_rebuild = 1  # the failed song is not reused
        if restart_map_worker and self._map_executor is not None:
            self._generation += 1
            self._map_executor.shutdown(wait=False, cancel_futures=True)
            self._map_executor = None
        # a failed *next* track must not cut the map of the song that is still playing
        self.prepare_ahead(start_pos, start_dir, keep_active=not restart_map_worker)
        return failed.song

    def metrics(self) -> dict:
        first = self.slots[0] if self.slots else None
        active = self.active_slot
        pending_ms = (perf_counter() - first.submitted_at) * 1000 if first else 0.0
        map_ms = first.map_ms if first else self.last_map_ms
        audio_ms = first.audio_ms if first else self.last_audio_ms
        if first and first.audio_future.done() and not self._future_error(first.audio_future, ""):
            audio_ms = first.audio_future.result().elapsed_ms
        return {
            "queued": len(self.slots),
            "ready": self.ready_count(),
            "pending_ms": pending_ms,
            "map_ms": map_ms,
            "audio_ms": audio_ms,
            "buffered_chunks": len(active.chunk_queue) if active else 0,
            "next_song": getattr(first.song, "name", "") if first else "",
            "error": self.last_error,
        }

    def shutdown(self):
        self._invalidate_streams()
        self.slots.clear()
        self.active_slot = None
        self._audio_executor.shutdown(wait=True, cancel_futures=True)
        if self._map_executor is not None:
            self._map_executor.shutdown(wait=True, cancel_futures=True)
            self._map_executor = None
