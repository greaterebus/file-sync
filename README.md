# File Sync

A small macOS menu bar app for the photo and video digitization workflow. Scanners
and recorders save files into a **staging folder**. When recordings are finished, an
operator uses File Sync to **move** those files into the local folder that
**Synology Drive Client** syncs.

- File Sync **only moves files when you click Move**. It never watches folders or moves anything on its own.
- Files are **moved, not copied**. After a move they are no longer in staging.
- Subfolders are kept: `Staging/Family/VHS_001.mp4` → `SynologyDrive/Scans/Family/VHS_001.mp4`.
- Existing files in the Synology folder are **never overwritten**.

> **⚠️ Finish every recording before moving.** File Sync skips a file if it changed
> since the list was refreshed, but it cannot guarantee that no other program is still
> writing to it. Stop the recording, then click **Refresh**, then move.

Requires macOS 14 Sonoma or newer. Python does not need to be installed.

---

## Installing

Download the latest version:
**[FileSync-macOS.dmg](https://github.com/greaterebus/file-sync/releases/latest/download/FileSync-macOS.dmg)**
(all versions are on the [Releases page](https://github.com/greaterebus/file-sync/releases)).

1. Double-click `FileSync-macOS.dmg`. A window opens showing FileSync and an Applications folder.
2. Drag **FileSync** onto **Applications**.
3. Eject the "File Sync" disk in Finder's sidebar, then open FileSync from Applications.

### First launch and the macOS security warning

File Sync is not signed with an Apple Developer ID or notarized by Apple, so the first
time you open it macOS will say it cannot verify the app. This is expected. To approve
it once:

1. Try to open FileSync.app, then click **Done** (or **Cancel**) on the warning.
2. Open **System Settings → Privacy & Security**.
3. Scroll down to the message about "FileSync" and click **Open Anyway**.
4. Confirm with **Open Anyway** and your password if asked.

After this, the app opens normally. Do **not** turn off Gatekeeper or change other
security settings. Approving this one app is all that's needed.

If your Mac's staging or Synology folder is in Documents, Desktop, Downloads, or on an
external drive, macOS may also ask whether File Sync can access that folder. Click **Allow**.

File Sync appears as a tray icon with an upward arrow in the menu bar at the top right of
the screen. It does not appear in the Dock. It stays there, using no CPU, until you click
it, and closing its window does not quit it.

---

## Using File Sync

### Choose your folders (first time only)

The Settings window opens automatically the first time. You can reopen it at any time
from the menu bar icon → **Settings…** or the **Settings…** button in the main window.

- **Staging folder**: where scanners and recorders save new files.
- **Synology folder**: the local folder that Synology Drive Client syncs.

Leave **Open File Sync automatically when I log in** ticked so the menu bar icon is
always there after restarting the Mac. You can untick it here later, or remove File Sync
under **System Settings → General → Login Items**.

Click **Choose…** next to each, then **Save**. File Sync checks that both folders exist,
are different, are not inside each other, and are on the same disk. Moving files is
instant within one disk, and File Sync never copies files between disks.

### See what's in staging

Click the menu bar icon → **Open File Sync**. The window lists every file in the staging
folder and its subfolders, with name, folder, size, and modified time.

The list updates only when you open the window, click **Refresh** (or **Refresh Files**
in the menu), or finish a move. Click **Refresh** after a recording finishes.

### Move Selected

1. Tick the checkbox next to each file to move. **Select All** and **Deselect All** help.
2. Click **Move Selected…**.
3. Read the confirmation (number of files, total size, destination), then click **Move**.

Only the ticked files are moved, even if new files have appeared since.

### Move All

1. Click **Move All…**. File Sync rescans staging and shows the count and total size.
2. Click **Move** to confirm. Pressing Return cancels, so a stray keypress can't start it.

Only the files counted in the confirmation are moved. Files that arrive afterwards stay
in staging until you run Move All again.

### Results

After every move, a summary appears, for example:

> 18 files moved successfully.
> 2 files skipped because they already exist in the destination.
> 1 file skipped because it changed since selection.

…followed by the name of each file that was not moved and why. Files that were not
moved stay in staging.

| Situation | What File Sync does |
|---|---|
| A file with the same name already exists in the Synology folder | Skips it and leaves both files untouched. It never renames or overwrites. Rename or remove one of them yourself, then move again. |
| The file changed after the list was refreshed (for example, still recording) | Skips it. Wait for the recording to finish, click Refresh, then move again. |
| The file was deleted or moved by something else | Skips it. |
| Permission or disk error | Leaves the file in staging and continues with the rest. |
| The Mac sleeps, crashes, or the app is quit mid-move | Each file is moved in one indivisible step, so every file is either fully in staging or fully in the Synology folder. Refresh and Move All again to finish. |

### History

Click **History** in the main window to see recent moves. Use **All / Moved / Skipped /
Failed** to filter. The full log is kept at
`~/Library/Application Support/FileSync/history.jsonl`. Settings are in `settings.json`
in the same folder.

### Quitting

Menu bar icon → **Quit File Sync**. If a move is in progress, wait for it to finish first.
To bring it back, open FileSync from Applications. If it opens at login, it also returns
after the next restart.

### Notes

- Finder's hidden `.DS_Store` files are ignored.
- Symbolic links (aliases made in Terminal) are never followed or moved.
- Empty folders are not moved.

---

## Development

The code lives in `src/filesync/`:

| File | Responsibility |
|---|---|
| `files.py` | Discovery, folder validation, the no-overwrite move (`renameatx_np` with `RENAME_EXCL`), and result summaries. All filesystem safety rules live here. |
| `settings.py` | Load and save `settings.json`. |
| `history.py` | Append to and read from `history.jsonl` (reads only the tail of the file). |
| `app.py` | Menu bar, windows, dialogs. Runs scans and moves on a short-lived worker thread. |

### Run from source

```bash
/path/to/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m filesync
```

### Run the tests

```bash
.venv/bin/pytest
```

### Build the disk image

Install Python 3.12 from python.org first: the **macOS 64-bit universal2 installer**
from <https://www.python.org/downloads/macos/>. Then:

```bash
./build.sh
```

The script creates `.venv` from that Python, installs pinned dependencies, runs the
tests, builds `dist/FileSync.app` with py2app, applies an ad-hoc code signature, and
packages **`dist/FileSync-macOS.dmg`**: a disk image with the app, an Applications
shortcut, and a short "How to Install" note. Send the DMG to users.

Why python.org Python: py2app copies the Python you build with into the app. The
python.org build supports old macOS versions and both Intel and Apple Silicon. Homebrew's
Python is compiled only for the macOS version it was installed on. `build.sh` checks the
bundled binaries and refuses to package an app that would not open on macOS 14. For a
build that only needs to run on your own Mac, use
`ALLOW_HOST_ONLY_BUILD=1 PYTHON=/opt/homebrew/bin/python3.12 ./build.sh`.

### Release a new version

GitHub Actions builds and publishes the disk image (`.github/workflows/release.yml`).

1. Set `__version__` in `src/filesync/__init__.py` (for example `"1.0.1"`) and commit.
2. Tag and push: `git tag v1.0.1 && git push origin main v1.0.1`.

The workflow installs the python.org Python on a macOS runner, runs `./build.sh`, and
attaches `FileSync-macOS.dmg` to a new GitHub Release. The download link above always
points at the newest release. It refuses to run if the tag and `__version__` differ. To
test a build without publishing, use **Run workflow** on the Actions tab; the disk image
is attached to that run.

Not included by design: Developer ID signing, notarization, installer packages, and
automatic updates.
