#!/usr/bin/env python3
"""
IWF Pipeline Scheduler
----------------------
Run this once to schedule the IWF pipeline at 07:00 and 18:00 daily.
Uses macOS launchd (no cron required). Detects your current Python automatically.

Run with: python3 schedule_setup.py
"""

import os
import subprocess
import sys

PYTHON_PATH = sys.executable
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
RUN_SCRIPT  = os.path.join(SCRIPT_DIR, "run.py")
LOGS_DIR    = os.path.join(SCRIPT_DIR, "logs")

JOBS = [
    {
        "label":  "com.iwf.pipeline.morning",
        "hour":   7,
        "minute": 0,
        "log":    os.path.join(LOGS_DIR, "pipeline-morning.log"),
    },
    {
        "label":  "com.iwf.pipeline.evening",
        "hour":   18,
        "minute": 0,
        "log":    os.path.join(LOGS_DIR, "pipeline-evening.log"),
    },
]


def _plist(job: dict) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{job["label"]}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{PYTHON_PATH}</string>
        <string>{RUN_SCRIPT}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{SCRIPT_DIR}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{job["hour"]}</integer>
        <key>Minute</key>
        <integer>{job["minute"]}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{job["log"]}</string>
    <key>StandardErrorPath</key>
    <string>{job["log"]}</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def main():
    print("\n" + "=" * 55)
    print("  IWF Pipeline Scheduler Setup")
    print("=" * 55 + "\n")

    if not os.path.exists(RUN_SCRIPT):
        print(f"  ERROR: run.py not found at:\n  {RUN_SCRIPT}\n")
        sys.exit(1)

    os.makedirs(os.path.expanduser("~/Library/LaunchAgents"), exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)

    for job in JOBS:
        plist_path = os.path.expanduser(f"~/Library/LaunchAgents/{job['label']}.plist")

        # Unload any existing job silently before overwriting
        subprocess.run(["launchctl", "unload", plist_path], capture_output=True)

        with open(plist_path, "w") as f:
            f.write(_plist(job))

        result = subprocess.run(
            ["launchctl", "load", plist_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  ERROR loading {job['label']}:\n  {result.stderr.strip()}\n")
            sys.exit(1)

        time_label = f"{job['hour']:02d}:{job['minute']:02d}"
        print(f"  Loaded: {job['label']} — runs daily at {time_label}")
        print(f"  Log:    {job['log']}")
        print()

    print("  Pipeline will now run at 07:00 and 18:00 every day.\n")
    print("  To disable, run:")
    for job in JOBS:
        plist_path = os.path.expanduser(f"~/Library/LaunchAgents/{job['label']}.plist")
        print(f"    launchctl unload {plist_path}")
    print()


if __name__ == "__main__":
    main()
