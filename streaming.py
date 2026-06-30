from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from collections import deque
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import random
from math import floor
from threading import Lock
from time import perf_counter
from typing import Optional
from uuid import uuid4
from zipfile import ZipFile

from bounce import Bounce
from config import Config
from errors import MapLoadingFailureError
from spatial import SpatialHash
from square import Square
from utils import read_midi_file, read_osu_file, remove_too_close_values


@dataclass(frozen=True)
class SongSpec:
    name: str
    filepath: str
    song_file_name: str
    audio_file_name: str
    music_offset: int
    is_from_osu_file: bool


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


def _read_notes(spec: SongSpec) -> list[float]:
    with ZipFile(spec.filepath) as archive:
        if spec.is_from_osu_file:
            return read_osu_file(archive.read(spec.song_file_name))
        return read_midi_file(BytesIO(archive.read(spec.song_file_name)))


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


class _WorkerMapSession:
    def __init__(
            self,
            notes: list[float],
            settings: dict,
            start_pos: list[float],
            start_dir: list[int],
    ):
        for name, value in settings.items():
            setattr(Config, name, value)
        notes = notes[:Config.max_notes] if Config.max_notes is not None else notes
        self.notes = remove_too_close_values(notes, Config.bounce_min_spacing)
        if not self.notes:
            raise MapLoadingFailureError("The map does not contain any playable notes")
        self.index = 0
        self.square = Square(*start_pos, *start_dir)
        self.previous_time = 0.0
        self.rng = random.Random(Config.seed)
        self.recent_colliders = deque()
        self.collision_index = SpatialHash(Config.spatial_cell_size)
        self.collider_sequence = 0
        self.collision_conflicts = 0
        self.chunk_seconds = max(float(Config.map_chunk_seconds), 1.0)
        self.collision_retention = max(float(Config.map_retention_seconds) * 2, 10.0)
        self.initial_pos = start_pos.copy()
        self.initial_dir = start_dir.copy()

    def next_chunk(self, session_id: str, include_notes: bool) -> StreamChunkResult:
        started_at = perf_counter()
        next_timestamp = self.notes[self.index]
        chunk_start = floor(next_timestamp / self.chunk_seconds) * self.chunk_seconds
        chunk_end = chunk_start + self.chunk_seconds
        chunk_start_pos = self.square.pos.copy()
        chunk_start_dir = self.square.dir.copy()
        output = []

        while self.index < len(self.notes) and self.notes[self.index] < chunk_end:
            timestamp = self.notes[self.index]
            elapsed = max(timestamp - self.previous_time, 0.0)
            self.square.x += self.square.dir_x * Config.square_speed * elapsed
            self.square.y += self.square.dir_y * Config.square_speed * elapsed

            while self.recent_colliders and self.recent_colliders[0][0] < timestamp - self.collision_retention:
                _, expired_key = self.recent_colliders.popleft()
                self.collision_index.remove(expired_key)

            preferred_axis = 1 if self.rng.random() * 100 < Config.direction_change_chance else 0
            candidates = []
            for axis in (preferred_axis, 1 - preferred_axis):
                direction = self.square.dir.copy()
                direction[axis] *= -1
                candidate = Bounce(self.square.pos, direction, timestamp, axis)
                collision_rect = candidate.get_collision_rect()
                candidates.append((len(self.collision_index.query(collision_rect)), candidate, collision_rect))

            collisions, selected, collision_rect = min(candidates, key=lambda item: item[0])
            if collisions:
                self.collision_conflicts += 1
            self.square.dir = selected.square_dir.copy()
            output.append((
                selected.square_pos.copy(),
                selected.square_dir.copy(),
                selected.time,
                selected.bounce_dir,
            ))
            self.collision_index.insert(self.collider_sequence, collision_rect)
            self.recent_colliders.append((timestamp, self.collider_sequence))
            self.collider_sequence += 1
            self.previous_time = timestamp
            self.index += 1

        complete = self.index >= len(self.notes)
        return StreamChunkResult(
            session_id=session_id,
            chunk=MapChunk(
                start_time=chunk_start,
                end_time=chunk_end,
                bounces=output,
                start_pos=chunk_start_pos,
                start_dir=chunk_start_dir,
            ),
            all_notes=self.notes.copy() if include_notes else [],
            start_pos=self.initial_pos.copy(),
            start_dir=self.initial_dir.copy(),
            complete=complete,
            end_pos=self.square.pos.copy(),
            end_dir=self.square.dir.copy(),
            elapsed_ms=(perf_counter() - started_at) * 1000,
            collision_conflicts=self.collision_conflicts,
        )


