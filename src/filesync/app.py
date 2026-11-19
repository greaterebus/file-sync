"""Menu bar app, windows, and dialogs.

All filesystem decisions are delegated to ``files``. This module only gathers
user intent, runs the requested work on a short-lived worker thread so the UI
stays responsive, and shows the results.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import objc
import rumps
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleInformational,
    NSAlertStyleWarning,
    NSBundle,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezelBorder,
    NSButton,
    NSButtonCell,
    NSButtonTypeSwitch,
    NSColor,
    NSFont,
    NSImage,
    NSLineBreakByTruncatingMiddle,
    NSLineBreakByTruncatingTail,
    NSModalResponseOK,
    NSOpenPanel,
    NSPathControl,
    NSProgressIndicator,
    NSProgressIndicatorStyleSpinning,
    NSScrollView,
    NSSegmentedControl,
    NSTableColumn,
    NSTableView,
    NSTableViewLastColumnOnlyAutoresizingStyle,
    NSTextAlignmentRight,
    NSTextField,
    NSURL,
    NSViewHeightSizable,
    NSViewMaxYMargin,
    NSViewMinXMargin,
    NSViewMinYMargin,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect, NSObject
from PyObjCTools import AppHelper
from ServiceManagement import SMAppService, SMAppServiceStatusEnabled, SMAppServiceStatusRequiresApproval

from . import BUNDLE_ID, files, history
from .files import FileRecord, Folders, MoveResult, Outcome, ScanResult, format_size, plural
from .settings import Settings, load_settings, save_settings

RECORDING_WARNING = (
    "Finish all scans and recordings before moving. Files are moved, not copied: "
    "they leave the staging folder and go to the Synology folder."
)

# --------------------------------------------------------------------------
# Work performed on the worker thread (no UI calls here)
# --------------------------------------------------------------------------


class ConfigurationError(Exception):
    def __init__(self, problems: tuple[str, ...]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class MovePlan:
    folders: Folders
    records: tuple[FileRecord, ...]  # Files to move, as confirmed by the user.
    skipped: tuple[MoveResult, ...] = ()  # Files already known to be unmovable.
    scan: ScanResult | None = None  # Fresh inventory, for Move All.


def require_folders(current: Settings) -> Folders:
    problems = files.validate_folders(current.source, current.destination)
    if problems:
        raise ConfigurationError(problems)
    return files.canonical_folders(current.source, current.destination)


def take_inventory(current: Settings) -> ScanResult:
    return files.scan_files(require_folders(current).source)


def plan_selected(current: Settings, selected: tuple[FileRecord, ...]) -> MovePlan:
    folders = require_folders(current)
    records, skipped = files.partition_current(selected, folders.source)
    return MovePlan(folders, records, skipped)


def plan_all(current: Settings) -> MovePlan:
    folders = require_folders(current)
    scan = files.scan_files(folders.source)
    return MovePlan(folders, scan.files, (), scan)


def execute_plan(plan: MovePlan, on_progress: Callable[[int, int], None]) -> tuple[tuple[MoveResult, ...], str]:
    """Move the planned files, logging each outcome as it happens.

    Returns the results and a history error message ("" if history was written).
    A history failure never affects or repeats a filesystem move.
    """
    history_errors: list[str] = []

    def log(results: tuple[MoveResult, ...]) -> None:
        now = datetime.now().astimezone()
        try:
            history.append_history(history.entry_from_result(r, now) for r in results)
        except OSError as error:
            history_errors.append(error.strerror or str(error))

    log(plan.skipped)
    total = len(plan.records)
    finished = 0

    def on_result(result: MoveResult) -> None:
        nonlocal finished
        finished += 1
        log((result,))
        on_progress(finished, total)

    moved = files.move_files(plan.records, plan.folders, on_result)
    return plan.skipped + moved, (history_errors[0] if history_errors else "")


def run_in_background(work: Callable[[], object], on_done: Callable[[object, Exception | None], None]) -> None:
    """Run ``work`` on a worker thread, then call ``on_done(result, error)`` on the main thread."""

    def runner() -> None:
        try:
            result, error = work(), None
        except Exception as exception:  # Reported to the user by on_done.
            result, error = None, exception
        AppHelper.callAfter(on_done, result, error)

    threading.Thread(target=runner, name="filesync-worker", daemon=True).start()


# --------------------------------------------------------------------------
# Text shown to the user (pure)
# --------------------------------------------------------------------------


def confirmation_message(plan: MovePlan) -> str:
    size = format_size(files.total_size(plan.records))
    lines = [
        f"{plural(len(plan.records), 'file')} ({size}) will be moved",
        f"from:  {plan.folders.source}",
        f"to:  {plan.folders.destination}",
        "",
        "The original files will be MOVED, not copied. They will no longer be in the staging folder.",
        "",
        "Make sure every scan and recording is finished. Do not move a video that is still recording.",
        "",
        "Files that already exist in the destination are skipped, never overwritten.",
    ]
    if plan.skipped:
        lines += ["", f"{plural(len(plan.skipped), 'selected file')} changed or disappeared and will be skipped."]
    return "\n".join(lines)


def result_message(results: tuple[MoveResult, ...], history_error: str) -> str:
    sections = ["\n".join(files.summary_lines(results)) or "No files were processed."]
    problems = files.problem_lines(results)
    if problems:
        sections.append("Not moved:\n" + "\n".join(problems))
    if history_error:
        sections.append(f"The history log could not be updated ({history_error}). The moves above still happened.")
    return "\n\n".join(sections)


def inventory_status(records: tuple[FileRecord, ...], checked: set[str], unreadable: tuple[str, ...]) -> str:
    if not records:
        status = "The staging folder is empty."
    else:
        selected = files.select_records(records, checked)
        status = f"{plural(len(records), 'file')} in staging ({format_size(files.total_size(records))})"
        if selected:
            status += f"  ·  {len(selected)} selected ({format_size(files.total_size(selected))})"
    if unreadable:
        status += f"  ·  {plural(len(unreadable), 'folder')} could not be read: {', '.join(unreadable[:3])}"
    return status


def format_timestamp(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------
# Small AppKit helpers
# --------------------------------------------------------------------------


def bring_to_front() -> None:
    app = NSApplication.sharedApplication()
    if app.respondsToSelector_("activate"):
        app.activate()
    else:
        app.activateIgnoringOtherApps_(True)


def show_alert(title: str, message: str, warning: bool = False) -> None:
    bring_to_front()
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.setAlertStyle_(NSAlertStyleWarning if warning else NSAlertStyleInformational)
    alert.addButtonWithTitle_("OK")
    alert.runModal()


def ask_to_confirm(title: str, message: str, action: str, cancel_is_default: bool) -> bool:
    bring_to_front()
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.setAlertStyle_(NSAlertStyleWarning)
    action_button = alert.addButtonWithTitle_(action)
    cancel_button = alert.addButtonWithTitle_("Cancel")
    if cancel_is_default:  # Pressing Return must not start the move.
        action_button.setKeyEquivalent_("")
        cancel_button.setKeyEquivalent_("\r")
    return alert.runModal() == NSAlertFirstButtonReturn


def make_window(title: str, width: float, height: float, resizable: bool) -> NSWindow:
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
    if resizable:
        style |= NSWindowStyleMaskResizable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False
    )
    window.setTitle_(title)
    window.setReleasedWhenClosed_(False)
    window.setMinSize_((min(width, 640), min(height, 360)))
    window.center()
    return window


def add_label(window: NSWindow, frame, text: str, mask: int = 0, wraps: bool = False) -> NSTextField:
    label = NSTextField.wrappingLabelWithString_(text) if wraps else NSTextField.labelWithString_(text)
    if not wraps:
        label.setLineBreakMode_(NSLineBreakByTruncatingMiddle)
    label.setFrame_(frame)
    label.setAutoresizingMask_(mask)
    window.contentView().addSubview_(label)
    return label


def add_button(window: NSWindow, frame, title: str, target, action: str, mask: int = 0) -> NSButton:
    button = NSButton.buttonWithTitle_target_action_(title, target, action)
    button.setFrame_(frame)
    button.setAutoresizingMask_(mask)
    window.contentView().addSubview_(button)
    return button


def add_table(window: NSWindow, frame, columns: tuple[tuple[str, str, float], ...], source) -> NSTableView:
    scroll = NSScrollView.alloc().initWithFrame_(frame)
    scroll.setHasVerticalScroller_(True)
    scroll.setBorderType_(NSBezelBorder)
    scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    table = NSTableView.alloc().initWithFrame_(scroll.contentView().bounds())
    for identifier, title, width in columns:
        column = NSTableColumn.alloc().initWithIdentifier_(identifier)
        column.setTitle_(title)
        column.setWidth_(width)
        column.setEditable_(False)
        column.dataCell().setLineBreakMode_(NSLineBreakByTruncatingMiddle)
        table.addTableColumn_(column)
    table.tableColumns()[-1].dataCell().setLineBreakMode_(NSLineBreakByTruncatingTail)
    table.setColumnAutoresizingStyle_(NSTableViewLastColumnOnlyAutoresizingStyle)
    table.setUsesAlternatingRowBackgroundColors_(True)
    table.setDataSource_(source)
    scroll.setDocumentView_(table)
    window.contentView().addSubview_(scroll)
    return table


def choose_folder(message: str, current: str) -> str | None:
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(False)
    panel.setCanChooseDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setCanCreateDirectories_(True)
    panel.setPrompt_("Choose")
    panel.setMessage_(message)
    if current and Path(current).is_dir():
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(current))
    bring_to_front()
    if panel.runModal() != NSModalResponseOK:
        return None
    return panel.URL().path()


def login_item():
    """This app's login item, or None when running from source (not as FileSync.app)."""
    if NSBundle.mainBundle().bundleIdentifier() != BUNDLE_ID:
        return None
    return SMAppService.mainAppService()


