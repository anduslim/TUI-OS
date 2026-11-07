"""Type-check the APK's Java helper against Android API stubs (no SDK needed)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sources = sorted(str(p) for p in (ROOT / "tools" / "android_stubs").rglob("*.java"))
    sources += sorted(str(p) for p in (ROOT / "android" / "src").rglob("*.java"))
    with tempfile.TemporaryDirectory() as out:
        result = subprocess.run(["javac", "-Xlint:all", "-Werror", "-d", out, *sources])
    if result.returncode == 0:
        print(f"javac OK ({len(sources)} files)")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
