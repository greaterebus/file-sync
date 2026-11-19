"""py2app configuration for the standalone File Sync menu bar app.

Build with ./build.sh rather than running this directly.
"""

import sys
from pathlib import Path

from setuptools import setup

sys.path.insert(0, str(Path(__file__).parent / "src"))  # Lets py2app find the filesync package.

from filesync import __version__  # noqa: E402

PLIST = {
    "CFBundleName": "FileSync",
    "CFBundleDisplayName": "File Sync",
    "CFBundleIdentifier": "local.filesync.FileSync",
    "CFBundleShortVersionString": __version__,
    "CFBundleVersion": __version__,
    "LSMinimumSystemVersion": "14.0",
    "LSUIElement": True,  # Menu bar only: no Dock icon.
    "NSHumanReadableCopyright": "Moves finished scans and recordings into Synology Drive.",
    # Shown by macOS if the chosen folders live in a protected location.
    "NSDocumentsFolderUsageDescription": "File Sync moves files between the folders you choose.",
    "NSDesktopFolderUsageDescription": "File Sync moves files between the folders you choose.",
    "NSDownloadsFolderUsageDescription": "File Sync moves files between the folders you choose.",
    "NSRemovableVolumesUsageDescription": "File Sync moves files between the folders you choose.",
}

setup(
    name="FileSync",
    version=__version__,
    app=["src/filesync/__main__.py"],
    options={
        "py2app": {
            "plist": PLIST,
            "packages": ["filesync", "rumps"],
            "excludes": ["tkinter", "pytest", "_pytest", "unittest", "pydoc", "test"],
            "argv_emulation": False,
            "semi_standalone": False,
        }
    },
)
