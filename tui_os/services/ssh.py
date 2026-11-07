"""SSH terminal sessions to the Olares One (used by the K9s tab).

Built on ``asyncssh`` (pure Python on top of ``cryptography``, which
python-for-android compiles for arm64), so the same code runs on a desktop
and inside the APK, on the app's asyncio loop with no threads.

Security model, sized for a Tailscale tailnet:

* **Host keys:** trust on first use. The first connection records the
  server key in ``known_hosts`` under the app's data dir. Later connections
  must match it, or they fail with :class:`HostKeyChanged`.
* **Client key:** an Ed25519 key is generated on the device on first use.
  Its public half is shown in the K9s tab for ``~/.ssh/authorized_keys``.
  Tailscale SSH (tailnet identity, no keys) also works, because asyncssh
  falls back to ``none`` auth when the server accepts it.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import asyncssh

REMOTE_CONFIG_DIR = ".config/tui-os/k9s"


class HostKeyChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class SshTarget:
    host: str
    username: str
    port: int = 22
    command: str = "k9s"
    kubeconfig: str = ""
    refresh_rate: int = 5
    key_path: Path = Path("id_ed25519")
    known_hosts_path: Path = Path("known_hosts")

    @property
    def label(self) -> str:
        return f"{self.username}@{self.host}" + ("" if self.port == 22 else f":{self.port}")

    @property
    def host_token(self) -> str:
        return self.host if self.port == 22 else f"[{self.host}]:{self.port}"


def ensure_client_key(path: Path) -> str:
    """Create the device's Ed25519 key if needed; return the public key line."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = asyncssh.generate_private_key("ssh-ed25519", comment="tui-os@boox")
        key.write_private_key(str(path))
        os.chmod(path, 0o600)
        key.write_public_key(str(path) + ".pub")
    return Path(str(path) + ".pub").read_text().strip()


def known_host(target: SshTarget) -> bool:
    try:
        lines = target.known_hosts_path.read_text().splitlines()
    except FileNotFoundError:
        return False
    return any(line.split(" ", 1)[0] == target.host_token for line in lines if line.strip())


def remember_host(target: SshTarget, key: asyncssh.SSHKey) -> None:
    target.known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{target.host_token} {key.export_public_key('openssh').decode().strip()}\n"
    with target.known_hosts_path.open("a") as fh:
        fh.write(line)


def k9s_skin() -> str:
    return resources.files("tui_os.k9s").joinpath("skins/eink.yaml").read_text()


def k9s_config(refresh_rate: int) -> str:
    return resources.files("tui_os.k9s").joinpath("config.yaml").read_text().replace(
        "refreshRate: 5", f"refreshRate: {max(2, int(refresh_rate))}"
    )


def remote_command(target: SshTarget) -> str:
    """Run k9s with our skin from a dedicated config dir (user config untouched)."""
    env = [f'K9S_CONFIG_DIR="$HOME/{REMOTE_CONFIG_DIR}"', "COLORTERM=truecolor"]
    if target.kubeconfig:
        env.append(f"KUBECONFIG={target.kubeconfig}")
    return (
        f"command -v {target.command.split()[0]} >/dev/null 2>&1 || "
        f"{{ echo 'k9s is not installed on this host (see README: K9s tab)'; exit 127; }}; "
        f"exec env {' '.join(env)} {target.command}"
    )


class SshTerminalSession:
    """One interactive PTY session running k9s on the target."""

    def __init__(
        self,
        target: SshTarget,
        on_output: Callable[[str], None],
        on_exit: Callable[[int | None, str | None], None],
    ) -> None:
        self.target = target
        self._on_output = on_output
        self._on_exit = on_exit
        self._conn: asyncssh.SSHClientConnection | None = None
        self._proc: asyncssh.SSHClientProcess | None = None
        self._reader: asyncio.Task | None = None

    async def _connect(self) -> asyncssh.SSHClientConnection:
        t = self.target
        trusted = known_host(t)
        keys = [str(t.key_path)] if t.key_path.exists() else []
        try:
            conn = await asyncssh.connect(
                t.host,
                port=t.port,
                username=t.username,
                client_keys=keys,
                known_hosts=str(t.known_hosts_path) if trusted else None,
                agent_path=None,
                connect_timeout=10,
            )
        except asyncssh.HostKeyNotVerifiable as exc:
            raise HostKeyChanged(
                f"host key for {t.host_token} does not match known_hosts ({exc}). "
                f"If the server was reinstalled, delete {t.known_hosts_path}."
            ) from exc
        if not trusted:
            key = conn.get_server_host_key()
            if key is not None:
                remember_host(t, key)
        return conn

    async def _install_skin(self, conn: asyncssh.SSHClientConnection) -> None:
        d = f'"$HOME/{REMOTE_CONFIG_DIR}"'
        files = {
            "skins/eink.yaml": k9s_skin(),
            "skin.yml": k9s_skin(),  # k9s < 0.29 reads a global skin file
            "config.yaml": k9s_config(self.target.refresh_rate),
        }
        for name, body in files.items():
            await conn.run(f"mkdir -p {d}/skins && cat > {d}/{name}", input=body, check=True)

    async def start(self, cols: int, rows: int) -> None:
        self._conn = await self._connect()
        await self._install_skin(self._conn)
        self._proc = await self._conn.create_process(
            remote_command(self.target),
            term_type="xterm-256color",
            term_size=(cols, rows),
            encoding="utf-8",
            errors="replace",
        )
        self._reader = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        proc = self._proc
        assert proc is not None
        error: str | None = None
        try:
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                self._on_output(chunk)
            await proc.wait_closed()
        except (asyncssh.Error, OSError) as exc:
            error = str(exc) or type(exc).__name__
        self._on_exit(proc.exit_status, error)

    def write(self, data: str) -> None:
        if self._proc is not None and not self._proc.stdin.is_closing():
            self._proc.stdin.write(data)

    def resize(self, cols: int, rows: int) -> None:
        if self._proc is not None:
            self._proc.change_terminal_size(cols, rows)

    async def close(self) -> None:
        if self._proc is not None:
            self._proc.close()
        if self._conn is not None:
            self._conn.close()
            await self._conn.wait_closed()
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