_WORKER_MAP_SESSIONS: dict[str, _WorkerMapSession] = {}


def start_song_map_stream(
        session_id: str,
        spec: SongSpec,
        settings: dict,
        start_pos: list[float],
        start_dir: list[int],
) -> StreamChunkResult:
    session = _WorkerMapSession(_read_notes(spec), settings, start_pos, start_dir)
    _WORKER_MAP_SESSIONS[session_id] = session
    result = session.next_chunk(session_id, include_notes=True)
    if result.complete:
        _WORKER_MAP_SESSIONS.pop(session_id, None)
    return result


def continue_song_map_stream(session_id: str) -> StreamChunkResult:
    session = _WORKER_MAP_SESSIONS.get(session_id)
    if session is None:
        raise RuntimeError(f"Map stream session was lost: {session_id}")
    result = session.next_chunk(session_id, include_notes=False)
    if result.complete:
        _WORKER_MAP_SESSIONS.pop(session_id, None)
    return result


def close_song_map_stream(session_id: str):
    _WORKER_MAP_SESSIONS.pop(session_id, None)


def _generate_streaming_bounces(
        notes: list[float], start_pos: list[float], start_dir: list[int], start_time: float = 0.0
) -> tuple[list[Bounce], list[float], int]:
    notes = notes[:Config.max_notes] if Config.max_notes is not None else notes
    notes = remove_too_close_values(notes, Config.bounce_min_spacing)
    if not notes:
        raise MapLoadingFailureError("The map does not contain any playable notes")

    rng = random.Random(Config.seed)
    square = Square(*start_pos, *start_dir)
    output = []
    recent_colliders = deque()
    collision_index = SpatialHash(Config.spatial_cell_size)
    collision_conflicts = 0
    previous_time = start_time
    collision_retention = max(float(Config.map_retention_seconds) * 2, 10.0)

    for timestamp in notes:
        elapsed = max(timestamp - previous_time, 0.0)
        square.x += square.dir_x * Config.square_speed * elapsed
        square.y += square.dir_y * Config.square_speed * elapsed

        while recent_colliders and recent_colliders[0][0] < timestamp - collision_retention:
            _, expired_key = recent_colliders.popleft()
            collision_index.remove(expired_key)

        preferred_axis = 1 if rng.random() * 100 < Config.direction_change_chance else 0
        axes = (preferred_axis, 1 - preferred_axis)
        candidates = []
        for axis in axes:
            direction = square.dir.copy()
            direction[axis] *= -1
            candidate = Bounce(square.pos, direction, timestamp, axis)
            collision_rect = candidate.get_collision_rect()
            collisions = len(collision_index.query(collision_rect))
            candidates.append((collisions, candidate, collision_rect))

        collisions, selected, collision_rect = min(candidates, key=lambda item: item[0])
        if collisions:
            collision_conflicts += 1
        square.dir = selected.square_dir.copy()
        output.append(selected)
        collider_key = len(output) - 1
        collision_index.insert(collider_key, collision_rect)
        recent_colliders.append((timestamp, collider_key))
        previous_time = timestamp
    return output, notes, collision_conflicts


def prepare_song_map(
        spec: SongSpec,
        settings: dict,
        start_pos: list[float],
        start_dir: list[int],
) -> PreparedMap:
    notes = _read_notes(spec)
    return prepare_notes_map(notes, settings, start_pos, start_dir)


