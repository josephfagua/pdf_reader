"""
MDIP.py — Invoice Pipeline Launcher

Pages:
  - Config screen (first launch or via button): set input + output folders
  - Main screen: select/drop a PDF and process it

Requires:
    pip install tkinterdnd2
"""

import os
import time
import subprocess
import ctypes
from pathlib import Path
import json
import threading
import sys
import pathlib
import datetime
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

from src.main import extract_text, refine_data, parse_items, extract_order_details, export_items_csv
from src.logging_config import setup_logging, get_logger, log_event


# ---------------------------------------------------------------------------
# Current user — resolved once at startup, used in status bar + log entries
# ---------------------------------------------------------------------------

try:
    CURRENT_USER = os.getlogin()
except Exception:
    CURRENT_USER = os.environ.get("USERNAME", "unknown")


# ---------------------------------------------------------------------------
# Helper functions for tkinter windows
# ---------------------------------------------------------------------------

def resource_path(relative_path: str) -> str:
    """Get an absolute path to a resource, works for dev and for PyInstaller .exe"""
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return str(base_path / relative_path)


def center_window(window, width: int, height: int):
    window.withdraw()  # hide

    window.update_idletasks()

    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()

    x = (screen_width - width) // 2
    y = (screen_height - height) // 2

    window.geometry(f"{width}x{height}+{x}+{y}")

    window.deiconify()  # reveal


def center_over(window, parent_window, width: int, height: int):
    """Center a window over a parent window (rather than the screen)."""
    window.withdraw()  # hide

    window.update_idletasks()
    parent_window.update_idletasks()

    parent_x = parent_window.winfo_x()
    parent_y = parent_window.winfo_y()
    parent_w = parent_window.winfo_width()
    parent_h = parent_window.winfo_height()

    x = parent_x + (parent_w - width) // 2
    y = parent_y + (parent_h - height) // 2

    window.geometry(f"{width}x{height}+{x}+{y}")

    window.deiconify()  # reveal


def bring_to_front(window):
    """Force a window to the foreground on launch, without keeping it pinned on top forever."""
    window.lift()
    window.attributes("-topmost", True)
    window.focus_force()
    window.after(100, lambda: window.attributes("-topmost", False))


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

APP_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "MD Invoice Processor"
APP_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = APP_DIR / "config.json"


def load_config() -> dict:
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def is_config_valid(config: dict) -> bool:
    return bool(config.get("input_folder") and config.get("output_folder"))


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

BG          = "#F7F8FA"
PANEL       = "#FFFFFF"
BORDER      = "#D1D5DB"
ACCENT      = "#2563EB"
ACCENT_DARK = "#1D4ED8"
TEXT        = "#111827"
TEXT_MUTED  = "#6B7280"
SECONDARY_FG = "#888888"   # muted grey for the status bar
SUCCESS_BG  = "#ECFDF5"
SUCCESS_FG  = "#065F46"
ERROR_BG    = "#FEF2F2"
ERROR_FG    = "#991B1B"
DROP_HOVER  = "#EFF6FF"

# Pacing for status updates (seconds). Keeps each step on screen long enough
# to actually be read, since the underlying pipeline runs almost instantly.
STEP_DELAY = 0.4

# Total number of steps tracked by the progress bar.
TOTAL_STEPS = 5


# ---------------------------------------------------------------------------
# Module-level logger (initialised after setup_logging() is called at startup)
# ---------------------------------------------------------------------------

_logger = get_logger("mdip")


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

# Machine name — resolved once, reused in every log entry.
CURRENT_MACHINE = os.environ.get("COMPUTERNAME", "unknown")


