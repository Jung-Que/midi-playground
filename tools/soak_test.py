"""Accelerated long-session validation using real bundled audio and MIDI maps."""

from __future__ import annotations

import argparse
from io import BytesIO
import gc
import json
import os
from pathlib import Path
import statistics
import sys
from time import perf_counter
import tracemalloc


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402

from config import Config  # noqa: E402
from game import Game  # noqa: E402
from songselector import make_song_from_zip  # noqa: E402
from streaming import map_settings_snapshot, prepare_song_map, read_song_audio, song_to_spec  # noqa: E402


class SoakFailure(RuntimeError):
    pass


def public_songs() -> list:
    songs = [
        make_song_from_zip(str(path))
        for path in sorted((ROOT / "songs").glob("*.zip"))
    ]
    if len(songs) < 2:
        raise SoakFailure("At least two public song packs are required")
    return songs


def validate_trajectory(prepared, speed: float) -> None:
    previous_position = prepared.start_pos.copy()
    previous_direction = prepared.start_dir.copy()
    previous_time = 0.0
    for index, (position, direction, timestamp, axis) in enumerate(prepared.bounces):
        elapsed = max(float(timestamp) - previous_time, 0.0)
        expected_position = [
            previous_position[coordinate] + previous_direction[coordinate] * speed * elapsed
            for coordinate in range(2)
        ]
        if any(abs(position[coordinate] - expected_position[coordinate]) > 0.01 for coordinate in range(2)):
            raise SoakFailure(f"Trajectory discontinuity at bounce {index}")
        expected_direction = previous_direction.copy()
        expected_direction[int(axis)] *= -1
        if list(direction) != expected_direction:
            raise SoakFailure(f"Invalid direction change at bounce {index}")
        if timestamp < previous_time:
            raise SoakFailure(f"Bounce order moved backwards at bounce {index}")
        previous_position = list(position)
        previous_direction = list(direction)
        previous_time = float(timestamp)


def load_audio(spec) -> int:
    audio_data, extension = read_song_audio(spec)
    if not audio_data:
        raise SoakFailure(f"Audio is empty: {spec.name}")
    buffer = BytesIO(audio_data)
    try:
        pygame.mixer.music.load(buffer, namehint=extension)
    except TypeError:
        pygame.mixer.music.load(buffer)
    pygame.mixer.music.play()
    pygame.mixer.music.stop()
    if hasattr(pygame.mixer.music, "unload"):
        pygame.mixer.music.unload()
    return len(audio_data)


def percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def run_soak(simulated_minutes: float, memory_growth_limit_mb: float) -> dict:
    target_seconds = max(float(simulated_minutes), 0.1) * 60.0
    songs = public_songs()
    setting_names = (
        "max_notes", "music_offset", "start_playing_delay", "square_speed",
        "do_particles_on_bounce", "map_retention_seconds", "map_preload_seconds",
        "map_chunk_seconds", "camera_mode", "SCREEN_WIDTH", "SCREEN_HEIGHT",
    )
    previous_settings = {name: getattr(Config, name) for name in setting_names}
    Config.max_notes = None
    Config.music_offset = 0
    Config.start_playing_delay = 0
    Config.do_particles_on_bounce = False
    Config.map_retention_seconds = 8
    Config.map_preload_seconds = 30
    Config.map_chunk_seconds = 15
    Config.SCREEN_WIDTH = 800
    Config.SCREEN_HEIGHT = 600

    pygame.init()
    pygame.mixer.init()
    screen = pygame.display.set_mode((800, 600))
    Config.screen = screen
    game = Game()
    screen_rect = screen.get_rect()
    tracemalloc.start()
    started_at = perf_counter()
    simulated_seconds = 0.0
    transitions = 0
    total_bounces = 0
    total_audio_bytes = 0
    fallback_tracks = 0
    max_geometry = 0
    max_safe_areas = 0
    max_chunks = 0
    peak_notes_per_second = 0
    generation_times: list[float] = []
    transition_times: list[float] = []
    track_results: list[dict] = []
    start_position = [0.0, 0.0]
    start_direction = [1, 1]
    baseline_memory = None

    try:
        while simulated_seconds < target_seconds:
            song = songs[transitions % len(songs)]
            speed = (600, 900, 1200)[transitions % 3]
            Config.square_speed = speed
            settings = map_settings_snapshot()
            settings["square_speed"] = speed
            transition_started = perf_counter()
            spec = song_to_spec(song)
            audio_bytes = load_audio(spec)
            prepared = prepare_song_map(spec, settings, start_position, start_direction)
            transition_ms = (perf_counter() - transition_started) * 1000
            if not prepared.bounces or not prepared.chunks:
                raise SoakFailure(f"Map is empty: {song.name}")
            validate_trajectory(prepared, speed)

            duration = max(float(prepared.bounces[-1][2]), 0.1)
            counts: dict[int, int] = {}
            for bounce in prepared.bounces:
                second = int(bounce[2])
                counts[second] = counts.get(second, 0) + 1
            track_peak_density = max(counts.values(), default=0)
            peak_notes_per_second = max(peak_notes_per_second, track_peak_density)
            if prepared.warning:
                fallback_tracks += 1

            game._apply_prepared_map(prepared)
            sample_time = 0.0
            while sample_time <= duration + Config.map_retention_seconds:
                game.world.time = sample_time
                game.world.handle_bouncing(game.world.square)
                game.camera.pos = [
                    game.world.square.x - Config.SCREEN_WIDTH / 2,
                    game.world.square.y - Config.SCREEN_HEIGHT / 2,
                ]
                game._prune_rolling_world(screen_rect)
                max_geometry = max(max_geometry, len(game.world.rectangles))
                max_safe_areas = max(max_safe_areas, len(game.safe_areas))
                max_chunks = max(max_chunks, len(game.map_chunks))
                sample_time += 1.0

            start_position, start_direction = prepared.end_state
            simulated_seconds += duration
            transitions += 1
            total_bounces += len(prepared.bounces)
            total_audio_bytes += audio_bytes
            generation_times.append(prepared.generation_ms)
            transition_times.append(transition_ms)
            track_results.append({
                "song": song.name,
                "duration_seconds": round(duration, 3),
                "speed": speed,
                "bounces": len(prepared.bounces),
                "peak_notes_per_second": track_peak_density,
                "generation_ms": round(prepared.generation_ms, 3),
                "transition_prepare_ms": round(transition_ms, 3),
                "least_collision_fallback": bool(prepared.warning),
            })
            gc.collect()
            current_memory, _ = tracemalloc.get_traced_memory()
            if baseline_memory is None and transitions >= len(songs):
                baseline_memory = current_memory

        current_memory, peak_memory = tracemalloc.get_traced_memory()
        if baseline_memory is None:
            baseline_memory = current_memory
        memory_growth_mb = max(current_memory - baseline_memory, 0) / 1024 / 1024
        if memory_growth_mb > memory_growth_limit_mb:
            raise SoakFailure(
                f"Python memory grew {memory_growth_mb:.1f} MB; limit is {memory_growth_limit_mb:.1f} MB"
            )
        if max_geometry > 5000 or max_safe_areas > 5000:
            raise SoakFailure(
                f"Rolling map exceeded bounds: geometry={max_geometry}, safe_areas={max_safe_areas}"
            )

        return {
            "status": "passed",
            "simulated_minutes": round(simulated_seconds / 60, 2),
            "wall_seconds": round(perf_counter() - started_at, 3),
            "songs_available": len(songs),
            "transitions": transitions,
            "total_bounces": total_bounces,
            "audio_megabytes_loaded": round(total_audio_bytes / 1024 / 1024, 2),
            "speed_variants": [600, 900, 1200],
            "peak_notes_per_second": peak_notes_per_second,
            "fallback_tracks": fallback_tracks,
            "generation_average_ms": round(statistics.mean(generation_times), 3),
            "generation_p95_ms": round(percentile_95(generation_times), 3),
            "transition_prepare_p95_ms": round(percentile_95(transition_times), 3),
            "max_geometry": max_geometry,
            "max_safe_areas": max_safe_areas,
            "max_chunks": max_chunks,
            "python_memory_current_mb": round(current_memory / 1024 / 1024, 2),
            "python_memory_peak_mb": round(peak_memory / 1024 / 1024, 2),
            "python_memory_growth_after_first_cycle_mb": round(memory_growth_mb, 2),
            "tracks": track_results,
        }
    finally:
        game.shutdown()
        pygame.quit()
        tracemalloc.stop()
        for name, value in previous_settings.items():
            setattr(Config, name, value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulated-minutes", type=float, default=60.0)
    parser.add_argument("--memory-growth-limit-mb", type=float, default=96.0)
    parser.add_argument("--report", type=Path, default=ROOT / "exports" / "soak" / "soak-report.json")
    args = parser.parse_args()
    try:
        report = run_soak(args.simulated_minutes, args.memory_growth_limit_mb)
    except (OSError, pygame.error, SoakFailure) as exc:
        print(f"Soak test failed: {exc}", file=sys.stderr)
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Soak report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
