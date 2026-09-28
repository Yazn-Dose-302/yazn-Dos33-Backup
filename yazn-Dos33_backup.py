"""
.yazn-Dos33 Backup v2.2
Clean multi-source Windows backup utility.

Changes in v2.2:
- Clear source manager instead of sequential/confusing pickers.
- Add multiple files and multiple folders in one job.
- Background scan/copy with a small progress window.
- Arabic UI text is reshaped for Tkinter where needed.
- Custom result dialog avoids mixed RTL/LTR messagebox problems.
- Duplicate hashes are registered only AFTER a successful copy.
- Reparse points/symlinks are skipped to avoid loops/unexpected targets.
- No CMD when built with the supplied --windowed build script.
"""

import os
import re
import stat
import shutil
import hashlib
import threading
import queue
from collections import Counter, defaultdict
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    arabic_reshaper = None
    get_display = None


APP_NAME = ".yazn-Dos33 Backup"
VERSION = "2.2"
HASH_CHUNK_SIZE = 4 * 1024 * 1024


# ============================================================================
# Arabic / text helpers
# ============================================================================

def ar(text):
    """Prepare Arabic text for Tk widgets that render primarily LTR."""
    if not text:
        return text

    if arabic_reshaper is None or get_display is None:
        return text

    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def human_size(size):
    value = float(size)

    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            return f"{value:.2f} {unit}"
        value /= 1024


def normalize(path):
    return os.path.normcase(os.path.abspath(path))


def is_inside(child, parent):
    """True when child is equal to or located inside parent."""
    try:
        return os.path.commonpath(
            [normalize(child), normalize(parent)]
        ) == normalize(parent)
    except ValueError:
        return False


def safe_source_name(path, index):
    """Create a safe unique output name for a selected source."""
    if os.path.isfile(path):
        name = os.path.basename(path)
    else:
        stripped = path.rstrip("\\/")
        drive, tail = os.path.splitdrive(stripped)

        if drive and not tail:
            name = f"{drive[0].upper()}_Drive"
        else:
            name = os.path.basename(stripped) or "Source"

    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")
    return f"{index:02d}_{name or 'Source'}"


def is_reparse_point(path):
    """Best-effort Windows reparse-point detection."""
    try:
        info = os.stat(path, follow_symlinks=False)
        attrs = getattr(info, "st_file_attributes", 0)
        flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attrs & flag)
    except OSError:
        return False


# ============================================================================
# Source management
# ============================================================================

def add_source(sources, path):
    """
    Add a source without redundant duplicates.

    If a selected folder already contains this path, the new path is redundant.
    If the new folder contains previously selected items, those child items are
    removed because the parent folder now covers them.
    """
    path = os.path.abspath(path)

    if not os.path.exists(path):
        return False

    for existing in sources:
        if normalize(existing) == normalize(path):
            return False

        if os.path.isdir(existing) and is_inside(path, existing):
            return False

    if os.path.isdir(path):
        sources[:] = [
            existing
            for existing in sources
            if not is_inside(existing, path)
        ]

    sources.append(path)
    return True


