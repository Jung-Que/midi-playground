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

## continuous playback and live settings

choosing a song starts a continuous playlist from that song. while the current song is playing, maps and audio
for the next two songs are prepared in the background. audio is queued only after its matching map is ready. a
slow preparation waits safely at the transition, and a broken song is skipped without stopping the playlist.

maps are generated incrementally in a worker process as time chunks. a bounded chunk buffer feeds only the
retention/preload window into the live world; old chunks and offscreen particles are removed. a spatial hash limits
collision and viewport queries. playback timing follows the mixer position when it is reliable and falls back to a
monotonic wall clock. the performance HUD shows FPS, python memory, active/buffered chunks, pegs, particles,
preparation time, transition wait, and measured synchronization drift.

press `F10` during gameplay to open the live settings overlay. use the up/down keys to select a setting and the
left/right keys to change it. colors, bounce effects, particles, glow, camera mode, volume, and map retention can
be changed without stopping playback. square speed, bounce spacing, and direction chance regenerate only the
unplayed portion of the current map, starting from the square's current state.

verified build command: `pyinstaller main.py --noconsole --onedir --clean --hidden-import glcontext --add-data "assets;assets" --add-data "songs;songs"`

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
