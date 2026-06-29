try:
    from glowing import make_glowy2
except ImportError:
    make_glowy2 = None
from utils import *
import pygame
from pygame import Color
from bounce import Bounce


class Square:
    def __init__(self, x: float = 0, y: float = 0, dx: int = 1, dy: int = 1):
        self.pos: list[float] = [x, y]
        self.dir: list[int] = [dx, dy]
        self.last_bounce_time = -100
        self.latest_bounce_direction = 0  # 0 = horiz, 1 = vert
        self.past_colors = []
        self.died = False

        self.time_since_glow_start = 0
        self.glowy_surfaces = {}

    def register_past_color(self, col: tuple[int, int, int]):
        for _ in range(max(Config.square_swipe_anim_speed, 1)):
            self.past_colors.insert(0, col)
        while len(self.past_colors) > Config.SQUARE_SIZE * 4 / 5:
            self.past_colors.pop()

    def get_surface(self, size: tuple[int, int]):
        ss = int(Config.SQUARE_SIZE * 4 / 5)
        surf = pygame.Surface((ss, ss))
        for index, col in enumerate(self.past_colors):
            y = index if self.dir_y != 1 else ss - 1 - index
            pygame.draw.line(surf, col, (0, y), (ss, y))
        return pygame.transform.scale(surf, size)

    def copy(self) -> "Square":
        new = Square(*self.pos, *self.dir)
        new.last_bounce_time = self.last_bounce_time
        new.latest_bounce_direction = self.latest_bounce_direction
        return new

    @property
    def x(self):
        return self.pos[0]

    @property
    def y(self):
        return self.pos[1]

    def title_screen_physics(self, bounding: pygame.Rect):
        self.reg_move()
        r = self.rect
        if r.right > bounding.right:
            self.dir[0] = -1
            self.latest_bounce_direction = 0
        elif r.left < bounding.left:
            self.dir[0] = 1
            self.latest_bounce_direction = 0
        elif r.bottom > bounding.bottom:
            self.dir[1] = -1
            self.latest_bounce_direction = 1
        elif r.top < bounding.top:
            self.dir[1] = 1
            self.latest_bounce_direction = 1
        else:
            return False
        self.start_bounce()
        self.last_bounce_time = get_current_time()
        return True

    def compute_glowy_surface(self, rect, val):
        level = max(1, int(round(val / 3) * 3))
        key = (rect.width, rect.height, level, tuple(Color(Config.glow_color)))
        cached = self.glowy_surfaces.get(key)
        if cached is not None:
            return cached
        glowy_borders = make_glowy2(
            (rect.size[0] + 40, rect.size[1] + 40),
            Color(Config.glow_color),
            level,
        )
        surface = pygame.Surface(rect.inflate(100, 100).size, pygame.SRCALPHA)
        surface.blit(glowy_borders, (20, 20), special_flags=pygame.BLEND_RGBA_ADD)
        if len(self.glowy_surfaces) >= 32:
            self.glowy_surfaces.clear()
        self.glowy_surfaces[key] = surface
        return surface

    def draw_glowing3(self, win, rect):
        if self.died:
            return

        if Config.square_glow:
            if pygame.time.get_ticks() - self.time_since_glow_start < Config.square_glow_duration * 1000:
                progress = 1 - (pygame.time.get_ticks() - self.time_since_glow_start) / (
                        Config.square_glow_duration * 1000)
                val = int(progress * Config.glow_intensity)
            else:
                val = 1
            val = max(val, Config.square_min_glow)
            surf = self.compute_glowy_surface(rect, val)

            win.blit(surf, rect.move(-40, -40).topleft, special_flags=pygame.BLEND_RGBA_ADD)

    def accent_color(self):
        palette = get_colors()["square"]
        square_color_index = round((self.dir_x + 1) / 2 + self.dir_y + 1)
        return pygame.Color(palette[square_color_index % len(palette)])

    def _draw_neon_core(self, screen: pygame.Surface, sqrect: pygame.Rect):
        accent = self.accent_color()
        background = pygame.Color(get_colors()["background"])
        core = background.lerp(pygame.Color(5, 7, 10), 0.45)
        border_radius = max(2, min(sqrect.width, sqrect.height) // 7)

        pygame.draw.rect(screen, accent, sqrect, border_radius=border_radius)
        inner = sqrect.inflate(-6, -6)
        if inner.width > 0 and inner.height > 0:
            pygame.draw.rect(screen, core, inner, border_radius=max(border_radius - 2, 1))

        edge_color = accent.lerp(pygame.Color(255, 255, 255), 0.45)
        edge_width = max(2, int(min(sqrect.width, sqrect.height) * 0.08))
        if self.dir_x > 0:
            pygame.draw.line(screen, edge_color, sqrect.topright, sqrect.bottomright, edge_width)
        elif self.dir_x < 0:
            pygame.draw.line(screen, edge_color, sqrect.topleft, sqrect.bottomleft, edge_width)
        if self.dir_y > 0:
            pygame.draw.line(screen, edge_color, sqrect.bottomleft, sqrect.bottomright, edge_width)
        elif self.dir_y < 0:
            pygame.draw.line(screen, edge_color, sqrect.topleft, sqrect.topright, edge_width)

        center_x = sqrect.centerx + int(self.dir_x * sqrect.width * 0.08)
        center_y = sqrect.centery + int(self.dir_y * sqrect.height * 0.08)
        radius = max(3, int(min(sqrect.width, sqrect.height) * 0.14))
        pygame.draw.polygon(screen, edge_color, [
            (center_x, center_y - radius),
            (center_x + radius, center_y),
            (center_x, center_y + radius),
            (center_x - radius, center_y),
        ])

        bounce_age = (pygame.time.get_ticks() - self.time_since_glow_start) / 1000
        if 0 <= bounce_age < 0.15:
            flash = 1.0 - bounce_age / 0.15
            flash_color = accent.lerp(pygame.Color(255, 255, 255), flash)
            width = max(2, int(5 * flash))
            if self.latest_bounce_direction == 0:
                x = sqrect.left if self.dir_x > 0 else sqrect.right
                pygame.draw.line(screen, flash_color, (x, sqrect.top), (x, sqrect.bottom), width)
            else:
                y = sqrect.top if self.dir_y > 0 else sqrect.bottom
                pygame.draw.line(screen, flash_color, (sqrect.left, y), (sqrect.right, y), width)

    def draw(self, screen: pygame.Surface, sqrect: pygame.Rect):
        if self.died:
            return

        if Config.theme == "dark_modern" and make_glowy2 is not None:
            self.draw_glowing3(screen, sqrect)
        self._draw_neon_core(screen, sqrect)

    @x.setter
    def x(self, val: int):
        self.pos[0] = val

    @y.setter
    def y(self, val: int):
        self.pos[1] = val

    @property
    def dir_x(self):
        return self.dir[0]

    @property
    def dir_y(self):
        return self.dir[1]

    @property
    def rect(self):
        return pygame.Rect(self.x - Config.SQUARE_SIZE / 2, self.y - Config.SQUARE_SIZE / 2,
                           *([Config.SQUARE_SIZE] * 2))

    def start_bounce(self):
        self.time_since_glow_start = pygame.time.get_ticks()

    def obey_bounce(self, bounce: Bounce):
        # planned bounces
        self.start_bounce()
        self.pos = bounce.square_pos
        self.dir = bounce.square_dir
        self.latest_bounce_direction = bounce.bounce_dir
        self.last_bounce_time = bounce.time
        return

    def reg_move(self, use_dt: bool = True):
        self.x += self.dir_x * Config.square_speed * (Config.dt if use_dt else 1 / FRAMERATE)
        self.y += self.dir_y * Config.square_speed * (Config.dt if use_dt else 1 / FRAMERATE)