def prepare_notes_map(
        notes: list[float],
        settings: dict,
        start_pos: list[float],
        start_dir: list[int],
        start_time: float = 0.0,
) -> PreparedMap:
    started_at = perf_counter()
    for name, value in settings.items():
        setattr(Config, name, value)

    bounces, unhit_notes, collision_conflicts = _generate_streaming_bounces(
        notes, start_pos, start_dir, start_time
    )
    used_fallback = False
    warning = (
        f"Used least-collision fallback for {collision_conflicts} bounces"
        if collision_conflicts else ""
    )

    serialized = [
        (bounce.square_pos.copy(), bounce.square_dir.copy(), bounce.time, bounce.bounce_dir)
        for bounce in bounces
    ]
    chunk_seconds = max(float(settings.get("map_chunk_seconds", 15.0)), 1.0)
    chunks: list[MapChunk] = []
    for bounce in serialized:
        chunk_start = int(bounce[2] // chunk_seconds) * chunk_seconds
        if not chunks or chunks[-1].start_time != chunk_start:
            chunks.append(MapChunk(chunk_start, chunk_start + chunk_seconds, []))
        chunks[-1].bounces.append(bounce)

    return PreparedMap(
        bounces=serialized,
        unhit_notes=unhit_notes,
        start_pos=start_pos.copy(),
        start_dir=start_dir.copy(),
        chunks=chunks,
        generation_ms=(perf_counter() - started_at) * 1000,
        used_fallback=used_fallback,
        warning=warning,
    )


@dataclass
class TimedAudio:
    data: bytes
    extension: str
    elapsed_ms: float

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

def read_song_audio_timed(spec: SongSpec) -> TimedAudio:
    started_at = perf_counter()
    data, extension = read_song_audio(spec)
    return TimedAudio(data, extension, (perf_counter() - started_at) * 1000)


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

    def _invalidate_streams(self):
        old_slots = [slot for slot in [self.active_slot, *self.slots] if slot is not None]
        self._generation += 1
        if self._map_executor is not None:
            for slot in old_slots:
                if slot.map_started and not slot.map_complete:
                    self._map_executor.submit(close_song_map_stream, slot.session_id)

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

    def _candidate_indices(self, base_index: int, count: int) -> list[int]:
        if not self.songs:
            return []
        count = min(count, len(self.songs))
        return [(base_index + offset) % len(self.songs) for offset in range(1, count + 1)]

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
    ):
        if slot.map_started:
            return
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
        if generation != self._generation:
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
            if first:
                slot.all_notes = result.all_notes.copy()
                warning = (
                    f"Used least-collision fallback for {result.collision_conflicts} bounces"
                    if result.collision_conflicts else ""
                )
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
            self._submit_stream_start(candidate, completed_slot.end_pos, completed_slot.end_dir)

    def prepare_current(self, start_pos: list[float], start_dir: list[int]) -> PreparedMap:
        self._invalidate_streams()
        self.slots.clear()
        slot = self._new_slot(self.current_index)
        self.active_slot = slot
        self._submit_stream_start(slot, start_pos, start_dir)
        prepared: PreparedMap = slot.map_future.result(timeout=30)
        # The first track is loaded synchronously elsewhere, so discard the duplicate preload.
        return prepared

    def prepare_ahead(self, start_pos: list[float], start_dir: list[int]):
        self._invalidate_streams()
        self.active_slot = None
        self.slots.clear()
        self.last_error = ""
        count = max(int(Config.playlist_prefetch_count), 1)
        for index in self._candidate_indices(self.current_index, count):
            self.slots.append(self._new_slot(index))
        if self.slots:
            self._submit_stream_start(self.slots[0], start_pos, start_dir)

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
        released.set_result(TimedAudio(b"", timed_audio.extension, timed_audio.elapsed_ms))
        slot.audio_future = released

    def ensure_prefetch(self, current_end_pos: list[float], current_end_dir: list[int]):
        if not self.songs:
            return
        target = min(max(int(Config.playlist_prefetch_count), 1), len(self.songs))
        anchor = self.slots[-1] if self.slots else self.active_slot
        base_index = anchor.index if anchor is not None else self.current_index
        while len(self.slots) < target:
            index = (base_index + 1) % len(self.songs)
            slot = self._new_slot(index)
            self.slots.append(slot)
            base_index = index

        first = self.slots[0] if self.slots else None
        if first is None or first.map_started:
            return
        if anchor is not None and anchor is not first:
            if not anchor.complete_future.done() or self._future_error(anchor.complete_future, ""):
                return
            start_pos, start_dir = anchor.complete_future.result()
        else:
            start_pos, start_dir = current_end_pos, current_end_dir
        self._submit_stream_start(first, start_pos, start_dir)

    def refresh_next_map(self, start_pos: list[float], start_dir: list[int]):
        self.prepare_ahead(start_pos, start_dir)

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
        if restart_map_worker and self._map_executor is not None:
            self._generation += 1
            self._map_executor.shutdown(wait=False, cancel_futures=True)
            self._map_executor = None
        self.prepare_ahead(start_pos, start_dir)
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
