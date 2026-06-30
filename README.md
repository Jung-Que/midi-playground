# midi playground
bouncing square video, FOSS edition (and gamified)

## NOTICES

### for content creators:

please try to put the link to this repository in your youtube video descriptions if the youtube video features this software, that is all i request

### for developers:

***this code is licensed under GPL3, it is illegal to publicly distribute modified copies of this software without providing the source upon request!***

it is ok, however, to modify the code and not release the source if you are not releasing the modified version to the public.

## how to do custom songs?

see [docs/SONGS.md](https://github.com/quasar098/midi-playground/blob/master/docs/SONGS.md) for custom song tutorial

## development guide

this is how you set up the code to run it from source, rather than a bundled pyinstaller executable

download python from [here](https://python.org) specifically (3.9.1 should work). do not download from windows store. that version is really janky and doesn't work that well for more complex python programs with lots of dependencies

install requirements with `python3 -m pip install -r requirements.txt`

start program with `python3 main.py`

private or rights-unverified song packs can be placed in `songs-local/`. they appear first in the song selector with
a `[LOCAL ONLY]` label, but `songs-local/`, `imports-local/`, and `exports/` are ignored by git and omitted from the
verified package build. only redistribution-cleared packs belong in the tracked `songs/` directory.

the main menu's `Import Local Song` tool builds these packs without manual ZIP editing. select or drag one
MP3/WAV/OGG file and one MIDI map, enter title/artist/mapper/source, preview the audio, adjust the saved timing
offset, and validate note density before creation. SHA-256 content fingerprints prevent duplicate local or public
packs, and successful imports appear immediately in the song selector.

`Customize Square` opens a dedicated live editor for the fixed outer square's inner symbol. it includes built-in
shape presets, colour pickers, size/outline/rotation/bounce-pulse controls, transparent custom PNG installation,
local preset saving, and JSON import/export. custom images and presets are copied under `presets-local/`, which is
ignored by git. invalid or missing image files fall back to the diamond symbol without changing the hitbox.

invalid or outdated values in `assets/settings.json` are clamped or restored during startup. packaged Windows
runs keep settings, local songs, and square presets under `%LOCALAPPDATA%/MidiPlayground`, separate from bundled
public resources. source runs write
rotating diagnostics to `logs/midi-playground.log`; packaged Windows runs use
`%LOCALAPPDATA%/MidiPlayground/logs/midi-playground.log` so crashes remain diagnosable without a console.

## continuous playback and live settings

choosing a song starts a continuous playlist from that song. while the current song is playing, maps and audio
for the next two songs are prepared in the background. audio is queued only after its matching map is ready. a
slow preparation waits safely at the transition, and a broken song is skipped without stopping the playlist.

maps are generated incrementally in a worker process as time chunks. chunks are transport units only: their pegs
and safe areas enter the live world one bounce at a time and fade in briefly instead of appearing as a block. old
records are removed individually after they leave the expanded viewport, while visible history is preserved. a
spatial hash limits collision and viewport queries. playback timing follows the mixer position when it is reliable and falls back to a
monotonic wall clock. the performance HUD shows FPS, python memory, active/buffered chunks, pegs, particles,
preparation time, transition wait, and measured synchronization drift.
initial and streamed bounces share one schedule offset so chunk boundaries cannot reorder pending collisions.

map generation remains 30 seconds ahead, but rendering is density-aware: only the next three to six readable pegs
are shown. overlapping later markers are suppressed without dropping their notes, the immediate target is outlined
and connected by a guide line, and at most three recent pegs fade out over 1.5 seconds behind the square.
the first three visible targets are numbered, the immediate target gets a music-timed countdown ring, and each hit
briefly compresses its peg and emits an expanding confirmation ring. the TargetLead camera frames the square and
next peg together with capped look-ahead and frame-rate-independent smoothing.

the square uses a layered neon core with directional edge lighting and a short collision-face flash. movement trail
particles are replaced by four time-based afterimages. bounce particles use delta-time-independent motion, directional
theme colors, adaptive counts for dense songs, alpha fade, and a global active-particle budget. glow surfaces are
quantized and cached instead of rebuilding the OpenCV bloom on every frame.

press `F10` during gameplay to open the live settings overlay. use the up/down keys to select a setting and the
left/right keys to change it. colors, bounce effects, particles, glow, camera mode, volume, map retention, peg count,
peg spacing, target guide, and fades can be changed without stopping playback. the square's fixed outer body can use
diamond, heart, star, circle, note, bolt, cross, or empty inner symbols with independent fill, outline, size, rotation,
and bounce-pulse settings. these visual options never change the square hitbox or map physics. square speed, bounce spacing, and direction chance regenerate only the
unplayed portion of the current map, starting from the square's current state.

shorts mode is available from the same `F10` overlay. choose a vertical resolution such as `540x960`, `720x1280`,
or `1080x1920` on the config page and restart. shorts mode keeps the square and immediate target inside configurable
recording-safe margins with dynamic camera zoom, can hide gameplay/debug UI, shows a recording countdown and song
title, and plays a repeatable 15, 30, or 60 second segment from a five-second-adjustable start point. segment and
mode changes apply when the next song starts; clean UI, title, countdown, duration, and repeat can be changed live.

## verified Windows release

run `python tools/build_release.py` from the repository root. the release builder runs all unit tests, copies only
the public files allow-listed in `packaging/release-manifest.txt`, builds the Windows application, launches the
packaged streaming/shorts/import/customizer smoke suite from outside its install directory, rejects private data,
and writes a versioned ZIP plus SHA-256 file under `exports/releases/`.

when adding a redistribution-cleared built-in song or asset, add its path to the release manifest. files under
`songs-local/`, `imports-local/`, `presets-local/`, `exports/`, logs, and user settings are never release inputs.
see [docs/RELEASE.md](docs/RELEASE.md) for the complete checklist.

## credits

see [docs/CREDITS.md](https://github.com/quasar098/midi-playground/blob/master/docs/CREDITS.md)

## contributors

- [quasar098](https://github.com/quasar098)
- [TheCodingCrafter](https://github.com/TheCodingCrafter) - Themes + QOL
- [PurpleJuiceBox](https://github.com/PurpleJuiceBox) - Reset to Default Button
- [sled45](https://github.com/sled45) - Mouse fix for high DPI displays
- [Times0](https://github.com/Times0) - dark_modern theme, Glowing, Colored pegs on bounce
- [Spring-Forever-with-me](https://github.com/Spring-Forever-with-me) - fix incorrect key name for screen resolution in the config
- [sj-dan](https://github.com/sj-dan) - opengl fix on mac os

- [cangerjun](https://github.com/cangerjun) - chinese translations
- [lucmsilva651](https://github.com/lucmsilva651) - brazilian portuguese and spanish translations
- [leo539](https://github.com/leo539) - french translations
- [simpansoftware](https://github.com/simpansoftware) - swedish translations
- [slideglide](https://github.com/slideglide) - turkish translations
- [Guavvva](https://github.com/Guavvva) - russian translations

## translation guide

want to add translations for a different language? please create a github issue with the word "translations" in the title

if so, please add translations for as many of the texts (they are listed below) as you can

- "play"
- "config"
- "contribute"
- "open songs folder"
- "quit"
- "back"
- "midi-playground" text (this is the title of the software)
- the marquee on the title screen (the moving text that appears underneath the title on the main screen; see translations.py file for english example)
- "restart required"

if you have any questions on what any texts are supposed to mean, see translations.py for the english examples before you make a github issue

currently, this game can be played in english, chinese, russian, brazillian portuguese, spanish, french, turkish, and swedish.

also, we are only adding real languages (no pirate speak or upside-down language like minecraft)

## (old) todo list

see [docs/TODO.md](https://github.com/quasar098/midi-playground/blob/master/docs/TODO.md)
