import json
import os
from io import BytesIO
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import mido
import pygame

from bounce import Bounce
from config import Config
from routeplanner import _segment_hits_box, count_route_violations, plan_route
from songselector import make_song_from_zip
from streaming import map_settings_snapshot, prepare_notes_map, prepare_song_map, song_to_spec
from utils import read_midi_notes, thin_notes


def _midi_bytes(notes: list[tuple[int, int, int]], ticks_per_beat: int = 480) -> bytes:
    """notes: (start_tick, end_tick, pitch) at the default 120 BPM, so 480 ticks = 0.5 s."""
    events = []
    for start, end, pitch in notes:
        events.append((start, 1, mido.Message("note_on", note=pitch, velocity=90)))
        events.append((end, 0, mido.Message("note_off", note=pitch, velocity=0)))
    events.sort(key=lambda event: (event[0], event[1]))
    track, last = mido.MidiTrack(), 0
    for tick, _, message in events:
        track.append(message.copy(time=tick - last))
        last = tick
    midi = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    midi.tracks.append(track)
    buffer = BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


def _melody_times(count: int, seed: int = 5) -> list[float]:
    rng = random.Random(seed)
    t, out = 0.4, []
    for _ in range(count):
        t += rng.choice((0.1, 0.15, 0.2, 0.2, 0.3, 0.4, 0.6, 1.0))
        out.append(round(t, 3))
    return out


class MelodyMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((800, 600))

    def setUp(self):
        self.saved = {name: getattr(Config, name) for name in (
            "direction_change_chance", "bounce_min_spacing", "seed", "max_notes", "square_speed")}
        Config.seed = 1
        Config.max_notes = None
        Config.bounce_min_spacing = 30
        Config.square_speed = 600

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(Config, name, value)

    # ---- notes ----------------------------------------------------------------------------------------------------
    def test_read_midi_notes_keeps_pitch_and_length_and_collapses_chords_to_top(self):
        data = _midi_bytes([(0, 480, 60), (0, 240, 67), (480, 960, 62)])
        self.assertEqual(read_midi_notes(BytesIO(data)), [(0.0, 67, 0.5), (0.5, 62, 0.5)])

    def test_ornament_folds_onto_main_note_but_fast_runs_survive(self):
        grace = [(1.0, 72, 0.04), (1.05, 71, 0.5)]  # 40 ms grace note, then the main note 50 ms later
        self.assertEqual(thin_notes(grace, 70), [(1.05, 71, 0.5)])
        sixteenths = [(i * 0.095, 60 + i, 0.09) for i in range(4)]  # 95 ms apart: all stay
        self.assertEqual(thin_notes(sixteenths, 70), sixteenths)

    def test_trill_thins_instead_of_sliding_to_its_last_note(self):
        trill = [(i * 0.05, 60 + i % 2, 0.04) for i in range(6)]  # 50 ms trill
        kept = thin_notes(trill, 70)
        self.assertEqual(kept[0][0], 0.05)  # first note was an ornament of the second
        self.assertGreater(len(kept), 1)
        self.assertTrue(all(b[0] - a[0] >= 0.07 - 1e-9 for a, b in zip(kept, kept[1:])))

    def test_song_metadata_spacing_is_a_floor(self):
        data = _midi_bytes([(0, 19, 72), (24, 480, 71), (960, 1200, 60)])  # 25 ms ornament before 71
        with TemporaryDirectory() as directory:
            pack = Path(directory) / "melody.zip"
            with ZipFile(pack, "w") as archive:
                archive.writestr("metadata.json", json.dumps({
                    "name": "t", "mapper": "t", "audio_file": "m.mid", "song_file": "m.mid",
                    "version": 2, "bounce_min_spacing": 70,
                }))
                archive.writestr("m.mid", data)
            song = make_song_from_zip(str(pack))
            self.assertEqual(song.min_spacing_ms, 70.0)
            prepared = prepare_song_map(song_to_spec(song), map_settings_snapshot(), [0.0, 0.0], [1, 1])
        self.assertEqual(prepared.unhit_notes, [0.025, 1.0])

    # ---- route planning -------------------------------------------------------------------------------------------
    def test_segment_box_test_is_exact_and_ignores_touching(self):
        self.assertTrue(_segment_hits_box(0, 0, 100, 100, 40, 40, 60, 60))
        self.assertFalse(_segment_hits_box(0, 0, 100, 100, 60, 0, 100, 30))   # beside the diagonal, inside its bbox
        self.assertFalse(_segment_hits_box(0, 0, 50, 50, 50, 50, 70, 70))     # touches a corner only
        self.assertTrue(_segment_hits_box(10, 10, 10, 10, 0, 0, 20, 20))      # zero length inside

    def test_planned_route_never_passes_through_pegs(self):
        times = _melody_times(400)
        bounces, stats = plan_route(times, [0.0, 0.0], [1, 1])
        self.assertEqual(len(bounces), len(times))
        self.assertEqual(stats.conflicts, 0)
        self.assertEqual(count_route_violations(bounces, [0.0, 0.0]), 0)

    def test_unchecked_random_route_is_flagged_by_checker(self):
        # the old generator never looked at the route; the checker must catch that on the same notes
        times = _melody_times(400)
        rng = random.Random(3)
        pos, direction, previous, bounces = [0.0, 0.0], [1, 1], 0.0, []
        for t in times:
            pos = [pos[0] + direction[0] * 600 * (t - previous), pos[1] + direction[1] * 600 * (t - previous)]
            axis = rng.randrange(2)
            direction = direction.copy()
            direction[axis] *= -1
            bounces.append(Bounce(pos, direction, t, axis))
            previous = t
        self.assertGreater(count_route_violations(bounces, [0.0, 0.0]), 0)

    def test_positions_follow_note_timing(self):
        times = _melody_times(50)
        bounces, _ = plan_route(times, [0.0, 0.0], [1, 1])
        previous_pos, previous_time, direction = [0.0, 0.0], 0.0, [1, 1]
        for bounce, t in zip(bounces, times):
            distance = 600 * (t - previous_time)
            self.assertAlmostEqual(bounce.square_pos[0], previous_pos[0] + direction[0] * distance, places=6)
            self.assertAlmostEqual(bounce.square_pos[1], previous_pos[1] + direction[1] * distance, places=6)
            self.assertEqual(sum(a != b for a, b in zip(direction, bounce.square_dir)), 1)  # exactly one axis flips
            previous_pos, previous_time, direction = bounce.square_pos, t, bounce.square_dir

    def test_same_seed_same_route(self):
        times = _melody_times(120)
        first, _ = plan_route(times, [0.0, 0.0], [1, 1], seed=9)
        second, _ = plan_route(times, [0.0, 0.0], [1, 1], seed=9)
        self.assertEqual([(b.square_pos, b.square_dir) for b in first], [(b.square_pos, b.square_dir) for b in second])

    def test_direction_change_chance_swaps_the_previous_priority_like_upstream(self):
        times, t = [], 0.0
        for i in range(8):  # growing gaps: alternating flips spiral outward, so every first choice is free
            t += 0.3 * (i + 1)
            times.append(round(t, 3))
        Config.direction_change_chance = 0
        sticky, _ = plan_route(times, [0.0, 0.0], [1, 1])
        self.assertEqual([b.bounce_dir for b in sticky], [0] * 8)
        Config.direction_change_chance = 100  # swap every note -> alternate; the old rule always picked axis 1
        swapping, _ = plan_route(times, [0.0, 0.0], [1, 1])
        self.assertEqual([b.bounce_dir for b in swapping], [1, 0] * 4)

    def test_impossible_spots_are_counted_and_planning_finishes(self):
        times = [0.5 + 0.001 * i for i in range(200)]  # pegs 0.6 px apart cannot avoid each other
        bounces, stats = plan_route(times, [0.0, 0.0], [1, 1], attempts_per_floor=50)
        self.assertEqual(len(bounces), len(times))
        self.assertGreater(stats.conflicts, 0)

    def test_live_regeneration_avoids_pegs_already_on_screen(self):
        Config.direction_change_chance = 0  # always prefer a side-wall bounce (flip x)
        notes = [0.5, 1.0, 1.5]
        free = prepare_notes_map(notes, map_settings_snapshot(), [0.0, 0.0], [1, 1])
        self.assertEqual(free.bounces[0][1], [-1, 1])  # bounce at (300, 300), then down-left
        old_peg = [(pygame.Rect(140, 440, 20, 20), 0.2)]  # sitting on that down-left route
        blocked = prepare_notes_map(notes, map_settings_snapshot(), [0.0, 0.0], [1, 1], obstacles=old_peg)
        self.assertEqual(blocked.bounces[0][1], [1, -1])  # turns up-right instead
        self.assertEqual(blocked.warning, "")

    # ---- drawing ahead ------------------------------------------------------------------------------------------------
    def test_upcoming_pegs_are_revealed_only_while_off_screen(self):
        from game import Game
        previous = Config.peg_visible_max
        Config.peg_visible_max = 1
        game = Game()
        try:
            game.world.rectangles = [pygame.Rect(0, 0, 10, 20), pygame.Rect(100, 0, 10, 20), pygame.Rect(5000, 0, 10, 20)]
            game.world.collision_times = [1.0, 2.0, 3.0]
            game.world.colors = [(255, 255, 255)] * 3
            game.safe_areas = [rect.copy() for rect in game.world.rectangles]
            game.safe_area_times = game.world.collision_times.copy()
            game.geometry_visible_since = [None] * 3
            game.safe_area_visible_since = [None] * 3

            game._reveal_unseen(pygame.Rect(-400, -300, 800, 600))
            geometry, safe_areas, next_peg, _ = game._visible_map_records(0.0)

            # 0 is the forced next peg, 2 was off screen; 1 is on screen and would pop in, so it waits
            self.assertEqual(geometry, {0, 2})
            self.assertEqual(safe_areas, {0, 2})
            game._reveal_unseen(pygame.Rect(2000, 2000, 800, 600))  # camera moved away: now 1 can appear unseen
            geometry, _, _, _ = game._visible_map_records(0.0)
            self.assertEqual(geometry, {0, 1, 2})
        finally:
            game.shutdown()
            Config.peg_visible_max = previous

    def test_planner_keeps_every_peg_of_the_song_clear_by_default(self):
        times = _melody_times(400)
        bounces, _ = plan_route(times, [0.0, 0.0], [1, 1])
        self.assertEqual(count_route_violations(bounces, [0.0, 0.0], window=float("inf")), 0)

    # ---- song clock ---------------------------------------------------------------------------------------------------
    def test_song_clock_runs_smoothly_over_a_stepping_mixer(self):
        from audioclock import AudioClock
        now = [0.0]
        mixer = lambda: int(now[0] * 1000 // 10 * 10)  # get_pos moves in 10 ms buffer steps
        clock = AudioClock(mixer, time_fn=lambda: now[0])
        clock.start_now(0.0)
        steps, previous = [], 0.0
        for frame in range(1, 600):
            now[0] = frame / 60
            position = clock.position()
            steps.append(position - previous)
            previous = position
        late = steps[60:]
        self.assertLess(max(late) - min(late), 0.001)  # every frame advances the same ~16.7 ms (raw: 10 or 20)
        self.assertLess(abs(previous - now[0]), 0.01)  # and stays on the audio

    def test_song_clock_snaps_after_a_stall(self):
        from audioclock import AudioClock
        now, mixer = [0.0], [0]
        clock = AudioClock(lambda: mixer[0], time_fn=lambda: now[0])
        clock.start_now(0.0)
        now[0], mixer[0] = 1.0, 1000
        clock.position()
        now[0], mixer[0] = 1.5, 1300  # the audio stalled: 200 ms behind the wall clock
        self.assertAlmostEqual(clock.position(), 1.3)  # snaps onto the audio (still later than the last 1.0 s)
        now[0], mixer[0] = 1.6, 1400
        self.assertAlmostEqual(clock.position(), 1.4)  # and keeps following it smoothly from there
        now[0], mixer[0] = 2.0, 1800
        self.assertAlmostEqual(clock.position(), 1.8, places=3)

    # ---- playlist transitions ----------------------------------------------------------------------------------------
    def test_truncated_map_midi_is_extended_with_the_audio_top_line(self):
        from streaming import _extend_to_audio_end
        melody = [(1.0, 72, 0.4), (2.0, 74, 0.4)]
        audio = [(1.0, 60, 0.4), (2.0, 62, 0.4), (2.2, 64, 0.2), (3.0, 65, 0.4), (6.0, 67, 0.4)]
        self.assertEqual(_extend_to_audio_end(melody, audio), melody + [(3.0, 65, 0.4), (6.0, 67, 0.4)])
        ends_together = [(1.0, 60, 0.4), (3.5, 62, 0.4)]  # within the tolerance: an outro tail, not a cut file
        self.assertEqual(_extend_to_audio_end(melody, ends_together), melody)

    def test_bundled_lit_fuse_map_now_lasts_until_its_audio_ends(self):
        from streaming import _read_notes
        spec = song_to_spec(make_song_from_zip("songs/lit-fuse.zip"))
        self.assertGreater(_read_notes(spec)[-1][0], 340.0)  # the melody file alone stops at 145.6 s of 359.5 s

    def test_next_song_plans_around_previous_songs_last_pegs_except_the_one_it_touches(self):
        import streaming
        notes = [(t, 60, 0.1) for t in _melody_times(60)]
        session = streaming._WorkerMapSession(notes, map_settings_snapshot(), [0.0, 0.0], [1, 1])
        tail = session.tail_obstacles()
        self.assertTrue(tail)
        last_rect = Bounce(*session.bounces[-1]).get_collision_rect()
        self.assertNotIn(tuple(last_rect), [rect for rect, _ in tail])
        self.assertTrue(all(t < 0 for _, t in tail))

    def test_playlist_start_keeps_the_first_song_streaming(self):
        import time
        from game import Game
        previous = (Config.max_notes, Config.map_chunk_seconds, Config.map_stream_buffer_chunks)
        Config.max_notes, Config.map_chunk_seconds, Config.map_stream_buffer_chunks = None, 5, 2
        game = Game()
        try:
            songs = [make_song_from_zip(p) for p in ("songs/tetris.zip", "songs/bgm_ryu.zip", "songs/calm_down.zip")]
            game.playlist.configure(songs, 0)
            game.playlist.prepare_current([0.0, 0.0], [1, 1])
            active = game.playlist.active_slot
            game.auto_advance = True
            game._prepare_next_song()  # what start_playlist does right after the first song starts

            self.assertIs(game.playlist.active_slot, active)
            self.assertFalse(active.cancelled)
            self.assertFalse(game.playlist.slots[0].map_started)  # waits for the playing song's whole map
            deadline = time.monotonic() + 30
            while not active.map_complete:  # drain chunk by chunk, like the game loop
                game.playlist.claim_chunks_until(active, float("inf"))
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertEqual(active.error, "")
            end_pos, end_dir = active.complete_future.result(timeout=30)
            following = game.playlist.slots[0].map_future.result(timeout=30)
            self.assertEqual((following.start_pos, following.start_dir), (end_pos, end_dir))
        finally:
            game.shutdown()
            Config.max_notes, Config.map_chunk_seconds, Config.map_stream_buffer_chunks = previous


if __name__ == "__main__":
    unittest.main()
