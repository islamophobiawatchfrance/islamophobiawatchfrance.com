#!/usr/bin/env python3
"""
IWF Menu Bar App
-----------------
Mac menu bar controller for the IWF publishing pipeline: status at a glance,
one-click pipeline/dashboard control, Telegram-approval notifications.

Run with: python3 iwf_menubar.py
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import rumps

REPO = Path(__file__).resolve().parent
LOGS_DIR = REPO / "logs"
QUEUE_FILE = REPO / "queue.json"
PUBLISHED_FILE = REPO / "published.json"
DASHBOARD_PORT = 5000
DASHBOARD_URL = f"http://localhost:{DASHBOARD_PORT}"


def notify(title: str, message: str) -> None:
    try:
        rumps.notification(title, "", message)
    except Exception:
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
                check=False,
            )
        except Exception:
            pass


def load_json(path: Path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def pending_count() -> int:
    posts = load_json(QUEUE_FILE, {}).get("posts", [])
    return sum(1 for p in posts if p.get("status") == "pending")


def approved_count() -> int:
    posts = load_json(QUEUE_FILE, {}).get("posts", [])
    return sum(1 for p in posts if p.get("status") == "approved")


def total_queue_count() -> int:
    return len(load_json(QUEUE_FILE, {}).get("posts", []))


def published_count() -> int:
    return len(load_json(PUBLISHED_FILE, {}).get("articles", []))


def last_run_label() -> str:
    candidates = []
    for name, period in (("pipeline-morning.log", "morning"), ("pipeline-evening.log", "evening")):
        path = LOGS_DIR / name
        if path.exists():
            candidates.append((path.stat().st_mtime, period))
    if not candidates:
        return "never"
    ts, period = max(candidates)
    return f"{datetime.fromtimestamp(ts).strftime('%H:%M, %d %b')} ({period})"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pids_on_port(port: int):
    try:
        out = subprocess.check_output(["lsof", "-ti", f":{port}"], stderr=subprocess.DEVNULL)
        return [int(pid) for pid in out.decode().split()]
    except Exception:
        return []


class IWFMenuBarApp(rumps.App):
    def __init__(self):
        super().__init__("IWF", quit_button=None)

        self.dashboard_proc = None
        self.dashboard_running = False
        self.pipeline_running = False
        self._last_approved_count = None

        self.status_last_run = rumps.MenuItem("Pipeline: Last run —")
        self.status_queue = rumps.MenuItem("Queue: — stories pending")
        self.status_site = rumps.MenuItem("Site: — articles published")

        self.menu = [
            self.status_last_run,
            self.status_queue,
            self.status_site,
            None,
            rumps.MenuItem("Run pipeline now", callback=self.run_pipeline),
            rumps.MenuItem("Open dashboard", callback=self.open_dashboard),
            rumps.MenuItem("Stop dashboard", callback=self.stop_dashboard),
            None,
            rumps.MenuItem("Open VS Code", callback=self.open_vscode),
            rumps.MenuItem("View logs", callback=self.view_logs),
            None,
            rumps.MenuItem("Quit IWF", callback=self.quit_app),
        ]

    # -- status -----------------------------------------------------------
    def update_title(self):
        if self.pipeline_running:
            self.title = "IWF ⟳"
        elif self.dashboard_running:
            self.title = "IWF ●"
        else:
            self.title = "IWF"

    def refresh_status(self):
        self.status_last_run.title = f"Pipeline: Last run {last_run_label()}"
        self.status_queue.title = f"Queue: {pending_count()} stories pending"
        self.status_site.title = f"Site: {published_count()} articles published"
        self.check_approvals()

    def check_approvals(self):
        current = approved_count()
        if self._last_approved_count is not None and current > self._last_approved_count:
            delta = current - self._last_approved_count
            notify("IWF", f"{delta} new stor{'y' if delta == 1 else 'ies'} approved via Telegram")
        self._last_approved_count = current

    @rumps.timer(60)
    def _on_timer(self, _sender):
        self.refresh_status()

    # -- pipeline -----------------------------------------------------------
    def run_pipeline(self, _sender=None):
        if self.pipeline_running:
            return
        self.pipeline_running = True
        self.update_title()
        threading.Thread(target=self._run_pipeline_thread, daemon=True).start()

    def _run_pipeline_thread(self):
        before = total_queue_count()
        try:
            subprocess.run([sys.executable, "drafter.py"], cwd=str(REPO))
        except Exception as e:
            notify("IWF Pipeline", f"Failed to run drafter.py: {e}")
        after = total_queue_count()
        found = max(after - before, 0)
        self.pipeline_running = False
        self.update_title()
        self.refresh_status()
        notify("IWF Pipeline finished", f"{found} new stor{'y' if found == 1 else 'ies'} found")

    # -- dashboard -----------------------------------------------------------
    def ensure_dashboard_running(self):
        if port_in_use(DASHBOARD_PORT):
            self.dashboard_running = True
            self.update_title()
            return
        self.dashboard_proc = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=str(REPO),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(20):
            if port_in_use(DASHBOARD_PORT):
                break
            time.sleep(0.25)
        self.dashboard_running = True
        self.update_title()

    def open_dashboard(self, _sender=None):
        self.ensure_dashboard_running()
        webbrowser.open(DASHBOARD_URL)

    def stop_dashboard(self, _sender=None):
        if self.dashboard_proc is not None:
            try:
                self.dashboard_proc.terminate()
                self.dashboard_proc.wait(timeout=5)
            except Exception:
                pass
            self.dashboard_proc = None
        for pid in pids_on_port(DASHBOARD_PORT):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.dashboard_running = False
        self.update_title()

    # -- misc -----------------------------------------------------------
    def open_vscode(self, _sender=None):
        subprocess.run(["open", "-a", "Visual Studio Code", str(REPO)])

    def view_logs(self, _sender=None):
        subprocess.run(["open", "-a", "Console", str(LOGS_DIR / "pipeline-morning.log")])

    def quit_app(self, _sender=None):
        if self.dashboard_running:
            self.stop_dashboard()
        rumps.quit_application()


def main():
    app = IWFMenuBarApp()
    app.refresh_status()
    app.open_dashboard()  # auto-start the dashboard + open the browser on launch
    app.run()


if __name__ == "__main__":
    main()
