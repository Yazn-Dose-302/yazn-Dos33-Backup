"""
.yazn-Dos33 Backup - Clean Multi Source

Simplified backup workflow:
- No CMD/console window is used by the final EXE build.
- No unused console banner, color theme, or drive-selection code.
- Supports selecting multiple files at once.
- Supports adding multiple source folders.
- Uses one destination folder for the whole backup.
- Scans and copies silently.
- Skips exact duplicate files using SHA-256 hashes.
- Writes a local backup_log.txt file inside the backup folder.
- Shows one completion message at the end.
"""

import os
import re
import shutil
import hashlib
from collections import Counter, defaultdict
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox


APP_NAME = ".yazn-Dos33 Backup"
VERSION = "2.1"
HASH_CHUNK_SIZE = 4 * 1024 * 1024


# ============================================================================
# General helper functions
# ============================================================================

def normalize(path):
    return os.path.normcase(os.path.abspath(path))


def is_inside(child, parent):
    """Return True if child is the same path as parent or is located inside it."""
    try:
        return os.path.commonpath(
            [normalize(child), normalize(parent)]
        ) == normalize(parent)
    except ValueError:
        return False


def human_size(size):
    value = float(size)

    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            return f"{value:.2f} {unit}"
        value /= 1024


def safe_folder_name(path, index):
    """Create a Windows-safe folder name for a source inside the backup."""
    stripped = path.rstrip("\\/")
    drive, tail = os.path.splitdrive(stripped)

    if drive and not tail:
        name = f"{drive[0].upper()}_Drive"
    else:
        name = os.path.basename(stripped) or "Source"

    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")
    return f"{index:02d}_{name or 'Source'}"


# ============================================================================
# Source and destination selection
# ============================================================================

def create_hidden_root():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    return root


def choose_sources(root):
    """
    Collect backup sources from the user.

    Step 1:
        Open a file picker that allows selecting multiple files at once.

    Step 2:
        Open a folder picker. After each selected folder, ask whether the
        user wants to add another folder.
    """
    sources = []

    # Allow selecting multiple individual files in one dialog.
    files = filedialog.askopenfilenames(
        parent=root,
        title="اختر الملفات المراد نسخها - يمكنك تحديد أكثر من ملف"
    )

    for path in files:
        add_source(sources, path)

    # Allow adding one or more folders.
    while True:
        folder = filedialog.askdirectory(
            parent=root,
            title="اختر مجلد للنسخ - اضغط إلغاء إذا انتهيت"
        )

        if not folder:
            break

        add_source(sources, folder)

        again = messagebox.askyesno(
            "إضافة مصدر",
            "هل تريد إضافة مجلد آخر؟",
            parent=root
        )

        if not again:
            break

    return sources


def add_source(sources, path):
    """
    Add a source only when it is not already covered by another source.

    Exact duplicates are ignored. If a file or folder is already located
    inside a selected source folder, it is not added again.
    """
    path = os.path.abspath(path)

    if not os.path.exists(path):
        return

    for existing in sources:
        if normalize(existing) == normalize(path):
            return

        if os.path.isdir(existing) and is_inside(path, existing):
            return

    # If a parent folder is added, remove existing sources already covered by it.
    if os.path.isdir(path):
        sources[:] = [
            existing
            for existing in sources
            if not is_inside(existing, path)
        ]

    sources.append(path)


def choose_destination(root, sources):
    while True:
        destination = filedialog.askdirectory(
            parent=root,
            title="اختر مكان حفظ النسخة الاحتياطية"
        )

        if not destination:
            return ""

        destination = os.path.abspath(destination)

        invalid = False

        for source in sources:
            if os.path.isdir(source) and is_inside(destination, source):
                invalid = True
                break

        if invalid:
            messagebox.showerror(
                "مكان غير صالح",
                "مكان الحفظ لا يمكن أن يكون داخل أحد مجلدات المصدر.",
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
                "خطأ",
                f"تعذر الكتابة إلى مكان الحفظ:\n{exc}",
                parent=root
            )


# ============================================================================
# Source scanning
# ============================================================================

def iter_source_files(source, error_callback=None):
    """Yield one selected file, or recursively yield every file inside a folder."""
    if os.path.isfile(source):
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
        for filename in files:
            yield os.path.join(current_root, filename)


def scan_sources(sources):
    """
    Scan all selected sources before copying.

    The scan calculates the total file count, total size, scan errors,
    and how many files share each file size. Size counts are later used
    to avoid calculating SHA-256 hashes unless duplicate content is possible.
    """
    total_files = 0
    total_bytes = 0
    scan_errors = 0
    size_counts = Counter()

    def on_error(_):
        nonlocal scan_errors
        scan_errors += 1

    for source in sources:
        for filepath in iter_source_files(
            source,
            error_callback=on_error
        ):
            try:
                size = os.path.getsize(filepath)
            except OSError:
                scan_errors += 1
                continue

            total_files += 1
            total_bytes += size
            size_counts[size] += 1

    return {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "scan_errors": scan_errors,
        "size_counts": size_counts,
    }


# ============================================================================
# Duplicate detection
# ============================================================================

