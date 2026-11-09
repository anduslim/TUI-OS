from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from tui_os.config import Settings
from tui_os.services.vault import (
    KDF_PBKDF2,
    TwoFactorRequired,
    VaultClient,
    VaultConfig,
    VaultError,
    make_master_key,
    master_password_hash,
    stretch,
)

EMAIL = "Andus@Example.com"
PASSWORD = "correct horse battery staple"
ITERATIONS = 1000  # small for test speed; real accounts use 600000


def encrypt(plain: bytes | str, key: bytes) -> str:
    if isinstance(plain, str):
        plain = plain.encode()
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plain) + padder.finalize()
    enc = Cipher(algorithms.AES(key[:32]), modes.CBC(iv)).encryptor()
    ct = enc.update(data) + enc.finalize()
    mac = hmac.new(key[32:], iv + ct, hashlib.sha256).digest()
    return "2." + "|".join(base64.b64encode(x).decode() for x in (iv, ct, mac))


def fake_server(*, require_totp: bool = False, item_name: str = "TUI-OS"):
    master = make_master_key(PASSWORD, EMAIL, KDF_PBKDF2, ITERATIONS)
    user_key = os.urandom(64)
    item_key = os.urandom(64)
    ciphers = [
        {"Type": 1, "OrganizationId": None, "Name": encrypt("Other", user_key),
         "Login": {"Username": encrypt("x", user_key)}},
        {"type": 1, "organizationId": "org-1", "name": "2.garbage|x|y"},  # skipped
        {   # per-item key, camelCase keys like newer servers
            "type": 1, "organizationId": None, "key": encrypt(item_key, user_key),
            "name": encrypt(item_name, item_key),
            "login": {"username": encrypt("andus@gmail.com", item_key),
                      "password": encrypt("abcd efgh ijkl mnop", item_key)},
            "fields": [{"name": encrypt("ical_url", item_key),
                        "value": encrypt("https://calendar.google.com/x/basic.ics", item_key)},
                       {"name": encrypt("mail_name", item_key), "value": encrypt("Andus", item_key)}],
        },
    ]
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/identity/accounts/prelogin":
            assert json.loads(request.content) == {"email": EMAIL}
            return httpx.Response(200, json={"Kdf": 0, "KdfIterations": ITERATIONS})
        if path == "/identity/connect/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            seen["form"] = form
            if form["grant_type"] == "password":
                if form["password"] != master_password_hash(master, PASSWORD):
                    return httpx.Response(400, json={"error": "invalid_grant",
                                                     "ErrorModel": {"Message": "Username or password is incorrect."}})
                if require_totp and form.get("twoFactorToken") != "123456":
                    return httpx.Response(400, json={"error": "invalid_grant", "TwoFactorProviders": [0]})
            return httpx.Response(200, json={"access_token": "tok", "Key": "unused"})
        if path == "/api/sync":
            assert request.headers["authorization"] == "Bearer tok"
            return httpx.Response(200, json={
                "Profile": {"Key": encrypt(user_key, stretch(master))}, "Ciphers": ciphers})
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


def client(transport, tmp_path, **kw) -> VaultClient:
    return VaultClient(VaultConfig(url="https://vault.example/", email=EMAIL,
                                   device_id_path=tmp_path / "dev", **kw), transport=transport)


async def test_fetch_item_decrypts_login_and_fields(tmp_path):
    transport, seen = fake_server()
    values = await client(transport, tmp_path).fetch_item(PASSWORD)
    assert values == {
        "mail_address": "andus@gmail.com", "mail_password": "abcd efgh ijkl mnop",
        "ical_url": "https://calendar.google.com/x/basic.ics", "mail_name": "Andus",
    }
    # Device id is generated once and reused.
    dev = (tmp_path / "dev").read_text()
    assert seen["form"]["deviceIdentifier"] == dev
    await client(transport, tmp_path).fetch_item(PASSWORD)
    assert seen["form"]["deviceIdentifier"] == dev


async def test_wrong_password_and_missing_item(tmp_path):
    transport, _ = fake_server()
    with pytest.raises(VaultError, match="incorrect"):
        await client(transport, tmp_path).fetch_item("nope")
    with pytest.raises(VaultError, match='No item named "Gmail"'):
        await client(transport, tmp_path, item="Gmail").fetch_item(PASSWORD)


async def test_two_factor_then_api_key(tmp_path):
    transport, seen = fake_server(require_totp=True)
    with pytest.raises(TwoFactorRequired):
        await client(transport, tmp_path).fetch_item(PASSWORD)
    values = await client(transport, tmp_path).fetch_item(PASSWORD, "123 456")
    assert values["mail_address"] == "andus@gmail.com"
    await client(transport, tmp_path, client_id="user.abc", client_secret="s3cret").fetch_item(PASSWORD)
    assert seen["form"]["grant_type"] == "client_credentials"
    assert seen["form"]["client_id"] == "user.abc"


def test_with_secrets_never_touches_vault_keys():
    s = Settings(vault_url="https://v", vault_email="e").with_secrets(
        {"mail_address": "a@b", "vault_url": "evil", "imap_port": "1993", "unknown": "x", "ical_url": ""})
    assert s.mail_address == "a@b" and s.imap_port == 1993
    assert s.vault_url == "https://v" and s.ical_url == ""


async def test_unlock_screen_reconfigures_tabs(settings, tmp_path, monkeypatch):
    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry
    from tui_os.widgets.calendar import CalendarPane
    from tui_os.widgets.mail import MailPane

    # Record instead of connecting to Gmail / Google for real.
    got = {}
    monkeypatch.setattr(MailPane, "reconfigure", lambda self, s: got.setdefault("mail", s))
    monkeypatch.setattr(CalendarPane, "reconfigure", lambda self, s: got.setdefault("cal", s))
    transport, _ = fake_server()
    app = TuiOS(settings, telemetry_source=DemoTelemetry(seed=1),
                vault_client=client(transport, tmp_path))
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        assert type(app.screen).__name__ == "UnlockScreen"
        await pilot.press(*PASSWORD, "enter")
        for _ in range(60):
            await pilot.pause(0.05)
            if app.settings.mail_address:
                break
        assert app.settings.mail_address == "andus@gmail.com"
        assert app.settings.ical_url.endswith("basic.ics")
        assert got["mail"].mail_password == "abcd efgh ijkl mnop"
        assert got["cal"].ical_url.endswith("basic.ics")
        assert type(app.screen).__name__ != "UnlockScreen"


async def test_unlock_screen_escape_keeps_config(settings, tmp_path):
    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry

    transport, _ = fake_server()
    app = TuiOS(settings, telemetry_source=DemoTelemetry(seed=1),
                vault_client=client(transport, tmp_path))
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert type(app.screen).__name__ != "UnlockScreen"
        assert app.settings.mail_address == ""
