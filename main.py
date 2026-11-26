from os import chdir
from pathlib import Path
import sys
import subprocess


def set_resource_root():
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    chdir(resource_root)


# Config is imported by most UI modules and loads settings immediately. Set the
# resource root before those imports so packaged launches never depend on the
# caller's working directory.
set_resource_root()

from diagnostics import install_exception_logging

install_exception_logging()

from utils import *
from menu import Menu
from game import Game
from configpage import ConfigPage
from songselector import SongSelector, make_song_from_zip
from songimportpage import SongImportPage
from squarecustomizerpage import SquareCustomizerPage
from errorscreen import ErrorScreen
from liveconfig import LiveConfigOverlay
from platform import system as get_os
from config import save_to_file
from paths import user_path
import debuginfo
import webbrowser
import pygame
from array import array
from time import monotonic, sleep
from version import __version__

def run_stream_smoke_test() -> int:
    """Exercise packaged multiprocessing map streaming without opening the full UI."""
    set_resource_root()
    pygame.init()
    screen = pygame.display.set_mode((64, 64))
    Config.screen = screen
    previous = (Config.max_notes, Config.map_chunk_seconds, Config.map_stream_buffer_chunks)
    Config.max_notes = 64
    Config.map_chunk_seconds = 1
    Config.map_stream_buffer_chunks = 2
    game = Game()
    game.active = True
    try:
        song = make_song_from_zip("songs/tetris.zip")
        error = game.start_playlist([song], 0, screen)
        if error:
            raise RuntimeError(error)
        slot = game.active_map_stream
        deadline = monotonic() + 30
        chunks_seen = 1
        while not game.playlist.stream_drained(slot) and monotonic() < deadline:
            chunks_seen += len(game.playlist.claim_chunks_until(slot, float("inf")))
            sleep(0.01)
        chunks_seen += len(game.playlist.claim_chunks_until(slot, float("inf")))
        if not game.playlist.stream_drained(slot) or chunks_seen < 2:
            raise RuntimeError("Packaged map stream did not complete")
        return 0
    finally:
        game.shutdown()
        Config.max_notes, Config.map_chunk_seconds, Config.map_stream_buffer_chunks = previous
        pygame.quit()


def run_shorts_smoke_test() -> int:
    """Exercise vertical camera safety, MP3 seeking, and segment replay in a packaged build."""
    set_resource_root()
    pygame.init()
    screen = pygame.display.set_mode((540, 960))
    Config.screen = screen
    names = (
        "SCREEN_WIDTH", "SCREEN_HEIGHT", "shorts_mode", "shorts_segment_start",
        "shorts_segment_duration", "shorts_loop", "max_notes", "start_playing_delay",
    )
    previous = {name: getattr(Config, name) for name in names}
    Config.SCREEN_WIDTH = 540
    Config.SCREEN_HEIGHT = 960
    Config.shorts_mode = True
    Config.shorts_segment_start = 1
    Config.shorts_segment_duration = 15
    Config.shorts_loop = True
    Config.max_notes = 64
    Config.start_playing_delay = 0
    game = Game()
    game.active = True
    try:
        song = make_song_from_zip("songs/calm_down.zip")
        error = game.start_playlist([song], 0, screen)
        if error:
            raise RuntimeError(error)
        if not game.shorts_session_active or game.auto_advance:
            raise RuntimeError("Shorts session did not activate cleanly")
        target = game.world.future_bounces[0].square_pos if game.world.future_bounces else game.world.square.pos
        game.camera.follow(game.world.square, target)
        margin_x = round(Config.SCREEN_WIDTH * Config.shorts_safe_margin_x)
        margin_y = round(Config.SCREEN_HEIGHT * Config.shorts_safe_margin_y)
        safe = pygame.Rect(
            margin_x,
            margin_y,
            Config.SCREEN_WIDTH - margin_x * 2,
            Config.SCREEN_HEIGHT - margin_y * 2,
        )
        if not safe.contains(game.camera.offset(game.world.square.rect)):
            raise RuntimeError("Square left the vertical recording-safe area")
        if not game._restart_short_segment(screen):
            raise RuntimeError(game.stream_message or "Segment replay failed")
        return 0
    finally:
        game.shutdown()
        for name, value in previous.items():
            setattr(Config, name, value)
        pygame.quit()


