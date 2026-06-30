from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from shutil import copyfile
from tempfile import NamedTemporaryFile

import pygame

from config import Config
from paths import user_path


STYLE_FIELDS = (
    "square_core_shape",
    "square_core_color",
    "square_core_outline_color",
    "square_core_scale",
    "square_core_outline_width",
    "square_core_rotation_speed",
    "square_core_pulse_strength",
    "square_core_image_path",
)
DEFAULT_STYLE = {
    "square_core_shape": "diamond",
    "square_core_color": "accent",
    "square_core_outline_color": "#FFFFFF",
    "square_core_scale": 0.42,
    "square_core_outline_width": 2,
    "square_core_rotation_speed": 0,
    "square_core_pulse_strength": 0.15,
    "square_core_image_path": "",
}
BUILTIN_PRESETS = {
    "Classic Diamond": DEFAULT_STYLE,
    "Heart Pop": {
        **DEFAULT_STYLE,
        "square_core_shape": "heart",
        "square_core_color": "#FF4F91",
        "square_core_scale": 0.52,
        "square_core_pulse_strength": 0.25,
    },
    "Electric Star": {
        **DEFAULT_STYLE,
        "square_core_shape": "star",
        "square_core_color": "#FFD166",
        "square_core_outline_color": "#FFFFFF",
        "square_core_rotation_speed": 45,
    },
    "Cool Note": {
        **DEFAULT_STYLE,
        "square_core_shape": "note",
        "square_core_color": "#55D9FF",
        "square_core_rotation_speed": 0,
    },
    "Violet Bolt": {
        **DEFAULT_STYLE,
        "square_core_shape": "bolt",
        "square_core_color": "#8D7CFF",
        "square_core_outline_width": 1,
        "square_core_rotation_speed": -15,
    },
}


class SquareStyleError(Exception):
    pass


@dataclass(frozen=True)
class StyleValidation:
    style: dict
    warnings: tuple[str, ...]


def _color(value, default):
    if value == "accent":
        return "accent", True
    if not isinstance(value, str):
        return default, False
    try:
        parsed = pygame.Color(value)
        return f"#{parsed.r:02X}{parsed.g:02X}{parsed.b:02X}", True
    except (TypeError, ValueError):
        return default, False


def validate_style(data) -> StyleValidation:
    if not isinstance(data, dict):
        raise SquareStyleError("Preset JSON must contain an object")
    warnings = []
    style = DEFAULT_STYLE.copy()
    shape = str(data.get("square_core_shape", style["square_core_shape"])).lower()
    if shape not in Config.square_core_shapes:
        warnings.append(f"Unknown shape {shape!r}; diamond used")
        shape = "diamond"
    style["square_core_shape"] = shape

    for name in ("square_core_color", "square_core_outline_color"):
        raw = data.get(name, style[name])
        parsed, valid = _color(raw, style[name])
        if not valid:
            warnings.append(f"Invalid {name}; default used")
        style[name] = parsed

    ranges = {
        "square_core_scale": (0.2, 0.75, float),
        "square_core_outline_width": (0, 6, int),
        "square_core_rotation_speed": (-180, 180, int),
        "square_core_pulse_strength": (0.0, 0.4, float),
    }
    for name, (minimum, maximum, converter) in ranges.items():
        raw = data.get(name, style[name])
        try:
            value = converter(raw)
        except (TypeError, ValueError):
            warnings.append(f"Invalid {name}; default used")
            value = style[name]
        style[name] = max(minimum, min(maximum, value))

    image_path = data.get("square_core_image_path", "")
    style["square_core_image_path"] = str(image_path) if image_path else ""
    if shape == "custom" and not style["square_core_image_path"]:
        warnings.append("Custom shape has no PNG; diamond fallback will be shown")
    elif shape == "custom" and not Path(style["square_core_image_path"]).is_file():
        warnings.append("Custom PNG is missing; diamond fallback will be shown")
    return StyleValidation(style, tuple(warnings))


def snapshot_style() -> dict:
    return {name: getattr(Config, name) for name in STYLE_FIELDS}


def apply_style(data) -> StyleValidation:
    result = validate_style(data)
    for name, value in result.style.items():
        setattr(Config, name, value)
    return result


def _safe_name(name: str) -> str:
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", name.strip())
    slug = re.sub(r"\s+", "-", slug).strip("-. ").lower()
    if not slug:
        raise SquareStyleError("Preset name must contain letters or numbers")
    return slug[:64]


def _atomic_json_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(data, temporary, indent=2, ensure_ascii=False)
        temporary_path.replace(path)
    except OSError as exc:
        raise SquareStyleError(f"Unable to save preset: {exc}") from exc
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def save_user_preset(name: str, directory: Path | None = None) -> Path:
    directory = Path(directory) if directory is not None else user_path("presets-local", "square")
    path = Path(directory) / f"{_safe_name(name)}.json"
    _atomic_json_write(path, snapshot_style())
    return path


def user_presets(directory: Path | None = None) -> dict[str, Path]:
    directory = Path(directory) if directory is not None else user_path("presets-local", "square")
    if not directory.is_dir():
        return {}
    return {path.stem: path for path in sorted(directory.glob("*.json"))}


def load_preset(name: str, directory: Path | None = None) -> StyleValidation:
    if name in BUILTIN_PRESETS:
        return apply_style(BUILTIN_PRESETS[name])
    path = user_presets(directory).get(name)
    if path is None:
        raise SquareStyleError(f"Preset not found: {name}")
    return import_style_json(path)


def export_style_json(path: Path) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".json":
        path = path.with_suffix(".json")
    _atomic_json_write(path, snapshot_style())
    return path


def import_style_json(path: Path) -> StyleValidation:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SquareStyleError(f"Unable to load preset JSON: {exc}") from exc
    return apply_style(data)


def install_custom_png(
        source: Path,
        asset_directory: Path | None = None,
) -> Path:
    source = Path(source).expanduser().resolve()
    if source.suffix.lower() != ".png" or not source.is_file():
        raise SquareStyleError("Custom symbol must be an existing PNG file")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise SquareStyleError(f"Unable to read PNG: {exc}") from exc
    if size <= 0 or size > 20 * 1024 * 1024:
        raise SquareStyleError("PNG must be between 1 byte and 20 MB")
    try:
        image = pygame.image.load(source)
    except (OSError, pygame.error) as exc:
        raise SquareStyleError(f"Invalid PNG image: {exc}") from exc
    if image.get_width() <= 0 or image.get_height() <= 0:
        raise SquareStyleError("PNG has no drawable pixels")
    if image.get_width() > 4096 or image.get_height() > 4096:
        raise SquareStyleError("PNG dimensions must not exceed 4096x4096")

    digest = sha256(source.read_bytes()).hexdigest()[:16]
    asset_directory = (
        Path(asset_directory) if asset_directory is not None
        else user_path("presets-local", "square", "assets")
    )
    asset_directory.mkdir(parents=True, exist_ok=True)
    destination = asset_directory / f"core-{digest}.png"
    if not destination.exists():
        try:
            copyfile(source, destination)
        except OSError as exc:
            raise SquareStyleError(f"Unable to install custom PNG: {exc}") from exc
    return destination
