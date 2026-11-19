"""File discovery, folder validation, and safe file moves.

All filesystem safety rules live in this module. Nothing here touches the UI.

Moves use the macOS ``renameatx_np`` system call with ``RENAME_EXCL``, so a file
is either renamed atomically into place or left untouched in staging. Files are
never copied, never overwritten, and never deleted.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
from dataclasses import dataclass
from enum import Enum
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

# Finder recreates this metadata file in every folder it opens. Moving it would
# only produce a conflict on every later Move All, so it is never listed.
IGNORED_FILE_NAMES = frozenset({".DS_Store"})


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FileRecord:
    """A file in staging, as it looked when the folder was scanned."""

    source_path: Path
    relative_path: str  # POSIX-style path relative to the staging folder
    size: int
    modified_ns: int
    device: int
    inode: int

    @property
    def name(self) -> str:
        return PurePosixPath(self.relative_path).name

    @property
    def directory(self) -> str:
        parent = str(PurePosixPath(self.relative_path).parent)
        return "" if parent == "." else parent


@dataclass(frozen=True)
class ScanResult:
    files: tuple[FileRecord, ...]
    unreadable_folders: tuple[str, ...]


@dataclass(frozen=True)
class Folders:
    """Canonical, validated source and destination folders."""

    source: Path
    destination: Path


class Outcome(Enum):
    MOVED = "moved"
    EXISTS = "exists"
    MISSING = "missing"
    CHANGED = "changed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"

    @property
    def category(self) -> str:
        """The history result: success, skipped, or error."""
        if self is Outcome.MOVED:
            return "success"
        if self is Outcome.FAILED:
            return "error"
        return "skipped"


@dataclass(frozen=True)
class MoveResult:
    record: FileRecord
    outcome: Outcome
    message: str = ""


class UnsafeDestinationError(Exception):
    """The destination path would leave the destination folder or pass through a non-folder."""


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def record_from_stat(source_path: Path, relative_path: str, info: os.stat_result) -> FileRecord:
    return FileRecord(
        source_path=source_path,
        relative_path=relative_path,
        size=info.st_size,
        modified_ns=info.st_mtime_ns,
        device=info.st_dev,
        inode=info.st_ino,
    )


def scan_files(source_root: Path) -> ScanResult:
    """Recursively list regular files under ``source_root`` without following symlinks.

    File contents are never read. Subfolders that cannot be read are reported,
    not fatal. An unreadable ``source_root`` raises ``OSError``.
    """
    root = Path(os.path.realpath(source_root))
    records: list[FileRecord] = []
    unreadable: list[str] = []
    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as iterator:
                entries = list(iterator)
        except OSError:
            if folder == root:
                raise
            unreadable.append(folder.relative_to(root).as_posix())
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False) and entry.name not in IGNORED_FILE_NAMES:
                    info = entry.stat(follow_symlinks=False)
                    records.append(record_from_stat(path, path.relative_to(root).as_posix(), info))
            except OSError:
                continue  # Disappeared or became unreadable while scanning.
    return ScanResult(
        files=tuple(sorted(records, key=lambda r: r.relative_path.casefold())),
        unreadable_folders=tuple(sorted(unreadable)),
    )


def select_records(records: Iterable[FileRecord], selected_paths: set[str]) -> tuple[FileRecord, ...]:
    """Keep only the records the user checked, in their original order."""
    return tuple(r for r in records if r.relative_path in selected_paths)


def total_size(records: Iterable[FileRecord]) -> int:
    return sum(r.size for r in records)


# --------------------------------------------------------------------------
# Folder validation
# --------------------------------------------------------------------------


def _folder_errors(label: str, raw_path: str) -> list[str]:
    if not raw_path:
        return [f"Choose a {label} folder."]
    path = Path(raw_path).expanduser()
    if not path.exists():
        return [f"The {label} folder does not exist: {path}"]
    if not path.is_dir():
        return [f"The {label} folder is not a folder: {path}"]
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        return [f"The {label} folder cannot be accessed. Check its permissions: {path}"]
    return []


def _identity(path: Path) -> tuple[int, int]:
    info = os.stat(path)
    return info.st_dev, info.st_ino


def is_same_or_inside(path: Path, ancestor: Path) -> bool:
    """True if ``path`` is ``ancestor`` or lies inside it.

    Compares folder identities (device and inode) rather than path strings, so
    it is not fooled by letter case, symlinks, or names sharing a prefix.
    Both paths must exist.
    """
    target = _identity(ancestor)
    canonical = Path(os.path.realpath(path))
    return any(_identity(candidate) == target for candidate in (canonical, *canonical.parents))


def canonical_folders(source: str, destination: str) -> Folders:
    return Folders(
        source=Path(os.path.realpath(Path(source).expanduser())),
        destination=Path(os.path.realpath(Path(destination).expanduser())),
    )


def validate_folders(source: str, destination: str) -> tuple[str, ...]:
    """Return human-readable problems with the folder pair. Empty means valid."""
    errors = _folder_errors("staging (source)", source) + _folder_errors(
        "Synology (destination)", destination
    )
    if errors:
        return tuple(errors)
    folders = canonical_folders(source, destination)
    try:
        if _identity(folders.source) == _identity(folders.destination):
            return ("The staging and Synology folders must be different folders.",)
        if is_same_or_inside(folders.source, folders.destination):
            return ("The staging folder cannot be inside the Synology folder.",)
        if is_same_or_inside(folders.destination, folders.source):
            return ("The Synology folder cannot be inside the staging folder.",)
        if os.stat(folders.source).st_dev != os.stat(folders.destination).st_dev:
            return ("The staging and Synology folders must be on the same disk.",)
    except OSError as error:
        return (f"The folders could not be checked: {error.strerror or error}",)
    return ()


# --------------------------------------------------------------------------
# No-overwrite rename (macOS)
# --------------------------------------------------------------------------

AT_FDCWD = -2
RENAME_EXCL = 0x00000004  # Fail with EEXIST if the destination exists.
RENAME_NOFOLLOW_ANY = 0x00000010  # Fail with ELOOP if any path component is a symlink.


@cache
def _renameatx_np() -> Callable[..., int]:
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = libc.renameatx_np
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    return function


def rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename ``source`` to ``destination``, never replacing an existing file.

    Raises ``FileExistsError`` if the destination exists, and another
    ``OSError`` for any other failure (for example ``EXDEV`` across volumes).
    There is no copy fallback: the file is either renamed or left in place.
    """
    rename = _renameatx_np()
    source_bytes, destination_bytes = os.fsencode(source), os.fsencode(destination)
    flags = RENAME_EXCL | RENAME_NOFOLLOW_ANY
    if rename(AT_FDCWD, source_bytes, AT_FDCWD, destination_bytes, flags) == 0:
        return
    code = ctypes.get_errno()
    if code == errno.EINVAL:
        # A kernel that does not recognise RENAME_NOFOLLOW_ANY rejects the
        # flags before doing anything. Retry with the essential no-replace flag.
        if rename(AT_FDCWD, source_bytes, AT_FDCWD, destination_bytes, RENAME_EXCL) == 0:
            return
        code = ctypes.get_errno()
    raise OSError(code, os.strerror(code), str(source), None, str(destination))


