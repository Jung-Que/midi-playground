import moderngl
import pygame
from typing import Optional, Any
from json import load, dump
import logging
from math import isfinite
from os.path import isfile

pygame.init()


class Config:
    # constants
    rSCREEN_WIDTH = pygame.display.Info().current_w if pygame.display.Info().current_w else 1920
    rSCREEN_HEIGHT = pygame.display.Info().current_h if pygame.display.Info().current_h else 1080
    # SCREEN_WIDTH = pygame.display.Info().current_w if pygame.display.Info().current_w else 1920
    # SCREEN_HEIGHT = pygame.display.Info().current_h if pygame.display.Info().current_h else 1080
    CAMERA_SPEED = 500
    SQUARE_SIZE = 50
    PARTICLE_SPEED = 10

    # colors
    #
    # each color theme requires a hallway color, a background color, and at least one square color
    # optionally, the color theme provides an hp_bar_border color (default 10, 9, 8),
    # an hp_bar_background color (default 34, 51, 59), and a list
    # of hp_bar_fill colors (default (156, 198, 155), (189, 228, 168), (215, 242, 186))
    #
    color_themes = {
        "dark_modern": {
            "hallway": pygame.Color(40, 44, 52),
            "background": pygame.Color(24, 26, 30),
            "square": [
                pygame.Color(224, 26, 79),
                pygame.Color(173, 247, 182),
                pygame.Color(249, 194, 46),
                pygame.Color(83, 179, 203)
            ]
        },

        "dark": {
            "hallway": pygame.Color(214, 209, 205),
            "background": pygame.Color(60, 63, 65),
            "square": [
                pygame.Color(224, 26, 79),
                pygame.Color(173, 247, 182),
                pygame.Color(249, 194, 46),
                pygame.Color(83, 179, 203)
            ]
        },
        # credits to TheCodingCrafter for these themes
        "light": {
            "hallway": pygame.Color(60, 63, 65),
            "background": pygame.Color(214, 209, 205),
            "square": [
                pygame.Color(224, 26, 79),
                pygame.Color(173, 247, 182),
                pygame.Color(249, 194, 46),
                pygame.Color(83, 179, 203)
            ]
        },
        "rainbow": {
            "hallway": pygame.Color((214, 209, 205)),
            "background": pygame.Color((60, 63, 65)),
            "square": [
                pygame.Color(0, 0, 0)
            ]
        },
        "autumn": {
            "hallway": pygame.Color((252, 191, 73)),
            "background": pygame.Color((247, 127, 0)),
            "square": [
                pygame.Color(224, 26, 79),
                pygame.Color(173, 247, 182),
                pygame.Color(249, 194, 46),
                pygame.Color(83, 179, 203)
            ]
        },
        "winter": {
            "hallway": pygame.Color((202, 240, 255)),
            "background": pygame.Color((0, 180, 216)),
            "square": [
                pygame.Color(224, 26, 79),
                pygame.Color(173, 247, 182),
                pygame.Color(249, 194, 46),
                pygame.Color(83, 179, 203)
            ]
        },
        "spring": {
            "hallway": pygame.Color((158, 240, 26)),
            "background": pygame.Color((112, 224, 0)),
            "square": [
                pygame.Color(224, 26, 79),
                pygame.Color(173, 247, 182),
                pygame.Color(249, 194, 46),
                pygame.Color(83, 179, 203)
            ]
        },
        "magenta": {
            "hallway": pygame.Color((239, 118, 116)),
            "background": pygame.Color((218, 52, 77)),
            "square": [
                pygame.Color(224, 26, 79),
                pygame.Color(173, 247, 182),
                pygame.Color(249, 194, 46),
                pygame.Color(83, 179, 203)
            ]
        },
        "monochromatic": {
            "hallway": pygame.Color((255, 255, 255)),
            "background": pygame.Color((0, 0, 0)),
            "square": [
                pygame.Color(80, 80, 80),
                pygame.Color(150, 150, 150),
                pygame.Color(100, 100, 100),
                pygame.Color(200, 200, 200)
            ]
        },
        "green-screen-hallway": {
            "hallway": pygame.Color(0, 255, 0),
            "background": pygame.Color(60, 63, 65),
            "square": [
                pygame.Color(224, 26, 79),
                pygame.Color(173, 247, 182),
                pygame.Color(249, 194, 46),
                pygame.Color(83, 179, 203)
            ]
        },
        "green-screen-background": {
            "hallway": pygame.Color(60, 63, 65),
            "background": pygame.Color(0, 255, 0),
            "square": [
                pygame.Color(224, 26, 79),
                pygame.Color(173, 247, 182),
                pygame.Color(249, 194, 46),
                pygame.Color(83, 179, 203)
            ]
        }
    }

    # intended configurable settings
    SCREEN_WIDTH = pygame.display.Info().current_w if pygame.display.Info().current_w else 1920
    SCREEN_HEIGHT = pygame.display.Info().current_h if pygame.display.Info().current_h else 1080
    theme: Optional[str] = "dark"
    seed: Optional[int] = None
    camera_mode: Optional[int] = 4
    start_playing_delay = 3000
    max_notes: Optional[int] = None
    bounce_min_spacing: Optional[float] = 30
    square_speed: Optional[int] = 600
    volume: Optional[int] = 70
    music_offset: Optional[int] = 0
    direction_change_chance: Optional[int] = 30
    hp_drain_rate = 10
    theatre_mode = True
    particle_trail = True
    shader_file_name = "none.glsl"
    do_color_bounce_pegs = False
    do_particles_on_bounce = True
    bounce_effect = True

    # rolling world
    map_retention_seconds = 5
    map_fade_seconds = 0.35
    peg_visible_min = 3
    peg_visible_max = 6
    peg_visible_past_max = 3
    peg_density_window_seconds = 1.0
    peg_preview_seconds = 0.5
    peg_past_fade_seconds = 1.5
    peg_overlap_padding = 8
    peg_guide_line = True
    peg_order_count = 3
    peg_countdown_seconds = 0.6
    peg_impact_seconds = 0.18
    peg_impact_ring_seconds = 0.3
    camera_target_lead = 0.3
    camera_max_lead_ratio = 0.22
    camera_smoothing_seconds = 0.2
    camera_max_speed = 2400
    square_afterimage_count = 4
    square_afterimage_seconds = 0.28
    square_afterimage_rate = 20
    square_core_shapes = ("diamond", "heart", "star", "circle", "note", "bolt", "cross", "none")
    square_core_colors = (
        "accent", "#FFFFFF", "#FF4F91", "#55D9FF", "#FFD166", "#8D7CFF", "#69F0AE", "#FF7043"
    )
    square_core_shape = "diamond"
    square_core_color = "accent"
    square_core_outline_color = "#FFFFFF"
    square_core_scale = 0.42
    square_core_outline_width = 2
    square_core_rotation_speed = 0
    square_core_pulse_strength = 0.15
    particle_max_active = 200
    particle_bounce_lifetime = 0.25
    particle_death_lifetime = 0.7
    map_view_margin = 2.0
    map_chunk_seconds = 15
    map_preload_seconds = 30
    map_stream_buffer_chunks = 8
    spatial_cell_size = 256
    playlist_prefetch_count = 2
    transition_wait_timeout_seconds = 20
    audio_clock_max_probe_ms = 5000
    performance_hud = True

    # settings that are not configurable (yet)
    backtrack_chance: Optional[float] = 0.02
    backtrack_amount: Optional[int] = 40
    rainbow_speed: Optional[int] = 30
    square_swipe_anim_speed: Optional[int] = 4
    particle_amount = 8
    language = "english"

    # other random stuff
    current_song = None
    ctx: moderngl.Context = None
    glsl_program: moderngl.Program = None
    render_object: moderngl.VertexArray = None
    screen: pygame.Surface = None
    dt = 0.01

    # ascii shader
    ascii_tex: moderngl.Texture = None

    # keys to save and load
    save_attrs = ["theme", "seed", "camera_mode", "start_playing_delay", "max_notes", "bounce_min_spacing",
                  "square_speed", "volume", "music_offset", "direction_change_chance", "hp_drain_rate", "theatre_mode",
                  "particle_trail", "shader_file_name", "do_color_bounce_pegs", 
                  "do_particles_on_bounce", "bounce_effect", "square_glow", "glow_intensity",
                  "particle_amount", "map_retention_seconds", "map_fade_seconds",
                  "peg_visible_max", "peg_past_fade_seconds", "peg_overlap_padding", "peg_guide_line",
                  "square_core_shape", "square_core_color", "square_core_outline_color",
                  "square_core_scale", "square_core_outline_width", "square_core_rotation_speed",
                  "square_core_pulse_strength",
                  "performance_hud", "language",
                  "SCREEN_WIDTH", "SCREEN_HEIGHT"]

    # glow effect, for dark_modern only for now
    square_glow = True
    square_glow_duration = 0.25
    glow_intensity = 15  # 1-40
    square_min_glow = 3
    border_color = pygame.Color(255, 255, 255)
    glow_color = pygame.Color(255, 255, 255)