def run_import_smoke_test() -> int:
    """Build and reload a local song pack entirely inside a temporary directory."""
    set_resource_root()
    from tempfile import TemporaryDirectory
    from zipfile import ZipFile
    from songimporter import DuplicateSongError, SongImportRequest, create_song_pack

    source = make_song_from_zip("songs/calm_down.zip")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        audio_path = root / Path(source.audio_file_name).name
        midi_path = root / Path(source.song_file_name).name
        with ZipFile(source.fp) as archive:
            audio_path.write_bytes(archive.read(source.audio_file_name))
            midi_path.write_bytes(archive.read(source.song_file_name))
        output = root / "songs-local"
        request = SongImportRequest(
            audio_path,
            midi_path,
            "Packaged Import Test",
            "Test Artist",
            "Test Mapper",
            "Temporary smoke test",
            125,
        )
        result = create_song_pack(request, output, duplicate_directories=(output,))
        imported = make_song_from_zip(str(result.output_path), local_only=True)
        if imported.name != request.title or imported.music_offset != 125 or not imported.local_only:
            raise RuntimeError("Generated local song pack did not reload correctly")
        try:
            create_song_pack(request, output, duplicate_directories=(output,))
        except DuplicateSongError:
            return 0
        raise RuntimeError("Duplicate local song was not rejected")


def run_customizer_smoke_test() -> int:
    """Exercise PNG installation, preset JSON, preview rendering, and hitbox isolation."""
    set_resource_root()
    from tempfile import TemporaryDirectory
    from squarecustomizer import (
        apply_style,
        export_style_json,
        import_style_json,
        install_custom_png,
        save_user_preset,
        snapshot_style,
    )
    from square import Square

    pygame.init()
    screen = pygame.display.set_mode((800, 600))
    previous_size = (Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT)
    previous_style = snapshot_style()
    Config.SCREEN_WIDTH = 800
    Config.SCREEN_HEIGHT = 600
    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "core.png"
            image = pygame.Surface((64, 64), pygame.SRCALPHA)
            pygame.draw.circle(image, (255, 60, 120, 255), (32, 32), 26)
            pygame.image.save(image, source)
            installed = install_custom_png(source, root / "assets")
            Config.square_core_shape = "custom"
            Config.square_core_image_path = str(installed)
            Config.square_core_rotation_speed = 0
            Config.square_core_pulse_strength = 0
            square = Square(100, 100)
            hitbox = square.rect.copy()
            square.draw(screen, pygame.Rect(75, 75, 50, 50))
            if square.rect != hitbox:
                raise RuntimeError("Customizer changed the square hitbox")
            preset = save_user_preset("Smoke Preset", root / "presets")
            exported = export_style_json(root / "export.json")
            apply_style(previous_style)
            import_style_json(exported)
            if Config.square_core_shape != "custom" or not preset.is_file():
                raise RuntimeError("Customizer preset did not round-trip")
            page = SquareCustomizerPage()
            page.active = True
            page.draw(screen)
        return 0
    finally:
        apply_style(previous_style)
        Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT = previous_size
        pygame.quit()


def run_release_smoke_test() -> int:
    """Run every feature check with a fresh pygame process."""
    checks = (
        "--stream-smoke-test",
        "--shorts-smoke-test",
        "--import-smoke-test",
        "--customizer-smoke-test",
    )
    for argument in checks:
        if getattr(sys, "frozen", False):
            command = [sys.executable, argument]
        else:
            command = [sys.executable, str(Path(__file__).resolve()), argument]
        result = subprocess.run(command, cwd=Path.cwd(), check=False)
        if result.returncode:
            return result.returncode
    return 0


