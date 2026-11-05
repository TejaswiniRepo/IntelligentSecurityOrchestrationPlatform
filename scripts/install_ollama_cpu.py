"""Install a CPU-only Ollama runtime from the official release zip, downloading only the needed files.

Why: the full Windows installer is ~1.6 GB (mostly NVIDIA CUDA libraries). On a machine without an NVIDIA
GPU only ~35 MB of it is used. This script reads the official GitHub release zip with HTTP Range
requests and extracts `ollama.exe` plus the CPU backends. ZIP CRCs are verified during extraction;
verify the Authenticode signature afterwards (setup-online.ps1 does).

    python scripts/install_ollama_cpu.py [--version v0.40.10] [--dest %LOCALAPPDATA%\\Programs\\Ollama]

Requires internet. Never run it from start-demo.ps1.
"""

from __future__ import annotations

import argparse
import io
import os
import zipfile
from pathlib import Path

import httpx

DEFAULT_VERSION = "v0.40.10"


class RangeReader(io.RawIOBase):
    """Seekable read-only file over HTTP Range requests."""

    def __init__(self, url: str) -> None:
        self.client = httpx.Client(follow_redirects=True, timeout=120)
        head = self.client.head(url)
        head.raise_for_status()
        if head.headers.get("accept-ranges") != "bytes":
            raise RuntimeError("server does not support range requests; use the official installer instead")
        self.url, self.size, self.pos, self.fetched = str(head.url), int(head.headers["content-length"]), 0, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def readinto(self, b) -> int:
        if self.pos >= self.size or len(b) == 0:
            return 0
        end = min(self.pos + len(b), self.size) - 1
        r = self.client.get(self.url, headers={"Range": f"bytes={self.pos}-{end}"})
        r.raise_for_status()
        data = r.content
        b[: len(data)] = data
        self.pos += len(data)
        self.fetched += len(data)
        return len(data)


def wanted(name: str) -> bool:
    # ollama.exe and files directly under lib/ollama/ (skip cuda_*, rocm, vulkan, mlx subfolders)
    if name == "ollama.exe":
        return True
    return name.startswith("lib/ollama/") and "/" not in name[len("lib/ollama/"):] and not name.endswith("/")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=DEFAULT_VERSION)
    ap.add_argument("--dest", default=str(Path(os.environ.get("LOCALAPPDATA", ".")) / "Programs" / "Ollama"))
    args = ap.parse_args()

    url = f"https://github.com/ollama/ollama/releases/download/{args.version}/ollama-windows-amd64.zip"
    reader = RangeReader(url)
    dest = Path(args.dest)
    with zipfile.ZipFile(io.BufferedReader(reader, buffer_size=4 * 1024 * 1024)) as zf:
        for info in zf.infolist():
            if not wanted(info.filename):
                continue
            target = dest / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:  # zipfile raises on CRC mismatch
                while chunk := src.read(8 * 1024 * 1024):
                    out.write(chunk)
    print(f"Installed Ollama {args.version} (CPU) to {dest}; downloaded {reader.fetched / 1e6:.1f} MB "
          f"of a {reader.size / 1e6:.0f} MB archive")


if __name__ == "__main__":
    main()
