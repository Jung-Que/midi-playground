import os
import random
from io import BytesIO
from time import monotonic, sleep, time
from types import SimpleNamespace
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import game as game_module
from audioclock import AudioClock
from camera import Camera
from config import Config
from configpage import CAMERA_MODE_LABELS, ConfigPage
from game import Game
from liveconfig import LiveConfigOverlay
from particle import Particle
from songselector import SongSelector, make_song_from_zip
from spatial import SpatialHash
from square import Square
from streaming import (
    MapChunk,
    PlaylistController,
    PreparedMap,
    map_settings_snapshot,
    prepare_song_map,
    read_song_audio,
    song_to_spec,
)
from utils import CameraFollow, get_camera_follow


class StreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((800, 600))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_prepares_serializable_map(self):
        previous_max_notes = Config.max_notes
        Config.max_notes = 32
        try:
            song = make_song_from_zip("songs/bad-piggies.zip")
            prepared = prepare_song_map(
                song_to_spec(song),
                map_settings_snapshot(),
                [0.0, 0.0],
                [1, 1],
            )
        finally:
            Config.max_notes = previous_max_notes

        self.assertGreater(len(prepared.bounces), 0)
        self.assertGreater(len(prepared.unhit_notes), 0)

    def test_local_song_packs_are_marked_and_scanned_before_public_songs(self):
        song = make_song_from_zip("songs/bad-piggies.zip", local_only=True)
        self.assertTrue(song.local_only)
        self.assertEqual(SongSelector.SONG_DIRECTORIES[0], "songs-local")

    def test_prunes_only_old_offscreen_geometry(self):
        previous_retention = Config.map_retention_seconds
        Config.map_retention_seconds = 5
        game = Game()
        try:
            game.play_delay_ms = 0
            game.world.time = 20
            game.world.rectangles = [
                pygame.Rect(-5000, -5000, 10, 10),
                pygame.Rect(10, 10, 10, 10),
                pygame.Rect(5000, 5000, 10, 10),
            ]
            game.world.collision_times = [0, 0, 19]
            game.world.colors = ["old", "visible", "future"]
            game.safe_areas = [rect.copy() for rect in game.world.rectangles]
            game.safe_area_times = game.world.collision_times.copy()

            game._prune_rolling_world(pygame.Rect(0, 0, 800, 600))

            self.assertEqual(game.world.colors, ["visible", "future"])
            self.assertEqual(len(game.safe_areas), 2)
        finally:
            game.shutdown()
            Config.map_retention_seconds = previous_retention

    def test_playlist_prepares_next_song_out_of_process(self):
        previous_max_notes = Config.max_notes
        Config.max_notes = 16
        controller = PlaylistController()
        try:
            songs = [
                make_song_from_zip("songs/bad-piggies.zip"),
                make_song_from_zip("songs/tetris.zip"),
            ]
            controller.configure(songs, 0)
            controller.prepare_next([0.0, 0.0], [1, 1])
            prepared = controller.map_future.result(timeout=30)
            audio = controller.audio_future.result(timeout=30)
            self.assertGreater(len(prepared.bounces), 0)
            self.assertGreater(len(audio[0]), 0)
        finally:
            controller.shutdown()
            Config.max_notes = previous_max_notes

    def test_playlist_prefetches_two_dependency_ordered_tracks(self):
        previous_max_notes = Config.max_notes
        previous_prefetch = Config.playlist_prefetch_count
        Config.max_notes = 12
        Config.playlist_prefetch_count = 2
        controller = PlaylistController()
        try:
            songs = [
                make_song_from_zip("songs/bad-piggies.zip"),
                make_song_from_zip("songs/tetris.zip"),
                make_song_from_zip("songs/rush-e.zip"),
            ]
            controller.configure(songs, 0)
            controller.prepare_ahead([0.0, 0.0], [1, 1])
            first, second = controller.slots
            first.map_future.result(timeout=30)
            first_end = first.complete_future.result(timeout=30)
            second.map_future.result(timeout=30)
            second.audio_future.result(timeout=30)

            self.assertEqual([slot.index for slot in controller.slots], [1, 2])
            self.assertEqual(controller.ready_count(), 2)
            self.assertEqual(second.map_future.result().start_pos, first_end[0])
            self.assertEqual(second.map_future.result().start_dir, first_end[1])
        finally:
            controller.shutdown()
            Config.max_notes = previous_max_notes
            Config.playlist_prefetch_count = previous_prefetch

    def test_map_stream_emits_chunks_incrementally_with_backpressure(self):
        previous_max_notes = Config.max_notes
        previous_chunk = Config.map_chunk_seconds
        previous_buffer = Config.map_stream_buffer_chunks
        Config.max_notes = 64
        Config.map_chunk_seconds = 1
        Config.map_stream_buffer_chunks = 2
        controller = PlaylistController()
        try:
            song = make_song_from_zip("songs/tetris.zip")
            controller.configure([song], 0)
            prepared = controller.prepare_current([0.0, 0.0], [1, 1])
            slot = controller.active_slot
            deadline = monotonic() + 10
            while len(slot.chunk_queue) < 2 and not slot.map_complete and monotonic() < deadline:
                sleep(0.01)

            self.assertEqual(len(prepared.chunks), 1)
            self.assertLessEqual(len(slot.chunk_queue), 2)
            self.assertFalse(slot.map_complete)

            claimed_count = 1
            while not controller.stream_drained(slot) and monotonic() < deadline:
                claimed_count += len(controller.claim_chunks_until(slot, float("inf")))
                sleep(0.01)

            claimed_count += len(controller.claim_chunks_until(slot, float("inf")))
            self.assertTrue(controller.stream_drained(slot))
            self.assertGreater(claimed_count, 2)
        finally:
            controller.shutdown()
            Config.max_notes = previous_max_notes
            Config.map_chunk_seconds = previous_chunk
            Config.map_stream_buffer_chunks = previous_buffer

    def test_audio_clock_prefers_plausible_mixer_position(self):
        now = [100.0]
        mixer_ms = [250]
        clock = AudioClock(lambda: mixer_ms[0], time_fn=lambda: now[0])
        started_at = clock.start_from_transition(99.5, max_probe_ms=5000)
        self.assertAlmostEqual(started_at, 99.75)

        now[0] = 100.5
        mixer_ms[0] = 750
        self.assertAlmostEqual(clock.position(), 0.75)
        self.assertTrue(clock.using_mixer)

        mixer_ms[0] = 30_000
        now[0] = 101.0
        self.assertAlmostEqual(clock.position(), 1.25)
        self.assertFalse(clock.using_mixer)

    def test_game_ingests_stream_chunks_without_loading_full_geometry(self):
        previous_max_notes = Config.max_notes
        previous_chunk = Config.map_chunk_seconds
        previous_buffer = Config.map_stream_buffer_chunks
        original_update_screen = game_module.update_screen
        Config.max_notes = 64
        Config.map_chunk_seconds = 1
        Config.map_stream_buffer_chunks = 2
        game_module.update_screen = lambda *args, **kwargs: None
        screen = pygame.display.get_surface()
        Config.screen = screen
        game = Game()
        game.active = True
        try:
            song = make_song_from_zip("songs/tetris.zip")
            self.assertIsNone(game.start_playlist([song], 0, screen))
            initial_total = game.world.total_bounces
            deadline = monotonic() + 10
            while not game.active_map_stream.chunk_queue and monotonic() < deadline:
                sleep(0.01)

            game._ingest_stream_chunks(1.0)

            self.assertGreater(game.world.total_bounces, initial_total)
            self.assertGreater(len(game.map_chunks), 1)
            self.assertLessEqual(len(game.active_map_stream.chunk_queue), 2)
        finally:
            game.shutdown()
            game_module.update_screen = original_update_screen
            Config.max_notes = previous_max_notes
            Config.map_chunk_seconds = previous_chunk
            Config.map_stream_buffer_chunks = previous_buffer

    def test_streamed_bounces_keep_the_initial_schedule_offset(self):
        initial = ([100.0, 100.0], [1, -1], 14.0, 1)
        streamed = ([200.0, 200.0], [-1, -1], 15.0, 0)
        prepared = PreparedMap(
            bounces=[initial],
            unhit_notes=[14.0, 15.0],
            start_pos=[0.0, 0.0],
            start_dir=[1, 1],
            chunks=[MapChunk(0, 15, [initial])],
            complete=False,
        )
        game = Game()
        try:
            game.play_delay_ms = 3000
            game._apply_prepared_map(prepared)
            game._shift_pending_bounce_schedule(3.0)
            stream = SimpleNamespace(error="")
            game.active_map_stream = stream
            game.playlist.claim_chunks_until = lambda _slot, _horizon: [
                MapChunk(15, 30, [streamed], [100.0, 100.0], [1, -1])
            ]
            game.playlist.stream_drained = lambda _slot: False

            game._ingest_stream_chunks(0.0)

            schedule = [bounce.time for bounce in game.world.future_bounces]
            self.assertEqual(schedule, [17.0, 18.0])
            self.assertEqual(schedule, sorted(schedule))
            self.assertEqual(game.world.collision_times, [14.0, 15.0])
        finally:
            game.shutdown()

    def test_world_does_not_stop_while_more_chunks_are_pending(self):
        game = Game()
        try:
            game.world.future_bounces.append(
                game_module.Bounce([10.0, 10.0], [-1, 1], 0.1, 0)
            )
            game.world.map_stream_complete = False
            game.world.time = 1.0
            game.world.handle_bouncing(game.world.square)
            self.assertEqual(game.world.square.dir, [-1, 1])
        finally:
            game.shutdown()

    def test_active_stream_failure_stops_map_safely(self):
        game = Game()
        try:
            game.active_map_stream = SimpleNamespace(error="worker lost")
            game.world.map_stream_complete = False
            game._ingest_stream_chunks(1.0)
            self.assertIsNone(game.active_map_stream)
            self.assertTrue(game.world.map_stream_complete)
            self.assertIn("stopped safely", game.stream_message)
        finally:
            game.shutdown()

    def test_multiple_real_mp3_assets_load_in_sequence(self):
        songs = [
            make_song_from_zip("songs/calm_down.zip"),
            make_song_from_zip("songs/wii_theme.zip"),
        ]
        for song in songs:
            data, extension = read_song_audio(song_to_spec(song))
            self.assertEqual(extension, ".mp3")
            self.assertGreater(len(data), 1024)
            pygame.mixer.music.load(BytesIO(data), namehint=extension)

    def test_game_promotes_between_real_mp3_tracks(self):
        previous_max_notes = Config.max_notes
        original_update_screen = game_module.update_screen
        Config.max_notes = 12
        game_module.update_screen = lambda *args, **kwargs: None
        screen = pygame.display.get_surface()
        Config.screen = screen
        songs = [
            make_song_from_zip("songs/calm_down.zip"),
            make_song_from_zip("songs/wii_theme.zip"),
        ]
        game = Game()
        game.active = True
        try:
            self.assertIsNone(game.start_playlist(songs, 0, screen))
            first_slot = game.playlist.slots[0]
            first_slot.map_future.result(timeout=30)
            first_slot.audio_future.result(timeout=30)
            game.music_has_played = True
            game._queue_next_audio()
            pygame.mixer.music.stop()
            game.transition_pending = True
            game.transition_started_at = time()
            game.transition_wait_started_at = game.transition_started_at

            game._update_playlist_transition()

            self.assertEqual(Config.current_song.name, songs[1].name)
            self.assertFalse(game.transition_pending)
            self.assertGreater(game.world.total_bounces, 0)
        finally:
            game.shutdown()
            game_module.update_screen = original_update_screen
            Config.max_notes = previous_max_notes

    def test_failed_track_is_skipped_and_prefetch_continues(self):
        previous_max_notes = Config.max_notes
        Config.max_notes = 8
        controller = PlaylistController()
        try:
            current = make_song_from_zip("songs/bad-piggies.zip")
            broken = make_song_from_zip("songs/tetris.zip")
            following = make_song_from_zip("songs/rush-e.zip")
            broken.fp = "songs/does-not-exist.zip"
            controller.configure([current, broken, following], 0)
            controller.prepare_ahead([0.0, 0.0], [1, 1])
            with self.assertRaises(Exception):
                controller.map_future.result(timeout=30)
            self.assertIn("failed", controller.first_error().lower())

            skipped = controller.skip_failed([0.0, 0.0], [1, 1])

            self.assertIs(skipped, broken)
            self.assertEqual(controller.next_index, 2)
        finally:
            controller.shutdown()
            Config.max_notes = previous_max_notes

    def test_transition_waits_without_starting_an_unprepared_track(self):
        class WaitingPlaylist:
            songs = [object(), object()]
            slots = [object()]
            last_error = ""

            @staticmethod
            def first_error():
                return ""

            @staticmethod
            def first_ready():
                return False

            @staticmethod
            def ensure_prefetch(*_args):
                return None

            @staticmethod
            def metrics():
                return {"ready": 0, "queued": 1, "pending_ms": 10, "map_ms": 0, "audio_ms": 0}

            @staticmethod
            def shutdown():
                return None

        game = Game()
        game.playlist.shutdown()
        game.playlist = WaitingPlaylist()
        game.auto_advance = True
        game.music_has_played = True
        game.transition_pending = True
        game.transition_wait_started_at = time()
        try:
            game._update_playlist_transition()
            self.assertTrue(game.transition_pending)
            self.assertIn("Preparing next track", game.stream_message)
        finally:
            game.shutdown()

    def test_spatial_hash_limits_collision_candidates(self):
        index = SpatialHash(cell_size=100)
        index.insert("near", pygame.Rect(10, 10, 20, 20))
        index.insert("far", pygame.Rect(1000, 1000, 20, 20))
        self.assertEqual(index.query(pygame.Rect(0, 0, 50, 50)), {"near"})
        index.remove("near")
        self.assertEqual(index.query(pygame.Rect(0, 0, 50, 50)), set())

    def test_hour_long_chunk_window_keeps_geometry_bounded(self):
        previous_chunk = Config.map_chunk_seconds
        previous_preload = Config.map_preload_seconds
        previous_retention = Config.map_retention_seconds
        Config.map_chunk_seconds = 15
        Config.map_preload_seconds = 30
        Config.map_retention_seconds = 5
        bounces = []
        chunks = []
        for second in range(1, 3601):
            bounce = ([float(second * 20), 0.0], [-1 if second % 2 else 1, 1], float(second), 0)
            bounces.append(bounce)
            chunk_start = int(second // 15) * 15
            if not chunks or chunks[-1].start_time != chunk_start:
                chunks.append(MapChunk(chunk_start, chunk_start + 15, []))
            chunks[-1].bounces.append(bounce)
        prepared = PreparedMap(
            bounces=bounces,
            unhit_notes=[float(value) for value in range(1, 3601)],
            start_pos=[0.0, 0.0],
            start_dir=[1, 1],
            chunks=chunks,
        )
        game = Game()
        try:
            game.play_delay_ms = 0
            game._apply_prepared_map(prepared)
            for map_time in range(0, 3601, 60):
                game.world.time = float(map_time)
                game._prune_rolling_world(pygame.Rect(0, 0, 800, 600))
                self.assertLessEqual(game.active_map_chunk_count, 4)
                # Future records plus old records still inside the expanded
                # viewport stay alive; both sets remain bounded.
                self.assertLessEqual(len(game.world.rectangles), 120)
            self.assertLessEqual(len(game.map_chunks), 2)
        finally:
            game.shutdown()
            Config.map_chunk_seconds = previous_chunk
            Config.map_preload_seconds = previous_preload
            Config.map_retention_seconds = previous_retention

    def test_chunk_boundary_reveals_individual_bounces(self):
        previous_preload = Config.map_preload_seconds
        Config.map_preload_seconds = 5
        bounces = [
            ([float(second * 100), 0.0], [1, 1], float(second), 0)
            for second in range(1, 31)
        ]
        chunks = [
            MapChunk(0, 15, bounces[:15]),
            MapChunk(15, 30, bounces[15:]),
        ]
        prepared = PreparedMap(
            bounces=bounces,
            unhit_notes=[],
            start_pos=[0.0, 0.0],
            start_dir=[1, 1],
            chunks=chunks,
        )
        game = Game()
        try:
            game._apply_prepared_map(prepared)
            before = len(game.world.rectangles)
            game._refresh_map_window(10.0)
            at_boundary = len(game.world.rectangles)
            game._refresh_map_window(10.1)
            just_after = len(game.world.rectangles)

            self.assertEqual(at_boundary - before, 10)
            self.assertEqual(just_after - at_boundary, 0)
            game._refresh_map_window(11.0)
            self.assertEqual(len(game.world.rectangles) - just_after, 1)
        finally:
            game.shutdown()
            Config.map_preload_seconds = previous_preload

    def test_streamed_chunks_keep_visible_old_geometry(self):
        previous_retention = Config.map_retention_seconds
        previous_preload = Config.map_preload_seconds
        Config.map_retention_seconds = 5
        Config.map_preload_seconds = 0
        old_visible = ([100.0, 100.0], [1, 1], 1.0, 0)
        future = ([5000.0, 5000.0], [1, 1], 30.0, 0)
        prepared = PreparedMap(
            bounces=[old_visible, future],
            unhit_notes=[],
            start_pos=[0.0, 0.0],
            start_dir=[1, 1],
            chunks=[MapChunk(0, 15, [old_visible]), MapChunk(30, 45, [future])],
        )
        game = Game()
        try:
            game._apply_prepared_map(prepared)
            game.world.time = 20
            game._prune_rolling_world(pygame.Rect(0, 0, 800, 600))

            self.assertEqual(len(game.world.rectangles), 1)
            self.assertTrue(game.world.rectangles[0].colliderect(pygame.Rect(0, 0, 800, 600)))
            self.assertEqual(len(game.geometry_visible_since), 1)
            self.assertEqual(game.world.visible_geometry(pygame.Rect(0, 0, 800, 600)), [0])
        finally:
            game.shutdown()
            Config.map_retention_seconds = previous_retention
            Config.map_preload_seconds = previous_preload

    def test_old_offscreen_records_expire_individually(self):
        previous_retention = Config.map_retention_seconds
        previous_preload = Config.map_preload_seconds
        Config.map_retention_seconds = 5
        Config.map_preload_seconds = 0
        bounces = [
            ([5000.0 + second * 100, 5000.0], [1, 1], float(second), 0)
            for second in range(1, 11)
        ]
        prepared = PreparedMap(
            bounces=bounces,
            unhit_notes=[],
            start_pos=[5000.0, 5000.0],
            start_dir=[1, 1],
            chunks=[MapChunk(0, 15, bounces)],
        )
        game = Game()
        try:
            game.play_delay_ms = 0
            game._apply_prepared_map(prepared)
            counts = []
            for map_time in range(1, 17):
                game.world.time = float(map_time)
                game._prune_rolling_world(pygame.Rect(0, 0, 800, 600))
                counts.append(len(game.world.rectangles))

            frame_changes = [abs(after - before) for before, after in zip(counts, counts[1:])]
            self.assertLessEqual(max(frame_changes), 1)
            self.assertEqual(counts[-1], 0)
            self.assertEqual(len(game.geometry_visible_since), 0)
            self.assertEqual(len(game.world.geometry_index), 0)
        finally:
            game.shutdown()
            Config.map_retention_seconds = previous_retention
            Config.map_preload_seconds = previous_preload

    def test_map_reveal_fade_is_configurable(self):
        previous_fade = Config.map_fade_seconds
        Config.map_fade_seconds = 0.4
        try:
            start = pygame.Color(0, 0, 0)
            target = pygame.Color(100, 200, 50)
            halfway = Game._reveal_color(start, target, 10.0, 10.2)
            finished = Game._reveal_color(start, target, 10.0, 10.5)
            self.assertEqual(halfway, pygame.Color(50, 100, 25))
            self.assertEqual(finished, target)
        finally:
            Config.map_fade_seconds = previous_fade

    def test_visible_peg_count_adapts_to_fast_music(self):
        previous = {
            "peg_visible_min": Config.peg_visible_min,
            "peg_visible_max": Config.peg_visible_max,
            "peg_density_window_seconds": Config.peg_density_window_seconds,
            "peg_preview_seconds": Config.peg_preview_seconds,
            "peg_overlap_padding": Config.peg_overlap_padding,
        }
        Config.peg_visible_min = 3
        Config.peg_visible_max = 6
        Config.peg_density_window_seconds = 1.0
        Config.peg_preview_seconds = 0.5
        Config.peg_overlap_padding = 0
        game = Game()
        try:
            game.world.rectangles = [
                pygame.Rect(index * 100, 0, 10, 20) for index in range(20)
            ]
            game.world.collision_times = [0.05 * (index + 1) for index in range(20)]
            game.world.colors = [(255, 255, 255)] * 20
            game.safe_areas = [rect.copy() for rect in game.world.rectangles]
            game.safe_area_times = game.world.collision_times.copy()

            geometry, safe_areas, next_peg, limit = game._visible_map_records(0.0)

            self.assertEqual(limit, 6)
            self.assertEqual(len(geometry), 6)
            self.assertEqual(len(safe_areas), 6)
            self.assertEqual(next_peg, 0)
        finally:
            game.shutdown()
            for name, value in previous.items():
                setattr(Config, name, value)

    def test_visible_peg_count_stays_small_for_slow_music(self):
        previous_min = Config.peg_visible_min
        previous_max = Config.peg_visible_max
        previous_padding = Config.peg_overlap_padding
        Config.peg_visible_min = 3
        Config.peg_visible_max = 6
        Config.peg_overlap_padding = 0
        game = Game()
        try:
            game.world.rectangles = [pygame.Rect(index * 100, 0, 10, 20) for index in range(5)]
            game.world.collision_times = [1.0, 2.0, 3.0, 4.0, 5.0]
            game.world.colors = [(255, 255, 255)] * 5
            game.safe_areas = [rect.copy() for rect in game.world.rectangles]
            game.safe_area_times = game.world.collision_times.copy()

            geometry, _, next_peg, limit = game._visible_map_records(0.0)

            self.assertEqual(limit, 3)
            self.assertEqual(geometry, {0, 1, 2})
            self.assertEqual(next_peg, 0)
        finally:
            game.shutdown()
            Config.peg_visible_min = previous_min
            Config.peg_visible_max = previous_max
            Config.peg_overlap_padding = previous_padding

    def test_next_peg_world_position_tracks_music_time(self):
        game = Game()
        try:
            game.world.rectangles = [
                pygame.Rect(100, 100, 10, 20),
                pygame.Rect(300, 200, 10, 20),
            ]
            game.world.collision_times = [1.0, 2.0]

            self.assertEqual(game._next_peg_world_position(1.5), (305, 210))
            self.assertIsNone(game._next_peg_world_position(2.1))
        finally:
            game.shutdown()

    def test_target_lead_camera_moves_toward_next_peg_smoothly(self):
        previous = {
            "SCREEN_WIDTH": Config.SCREEN_WIDTH,
            "SCREEN_HEIGHT": Config.SCREEN_HEIGHT,
            "dt": Config.dt,
            "camera_target_lead": Config.camera_target_lead,
            "camera_max_lead_ratio": Config.camera_max_lead_ratio,
            "camera_smoothing_seconds": Config.camera_smoothing_seconds,
            "camera_max_speed": Config.camera_max_speed,
        }
        Config.SCREEN_WIDTH = 800
        Config.SCREEN_HEIGHT = 600
        Config.dt = 1 / 60
        Config.camera_target_lead = 0.3
        Config.camera_max_lead_ratio = 0.22
        Config.camera_smoothing_seconds = 0.2
        Config.camera_max_speed = 2400
        camera = Camera()
        camera.lock_type = CameraFollow.TargetLead
        square = Square(400, 300, 1, 1)
        try:
            camera.follow(square, (800, 300))
            first_x = camera.x
            camera.follow(square, (800, 300))

            self.assertGreater(first_x, 0)
            self.assertGreater(camera.x, first_x)
            self.assertLess(camera.x, 120)
            self.assertAlmostEqual(camera.y, 0.0)
        finally:
            for name, value in previous.items():
                setattr(Config, name, value)

    def test_config_page_supports_target_lead_and_invalid_saved_modes(self):
        previous_mode = Config.camera_mode
        try:
            def selected_label(page):
                selected = page.s_camera_mode.selected_option
                return selected[0] if isinstance(selected, tuple) else selected

            Config.camera_mode = CameraFollow.TargetLead.value
            page = ConfigPage()
            self.assertEqual(
                selected_label(page),
                CAMERA_MODE_LABELS[CameraFollow.TargetLead],
            )

            Config.camera_mode = 999
            fallback_page = ConfigPage()
            self.assertEqual(Config.camera_mode, CameraFollow.TargetLead.value)
            self.assertEqual(
                selected_label(fallback_page),
                CAMERA_MODE_LABELS[CameraFollow.TargetLead],
            )
            self.assertEqual(get_camera_follow("invalid"), CameraFollow.TargetLead)
        finally:
            Config.camera_mode = previous_mode

    def test_target_vfx_draws_with_numbering_countdown_and_impact(self):
        previous = {
            "theme": Config.theme,
            "particle_trail": Config.particle_trail,
            "do_particles_on_bounce": Config.do_particles_on_bounce,
            "performance_hud": Config.performance_hud,
            "theatre_mode": Config.theatre_mode,
            "camera_mode": Config.camera_mode,
        }
        Config.theme = "dark"
        Config.particle_trail = False
        Config.do_particles_on_bounce = False
        Config.performance_hud = False
        Config.theatre_mode = True
        Config.camera_mode = CameraFollow.TargetLead.value
        bounces = [
            ([400.0, 300.0], [-1, 1], 1.0, 0),
            ([500.0, 400.0], [-1, -1], 2.0, 1),
            ([300.0, 200.0], [1, -1], 3.0, 0),
        ]
        prepared = PreparedMap(
            bounces=bounces,
            unhit_notes=[1.0, 2.0, 3.0],
            start_pos=[300.0, 200.0],
            start_dir=[1, 1],
            chunks=[MapChunk(0, 15, bounces)],
        )
        game = Game()
        game.active = True
        game.music_has_played = True
        game.play_delay_ms = 0
        screen = pygame.display.get_surface()
        try:
            game._apply_prepared_map(prepared)
            game.audio_clock.position = lambda: 1.5
            screen.fill(Config.color_themes["dark"]["background"])

            game.draw(screen, 0)

            self.assertGreater(game.visible_peg_count, 0)
            self.assertEqual(game._next_peg_world_position(1.5), game.world.rectangles[1].center)
        finally:
            game.shutdown()
            for name, value in previous.items():
                setattr(Config, name, value)

    def test_particle_motion_is_nearly_frame_rate_independent(self):
        previous_dt = Config.dt
        try:
            random.seed(123)
            sixty_fps = Particle([0.0, 0.0], [1.0, 0.0], lifetime=1.0)
            Config.dt = 1 / 60
            for _ in range(30):
                sixty_fps.age()

            random.seed(123)
            one_twenty_fps = Particle([0.0, 0.0], [1.0, 0.0], lifetime=1.0)
            Config.dt = 1 / 120
            for _ in range(60):
                one_twenty_fps.age()

            self.assertAlmostEqual(sixty_fps.x, one_twenty_fps.x, delta=4.0)
            self.assertAlmostEqual(sixty_fps.y, one_twenty_fps.y, delta=4.0)
            self.assertAlmostEqual(sixty_fps.size, one_twenty_fps.size, delta=0.1)
        finally:
            Config.dt = previous_dt

    def test_bounce_particles_respect_global_budget(self):
        previous_max = Config.particle_max_active
        previous_amount = Config.particle_amount
        Config.particle_max_active = 5
        Config.particle_amount = 20
        game = Game()
        try:
            game.world.add_bounce_particles([100.0, 100.0], [1, 0], 0.0)
            game.world.add_bounce_particles([100.0, 100.0], [1, 0], 0.0)
            self.assertEqual(len(game.world.particles), 5)
        finally:
            game.shutdown()
            Config.particle_max_active = previous_max
            Config.particle_amount = previous_amount

    def test_square_afterimages_are_time_based_and_bounded(self):
        previous = {
            "particle_trail": Config.particle_trail,
            "square_afterimage_count": Config.square_afterimage_count,
            "square_afterimage_rate": Config.square_afterimage_rate,
            "square_afterimage_seconds": Config.square_afterimage_seconds,
            "dt": Config.dt,
        }
        Config.particle_trail = True
        Config.square_afterimage_count = 4
        Config.square_afterimage_rate = 20
        Config.square_afterimage_seconds = 0.28
        Config.dt = 0.05
        screen = pygame.display.get_surface()
        game = Game()
        try:
            for frame in range(10):
                game.world.square.x += 10
                game._draw_square_afterimages(screen, frame * 0.05)
            self.assertLessEqual(len(game.square_afterimages), 4)
            self.assertGreater(len(game.square_afterimages), 0)
        finally:
            game.shutdown()
            for name, value in previous.items():
                setattr(Config, name, value)

    def test_overlapping_future_pegs_never_hide_immediate_target(self):
        previous_min = Config.peg_visible_min
        previous_max = Config.peg_visible_max
        previous_padding = Config.peg_overlap_padding
        Config.peg_visible_min = 3
        Config.peg_visible_max = 3
        Config.peg_overlap_padding = 8
        game = Game()
        try:
            game.world.rectangles = [
                pygame.Rect(100, 100, 10, 20),
                pygame.Rect(105, 100, 10, 20),
                pygame.Rect(300, 100, 10, 20),
                pygame.Rect(500, 100, 10, 20),
            ]
            game.world.collision_times = [1.0, 1.1, 1.2, 1.3]
            game.world.colors = [(255, 255, 255)] * 4
            game.safe_areas = [rect.copy() for rect in game.world.rectangles]
            game.safe_area_times = game.world.collision_times.copy()

            geometry, _, next_peg, _ = game._visible_map_records(0.0)

            self.assertEqual(next_peg, 0)
            self.assertIn(0, geometry)
            self.assertNotIn(1, geometry)
            self.assertEqual(geometry, {0, 2, 3})
        finally:
            game.shutdown()
            Config.peg_visible_min = previous_min
            Config.peg_visible_max = previous_max
            Config.peg_overlap_padding = previous_padding

    def test_only_two_recent_past_pegs_remain_visible(self):
        previous_past_max = Config.peg_visible_past_max
        previous_past_fade = Config.peg_past_fade_seconds
        previous_padding = Config.peg_overlap_padding
        Config.peg_visible_past_max = 2
        Config.peg_past_fade_seconds = 0.6
        Config.peg_overlap_padding = 0
        game = Game()
        try:
            game.world.rectangles = [pygame.Rect(index * 100, 0, 10, 20) for index in range(4)]
            game.world.collision_times = [9.5, 9.7, 9.9, 10.2]
            game.world.colors = [(255, 255, 255)] * 4
            game.safe_areas = [rect.copy() for rect in game.world.rectangles]
            game.safe_area_times = game.world.collision_times.copy()

            geometry, _, next_peg, _ = game._visible_map_records(10.0)

            self.assertEqual(next_peg, 3)
            self.assertEqual(geometry, {1, 2, 3})
        finally:
            game.shutdown()
            Config.peg_visible_past_max = previous_past_max
            Config.peg_past_fade_seconds = previous_past_fade
            Config.peg_overlap_padding = previous_padding

    def test_game_starts_playlist_and_schedules_next_track(self):
        previous_max_notes = Config.max_notes
        original_update_screen = game_module.update_screen
        Config.max_notes = 16
        game_module.update_screen = lambda *args, **kwargs: None
        screen = pygame.display.get_surface()
        Config.screen = screen
        game = Game()
        game.active = True
        try:
            songs = [
                make_song_from_zip("songs/bad-piggies.zip"),
                make_song_from_zip("songs/tetris.zip"),
            ]
            result = game.start_playlist(songs, 0, screen)
            self.assertIsNone(result)
            prepared = game.playlist.map_future.result(timeout=30)
            audio = game.playlist.audio_future.result(timeout=30)
            self.assertGreater(game.world.total_bounces, 0)
            self.assertGreater(len(prepared.bounces), 0)
            self.assertGreater(len(audio[0]), 0)
        finally:
            game.shutdown()
            game_module.update_screen = original_update_screen
            Config.max_notes = previous_max_notes

    def test_game_promotes_prepared_track(self):
        previous_max_notes = Config.max_notes
        original_update_screen = game_module.update_screen
        Config.max_notes = 16
        game_module.update_screen = lambda *args, **kwargs: None
        screen = pygame.display.get_surface()
        Config.screen = screen
        game = Game()
        game.active = True
        try:
            songs = [
                make_song_from_zip("songs/bad-piggies.zip"),
                make_song_from_zip("songs/tetris.zip"),
            ]
            self.assertIsNone(game.start_playlist(songs, 0, screen))
            game.playlist.map_future.result(timeout=30)
            game.playlist.audio_future.result(timeout=30)
            pygame.mixer.music.stop()
            game.music_has_played = True
            game.transition_pending = True
            game.transition_started_at = 0.0

            game._update_playlist_transition()

            self.assertEqual(game.playlist.current_index, 1)
            self.assertEqual(Config.current_song.name, songs[1].name)
            self.assertGreater(game.world.total_bounces, 0)
        finally:
            game.shutdown()
            game_module.update_screen = original_update_screen
            Config.max_notes = previous_max_notes

    def test_live_physics_change_regenerates_only_future_map(self):
        previous_max_notes = Config.max_notes
        previous_speed = Config.square_speed
        original_update_screen = game_module.update_screen
        Config.max_notes = 32
        game_module.update_screen = lambda *args, **kwargs: None
        screen = pygame.display.get_surface()
        Config.screen = screen
        game = Game()
        game.active = True
        try:
            songs = [
                make_song_from_zip("songs/bad-piggies.zip"),
                make_song_from_zip("songs/tetris.zip"),
            ]
            self.assertIsNone(game.start_playlist(songs, 0, screen))
            game.world.time = 1.0
            game.play_delay_ms = 0
            Config.square_speed += 100

            game.regenerate_future_map()

            self.assertGreater(len(game.world.future_bounces), 0)
            self.assertEqual(
                game.world.total_bounces,
                game.world.completed_bounces + len(game.world.future_bounces),
            )
        finally:
            game.shutdown()
            game_module.update_screen = original_update_screen
            Config.max_notes = previous_max_notes
            Config.square_speed = previous_speed

    def test_live_overlay_opens_and_renders_during_gameplay(self):
        game = Game()
        overlay = LiveConfigOverlay()
        game.active = True
        screen = pygame.display.get_surface()
        try:
            consumed = overlay.handle_event(
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F10),
                game,
            )
            self.assertTrue(consumed)
            self.assertTrue(overlay.active)
            overlay.draw(screen, game_active=True)
            overlay.selected = len(overlay.OPTIONS) - 1
            overlay.draw(screen, game_active=True)
        finally:
            game.shutdown()

    def test_live_overlay_changes_peg_readability_settings(self):
        previous_max = Config.peg_visible_max
        previous_spacing = Config.peg_overlap_padding
        previous_guide = Config.peg_guide_line
        game = Game()
        try:
            LiveConfigOverlay._adjust("peg_visible_max", -1, game)
            LiveConfigOverlay._adjust("peg_overlap_padding", 1, game)
            LiveConfigOverlay._adjust("peg_guide_line", 1, game)

            self.assertEqual(Config.peg_visible_max, max(Config.peg_visible_min, previous_max - 1))
            self.assertEqual(Config.peg_overlap_padding, min(24, previous_spacing + 2))
            self.assertEqual(Config.peg_guide_line, not previous_guide)
        finally:
            game.shutdown()
            Config.peg_visible_max = previous_max
            Config.peg_overlap_padding = previous_spacing
            Config.peg_guide_line = previous_guide


if __name__ == "__main__":
    unittest.main()
