try:
    from glowing import make_glowy2
except ImportError:
    make_glowy2 = None
from utils import *
import pygame
from pygame import Color
from bounce import Bounce
from math import cos, pi, sin
from pathlib import Path


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
        self.custom_core_images = {}

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

    @staticmethod
    def _configured_color(value, accent: pygame.Color) -> pygame.Color:
        if str(value).lower() == "accent":
            return pygame.Color(accent)
        try:
            return pygame.Color(value)
        except (TypeError, ValueError):
            return pygame.Color(accent)

    @staticmethod
    def _shape_points(shape: str, center: tuple[float, float], radius: float):
        cx, cy = center
        normalized = {
            "diamond": ((0, -1), (1, 0), (0, 1), (-1, 0)),
            "heart": (
                (0, 1), (-0.88, 0.18), (-1, -0.3), (-0.72, -0.72),
                (-0.3, -0.78), (0, -0.42), (0.3, -0.78), (0.72, -0.72),
                (1, -0.3), (0.88, 0.18),
            ),
            "bolt": (
                (0.05, -1), (-0.65, 0.08), (-0.12, 0.08), (-0.35, 1),
                (0.72, -0.22), (0.18, -0.22),
            ),
            "cross": (
                (-0.3, -1), (0.3, -1), (0.3, -0.3), (1, -0.3),
                (1, 0.3), (0.3, 0.3), (0.3, 1), (-0.3, 1),
                (-0.3, 0.3), (-1, 0.3), (-1, -0.3), (-0.3, -0.3),
            ),
        }.get(shape)
        if shape == "star":
            normalized = tuple(
                (
                    cos(-pi / 2 + index * pi / 5) * (1 if index % 2 == 0 else 0.45),
                    sin(-pi / 2 + index * pi / 5) * (1 if index % 2 == 0 else 0.45),
                )
                for index in range(10)
            )
        if normalized is None:
            return []
        return [(cx + x * radius, cy + y * radius) for x, y in normalized]

    @staticmethod
    def _draw_note(
            surface: pygame.Surface,
            center: tuple[int, int],
            radius: int,
            fill: pygame.Color,
            outline: pygame.Color,
            outline_width: int,
    ):
        cx, cy = center
        head_center = (int(cx - radius * 0.38), int(cy + radius * 0.48))
        head_radius = max(2, int(radius * 0.34))
        stem_x = int(cx - radius * 0.08)
        stem_top = int(cy - radius * 0.9)
        stem_bottom = int(cy + radius * 0.45)
        stem_width = max(2, int(radius * 0.22))
        flag = [
            (stem_x, stem_top),
            (int(cx + radius * 0.75), int(cy - radius * 0.62)),
            (int(cx + radius * 0.68), int(cy - radius * 0.12)),
            (stem_x, int(cy - radius * 0.42)),
        ]

        if outline_width > 0:
            pygame.draw.circle(surface, outline, head_center, head_radius + outline_width)
            pygame.draw.line(
                surface, outline, (stem_x, stem_bottom), (stem_x, stem_top),
                stem_width + outline_width * 2,
            )
            pygame.draw.polygon(surface, outline, flag)
        pygame.draw.circle(surface, fill, head_center, head_radius)
        pygame.draw.line(surface, fill, (stem_x, stem_bottom), (stem_x, stem_top), stem_width)
        if outline_width > 0:
            inset_flag = [
                (stem_x, stem_top + outline_width),
                (int(cx + radius * 0.68), int(cy - radius * 0.57)),
                (int(cx + radius * 0.61), int(cy - radius * 0.21)),
                (stem_x, int(cy - radius * 0.46)),
            ]
            pygame.draw.polygon(surface, fill, inset_flag)
        else:
            pygame.draw.polygon(surface, fill, flag)

    def _draw_custom_core(
            self,
            screen: pygame.Surface,
            sqrect: pygame.Rect,
            center: tuple[int, int],
            accent: pygame.Color,
    ):
        shape = str(Config.square_core_shape).lower()
        if shape not in Config.square_core_shapes or shape == "none":
            return

        bounce_age = (pygame.time.get_ticks() - self.time_since_glow_start) / 1000
        pulse = 1.0
        if 0 <= bounce_age < 0.2:
            pulse += max(0.0, float(Config.square_core_pulse_strength)) * (1 - bounce_age / 0.2)
        scale = max(0.2, min(float(Config.square_core_scale), 0.75))
        radius = max(3, int(min(sqrect.width, sqrect.height) * scale * pulse / 2))
        outline_width = max(0, min(int(Config.square_core_outline_width), 6))
        fill = self._configured_color(Config.square_core_color, accent)
        outline = self._configured_color(Config.square_core_outline_color, accent)
        side = max(12, int(radius * 3 + outline_width * 4))
        symbol = pygame.Surface((side, side), pygame.SRCALPHA)
        local_center = (side // 2, side // 2)

        if shape == "custom":
            custom = self._load_custom_core_image(Config.square_core_image_path)
            if custom is not None:
                maximum = max(2, radius * 2)
                ratio = min(maximum / custom.get_width(), maximum / custom.get_height())
                size = (
                    max(1, round(custom.get_width() * ratio)),
                    max(1, round(custom.get_height() * ratio)),
                )
                scaled = pygame.transform.smoothscale(custom, size)
                symbol.blit(scaled, scaled.get_rect(center=local_center))
            else:
                points = self._shape_points("diamond", local_center, radius)
                pygame.draw.polygon(symbol, fill, points)
                if outline_width:
                    pygame.draw.polygon(symbol, outline, points, outline_width)
        elif shape == "circle":
            pygame.draw.circle(symbol, fill, local_center, radius)
            if outline_width:
                pygame.draw.circle(symbol, outline, local_center, radius, outline_width)
        elif shape == "note":
            self._draw_note(symbol, local_center, radius, fill, outline, outline_width)
        else:
            points = self._shape_points(shape, local_center, radius)
            if not points:
                return
            pygame.draw.polygon(symbol, fill, points)
            if outline_width:
                pygame.draw.polygon(symbol, outline, points, outline_width)

        rotation = float(Config.square_core_rotation_speed) * pygame.time.get_ticks() / 1000
        if rotation:
            symbol = pygame.transform.rotozoom(symbol, -rotation, 1.0)
        screen.blit(symbol, symbol.get_rect(center=center))

    def _load_custom_core_image(self, path_value):
        if not path_value:
            return None
        path = Path(str(path_value)).expanduser()
        try:
            stat = path.stat()
        except OSError:
            return None
        if stat.st_size <= 0 or stat.st_size > 20 * 1024 * 1024:
            return None
        key = str(path.resolve())
        cached = self.custom_core_images.get(key)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        try:
            image = pygame.image.load(path).convert_alpha()
        except (OSError, pygame.error):
            return None
        if image.get_width() > 4096 or image.get_height() > 4096:
            return None
        if len(self.custom_core_images) >= 8:
            self.custom_core_images.clear()
        self.custom_core_images[key] = (stat.st_mtime_ns, stat.st_size, image)
        return image

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
        self._draw_custom_core(screen, sqrect, (center_x, center_y), accent)

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
