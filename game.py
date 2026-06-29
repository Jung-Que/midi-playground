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
from io import BytesIO
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
        self.auto_advance = False
        self.stream_message = ""

    def start_playlist(self, songs: list, selected_index: int, screen: pygame.Surface):
        self.stop_playlist(recreate_controller=True)
        self.playlist.configure(songs, selected_index)
        Config.current_song = songs[selected_index]
        result = self.start_song(screen)
        if result:
            return result
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
    ):
        random.seed(Config.seed)
        self.world = World()
        self.notes = []
        self.safe_areas = []
        self.safe_area_times = []
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

        self._build_safe_areas(start_pos)

        if audio_already_playing:
            self.world.start_time = track_started_at or get_current_time()
            self.world.square.pos = start_pos.copy()
            self.world.square.dir = start_dir.copy()
        else:
            try:
                audio_data, extension = read_song_audio(song_to_spec(Config.current_song))
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
        self.world.rectangles = [bounce.get_collision_rect() for bounce in bounces]
        self.world.collision_times = [bounce.time for bounce in bounces]
        palette = [(224, 50, 50), (80, 210, 100), (230, 220, 50), (174, 170, 210), (245, 77, 247), (255, 153, 0)]
        self.world.colors = [random.choice(palette) for _ in bounces]

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
        self.playlist.prepare_next(start_pos, start_dir)
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
        base_collision_times = [bounce.time for bounce in bounces]
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
        palette = [(224, 50, 50), (80, 210, 100), (230, 220, 50), (174, 170, 210), (245, 77, 247), (255, 153, 0)]
        self.world.future_bounces = deque(bounces)
        self.world.scorekeeper.unhit_notes = prepared.unhit_notes.copy()
        self.world.total_bounces = self.world.completed_bounces + len(bounces)
        self.world.rectangles = [item[0] for item in past_geometry] + [bounce.get_collision_rect() for bounce in bounces]
        self.world.collision_times = [item[1] for item in past_geometry] + base_collision_times
        self.world.colors = [item[2] for item in past_geometry] + [random.choice(palette) for _ in bounces]

        past_safe_areas = [
            (rect, timestamp)
            for rect, timestamp in zip(self.safe_areas, self.safe_area_times)
            if timestamp <= map_time
        ]
        self.safe_areas = [item[0] for item in past_safe_areas]
        self.safe_area_times = [item[1] for item in past_safe_areas]
        self._build_safe_areas(start_pos, time_offset=schedule_offset)

        end_pos, end_dir = self._end_state()
        self.playlist.refresh_next_map(end_pos, end_dir)
        self.stream_message = "Future map updated"

    def _queue_next_audio(self):
        if not self.auto_advance or self.next_audio_queued or not self.music_has_played:
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

    def _update_playlist_transition(self):
        self._queue_next_audio()
        if not self.transition_pending:
            return

        if not pygame.mixer.music.get_busy() and self.unqueued_audio is not None:
            audio_data, extension = self.unqueued_audio
            self._load_music(audio_data, extension)
            pygame.mixer.music.play()
            self.transition_started_at = get_current_time()

        transition = self.playlist.consume_prepared_map()
        if transition is None:
            if self.playlist.last_error:
                self.stream_message = self.playlist.last_error
            return

        _, song, prepared = transition
        Config.current_song = song
        if self.next_audio_queued and self.queued_audio_buffer is not None:
            self.current_audio_buffer = self.queued_audio_buffer
        result = self.start_song(
            Config.screen,
            prepared=prepared,
            seamless=True,
            audio_already_playing=pygame.mixer.music.get_busy(),
            track_started_at=self.transition_started_at,
        )
        if result:
            self.stream_message = str(result)
            return
        self.transition_pending = False
        self._prepare_next_song()

    def stop_playlist(self, recreate_controller: bool = False):
        self.auto_advance = False
        self.transition_pending = False
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
                for bnc_change in self.world.future_bounces:
                    bnc_change.time += get_current_time()-song_load_before

        screen_rect = screen.get_rect()

        # set world time
        self.world.update_time()

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
        for safe_area in self.safe_areas:
            offsetted = self.camera.offset(safe_area)
            if screen_rect.colliderect(offsetted):
                total_rects += 1
                pygame.draw.rect(screen, get_colors()["hallway"], offsetted)

        # draw pegs
        for i, bounce_rect in enumerate(self.world.rectangles):
            offsetted = self.camera.offset(bounce_rect)

            if offsetted.colliderect(screen_rect):
                total_rects += 1
                if Config.do_color_bounce_pegs and self.world.collision_times[i] < (self.world.time * 1000 + Config.music_offset - self.play_delay_ms)/1000:
                    pygame.draw.rect(screen, self.world.colors[i], offsetted)
                else:
                    pygame.draw.rect(screen, get_colors()["background"], offsetted)

        # particles
        for particle in self.world.particles:
            pygame.draw.rect(screen, particle.color, self.camera.offset(particle.rect))
        for remove_particle in [particle for particle in self.world.particles if particle.age()]:
            self.world.particles.remove(remove_particle)

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

    def handle_event(self, event: pygame.event.Event):
        if event.type == TRACK_END_EVENT and self.active and self.auto_advance:
            self.transition_pending = True
            self.transition_started_at = get_current_time()
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
