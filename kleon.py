#!/usr/bin/env python3
"""
 *  Eleòra Kleon
 *  System maintenance utility for Fedora Linux/KDE.
 *
 *  https://github.com/eleora-dev/kleon
 *  License: MIT
"""

import os, pwd, shutil, subprocess, threading, shlex, platform, socket, random, signal, selectors, tempfile

try:
    import resources
except ImportError:
    resources = None
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from PySide6.QtCore import (
    QObject, QThread, Signal, Qt, QSize, QByteArray
)
from PySide6.QtGui import (
    QIcon, QSyntaxHighlighter, QTextCharFormat, QColor,
    QPalette, QAction, QKeySequence, QFont, QFontDatabase
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMessageBox, QMainWindow,
    QToolBar, QWidget, QSizePolicy, QStackedWidget,
    QHBoxLayout, QVBoxLayout, QStatusBar, QLabel,
    QGroupBox, QCheckBox, QPlainTextEdit, QProgressBar
)

from locales import T

APP_TITLE   = "Kleon"
APP_VERSION = "1.0"
APP_YEAR    = "2026"
APP_AUTHOR  = "Gerardo Perilli"
APP_STUDIO  = "Eleòra"
APP_URL     = "https://eleora.github.io/kleon/"
APP_SOURCE  = "https://github.com/eleora-dev/kleon"
APP_ISSUES  = "https://github.com/eleora-dev/kleon/issues"


# ── Utility functions ─────────────────────────────────────────────────────────

