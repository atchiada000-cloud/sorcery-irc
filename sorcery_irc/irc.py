"""Minimal async IRC connection, hard-wired to SorceryNet."""

from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass, field

HOST = "irc.sorcery.net"
PORT = 6697  # TLS

# mIRC formatting: colours (\x03fg,bg), bold, italic, underline, reverse, reset...
_FORMATTING = re.compile(r"\x03(\d{1,2}(,\d{1,2})?)?|[\x02\x0f\x11\x16\x1d\x1e\x1f]")


def strip_formatting(text: str) -> str:
    return _FORMATTING.sub("", text)


@dataclass
class Message:
    command: str
    params: list[str] = field(default_factory=list)
    prefix: str = ""

    @property
    def nick(self) -> str:
        return self.prefix.split("!", 1)[0]

    @property
    def text(self) -> str:
        return self.params[-1] if self.params else ""


def parse(line: str) -> Message:
    if line.startswith("@"):  # IRCv3 tags; not used
        line = line.split(" ", 1)[1] if " " in line else ""
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, trailing = line.split(" :", 1)
    elif line.startswith(":"):
        line, trailing = "", line[1:]
    parts = line.split()
    command, params = (parts[0].upper(), parts[1:]) if parts else ("", [])
    if trailing is not None:
        params.append(trailing)
    return Message(command, params, prefix)


def _tls_context() -> ssl.SSLContext:
    # SorceryNet's servers present certificates for their own names
    # (circe.sorcery.net, ...), not the round-robin irc.sorcery.net, so the
    # chain is verified normally and the hostname is checked by hand below.
    try:  # bundled CA list; python.org builds on macOS have none by default
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    ctx.check_hostname = False
    return ctx


def _is_sorcery_cert(cert: dict) -> bool:
    names = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
    return any(n == "sorcery.net" or n.endswith(".sorcery.net") for n in names)


class Connection:
    def __init__(self) -> None:
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.server_name = HOST

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(
            HOST, PORT, ssl=_tls_context(), server_hostname=HOST
        )
        cert = self.writer.get_extra_info("peercert") or {}
        if not _is_sorcery_cert(cert):
            self.writer.close()
            raise ssl.SSLError("server certificate is not for sorcery.net")
        self.server_name = next(
            (v for k, v in cert.get("subjectAltName", ()) if k == "DNS"), HOST
        )

    async def lines(self):
        assert self.reader
        while True:
            raw = await self.reader.readline()
            if not raw:
                return
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")

    def send(self, line: str) -> None:
        if self.writer and not self.writer.is_closing():
            # IRC lines are capped at 512 bytes including CRLF.
            data = line.replace("\r", "").replace("\n", " ").encode()[:510]
            self.writer.write(data + b"\r\n")

    def close(self) -> None:
        if self.writer:
            self.writer.close()

    @property
    def connected(self) -> bool:
        return bool(self.writer) and not self.writer.is_closing()
