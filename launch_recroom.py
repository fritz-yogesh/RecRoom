from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
URL = f"http://127.0.0.1:{os.environ.get('RECROOM_PORT', '8000')}/"


def main() -> int:
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=ROOT)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                print("RecRoom server stopped before it was ready.", file=sys.stderr)
                return process.returncode or 1
            try:
                with urllib.request.urlopen(URL, timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        else:
            print(f"RecRoom did not become ready. Open {URL} after checking the server output.", file=sys.stderr)
            process.terminate()
            process.wait()
            return 1

        print(f"Opening RecRoom at {URL}")
        if not webbrowser.open(URL):
            print(f"Open this address in your browser: {URL}")
        return process.wait()
    except KeyboardInterrupt:
        print("\nStopping RecRoom.")
        process.terminate()
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
