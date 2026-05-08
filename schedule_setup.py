#!/usr/bin/env python3
"""
IWF Pipeline Scheduler
----------------------
Run this once to schedule the IWF pipeline to run automatically at 7am daily.
Uses macOS launchd (no cron required).

Run with: python3 schedule_setup.py
"""

import os
import subprocess
import sys

PYTHON_PATH  = "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
RUN_SCRIPT   = os.path.join(SCRIPT_DIR, "run.py")
LOG_PATH     = os.path.join(SCRIPT_DIR, "pipeline.log")
PLIST_LABEL  = "com.iwf.pipeline"
PLIST_PATH   = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_LABEL}.plist")

PLIST_CONTENT = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
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
        <integer>7</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>{LOG_PATH}</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def main():
    print("\n" + "=" * 55)
    print("  IWF Pipeline Scheduler Setup")
    print("=" * 55 + "\n")

    if not os.path.exists(PYTHON_PATH):
        print(f"  ERROR: python3 not found at:\n  {PYTHON_PATH}")
        print("\n  Find yours with:  which python3")
        print("  Then update PYTHON_PATH in this file.")
        sys.exit(1)

    if not os.path.exists(RUN_SCRIPT):
        print(f"  ERROR: run.py not found at:\n  {RUN_SCRIPT}")
        sys.exit(1)

    os.makedirs(os.path.expanduser("~/Library/LaunchAgents"), exist_ok=True)

    # Unload any existing job silently before overwriting
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)

    with open(PLIST_PATH, "w") as f:
        f.write(PLIST_CONTENT)
    print(f"  Plist written:  {PLIST_PATH}")

    result = subprocess.run(
        ["launchctl", "load", PLIST_PATH],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"\n  ERROR loading schedule:\n  {result.stderr.strip()}")
        sys.exit(1)

    print("  Schedule loaded: pipeline will run at 07:00 every day.")
    print(f"  Log output:     {LOG_PATH}")
    print("\n  To disable the schedule, run:")
    print(f"    launchctl unload {PLIST_PATH}")
    print(f"    rm {PLIST_PATH}")
    print()


if __name__ == "__main__":
    main()
