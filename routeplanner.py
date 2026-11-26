"""Whole-song bounce planning that keeps the square's route clear of pegs.

The square moves diagonally between bounces. At each note it bounces off a peg on one of two axes; that choice decides
where the peg sits and which way the next stretch of route goes. A choice is accepted only if, against everything
placed in the last `window` seconds,

  1. the new peg does not sit on a stretch of route the square has already travelled (it would have passed through it),
  2. the next stretch of route does not pass through an existing peg,
  3. the new peg does not overlap another peg.

"Route" means the area the square actually sweeps (the diagonal band), tested exactly: a square of size S moving from
P0 to P1 touches a rectangle R iff the segment P0-P1 touches R grown by S/2 on every side.

When neither axis works at a note, the planner backtracks to earlier notes and tries their other axis (depth-first,
like the original recursive generator). Search is bounded: after `attempts_per_floor` attempts without getting past
a note, or once `time_budget` is spent, the planner stops backtracking below that note and takes the axis with the
fewest violations there, counting it as a conflict. So it always finishes.
"""

from __future__ import annotations

from dataclasses import dataclass
import random
from time import perf_counter
from typing import Optional, Sequence

import pygame

from bounce import Bounce
from config import Config
from spatial import SpatialHash

ROUTE_CLEARANCE = 4  # px of air required between the swept route and a peg, and between pegs


@dataclass
class RouteStats:
    conflicts: int = 0          # notes where every option violated something (least-violation axis used)
    backtracks: int = 0         # times a note's choice was undone
    attempts: int = 0           # options tested
    budget_exhausted: bool = False
    elapsed_ms: float = 0.0


def _segment_hits_box(x0: float, y0: float, x1: float, y1: float,
                      left: float, top: float, right: float, bottom: float) -> bool:
    """Liang-Barsky: does segment (x0,y0)-(x1,y1) pass through the open box? Touching the edge does not count."""
    t0, t1 = 0.0, 1.0
    dx, dy = x1 - x0, y1 - y0
    for p, q in ((-dx, x0 - left), (dx, right - x0), (-dy, y0 - top), (dy, bottom - y0)):
        if p == 0:
            if q <= 0:
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return False
            t0 = max(t0, r)
        else:
            if r < t0:
                return False
            t1 = min(t1, r)
    return t1 - t0 > 1e-9