class SourcePicker:
    """One small window for managing all files/folders before backup."""

    def __init__(self, root):
        self.root = root
        self.sources = []
        self.result = None

        self.win = tk.Toplevel(root)
        self.win.title(APP_NAME)
        self.win.geometry("760x430")
        self.win.minsize(650, 360)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.transient(root)
        self.win.grab_set()

        self._build()

    def _build(self):
        main = ttk.Frame(self.win, padding=16)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text=APP_NAME,
            font=("Segoe UI", 16, "bold")
        ).pack(anchor="w")

        ttk.Label(
            main,
            text=ar("اختر كل الملفات والمجلدات التي تريد نسخها، ثم اضغط متابعة"),
            font=("Segoe UI", 10)
        ).pack(anchor="w", pady=(4, 12))

        buttons = ttk.Frame(main)
        buttons.pack(fill="x")

        ttk.Button(
            buttons,
            text=ar("إضافة ملفات"),
            command=self.add_files
        ).pack(side="left")

        ttk.Button(
            buttons,
            text=ar("إضافة مجلد"),
            command=self.add_folder
        ).pack(side="left", padx=6)

        ttk.Button(
            buttons,
            text=ar("حذف المحدد"),
            command=self.remove_selected
        ).pack(side="left", padx=6)

        ttk.Button(
            buttons,
            text=ar("مسح القائمة"),
            command=self.clear_all
        ).pack(side="left")

        list_frame = ttk.Frame(main)
        list_frame.pack(fill="both", expand=True, pady=12)

        self.listbox = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            font=("Consolas", 10),
            activestyle="none"
        )
        scroll = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.listbox.yview
        )
        self.listbox.configure(yscrollcommand=scroll.set)

        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        bottom = ttk.Frame(main)
        bottom.pack(fill="x")

        self.count_label = ttk.Label(
            bottom,
            text=ar("لا توجد مصادر بعد")
        )
        self.count_label.pack(side="left")

        ttk.Button(
            bottom,
            text=ar("إلغاء"),
            command=self.cancel
        ).pack(side="right")

        ttk.Button(
            bottom,
            text=ar("متابعة"),
            command=self.finish
        ).pack(side="right", padx=(0, 8))

    def add_files(self):
        paths = filedialog.askopenfilenames(
            parent=self.win,
            title="Select files to back up"
        )

        changed = False
        for path in paths:
            changed = add_source(self.sources, path) or changed

        if changed:
            self.refresh()

    def add_folder(self):
        path = filedialog.askdirectory(
            parent=self.win,
            title="Select a folder to back up"
        )

        if path and add_source(self.sources, path):
            self.refresh()

    def remove_selected(self):
        indexes = list(self.listbox.curselection())
        for index in reversed(indexes):
            del self.sources[index]
        self.refresh()

    def clear_all(self):
        self.sources.clear()
        self.refresh()

    def refresh(self):
        self.listbox.delete(0, "end")

        for index, path in enumerate(self.sources, 1):
            kind = "FILE" if os.path.isfile(path) else "DIR "
            self.listbox.insert(
                "end",
                f"{index:02d}  [{kind}]  {path}"
            )

        if self.sources:
            self.count_label.config(
                text=ar("عدد المصادر المحددة") + f": {len(self.sources)}"
            )
        else:
            self.count_label.config(text=ar("لا توجد مصادر بعد"))

    def finish(self):
        if not self.sources:
            messagebox.showwarning(
                ar("تنبيه"),
                ar("أضف ملفاً أو مجلداً واحداً على الأقل."),
                parent=self.win
            )
            return

        self.result = list(self.sources)
        self.win.destroy()

    def cancel(self):
        self.result = None
        self.win.destroy()

    def show(self):
        self.root.wait_window(self.win)
        return self.result


# ============================================================================
# Destination
# ============================================================================

def choose_destination(root, sources):
    while True:
        destination = filedialog.askdirectory(
            parent=root,
            title="Select backup destination"
        )

        if not destination:
            return ""

        destination = os.path.abspath(destination)

        if any(
            os.path.isdir(source) and is_inside(destination, source)
            for source in sources
        ):
            messagebox.showerror(
                ar("مكان غير صالح"),
                ar("مكان الحفظ لا يمكن أن يكون داخل أحد مجلدات المصدر."),
                parent=root
            )
            continue

        try:
            os.makedirs(destination, exist_ok=True)
            test_path = os.path.join(
                destination,
                f".yazn_write_test_{os.getpid()}.tmp"
            )

            with open(test_path, "wb") as handle:
                handle.write(b"ok")

            os.remove(test_path)
            return destination

        except OSError as exc:
            messagebox.showerror(
                ar("خطأ"),
                ar("تعذر الكتابة إلى مكان الحفظ.") + f"\n\n{exc}",
                parent=root
            )


# ============================================================================
# File walking / hashing
# ============================================================================

