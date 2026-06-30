from collections import defaultdict
from math import floor
from typing import Hashable, Iterable

import pygame


class SpatialHash:
    """Small mutable spatial index for collision and viewport queries."""

    def __init__(self, cell_size: int = 256):
        self.cell_size = max(int(cell_size), 1)
        self._cells: dict[tuple[int, int], set[Hashable]] = defaultdict(set)
        self._rects: dict[Hashable, pygame.Rect] = {}
        self._item_cells: dict[Hashable, tuple[tuple[int, int], ...]] = {}

    def clear(self):
        self._cells.clear()
        self._rects.clear()
        self._item_cells.clear()

    def _cell_keys(self, rect: pygame.Rect) -> Iterable[tuple[int, int]]:
        left = floor(rect.left / self.cell_size)
        right = floor((max(rect.right, rect.left + 1) - 1) / self.cell_size)
        top = floor(rect.top / self.cell_size)
        bottom = floor((max(rect.bottom, rect.top + 1) - 1) / self.cell_size)
        for x in range(left, right + 1):
            for y in range(top, bottom + 1):
                yield x, y

    def insert(self, key: Hashable, rect: pygame.Rect):
        self.remove(key)
        stored = rect.copy()
        cells = tuple(self._cell_keys(stored))
        self._rects[key] = stored
        self._item_cells[key] = cells
        for cell in cells:
            self._cells[cell].add(key)

    def remove(self, key: Hashable):
        for cell in self._item_cells.pop(key, ()):
            items = self._cells.get(cell)
            if items is None:
                continue
            items.discard(key)
            if not items:
                self._cells.pop(cell, None)
        self._rects.pop(key, None)

    def query(self, rect: pygame.Rect) -> set[Hashable]:
        candidates: set[Hashable] = set()
        for cell in self._cell_keys(rect):
            candidates.update(self._cells.get(cell, ()))
        return {
            key for key in candidates
            if self._rects[key].colliderect(rect)
        }

    def get_rect(self, key: Hashable) -> pygame.Rect:
        return self._rects[key]

    def __len__(self) -> int:
        return len(self._rects)
