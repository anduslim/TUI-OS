"""Read secrets from a Vaultwarden / Bitwarden server.

The TUI keeps only the server URL and your email in ``config.toml``. At
launch it asks for the master password, logs in, downloads the vault,
decrypts ONE item and keeps the values in memory. Nothing decrypted is
written to disk.

Item layout (one Login item, default name "TUI-OS"):

* username -> ``mail_address``
* password -> ``mail_password``
* custom fields whose name is a ``config.toml`` key (``ical_url``,
  ``mail_name``, ``imap_host``, ...) -> that key.

Protocol (Bitwarden's, as served by Vaultwarden):

1. ``POST /identity/accounts/prelogin`` -> KDF type and parameters.
2. master key = PBKDF2-SHA256(password, email) or Argon2id(password, SHA256(email)).
3. ``POST /identity/connect/token`` with a hash of the master key (or an
   API key with ``client_credentials``, which skips 2FA prompts).
4. ``GET /api/sync``; the account key (``profile.key``) is decrypted with the
   HKDF-stretched master key, then each item with that key (or its own key).

Only personal items are read; items owned by an organization are skipped.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

KDF_PBKDF2 = 0
KDF_ARGON2ID = 1
#: Bitwarden DeviceType "LinuxDesktop" (known to every Vaultwarden version).
DEVICE_TYPE = 8


class VaultError(Exception):
    """Login refused, item missing, bad data: shown to the user as-is."""


class TwoFactorRequired(VaultError):
    def __init__(self, providers: list[int]) -> None:
        super().__init__("This account needs a two-step login code.")
        self.providers = providers


# ------------------------------------------------------------------ crypto
def _crypto():
    # Imported lazily: the APK only needs cryptography when a vault is set.
    from cryptography.hazmat.primitives import hashes, padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

    return hashes, padding, Cipher, algorithms, modes, HKDFExpand


def make_master_key(password: str, email: str, kdf: int, iterations: int,
                    memory_mib: int | None = None, parallelism: int | None = None) -> bytes:
    email = email.strip().lower()
    if kdf == KDF_PBKDF2:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), email.encode(), iterations, 32)
    if kdf == KDF_ARGON2ID:
        try:
            from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
        except ImportError as exc:  # pragma: no cover - cryptography < 44
            raise VaultError("Argon2id vaults need cryptography 44+; switch the account KDF "
                             "to PBKDF2 in the web vault (Settings > Security > Keys).") from exc
        from cryptography.exceptions import UnsupportedAlgorithm

        try:
            return Argon2id(
                salt=hashlib.sha256(email.encode()).digest(), length=32, iterations=iterations,
                lanes=parallelism or 4, memory_cost=(memory_mib or 64) * 1024,
            ).derive(password.encode())
        except UnsupportedAlgorithm as exc:  # OpenSSL older than 3.2 in this build
            raise VaultError("This build's OpenSSL has no Argon2id; switch the account KDF to "
                             "PBKDF2 in the web vault (Settings > Security > Keys).") from exc
    raise VaultError(f"Unsupported KDF type {kdf}.")


def master_password_hash(master_key: bytes, password: str) -> str:
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", master_key, password.encode(), 1, 32)).decode()


def stretch(master_key: bytes) -> bytes:
    """64-byte enc+mac key from the 32-byte master key (HKDF-Expand, SHA-256)."""
    hashes, _, _, _, _, HKDFExpand = _crypto()

    def expand(info: bytes) -> bytes:
        return HKDFExpand(algorithm=hashes.SHA256(), length=32, info=info).derive(master_key)

    return expand(b"enc") + expand(b"mac")


def decrypt(enc_string: str, key: bytes) -> bytes:
    """Decrypt a type-2 EncString ``2.iv|data|mac`` with a 64-byte key."""
    _, padding, Cipher, algorithms, modes, _ = _crypto()
    head, _, rest = enc_string.partition(".")
    if head != "2" or not rest:
        raise VaultError(f"Unsupported encryption type {head!r} (only AES-CBC-256 + HMAC).")
    try:
        iv, data, mac = (base64.b64decode(p) for p in rest.split("|"))
    except ValueError as exc:
        raise VaultError("Malformed encrypted value.") from exc
    enc_key, mac_key = key[:32], key[32:]
    if not hmac.compare_digest(hmac.new(mac_key, iv + data, hashlib.sha256).digest(), mac):
        raise VaultError("Wrong master password (vault key did not verify).")
    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def decrypt_str(enc_string: str | None, key: bytes) -> str:
    return decrypt(enc_string, key).decode() if enc_string else ""


# ------------------------------------------------------------------ client
@dataclass(frozen=True)
class VaultConfig:
    url: str
    email: str
    item: str = "TUI-OS"
    client_id: str = ""
    client_secret: str = ""
    #: Remembered so the server sees one device, not a new one per launch.
    device_id_path: Path | None = None


class VaultClient:
    def __init__(self, config: VaultConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self.base = config.url.rstrip("/")
        self._transport = transport

    def _device_id(self) -> str:
        path = self.config.device_id_path
        if path is not None:
            try:
                value = path.read_text().strip()
                if value:
                    return value
            except OSError:
                pass
        value = str(uuid.uuid4())
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
            except OSError:
                pass
        return value

    async def fetch_item(self, password: str, totp: str = "") -> dict[str, str]:
        """Log in, decrypt the configured item, return ``{config key: value}``."""
        cfg = self.config
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=30.0,
                                         follow_redirects=True) as http:
                pre = await http.post(f"{self.base}/identity/accounts/prelogin", json={"email": cfg.email})
                if pre.status_code == 404:  # Vaultwarden before 1.30
                    pre = await http.post(f"{self.base}/api/accounts/prelogin", json={"email": cfg.email})
                _raise_for(pre, "prelogin")
                p = _lower_keys(pre.json())
                kdf = int(p.get("kdf", KDF_PBKDF2))
                master_key = make_master_key(
                    password, cfg.email, kdf, int(p.get("kdfiterations") or 600000),
                    p.get("kdfmemory"), p.get("kdfparallelism"),
                )
                form = {
                    "scope": "api offline_access", "client_id": "cli",
                    "deviceType": str(DEVICE_TYPE), "deviceIdentifier": self._device_id(),
                    "deviceName": "tui-os",
                }
                if cfg.client_id and cfg.client_secret:
                    form.update(grant_type="client_credentials", scope="api",
                                client_id=cfg.client_id, client_secret=cfg.client_secret)
                else:
                    form.update(grant_type="password", username=cfg.email,
                                password=master_password_hash(master_key, password))
                    if totp:
                        form.update(twoFactorToken=totp.strip().replace(" ", ""),
                                    twoFactorProvider="0", twoFactorRemember="0")
                headers = {"Auth-Email": base64.urlsafe_b64encode(cfg.email.encode()).decode().rstrip("=")}
                tok = await http.post(f"{self.base}/identity/connect/token", data=form, headers=headers)
                if tok.status_code == 400:
                    body = _lower_keys(_json(tok))
                    if body.get("twofactorproviders") is not None:
                        raise TwoFactorRequired([int(x) for x in body["twofactorproviders"]])
                    msg = (_lower_keys(body.get("errormodel") or {}).get("message")
                           or body.get("error_description") or "login refused")
                    raise VaultError(f"Vault login failed: {msg}")
                _raise_for(tok, "login")
                access = _lower_keys(tok.json())["access_token"]
                sync = await http.get(f"{self.base}/api/sync", params={"excludeDomains": "true"},
                                      headers={"Authorization": f"Bearer {access}"})
                _raise_for(sync, "sync")
                data = _lower_keys(sync.json())
        except httpx.HTTPError as exc:
            raise VaultError(f"Vault {self.base} unreachable: {exc}") from exc

        user_key = decrypt(_lower_keys(data["profile"])["key"], stretch(master_key))
        return find_item(data.get("ciphers") or [], user_key, cfg.item)


def find_item(ciphers: list[dict[str, Any]], user_key: bytes, name: str) -> dict[str, str]:
    for raw in ciphers:
        c = _lower_keys(raw)
        if c.get("organizationid") or c.get("deleteddate"):
            continue
        key = decrypt(c["key"], user_key) if c.get("key") else user_key
        try:
            if decrypt_str(c.get("name"), key) != name:
                continue
        except VaultError:
            continue
        values: dict[str, str] = {}
        login = _lower_keys(c.get("login") or {})
        if login.get("username"):
            values["mail_address"] = decrypt_str(login["username"], key)
        if login.get("password"):
            values["mail_password"] = decrypt_str(login["password"], key)
        for f in c.get("fields") or []:
            f = _lower_keys(f)
            fname = decrypt_str(f.get("name"), key).strip()
            if fname:
                values[fname] = decrypt_str(f.get("value"), key)
        return values
    raise VaultError(f'No item named "{name}" in the vault (personal items only).')


def _lower_keys(d: dict[str, Any]) -> dict[str, Any]:
    # Bitwarden servers mix PascalCase and camelCase across versions.
    return {str(k).lower(): v for k, v in d.items()}


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _raise_for(resp: httpx.Response, step: str) -> None:
    if resp.status_code >= 400:
        raise VaultError(f"Vault {step} failed: HTTP {resp.status_code}")
