#!/usr/bin/env python3
"""
Omni Studio Pro — 24/7 Watchdog Supervisor
Ensures omni_server.py (--host 0.0.0.0 --port 8765) stays alive permanently and auto-recovers within 1s if terminated.
"""
import os
import socket
import subprocess
import sys
import time

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(REPO_DIR, "omni_server.py")
PORT = int(os.environ.get("PORT", "8765"))


def is_port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def main():
    os.chdir(REPO_DIR)
    log_path = os.path.join(REPO_DIR, "outputs", "watchdog.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    proc = None
    while True:
        try:
            if proc is not None and proc.poll() is None:
                time.sleep(2.0)
                continue
            if is_port_listening(PORT):
                time.sleep(2.0)
                continue
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting omni_server.py on 0.0.0.0:{PORT}...\n")
            err_log = open(os.path.join(REPO_DIR, "outputs", "omni_server_live.log"), "a", encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, SERVER_SCRIPT, "--host", "0.0.0.0", "--port", str(PORT)],
                cwd=REPO_DIR,
                stdout=err_log,
                stderr=err_log,
            )
            time.sleep(2.0)
        except Exception as e:
            try:
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Watchdog warning: {e}\n")
            except Exception:
                pass
            time.sleep(2.0)


if __name__ == "__main__":
    main()
