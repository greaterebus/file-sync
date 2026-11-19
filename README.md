# File Sync

File Sync is a small Mac app for the photo and video digitization workflow. Scanners and
recorders save new files into a **staging folder**. When a batch is finished, an operator
clicks the File Sync icon in the menu bar and **moves** those files into the folder that
**Synology Drive Client** uploads to the Synology.

- **Always at hand:** it lives in the menu bar at the top right of the screen, starts when
  you log in, and uses no CPU while it waits.
- **You're in control:** nothing moves until you click **Move** and confirm. File Sync
  never watches folders or moves files on its own.
- **Moved, not copied:** a moved file leaves staging and appears in the Synology folder,
  instantly, even for large videos.
- **Folders are kept:** `Staging/Family/VHS_001.mp4` → `SynologyDrive/Scans/Family/VHS_001.mp4`.
- **Nothing is overwritten or deleted:** if a file with the same name already exists in
  the Synology folder, File Sync leaves both alone and tells you.

> **⚠️ Finish every recording before moving.** File Sync skips any file that changed since
> the list was refreshed, but it can't tell whether a program is still writing to it.
> Stop the recording, click **Refresh**, then move.

Requires macOS 14 Sonoma or newer, on Intel or Apple Silicon. Nothing else needs to be
installed.

---

## Daily use at a glance

1. Finish the scans or recordings.
2. Click the File Sync icon (a tray with an upward arrow) in the menu bar → **Open File Sync**.
3. Check the list of files waiting in staging.
4. Click **Move All…**, or tick the files you want and click **Move Selected…**.
5. Read the confirmation and click **Move**.
6. Read the summary. The files are now in the Synology folder, and Synology Drive Client
   uploads them from there.

---

## Installing

