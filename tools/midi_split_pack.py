"""Split personal MIDIs into melody / harmony, render the full arrangement as marimba, and build song packs.

Pegs (song_file) come from the melody only; the audio (audio_file) is the whole arrangement played on marimba.

Melody extraction is a port of midi_remake/src/lines.js `topLine`: the highest note at each onset, unless a higher
note of the same track is still ringing (a held melody note over moving inner notes) — then the onset is harmony.
Optionally (`drop_dips`) single notes that fall more than 7 semitones below both neighbours go back to harmony
(midi_remake/songs/karakuri_common.js `melodyOf`).

Usage:
    python tools/midi_split_pack.py                # build every song in SONGS
    python tools/midi_split_pack.py --only karakuri
Outputs: songs-local/<slug>.zip and a work folder (build/midi-split/<slug>/) with melody/harmony/full MIDIs and WAV.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import mido

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = Path.home() / "Desktop"
FLUIDSYNTH = ROOT.parent / "tools-ext" / "fluidsynth" / "fluidsynth-v2.6.3-win10-x64-cpp11" / "bin" / "fluidsynth.exe"
SOUNDFONT = Path(r"C:\Program Files\MuseScore 4\sound\MS Basic.sf3")
MARIMBA = 12  # General MIDI program 13 (0-based 12)
DRUM_CHANNEL = 9
# Melody maps: ornaments (40-60 ms before the main note) fold onto the main note; 16ths at ~155 BPM (90+ ms) stay.
MELODY_MIN_SPACING_MS = 70
# Gap fill: where the melody rests longer than FILL_GAP_S, harmony onsets become pegs (the audio is unchanged).
FILL_GAP_S = 0.6        # melody silence that counts as "nothing happening"
FILL_MARGIN_S = 0.15    # a fill peg keeps this far from the melody notes around the gap
FILL_MIN_SPACING_S = 0.15
FILL_GRID_DIV = 2       # fill pegs at most one per 8th note (beat / 2), on-grid onsets first


@dataclass
class SongDef:
    slug: str
    source: str
    title: str
    artist: str
    melody_tracks: list[int]
    split_top: bool = True       # run topLine over the melody tracks (False: whole track is melody)
    drop_dips: bool = False
    notes: str = ""


SONGS = [
    SongDef("looping-the-rooms", "Looping_the_Rooms_ループザルーム_–_rusino.mid",
            "Looping the Rooms", "rusino", [0], notes="right hand top line"),
    SongDef("trick-heart", "TRICK_HEART_トリックハート_feat_Kasane_Teto_重音テト_–_MIMI-피아노,_Piano.mid",
            "TRICK HEART", "MIMI feat. Kasane Teto", [0], notes="right hand only; top line vs inner chord notes"),
    SongDef("karakuri-pierrot", "karakuri-current-backup.mid",
            "Karakuri Pierrot", "40mP", [0], drop_dips=True, notes="marimba RH top line minus dips"),
    SongDef("brainrot", "mabinogi_separate_parts.mid",
            "Brainrot", "Tokyo Makika", [0], notes="track 0 = theme (marimba 1); tracks 1-2 harmony/bass"),
    # the Desktop copy (世界は可愛く出来ている_..._ZUN-피아노,_钢琴.mid) has the right hand only; this one adds the left
    SongDef("sekai-wa-kawaiku", "midi_remake/source/sekai_zun.mid",
            "Sekai wa Kawaiku Dekiteiru", "ZUN", [0], notes="track 0 right hand top line; track 1 left hand"),
]


@dataclass(eq=False)
class Note:
    start: int
    end: int
    key: int
    vel: int
    channel: int
    track: int


@dataclass
class Parsed:
    midi: mido.MidiFile
    notes: list[Note] = field(default_factory=list)
    meta: list[tuple[int, mido.MetaMessage]] = field(default_factory=list)  # tempo / time & key signatures


def parse(path: Path) -> Parsed:
    midi = mido.MidiFile(path)
    out = Parsed(midi)
    for ti, track in enumerate(midi.tracks):
        tick = 0
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for msg in track:
            tick += msg.time
            if msg.is_meta and msg.type in ("set_tempo", "time_signature", "key_signature"):
                out.meta.append((tick, msg))
            elif msg.type == "note_on" and msg.velocity:
                open_notes.setdefault((msg.channel, msg.note), []).append((tick, msg.velocity))
            elif msg.type in ("note_off", "note_on"):
                stack = open_notes.get((msg.channel, msg.note))
                if stack:
                    start, vel = stack.pop(0)
                    out.notes.append(Note(start, max(tick, start + 1), msg.note, vel, msg.channel, ti))
        for (channel, key), stack in open_notes.items():
            for start, vel in stack:
                out.notes.append(Note(start, start + midi.ticks_per_beat, key, vel, channel, ti))
    out.notes.sort(key=lambda n: (n.start, n.key))
    out.meta.sort(key=lambda item: item[0])
    return out


def top_line(notes: list[Note]) -> tuple[list[Note], list[Note]]:
    groups: list[list[Note]] = []
    for n in sorted(notes, key=lambda n: (n.start, n.key)):
        if groups and groups[-1][0].start == n.start:
            groups[-1].append(n)
        else:
            groups.append([n])
    line, rest, ringing = [], [], []
    for group in groups:
        onset = group[0].start
        ringing = [r for r in ringing if r.end > onset]
        cand = max(group, key=lambda n: n.key)
        covered = any(r.key > cand.key for r in ringing)
        for n in group:
            (line if (not covered and n is cand) else rest).append(n)
        ringing.extend(group)
    return line, rest


def drop_dips(line: list[Note]) -> tuple[list[Note], list[Note]]:
    line = sorted(line, key=lambda n: n.start)
    keep, dips = [], []
    for i, n in enumerate(line):
        is_dip = 0 < i < len(line) - 1 and line[i - 1].key - n.key > 7 and line[i + 1].key - n.key > 7
        (dips if is_dip else keep).append(n)
    return keep, dips


def split(parsed: Parsed, song: SongDef) -> tuple[list[Note], list[Note]]:
    pool = [n for n in parsed.notes if n.track in song.melody_tracks and n.channel != DRUM_CHANNEL]
    others = [n for n in parsed.notes if n not in pool]
    if song.split_top:
        melody, inner = top_line(pool)
    else:
        melody, inner = pool, []
    if song.drop_dips:
        melody, dips = drop_dips(melody)
        inner += dips
    return sorted(melody, key=lambda n: n.start), sorted(inner + others, key=lambda n: (n.start, n.key))


class TempoMap:
    """tick -> seconds with the source's tempo changes (same result as mido's playback timing)."""

    def __init__(self, parsed: Parsed):
        self.tpb = parsed.midi.ticks_per_beat
        self.points = [(0, 0.0, 500_000)]  # (tick, seconds at tick, tempo from tick)
        for tick, msg in parsed.meta:
            if msg.type != "set_tempo":
                continue
            last_tick, last_s, last_tempo = self.points[-1]
            seconds_at = last_s + mido.tick2second(tick - last_tick, self.tpb, last_tempo)
            if tick == last_tick:
                self.points[-1] = (tick, last_s, msg.tempo)
            else:
                self.points.append((tick, seconds_at, msg.tempo))

    def seconds(self, tick: int) -> float:
        point = self.points[0]
        for candidate in self.points:
            if candidate[0] > tick:
                break
            point = candidate
        return point[1] + mido.tick2second(tick - point[0], self.tpb, point[2])

    def beat_seconds(self, tick: int) -> float:
        point = self.points[0]
        for candidate in self.points:
            if candidate[0] > tick:
                break
            point = candidate
        return point[2] / 1_000_000


def fill_gaps(melody: list[Note], harmony: list[Note], tempo: TempoMap) -> list[Note]:
    """Harmony onsets for the places where the melody rests longer than FILL_GAP_S (also before the melody starts
    and after it ends). One note per onset (the top one), kept FILL_MARGIN_S from the melody, at most one per
    1/FILL_GRID_DIV beat; onsets on that grid are taken first. No note is invented: no harmony, no fill."""
    melody_times = sorted({tempo.seconds(n.start) for n in melody})
    tops: dict[int, Note] = {}
    for n in harmony:
        if n.channel == DRUM_CHANNEL:
            continue
        if n.start not in tops or n.key > tops[n.start].key:
            tops[n.start] = n
    if not tops:
        return []
    first_h, last_h = tempo.seconds(min(tops)), tempo.seconds(max(tops))
    bounds = [first_h - FILL_MARGIN_S] + melody_times + [last_h + FILL_MARGIN_S]
    gaps = [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a > FILL_GAP_S]
    grid = tempo.tpb / FILL_GRID_DIV
    picked: list[tuple[float, Note]] = []
    for a, b in gaps:
        inside = [(tempo.seconds(tick), note) for tick, note in tops.items()
                  if a + FILL_MARGIN_S <= tempo.seconds(tick) <= b - FILL_MARGIN_S]
        def off_grid(item):
            phase = item[1].start % grid
            return min(phase, grid - phase) > tempo.tpb / 16
        chosen: list[float] = []
        for t, note in sorted(inside, key=lambda item: (off_grid(item), item[0])):
            spacing = max(FILL_MIN_SPACING_S, tempo.beat_seconds(note.start) / FILL_GRID_DIV * 0.9)
            if all(abs(t - c) >= spacing for c in chosen):
                chosen.append(t)
                picked.append((t, note))
    return [note for _, note in sorted(picked, key=lambda item: item[0])]


def melody_silence(melody_times: list[float], end: float) -> float:
    """Seconds inside melody gaps longer than FILL_GAP_S (the 'nothing happening' time), up to `end`."""
    times = sorted(melody_times)
    return sum(b - a for a, b in zip(times, times[1:] + [end]) if b - a > FILL_GAP_S)


def write_notes_midi(path: Path, parsed: Parsed, notes: list[Note], program: int = MARIMBA, name: str = ""):
    """One conductor track (tempo map) + one note track on channel 0, same ticks_per_beat as the source."""
    out = mido.MidiFile(type=1, ticks_per_beat=parsed.midi.ticks_per_beat)
    conductor = mido.MidiTrack()
    last = 0
    for tick, msg in parsed.meta:
        conductor.append(msg.copy(time=tick - last))
        last = tick
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    out.tracks.append(conductor)

    events = []
    for n in notes:
        events.append((n.start, 1, mido.Message("note_on", channel=0, note=n.key, velocity=n.vel)))
        events.append((n.end, 0, mido.Message("note_off", channel=0, note=n.key, velocity=0)))
    events.sort(key=lambda e: (e[0], e[1]))
    track = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0),
                            mido.Message("program_change", channel=0, program=program, time=0)])
    last = 0
    for tick, _, msg in events:
        track.append(msg.copy(time=tick - last))
        last = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    out.tracks.append(track)
    out.save(path)


def write_full_marimba(path: Path, source: Path):
    """Same file, every melodic channel forced to marimba (program changes rewritten, one added at time 0)."""
    midi = mido.MidiFile(source)
    for track in midi.tracks:
        channels = sorted({m.channel for m in track if m.type == "note_on" and m.channel != DRUM_CHANNEL})
        rewritten = mido.MidiTrack()
        for msg in track:
            if msg.type == "program_change" and msg.channel != DRUM_CHANNEL:
                msg = msg.copy(program=MARIMBA)
            rewritten.append(msg)
        head = [mido.Message("program_change", channel=c, program=MARIMBA, time=0) for c in channels]
        track[:] = head + list(rewritten)
    midi.save(path)


def render_ogg(midi_path: Path, wav_path: Path, ogg_path: Path):
    subprocess.run([str(FLUIDSYNTH), "-ni", "-q", "-g", "0.5", "-r", "44100", "-F", str(wav_path),
                    str(SOUNDFONT), str(midi_path)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
                    # trailing silence (synth release + padding) is cut to 0.5 s so the next song follows promptly;
                    # the start is untouched, the map's timing depends on it
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,"
                           "areverse,silenceremove=start_periods=1:start_threshold=-60dB:start_silence=0.5,areverse",
                    "-ar", "44100",
                    "-c:a", "libvorbis", "-q:a", "6", str(ogg_path)], check=True)


def seconds(midi_path: Path) -> list[float]:
    """Same timing as the game's utils.read_midi_file."""
    t, out = 0.0, []
    for msg in mido.MidiFile(midi_path):
        t += msg.time
        if msg.type == "note_on" and msg.velocity:
            out.append(round(t * 1000) / 1000)
    return out


