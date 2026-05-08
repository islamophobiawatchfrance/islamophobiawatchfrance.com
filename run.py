#!/usr/bin/env python3
"""
IWF Run - Morning Pipeline
--------------------------
The single command to run each morning.
Fetches news, drafts posts, then opens the dashboard in your browser.

Run with: python3 run.py
"""

import subprocess
import sys
import time
import webbrowser
import os

DASHBOARD_URL = "http://localhost:5000"

# Absolute path to this directory so subprocess calls work from anywhere.
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    print("\n" + "=" * 55)
    print("  IWF Publishing Pipeline")
    print("  Good morning. Let's see what's happening in France.")
    print("=" * 55 + "\n")

    # ── Step 1: Run the drafter ──────────────────────────────
    print("  Step 1: Fetching stories and drafting posts...\n")
    result = subprocess.run(
        [sys.executable, "drafter.py"],
        cwd=HERE,
    )
    if result.returncode != 0:
        print("\n  ERROR: Drafter failed. See error messages above.")
        print("  Check your ANTHROPIC_API_KEY in the .env file, then try again.\n")
        sys.exit(1)

    # ── Step 2: Start the Flask dashboard in the background ──
    print("  Step 2: Starting dashboard server...")
    flask_proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=HERE,
    )

    # Give Flask a moment to bind to the port before the browser hits it.
    time.sleep(2)

    # ── Step 3: Open the browser ─────────────────────────────
    print(f"  Step 3: Opening {DASHBOARD_URL} in your browser...\n")
    webbrowser.open(DASHBOARD_URL)

    # ── Step 4: Wait for dashboard (Ctrl+C to stop) ──────────
    print("  Dashboard is running. Press Ctrl+C to stop.\n")
    try:
        flask_proc.wait()
    except KeyboardInterrupt:
        print("\n\n  Shutting down. Good editing!\n")
        flask_proc.terminate()


if __name__ == "__main__":
    main()
