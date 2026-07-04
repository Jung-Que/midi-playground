import pygame

from config import Config, get_colors, save_to_file
from square import Square
from utils import CameraFollow, get_camera_follow, get_font


class LiveConfigOverlay:
    """Wide, non-blocking keyboard and mouse settings panel."""

    OPTIONS = (
        ("Theme / map colors", "theme"),
        ("Bounce squash", "bounce_effect"),
        ("Bounce particles", "do_particles_on_bounce"),
        ("Particle trail", "particle_trail"),
        ("Colored pegs", "do_color_bounce_pegs"),
        ("Square glow", "square_glow"),
        ("Glow intensity", "glow_intensity"),
        ("Directional edge", "square_edge_highlight"),
        ("Outer border", "square_body_border_width"),
        ("Corner radius", "square_body_corner_radius"),
        ("Border bounce pulse", "square_border_pulse_strength"),
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
        ("Shorts mode (next song)", "shorts_mode"),
        ("Clean recording UI", "shorts_clean_ui"),
        ("Segment start", "shorts_segment_start"),
        ("Segment length", "shorts_segment_duration"),
        ("Repeat segment", "shorts_loop"),
        ("Title overlay", "shorts_title_overlay"),
        ("Recording countdown", "shorts_countdown"),
        ("Map retention", "map_retention_seconds"),
        ("Map reveal fade", "map_fade_seconds"),
        ("Minimum future pegs", "peg_visible_min"),
        ("Maximum future pegs", "peg_visible_max"),
        ("Peg preview time", "peg_preview_seconds"),
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
        self.row_hitboxes: dict[int, pygame.Rect] = {}
        self.minus_hitboxes: dict[int, pygame.Rect] = {}
        self.plus_hitboxes: dict[int, pygame.Rect] = {}
        self.panel_rect = pygame.Rect(0, 0, 0, 0)
        self.page_capacity = 1

    def handle_event(self, event: pygame.event.Event, game) -> bool:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_F10 and game.active:
            self.active = not self.active
            if not self.active:
                save_to_file()
            return True

        if not self.active:
            return False

        if event.type == pygame.MOUSEMOTION:
            for index, rect in self.row_hitboxes.items():
                if rect.collidepoint(event.pos):
                    self.selected = index
                    break
            return True

        if event.type == pygame.MOUSEWHEEL:
            step = -1 if event.y > 0 else 1
            self.selected = (self.selected + step) % len(self.OPTIONS)
            return True

        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button in (4, 5):
                step = -1 if event.button == 4 else 1
                self.selected = (self.selected + step) % len(self.OPTIONS)
                return True
            for index, rect in self.row_hitboxes.items():
                if not rect.collidepoint(event.pos):
                    continue
                self.selected = index
                if event.button == 1 and self.minus_hitboxes[index].collidepoint(event.pos):
                    self._adjust(self.OPTIONS[index][1], -1, game)
                elif event.button == 1 and self.plus_hitboxes[index].collidepoint(event.pos):
                    self._adjust(self.OPTIONS[index][1], 1, game)
                elif event.button == 3:
                    self._adjust(self.OPTIONS[index][1], -1, game)
                return True
            return True

        if event.type == pygame.KEYDOWN:
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
            if event.key == pygame.K_PAGEUP:
                self.selected = max(0, self.selected - self.page_capacity)
                return True
            if event.key == pygame.K_PAGEDOWN:
                self.selected = min(len(self.OPTIONS) - 1, self.selected + self.page_capacity)
                return True
            if event.key == pygame.K_HOME:
                self.selected = 0
                return True
            if event.key == pygame.K_END:
                self.selected = len(self.OPTIONS) - 1
                return True
            if event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_RETURN, pygame.K_SPACE):
                direction = -1 if event.key == pygame.K_LEFT else 1
                self._adjust(self.OPTIONS[self.selected][1], direction, game)
                return True
            return True
        return event.type in {
            pygame.MOUSEBUTTONUP,
            pygame.MOUSEMOTION,
        }

    @staticmethod
    def _adjust(name: str, direction: int, game):
        if name == "theme":
            themes = list(Config.color_themes)
            current_index = themes.index(Config.theme) if Config.theme in themes else 0
            Config.theme = themes[(current_index + direction) % len(themes)]
        elif name in {"shorts_mode", "shorts_clean_ui", "shorts_loop", "shorts_title_overlay", "shorts_countdown"}:
            setattr(Config, name, not bool(getattr(Config, name)))
            if name == "shorts_mode" and game.active:
                game.stream_message = "Shorts mode will apply when the next song starts"
        elif name in {
            "bounce_effect", "do_particles_on_bounce", "particle_trail",
            "do_color_bounce_pegs", "square_glow", "performance_hud", "peg_guide_line",
            "square_edge_highlight",
        }:
            setattr(Config, name, not bool(getattr(Config, name)))
        elif name == "glow_intensity":
            Config.glow_intensity = max(1, min(40, int(Config.glow_intensity) + direction))
        elif name == "square_body_border_width":
            Config.square_body_border_width = max(
                1, min(8, int(Config.square_body_border_width) + direction)
            )
        elif name == "square_body_corner_radius":
            Config.square_body_corner_radius = max(
                0, min(16, int(Config.square_body_corner_radius) + direction)
            )
        elif name == "square_border_pulse_strength":
            Config.square_border_pulse_strength = max(
                0.0, min(1.0, round(float(Config.square_border_pulse_strength) + direction * 0.05, 2))
            )
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
        elif name == "shorts_segment_start":
            Config.shorts_segment_start = max(0, int(Config.shorts_segment_start) + direction * 5)
            if game.active:
                game.stream_message = "Segment start will apply when the next song starts"
        elif name == "shorts_segment_duration":
            durations = (15, 30, 60)
            current = Config.shorts_segment_duration if Config.shorts_segment_duration in durations else 30
            Config.shorts_segment_duration = durations[(durations.index(current) + direction) % len(durations)]
        elif name == "map_retention_seconds":
            Config.map_retention_seconds = max(1, min(30, int(Config.map_retention_seconds) + direction))
        elif name == "map_fade_seconds":
            Config.map_fade_seconds = max(0.0, min(2.0, round(float(Config.map_fade_seconds) + direction * 0.05, 2)))
        elif name == "peg_visible_min":
            Config.peg_visible_min = max(
                1, min(20, int(Config.peg_visible_min) + direction)
            )
            Config.peg_visible_max = max(
                int(Config.peg_visible_min), int(Config.peg_visible_max)
            )
        elif name == "peg_visible_max":
            Config.peg_visible_max = max(
                int(Config.peg_visible_min), min(20, int(Config.peg_visible_max) + direction)
            )
        elif name == "peg_preview_seconds":
            Config.peg_preview_seconds = max(
                0.1, min(5.0, round(float(Config.peg_preview_seconds) + direction * 0.1, 1))
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
        if name == "shorts_segment_start":
            minutes, seconds = divmod(int(value), 60)
            return f"{minutes}:{seconds:02d}"
        if name == "shorts_segment_duration":
            return f"{int(value)}s"
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
        if name in {"square_body_border_width", "square_body_corner_radius"}:
            return f"{int(value)}px"
        if name == "square_border_pulse_strength":
            return f"{float(value) * 100:.0f}%"
        if name == "map_retention_seconds":
            return f"{value}s"
        if name == "map_fade_seconds":
            return f"{float(value):.2f}s"
        if name in {"peg_visible_min", "peg_visible_max"}:
            return str(int(value))
        if name == "peg_preview_seconds":
            return f"{float(value):.1f}s"
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

        if not self.active and not (Config.shorts_mode and Config.shorts_clean_ui):
            hint = get_font(18).render("F10: live settings", True, (255, 255, 255))
            screen.blit(hint, hint.get_rect(topright=(Config.SCREEN_WIDTH - 15, 15)))
        if not self.active:
            return

        screen_width, screen_height = screen.get_size()
        width = max(320, min(1180, screen_width - 24))
        height = max(300, min(820, screen_height - 24))
        self.panel_rect = pygame.Rect(0, 0, width, height)
        self.panel_rect.center = screen.get_rect().center
        panel = pygame.Surface((width, height), pygame.SRCALPHA)
        panel.fill((10, 12, 18, 238))
        pygame.draw.rect(panel, get_colors()["hallway"], panel.get_rect(), width=3, border_radius=8)

        title = get_font(30).render("Live Settings", True, (255, 255, 255))
        panel.blit(title, (24, 18))
        help_text = get_font(16).render(
            "Mouse +/- change  Wheel or Up/Down select  Left/Right change  F10/Esc close",
            True,
            (190, 195, 205),
        )
        panel.blit(help_text, (24, 58))

        preview_rect = pygame.Rect(width - 84, 10, 64, 64)
        pygame.draw.rect(panel, (25, 29, 38), preview_rect, border_radius=8)
        pygame.draw.rect(panel, (95, 105, 125), preview_rect, width=1, border_radius=8)
        if pygame.time.get_ticks() - self.preview_square.time_since_glow_start > 1200:
            self.preview_square.start_bounce()
        self.preview_square.draw(panel, preview_rect.inflate(-12, -12))

        row_top = 94
        footer_height = 38
        row_height = 32
        columns = 2 if width >= 1000 else 1
        visible_rows = max(1, (height - row_top - footer_height) // row_height)
        self.page_capacity = max(visible_rows * columns, 1)
        page = self.selected // self.page_capacity
        start = page * self.page_capacity
        end = min(start + self.page_capacity, len(self.OPTIONS))
        column_width = (width - 28) // columns

        self.row_hitboxes.clear()
        self.minus_hitboxes.clear()
        self.plus_hitboxes.clear()

        for display_index, index in enumerate(range(start, end)):
            label, name = self.OPTIONS[index]
            column = display_index // visible_rows
            row = display_index % visible_rows
            x = 14 + column * column_width
            y = row_top + row * row_height
            row_rect = pygame.Rect(x, y - 2, column_width - 8, row_height - 2)
            minus_rect = pygame.Rect(row_rect.right - 180, y + 1, 26, 24)
            plus_rect = pygame.Rect(row_rect.right - 30, y + 1, 26, 24)
            value_rect = pygame.Rect(minus_rect.right + 4, y + 1, plus_rect.left - minus_rect.right - 8, 24)

            screen_offset = self.panel_rect.topleft
            self.row_hitboxes[index] = row_rect.move(*screen_offset)
            self.minus_hitboxes[index] = minus_rect.move(*screen_offset)
            self.plus_hitboxes[index] = plus_rect.move(*screen_offset)

            if index == self.selected:
                pygame.draw.rect(panel, (60, 75, 95, 225), row_rect, border_radius=5)
            color = (255, 255, 255) if index == self.selected else (210, 214, 222)
            label_surface = get_font(17).render(label, True, color)
            value_surface = get_font(16).render(self._value(name), True, color)
            minus_surface = get_font(19).render("-", True, color)
            plus_surface = get_font(19).render("+", True, color)
            panel.blit(label_surface, label_surface.get_rect(midleft=(row_rect.left + 10, row_rect.centery)))
            pygame.draw.rect(panel, (32, 39, 51), minus_rect, border_radius=5)
            pygame.draw.rect(panel, (32, 39, 51), plus_rect, border_radius=5)
            pygame.draw.rect(panel, (88, 101, 124), minus_rect, width=1, border_radius=5)
            pygame.draw.rect(panel, (88, 101, 124), plus_rect, width=1, border_radius=5)
            panel.blit(minus_surface, minus_surface.get_rect(center=minus_rect.center))
            panel.blit(plus_surface, plus_surface.get_rect(center=plus_rect.center))
            panel.blit(value_surface, value_surface.get_rect(center=value_rect.center))

        page_count = max(1, (len(self.OPTIONS) + self.page_capacity - 1) // self.page_capacity)
        footer = get_font(15).render(
            f"Page {page + 1}/{page_count}  |  Right-click a row to decrease  |  Home/End and Page Up/Down supported",
            True,
            (155, 164, 180),
        )
        panel.blit(footer, footer.get_rect(midbottom=(width // 2, height - 10)))
        screen.blit(panel, self.panel_rect)
