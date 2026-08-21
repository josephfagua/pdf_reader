"""
explorer_manager.py — Windows Explorer integration for MDIP.

The rest of the application should call only ExplorerManager.reveal().
"""

from __future__ import annotations

import os
import time

import pythoncom
import win32com.client
import win32con
import win32gui


class ExplorerManager:
    """Reveal a processed file using the Windows Shell."""

    def reveal(self, file_path: str) -> bool:
        """Reuse an Explorer window for the folder or open one if needed."""

        file_path = os.path.abspath(file_path)
        folder = os.path.dirname(file_path)
        filename = os.path.basename(file_path)

        pythoncom.CoInitialize()

        try:
            shell = win32com.client.Dispatch("Shell.Application")
            explorer = self._find_window(shell, folder)

            if explorer is None:
                shell.Open(folder)
                explorer = self._wait_for_window(shell, folder)

            if explorer is None:
                return False

            hwnd = int(explorer.HWND)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass

            item = explorer.Document.Folder.ParseName(filename)

            if item is None:
                return False

            explorer.Document.SelectItem(item, 17)
            return True

        except Exception:
            return False

        finally:
            pythoncom.CoUninitialize()

    @staticmethod
    def _find_window(shell, folder):
        target = os.path.normcase(
            os.path.normpath(os.path.abspath(folder))
        )

        for window in shell.Windows():
            try:
                current = os.path.normcase(
                    os.path.normpath(
                        os.path.abspath(window.Document.Folder.Self.Path)
                    )
                )
                if current == target:
                    return window
            except Exception:
                continue

        return None

    def _wait_for_window(self, shell, folder, timeout=2.0):
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            window = self._find_window(shell, folder)
            if window is not None:
                return window
            time.sleep(0.1)

        return None


explorer_manager = ExplorerManager()
