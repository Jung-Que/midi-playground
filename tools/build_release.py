"""Build and verify a privacy-safe Windows release archive."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "packaging" / "release-manifest.txt"
EXPORT_ROOT = ROOT / "exports" / "releases"
FORBIDDEN_PARTS = {"songs-local", "imports-local", "presets-local", "exports", "logs"}
FORBIDDEN_FILES = {"settings.json"}

sys.path.insert(0, str(ROOT))
from version import __version__  # noqa: E402


class ReleaseError(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise ReleaseError(f"Command failed with exit code {completed.returncode}")


def release_files() -> list[Path]:
    entries: list[Path] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(MANIFEST_PATH.read_text(encoding="utf-8").splitlines(), 1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        normalized = PurePosixPath(value)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ReleaseError(f"Unsafe manifest path on line {line_number}: {value}")
        if not normalized.parts or normalized.parts[0] not in {"assets", "songs"}:
            raise ReleaseError(f"Manifest path must begin with assets/ or songs/: {value}")
        if any(part.lower() in FORBIDDEN_PARTS for part in normalized.parts):
            raise ReleaseError(f"Private directory is forbidden in manifest: {value}")
        if normalized.name.lower() in FORBIDDEN_FILES:
            raise ReleaseError(f"Private file is forbidden in manifest: {value}")
        key = normalized.as_posix().lower()
        if key in seen:
            raise ReleaseError(f"Duplicate manifest entry: {value}")
        seen.add(key)
        source = ROOT.joinpath(*normalized.parts)
        if not source.is_file():
            raise ReleaseError(f"Manifest file is missing: {value}")
        entries.append(source)
    if not entries:
        raise ReleaseError("Release manifest is empty")
    return entries


def copy_payload(destination: Path) -> int:
    files = release_files()
    for source in files:
        relative = source.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return len(files)


def inspect_package(package_directory: Path) -> None:
    violations: list[str] = []
    for path in package_directory.rglob("*"):
        relative = path.relative_to(package_directory)
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts & FORBIDDEN_PARTS:
            violations.append(relative.as_posix())
        if path.is_file() and path.name.lower() in FORBIDDEN_FILES:
            violations.append(relative.as_posix())
    if violations:
        raise ReleaseError("Private data entered package: " + ", ".join(sorted(set(violations))))


def build(skip_tests: bool = False) -> tuple[Path, Path]:
    if os.name != "nt":
        raise ReleaseError("The current release target is Windows only")
    if not skip_tests:
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"])

    with TemporaryDirectory(prefix="midi-playground-release-") as temporary:
        temp_root = Path(temporary)
        payload = temp_root / "payload"
        copied_count = copy_payload(payload)
        dist = temp_root / "dist"
        work = temp_root / "build"
        spec = temp_root / "spec"
        separator = os.pathsep
        run([
            sys.executable, "-m", "PyInstaller", "main.py",
            "--name", "midi-playground",
            "--noconsole", "--onedir", "--clean", "--noupx",
            "--contents-directory", ".",
            "--hidden-import", "glcontext",
            "--add-data", f"{payload / 'assets'}{separator}assets",
            "--add-data", f"{payload / 'songs'}{separator}songs",
            "--distpath", str(dist),
            "--workpath", str(work),
            "--specpath", str(spec),
        ])
        package = dist / "midi-playground"
        executable = package / "midi-playground.exe"
        if not executable.is_file():
            raise ReleaseError("PyInstaller did not create midi-playground.exe")
        inspect_package(package)

        smoke_data = temp_root / "smoke-user-data"
        smoke_working_directory = temp_root / "outside-package"
        smoke_working_directory.mkdir()
        smoke_env = os.environ.copy()
        smoke_env["MIDI_PLAYGROUND_DATA_DIR"] = str(smoke_data)
        smoke_env.setdefault("SDL_AUDIODRIVER", "dummy")
        run([str(executable), "--release-smoke-test"], cwd=smoke_working_directory, env=smoke_env)
        if not (smoke_data / "settings.json").is_file():
            raise ReleaseError("Packaged settings were not written to the user-data directory")
        inspect_package(package)

        release_name = f"MidiPlayground-v{__version__}-windows-x64"
        release_directory = EXPORT_ROOT / release_name
        archive_path = EXPORT_ROOT / f"{release_name}.zip"
        checksum_path = archive_path.with_suffix(".zip.sha256")
        EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
        if release_directory.exists():
            shutil.rmtree(release_directory)
        archive_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        shutil.copytree(package, release_directory)
        (release_directory / "RELEASE.json").write_text(json.dumps({
            "name": "MidiPlayground",
            "version": __version__,
            "platform": "windows-x64",
            "public_resource_files": copied_count,
            "source": "https://github.com/Jung-Que/midi-playground",
            "license": "GPL-3.0",
        }, indent=2), encoding="utf-8")
        shutil.make_archive(
            str(archive_path.with_suffix("")),
            "zip",
            root_dir=EXPORT_ROOT,
            base_dir=release_name,
        )
        digest = sha256(archive_path.read_bytes()).hexdigest()
        checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
        return archive_path, checksum_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-manifest", action="store_true", help="validate public release inputs only")
    parser.add_argument("--skip-tests", action="store_true", help="skip source unit tests before building")
    args = parser.parse_args()
    try:
        files = release_files()
        if args.check_manifest:
            print(f"Release manifest OK: {len(files)} public files")
            return 0
        archive, checksum = build(skip_tests=args.skip_tests)
        print(f"Release archive: {archive}")
        print(f"SHA-256 file:   {checksum}")
        return 0
    except (OSError, ReleaseError) as exc:
        print(f"Release failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
