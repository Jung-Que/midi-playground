import os
from io import BytesIO
from time import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import game as game_module
from config import Config
from game import Game
from liveconfig import LiveConfigOverlay
from songselector import make_song_from_zip
from spatial import SpatialHash
from streaming import (
    MapChunk,
    PlaylistController,
    PreparedMap,
    map_settings_snapshot,
    prepare_song_map,
    read_song_audio,
    song_to_spec,
)


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

    def test_prunes_only_old_offscreen_geometry(self):
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
            for slot in controller.slots:
                slot.map_future.result(timeout=30)
                slot.audio_future.result(timeout=30)

            self.assertEqual([slot.index for slot in controller.slots], [1, 2])
            self.assertEqual(controller.ready_count(), 2)
            first, second = controller.slots
            self.assertEqual(second.map_future.result().start_pos, first.map_future.result().end_state[0])
            self.assertEqual(second.map_future.result().start_dir, first.map_future.result().end_state[1])
        finally:
            controller.shutdown()
            Config.max_notes = previous_max_notes
            Config.playlist_prefetch_count = previous_prefetch

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
            game._apply_prepared_map(prepared)
            for map_time in range(0, 3601, 60):
                game._refresh_map_window(float(map_time), force=True)
                self.assertLessEqual(game.active_map_chunk_count, 4)
                self.assertLessEqual(len(game.world.rectangles), 60)
            self.assertLessEqual(len(game.map_chunks), 2)
        finally:
            game.shutdown()
            Config.map_chunk_seconds = previous_chunk
            Config.map_preload_seconds = previous_preload
            Config.map_retention_seconds = previous_retention

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
        finally:
            game.shutdown()


if __name__ == "__main__":
    unittest.main()
