#!/usr/bin/env python3
"""
Eleòra Kleon
System maintenance utility for Fedora Linux/KDE.

https://github.com/eleora-dev/kleon
License: MIT
"""

from __future__ import annotations

import json
import os
import platform
import pwd
import random
import re
import selectors
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    import resources  # noqa: F401 - Qt resource registration side effect
except ImportError:
    pass
from PySide6.QtCore import (
    QObject, QThread, Signal, Qt, QSize, QUrl, QEvent
)
from PySide6.QtGui import (
    QIcon, QPixmap, QSyntaxHighlighter, QTextCharFormat, QColor, QPainter,
    QPalette, QAction, QKeySequence, QFont, QFontDatabase, QDesktopServices
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMessageBox, QMainWindow,
    QWidget, QSizePolicy, QStackedWidget,
    QHBoxLayout, QVBoxLayout, QLabel,
    QGroupBox, QCheckBox, QPlainTextEdit, QProgressBar,
    QGraphicsDropShadowEffect, QFrame, QPushButton, QToolButton, QDialog
)

from locales import T

APP_TITLE = "Kleon"
APP_VERSION = "1.0"
APP_YEAR = "2026"
APP_AUTHOR = "Gerardo Perilli"
APP_STUDIO = "Eleòra"
APP_DIR = Path(__file__).resolve().parent
APP_ICON_RESOURCE = ":/icons/kleon.png"
APP_ICON_FALLBACK = APP_DIR / "assets" / "kleon.png"
APP_ICON_CHAR = "K"
DEV_URL = "https://github.com/eleora-dev"
APP_ISSUES = "https://github.com/eleora-dev/kleon/issues"
APP_ACCENT = "#fe9306"
APP_FOOTER = "#0161dc"
APP_LOG_OK = "#1cdc9a"
APP_LOG_ERROR = "#da4453"
APP_LOG_STOP = "#f67400"

APP_TITLEBAR_BORDER = "#d98d00"
APP_TITLEBAR_TEXT = "#1f1f1f"
APP_TITLEBAR_SUBTLE = "#4d3512"
APP_TITLEBAR_HOVER = "rgba(0, 0, 0, 0.12)"
APP_TITLEBAR_PRESSED = "rgba(0, 0, 0, 0.20)"
APP_TITLEBAR_DISABLED = "rgba(31, 31, 31, 0.45)"
APP_FOOTER_BUTTON_BG = "rgba(255, 255, 255, 0.15)"
APP_FOOTER_BUTTON_HOVER = "rgba(255, 255, 255, 0.25)"
LOG_RULE_CHAR = "━"
LOG_RULE_WIDTH = 72
CONFIG_DIR_NAME = "eleora-kleon"
SETTINGS_FILE_NAME = "settings.json"
SETTINGS_VERSION = 1


def log_banner(title: str) -> tuple[str, str]:
    """Return a left-aligned log banner with a title and a rule."""
    return (
        f"┏━ {title}",
        f"┗{LOG_RULE_CHAR * LOG_RULE_WIDTH}",
    )

APP_THEME_LIGHT = {
    "bg": "#ffffff",
    "text": "#222222",
    "nav_bg": "#f5f5f5",
    "nav_border": "#e8e8e8",
    "accent": APP_ACCENT,
    "accent_text": "#ffffff",
    "btn_color": "#222222",
    "btn_hover_bg": "#eeeeee",
    "footer_bg": APP_FOOTER,
    "footer_text": "#ffffff",
    "ctx_bg": "#ffffff",
    "ctx_border": "#e0e0e0",
    "ctx_text": "#222222",
    "ctx_separator": "#e8e8e8",
    "disabled": "#9a9a9a",
    "titlebar_bg": APP_ACCENT,
    "titlebar_border": APP_TITLEBAR_BORDER,
    "titlebar_text": APP_TITLEBAR_TEXT,
    "titlebar_subtle": APP_TITLEBAR_SUBTLE,
    "titlebar_button_hover": APP_TITLEBAR_HOVER,
    "titlebar_button_pressed": APP_TITLEBAR_PRESSED,
    "titlebar_close_hover": APP_TITLEBAR_HOVER,
    "titlebar_close_pressed": APP_TITLEBAR_PRESSED,
    "titlebar_disabled": APP_TITLEBAR_DISABLED,
    "footer_button_bg": APP_FOOTER_BUTTON_BG,
    "footer_button_hover": APP_FOOTER_BUTTON_HOVER,
    "log_ok": APP_LOG_OK,
    "log_error": APP_LOG_ERROR,
    "log_stop": APP_LOG_STOP,
}

APP_THEME_DARK = {
    **APP_THEME_LIGHT,
    "bg": "#1e1e1e",
    "text": "#eeeeee",
    "nav_bg": "#252525",
    "nav_border": "#333333",
    "btn_color": "#bbbbbb",
    "btn_hover_bg": "#333333",
    "ctx_bg": "#252525",
    "ctx_border": "#383838",
    "ctx_text": "#eeeeee",
    "ctx_separator": "#383838",
    "disabled": "#777777",
}


def _app_icon_source_pixmap() -> QPixmap:
    """Return the best source pixmap for the application icon."""
    pixmap = QPixmap(APP_ICON_RESOURCE)
    if not pixmap.isNull():
        return pixmap

    if APP_ICON_FALLBACK.exists():
        pixmap = QPixmap(str(APP_ICON_FALLBACK))
        if not pixmap.isNull():
            return pixmap

    return QPixmap()


