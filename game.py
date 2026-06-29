from utils import *
import pygame
from world import World
import random
from camera import Camera
from keystrokes import Keystrokes
from particle import Particle
from bounce import Bounce
from square import Square
from collections import deque
from dataclasses import dataclass, field
from io import BytesIO
import tracemalloc
from audioclock import AudioClock
from streaming import (
    PlaylistController,
    PreparedMap,
    map_settings_snapshot,
    prepare_notes_map,
    prepare_song_map,
    read_song_audio,
    song_to_spec,
)


TRACK_END_EVENT = pygame.USEREVENT + 17


@dataclass
class RuntimeMapChunk:
    start_time: float
    end_time: float
    rectangles: list[pygame.Rect]
    collision_times: list[float]
    colors: list
    safe_areas: list[pygame.Rect]
    safe_area_times: list[float]
    bounce_data: list[tuple[list[float], list[int], float, int]] = field(default_factory=list)
    start_position: list[float] = field(default_factory=list)
    materialized: bool = True


class Game:
    def __init__(self):
        self.active = False
        self.notes = []
        self.camera = Camera()
        self.world = World()
        self.safe_areas: list[pygame.Rect] = []
        self.safe_area_times: list[float] = []
        self.camera_ctrl_text = get_font(24).render("Manual Camera Control Activated", True, (0, 255, 0))
        self.music_has_played = False
        self.offset_happened = False
        self.loading_text = get_font(24).render("Loading...", True, (255, 255, 255))
        self.keystrokes = Keystrokes()
        self.misses = 0
        self.mouse_down = False
        self.hitted_rectangles = []
        self.play_delay_ms = Config.start_playing_delay
        self.playlist = PlaylistController()
        self.current_audio_buffer = None
        self.queued_audio_buffer = None
        self.unqueued_audio = None
        self.next_audio_queued = False
        self.transition_pending = False
        self.transition_started_at = 0.0
        self.transition_wait_started_at = 0.0
        self.transition_lag_ms = 0.0
        self.auto_advance = False
        self.stream_message = ""
        self.map_chunks: deque[RuntimeMapChunk] = deque()
        self._active_chunk_signature = None
        self.active_map_chunk_count = 0
        self.active_map_stream = None
        self.consecutive_skips = 0
        self.fps_smoothed = 0.0
        self.audio_clock = AudioClock(pygame.mixer.music.get_pos)

    def start_playlist(self, songs: list, selected_index: int, screen: pygame.Surface):
        self.stop_playlist(recreate_controller=True)
        self.consecutive_skips = 0
        self.playlist.configure(songs, selected_index)
        Config.current_song = songs[selected_index]
        try:
            prepared = self.playlist.prepare_current([0.0, 0.0], [1, 1])
            timed_audio = self.playlist.active_slot.audio_future.result(timeout=30)
            initial_audio = (timed_audio.data, timed_audio.extension)
        except Exception as exc:
            return f"Unable to prepare initial stream: {exc}"
        result = self.start_song(screen, prepared=prepared, audio_override=initial_audio)
        if result:
            return result
        self.playlist.release_active_audio()
        self.auto_advance = len(songs) > 1
        pygame.mixer.music.set_endevent(TRACK_END_EVENT)
        self._prepare_next_song()

    def start_song(
            self,
            screen: pygame.Surface,
            prepared: PreparedMap = None,
            seamless: bool = False,
            audio_already_playing: bool = False,
            track_started_at: float = None,
            audio_override: tuple[bytes, str] = None,
    ):
        random.seed(Config.seed)
        self.world = World()
        self.notes = []
        self.safe_areas = []
        self.safe_area_times = []
        self.map_chunks = deque()
        self._active_chunk_signature = None
        self.active_map_stream = prepared.stream_slot if prepared is not None else None
        self.music_has_played = audio_already_playing
        self.offset_happened = seamless
        self.play_delay_ms = 0 if seamless else Config.start_playing_delay
        self.misses = 0
        self.mouse_down = False
        self.keystrokes = Keystrokes()
        self.stream_message = ""

        if not seamless:
            self.camera = Camera()
        self.camera.lock_type = CameraFollow(Config.camera_mode)
        self.camera.locked_on_square = True

        if prepared is None:
            try:
                prepared = prepare_song_map(
                    song_to_spec(Config.current_song),
                    map_settings_snapshot(),
                    [0.0, 0.0],
                    [1, 1],
                )
            except Exception as exc:
                return f"Unable to generate map: {exc}"

        self._apply_prepared_map(prepared)
        self.notes = prepared.unhit_notes.copy()
        start_pos = prepared.start_pos.copy()
        start_dir = prepared.start_dir.copy()
        if prepared.used_fallback:
            self.stream_message = prepared.warning or "Least-collision map fallback active"

        if audio_already_playing:
            self.world.start_time = track_started_at or get_current_time()
            self.audio_clock.start_now(self.world.start_time)
            self.world.square.pos = start_pos.copy()
            self.world.square.dir = start_dir.copy()
        else:
            try:
                if audio_override is None:
                    audio_data, extension = read_song_audio(song_to_spec(Config.current_song))
                else:
                    audio_data, extension = audio_override
                self._load_music(audio_data, extension)
            except Exception as exc:
                return f"Unable to load audio: {exc}"
            self.world.start_time = get_current_time()
            self.world.square.dir = [0, 0]
            self.world.square.pos = self.world.future_bounces[0].square_pos.copy()

    def _apply_prepared_map(self, prepared: PreparedMap):
        self.world.square = Square(*prepared.start_pos, *prepared.start_dir)
        bounces = [Bounce(pos, direction, timestamp, axis) for pos, direction, timestamp, axis in prepared.bounces]
        self.world.future_bounces = deque(bounces)
        self.world.total_bounces = len(bounces)
        self.world.scorekeeper.unhit_notes = prepared.unhit_notes.copy()
        self.active_map_stream = prepared.stream_slot
        self.world.map_stream_complete = prepared.complete
        self.map_chunks = self._build_runtime_chunks(prepared)
        self._active_chunk_signature = None
        self._refresh_map_window(0.0, force=True)

    @staticmethod
    def _square_rect(position: list[float]) -> pygame.Rect:
        return pygame.Rect(
            position[0] - Config.SQUARE_SIZE / 2,
            position[1] - Config.SQUARE_SIZE / 2,
            Config.SQUARE_SIZE,
            Config.SQUARE_SIZE,
        )

    def _build_runtime_chunks(self, prepared: PreparedMap) -> deque[RuntimeMapChunk]:
        runtime_chunks = deque()
        previous_position = prepared.start_pos.copy()
        source_chunks = prepared.chunks
        if not source_chunks and prepared.bounces:
            source_chunks = [type("MapChunkFallback", (), {
                "start_time": prepared.bounces[0][2],
                "end_time": prepared.bounces[-1][2],
                "bounces": prepared.bounces,
            })()]

        for chunk in source_chunks:
            chunk_start_position = chunk.start_pos or previous_position
            runtime_chunks.append(RuntimeMapChunk(
                start_time=chunk.start_time,
                end_time=chunk.end_time,
                rectangles=[],
                collision_times=[],
                colors=[],
                safe_areas=[],
                safe_area_times=[],
                bounce_data=list(chunk.bounces),
                start_position=chunk_start_position.copy(),
                materialized=False,
            ))
            if chunk.bounces:
                previous_position = chunk.bounces[-1][0].copy()
        return runtime_chunks

    def _materialize_chunk(self, chunk: RuntimeMapChunk):
        if chunk.materialized:
            return
        palette = [
            (224, 50, 50), (80, 210, 100), (230, 220, 50),
            (174, 170, 210), (245, 77, 247), (255, 153, 0),
        ]
        previous = self._square_rect(chunk.start_position)
        for position, direction, timestamp, axis in chunk.bounce_data:
            bounce = Bounce(position, direction, timestamp, axis)
            target = self._square_rect(position)
            chunk.rectangles.append(bounce.get_collision_rect())
            chunk.collision_times.append(timestamp)
            chunk.colors.append(random.choice(palette))
            chunk.safe_areas.append(previous.union(target))
            chunk.safe_area_times.append(timestamp)
            previous = target
        chunk.bounce_data = []
        chunk.materialized = True

    def _refresh_map_window(self, map_time: float, force: bool = False):
        cutoff = map_time - float(Config.map_retention_seconds)
        horizon = map_time + float(Config.map_preload_seconds)
        while self.map_chunks and self.map_chunks[0].end_time < cutoff:
            self.map_chunks.popleft()

        active = [
            chunk for chunk in self.map_chunks
            if chunk.end_time >= cutoff and chunk.start_time <= horizon
        ]
        for chunk in active:
            self._materialize_chunk(chunk)
        signature = tuple((chunk.start_time, chunk.end_time, len(chunk.rectangles)) for chunk in active)
        if not force and signature == self._active_chunk_signature:
            return
        self._active_chunk_signature = signature
        self.active_map_chunk_count = len(active)
        self.world.rectangles = [rect for chunk in active for rect in chunk.rectangles]
        self.world.collision_times = [timestamp for chunk in active for timestamp in chunk.collision_times]
        self.world.colors = [color for chunk in active for color in chunk.colors]
        self.safe_areas = [rect for chunk in active for rect in chunk.safe_areas]
        self.safe_area_times = [timestamp for chunk in active for timestamp in chunk.safe_area_times]
        self.world.rebuild_spatial_indexes(self.safe_areas)

    def _ingest_stream_chunks(self, map_time: float):
        if self.active_map_stream is None:
            return
        if self.active_map_stream.error:
            error = self.active_map_stream.error
            start_pos = self.world.square.pos.copy()
            start_dir = self.world.square.dir.copy()
            if start_dir == [0, 0]:
                start_dir = [1, 1]
            self.active_map_stream = None
            self.world.map_stream_complete = True
            if self.auto_advance:
                self.playlist.prepare_ahead(start_pos, start_dir)
            self.stream_message = f"Current map stream stopped safely: {error}"
            return
        horizon = map_time + float(Config.map_preload_seconds)
        chunks = self.playlist.claim_chunks_until(self.active_map_stream, horizon)
        if chunks:
            for chunk in chunks:
                new_bounces = [
                    Bounce(position, direction, timestamp, axis)
                    for position, direction, timestamp, axis in chunk.bounces
                ]
                self.world.future_bounces.extend(new_bounces)
                self.world.total_bounces += len(new_bounces)
                chunk_map = PreparedMap(
                    bounces=list(chunk.bounces),
                    unhit_notes=[],
                    start_pos=(chunk.start_pos or self.world.square.pos).copy(),
                    start_dir=(chunk.start_dir or self.world.square.dir).copy(),
                    chunks=[chunk],
                )
                self.map_chunks.extend(self._build_runtime_chunks(chunk_map))
            self._active_chunk_signature = None
            self._refresh_map_window(map_time, force=True)

        if self.playlist.stream_drained(self.active_map_stream):
            self.world.map_stream_complete = True

    def _build_safe_areas(self, start_pos: list[float], time_offset: float = 0.0):
        previous = pygame.Rect(
            start_pos[0] - Config.SQUARE_SIZE / 2,
            start_pos[1] - Config.SQUARE_SIZE / 2,
            Config.SQUARE_SIZE,
            Config.SQUARE_SIZE,
        )
        for bounce in self.world.future_bounces:
            target = pygame.Rect(
                bounce.square_pos[0] - Config.SQUARE_SIZE / 2,
                bounce.square_pos[1] - Config.SQUARE_SIZE / 2,
                Config.SQUARE_SIZE,
                Config.SQUARE_SIZE,
            )
            self.safe_areas.append(previous.union(target))
            self.safe_area_times.append(bounce.time - time_offset)
            previous = target

    def _load_music(self, audio_data: bytes, extension: str):
        self.current_audio_buffer = BytesIO(audio_data)
        try:
            pygame.mixer.music.load(self.current_audio_buffer, namehint=extension)
        except TypeError:
            pygame.mixer.music.load(self.current_audio_buffer)
        pygame.mixer.music.set_volume(Config.volume / 100)

    def _end_state(self) -> tuple[list[float], list[int]]:
        if self.world.future_bounces:
            final = self.world.future_bounces[-1]
            return final.square_pos.copy(), final.square_dir.copy()
        return self.world.square.pos.copy(), self.world.square.dir.copy()

    def _prepare_next_song(self):
        if not self.auto_advance:
            return
        start_pos, start_dir = self._end_state()
        if self.playlist.slots:
            self.playlist.ensure_prefetch(start_pos, start_dir)
        else:
            self.playlist.prepare_ahead(start_pos, start_dir)
        self.next_audio_queued = False
        self.queued_audio_buffer = None
        self.unqueued_audio = None

    def regenerate_future_map(self):
        """Apply map-affecting live settings from the next bounce onward."""
        map_time = self.world.time - self.play_delay_ms / 1000 + Config.music_offset / 1000
        remaining_notes = [
            timestamp for timestamp in self.world.scorekeeper.unhit_notes
            if timestamp > map_time + 0.05
        ]
        if not remaining_notes:
            return

        start_pos = self.world.square.pos.copy()
        start_dir = self.world.square.dir.copy()
        if start_dir == [0, 0] and self.world.future_bounces:
            start_dir = self.world.future_bounces[0].square_dir.copy()

        try:
            prepared = prepare_notes_map(
                remaining_notes,
                map_settings_snapshot(),
                start_pos,
                start_dir,
                start_time=map_time,
            )
        except Exception as exc:
            self.stream_message = f"Live map update failed: {exc}"
            return

        bounces = [Bounce(pos, direction, timestamp, axis) for pos, direction, timestamp, axis in prepared.bounces]
        schedule_offset = self.play_delay_ms / 1000
        for bounce in bounces:
            bounce.time += schedule_offset
        if schedule_offset:
            self.offset_happened = True
        past_geometry = [
            (rect, timestamp, color)
            for rect, timestamp, color in zip(
                self.world.rectangles, self.world.collision_times, self.world.colors
            )
            if timestamp <= map_time
        ]
        self.world.future_bounces = deque(bounces)
        self.world.map_stream_complete = True
        self.active_map_stream = None
        self.world.scorekeeper.unhit_notes = prepared.unhit_notes.copy()
        self.world.total_bounces = self.world.completed_bounces + len(bounces)

        past_safe_areas = [
            (rect, timestamp)
            for rect, timestamp in zip(self.safe_areas, self.safe_area_times)
            if timestamp <= map_time
        ]
        rebuilt_chunks = self._build_runtime_chunks(prepared)
        self.map_chunks = deque()
        if past_geometry or past_safe_areas:
            retained_times = [item[1] for item in past_geometry] + [item[1] for item in past_safe_areas]
            self.map_chunks.append(RuntimeMapChunk(
                start_time=min(retained_times, default=map_time),
                end_time=map_time,
                rectangles=[item[0] for item in past_geometry],
                collision_times=[item[1] for item in past_geometry],
                colors=[item[2] for item in past_geometry],
                safe_areas=[item[0] for item in past_safe_areas],
                safe_area_times=[item[1] for item in past_safe_areas],
            ))
        self.map_chunks.extend(rebuilt_chunks)
        self._active_chunk_signature = None
        self._refresh_map_window(map_time, force=True)

        end_pos, end_dir = self._end_state()
        self.playlist.refresh_next_map(end_pos, end_dir)
        self.stream_message = "Future map updated"

    def _queue_next_audio(self):
        if (
                not self.auto_advance or self.next_audio_queued or
                not self.music_has_played or self.transition_pending
        ):
            return
        error = self.playlist.first_error()
        if error:
            self._skip_failed_next(error)
            return
        # Never queue audio before its collision map is ready.
        if not self.playlist.first_ready():
            return
        audio = self.playlist.claim_audio()
        if audio is None:
            return
        audio_data, extension = audio
        self.queued_audio_buffer = BytesIO(audio_data)
        try:
            pygame.mixer.music.queue(self.queued_audio_buffer, namehint=extension)
            self.next_audio_queued = True
        except (TypeError, pygame.error):
            self.unqueued_audio = (audio_data, extension)

    def _skip_failed_next(self, reason: str, restart_map_worker: bool = False):
        start_pos, start_dir = self._end_state()
        skipped = self.playlist.skip_failed(
            start_pos,
            start_dir,
            restart_map_worker=restart_map_worker,
        )
        self.next_audio_queued = False
        self.queued_audio_buffer = None
        self.unqueued_audio = None
        self.consecutive_skips += 1
        if skipped is None or self.consecutive_skips >= len(self.playlist.songs):
            self.auto_advance = False
            self.transition_pending = False
            self.stream_message = f"Playlist stopped: no playable next track ({reason})"
            return
        self.transition_wait_started_at = get_current_time()
        name = getattr(skipped, "name", "track")
        self.stream_message = f"Skipped {name}: {reason}"

    def _update_playlist_transition(self):
        if self.auto_advance:
            end_pos, end_dir = self._end_state()
            self.playlist.ensure_prefetch(end_pos, end_dir)
        self._queue_next_audio()
        if not self.transition_pending:
            return

        error = self.playlist.first_error()
        if error:
            self._skip_failed_next(error)
            return

        now = get_current_time()
        if not self.transition_wait_started_at:
            self.transition_wait_started_at = now
        waited = now - self.transition_wait_started_at
        if not self.playlist.first_ready():
            if waited > float(Config.transition_wait_timeout_seconds):
                self._skip_failed_next("Preparation timed out", restart_map_worker=True)
            else:
                self.stream_message = f"Preparing next track... {waited:.1f}s"
            return

        audio = self.playlist.claim_audio()
        if audio is None:
            return
        audio_data, extension = audio

        queued_and_playing = self.next_audio_queued and pygame.mixer.music.get_busy()
        if queued_and_playing:
            track_started_at = self.audio_clock.start_from_transition(
                self.transition_started_at or now,
                Config.audio_clock_max_probe_ms,
                now=now,
            )
            self.current_audio_buffer = self.queued_audio_buffer
        else:
            self._load_music(audio_data, extension)
            pygame.mixer.music.play()
            track_started_at = self.audio_clock.start_now(get_current_time())

        transition = self.playlist.consume_prepared_map()
        if transition is None:
            if self.playlist.last_error:
                self.stream_message = self.playlist.last_error
            return

        _, song, prepared = transition
        Config.current_song = song
        self.transition_lag_ms = max(0.0, (get_current_time() - track_started_at) * 1000)
        result = self.start_song(
            Config.screen,
            prepared=prepared,
            seamless=True,
            audio_already_playing=True,
            track_started_at=track_started_at,
        )
        if result:
            self.stream_message = str(result)
            return
        self.playlist.release_active_audio()
        self.transition_pending = False
        self.transition_wait_started_at = 0.0
        self.consecutive_skips = 0
        self._prepare_next_song()

    def stop_playlist(self, recreate_controller: bool = False):
        self.auto_advance = False
        self.transition_pending = False
        self.transition_wait_started_at = 0.0
        pygame.mixer.music.set_endevent()
        if recreate_controller:
            self.playlist.shutdown()
            self.playlist = PlaylistController()

    def shutdown(self):
        self.stop_playlist()
        self.playlist.shutdown()

    def _prune_rolling_world(self, screen_rect: pygame.Rect):
        retention = float(Config.map_retention_seconds)
        map_time = self.world.time - self.play_delay_ms / 1000 + Config.music_offset / 1000
        if self.map_chunks:
            self._refresh_map_window(map_time)
            self.world.prune_past_bounces(retention)
            return

        cutoff = map_time - retention
        margin = max(float(Config.map_view_margin), 0.0)
        expanded_view = screen_rect.inflate(
            int(screen_rect.width * margin),
            int(screen_rect.height * margin),
        )

        kept_geometry = []
        for rect, collision_time, color in zip(
                self.world.rectangles, self.world.collision_times, self.world.colors
        ):
            visible_nearby = expanded_view.colliderect(self.camera.offset(rect))
            if collision_time >= cutoff or visible_nearby:
                kept_geometry.append((rect, collision_time, color))
        self.world.rectangles = [item[0] for item in kept_geometry]
        self.world.collision_times = [item[1] for item in kept_geometry]
        self.world.colors = [item[2] for item in kept_geometry]

        kept_safe_areas = []
        for rect, area_time in zip(self.safe_areas, self.safe_area_times):
            visible_nearby = expanded_view.colliderect(self.camera.offset(rect))
            if area_time >= cutoff or visible_nearby:
                kept_safe_areas.append((rect, area_time))
        self.safe_areas = [item[0] for item in kept_safe_areas]
        self.safe_area_times = [item[1] for item in kept_safe_areas]
        self.world.rebuild_spatial_indexes(self.safe_areas)
        self.world.prune_past_bounces(retention)

    def draw(self, screen: pygame.Surface, n_frames: int):

        if not self.active:
            return

        self._update_playlist_transition()

        if not self.music_has_played:
            if not self.offset_happened:
                for bnc_change in self.world.future_bounces:
                    bnc_change.time += self.play_delay_ms / 1000
            self.offset_happened = True
            if self.world.time-Config.current_song.music_offset/1000 > self.play_delay_ms/1000:
                self.music_has_played = True
                song_load_before = get_current_time()
                pygame.mixer.music.play()
                self.audio_clock.start_now(get_current_time())
                for bnc_change in self.world.future_bounces:
                    bnc_change.time += get_current_time()-song_load_before

        screen_rect = screen.get_rect()

        # set world time
        self.world.update_time()
        if self.music_has_played:
            self.world.time = self.audio_clock.position() + self.play_delay_ms / 1000
            self.transition_lag_ms = abs(self.audio_clock.drift_ms)
        map_time = self.world.time - self.play_delay_ms / 1000 + Config.music_offset / 1000
        self._ingest_stream_chunks(map_time)

        # move camera (only works if not locked on square)
        self.camera.attempt_movement()

        # handle square bounces
        self.world.handle_bouncing(self.world.square)

        # move square
        self.world.square.reg_move()

        # square in center of camera if locked
        if self.camera.locked_on_square:
            self.camera.follow(self.world.square)

        self._prune_rolling_world(screen_rect)
        world_view = screen_rect.move(int(self.camera.x), int(self.camera.y))

        # bounce anim
        sqrect = self.camera.offset(self.world.square.rect)
        if Config.bounce_effect and (self.world.time - 0.25) + Config.music_offset / 1000 < self.world.square.last_bounce_time:
            lerp = abs((self.world.time - 0.25 + Config.music_offset / 1000) - self.world.square.last_bounce_time) * 5
            lerp = lerp ** 2  # square it for better-looking interpolation
            if self.world.square.latest_bounce_direction:
                sqrect.inflate_ip((lerp * 5, -10 * lerp))
            else:
                sqrect.inflate_ip((-10 * lerp, lerp * 5))

        # safe areas
        total_rects = 0
        for safe_index in self.world.visible_safe_areas(world_view):
            safe_area = self.safe_areas[safe_index]
            offsetted = self.camera.offset(safe_area)
            if screen_rect.colliderect(offsetted):
                total_rects += 1
                pygame.draw.rect(screen, get_colors()["hallway"], offsetted)

        # draw pegs
        for i in self.world.visible_geometry(world_view):
            bounce_rect = self.world.rectangles[i]
            offsetted = self.camera.offset(bounce_rect)

            if offsetted.colliderect(screen_rect):
                total_rects += 1
                if Config.do_color_bounce_pegs and self.world.collision_times[i] < (self.world.time * 1000 + Config.music_offset - self.play_delay_ms)/1000:
                    pygame.draw.rect(screen, self.world.colors[i], offsetted)
                else:
                    pygame.draw.rect(screen, get_colors()["background"], offsetted)

        # particles
        particle_view = screen_rect.inflate(screen_rect.width, screen_rect.height)
        alive_particles = []
        for particle in self.world.particles:
            expired = particle.age()
            offsetted = self.camera.offset(particle.rect)
            if expired or not particle_view.colliderect(offsetted):
                continue
            alive_particles.append(particle)
            if screen_rect.colliderect(offsetted):
                pygame.draw.rect(screen, particle.color, offsetted)
        self.world.particles = alive_particles

        # particle trail in game
        if Config.particle_trail:
            # every 2 frames add a particle
            if not self.world.square.died:
                if n_frames % 2 == 0:
                    new = Particle(self.world.square.pos, [0, 0], True)
                    new.delta = [random.randint(-10, 10)/20, random.randint(-10, 10)/20]
                    self.world.particles.append(new)
                
        # scorekeeper drawing
        time_from_start = self.world.time-self.play_delay_ms/1000+Config.music_offset/1000
        if not Config.theatre_mode:
            self.misses = self.world.scorekeeper.draw(screen, time_from_start if len(self.world.future_bounces) else -1, self.misses)

            # hit icons
            to_remove = []
            for hiticon in self.world.scorekeeper.hit_icons:
                if hiticon.draw(screen, self.camera):
                    to_remove.append(hiticon)
            for remove in to_remove:
                self.world.scorekeeper.hit_icons.remove(remove)

            if self.world.scorekeeper.hp <= 0 and not self.world.square.died:
                self.world.square.died = True
                self.world.square.dir = [0, 0]
                self.auto_advance = False
                pygame.mixer.music.stop()
                play_sound("death.mp3", 0.5)
                self.world.future_bounces = []
                for _ in range(100):
                    self.world.particles.append(Particle(self.world.square.pos, [random.randint(-3, 3), random.randint(-3, 3)]))
            if self.world.square.died:
                self.world.scorekeeper.hp = 0

        # draw square
        self.world.square.draw(screen, sqrect)

        if not Config.theatre_mode:
            # keystrokes
            self.keystrokes.draw(screen)

            # countdown to start
            if time_from_start < 0:
                repr_time = f"{abs(int((time_from_start+0.065)*10)/10)}s"
                countdown_surface = get_font(36).render(repr_time, True, (255, 255, 255))
                screen.blit(countdown_surface, countdown_surface.get_rect(center=(Config.SCREEN_WIDTH / 2, Config.SCREEN_HEIGHT / 4)))
            elif time_from_start < 0.5:
                repr_zero = f"0.0s"
                countdown_surface = get_font(36).render(repr_zero, True, (255, 255, 255))
                countdown_surface.set_alpha((0.5-time_from_start)*2*255)
                screen.blit(countdown_surface, countdown_surface.get_rect(center=(Config.SCREEN_WIDTH / 2, Config.SCREEN_HEIGHT / 4)))

            # handle mouse clicks because self.handle_event doesn't get called for mouse clicks
            if pygame.mouse.get_pressed()[0] and not self.mouse_down:
                self.misses = self.world.handle_keypress(time_from_start, self.misses)
                self.mouse_down = True
            elif not pygame.mouse.get_pressed()[0]:
                self.mouse_down = False
            
            # draw accuracy
            # noinspection PyBroadException
            try:
                n_bounces = self.world.completed_bounces
                n_total_bounces = self.world.total_bounces
                if n_bounces > 0:
                    n_misses = self.misses
                    acc = round((n_bounces-n_misses)/n_bounces*100, 2)
                    acct = round((n_total_bounces-n_misses)/n_total_bounces*100, 2)
                    # clamp to 0-100
                    acc = max(0, min(100, acc))
                    acct = max(0, min(100, acct))
                    acc_text = get_font(24).render(f"Accuracy: {acc}%", True, (255, 255, 255))
                    acct_text = get_font(24).render(f"Total Accuracy: {acct}%", True, (255, 255, 255))
                    topleft1 = self.world.scorekeeper.life_bar_rect.move(0, 10).bottomleft
                    screen.blit(acc_text, acc_text.get_rect(topleft=topleft1))
                    screen.blit(acct_text, acct_text.get_rect(topleft=acc_text.get_rect(topleft=topleft1).move(0, 10).bottomleft))
            except ZeroDivisionError:
                pass
            except Exception:
                pass

            # failure message
            if self.world.square.died:
                # calculate accuracy
                n_bounces = self.world.completed_bounces
                n_total_bounces = self.world.total_bounces
                # clamp bounces to 1-infinity
                n_bounces = max(1, n_bounces)
                n_total_bounces = max(1, n_total_bounces)
                n_misses = self.misses

                acc = round((n_bounces-n_misses)/n_bounces*100, 2)
                acct = round((n_total_bounces-n_misses)/n_total_bounces*100, 2)

                # clamp to 0-100
                acc = max(0, min(100, acc))
                acct = max(0, min(100, acct))

                try_again_text = get_font(36).render("Press escape to go back.", True, (255, 255, 255))
                acc_text = get_font(36).render(f"Accuracy: {acc}%", True, (255, 255, 255))
                acct_text = get_font(36).render(f"Total Accuracy: {acct}%", True, (255, 255, 255))
                screen.blit(try_again_text, try_again_text.get_rect(center=(Config.SCREEN_WIDTH / 2, Config.SCREEN_HEIGHT / 4)))
                screen.blit(acc_text, acc_text.get_rect(center=(Config.SCREEN_WIDTH / 2, Config.SCREEN_HEIGHT / 4 + 50)))
                screen.blit(acct_text, acct_text.get_rect(center=(Config.SCREEN_WIDTH / 2, Config.SCREEN_HEIGHT / 4 + 100)))

        if not self.camera.locked_on_square:
            screen.blit(self.camera_ctrl_text, (10, 10))

        if self.stream_message:
            stream_surface = get_font(18).render(self.stream_message, True, (255, 190, 80))
            screen.blit(stream_surface, stream_surface.get_rect(midtop=(Config.SCREEN_WIDTH / 2, 12)))

        self._draw_performance_hud(screen)

    def _draw_performance_hud(self, screen: pygame.Surface):
        instant_fps = 1.0 / max(float(Config.dt), 0.0001)
        if self.fps_smoothed <= 0:
            self.fps_smoothed = instant_fps
        else:
            self.fps_smoothed = self.fps_smoothed * 0.9 + instant_fps * 0.1
        if not Config.performance_hud:
            return

        if not tracemalloc.is_tracing():
            tracemalloc.start()
        memory_bytes, _ = tracemalloc.get_traced_memory()
        metrics = self.playlist.metrics()
        wait_ms = 0.0
        if self.transition_pending and self.transition_wait_started_at:
            wait_ms = (get_current_time() - self.transition_wait_started_at) * 1000
        lines = (
            f"FPS {self.fps_smoothed:5.1f} | Python memory {memory_bytes / 1024 / 1024:5.1f} MB",
            f"Chunks {self.active_map_chunk_count}+{metrics['buffered_chunks']} buffered | Pegs {len(self.world.rectangles)} | Particles {len(self.world.particles)}",
            f"Prefetch {metrics['ready']}/{metrics['queued']} | Map {metrics['map_ms']:.0f} ms | Audio {metrics['audio_ms']:.0f} ms",
            f"Prepare {metrics['pending_ms']:.0f} ms | Wait {wait_ms:.0f} ms | Sync {self.transition_lag_ms:.0f} ms",
        )
        font = get_font(15)
        surfaces = [font.render(line, True, (220, 230, 238)) for line in lines]
        width = max(surface.get_width() for surface in surfaces) + 20
        height = sum(surface.get_height() for surface in surfaces) + 14
        panel = pygame.Surface((width, height), pygame.SRCALPHA)
        panel.fill((5, 8, 12, 185))
        y = 7
        for surface in surfaces:
            panel.blit(surface, (10, y))
            y += surface.get_height()
        screen.blit(panel, panel.get_rect(bottomright=(screen.get_width() - 12, screen.get_height() - 12)))

    def handle_event(self, event: pygame.event.Event):
        if event.type == TRACK_END_EVENT and self.active and self.auto_advance:
            self.transition_pending = True
            self.transition_started_at = get_current_time()
            self.transition_wait_started_at = self.transition_started_at
            return False

        if not self.active:
            return False

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                return True
            if event.key == pygame.K_TAB:
                self.camera.locked_on_square = not self.camera.locked_on_square
            if not Config.theatre_mode:
                if self.camera.locked_on_square:
                    time_from_start = self.world.time-self.play_delay_ms/1000+Config.music_offset/1000
                    if time_from_start < -0.2:
                        return
                    arrows_n_space = (pygame.K_SPACE, pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN)

                    if 97+26 > event.key >= 97 or event.key in arrows_n_space:  # press a to z key or space or arrows
                        self.misses = self.world.handle_keypress(time_from_start, self.misses)