def run_pipeline(pdf_path: str, output_folder: str, status_callback) -> None:
    """
    Runs the full pipeline in a background thread, reporting progress via
    status_callback(header, detail, step, is_error, success, output_path).

    `header` is a short label meant for the dialog's prominent header text.
    `detail` is optional longer text shown on a smaller line below — used
    only for the final success/error states, empty during normal steps.

    `step` is the progress bar value (1-TOTAL_STEPS) to display, or None to
    leave the bar exactly where it currently is (used on error, so the bar
    freezes at the point of failure instead of resetting).

    `output_path` is only set on success, so the dialog can offer to reveal
    the finished file once the user closes it.
    """
    pdf_path = pdf_path.strip().strip("{}")

    if not os.path.isfile(pdf_path):
        status_callback(
            "File not found",
            "We couldn't find that file. Please select it again using the "
            "Browse button or drag it in.",
            step=None,
            is_error=True,
        )
        return

    if not pdf_path.lower().endswith(".pdf"):
        status_callback(
            "Wrong file type",
            "Please choose a PDF file — this looks like a different file type.",
            step=None,
            is_error=True,
        )
        return

    # Collect file metadata before processing starts.
    invoice_filename = os.path.basename(pdf_path)
    try:
        file_size_kb = round(os.path.getsize(pdf_path) / 1024, 1)
    except OSError:
        file_size_kb = 0.0

    start_time = time.monotonic()

    try:
        status_callback("Getting started…", "", step=1, is_error=False)
        time.sleep(STEP_DELAY)

        raw_text = extract_text(pdf_path)
        status_callback("Reading your invoice…", "", step=2, is_error=False)
        time.sleep(STEP_DELAY)

        cleaned_text = refine_data(raw_text)
        status_callback("Organizing the details…", "", step=3, is_error=False)
        time.sleep(STEP_DELAY)

        order_data = {
            "order_details": extract_order_details(cleaned_text),
            "items":         parse_items(cleaned_text),
        }
        item_count = len(order_data["items"])
        status_callback("Finding your items and order info…", "", step=4, is_error=False)
        time.sleep(STEP_DELAY)

        os.makedirs(output_folder, exist_ok=True)
        output_path = export_items_csv(order_data, output_folder)
        status_callback("Saving your file…", "", step=5, is_error=False)
        time.sleep(STEP_DELAY)

        duration_s = round(time.monotonic() - start_time, 1)

        status_callback(
            "All done!",
            f"Your processed invoice has been saved to:\n{output_path}",
            step=5,
            is_error=False,
            success=True,
            output_path=output_path,
        )

        log_event(
            _logger,
            user=CURRENT_USER,
            machine=CURRENT_MACHINE,
            invoice_file=invoice_filename,
            file_size_kb=file_size_kb,
            duration_s=duration_s,
            item_count=item_count,
            status="SUCCESS",
            detail=os.path.basename(output_path),
        )

    except Exception as exc:
        duration_s = round(time.monotonic() - start_time, 1)
        log_event(
            _logger,
            user=CURRENT_USER,
            machine=CURRENT_MACHINE,
            invoice_file=invoice_filename,
            file_size_kb=file_size_kb,
            duration_s=duration_s,
            item_count=0,
            status="ERROR",
            detail=str(exc),
        )
        # The real exception is logged above; we show a friendly message to
        # the user — raw tracebacks are meaningless and alarming to
        # non-technical staff.
        status_callback(
            "Something went wrong",
            "Please make sure it's a Martin's Distribution invoice PDF, then "
            "try again. If this keeps happening, contact support.",
            step=None,  # freeze the bar at whatever step it last reached
            is_error=True,
        )


# ---------------------------------------------------------------------------
# Reusable widgets
# ---------------------------------------------------------------------------

def divider(parent):
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(0, 20))


def section_label(parent, text):
    tk.Label(parent, text=text, bg=BG, fg=TEXT_MUTED,
             font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=24)


def folder_row(parent, label_text, path_var):
    """A labelled path entry + Browse button row. Returns the Entry widget."""
    section_label(parent, label_text)
    row = tk.Frame(parent, bg=BG)
    row.pack(fill="x", padx=24, pady=(2, 16))

    entry = tk.Entry(row, textvariable=path_var, font=("Segoe UI", 10),
                     bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                     highlightbackground=BORDER, highlightthickness=1)
    entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))

    def browse():
        chosen = filedialog.askdirectory(title=f"Select {label_text}")
        if chosen:
            path_var.set(chosen)

    tk.Button(row, text="Browse…", command=browse,
              font=("Segoe UI", 9), bg=PANEL, fg=ACCENT,
              activebackground=DROP_HOVER, activeforeground=ACCENT_DARK,
              relief="flat", highlightbackground=BORDER, highlightthickness=1,
              cursor="hand2", padx=10, pady=6).pack(side="right")

    return entry


# ---------------------------------------------------------------------------
# Config screen (Toplevel so it can be opened from main screen too)
# ---------------------------------------------------------------------------

