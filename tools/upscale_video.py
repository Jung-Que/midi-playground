"""Create an edit-friendly high-resolution master from a recorded video."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


class UpscaleError(RuntimeError):
    pass


def default_output_path(source: Path, scale: int) -> Path:
    return source.with_name(f"{source.stem}_{scale}x_edit_master.mp4")


def build_ffmpeg_command(
        ffmpeg: str,
        source: Path,
        output: Path,
        *,
        scale: int = 2,
        crf: int = 14,
        preset: str = "slow",
        sharpen: bool = True,
) -> list[str]:
    if scale not in (2, 3, 4):
        raise UpscaleError("Scale must be 2, 3, or 4")
    if not 0 <= crf <= 51:
        raise UpscaleError("CRF must be between 0 and 51")

    filters = [
        f"scale=trunc(iw*{scale}/2)*2:trunc(ih*{scale}/2)*2:flags=lanczos",
    ]
    if sharpen:
        # Flat game graphics benefit from restrained edge recovery. Stronger
        # sharpening creates halos around pegs, text, and the square border.
        filters.append("unsharp=5:5:0.40:5:5:0.0")

    return [
        ffmpeg,
        "-hide_banner",
        "-i", str(source),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-vf", ",".join(filters),
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-c:a", "copy",
        "-n",
        str(output),
    ]


def upscale(
        source: Path,
        output: Path | None = None,
        *,
        scale: int = 2,
        crf: int = 14,
        preset: str = "slow",
        sharpen: bool = True,
        ffmpeg: str | None = None,
        dry_run: bool = False,
) -> Path:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise UpscaleError(f"Input video does not exist: {source}")
    output = (output or default_output_path(source, scale)).expanduser().resolve()
    if output == source:
        raise UpscaleError("Output must not overwrite the source video")
    if output.exists():
        raise UpscaleError(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        raise UpscaleError(
            "FFmpeg was not found. Install it with `winget install Gyan.FFmpeg` "
            "and open a new terminal."
        )
    command = build_ffmpeg_command(
        executable, source, output,
        scale=scale, crf=crf, preset=preset, sharpen=sharpen,
    )
    print("+", subprocess.list2cmdline(command), flush=True)
    if dry_run:
        return output
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        output.unlink(missing_ok=True)
        raise UpscaleError(f"FFmpeg failed with exit code {completed.returncode}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="recorded MP4/MOV source")
    parser.add_argument("--output", type=Path, help="output MP4 path")
    parser.add_argument("--scale", type=int, choices=(2, 3, 4), default=2)
    parser.add_argument("--crf", type=int, default=14, help="H.264 quality; lower is larger/better")
    parser.add_argument("--preset", default="slow", help="FFmpeg x264 preset")
    parser.add_argument("--no-sharpen", action="store_true")
    parser.add_argument("--ffmpeg", help="explicit ffmpeg executable path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        output = upscale(
            args.input,
            args.output,
            scale=args.scale,
            crf=args.crf,
            preset=args.preset,
            sharpen=not args.no_sharpen,
            ffmpeg=args.ffmpeg,
            dry_run=args.dry_run,
        )
        print(f"Edit master: {output}")
        return 0
    except (OSError, UpscaleError) as exc:
        print(f"Upscale failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
