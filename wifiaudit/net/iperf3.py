"""Client iperf3 (protocole TCP) en Python pur : mesure de débit montant et
descendant vers n'importe quel serveur ``iperf3 -s`` (serveur local de
l'entreprise, NAS, ou serveur public). Aucun exécutable externe requis."""
from __future__ import annotations

import json
import random
import socket
import string
import struct
import threading
import time
from dataclasses import dataclass

TEST_START, TEST_RUNNING, TEST_END = 1, 2, 4
PARAM_EXCHANGE, CREATE_STREAMS = 9, 10
SERVER_TERMINATE, CLIENT_TERMINATE = 11, 12
EXCHANGE_RESULTS, DISPLAY_RESULTS, IPERF_DONE = 13, 14, 16
ACCESS_DENIED, SERVER_ERROR = -1, -2

BLOCK = 128 * 1024


class IperfError(Exception):
    pass


@dataclass
class IperfResult:
    direction: str          # "down" (serveur -> poste) ou "up"
    mbps: float
    bytes: int
    seconds: float
    server: str
    intervals: list[float]  # Mb/s par seconde (côté client)

    def to_dict(self):
        return {"direction": self.direction, "mbps": round(self.mbps, 2), "bytes": self.bytes,
                "seconds": round(self.seconds, 2), "server": self.server,
                "intervals": [round(v, 2) for v in self.intervals]}


def _cookie() -> bytes:
    return ("".join(random.choices(string.ascii_lowercase + "234567", k=36)) + "\0").encode()


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise IperfError("Connexion fermée par le serveur")
        buf += chunk
    return buf


def _send_json(s, obj) -> None:
    data = json.dumps(obj).encode()
    s.sendall(struct.pack(">I", len(data)) + data)


def _recv_json(s) -> dict:
    (n,) = struct.unpack(">I", _recv_exact(s, 4))
    return json.loads(_recv_exact(s, n))


def run(host: str, port: int = 5201, seconds: float = 10.0, reverse: bool = True,
        streams: int = 1, timeout: float = 10.0, progress=None, stop: threading.Event | None = None
        ) -> IperfResult:
    """Lance un test. ``reverse=True`` : téléchargement (serveur -> poste).
    ``progress(mbps_instantané, écoulé)`` est appelé chaque seconde."""
    stop = stop or threading.Event()
    cookie = _cookie()
    try:
        ctrl = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise IperfError(f"Impossible de joindre {host}:{port} ({exc})") from exc
    ctrl.settimeout(timeout + seconds)
    data_socks: list[socket.socket] = []
    counted = [0] * streams
    intervals: list[float] = []
    t_start = t_end = 0.0
    workers: list[threading.Thread] = []
    running = threading.Event()
    server_res: dict = {}
    try:
        ctrl.sendall(cookie)
        while True:
            state = struct.unpack("b", _recv_exact(ctrl, 1))[0]
            if state == PARAM_EXCHANGE:
                params = {"tcp": True, "omit": 0, "time": int(seconds), "num": 0, "blockcount": 0,
                          "parallel": streams, "len": BLOCK, "pacing_timer": 1000,
                          "client_version": "3.17"}
                if reverse:
                    params["reverse"] = True
                _send_json(ctrl, params)
            elif state == CREATE_STREAMS:
                for _ in range(streams):
                    ds = socket.create_connection((host, port), timeout=timeout)
                    ds.sendall(cookie)
                    ds.settimeout(2.0)
                    data_socks.append(ds)
            elif state == TEST_START:
                pass
            elif state == TEST_RUNNING:
                running.set()
                t_start = time.monotonic()

                def pump(i, sock):
                    payload = b"\x00" * BLOCK
                    while running.is_set() and not stop.is_set():
                        try:
                            if reverse:
                                n = len(sock.recv(BLOCK))
                                if n == 0:
                                    break
                            else:
                                n = sock.send(payload)
                            counted[i] += n
                        except socket.timeout:
                            continue
                        except OSError:
                            break

                for i, sock in enumerate(data_socks):
                    th = threading.Thread(target=pump, args=(i, sock), daemon=True)
                    th.start()
                    workers.append(th)
                last, last_t = 0, t_start
                while time.monotonic() - t_start < seconds and not stop.is_set():
                    time.sleep(0.25)
                    now = time.monotonic()
                    if now - last_t >= 1.0:
                        tot = sum(counted)
                        mbps = (tot - last) * 8 / (now - last_t) / 1e6
                        intervals.append(mbps)
                        last, last_t = tot, now
                        if progress:
                            progress(mbps, now - t_start)
                t_end = time.monotonic()
                running.clear()
                ctrl.sendall(struct.pack("b", TEST_END))
            elif state == EXCHANGE_RESULTS:
                dur = t_end - t_start
                _send_json(ctrl, {
                    "cpu_util_total": 0, "cpu_util_user": 0, "cpu_util_system": 0,
                    "sender_has_retransmits": 0 if reverse else -1,
                    "streams": [{"id": (1 if i == 0 else i + 2), "bytes": counted[i], "retransmits": -1,
                                 "jitter": 0, "errors": 0, "packets": 0,
                                 "start_time": 0, "end_time": dur} for i in range(streams)]})
                server_res = _recv_json(ctrl)
            elif state == DISPLAY_RESULTS:
                ctrl.sendall(struct.pack("b", IPERF_DONE))
                break
            elif state in (ACCESS_DENIED,):
                raise IperfError("Serveur occupé par un autre test (réessayez ou changez de port)")
            elif state == SERVER_ERROR:
                raise IperfError("Erreur signalée par le serveur iperf3")
            elif state in (SERVER_TERMINATE, CLIENT_TERMINATE):
                raise IperfError("Test interrompu par le serveur")
    except (socket.timeout, OSError, struct.error, json.JSONDecodeError) as exc:
        raise IperfError(f"Échec du test iperf3 : {exc}") from exc
    finally:
        running.clear()
        for s in data_socks:
            try:
                s.close()
            except OSError:
                pass
        try:
            ctrl.close()
        except OSError:
            pass
    dur = max(t_end - t_start, 1e-3)
    total = sum(counted)
    if not reverse:   # en envoi, on retient ce que le serveur a réellement reçu
        try:
            recv = sum(st["bytes"] for st in server_res.get("streams", []))
            if recv:
                total = recv
        except (KeyError, TypeError):
            pass
    return IperfResult("down" if reverse else "up", total * 8 / dur / 1e6, total, dur,
                       f"{host}:{port}", intervals)