def sha256_file(filepath):
    """
    Calculate the SHA-256 hash of a file in chunks.

    Reading in chunks prevents large files from being loaded entirely
    into memory.
    """
    digest = hashlib.sha256()

    with open(filepath, "rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_SIZE)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


# ============================================================================
# Backup logging
# ============================================================================

class BackupLogger:
    def __init__(self, filepath):
        self.handle = open(
            filepath,
            "a",
            encoding="utf-8"
        )

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
# Backup copy engine
# ============================================================================

def copy_sources(sources, destination, scan_info):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    backup_root = os.path.join(
        destination,
        f"Backup_{timestamp}"
    )

    os.makedirs(
        backup_root,
        exist_ok=False
    )

    logger = BackupLogger(
        os.path.join(
            backup_root,
            "backup_log.txt"
        )
    )

    copied = 0
    duplicates = 0
    failed = 0

    size_counts = scan_info["size_counts"]

    # Map each file size to {sha256_hash: first_seen_source_path}.
    seen_hashes = defaultdict(dict)

    logger.write(f"{APP_NAME} v{VERSION}")
    logger.write("Backup started")
    logger.write(
        "Sources: " + " | ".join(sources)
    )
    logger.write(f"Destination: {backup_root}")

    try:
        for source_index, source in enumerate(sources, 1):

            # ------------------------------------------------------------
            # Source type: individual file
            # ------------------------------------------------------------
            if os.path.isfile(source):
                filepath = source

                try:
                    size = os.path.getsize(filepath)

                    if size_counts.get(size, 0) > 1:
                        file_hash = sha256_file(filepath)

                        if file_hash in seen_hashes[size]:
                            duplicates += 1
                            logger.write(
                                f"DUPLICATE SKIPPED: {filepath} | "
                                f"same_as={seen_hashes[size][file_hash]}"
                            )
                            continue

                        seen_hashes[size][file_hash] = filepath

                    destination_path = os.path.join(
                        backup_root,
                        f"{source_index:02d}_{os.path.basename(filepath)}"
                    )

                    shutil.copy2(
                        filepath,
                        destination_path
                    )

                    copied += 1

                    logger.write(
                        f"COPIED: {filepath} -> {destination_path}"
                    )

                except (PermissionError, OSError, shutil.Error) as exc:
                    failed += 1
                    logger.write(
                        f"FAILED: {filepath} | {exc}"
                    )

                continue

            # ------------------------------------------------------------
            # Source type: directory
            # ------------------------------------------------------------
            source_output = os.path.join(
                backup_root,
                safe_folder_name(source, source_index)
            )

            os.makedirs(
                source_output,
                exist_ok=True
            )

            for filepath in iter_source_files(
                source,
                error_callback=lambda error: logger.write(
                    f"WALK ERROR: {error}"
                )
            ):
                try:
                    size = os.path.getsize(filepath)

                    if size_counts.get(size, 0) > 1:
                        file_hash = sha256_file(filepath)

                        if file_hash in seen_hashes[size]:
                            duplicates += 1
                            logger.write(
                                f"DUPLICATE SKIPPED: {filepath} | "
                                f"same_as={seen_hashes[size][file_hash]}"
                            )
                            continue

                        seen_hashes[size][file_hash] = filepath

                    relative_path = os.path.relpath(
                        filepath,
                        source
                    )

                    destination_path = os.path.join(
                        source_output,
                        relative_path
                    )

                    os.makedirs(
                        os.path.dirname(destination_path),
                        exist_ok=True
                    )

                    shutil.copy2(
                        filepath,
                        destination_path
                    )

                    copied += 1

                    logger.write(
                        f"COPIED: {filepath} -> {destination_path}"
                    )

                except (PermissionError, OSError, shutil.Error) as exc:
                    failed += 1
                    logger.write(
                        f"FAILED: {filepath} | {exc}"
                    )

    finally:
        logger.write(
            f"Summary copied={copied} "
            f"duplicates={duplicates} "
            f"failed={failed}"
        )
        logger.close()

    return backup_root, copied, duplicates, failed


# ============================================================================
# Completion result
# ============================================================================

def show_result(
    root,
    backup_root,
    copied,
    duplicates,
    failed
):
    messagebox.showinfo(
        "Backup",
        (
            "تم الانتهاء من النسخ الاحتياطي.\n\n"
            f"تم نسخ: {copied:,} ملف\n"
            f"تم تجاهل المكرر: {duplicates:,}\n"
            f"الأخطاء/الفشل: {failed:,}\n\n"
            f"مكان النسخة:\n{backup_root}"
        ),
        parent=root
    )


# ============================================================================
# Application entry point
# ============================================================================

def main():
    root = create_hidden_root()

    try:
        # 1) Select one or more source files/folders.
        sources = choose_sources(root)

        if not sources:
            return 0

        # 2) Select the backup destination.
        destination = choose_destination(
            root,
            sources
        )

        if not destination:
            return 0

        # 3) Scan all selected sources before copying.
        scan_info = scan_sources(sources)

        # 4) Verify that the destination has enough free space.
        free_space = shutil.disk_usage(destination).free

        if free_space < scan_info["total_bytes"]:
            messagebox.showerror(
                "المساحة غير كافية",
                (
                    f"الحجم المتوقع: {human_size(scan_info['total_bytes'])}\n"
                    f"المتاح: {human_size(free_space)}"
                ),
                parent=root
            )
            return 1

        # 5) Copy all selected sources.
        backup_root, copied, duplicates, failed = copy_sources(
            sources,
            destination,
            scan_info
        )

        # 6) Show the final backup summary.
        show_result(
            root,
            backup_root,
            copied,
            duplicates,
            failed
        )

        return 0

    except Exception as exc:
        messagebox.showerror(
            "خطأ",
            f"حدث خطأ غير متوقع:\n{exc}",
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
