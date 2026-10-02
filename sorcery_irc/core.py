"""IRC client state and logic for the Sorcery desktop app, independent of any toolkit.

The frontend supplies a `net` object (connect / send / close) that calls back
into `on_connected`, `on_line` and `on_disconnected`, and a `ui` object that
renders what the client produces (see `Frontend`).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .irc import HOST, PORT, Message, parse, strip_formatting



def _config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / "Sorcery"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Sorcery"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "sorcery-irc"


CONFIG = _config_dir() / "config.json"
SERVER = "SorceryNet"
VERSION = "Sorcery 0.2 — a custom SorceryNet client"

# RFC 2812-style nick: a letter or special first, then letters, digits,
# specials or '-'. No dots or spaces.
NICK_RE = re.compile(r"^[A-Za-z\[\]\\`_^{|}][A-Za-z0-9\[\]\\`_^{|}-]{0,29}$")
NICK_RULES = (
    "Nicks can use letters, numbers and [ ] \\ ` _ ^ { | } -, can't start with a "
    "number or '-', and can't contain dots or spaces (max 30 characters)."
)
RANKS = {"~": 0, "&": 1, "@": 2, "%": 3, "+": 4}
SERVICES = {"nickserv", "chanserv", "memoserv", "operserv", "hostserv", "botserv"}
NICK_COLOURS = 7  # frontends define tags nick0 .. nick6

HELP = """\
Commands
  /join #channel          join a channel              /part [reason]   leave this channel
  /msg nick text          private message             /query nick      open a private chat
  /me does something      action                      /notice nick text
  /nick NewNick           change your nick            /topic [text]    show or set the topic
  /whois nick             about someone               /names           refresh the user list
  /list [*filter*]        list channels               /close           close this window
  /ns ... /cs ... /ms ... talk to NickServ / ChanServ / MemoServ
  /reconnect              reconnect                   /quote RAW LINE  send a raw IRC command
  /clear                  clear this window           /quit [message]  leave SorceryNet
Keys
  Ctrl+PgDn / Ctrl+PgUp  next / previous window    Alt+1..9  jump to window
  Tab  complete a nick    Up / Down  earlier messages    Ctrl+W  close window    Ctrl+Q  quit
  Double-click a name in the user list to chat privately.
