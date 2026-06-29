from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from collections import deque
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import random
from time import perf_counter
from typing import Optional
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
class MapBatchResult:
    index: int
    prepared: Optional[PreparedMap]
    elapsed_ms: float
    error: str = ""


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


def prepare_song_batch(
        jobs: list[tuple[int, SongSpec]],
        settings: dict,
        start_pos: list[float],
        start_dir: list[int],
) -> list[MapBatchResult]:
    """Generate sequential maps so every track starts where the prior track ended."""
    output = []
    position = start_pos.copy()
    direction = start_dir.copy()
    for index, spec in jobs:
        started_at = perf_counter()
        try:
            prepared = prepare_song_map(spec, settings, position, direction)
        except Exception as exc:
            output.append(MapBatchResult(index, None, (perf_counter() - started_at) * 1000, str(exc)))
            continue
        output.append(MapBatchResult(index, prepared, (perf_counter() - started_at) * 1000))
        position, direction = prepared.end_state
    return output


def read_song_audio_timed(spec: SongSpec) -> TimedAudio:
    started_at = perf_counter()
    data, extension = read_song_audio(spec)
    return TimedAudio(data, extension, (perf_counter() - started_at) * 1000)


class PlaylistController:
    """Keeps a dependency-correct two-track map/audio preparation pipeline."""

    def __init__(self):
        self.songs = []
        self.current_index = -1
        self.slots: deque[PrefetchSlot] = deque()
        self.last_error = ""
        self.last_skipped_song = ""
        self.last_map_ms = 0.0
        self.last_audio_ms = 0.0
        self._generation = 0
        self._map_executor: Optional[ProcessPoolExecutor] = None
        self._map_batch_future: Optional[Future] = None
        self._audio_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio-preload")

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

    def _candidate_indices(self, base_index: int, count: int) -> list[int]:
        if not self.songs:
            return []
        count = min(count, len(self.songs))
        return [(base_index + offset) % len(self.songs) for offset in range(1, count + 1)]

    def _submit_batch(
            self,
            indices: list[int],
            start_pos: list[float],
            start_dir: list[int],
            replace: bool,
    ):
        if replace:
            self._generation += 1
            self.slots.clear()
        if not indices:
            return

        generation = self._generation
        new_slots = []
        for index in indices:
            spec = song_to_spec(self.songs[index])
            slot = PrefetchSlot(
                index=index,
                song=self.songs[index],
                spec=spec,
                map_future=Future(),
                audio_future=self._audio_executor.submit(read_song_audio_timed, spec),
                submitted_at=perf_counter(),
            )
            self.slots.append(slot)
            new_slots.append(slot)

        self._ensure_map_executor()
        jobs = [(slot.index, slot.spec) for slot in new_slots]
        self._map_batch_future = self._map_executor.submit(
            prepare_song_batch,
            jobs,
            map_settings_snapshot(),
            start_pos.copy(),
            start_dir.copy(),
        )

        def finish_batch(batch_future: Future):
            if generation != self._generation:
                return
            try:
                results = batch_future.result()
            except Exception as exc:
                for slot in new_slots:
                    if not slot.map_future.done():
                        slot.map_future.set_exception(exc)
                return
            for slot, result in zip(new_slots, results):
                if generation != self._generation or slot.map_future.done():
                    continue
                slot.map_ms = result.elapsed_ms
                if result.error:
                    slot.map_future.set_exception(RuntimeError(result.error))
                else:
                    slot.map_future.set_result(result.prepared)

        self._map_batch_future.add_done_callback(finish_batch)

    def prepare_ahead(self, start_pos: list[float], start_dir: list[int]):
        self.last_error = ""
        count = max(int(Config.playlist_prefetch_count), 1)
        self._submit_batch(
            self._candidate_indices(self.current_index, count),
            start_pos,
            start_dir,
            replace=True,
        )

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

    def claim_audio(self) -> Optional[tuple[bytes, str]]:
        if not self.first_ready():
            return None
        slot = self.slots[0]
        timed_audio: TimedAudio = slot.audio_future.result()
        slot.audio_ms = timed_audio.elapsed_ms
        self.last_audio_ms = timed_audio.elapsed_ms
        if slot.audio_claimed:
            return timed_audio.data, timed_audio.extension
        slot.audio_claimed = True
        return timed_audio.data, timed_audio.extension

    def consume_prepared_map(self) -> Optional[tuple[int, object, PreparedMap]]:
        if not self.first_ready():
            return None
        slot = self.slots.popleft()
        prepared: PreparedMap = slot.map_future.result()
        timed_audio: TimedAudio = slot.audio_future.result()
        self.last_map_ms = slot.map_ms or prepared.generation_ms
        self.last_audio_ms = timed_audio.elapsed_ms
        self.current_index = slot.index
        self.last_error = ""
        return slot.index, slot.song, prepared

    def ensure_prefetch(self, current_end_pos: list[float], current_end_dir: list[int]):
        target = min(max(int(Config.playlist_prefetch_count), 1), len(self.songs))
        missing = target - len(self.slots)
        if missing <= 0:
            return
        if self._map_batch_future is not None and not self._map_batch_future.done():
            return

        if self.slots:
            last = self.slots[-1]
            if self._future_error(last.map_future, "") or not last.map_future.done():
                return
            prepared: PreparedMap = last.map_future.result()
            start_pos, start_dir = prepared.end_state
            base_index = last.index
        else:
            start_pos, start_dir = current_end_pos, current_end_dir
            base_index = self.current_index
        self._submit_batch(
            self._candidate_indices(base_index, missing),
            start_pos,
            start_dir,
            replace=False,
        )

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
        pending_ms = (perf_counter() - first.submitted_at) * 1000 if first else 0.0
        map_ms = first.map_ms if first else self.last_map_ms
        audio_ms = first.audio_ms if first else self.last_audio_ms
        if first and first.audio_future.done() and not self._future_error(first.audio_future, ""):
            timed_audio: TimedAudio = first.audio_future.result()
            audio_ms = timed_audio.elapsed_ms
        return {
            "queued": len(self.slots),
            "ready": self.ready_count(),
            "pending_ms": pending_ms,
            "map_ms": map_ms,
            "audio_ms": audio_ms,
            "next_song": getattr(first.song, "name", "") if first else "",
            "error": self.last_error,
        }

    def shutdown(self):
        self._generation += 1
        self.slots.clear()
        self._audio_executor.shutdown(wait=False, cancel_futures=True)
        if self._map_executor is not None:
            self._map_executor.shutdown(wait=False, cancel_futures=True)