def update_rainbow_theme():
    # thanks to TheCodingCrafter for the implementation
    if Config.theme == "rainbow":
        to_set_as_rainbow = pygame.Color((0, 0, 0))
        to_set_as_rainbow2 = pygame.Color((0, 0, 0))
        to_set_as_rainbow.hsva = (((pygame.time.get_ticks() / 1000) * Config.rainbow_speed) % 360, 100, 75, 100)
        to_set_as_rainbow2.hsva = ((((pygame.time.get_ticks() / 1000) * Config.rainbow_speed) + 180) % 360, 100, 75, 100)
        get_colors()["background"] = to_set_as_rainbow
        get_colors()["hallway"] = to_set_as_rainbow2
        get_colors()["square"][0] = to_set_as_rainbow


def live_window_size(settings: dict) -> list[int]:
    """The game renders at live width x height; the window shows it scaled to fit the monitor."""
    height = int(settings["window_height"])
    if height <= 0:  # 1280 (OBS 720x1280, 1:1) when it fits, else 90% of the monitor height (laptops)
        sizes = pygame.display.get_desktop_sizes()
        height = min(1280, int((sizes[0][1] if sizes else 1080) * 0.9))
    return [max(round(height * settings["width"] / settings["height"]), 1), height]


def run_live_loop(screen, glsl_program, render_object, clock, settings: dict, saved_size) -> None:
    from live import LiveSession, LiveOverlay, LibraryWatcher, make_chat_source, view_center_offset
    from paths import resource_path

    local_dir = user_path("songs-local")
    watcher = LibraryWatcher(
        [local_dir, resource_path("songs")],
        settings["category"],
        lambda path: make_song_from_zip(path, local_only=Path(path).parent == local_dir),
    )
    songs = list(watcher.scan().values())  # packs added while live are picked up by watcher.poll()
    saved = {
        "theme": Config.theme,
        "playlist_prefetch_count": Config.playlist_prefetch_count,
        "view_center_offset_y": Config.view_center_offset_y,
        "shorts_mode": Config.shorts_mode,
        "camera_zoom": Config.camera_zoom,
    }
    Config.camera_zoom = max(float(settings["zoom"]), 0.1)
    # the 30 s Shorts segment recorder (its own snapping camera, segment loop, no auto-advance) is not live mode
    Config.shorts_mode = False
    # live.json game_settings: the stream's look, the same on every PC (checked like settings.json, not saved)
    from config import sanitize_settings
    wanted = {k: v for k, v in dict(settings.get("game_settings") or {}).items() if hasattr(Config, k)}
    clean, _ = sanitize_settings(wanted)
    for name in wanted:
        saved.setdefault(name, getattr(Config, name))
        setattr(Config, name, clean.get(name, wanted[name]))
    if not songs:
        print(f"Live mode: no songs in category {settings['category']!r}")
        return
    themes = [name for name in settings["themes"] if name in Config.color_themes] or list(Config.color_themes)

    def apply_theme(name: str):
        Config.theme = name

    session = LiveSession(settings, songs, apply_theme, make_chat_source(settings), themes=themes)
    overlay = LiveOverlay(settings)
    Config.view_center_offset_y = view_center_offset(settings, Config.SCREEN_HEIGHT)
    Config.playlist_prefetch_count = 1  # chat requests reach the player as early as possible
    game = Game()
    game.live_mode = True
    game.live_show_hud = bool(settings["show_hud"])
    live_config = LiveConfigOverlay()
    session.start()
    retry_at = 0.0
    n_frames = 0
    running = True
    # MIDI_FRAME_LOG=<file>: per-frame timings (draw / present / tick) for frame-pacing checks
    import os
    from time import perf_counter
    frame_log = open(os.environ["MIDI_FRAME_LOG"], "w") if os.environ.get("MIDI_FRAME_LOG") else None
    pacer = FramePacer()
    try:
        while running:
            n_frames += 1
            frame_start = perf_counter()
            fps = int(Config.fps_limit) or FRAMERATE
            Config.frame_time = pacer.plan(min(fps, FRAMERATE) if FRAMERATE else fps)
            update_rainbow_theme()
            screen.fill(get_colors()["background"])
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                    continue
                if live_config.handle_event(event, game):
                    continue
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
                    continue
                game.handle_event(event)

            Config.shorts_mode = False  # also when toggled from the F10 panel
            session.update()
            found = watcher.poll()
            if found is not None:
                added, replaced = session.refresh_library(found)
                for index, song in added:
                    if index == len(game.playlist.songs):
                        game.playlist.songs.append(song)
                    print(f"Live mode: added {song.name}")
                for index, song in replaced:
                    if index < len(game.playlist.songs):
                        game.playlist.songs[index] = song
            # endless: (re)start whenever the playlist is not running
            if (not game.active or not game.auto_advance) and monotonic() >= retry_at:
                game.active = True
                message = game.start_playlist(
                    session.songs, session.next_index(), screen, loop=True, next_index_provider=session.next_index,
                )
                if message:
                    print(f"Live mode: {message}")
                    game.active = False
                    retry_at = monotonic() + 5.0
            session.queue.locked = [game.playlist.current_index] + [slot.index for slot in game.playlist.slots]

            game.draw(screen, n_frames)
            current = Config.current_song
            next_slot = game.playlist.slots[0] if game.playlist.slots else None
            overlay.draw(
                screen,
                session,
                getattr(current, "name", ""),
                getattr(next_slot.song, "name", "") if next_slot else settings["labels"]["empty_slot"],
                get_colors()["square"][0],
            )
            if live_config.active:  # no 'F10' hint on the stream; the panel still opens with F10
                live_config.draw(screen, game.active)
            drawn = perf_counter()
            pacer.wait()
            update_screen(screen, glsl_program, render_object)
            presented = perf_counter()
            Config.dt = pacer.dt
            if frame_log is not None:
                frame_log.write(f"{frame_start:.6f} {drawn:.6f} {presented:.6f} {Config.frame_time:.6f}\n")
    finally:
        Config.frame_time = None
        if frame_log is not None:
            frame_log.close()
        session.stop()
        game.shutdown()
        for name, value in saved.items():
            setattr(Config, name, value)
        Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT = saved_size


