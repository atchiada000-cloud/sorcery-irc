"""Sorcery — a small terminal IRC client that only talks to SorceryNet."""

from __future__ import annotations

import json
import re
import ssl
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .irc import HOST, PORT, Connection, Message, parse, strip_formatting

CONFIG = Path.home() / ".config" / "sorcery-irc" / "config.json"
SERVER = "SorceryNet"
VERSION = "Sorcery 0.1 — a custom SorceryNet client"

# RFC 2812-style nick: a letter or special first, then letters, digits,
# specials or '-'. No dots or spaces.
NICK_RE = re.compile(r"^[A-Za-z\[\]\\`_^{|}][A-Za-z0-9\[\]\\`_^{|}-]{0,29}$")
NICK_RULES = (
    "Nicks can use letters, numbers and [ ] \\ ` _ ^ { | } -, can't start with a "
    "number or '-', and can't contain dots or spaces (max 30 characters)."
)
NICK_COLOURS = ["red", "green", "yellow", "blue", "magenta", "cyan",
                "bright_red", "bright_green", "bright_yellow", "bright_blue",
                "bright_magenta", "bright_cyan"]
RANKS = {"~": 0, "&": 1, "@": 2, "%": 3, "+": 4}
SERVICES = {"nickserv", "chanserv", "memoserv", "operserv", "hostserv", "botserv"}

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
  Ctrl+N / Ctrl+P  next / previous window    Alt+1..9  jump to window
  Tab              complete a nick           Ctrl+W    close window     Ctrl+Q  quit
