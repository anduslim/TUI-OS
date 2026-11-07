"""Telemetry sources for the Olares One box.

``PrometheusTelemetry`` queries the Prometheus HTTP API (``/api/v1/query``)
concurrently for each configured metric. ``DemoTelemetry`` produces a
plausible random walk so the UI can be developed away from the server.

Both return an immutable :class:`TelemetrySnapshot`; the UI never talks to the
network directly.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx


@dataclass(frozen=True)
class TelemetrySnapshot:
    cpu_temp: float | None = None
    gpu_temp: float | None = None
    vram_used: float | None = None  # bytes
    vram_total: float | None = None  # bytes
    net_rx: float | None = None  # bytes / second
    net_tx: float | None = None  # bytes / second
    source: str = "offline"  # "prometheus" | "demo" | "offline"
    error: str | None = None
    #: Per-metric failures (e.g. a query that returned no series).
    missing: tuple[str, ...] = ()
    timestamp: float = field(default_factory=time.time)

    @property
    def vram_ratio(self) -> float | None:
        if self.vram_used is None or not self.vram_total:
            return None
        return max(0.0, min(1.0, self.vram_used / self.vram_total))


class TelemetrySource(Protocol):
    async def fetch(self) -> TelemetrySnapshot: ...

    async def aclose(self) -> None: ...


class PrometheusTelemetry:
    """Polls a Prometheus server for the configured PromQL expressions."""

    def __init__(
        self,
        base_url: str,
        queries: dict[str, str],
        *,
        timeout: float = 4.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.queries = dict(queries)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def _query(self, expr: str) -> float | None:
        resp = await self._client.get("/api/v1/query", params={"query": expr})
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "success":
            raise RuntimeError(payload.get("error", "query failed"))
        data = payload.get("data", {})
        result = data.get("result")
        if data.get("resultType") == "scalar":
            value = result[1]
        elif result:
            value = result[0]["value"][1]
        else:
            return None
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number

    async def fetch(self) -> TelemetrySnapshot:
        keys = list(self.queries)
        results = await asyncio.gather(
            *(self._query(self.queries[k]) for k in keys), return_exceptions=True
        )
        values: dict[str, float | None] = {}
        missing: list[str] = []
        errors: list[BaseException] = []
        for key, result in zip(keys, results):
            if isinstance(result, BaseException):
                errors.append(result)
                values[key] = None
                missing.append(key)
            else:
                values[key] = result
                if result is None:
                    missing.append(key)

        known = {
            k: values.get(k)
            for k in ("cpu_temp", "gpu_temp", "vram_used", "vram_total", "net_rx", "net_tx")
        }
        if errors and len(errors) == len(keys):
            return TelemetrySnapshot(
                source="offline", error=_describe(errors[0]), missing=tuple(missing)
            )
        return TelemetrySnapshot(
            **known,
            source="prometheus",
            error=_describe(errors[0]) if errors else None,
            missing=tuple(missing),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class DemoTelemetry:
    """Synthetic metrics: a bounded random walk around realistic values."""

    VRAM_TOTAL = 24 * 1024**3

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._state = {
            "cpu_temp": 52.0,
            "gpu_temp": 47.0,
            "vram_used": 9.5 * 1024**3,
            "net_rx": 2.0e6,
            "net_tx": 4.0e5,
        }

    def _walk(self, key: str, step: float, lo: float, hi: float) -> float:
        value = self._state[key] + self._rng.uniform(-step, step)
        value = max(lo, min(hi, value))
        self._state[key] = value
        return value

    async def fetch(self) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            cpu_temp=self._walk("cpu_temp", 2.5, 38, 88),
            gpu_temp=self._walk("gpu_temp", 3.0, 35, 86),
            vram_used=self._walk("vram_used", 0.6 * 1024**3, 2 * 1024**3, 23 * 1024**3),
            vram_total=float(self.VRAM_TOTAL),
            net_rx=self._walk("net_rx", 1.5e6, 1.0e3, 1.2e8),
            net_tx=self._walk("net_tx", 4.0e5, 1.0e3, 4.0e7),
            source="demo",
        )

    async def aclose(self) -> None:
        return None


def _describe(exc: BaseException) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "connection refused"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return str(exc) or type(exc).__name__