# --------------------------------------------------------------------------
# Safe move
# --------------------------------------------------------------------------


def _relative_parts(relative_path: str) -> tuple[str, ...]:
    pure = PurePosixPath(relative_path)
    parts = pure.parts
    if pure.is_absolute() or not parts or any(part in ("", ".", "..") for part in parts):
        raise UnsafeDestinationError(f"Unsafe relative path: {relative_path!r}")
    return parts


def matches_record(record: FileRecord, info: os.stat_result) -> bool:
    """True if the file on disk is still the same, unchanged file the user saw."""
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_dev == record.device
        and info.st_ino == record.inode
        and info.st_size == record.size
        and info.st_mtime_ns == record.modified_ns
    )


def check_source(record: FileRecord, source_root: Path) -> MoveResult | None:
    """Return a skip/failure result if the source is no longer safe to move, else None."""
    try:
        expected = source_root.joinpath(*_relative_parts(record.relative_path))
    except UnsafeDestinationError as error:
        return MoveResult(record, Outcome.UNSUPPORTED, str(error))
    if expected != record.source_path:
        return MoveResult(record, Outcome.CHANGED, "The staging folder changed. Refresh the list.")
    try:
        info = os.lstat(record.source_path)
    except FileNotFoundError:
        return MoveResult(record, Outcome.MISSING, "The file no longer exists in staging.")
    except OSError as error:
        return MoveResult(record, Outcome.FAILED, describe_os_error(error))
    if stat.S_ISLNK(info.st_mode):
        return MoveResult(record, Outcome.UNSUPPORTED, "The file is a symbolic link.")
    if not stat.S_ISREG(info.st_mode):
        return MoveResult(record, Outcome.UNSUPPORTED, "The item is not a regular file.")
    if not matches_record(record, info):
        return MoveResult(record, Outcome.CHANGED, "The file changed since the list was refreshed.")
    return None


def partition_current(
    records: Iterable[FileRecord], source_root: Path
) -> tuple[tuple[FileRecord, ...], tuple[MoveResult, ...]]:
    """Split records into those still unchanged on disk and results for those that are not."""
    current: list[FileRecord] = []
    problems: list[MoveResult] = []
    for record in records:
        problem = check_source(record, source_root)
        if problem is None:
            current.append(record)
        else:
            problems.append(problem)
    return tuple(current), tuple(problems)


