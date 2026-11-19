"""Append-only JSON Lines audit log of file operations.

History is a record of what happened, not a source of truth for what to move.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .files import MoveResult
from .settings import SUPPORT_DIR

HISTORY_PATH = SUPPORT_DIR / "history.jsonl"
RESULTS = ("success", "skipped", "error")


@dataclass(frozen=True)
class HistoryEntry:
    timestamp: str  # ISO 8601 with local UTC offset
    relative_path: str
    operation: str  # always "move" today
    result: str  # one of RESULTS
    message: str = ""  # reason for a skip or error


def entry_from_result(result: MoveResult, now: datetime) -> HistoryEntry:
    return HistoryEntry(
        timestamp=now.isoformat(timespec="seconds"),
        relative_path=result.record.relative_path,
        operation="move",
        result=result.outcome.category,
        message="" if result.outcome.category == "success" else result.message,
    )


def entry_to_json(entry: HistoryEntry) -> str:
    return json.dumps(asdict(entry), ensure_ascii=False)


def entry_from_json(line: str) -> HistoryEntry | None:
    """Parse one line, returning None for blank or malformed lines."""
    try:
        data = json.loads(line)
        return HistoryEntry(
            timestamp=str(data["timestamp"]),
            relative_path=str(data["relative_path"]),
            operation=str(data["operation"]),
            result=str(data["result"]),
            message=str(data.get("message") or ""),
        )
    except (ValueError, KeyError, TypeError):
        return None


def append_history(entries: Iterable[HistoryEntry], path: Path = HISTORY_PATH) -> None:
    lines = "".join(entry_to_json(entry) + "\n" for entry in entries)
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(lines)


def _tail_lines(path: Path, limit: int, chunk_size: int, max_bytes: int) -> list[bytes]:
    """Read at most ``limit`` trailing lines, scanning backwards in chunks."""
    with open(path, "rb") as handle:
        position = handle.seek(0, os.SEEK_END)
        data = b""
        while position > 0 and data.count(b"\n") <= limit and len(data) < max_bytes:
            step = min(chunk_size, position)
            position -= step
            handle.seek(position)
            data = handle.read(step) + data
    lines = data.splitlines()
    if position > 0 and lines:
        lines = lines[1:]  # The first line is probably partial.
    return lines[-limit:] if limit else []


def read_recent_history(
    path: Path = HISTORY_PATH, limit: int = 1000, chunk_size: int = 64 * 1024, max_bytes: int = 8 * 1024 * 1024
) -> tuple[HistoryEntry, ...]:
    """Return up to ``limit`` most recent entries, newest first, without reading the whole file."""
    try:
        lines = _tail_lines(path, limit, chunk_size, max_bytes)
    except FileNotFoundError:
        return ()
    entries = (entry_from_json(line.decode("utf-8", errors="replace")) for line in reversed(lines))
    return tuple(entry for entry in entries if entry is not None)


def filter_history(entries: Iterable[HistoryEntry], result: str | None) -> tuple[HistoryEntry, ...]:
    """Keep entries with the given result; None keeps everything."""
    return tuple(entry for entry in entries if result is None or entry.result == result)