def real_user_info() -> Tuple[int, str, str]:
    """
    Return (uid, username, home) of the real user,
    accounting for sudo and pkexec elevation.
    """
    uid = int(os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID") or os.getuid())
    pw = pwd.getpwuid(uid)
    return uid, pw.pw_name, pw.pw_dir


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


def df_avail_size_root() -> Tuple[int, int]:
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


def run_root_block(commands: List[List[str]], on_line, cancel_event) -> int:
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
    dnf:        bool
    flatpak:    bool
    cache:      bool
    kernel:     bool
    systemd:    bool
    bash:       bool
    browser:    bool
    passwords:  bool
    recent:     bool
    logs:       bool
    coredump:   bool
    packagekit: bool
    tmp:        bool
    abrt:       bool


# ── Worker: runs all operations in a separate thread ──────────────────────────

class Worker(QObject):
    log        = Signal(str)
    progress   = Signal(int)
    running    = Signal(bool)
    finished   = Signal()
    current_op = Signal(str)

    def __init__(self, ops: SelectedOps, user: str, home: str):
        super().__init__()
        self.ops     = ops
        self._user   = user
        self._home   = Path(home)
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

        self._log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━  {APP_STUDIO} {APP_TITLE.upper()} v{APP_VERSION}  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
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

        root_cmds:  List[List[str]]                      = []
        user_steps: List[Tuple[str, Callable[[], None]]] = []

        def add_root_title(title: str):
            root_cmds.append(["echo", ""])
            root_cmds.append(["echo", f"━━━━━ {title}"])
            root_cmds.append(["sleep", "0.35"])

        def add_root_op(enabled: bool, title: str, cmds: List[List[str]]):
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
            "DNF (root)",
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
            add_root_title("Flatpak (root)")
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
            "Journal systemd (root)",
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
                 f'n=$(find /var/log -type f '
                 r'\( -name "*.gz" -o -name "*.xz" -o -name "*.zst" '
                 r'-o -name "*.[0-9]" -o -name "*.[0-9][0-9]" \) '
                 f'-print -delete 2>/dev/null | wc -l) || n=0; '
                 f'echo "{T["logs_ok"]}"',
                ]
            ],
        )

        add_root_op(
            self.ops.coredump,
            "Core dump (root)",
            [
                ["bash", "-c",
                 f'dir=/var/lib/systemd/coredump; '
                 f'if [ -d "$dir" ]; then '
                 f'  n=$(find "$dir" -mindepth 1 2>/dev/null | wc -l) || n=0; '
                 f'  find "$dir" -mindepth 1 -depth -delete 2>/dev/null || true; '
                 f'  echo "{T["coredump_ok"]}"; '
                 f'else echo "{T["coredump_no_dir"]}"; fi',
                ]
            ],
        )

        add_root_op(
            self.ops.packagekit,
            "Cache PackageKit (root)",
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
                 f'n=$(find /tmp -mindepth 1 -maxdepth 1 -atime +1 2>/dev/null | wc -l) || n=0; '
                 f'find /tmp -mindepth 1 -maxdepth 1 -atime +1 -exec rm -rf {{}} + 2>/dev/null || true; '
                 f'm=$(find /var/tmp -mindepth 1 -maxdepth 1 -atime +7 2>/dev/null | wc -l) || m=0; '
                 f'find /var/tmp -mindepth 1 -maxdepth 1 -atime +7 -exec rm -rf {{}} + 2>/dev/null || true; '
                 f'echo "{T["tmp_ok"]}"',
                ]
            ],
        )

        add_root_op(
            self.ops.abrt,
            "Crash report (root)",
            [
                ["bash", "-c",
                 f'n=0; '
                 f'for dir in /var/spool/abrt /var/tmp/abrt; do '
                 f'  [ -d "$dir" ] || continue; '
                 f'  c=$(find "$dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l); '
                 f'  find "$dir" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {{}} + 2>/dev/null || true; '
                 f'  n=$((n+c)); '
                 f'done; '
                 f'echo "{T["abrt_root_ok"]}"',
                ]
            ],
        )

        add_root_op(
            self.ops.bash,
            "Bash (root)",
            [
                ["bash", "-c",
                 f'if [ -f /root/.bash_history ]; then '
                 f'  truncate -s 0 /root/.bash_history && echo "{T["bash_root_ok"]}"; '
                 f'else echo "{T["bash_root_missing"]}"; fi',
                ]
            ],
        )

        # SMART data collection, only when root operations are already requested.
        smart_dir: Optional[Path] = None
        smart_file: Optional[Path] = None
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
                    '  [ "$health" = "FAILED" ] && health="ERRORE"; '
                    '  if [ -z "$health" ]; then '
                    '    warn=$(echo "$out" | grep "Critical Warning" | awk "{print \\$NF}" | head -1); '
                    '    if [ -n "$warn" ]; then '
                    '      [ "$warn" = "0x00" ] && health="OK" || health="ATTENZIONE"; '
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

        add_user_op(self.ops.abrt,    T["sec_abrt_user"],    lambda: self.op_abrt(self._home))
        add_user_op(self.ops.bash,    T["sec_bash_user"],    lambda: self.op_bash(self._home))
        add_user_op(self.ops.cache,   T["sec_cache_user"],   lambda: self.op_cache(self._home))
        add_user_op(self.ops.recent,  T["sec_recent_user"],  lambda: self.op_recent(self._home))
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
            self._log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  {T['summary_title']}  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

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
            ("Brave",  home / ".config/BraveSoftware/Brave-Browser/Default", ["brave"]),
            ("Chrome", home / ".config/google-chrome/Default",               ["chrome", "google-chrome"]),
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

            # Passwords are only removed if the user explicitly requested it
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
        """Rebuild formats using the current palette."""
        palette = QApplication.palette()
        accent  = palette.color(QPalette.ColorRole.Highlight)

        # Semantic colors aligned with Breeze
        ok_color   = QColor("#1cdc9a")   # Breeze positive green
        err_color  = QColor("#da4453")   # Breeze red
        stop_color = QColor("#f67400")   # Breeze orange

        self.formats = {
            "accent": self._fmt(accent.name(), bold=True),
            "ok":     self._fmt(ok_color.name()),
            "err":    self._fmt(err_color.name()),
            "stop":   self._fmt(stop_color.name()),
            "dim":    self._fmt_weight(600),
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

    _SETTINGS_GEOMETRY = "mainWindow/geometry"
    _SETTINGS_STATE    = "mainWindow/windowState"

    def __init__(self):
        super().__init__()

        # Real user info computed once at startup
        self._uid, self._real_user, self._real_home = real_user_info()

        self._build_main_layout()
        self._setup_ui()
        self.highlighter = LogHighlighter(self.logText.document())
        self._show_welcome()

        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[Worker] = None

        self.actionRun.triggered.connect(self.on_start)
        self.actionStop.triggered.connect(self.on_stop)
        self.actionExport.triggered.connect(self.on_export)
        self.actionInfo.triggered.connect(self.on_info)
        self.actionExit.triggered.connect(self.close)

    # ── Close event ───────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._worker is not None:
            self._worker.cancel()
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait()
        super().closeEvent(event)

    # ── UI setup helpers ──────────────────────────────────────────────────────

    def _theme(self) -> dict[str, str]:
        """Return a small CharM-inspired palette adapted to the active KDE theme."""
        palette = self.palette()
        is_dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        if is_dark:
            return {
                "bg": "#1e1e1e",
                "panel_bg": "#252525",
                "panel_alt": "#2b2118",
                "panel_border": "#383838",
                "text": "#eeeeee",
                "muted": "#b8b8b8",
                "disabled": "#777777",
                "accent": "#ffa726",
                "accent_soft": "#332514",
                "accent_mid": "#4a3217",
                "accent_text": "#ffffff",
                "log_bg": "#202020",
                "log_text": "#f2f2f2",
                "toolbar_bg": "#241f18",
                "toolbar_border": "#3a3024",
                "status_bg": "#252525",
                "positive": "#1cdc9a",
                "danger": "#da4453",
            }
        return {
            "bg": "#ffffff",
            "panel_bg": "#fafafa",
            "panel_alt": "#fff3e0",
            "panel_border": "#e8e8e8",
            "text": "#222222",
            "muted": "#666666",
            "disabled": "#9a9a9a",
            "accent": "#ffa726",
            "accent_soft": "#fff3e0",
            "accent_mid": "#ffe0b2",
            "accent_text": "#ffffff",
            "log_bg": "#f5f5f5",
            "log_text": "#1a1a2e",
            "toolbar_bg": "#fff3e0",
            "toolbar_border": "#eadfce",
            "status_bg": "#ffffff",
            "positive": "#1cdc9a",
            "danger": "#da4453",
        }

    def _apply_app_styles(self):
        """Apply the CharM-inspired visual layer while keeping a KDE-native window."""
        t = self._theme()
        self.setStyleSheet(f"""
            QMainWindow {{
                background: {t['bg']};
                color: {t['text']};
            }}

            QWidget#centralwidget {{
                background: {t['bg']};
                color: {t['text']};
            }}

            QGroupBox {{
                background: {t['panel_bg']};
                color: {t['text']};
                border: 1px solid {t['panel_border']};
                border-radius: 13px;
                margin-top: 15px;
                padding-top: 17px;
                font-weight: 700;
            }}

            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 10px;
                color: {t['text']};
                background: {t['bg']};
            }}

            QLabel#sectionLabel {{
                color: {t['accent']};
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 0.8px;
                text-transform: uppercase;
                padding: 7px 0 2px 0;
            }}

            QCheckBox {{
                color: {t['text']};
                spacing: 8px;
                padding: 4px 7px 6px 7px;
                border-radius: 7px;
            }}

            QCheckBox:hover {{
                background: {t['accent_soft']};
            }}

            QCheckBox:disabled {{
                color: {t['disabled']};
            }}

            QToolBar#mainToolBar {{
                background: {t['toolbar_bg']};
                border: none;
                border-bottom: 1px solid {t['toolbar_border']};
                padding: 6px 8px;
                spacing: 5px;
            }}

            QToolBar#mainToolBar QToolButton {{
                color: {t['text']};
                background: transparent;
                border: none;
                border-radius: 8px;
                padding: 6px 10px;
            }}

            QToolBar#mainToolBar QToolButton:hover {{
                background: {t['accent_soft']};
            }}

            QToolBar#mainToolBar QToolButton:pressed {{
                background: {t['accent']};
                color: {t['accent_text']};
            }}

            QToolBar#mainToolBar QToolButton:disabled {{
                color: {t['disabled']};
                background: transparent;
            }}

            QProgressBar {{
                min-height: 17px;
                max-height: 17px;
                border: 1px solid {t['panel_border']};
                border-radius: 8px;
                background: {t['panel_bg']};
                color: {t['text']};
                text-align: center;
                font-size: 10px;
                font-weight: 700;
            }}

            QProgressBar::chunk {{
                border-radius: 7px;
                background: {t['accent']};
            }}

            QStatusBar {{
                background: {t['status_bg']};
                color: {t['muted']};
                border-top: 1px solid {t['panel_border']};
            }}

            QStatusBar::item {{
                border: none;
            }}
        """)

    def _build_main_layout(self):
        """
        Build the main window programmatically, without Qt Designer/Creator.
        The widget names intentionally match the old .ui-generated attributes
        so the application logic can stay almost unchanged.
        """
        self.setObjectName("Kleon")
        self.resize(1000, 650)
        self.setMinimumSize(840, 550)

        self.centralwidget = QWidget(self)
        self.centralwidget.setObjectName("centralwidget")
        self.setCentralWidget(self.centralwidget)

        root_layout = QHBoxLayout(self.centralwidget)
        root_layout.setContentsMargins(24, 24, 24, 20)
        root_layout.setSpacing(22)

        # ── Left panel: maintenance operations ────────────────────────────
        self.opsBox = QGroupBox(self.centralwidget)
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
        ops_layout.setContentsMargins(22, 28, 22, 22)
        ops_layout.setSpacing(5)

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

        self.dnfOpt        = make_check("dnfOpt",        "DNF")
        self.flatpakOpt    = make_check("flatpakOpt",    "Flatpak")
        self.kernelOpt     = make_check("kernelOpt",     "Kernel Obsoleti")
        self.systemdOpt    = make_check("systemdOpt",    "Journal systemd")
        self.logsOpt       = make_check("logsOpt",       "Log Obsoleti")
        self.coredumpOpt   = make_check("coredumpOpt",   "Core Dump")
        self.packagekitOpt = make_check("packagekitOpt", "PackageKit")
        self.tmpOpt        = make_check("tmpOpt",        "File Temporanei")
        self.abrtOpt       = make_check("abrtOpt",       "Crash Report")
        self.bashOpt       = make_check("bashOpt",       "Bash")
        self.cacheOpt      = make_check("cacheOpt",      "Cache Utente")
        self.recentOpt     = make_check("recentOpt",     "Documenti Recenti")
        self.browserOpt    = make_check("browserOpt",    "Pulizia Browser")
        self.passwordOpt   = make_check("passwordOpt",   "Elimina Password", checked=False, italic=True)

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
        self.logBox = QGroupBox(self.centralwidget)
        self.logBox.setObjectName("logBox")
        self.logBox.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.logBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.logBox.setFont(title_font)

        log_layout = QVBoxLayout(self.logBox)
        log_layout.setContentsMargins(22, 30, 22, 18)
        log_layout.setSpacing(0)

        self.logText = QPlainTextEdit(self.logBox)
        self.logText.setObjectName("logText")
        self.logText.setReadOnly(True)
        self.logText.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.logText.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_layout.addWidget(self.logText)

        # Created here for compatibility with the old .ui version; the toolbar
        # re-parents it into its center area during _build_toolbar().
        self.progressBar = QProgressBar(self.logBox)
        self.progressBar.setObjectName("progressBar")
        self.progressBar.hide()

        root_layout.addWidget(self.opsBox)
        root_layout.addWidget(self.logBox, 1)

    def _hide_legacy_buttons(self):
        """Hide legacy buttons defined in the .ui file (replaced by the toolbar)."""
        for name in ("runButton", "stopButton", "exportButton", "infoButton", "exitButton"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.hide()
                widget.setEnabled(False)

    def _build_toolbar(self):
        """Build the main toolbar with actions, elastic spacer and progress bar."""
        tb = QToolBar(self)
        tb.setObjectName("mainToolBar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(22, 22))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.mainToolBar = tb

        self.actionRun = QAction(
            QIcon.fromTheme("media-playback-start"),
            T["btn_run"].strip(), self
        )
        self.actionRun.setShortcut(QKeySequence("F5"))
        self.actionRun.setToolTip(f"{T['btn_run'].strip()}  (F5)")

        self.actionStop = QAction(
            QIcon.fromTheme("process-stop"),
            T["btn_stop_tip"], self
        )
        self.actionStop.setShortcut(QKeySequence("Escape"))
        self.actionStop.setToolTip(T["btn_stop_tip"])

        self.actionExport = QAction(
            QIcon.fromTheme("document-save"),
            T["btn_export_tip"], self
        )
        self.actionExport.setShortcut(QKeySequence.StandardKey.Save)
        self.actionExport.setToolTip(T["btn_export_tip"])

        self.actionInfo = QAction(
            QIcon.fromTheme("help-about"),
            T["btn_info_tip"], self
        )
        self.actionInfo.setShortcut(QKeySequence("F1"))
        self.actionInfo.setToolTip(T["btn_info_tip"])

        self.actionExit = QAction(
            QIcon.fromTheme("application-exit"),
            T["btn_exit_tip"], self
        )
        self.actionExit.setShortcut(QKeySequence.StandardKey.Quit)
        self.actionExit.setToolTip(T["btn_exit_tip"])

        tb.addAction(self.actionRun)
        tb.addAction(self.actionStop)
        tb.addAction(self.actionExport)

        # Center widget: alternates between empty spacer and progress bar
        self.toolbarCenter = QStackedWidget(self)
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

        self.toolbarCenter.addWidget(spacer_page)    # index 0: spacer
        self.toolbarCenter.addWidget(progress_page)  # index 1: progress bar
        self.toolbarCenter.setCurrentIndex(0)

        tb.addWidget(self.toolbarCenter)
        tb.addAction(self.actionInfo)
        tb.addAction(self.actionExit)

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
        Style the log area using the active KDE palette plus the Kleon/CharM accent.
        Works correctly with both light and dark themes.
        """
        t = self._theme()
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
                border: 1px solid {t['panel_border']};
                border-radius: 10px;
                padding: 10px 12px;
            }}
        """)

    def _setup_ui(self):
        """Initialize all widgets of the main window."""
        self.setWindowTitle(f"{APP_STUDIO} {APP_TITLE}")
        self.setWindowIcon(QIcon(":/kleon"))

        self.opsBox.setTitle(T["grp_actions"])
        self.logBox.setTitle(T["grp_log"])

        self.kernelOpt.setText(T["cb_kernel"])
        self.logsOpt.setText(T["cb_logs"])
        self.tmpOpt.setText(T["cb_tmp"])
        self.cacheOpt.setText(T["cb_cache"])
        self.browserOpt.setText(T["cb_browser"])
        self.passwordOpt.setText(T["cb_passwords"])
        self.recentOpt.setText(T["cb_recent"])

        # passwordOpt is enabled only when browserOpt is checked
        self.passwordOpt.setEnabled(self.browserOpt.isChecked())
        self.browserOpt.toggled.connect(self.passwordOpt.setEnabled)

        for cb in [
            self.dnfOpt, self.flatpakOpt, self.cacheOpt, self.kernelOpt,
            self.systemdOpt, self.bashOpt, self.browserOpt, self.recentOpt,
            self.abrtOpt, self.logsOpt, self.coredumpOpt, self.packagekitOpt,
            self.tmpOpt,
        ]:
            cb.toggled.connect(self._update_run_enabled)

        sb = QStatusBar(self)
        sb.setSizeGripEnabled(True)
        self.setStatusBar(sb)
        self._status_label = QLabel(T["status_ready"])
        sb.addWidget(self._status_label)

        self._hide_legacy_buttons()
        self._build_toolbar()
        self._apply_app_styles()
        self._setup_log_widget()
        self._update_run_enabled()

        self.actionStop.setEnabled(False)
        self.actionExport.setEnabled(False)
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self._set_toolbar_progress_visible(False)

    def _set_status(self, message: str):
        """Update the status bar text."""
        self._status_label.setText(message)

    def _update_run_enabled(self):
        self.actionRun.setEnabled(
            self.dnfOpt.isChecked() or
            self.flatpakOpt.isChecked() or
            self.cacheOpt.isChecked() or
            self.kernelOpt.isChecked() or
            self.systemdOpt.isChecked() or
            self.bashOpt.isChecked() or
            self.browserOpt.isChecked() or
            self.recentOpt.isChecked() or
            self.abrtOpt.isChecked() or
            self.logsOpt.isChecked() or
            self.coredumpOpt.isChecked() or
            self.packagekitOpt.isChecked() or
            self.tmpOpt.isChecked()
        )

    # ── Welcome screen ────────────────────────────────────────────────────────

    def _show_welcome(self):
        """Populate the log with system information at startup."""
        try:
            rel     = platform.freedesktop_os_release()
            os_name = f"{rel.get('NAME', 'Linux')} {rel.get('VERSION', '')}"
        except Exception:
            os_name = platform.system()

        user = self._real_user
        home = self._real_home
        lines = [
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  {APP_STUDIO} {APP_TITLE.upper()} v{APP_VERSION}  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
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
        self._set_toolbar_progress_visible(True)

        for cb in [
            self.dnfOpt, self.flatpakOpt, self.cacheOpt, self.kernelOpt,
            self.systemdOpt, self.bashOpt, self.browserOpt, self.recentOpt,
            self.abrtOpt, self.logsOpt, self.coredumpOpt, self.packagekitOpt,
            self.tmpOpt, self.passwordOpt,
        ]:
            cb.setEnabled(False)

        self.setWindowTitle(f"{APP_STUDIO} {APP_TITLE} — {T['status_running']}")
        self._set_status(T["status_running"])

    def on_info(self):
        """Show the About dialog."""
        palette     = self.palette()
        link_color  = palette.color(QPalette.ColorRole.Link).name()
        muted_color = palette.color(QPalette.ColorRole.PlaceholderText).name()

        QMessageBox.about(
            self,
            f"{T['ui_about_title']} {APP_TITLE}",
            f"""
            <div style="min-width: 520px;">

                <p style="font-size: 1.3em; font-weight: bold; margin: 0 0 2px 0;">
                    {APP_STUDIO} {APP_TITLE}
                </p>
                <p style="font-size: 0.9em; color: {muted_color}; margin: 0 0 12px 0;">
                    {T['about_version']} {APP_VERSION}
                </p>

                <hr style="border: none; border-top: 1px solid {muted_color}; margin: 0 0 10px 0;">

                <p style="margin: 0 0 10px 0; line-height: 1.6; font-size: 0.92em;">
                    {T['about_description']}
                </p>

                <hr style="border: none; border-top: 1px solid {muted_color}; margin: 0 0 10px 0;">

                <p style="margin: 0 0 6px 0; font-size: 0.88em; color: {muted_color};">
                    © {APP_YEAR} {APP_STUDIO} · {APP_AUTHOR}
                </p>

                <p style="margin: 0; font-size: 0.88em;">
                    <a style="color: {link_color}; text-decoration: none;"
                       href="{APP_SOURCE}">{T['about_source']}</a>
                    &nbsp;·&nbsp;
                    <a style="color: {link_color}; text-decoration: none;"
                       href="https://github.com/eleora-dev/kleon/blob/main/LICENSE">{T['about_license']}</a>
                    &nbsp;·&nbsp;
                    <a style="color: {link_color}; text-decoration: none;"
                       href="{APP_ISSUES}">{T['about_bugreport']}</a>
                </p>

            </div>
            """,
        )

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

        self.logText.clear()
        self.progressBar.setValue(0)
        self._set_toolbar_progress_visible(True)

        self._worker        = Worker(ops, self._real_user, str(self._real_home))
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        # Connect worker signals to UI slots (QueuedConnection for thread safety)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.log.connect(self.append_log,           Qt.ConnectionType.QueuedConnection)
        self._worker.progress.connect(self.on_progress_changed, Qt.ConnectionType.QueuedConnection)
        self._worker.running.connect(self.set_running,      Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._on_worker_done, Qt.ConnectionType.QueuedConnection)
        self._worker.current_op.connect(self._on_current_op, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._worker_thread.quit,      Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._worker.deleteLater,      Qt.ConnectionType.QueuedConnection)
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
        self._set_toolbar_progress_visible(False)

        for cb in [
            self.dnfOpt, self.flatpakOpt, self.cacheOpt, self.kernelOpt,
            self.systemdOpt, self.bashOpt, self.browserOpt, self.recentOpt,
            self.abrtOpt, self.logsOpt, self.coredumpOpt, self.packagekitOpt,
            self.tmpOpt, self.passwordOpt,
        ]:
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
    app.setWindowIcon(QIcon(":/kleon"))

    w = MainWindow()
    w.show()
    app.exec()


if __name__ == "__main__":
    main()
