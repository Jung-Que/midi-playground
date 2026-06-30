from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import unicodedata
import wave
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

import mido


AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg"}
MIDI_EXTENSIONS = {".mid", ".midi"}
MAX_AUDIO_BYTES = 250 * 1024 * 1024
MAX_MIDI_BYTES = 20 * 1024 * 1024
_PACK_FINGERPRINT_CACHE: dict[Path, tuple[int, int, str | None]] = {}


class SongImportError(Exception):
    pass


class DuplicateSongError(SongImportError):
    def __init__(self, duplicate_path: Path):
        self.duplicate_path = duplicate_path
        super().__init__(f"These audio and MIDI files already exist in {duplicate_path}")


@dataclass(frozen=True)
class SongImportRequest:
    audio_path: Path
    midi_path: Path
    title: str
    artist: str
    mapper: str
    source: str = ""
    music_offset: int = 0


@dataclass(frozen=True)
class SongImportAnalysis:
    note_count: int
    playable_note_count: int
    duration_seconds: float
    average_notes_per_second: float
    peak_notes_per_second: int
    audio_bytes: int
    midi_bytes: int
    fingerprint: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SongImportResult:
    output_path: Path
    analysis: SongImportAnalysis


def _read_limited(path: Path, maximum: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SongImportError(f"Unable to read {label}: {exc}") from exc
    if size <= 0:
        raise SongImportError(f"{label} is empty")
    if size > maximum:
        raise SongImportError(f"{label} is too large ({size / 1024 / 1024:.1f} MB)")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SongImportError(f"Unable to read {label}: {exc}") from exc


def _validate_audio(path: Path, data: bytes):
    extension = path.suffix.lower()
    if extension not in AUDIO_EXTENSIONS:
        raise SongImportError("Audio must be MP3, WAV, or OGG")
    if extension == ".wav":
        try:
            with wave.open(BytesIO(data), "rb") as audio:
                if audio.getnframes() <= 0 or audio.getframerate() <= 0:
                    raise SongImportError("WAV contains no playable audio")
        except (EOFError, wave.Error) as exc:
            raise SongImportError(f"Invalid WAV file: {exc}") from exc
    elif extension == ".ogg" and not data.startswith(b"OggS"):
        raise SongImportError("Invalid OGG header")
    elif extension == ".mp3":
        has_id3 = data.startswith(b"ID3")
        has_frame = any(
            data[index] == 0xFF and data[index + 1] & 0xE0 == 0xE0
            for index in range(min(len(data) - 1, 8192))
        )
        if not has_id3 and not has_frame:
            raise SongImportError("Invalid MP3 header")


def _read_midi_notes(path: Path, data: bytes) -> list[float]:
    if path.suffix.lower() not in MIDI_EXTENSIONS:
        raise SongImportError("Map file must be MIDI (.mid or .midi)")
    try:
        midi = mido.MidiFile(file=BytesIO(data))
        notes = []
        current_time = 0.0
        for message in midi:
            current_time += message.time
            if message.type == "note_on" and message.velocity:
                notes.append(round(current_time, 3))
    except (EOFError, OSError, ValueError, TypeError) as exc:
        raise SongImportError(f"Invalid MIDI file: {exc}") from exc
    if not notes:
        raise SongImportError("MIDI contains no note-on events")
    return notes


def _remove_close_notes(notes: list[float], spacing_ms: float) -> list[float]:
    spacing = max(float(spacing_ms), 0.0) / 1000
    playable = []
    previous = None
    for timestamp in notes:
        if previous is None or timestamp >= previous + spacing:
            playable.append(timestamp)
            previous = timestamp
    return playable


def _peak_density(notes: list[float]) -> int:
    window = deque()
    peak = 0
    for timestamp in notes:
        window.append(timestamp)
        while window and window[0] < timestamp - 1.0:
            window.popleft()
        peak = max(peak, len(window))
    return peak


def import_fingerprint(audio_data: bytes, midi_data: bytes) -> str:
    digest = sha256()
    digest.update(b"midi-playground-import-v1\0")
    digest.update(sha256(audio_data).digest())
    digest.update(sha256(midi_data).digest())
    return digest.hexdigest()


def analyze_song_import(request: SongImportRequest, bounce_spacing_ms: float = 30) -> SongImportAnalysis:
    audio_path = Path(request.audio_path).expanduser().resolve()
    midi_path = Path(request.midi_path).expanduser().resolve()
    audio_data = _read_limited(audio_path, MAX_AUDIO_BYTES, "audio file")
    midi_data = _read_limited(midi_path, MAX_MIDI_BYTES, "MIDI file")
    _validate_audio(audio_path, audio_data)
    notes = _read_midi_notes(midi_path, midi_data)
    playable = _remove_close_notes(notes, bounce_spacing_ms)
    duration = max(notes[-1], 0.001)
    peak = _peak_density(playable)
    warnings = []
    if len(playable) < len(notes) * 0.7:
        warnings.append("Many MIDI notes are closer than the current bounce spacing")
    if peak >= 20:
        warnings.append(f"Very dense section detected ({peak} playable notes/s)")
    if duration < 5:
        warnings.append("The MIDI map is shorter than five seconds")
    return SongImportAnalysis(
        note_count=len(notes),
        playable_note_count=len(playable),
        duration_seconds=duration,
        average_notes_per_second=len(playable) / duration,
        peak_notes_per_second=peak,
        audio_bytes=len(audio_data),
        midi_bytes=len(midi_data),
        fingerprint=import_fingerprint(audio_data, midi_data),
        warnings=tuple(warnings),
    )


def _fingerprint_from_pack(path: Path) -> str | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    cache_key = path.resolve()
    cached = _PACK_FINGERPRINT_CACHE.get(cache_key)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    try:
        with ZipFile(path) as archive:
            metadata = json.loads(archive.read("metadata.json"))
            stored = metadata.get("import_fingerprint")
            if stored:
                fingerprint = str(stored)
            else:
                audio_data = archive.read(metadata["audio_file"])
                midi_data = archive.read(metadata["song_file"])
                fingerprint = import_fingerprint(audio_data, midi_data)
    except (BadZipFile, KeyError, OSError, RuntimeError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        fingerprint = None
    _PACK_FINGERPRINT_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, fingerprint)
    return fingerprint


def find_duplicate_song(
        fingerprint: str,
        directories: tuple[Path, ...] = (Path("songs-local"), Path("songs")),
) -> Path | None:
    for directory in directories:
        if not directory.is_dir():
            continue
        for candidate in directory.iterdir():
            if candidate.is_file() and candidate.suffix.lower() in {".zip", ".midiplayground"}:
                if _fingerprint_from_pack(candidate) == fingerprint:
                    return candidate
    return None


def safe_song_slug(title: str, fingerprint: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug[:64] or f"song-{fingerprint[:8]}"


def create_song_pack(
        request: SongImportRequest,
        output_directory: Path = Path("songs-local"),
        bounce_spacing_ms: float = 30,
        duplicate_directories: tuple[Path, ...] | None = None,
) -> SongImportResult:
    title = request.title.strip()
    artist = request.artist.strip()
    mapper = request.mapper.strip()
    if not title:
        raise SongImportError("Song title is required")
    if not artist:
        raise SongImportError("Artist is required")
    if not mapper:
        raise SongImportError("Mapper/importer name is required")

    analysis = analyze_song_import(request, bounce_spacing_ms)
    directories = duplicate_directories or (output_directory, Path("songs"))
    duplicate = find_duplicate_song(analysis.fingerprint, tuple(Path(item) for item in directories))
    if duplicate is not None:
        raise DuplicateSongError(duplicate)

    audio_path = Path(request.audio_path).expanduser().resolve()
    midi_path = Path(request.midi_path).expanduser().resolve()
    slug = safe_song_slug(title, analysis.fingerprint)
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"{slug}.zip"
    if output_path.exists():
        raise SongImportError(f"A different song pack already uses {output_path.name}")

    audio_name = f"{slug}{audio_path.suffix.lower()}"
    midi_name = f"{slug}{midi_path.suffix.lower()}"
    metadata = {
        "name": title,
        "artist": artist,
        "author": artist,
        "mapper": mapper,
        "source": request.source.strip(),
        "audio_file": audio_name,
        "song_file": midi_name,
        "version": 2,
        "music_offset": int(request.music_offset),
        "import_fingerprint": analysis.fingerprint,
        "created_by": "midi-playground local importer",
    }

    temporary_path = None
    try:
        with NamedTemporaryFile(
                mode="wb",
                prefix=f".{slug}-",
                suffix=".tmp",
                dir=output_directory,
                delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))
            archive.write(audio_path, audio_name)
            archive.write(midi_path, midi_name)
        temporary_path.replace(output_path)
    except OSError as exc:
        raise SongImportError(f"Unable to create song pack: {exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)

    return SongImportResult(output_path=output_path, analysis=analysis)
