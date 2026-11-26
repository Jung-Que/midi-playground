"""Create the OBS profile + scene collection for the vertical live (run once per PC, with OBS closed).

    python tools/obs_live_setup.py              # encoder picked from the graphics card
    python tools/obs_live_setup.py --encoder x264

Profile "MidiPlayground Live": 720x1280 canvas and output, 60 fps, YouTube RTMPS (stream key left empty),
hardware H.264 when there is one (NVIDIA nvenc / AMD amd / Intel qsv, else x264), 4500 kbps.
Scene collection "MidiPlayground Live": window capture of the "Midi Playground" window + that window's audio
only (desktop audio and microphone muted). Existing profiles / collections are not touched; OBS opens on the
new ones next time (user.ini is backed up first).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

PROFILE_DIR, PROFILE_NAME = "MidiPlayground_Live", "MidiPlayground Live"
SCENES_FILE, SCENES_NAME = "MidiPlayground_Live.json", "MidiPlayground Live"
WINDOW = "Midi Playground:pygame:python.exe"  # title:class:exe of the live window
W, H = 720, 1280
OBS_VERSION = 536936450  # 32.1.2; older OBS reads it too


def obs_root() -> Path:
    return Path(os.environ["APPDATA"]) / "obs-studio"


def obs_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe"], capture_output=True, text=True).stdout
    except OSError:
        return False
    return "obs64.exe" in out


def pick_encoder() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=20,
        ).stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    if "nvidia" in out:
        return "nvenc"
    if "amd" in out or "radeon" in out:
        return "amd"
    if "intel" in out:
        return "qsv"
    return "x264"


def write_profile(root: Path, encoder: str):
    profile = root / "basic" / "profiles" / PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)
    ini = f"""[General]
Name={PROFILE_NAME}

[Output]
Mode=Simple
Reconnect=true
RetryDelay=2
MaxRetries=25

[SimpleOutput]
StreamEncoder={encoder}
VBitrate=4500
ABitrate=160
UseAdvanced=false
Preset=veryfast
RecFormat2=mkv
RecQuality=Stream
RecEncoder={encoder}

[Video]
BaseCX={W}
BaseCY={H}
OutputCX={W}
OutputCY={H}
FPSType=0
FPSCommon=60
ScaleType=bicubic
ColorFormat=NV12
ColorSpace=709
ColorRange=Partial

[Audio]
SampleRate=48000
ChannelSetup=Stereo

[Panels]
CookieId={random.getrandbits(64):016X}
"""
    (profile / "basic.ini").write_text(ini, encoding="utf-8-sig")
    (profile / "service.json").write_text(json.dumps({
        "type": "rtmp_common",
        "settings": {"service": "YouTube - RTMPS", "server": "rtmps://a.rtmps.youtube.com:443/live2", "key": ""},
    }, indent=4), encoding="utf-8")
    return profile


def _source(name: str, source_id: str, settings: dict, muted: bool = False) -> dict:
    return {"prev_ver": OBS_VERSION, "name": name, "uuid": str(uuid.uuid4()), "id": source_id,
            "versioned_id": source_id, "settings": settings, "mixers": 255, "sync": 0, "flags": 0,
            "volume": 1.0, "balance": 0.5, "enabled": True, "muted": muted, "monitoring_type": 0,
            "private_settings": {}}


def _item(index: int, source: dict, fit: bool) -> dict:
    return {"name": source["name"], "source_uuid": source["uuid"], "visible": True, "locked": True, "rot": 0.0,
            "scale_ref": {"x": float(W), "y": float(H)}, "align": 5, "bounds_type": 2 if fit else 0,
            "bounds_align": 0, "bounds_crop": False, "crop_left": 0, "crop_top": 0, "crop_right": 0,
            "crop_bottom": 0, "id": index, "group_item_backup": False, "pos": {"x": 0.0, "y": 0.0},
            "scale": {"x": 1.0, "y": 1.0}, "bounds": {"x": float(W), "y": float(H)},
            "blend_method": "default", "blend_type": "normal", "private_settings": {}}


def write_scenes(root: Path) -> Path:
    game = _source("Game window", "window_capture",
                   {"window": WINDOW, "method": 2, "priority": 0, "cursor": False, "client_area": True})
    sound = _source("Game audio", "wasapi_process_output_capture", {"window": WINDOW, "priority": 0})
    scene = _source("Live", "scene", {"id_counter": 2, "custom_size": False,
                                      "items": [_item(1, game, True), _item(2, sound, False)]})
    collection = {
        "name": SCENES_NAME,
        # only the game's own sound goes out: other programs and the microphone are muted
        "DesktopAudioDevice1": _source("Desktop Audio", "wasapi_output_capture", {"device_id": "default"}, True),
        "AuxAudioDevice1": _source("Mic/Aux", "wasapi_input_capture", {"device_id": "default"}, True),
        "sources": [game, sound, scene], "groups": [], "scene_order": [{"name": "Live"}],
        "current_scene": "Live", "current_program_scene": "Live", "canvases": [],
        "current_transition": "Fade", "transition_duration": 300, "transitions": [], "quick_transitions": [],
        "saved_projectors": [], "preview_locked": False, "scaling_enabled": False, "scaling_level": 0,
        "scaling_off_x": 0.0, "scaling_off_y": 0.0, "modules": {}, "resolution": {"x": W, "y": H}, "version": 2,
    }
    path = root / "basic" / "scenes" / SCENES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(collection, ensure_ascii=False, indent=4), encoding="utf-8")
    return path


def select_on_start(root: Path):
    """Make OBS open on the new profile / collection (keys live in user.ini on OBS 31+, global.ini before)."""
    keys = {"Profile": PROFILE_NAME, "ProfileDir": PROFILE_DIR,
            "SceneCollection": SCENES_NAME, "SceneCollectionFile": SCENES_FILE}
    target = next((root / name for name in ("user.ini", "global.ini")
                   if (root / name).is_file() and "ProfileDir=" in (root / name).read_text(encoding="utf-8-sig")),
                  root / "user.ini")
    text = target.read_text(encoding="utf-8-sig") if target.is_file() else ""
    if target.is_file():
        shutil.copy2(target, target.with_name(target.name + ".before-midiplayground"))
    lines, done = text.splitlines(), set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0]
        if key in keys and "=" in line:
            lines[i] = f"{key}={keys[key]}"
            done.add(key)
    missing = [f"{k}={v}" for k, v in keys.items() if k not in done]
    if missing:
        if "[Basic]" in lines:
            at = lines.index("[Basic]") + 1
            lines[at:at] = missing
        else:
            lines += ["", "[Basic]"] + missing
    target.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return target


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--encoder", choices=["auto", "x264", "nvenc", "amd", "qsv"], default="auto")
    args = parser.parse_args(argv)
    root = obs_root()
    if not root.is_dir():
        print("OBS settings folder not found: install OBS and start it once, then run this again.")
        return 1
    if obs_running():
        print("Close OBS first (it rewrites its settings when it exits).")
        return 1
    encoder = pick_encoder() if args.encoder == "auto" else args.encoder
    print("profile :", write_profile(root, encoder), f"(encoder: {encoder})")
    print("scenes  :", write_scenes(root))
    print("selected:", select_on_start(root))
    print("Next: open OBS > Settings > Stream, enter the YouTube stream key (or connect the account).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
