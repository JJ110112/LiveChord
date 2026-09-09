"""UI server for the engine: static files + WebSocket on one port (default 8810).

Deliberately stdlib-only (no `websockets`, no asyncio): a `ThreadingHTTPServer`
serves `frontend/mie.html` + `/js` + `/css`, and `/ws` upgrades to a minimal
RFC 6455 connection handled on its own thread.  A broadcaster thread pushes a
`state` snapshot at 10 Hz plus every queued `event`.  Messages from the page
are handed to the engine through `post_control()` (engine thread applies them).

Wire format (plan §9):
    -> {"type":"state", ...snapshot}        10 Hz
    -> {"type":"event", "events":[...]}     whenever the engine produced some
    <- {"type":"set","path":"global.prob_scale","value":0.5}
    <- {"type":"set","path":"edge.<id>.<field>","value":...}
    <- {"type":"set","path":"inst.<ch>.<field>","value":...}
    <- {"type":"mode","value":"SAFE"} | {"type":"panic"} | {"type":"resume"}
    <- {"type":"scene","id":"01"} | {"type":"playhead", t,chord,key,bpm,beat,bar}
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import struct
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from .graph import REPO_ROOT

log = logging.getLogger("mie.ui")

FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
# The session recordings, for the panel's piano roll. Only ever read, and only
# ever files this engine itself writes: `_log_path` refuses anything that is not
# a bare `session-*.jsonl`, so a request cannot address a file outside here.
from .eventlog import LOG_DIR              # noqa: E402  (one definition, see there)
_LOG_RE = re.compile(r"^session-[0-9]{8}-[0-9]{6}\.jsonl$")


def _list_logs(limit: int = 40) -> list:
    try:
        names = [n for n in os.listdir(LOG_DIR) if _LOG_RE.match(n)]
    except OSError:
        return []
    names.sort(reverse=True)                    # newest first: that is the one you want
    out = []
    for n in names[:limit]:
        p = os.path.join(LOG_DIR, n)
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        # How many notes are in it, so an empty one reads as empty. The player
        # restarted the engine, went straight to the file dialog and picked the
        # newest file - which was the session they had just started and never
        # played into. It drew nothing, correctly, and looked broken
        # (2026-09-09). A count in the list makes that choice obvious instead.
        human = gen = 0
        try:
            with open(p, "rb") as f:
                for line in f:
                    if b'"human"' in line:
                        human += 1
                    elif b'"gen"' in line:
                        gen += 1
        except OSError:
            pass
        out.append({"name": n, "bytes": size, "human": human, "gen": gen})
    return out


def _log_path(name: str) -> Optional[str]:
    if not _LOG_RE.match(name or ""):
        return None
    p = os.path.join(LOG_DIR, name)
    return p if os.path.isfile(p) else None


MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2"}


class WsConn:
    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()
        self.alive = True

    def send_text(self, text: str) -> None:
        data = text.encode("utf-8")
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(n)
        elif n < 65536:
            head.append(126)
            head += struct.pack(">H", n)
        else:
            head.append(127)
            head += struct.pack(">Q", n)
        with self.lock:
            try:
                self.sock.sendall(bytes(head) + data)
            except OSError:
                self.alive = False

    def _send_ctrl(self, opcode: int, payload: bytes = b"") -> None:
        with self.lock:
            try:
                self.sock.sendall(bytes([0x80 | opcode, len(payload)]) + payload)
            except OSError:
                self.alive = False

    def recv_frame(self) -> Optional[tuple[int, bytes]]:
        def rd(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError
                buf += chunk
            return buf
        try:
            b1, b2 = rd(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", rd(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", rd(8))[0]
            mask = rd(4) if masked else b""
            payload = rd(n)
            if masked:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            return opcode, payload
        except (OSError, ConnectionError):
            self.alive = False
            return None

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


class UiServer:
    def __init__(self, engine, *, port: int = 8810, on_message: Optional[Callable[[dict], None]] = None,
                 scene_loader: Optional[Callable[[str], None]] = None):
        self.engine = engine
        self.port = port
        self.on_message = on_message
        self.scene_loader = scene_loader
        self.conns: list[WsConn] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.last_client_seen = time.time()

    # ---- http ----
    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # quiet
                pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/ws":
                    server._upgrade(self)
                    return
                if path in ("/", "/mie", "/mie.html"):
                    path = "/mie.html"
                if path == "/api/scenes":
                    from .graph import list_scenes
                    body = json.dumps(list_scenes()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", MIME[".json"])
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                # The panel's own recordings, so reviewing a take is a click
                # rather than a file dialog. Read-only, and confined to the log
                # directory: the name is rejected outright if it is not a plain
                # `session-*.jsonl`, so no path can walk out of there.
                if path == "/api/logs":
                    body = json.dumps(_list_logs()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", MIME[".json"])
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path.startswith("/api/log/"):
                    name = path[len("/api/log/"):]
                    fp = _log_path(name)
                    if fp is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    with open(fp, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                fs_path = os.path.normpath(os.path.join(FRONTEND_DIR, path.lstrip("/")))
                if not fs_path.startswith(FRONTEND_DIR) or not os.path.isfile(fs_path):
                    self.send_response(404)
                    self.end_headers()
                    return
                ext = os.path.splitext(fs_path)[1].lower()
                with open(fs_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                # `no-cache` still lets a browser reuse a stored copy after a
                # revalidation it may skip. The panel is served off local disk
                # and is edited constantly, so a stale page costs more than a
                # re-fetch ever will: the player went looking for a control that
                # was already there (2026-09-08).
                self.send_header("Cache-Control", "no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def _upgrade(self, h: BaseHTTPRequestHandler) -> None:
        key = h.headers.get("Sec-WebSocket-Key")
        if not key or "upgrade" not in h.headers.get("Connection", "").lower():
            h.send_response(400)
            h.end_headers()
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        h.send_response(101, "Switching Protocols")
        h.send_header("Upgrade", "websocket")
        h.send_header("Connection", "Upgrade")
        h.send_header("Sec-WebSocket-Accept", accept)
        h.end_headers()
        conn = WsConn(h.connection)
        with self.lock:
            self.conns.append(conn)
        self.last_client_seen = time.time()
        self.engine._ui("ui", event="connect", clients=self.client_count)
        try:
            conn.send_text(json.dumps({"type": "hello", "snapshot": self.engine.snapshot()}))
            self._reader(conn)
        finally:
            with self.lock:
                if conn in self.conns:
                    self.conns.remove(conn)
            conn.close()
            self.engine._ui("ui", event="disconnect", clients=self.client_count)
        # keep the handler from writing anything else on the socket
        h.close_connection = True

    def _reader(self, conn: WsConn) -> None:
        while conn.alive and not self.stop.is_set():
            fr = conn.recv_frame()
            if fr is None:
                return
            opcode, payload = fr
            if opcode == 0x8:
                return
            if opcode == 0x9:
                conn._send_ctrl(0xA, payload)
                continue
            if opcode != 0x1:
                continue
            self.last_client_seen = time.time()
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                log.debug("mie: ignoring a malformed websocket message", exc_info=True)
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        if self.on_message is not None:
            self.on_message(msg)

    # ---- broadcast ----
    def broadcast(self, obj: dict) -> None:
        text = json.dumps(obj, ensure_ascii=False, default=str)
        with self.lock:
            conns = list(self.conns)
        for c in conns:
            c.send_text(text)

    def _broadcaster(self) -> None:
        while not self.stop.is_set():
            time.sleep(0.1)
            with self.lock:
                have = bool(self.conns)
            if not have:
                continue
            evs = self.engine.pop_events()
            if evs:
                self.broadcast({"type": "event", "events": evs})
            self.broadcast({"type": "state", **self.engine.snapshot()})

    def start(self) -> None:
        # Windows lets a second socket bind a port that is already LISTENing
        # when SO_REUSEADDR is set, which HTTPServer sets by default. Two
        # servers then share the port and the OS hands each connection to one
        # of them at random. On 2026-09-08 a leftover test server from hours
        # earlier was still on 8810: the engine bound alongside it, the panel
        # talked to the wrong one, the engine saw no client and PANICked into
        # BYPASS, and the whole session was lost. Refuse loudly instead.
        probe = socket.socket()
        probe.settimeout(0.3)
        try:
            probe.connect(("127.0.0.1", self.port))
            raise OSError(
                f"port {self.port} is already serving - another MIE (or a leftover "
                f"test server) is running. Close it first: the two would share the "
                f"port and the panel would reach whichever the OS picked.")
        except OSError as e:
            if "already serving" in str(e):
                raise
        finally:
            probe.close()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), self._make_handler())
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, name="mie-http", daemon=True).start()
        threading.Thread(target=self._broadcaster, name="mie-ws-bcast", daemon=True).start()

    def shutdown(self) -> None:
        """Stop serving, but never block the process from exiting.

        `httpd.shutdown()` waits for `serve_forever` to acknowledge, and if that
        thread is wedged it waits forever - which is how a hung quit swallowed a
        whole session's log on 2026-09-08: the exit path never reached
        `evlog.close()` and the file was left at zero bytes. Everything after
        this is more important than a tidy socket.
        """
        self.stop.set()
        with self.lock:
            conns = list(self.conns)
        for c in conns:
            c.close()
        if self.httpd:
            t = threading.Thread(target=self.httpd.shutdown, name="mie-http-stop", daemon=True)
            t.start()
            t.join(2.0)
            if t.is_alive():
                log.warning("mie: the UI server did not stop; leaving it and exiting")

    @property
    def client_count(self) -> int:
        with self.lock:
            return len(self.conns)
