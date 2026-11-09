"""Runtime configuration for TUI-OS.

Resolution order (later wins):

1. Built-in defaults (below).
2. TOML file at ``$TUI_OS_CONFIG`` or ``~/.config/tui-os/config.toml``.
3. Override TOML at ``$TUI_OS_CONFIG_OVERRIDE``, if it exists. The APK points
   this at its external files dir, so ``adb push config.toml`` changes
   settings on a release build without rebuilding.
4. Environment variables (``OLARES_HOST``, ``LLM_URL``, ...).
5. Command-line flags (see ``tui_os.__main__``).

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
    #: LLM wire protocol: "ollama" (/api/generate) or "openai"
    #: (/v1/chat/completions: llama.cpp server, vLLM, LM Studio, Ollama's /v1).
    llm_api: str = "ollama"
    #: Base URL of the LLM server; empty = ollama_url, else http://HOST:11434.
    #: For "openai" a trailing /v1 is optional.
    llm_url: str = ""
    #: Model name sent to the server. Empty: ollama_model for "ollama"; for
    #: "openai" the server's loaded model (llama.cpp serves one model and
    #: accepts any name; on Olares its alias is the app's MODEL_NAME).
    llm_model: str = ""
    #: Sent as "Authorization: Bearer <key>" when set (OpenAI-style servers).
    llm_api_key: str = ""
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
    #: Tab shown at start: shell, mail, calendar, browser, k9s or terminal
    #: (the LLM terminal).
    start_tab: str = "shell"
    #: Shell tab: SSH target (empty host = olares_host).
    shell_host: str = ""
    shell_user: str = "olares"
    shell_port: int = 22
    #: Run this instead of the login shell, e.g. "tmux new -A -s main".
    shell_command: str = ""
    #: Connect as soon as the Shell tab is first shown.
    shell_autoconnect: bool = True
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
    #: APK: log every key / text-input event to logcat (tag "python") to
    #: debug keyboards and input methods.
    debug_input: bool = False
    #: Mail tab (Gmail over IMAP/SMTP). Use a Google *app password*
    #: (myaccount.google.com/apppasswords), not your account password. Both
    #: empty = the Mail tab shows the built-in demo mailbox.
    mail_address: str = ""
    mail_password: str = ""
    #: Shown in the From: header of mail you send; empty = just the address.
    mail_name: str = ""
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    #: Port 465 = implicit TLS; any other port uses STARTTLS (e.g. 587).
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    #: Newest N messages of the INBOX loaded per refresh.
    mail_fetch_count: int = 40
    #: Seconds between automatic INBOX refreshes; 0 = only on "g".
    mail_refresh_interval: float = 300.0
    #: Calendar tab: secret iCal URL (Google Calendar > Settings > your
    #: calendar > "Secret address in iCal format"). Read-only. Empty = demo.
    ical_url: str = ""
    #: Seconds between automatic calendar refreshes; 0 = only on "g".
    calendar_refresh_interval: float = 900.0
    #: Vaultwarden / Bitwarden server holding the mail password and iCal URL
    #: (e.g. "https://vaultwarden.you.olares.com"). With vault_url and
    #: vault_email set, the TUI asks for the master password at launch and
    #: reads the item named vault_item; its values override this file.
    vault_url: str = ""
    vault_email: str = ""
    vault_item: str = "TUI-OS"
    #: Optional personal API key (web vault > Account settings > Security >
    #: Keys > View API key). Lets accounts with two-step login unlock
    #: without a code. The master password is still needed to decrypt.
    vault_client_id: str = ""
    vault_client_secret: str = ""
    queries: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_QUERIES))

    # ------------------------------------------------------------------ derived
    @property
    def vault_configured(self) -> bool:
        return bool(self.vault_url and self.vault_email)

    def with_secrets(self, values: dict[str, Any]) -> "Settings":
        """Apply values read from the vault (any setting except vault_* and queries)."""
        allowed = {f.name for f in fields(self)
                   if f.name != "queries" and not f.name.startswith("vault_")}
        return self._merge({k: v for k, v in values.items() if k in allowed and v != ""})

    @property
    def mail_configured(self) -> bool:
        return bool(self.mail_address and self.mail_password)

    @property
    def resolved_ollama_url(self) -> str:
        return (self.ollama_url or f"http://{self.olares_host}:11434").rstrip("/")

    @property
    def resolved_llm_api(self) -> str:
        api = self.llm_api.strip().lower()
        if api not in ("ollama", "openai"):
            raise ValueError(f'llm_api must be "ollama" or "openai", not {self.llm_api!r}')
        return api

    @property
    def resolved_llm_url(self) -> str:
        return (self.llm_url or self.resolved_ollama_url).rstrip("/")

    @property
    def resolved_llm_model(self) -> str:
        # ollama_model's default ("llama3.1:8b") means nothing to an
        # OpenAI-style server, so only Ollama falls back to it.
        if self.resolved_llm_api == "openai":
            return self.llm_model
        return self.llm_model or self.ollama_model

    @property
    def resolved_prometheus_url(self) -> str:
        return (self.prometheus_url or f"http://{self.olares_host}:9090").rstrip("/")

    @property
    def data_dir(self) -> Path:
        """Keys and known_hosts live next to the config file (app storage on Android)."""
        env = os.environ.get("TUI_OS_DATA")
        return Path(env).expanduser() if env else default_config_path().parent

    @property
    def resolved_shell_host(self) -> str:
        return self.shell_host or self.olares_host

    @property
    def resolved_k9s_host(self) -> str:
        return self.k9s_host or self.olares_host

    @property
    def resolved_ssh_key_path(self) -> Path:
        return Path(self.ssh_key_path).expanduser() if self.ssh_key_path else self.data_dir / "id_ed25519"

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: Path | None = None, **overrides: Any) -> "Settings":
        LOAD_WARNINGS.clear()
        settings = cls()
        settings = settings._merge(_read_toml(path or default_config_path()))
        override = os.environ.get("TUI_OS_CONFIG_OVERRIDE")
        if override:
            # The on-device override is hand-edited: a typo there must not
            # stop the app. Skip the file and say why at start instead.
            try:
                settings = settings._merge(_read_toml(Path(override).expanduser()))
            except ValueError as exc:
                LOAD_WARNINGS.append(f"{exc}. Ignored that file; using the bundled settings.")
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


#: Problems found while loading config files; the app shows them at start.
LOAD_WARNINGS: list[str] = []


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        # Shown on the crash screen; name the file so a bad `adb push` is obvious.
        raise ValueError(f"invalid TOML in {path}: {exc}") from exc
    return _hoist_misplaced(data, path)


def _hoist_misplaced(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """Rescue settings written below the ``[queries]`` table.

    In TOML every ``key = value`` after a ``[table]`` header belongs to that
    table, so a line like ``ical_url = "..."`` appended at the end of the
    file silently became a PromQL query. Move known setting names back to
    the top level (unless the top level already sets them) and say so.
    """
    queries = data.get("queries")
    if not isinstance(queries, dict):
        return data
    settings_keys = {f.name for f in fields(Settings)} - {"queries"}
    moved = [k for k in queries if k in settings_keys and k not in DEFAULT_QUERIES]
    if not moved:
        return data
    data = dict(data)
    queries = dict(queries)
    for key in moved:
        value = queries.pop(key)
        data.setdefault(key, value)
    data["queries"] = queries
    LOAD_WARNINGS.append(
        f"{path.name}: {', '.join(moved)} sit below [queries]; used them as settings. "
        "Move them above the [queries] line."
    )
    return data


_ENV_MAP = {
    "OLARES_HOST": "olares_host",
    "OLLAMA_URL": "ollama_url",
    "OLLAMA_MODEL": "ollama_model",
    "OLLAMA_SYSTEM_PROMPT": "ollama_system_prompt",
    "LLM_API": "llm_api",
    "LLM_URL": "llm_url",
    "LLM_MODEL": "llm_model",
    "LLM_API_KEY": "llm_api_key",
    "PROMETHEUS_URL": "prometheus_url",
    "TUI_OS_TELEMETRY_INTERVAL": "telemetry_interval",
    "TUI_OS_STREAM_FLUSH": "stream_flush_interval",
    "TUI_OS_DEMO": "demo_mode",
    "TUI_OS_BROWSER_HOME": "browser_home",
    "TUI_OS_K9S_HOST": "k9s_host",
    "TUI_OS_K9S_USER": "k9s_user",
    "TUI_OS_DEBUG_INPUT": "debug_input",
    "TUI_OS_MAIL_ADDRESS": "mail_address",
    "TUI_OS_MAIL_PASSWORD": "mail_password",
    "TUI_OS_ICAL_URL": "ical_url",
    "TUI_OS_START_TAB": "start_tab",
    "TUI_OS_SHELL_HOST": "shell_host",
    "TUI_OS_SHELL_USER": "shell_user",
    "TUI_OS_SHELL_COMMAND": "shell_command",
}


def _read_env() -> dict[str, Any]:
    return {attr: os.environ[env] for env, attr in _ENV_MAP.items() if os.environ.get(env)}