def destination_path(record: FileRecord, destination_root: Path) -> Path:
    """Where the record goes, guaranteed to stay inside ``destination_root``."""
    return destination_root.joinpath(*_relative_parts(record.relative_path))


def create_parent_folders(destination_root: Path, destination: Path) -> None:
    """Create missing folders between the root and the destination file.

    Refuses to pass through anything that is not a real folder (such as a symlink).
    """
    current = destination_root
    for part in destination.relative_to(destination_root).parts[:-1]:
        current = current / part
        try:
            os.mkdir(current)
        except FileExistsError:
            pass
        if not stat.S_ISDIR(os.lstat(current).st_mode):
            raise UnsafeDestinationError(f"Destination path is not a folder: {current}")


def describe_os_error(error: OSError) -> str:
    if error.errno in (errno.EACCES, errno.EPERM):
        return "Permission denied."
    if error.errno == errno.EXDEV:
        return "The destination is on a different disk. Files can only be moved within one disk."
    if error.errno == errno.ENOSPC:
        return "The disk is full."
    return error.strerror or str(error)


def _rename_failure(record: FileRecord, error: OSError) -> MoveResult:
    if error.errno == errno.EEXIST:
        return MoveResult(record, Outcome.EXISTS, "A file with this name already exists in the destination.")
    if error.errno == errno.ENOENT:
        return MoveResult(record, Outcome.MISSING, "The file disappeared before it could be moved.")
    if error.errno == errno.ELOOP:
        return MoveResult(record, Outcome.UNSUPPORTED, "The path contains a symbolic link.")
    return MoveResult(record, Outcome.FAILED, describe_os_error(error))


Rename = Callable[[Path, Path], None]


def move_file(record: FileRecord, folders: Folders, rename: Rename = rename_no_replace) -> MoveResult:
    """Move one file from staging to the same relative path in the destination."""
    problem = check_source(record, folders.source)
    if problem is not None:
        return problem
    try:
        destination = destination_path(record, folders.destination)
        create_parent_folders(folders.destination, destination)
    except UnsafeDestinationError as error:
        return MoveResult(record, Outcome.UNSUPPORTED, str(error))
    except OSError as error:
        return MoveResult(record, Outcome.FAILED, f"Could not create destination folder: {describe_os_error(error)}")
    try:
        rename(record.source_path, destination)
    except OSError as error:
        return _rename_failure(record, error)
    return MoveResult(record, Outcome.MOVED, str(destination))


def move_files(
    records: Iterable[FileRecord],
    folders: Folders,
    on_result: Callable[[MoveResult], None] = lambda _result: None,
    rename: Rename = rename_no_replace,
) -> tuple[MoveResult, ...]:
    """Move each record independently; one failure never stops the rest."""
    results = []
    for record in records:
        result = move_file(record, folders, rename)
        results.append(result)
        on_result(result)
    return tuple(results)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

# (phrase for one file, phrase for several files)
_SUMMARY_PHRASES = {
    Outcome.MOVED: ("moved successfully", "moved successfully"),
    Outcome.EXISTS: (
        "skipped because it already exists in the destination",
        "skipped because they already exist in the destination",
    ),
    Outcome.CHANGED: ("skipped because it changed since selection", "skipped because they changed since selection"),
    Outcome.MISSING: ("skipped because it no longer exists", "skipped because they no longer exist"),
    Outcome.UNSUPPORTED: (
        "skipped because it is not a regular file",
        "skipped because they are not regular files",
    ),
    Outcome.FAILED: ("failed to move", "failed to move"),
}


def count_outcomes(results: Iterable[MoveResult]) -> dict[Outcome, int]:
    counts = {outcome: 0 for outcome in Outcome}
    for result in results:
        counts[result.outcome] += 1
    return counts


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural_form or singular + 's')}"


def summary_lines(results: Iterable[MoveResult]) -> tuple[str, ...]:
    """One line per outcome that occurred, e.g. '18 files moved successfully.'"""
    return tuple(
        f"{plural(count, 'file')} {_SUMMARY_PHRASES[outcome][0 if count == 1 else 1]}."
        for outcome, count in count_outcomes(results).items()
        if count
    )


def problem_lines(results: Iterable[MoveResult], limit: int = 10) -> tuple[str, ...]:
    """Identify files that were not moved, capped at ``limit`` lines."""
    problems = [r for r in results if r.outcome is not Outcome.MOVED]
    lines = [f"{r.record.relative_path}: {r.message}" for r in problems[:limit]]
    if len(problems) > limit:
        lines.append(f"…and {len(problems) - limit} more. See History for details.")
    return tuple(lines)


def format_size(size: int) -> str:
    """Human-readable size using decimal units, matching Finder."""
    if size < 1000:
        return plural(size, "byte")
    value = size / 1000
    for unit in ("KB", "MB", "GB"):
        if value < 1000:
            return f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"
