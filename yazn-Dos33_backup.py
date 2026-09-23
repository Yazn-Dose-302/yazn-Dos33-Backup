"""
.yazn-Dos33 Backup - Clean Multi Source

نسخة مبسطة:
- لا يوجد CMD.
- لا يوجد Banner أو ألوان Console أو كود أقراص غير مستخدم.
- اختيار عدة ملفات دفعة واحدة.
- اختيار أكثر من مجلد.
- اختيار وجهة واحدة.
- فحص صامت ثم نسخ صامت.
- تجاهل الملفات المتطابقة فعلياً باستخدام SHA-256.
- Log محلي داخل مجلد النسخة.
- رسالة واحدة في النهاية.
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
# مساعدات عامة
# ============================================================================

def normalize(path):
    return os.path.normcase(os.path.abspath(path))


def is_inside(child, parent):
    """هل child داخل parent أو يساويه؟"""
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
    """اسم آمن لمجلد المصدر داخل الـ Backup."""
    stripped = path.rstrip("\\/")
    drive, tail = os.path.splitdrive(stripped)

    if drive and not tail:
        name = f"{drive[0].upper()}_Drive"
    else:
        name = os.path.basename(stripped) or "Source"

    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")
    return f"{index:02d}_{name or 'Source'}"


# ============================================================================
# اختيار المصادر والوجهة
# ============================================================================

def create_hidden_root():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    return root


def choose_sources(root):
    """
    1) نافذة اختيار ملفات، وتدعم تحديد عدة ملفات دفعة واحدة.
    2) بعدها نافذة اختيار مجلد، ويمكن إضافة أكثر من مجلد.
       بعد كل مجلد نسأل إذا يريد إضافة مجلد آخر.
    """
    sources = []

    # عدة ملفات دفعة واحدة.
    files = filedialog.askopenfilenames(
        parent=root,
        title="اختر الملفات المراد نسخها - يمكنك تحديد أكثر من ملف"
    )

    for path in files:
        add_source(sources, path)

    # مجلد أو أكثر.
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
    يمنع تكرار نفس المصدر.
    وإذا كان ملف/مجلد داخل مجلد مصدر تمت إضافته، لا نضيفه مرة ثانية.
    """
    path = os.path.abspath(path)

    if not os.path.exists(path):
        return

    for existing in sources:
        if normalize(existing) == normalize(path):
            return

        if os.path.isdir(existing) and is_inside(path, existing):
            return

    # إذا أضفنا مجلد أب، نحذف العناصر الموجودة داخله لأنها أصبحت مغطاة.
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
# الفحص
# ============================================================================

def iter_source_files(source, error_callback=None):
    """يمر على ملف واحد أو كل الملفات داخل مجلد."""
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
    يحسب عدد الملفات والحجم ويجمع عدد الملفات لكل حجم.
    جمع الأحجام يساعدنا أن نحسب SHA-256 فقط عند وجود احتمال تكرار.
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
# منع التكرار
# ============================================================================

def sha256_file(filepath):
    """
    يحسب بصمة الملف على أجزاء.
    لا يتم تحميل الملف كاملاً في الذاكرة.
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
# Log
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
# النسخ
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

    # size -> {sha256: first_path}
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
            # مصدر = ملف واحد
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
            # مصدر = مجلد
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
# النتيجة
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
# Main
# ============================================================================

def main():
    root = create_hidden_root()

    try:
        # 1) اختيار أكثر من ملف/مجلد
        sources = choose_sources(root)

        if not sources:
            return 0

        # 2) اختيار مكان النسخ
        destination = choose_destination(
            root,
            sources
        )

        if not destination:
            return 0

        # 3) فحص صامت
        scan_info = scan_sources(sources)

        # 4) فحص مساحة الوجهة
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

        # 5) النسخ
        backup_root, copied, duplicates, failed = copy_sources(
            sources,
            destination,
            scan_info
        )

        # 6) رسالة النهاية
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
