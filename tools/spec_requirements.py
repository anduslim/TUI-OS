"""Print buildozer.spec's pip-installable requirements, one per line.

python-for-android builds the recipe packages (python3, kivy, ...) itself and
pip-installs everything else. CI feeds this list to `pip install --dry-run`
so a dependency conflict fails in seconds rather than deep in an APK build.
"""

from __future__ import annotations

import configparser
import sys
from pathlib import Path

RECIPES = {"hostpython3", "python3", "kivy"}


def requirements(spec: Path) -> list[str]:
    cfg = configparser.ConfigParser()
    cfg.read(spec)
    reqs = [r.strip() for r in cfg["app"]["requirements"].split(",")]
    return [r for r in reqs if r and r.split("==")[0] not in RECIPES]


if __name__ == "__main__":
    print("\n".join(requirements(Path(sys.argv[1] if len(sys.argv) > 1 else "buildozer.spec"))))
