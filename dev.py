"""
OpenAVC development launcher.

Builds the Programmer UI, starts the PJLink simulator,
and starts the server — all in one command, one terminal.

Usage:
    python dev.py
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROGRAMMER_DIR = ROOT / "web" / "programmer"


def build_programmer_ui():
    """Build the React Programmer UI into static files."""
    print("[dev] Building Programmer UI...")
    node_modules = PROGRAMMER_DIR / "node_modules"
    if not node_modules.exists():
        print("[dev]   Installing npm dependencies...")
        subprocess.run(
            ["npm", "install"],
            cwd=str(PROGRAMMER_DIR),
            check=True,
            shell=(sys.platform == "win32"),
        )

    subprocess.run(
        ["npm", "run", "build"],
        cwd=str(PROGRAMMER_DIR),
        check=True,
        shell=(sys.platform == "win32"),
    )
    print("[dev] Programmer UI built successfully.")


def main():
    # Step 1: Always build programmer UI (takes ~2s)
    build_programmer_ui()

    # Step 2: Start PJLink simulator in background
    print("[dev] Starting PJLink simulator...")
    simulator = subprocess.Popen(
        [sys.executable, "-m", "tests.simulators.pjlink_simulator"],
        cwd=str(ROOT),
    )
    time.sleep(0.5)

    # Step 3: Start the server (foreground)
    print("[dev] Starting OpenAVC server...")
    print("[dev]")
    print("[dev]   Panel:      http://localhost:8080/panel")
    print("[dev]   UI Builder: http://localhost:8080/programmer")
    print("[dev]   API:        http://localhost:8080/api")
    print("[dev]")
    print("[dev] Press Ctrl+C to stop everything.")
    print()

    try:
        server = subprocess.Popen(
            [sys.executable, "-m", "server.main"],
            cwd=str(ROOT),
        )
        server.wait()
    except KeyboardInterrupt:
        print("\n[dev] Shutting down...")
    finally:
        simulator.terminate()
        try:
            simulator.wait(timeout=3)
        except subprocess.TimeoutExpired:
            simulator.kill()
        print("[dev] Stopped.")


if __name__ == "__main__":
    main()
