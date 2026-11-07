"""Runtime configuration for TUI-OS.

Resolution order (later wins):

1. Built-in defaults (below).
2. TOML file at ``$TUI_OS_CONFIG`` or ``~/.config/tui-os/config.toml``.
3. Environment variables (``OLARES_HOST``, ``OLLAMA_URL``, ...).
4. Command-line flags (see ``tui_os.__main__``).

Example ``config.toml``::

    olares_host = "192.168.1.50"
    ollama_model = "qwen2.5-coder:14b"
    telemetry_interval = 5

    [queries]
    cpu_temp = 'max(node_hwmon_temp_celsius{chip=~".*k10temp.*"})'
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

# PromQL defaults target node-exporter + NVIDIA DCGM exporter, which is what a
# stock Olares GPU node ships. Every query must reduce to a single scalar
# sample (hence the outer sum()/max()). Override per-key in config.toml.
_NET_IFACE_FILTER = 'device!~"lo|veth.*|docker.*|br-.*|cali.*|flannel.*|cni.*|tunl.*|kube.*"'

DEFAULT_QUERIES: dict[str, str] = {
    "cpu_temp": "max(node_hwmon_temp_celsius)",
    "gpu_temp": "max(DCGM_FI_DEV_GPU_TEMP)",
    # DCGM reports framebuffer memory in MiB.
    "vram_used": "sum(DCGM_FI_DEV_FB_USED) * 1048576",
    "vram_total": "sum(DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE) * 1048576",
    "net_rx": f"sum(rate(node_network_receive_bytes_total{{{_NET_IFACE_FILTER}}}[1m]))",
    "net_tx": f"sum(rate(node_network_transmit_bytes_total{{{_NET_IFACE_FILTER}}}[1m]))",
}


def default_config_path() -> Path:
    env = os.environ.get("TUI_OS_CONFIG")
    if env:
        return Path(env).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "tui-os" / "config.toml"


@dataclass(frozen=True)
class Settings:
    olares_host: str = "127.0.0.1"
    #: Empty means "derive from olares_host".
    ollama_url: str = ""
    ollama_model: str = "llama3.1:8b"
    ollama_system_prompt: str = ""
    #: Empty means "derive from olares_host".
    prometheus_url: str = ""
    #: Seconds between telemetry polls. Keep >= 2s on E-ink.
    telemetry_interval: float = 5.0
    #: Seconds between repaints of a streaming LLM answer. Tokens are applied
    #: to the buffer as they arrive; the screen is repainted at most this
    #: often so the E-ink panel is not hammered with partial refreshes.
    #: Set to 0 to repaint on every token (LCD/OLED displays).
    stream_flush_interval: float = 0.35
    #: Use a synthetic telemetry source instead of Prometheus.
    demo_mode: bool = False
    #: Start with the telemetry sidebar visible.
    telemetry_visible: bool = True
    browser_home: str = "about:home"
    #: K9s tab: SSH target running k9s (empty host = olares_host, which can
    #: be a Tailscale MagicDNS name such as "olares-one").
    k9s_host: str = ""
    k9s_user: str = "olares"
    k9s_port: int = 22
    k9s_command: str = "k9s"
    k9s_kubeconfig: str = ""
    #: Seconds between k9s refreshes (E-ink: keep it slow).
    k9s_refresh_rate: int = 5
    #: Connect as soon as the K9s tab is first opened.
    k9s_autoconnect: bool = True
    #: Private key path; empty = <data dir>/id_ed25519 (generated on first use).
    ssh_key_path: str = ""
    queries: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_QUERIES))

    # ------------------------------------------------------------------ derived
    @property
    def resolved_ollama_url(self) -> str:
        return (self.ollama_url or f"http://{self.olares_host}:11434").rstrip("/")

    @property
    def resolved_prometheus_url(self) -> str:
        return (self.prometheus_url or f"http://{self.olares_host}:9090").rstrip("/")

    @property
    def data_dir(self) -> Path:
        """Keys and known_hosts live next to the config file (app storage on Android)."""
        env = os.environ.get("TUI_OS_DATA")
        return Path(env).expanduser() if env else default_config_path().parent

    @property
    def resolved_k9s_host(self) -> str:
        return self.k9s_host or self.olares_host

    @property
    def resolved_ssh_key_path(self) -> Path:
        return Path(self.ssh_key_path).expanduser() if self.ssh_key_path else self.data_dir / "id_ed25519"

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: Path | None = None, **overrides: Any) -> "Settings":
        settings = cls()
        settings = settings._merge(_read_toml(path or default_config_path()))
        settings = settings._merge(_read_env())
        settings = settings._merge({k: v for k, v in overrides.items() if v is not None})
        return settings

    def _merge(self, data: dict[str, Any]) -> "Settings":
        known = {f.name: f for f in fields(self)}
        changes: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
            if key == "queries":
                merged = dict(self.queries)
                merged.update({str(k): str(v) for k, v in dict(value).items()})
                changes[key] = merged
                continue
            current = getattr(self, key)
            changes[key] = _coerce(value, type(current))
        return replace(self, **changes)


def _coerce(value: Any, target: type) -> Any:
    if target is bool and isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if target in (int, float, str, bool):
        return target(value)
    return value


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}


_ENV_MAP = {
    "OLARES_HOST": "olares_host",
    "OLLAMA_URL": "ollama_url",
    "OLLAMA_MODEL": "ollama_model",
    "OLLAMA_SYSTEM_PROMPT": "ollama_system_prompt",
    "PROMETHEUS_URL": "prometheus_url",
    "TUI_OS_TELEMETRY_INTERVAL": "telemetry_interval",
    "TUI_OS_STREAM_FLUSH": "stream_flush_interval",
    "TUI_OS_DEMO": "demo_mode",
    "TUI_OS_BROWSER_HOME": "browser_home",
    "TUI_OS_K9S_HOST": "k9s_host",
    "TUI_OS_K9S_USER": "k9s_user",
}


def _read_env() -> dict[str, Any]:
    return {attr: os.environ[env] for env, attr in _ENV_MAP.items() if os.environ.get(env)}