class ConfigScreen(tk.Toplevel):
    """
    Opened on first launch (before main screen) or via the config button.
    on_save(config) is called when the user saves valid config.
    """

    def __init__(self, parent, current_config: dict, on_save, is_first_launch: bool = False):
        super().__init__(parent)
        self.title("Configuration")
        self.configure(bg=BG)
        self.resizable(False, False)
        center_window(self, 500, 360)
        bring_to_front(self)
        self.grab_set()
        self.iconbitmap(resource_path("Martins-Distribution_RGB.ico"))
        self.on_save = on_save
        self.is_first_launch = is_first_launch
        self._current_config = current_config  # preserved so _save can carry log_path forward

        # If closed via the X on first launch, quit the whole app
        if is_first_launch:
            self.protocol("WM_DELETE_WINDOW", parent.destroy)

        self._build_ui(current_config)

    def _build_ui(self, current_config: dict):
        # Title
        title_frame = tk.Frame(self, bg=BG)
        title_frame.pack(fill="x", padx=24, pady=(28, 4))

        heading = "Welcome — let's get set up" if self.is_first_launch else "Configuration"
        tk.Label(title_frame, text=heading, bg=BG, fg=TEXT,
                 font=("Segoe UI", 16, "bold"), anchor="w").pack(side="left")

        divider(self)

        # Folder fields
        self.input_var  = tk.StringVar(value=current_config.get("input_folder", ""))
        self.output_var = tk.StringVar(value=current_config.get("output_folder", ""))

        folder_row(self, "Where are your invoice PDFs saved?", self.input_var)
        folder_row(self, "Where should we save your finished files?", self.output_var)

        # Validation message
        self.validation_label = tk.Label(self, text="", bg=BG, fg=ERROR_FG,
                                         font=("Segoe UI", 9), anchor="w")
        self.validation_label.pack(fill="x", padx=24, pady=(0, 12))

        # Save button
        btn_text = "Save & Continue" if self.is_first_launch else "Save"
        tk.Button(self, text=btn_text, command=self._save,
                  font=("Segoe UI", 11, "bold"),
                  bg=ACCENT, fg="white",
                  activebackground=ACCENT_DARK, activeforeground="white",
                  relief="flat", cursor="hand2", pady=10).pack(fill="x", padx=24)

    def _save(self):
        input_folder  = self.input_var.get().strip()
        output_folder = self.output_var.get().strip()

        if not input_folder or not output_folder:
            self.validation_label.configure(text="Please choose both an input and output folder.")
            return

        if not os.path.isdir(input_folder):
            self.validation_label.configure(text="That input folder couldn't be found. Please check the path or use Browse.")
            return

        if not os.path.isdir(output_folder):
            try:
                os.makedirs(output_folder, exist_ok=True)
            except OSError:
                self.validation_label.configure(text="We couldn't create that output folder. Please choose a different location.")
                return

        config = {
            "input_folder":  input_folder,
            "output_folder": output_folder,
            "log_path":      self._current_config.get("log_path", "\\\\SERVER\\MDIPLogs\\app.log"),
        }
        save_config(config)
        self.on_save(config)
        self.destroy()


# ---------------------------------------------------------------------------
# Processing dialog (modal — shown while an invoice is being processed)
# ---------------------------------------------------------------------------