Registered nicks
  If it's yours:  /ns IDENTIFY <password>     Register yours:  /ns REGISTER <password> <email>
  Leave a note for someone offline:  /ms SEND <nick> <message>"""


def nick_colour(nick: str) -> str:
    return NICK_COLOURS[zlib.crc32(nick.lower().encode()) % len(NICK_COLOURS)]


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
    lines: list[Text] = field(default_factory=list)
    users: dict[str, str] = field(default_factory=dict)  # nick -> mode prefix
    topic: str = ""
    unread: int = 0
    highlight: bool = False
    joined: bool = False


class NickScreen(ModalScreen[str]):
    """Asks for a nick before connecting."""

    DEFAULT_CSS = """
    NickScreen { align: center middle; }
    #nickbox { width: 64; height: auto; border: round $accent; padding: 1 2; background: $surface; }
    #nickbox Static { margin-bottom: 1; }
    #nickerror { color: $error; }
    """

    def __init__(self, suggestion: str = "") -> None:
        super().__init__()
        self.suggestion = suggestion

    def compose(self) -> ComposeResult:
        with Vertical(id="nickbox"):
            yield Static(Text("❦ SorceryNet", style="bold"))
            yield Static("Pick a nick for this session. Any nick you like — if it's taken "
                         "or registered, you'll be shown how to switch.")
            yield Input(value=self.suggestion, placeholder="nick", id="nickinput")
            yield Static("", id="nickerror")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        nick = event.value.strip()
        if NICK_RE.match(nick):
            self.dismiss(nick)
        else:
            self.query_one("#nickerror", Static).update(NICK_RULES)


class ChatInput(Input):
    """Input with Tab nick-completion."""

    def __init__(self, app_ref: "Sorcery", **kw) -> None:
        super().__init__(**kw)
        self.app_ref = app_ref
        self._cycle: tuple[int, list[str], int] | None = None

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "tab":
            event.prevent_default()
            event.stop()
            self.complete()
            return
        self._cycle = None
        await super()._on_key(event)

    def complete(self) -> None:
        # Repeated Tabs cycle through matches; any other key resets.
        if self._cycle:
            start, matches, i = self._cycle
            i = (i + 1) % len(matches)
        else:
            value = self.value[: self.cursor_position]
            start = value.rfind(" ") + 1
            stem = value[start:]
            if not stem:
                return
            nicks = sorted(self.app_ref.active.users, key=str.lower)
            matches = [n for n in nicks if n.lower().startswith(stem.lower())]
            if not matches:
                return
            i = 0
        suffix = ": " if start == 0 else " "
        completion = matches[i] + suffix
        rest = self.value[self.cursor_position:]
        self.value = self.value[:start] + completion + rest
        self.cursor_position = start + len(completion)
        self._cycle = (start, matches, i)


class Sorcery(App):
    TITLE = "Sorcery"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #topic { height: 1; padding: 0 1; background: $panel; color: $text; }
    #main { height: 1fr; }
    #buffers { width: 24; border: none; border-right: solid $panel; }
    #log { width: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }
    #users { width: 20; border: none; border-left: solid $panel; }
    #users.hidden { display: none; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    #input { border: none; height: 1; padding: 0 1; }
    OptionList > .option-list--option-highlighted { text-style: bold; }
    """
    BINDINGS = [
        Binding("ctrl+n", "cycle(1)", "Next", priority=True),
        Binding("ctrl+p", "cycle(-1)", "Prev", priority=True),
        Binding("ctrl+w", "close_buffer", "Close", priority=True),
        Binding("ctrl+q", "quit_irc", "Quit", priority=True),
        *[Binding(f"alt+{n}", f"jump({n - 1})", show=False, priority=True) for n in range(1, 10)],
    ]

    def __init__(self) -> None:
        super().__init__()
        self.theme = "ansi-dark"  # follow the terminal's (Omarchy) colours
        self.cfg = load_config()
        self.conn = Connection()
        self.nick = self.cfg.get("nick", "")
        self.registered = False
        self.buffers: dict[str, Buffer] = {SERVER.lower(): Buffer(SERVER, "server")}
        self.order: list[str] = [SERVER.lower()]
        self.active_key = SERVER.lower()
        self.quitting = False

    # ── layout ────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Static("", id="topic")
        with Horizontal(id="main"):
            yield OptionList(id="buffers")
            yield RichLog(id="log", wrap=True, markup=False, highlight=False, max_lines=3000)
            yield OptionList(id="users", classes="hidden")
        yield Static("", id="status")
        yield ChatInput(self, id="input", placeholder="")

    def on_mount(self) -> None:
        self.refresh_ui()
        self.push_screen(NickScreen(self.nick), self.start)

    def start(self, nick: str | None) -> None:
        if not nick:
            self.exit()
            return
        self.nick = nick
        self.query_one("#input").focus()
        self.run_irc()

    @property
    def active(self) -> Buffer:
        return self.buffers[self.active_key]

    # ── output helpers ────────────────────────────────────────────────────
    def stamp(self) -> Text:
        return Text(time.strftime("%H:%M "), style="dim")

    def add(self, key: str, body: Text, *, count: bool = True, hl: bool = False) -> None:
        buf = self.buffers.get(key.lower()) or self.buffers[SERVER.lower()]
        line = self.stamp() + body
        buf.lines.append(line)
        del buf.lines[:-3000]
        if buf is self.active:
            self.query_one("#log", RichLog).write(line)
        elif count:
            buf.unread += 1
            buf.highlight = buf.highlight or hl
            self.refresh_sidebar()

    def info(self, key: str, text: str, style: str = "dim italic") -> None:
        self.add(key, Text("── " + text, style=style), count=False)

    def error(self, text: str, key: str | None = None) -> None:
        self.add(key or self.active_key, Text("✖ " + text, style="bold red"), count=False)

    def hint(self, text: str, key: str | None = None) -> None:
        self.add(key or self.active_key, Text(text, style="yellow"), count=False)

    def chat_line(self, nick: str, text: str, *, action=False, notice=False, mine=False) -> tuple[Text, bool]:
        text = strip_formatting(text)
        hl = not mine and bool(self.nick) and re.search(rf"\b{re.escape(self.nick)}\b", text, re.I) is not None
        colour = "bold" if mine else nick_colour(nick)
        if action:
            body = Text("* ", style=colour) + Text(nick, style=colour) + Text(" " + text, style="italic")
        elif notice:
            body = Text(f"-{nick}- ", style="bold magenta") + Text(text)
        else:
            body = Text("<", style="dim") + Text(nick, style=colour) + Text("> ", style="dim") + Text(text)
        if hl:
            body.stylize("reverse", 0, len(body))
        return body, hl

    def buffer(self, name: str, kind: str, switch: bool = False) -> Buffer:
        key = name.lower()
        if key not in self.buffers:
            self.buffers[key] = Buffer(name, kind)
            self.order.append(key)
        if switch:
            self.switch(key)
        else:
            self.refresh_sidebar()
        return self.buffers[key]

    # ── UI refresh ────────────────────────────────────────────────────────
    def switch(self, key: str) -> None:
        self.active_key = key
        buf = self.active
        buf.unread, buf.highlight = 0, False
        log = self.query_one("#log", RichLog)
        log.clear()
        for line in buf.lines:
            log.write(line)
        self.refresh_ui()

    def refresh_ui(self) -> None:
        self.refresh_sidebar()
        self.refresh_users()
        buf = self.active
        title = Text(f" {buf.name} ", style="bold")
        if buf.topic:
            title += Text("— " + strip_formatting(buf.topic), style="")
        elif buf.kind == "server":
            title += Text(f"— {self.conn.server_name if self.conn.connected else HOST}:{PORT} (TLS)", style="dim")
        self.query_one("#topic", Static).update(title)
        state = "connected" if self.registered else ("connecting…" if self.conn.connected else "offline")
        status = Text(f"[{self.nick or '?'}] ", style="bold") + Text(f"{state} · {buf.name}", style="dim")
        if buf.kind == "channel":
            status += Text(f" · {len(buf.users)} users", style="dim")
        self.query_one("#status", Static).update(status)
        target = buf.name if buf.kind != "server" else "type /join #channel or /help"
        self.query_one("#input", Input).placeholder = f"message {target}" if buf.kind != "server" else target

    def refresh_sidebar(self) -> None:
        ol = self.query_one("#buffers", OptionList)
        ol.clear_options()
        for i, key in enumerate(self.order):
            b = self.buffers[key]
            label = Text(("" if b.kind == "server" else "  ") + b.name)
            if b.kind == "channel" and not b.joined:
                label.stylize("dim")
            if b.unread:
                label += Text(f" {b.unread}", style="bold red" if b.highlight else "bold yellow")
            ol.add_option(Option(label, id=key))
        ol.highlighted = self.order.index(self.active_key)

    def refresh_users(self) -> None:
        ul = self.query_one("#users", OptionList)
        buf = self.active
        ul.set_class(buf.kind != "channel", "hidden")
        if buf.kind != "channel":
            return
        ul.clear_options()
        users = sorted(buf.users.items(), key=lambda kv: (RANKS.get(kv[1][:1], 9), kv[0].lower()))
        for nick, mode in users:
            ul.add_option(Option(Text(mode[:1] or " ", style="bold green") + Text(nick, style=nick_colour(nick))))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "buffers" and event.option.id:
            self.switch(event.option.id)
        elif event.option_list.id == "users":
            nick = str(event.option.prompt)[1:]
            self.buffer(nick, "query", switch=True)
        self.query_one("#input").focus()

    # ── actions ───────────────────────────────────────────────────────────
    def action_cycle(self, step: int) -> None:
        i = (self.order.index(self.active_key) + step) % len(self.order)
        self.switch(self.order[i])

    def action_jump(self, i: int) -> None:
        if i < len(self.order):
            self.switch(self.order[i])

    def action_close_buffer(self) -> None:
        self.close_buffer(self.active_key)

    def action_quit_irc(self) -> None:
        self.quit_irc("")

    def close_buffer(self, key: str, reason: str = "") -> None:
        buf = self.buffers[key]
        if buf.kind == "server":
            return
        if buf.kind == "channel" and buf.joined:
            self.conn.send(f"PART {buf.name}" + (f" :{reason}" if reason else ""))
        i = self.order.index(key)
        self.order.remove(key)
        del self.buffers[key]
        self.remember_channels()
        self.switch(self.order[max(0, i - 1)])

    def quit_irc(self, message: str) -> None:
        self.quitting = True
        self.conn.send("QUIT :" + (message or "Sorcery, signing off"))
        self.conn.close()
        self.exit()

    def remember_channels(self) -> None:
        self.cfg["channels"] = [b.name for b in self.buffers.values() if b.kind == "channel" and b.joined]
        save_config(self.cfg)

    # ── networking ────────────────────────────────────────────────────────
    @work(exclusive=True, group="irc")
    async def run_irc(self) -> None:
        self.registered = False
        self.info(SERVER, f"Connecting to {HOST}:{PORT} (TLS)…")
        try:
            await self.conn.connect()
        except (OSError, ssl.SSLError) as e:
            self.error(f"Couldn't connect: {e}. Type /reconnect to try again.", SERVER)
            self.refresh_ui()
            return
        self.info(SERVER, f"Connected to {self.conn.server_name}. Signing in as {self.nick}…")
        self.conn.send(f"NICK {self.nick}")
        self.conn.send(f"USER {self.nick} 0 * :{self.nick}")
        self.refresh_ui()
        async for line in self.conn.lines():
            try:
                self.handle(parse(line))
            except Exception as e:  # one bad line shouldn't kill the session
                self.error(f"(couldn't handle: {line[:80]!r} — {e})", SERVER)
        self.registered = False
        for b in self.buffers.values():
            b.joined = False
        if not self.quitting:
            self.error("Disconnected from SorceryNet. Type /reconnect to connect again.", self.active_key)
        self.refresh_ui()

    def nick_help(self, nick: str, why: str, key: str | None = None) -> None:
        self.hint(f"⚠ {why}", key)
        self.hint(f"   Choose another with:  /nick NewNick", key)

    def handle(self, m: Message) -> None:  # noqa: C901 — one big dispatcher reads best here
        c, p = m.command, m.params
        if c == "PING":
            self.conn.send(f"PONG :{m.text}")
            return
        if c == "ERROR":
            self.error(m.text, SERVER)
            return

        # ── numerics ──
        if c == "001":
            self.registered = True
            self.nick = p[0]
            self.cfg["nick"] = self.nick  # only remember nicks the server accepted
            save_config(self.cfg)
            self.info(SERVER, f"Welcome to SorceryNet, {self.nick}. Type /help for commands.", "bold green")
            for ch in self.cfg.get("channels", []):
                self.conn.send(f"JOIN {ch}")
            self.refresh_ui()
        elif c in {"002", "003", "251", "252", "254", "255", "265", "266", "372", "375", "376", "422"}:
            self.add(SERVER, Text(strip_formatting(" ".join(p[1:]))), count=False)
        elif c in {"433", "436"}:  # nick in use / collision
            bad = p[1] if len(p) > 1 else "?"
            self.nick_help(bad, f"The nick '{bad}' is already in use.", None if self.registered else SERVER)
            if not self.registered:
                self.switch(SERVER.lower())
        elif c == "432":
            bad = p[1] if len(p) > 1 else "?"
            self.nick_help(bad, f"'{bad}' isn't allowed as a nick. {NICK_RULES}", None if self.registered else SERVER)
        elif c == "437":
            self.nick_help(p[1], f"'{p[1]}' is temporarily unavailable.")
        elif c == "332":
            b = self.buffer(p[1], "channel")
            b.topic = p[2]
            self.info(p[1], "Topic: " + strip_formatting(p[2]), "dim")
            self.refresh_ui()
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
            if p[1].lower() == self.active_key:
                self.refresh_ui()
        elif c == "321":
            self.info(SERVER, "Channel list:", "bold")
        elif c == "322":
            topic = strip_formatting(re.sub(r"^\[\+[^\]]*\] ?", "", p[3] if len(p) > 3 else ""))
            if "Fake channel for confusing spambots" in topic:
                return
            self.add(SERVER, Text(f"{p[1]:<24}", style="bold") + Text(f"{p[2]:>4}  ", style="cyan") + Text(topic[:100]), count=False)
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
            self.add(self.active_key, Text("ⓘ " + strip_formatting(text), style="cyan"), count=False)
        elif c in {"401", "402", "403", "404", "405", "406", "421", "441", "442", "443", "461", "471", "473", "474", "475", "477", "482"}:
            self.error(" ".join(p[1:]))
        elif c.isdigit():
            self.add(SERVER, Text(strip_formatting(" ".join(p[1:])), style="dim"), count=False)

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
                elif ctcp.upper() == "VERSION":
                    self.conn.send(f"NOTICE {m.nick} :\x01VERSION {VERSION}\x01")
                elif ctcp.upper() == "PING":
                    self.conn.send(f"NOTICE {m.nick} :\x01PING {arg}\x01")
                return
            body, hl = self.chat_line(m.nick, text)
            self.buffer(key, "channel" if is_channel(key) else "query")
            self.add(key, body, hl=hl or not is_channel(key))
        elif c == "NOTICE":
            sender = m.nick or self.conn.server_name
            text = m.text
            if sender.lower() in SERVICES:
                body, _ = self.chat_line(sender, text, notice=True)
                self.add(self.active_key if self.registered else SERVER, body, count=False)
                if sender.lower() == "nickserv" and re.search(r"nickname is registered|is registered and protected", text, re.I):
                    self.hint(f"⚠ '{self.nick}' is a registered nick.  If it's yours:  /ns IDENTIFY <password>")
                    self.hint("   Otherwise choose another with:  /nick NewNick   (NickServ may rename you soon)")
            elif "!" not in m.prefix or not self.registered:
                self.add(SERVER, Text(strip_formatting(text), style="dim"), count=False)
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
                self.add(ch, Text(f"→ {m.nick} joined", style="dim green"), count=False)
            if ch.lower() == self.active_key:
                self.refresh_ui()
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
                self.add(ch, Text(f"← {who} {verb}" + (f" ({reason})" if reason else ""), style="dim"), count=False)
            self.refresh_ui()
        elif c == "QUIT":
            for b in self.buffers.values():
                if m.nick in b.users:
                    b.users.pop(m.nick)
                    self.add(b.name, Text(f"← {m.nick} quit ({strip_formatting(m.text)})", style="dim"), count=False)
            self.refresh_users()
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
                    self.add(b.name, Text(f"{old} is now {new}", style="dim"), count=False)
            if old.lower() in self.buffers and self.buffers[old.lower()].kind == "query":
                b = self.buffers.pop(old.lower())
                b.name = new
                self.buffers[new.lower()] = b
                self.order[self.order.index(old.lower())] = new.lower()
                if self.active_key == old.lower():
                    self.active_key = new.lower()
            self.refresh_ui()
        elif c == "TOPIC":
            b = self.buffer(p[0], "channel")
            b.topic = m.text
            self.info(p[0], f"{m.nick} set the topic: {strip_formatting(m.text)}")
            self.refresh_ui()
        elif c == "MODE" and is_channel(p[0]):
            self.info(p[0], f"{m.nick} sets mode {' '.join(p[1:])}")
            self.conn.send(f"NAMES {p[0]}")
            self.buffers.get(p[0].lower(), Buffer("", "")).users.clear()
        elif c == "INVITE":
            self.hint(f"{m.nick} invited you to {m.text}.  Join with:  /join {m.text}")

    # ── input ─────────────────────────────────────────────────────────────
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "input":
            return
        line = event.value
        event.input.value = ""
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
        self.conn.send(f"PRIVMSG {name} :{text}")
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
                self.add(self.active_key, Text(l, style="cyan" if not l.startswith(" ") else ""), count=False)
            return
        if cmd == "clear":
            buf.lines.clear()
            self.query_one("#log", RichLog).clear()
            return
        if cmd in {"quit", "exit"}:
            self.quit_irc(arg)
            return
        if cmd in {"reconnect", "connect", "server"}:
            if self.conn.connected:
                self.conn.send("QUIT :reconnecting")
                self.conn.close()
            self.conn = Connection()
            self.run_irc()
            return
        if cmd == "close":
            self.close_buffer(self.active_key, arg)
            return
        if cmd == "nick":
            if not arg:
                self.hint(f"Your nick is {self.nick}. Change it with:  /nick NewNick")
            elif not NICK_RE.match(arg):
                self.nick_help(arg, f"'{arg}' isn't a valid nick. {NICK_RULES}")
            else:
                if not self.registered:
                    self.nick = arg
                self.conn.send(f"NICK {arg}")
            return
        if not self.conn.connected:
            self.error("Not connected. Type /reconnect.")
            return
        if cmd in {"join", "j"}:
            if not arg:
                self.hint("Usage:  /join #channel")
                return
            ch = arg.split()[0]
            ch = ch if is_channel(ch) else "#" + ch
            self.conn.send(f"JOIN {ch}" + (" " + arg.split()[1] if len(arg.split()) > 1 else ""))
        elif cmd in {"part", "leave"}:
            if buf.kind != "channel":
                self.hint("Use /part in a channel window (or /close).")
                return
            self.conn.send(f"PART {buf.name}" + (f" :{arg}" if arg else ""))
        elif cmd in {"msg", "m"}:
            target, _, text = arg.partition(" ")
            if not text:
                self.hint("Usage:  /msg nick message")
                return
            if target.lower() in SERVICES:
                self.conn.send(f"PRIVMSG {target} :{text}")
                shown = text.split()[0] + " ••••••" if text.split()[0].upper() in {"IDENTIFY", "REGISTER", "GHOST", "RECOVER"} else text
                self.add(self.active_key, Text(f"→ {target}: {shown}", style="magenta"), count=False)
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
            self.buffer(target, "query", switch=True)
            if text:
                self.say(text, target)
        elif cmd == "me":
            if buf.kind == "server":
                self.hint("Use /me in a channel or private chat.")
                return
            self.conn.send(f"PRIVMSG {buf.name} :\x01ACTION {arg}\x01")
            body, _ = self.chat_line(self.nick, arg, action=True, mine=True)
            self.add(buf.name, body, count=False)
        elif cmd == "notice":
            target, _, text = arg.partition(" ")
            self.conn.send(f"NOTICE {target} :{text}")
            self.add(self.active_key, Text(f"-> -{target}- {text}", style="magenta"), count=False)
        elif cmd == "topic":
            if buf.kind != "channel":
                self.hint("Use /topic in a channel.")
            elif arg:
                self.conn.send(f"TOPIC {buf.name} :{arg}")
            else:
                self.conn.send(f"TOPIC {buf.name}")
        elif cmd in {"whois", "wi"}:
            self.conn.send(f"WHOIS {arg or (buf.name if buf.kind == 'query' else self.nick)}")
        elif cmd == "names":
            if buf.kind == "channel":
                buf.users.clear()
                self.conn.send(f"NAMES {buf.name}")
        elif cmd == "list":
            self.switch(SERVER.lower())
            self.conn.send("LIST" + (f" {arg}" if arg else ""))
        elif cmd in {"quote", "raw"}:
            self.conn.send(arg)
            self.info(self.active_key, f"sent: {arg}")
        else:
            self.error(f"Unknown command /{cmd}. Type /help for the list.")


def main() -> None:
    Sorcery().run()


if __name__ == "__main__":
    main()
