from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def read_actor_lines(path: str) -> list[str]:
    p = Path(path).expanduser()
    if not p.exists():
        raise RuntimeError(f"List file not found: {p}")

    out: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        # allow inline comments
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        if not line:
            continue
        out.append(line)

    # de-dupe while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for a in out:
        if a in seen:
            continue
        seen.add(a)
        uniq.append(a)

    return uniq


def sleep_between(min_delay: float, max_delay: float, buffer: float) -> None:
    min_d = max(0.0, float(min_delay)) * (1.0 + float(buffer))
    max_d = max(min_d, float(max_delay)) * (1.0 + float(buffer))
    if max_d <= 0:
        return
    time.sleep(random.uniform(min_d, max_d))


def append_line(path: str | None, line: str) -> None:
    if not path:
        return
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def atomic_write_lines(path: str, lines: list[str]) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, p)


def rewrite_list_file(path: str, remaining: list[str]) -> None:
    atomic_write_lines(path, remaining)


def normalize_handle(value: str) -> str:
    value = value.strip()
    if value.startswith("@"):
        value = value[1:]
    # If no domain is provided, assume .bsky.social
    if value and "." not in value and not value.startswith("did:"):
        value = f"{value}.bsky.social"
    return value