class ProcessingDialog(tk.Toplevel):
    """
    Modal dialog shown while an invoice is processed. Owns the progress bar
    and status messages that used to live on MainScreen. Starts the pipeline
    immediately on open. The window cannot be closed (X button is blocked)
    until processing reaches a final state (success or error).
    """

    WIDTH = 420
    HEIGHT = 260

    def __init__(self, parent, pdf_path: str, output_folder: str):
        super().__init__(parent)
        self.parent = parent
        self.is_finished = False
        self.success = False
        self.output_path = None

        self.title("Processing Invoice")
        self.configure(bg=BG)
        self.resizable(False, False)
        center_over(self, parent, self.WIDTH, self.HEIGHT)
        bring_to_front(self)
        self.iconbitmap(resource_path("Martins-Distribution_RGB.ico"))

        # Block the X button until processing is finished
        self.protocol("WM_DELETE_WINDOW", self._block_close)
        self.grab_set()

        self._build_ui()

        threading.Thread(
            target=run_pipeline,
            args=(pdf_path, output_folder, self._thread_safe_status),
            daemon=True,
        ).start()

    def _build_ui(self):
        title_frame = tk.Frame(self, bg=BG)
        title_frame.pack(fill="x", padx=24, pady=(24, 8))

        self.header_label = tk.Label(title_frame, text="Getting started…", bg=BG, fg=TEXT,
                                     font=("Segoe UI", 13, "bold"), anchor="w",
                                     justify="left", wraplength=370)
        self.header_label.pack(side="left")

        self.progress_bar = ttk.Progressbar(
            self, mode="determinate", maximum=TOTAL_STEPS, value=0
        )
        self.progress_bar.pack(fill="x", padx=24, pady=(8, 12))

        self.status_frame = tk.Frame(self, bg=BG)
        self.status_frame.pack(fill="x", padx=24, pady=(0, 16))

        # Smaller detail line — empty during normal steps, used only to carry
        # the full success/error message alongside the short header above.
        self.status_label = tk.Label(self.status_frame, text="",
                                     bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 9),
                                     anchor="w", justify="left", wraplength=370)
        self.status_label.pack(fill="x")

        self.close_btn = tk.Button(self, text="Close", command=self._on_close,
                                   font=("Segoe UI", 10, "bold"),
                                   bg="#93C5FD", fg="white",
                                   activebackground=ACCENT_DARK, activeforeground="white",
                                   relief="flat", cursor="hand2", pady=8,
                                   state="disabled")
        self.close_btn.pack(fill="x", padx=24, pady=(0, 20))

    def _block_close(self):
        """Ignore the X button while processing is still in progress."""
        if self.is_finished:
            self._on_close()
        # else: do nothing — processing must reach a final state first

    def _on_close(self):
        if self.success and self.output_path:
            self._reveal_output_file(self.output_path)
        self.destroy()

    def _reveal_output_file(self, path: str):
        """Open Explorer with the finished file highlighted, so the user can
        immediately see and act on it without hunting for it manually."""
        try:
            # Windows normally prevents background processes from stealing
            # focus, which is why Explorer can open behind other windows.
            # Calling this just before launching it allows the next window
            # that requests focus to actually receive it.
            ASFW_ANY = -1
            ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
        except Exception:
            # Never let a failure here block the dialog from closing —
            # worst case the user just doesn't get the folder popped open.
            pass

    def _thread_safe_status(self, header, detail, step, is_error, success=False, output_path=None):
        self.parent.after(0, self._set_status, header, detail, step, is_error, success, output_path)

    def _set_status(self, header, detail, step, is_error, success=False, output_path=None):
        # step=None means "leave the progress bar exactly where it is" —
        # used on error, so the bar freezes at the point of failure.
        if step is not None:
            self.progress_bar["value"] = step

        if success:
            bg, fg = SUCCESS_BG, SUCCESS_FG
        elif is_error:
            bg, fg = ERROR_BG, ERROR_FG
        else:
            bg, fg = BG, TEXT_MUTED

        # Header always shows the short, prominent status; detail line below
        # only carries text on the final success/error states.
        self.header_label.configure(text=header, fg=fg if (success or is_error) else TEXT)
        self.status_frame.configure(bg=bg)
        self.status_label.configure(text=detail, bg=bg, fg=fg)

        if success or is_error:
            self.is_finished = True
            self.success = success
            self.output_path = output_path
            self.close_btn.configure(state="normal", bg=ACCENT)


# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------