def iter_source_files(source, stop_event=None, error_callback=None):
    """Yield real files while avoiding symlink/reparse loops."""
    if os.path.isfile(source):
        if not os.path.islink(source) and not is_reparse_point(source):
            yield source
        return

    def on_walk_error(exc):
        if error_callback:
            error_callback(str(exc))

    for current_root, dirs, files in os.walk(
        source,
        topdown=True,
        onerror=on_walk_error,
        followlinks=False
    ):
        if stop_event and stop_event.is_set():
            return

        dirs[:] = [
            dirname
            for dirname in dirs
            if not os.path.islink(os.path.join(current_root, dirname))
            and not is_reparse_point(os.path.join(current_root, dirname))
        ]

        for filename in files:
            if stop_event and stop_event.is_set():
                return

            path = os.path.join(current_root, filename)

            if os.path.islink(path) or is_reparse_point(path):
                continue

            yield path


def sha256_file(filepath, stop_event=None):
    digest = hashlib.sha256()

    with open(filepath, "rb") as handle:
        while True:
            if stop_event and stop_event.is_set():
                return None

            chunk = handle.read(HASH_CHUNK_SIZE)
            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


# ============================================================================
# Logger
# ============================================================================

class BackupLogger:
    def __init__(self, filepath):
        self.handle = open(filepath, "a", encoding="utf-8")

    def write(self, text):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.handle.write(f"[{stamp}] {text}\n")
        self.handle.flush()

    def close(self):
        try:
            self.handle.close()
        except Exception:
            pass


# ============================================================================
# Background worker
# ============================================================================

