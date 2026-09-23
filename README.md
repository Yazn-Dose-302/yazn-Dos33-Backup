# .yazn-Dos33 Backup

Windows backup utility focused on a simple workflow: select multiple files and/or folders, choose one destination, and create a timestamped backup locally.

## Features

- Select multiple files in one step.
- Add one or more folders to the same backup job.
- Keeps folder structure for selected directories.
- Detects exact duplicate file content with SHA-256 and skips duplicate copies.
- Checks destination free space before copying.
- Writes a local `backup_log.txt` inside each backup folder.
- No network, cloud, API, telemetry, or upload functionality.
- Windows EXE can be built as a single windowed file with UAC elevation.

## How it works

1. Start the application.
2. Select any files you want to include. You can select multiple files at once.
3. Add one or more folders when prompted.
4. Choose the backup destination.
5. The program scans the selected sources, checks available space, copies the data, and skips exact duplicates.
6. A summary is shown when the backup finishes.

Backups are created using a timestamped directory such as:

```text
Backup_2026-09-16_12-30-00/
├── 01_example.pdf
├── 02_Project/
│   └── ...
└── backup_log.txt
```

## Run from Python

Requires Python 3 on Windows:

```powershell
python yazn-Dos33_backup.py
```

The runtime application uses Python's standard library and Tkinter.

## Build the Windows EXE

Run:

```text
build_exe.bat
```

The build script installs PyInstaller and creates:

```text
dist\yazn-Dos33-Backup.exe
```

The EXE is built with:

- `--onefile`
- `--windowed`
- `--uac-admin`

So the final app does not open a CMD window and Windows will request administrator approval through UAC.

## Duplicate handling

The scanner first groups files by size. SHA-256 is only calculated when more than one file has the same size. If two files have the same SHA-256 digest, only the first copy is kept in the backup.

## Safety notes

- Do not choose a backup destination inside one of the selected source folders.
- Test the application with non-critical data before using it for important backups.
- Administrator access does not bypass encryption or Windows security protections.
- Always keep an independent backup of important data.
