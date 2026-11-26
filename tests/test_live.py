import os
import random
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from live import (
    CommandWindow, LiveQueue, LiveSession, PLACEHOLDER_SETTINGS, ThemeVote, categories_from_metadata,
    match_choice, songs_in_category, apply_cli_overrides,
)
from streaming import PlaylistController


class FakeSong:
    def __init__(self, name, categories=()):
        self.name = name
        self.categories = list(categories)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def settings(**overrides):
    data = dict(PLACEHOLDER_SETTINGS)
    data.update(queue_size=3, window_every_seconds=60, window_open_seconds=20, recent_song_memory=2)
    data.update(overrides)
    return data


SONGS = [FakeSong("Rush E", ["meme"]), FakeSong("Karakuri Pierrot", ["vocaloid"]),
         FakeSong("Looping the Rooms", ["game", "chill"]), FakeSong("Trick Heart", ["vocaloid", "game"]),
         FakeSong("Six Trillion Years", ["vocaloid"])]


class CategoryTests(unittest.TestCase):
    def test_metadata_category_forms(self):
        self.assertEqual(categories_from_metadata({"category": "Game"}), ["game"])
        self.assertEqual(categories_from_metadata({"category": "a, b"}), ["a", "b"])
        self.assertEqual(categories_from_metadata({"categories": ["A", "b", "a"], "category": "c"}), ["c", "a", "b"])
        self.assertEqual(categories_from_metadata({}), [])

    def test_song_in_several_categories(self):
        names = lambda songs: [s.name for s in songs]
        self.assertEqual(names(songs_in_category(SONGS, "game")), ["Looping the Rooms", "Trick Heart"])
        self.assertIn("Trick Heart", names(songs_in_category(SONGS, "vocaloid")))
        self.assertEqual(len(songs_in_category(SONGS, "")), 5)
        self.assertEqual(len(songs_in_category(SONGS, "meme,chill")), 2)

    def test_cli_overrides(self):
        out = apply_cli_overrides(settings(), ["main.py", "--live", "--category", "game", "--chat", "file"])
        self.assertEqual((out["category"], out["chat_source"]), ("game", "file"))


class MatchTests(unittest.TestCase):
    def test_match(self):
        names = [s.name for s in SONGS]
        self.assertEqual(match_choice("rush e", names), 0)
        self.assertEqual(match_choice("karakuri", names), 1)
        self.assertEqual(match_choice("3", names), 2)
        self.assertEqual(match_choice("trik hart", names), 3)  # fuzzy
        self.assertIsNone(match_choice("zzzz", names))
        self.assertIsNone(match_choice("9", names))


class QueueTests(unittest.TestCase):
    def test_requests_fill_empty_slots_then_auto_fill(self):
        q = LiveQueue(SONGS, 3, rng=random.Random(1))
        self.assertEqual(q.request("a", "rush"), 0)
        self.assertEqual(q.request("b", "trick"), 3)
        q.close_window()
        self.assertEqual([e.index for e in q.entries][:2], [0, 3])
        self.assertEqual([e.requested_by for e in q.entries], ["a", "b", ""])
        self.assertEqual(len({e.index for e in q.entries}), 3)

    def test_full_queue_takes_no_more_requests(self):
        q = LiveQueue(SONGS, 2, rng=random.Random(2))
        q.close_window()  # auto-filled
        q.request("a", "six")
        q.close_window()
        self.assertEqual(len(q.entries), 2)
        self.assertNotIn("a", [e.requested_by for e in q.entries])
        q.pop_next()  # one song played -> one free slot
        q.request("a", "looping")
        q.close_window()
        self.assertEqual(len(q.entries), 2)

    def test_one_request_per_viewer_and_no_duplicates(self):
        q = LiveQueue(SONGS, 3, rng=random.Random(3))
        q.request("a", "rush")
        q.request("a", "karakuri")  # latest counts
        self.assertIsNone(q.request("b", "karakuri"))  # already requested
        q.locked = [4]
        self.assertIsNone(q.request("c", "six"))  # playing / next
        self.assertEqual([(e.index, e.requested_by) for e in q.pending], [(1, "a")])

    def test_auto_pick_avoids_recent(self):
        q = LiveQueue(SONGS, 3, recent_memory=3, rng=random.Random(4))
        played = [q.pop_next() for _ in range(40)]
        for i in range(3, len(played)):
            self.assertNotIn(played[i], played[i - 3:i])

    def test_single_song_category_loops(self):
        q = LiveQueue([SONGS[0]], 3, rng=random.Random(5))
        self.assertEqual([q.pop_next() for _ in range(3)], [0, 0, 0])


