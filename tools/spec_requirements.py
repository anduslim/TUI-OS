"""Print buildozer.spec's pip-installable requirements, one per line.

python-for-android builds the recipe packages (python3, kivy, ...) itself and
pip-installs everything else. CI feeds this list to `pip install --dry-run`
so a dependency conflict fails in seconds rather than deep in an APK build.
"""

from __future__ import annotations

import configparser
import sys
from pathlib import Path

RECIPES = {"hostpython3", "python3", "kivy", "openssl", "cffi"}
#: Recipes whose Python package other requirements depend on: resolve against
#: the version python-for-android builds, not the latest on PyPI.
RECIPE_VERSIONS = {"cryptography": "46.0.3"}


def requirements(spec: Path) -> list[str]:
    cfg = configparser.ConfigParser()
    cfg.read(spec)
    reqs = [r.strip() for r in cfg["app"]["requirements"].split(",")]
    out = []
    for r in reqs:
        name = r.split("==")[0]
        if not r or name in RECIPES:
            continue
        out.append(f"{name}=={RECIPE_VERSIONS[name]}" if name in RECIPE_VERSIONS else r)
    return out


if __name__ == "__main__":
    print("\n".join(requirements(Path(sys.argv[1] if len(sys.argv) > 1 else "buildozer.spec"))))