def opens_at_login() -> bool:
    service = login_item()
    return service is not None and service.status() == SMAppServiceStatusEnabled


def set_open_at_login(enabled: bool) -> str:
    """Add or remove File Sync from Login Items. Returns a problem to show, or ""."""
    service = login_item()
    if service is None:
        return ""
    if enabled:
        succeeded, error = service.registerAndReturnError_(None)
    else:
        succeeded, error = service.unregisterAndReturnError_(None)
    if not succeeded:
        return f"Could not change the login setting: {error.localizedDescription() if error else 'unknown error'}"
    if enabled and service.status() == SMAppServiceStatusRequiresApproval:
        SMAppService.openSystemSettingsLoginItems()
        return "Turn on File Sync under Login Items in System Settings to finish."
    return ""


# --------------------------------------------------------------------------
# Main window: file list and move actions
# --------------------------------------------------------------------------

FILE_COLUMNS = (
    ("check", "", 26.0),
    ("name", "Name", 260.0),
    ("folder", "Folder", 230.0),
    ("size", "Size", 90.0),
    ("modified", "Modified", 140.0),
)


class MainWindow(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(MainWindow, self).init()
        if self is None:
            return None
        self.owner = owner
        self.records: tuple[FileRecord, ...] = ()
        self.checked: set[str] = set()
        self.unreadable: tuple[str, ...] = ()
        self.busy = False
        self.build()
        return self

    @objc.python_method
    def build(self) -> None:
        width, height = 860.0, 560.0
        self.window = make_window("File Sync", width, height, resizable=True)
        self.window.setFrameAutosaveName_("FileSyncMain")
        top = NSViewWidthSizable | NSViewMinYMargin

        warning = add_label(self.window, NSMakeRect(20, height - 56, width - 40, 40), RECORDING_WARNING, top, True)
        warning.setFont_(NSFont.boldSystemFontOfSize_(13))
        warning.setTextColor_(NSColor.systemOrangeColor())
        self.source_label = add_label(self.window, NSMakeRect(20, height - 80, width - 40, 18), "", top)
        self.destination_label = add_label(self.window, NSMakeRect(20, height - 100, width - 40, 18), "", top)
        for label in (self.source_label, self.destination_label):
            label.setTextColor_(NSColor.secondaryLabelColor())

        self.table = add_table(self.window, NSMakeRect(20, 80, width - 40, height - 186), FILE_COLUMNS, self)
        self.table.setAllowsMultipleSelection_(True)
        checkbox = NSButtonCell.alloc().init()
        checkbox.setButtonType_(NSButtonTypeSwitch)
        checkbox.setTitle_("")
        check_column = self.table.tableColumnWithIdentifier_("check")
        check_column.setDataCell_(checkbox)
        check_column.setEditable_(True)
        self.table.tableColumnWithIdentifier_("size").dataCell().setAlignment_(NSTextAlignmentRight)

        bottom = NSViewMaxYMargin
        self.spinner = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(20, 52, 16, 16))
        self.spinner.setStyle_(NSProgressIndicatorStyleSpinning)
        self.spinner.setControlSize_(1)  # Small
        self.spinner.setDisplayedWhenStopped_(False)
        self.window.contentView().addSubview_(self.spinner)
        self.status_label = add_label(
            self.window, NSMakeRect(42, 52, width - 62, 18), "", NSViewWidthSizable | bottom
        )

        self.buttons = [
            add_button(self.window, NSMakeRect(20, 12, 100, 32), "Select All", self, "selectAll:", bottom),
            add_button(self.window, NSMakeRect(122, 12, 110, 32), "Deselect All", self, "deselectAll:", bottom),
            add_button(self.window, NSMakeRect(234, 12, 90, 32), "Refresh", self, "refresh:", bottom),
            add_button(self.window, NSMakeRect(326, 12, 90, 32), "Settings…", self, "openSettings:", bottom),
            add_button(
                self.window, NSMakeRect(width - 260, 12, 100, 32), "Move All…", self, "moveAll:",
                bottom | NSViewMinXMargin,
            ),
            add_button(
                self.window, NSMakeRect(width - 156, 12, 136, 32), "Move Selected…", self, "moveSelected:",
                bottom | NSViewMinXMargin,
            ),
        ]
        add_button(self.window, NSMakeRect(418, 12, 90, 32), "History", self, "openHistory:", bottom)

    # ----- Showing state -----

    @objc.python_method
    def show(self) -> None:
        bring_to_front()
        self.window.makeKeyAndOrderFront_(None)
        self.refresh()

    @objc.python_method
    def show_folders(self) -> None:
        current = self.owner.settings
        if current.is_complete:
            self.source_label.setStringValue_(f"From (staging):  {current.source}")
            self.destination_label.setStringValue_(f"To (Synology):  {current.destination}")
        else:
            self.source_label.setStringValue_("Folders are not set up yet. Click Settings… to choose them.")
            self.destination_label.setStringValue_("")

    @objc.python_method
    def set_busy(self, message: str) -> None:
        self.busy = True
        self.status_label.setStringValue_(message)
        self.spinner.startAnimation_(None)
        self.table.setEnabled_(False)
        for button in self.buttons:
            button.setEnabled_(False)

    @objc.python_method
    def set_idle(self, message: str | None = None) -> None:
        self.busy = False
        self.spinner.stopAnimation_(None)
        self.table.setEnabled_(True)
        for button in self.buttons:
            button.setEnabled_(True)
        self.status_label.setStringValue_(
            message if message is not None else inventory_status(self.records, self.checked, self.unreadable)
        )

    @objc.python_method
    def show_inventory(self, scan: ScanResult) -> None:
        self.records = scan.files
        self.unreadable = scan.unreadable_folders
        present = {record.relative_path for record in scan.files}
        self.checked &= present  # Keep checks only for files still listed.
        self.table.reloadData()

    @objc.python_method
    def show_problem(self, error: Exception, during: str, alert: bool = True) -> None:
        if isinstance(error, ConfigurationError):
            self.records, self.unreadable = (), ()
            self.table.reloadData()
            self.set_idle("Folder settings need attention: " + error.problems[0])
            if alert:
                show_alert("Check your folder settings", "\n".join(error.problems), warning=True)
        else:
            self.set_idle(f"Could not {during}.")
            show_alert(f"Could not {during}", str(error), warning=True)

    # ----- Refresh -----

    @objc.python_method
    def refresh(self) -> None:
        self.show_folders()
        if self.busy:
            return
        if not self.owner.settings.is_complete:
            self.records, self.unreadable = (), ()
            self.table.reloadData()
            self.set_idle("Choose your staging and Synology folders in Settings to get started.")
            return
        self.set_busy("Scanning the staging folder…")
        current = self.owner.settings
        run_in_background(lambda: take_inventory(current), self.finish_refresh)

    @objc.python_method
    def finish_refresh(self, scan: ScanResult | None, error: Exception | None) -> None:
        if error is not None:
            self.show_problem(error, "scan the staging folder", alert=False)
            return
        self.show_inventory(scan)
        self.set_idle()

    # ----- Moving -----

    @objc.python_method
    def confirm_and_move(self, plan: MovePlan | None, error: Exception | None, move_all: bool) -> None:
        if error is not None:
            self.show_problem(error, "prepare the move")
            return
        if plan.scan is not None:
            self.show_inventory(plan.scan)
        if move_all and not plan.records:
            self.set_idle()
            show_alert("Nothing to move", "There are no files in the staging folder.")
            return
        if plan.records:
            count = plural(len(plan.records), "file")
            title = f"Move all {count}?" if move_all else f"Move {count}?"
            if not ask_to_confirm(title, confirmation_message(plan), "Move", cancel_is_default=move_all):
                self.set_idle()
                return
        self.set_busy(f"Moving {plural(len(plan.records), 'file')}…")
        run_in_background(lambda: execute_plan(plan, self.report_progress), self.finish_move)

    @objc.python_method
    def report_progress(self, finished: int, total: int) -> None:
        # Called on the worker thread; hand the update to the main thread.
        AppHelper.callAfter(self.status_label.setStringValue_, f"Moving… {finished} of {total} done")

    @objc.python_method
    def finish_move(self, outcome: tuple | None, error: Exception | None) -> None:
        self.busy = False
        if error is not None:
            show_alert(
                "The move stopped unexpectedly",
                f"{error}\n\nFiles that were already moved stay moved. The list will now refresh.",
                warning=True,
            )
        else:
            results, history_error = outcome
            moved_any = any(r.outcome is Outcome.MOVED for r in results)
            title = "Move completed" if moved_any else "No files were moved"
            show_alert(title, result_message(results, history_error), warning=not moved_any)
        self.refresh()

    # ----- Actions (target/action selectors) -----

    def refresh_(self, sender) -> None:
        self.refresh()

    def selectAll_(self, sender) -> None:
        self.checked = {record.relative_path for record in self.records}
        self.table.reloadData()
        self.set_idle()

    def deselectAll_(self, sender) -> None:
        self.checked = set()
        self.table.reloadData()
        self.set_idle()

    def openSettings_(self, sender) -> None:
        self.owner.open_settings()

    def openHistory_(self, sender) -> None:
        self.owner.open_history()

    def moveSelected_(self, sender) -> None:
        if self.busy:
            return
        selected = files.select_records(self.records, self.checked)
        if not selected:
            show_alert("No files selected", "Check the box next to each file you want to move, or use Move All.")
            return
        self.set_busy("Checking the selected files…")
        current = self.owner.settings
        run_in_background(
            lambda: plan_selected(current, selected),
            lambda plan, error: self.confirm_and_move(plan, error, move_all=False),
        )

    def moveAll_(self, sender) -> None:
        if self.busy:
            return
        self.set_busy("Scanning the staging folder…")
        current = self.owner.settings
        run_in_background(
            lambda: plan_all(current),
            lambda plan, error: self.confirm_and_move(plan, error, move_all=True),
        )

    # ----- Table data source -----

    def numberOfRowsInTableView_(self, table) -> int:
        return len(self.records)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        record = self.records[row]
        identifier = column.identifier()
        if identifier == "check":
            return 1 if record.relative_path in self.checked else 0
        if identifier == "name":
            return record.name
        if identifier == "folder":
            return record.directory or "—"
        if identifier == "size":
            return format_size(record.size)
        return format_timestamp(record.modified_ns)

    def tableView_setObjectValue_forTableColumn_row_(self, table, value, column, row) -> None:
        if column.identifier() != "check" or self.busy:
            return
        path = self.records[row].relative_path
        if value:
            self.checked.add(path)
        else:
            self.checked.discard(path)
        self.set_idle()


