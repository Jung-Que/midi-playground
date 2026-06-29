import pygame

from config import Config, get_colors, save_to_file
from utils import CameraFollow, get_font


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
        ("Particle amount", "particle_amount"),
        ("Camera mode", "camera_mode"),
        ("Music volume", "volume"),
        ("Map retention", "map_retention_seconds"),
        ("Square speed (future)", "square_speed"),
        ("Bounce spacing (future)", "bounce_min_spacing"),
        ("Direction change (future)", "direction_change_chance"),
    )

    def __init__(self):
        self.active = False
        self.selected = 0

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
            "do_color_bounce_pegs", "square_glow",
        }:
            setattr(Config, name, not bool(getattr(Config, name)))
        elif name == "glow_intensity":
            Config.glow_intensity = max(1, min(40, int(Config.glow_intensity) + direction))
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
            return CameraFollow(int(value)).name
        if name == "volume":
            return f"{value}%"
        if name == "map_retention_seconds":
            return f"{value}s"
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
        height = min(540, Config.SCREEN_HEIGHT - 40)
        panel = pygame.Surface((width, height), pygame.SRCALPHA)
        panel.fill((10, 12, 18, 225))
        pygame.draw.rect(panel, get_colors()["hallway"], panel.get_rect(), width=3, border_radius=8)

        title = get_font(30).render("Live Settings", True, (255, 255, 255))
        panel.blit(title, (24, 18))
        help_text = get_font(16).render("Up/Down select  Left/Right change  F10/Esc close", True, (190, 195, 205))
        panel.blit(help_text, (24, 58))

        row_height = 30
        for index, (label, name) in enumerate(self.OPTIONS):
            y = 94 + index * row_height
            if y + row_height > height - 10:
                break
            if index == self.selected:
                pygame.draw.rect(panel, (60, 75, 95, 220), (14, y - 3, width - 28, 28), border_radius=5)
            color = (255, 255, 255) if index == self.selected else (210, 214, 222)
            label_surface = get_font(18).render(label, True, color)
            value_surface = get_font(18).render(self._value(name), True, color)
            panel.blit(label_surface, (26, y))
            panel.blit(value_surface, value_surface.get_rect(topright=(width - 26, y)))

        screen.blit(panel, panel.get_rect(center=screen.get_rect().center))
