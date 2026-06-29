from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import random
from typing import Optional
from zipfile import ZipFile

from bounce import Bounce
from config import Config
from errors import MapLoadingFailureError
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
class PreparedMap:
    bounces: list[tuple[list[float], list[int], float, int]]
    unhit_notes: list[float]
    start_pos: list[float]
    start_dir: list[int]
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
    collision_conflicts = 0
    previous_time = start_time
    collision_retention = max(float(Config.map_retention_seconds) * 2, 10.0)

    for timestamp in notes:
        elapsed = max(timestamp - previous_time, 0.0)
        square.x += square.dir_x * Config.square_speed * elapsed
        square.y += square.dir_y * Config.square_speed * elapsed

        while recent_colliders and recent_colliders[0][0] < timestamp - collision_retention:
            recent_colliders.popleft()

        preferred_axis = 1 if rng.random() * 100 < Config.direction_change_chance else 0
        axes = (preferred_axis, 1 - preferred_axis)
        candidates = []
        for axis in axes:
            direction = square.dir.copy()
            direction[axis] *= -1
            candidate = Bounce(square.pos, direction, timestamp, axis)
            collision_rect = candidate.get_collision_rect()
            collisions = sum(collision_rect.colliderect(existing[1]) for existing in recent_colliders)
            candidates.append((collisions, candidate, collision_rect))

        collisions, selected, collision_rect = min(candidates, key=lambda item: item[0])
        if collisions:
            collision_conflicts += 1
        square.dir = selected.square_dir.copy()
        output.append(selected)
        recent_colliders.append((timestamp, collision_rect))
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
    return PreparedMap(
        bounces=serialized,
        unhit_notes=unhit_notes,
        start_pos=start_pos.copy(),
        start_dir=start_dir.copy(),
        used_fallback=used_fallback,
        warning=warning,
    )


class PlaylistController:
    """Prepares one queued song while the current song is playing."""

    def __init__(self):
        self.songs = []
        self.current_index = -1
        self.next_index = -1
        self.next_spec: Optional[SongSpec] = None
        self.map_future: Optional[Future] = None
        self.audio_future: Optional[Future] = None
        self.audio_claimed = False
        self.last_error = ""
        self._map_executor: Optional[ProcessPoolExecutor] = None
        self._audio_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-preload")

    def configure(self, songs: list, current_index: int):
        self.songs = list(songs)
        self.current_index = current_index

    def _ensure_map_executor(self):
        if self._map_executor is None:
            self._map_executor = ProcessPoolExecutor(max_workers=1)

    def prepare_next(self, start_pos: list[float], start_dir: list[int]):
        if not self.songs:
            return
        self.next_index = (self.current_index + 1) % len(self.songs)
        self.next_spec = song_to_spec(self.songs[self.next_index])
        self.audio_claimed = False
        self.last_error = ""
        self._ensure_map_executor()
        self.map_future = self._map_executor.submit(
            prepare_song_map,
            self.next_spec,
            map_settings_snapshot(),
            start_pos.copy(),
            start_dir.copy(),
        )
        self.audio_future = self._audio_executor.submit(read_song_audio, self.next_spec)

    def refresh_next_map(self, start_pos: list[float], start_dir: list[int]):
        if self.next_spec is None:
            return
        self._ensure_map_executor()
        self.map_future = self._map_executor.submit(
            prepare_song_map,
            self.next_spec,
            map_settings_snapshot(),
            start_pos.copy(),
            start_dir.copy(),
        )

    def claim_audio(self) -> Optional[tuple[bytes, str]]:
        if self.audio_claimed or self.audio_future is None or not self.audio_future.done():
            return None
        self.audio_claimed = True
        try:
            return self.audio_future.result()
        except Exception as exc:
            self.last_error = f"Audio preload failed: {exc}"
            return None

    def consume_prepared_map(self) -> Optional[tuple[int, object, PreparedMap]]:
        if self.map_future is None or not self.map_future.done():
            return None
        try:
            prepared = self.map_future.result()
        except Exception as exc:
            self.last_error = f"Map generation failed: {exc}"
            return None

        index = self.next_index
        song = self.songs[index]
        self.current_index = index
        self.map_future = None
        self.audio_future = None
        self.next_spec = None
        return index, song, prepared

    def shutdown(self):
        self._audio_executor.shutdown(wait=False, cancel_futures=True)
        if self._map_executor is not None:
            self._map_executor.shutdown(wait=False, cancel_futures=True)