class MainScreen(tk.Frame):
    def __init__(self, parent, config: dict):
        super().__init__(parent, bg=BG)
        self.parent = parent
        self.config = config
        self._build_ui()

        if DND_AVAILABLE:
            self.drop_zone.drop_target_register(DND_FILES)
            self.drop_zone.dnd_bind("<<Drop>>",      self._on_drop)
            self.drop_zone.dnd_bind("<<DragEnter>>", self._on_drag_enter)
            self.drop_zone.dnd_bind("<<DragLeave>>", self._on_drag_leave)

    def update_config(self, config: dict):
        self.config = config
        self._refresh_output_preview()

    def _build_ui(self):
        # ── User / clock — quiet annotation above the main header ─────
        self.user_clock_label = tk.Label(
            self,
            text=self._status_bar_text(),
            bg=BG,
            fg=SECONDARY_FG,
            font=("Segoe UI", 8),
            anchor="w",
        )
        self.user_clock_label.pack(fill="x", padx=26, pady=(14, 0))
        self._tick_clock()

        # ── Header ────────────────────────────────────────────────────
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=24, pady=(2, 4))

        tk.Label(header, text="Invoice Processor", bg=BG, fg=TEXT,
                 font=("Segoe UI", 18, "bold"), anchor="w").pack(side="left")

        # Config button (top-right of header)
        tk.Button(header, text="⚙ Configure", command=self._open_config,
                  font=("Segoe UI", 9), bg=BG, fg=TEXT_MUTED,
                  activebackground=DROP_HOVER, activeforeground=ACCENT,
                  relief="flat", cursor="hand2").pack(side="right", pady=(6, 0))

        tk.Label(header, text="PDF → CSV", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 11)).pack(side="right", pady=(6, 0), padx=(0, 12))

        divider(self)

        # ── Drop zone ─────────────────────────────────────────────────
        self.drop_zone = tk.Frame(self, bg=PANEL,
                                  highlightbackground=BORDER, highlightthickness=1,
                                  cursor="hand2")
        self.drop_zone.pack(fill="x", padx=24, pady=(0, 16))

        inner = tk.Frame(self.drop_zone, bg=PANEL)
        inner.pack(pady=28)

        tk.Label(inner, text="⬆", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 28)).pack()

        self.drop_label = tk.Label(
            inner,
            text="Drag & drop your invoice PDF here" if DND_AVAILABLE else "Select your invoice PDF below",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"),
        )
        self.drop_label.pack(pady=(6, 2))

        tk.Label(inner, text="or click anywhere here to browse for the invoice file",
                 bg=PANEL, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack()

        tk.Label(inner, text="Accepts the invoice PDF you received from Martin's Distribution.",
                 bg=PANEL, fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(pady=(4, 0))

        self.drop_zone.bind("<Button-1>", lambda e: self._browse())
        for child in inner.winfo_children():
            child.bind("<Button-1>", lambda e: self._browse())

        # ── Selected invoice ──────────────────────────────────────────
        section_label(self, "Selected invoice:")

        selected_row = tk.Frame(self, bg=BG)
        selected_row.pack(fill="x", padx=24, pady=(2, 0))

        selected_box = tk.Frame(selected_row, bg=PANEL, relief="flat",
                                highlightbackground=BORDER, highlightthickness=1)
        selected_box.pack(fill="x", expand=True)

        self.filename_label = tk.Label(
            selected_box, text="No file selected yet",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold"),
            anchor="w", justify="left",
        )
        self.filename_label.pack(fill="x", padx=10, pady=(8, 0))

        self.path_label = tk.Label(
            selected_box, text="",
            bg=PANEL, fg=TEXT_MUTED, font=("Segoe UI", 8),
            anchor="w", justify="left", wraplength=380,
        )
        self.path_label.pack(fill="x", padx=10, pady=(0, 8))

        # path_var holds the actual full path used internally by the pipeline
        self.path_var = tk.StringVar()

        # ── Output folder preview ─────────────────────────────────────
        self.output_preview_label = tk.Label(
            self, text="", bg=BG, fg=TEXT_MUTED,
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=490,
        )
        self.output_preview_label.pack(fill="x", padx=24, pady=(12, 12))
        self._refresh_output_preview()

        # ── Process button ────────────────────────────────────────────
        self.process_btn = tk.Button(self, text="Process Invoice",
                                     command=self._start_processing,
                                     font=("Segoe UI", 11, "bold"),
                                     bg=ACCENT, fg="white",
                                     activebackground=ACCENT_DARK, activeforeground="white",
                                     relief="flat", cursor="hand2", pady=10)
        self.process_btn.pack(fill="x", padx=24, pady=(0, 24))

    # ── User / clock ──────────────────────────────────────────────────

    def _status_bar_text(self) -> str:
        now = datetime.datetime.now().strftime("%d %b %Y  %H:%M")
        return f"Logged in as:  {CURRENT_USER}  \u2022  {now}"

    def _tick_clock(self):
        """Update the clock label every 60 seconds."""
        self.user_clock_label.config(text=self._status_bar_text())
        self.after(60_000, self._tick_clock)

    # ── Output preview ────────────────────────────────────────────────

    def _refresh_output_preview(self):
        output_folder = self.config.get("output_folder", "")
        self.output_preview_label.configure(
            text=f"Your processed invoice will be saved to: {output_folder}"
        )

    # ── Selected invoice display ──────────────────────────────────────

    def _update_selected_display(self, path: str):
        self.path_var.set(path)

        if path:
            self.filename_label.configure(text=os.path.basename(path), fg=TEXT)
            self.path_label.configure(text=path, fg=TEXT_MUTED)
        else:
            self.filename_label.configure(text="No file selected yet", fg=TEXT)
            self.path_label.configure(text="", fg=TEXT_MUTED)

    def _mark_invalid_selection(self, reason: str):
        """Flag the currently selected file as invalid, in place, with a reason."""
        self.filename_label.configure(fg=ERROR_FG)
        self.path_label.configure(text=reason, fg=ERROR_FG)

    # ── Drag-and-drop ──────────────────────────────────────────────────

    def _on_drag_enter(self, event):
        self.drop_zone.configure(bg=DROP_HOVER, highlightbackground=ACCENT)
        self.drop_label.configure(bg=DROP_HOVER, text="Release to select this file")

    def _on_drag_leave(self, event):
        self.drop_zone.configure(bg=PANEL, highlightbackground=BORDER)
        self.drop_label.configure(bg=PANEL, text="Drag & drop your invoice PDF here")

    def _on_drop(self, event):
        self.drop_zone.configure(bg=PANEL, highlightbackground=BORDER)
        self.drop_label.configure(bg=PANEL, text="Drag & drop your invoice PDF here")
        dropped_path = event.data.strip().strip("{}")
        self._update_selected_display(dropped_path)

    # ── File browser ───────────────────────────────────────────────────

    def _browse(self):
        initial = self.config.get("input_folder") or "/"
        path = filedialog.askopenfilename(
            title="Select an invoice PDF",
            initialdir=initial,
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self._update_selected_display(path)

    # ── Config ────────────────────────────────────────────────────────

    def _open_config(self):
        ConfigScreen(
            parent=self.parent,
            current_config=self.config,
            on_save=self.update_config,
            is_first_launch=False,
        )

    # ── Processing ────────────────────────────────────────────────────

    def _start_processing(self):
        pdf_path = self.path_var.get().strip()

        if not pdf_path:
            messagebox.showwarning(
                "No invoice selected",
                "Please drag in or browse for an invoice before processing.",
                parent=self.parent,
            )
            return

        if not os.path.isfile(pdf_path):
            messagebox.showwarning(
                "File not found",
                "We couldn't find that file. Please select it again using the "
                "Browse button or drag it in.",
                parent=self.parent,
            )
            self._mark_invalid_selection("This file couldn't be found. Please choose it again.")
            return

        if not pdf_path.lower().endswith(".pdf"):
            messagebox.showwarning(
                "Wrong file type",
                "Please choose a PDF file — this looks like a different file type.",
                parent=self.parent,
            )
            self._mark_invalid_selection("This file type isn't supported — please choose a PDF.")
            return

        output_folder = self.config.get("output_folder", "pdf_output")

        self.process_btn.configure(state="disabled", text="Processing…", bg="#93C5FD")
        dialog = ProcessingDialog(self.parent, pdf_path, output_folder)
        self.parent.wait_window(dialog)
        self.process_btn.configure(state="normal", text="Process Invoice", bg=ACCENT)


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

class App:
    def __init__(self):
        root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
        self.root = root
        self.root.title("Invoice Processor")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        center_window(self.root, 540, 540)
        bring_to_front(self.root)
        self.root.iconbitmap(resource_path("Martins-Distribution_RGB.ico"))

        # ── Config migration ──────────────────────────────────────────
        # Existing installs won't have "log_path" in their config.json
        # (it was only added this session).  If the key is absent, write the
        # placeholder in now so the file is always up to date after first run.
        config = load_config()
        if "log_path" not in config:
            config["log_path"] = "\\\\SERVER\\MDIPLogs\\app.log"
            # Only save if there's already a real config worth preserving —
            # first-launch configs are saved by ConfigScreen._save() instead.
            if is_config_valid(config):
                save_config(config)

        # Initialise logging before anything else runs
        setup_logging(config.get("log_path"))

        if not is_config_valid(config):
            # First launch — hide the main window until config is saved
            self.root.withdraw()
            self.main_screen = None
            ConfigScreen(
                parent=self.root,
                current_config=config,
                on_save=self._on_first_config_save,
                is_first_launch=True,
            )
        else:
            self._show_main(config)

        self.root.mainloop()

    def _on_first_config_save(self, config: dict):
        self.root.deiconify()
        self._show_main(config)

    def _show_main(self, config: dict):
        self.main_screen = MainScreen(self.root, config)
        self.main_screen.pack(fill="both", expand=True)


if __name__ == "__main__":
    App()