Registered nicks
  If it's yours:  /ns IDENTIFY <password>     Register yours:  /ns REGISTER <password> <email>
  Leave a note for someone offline:  /ms SEND <nick> <message>"""

# A line is a list of (text, tags) spans; tags are space-separated names.
Span = tuple[str, str]
Line = list[Span]


def nick_tag(nick: str) -> str:
    return f"nick{zlib.crc32(nick.lower().encode()) % NICK_COLOURS}"


def is_channel(name: str) -> bool:
    return name[:1] in "#&+!"


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=2))
    except OSError:
        pass


@dataclass
class Buffer:
    name: str
    kind: str  # "server" | "channel" | "query"
    users: dict[str, str] = field(default_factory=dict)  # nick -> mode prefix
    topic: str = ""
    unread: int = 0
    highlight: bool = False
    joined: bool = False


class Frontend(Protocol):
    def show_line(self, key: str, line: Line) -> None: ...
    def clear_lines(self, key: str) -> None: ...
    def rename_buffer(self, old_key: str, new_key: str) -> None: ...
    def changed(self) -> None: ...  # buffers, topic, users or status changed
    def notify(self, key: str, title: str, body: str) -> None: ...
    def later(self, seconds: int, fn: Callable[[], None]) -> None: ...
    def quit(self) -> None: ...


class Net(Protocol):
    def connect(self) -> None: ...
    def send(self, line: str) -> None: ...
    def close(self) -> None: ...


class Client:
    def __init__(self, ui: Frontend, net: Net) -> None:
        self.ui, self.net = ui, net
        self.cfg = load_config()
        self.nick: str = self.cfg.get("nick", "")
        self.connecting = False
        self.connected = False
        self.registered = False
        self.server_name = HOST
        self.buffers: dict[str, Buffer] = {SERVER.lower(): Buffer(SERVER, "server")}
        self.order: list[str] = [SERVER.lower()]
        self.active_key = SERVER.lower()
        self.quitting = False
        self.retry_delay = 5
        self.attempt = 0  # bumped on every connect, so stale retries do nothing

    @property
    def active(self) -> Buffer:
        return self.buffers[self.active_key]

    @property
    def state(self) -> str:
        if self.registered:
            return "connected"
        return "connecting…" if self.connected or self.connecting else "offline"

    # ── output helpers ────────────────────────────────────────────────────
    def add(self, key: str, body: Line, *, count: bool = True, hl: bool = False) -> None:
        buf = self.buffers.get(key.lower()) or self.buffers[SERVER.lower()]
        line = [(time.strftime("%H:%M "), "time highlight" if hl else "time"), *body]
        self.ui.show_line(buf.name.lower(), line)
        if buf is not self.active and count:
            buf.unread += 1
            buf.highlight = buf.highlight or hl
            self.ui.changed()

    def info(self, key: str, text: str, tags: str = "dim italic") -> None:
        self.add(key, [("── " + text, tags)], count=False)

    def error(self, text: str, key: str | None = None) -> None:
        self.add(key or self.active_key, [("✖ " + text, "error")], count=False)

    def hint(self, text: str, key: str | None = None) -> None:
        self.add(key or self.active_key, [(text, "hint")], count=False)

    def chat_line(self, nick: str, text: str, *, action=False, notice=False, mine=False) -> tuple[Line, bool]:
        text = strip_formatting(text)
        hl = not mine and bool(self.nick) and re.search(rf"\b{re.escape(self.nick)}\b", text, re.I) is not None
        who = "mine" if mine else nick_tag(nick)
        if action:
            body: Line = [("* ", who), (nick, who + " bold"), (" " + text, "italic")]
        elif notice:
            body = [(f"-{nick}- ", "magenta bold"), (text, "")]
        else:
            body = [("<", "dim"), (nick, who + " bold"), ("> ", "dim"), (text, "")]
        if hl:
            body = [(t, f"{tags} highlight".strip()) for t, tags in body]
        return body, hl

    def buffer(self, name: str, kind: str, switch: bool = False) -> Buffer:
        key = name.lower()
        if key not in self.buffers:
            self.buffers[key] = Buffer(name, kind)
            self.order.append(key)
        if switch:
            self.switch(key)
        else:
            self.ui.changed()
        return self.buffers[key]

    # ── windows ───────────────────────────────────────────────────────────
    def switch(self, key: str) -> None:
        if key not in self.buffers:
            return
        self.active_key = key
        buf = self.active
        buf.unread, buf.highlight = 0, False
        self.ui.changed()

    def cycle(self, step: int) -> None:
        i = (self.order.index(self.active_key) + step) % len(self.order)
        self.switch(self.order[i])

    def jump(self, i: int) -> None:
        if i < len(self.order):
            self.switch(self.order[i])

    def close_buffer(self, key: str, reason: str = "") -> None:
        buf = self.buffers[key]
        if buf.kind == "server":
            return
        if buf.kind == "channel" and buf.joined:
            self.send(f"PART {buf.name}" + (f" :{reason}" if reason else ""))
        i = self.order.index(key)
        self.order.remove(key)
        del self.buffers[key]
        self.remember_channels()
        if key == self.active_key:
            self.switch(self.order[max(0, i - 1)])
        else:
            self.ui.changed()

    def open_query(self, nick: str) -> None:
        self.buffer(nick, "query", switch=True)

    def remember_channels(self) -> None:
        self.cfg["channels"] = [b.name for b in self.buffers.values() if b.kind == "channel" and b.joined]
        save_config(self.cfg)

    # ── connection ────────────────────────────────────────────────────────
    def send(self, line: str) -> None:
        self.net.send(line)

    def connect(self) -> None:
        self.attempt += 1
        self.quitting = False
        self.registered = self.connected = False
        self.connecting = True
        self.info(SERVER, f"Connecting to {HOST}:{PORT} (TLS)…")
        self.net.connect()
        self.ui.changed()

    def reconnect(self) -> None:
        if self.connected:
            self.send("QUIT :reconnecting")
        self.net.close()
        self.retry_delay = 5
        self.connect()

    def quit(self, message: str = "") -> None:
        self.quitting = True
        if self.connected:
            self.send("QUIT :" + (message or "Sorcery, signing off"))
        self.net.close()
        self.ui.quit()

    def on_connected(self, server_name: str) -> None:
        self.connected, self.connecting = True, False
        self.server_name = server_name
        self.info(SERVER, f"Connected to {server_name}. Signing in as {self.nick}…")
        self.send(f"NICK {self.nick}")
        self.send(f"USER {self.nick} 0 * :{self.nick}")
        self.ui.changed()

    def on_disconnected(self, error: str | None) -> None:
        was_connected = self.connected
        self.connected = self.registered = self.connecting = False
        for b in self.buffers.values():
            b.joined = False
        if self.quitting:
            return
        delay = self.retry_delay
        self.retry_delay = min(self.retry_delay * 2, 60)
        if error and not was_connected:
            self.error(f"Couldn't connect: {error}. Trying again in {delay}s (or /reconnect).", SERVER)
        else:
            self.error(f"Disconnected from SorceryNet. Reconnecting in {delay}s (or /reconnect).", self.active_key)
        attempt = self.attempt
        self.ui.later(delay, lambda: self.attempt == attempt and not self.connected and self.connect())
        self.ui.changed()

    def on_line(self, line: str) -> None:
        try:
            self.handle(parse(line))
        except Exception as e:  # one bad line shouldn't kill the session
            self.error(f"(couldn't handle: {line[:80]!r} — {e})", SERVER)

    def nick_help(self, why: str, key: str | None = None) -> None:
        self.hint(f"⚠ {why}", key)
        self.hint("   Choose another with:  /nick NewNick", key)

    # ── incoming ──────────────────────────────────────────────────────────
    def handle(self, m: Message) -> None:  # noqa: C901 — one big dispatcher reads best here
        c, p = m.command, m.params
        if c == "PING":
            self.send(f"PONG :{m.text}")
            return
        if c == "ERROR":
            self.error(m.text, SERVER)
            return

        # ── numerics ──
        if c == "001":
            self.registered = True
            self.retry_delay = 5
            self.nick = p[0]
            self.cfg["nick"] = self.nick  # only remember nicks the server accepted
            save_config(self.cfg)
            self.info(SERVER, f"Welcome to SorceryNet, {self.nick}. Type /help for commands.", "green bold")
            for ch in self.cfg.get("channels", []):
                self.send(f"JOIN {ch}")
            self.ui.changed()
        elif c in {"002", "003", "251", "252", "254", "255", "265", "266", "372", "375", "376", "422"}:
            self.add(SERVER, [(strip_formatting(" ".join(p[1:])), "")], count=False)
        elif c in {"433", "436"}:  # nick in use / collision
            bad = p[1] if len(p) > 1 else "?"
            self.nick_help(f"The nick '{bad}' is already in use.", None if self.registered else SERVER)
            if not self.registered:
                self.switch(SERVER.lower())
        elif c == "432":
            bad = p[1] if len(p) > 1 else "?"
            self.nick_help(f"'{bad}' isn't allowed as a nick. {NICK_RULES}", None if self.registered else SERVER)
        elif c == "437":
            self.nick_help(f"'{p[1]}' is temporarily unavailable.")
        elif c == "332":
            b = self.buffer(p[1], "channel")
            b.topic = p[2]
            self.info(p[1], "Topic: " + strip_formatting(p[2]), "dim")
            self.ui.changed()
        elif c == "333":
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(p[3]))) if len(p) > 3 and p[3].isdigit() else "?"
            self.info(p[1], f"Set by {p[2].split('!')[0]} on {when}")
        elif c == "353":
            b = self.buffer(p[2], "channel")
            for entry in p[3].split():
                i = 0
                while i < len(entry) and entry[i] in RANKS:
                    i += 1
                b.users[entry[i:]] = entry[:i]
        elif c == "366":
            self.ui.changed()
        elif c == "321":
            self.info(SERVER, "Channel list:", "bold")
        elif c == "322":
            topic = strip_formatting(re.sub(r"^\[\+[^\]]*\] ?", "", p[3] if len(p) > 3 else ""))
            if "Fake channel for confusing spambots" in topic:
                return
            self.add(SERVER, [(f"{p[1]:<24}", "bold link-channel"), (f"{p[2]:>4}  ", "cyan"), (topic[:100], "")], count=False)
        elif c == "323":
            self.info(SERVER, "End of list. (SorceryNet only shows real channels after you've been connected ~2 minutes.)")
        elif c in {"311", "312", "313", "317", "318", "319", "301", "330", "338", "378", "671", "314", "369"}:
            if c == "317" and len(p) > 2 and p[2].isdigit():
                text = f"{p[1]} has been idle {int(p[2]) // 60} min"
            elif c in {"318", "369"}:
                text = "End of WHOIS"
            elif c == "311":
                text = f"{p[1]} is {p[2]}@{p[3]} ({p[-1]})"
            else:
                text = " ".join(p[1:])
            self.add(self.active_key, [("ⓘ " + strip_formatting(text), "cyan")], count=False)
        elif c in {"401", "402", "403", "404", "405", "406", "421", "441", "442", "443", "461", "471", "473", "474", "475", "477", "482"}:
            self.error(" ".join(p[1:]))
        elif c.isdigit():
            self.add(SERVER, [(strip_formatting(" ".join(p[1:])), "dim")], count=False)

        # ── commands ──
        elif c == "PRIVMSG":
            target, text = p[0], m.text
            key = target if is_channel(target) else m.nick
            if text.startswith("\x01") and text.endswith("\x01"):
                ctcp, _, arg = text.strip("\x01").partition(" ")
                if ctcp.upper() == "ACTION":
                    body, hl = self.chat_line(m.nick, arg, action=True)
                    self.buffer(key, "channel" if is_channel(key) else "query")
                    self.add(key, body, hl=hl or not is_channel(key))
                    if hl or not is_channel(key):
                        self.ui.notify(key.lower(), key, f"* {m.nick} {strip_formatting(arg)}")
                elif ctcp.upper() == "VERSION":
                    self.send(f"NOTICE {m.nick} :\x01VERSION {VERSION}\x01")
                elif ctcp.upper() == "PING":
                    self.send(f"NOTICE {m.nick} :\x01PING {arg}\x01")
                return
            body, hl = self.chat_line(m.nick, text)
            self.buffer(key, "channel" if is_channel(key) else "query")
            self.add(key, body, hl=hl or not is_channel(key))
            if hl or not is_channel(key):
                title = f"{m.nick} in {key}" if is_channel(key) else m.nick
                self.ui.notify(key.lower(), title, strip_formatting(text))
        elif c == "NOTICE":
            sender = m.nick or self.server_name
            text = m.text
            if sender.lower() in SERVICES:
                body, _ = self.chat_line(sender, text, notice=True)
                self.add(self.active_key if self.registered else SERVER, body, count=False)
                if sender.lower() == "nickserv" and re.search(r"nickname is registered|is registered and protected", text, re.I):
                    self.hint(f"⚠ '{self.nick}' is a registered nick.  If it's yours:  /ns IDENTIFY <password>")
                    self.hint("   Otherwise choose another with:  /nick NewNick   (NickServ may rename you soon)")
            elif "!" not in m.prefix or not self.registered:
                self.add(SERVER, [(strip_formatting(text), "dim")], count=False)
            else:
                key = p[0] if is_channel(p[0]) else self.active_key
                body, hl = self.chat_line(sender, text, notice=True)
                self.add(key, body, hl=hl)
        elif c == "JOIN":
            ch = p[0]
            if m.nick.lower() == self.nick.lower():
                b = self.buffer(ch, "channel", switch=True)
                b.joined, b.users = True, {}
                self.info(ch, f"You joined {ch}", "green")
                self.remember_channels()
            else:
                b = self.buffer(ch, "channel")
                b.users[m.nick] = ""
                self.add(ch, [(f"→ {m.nick} joined", "dim green")], count=False)
            self.ui.changed()
        elif c in {"PART", "KICK"}:
            ch = p[0]
            who = p[1] if c == "KICK" else m.nick
            b = self.buffers.get(ch.lower())
            if not b:
                return
            b.users.pop(who, None)
            reason = strip_formatting(m.text) if len(p) > (2 if c == "KICK" else 1) else ""
            if who.lower() == self.nick.lower():
                b.joined, b.users = False, {}
                self.remember_channels()
                msg = f"You were kicked by {m.nick}" if c == "KICK" else "You left"
                self.info(ch, msg + (f" ({reason})" if reason else ""), "yellow")
            else:
                verb = f"was kicked by {m.nick}" if c == "KICK" else "left"
                self.add(ch, [(f"← {who} {verb}" + (f" ({reason})" if reason else ""), "dim")], count=False)
            self.ui.changed()
        elif c == "QUIT":
            for b in self.buffers.values():
                if m.nick in b.users:
                    b.users.pop(m.nick)
                    self.add(b.name, [(f"← {m.nick} quit ({strip_formatting(m.text)})", "dim")], count=False)
            self.ui.changed()
        elif c == "NICK":
            old, new = m.nick, m.text
            if old.lower() == self.nick.lower():
                self.nick = new
                self.cfg["nick"] = new
                save_config(self.cfg)
                self.info(self.active_key, f"You are now known as {new}", "bold")
                if new.lower().startswith("guest"):
                    self.hint("NickServ renamed you. Pick a nick of your own with:  /nick NewNick")
            for b in self.buffers.values():
                if old in b.users:
                    b.users[new] = b.users.pop(old)
                    self.add(b.name, [(f"{old} is now {new}", "dim")], count=False)
            if old.lower() in self.buffers and self.buffers[old.lower()].kind == "query" and new.lower() not in self.buffers:
                b = self.buffers.pop(old.lower())
                b.name = new
                self.buffers[new.lower()] = b
                self.order[self.order.index(old.lower())] = new.lower()
                if self.active_key == old.lower():
                    self.active_key = new.lower()
                self.ui.rename_buffer(old.lower(), new.lower())
            self.ui.changed()
        elif c == "TOPIC":
            b = self.buffer(p[0], "channel")
            b.topic = m.text
            self.info(p[0], f"{m.nick} set the topic: {strip_formatting(m.text)}")
            self.ui.changed()
        elif c == "MODE" and is_channel(p[0]):
            self.info(p[0], f"{m.nick} sets mode {' '.join(p[1:])}")
            if p[0].lower() in self.buffers:
                self.buffers[p[0].lower()].users.clear()
            self.send(f"NAMES {p[0]}")
        elif c == "INVITE":
            self.hint(f"{m.nick} invited you to {m.text}.  Join with:  /join {m.text}")

    # ── input ─────────────────────────────────────────────────────────────
    def submit(self, line: str) -> None:
        if not line.strip():
            return
        if line.startswith("/") and not line.startswith("//"):
            self.command(line[1:])
        else:
            self.say(line[1:] if line.startswith("//") else line)

    def say(self, text: str, target: str | None = None) -> None:
        buf = self.buffers.get((target or self.active_key).lower())
        name = target or self.active.name
        if (buf is None and not target) or (buf and buf.kind == "server"):
            self.hint("This is the server window. Join a channel first:  /join #channel   (or /help)")
            return
        if not self.registered:
            self.error("Not connected yet.")
            return
        self.send(f"PRIVMSG {name} :{text}")
        if buf is None:
            buf = self.buffer(name, "query")
        body, _ = self.chat_line(self.nick, text, mine=True)
        self.add(buf.name, body, count=False)

    def command(self, line: str) -> None:  # noqa: C901
        cmd, _, arg = line.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        buf = self.active
        if cmd == "help":
            for l in HELP.splitlines():
                self.add(self.active_key, [(l, "" if l.startswith(" ") else "cyan bold")], count=False)
            return
        if cmd == "clear":
            self.ui.clear_lines(self.active_key)
            return
        if cmd in {"quit", "exit"}:
            self.quit(arg)
            return
        if cmd in {"reconnect", "connect", "server"}:
            self.reconnect()
            return
        if cmd == "close":
            self.close_buffer(self.active_key, arg)
            return
        if cmd == "nick":
            if not arg:
                self.hint(f"Your nick is {self.nick}. Change it with:  /nick NewNick")
            elif not NICK_RE.match(arg):
                self.nick_help(f"'{arg}' isn't a valid nick. {NICK_RULES}")
            else:
                if not self.registered:
                    self.nick = arg
                self.send(f"NICK {arg}")
            return
        if not self.connected:
            self.error("Not connected. Type /reconnect.")
            return
        if cmd in {"join", "j"}:
            if not arg:
                self.hint("Usage:  /join #channel")
                return
            ch = arg.split()[0]
            ch = ch if is_channel(ch) else "#" + ch
            self.send(f"JOIN {ch}" + (" " + arg.split()[1] if len(arg.split()) > 1 else ""))
        elif cmd in {"part", "leave"}:
            if buf.kind != "channel":
                self.hint("Use /part in a channel window (or /close).")
                return
            self.send(f"PART {buf.name}" + (f" :{arg}" if arg else ""))
        elif cmd in {"msg", "m"}:
            target, _, text = arg.partition(" ")
            if not text:
                self.hint("Usage:  /msg nick message")
                return
            if target.lower() in SERVICES:
                self.send(f"PRIVMSG {target} :{text}")
                secret = text.split()[0].upper() in {"IDENTIFY", "REGISTER", "GHOST", "RECOVER"}
                shown = text.split()[0] + " ••••••" if secret else text
                self.add(self.active_key, [(f"→ {target}: {shown}", "magenta")], count=False)
            else:
                self.say(text, target)
        elif cmd in {"ns", "cs", "ms", "nickserv", "chanserv", "memoserv"}:
            service = {"ns": "NickServ", "cs": "ChanServ", "ms": "MemoServ"}.get(cmd, cmd.capitalize().replace("serv", "Serv"))
            if not arg:
                self.hint(f"Usage:  /{cmd} HELP")
                return
            self.command(f"msg {service} {arg}")
        elif cmd in {"query", "q"}:
            if not arg:
                self.hint("Usage:  /query nick")
                return
            target, _, text = arg.partition(" ")
            self.open_query(target)
            if text:
                self.say(text, target)
        elif cmd == "me":
            if buf.kind == "server":
                self.hint("Use /me in a channel or private chat.")
                return
            self.send(f"PRIVMSG {buf.name} :\x01ACTION {arg}\x01")
            body, _ = self.chat_line(self.nick, arg, action=True, mine=True)
            self.add(buf.name, body, count=False)
        elif cmd == "notice":
            target, _, text = arg.partition(" ")
            self.send(f"NOTICE {target} :{text}")
            self.add(self.active_key, [(f"-> -{target}- {text}", "magenta")], count=False)
        elif cmd == "topic":
            if buf.kind != "channel":
                self.hint("Use /topic in a channel.")
            elif arg:
                self.send(f"TOPIC {buf.name} :{arg}")
            else:
                self.send(f"TOPIC {buf.name}")
        elif cmd in {"whois", "wi"}:
            self.send(f"WHOIS {arg or (buf.name if buf.kind == 'query' else self.nick)}")
        elif cmd == "names":
            if buf.kind == "channel":
                buf.users.clear()
                self.send(f"NAMES {buf.name}")
        elif cmd == "list":
            self.switch(SERVER.lower())
            self.send("LIST" + (f" {arg}" if arg else ""))
        elif cmd in {"quote", "raw"}:
            self.send(arg)
            self.info(self.active_key, f"sent: {arg}")
        else:
            self.error(f"Unknown command /{cmd}. Type /help for the list.")
