"""Package only explicitly selected public source and validated executable files."""
from __future__ import annotations

import hashlib
import argparse
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))
from wuwa_uhd import __version__ as VERSION


def archive(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for source, name in sorted(files, key=lambda item: item[1]):
            z.write(source, name)
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise RuntimeError("ZIP CRC validation failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=DIST / "WuwaUHDTool.exe")
    parser.add_argument("--output", type=Path, default=DIST)
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    exe = args.exe
    if not exe.is_file():
        raise RuntimeError("Build and validate the executable first")
    for name in ("README.md", "VALIDATION.md"):
        shutil.copyfile(ROOT / name, output / name)
    portable = output / f"WuwaUHDTool-{VERSION}-Windows.zip"
    portable_names = ["README.md", "VALIDATION.md", "validation/tool.json"]
    archive(portable, [(exe, exe.name)] + [(ROOT / name, name) for name in portable_names if (ROOT / name).is_file()])
    names = ["main.py", "build.ps1", "README.md", "VALIDATION.md", ".gitignore", ".gitattributes", ".github/workflows/offline-tests.yml", "scripts/package_release.py"]
    source_files = [(ROOT / name, name) for name in names]
    for folder in ("wuwa_uhd", "validation"):
        for path in (ROOT / folder).rglob("*"):
            relative = path.relative_to(ROOT)
            if not path.is_file() or "__pycache__" in relative.parts:
                continue
            if path.suffix.lower() not in {".py", ".json", ".ico"}:
                continue
            source_files.append((path, relative.as_posix()))
    archive(output / f"WuwaUHDTool-{VERSION}-Source.zip", source_files)
    files = [exe, portable, output / f"WuwaUHDTool-{VERSION}-Source.zip"]
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files)
    (output / "SHA256SUMS.txt").write_text(sums, encoding="ascii")
    print(sums, end="")


if __name__ == "__main__":
    main()
