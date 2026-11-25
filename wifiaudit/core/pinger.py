"""Ping continu (ICMP natif) d'une ou plusieurs cibles, utilisé pour la
supervision, les tests aux points de relevé et la mesure des coupures
pendant l'itinérance."""
from __future__ import annotations

import socket
import statistics
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from ..net import icmp


def gateway_ip() -> str | None:
    """Passerelle : premier routeur (sonde TTL=1)."""
    for t in ("1.1.1.1", "8.8.8.8"):
        r = icmp.probe(t, ttl=1, timeout_ms=800)
        if r.address:
            return r.address
    return None


def resolve(host: str) -> str | None:
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except (OSError, IndexError, UnicodeError):
        return None


def ping_series(target: str, count: int = 10, interval: float = 0.2, timeout_ms: int = 1000) -> dict:
    """Série de pings (test ponctuel)."""
    ip = resolve(target) or target
    rtts, lost = [], 0
    for _ in range(count):
        r = icmp.probe(ip, timeout_ms=timeout_ms)
        if r.reached and r.rtt is not None:
            rtts.append(r.rtt)
        else:
            lost += 1
        time.sleep(interval)
    res = {"target": target, "ip": ip, "sent": count, "received": len(rtts),
           "loss": round(100.0 * lost / count, 1)}
    if rtts:
        res.update(avg=round(statistics.mean(rtts), 2), min=round(min(rtts), 2), max=round(max(rtts), 2),
                   jitter=round(statistics.mean(abs(a - b) for a, b in zip(rtts, rtts[1:])), 2)
                   if len(rtts) > 1 else 0.0)
    else:
        res.update(avg=None, min=None, max=None, jitter=None)
    return res


class Pinger:
    """Ping continu ; conserve (horodatage, rtt|None) par cible."""

    def __init__(self, targets: list[str], interval: float = 1.0, timeout_ms: int = 1000,
                 history: int = 3600):
        self.targets = targets
        self.ips = {t: resolve(t) or t for t in targets}
        self.interval = interval
        self.timeout_ms = timeout_ms
        self.data = {t: deque(maxlen=history) for t in targets}
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max(4, len(targets) * 4), thread_name_prefix="ping")
        self._thread = threading.Thread(target=self._loop, name="pinger", daemon=True)
        self.lock = threading.Lock()

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def set_interval(self, s: float):
        self.interval = max(0.05, s)

    def _loop(self):
        nxt = time.monotonic()
        while not self._stop.is_set():
            ts = time.time()
            for t, ip in self.ips.items():
                try:
                    fut = self._pool.submit(icmp.probe, ip, 128, self.timeout_ms, 32)
                except RuntimeError:
                    return
                fut.add_done_callback(lambda f, t=t, ts=ts: self._done(t, ts, f))
            nxt += self.interval
            self._stop.wait(max(0.0, nxt - time.monotonic()))
            if time.monotonic() - nxt > 2:
                nxt = time.monotonic()

    def _done(self, t, ts, fut):
        try:
            r = fut.result()
            v = r.rtt if r.reached else None
        except Exception:
            v = None
        with self.lock:
            self.data[t].append((ts, v))

    def series(self, target: str, since: float = 0.0) -> list[tuple[float, float | None]]:
        with self.lock:
            return sorted(x for x in self.data[target] if x[0] >= since)

    def consecutive_losses(self, target: str) -> int:
        n = 0
        for _ts, v in reversed(self.series(target)):
            if v is not None:
                break
            n += 1
        return n

    def gap_around(self, target: str, t0: float, window: float = 6.0) -> float | None:
        """Durée de coupure (ms) autour de l'instant ``t0`` : écart entre la
        dernière réponse avant la coupure et la première après."""
        s = [x for x in self.series(target, t0 - window) if x[0] <= t0 + window]
        if not s:
            return None
        worst = 0.0
        last_ok = None
        for ts, v in s:
            if v is not None:
                if last_ok is not None:
                    worst = max(worst, ts - last_ok - self.interval)
                last_ok = ts
        return round(max(0.0, worst) * 1000, 0)
