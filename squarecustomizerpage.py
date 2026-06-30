from html import escape
from pathlib import Path
from typing import Optional

try:
    from tkinter import Tk, filedialog
except ImportError:
    Tk = None
    filedialog = None

import pygame
import pygame_gui as pgui

from config import Config, get_colors, save_to_file
from square import Square
from squarecustomizer import (
    BUILTIN_PRESETS,
    DEFAULT_STYLE,
    SquareStyleError,
    apply_style,
    export_style_json,
    import_style_json,
    install_custom_png,
    load_preset,
    save_user_preset,
    user_presets,
)
from utils import get_font


class SquareCustomizerPage:
    def __init__(self):
        self.active = False
        self.manager = pgui.UIManager((Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT))
        self.preview_square = Square(0, 0, 1, 1)
        self.color_dialog = None
        self.color_target = None
        panel_width = min(1040, Config.SCREEN_WIDTH - 30)
        panel_height = min(760, Config.SCREEN_HEIGHT - 30)
        self.panel_rect = pygame.Rect(0, 0, panel_width, panel_height)
        self.panel_rect.center = (Config.SCREEN_WIDTH // 2, Config.SCREEN_HEIGHT // 2)
        x = self.panel_rect.x + 20
        y = self.panel_rect.y
        gap = 24
        left_width = max(165, min(250, int(panel_width * 0.32)))
        right_x = x + left_width + gap
        right_width = self.panel_rect.right - 20 - right_x
        preview_size = min(210, left_width - 10)
        self.preview_rect = pygame.Rect(
            x + (left_width - preview_size) // 2,
            y + 80,
            preview_size,
            preview_size,
        )

        self.back_button = pgui.elements.UIButton(
            pygame.Rect(self.panel_rect.right - 110, y + 16, 90, 32), "Back", self.manager
        )
        self.shape_dropdown = pgui.elements.UIDropDownMenu(
            list(Config.square_core_shapes),
            Config.square_core_shape,
            pygame.Rect(right_x, y + 85, right_width, 34),
            self.manager,
        )
        half = (right_width - 10) // 2
        self.fill_button = pgui.elements.UIButton(
            pygame.Rect(right_x, y + 140, half, 36), self._color_label("Fill", Config.square_core_color), self.manager
        )
        self.outline_button = pgui.elements.UIButton(
            pygame.Rect(right_x + half + 10, y + 140, half, 36),
            self._color_label("Outline", Config.square_core_outline_color),
            self.manager,
        )
        self.scale_slider = pgui.elements.UIHorizontalSlider(
            pygame.Rect(right_x, y + 205, right_width, 28), Config.square_core_scale,
            (0.2, 0.75), self.manager,
        )
        self.outline_slider = pgui.elements.UIHorizontalSlider(
            pygame.Rect(right_x, y + 260, right_width, 28), Config.square_core_outline_width,
            (0, 6), self.manager,
        )
        self.rotation_slider = pgui.elements.UIHorizontalSlider(
            pygame.Rect(right_x, y + 315, right_width, 28), Config.square_core_rotation_speed,
            (-180, 180), self.manager,
        )
        self.pulse_slider = pgui.elements.UIHorizontalSlider(
            pygame.Rect(right_x, y + 370, right_width, 28), Config.square_core_pulse_strength,
            (0.0, 0.4), self.manager,
        )
        png_button_width = min(130, max(95, right_width // 3))
        self.png_entry = pgui.elements.UITextEntryLine(
            pygame.Rect(right_x, y + 425, right_width - png_button_width - 8, 34),
            self.manager,
            placeholder_text="Optional custom PNG",
        )
        self.png_entry.set_text(Config.square_core_image_path)
        self.png_button = pgui.elements.UIButton(
            pygame.Rect(right_x + right_width - png_button_width, y + 425, png_button_width, 34),
            "Select PNG",
            self.manager,
        )
        self.reset_button = pgui.elements.UIButton(
            pygame.Rect(right_x, y + 475, right_width, 36), "Reset visual defaults", self.manager
        )

        self.preset_rect = pygame.Rect(x, y + 320, left_width, 34)
        self.preset_dropdown = None
        self._rebuild_preset_dropdown("Classic Diamond")
        self.preset_name = pgui.elements.UITextEntryLine(
            pygame.Rect(x, y + 365, left_width, 34), self.manager, placeholder_text="New preset name"
        )
        preset_half = (left_width - 8) // 2
        self.load_button = pgui.elements.UIButton(
            pygame.Rect(x, y + 410, preset_half, 34), "Load", self.manager
        )
        self.save_button = pgui.elements.UIButton(
            pygame.Rect(x + preset_half + 8, y + 410, preset_half, 34), "Save", self.manager
        )
        self.import_button = pgui.elements.UIButton(
            pygame.Rect(x, y + 455, preset_half, 34), "Import JSON", self.manager
        )
        self.export_button = pgui.elements.UIButton(
            pygame.Rect(x + preset_half + 8, y + 455, preset_half, 34), "Export JSON", self.manager
        )
        status_y = y + 510
        self.status_box = pgui.elements.UITextBox(
            "Changes preview and apply immediately. The outer hitbox never changes.",
            pygame.Rect(x, status_y, panel_width - 40, max(55, self.panel_rect.bottom - status_y - 18)),
            self.manager,
        )
        self.right_x = right_x
        self.labels = [
            ("SQUARE CUSTOMIZER", (x, y + 20), 30),
            ("Live preview", (x, y + 58), 17),
            ("Inner shape", (right_x, y + 63), 17),
            ("Core colors", (right_x, y + 118), 17),
            ("Preset library", (x, y + 298), 17),
        ]

    @staticmethod
    def _color_label(prefix: str, value) -> str:
        return f"{prefix}: {'Accent' if value == 'accent' else str(value).upper()}"

    def _preset_names(self):
        return [*BUILTIN_PRESETS.keys(), *user_presets().keys()]

    def _rebuild_preset_dropdown(self, selected: str):
        if self.preset_dropdown is not None:
            self.preset_dropdown.kill()
        options = self._preset_names()
        if selected not in options:
            selected = options[0]
        self.preset_dropdown = pgui.elements.UIDropDownMenu(
            options,
            selected,
            self.preset_rect,
            self.manager,
        )

    def _set_dropdown(self, element, value: str):
        element.selected_option = value
        element.current_state.finish()
        element.current_state.selected_option = value
        element.current_state.start()

    def _set_status(self, message: str, error: bool = False):
        color = "#ff7b72" if error else "#dce6ef"
        self.status_box.set_text(f'<font color="{color}">{escape(message)}</font>')

    @staticmethod
    def _dialog(mode: str) -> str:
        if Tk is None or filedialog is None:
            return ""
        root = None
        try:
            root = Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if mode == "png":
                return filedialog.askopenfilename(title="Select core PNG", filetypes=[("PNG", "*.png")])
            if mode == "import":
                return filedialog.askopenfilename(title="Import square preset", filetypes=[("JSON", "*.json")])
            return filedialog.asksaveasfilename(
                title="Export square preset",
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
            )
        except Exception:
            return ""
        finally:
            if root is not None:
                root.destroy()

    def _open_color_picker(self, target: str):
        raw = getattr(Config, target)
        initial = self.preview_square.accent_color() if raw == "accent" else pygame.Color(raw)
        width = min(420, Config.SCREEN_WIDTH - 30)
        height = min(460, Config.SCREEN_HEIGHT - 30)
        rect = pygame.Rect(0, 0, width, height)
        rect.center = (Config.SCREEN_WIDTH // 2, Config.SCREEN_HEIGHT // 2)
        self.color_target = target
        self.color_dialog = pgui.windows.UIColourPickerDialog(
            rect,
            self.manager,
            initial_colour=initial,
            window_title="Choose core color",
            always_on_top=True,
        )

    def _sync_controls(self):
        self._set_dropdown(self.shape_dropdown, Config.square_core_shape)
        self.fill_button.set_text(self._color_label("Fill", Config.square_core_color))
        self.outline_button.set_text(self._color_label("Outline", Config.square_core_outline_color))
        self.scale_slider.set_current_value(Config.square_core_scale)
        self.outline_slider.set_current_value(Config.square_core_outline_width)
        self.rotation_slider.set_current_value(Config.square_core_rotation_speed)
        self.pulse_slider.set_current_value(Config.square_core_pulse_strength)
        self.png_entry.set_text(Config.square_core_image_path)

    def _load_selected_preset(self):
        try:
            selected = self.preset_dropdown.selected_option
            result = load_preset(selected)
            self._sync_controls()
            warning = " | ".join(result.warnings)
            self._set_status(f"Loaded {selected}" + (f": {warning}" if warning else ""))
        except SquareStyleError as exc:
            self._set_status(str(exc), error=True)

    def _save_preset(self):
        try:
            name = self.preset_name.get_text().strip()
            path = save_user_preset(name)
            self._rebuild_preset_dropdown(path.stem)
            self._set_status(f"Saved local preset: {path}")
        except SquareStyleError as exc:
            self._set_status(str(exc), error=True)

    def _select_png(self):
        selected = self._dialog("png")
        if not selected:
            return
        try:
            installed = install_custom_png(Path(selected))
            Config.square_core_image_path = str(installed)
            Config.square_core_shape = "custom"
            self._sync_controls()
            self._set_status(f"Installed custom PNG: {installed}")
        except SquareStyleError as exc:
            self._set_status(str(exc), error=True)

    def handle_event(self, event: pygame.event.Event):
        if not self.active:
            return None
        if event.type == pygame.DROPFILE and Path(event.file).suffix.lower() == ".png":
            try:
                installed = install_custom_png(Path(event.file))
                Config.square_core_image_path = str(installed)
                Config.square_core_shape = "custom"
                self._sync_controls()
                self._set_status(f"Installed custom PNG: {installed}")
            except SquareStyleError as exc:
                self._set_status(str(exc), error=True)
        if event.type == pgui.UI_DROP_DOWN_MENU_CHANGED and event.ui_element == self.shape_dropdown:
            Config.square_core_shape = event.text
        if event.type == pgui.UI_HORIZONTAL_SLIDER_MOVED:
            if event.ui_element == self.scale_slider:
                Config.square_core_scale = round(float(event.value), 2)
            elif event.ui_element == self.outline_slider:
                Config.square_core_outline_width = int(round(event.value))
            elif event.ui_element == self.rotation_slider:
                Config.square_core_rotation_speed = int(round(event.value))
            elif event.ui_element == self.pulse_slider:
                Config.square_core_pulse_strength = round(float(event.value), 2)
        if event.type == pgui.UI_COLOUR_PICKER_COLOUR_PICKED and event.ui_element == self.color_dialog:
            color = event.colour
            setattr(Config, self.color_target, f"#{color.r:02X}{color.g:02X}{color.b:02X}")
            self.color_dialog = None
            self.color_target = None
            self._sync_controls()
        if event.type == pgui.UI_BUTTON_PRESSED:
            if event.ui_element == self.back_button:
                save_to_file()
                return "back"
            if event.ui_element == self.fill_button:
                self._open_color_picker("square_core_color")
            elif event.ui_element == self.outline_button:
                self._open_color_picker("square_core_outline_color")
            elif event.ui_element == self.png_button:
                self._select_png()
            elif event.ui_element == self.reset_button:
                apply_style(DEFAULT_STYLE)
                self._sync_controls()
                self._set_status("Square visuals reset to defaults.")
            elif event.ui_element == self.load_button:
                self._load_selected_preset()
            elif event.ui_element == self.save_button:
                self._save_preset()
            elif event.ui_element == self.import_button:
                selected = self._dialog("import")
                if selected:
                    try:
                        result = import_style_json(Path(selected))
                        self._sync_controls()
                        self._set_status("Imported preset JSON." + (" " + " | ".join(result.warnings) if result.warnings else ""))
                    except SquareStyleError as exc:
                        self._set_status(str(exc), error=True)
            elif event.ui_element == self.export_button:
                selected = self._dialog("export")
                if selected:
                    try:
                        path = export_style_json(Path(selected))
                        self._set_status(f"Exported preset: {path}")
                    except SquareStyleError as exc:
                        self._set_status(str(exc), error=True)
        self.manager.process_events(event)
        return None

    def draw(self, screen: pygame.Surface):
        if not self.active:
            return
        panel = pygame.Surface(self.panel_rect.size, pygame.SRCALPHA)
        panel.fill((8, 11, 17, 238))
        pygame.draw.rect(panel, (80, 105, 130), panel.get_rect(), width=2, border_radius=8)
        screen.blit(panel, self.panel_rect)
        pygame.draw.rect(screen, (20, 25, 34), self.preview_rect, border_radius=12)
        pygame.draw.rect(screen, (100, 115, 135), self.preview_rect, width=2, border_radius=12)
        if pygame.time.get_ticks() - self.preview_square.time_since_glow_start > 1200:
            self.preview_square.start_bounce()
        square_size = min(100, int(self.preview_rect.width * 0.55))
        square_rect = pygame.Rect(0, 0, square_size, square_size)
        square_rect.center = self.preview_rect.center
        self.preview_square.draw(screen, square_rect)
        pygame.draw.rect(screen, (255, 255, 255, 90), square_rect, width=1)
        for text, position, size in self.labels:
            screen.blit(get_font(size).render(text, True, (225, 231, 240)), position)
        values = [
            (f"Core size: {Config.square_core_scale * 100:.0f}%", self.scale_slider.relative_rect.top - 20),
            (f"Outline: {Config.square_core_outline_width}px", self.outline_slider.relative_rect.top - 20),
            (f"Rotation: {Config.square_core_rotation_speed:+d} deg/s", self.rotation_slider.relative_rect.top - 20),
            (f"Bounce pulse: {Config.square_core_pulse_strength * 100:.0f}%", self.pulse_slider.relative_rect.top - 20),
        ]
        for text, value_y in values:
            screen.blit(get_font(16).render(text, True, (225, 231, 240)), (self.right_x, value_y))
        self.manager.update(max(float(Config.dt), 1 / 240))
        self.manager.draw_ui(screen)
