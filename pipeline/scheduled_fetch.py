"""Run one RSS fetch and append readable output to the local task log."""
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / ".logs"
LOG_FILE = LOG_DIR / "rss-fetch.log"


def main() -> int:
    LOG_DIR.mkdir(exist_ok=True)
    if LOG_FILE.exists():
        existing = LOG_FILE.read_bytes()
        if b"\0" in existing[:2048]:
            LOG_FILE.replace(LOG_DIR / "rss-fetch-legacy.log")
        elif LOG_FILE.stat().st_size > 2 * 1024 * 1024:
            LOG_FILE.replace(LOG_DIR / "rss-fetch.log.1")

    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(f"[{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}] Starting RSS fetch.\n")
        log.flush()
        result = subprocess.run(
            [sys.executable, "-m", "pipeline.fetch_rss"],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
            env=os.environ.copy(),
        )
        log.write(f"RSS fetch exited with code {result.returncode}.\n\n")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