DEFAULT_SETTINGS = {name: getattr(Config, name) for name in Config.save_attrs}

_BOOLEAN_SETTINGS = {
    "theatre_mode", "particle_trail", "do_color_bounce_pegs", "do_particles_on_bounce",
    "bounce_effect", "square_glow", "peg_guide_line", "performance_hud",
}
_INTEGER_RANGES = {
    "camera_mode": (0, 4),
    "start_playing_delay": (0, 10_000),
    "square_speed": (100, 2_000),
    "volume": (0, 100),
    "music_offset": (-5_000, 5_000),
    "direction_change_chance": (0, 100),
    "hp_drain_rate": (0, 100),
    "glow_intensity": (1, 40),
    "particle_amount": (0, 50),
    "map_retention_seconds": (1, 60),
    "peg_visible_max": (1, 20),
    "peg_overlap_padding": (0, 50),
    "square_core_outline_width": (0, 6),
    "square_core_rotation_speed": (-180, 180),
    "SCREEN_WIDTH": (320, 7_680),
    "SCREEN_HEIGHT": (240, 4_320),
}
_FLOAT_RANGES = {
    "bounce_min_spacing": (5.0, 200.0),
    "map_fade_seconds": (0.0, 5.0),
    "peg_past_fade_seconds": (0.0, 10.0),
    "square_core_scale": (0.2, 0.75),
    "square_core_pulse_strength": (0.0, 0.4),
}


