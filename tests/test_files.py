import errno
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from filesync.files import (
    FileRecord,
    Folders,
    MoveResult,
    Outcome,
    canonical_folders,
    check_source,
    count_outcomes,
    destination_path,
    format_size,
    is_same_or_inside,
    matches_record,
    move_file,
    move_files,
    partition_current,
    problem_lines,
    rename_no_replace,
    scan_files,
    select_records,
    summary_lines,
    total_size,
    UnsafeDestinationError,
    validate_folders,
)

macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="renameatx_np is macOS-only")


def write(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture
def folders(tmp_path: Path) -> Folders:
    (tmp_path / "staging").mkdir()
    (tmp_path / "synology").mkdir()
    return canonical_folders(str(tmp_path / "staging"), str(tmp_path / "synology"))


def scan(folders: Folders) -> dict[str, FileRecord]:
    return {r.relative_path: r for r in scan_files(folders.source).files}


def plain_rename(source: Path, destination: Path) -> None:
    """Portable stand-in for the native adapter: refuses to replace, like RENAME_EXCL."""
    if os.path.lexists(destination):
        raise FileExistsError(errno.EEXIST, "File exists", str(destination))
    os.rename(source, destination)


# 1. Recursive enumeration / 2. Relative paths ------------------------------


def test_scan_finds_regular_files_recursively_with_relative_paths(folders):
    write(folders.source / "top.jpg")
    write(folders.source / "Family" / "VHS_001.mp4", "video")
    write(folders.source / "Family" / "1990" / "deep.tif")
    (folders.source / "Empty").mkdir()
    write(folders.source / ".DS_Store")

    result = scan_files(folders.source)

    assert [r.relative_path for r in result.files] == ["Family/1990/deep.tif", "Family/VHS_001.mp4", "top.jpg"]
    video = next(r for r in result.files if r.name == "VHS_001.mp4")
    assert video.directory == "Family"
    assert video.size == 5
    assert video.source_path == folders.source / "Family" / "VHS_001.mp4"
    assert video.inode == os.stat(video.source_path).st_ino


def test_scan_does_not_read_unreadable_subfolders_but_reports_them(folders):
    write(folders.source / "ok.jpg")
    locked = folders.source / "Locked"
    write(locked / "hidden.jpg")
    locked.chmod(0)
    try:
        result = scan_files(folders.source)
    finally:
        locked.chmod(0o755)
    if os.geteuid() != 0:
        assert result.unreadable_folders == ("Locked",)
    assert "ok.jpg" in [r.relative_path for r in result.files]


def test_destination_preserves_relative_structure(folders):
    write(folders.source / "Family" / "VHS_001.mp4")
    record = scan(folders)["Family/VHS_001.mp4"]
    assert destination_path(record, folders.destination) == folders.destination / "Family" / "VHS_001.mp4"


def test_destination_rejects_paths_escaping_the_destination(folders):
    record = FileRecord(folders.source / "x", "../escape.mp4", 1, 1, 1, 1)
    with pytest.raises(UnsafeDestinationError):
        destination_path(record, folders.destination)


# 3. Canonical validation / 4. Nesting --------------------------------------


def test_valid_folders_have_no_errors(folders):
    assert validate_folders(str(folders.source), str(folders.destination)) == ()


def test_missing_and_non_folder_paths_are_rejected(tmp_path):
    file_path = write(tmp_path / "file.txt")
    errors = validate_folders(str(tmp_path / "missing"), str(file_path))
    assert any("does not exist" in e for e in errors)
    assert any("is not a folder" in e for e in errors)
    assert validate_folders("", "") == ("Choose a staging (source) folder.", "Choose a Synology (destination) folder.")


def test_identical_folders_are_rejected_even_via_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert "must be different" in validate_folders(str(real), str(link))[0]
    assert "must be different" in validate_folders(str(real), str(tmp_path / "real" / "." ))[0]


def test_nested_folders_are_rejected_both_ways(tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "a" / "inner"
    inner.mkdir(parents=True)
    assert "inside the Synology" in validate_folders(str(inner), str(outer))[0]
    assert "inside the staging" in validate_folders(str(outer), str(inner))[0]


def test_shared_name_prefix_is_not_treated_as_nesting(tmp_path):
    (tmp_path / "Scans").mkdir()
    (tmp_path / "Scans-Synology").mkdir()
    assert validate_folders(str(tmp_path / "Scans"), str(tmp_path / "Scans-Synology")) == ()
    assert not is_same_or_inside(tmp_path / "Scans-Synology", tmp_path / "Scans")


def test_nesting_through_a_symlink_is_detected(tmp_path):
    outer = tmp_path / "outer"
    (outer / "inner").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(outer / "inner")
    assert "inside the Synology" in validate_folders(str(alias), str(outer))[0]


def test_different_volumes_are_rejected(folders, monkeypatch):
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if Path(path) == folders.destination:
            values = list(info)
            values[2] = info.st_dev + 1  # st_dev
            return os.stat_result(values)
        return info

    monkeypatch.setattr(os, "stat", fake_stat)
    assert "same disk" in validate_folders(str(folders.source), str(folders.destination))[0]


# 5. Symlinks ----------------------------------------------------------------


def test_scan_does_not_follow_or_list_symlinks(folders, tmp_path):
    outside = write(tmp_path / "outside" / "secret.mp4")
    (folders.source / "file_link.mp4").symlink_to(outside)
    (folders.source / "dir_link").symlink_to(outside.parent)
    write(folders.source / "real.mp4")
    assert list(scan(folders)) == ["real.mp4"]


def test_source_replaced_by_symlink_is_not_moved(folders, tmp_path):
    source = write(folders.source / "clip.mp4")
    record = scan(folders)["clip.mp4"]
    source.unlink()
    source.symlink_to(write(tmp_path / "elsewhere.mp4"))

    result = move_file(record, folders, plain_rename)

    assert result.outcome is Outcome.UNSUPPORTED
    assert source.is_symlink()
    assert not (folders.destination / "clip.mp4").exists()


def test_destination_folder_that_is_a_symlink_is_refused(folders, tmp_path):
    write(folders.source / "Family" / "clip.mp4")
    record = scan(folders)["Family/clip.mp4"]
    (tmp_path / "elsewhere").mkdir()
    (folders.destination / "Family").symlink_to(tmp_path / "elsewhere")

    result = move_file(record, folders, plain_rename)

    assert result.outcome is Outcome.UNSUPPORTED
    assert (folders.source / "Family" / "clip.mp4").exists()
    assert list((tmp_path / "elsewhere").iterdir()) == []


# 6. Identity and metadata ---------------------------------------------------


def test_matches_record_detects_size_mtime_and_inode_changes(folders):
    path = write(folders.source / "clip.mp4", "abc")
    record = scan(folders)["clip.mp4"]
    info = os.stat(path)
    assert matches_record(record, info)
    assert not matches_record(replace(record, size=record.size + 1), info)
    assert not matches_record(replace(record, modified_ns=record.modified_ns + 1), info)
    assert not matches_record(replace(record, inode=record.inode + 1), info)
    assert not matches_record(replace(record, device=record.device + 1), info)


def test_file_that_grew_after_scan_is_skipped_as_changed(folders):
    path = write(folders.source / "recording.mov", "partial")
    record = scan(folders)["recording.mov"]
    with open(path, "a") as handle:
        handle.write(" more frames")

    result = move_file(record, folders, plain_rename)

    assert result.outcome is Outcome.CHANGED
    assert path.exists()


def test_file_replaced_with_a_different_file_is_skipped(folders):
    path = write(folders.source / "a.jpg", "one")
    record = scan(folders)["a.jpg"]
    path.unlink()
    write(folders.source / "other.tmp", "padding")  # keeps the old inode from being reused
    write(path, "two")
    os.utime(path, ns=(record.modified_ns, record.modified_ns))

    assert check_source(record, folders.source).outcome is Outcome.CHANGED


def test_record_from_a_different_source_folder_is_skipped(folders, tmp_path):
    write(folders.source / "a.jpg")
    record = scan(folders)["a.jpg"]
    (tmp_path / "other").mkdir()
    other = canonical_folders(str(tmp_path / "other"), str(folders.destination))
    assert move_file(record, other, plain_rename).outcome is Outcome.CHANGED


# 7. Selection / 8. Move All snapshot ---------------------------------------


def test_select_records_keeps_only_checked_files_in_order(folders):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        write(folders.source / name)
    records = scan_files(folders.source).files
    assert [r.relative_path for r in select_records(records, {"c.jpg", "a.jpg", "gone.jpg"})] == ["a.jpg", "c.jpg"]
    assert total_size(records) == 12


def test_move_selected_moves_only_selected_files(folders):
    for name in ("a.jpg", "b.jpg"):
        write(folders.source / name)
    selected = select_records(scan_files(folders.source).files, {"a.jpg"})

    move_files(selected, folders, rename=plain_rename)

    assert (folders.destination / "a.jpg").exists()
    assert (folders.source / "b.jpg").exists()
    assert not (folders.destination / "b.jpg").exists()


def test_move_all_snapshot_ignores_files_created_after_confirmation(folders):
    write(folders.source / "Family" / "one.mp4")
    snapshot = scan_files(folders.source).files
    write(folders.source / "Family" / "late.mp4")

    results = move_files(snapshot, folders, rename=plain_rename)

    assert [r.record.relative_path for r in results] == ["Family/one.mp4"]
    assert (folders.source / "Family" / "late.mp4").exists()
    assert not (folders.destination / "Family" / "late.mp4").exists()


def test_partition_current_separates_unchanged_from_stale(folders):
    write(folders.source / "keep.jpg")
    gone = write(folders.source / "gone.jpg")
    records = scan_files(folders.source).files
    gone.unlink()

    current, problems = partition_current(records, folders.source)

    assert [r.relative_path for r in current] == ["keep.jpg"]
    assert [(p.record.relative_path, p.outcome) for p in problems] == [("gone.jpg", Outcome.MISSING)]


# 9. Collisions / 10. Missing source / 11. Failures / 12. Success ------------


def test_existing_destination_is_never_overwritten(folders):
    write(folders.source / "Family" / "clip.mp4", "new")
    existing = write(folders.destination / "Family" / "clip.mp4", "original")
    record = scan(folders)["Family/clip.mp4"]

    result = move_file(record, folders)  # native adapter on macOS

    assert result.outcome is Outcome.EXISTS
    assert existing.read_text() == "original"
    assert (folders.source / "Family" / "clip.mp4").read_text() == "new"


def test_missing_source_is_reported_and_batch_continues(folders):
    gone = write(folders.source / "gone.jpg")
    write(folders.source / "here.jpg")
    records = scan_files(folders.source).files
    gone.unlink()

    results = move_files(records, folders, rename=plain_rename)

    assert [(r.record.relative_path, r.outcome) for r in results] == [
        ("gone.jpg", Outcome.MISSING),
        ("here.jpg", Outcome.MOVED),
    ]


def test_source_vanishing_during_rename_is_reported_as_missing(folders):
    write(folders.source / "a.jpg")
    record = scan(folders)["a.jpg"]

    def vanished(_source, _destination):
        raise FileNotFoundError(errno.ENOENT, "No such file")

    assert move_file(record, folders, vanished).outcome is Outcome.MISSING


@pytest.mark.parametrize(
    ("code", "expected_text"),
    [(errno.EACCES, "Permission denied"), (errno.EXDEV, "different disk"), (errno.EIO, "")],
)
def test_rename_failure_keeps_file_in_staging_and_continues(folders, code, expected_text):
    write(folders.source / "a.jpg")
    write(folders.source / "b.jpg")
    records = scan_files(folders.source).files
    calls = []

    def failing_then_working(source, destination):
        calls.append(source.name)
        if source.name == "a.jpg":
            raise OSError(code, os.strerror(code))
        plain_rename(source, destination)

    results = move_files(records, folders, rename=failing_then_working)

    assert calls == ["a.jpg", "b.jpg"]
    assert results[0].outcome is Outcome.FAILED
    assert expected_text in results[0].message
    assert (folders.source / "a.jpg").exists()
    assert results[1].outcome is Outcome.MOVED


def test_successful_move_creates_folders_and_keeps_the_same_file(folders):
    source = write(folders.source / "Family" / "1990" / "VHS_001.mp4", "video")
    record = scan(folders)["Family/1990/VHS_001.mp4"]
    reported = []

    results = move_files([record], folders, on_result=reported.append)

    destination = folders.destination / "Family" / "1990" / "VHS_001.mp4"
    assert results == tuple(reported)
    assert results[0] == MoveResult(record, Outcome.MOVED, str(destination))
    assert not source.exists()
    assert destination.read_text() == "video"
    assert os.stat(destination).st_ino == record.inode  # renamed, not copied


# 13. Native no-overwrite adapter ------------------------------------------


@macos_only
def test_rename_no_replace_refuses_to_replace_an_existing_file(tmp_path):
    source = write(tmp_path / "source.mp4", "new")
    destination = write(tmp_path / "destination.mp4", "original")

    with pytest.raises(FileExistsError):
        rename_no_replace(source, destination)

    assert source.read_text() == "new"
    assert destination.read_text() == "original"


@macos_only
def test_rename_no_replace_moves_atomically_when_destination_is_free(tmp_path):
    source = write(tmp_path / "source.mp4", "data")
    inode = os.stat(source).st_ino
    destination = tmp_path / "moved.mp4"

    rename_no_replace(source, destination)

    assert not source.exists()
    assert os.stat(destination).st_ino == inode


@macos_only
def test_rename_no_replace_reports_missing_source_and_symlinked_paths(tmp_path):
    with pytest.raises(FileNotFoundError):
        rename_no_replace(tmp_path / "missing", tmp_path / "anywhere")

    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    source = write(tmp_path / "source.mp4")
    with pytest.raises(OSError) as raised:
        rename_no_replace(source, tmp_path / "alias" / "source.mp4")
    assert raised.value.errno == errno.ELOOP
    assert source.exists()


# 14. Result aggregation -----------------------------------------------------


def make_results(*outcomes: Outcome) -> tuple[MoveResult, ...]:
    return tuple(
        MoveResult(FileRecord(Path(f"/s/{i}.mp4"), f"{i}.mp4", 1, 1, 1, i), outcome, f"reason {i}")
        for i, outcome in enumerate(outcomes)
    )


def test_summary_counts_and_wording():
    results = make_results(*[Outcome.MOVED] * 18, Outcome.EXISTS, Outcome.EXISTS, Outcome.CHANGED)

    assert count_outcomes(results)[Outcome.MOVED] == 18
    assert summary_lines(results) == (
        "18 files moved successfully.",
        "2 files skipped because they already exist in the destination.",
        "1 file skipped because it changed since selection.",
    )


def test_problem_lines_identify_failed_files_and_cap_output():
    results = make_results(Outcome.MOVED, *[Outcome.FAILED] * 4)
    lines = problem_lines(results, limit=2)
    assert lines == ("1.mp4: reason 1", "2.mp4: reason 2", "…and 2 more. See History for details.")


def test_outcome_categories_match_history_results():
    assert Outcome.MOVED.category == "success"
    assert Outcome.FAILED.category == "error"
    assert {o.category for o in (Outcome.EXISTS, Outcome.MISSING, Outcome.CHANGED, Outcome.UNSUPPORTED)} == {"skipped"}


@pytest.mark.parametrize(
    ("size", "text"),
    [(1, "1 byte"), (999, "999 bytes"), (1_500, "1.5 KB"), (4_300_000_000, "4.3 GB"), (2_000_000_000_000, "2.0 TB")],
)
def test_format_size(size, text):
    assert format_size(size) == text
