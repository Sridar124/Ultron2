"""Quiet Windows Ctrl+U launcher and shutdown toggle for Ultron."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time


BASE_DIR = Path(__file__).resolve().parent
MAIN_PY = BASE_DIR / "main.py"
HOTKEY_ID = 0x554C
WM_HOTKEY = 0x0312
WM_CLOSE = 0x0010
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_U = 0x55
ERROR_ALREADY_EXISTS = 183


def _log(message: str) -> None:
    try:
        local_app_data = Path(os.environ.get("LOCALAPPDATA", str(BASE_DIR)))
        log_path = local_app_data / "Ultron" / "startup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"[Ctrl+U] {message}\n")
    except Exception:
        pass


def _running_app_pids(psutil) -> list[int]:
    main_path = str(MAIN_PY.resolve()).casefold()
    app_dir = str(BASE_DIR.resolve()).casefold()
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            info = proc.info
            args = [str(arg).casefold() for arg in (info.get("cmdline") or [])]
            is_main_script = any(
                arg == main_path or Path(arg.strip('"')).name.casefold() == "main.py"
                and main_path in arg
                for arg in args
            )
            exe = str(info.get("exe") or "").casefold()
            is_bundled_app = (
                Path(exe).name.casefold() == "ultron.exe"
                and str(Path(exe).parent).casefold() == app_dir
            )
            if int(info.get("pid") or 0) != os.getpid() and (is_main_script or is_bundled_app):
                pids.append(int(info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, OSError):
            continue
    return pids


def _close_app(pids: list[int], psutil) -> None:
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    windows: list[int] = []

    @enum_proc
    def collect(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    for hwnd in windows:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        remaining = _running_app_pids(psutil)
        if not remaining:
            return
        time.sleep(0.1)

    # A hidden or hung window can ignore WM_CLOSE. End only the Ultron process
    # identified above so the toggle still reliably reaches the off state.
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            if proc.is_running():
                proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _show_app(pids: list[int]) -> bool:
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindowAsync.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    windows: list[int] = []

    @enum_proc
    def collect(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    if not windows:
        return False
    for hwnd in windows:
        # SW_RESTORE also handles minimized windows; ShowWindowAsync is safe
        # to call from the listener's message thread.
        user32.ShowWindowAsync(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
    return True


def _launch_app() -> None:
    python = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        current = Path(sys.executable)
        python = current.with_name("python.exe") if current.name.lower() == "pythonw.exe" else current
    log_path = Path(os.environ.get("LOCALAPPDATA", str(BASE_DIR))) / "Ultron" / "startup.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("[Ctrl+U] Launching visible Ultron UI.\n")
            log_file.flush()
            subprocess.Popen(
                [str(python), str(MAIN_PY), "--startup"],
                cwd=str(BASE_DIR),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
        _log("Ctrl+U launch request sent")
    except Exception as exc:
        _log(f"Ctrl+U could not start Ultron: {exc}")


def _toggle_app(psutil) -> None:
    pids = _running_app_pids(psutil)
    if not pids:
        _launch_app()
        return

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    foreground = user32.GetForegroundWindow()
    foreground_pid = wintypes.DWORD()
    if foreground:
        user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
    if foreground_pid.value in pids:
        _close_app(pids, psutil)
        _log("Ctrl+U deactivated Ultron")
    else:
        if _show_app(pids):
            _log("Ctrl+U restored Ultron to the screen")
            return
        # A leftover Python process with no UI is not an active Ultron window.
        # Clear that failed launch and start a fresh visible app instance.
        for pid in pids:
            try:
                psutil.Process(pid).terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _launch_app()


def _poll_hotkey(psutil, user32) -> None:
    """Fallback for desktops where another app has claimed RegisterHotKey."""
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    was_down = False
    while True:
        ctrl_down = bool(user32.GetAsyncKeyState(0x11) & 0x8000)
        u_down = bool(user32.GetAsyncKeyState(VK_U) & 0x8000)
        is_down = ctrl_down and u_down
        if is_down and not was_down:
            _toggle_app(psutil)
        was_down = is_down
        time.sleep(0.025)


def main() -> int:
    if os.name != "nt":
        return 0
    try:
        import psutil
    except ImportError as exc:
        _log(f"Ctrl+U listener requires psutil: {exc}")
        return 1

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel32.CreateMutexW(None, True, "Local\\UltronCtrlUToggle")
    if not mutex:
        _log("Ctrl+U listener could not create its singleton mutex")
        return 1
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(mutex)
        return 0

    user32 = ctypes.windll.user32
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_NOREPEAT, VK_U))
    try:
        if registered:
            _log("Ctrl+U global toggle listener ready")
            msg = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    _toggle_app(psutil)
        else:
            _log("Ctrl+U RegisterHotKey unavailable; using global key polling")
            _poll_hotkey(psutil, user32)
    except Exception as exc:
        _log(f"Ctrl+U listener stopped: {exc}")
        return 1
    finally:
        if registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
        kernel32.ReleaseMutex(mutex)
        kernel32.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
