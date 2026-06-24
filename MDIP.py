"""
gui.py — Invoice Pipeline Launcher

Pages:
  - Config screen (first launch or via button): set input + output folders
  - Main screen: select/drop a PDF and process it

Requires:
    pip install tkinterdnd2
"""

import os
from pathlib import Path
import json
import threading
import sys
import tkinter as tk
from tkinter import filedialog

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

from src.main import extract_text, refine_data, parse_items, extract_order_details, export_items_csv

def resource_path(relative_path: str) -> str:
    """Get an absolute path to a resource, works for dev and for PyInstaller .exe"""
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return str(base_path / relative_path)

# ---------------------------------------------------------------------------
# Helper functions for tkinter windows
# ---------------------------------------------------------------------------
def center_window(window, width: int, height: int):
    window.withdraw()  # hide 

    window.update_idletasks()

    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()

    x = (screen_width - width) // 2
    y = (screen_height - height) // 2

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

from pathlib import Path
import os

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
SUCCESS_BG  = "#ECFDF5"
SUCCESS_FG  = "#065F46"
ERROR_BG    = "#FEF2F2"
ERROR_FG    = "#991B1B"
DROP_HOVER  = "#EFF6FF"


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(pdf_path: str, output_folder: str, status_callback) -> None:
    pdf_path = pdf_path.strip().strip("{}")

    if not os.path.isfile(pdf_path):
        status_callback(f"File not found:\n{pdf_path}", is_error=True)
        return

    if not pdf_path.lower().endswith(".pdf"):
        status_callback("Please select a PDF file.", is_error=True)
        return

    try:
        status_callback("Extracting text from PDF…", is_error=False)
        raw_text = extract_text(pdf_path)

        status_callback("Cleaning invoice data…", is_error=False)
        cleaned_text = refine_data(raw_text)

        status_callback("Parsing line items and order details…", is_error=False)
        order_data = {
            "order_details": extract_order_details(cleaned_text),
            "items":         parse_items(cleaned_text),
        }

        status_callback("Exporting CSV…", is_error=False)
        os.makedirs(output_folder, exist_ok=True)
        output_path = export_items_csv(order_data, output_folder)

        # # Copy the original PDF into the output folder (not move)
        # pdf_copy_dest = os.path.join(output_folder, os.path.basename(pdf_path))
        # if os.path.abspath(pdf_path) != os.path.abspath(pdf_copy_dest):
        #     shutil.copy2(pdf_path, pdf_copy_dest)

        status_callback(
            f"Done!\n\nCSV saved to:\n{output_path}\n",
            is_error=False,
            success=True,
        )

    except Exception as exc:
        status_callback(f"Error during processing:\n{exc}", is_error=True)


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
        bring_to_front(self.root)
        self.grab_set()  
        self.iconbitmap(resource_path("Martins-Distribution_RGB.ico"))
        self.on_save = on_save
        self.is_first_launch = is_first_launch

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

        folder_row(self, "Input folder  (where your PDF invoices are located)", self.input_var)
        folder_row(self, "Output folder  (where CSVs will be saved)", self.output_var)

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
            self.validation_label.configure(text="Both folders are required.")
            return

        if not os.path.isdir(input_folder):
            self.validation_label.configure(text="Input folder path does not exist.")
            return

        if not os.path.isdir(output_folder):
            try:
                os.makedirs(output_folder, exist_ok=True)
            except OSError:
                self.validation_label.configure(text="Could not create output folder.")
                return

        config = {"input_folder": input_folder, "output_folder": output_folder}
        save_config(config)
        self.on_save(config)
        self.destroy()


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

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=24, pady=(28, 4))

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
            text="Drag & drop a PDF here" if DND_AVAILABLE else "Select a PDF below",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"),
        )
        self.drop_label.pack(pady=(6, 2))

        tk.Label(inner, text="or use the Browse button below",
                 bg=PANEL, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack()

        self.drop_zone.bind("<Button-1>", lambda e: self._browse())
        for child in inner.winfo_children():
            child.bind("<Button-1>", lambda e: self._browse())

        # ── Path entry ────────────────────────────────────────────────
        section_label(self, "File path")

        entry_row = tk.Frame(self, bg=BG)
        entry_row.pack(fill="x", padx=24, pady=(2, 16))

        self.path_var = tk.StringVar()
        tk.Entry(entry_row, textvariable=self.path_var,
                 font=("Segoe UI", 10), bg=PANEL, fg=TEXT,
                 insertbackground=TEXT, relief="flat",
                 highlightbackground=BORDER, highlightthickness=1
                 ).pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))

        tk.Button(entry_row, text="Browse…", command=self._browse,
                  font=("Segoe UI", 9), bg=PANEL, fg=ACCENT,
                  activebackground=DROP_HOVER, activeforeground=ACCENT_DARK,
                  relief="flat", highlightbackground=BORDER, highlightthickness=1,
                  cursor="hand2", padx=10, pady=6).pack(side="right")

        # ── Process button ────────────────────────────────────────────
        self.process_btn = tk.Button(self, text="Process Invoice",
                                     command=self._start_processing,
                                     font=("Segoe UI", 11, "bold"),
                                     bg=ACCENT, fg="white",
                                     activebackground=ACCENT_DARK, activeforeground="white",
                                     relief="flat", cursor="hand2", pady=10)
        self.process_btn.pack(fill="x", padx=24, pady=(0, 20))

        # ── Status ────────────────────────────────────────────────────
        self.status_frame = tk.Frame(self, bg=BG)
        self.status_frame.pack(fill="x", padx=24, pady=(0, 24))

        self.status_label = tk.Label(self.status_frame, text="", bg=BG, fg=TEXT_MUTED,
                                     font=("Segoe UI", 9), anchor="w",
                                     justify="left", wraplength=490)
        self.status_label.pack(fill="x")

    # ── Drag-and-drop ─────────────────────────────────────────────────

    def _on_drag_enter(self, event):
        self.drop_zone.configure(bg=DROP_HOVER, highlightbackground=ACCENT)
        self.drop_label.configure(bg=DROP_HOVER, text="Drop to select")

    def _on_drag_leave(self, event):
        self.drop_zone.configure(bg=PANEL, highlightbackground=BORDER)
        self.drop_label.configure(bg=PANEL, text="Drag & drop a PDF here")

    def _on_drop(self, event):
        self.drop_zone.configure(bg=PANEL, highlightbackground=BORDER)
        self.drop_label.configure(bg=PANEL, text="Drag & drop a PDF here")
        self.path_var.set(event.data.strip().strip("{}"))

    # ── File browser ──────────────────────────────────────────────────

    def _browse(self):
        initial = self.config.get("input_folder") or "/"
        path = filedialog.askopenfilename(
            title="Select an invoice PDF",
            initialdir=initial,
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self.path_var.set(path)

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
            self._set_status("Please select or enter a PDF path first.", is_error=True)
            return

        self.process_btn.configure(state="disabled", text="Processing…", bg="#93C5FD")
        self._set_status("Starting…", is_error=False)

        output_folder = self.config.get("output_folder", "pdf_output")

        threading.Thread(
            target=run_pipeline,
            args=(pdf_path, output_folder, self._thread_safe_status),
            daemon=True,
        ).start()

    def _thread_safe_status(self, message, is_error, success=False):
        self.parent.after(0, self._set_status, message, is_error, success)

    def _set_status(self, message, is_error, success=False):
        if success:
            bg, fg = SUCCESS_BG, SUCCESS_FG
        elif is_error:
            bg, fg = ERROR_BG, ERROR_FG
        else:
            bg, fg = BG, TEXT_MUTED

        self.status_frame.configure(bg=bg)
        self.status_label.configure(text=message, bg=bg, fg=fg)

        if success or is_error:
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
        center_window(self.root, 540, 580)
        bring_to_front(self.root)
        self.root.iconbitmap(resource_path("Martins-Distribution_RGB.ico"))
        config = load_config()

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