def build(song: SongDef, out_dir: Path, work_root: Path, fill_enabled: bool = True) -> Path:
    source = DESKTOP / song.source
    work = work_root / song.slug
    work.mkdir(parents=True, exist_ok=True)
    parsed = parse(source)
    melody, harmony = split(parsed, song)
    tempo = TempoMap(parsed)
    fill = fill_gaps(melody, harmony, tempo) if fill_enabled else []
    map_notes = sorted(melody + fill, key=lambda n: (n.start, n.key))

    melody_mid = work / f"{song.slug}-melody.mid"
    map_mid = work / f"{song.slug}-map.mid"
    harmony_mid = work / f"{song.slug}-harmony.mid"
    full_mid = work / f"{song.slug}-full-marimba.mid"
    wav = work / f"{song.slug}.wav"
    ogg = work / f"{song.slug}.ogg"
    write_notes_midi(melody_mid, parsed, melody, name="melody")
    write_notes_midi(map_mid, parsed, map_notes, name="melody + harmony fill")
    write_notes_midi(harmony_mid, parsed, harmony, name="harmony")
    write_full_marimba(full_mid, source)
    render_ogg(full_mid, wav, ogg)

    # Melody onsets must line up with the full arrangement's onsets (same tempo map).
    full_onsets = set(seconds(full_mid))
    misaligned = [t for t in seconds(map_mid) if t not in full_onsets]
    if misaligned:
        raise SystemExit(f"{song.slug}: {len(misaligned)} melody onsets not found in the full MIDI, e.g. {misaligned[:5]}")

    metadata = {
        "name": song.title,
        "artist": song.artist,
        "author": song.artist,
        "mapper": "melody split",
        "source": f"personal MIDI ({song.source}); {song.notes}",
        "audio_file": ogg.name,
        "song_file": map_mid.name,          # pegs: melody + harmony onsets in the melody's long rests
        "melody_file": melody_mid.name,
        "fill_notes": len(fill),
        "harmony_file": harmony_mid.name,
        "version": 2,
        "music_offset": 0,
        "bounce_min_spacing": MELODY_MIN_SPACING_MS,
        "created_by": "tools/midi_split_pack.py",
        "categories": ["marimba"],  # live mode: python main.py --live --category marimba
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    pack = out_dir / f"{song.slug}.zip"
    with ZipFile(pack, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))
        archive.write(ogg, ogg.name)
        archive.write(map_mid, map_mid.name)
        archive.write(melody_mid, melody_mid.name)
        archive.write(harmony_mid, harmony_mid.name)
        archive.write(full_mid, full_mid.name)
    end = max(seconds(full_mid) or [0.0])
    before = melody_silence([tempo.seconds(n.start) for n in melody], end)
    after = melody_silence([tempo.seconds(n.start) for n in map_notes], end)
    print(f"{song.slug:22} melody {len(melody):5}  harmony {len(harmony):5}  fill +{len(fill):4}  "
          f"silent {before:5.1f}s -> {after:5.1f}s  -> {pack.relative_to(ROOT)}")
    return pack


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="slugs to build")
    parser.add_argument("--out", default=str(ROOT / "songs-local"))
    parser.add_argument("--work", default=str(ROOT / "build" / "midi-split"))
    parser.add_argument("--no-fill", action="store_true", help="pegs from the melody only")
    args = parser.parse_args()
    for path in (FLUIDSYNTH, SOUNDFONT):
        if not path.exists():
            sys.exit(f"missing: {path}")
    for song in SONGS:
        if args.only and song.slug not in args.only:
            continue
        build(song, Path(args.out), Path(args.work), fill_enabled=not args.no_fill)


if __name__ == "__main__":
    main()