def main(live_settings: dict | None = None):
    set_resource_root()

    # patch to fix mouse on high dpi displays
    if "Windows" in get_os():
        from ctypes import windll
        windll.user32.SetProcessDPIAware()
    # patch to fix opengl error on mac
    elif "Darwin" in get_os():
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG, True)

    # pygame and other boilerplate
    n_frames = 0
    saved_size = (Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT)
    if live_settings is None:
        pygame.mixer.music.load("./assets/mainmenu.mp3")
        pygame.mixer.music.play(loops=-1, start=2)
    else:
        Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT = int(live_settings["width"]), int(live_settings["height"])
    pygame.mixer.music.set_volume(Config.volume / 100)

    clock = pygame.time.Clock()

    flags = pygame.HWACCEL | pygame.HWSURFACE | pygame.OPENGL | pygame.DOUBLEBUF
    do_vsync = 1
    if "Linux" in get_os():
        do_vsync = 0  # otherwise error of "regular vsync for OpenGL not available" at least that's what i got under wsl
    # noinspection PyUnusedLocal
    window_size = [Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT]
    if live_settings is not None:
        window_size = live_window_size(live_settings)
        # OBS window capture only gets the part of the window that is on a screen: open it fully inside the
        # primary monitor (right side, below the title bar) - window_pos [x, y] in live.json overrides
        import os
        pos = live_settings.get("window_pos") or []
        if len(pos) != 2:
            desktop = pygame.display.get_desktop_sizes()[0]
            pos = [max(desktop[0] - window_size[0] - 40, 0), max(min(40, desktop[1] - window_size[1]), 0)]
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{int(pos[0])},{int(pos[1])}"
    elif window_size == [pygame.display.Info().current_w, pygame.display.Info().current_h]:
        flags |= pygame.FULLSCREEN

    real_screen = pygame.display.set_mode(
        window_size,
        flags,
        vsync=do_vsync
    )
    screen = pygame.Surface([Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT])

    # noinspection PyBroadException
    try:
        pygame.display.set_caption("Midi Playground")
        pygame.display.set_icon(pygame.image.load("./assets/icon.png").convert_alpha())
    except Exception as e:
        print(e)

    # moderngl stuff
    ctx = moderngl.create_context()
    Config.ctx = ctx

    quad_buffer = ctx.buffer(data=array('f', [
        # position, uv coords
        -1.0, 1.0, 0.0, 0.0,  # topleft
        1.0, 1.0, 1.0, 0.0,  # topright
        -1.0, -1.0, 0.0, 1.0,  # bottomleft
        1.0, -1.0, 1.0, 1.0  # bottomright
    ]))

    vert_shader = '''
    #version 330 core
    
    in vec2 vert;
    in vec2 texcoord;
    out vec2 uvs;
    
    void main() {
        uvs = texcoord;
        gl_Position = vec4(vert.x, vert.y, 0.0, 1.0);
    }
    '''

    with open(f"./assets/shaders/{Config.shader_file_name}") as shader_file:
        frag_shader = shader_file.read()

    glsl_program = ctx.program(vertex_shader=vert_shader, fragment_shader=frag_shader)
    render_object = ctx.vertex_array(glsl_program, [(quad_buffer, '2f 2f', 'vert', 'texcoord')])

    Config.glsl_program = glsl_program
    Config.render_object = render_object
    Config.screen = screen
    # the live window shows the 1080x1920 frame scaled down: smooth filtering instead of dropped pixels
    Config.texture_smooth = list(window_size) != [Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT]

    if live_settings is not None:
        try:
            run_live_loop(screen, glsl_program, render_object, clock, live_settings, saved_size)
        finally:
            pygame.quit()
            save_to_file()
        return

    # the big guns
    menu = Menu()
    song_selector = SongSelector()
    config_page = ConfigPage()
    error_screen = ErrorScreen()
    game = Game()
    live_config = LiveConfigOverlay()
    song_import_page = SongImportPage()
    square_customizer_page = SquareCustomizerPage()

    # game loop
    running = True
    pacer = FramePacer()
    while running:
        n_frames += 1
        fps = int(Config.fps_limit) or FRAMERATE
        Config.frame_time = pacer.plan(min(fps, FRAMERATE) if FRAMERATE else fps)
        update_rainbow_theme()

        screen.fill(get_colors()["background"])
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                continue
            if live_config.handle_event(event, game):
                continue
            if event.type == pygame.KEYDOWN:
                # artificial lag spike for debugging purposes
                if event.key == pygame.K_F12:
                    total = 0
                    for _ in range(10_000_000):
                        total += 1
                if event.key == pygame.K_F3:
                    print("Debug information copied to clipboard")
                    debuginfo.print_debug_info()
                if event.key == pygame.K_F2:
                    if game.active:
                        debuginfo.debug_rectangles(game.safe_areas)
                if event.key == pygame.K_ESCAPE:
                    if song_selector.active:
                        song_selector.active = False
                        menu.active = True
                        if song_selector.selected_index + 1:
                            pygame.mixer.music.load("./assets/mainmenu.mp3")
                            pygame.mixer.music.set_volume(Config.volume / 100)
                            pygame.mixer.music.play(loops=-1, start=2)
                            song_selector.selected_index = -1
                        continue
                    if game.active:
                        game.stop_playlist()
                        game.active = False
                        song_selector.active = True
                        pygame.mixer.music.load("./assets/mainmenu.mp3")
                        pygame.mixer.music.set_volume(Config.volume / 100)
                        pygame.mixer.music.play(loops=-1, start=2)
                        song_selector.selected_index = -1
                        continue
                    if config_page.active:
                        config_page.active = False
                        menu.active = True
                        continue
                    if song_import_page.active:
                        song_import_page.active = False
                        song_import_page.stop_preview(resume_menu=True)
                        menu.active = True
                        continue
                    if square_customizer_page.active:
                        square_customizer_page.active = False
                        save_to_file()
                        menu.active = True
                        continue
                    if error_screen.active:
                        error_screen.active = False
                        song_selector.active = True
                        continue
                    running = False

            # handle menu events
            option_id = menu.handle_event(event)
            if option_id:
                if option_id == "open-songs-folder":
                    local_songs = user_path("songs-local")
                    local_songs.mkdir(parents=True, exist_ok=True)
                    open_file(str(local_songs))
                    continue
                if option_id == "contribute":
                    webbrowser.open("https://github.com/quasar098/midi-playground")
                    continue
                menu.active = False
                if option_id == "config":
                    config_page.active = True
                if option_id == "import-song":
                    song_import_page.active = True
                if option_id == "customize-square":
                    square_customizer_page.active = True
                if option_id == "play":
                    song_selector.active = True
                    song_selector.reload_songs()
                if option_id == "quit":
                    running = False
                continue

            import_result = song_import_page.handle_event(event)
            if import_result == "back":
                song_import_page.active = False
                menu.active = True
                continue
            if isinstance(import_result, tuple) and import_result[0] == "created":
                song_selector.reload_songs()
                continue

            customizer_result = square_customizer_page.handle_event(event)
            if customizer_result == "back":
                square_customizer_page.active = False
                menu.active = True
                continue

            # handle song selector events
            song = song_selector.handle_event(event)
            if song:
                if isinstance(song, bool):
                    menu.active = True
                    if song_selector.selected_index + 1:
                        pygame.mixer.music.load("./assets/mainmenu.mp3")
                        pygame.mixer.music.set_volume(Config.volume / 100)
                        pygame.mixer.music.play(loops=-1, start=2)
                        song_selector.selected_index = -1
                    continue
                # starting song now
                Config.current_song = song
                game.active = True
                selected_index = song_selector.songs.index(song)
                if msg := game.start_playlist(song_selector.songs, selected_index, screen):
                    if isinstance(msg, str):
                        game.active = False
                        error_screen.active = True
                        error_screen.msg = msg
                    else:
                        game.active = False
                        song_selector.active = True
                    pygame.mixer.music.load("./assets/mainmenu.mp3")
                    pygame.mixer.music.set_volume(Config.volume / 100)
                    pygame.mixer.music.play(loops=-1, start=2)

            # handle config page events
            if config_page.handle_event(event):
                config_page.active = False
                menu.active = True

            # handle game events
            if game.handle_event(event):
                game.stop_playlist()
                game.active = False
                song_selector.active = True

        # draw stuff here
        game.draw(screen, n_frames)
        song_selector.draw(screen)
        config_page.draw(screen)
        menu.draw(screen, n_frames)
        error_screen.draw(screen)
        song_import_page.draw(screen)
        square_customizer_page.draw(screen)
        live_config.draw(screen, game.active)

        pacer.wait()  # exact 1/fps, just before a display refresh (pygame's tick waits whole milliseconds)
        update_screen(screen, glsl_program, render_object)
        Config.dt = pacer.dt
    game.shutdown()
    pygame.quit()
    save_to_file()


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    if "--version" in sys.argv:
        print(__version__)
        raise SystemExit(0)
    if "--release-smoke-test" in sys.argv:
        raise SystemExit(run_release_smoke_test())
    if "--stream-smoke-test" in sys.argv:
        raise SystemExit(run_stream_smoke_test())
    if "--shorts-smoke-test" in sys.argv:
        raise SystemExit(run_shorts_smoke_test())
    if "--import-smoke-test" in sys.argv:
        raise SystemExit(run_import_smoke_test())
    if "--customizer-smoke-test" in sys.argv:
        raise SystemExit(run_customizer_smoke_test())
    if "--live" in sys.argv:
        from live import load_live_settings, apply_cli_overrides
        main(apply_cli_overrides(load_live_settings(), sys.argv))
        raise SystemExit(0)
    main()
