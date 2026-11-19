import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filesync.files import FileRecord, MoveResult, Outcome
from filesync.history import (
    HistoryEntry,
    append_history,
    entry_from_json,
    entry_from_result,
    entry_to_json,
    filter_history,
    read_recent_history,
)

NOW = datetime(2026, 11, 19, 14, 30, 5, tzinfo=timezone(timedelta(hours=-5)))


def entry(index: int, result: str = "success") -> HistoryEntry:
    return HistoryEntry(f"2026-11-19T10:00:{index % 60:02d}-05:00", f"Family/{index}.mp4", "move", result)


def test_entry_round_trip():
    original = HistoryEntry("2026-11-19T14:30:05-05:00", "Family/Café.mp4", "move", "error", "Permission denied.")
    line = entry_to_json(original)
    assert "\n" not in line
    assert json.loads(line)["relative_path"] == "Family/Café.mp4"
    assert entry_from_json(line) == original


def test_entry_from_result_maps_outcomes():
    record = FileRecord(Path("/s/Family/VHS_001.mp4"), "Family/VHS_001.mp4", 1, 1, 1, 1)
    moved = entry_from_result(MoveResult(record, Outcome.MOVED, "/dest/Family/VHS_001.mp4"), NOW)
    skipped = entry_from_result(MoveResult(record, Outcome.EXISTS, "Already exists."), NOW)
    failed = entry_from_result(MoveResult(record, Outcome.FAILED, "Permission denied."), NOW)

    assert moved == HistoryEntry("2026-11-19T14:30:05-05:00", "Family/VHS_001.mp4", "move", "success", "")
    assert (skipped.result, skipped.message) == ("skipped", "Already exists.")
    assert (failed.result, failed.message) == ("error", "Permission denied.")


def test_malformed_lines_are_ignored():
    assert entry_from_json("") is None
    assert entry_from_json("{oops") is None
    assert entry_from_json('{"timestamp": "x"}') is None
    assert entry_from_json("[]") is None


def test_append_and_read_newest_first(tmp_path):
    path = tmp_path / "FileSync" / "history.jsonl"
    append_history([entry(1), entry(2)], path)
    append_history([entry(3)], path)
    append_history([], path)

    assert path.read_text().count("\n") == 3
    assert read_recent_history(path) == (entry(3), entry(2), entry(1))


def test_reading_is_bounded_and_handles_chunk_boundaries(tmp_path):
    path = tmp_path / "history.jsonl"
    append_history([entry(i) for i in range(500)], path)
    with open(path, "a") as handle:
        handle.write("garbage line\n")

    recent = read_recent_history(path, limit=10, chunk_size=97)

    assert len(recent) == 9  # The garbage line counts toward the limit but is dropped.
    assert recent[0] == entry(499)
    assert recent[-1] == entry(491)


def test_reading_stops_at_max_bytes(tmp_path):
    path = tmp_path / "history.jsonl"
    append_history([entry(i) for i in range(1000)], path)
    recent = read_recent_history(path, limit=1000, chunk_size=1024, max_bytes=4096)
    assert 0 < len(recent) < 100
    assert recent[0] == entry(999)


def test_missing_history_reads_as_empty(tmp_path):
    assert read_recent_history(tmp_path / "none.jsonl") == ()


def test_filter_history():
    entries = (entry(1), entry(2, "skipped"), entry(3, "error"))
    assert filter_history(entries, None) == entries
    assert filter_history(entries, "skipped") == (entry(2, "skipped"),)
