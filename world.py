from utils import *
from bounce import Bounce
from particle import Particle
from square import Square
from time import time as get_current_time
from scorekeeper import Scorekeeper
import random
import pygame
from collections import deque
from spatial import SpatialHash


class World:
    """it's a cruel world out there"""

    def __init__(self):
        self.future_bounces = deque()
        self.past_bounces: list[Bounce] = []
        self.completed_bounces = 0
        self.total_bounces = 0
        self.map_stream_complete = True
        self.start_time = 0
        self.time = 0
        self.rectangles: list[pygame.Rect] = []
        self.collision_times: list[float] = []
        self.particles: list[Particle] = []
        self.timestamps = []
        self.square = Square()
        self.scorekeeper = Scorekeeper(self)
        self.colors = []
        self.geometry_index = SpatialHash(Config.spatial_cell_size)
        self.safe_area_index = SpatialHash(Config.spatial_cell_size)

    def update_time(self) -> None:
        self.time = get_current_time() - self.start_time

    def get_next_bounce(self) -> Bounce:
        """Also pops the bounce from the future_bounces list"""
        self.past_bounces.append(self.future_bounces.popleft())
        self.completed_bounces += 1
        return self.past_bounces[-1]

    def prune_past_bounces(self, retention_seconds: float):
        cutoff = self.time - retention_seconds
        self.past_bounces = [bounce for bounce in self.past_bounces if bounce.time >= cutoff]

    def rebuild_spatial_indexes(self, safe_areas: list[pygame.Rect]):
        self.geometry_index = SpatialHash(Config.spatial_cell_size)
        self.safe_area_index = SpatialHash(Config.spatial_cell_size)
        for index, rect in enumerate(self.rectangles):
            self.geometry_index.insert(index, rect)
        for index, rect in enumerate(safe_areas):
            self.safe_area_index.insert(index, rect)

    def visible_geometry(self, world_view: pygame.Rect) -> list[int]:
        return sorted(self.geometry_index.query(world_view))

    def visible_safe_areas(self, world_view: pygame.Rect) -> list[int]:
        return sorted(self.safe_area_index.query(world_view))

    def add_bounce_particles(self, sp: list[float], sd: list[float], bounce_time: float = 0.0):
        local_rate = sum(
            bounce.time <= bounce_time + 1.0 for bounce in self.future_bounces
        )
        adaptive_amount = max(4, min(10, round(12 - local_rate * 0.45)))
        remaining_budget = max(int(Config.particle_max_active) - len(self.particles), 0)
        amount = min(int(Config.particle_amount), adaptive_amount, remaining_budget)
        accent = get_colors()["square"][self.completed_bounces % len(get_colors()["square"])]
        for _ in range(amount):
            new = Particle(
                [sp[0] + random.randint(-8, 8), sp[1] + random.randint(-8, 8)],
                sd,
                color=accent,
                lifetime=Config.particle_bounce_lifetime,
                size_range=(3, 8),
                speed_scale=0.75,
            )
            self.particles.append(new)

    def handle_bouncing(self, square: Square):
        while self.future_bounces and (self.time * 1000 + Config.music_offset) / 1000 > self.future_bounces[0].time:
            current_bounce = self.get_next_bounce()
            before = square.dir.copy()
            square.obey_bounce(current_bounce)
            changed = square.dir.copy()
            for axis in range(2):
                if before[axis] == changed[axis]:
                    changed[axis] = 0
                else:
                    changed[axis] = -changed[axis]
            if Config.do_particles_on_bounce:
                self.add_bounce_particles(square.pos, changed, current_bounce.time)

            # stop square at end
            if not self.future_bounces and self.map_stream_complete:
                square.dir = [0, 0]
                square.pos = current_bounce.square_pos

    def handle_keypress(self, time_from_start, misses):
        return self.scorekeeper.do_keypress(time_from_start, misses)

    def gen_future_bounces(self, _start_notes: list[tuple[int, int, int]], percent_update_callback):
        """Recursive solution may be necessary"""
        _start_notes = _start_notes[:Config.max_notes] if Config.max_notes is not None else _start_notes
        filtered_notes = remove_too_close_values(list(_start_notes), Config.bounce_min_spacing)
        total_notes = len(filtered_notes)
        if total_notes == 0:
            raise MapLoadingFailureError("The map does not contain any playable notes")
        max_percent = 0
        path = []
        safe_areas = []
        force_return = 0

        def recurs(
                square: Square,
                notes: list[float],
                bounces_so_far: list[Bounce] = None,
                prev_index_priority=None,
                t: float = 0,
                depth: int = 0
        ) -> Union[list[Bounce], bool]:
            nonlocal force_return, max_percent
            if prev_index_priority is None:
                prev_index_priority = [0, 1]
            if bounces_so_far is None:
                bounces_so_far = []
            gone_through_percent = (total_notes-len(notes)) * 100 // total_notes
            while gone_through_percent > max_percent:
                max_percent += 1
                if percent_update_callback(f"{max_percent}% done generating map"):
                    raise UserCancelsLoadingError()

            all_bounce_rects = [_bounc.get_collision_rect() for _bounc in bounces_so_far]
            if len(notes) == 0:
                return bounces_so_far
            # print(depth * 100 // total_notes)
            path_segment_start = len(path)
            start_rect = square.rect.copy()
            while True:
                t += 1/FRAMERATE
                square.reg_move(False)
                path.append(square.rect)
                if t > notes[0]:
                    # no collision (we good)
                    bounce_indexes = prev_index_priority

                    # randomly change direction every X% of the time
                    if random.random() * 100 < Config.direction_change_chance:
                        bounce_indexes = list(bounce_indexes.__reversed__())

                    # add safe area
                    safe_areas.append(start_rect.union(square.rect))

                    for direction_to_bounce in bounce_indexes:
                        square.dir[direction_to_bounce] *= -1
                        bounces_so_far.append(Bounce(square.pos, square.dir, t, direction_to_bounce))

                        toextend = recurs(
                            square=square.copy(),
                            notes=notes[1:],
                            bounces_so_far=[_b.copy() for _b in bounces_so_far],
                            t=t,
                            prev_index_priority=bounce_indexes.copy(),
                            depth=depth+1
                        )

                        if toextend:
                            return toextend
                        else:
                            bounces_so_far = bounces_so_far[:-1]
                            square.dir[direction_to_bounce] *= -1

                            # instead of trying other path from here, just exit a bit back to try another from previous
                            if force_return:
                                force_return -= 1
                                while len(path) != path_segment_start:
                                    path.pop()
                                return False

                            continue
                    while len(path) != path_segment_start:
                        path.pop()
                    return False

                othercheck = False
                if len(bounces_so_far):
                    othercheck = bounces_so_far[-1].get_collision_rect().collidelist(path[:-10])+1

                if square.rect.collidelist(all_bounce_rects) != -1 or othercheck:
                    if depth > 200:
                        if random.random() < Config.backtrack_chance:
                            max_percent -= (Config.backtrack_amount * 100 // total_notes) + 1
                            force_return = Config.backtrack_amount

                    while len(path) != path_segment_start:
                        path.pop()
                    return False

        self.scorekeeper.unhit_notes = filtered_notes.copy()

        generated_bounces = recurs(
            square=self.square.copy(),
            notes=filtered_notes
        )

        if generated_bounces is False:
            raise MapLoadingFailureError("The map failed to generate because of the recursion function. " +
                                         "If the midi has too many notes too close, it may not generate. " +
                                         "Maybe try changing the \"square speed\" or \"change dir chance\" in the config")

        if len(generated_bounces) == 0:
            raise MapLoadingFailureError("Map safearea list empty. Please report to the github under the issues tab")

        self.future_bounces = deque(generated_bounces)
        self.total_bounces = len(generated_bounces)

        percent_update_callback("Removing overlapping safe areas")

        # eliminate fully overlapping safe areas
        safe_areas: list[pygame.Rect]
        while True:
            new = []
            before_safe_count = len(safe_areas)
            for safe1 in safe_areas:
                for safe2 in safe_areas:
                    if safe2 == safe1:
                        continue
                    if safe2.contains(safe1):
                        break
                else:
                    new.append(safe1)
            safe_areas = new.copy()
            after_safe_count = len(safe_areas)
            if after_safe_count == before_safe_count:
                break
        safe_areas = safe_areas

        self.rectangles = [_fb.get_collision_rect() for _fb in self.future_bounces]
        self.collision_times = [_fb.time for _fb in self.future_bounces]
        
        # Setting random colors for the generated pegs
        self.colors = [random.choice([(224, 50, 50), (80, 210, 100), (230, 220, 50), (174, 170, 210), (245, 77, 247), (255, 153, 0)]) for _ in self.future_bounces]
        return safe_areas