def app_window_icon() -> QIcon:
    """Return the app/window icon from Qt resources, with a file fallback."""
    source = _app_icon_source_pixmap()
    if source.isNull():
        return QIcon()

    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(
            source.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
    icon.addPixmap(source)
    return icon


def app_header_icon_pixmap(size: int = 34) -> QPixmap:
    """Return a sharp app icon pixmap for custom title bars."""
    source = _app_icon_source_pixmap()
    if source.isNull():
        return QPixmap()

    screen = QApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    physical_size = int(round(size * dpr))

    pixmap = source.scaled(
        physical_size,
        physical_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _rgb_lightness_from_config_value(value: str) -> float | None:
    """Return approximate lightness from KDE RGB config values such as '35,38,41'."""
    try:
        nums = [int(n) for n in re.findall(r"\d+", value)[:3]]
        if len(nums) != 3:
            return None
        r, g, b = nums
        return (max(r, g, b) + min(r, g, b)) / 2
    except Exception:
        return None


def kde_uses_dark_palette() -> bool | None:
    """Detect KDE/Plasma light/dark preference from kdeglobals, when available.

    Qt can report a light palette for Python apps even while Plasma is using a
    dark color scheme. Reading kdeglobals first keeps the whole Kleon window
    in sync with the actual KDE theme.
    """
    candidate_config_dirs: list[Path] = []

    raw_xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if raw_xdg_config:
        candidate_config_dirs.append(Path(raw_xdg_config).expanduser())

    # When the app is launched through sudo/pkexec, Path.home() may point to
    # /root.  KDE's theme lives in the real user's config directory, so include
    # that path explicitly as well.
    try:
        real_uid = int(os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID") or os.getuid())
        real_home = Path(pwd.getpwuid(real_uid).pw_dir)
        candidate_config_dirs.append(real_home / ".config")
    except Exception:
        pass

    candidate_config_dirs.append(Path.home() / ".config")

    candidates: list[Path] = []
    seen_paths: set[Path] = set()
    for config_dir in candidate_config_dirs:
        for candidate in (config_dir / "kdeglobals", config_dir / "kdedefaults" / "kdeglobals"):
            if candidate not in seen_paths:
                candidates.append(candidate)
                seen_paths.add(candidate)

    for path in candidates:
        if not path.exists():
            continue

        section = ""
        color_scheme_name = ""
        bg_lightness_values: list[float] = []

        try:
            for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw_line.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1]
                    continue
                if "=" not in line:
                    continue
                key, value = [part.strip() for part in line.split("=", 1)]

                if section == "General" and key in {"ColorScheme", "Name"}:
                    color_scheme_name = value.lower()

                if section in {"Colors:Window", "Colors:View"} and key in {"BackgroundNormal", "BackgroundAlternate"}:
                    lightness = _rgb_lightness_from_config_value(value)
                    if lightness is not None:
                        bg_lightness_values.append(lightness)
        except Exception:
            continue

        if "dark" in color_scheme_name or "night" in color_scheme_name:
            return True
        if "light" in color_scheme_name:
            return False
        if bg_lightness_values:
            return (sum(bg_lightness_values) / len(bg_lightness_values)) < 128

    return None


def app_uses_dark_palette() -> bool:
    """Return True when the active KDE/Qt palette is dark.

    KDE config is checked before Qt's palette because Qt may expose a light
    default palette to PySide apps even when Plasma itself is in dark mode.
    """
    forced = os.environ.get("KLEON_THEME", "").strip().lower()
    if forced in {"dark", "d", "1", "true"}:
        return True
    if forced in {"light", "l", "0", "false"}:
        return False

    kde_dark = kde_uses_dark_palette()
    if kde_dark is not None:
        return kde_dark

    app = QApplication.instance()

    try:
        if app is not None:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False
    except Exception:
        pass

    try:
        palette = app.palette() if app is not None else QApplication.palette()
        base = palette.color(QPalette.ColorRole.Base).lightness()
        window = palette.color(QPalette.ColorRole.Window).lightness()
        return ((base + window) / 2) < 128
    except Exception:
        return False


def app_theme() -> dict[str, str]:
    """Return the shared light/dark visual palette used by the main window and About."""
    t = dict(APP_THEME_DARK if app_uses_dark_palette() else APP_THEME_LIGHT)
    # Keep the log area part of the same card palette. It must not keep a
    # terminal-like dark background while the rest of the app is light.
    t.update({
        "log_bg": t["ctx_bg"],
        "log_text": t["ctx_text"],
    })
    return t


# ── Utility functions ─────────────────────────────────────────────────────────

def real_user_info() -> tuple[int, str, str]:
    """
    Return (uid, username, home) of the real user,
    accounting for sudo and pkexec elevation.
    """
    uid = int(os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID") or os.getuid())
    pw = pwd.getpwuid(uid)
    return uid, pw.pw_name, pw.pw_dir


def app_settings_path(real_home: str | Path) -> Path:
    """Return the per-user settings path under ~/.config/eleora-kleon."""
    return Path(real_home).expanduser() / ".config" / CONFIG_DIR_NAME / SETTINGS_FILE_NAME


def write_text_for_user(path: Path, text: str, uid: int) -> None:
    """Write a text file and keep it owned by the real user when elevated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

    try:
        gid = pwd.getpwuid(uid).pw_gid
        os.chown(path.parent, uid, gid)
        os.chown(path, uid, gid)
    except Exception:
        pass


def fmt_bytes_1dp(num_bytes: int) -> str:
    """Format a byte count as a human-readable string with one decimal place."""
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    b = float(max(0, num_bytes))
    i = 0
    while b >= 1024.0 and i < len(units) - 1:
        b /= 1024.0
        i += 1
    return f"{b:.1f}{units[i]}"


def df_used_bytes_root() -> int:
    """Return the number of used bytes on the root partition."""
    st = os.statvfs("/")
    return int((st.f_blocks - st.f_bfree) * st.f_frsize)


def df_avail_size_root() -> tuple[int, int]:
    """Return (available bytes, total bytes) on the root partition."""
    st = os.statvfs("/")
    avail = st.f_bavail * st.f_frsize
    size = st.f_blocks * st.f_frsize
    return int(avail), int(size)


def _terminate_process_tree(p: subprocess.Popen) -> None:
    """Terminate a subprocess and its process group when possible."""
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except Exception:
        try:
            p.terminate()
        except Exception:
            pass

    try:
        p.wait(timeout=3)
        return
    except Exception:
        pass

    try:
        os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def _stream_process_output(p: subprocess.Popen, on_line, cancel_event, *, filter_pkexec_noise: bool = False) -> int:
    """
    Stream process output without blocking forever, so cancel requests can be
    handled even while commands such as dnf/flatpak are quiet.
    """
    assert p.stdout is not None

    pkexec_noise = (
        "Error executing command as another user",
        "This incident has been reported",
        "==== AUTHENTICATING FOR",
        "==== AUTHENTICATION COMPLETE",
    )

    def emit(line: str):
        stripped = line.rstrip("\n")
        if filter_pkexec_noise and any(noise in stripped for noise in pkexec_noise):
            return
        on_line(stripped)

    selector = selectors.DefaultSelector()
    selector.register(p.stdout, selectors.EVENT_READ)

    try:
        while True:
            if cancel_event.is_set():
                _terminate_process_tree(p)
                on_line(T["interrupted"])
                return 130

            events = selector.select(timeout=0.1)
            if events:
                line = p.stdout.readline()
                if line:
                    emit(line)
                    continue

            if p.poll() is not None:
                rest = p.stdout.read()
                if rest:
                    for line in rest.splitlines():
                        emit(line)
                return p.wait()
    finally:
        try:
            selector.close()
        except Exception:
            pass


def run_cmd(args, on_line, cancel_event) -> int:
    """
    Run a command, sending each output line to on_line.
    Returns the process exit code.
    """
    on_line(f"$ {' '.join(args)}")
    try:
        p = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except FileNotFoundError:
        on_line(f"{T['cmd_not_found']}: {args[0]}")
        return 127

    try:
        return _stream_process_output(p, on_line, cancel_event, filter_pkexec_noise=True)
    finally:
        try:
            if p.stdout:
                p.stdout.close()
        except Exception:
            pass


def run_root_block(commands: list[list[str]], on_line, cancel_event) -> int:
    """
    Run a list of commands with root privileges.
    If already root, executes them directly; otherwise batches them into
    a single bash script passed to pkexec (one authentication prompt).
    """
    if os.geteuid() == 0:
        rc = 0
        for cmd in commands:
            if cancel_event.is_set():
                on_line(T["interrupted"])
                return 130
            rc = run_cmd(cmd, on_line, cancel_event)
            if rc == 130:
                return rc
        return rc

    script = ""
    for cmd in commands:
        script += " ".join(shlex.quote(x) for x in cmd) + " || true\n"

    pk = ["pkexec", "bash", "-lc", script]
    try:
        p = subprocess.Popen(
            pk,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except FileNotFoundError:
        on_line(T["pkexec_not_found"])
        return 127

    try:
        return _stream_process_output(p, on_line, cancel_event)
    finally:
        try:
            if p.stdout:
                p.stdout.close()
        except Exception:
            pass


# ── Selected operations dataclass ─────────────────────────────────────────────

@dataclass
class SelectedOps:
    dnf: bool
    flatpak: bool
    cache: bool
    kernel: bool
    systemd: bool
    bash: bool
    browser: bool
    passwords: bool
    recent: bool
    logs: bool
    coredump: bool
    packagekit: bool
    tmp: bool
    abrt: bool


# ── Worker: runs all operations in a separate thread ──────────────────────────

class Worker(QObject):
    log = Signal(str)
    progress = Signal(int)
    running = Signal(bool)
    finished = Signal()
    current_op = Signal(str)

    def __init__(self, ops: SelectedOps, user: str, home: str):
        super().__init__()
        self.ops = ops
        self._user = user
        self._home = Path(home)
        self._cancel = threading.Event()

    def cancel(self):
        """Signal the worker to stop as soon as possible."""
        self._cancel.set()

    def _log(self, s: str):
        self.log.emit(s)

    def run(self):
        """Thread entry point: execute all enabled operations."""
        self.running.emit(True)
        self.progress.emit(0)

        try:
            rel = platform.freedesktop_os_release()
            os_name = f"{rel.get('NAME', 'Linux')} {rel.get('VERSION', '')}"
        except Exception:
            os_name = platform.system()

        # Snapshot of used space before operations (to compute freed space)
        root_before = df_used_bytes_root()
        for line in log_banner(f"{APP_STUDIO} {APP_TITLE.upper()} v{APP_VERSION}"):
            self._log(line)
        self._log("")
        self._log(f"{T['welcome_os']} {os_name}")
        self._log(f"{T['welcome_kernel']} {platform.release()}")
        self._log(f"{T['welcome_host']} {socket.gethostname()}")
        self._log("")
        self._log(f"{T['label_user']} {self._user}")
        self._log(f"{T['label_home']} {str(self._home)}")
        self._log("")

        completed = [0]
        total_ops = 0
        done_marker = "__KLEON_OPERATION_DONE__"
        initial_progress_emitted = [False]

        def emit_initial_progress_once():
            if not initial_progress_emitted[0]:
                initial_progress_emitted[0] = True
                self.progress.emit(random.randint(1, 2))

        def is_title_line(s: str) -> bool:
            return s.strip().startswith("━━━━━ ")

        def advance():
            completed[0] += 1
            pct = int((completed[0] / total_ops) * 100) if total_ops else 100
            self.progress.emit(min(pct, 99))

        def log_and_track(line: str):
            if line.strip() == done_marker:
                advance()
                return

            self.log.emit(line)
            if is_title_line(line):
                emit_initial_progress_once()
                op_name = line.strip().removeprefix("━━━━━ ").strip()
                self.current_op.emit(op_name)
                if "DNF" not in op_name:
                    self._cancel.wait(random.uniform(0.2, 0.9))

        root_cmds: list[list[str]] = []
        user_steps: list[tuple[str, Callable[[], None]]] = []

        def add_root_title(title: str):
            root_cmds.append(["echo", ""])
            root_cmds.append(["echo", f"━━━━━ {title}"])
            root_cmds.append(["sleep", "0.35"])

        def add_root_op(enabled: bool, title: str, cmds: list[list[str]]):
            nonlocal total_ops
            if not enabled:
                return
            total_ops += 1
            add_root_title(title)
            root_cmds.extend(cmds)
            root_cmds.append(["echo", done_marker])

        def add_user_op(enabled: bool, title: str, fn: Callable[[], None]):
            nonlocal total_ops
            if not enabled:
                return
            total_ops += 1
            user_steps.append((title, fn))

        # ── Root operations ───────────────────────────────────────────────

        add_root_op(
            self.ops.dnf,
            T["sec_dnf_root"],
            [
                ["bash", "-c",
                 f'log=$(mktemp /tmp/kleon_dnf.XXXXXX) || exit 1; '
                 f'trap \'rm -f "$log"\' EXIT; '
                 f'dnf upgrade -y > "$log" 2>&1; rc=$?; '
                 f'if grep -qi "nothing to do" "$log"; then '
                 f'  echo "{T["dnf_upgrade_nothing"]}"; '
                 f'elif [ $rc -eq 0 ]; then '
                 f'  echo "{T["dnf_upgrade_ok"]}"; '
                 f'else '
                 f'  echo "{T["dnf_upgrade_err"]} (rc=$rc)"; '
                 f'fi; '
                 f': > "$log"; '
                 f'dnf autoremove -y > "$log" 2>&1; rc=$?; '
                 f'if grep -qi "nothing to do" "$log"; then '
                 f'  echo "{T["dnf_remove_nothing"]}"; '
                 f'elif [ $rc -eq 0 ]; then '
                 f'  echo "{T["dnf_remove_ok"]}"; '
                 f'else '
                 f'  echo "{T["dnf_remove_err"]} (rc=$rc)"; '
                 f'fi; '
                 f'dnf clean all > /dev/null 2>&1 '
                 f'  && echo "{T["dnf_clean_ok"]}" '
                 f'  || echo "{T["dnf_clean_err"]}"; '
                 f'rm -rf /var/cache/libdnf5/* 2>/dev/null || true; '
                 f'echo "{T["dnf_libdnf5_ok"]}"; '
                 f'dnf system-upgrade clean > /dev/null 2>&1 || true; '
                 f'echo "{T["dnf_sysupgrade_ok"]}"',
                ],
            ],
        )

        if self.ops.flatpak:
            total_ops += 1
            add_root_title(T["sec_flatpak_root"])
            if shutil.which("flatpak") is None:
                root_cmds.append(["echo", T["fp_not_found"]])
            else:
                root_cmds.append(["bash", "-c",
                    f'log=$(mktemp /tmp/kleon_flatpak.XXXXXX) || exit 1; '
                    f'trap \'rm -f "$log"\' EXIT; '
                    f'flatpak --system update -y > "$log" 2>&1; rc=$?; '
                    f'if grep -qiE "nothing to do|already up.to.date" "$log"; then '
                    f'  echo "{T["fp_update_nothing"]}"; '
                    f'elif [ $rc -eq 0 ]; then '
                    f'  echo "{T["fp_update_ok"]}"; '
                    f'else '
                    f'  echo "{T["fp_update_err"]} (rc=$rc)"; '
                    f'fi; '
                    f': > "$log"; '
                    f'flatpak --system uninstall --unused -y > "$log" 2>&1; rc=$?; '
                    f'if grep -qiE "nothing to do|no unused" "$log"; then '
                    f'  echo "{T["fp_unused_nothing"]}"; '
                    f'elif [ $rc -eq 0 ]; then '
                    f'  echo "{T["fp_unused_ok"]}"; '
                    f'else '
                    f'  echo "{T["fp_unused_err"]} (rc=$rc)"; '
                    f'fi',
                ])
            root_cmds.append(["echo", done_marker])

        add_root_op(
            self.ops.kernel,
            T["sec_kernel"],
            [
                ["bash", "-c",
                 f'pkgs=$(dnf repoquery --installonly --latest-limit=-2 -q 2>/dev/null); '
                 f'if [ -z "$pkgs" ]; then '
                 f'  echo "{T["kernel_nothing"]}"; '
                 f'else '
                 f'  echo "$pkgs" | xargs -r dnf remove -y > /dev/null 2>&1 || true; '
                 f'  echo "{T["kernel_ok"]}"; '
                 f'fi',
                ],
            ],
        )

        add_root_op(
            self.ops.systemd,
            T["sec_systemd_root"],
            [
                ["bash", "-c",
                 f'before=$(du -sk /run/log/journal/ /var/log/journal/ 2>/dev/null '
                 f'  | awk "{{s+=\\$1}} END{{print s+0}}"); '
                 f'journalctl --vacuum-size=50M > /dev/null 2>&1; '
                 f'after=$(du -sk /run/log/journal/ /var/log/journal/ 2>/dev/null '
                 f'  | awk "{{s+=\\$1}} END{{print s+0}}"); '
                 f'freed=$(( (before - after) * 1024 )); '
                 f'after_h=$(numfmt --to=iec $((after * 1024)) 2>/dev/null || echo "${{after}} KiB"); '
                 f'if [ "$freed" -gt 0 ]; then '
                 f'  freed_h=$(numfmt --to=iec "$freed" 2>/dev/null || echo "$((freed / 1024)) KiB"); '
                 f'  echo "{T["journal_reduced"]} ${{after_h}} ({T["journal_freed"]} ${{freed_h}})"; '
                 f'else '
                 f'  echo "{T["journal_within"]} (${{after_h}} {T["journal_used"]})"; '
                 f'fi',
                ],
            ],
        )

        add_root_op(
            self.ops.logs,
            T["sec_logs"],
            [
                ["bash", "-c",
                 f'find /var/log -type f '
                 r'\( -name "*.gz" -o -name "*.xz" -o -name "*.zst" '
                 r'-o -name "*.[0-9]" -o -name "*.[0-9][0-9]" \) '
                 f'-delete 2>/dev/null || true; '
                 f'echo "{T["logs_ok"]}"',
                ]
            ],
        )

        add_root_op(
            self.ops.coredump,
            T["sec_coredump_root"],
            [
                ["bash", "-c",
                 f'dir=/var/lib/systemd/coredump; '
                 f'if [ -d "$dir" ]; then '
                 f'  find "$dir" -mindepth 1 -depth -delete 2>/dev/null || true; '
                 f'  echo "{T["coredump_ok"]}"; '
                 f'else echo "{T["coredump_no_dir"]}"; fi',
                ]
            ],
        )

        add_root_op(
            self.ops.packagekit,
            T["sec_packagekit_root"],
            [
                ["bash", "-c",
                 f'dir=/var/cache/PackageKit; '
                 f'if [ -d "$dir" ]; then '
                 f'  rm -rf "$dir" && echo "{T["pk_ok"]}"; '
                 f'else echo "{T["pk_missing"]}"; fi',
                ]
            ],
        )

        add_root_op(
            self.ops.tmp,
            T["sec_tmp"],
            [
                ["bash", "-c",
                 f'find /tmp -mindepth 1 -maxdepth 1 -atime +1 -exec rm -rf {{}} + 2>/dev/null || true; '
                 f'find /var/tmp -mindepth 1 -maxdepth 1 -atime +7 -exec rm -rf {{}} + 2>/dev/null || true; '
                 f'echo "{T["tmp_ok"]}"',
                ]
            ],
        )

        add_root_op(
            self.ops.abrt,
            T["sec_abrt_root"],
            [
                ["bash", "-c",
                 f'for dir in /var/spool/abrt /var/tmp/abrt; do '
                 f'  [ -d "$dir" ] || continue; '
                 f'  find "$dir" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {{}} + 2>/dev/null || true; '
                 f'done; '
                 f'echo "{T["abrt_root_ok"]}"',
                ]
            ],
        )

        add_root_op(
            self.ops.bash,
            T["sec_bash_root"],
            [
                ["bash", "-c",
                 f'if [ -f /root/.bash_history ]; then '
                 f'  truncate -s 0 /root/.bash_history && echo "{T["bash_root_ok"]}"; '
                 f'else echo "{T["bash_root_missing"]}"; fi',
                ]
            ],
        )

        # SMART data collection, only when root operations are already requested.
        smart_dir: Path | None = None
        smart_file: Path | None = None
        if root_cmds:
            smart_dir = Path(tempfile.mkdtemp(prefix="kleon_smart_"))
            smart_file = smart_dir / "smart.txt"
            smart_file_q = shlex.quote(str(smart_file))
            root_cmds.append(
                [
                    "bash", "-c",
                    f': > {smart_file_q}; '
                    'command -v smartctl >/dev/null 2>&1 || exit 0; '
                    'for dev in /dev/sd? /dev/nvme*n1; do '
                    '  [ -e "$dev" ] || continue; '
                    '  out=$(smartctl -a "$dev" 2>/dev/null || true); '
                    '  model=$(echo "$out" | grep -E "Device Model|Model Number" | head -1 | cut -d: -f2 | xargs); '
                    '  [ -z "$model" ] && model="N/D"; '
                    '  health=$(echo "$out" | grep -E "self-assessment test result" | grep -o "PASSED\\|FAILED" | head -1); '
                    '  [ "$health" = "PASSED" ] && health="OK"; '
                    f'  [ "$health" = "FAILED" ] && health="{T["smart_health_error"]}"; '
                    '  if [ -z "$health" ]; then '
                    '    warn=$(echo "$out" | grep "Critical Warning" | awk "{print \\$NF}" | head -1); '
                    '    if [ -n "$warn" ]; then '
                    f'      [ "$warn" = "0x00" ] && health="OK" || health="{T["smart_health_warning"]}"; '
                    '    fi; '
                    '  fi; '
                    '  [ -z "$health" ] && health="N/D"; '
                    '  temp=$(echo "$out" | grep -i "Temperature" | head -1 | awk "{print \\$(NF-1)}"); '
                    '  life=$(echo "$out" | grep -i "Percentage Used" | awk "{print \\$NF}" | head -1); '
                    '  uptime=$(echo "$out" | grep -i "Power_On_Hours" | awk "{print \\$NF}" | head -1); '
                    '  { '
                    '    echo "$dev  $model"; '
                    f'    echo "  {T["smart_status"]} *** $health ***"; '
                    '    [ -n "$temp" ] && echo "  Temp.  : ${temp}°C"; '
                    '    if [ -n "$life" ]; then '
                    f'      echo "  {T["smart_usage"]} ${{life}}"; '
                    '    elif [ -n "$uptime" ]; then '
                    '      echo "  Uptime : ${uptime}h"; '
                    '    fi; '
                    '    echo ""; '
                    f'  }} >> {smart_file_q}; '
                    'done; '
                    'exit 0',
                ]
            )

        # ── User operations ───────────────────────────────────────────────

        add_user_op(self.ops.abrt, T["sec_abrt_user"], lambda: self.op_abrt(self._home))
        add_user_op(self.ops.bash, T["sec_bash_user"], lambda: self.op_bash(self._home))
        add_user_op(self.ops.cache, T["sec_cache_user"], lambda: self.op_cache(self._home))
        add_user_op(self.ops.recent, T["sec_recent_user"], lambda: self.op_recent(self._home))
        add_user_op(self.ops.browser, T["sec_browser_user"],
                    lambda: self.op_browser(self._user, self._home, self.ops.passwords))

        if not root_cmds and not user_steps:
            self._log(T["no_ops"])
            self.progress.emit(0)
            self.running.emit(False)
            self.finished.emit()
            return

        if root_cmds:
            rc = run_root_block(root_cmds, log_and_track, self._cancel)
            if rc != 0:
                self._cancel.set()
                self._log(T["interrupted"])

        for title, fn in user_steps:
            if self._cancel.is_set():
                break
            self._log("")
            self._log(f"━━━━━ {title}")
            emit_initial_progress_once()
            self.current_op.emit(title)
            self._cancel.wait(random.uniform(0.2, 0.9))
            try:
                fn()
            except Exception as e:
                self._log(f"{T['op_error']} {title}: {e}")
            advance()

        # ── Final summary ─────────────────────────────────────────────────

        if not self._cancel.is_set():
            self.progress.emit(100)
            self._log("")
            self._log("")
            for line in log_banner(T["summary_title"]):
                self._log(line)

            self._log("")

            avail, size = df_avail_size_root()
            self._log(f"{T['summary_avail']} {fmt_bytes_1dp(avail)} {T['summary_avail_unit']} {fmt_bytes_1dp(size)}")

            try:
                subprocess.run(["sync"], check=False)
            except Exception:
                pass

            root_after = df_used_bytes_root()
            freed = root_before - root_after
            if freed > 0:
                self._log(f"{T['summary_freed_label']} {fmt_bytes_1dp(freed)}")
            else:
                self._log(T["summary_freed_none"])

            cache_dir = self._home / ".cache"
            if cache_dir.is_dir():
                try:
                    total_bytes = sum(
                        p.stat().st_size
                        for p in cache_dir.rglob("*")
                        if p.is_file()
                    )
                    self._log(f"{T['summary_cache']} {fmt_bytes_1dp(total_bytes)}")
                except Exception:
                    pass

            self._log("")
            self._log(T["summary_smart"])
            if smart_file is not None and smart_file.exists():
                has_smart_data = False
                try:
                    for line in smart_file.read_text().splitlines():
                        if line.strip():
                            has_smart_data = True
                            self._log(f"  {line.strip()}")
                    if not has_smart_data:
                        self._log(T["summary_no_data"])
                except Exception:
                    self._log(T["summary_no_data"])
                try:
                    smart_file.unlink()
                except Exception:
                    pass
                try:
                    if smart_dir is not None:
                        smart_dir.rmdir()
                except Exception:
                    pass
            else:
                self._log(T["summary_no_data"])
                try:
                    if smart_dir is not None:
                        shutil.rmtree(smart_dir, ignore_errors=True)
                except Exception:
                    pass

        if self._cancel.is_set() and smart_dir is not None:
            try:
                shutil.rmtree(smart_dir, ignore_errors=True)
            except Exception:
                pass

        self.running.emit(False)
        self.finished.emit()

    # ── User operation methods ────────────────────────────────────────────────

    def op_cache(self, home: Path):
        """Clear the user cache (~/.cache), preserving ksycoca6."""
        cache_dir = home / ".cache"
        if not cache_dir.is_dir():
            self._log(f"{T['cache_no_dir']}: {cache_dir}")
            return
        removed = 0
        try:
            for child in cache_dir.iterdir():
                # Preserve the KDE cache database (required for Plasma to function)
                if child.name.startswith("ksycoca6"):
                    continue
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                    removed += 1
                except Exception:
                    pass
        except Exception as e:
            self._log(f"{T['cache_read_err']}: {e}")
            return
        if removed > 0:
            self._log(f"✔ {removed} {T['cache_items_removed']}")
        else:
            self._log(T["cache_empty"])

        pycache_count = 0
        for p in home.rglob("__pycache__"):
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                    pycache_count += 1
            except Exception:
                pass
        if pycache_count > 0:
            self._log(f"✔ {pycache_count} {T['cache_pycache']}")

    def op_abrt(self, home: Path):
        """Remove ABRT user crash reports."""
        abrt_dir = home / ".local/share/abrt"
        if not abrt_dir.is_dir():
            self._log(T["abrt_user_nothing"])
            return
        try:
            n = sum(1 for p in abrt_dir.iterdir() if p.is_dir())
            shutil.rmtree(abrt_dir, ignore_errors=True)
            self._log(f"{T['abrt_user_ok']}: {n}")
        except Exception as e:
            self._log(f"{T['err_generic']}: {e}")

    def op_bash(self, home: Path):
        """Clear the user's Bash history."""
        user_hist = home / ".bash_history"
        if not user_hist.exists():
            self._log(T["bash_user_missing"])
            return
        try:
            user_hist.write_text("")
            try:
                os.chmod(user_hist, 0o600)
            except Exception:
                pass
            self._log(T["bash_user_ok"])
        except Exception:
            self._log(T["bash_user_missing"])

    def op_recent(self, home: Path):
        """Remove recent documents (KDE and GTK)."""
        removed = 0
        kde_recent = home / ".local/share/RecentDocuments"
        if kde_recent.is_dir():
            for f in kde_recent.iterdir():
                try:
                    f.unlink(missing_ok=True)
                    removed += 1
                except Exception:
                    pass
        gtk_recent = home / ".local/share/recently-used.xbel"
        if gtk_recent.exists():
            try:
                gtk_recent.unlink(missing_ok=True)
                removed += 1
            except Exception:
                pass
        self._log(T["recent_ok"] if removed > 0 else T["recent_nothing"])

    def op_browser(self, real_user: str, home: Path, delete_passwords: bool):
        """
        Clear cache, history, sessions and (optionally) saved passwords
        for Brave, Chrome and Firefox.
        """
        chromium_browsers = [
            ("Brave", home / ".config/BraveSoftware/Brave-Browser/Default", ["brave"]),
            ("Chrome", home / ".config/google-chrome/Default", ["chrome", "google-chrome"]),
        ]
        for name, profile, procs in chromium_browsers:
            if not profile.is_dir():
                continue

            if shutil.which("pgrep") is not None:
                is_open = any(
                    subprocess.run(
                        ["pgrep", "-u", real_user, p], capture_output=True
                    ).returncode == 0
                    for p in procs
                )
                if is_open:
                    self._log(f"{name} {T['browser_open']}")
                    continue

            for n in ["Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache"]:
                p = profile / n
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
            self._log(f"✔ {name}: {T['browser_cache']}")

            for n in ["History", "History-journal"]:
                p = profile / n
                if p.exists():
                    p.unlink(missing_ok=True)
            self._log(f"✔ {name}: {T['browser_history']}")

            sess = profile / "Sessions"
            if sess.is_dir():
                shutil.rmtree(sess, ignore_errors=True)
            for n in ["Current Session", "Current Tabs", "Last Session", "Last Tabs"]:
                p = profile / n
                if p.exists():
                    p.unlink(missing_ok=True)
            self._log(f"✔ {name}: {T['browser_sessions']}")

            if delete_passwords:
                for n in ["Login Data", "Login Data-journal"]:
                    p = profile / n
                    if p.exists():
                        p.unlink(missing_ok=True)
                self._log(f"✔ {name}: {T['browser_passwords']}")

        ff_open = (
            shutil.which("pgrep") is not None and
            subprocess.run(
                ["pgrep", "-u", real_user, "firefox"], capture_output=True
            ).returncode == 0
        )
        if ff_open:
            self._log(f"Firefox: {T['browser_open']}")
        else:
            ff_cache = home / ".cache/mozilla/firefox"
            if ff_cache.is_dir():
                shutil.rmtree(ff_cache, ignore_errors=True)
                self._log(f"✔ Firefox: {T['browser_cache']}")


# ── Custom frameless title bar ────────────────────────────────────────────────

class WindowTitleBar(QFrame):
    """Draggable title bar for the frameless, fixed-size window.

    On KDE/Wayland, manually calling QWidget.move() is often ignored for
    top-level windows.  Qt's native startSystemMove() asks the compositor to
    perform the drag, exactly like a real title bar.  A manual fallback is kept
    for X11/older Qt builds.
    """

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._drag_pos = None
        self._system_move_active = False
        self.setObjectName("TitleBar")
        self.setMouseTracking(True)

    def _start_system_move(self) -> bool:
        try:
            handle = self.owner.windowHandle()
            if handle is not None and handle.startSystemMove():
                self._system_move_active = True
                return True
        except Exception:
            pass
        self._system_move_active = False
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Preferred path on KDE/Wayland. If the compositor accepts it, it
            # will handle the whole drag and manual move() calls are skipped.
            if self._start_system_move():
                event.accept()
                return

            # Fallback for X11 or platforms where startSystemMove() is not
            # available/accepted.
            self._drag_pos = event.globalPosition().toPoint() - self.owner.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._system_move_active:
            event.accept()
            return

        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.owner.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._system_move_active = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # The main window is intentionally fixed-size, so double-click does not
        # maximize/resize it.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class AboutTitleBar(QFrame):
    """Draggable title strip for the custom About dialog."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._drag_pos = None
        self._system_move_active = False
        self.setObjectName("AboutTitleBar")
        self.setMouseTracking(True)

    def _start_system_move(self) -> bool:
        try:
            handle = self.owner.windowHandle()
            if handle is not None and handle.startSystemMove():
                self._system_move_active = True
                return True
        except Exception:
            pass
        self._system_move_active = False
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._start_system_move():
                event.accept()
                return
            self._drag_pos = event.globalPosition().toPoint() - self.owner.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._system_move_active:
            event.accept()
            return
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.owner.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._system_move_active = False
        super().mouseReleaseEvent(event)


class AboutDialog(QDialog):
    """Custom, app-styled About window; no native QMessageBox."""

    def __init__(self, parent: "MainWindow" | None = None):
        super().__init__(parent)
        self.parent_window = parent
        self.t = app_theme()
        self.setWindowTitle(f"{T['ui_about_title']} {APP_TITLE}")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(560)
        self.setMaximumWidth(680)
        self._build_ui()
        self._apply_styles()


    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _link_button(self, text: str, url: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setObjectName("AboutLinkButton")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolTip(tooltip)
        btn.clicked.connect(lambda: self._open_url(url))
        return btn

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("AboutCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 150 if app_uses_dark_palette() else 46))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        header = AboutTitleBar(self)
        header.setFixedHeight(76)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 14, 12)
        header_layout.setSpacing(12)

        icon = QLabel(header)
        icon.setObjectName("AboutIcon")
        icon.setFixedSize(50, 50)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = app_header_icon_pixmap(44)
        if not pix.isNull():
            icon.setPixmap(pix)
        else:
            icon.setText(APP_ICON_CHAR)
            f = icon.font()
            f.setPointSize(22)
            f.setBold(True)
            icon.setFont(f)

        title_box = QWidget(header)
        title_box.setObjectName("AboutTitleBox")
        title_layout = QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(1)

        title = QLabel(f"{APP_STUDIO} {APP_TITLE}", title_box)
        title.setObjectName("AboutTitle")
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setWeight(QFont.Weight.Bold)
        title.setFont(title_font)

        subtitle = QLabel(f"{T['about_version']} {APP_VERSION} · {T['titlebar_subtitle']}", title_box)
        subtitle.setObjectName("AboutSubtitle")
        subtitle_font = subtitle.font()
        subtitle_font.setPointSize(10)
        subtitle.setFont(subtitle_font)

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)

        close_btn = QPushButton("×", header)
        close_btn.setObjectName("AboutCloseButton")
        close_btn.setFixedSize(30, 30)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_font = close_btn.font()
        close_font.setPointSize(14)
        close_font.setBold(True)
        close_btn.setFont(close_font)
        close_btn.setToolTip(T["about_close_tooltip"])
        close_btn.clicked.connect(self.accept)

        header_layout.addWidget(icon)
        header_layout.addWidget(title_box, 1)
        header_layout.addWidget(close_btn)

        body = QWidget(card)
        body.setObjectName("AboutBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 20, 22, 18)
        body_layout.setSpacing(14)

        description = QLabel(T["about_description"], body)
        description.setObjectName("AboutDescription")
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_layout.addWidget(description)

        meta = QFrame(body)
        meta.setObjectName("AboutMeta")
        meta_layout = QVBoxLayout(meta)
        meta_layout.setContentsMargins(14, 12, 14, 12)
        meta_layout.setSpacing(6)

        def add_meta(label: str, value: str) -> None:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            key = QLabel(label, meta)
            key.setObjectName("AboutMetaKey")
            key.setMinimumWidth(105)
            val = QLabel(value, meta)
            val.setObjectName("AboutMetaValue")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(key)
            row.addWidget(val, 1)
            meta_layout.addLayout(row)

        add_meta(T["about_version"], APP_VERSION)
        add_meta(T["about_author"], APP_AUTHOR)
        add_meta(T["about_project"], f"{APP_STUDIO} {APP_TITLE}")
        add_meta(T["about_license"], "MIT")
        add_meta(T["about_platform"], "Fedora Linux / KDE Plasma")
        body_layout.addWidget(meta)

        links = QHBoxLayout()
        links.setContentsMargins(0, 0, 0, 0)
        links.setSpacing(8)
        links.addWidget(self._link_button(T["about_github"], DEV_URL, T["about_github_tooltip"]))
        links.addWidget(self._link_button(T["about_license"], "https://github.com/eleora-dev/kleon/blob/main/LICENSE", T["about_license_tooltip"]))
        links.addWidget(self._link_button(T["about_bugreport"], APP_ISSUES, T["about_bugreport_tooltip"]))
        links.addStretch(1)
        body_layout.addLayout(links)

        footer = QFrame(card)
        footer.setObjectName("AboutFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 5, 18, 5)
        footer_layout.setSpacing(8)

        copyright_label = QLabel(f"© {APP_YEAR} {APP_STUDIO} · {APP_AUTHOR}", footer)
        copyright_label.setObjectName("AboutCopyright")
        ok_btn = QPushButton(T["about_ok"], footer)
        ok_btn.setObjectName("AboutOkButton")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ok_btn.setToolTip(T["about_ok_tooltip"])
        ok_btn.clicked.connect(self.accept)

        footer_layout.addWidget(copyright_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(ok_btn)

        card_layout.addWidget(header)
        card_layout.addWidget(body)
        card_layout.addWidget(footer)
        outer.addWidget(card)

    def _apply_styles(self) -> None:
        t = self.t
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(t["ctx_bg"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(t["ctx_bg"]))
        pal.setColor(QPalette.ColorRole.Text, QColor(t["text"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(t["text"]))
        self.setPalette(pal)
        self.setStyleSheet(f"""
            QFrame#AboutCard {{
                background: {t['ctx_bg']};
                color: {t['text']};
                border: 1px solid {t['ctx_border']};
                border-radius: 14px;
            }}

            QFrame#AboutTitleBar {{
                background: {t['titlebar_bg']};
                color: {t['titlebar_text']};
                border-top-left-radius: 13px;
                border-top-right-radius: 13px;
                border-bottom: 1px solid {t['titlebar_border']};
            }}

            QLabel#AboutIcon,
            QWidget#AboutTitleBox,
            QLabel#AboutTitle,
            QLabel#AboutSubtitle {{
                background: transparent;
            }}

            QLabel#AboutIcon {{
                color: {t['titlebar_text']};
            }}

            QLabel#AboutTitle {{
                color: {t['accent_text']};
            }}

            QLabel#AboutSubtitle {{
                color: {t['titlebar_subtle']};
            }}

            QPushButton#AboutCloseButton {{
                border: none;
                border-radius: 6px;
                background: transparent;
                color: {t['titlebar_text']};
                padding: 0px;
            }}

            QPushButton#AboutCloseButton:hover {{
                background: {t['titlebar_close_hover']};
                color: {t['titlebar_text']};
            }}

            QPushButton#AboutCloseButton:pressed {{
                background: {t['titlebar_close_pressed']};
                color: {t['titlebar_text']};
            }}

            QWidget#AboutBody {{
                background: {t['ctx_bg']};
                color: {t['text']};
            }}

            QWidget#AboutBody QLabel {{
                background: transparent;
            }}

            QLabel#AboutDescription {{
                color: {t['text']};
                font-size: 11pt;
                line-height: 150%;
            }}

            QFrame#AboutMeta {{
                background: {t['bg']};
                border: 1px solid {t['ctx_border']};
                border-radius: 10px;
            }}

            QLabel#AboutMetaKey {{
                color: {t['btn_color']};
                font-weight: 700;
            }}

            QLabel#AboutMetaValue {{
                color: {t['text']};
            }}

            QPushButton#AboutLinkButton {{
                border: none;
                border-radius: 6px;
                background: {t['btn_hover_bg']};
                color: {t['text']};
                padding: 6px 10px;
                font-weight: 600;
            }}

            QPushButton#AboutLinkButton:hover {{
                background: {t['accent']};
                color: {t['titlebar_text']};
            }}

            QFrame#AboutFooter {{
                background: {t['footer_bg']};
                color: {t['footer_text']};
                border-bottom-left-radius: 13px;
                border-bottom-right-radius: 13px;
            }}

            QLabel#AboutCopyright {{
                background: transparent;
                color: {t['footer_text']};
            }}

            QPushButton#AboutOkButton {{
                border: none;
                border-radius: 6px;
                background: {t['footer_button_bg']};
                color: {t['footer_text']};
                padding: 3px 15px;
                font-weight: 700;
            }}

            QPushButton#AboutOkButton:hover {{
                background: {t['footer_button_hover']};
            }}
        """)


# ── Log syntax highlighter ────────────────────────────────────────────────────

class LogHighlighter(QSyntaxHighlighter):
    """
    Adaptive log syntax highlighter for the active KDE theme.
    The accent color is read from the application palette, so it works
    correctly with Breeze Light, Breeze Dark and any other KDE color scheme.
    """

    def __init__(self, document):
        super().__init__(document)
        self._rebuild_formats()

    def _rebuild_formats(self):
        """Rebuild formats using the shared app palette."""
        t = app_theme()

        self.formats = {
            "accent": self._fmt(t["accent"], bold=True),
            "ok": self._fmt(t["log_ok"]),
            "err": self._fmt(t["log_error"]),
            "stop": self._fmt(t["log_stop"]),
            "dim": self._fmt_weight(600),
        }
        self.labels = T["hl_labels"]

    @staticmethod
    def _fmt(color: str, bold: bool = False) -> QTextCharFormat:
        """Create a text format with the given color and weight."""
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        f.setFontWeight(700 if bold else 600)
        return f

    @staticmethod
    def _fmt_weight(weight: int) -> QTextCharFormat:
        """Create a text format with only the given weight."""
        f = QTextCharFormat()
        f.setFontWeight(weight)
        return f

    def highlightBlock(self, text: str):
        """Apply highlighting to each log line."""
        # Main log banners: accent color on the whole line
        if text.startswith(("┏", "┗")):
            self.setFormat(0, len(text), self.formats["accent"])
            return

        # Section headers: accent color on the whole line
        if "━━━━━" in text:
            self.setFormat(0, len(text), self.formats["accent"])
            return

        # Summary labels: accent color on the prefix only
        for label in self.labels:
            if text.startswith(label):
                self.setFormat(0, len(label), self.formats["accent"])
                break

        # Status symbols: ✔ green, ✖ red, ⏹ orange
        for i, ch in enumerate(text):
            if ch == "✔":
                self.setFormat(i, 1, self.formats["ok"])
            elif ch == "✖":
                self.setFormat(i, 1, self.formats["err"])
            elif ch == "⏹":
                self.setFormat(i, 1, self.formats["stop"])

        # Run button label in text: dimmed color
        btn_label = T["btn_run"].strip()
        idx = text.find(btn_label)
        if idx != -1:
            self.setFormat(idx, len(btn_label), self.formats["dim"])


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.MSWindowsFixedSizeDialogHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._applying_theme = False

        # Real user info computed once at startup
        self._uid, self._real_user, self._real_home = real_user_info()
        self._settings_path = app_settings_path(self._real_home)

        self._build_main_layout()
        self._setup_ui()
        self.highlighter = LogHighlighter(self.logText.document())
        self._show_welcome()

        self._worker_thread: QThread | None = None
        self._worker: Worker | None = None

        self.actionRun.triggered.connect(self.on_start)
        self.actionStop.triggered.connect(self.on_stop)
        self.actionExport.triggered.connect(self.on_export)
        self.actionInfo.triggered.connect(self.on_info)
        self.actionExit.triggered.connect(self.close)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_window_buttons()

    def changeEvent(self, event):
        super().changeEvent(event)
        self._refresh_window_buttons()
        if event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.PaletteChange):
            self._apply_app_styles()
            self._apply_window_shadow()
            self._apply_panel_shadows()
            self._setup_log_widget()

    # ── Close event ───────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._save_option_settings()
        if self._worker is not None:
            self._worker.cancel()
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait()
        super().closeEvent(event)

    # ── UI setup helpers ──────────────────────────────────────────────────────

    def _build_title_bar(self):
        """Build the custom title bar for the frameless window."""
        title_bar = WindowTitleBar(self)
        title_bar.setFixedHeight(58)

        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(8)

        icon = QToolButton(title_bar)
        icon.setObjectName("TitleBarIcon")
        icon.setFixedSize(40, 40)
        icon.setIconSize(QSize(34, 34))
        icon.setCursor(Qt.CursorShape.PointingHandCursor)
        icon.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        icon.setToolTip(T["titlebar_github_tooltip"])
        icon.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DEV_URL)))
        pix = app_header_icon_pixmap(34)
        if not pix.isNull():
            icon.setIcon(QIcon(pix))
            icon.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        else:
            icon.setText(APP_ICON_CHAR)
            icon.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            f = icon.font()
            f.setPointSize(16)
            f.setBold(True)
            icon.setFont(f)

        title_box = QWidget(title_bar)
        title_box.setObjectName("TitleBarText")
        title_layout = QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(0)

        title = QLabel(f"{APP_STUDIO} {APP_TITLE}", title_box)
        title.setObjectName("TitleBarTitle")
        title_font = title.font()
        title_font.setPointSize(12)
        title_font.setWeight(QFont.Weight.Medium)
        title.setFont(title_font)

        subtitle = QLabel(f"v{APP_VERSION} · {T['titlebar_subtitle']}", title_box)
        subtitle.setObjectName("TitleBarSubtitle")
        subtitle_font = subtitle.font()
        subtitle_font.setPointSize(9)
        subtitle.setFont(subtitle_font)

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)

        # Let clicks on title/subtitle reach WindowTitleBar, so the top strip
        # behaves like a draggable custom title bar. The icon remains clickable
        # and opens Eleòra on GitHub. Window controls keep their own events.
        for drag_passthrough in (title_box, title, subtitle):
            drag_passthrough.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.title_min_btn = self._window_button("–", T["window_minimize"], self.showMinimized)
        self.title_close_btn = self._window_button("×", T["window_close"], self.close, close=True)

        layout.addWidget(icon)
        layout.addWidget(title_box)
        layout.addStretch(1)
        layout.addWidget(self.title_min_btn)
        layout.addWidget(self.title_close_btn)

        self.titleBar = title_bar
        return title_bar

    def _window_button(self, text: str, tooltip: str, slot, *, close: bool = False) -> QPushButton:
        """Create one compact window-control button for the custom title bar."""
        btn = QPushButton(text, self)
        btn.setObjectName("CloseWindowButton" if close else "WindowButton")
        btn.setFixedSize(28, 28)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        font = btn.font()
        font.setPointSize(12 if close else 11)
        font.setBold(True)
        btn.setFont(font)
        btn.clicked.connect(slot)
        return btn

    def _refresh_window_buttons(self):
        """Keep the fixed-size shell geometry consistent."""
        # Keep the transparent gutter visible so the card shadow is not clipped.
        if hasattr(self, "outerLayout"):
            self.outerLayout.setContentsMargins(10, 10, 10, 10)

    def _apply_window_shadow(self):
        """Apply a soft outer shadow to the whole window card."""
        if not hasattr(self, "windowCard"):
            return
        is_dark = app_uses_dark_palette()
        shadow = QGraphicsDropShadowEffect(self.windowCard)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 150 if is_dark else 46))
        self.windowCard.setGraphicsEffect(shadow)

    def _apply_panel_shadows(self):
        """Apply a soft card shadow to the two main panels."""
        is_dark = app_uses_dark_palette()
        for widget in (self.opsBox, self.logBox):
            shadow = QGraphicsDropShadowEffect(widget)
            shadow.setBlurRadius(18)
            shadow.setOffset(0, 4)
            shadow.setColor(QColor(0, 0, 0, 128 if is_dark else 38))
            widget.setGraphicsEffect(shadow)

    def _apply_app_styles(self):
        """Apply the app stylesheet for the active KDE/Qt theme."""
        if self._applying_theme:
            return
        self._applying_theme = True
        t = app_theme()
        # Force the detected KDE theme on root widgets before styling children.
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(t["ctx_bg"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(t["ctx_bg"]))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(t["bg"]))
        pal.setColor(QPalette.ColorRole.Text, QColor(t["text"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(t["text"]))
        self.setPalette(pal)
        self.centralwidget.setPalette(pal)
        self.windowCard.setPalette(pal)
        self.setStyleSheet(f"""
            QMainWindow {{
                background: transparent;
                color: {t['text']};
            }}

            QWidget#centralwidget {{
                background: transparent;
                color: {t['text']};
            }}

            QFrame#windowCard {{
                background: {t['ctx_bg']};
                color: {t['text']};
                border: 1px solid {t['nav_border']};
                border-radius: 14px;
            }}

            QFrame#TitleBar {{
                background: {t['titlebar_bg']};
                color: {t['titlebar_text']};
                border-top-left-radius: 13px;
                border-top-right-radius: 13px;
                border-bottom: 1px solid {t['titlebar_border']};
            }}

            QToolButton#TitleBarIcon {{
                border: none;
                border-radius: 8px;
                background: transparent;
                color: {t['titlebar_text']};
                font-weight: 800;
                padding: 0px;
            }}

            QToolButton#TitleBarIcon:hover {{
                background: {t['titlebar_button_hover']};
            }}

            QToolButton#TitleBarIcon:pressed {{
                background: {t['titlebar_button_pressed']};
            }}

            QWidget#TitleBarText {{
                background: transparent;
            }}

            QLabel#TitleBarTitle {{
                background: transparent;
                color: {t['accent_text']};
                font-weight: 700;
            }}

            QLabel#TitleBarSubtitle {{
                background: transparent;
                color: {t['titlebar_subtle']};
            }}

            QPushButton#WindowButton,
            QPushButton#CloseWindowButton {{
                border: none;
                border-radius: 6px;
                background: transparent;
                color: {t['titlebar_text']};
                padding: 0px;
            }}

            QPushButton#WindowButton:hover {{
                background: {t['titlebar_button_hover']};
                color: {t['titlebar_text']};
            }}

            QPushButton#WindowButton:pressed {{
                background: {t['titlebar_button_pressed']};
                color: {t['titlebar_text']};
            }}

            QPushButton#CloseWindowButton:hover {{
                background: {t['titlebar_close_hover']};
                color: {t['titlebar_text']};
            }}

            QPushButton#CloseWindowButton:pressed {{
                background: {t['titlebar_close_pressed']};
                color: {t['titlebar_text']};
            }}

            QPushButton#CloseWindowButton:disabled {{
                color: {t['titlebar_disabled']};
                background: transparent;
            }}

            QWidget#contentWidget {{
                background: {t['bg']};
                color: {t['text']};
            }}

            QFrame#bodyArea {{
                background: {t['bg']};
                border: none;
            }}

            QGroupBox {{
                background: {t['ctx_bg']};
                color: {t['text']};
                border: 1px solid {t['ctx_border']};
                border-radius: 12px;
                margin-top: 16px;
                padding-top: 18px;
                font-weight: 700;
            }}

            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                padding: 0 7px;
                color: {t['text']};
                background: {t['ctx_bg']};
            }}

            QLabel#sectionLabel {{
                color: {t['accent']};
                font-size: 11px;
                font-weight: 800;
                padding: 8px 0 2px 0;
            }}

            QCheckBox {{
                color: {t['btn_color']};
                spacing: 8px;
                padding: 5px 7px;
                border: none;
                border-radius: 6px;
                background: transparent;
            }}

            QCheckBox::indicator {{
                width: 12px;
                height: 12px;
                border: 2px solid {t['btn_color']};
                border-radius: 4px;
                background: {t['ctx_bg']};
            }}

            QCheckBox::indicator:hover {{
                border-color: {t['accent']};
            }}

            QCheckBox::indicator:checked {{
                background: {t['accent']};
                border-color: {t['accent']};
            }}

            QCheckBox::indicator:disabled {{
                border-color: {t['disabled']};
                background: transparent;
            }}

            QCheckBox:hover {{
                background: {t['btn_hover_bg']};
                color: {t['accent']};
            }}

            QCheckBox:disabled {{
                color: {t['disabled']};
                background: transparent;
            }}

            QFrame#mainToolBar {{
                background: {t['nav_bg']};
                border: none;
                border-bottom: 1px solid {t['nav_border']};
                padding: 7px 10px;
                spacing: 5px;
            }}

            QFrame#mainToolBar QToolButton {{
                color: {t['btn_color']};
                background: transparent;
                border: none;
                border-radius: 6px;
                padding: 5px 9px;
            }}

            QFrame#mainToolBar QToolButton:hover {{
                background: {t['btn_hover_bg']};
                color: {t['accent']};
            }}

            QFrame#mainToolBar QToolButton:pressed {{
                background: {t['accent']};
                color: {t['accent_text']};
            }}

            QFrame#mainToolBar QToolButton:disabled {{
                color: {t['disabled']};
                background: transparent;
            }}

            QProgressBar {{
                min-height: 17px;
                max-height: 17px;
                border: 1px solid {t['ctx_border']};
                border-radius: 8px;
                background: {t['ctx_bg']};
                color: {t['text']};
                text-align: center;
                font-size: 10px;
                font-weight: 700;
            }}

            QProgressBar::chunk {{
                border-radius: 7px;
                background: {t['accent']};
            }}

            QFrame#statusFooter {{
                background: {t['footer_bg']};
                color: {t['footer_text']};
                border-bottom-left-radius: 13px;
                border-bottom-right-radius: 13px;
                border: none;
            }}

            QFrame#statusFooter QLabel {{
                background: transparent;
                color: {t['footer_text']};
            }}

            QMenu {{
                background: {t['ctx_bg']};
                color: {t['ctx_text']};
                border: 1px solid {t['ctx_border']};
                border-radius: 8px;
                padding: 4px 0px;
            }}

            QMenu::item {{
                padding: 7px 18px 7px 14px;
            }}

            QMenu::item:selected {{
                background: {t['accent']};
                color: {t['accent_text']};
            }}

            QMenu::separator {{
                height: 1px;
                background: {t['ctx_separator']};
                margin: 4px 0px;
            }}
        """)
        self._applying_theme = False

    def _build_main_layout(self):
        """
        Build the main window programmatically.
        """
        self.setObjectName("Kleon")
        # Fixed-size window: no edge resize and no maximize control.
        self.setFixedSize(1040, 760)

        self.centralwidget = QWidget(self)
        self.centralwidget.setObjectName("centralwidget")
        self.centralwidget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCentralWidget(self.centralwidget)

        # Rounded card on a transparent gutter so the shadow remains visible.
        self.outerLayout = QVBoxLayout(self.centralwidget)
        self.outerLayout.setContentsMargins(10, 10, 10, 10)
        self.outerLayout.setSpacing(0)

        self.windowCard = QFrame(self.centralwidget)
        self.windowCard.setObjectName("windowCard")
        self.windowCard.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.outerLayout.addWidget(self.windowCard)

        self.windowRootLayout = QVBoxLayout(self.windowCard)
        self.windowRootLayout.setContentsMargins(0, 0, 0, 0)
        self.windowRootLayout.setSpacing(0)
        self.windowRootLayout.addWidget(self._build_title_bar())

        self.contentWidget = QWidget(self.windowCard)
        self.contentWidget.setObjectName("contentWidget")
        root_layout = QHBoxLayout(self.contentWidget)
        root_layout.setContentsMargins(18, 18, 18, 16)
        root_layout.setSpacing(14)

        # ── Left panel: maintenance operations ────────────────────────────
        self.opsBox = QGroupBox(self.contentWidget)
        self.opsBox.setObjectName("opsBox")
        self.opsBox.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.opsBox.setMinimumWidth(235)
        self.opsBox.setMaximumWidth(285)
        self.opsBox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        self.opsBox.setFont(title_font)

        ops_layout = QVBoxLayout(self.opsBox)
        ops_layout.setContentsMargins(18, 24, 18, 18)
        ops_layout.setSpacing(4)

        cb_font = QFont()
        cb_font.setPointSize(10)

        def make_check(name: str, text: str, checked: bool = True, italic: bool = False) -> QCheckBox:
            cb = QCheckBox(text, self.opsBox)
            cb.setObjectName(name)
            f = QFont(cb_font)
            f.setItalic(italic)
            cb.setFont(f)
            cb.setMinimumHeight(cb.fontMetrics().height() + 10)
            cb.setChecked(checked)
            cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            return cb

        def add_section(title: str):
            label = QLabel(title, self.opsBox)
            label.setObjectName("sectionLabel")
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            ops_layout.addSpacing(8)
            ops_layout.addWidget(label)

        self.dnfOpt = make_check("dnfOpt", T["cb_dnf"])
        self.flatpakOpt = make_check("flatpakOpt", T["cb_flatpak"])
        self.kernelOpt = make_check("kernelOpt", T["cb_kernel"])
        self.systemdOpt = make_check("systemdOpt", T["cb_systemd"])
        self.logsOpt = make_check("logsOpt", T["cb_logs"])
        self.coredumpOpt = make_check("coredumpOpt", T["cb_coredump"])
        self.packagekitOpt = make_check("packagekitOpt", T["cb_packagekit"])
        self.tmpOpt = make_check("tmpOpt", T["cb_tmp"])
        self.abrtOpt = make_check("abrtOpt", T["cb_abrt"])
        self.bashOpt = make_check("bashOpt", T["cb_bash"])
        self.cacheOpt = make_check("cacheOpt", T["cb_cache"])
        self.recentOpt = make_check("recentOpt", T["cb_recent"])
        self.browserOpt = make_check("browserOpt", T["cb_browser"])
        self.passwordOpt = make_check("passwordOpt", T["cb_passwords"], checked=False, italic=True)

        add_section(T["ui_sec_system"])
        for cb in [
            self.dnfOpt, self.flatpakOpt, self.kernelOpt, self.systemdOpt,
            self.logsOpt, self.coredumpOpt, self.packagekitOpt, self.tmpOpt,
        ]:
            ops_layout.addWidget(cb)

        add_section(T["ui_sec_user"])
        for cb in [self.abrtOpt, self.bashOpt, self.cacheOpt, self.recentOpt]:
            ops_layout.addWidget(cb)

        add_section(T["ui_sec_browser"])
        ops_layout.addWidget(self.browserOpt)

        password_row = QWidget(self.opsBox)
        password_row.setObjectName("passwordRow")
        password_layout = QHBoxLayout(password_row)
        password_layout.setContentsMargins(24, 0, 0, 0)
        password_layout.setSpacing(0)
        password_layout.addWidget(self.passwordOpt)
        ops_layout.addWidget(password_row)
        ops_layout.addStretch(1)

        # ── Right panel: log output ───────────────────────────────────────
        self.logBox = QGroupBox(self.contentWidget)
        self.logBox.setObjectName("logBox")
        self.logBox.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.logBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.logBox.setFont(title_font)

        log_layout = QVBoxLayout(self.logBox)
        log_layout.setContentsMargins(16, 24, 16, 16)
        log_layout.setSpacing(0)

        self.logText = QPlainTextEdit(self.logBox)
        self.logText.setObjectName("logText")
        self.logText.setReadOnly(True)
        self.logText.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.logText.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_layout.addWidget(self.logText)

        # Created before the toolbar, then re-parented into its center area.
        self.progressBar = QProgressBar(self.logBox)
        self.progressBar.setObjectName("progressBar")
        self.progressBar.hide()

        root_layout.addWidget(self.opsBox)
        root_layout.addWidget(self.logBox, 1)
        self.windowRootLayout.addWidget(self.contentWidget, 1)

    def _toolbar_icon(self, name: str) -> QIcon:
        """Return the theme icon, tinting it like toolbar text in dark mode."""
        icon = QIcon.fromTheme(name)
        if not app_uses_dark_palette():
            return icon

        pixmap = icon.pixmap(QSize(20, 20))
        if pixmap.isNull():
            return icon

        tinted = QPixmap(pixmap.size())
        tinted.setDevicePixelRatio(pixmap.devicePixelRatio())
        tinted.fill(Qt.GlobalColor.transparent)

        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(app_theme()["btn_color"]))
        painter.end()

        return QIcon(tinted)

    def _build_toolbar(self):
        """Build the in-window action bar under the custom title bar."""
        tb = QFrame(self.windowCard)
        tb.setObjectName("mainToolBar")
        tb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(tb)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(5)
        self.mainToolBar = tb

        self.actionRun = QAction(
            self._toolbar_icon("media-playback-start"),
            T["btn_run"].strip(), self
        )
        self.actionRun.setShortcut(QKeySequence("F5"))
        self.actionRun.setToolTip(f"{T['btn_run'].strip()}  (F5)")

        self.actionStop = QAction(
            self._toolbar_icon("process-stop"),
            T["btn_stop_tip"], self
        )
        self.actionStop.setShortcut(QKeySequence("Escape"))
        self.actionStop.setToolTip(T["btn_stop_tip"])

        self.actionExport = QAction(
            self._toolbar_icon("document-save"),
            T["btn_export_tip"], self
        )
        self.actionExport.setShortcut(QKeySequence.StandardKey.Save)
        self.actionExport.setToolTip(T["btn_export_tip"])

        self.actionInfo = QAction(
            self._toolbar_icon("help-about"),
            T["btn_info_tip"], self
        )
        self.actionInfo.setShortcut(QKeySequence("F1"))
        self.actionInfo.setToolTip(T["btn_info_tip"])

        self.actionExit = QAction(
            self._toolbar_icon("application-exit"),
            T["btn_exit_tip"], self
        )
        self.actionExit.setShortcut(QKeySequence.StandardKey.Quit)
        self.actionExit.setToolTip(T["btn_exit_tip"])

        for action in (self.actionRun, self.actionStop, self.actionExport, self.actionInfo, self.actionExit):
            self.addAction(action)

        def add_action_button(action: QAction) -> QToolButton:
            btn = QToolButton(tb)
            btn.setDefaultAction(action)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setIconSize(QSize(20, 20))
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(btn)
            return btn

        self.runToolButton = add_action_button(self.actionRun)
        self.stopToolButton = add_action_button(self.actionStop)
        self.exportToolButton = add_action_button(self.actionExport)

        # Center widget: alternates between empty spacer and progress bar.
        self.toolbarCenter = QStackedWidget(tb)
        self.toolbarCenter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbarCenter.setMinimumWidth(1)

        spacer_page = QWidget(self.toolbarCenter)
        spacer_page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        progress_page = QWidget(self.toolbarCenter)
        progress_page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        prog_layout = QHBoxLayout(progress_page)
        prog_layout.setContentsMargins(16, 0, 16, 0)
        prog_layout.setSpacing(0)

        self.progressBar.setParent(progress_page)
        self.progressBar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.progressBar.setTextVisible(True)
        prog_layout.addWidget(self.progressBar)

        self.toolbarCenter.addWidget(spacer_page)  # index 0: spacer
        self.toolbarCenter.addWidget(progress_page)  # index 1: progress bar
        self.toolbarCenter.setCurrentIndex(0)

        layout.addWidget(self.toolbarCenter, 1)
        self.infoToolButton = add_action_button(self.actionInfo)
        self.exitToolButton = add_action_button(self.actionExit)

        self.windowRootLayout.insertWidget(1, tb)

    def _set_toolbar_progress_visible(self, visible: bool):
        """Show or hide the progress bar in the toolbar."""
        self.progressBar.setVisible(visible)
        self.toolbarCenter.setCurrentIndex(1 if visible else 0)

    def _pick_monospace_font(self) -> QFont:
        """Return the best available monospace font with progressive fallback."""
        preferred = ["Hack", "JetBrains Mono", "Fira Code",
                     "Noto Sans Mono", "DejaVu Sans Mono", "Monospace"]
        for family in preferred:
            if family in QFontDatabase.families():
                f = QFont(family)
                f.setPointSize(10)
                return f
        f = QFont()
        f.setFixedPitch(True)
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(10)
        return f

    def _setup_log_widget(self):
        """
        Style the log area with the active KDE palette and Kleon accent.
        """
        t = app_theme()
        palette = self.palette()
        sel_bg = palette.color(QPalette.ColorRole.Highlight).name()
        sel_fg = palette.color(QPalette.ColorRole.HighlightedText).name()

        font = self._pick_monospace_font()
        self.logText.setFont(font)
        self.logText.setStyleSheet(f"""
            QPlainTextEdit#logText {{
                background-color: {t['log_bg']};
                color: {t['log_text']};
                selection-background-color: {sel_bg};
                selection-color: {sel_fg};
                border: 1px solid {t['ctx_border']};
                border-radius: 10px;
                padding: 10px 12px;
            }}
        """)

    def _option_checkboxes_by_key(self) -> dict[str, QCheckBox]:
        """Return option checkboxes keyed like the SelectedOps fields."""
        return {
            "dnf": self.dnfOpt,
            "flatpak": self.flatpakOpt,
            "cache": self.cacheOpt,
            "kernel": self.kernelOpt,
            "systemd": self.systemdOpt,
            "bash": self.bashOpt,
            "browser": self.browserOpt,
            "passwords": self.passwordOpt,
            "recent": self.recentOpt,
            "logs": self.logsOpt,
            "coredump": self.coredumpOpt,
            "packagekit": self.packagekitOpt,
            "tmp": self.tmpOpt,
            "abrt": self.abrtOpt,
        }

    def _current_option_settings(self) -> dict[str, bool]:
        """Return the current checkbox state as a serializable dict."""
        return {
            key: checkbox.isChecked()
            for key, checkbox in self._option_checkboxes_by_key().items()
        }

    def _load_option_settings(self) -> None:
        """Restore checkbox states from the previous run, if available."""
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            return

        options = data.get("options") if isinstance(data, dict) else None
        if not isinstance(options, dict):
            return

        for key, checkbox in self._option_checkboxes_by_key().items():
            value = options.get(key)
            if isinstance(value, bool):
                checkbox.setChecked(value)

        self.passwordOpt.setEnabled(self.browserOpt.isChecked())

    def _save_option_settings(self, *_args) -> None:
        """Persist checkbox states under ~/.config/eleora-kleon/settings.json."""
        if not hasattr(self, "_settings_path"):
            return

        payload = {
            "version": SETTINGS_VERSION,
            "options": self._current_option_settings(),
        }

        try:
            write_text_for_user(
                self._settings_path,
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                self._uid,
            )
        except Exception:
            # Settings persistence is convenient, not critical: never block Kleon
            # from starting or closing because the config file is unavailable.
            pass


    def _runnable_checkboxes(self) -> tuple[QCheckBox, ...]:
        return (
            self.dnfOpt, self.flatpakOpt, self.cacheOpt, self.kernelOpt,
            self.systemdOpt, self.bashOpt, self.browserOpt, self.recentOpt,
            self.abrtOpt, self.logsOpt, self.coredumpOpt, self.packagekitOpt,
            self.tmpOpt,
        )

    def _all_option_checkboxes(self) -> tuple[QCheckBox, ...]:
        return (*self._runnable_checkboxes(), self.passwordOpt)

    def _setup_ui(self):
        """Initialize all widgets of the main window."""
        self.setWindowTitle(f"{APP_STUDIO} {APP_TITLE}")
        self.setWindowIcon(app_window_icon())

        self.opsBox.setTitle(T["grp_actions"])
        self.logBox.setTitle(T["grp_log"])

        self.dnfOpt.setText(T["cb_dnf"])
        self.flatpakOpt.setText(T["cb_flatpak"])
        self.kernelOpt.setText(T["cb_kernel"])
        self.systemdOpt.setText(T["cb_systemd"])
        self.logsOpt.setText(T["cb_logs"])
        self.coredumpOpt.setText(T["cb_coredump"])
        self.packagekitOpt.setText(T["cb_packagekit"])
        self.tmpOpt.setText(T["cb_tmp"])
        self.abrtOpt.setText(T["cb_abrt"])
        self.bashOpt.setText(T["cb_bash"])
        self.cacheOpt.setText(T["cb_cache"])
        self.browserOpt.setText(T["cb_browser"])
        self.passwordOpt.setText(T["cb_passwords"])
        self.recentOpt.setText(T["cb_recent"])

        self.passwordOpt.setEnabled(self.browserOpt.isChecked())
        self.browserOpt.toggled.connect(self.passwordOpt.setEnabled)

        self._load_option_settings()

        for cb in self._runnable_checkboxes():
            cb.toggled.connect(self._update_run_enabled)
        for cb in self._all_option_checkboxes():
            cb.toggled.connect(self._save_option_settings)

        self._build_toolbar()
        self._build_status_footer()
        self._apply_app_styles()
        self._apply_window_shadow()
        self._apply_panel_shadows()
        self._setup_log_widget()
        self._update_run_enabled()

        self.actionStop.setEnabled(False)
        self.actionExport.setEnabled(False)
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self._set_toolbar_progress_visible(False)

    def _build_status_footer(self):
        """Build the custom status footer."""
        footer = QFrame(self.windowCard)
        footer.setObjectName("statusFooter")
        footer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(8)

        self._status_label = QLabel(T["status_ready"], footer)
        self._status_label.setObjectName("statusLabel")
        footer_font = self._status_label.font()
        footer_font.setPointSize(10)
        self._status_label.setFont(footer_font)

        layout.addWidget(self._status_label, 1)
        self.statusFooter = footer
        self.windowRootLayout.addWidget(footer)

    def _set_status(self, message: str):
        """Update the status footer label."""
        self._status_label.setText(message)

    def _update_run_enabled(self):
        self.actionRun.setEnabled(any(cb.isChecked() for cb in self._runnable_checkboxes()))

    # ── Welcome screen ────────────────────────────────────────────────────────

    def _show_welcome(self):
        """Populate the log with system information at startup."""
        try:
            rel = platform.freedesktop_os_release()
            os_name = f"{rel.get('NAME', 'Linux')} {rel.get('VERSION', '')}"
        except Exception:
            os_name = platform.system()

        user = self._real_user
        home = self._real_home
        lines = [
            *log_banner(f"{APP_STUDIO} {APP_TITLE.upper()} v{APP_VERSION}"),
            "",
            f"{T['welcome_os']} {os_name}",
            f"{T['welcome_kernel']} {platform.release()}",
            f"{T['welcome_host']} {socket.gethostname()}",
            "",
            f"{T['label_user']} {user}",
            f"{T['label_home']} {home}",
            "",
            T["welcome_prompt"],
            "",
        ]
        self.logText.setPlainText("\n".join(lines))

    # ── Slots ─────────────────────────────────────────────────────────────────

    def append_log(self, s: str):
        """Append a line to the log (called from the worker via signal)."""
        self.logText.appendPlainText(s.rstrip("\n"))

    def on_progress_changed(self, value: int):
        """Update the progress bar (called from the worker via signal)."""
        self.progressBar.setValue(value)

    def set_running(self, running: bool):
        """Lock the UI while the worker is running."""
        if not running:
            # The UI is unlocked by _on_thread_finished(), after QThread has
            # actually stopped. This avoids enabling Run while the previous
            # worker/thread is still being torn down.
            return

        self.actionRun.setEnabled(False)
        self.actionStop.setEnabled(True)
        self.actionExport.setEnabled(False)
        self.actionInfo.setEnabled(False)
        self.actionExit.setEnabled(False)
        if hasattr(self, "title_close_btn"):
            self.title_close_btn.setEnabled(False)
        self._set_toolbar_progress_visible(True)

        for cb in self._all_option_checkboxes():
            cb.setEnabled(False)

        self.setWindowTitle(f"{APP_STUDIO} {APP_TITLE} — {T['status_running']}")
        self._set_status(T["status_running"])

    def on_info(self):
        """Show the custom About dialog."""
        AboutDialog(self).exec()

    def on_export(self):
        """Export the log contents to a text file."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            T["ui_export_title"],
            "kleon.log",
            T["ui_export_filter"],
        )
        if not path:
            return
        try:
            Path(path).write_text(self.logText.toPlainText(), encoding="utf-8")
            QMessageBox.information(self, T["ui_export_box_title"], T["ui_export_ok"])
        except Exception as e:
            QMessageBox.critical(self, T["ui_export_box_title"], f"{T['ui_export_err']}{e}")

    def on_start(self):
        """Collect selected options and start the worker in a dedicated thread."""

        if self._worker_thread is not None:
            return

        self.actionRun.setEnabled(False)
        self.actionStop.setEnabled(True)

        ops = SelectedOps(
            dnf=self.dnfOpt.isChecked(),
            flatpak=self.flatpakOpt.isChecked(),
            cache=self.cacheOpt.isChecked(),
            kernel=self.kernelOpt.isChecked(),
            systemd=self.systemdOpt.isChecked(),
            bash=self.bashOpt.isChecked(),
            browser=self.browserOpt.isChecked(),
            passwords=self.passwordOpt.isChecked(),
            recent=self.recentOpt.isChecked(),
            logs=self.logsOpt.isChecked(),
            coredump=self.coredumpOpt.isChecked(),
            packagekit=self.packagekitOpt.isChecked(),
            tmp=self.tmpOpt.isChecked(),
            abrt=self.abrtOpt.isChecked(),
        )

        self._save_option_settings()

        self.logText.clear()
        self.progressBar.setValue(0)
        self._set_toolbar_progress_visible(True)

        self._worker = Worker(ops, self._real_user, str(self._real_home))
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.log.connect(self.append_log, Qt.ConnectionType.QueuedConnection)
        self._worker.progress.connect(self.on_progress_changed, Qt.ConnectionType.QueuedConnection)
        self._worker.running.connect(self.set_running, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._on_worker_done, Qt.ConnectionType.QueuedConnection)
        self._worker.current_op.connect(self._on_current_op, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._worker_thread.quit, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        self._worker_thread.finished.connect(self._on_thread_finished, Qt.ConnectionType.QueuedConnection)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater, Qt.ConnectionType.QueuedConnection)

        self._worker_thread.start()

    def _on_worker_done(self):
        """Update the status bar when the worker finishes."""
        if self._worker and self._worker._cancel.is_set():
            self._set_status(T["status_stopped"])
        else:
            self._set_status(T["status_done"])

    def _on_thread_finished(self):
        """Unlock the UI and clear references after the QThread has really finished."""
        final_status = self._status_label.text()

        self._worker = None
        self._worker_thread = None

        self.actionStop.setEnabled(False)
        self.actionExport.setEnabled(True)
        self.actionInfo.setEnabled(True)
        self.actionExit.setEnabled(True)
        if hasattr(self, "title_close_btn"):
            self.title_close_btn.setEnabled(True)
        self._set_toolbar_progress_visible(False)

        for cb in self._all_option_checkboxes():
            cb.setEnabled(True)

        self.passwordOpt.setEnabled(self.browserOpt.isChecked())
        self.setWindowTitle(f"{APP_STUDIO} {APP_TITLE}")
        self._update_run_enabled()
        self._set_status(final_status)

    def _on_current_op(self, op: str):
        """Update the status bar with the current operation name."""
        self._set_status(f"{T['status_running']}  {op}")

    def on_stop(self):
        """Send the cancellation signal to the worker."""
        if self._worker:
            self._worker.cancel()
            self.append_log(T["stop_requested"])
            self._set_status(T["status_stopped"])


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication([])
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_AUTHOR)
    app.setWindowIcon(app_window_icon())

    w = MainWindow()
    w.show()
    app.exec()


if __name__ == "__main__":
    main()
