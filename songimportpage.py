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

from config import Config
from songimporter import (
    AUDIO_EXTENSIONS,
    MIDI_EXTENSIONS,
    DuplicateSongError,
    SongImportError,
    SongImportRequest,
    analyze_song_import,
    create_song_pack,
    find_duplicate_song,
)
from utils import get_font


class SongImportPage:
    """Local-only MP3 + MIDI pack builder with drag-and-drop support."""

    def __init__(self):
        self.active = False
        self.previewing = False
        self.created_path: Optional[Path] = None
        self.manager = pgui.UIManager((Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT))
        panel_width = min(1000, Config.SCREEN_WIDTH - 30)
        panel_height = min(760, Config.SCREEN_HEIGHT - 30)
        self.panel_rect = pygame.Rect(0, 0, panel_width, panel_height)
        self.panel_rect.center = (Config.SCREEN_WIDTH // 2, Config.SCREEN_HEIGHT // 2)
        x = self.panel_rect.x + 20
        y = self.panel_rect.y
        content_width = panel_width - 40
        path_button_width = min(130, max(95, content_width // 4))
        path_width = content_width - path_button_width - 10
        half_width = (content_width - 16) // 2

        self.back_button = pgui.elements.UIButton(
            pygame.Rect(self.panel_rect.right - 110, y + 16, 90, 32), "Back", self.manager
        )
        self.audio_entry = pgui.elements.UITextEntryLine(
            pygame.Rect(x, y + 95, path_width, 34), self.manager,
            placeholder_text="Drop or select an MP3/WAV/OGG file",
        )
        self.audio_button = pgui.elements.UIButton(
            pygame.Rect(x + path_width + 10, y + 95, path_button_width, 34), "Select audio", self.manager
        )
        self.midi_entry = pgui.elements.UITextEntryLine(
            pygame.Rect(x, y + 150, path_width, 34), self.manager,
            placeholder_text="Drop or select a MIDI file",
        )
        self.midi_button = pgui.elements.UIButton(
            pygame.Rect(x + path_width + 10, y + 150, path_button_width, 34), "Select MIDI", self.manager
        )

        self.title_entry = pgui.elements.UITextEntryLine(
            pygame.Rect(x, y + 225, half_width, 34), self.manager, placeholder_text="Song title"
        )
        self.artist_entry = pgui.elements.UITextEntryLine(
            pygame.Rect(x + half_width + 16, y + 225, half_width, 34), self.manager,
            placeholder_text="Artist / composer",
        )
        self.mapper_entry = pgui.elements.UITextEntryLine(
            pygame.Rect(x, y + 295, half_width, 34), self.manager, placeholder_text="Mapper / importer"
        )
        self.mapper_entry.set_text("Local Importer")
        self.source_entry = pgui.elements.UITextEntryLine(
            pygame.Rect(x + half_width + 16, y + 295, half_width, 34), self.manager,
            placeholder_text="Source or rights note (optional)",
        )

        self.offset_slider = pgui.elements.UIHorizontalSlider(
            pygame.Rect(x, y + 365, content_width, 30),
            start_value=0,
            value_range=(-5000, 5000),
            manager=self.manager,
        )
        button_width = max(100, (content_width - 30) // 4)
        self.preview_button = pgui.elements.UIButton(
            pygame.Rect(x, y + 420, button_width, 38), "Preview", self.manager
        )
        self.stop_button = pgui.elements.UIButton(
            pygame.Rect(x + button_width + 10, y + 420, button_width, 38), "Stop", self.manager
        )
        self.validate_button = pgui.elements.UIButton(
            pygame.Rect(x + (button_width + 10) * 2, y + 420, button_width, 38), "Validate", self.manager
        )
        self.create_button = pgui.elements.UIButton(
            pygame.Rect(x + (button_width + 10) * 3, y + 420, button_width, 38), "Create local ZIP", self.manager
        )
        status_height = max(60, self.panel_rect.bottom - (y + 480) - 18)
        self.status_box = pgui.elements.UITextBox(
            "Drop one audio file and one MIDI file to begin.",
            pygame.Rect(x, y + 480, content_width, status_height),
            self.manager,
        )

        self.labels = [
            ("LOCAL SONG IMPORTER", (x, y + 20), 30),
            ("Files are validated and written only to songs-local/.", (x, y + 58), 17),
            ("Audio", (x, y + 75), 16),
            ("MIDI map", (x, y + 130), 16),
            ("Title", (x, y + 205), 16),
            ("Artist / composer", (x + half_width + 16, y + 205), 16),
            ("Mapper / importer", (x, y + 275), 16),
            ("Source / rights note", (x + half_width + 16, y + 275), 16),
        ]
        self.offset_label_position = (x, y + 342)

    @property
    def music_offset(self) -> int:
        return int(round(self.offset_slider.get_current_value()))

    def _set_status(self, message: str, error: bool = False):
        color = "#ff7b72" if error else "#dce6ef"
        self.status_box.set_text(f'<font color="{color}">{escape(message)}</font>')

    def _request(self) -> SongImportRequest:
        return SongImportRequest(
            audio_path=Path(self.audio_entry.get_text().strip()),
            midi_path=Path(self.midi_entry.get_text().strip()),
            title=self.title_entry.get_text(),
            artist=self.artist_entry.get_text(),
            mapper=self.mapper_entry.get_text(),
            source=self.source_entry.get_text(),
            music_offset=self.music_offset,
        )

    def _set_file(self, path_text: str):
        path = Path(path_text.strip().strip('"'))
        extension = path.suffix.lower()
        if extension in AUDIO_EXTENSIONS:
            self.audio_entry.set_text(str(path))
            if not self.title_entry.get_text().strip():
                self.title_entry.set_text(path.stem.replace("_", " ").replace("-", " ").title())
            self._set_status(f"Audio selected: {path.name}")
        elif extension in MIDI_EXTENSIONS:
            self.midi_entry.set_text(str(path))
            self._set_status(f"MIDI selected: {path.name}")
        else:
            self._set_status("Unsupported file. Use MP3/WAV/OGG audio or MID/MIDI map files.", error=True)

    @staticmethod
    def _choose_file(kind: str) -> str:
        if Tk is None or filedialog is None:
            return ""
        try:
            root = Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if kind == "audio":
                filetypes = [("Audio", "*.mp3 *.wav *.ogg"), ("All files", "*.*")]
            else:
                filetypes = [("MIDI", "*.mid *.midi"), ("All files", "*.*")]
            selected = filedialog.askopenfilename(title=f"Select {kind} file", filetypes=filetypes)
            root.destroy()
            return selected
        except Exception:
            return ""

    def _preview(self):
        path = Path(self.audio_entry.get_text().strip())
        if not path.is_file():
            self._set_status("Select a valid audio file before previewing.", error=True)
            return
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(Config.volume / 100)
            pygame.mixer.music.play()
            self.previewing = True
            self._set_status(f"Previewing {path.name}. Offset {self.music_offset:+d} ms will be saved in the pack.")
        except pygame.error as exc:
            self._set_status(f"Audio preview failed: {exc}", error=True)

    def stop_preview(self, resume_menu: bool = False):
        if self.previewing:
            pygame.mixer.music.stop()
        self.previewing = False
        if resume_menu:
            try:
                pygame.mixer.music.load("./assets/mainmenu.mp3")
                pygame.mixer.music.set_volume(Config.volume / 100)
                pygame.mixer.music.play(loops=-1, start=2)
            except pygame.error:
                pass

    def _validate(self):
        try:
            request = self._request()
            analysis = analyze_song_import(request, Config.bounce_min_spacing)
            duplicate = find_duplicate_song(analysis.fingerprint)
            lines = [
                f"Valid files | notes {analysis.note_count} | playable {analysis.playable_note_count}",
                f"map {analysis.duration_seconds:.1f}s | average {analysis.average_notes_per_second:.1f}/s | peak {analysis.peak_notes_per_second}/s",
            ]
            if duplicate:
                lines.append(f"DUPLICATE: {duplicate}")
            lines.extend(f"Warning: {warning}" for warning in analysis.warnings)
            self._set_status("\n".join(lines), error=duplicate is not None)
        except SongImportError as exc:
            self._set_status(str(exc), error=True)

    def _create(self) -> Optional[Path]:
        try:
            result = create_song_pack(
                self._request(),
                output_directory=Path("songs-local"),
                bounce_spacing_ms=Config.bounce_min_spacing,
            )
            self.created_path = result.output_path
            self.stop_preview(resume_menu=True)
            self._set_status(
                f"Created {result.output_path}\n"
                f"{result.analysis.playable_note_count} playable notes, peak {result.analysis.peak_notes_per_second}/s\n"
                "The pack is local-only and excluded from GitHub."
            )
            return result.output_path
        except DuplicateSongError as exc:
            self._set_status(f"Duplicate not created: {exc.duplicate_path}", error=True)
        except SongImportError as exc:
            self._set_status(str(exc), error=True)
        return None

    def handle_event(self, event: pygame.event.Event):
        if not self.active:
            return None
        if event.type == pygame.DROPFILE:
            self._set_file(event.file)
        if event.type == pgui.UI_BUTTON_PRESSED:
            if event.ui_element == self.back_button:
                self.stop_preview(resume_menu=True)
                return "back"
            if event.ui_element == self.audio_button:
                selected = self._choose_file("audio")
                if selected:
                    self._set_file(selected)
            elif event.ui_element == self.midi_button:
                selected = self._choose_file("midi")
                if selected:
                    self._set_file(selected)
            elif event.ui_element == self.preview_button:
                self._preview()
            elif event.ui_element == self.stop_button:
                self.stop_preview(resume_menu=True)
                self._set_status("Preview stopped.")
            elif event.ui_element == self.validate_button:
                self._validate()
            elif event.ui_element == self.create_button:
                created = self._create()
                if created:
                    return ("created", created)
        self.manager.process_events(event)
        return None

    def draw(self, screen: pygame.Surface):
        if not self.active:
            return
        panel = pygame.Surface(self.panel_rect.size, pygame.SRCALPHA)
        panel.fill((8, 11, 17, 235))
        pygame.draw.rect(panel, (80, 105, 130), panel.get_rect(), width=2, border_radius=8)
        screen.blit(panel, self.panel_rect)
        for text, position, size in self.labels:
            surface = get_font(size).render(text, True, (225, 231, 240))
            screen.blit(surface, position)
        offset_surface = get_font(16).render(
            f"Music offset: {self.music_offset:+d} ms", True, (225, 231, 240)
        )
        screen.blit(offset_surface, self.offset_label_position)
        self.manager.update(max(float(Config.dt), 1 / 240))
        self.manager.draw_ui(screen)
