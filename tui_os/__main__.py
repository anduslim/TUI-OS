"""Entry point: ``python -m tui_os`` or the ``tui-os`` console script."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .config import Settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="tui-os", description="E-ink TUI workspace for Olares One")
    p.add_argument("--host", dest="olares_host", help="Olares One host/IP (default 127.0.0.1)")
    p.add_argument("--ollama-url", help="override Ollama base URL (default http://HOST:11434)")
    p.add_argument("--model", dest="ollama_model", help="Ollama model name")
    p.add_argument("--prometheus-url", help="override Prometheus URL (default http://HOST:9090)")
    p.add_argument("--interval", dest="telemetry_interval", type=float, help="telemetry poll seconds")
    p.add_argument(
        "--flush", dest="stream_flush_interval", type=float,
        help="seconds between LLM stream repaints (0 = every token)",
    )
    p.add_argument("--demo", dest="demo_mode", action="store_true", default=None,
                   help="use synthetic telemetry")
    p.add_argument("--hide-telemetry", dest="telemetry_visible", action="store_false", default=None,
                   help="start with the telemetry panel collapsed")
    p.add_argument("--config", type=Path, help="path to config.toml")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = vars(parse_args(argv))
    config = args.pop("config")
    settings = Settings.load(config, **args)

    from .app import TuiOS  # imported late so --help/--version stay instant

    TuiOS(settings).run()


if __name__ == "__main__":
    main()