class RoutePlanner:
    def __init__(
            self,
            start_pos: Sequence[float],
            start_dir: Sequence[int],
            start_time: float = 0.0,
            obstacles: Sequence[tuple[pygame.Rect, float]] = (),
            window: Optional[float] = None,
            obstacle_segments: Sequence[tuple[float, float, float, float]] = (),
            seed=None,
            attempts_per_floor: int = 4000,
            time_budget: float = 8.0,
    ):
        self.start_pos = [float(start_pos[0]), float(start_pos[1])]
        self.start_dir = [int(start_dir[0]), int(start_dir[1])]
        self.start_time = start_time
        self.half = Config.SQUARE_SIZE / 2 + ROUTE_CLEARANCE
        # default: the whole song. Pegs stay drawn while on screen (and are drawn far ahead), so a route must not
        # cross any of them, however old or far in the future.
        self.window = window if window is not None else float("inf")
        self.rng = random.Random(Config.seed if seed is None else seed)
        self.attempts_per_floor = attempts_per_floor
        self.time_budget = time_budget
        cell = Config.spatial_cell_size
        self.pegs = SpatialHash(cell)
        self.segments = SpatialHash(cell)
        self.peg_rects: dict[int, pygame.Rect] = {}
        self.peg_times: dict[int, float] = {}
        self.segment_ends: dict[int, tuple[float, float, float, float]] = {}
        self.segment_times: dict[int, float] = {}
        for index, (rect, timestamp) in enumerate(obstacles):
            key = -1 - index  # obstacles never collide with "own" route
            self.peg_rects[key] = rect.copy()
            self.peg_times[key] = timestamp
            self.pegs.insert(key, rect)
        for index, ends in enumerate(obstacle_segments):  # a route travelled before this one (previous song)
            self._insert_segment(-1 - index, tuple(float(value) for value in ends), float("-inf"))

    # ---- geometry -------------------------------------------------------------------------------------------------
    def _route_box(self, rect: pygame.Rect) -> tuple[float, float, float, float]:
        return rect.left - self.half, rect.top - self.half, rect.right + self.half, rect.bottom + self.half

    def _segment_bbox(self, x0, y0, x1, y1) -> pygame.Rect:
        left, top = min(x0, x1) - self.half, min(y0, y1) - self.half
        return pygame.Rect(int(left) - 1, int(top) - 1,
                           int(abs(x1 - x0) + 2 * self.half) + 3, int(abs(y1 - y0) + 2 * self.half) + 3)

    def _violations(self, k: int, peg: pygame.Rect, peg_time: float,
                    segment: Optional[tuple[float, float, float, float]], stop_at_first: bool) -> int:
        """Count rule breaks for placing peg k and the route segment k+1 that leaves it."""
        oldest = peg_time - self.window
        count = 0
        grown = peg.inflate(ROUTE_CLEARANCE * 2, ROUTE_CLEARANCE * 2)
        # 3. peg vs pegs
        for key in self.pegs.query(grown):
            if self.peg_times[key] >= oldest:
                count += 1
                if stop_at_first:
                    return count
        # 1. new peg vs travelled route (segments k and k+1 touch peg k by construction)
        box = self._route_box(peg)
        for key in self.segments.query(grown):
            if key >= k or self.segment_times[key] < oldest:
                continue
            if _segment_hits_box(*self.segment_ends[key], *box):
                count += 1
                if stop_at_first:
                    return count
        # 2. next route vs existing pegs (peg k is where it starts)
        if segment is not None:
            for key in self.pegs.query(self._segment_bbox(*segment)):
                if self.peg_times[key] < oldest:
                    continue
                if _segment_hits_box(*segment, *self._route_box(self.peg_rects[key])):
                    count += 1
                    if stop_at_first:
                        return count
        return count

    # ---- search ---------------------------------------------------------------------------------------------------
    def plan(self, times: Sequence[float]) -> tuple[list[Bounce], RouteStats]:
        started = perf_counter()
        stats = RouteStats()
        n = len(times)
        if n == 0:
            return [], stats
        speed = float(Config.square_speed)
        # arrival position/direction at each note; index -1 is the start
        arrive_pos: list[Optional[list[float]]] = [None] * n
        arrive_dir: list[Optional[list[int]]] = [None] * n
        order: list[Optional[tuple[int, int]]] = [None] * n
        tried = [0] * n
        chosen: list[Optional[Bounce]] = [None] * n

        def travel(pos, direction, seconds):
            return [pos[0] + direction[0] * speed * seconds, pos[1] + direction[1] * speed * seconds]

        first = travel(self.start_pos, self.start_dir, max(times[0] - self.start_time, 0.0))
        arrive_pos[0], arrive_dir[0] = first, self.start_dir.copy()
        self._insert_segment(0, (*self.start_pos, *first), times[0])

        floor_index = 0           # never backtrack below this note
        attempts_since_progress = 0
        frontier = 0
        k = 0
        while k < n:
            if order[k] is None:
                # upstream rule: keep the previous note's axis priority, swap it with direction_change_chance
                base = order[k - 1] if k > 0 else (0, 1)
                if self.rng.random() * 100 < Config.direction_change_chance:
                    base = (base[1], base[0])
                order[k] = base
                tried[k] = 0
            placed = False
            while tried[k] < 2:
                axis = order[k][tried[k]]
                tried[k] += 1
                stats.attempts += 1
                attempts_since_progress += 1
                bounce, peg, segment = self._candidate(k, axis, times, arrive_pos[k], arrive_dir[k], travel)
                if self._violations(k, peg, times[k], segment, stop_at_first=True) == 0:
                    self._commit(k, bounce, peg, segment, times)
                    chosen[k] = bounce
                    placed = True
                    break
            if placed:
                k += 1
                if k < n:
                    arrive_pos[k] = chosen[k - 1].square_pos.copy()
                    arrive_pos[k] = travel(arrive_pos[k], chosen[k - 1].square_dir, times[k] - times[k - 1])
                    arrive_dir[k] = chosen[k - 1].square_dir.copy()
                    order[k] = None
                if k > frontier:
                    frontier = k
                    attempts_since_progress = 0
                continue

            out_of_time = perf_counter() - started > self.time_budget
            if out_of_time:
                stats.budget_exhausted = True
            if k <= floor_index or out_of_time or attempts_since_progress > self.attempts_per_floor:
                # give up on a clean route here: least-violation axis, and never undo it
                best = None
                for axis in order[k]:
                    bounce, peg, segment = self._candidate(k, axis, times, arrive_pos[k], arrive_dir[k], travel)
                    score = self._violations(k, peg, times[k], segment, stop_at_first=False)
                    if best is None or score < best[0]:
                        best = (score, bounce, peg, segment)
                _, bounce, peg, segment = best
                self._commit(k, bounce, peg, segment, times)
                chosen[k] = bounce
                stats.conflicts += 1
                floor_index = k + 1
                attempts_since_progress = 0
                k += 1
                if k < n:
                    arrive_pos[k] = travel(bounce.square_pos, bounce.square_dir, times[k] - times[k - 1])
                    arrive_dir[k] = bounce.square_dir.copy()
                    order[k] = None
                frontier = max(frontier, k)
                if out_of_time:
                    self.attempts_per_floor = 0  # greedy from here on
                continue

            # backtrack one note
            order[k] = None
            k -= 1
            self._uncommit(k)
            chosen[k] = None
            stats.backtracks += 1

        stats.elapsed_ms = (perf_counter() - started) * 1000
        return [bounce for bounce in chosen], stats

    def _candidate(self, k, axis, times, pos, direction, travel):
        new_dir = list(direction)
        new_dir[axis] *= -1
        bounce = Bounce(pos, new_dir, times[k], axis)
        peg = bounce.get_collision_rect()
        segment = None
        if k + 1 < len(times):
            nxt = travel(pos, new_dir, times[k + 1] - times[k])
            segment = (pos[0], pos[1], nxt[0], nxt[1])
        return bounce, peg, segment

    def _insert_segment(self, key: int, ends: tuple[float, float, float, float], timestamp: float):
        self.segment_ends[key] = ends
        self.segment_times[key] = timestamp
        self.segments.insert(key, self._segment_bbox(*ends))

    def _commit(self, k, bounce, peg, segment, times):
        self.peg_rects[k] = peg
        self.peg_times[k] = times[k]
        self.pegs.insert(k, peg)
        if segment is not None:
            self._insert_segment(k + 1, segment, times[k + 1])

    def _uncommit(self, k):
        self.pegs.remove(k)
        self.peg_rects.pop(k, None)
        self.peg_times.pop(k, None)
        self.segments.remove(k + 1)
        self.segment_ends.pop(k + 1, None)
        self.segment_times.pop(k + 1, None)


def plan_route(times: Sequence[float], start_pos, start_dir, start_time: float = 0.0,
               obstacles: Sequence[tuple[pygame.Rect, float]] = (), **kwargs) -> tuple[list[Bounce], RouteStats]:
    return RoutePlanner(start_pos, start_dir, start_time, obstacles, **kwargs).plan(times)


def count_route_violations(bounces: Sequence[Bounce], start_pos, window: Optional[float] = None) -> int:
    """Independent checker: notes whose peg or outgoing route breaks a rule (same rules as the planner)."""
    if not bounces:
        return 0
    planner = RoutePlanner(start_pos, [1, 1], window=window)
    bad = 0
    previous = list(start_pos)
    planner._insert_segment(0, (*previous, *bounces[0].square_pos), bounces[0].time)
    for k, bounce in enumerate(bounces):
        peg = bounce.get_collision_rect()
        segment = None
        if k + 1 < len(bounces):
            segment = (*bounce.square_pos, *bounces[k + 1].square_pos)
        if planner._violations(k, peg, bounce.time, segment, stop_at_first=True):
            bad += 1
        planner._commit(k, bounce, peg, segment, [b.time for b in bounces])
    return bad
