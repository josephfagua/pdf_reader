"""
explorer_manager.py

Centralized Windows Explorer integration.

Responsibilities
----------------
• Reveal newly created files.
• Reuse an existing Explorer window for the output folder whenever possible.
• Open a new Explorer window only when necessary.

No other module should call explorer.exe directly.
"""

from __future__ import annotations

import os
import pythoncom
import win32com.client
import win32gui
import win32con


class ExplorerManager:
    """Handles all interactions with Windows Explorer."""

    def reveal(self, file_path: str) -> None:
        """
        Reveal a file inside Windows Explorer.

        If an Explorer window is already displaying the parent folder,
        it is reused and brought to the foreground.

        Otherwise a new Explorer window is opened.
        """

        pythoncom.CoInitialize()

        folder = os.path.dirname(os.path.abspath(file_path))
        filename = os.path.basename(file_path)

        shell = win32com.client.Dispatch("Shell.Application")

        explorer = self._find_existing_window(shell, folder)

        if explorer is None:
            explorer = shell.Open(folder)

            # Refresh the collection
            shell = win32com.client.Dispatch("Shell.Application")
            explorer = self._find_existing_window(shell, folder)

        if explorer is None:
            return

        hwnd = explorer.HWND

        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)

        item = explorer.Document.Folder.ParseName(filename)

        if item:
            explorer.Document.SelectItem(item, 17)

    def _find_existing_window(self, shell, folder):

        folder = os.path.normcase(os.path.abspath(folder))

        for window in shell.Windows():

            try:

                current = os.path.normcase(
                    os.path.abspath(window.Document.Folder.Self.Path)
                )

                if current == folder:
                    return window

            except Exception:
                continue

        return None


explorer_manager = ExplorerManager()