class WindowAndSessionTests(unittest.TestCase):
    def test_window_cycle(self):
        w = CommandWindow(60, 20, started_at=0)
        self.assertEqual(w.state(5), (True, 15))
        self.assertEqual(w.state(30), (False, 30))
        self.assertTrue(w.state(61)[0])

    def test_session_commands_only_while_open_and_theme_vote(self):
        clock = FakeClock()
        applied = []
        session = LiveSession(settings(), SONGS, applied.append, clock=clock, themes=["dark", "light", "autumn"])
        self.assertTrue(session.handle_message("a", "!color light"))
        self.assertTrue(session.handle_message("b", "!COLOR autumn"))
        self.assertTrue(session.handle_message("c", "!color light"))
        self.assertTrue(session.handle_message("d", "!song rush e"))
        self.assertFalse(session.handle_message("e", "hello"))
        clock.now += 25  # window closed
        session.update()
        self.assertEqual(applied, ["light"])
        self.assertEqual(session.queue.entries[0].index, 0)
        self.assertEqual(len(session.queue.entries), 3)
        self.assertFalse(session.handle_message("f", "!color dark"))  # closed: ignored
        clock.now += 40  # next window opens
        session.update()
        self.assertTrue(session.handle_message("f", "!color dark"))

    def test_theme_tie_goes_to_first_voted(self):
        vote = ThemeVote(["dark", "light"])
        vote.vote("a", "light")
        vote.vote("b", "dark")
        self.assertEqual(vote.close(), "light")


class HelpTests(unittest.TestCase):
    def test_help_lists_split_under_200_chars(self):
        from live import build_help_messages
        names = [f"Song number {i} with a fairly long title" for i in range(20)]
        messages = build_help_messages(["!song <title>", "Songs: {songs}", "Themes: {themes}"], names, ["dark", "light"])
        self.assertEqual(messages[0], "!song <title>")
        self.assertTrue(all(len(m) <= 200 for m in messages))
        self.assertTrue(all(m.startswith("Songs: ") for m in messages[1:-1]))
        self.assertGreater(len(messages), 3)
        self.assertEqual(messages[-1], "Themes: dark, light")
        joined = " ".join(messages)
        self.assertIn("1. Song number 0", joined)
        self.assertIn("20. Song number 19", joined)

    def test_help_answers_outside_window_with_cooldown(self):
        clock = FakeClock()
        session = LiveSession(settings(), SONGS, lambda t: None, clock=clock, themes=["dark"])
        clock.now += 30  # window closed
        self.assertTrue(session.handle_message("a", "!help"))
        self.assertFalse(session.handle_message("b", "!help"))  # cooldown
        clock.now += 31
        self.assertTrue(session.handle_message("b", "!HELP"))
        self.assertEqual(len(session.outbox), 2 * len(session.help_messages()))


class LibraryTests(unittest.TestCase):
    def test_watcher_picks_up_added_rebuilt_and_removed_packs(self):
        import json, tempfile, zipfile
        from pathlib import Path
        from live import LibraryWatcher

        def write(path, name, cats):
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("metadata.json", json.dumps({"name": name, "categories": cats}))

        def load(path):
            with zipfile.ZipFile(path) as z:
                meta = json.loads(z.read("metadata.json"))
            song = FakeSong(meta["name"], categories_from_metadata(meta))
            song.fp = path
            return song

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root / "a.zip", "A", ["marimba"])
            write(root / "b.zip", "B", ["other"])
            (root / "c.zip").write_bytes(b"half copied")
            watcher = LibraryWatcher([root], "marimba", load)
            found = watcher.scan()
            self.assertEqual([s.name for s in found.values()], ["A"])
            session = LiveSession(settings(), list(found.values()), lambda t: None, themes=["dark"])

            write(root / "c.zip", "C", ["marimba"])  # copy finished
            write(root / "b.zip", "B", ["marimba", "other"])  # moved into the category
            added, replaced = session.refresh_library(watcher.scan())
            self.assertEqual(sorted(s.name for _, s in added), ["B", "C"])
            self.assertEqual([s.name for s in session.songs][0], "A")

            (root / "a.zip").unlink()
            session.refresh_library(watcher.scan())
            self.assertEqual(session.queue.disabled, {0})
            self.assertNotEqual(session.queue.pop_next(), 0)
            self.assertIsNone(session.queue.request("x", "A"))
            self.assertNotIn("1. A", " ".join(session.help_messages()))

            write(root / "a.zip", "A2", ["marimba"])  # back, rebuilt
            added, replaced = session.refresh_library(watcher.scan())
            self.assertEqual((added, [s.name for _, s in replaced]), ([], ["A2"]))
            self.assertEqual(session.queue.disabled, set())


class ProviderTests(unittest.TestCase):
    def test_playlist_uses_provider_and_reuses_on_rebuild(self):
        pygame.init()
        controller = PlaylistController()
        picks = iter([2, 4, 1, 3])
        controller.songs = ["a", "b", "c", "d", "e"]
        controller.next_index_provider = lambda base: next(picks)
        self.assertEqual(controller._candidate_indices(0, 2), [2, 4])
        self.assertEqual(controller._candidate_indices(0, 2, reuse=(2,)), [2, 1])
        controller.next_index_provider = None
        self.assertEqual(controller._candidate_indices(0, 2), [1, 2])
        controller.shutdown()


if __name__ == "__main__":
    unittest.main()