# --------------------------------------------------------------------------
# Settings window
# --------------------------------------------------------------------------


class SettingsWindow(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(SettingsWindow, self).init()
        if self is None:
            return None
        self.owner = owner
        self.source = ""
        self.destination = ""
        self.build()
        return self

    @objc.python_method
    def build(self) -> None:
        width, height = 640.0, 300.0
        self.window = make_window("File Sync Settings", width, height, resizable=False)
        add_label(self.window, NSMakeRect(20, height - 40, width - 40, 20),
                  "Staging folder (where scanners and recorders save files):")
        self.source_control = self.add_path_control(NSMakeRect(20, height - 70, width - 150, 24))
        add_button(self.window, NSMakeRect(width - 122, height - 74, 102, 32), "Choose…", self, "chooseSource:")
        add_label(self.window, NSMakeRect(20, height - 110, width - 40, 20),
                  "Synology folder (the local folder Synology Drive Client syncs):")
        self.destination_control = self.add_path_control(NSMakeRect(20, height - 140, width - 150, 24))
        add_button(self.window, NSMakeRect(width - 122, height - 144, 102, 32), "Choose…", self, "chooseDestination:")
        self.login_checkbox = NSButton.checkboxWithTitle_target_action_(
            "Open File Sync automatically when I log in", None, None
        )
        self.login_checkbox.setFrame_(NSMakeRect(18, 116, width - 40, 22))
        self.window.contentView().addSubview_(self.login_checkbox)
        self.message_label = add_label(self.window, NSMakeRect(20, 52, width - 40, 56), "", wraps=True)
        self.message_label.setTextColor_(NSColor.systemRedColor())
        add_button(self.window, NSMakeRect(width - 212, 12, 96, 32), "Cancel", self, "cancel:")
        save = add_button(self.window, NSMakeRect(width - 116, 12, 96, 32), "Save", self, "save:")
        save.setKeyEquivalent_("\r")

    @objc.python_method
    def add_path_control(self, frame) -> NSPathControl:
        control = NSPathControl.alloc().initWithFrame_(frame)
        control.setEditable_(False)
        control.setPlaceholderString_("Not chosen")
        self.window.contentView().addSubview_(control)
        return control

    @objc.python_method
    def show(self) -> None:
        self.source = self.owner.settings.source
        self.destination = self.owner.settings.destination
        self.message_label.setStringValue_("")
        self.update_controls()
        self.update_login_checkbox()
        bring_to_front()
        self.window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def update_controls(self) -> None:
        for control, path in ((self.source_control, self.source), (self.destination_control, self.destination)):
            control.setURL_(NSURL.fileURLWithPath_(path) if path else None)

    @objc.python_method
    def update_login_checkbox(self) -> None:
        available = login_item() is not None
        self.login_checkbox.setEnabled_(available)
        # On first setup the box starts checked, so operators get it by default.
        checked = available and (opens_at_login() or not self.owner.settings.is_complete)
        self.login_checkbox.setState_(1 if checked else 0)
        if not available:
            self.login_checkbox.setTitle_("Open File Sync automatically when I log in (installed app only)")

    def chooseSource_(self, sender) -> None:
        chosen = choose_folder("Choose the staging folder where new scans and recordings are saved.", self.source)
        if chosen:
            self.source = chosen
            self.update_controls()
        self.window.makeKeyAndOrderFront_(None)

    def chooseDestination_(self, sender) -> None:
        chosen = choose_folder("Choose the local folder that Synology Drive Client syncs.", self.destination)
        if chosen:
            self.destination = chosen
            self.update_controls()
        self.window.makeKeyAndOrderFront_(None)

    def cancel_(self, sender) -> None:
        self.window.close()

    def save_(self, sender) -> None:
        problems = files.validate_folders(self.source, self.destination)
        if problems:
            self.message_label.setStringValue_("\n".join(problems))
            return
        new_settings = Settings(source=self.source, destination=self.destination)
        try:
            save_settings(new_settings)
        except OSError as error:
            self.message_label.setStringValue_(f"Settings could not be saved: {error.strerror or error}")
            return
        login_problem = ""
        wants_login = bool(self.login_checkbox.state())
        if self.login_checkbox.isEnabled() and wants_login != opens_at_login():
            login_problem = set_open_at_login(wants_login)
        self.window.close()
        self.owner.apply_settings(new_settings)
        if login_problem:
            show_alert("Open at login", login_problem, warning=True)


# --------------------------------------------------------------------------
# History window
# --------------------------------------------------------------------------

HISTORY_FILTERS = (("All", None), ("Moved", "success"), ("Skipped", "skipped"), ("Failed", "error"))
RESULT_LABELS = {"success": "Moved", "skipped": "Skipped", "error": "Failed"}
HISTORY_COLUMNS = (
    ("time", "Time", 140.0),
    ("result", "Result", 70.0),
    ("file", "File", 250.0),
    ("details", "Details", 340.0),
)
HISTORY_LIMIT = 1000


class HistoryWindow(NSObject):
    def init(self):
        self = objc.super(HistoryWindow, self).init()
        if self is None:
            return None
        self.all_entries: tuple[history.HistoryEntry, ...] = ()
        self.entries: tuple[history.HistoryEntry, ...] = ()
        self.build()
        return self

    @objc.python_method
    def build(self) -> None:
        width, height = 860.0, 480.0
        self.window = make_window("File Sync History", width, height, resizable=True)
        self.window.setFrameAutosaveName_("FileSyncHistory")
        top = NSViewMinYMargin
        self.filter_control = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            [label for label, _ in HISTORY_FILTERS], 0, self, "filterChanged:"
        )
        self.filter_control.setFrame_(NSMakeRect(20, height - 44, 300, 28))
        self.filter_control.setSelectedSegment_(0)
        self.filter_control.setAutoresizingMask_(top)
        self.window.contentView().addSubview_(self.filter_control)
        add_button(self.window, NSMakeRect(width - 110, height - 46, 90, 32), "Refresh", self, "reload:",
                   top | NSViewMinXMargin)
        self.table = add_table(self.window, NSMakeRect(20, 40, width - 40, height - 96), HISTORY_COLUMNS, self)
        self.count_label = add_label(self.window, NSMakeRect(20, 12, width - 40, 18), "", NSViewWidthSizable)
        self.count_label.setTextColor_(NSColor.secondaryLabelColor())

    @objc.python_method
    def show(self) -> None:
        self.load()
        bring_to_front()
        self.window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def load(self) -> None:
        self.all_entries = history.read_recent_history(limit=HISTORY_LIMIT)
        self.apply_filter()

    @objc.python_method
    def apply_filter(self) -> None:
        _, result = HISTORY_FILTERS[max(self.filter_control.selectedSegment(), 0)]
        self.entries = history.filter_history(self.all_entries, result)
        self.table.reloadData()
        self.count_label.setStringValue_(
            f"Showing {len(self.entries)} of the {plural(len(self.all_entries), 'most recent entry', 'most recent entries')}."
            f"  Log file: {history.HISTORY_PATH}"
        )

    def filterChanged_(self, sender) -> None:
        self.apply_filter()

    def reload_(self, sender) -> None:
        self.load()

    def numberOfRowsInTableView_(self, table) -> int:
        return len(self.entries)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        entry = self.entries[row]
        identifier = column.identifier()
        if identifier == "time":
            return entry.timestamp[:19].replace("T", " ")
        if identifier == "result":
            return RESULT_LABELS.get(entry.result, entry.result)
        if identifier == "file":
            return entry.relative_path
        return entry.message