class BackupWorker(threading.Thread):
    def __init__(self, sources, destination, events, stop_event):
        super().__init__(daemon=True)
        self.sources = list(sources)
        self.destination = destination
        self.events = events
        self.stop_event = stop_event

    def emit(self, event, **data):
        self.events.put((event, data))

    def run(self):
        try:
            self._run()
        except Exception as exc:
            self.emit("fatal", message=str(exc))

    def _run(self):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_root = os.path.join(
            self.destination,
            f"Backup_{timestamp}"
        )

        os.makedirs(backup_root, exist_ok=False)

        logger = BackupLogger(
            os.path.join(backup_root, "backup_log.txt")
        )

        logger.write(f"{APP_NAME} v{VERSION}")
        logger.write("Backup started")
        logger.write("Sources: " + " | ".join(self.sources))
        logger.write(f"Destination: {backup_root}")

        try:
            # ------------------------------------------------------------
            # Scan
            # ------------------------------------------------------------
            self.emit("phase", name="scan")

            total_files = 0
            total_bytes = 0
            scan_errors = 0
            size_counts = Counter()

            def scan_error(message):
                nonlocal scan_errors
                scan_errors += 1
                logger.write("SCAN ERROR: " + message)

            for source in self.sources:
                for filepath in iter_source_files(
                    source,
                    stop_event=self.stop_event,
                    error_callback=scan_error
                ):
                    if self.stop_event.is_set():
                        self.emit("stopped", backup_root=backup_root)
                        return

                    self.emit("current", path=filepath)

                    try:
                        size = os.path.getsize(filepath)
                    except OSError as exc:
                        scan_errors += 1
                        logger.write(f"SCAN STAT ERROR: {filepath} | {exc}")
                        continue

                    total_files += 1
                    total_bytes += size
                    size_counts[size] += 1

                    if total_files % 100 == 0:
                        self.emit(
                            "scan_progress",
                            files=total_files,
                            bytes=total_bytes,
                            errors=scan_errors
                        )

            self.emit(
                "scan_done",
                total_files=total_files,
                total_bytes=total_bytes,
                scan_errors=scan_errors
            )

            if self.stop_event.is_set():
                self.emit("stopped", backup_root=backup_root)
                return

            free_space = shutil.disk_usage(self.destination).free

            # This is a conservative pre-dedup estimate. Ask the UI instead
            # of hard-failing, because duplicates can lower final size.
            if free_space < total_bytes:
                self.emit(
                    "space_warning",
                    required=total_bytes,
                    free=free_space,
                    backup_root=backup_root
                )
                return

            self._copy(
                backup_root,
                logger,
                size_counts,
                total_files,
                total_bytes,
                scan_errors
            )

        finally:
            # _copy closes through its result path; close is idempotent.
            logger.close()

    def _copy(
        self,
        backup_root,
        logger,
        size_counts,
        total_files,
        total_bytes,
        scan_errors
    ):
        self.emit("phase", name="copy")

        copied = 0
        duplicates = 0
        failed = 0
        processed_files = 0
        processed_bytes = 0

        # size -> {sha256: successfully_copied_source_path}
        seen_hashes = defaultdict(dict)

        for source_index, source in enumerate(self.sources, 1):
            if self.stop_event.is_set():
                break

            if os.path.isfile(source):
                source_output = backup_root
            else:
                source_output = os.path.join(
                    backup_root,
                    safe_source_name(source, source_index)
                )
                os.makedirs(source_output, exist_ok=True)

            for filepath in iter_source_files(
                source,
                stop_event=self.stop_event,
                error_callback=lambda msg: logger.write("WALK ERROR: " + msg)
            ):
                if self.stop_event.is_set():
                    break

                self.emit("current", path=filepath)
                size = 0
                digest = None

                try:
                    size = os.path.getsize(filepath)

                    if size_counts.get(size, 0) > 1:
                        digest = sha256_file(
                            filepath,
                            stop_event=self.stop_event
                        )

                        if digest is None:
                            break

                        if digest in seen_hashes[size]:
                            duplicates += 1
                            logger.write(
                                f"DUPLICATE SKIPPED: {filepath} | "
                                f"same_as={seen_hashes[size][digest]}"
                            )
                            continue

                    if os.path.isfile(source):
                        destination_path = os.path.join(
                            backup_root,
                            safe_source_name(source, source_index)
                        )
                    else:
                        relative_path = os.path.relpath(filepath, source)
                        destination_path = os.path.join(
                            source_output,
                            relative_path
                        )

                    os.makedirs(
                        os.path.dirname(destination_path),
                        exist_ok=True
                    )

                    shutil.copy2(filepath, destination_path)
                    copied += 1

                    # IMPORTANT: record a duplicate fingerprint only after a
                    # successful copy. If copy fails, a later identical file
                    # must still get a chance to be copied.
                    if digest is not None:
                        seen_hashes[size][digest] = filepath

                    logger.write(
                        f"COPIED: {filepath} -> {destination_path}"
                    )

                except (PermissionError, OSError, shutil.Error) as exc:
                    failed += 1
                    logger.write(f"FAILED: {filepath} | {exc}")

                finally:
                    processed_files += 1
                    processed_bytes += size

                    self.emit(
                        "progress",
                        copied=copied,
                        duplicates=duplicates,
                        failed=failed,
                        processed_files=processed_files,
                        total_files=total_files,
                        processed_bytes=processed_bytes,
                        total_bytes=total_bytes,
                        scan_errors=scan_errors
                    )

        if self.stop_event.is_set():
            logger.write("Backup stopped by user")
            self.emit(
                "stopped",
                backup_root=backup_root,
                copied=copied,
                duplicates=duplicates,
                failed=failed
            )
            return

        logger.write(
            f"Summary copied={copied} duplicates={duplicates} "
            f"failed={failed} scan_errors={scan_errors}"
        )

        self.emit(
            "finished",
            backup_root=backup_root,
            copied=copied,
            duplicates=duplicates,
            failed=failed,
            scan_errors=scan_errors
        )


# ============================================================================
# Progress window
# ============================================================================

