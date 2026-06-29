from utils import *
from menu import Menu
from game import Game
from configpage import ConfigPage
from songselector import SongSelector, make_song_from_zip
from errorscreen import ErrorScreen
from liveconfig import LiveConfigOverlay
from os import chdir, getcwd
from platform import system as get_os
from pathlib import Path
import sys
from config import save_to_file
import debuginfo
import webbrowser
import pygame
from array import array
from time import monotonic, sleep


def set_resource_root():
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    chdir(resource_root)


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


def main():
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
    pygame.mixer.music.load("./assets/mainmenu.mp3")
    pygame.mixer.music.set_volume(Config.volume / 100)
    pygame.mixer.music.play(loops=-1, start=2)

    clock = pygame.time.Clock()

    flags = pygame.HWACCEL | pygame.HWSURFACE | pygame.OPENGL | pygame.DOUBLEBUF
    do_vsync = 1
    if "Linux" in get_os():
        do_vsync = 0  # otherwise error of "regular vsync for OpenGL not available" at least that's what i got under wsl
    # noinspection PyUnusedLocal
    if [Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT] == [pygame.display.Info().current_w,pygame.display.Info().current_h]:
        flags |= pygame.FULLSCREEN
    
    real_screen = pygame.display.set_mode(
        [Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT],
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

    # the big guns
    menu = Menu()
    song_selector = SongSelector()
    config_page = ConfigPage()
    error_screen = ErrorScreen()
    game = Game()
    live_config = LiveConfigOverlay()

    # game loop
    running = True
    while running:
        n_frames += 1
        # thanks to TheCodingCrafter for the implementation
        if Config.theme == "rainbow":
            to_set_as_rainbow = pygame.Color((0, 0, 0))
            to_set_as_rainbow2 = pygame.Color((0, 0, 0))
            to_set_as_rainbow.hsva = (((pygame.time.get_ticks() / 1000) * Config.rainbow_speed) % 360, 100, 75, 100)
            to_set_as_rainbow2.hsva = ((((pygame.time.get_ticks() / 1000) * Config.rainbow_speed) + 180) % 360, 100, 75, 100)
            get_colors()["background"] = to_set_as_rainbow
            get_colors()["hallway"] = to_set_as_rainbow2
            get_colors()["square"][0] = to_set_as_rainbow

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
                    if error_screen.active:
                        error_screen.active = False
                        song_selector.active = True
                        continue
                    running = False

            # handle menu events
            option_id = menu.handle_event(event)
            if option_id:
                if option_id == "open-songs-folder":
                    open_file(join(getcwd(), "songs"))
                    continue
                if option_id == "contribute":
                    webbrowser.open("https://github.com/quasar098/midi-playground")
                    continue
                menu.active = False
                if option_id == "config":
                    config_page.active = True
                if option_id == "play":
                    song_selector.active = True
                    song_selector.reload_songs()
                if option_id == "quit":
                    running = False
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
        live_config.draw(screen, game.active)

        update_screen(screen, glsl_program, render_object)

        Config.dt = clock.tick(FRAMERATE) / 1000
    game.shutdown()
    pygame.quit()
    save_to_file()


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    if "--stream-smoke-test" in sys.argv:
        raise SystemExit(run_stream_smoke_test())
    main()
