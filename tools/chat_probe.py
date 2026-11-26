"""Chat link check without the game: prints every chat message and what the live mode would do with it.

    python tools/chat_probe.py <video id or URL>          (Ctrl+C to stop)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
from live import ChatSource, extract_video_id, load_live_settings  # noqa: E402


def main(argv):
    settings = load_live_settings()
    target = argv[0] if argv else settings["video_id"]
    if not target:
        print(__doc__)
        return 1
    commands = {settings[key].lower(): key for key in ("song_command", "color_command", "help_command")}
    source = ChatSource("pytchat", target, Path("unused.txt"))
    print(f"video id: {extract_video_id(target)}  (commands: {', '.join(commands)})")
    source.start()
    status = ""
    try:
        while True:
            if source.status != status:
                status = source.status
                print(f"[status] {status}", flush=True)
            for message in source.drain():
                word = message.text.strip().partition(" ")[0].lower()
                tag = f"  <- {commands[word]}" if word in commands else ""
                print(f"{time.strftime('%H:%M:%S')} {message.user}: {message.text}{tag}", flush=True)
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        source.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
