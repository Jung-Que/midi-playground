import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import game as game_module
from config import Config
from game import Game
from liveconfig import LiveConfigOverlay
from songselector import make_song_from_zip
from streaming import PlaylistController, map_settings_snapshot, prepare_song_map, song_to_spec


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