def _clamped_number(value, default, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        if not isfinite(value):
            return default
    except OverflowError:
        return default
    value = max(minimum, min(maximum, value))
    return int(round(value)) if integer else value


def _valid_color(value, default):
    if value == "accent":
        return value
    if not isinstance(value, str):
        return default
    try:
        pygame.Color(value)
    except (TypeError, ValueError):
        return default
    return value


def sanitize_settings(data: Any) -> tuple[dict[str, Any], list[str]]:
    """Return a complete, safe settings dictionary and human-readable corrections."""
    corrections = []
    if not isinstance(data, dict):
        corrections.append("settings root was not an object")
        data = {}
    clean = {}

    for name, default in DEFAULT_SETTINGS.items():
        value = data.get(name, default)
        if name in _BOOLEAN_SETTINGS:
            sanitized = value if isinstance(value, bool) else default
        elif name in _INTEGER_RANGES:
            sanitized = _clamped_number(value, default, *_INTEGER_RANGES[name], integer=True)
        elif name in _FLOAT_RANGES:
            sanitized = _clamped_number(value, default, *_FLOAT_RANGES[name])
        elif name == "seed":
            sanitized = value if value is None or (isinstance(value, int) and not isinstance(value, bool)) else default
        elif name == "max_notes":
            sanitized = value if value is None or (isinstance(value, int) and value > 0) else default
        elif name == "theme":
            sanitized = value if value in Config.color_themes else default
        elif name == "square_core_shape":
            sanitized = value if value in Config.square_core_shapes else default
        elif name in {"square_core_color", "square_core_outline_color"}:
            sanitized = _valid_color(value, default)
        elif name == "shader_file_name":
            sanitized = (
                value if isinstance(value, str) and value.endswith(".glsl")
                and "/" not in value and "\\" not in value else default
            )
        elif name == "language":
            sanitized = value if isinstance(value, str) and value.strip() else default
        else:
            sanitized = value if isinstance(value, type(default)) else default

        clean[name] = sanitized
        if name in data and sanitized != value:
            corrections.append(f"{name}: {value!r} -> {sanitized!r}")

    for unknown in sorted(set(data) - set(DEFAULT_SETTINGS)):
        corrections.append(f"ignored unknown setting: {unknown}")
    return clean, corrections


def get_colors():
    return Config.color_themes.get(Config.theme, Config.color_themes["dark"])


def save_to_file(dat: Optional[dict[str, Any]] = None, path: str = "./assets/settings.json"):
    if dat is None:
        dat = {k: getattr(Config, k) for k in Config.save_attrs}
    with open(path, "w") as f:
        dump(dat, f, indent=4)


def load_from_file(path: str = "./assets/settings.json"):
    logger = logging.getLogger("midi_playground.settings")
    read_failed = False
    try:
        if isfile(path):
            with open(path, "r") as f:
                data = load(f)
        else:
            data = {}
    except Exception as e:
        logger.exception("Unable to read settings; defaults restored: %s", e)
        data = {}
        read_failed = True

    clean, corrections = sanitize_settings(data)
    if read_failed:
        corrections.insert(0, "settings file was unreadable; defaults restored")
    for setting, value in clean.items():
        setattr(Config, setting, value)
    if corrections:
        logger.warning("Corrected settings: %s", "; ".join(corrections))
    if corrections or read_failed or not isfile(path):
        try:
            save_to_file(clean, path)
        except OSError:
            logger.exception("Unable to save corrected settings")
    return corrections


if __name__ == "config":
    load_from_file()
