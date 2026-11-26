"""Set or list song-pack categories (metadata.json "categories"; a song may be in several).

    python tools/set_category.py songs-local/foo.zip ballad jpop     # foo is in "ballad" and "jpop"
    python tools/set_category.py songs-local/foo.zip                 # clear foo's categories
    python tools/set_category.py --list [songs-local songs]          # every pack with its categories
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
from live import categories_from_metadata, normalize_category  # noqa: E402


def read_metadata(pack: Path) -> dict:
    with zipfile.ZipFile(pack) as archive:
        return json.loads(archive.read("metadata.json"))


def set_categories(pack: Path, categories: list[str]):
    metadata = read_metadata(pack)
    metadata.pop("category", None)
    clean = []
    for name in categories:
        name = normalize_category(name)
        if name and name not in clean:
            clean.append(name)
    if clean:
        metadata["categories"] = clean
    else:
        metadata.pop("categories", None)
    handle, temp_name = tempfile.mkstemp(suffix=".zip", dir=pack.parent)
    os.close(handle)
    try:
        with zipfile.ZipFile(pack) as source, zipfile.ZipFile(temp_name, "w") as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "metadata.json":
                    data = json.dumps(metadata, ensure_ascii=False, indent=4).encode("utf-8")
                target.writestr(info, data, compress_type=info.compress_type)
        os.replace(temp_name, pack)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)
    return clean


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    if argv[0] == "--list":
        folders = [Path(arg) for arg in argv[1:]] or [Path("songs-local"), Path("songs")]
        for folder in folders:
            for pack in sorted(folder.glob("*.zip")):
                metadata = read_metadata(pack)
                print(f"{pack}: {', '.join(categories_from_metadata(metadata)) or '-'}  ({metadata.get('name')})")
        return 0
    pack = Path(argv[0])
    print(f"{pack}: {', '.join(set_categories(pack, argv[1:])) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