class ProgressWindow:
    def __init__(self, root, sources, destination):
        self.root = root
        self.sources = sources
        self.destination = destination
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.finished_data = None

        self.win = tk.Toplevel(root)
        self.win.title(APP_NAME)
        self.win.geometry("720x300")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.request_stop)
        self.win.transient(root)

        self._build()

    def _build(self):
        main = ttk.Frame(self.win, padding=18)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text=APP_NAME,
            font=("Segoe UI", 15, "bold")
        ).pack(anchor="w")

        self.phase_label = ttk.Label(
            main,
            text=ar("جاري التحضير..."),
            font=("Segoe UI", 10, "bold")
        )
        self.phase_label.pack(anchor="w", pady=(12, 5))

        self.progress = ttk.Progressbar(
            main,
            mode="indeterminate",
            maximum=100
        )
        self.progress.pack(fill="x")
        self.progress.start(12)

        self.amount_label = ttk.Label(main, text="0 B / 0 B")
        self.amount_label.pack(anchor="w", pady=(6, 3))

        ttk.Label(
            main,
            text=ar("الملف الحالي"),
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(7, 2))

        self.current_label = ttk.Label(
            main,
            text="-",
            wraplength=675,
            font=("Consolas", 9)
        )
        self.current_label.pack(anchor="w", fill="x")

        stats = ttk.Frame(main)
        stats.pack(fill="x", pady=(13, 0))

        self.ok_label = ttk.Label(stats, text="OK: 0")
        self.ok_label.pack(side="left")

        self.dup_label = ttk.Label(stats, text="DUP: 0")
        self.dup_label.pack(side="left", padx=20)

        self.err_label = ttk.Label(stats, text="ERR: 0")
        self.err_label.pack(side="left")

        self.stop_button = ttk.Button(
            stats,
            text=ar("إيقاف"),
            command=self.request_stop
        )
        self.stop_button.pack(side="right")

    def start(self):
        self.worker = BackupWorker(
            self.sources,
            self.destination,
            self.events,
            self.stop_event
        )
        self.worker.start()
        self.win.after(100, self.poll)
        self.root.wait_window(self.win)
        return self.finished_data

    def request_stop(self):
        if self.stop_event.is_set():
            return

        self.stop_event.set()
        self.stop_button.config(state="disabled")
        self.phase_label.config(text=ar("جاري الإيقاف..."))

    def poll(self):
        try:
            while True:
                event, data = self.events.get_nowait()
                self.handle_event(event, data)
        except queue.Empty:
            pass

        try:
            if self.win.winfo_exists():
                self.win.after(100, self.poll)
        except tk.TclError:
            pass

    def handle_event(self, event, data):
        if event == "phase":
            if data["name"] == "scan":
                self.phase_label.config(text=ar("جاري فحص الملفات..."))
                self.progress.config(mode="indeterminate")
                self.progress.start(12)
            else:
                self.phase_label.config(text=ar("جاري نسخ الملفات..."))
                self.progress.stop()
                self.progress.config(mode="determinate", value=0)

        elif event == "current":
            self.current_label.config(text=data["path"])

        elif event == "scan_progress":
            self.amount_label.config(
                text=(
                    f"{data['files']:,} files  |  "
                    f"{human_size(data['bytes'])}"
                )
            )

        elif event == "scan_done":
            self.progress.stop()
            self.progress.config(mode="determinate", value=0)
            self.amount_label.config(
                text=f"0 B / {human_size(data['total_bytes'])}"
            )

        elif event == "progress":
            total = data["total_bytes"]
            done = data["processed_bytes"]
            percent = min(100.0, done / total * 100) if total else 100.0

            self.progress["value"] = percent
            self.amount_label.config(
                text=f"{human_size(done)} / {human_size(total)}"
            )
            self.ok_label.config(text=f"OK: {data['copied']:,}")
            self.dup_label.config(text=f"DUP: {data['duplicates']:,}")
            self.err_label.config(text=f"ERR: {data['failed']:,}")

        elif event == "space_warning":
            # We do not continue the same worker because the logger/file state
            # belongs to that thread. Abort conservatively with a clear result.
            required = human_size(data["required"])
            free = human_size(data["free"])
            self.finished_data = {
                "status": "no_space",
                "backup_root": data["backup_root"],
                "required": required,
                "free": free,
            }
            self.win.destroy()

        elif event == "finished":
            self.finished_data = {"status": "finished", **data}
            self.win.destroy()

        elif event == "stopped":
            self.finished_data = {"status": "stopped", **data}
            self.win.destroy()

        elif event == "fatal":
            self.finished_data = {
                "status": "fatal",
                "message": data["message"]
            }
            self.win.destroy()