# --------------------------------------------------------------------------
# Menu bar application
# --------------------------------------------------------------------------


class FileSyncApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("File Sync", title="File Sync", quit_button=None)
        self.settings = load_settings()
        self.main_window: MainWindow | None = None
        self.settings_window: SettingsWindow | None = None
        self.history_window: HistoryWindow | None = None
        self.menu = [
            rumps.MenuItem("Open File Sync", callback=lambda _: self.open_main()),
            rumps.MenuItem("Refresh Files", callback=lambda _: self.open_main()),
            None,
            rumps.MenuItem("Settings…", callback=lambda _: self.open_settings()),
            None,
            rumps.MenuItem("Quit File Sync", callback=lambda _: self.quit()),
        ]
        rumps.events.before_start.register(self.on_start)

    def on_start(self) -> None:
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.use_symbol_icon()
        if not self.settings.is_complete:
            AppHelper.callAfter(self.open_settings)

    def use_symbol_icon(self) -> None:
        """Show a template SF Symbol in the menu bar; keep the text title if that fails."""
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("tray.and.arrow.up", "File Sync")
        status_item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
        if image is None or status_item is None:
            return
        image.setTemplate_(True)
        status_item.button().setImage_(image)
        status_item.button().setTitle_("")
        status_item.button().setToolTip_("File Sync")

    def open_main(self) -> None:
        if self.main_window is None:
            self.main_window = MainWindow.alloc().initWithOwner_(self)
        self.main_window.show()

    def open_settings(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow.alloc().initWithOwner_(self)
        self.settings_window.show()

    def open_history(self) -> None:
        if self.history_window is None:
            self.history_window = HistoryWindow.alloc().init()
        self.history_window.show()

    def apply_settings(self, new_settings: Settings) -> None:
        self.settings = new_settings
        self.open_main()

    def quit(self) -> None:
        if self.main_window is not None and self.main_window.busy:
            show_alert(
                "Please wait",
                "File Sync is busy moving or checking files. Quit after it finishes so the results can be shown.",
            )
            return
        rumps.quit_application()


def main() -> None:
    FileSyncApp().run()


if __name__ == "__main__":
    main()