Download **FileSync-macOS.dmg** from the
[Releases page](https://github.com/greaterebus/file-sync/releases). Test versions are
marked *Pre-release*.

1. Double-click `FileSync-macOS.dmg`. A window opens showing FileSync, an Applications
   folder, and a "How to Install" note.
2. Drag **FileSync** onto **Applications**.
3. Eject the "File Sync" disk in Finder's sidebar, then open FileSync from Applications.

### Approving the app the first time

File Sync is free and isn't registered with Apple, so the first time you open it macOS
says it can't verify the app. This is expected, and you only need to do this once:

1. Click **Done** on the warning.
2. Open **System Settings → Privacy & Security**.
3. Scroll down to the message about "FileSync" and click **Open Anyway**.
4. Confirm with **Open Anyway**, and enter your password if asked.

Don't turn off Gatekeeper or change any other security setting. Approving this one app
is all that's needed.

If your staging or Synology folder is in Documents, Desktop, Downloads, or on an
external drive, macOS may also ask whether File Sync can access it. Click **Allow**.

---

## Setting up

The **Settings** window opens by itself the first time. Reopen it later from the menu
bar icon → **Settings…**, or the **Settings…** button in the main window.

1. **Staging folder** → **Choose…**: the folder where scanners and recorders save files.
2. **Synology folder** → **Choose…**: the local folder that Synology Drive Client syncs.
3. Leave **Open File Sync automatically when I log in** ticked, so the icon is always in
   the menu bar after a restart.
4. Click **Save**.

File Sync won't save folders it can't use safely. Both must exist, be different, not be
inside one another, and be on the same disk. That last rule is what makes moves instant
and lets File Sync avoid ever copying files.

To stop File Sync opening at login, untick the box in Settings, or remove it under
**System Settings → General → Login Items**.

---

## Using File Sync

### The file list

**Open File Sync** shows every file in the staging folder and its subfolders, with its
name, folder, size, and modified time. Above the list are the two folders in use, and
below it the number of files and their total size.

The list is read only when you open the window, click **Refresh** (or **Refresh Files**
in the menu), or finish a move. It doesn't update by itself, so click **Refresh** after
a recording finishes.

### Move Selected

1. Tick the files to move. **Select All** and **Deselect All** help with long lists.
2. Click **Move Selected…**.
3. The confirmation shows how many files, their total size, and where they're going.
   Click **Move**.

Only the ticked files are moved, even if new files have appeared since.

### Move All

1. Click **Move All…**. File Sync rereads staging and shows the count and total size.
2. Click **Move**. Return cancels this one, so a stray keypress can't start it.

Only the files counted in the confirmation are moved. Anything that arrives afterwards
stays in staging until the next Move All.

### The summary

When a move finishes, File Sync shows what happened, for example:

> 18 files moved successfully.
> 2 files skipped because they already exist in the destination.
> 1 file skipped because it changed since selection.

It then lists each file that wasn't moved and why. Those files stay in staging.

| Situation | What File Sync does |
|---|---|
| A file with the same name is already in the Synology folder | Skips it and leaves both files untouched. It never renames or replaces. Rename or remove one yourself, then move again. |
| The file changed after the list was read (for example, still recording) | Skips it. Let the recording finish, click **Refresh**, and move again. |
| The file was deleted or moved by something else | Skips it. |
| Permission or disk problem | Leaves the file in staging and carries on with the rest. |
| The Mac sleeps, loses power, or File Sync is closed mid-move | Each file moves in a single step, so it is either still in staging or fully in the Synology folder. Click **Refresh** and move the rest. |

### History

Click **History** in the main window to see past moves. Use **All / Moved / Skipped /
Failed** to filter. The window shows the most recent 1,000 entries.

### Quitting

Menu bar icon → **Quit File Sync**. While a move is running, File Sync asks you to wait
until it finishes. To start it again, open FileSync from Applications, or log out and
back in if it opens at login.

---

## How a move works

For each file, File Sync:

1. **Rechecks the file.** It makes sure the file is still there, is a regular file and
   not a link, and is the same file you saw: same identity on disk, same size, same
   modified time. If anything differs, the file is skipped.
2. **Makes the matching folders** inside the Synology folder, such as `Family/1990/`.
   It refuses to go through anything that isn't a real folder.
3. **Renames the file into place.** This is one instant step on the same disk, so the
   data is never copied and Synology Drive never sees a half-written file. The macOS
   call it uses (`renameatx_np` with `RENAME_EXCL`) fails rather than replace an
   existing file.
4. **Records the result** in the history log, then moves on to the next file. A problem
   with one file never stops the rest.

File Sync never deletes files. It ignores Finder's hidden `.DS_Store` files, doesn't
follow symbolic links, and doesn't move empty folders.

Its own data lives in `~/Library/Application Support/FileSync/`:

- `settings.json`: the two folders.
- `history.jsonl`: one line per file processed, kept as a permanent record.

---

## Development

| File | Responsibility |
|---|---|
| `src/filesync/files.py` | Finding files, checking folders, the no-overwrite move, and result summaries. All file-safety rules live here. |
| `src/filesync/settings.py` | Loading and saving `settings.json`. |
| `src/filesync/history.py` | Appending to and reading `history.jsonl`. It reads only the end of the file. |
| `src/filesync/app.py` | The menu bar item, windows, and dialogs, plus the open-at-login switch. Scans and moves run on a short-lived worker thread so the window stays responsive. |

### Run from source and test

```bash
/path/to/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m filesync   # run the app
.venv/bin/pytest                              # run the tests
```

When run from source, the open-at-login checkbox is disabled. It only works in the built
app.

### Build the disk image locally

Install Python 3.12 using the **macOS 64-bit universal2 installer** from
<https://www.python.org/downloads/macos/>, then run:

```bash
./build.sh
```

This creates `.venv`, installs the pinned dependencies, runs the tests, and builds
`dist/FileSync.app` with py2app. It then signs the app ad hoc (the free kind of
signature) and packages `dist/FileSync-macOS.dmg`.

The python.org Python matters because py2app copies the Python you build with into the
app. The python.org build supports older macOS versions and both kinds of Mac. Homebrew's
Python only supports the macOS version it was installed on, so `build.sh` refuses to
package an app that wouldn't open on macOS 14. For a build only for your own Mac, run
`ALLOW_HOST_ONLY_BUILD=1 PYTHON=/opt/homebrew/bin/python3.12 ./build.sh`.

### Release a version

GitHub Actions builds and publishes releases (`.github/workflows/release.yml`):

1. Set `__version__` in `src/filesync/__init__.py`, for example `"1.0.1"` or
   `"1.1.0-beta.1"`, and commit.
2. Tag with the same version and push: `git tag v1.0.1 && git push origin main v1.0.1`.

The workflow builds on a GitHub-hosted Mac with the python.org Python and attaches
`FileSync-macOS.dmg` to a GitHub Release. Tags with a suffix, such as `-beta.1`, become
pre-releases. The workflow stops if the tag and `__version__` don't match. To test a build
without publishing, use **Run workflow** on the Actions tab; the disk image is attached
to that run.

Inside the app, macOS shows only the numeric part of the version (`1.1.0` for
`1.1.0-beta.1`).

Not included on purpose: paid Apple Developer ID signing and notarization, installer
packages, and automatic updates.