# ============================================================================
# Result window - custom UI avoids RTL/messagebox direction issues
# ============================================================================

def show_result(root, result):
    win = tk.Toplevel(root)
    win.title(APP_NAME)
    win.geometry("560x360")
    win.resizable(False, False)
    win.transient(root)
    win.grab_set()

    main = ttk.Frame(win, padding=22)
    main.pack(fill="both", expand=True)

    status = result.get("status")

    if status == "finished":
        heading = "اكتملت عملية النسخ"
        subtitle = "تم الانتهاء من النسخ الاحتياطي بنجاح"
    elif status == "stopped":
        heading = "تم إيقاف النسخ"
        subtitle = "توقفت العملية قبل اكتمال جميع الملفات"
    elif status == "no_space":
        heading = "المساحة غير كافية"
        subtitle = "المساحة المتاحة أقل من الحجم المتوقع للنسخة"
    else:
        heading = "حدث خطأ"
        subtitle = "تعذر إكمال عملية النسخ"

    ttk.Label(
        main,
        text=ar(heading),
        font=("Segoe UI", 16, "bold")
    ).pack(anchor="center")

    ttk.Label(
        main,
        text=ar(subtitle),
        font=("Segoe UI", 10)
    ).pack(anchor="center", pady=(6, 18))

    if status in {"finished", "stopped"}:
        rows = [
            ("تم النسخ", result.get("copied", 0)),
            ("المكرر", result.get("duplicates", 0)),
            ("الفاشل", result.get("failed", 0)),
            ("أخطاء الفحص", result.get("scan_errors", 0)),
        ]

        for label, value in rows:
            row = ttk.Frame(main)
            row.pack(fill="x", pady=2)

            ttk.Label(
                row,
                text=ar(label),
                width=18,
                anchor="e"
            ).pack(side="right")

            ttk.Label(
                row,
                text=f"{value:,}",
                width=15,
                anchor="w",
                font=("Consolas", 10, "bold")
            ).pack(side="right", padx=(10, 0))

        backup_root = result.get("backup_root", "")

        if backup_root:
            ttk.Separator(main).pack(fill="x", pady=14)
            ttk.Label(
                main,
                text=ar("مكان النسخة"),
                font=("Segoe UI", 9, "bold")
            ).pack(anchor="e")

            path_box = ttk.Entry(main)
            path_box.pack(fill="x", pady=(5, 0))
            path_box.insert(0, backup_root)
            path_box.config(state="readonly")

    elif status == "no_space":
        ttk.Label(
            main,
            text=f"Required: {result.get('required', '-')}\nFree: {result.get('free', '-')}",
            font=("Consolas", 11)
        ).pack(pady=12)

    else:
        ttk.Label(
            main,
            text=result.get("message", "Unknown error"),
            wraplength=500,
            font=("Consolas", 9)
        ).pack(pady=12)

    ttk.Button(
        main,
        text=ar("إغلاق"),
        command=win.destroy
    ).pack(side="bottom", pady=(18, 0))

    root.wait_window(win)


# ============================================================================
# Main
# ============================================================================

def main():
    root = tk.Tk()
    root.withdraw()

    try:
        picker = SourcePicker(root)
        sources = picker.show()

        if not sources:
            return 0

        destination = choose_destination(root, sources)
        if not destination:
            return 0

        progress = ProgressWindow(root, sources, destination)
        result = progress.start()

        if result:
            show_result(root, result)

        return 0

    except Exception as exc:
        messagebox.showerror(
            ar("خطأ"),
            ar("حدث خطأ غير متوقع.") + f"\n\n{exc}",
            parent=root
        )
        return 1

    finally:
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
