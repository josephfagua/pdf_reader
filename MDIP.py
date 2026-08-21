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
from src.validation import validate_invoice
from src.services.explorer_manager import explorer_manager
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


def run_pipeline(
    pdf_path: str,
    output_folder: str,
    status_callback,
    po_exception_approved: bool = False,
) -> None:
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
    invoice_number_for_log = "UNKNOWN"
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
        invoice_number_for_log = order_data["order_details"].invoice_number or "UNKNOWN"
        status_callback("Finding your items and order info…", "", step=4, is_error=False)
        time.sleep(STEP_DELAY)

        os.makedirs(output_folder, exist_ok=True)
        output_path = export_items_csv(
            order_data,
            output_folder,
            po_exception_approved=po_exception_approved,
        )
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
            invoice_number=invoice_number_for_log,
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
            invoice_number=invoice_number_for_log,
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
    Modal dialog shown while a batch of invoices is processed.

    Each invoice is still processed by run_pipeline(), which remains
    responsible for one invoice. This dialog coordinates the batch and
    reports overall progress to the user.
    """

    WIDTH = 460
    HEIGHT = 280

    def __init__(
        self,
        parent,
        pdf_paths: list[str],
        output_folder: str,
        po_exceptions: set[str] | None = None,
    ):
        super().__init__(parent)

        self.parent = parent
        self.pdf_paths = list(pdf_paths)
        self._output_folder = os.path.normpath(output_folder)
        self.po_exceptions = po_exceptions or set()

        self.is_finished = False
        self.success = False
        self.output_paths: list[str] = []
        self.error_message = ""

        self.title("Processing Invoices")
        self.configure(bg=BG)
        self.resizable(False, False)
        center_over(self, parent, self.WIDTH, self.HEIGHT)
        bring_to_front(self)
        self.iconbitmap(resource_path("Martins-Distribution_RGB.ico"))

        self.protocol("WM_DELETE_WINDOW", self._block_close)
        self.grab_set()

        self._build_ui()

        threading.Thread(
            target=self._process_batch,
            daemon=True,
        ).start()

    def _build_ui(self):
        title_frame = tk.Frame(self, bg=BG)
        title_frame.pack(fill="x", padx=24, pady=(24, 8))

        self.header_label = tk.Label(
            title_frame,
            text="Preparing invoices…",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 13, "bold"),
            anchor="w",
            justify="left",
            wraplength=400,
        )
        self.header_label.pack(side="left")

        self.progress_bar = ttk.Progressbar(
            self,
            mode="determinate",
            maximum=max(len(self.pdf_paths) * TOTAL_STEPS, 1),
            value=0,
        )
        self.progress_bar.pack(fill="x", padx=24, pady=(8, 12))

        self.status_frame = tk.Frame(self, bg=BG)
        self.status_frame.pack(fill="x", padx=24, pady=(0, 16))

        self.status_label = tk.Label(
            self.status_frame,
            text="",
            bg=BG,
            fg=TEXT_MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=400,
        )
        self.status_label.pack(fill="x")

        self.close_btn = tk.Button(
            self,
            text="Close",
            command=self._on_close,
            font=("Segoe UI", 10, "bold"),
            bg="#93C5FD",
            fg="white",
            activebackground=ACCENT_DARK,
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            pady=8,
            state="disabled",
        )
        self.close_btn.pack(fill="x", padx=24, pady=(0, 20))

    def _block_close(self):
        if self.is_finished:
            self._on_close()

    def _process_batch(self):
        total = len(self.pdf_paths)

        for index, pdf_path in enumerate(self.pdf_paths, start=1):
            invoice_name = os.path.basename(pdf_path)

            def callback(
                header,
                detail,
                step,
                is_error,
                success=False,
                output_path=None,
                current_index=index,
                total_count=total,
                current_name=invoice_name,
            ):
                if output_path:
                    self.output_paths.append(output_path)

                # A successful invoice is an intermediate result during a
                # batch, so it must not unlock the Close button yet.
                if success:
                    self._thread_safe_status(
                        f"Invoice {current_index} of {total_count} complete",
                        detail,
                        step,
                        False,
                        False,
                        None,
                    )
                    return

                self._thread_safe_status(
                    f"Invoice {current_index} of {total_count}: {header}",
                    detail,
                    step,
                    is_error,
                    False,
                    None,
                )

            before_count = len(self.output_paths)

            run_pipeline(
                pdf_path,
                self._output_folder,
                callback,
                po_exception_approved=pdf_path in self.po_exceptions,
            )

            # If this invoice did not create an output file, its pipeline
            # encountered an error and the batch should stop.
            if len(self.output_paths) == before_count:
                self.parent.after(
                    0,
                    self._finish_batch,
                    False,
                    f"Processing stopped while handling {invoice_name}.",
                )
                return

        self.parent.after(0, self._finish_batch, True, "")

    def _finish_batch(self, success: bool, error_message: str):
        self.is_finished = True
        self.success = success
        self.error_message = error_message

        if success:
            self.header_label.configure(
                text=f"Batch complete — {len(self.output_paths)} invoice(s)",
                fg=SUCCESS_FG,
            )
            self.status_frame.configure(bg=SUCCESS_BG)
            self.status_label.configure(
                text="All selected invoices were processed successfully.",
                bg=SUCCESS_BG,
                fg=SUCCESS_FG,
            )
            self.progress_bar["value"] = len(self.pdf_paths) * TOTAL_STEPS
        else:
            self.header_label.configure(
                text="Batch processing stopped",
                fg=ERROR_FG,
            )
            self.status_frame.configure(bg=ERROR_BG)
            self.status_label.configure(
                text=error_message,
                bg=ERROR_BG,
                fg=ERROR_FG,
            )

        self.close_btn.configure(state="normal", bg=ACCENT)

    def _thread_safe_status(
        self,
        header,
        detail,
        step,
        is_error,
        success=False,
        output_path=None,
    ):
        # Each invoice owns TOTAL_STEPS progress units.
        # The current invoice number is derived from the header's prefix.
        try:
            current_index = int(header.split()[1])
        except (ValueError, IndexError):
            current_index = 1

        absolute_step = (
            max(current_index - 1, 0) * TOTAL_STEPS
            + (step or 0)
        )

        self.parent.after(
            0,
            self._set_status,
            header,
            detail,
            absolute_step,
            is_error,
            success,
            output_path,
        )

    def _set_status(
        self,
        header,
        detail,
        step,
        is_error,
        success=False,
        output_path=None,
    ):
        if step is not None:
            self.progress_bar["value"] = step

        bg, fg = BG, TEXT_MUTED

        if is_error:
            bg, fg = ERROR_BG, ERROR_FG
        elif success:
            bg, fg = SUCCESS_BG, SUCCESS_FG

        self.header_label.configure(
            text=header,
            fg=fg if (success or is_error) else TEXT,
        )
        self.status_frame.configure(bg=bg)
        self.status_label.configure(
            text=detail,
            bg=bg,
            fg=fg,
        )

    def _on_close(self):
        if self.success and self.output_paths:
            explorer_manager.reveal(self.output_paths[-1])

        self.destroy()


class ValidationDialog(tk.Toplevel):
    """
    Scan all selected invoices before processing.

    Blank Customer PO values, and Velvet Taco "Verbal" values, may be explicitly
    approved as office-created second-delivery exceptions. Other invalid values
    cannot be bypassed.
    """

    WIDTH = 620
    HEIGHT = 430

    def __init__(self, parent, pdf_paths: list[str]):
        super().__init__(parent)

        self.parent = parent
        self.pdf_paths = list(pdf_paths)
        self.results = []
        self.approved_exceptions: set[str] = set()
        self.validation_passed = False

        self.title("Invoice Validation")
        self.configure(bg=BG)
        self.resizable(False, False)
        center_over(self, parent, self.WIDTH, self.HEIGHT)
        bring_to_front(self)
        self.iconbitmap(resource_path("Martins-Distribution_RGB.ico"))
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._build_ui()

        threading.Thread(
            target=self._scan_invoices,
            daemon=True,
        ).start()

    def _build_ui(self):
        tk.Label(
            self,
            text="Checking selected invoices…",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 14, "bold"),
            anchor="w",
        ).pack(fill="x", padx=24, pady=(24, 8))

        self.progress_label = tk.Label(
            self,
            text="Preparing scan…",
            bg=BG,
            fg=TEXT_MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.progress_label.pack(fill="x", padx=24, pady=(0, 10))

        self.results_text = tk.Text(
            self,
            height=15,
            width=72,
            bg=PANEL,
            fg=TEXT,
            relief="flat",
            highlightbackground=BORDER,
            highlightthickness=1,
            state="disabled",
            wrap="word",
        )
        self.results_text.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        self.action_btn = tk.Button(
            self,
            text="Checking…",
            command=self._review_results,
            state="disabled",
            font=("Segoe UI", 10, "bold"),
            bg=ACCENT,
            fg="white",
            activebackground=ACCENT_DARK,
            relief="flat",
            cursor="hand2",
            pady=8,
        )
        self.action_btn.pack(fill="x", padx=24, pady=(0, 10))

        self.close_review_btn = tk.Button(
            self,
            text="Close Review",
            command=self._cancel,
            font=("Segoe UI", 9),
            bg=PANEL,
            fg=TEXT_MUTED,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            cursor="hand2",
        )
        self.close_review_btn.pack(fill="x", padx=24, pady=(0, 20))

    def _scan_invoices(self):
        for index, pdf_path in enumerate(self.pdf_paths, start=1):
            self.parent.after(
                0,
                lambda i=index, total=len(self.pdf_paths):
                    self.progress_label.configure(
                        text=f"Scanning invoice {i} of {total}…"
                    ),
            )

            try:
                raw_text = extract_text(pdf_path)
                cleaned_text = refine_data(raw_text)
                order_details = extract_order_details(cleaned_text)

                result = validate_invoice(pdf_path, order_details)
                self.results.append(result)

            except Exception as exc:
                from types import SimpleNamespace

                self.results.append(
                    SimpleNamespace(
                        invoice_path=pdf_path,
                        invoice_number=None,
                        customer_name=None,
                        client=None,
                        customer_po=None,
                        valid=False,
                        can_approve_exception=False,
                        message=f"Could not scan invoice: {exc}",
                    )
                )

        self.parent.after(0, self._display_results)

    def _display_results(self):
        invalid = [result for result in self.results if not result.valid]

        self.results_text.configure(state="normal")
        self.results_text.delete("1.0", "end")

        if not invalid:
            self.results_text.insert(
                "end",
                "✓ All selected invoices passed the required client validation.\n\n"
                "The batch is ready to be processed.",
            )
            self.action_btn.configure(
                text="Continue to Processing",
                state="normal",
            )
        else:
            self.results_text.insert(
                "end",
                f"{len(invalid)} invoice(s) require attention:\n\n",
            )

            for result in invalid:
                self.results_text.insert(
                    "end",
                    f"Invoice: {result.invoice_number or 'UNKNOWN'}\n"
                    f"Client: {result.client or 'UNKNOWN'}\n"
                    f"File: {os.path.basename(result.invoice_path)}\n"
                    f"Problem: {result.message}\n"
                    f"Exception available: "
                    f"{'Yes' if result.can_approve_exception else 'No'}\n\n",
                )

            exception_count = sum(
                result.can_approve_exception for result in invalid
            )

            if exception_count:
                self.action_btn.configure(
                    text="Review PO Exceptions",
                    state="normal",
                )
            else:
                self.action_btn.configure(
                    text="Cannot Continue",
                    state="disabled",
                )

        self.results_text.configure(state="disabled")

    def _review_results(self):
        invalid = [result for result in self.results if not result.valid]

        for result in invalid:
            if not result.can_approve_exception:
                continue

            if not result.customer_po:
                po_message = (
                    "The Customer PO field is blank.\n\n"
                )
            else:
                po_message = (
                    f"The Customer PO field contains "
                    f"'{result.customer_po}'.\n\n"
                )

            exception_message = (
                f"Invoice: {result.invoice_number or 'UNKNOWN'}\n"
                f"Client: {result.client}\n\n"
                f"{po_message}"
                "Approve this invoice as an office-created "
                "second-delivery exception?\n\n"
                "If approved, the outbound CSV Customer PO will be "
                "set to 'Verbal'."
            )

            approved = messagebox.askyesno(
                "Approve Customer PO Exception",
                exception_message,
                parent=self,
            )

            if approved:
                self.approved_exceptions.add(result.invoice_path)

        remaining = [
            result
            for result in invalid
            if result.invoice_path not in self.approved_exceptions
        ]

        # A non-exception validation error can never be bypassed.
        if any(not result.can_approve_exception for result in remaining):
            messagebox.showerror(
                "Invoices Require Correction",
                "One or more invoices still contain validation errors. "
                "Correct those invoices and scan the batch again.",
                parent=self,
            )
            return

        # An exception was available but the user did not approve it.
        if remaining:
            messagebox.showerror(
                "Invoices Require Correction",
                "One or more invoices were not approved for the exception. "
                "Correct those invoices and scan the batch again.",
                parent=self,
            )
            return

        self.validation_passed = True
        self.destroy()


    def _cancel(self):
        self.validation_passed = False
        self.destroy()


# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------

class MainScreen(tk.Frame):
    def __init__(self, parent, config: dict):
        super().__init__(parent, bg=BG)
        self.parent = parent
        self.config = config
        # All PDFs selected for the current processing session.
        self.selected_files: list[str] = []
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
        section_label(self, "Selected invoices:")

        selected_row = tk.Frame(self, bg=BG)
        selected_row.pack(fill="x", padx=24, pady=(2, 0))

        selected_box = tk.Frame(selected_row, bg=PANEL, relief="flat",
                                highlightbackground=BORDER, highlightthickness=1)
        selected_box.pack(fill="x", expand=True)

        self.filename_label = tk.Label(
            selected_box, text="No invoices selected yet",
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

        # selected_files holds the full paths used internally by the batch pipeline.
        self.selected_files = []

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
        return f"Logged in as:  {CURRENT_USER}     \u2022     {now}"

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

    def _update_selected_display(self, paths):
        """Store and display one or more selected invoice PDFs."""

        if isinstance(paths, str):
            paths = [paths]

        self.selected_files = list(paths)

        self.process_btn.configure(
            text="Process Invoice"
            if len(self.selected_files) == 1
            else "Process Invoices"
        )

        if not self.selected_files:
            self.filename_label.configure(
                text="No invoices selected yet",
                fg=TEXT,
            )
            self.path_label.configure(text="", fg=TEXT_MUTED)
            return

        if len(self.selected_files) == 1:
            path = self.selected_files[0]
            self.filename_label.configure(
                text=os.path.basename(path),
                fg=TEXT,
            )
            self.path_label.configure(
                text=path,
                fg=TEXT_MUTED,
            )
            return

        names = [
            os.path.basename(path)
            for path in self.selected_files[:6]
        ]

        display = "\n".join(names)

        if len(self.selected_files) > 6:
            display += f"\n…and {len(self.selected_files) - 6} more"

        self.filename_label.configure(
            text=f"{len(self.selected_files)} invoices selected",
            fg=TEXT,
        )
        self.path_label.configure(
            text=display,
            fg=TEXT_MUTED,
        )

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
        dropped_paths = self.parent.tk.splitlist(event.data)
        cleaned_paths = [path.strip("{}") for path in dropped_paths]
        self._update_selected_display(cleaned_paths)

    # ── File browser ───────────────────────────────────────────────────

    def _browse(self):
        initial = self.config.get("input_folder") or "/"
        paths = filedialog.askopenfilenames(
            title="Select invoice PDFs",
            initialdir=initial,
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if paths:
            self._update_selected_display(paths)

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
        if not self.selected_files:
            messagebox.showwarning(
                "No invoices selected",
                "Please select one or more invoice PDFs before processing.",
                parent=self.parent,
            )
            return

        invalid_files = [
            path
            for path in self.selected_files
            if not os.path.isfile(path)
            or not path.lower().endswith(".pdf")
        ]

        if invalid_files:
            messagebox.showwarning(
                "Invalid invoice selection",
                "One or more selected files are missing or are not PDF files. "
                "Please correct the selection and try again.",
                parent=self.parent,
            )
            return

        output_folder = self.config.get("output_folder", "pdf_output")

        # Scan the entire batch before any invoice is processed.
        validation_dialog = ValidationDialog(
            self.parent,
            self.selected_files,
        )
        self.parent.wait_window(validation_dialog)

        if not validation_dialog.validation_passed:
            return

        self.process_btn.configure(
            state="disabled",
            text="Processing…",
            bg="#93C5FD",
        )

        dialog = ProcessingDialog(
            self.parent,
            self.selected_files,
            output_folder,
            po_exceptions=validation_dialog.approved_exceptions,
        )

        self.parent.wait_window(dialog)

        self.process_btn.configure(
            state="normal",
            text="Process Invoice" if len(self.selected_files) == 1 else "Process Invoices",
            bg=ACCENT,
        )


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

class App:
    # Unique name for the Windows mutex — must not collide with any other app.
    _MUTEX_NAME = "MDInvoiceProcessor_SingleInstance"

    def __init__(self):
        # ── Single-instance guard ─────────────────────────────────────
        # Create a named mutex. If it already exists, another instance is
        # running — focus that window and exit this one silently.
        self._mutex = ctypes.windll.kernel32.CreateMutexW(None, False, self._MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            # Try to find and restore the existing window by its title.
            hwnd = ctypes.windll.user32.FindWindowW(None, "Invoice Processor")
            if hwnd:
                # SW_RESTORE (9) un-minimises the window if it was minimised.
                ctypes.windll.user32.ShowWindow(hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            sys.exit(0)

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