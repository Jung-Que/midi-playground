import pygame

from config import Config, get_colors, save_to_file
from square import Square
from utils import CameraFollow, get_camera_follow, get_font


class LiveConfigOverlay:
    """Small non-blocking settings panel used while a song keeps playing."""

    OPTIONS = (
        ("Theme / map colors", "theme"),
        ("Bounce squash", "bounce_effect"),
        ("Bounce particles", "do_particles_on_bounce"),
        ("Particle trail", "particle_trail"),
        ("Colored pegs", "do_color_bounce_pegs"),
        ("Square glow", "square_glow"),
        ("Glow intensity", "glow_intensity"),
        ("Core shape", "square_core_shape"),
        ("Core fill", "square_core_color"),
        ("Core outline", "square_core_outline_color"),
        ("Core size", "square_core_scale"),
        ("Core outline width", "square_core_outline_width"),
        ("Core rotation", "square_core_rotation_speed"),
        ("Core bounce pulse", "square_core_pulse_strength"),
        ("Particle amount", "particle_amount"),
        ("Camera mode", "camera_mode"),
        ("Music volume", "volume"),
        ("Map retention", "map_retention_seconds"),
        ("Map reveal fade", "map_fade_seconds"),
        ("Visible peg cap", "peg_visible_max"),
        ("Past peg fade", "peg_past_fade_seconds"),
        ("Peg visual spacing", "peg_overlap_padding"),
        ("Next peg guide", "peg_guide_line"),
        ("Performance HUD", "performance_hud"),
        ("Square speed (future)", "square_speed"),
        ("Bounce spacing (future)", "bounce_min_spacing"),
        ("Direction change (future)", "direction_change_chance"),
    )

    def __init__(self):
        self.active = False
        self.selected = 0
        self.preview_square = Square(0, 0, 1, 1)

    def handle_event(self, event: pygame.event.Event, game) -> bool:
        if event.type != pygame.KEYDOWN:
            return False

        if event.key == pygame.K_F10 and game.active:
            self.active = not self.active
            if not self.active:
                save_to_file()
            return True

        if not self.active:
            return False

        if event.key == pygame.K_ESCAPE:
            self.active = False
            save_to_file()
            return True
        if event.key == pygame.K_UP:
            self.selected = (self.selected - 1) % len(self.OPTIONS)
            return True
        if event.key == pygame.K_DOWN:
            self.selected = (self.selected + 1) % len(self.OPTIONS)
            return True
        if event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_RETURN, pygame.K_SPACE):
            direction = -1 if event.key == pygame.K_LEFT else 1
            self._adjust(self.OPTIONS[self.selected][1], direction, game)
            return True
        return True

    @staticmethod
    def _adjust(name: str, direction: int, game):
        if name == "theme":
            themes = list(Config.color_themes)
            current_index = themes.index(Config.theme) if Config.theme in themes else 0
            Config.theme = themes[(current_index + direction) % len(themes)]
        elif name in {
            "bounce_effect", "do_particles_on_bounce", "particle_trail",
            "do_color_bounce_pegs", "square_glow", "performance_hud", "peg_guide_line",
        }:
            setattr(Config, name, not bool(getattr(Config, name)))
        elif name == "glow_intensity":
            Config.glow_intensity = max(1, min(40, int(Config.glow_intensity) + direction))
        elif name == "square_core_shape":
            shapes = Config.square_core_shapes
            current = Config.square_core_shape if Config.square_core_shape in shapes else shapes[0]
            Config.square_core_shape = shapes[(shapes.index(current) + direction) % len(shapes)]
        elif name in {"square_core_color", "square_core_outline_color"}:
            colors = Config.square_core_colors
            current = getattr(Config, name)
            if current not in colors:
                current = colors[0]
            setattr(Config, name, colors[(colors.index(current) + direction) % len(colors)])
        elif name == "square_core_scale":
            Config.square_core_scale = max(
                0.2, min(0.75, round(float(Config.square_core_scale) + direction * 0.05, 2))
            )
        elif name == "square_core_outline_width":
            Config.square_core_outline_width = max(
                0, min(6, int(Config.square_core_outline_width) + direction)
            )
        elif name == "square_core_rotation_speed":
            Config.square_core_rotation_speed = max(
                -180, min(180, int(Config.square_core_rotation_speed) + direction * 15)
            )
        elif name == "square_core_pulse_strength":
            Config.square_core_pulse_strength = max(
                0.0, min(0.4, round(float(Config.square_core_pulse_strength) + direction * 0.05, 2))
            )
        elif name == "particle_amount":
            Config.particle_amount = max(0, min(50, int(Config.particle_amount) + direction))
        elif name == "camera_mode":
            Config.camera_mode = (int(Config.camera_mode) + direction) % len(CameraFollow)
            game.camera.lock_type = CameraFollow(Config.camera_mode)
            game.camera.locked_on_square = True
        elif name == "volume":
            Config.volume = max(0, min(100, int(Config.volume) + direction * 5))
            pygame.mixer.music.set_volume(Config.volume / 100)
        elif name == "map_retention_seconds":
            Config.map_retention_seconds = max(1, min(30, int(Config.map_retention_seconds) + direction))
        elif name == "map_fade_seconds":
            Config.map_fade_seconds = max(0.0, min(2.0, round(float(Config.map_fade_seconds) + direction * 0.05, 2)))
        elif name == "peg_visible_max":
            Config.peg_visible_max = max(
                int(Config.peg_visible_min), min(10, int(Config.peg_visible_max) + direction)
            )
        elif name == "peg_past_fade_seconds":
            Config.peg_past_fade_seconds = max(
                0.0, min(5.0, round(float(Config.peg_past_fade_seconds) + direction * 0.1, 1))
            )
        elif name == "peg_overlap_padding":
            Config.peg_overlap_padding = max(
                0, min(24, int(Config.peg_overlap_padding) + direction * 2)
            )
        elif name == "square_speed":
            Config.square_speed = max(100, min(2000, int(Config.square_speed) + direction * 50))
            game.regenerate_future_map()
        elif name == "bounce_min_spacing":
            Config.bounce_min_spacing = max(5, min(200, int(Config.bounce_min_spacing) + direction * 5))
            game.regenerate_future_map()
        elif name == "direction_change_chance":
            Config.direction_change_chance = max(
                0, min(100, int(Config.direction_change_chance) + direction * 5)
            )
            game.regenerate_future_map()

    @staticmethod
    def _value(name: str) -> str:
        value = getattr(Config, name)
        if isinstance(value, bool):
            return "On" if value else "Off"
        if name == "camera_mode":
            return get_camera_follow(value).name
        if name == "volume":
            return f"{value}%"
        if name == "square_core_shape":
            return str(value).replace("_", " ").title()
        if name in {"square_core_color", "square_core_outline_color"}:
            return "Accent" if value == "accent" else str(value).upper()
        if name == "square_core_scale":
            return f"{float(value) * 100:.0f}%"
        if name == "square_core_outline_width":
            return f"{int(value)}px"
        if name == "square_core_rotation_speed":
            return f"{int(value)} deg/s"
        if name == "square_core_pulse_strength":
            return f"{float(value) * 100:.0f}%"
        if name == "map_retention_seconds":
            return f"{value}s"
        if name == "map_fade_seconds":
            return f"{float(value):.2f}s"
        if name == "peg_visible_max":
            return str(int(value))
        if name == "peg_past_fade_seconds":
            return f"{float(value):.1f}s"
        if name == "peg_overlap_padding":
            return f"{int(value)}px"
        if name == "square_speed":
            return f"{value}px/s"
        if name == "bounce_min_spacing":
            return f"{value}ms"
        if name == "direction_change_chance":
            return f"{value}%"
        return str(value)

    def draw(self, screen: pygame.Surface, game_active: bool):
        if not game_active:
            self.active = False
            return

        hint = get_font(18).render("F10: live settings", True, (255, 255, 255))
        screen.blit(hint, hint.get_rect(topright=(Config.SCREEN_WIDTH - 15, 15)))
        if not self.active:
            return

        width = min(620, Config.SCREEN_WIDTH - 40)
        height = min(600, Config.SCREEN_HEIGHT - 40)
        panel = pygame.Surface((width, height), pygame.SRCALPHA)
        panel.fill((10, 12, 18, 225))
        pygame.draw.rect(panel, get_colors()["hallway"], panel.get_rect(), width=3, border_radius=8)

        title = get_font(30).render("Live Settings", True, (255, 255, 255))
        panel.blit(title, (24, 18))
        help_text = get_font(16).render("Up/Down select  Left/Right change  F10/Esc close", True, (190, 195, 205))
        panel.blit(help_text, (24, 58))

        preview_rect = pygame.Rect(width - 84, 10, 64, 64)
        pygame.draw.rect(panel, (25, 29, 38), preview_rect, border_radius=8)
        pygame.draw.rect(panel, (95, 105, 125), preview_rect, width=1, border_radius=8)
        if pygame.time.get_ticks() - self.preview_square.time_since_glow_start > 1200:
            self.preview_square.start_bounce()
        self.preview_square.draw(panel, preview_rect.inflate(-12, -12))

        row_height = 28
        visible_rows = max(1, (height - 104) // row_height)
        start = max(0, min(
            self.selected - visible_rows // 2,
            len(self.OPTIONS) - visible_rows,
        ))
        for display_index, index in enumerate(range(start, min(start + visible_rows, len(self.OPTIONS)))):
            label, name = self.OPTIONS[index]
            y = 94 + display_index * row_height
            if index == self.selected:
                pygame.draw.rect(panel, (60, 75, 95, 220), (14, y - 3, width - 28, 28), border_radius=5)
            color = (255, 255, 255) if index == self.selected else (210, 214, 222)
            label_surface = get_font(18).render(label, True, color)
            value_surface = get_font(18).render(self._value(name), True, color)
            panel.blit(label_surface, (26, y))
            panel.blit(value_surface, value_surface.get_rect(topright=(width - 26, y)))

        screen.blit(panel, panel.get_rect(center=screen.get_rect().center))
