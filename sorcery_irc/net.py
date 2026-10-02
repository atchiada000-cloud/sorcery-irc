"""Background network thread shared by the desktop apps."""

from __future__ import annotations

import asyncio
import ssl
import threading
from typing import Callable

from .core import Client
from .irc import Connection


class NetThread:
    """Runs the IRC connection on a background asyncio thread and hands each
    event to the client on the UI thread (via `post`)."""

    def __init__(self, post: Callable[[Callable[[], None]], None]) -> None:
        self.post = post  # runs a function on the UI thread
        self.client: Client | None = None
        self.gen = 0  # bumped on connect/close so events from old connections are dropped
        self.loop: asyncio.AbstractEventLoop | None = None
        self.conn: Connection | None = None

    def connect(self) -> None:
        self.close()
        gen = self.gen
        threading.Thread(target=lambda: asyncio.run(self._run(gen)), daemon=True).start()

    def send(self, line: str) -> None:
        self._call(lambda conn: conn.send(line))

    def close(self) -> None:
        self._call(lambda conn: conn.close())
        self.gen += 1
        self.loop = self.conn = None

    def _call(self, fn: Callable[[Connection], None]) -> None:
        loop, conn = self.loop, self.conn
        if loop and conn:
            try:
                loop.call_soon_threadsafe(fn, conn)
            except RuntimeError:  # that connection's loop has already finished
                pass

    def _post(self, gen: int, fn: Callable, *args) -> None:
        def run() -> None:
            if gen == self.gen:
                fn(*args)
        self.post(run)

    async def _run(self, gen: int) -> None:
        client = self.client
        assert client
        conn = Connection()
        try:
            await asyncio.wait_for(conn.connect(), 20)
        except (TimeoutError, OSError, ssl.SSLError) as e:
            self._post(gen, client.on_disconnected, str(e) or "timed out")
            return
        loop = asyncio.get_running_loop()

        def adopt() -> None:  # runs on the GTK thread
            self.loop, self.conn = loop, conn
            client.on_connected(conn.server_name)
        self._post(gen, adopt)

        waiting_for_pong = False
        while True:
            try:
                raw = await asyncio.wait_for(conn.reader.readline(), 120)
            except TimeoutError:
                if waiting_for_pong:  # silent for 4 minutes: the link is dead (e.g. after sleep)
                    break
                waiting_for_pong = True
                conn.send("PING :sorcery")
                continue
            except OSError:
                break
            if not raw:
                break
            waiting_for_pong = False
            self._post(gen, client.on_line, raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        conn.close()
        self._post(gen, client.on_disconnected, None)
