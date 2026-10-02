"""Sorcery desktop app — a GTK4 / libadwaita SorceryNet client.

Colours follow the current Omarchy theme and update live when it changes.
Run with the system Python (it needs PyGObject):  python3 -m sorcery_irc.gui
"""

from __future__ import annotations

import re
import sys
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .core import NICK_RE, NICK_RULES, RANKS, SERVER, Client, Line, nick_tag  # noqa: E402
from .irc import strip_formatting  # noqa: E402
from .net import NetThread  # noqa: E402
from .theme import NICK_PALETTE, THEME_STATE, load_theme  # noqa: E402

APP_ID = "com.kenobi.Sorcery"
MAX_LINES = 3000
URL_RE = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]]")


def theme_css(c: dict) -> str:
    return f"""
:root {{
  --window-bg-color: {c['background']};  --window-fg-color: {c['foreground']};
  --view-bg-color: {c['background']};    --view-fg-color: {c['foreground']};
  --headerbar-bg-color: {c['dark_background']}; --headerbar-fg-color: {c['foreground']};
  --headerbar-backdrop-color: {c['dark_background']};
  --sidebar-bg-color: {c['dark_background']}; --sidebar-fg-color: {c['foreground']};
  --sidebar-backdrop-color: {c['dark_background']};
  --accent-bg-color: {c['accent']}; --accent-fg-color: {c['background']}; --accent-color: {c['accent']};
  --popover-bg-color: {c['lighter_background']}; --popover-fg-color: {c['foreground']};
  --dialog-bg-color: {c['lighter_background']};  --dialog-fg-color: {c['foreground']};
  --card-bg-color: {c['lighter_background']};    --card-fg-color: {c['foreground']};
}}
.pane {{ background: {c['dark_background']}; }}
.pane-title {{ color: {c['light_foreground']}; font-size: smaller; font-weight: bold; letter-spacing: 1px; }}
.status {{ color: {c['light_foreground']}; font-size: smaller; }}
.status.online {{ color: {c['green']}; }}
.badge {{ background: {c['muted']}; color: {c['foreground']}; border-radius: 9px;
          padding: 0 7px; font-size: smaller; font-weight: bold; }}
.badge.hl {{ background: {c['red']}; color: {c['background']}; }}
.parted {{ opacity: 0.5; }}
row .close {{ opacity: 0; min-height: 20px; min-width: 20px; padding: 0; }}
row:hover .close {{ opacity: 0.7; }}
textview.chat, textview.chat text {{ background: {c['background']}; color: {c['foreground']};
                                      font-family: "JetBrainsMono Nerd Font", monospace; }}
entry.chat-entry {{ background: {c['lighter_background']}; color: {c['foreground']}; min-height: 36px; }}
"""


class BufferRow(Gtk.ListBoxRow):
    def __init__(self, win: "Window", key: str) -> None:
        super().__init__()
        self.key = key
        box = Gtk.Box(spacing=6, margin_start=6, margin_end=4, margin_top=3, margin_bottom=3)
        self.label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.badge = Gtk.Label(visible=False, css_classes=["badge"])
        box.append(self.label)
        box.append(self.badge)
        if key != SERVER.lower():
            close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular", "close"],
                               tooltip_text="Close", valign=Gtk.Align.CENTER)
            close.connect("clicked", lambda *_: win.client.close_buffer(self.key))
            box.append(close)
        self.set_child(box)
        middle = Gtk.GestureClick(button=Gdk.BUTTON_MIDDLE)
        middle.connect("released", lambda *_: win.client.close_buffer(self.key))
        self.add_controller(middle)


class Window(Adw.ApplicationWindow):
    def __init__(self, app: "SorceryApp") -> None:
        super().__init__(application=app, title="Sorcery")
        self.set_default_size(1100, 700)
        self.app = app
        self.colours = load_theme()
        self.tags = Gtk.TextTagTable()
        self.make_tags()
        self.texts: dict[str, Gtk.TextBuffer] = {}
        self.rows: dict[str, BufferRow] = {}
        self.row_order: list[str] = []
        self.shown_users: tuple = ()
        self.refresh_queued = False
        self.history: list[str] = []
        self.history_pos = 0
        self.completion: tuple[int, list[str], int] | None = None

        self.net = NetThread(lambda fn: GLib.idle_add(lambda: (fn(), False)[1]))
        self.client = Client(self, self.net)
        self.net.client = self.client

        self.build()
        self.connect("close-request", self.on_close_request)
        self.refresh()

    # ── layout ────────────────────────────────────────────────────────────
    def build(self) -> None:
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Sorcery")
        header.set_title_widget(self.title)

        menu = Gio.Menu()
        menu.append("Browse channels", "win.list")
        menu.append("Reconnect", "win.reconnect")
        menu.append("Help", "win.help")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu"))
        self.users_toggle = Gtk.ToggleButton(icon_name="system-users-symbolic", active=True, tooltip_text="People")
        self.users_toggle.connect("toggled", lambda *_: self.refresh())
        header.pack_end(self.users_toggle)
        join = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Join a channel")
        join.connect("clicked", lambda *_: self.ask_join())
        header.pack_start(join)

        for name, fn in {
            "list": lambda: self.client.command("list"),
            "reconnect": self.client.reconnect,
            "help": lambda: self.client.command("help"),
            "next": lambda: self.client.cycle(1),
            "prev": lambda: self.client.cycle(-1),
            "close-buffer": lambda: self.client.close_buffer(self.client.active_key),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_, fn=fn: fn())
            self.add_action(action)

        # Sidebar: windows (server, channels, private chats) and connection status.
        self.buffer_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.buffer_list.set_sort_func(self.sort_rows)
        self.buffer_list.connect("row-selected", self.on_row_selected)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=200, css_classes=["pane"])
        sidebar.append(Gtk.ScrolledWindow(child=self.buffer_list, vexpand=True,
                                          hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.status = Gtk.Label(xalign=0, margin_start=12, margin_end=12, margin_top=8, margin_bottom=10,
                                ellipsize=Pango.EllipsizeMode.END, css_classes=["status"])
        sidebar.append(self.status)

        # Chat: the scrollback and the input line.
        self.view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, css_classes=["chat"],
                                 left_margin=14, right_margin=14, top_margin=10, bottom_margin=10)
        click = Gtk.GestureClick()
        click.connect("released", self.on_view_click)
        self.view.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_view_motion)
        self.view.add_controller(motion)
        self.scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True, hexpand=True)
        self.entry = Gtk.Entry(css_classes=["chat-entry"], margin_start=10, margin_end=10,
                               margin_top=6, margin_bottom=10)
        self.entry.connect("activate", self.on_entry_activate)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_entry_key)
        self.entry.add_controller(keys)
        chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        chat.append(self.scroller)
        chat.append(self.entry)

        # People in the current channel.
        self.user_list = Gtk.ListBox(css_classes=["navigation-sidebar"], activate_on_single_click=False)
        self.user_list.connect("row-activated", lambda _l, row: self.client.open_query(row.nick))
        self.user_title = Gtk.Label(xalign=0, margin_start=12, margin_top=10, margin_bottom=2,
                                    css_classes=["pane-title"])
        users = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=180, css_classes=["pane"])
        users.append(self.user_title)
        users.append(Gtk.ScrolledWindow(child=self.user_list, vexpand=True,
                                        hscrollbar_policy=Gtk.PolicyType.NEVER))
        users_box = Gtk.Box()
        users_box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        users_box.append(users)
        self.users_revealer = Gtk.Revealer(child=users_box, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT)

        body = Gtk.Box()
        body.append(sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        body.append(chat)
        body.append(self.users_revealer)

        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.set_content(view)

        win_keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        win_keys.connect("key-pressed", self.on_window_key)
        self.add_controller(win_keys)

    def make_tags(self) -> None:
        for name in ["time", "dim", "italic", "bold", "error", "hint", "green", "yellow", "magenta",
                     "cyan", "mine", "highlight", "link", "link-channel",
                     *[f"nick{i}" for i in range(len(NICK_PALETTE))]]:
            self.tags.add(Gtk.TextTag(name=name))
        self.apply_tag_colours()

    def apply_tag_colours(self) -> None:
        c = self.colours
        look = {
            "time": {"foreground": c["light_foreground"], "scale": 0.9},
            "dim": {"foreground": c["light_foreground"]},
            "italic": {"style": Pango.Style.ITALIC},
            "bold": {"weight": Pango.Weight.BOLD},
            "error": {"foreground": c["red"], "weight": Pango.Weight.BOLD},
            "hint": {"foreground": c["yellow"]},
            "green": {"foreground": c["green"]},
            "yellow": {"foreground": c["yellow"]},
            "magenta": {"foreground": c["magenta"]},
            "cyan": {"foreground": c["cyan"]},
            "mine": {"foreground": c["foreground"], "weight": Pango.Weight.BOLD},
            "highlight": {"paragraph_background": c["selection"]},
            "link": {"foreground": c["accent"], "underline": Pango.Underline.SINGLE},
            "link-channel": {"foreground": c["accent"]},
        }
        for i, colour in enumerate(NICK_PALETTE):
            look[f"nick{i}"] = {"foreground": c.get(colour, c["foreground"])}
        for name, props in look.items():
            tag = self.tags.lookup(name)
            for prop, value in props.items():
                tag.set_property(prop, value)

    def reload_theme(self) -> None:
        self.colours = load_theme()
        self.apply_tag_colours()
        self.shown_users = ()  # user list colours come from the theme too
        self.refresh()

    # ── Frontend (called by the client) ───────────────────────────────────
    def text_for(self, key: str) -> Gtk.TextBuffer:
        if key not in self.texts:
            buf = Gtk.TextBuffer(tag_table=self.tags)
            buf.create_mark("end", buf.get_end_iter(), False)
            self.texts[key] = buf
        return self.texts[key]

    def show_line(self, key: str, line: Line) -> None:
        buf = self.text_for(key)
        active = key == self.client.active_key
        adj = self.scroller.get_vadjustment()
        at_bottom = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 40
        if buf.get_char_count():
            buf.insert(buf.get_end_iter(), "\n")
        for text, tags in line:
            names = tags.split()
            pos = 0
            for m in URL_RE.finditer(text):  # make web links clickable
                self.insert(buf, text[pos:m.start()], names)
                self.insert(buf, m.group(), names + ["link"])
                pos = m.end()
            self.insert(buf, text[pos:], names)
        extra = buf.get_line_count() - MAX_LINES
        if extra > 0:
            buf.delete(buf.get_start_iter(), buf.get_iter_at_line(extra)[1])
        if active and at_bottom:
            self.scroll_to_end()

    @staticmethod
    def insert(buf: Gtk.TextBuffer, text: str, tags: list[str]) -> None:
        if text:
            buf.insert_with_tags_by_name(buf.get_end_iter(), text, *tags)

    def clear_lines(self, key: str) -> None:
        self.text_for(key).set_text("")

    def rename_buffer(self, old_key: str, new_key: str) -> None:
        if old_key in self.texts:
            self.texts[new_key] = self.texts.pop(old_key)
        if old_key in self.rows:
            row = self.rows.pop(old_key)
            row.key = new_key
            self.rows[new_key] = row

    def changed(self) -> None:
        if not self.refresh_queued:  # many events arrive at once; redraw once
            self.refresh_queued = True
            GLib.idle_add(self.refresh)

    def notify(self, key: str, title: str, body: str) -> None:
        if self.is_active() and key == self.client.active_key:
            return
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_default_action_and_target_value("app.show-buffer", GLib.Variant("s", key))
        self.app.send_notification(f"sorcery-{key}", n)

    def later(self, seconds: int, fn: Callable[[], None]) -> None:
        GLib.timeout_add_seconds(seconds, lambda: (fn(), False)[1])

    def quit(self) -> None:
        # Give the QUIT line a moment to reach the server.
        GLib.timeout_add(300, lambda: (self.app.quit(), False)[1])

    # ── redraw ────────────────────────────────────────────────────────────
    def refresh(self) -> bool:
        self.refresh_queued = False
        cl = self.client
        buf = cl.active

        # Sidebar rows: update in place, so the list doesn't jump while you click.
        for key in list(self.rows):
            if key not in cl.buffers:
                self.buffer_list.remove(self.rows.pop(key))
        for key in cl.order:
            if key not in self.rows:
                self.rows[key] = BufferRow(self, key)
                self.buffer_list.append(self.rows[key])
            b, row = cl.buffers[key], self.rows[key]
            row.label.set_label(b.name)
            row.label.set_css_classes(["heading"] if b.kind == "server"
                                      else ["parted"] if b.kind == "channel" and not b.joined else [])
            row.badge.set_visible(b.unread > 0)
            row.badge.set_label(str(b.unread))
            row.badge.set_css_classes(["badge", "hl"] if b.highlight else ["badge"])
        if self.row_order != cl.order:
            self.row_order = list(cl.order)
            self.buffer_list.invalidate_sort()
        selected = self.buffer_list.get_selected_row()
        if not selected or selected.key != cl.active_key:
            self.buffer_list.select_row(self.rows[cl.active_key])

        # Scrollback.
        text = self.text_for(cl.active_key)
        if self.view.get_buffer() is not text:
            self.view.set_buffer(text)
            self.scroll_to_end()

        # Title, status and input hint.
        self.title.set_title(buf.name)
        if buf.topic:
            self.title.set_subtitle(strip_formatting(buf.topic))
        elif buf.kind == "server":
            self.title.set_subtitle(f"{cl.server_name} · TLS" if cl.connected else "irc.sorcery.net · TLS")
        else:
            self.title.set_subtitle("private chat" if buf.kind == "query" else "")
        self.title.set_tooltip_text(self.title.get_subtitle() or None)
        self.status.set_label(f"● {cl.nick or '?'} · {cl.state}")
        self.status.set_css_classes(["status", "online"] if cl.registered else ["status"])
        self.entry.set_placeholder_text(
            f"Message {buf.name}" if buf.kind != "server" else "Type /join #channel, or /help")
        self.set_title(f"{buf.name} — Sorcery")

        # People list.
        show_users = buf.kind == "channel" and self.users_toggle.get_active()
        self.users_revealer.set_reveal_child(show_users)
        self.users_toggle.set_sensitive(buf.kind == "channel")
        if buf.kind == "channel":
            users = tuple(sorted(buf.users.items(), key=lambda kv: (RANKS.get(kv[1][:1], 9), kv[0].lower())))
            self.user_title.set_label(f"PEOPLE · {len(users)}")
            if users != self.shown_users:
                self.shown_users = users
                self.user_list.remove_all()
                for nick, mode in users:
                    self.user_list.append(self.user_row(nick, mode))
        return False

    def user_row(self, nick: str, mode: str) -> Gtk.ListBoxRow:
        c = self.colours
        colour = c.get(NICK_PALETTE[int(nick_tag(nick)[4:])], c["foreground"])
        rank = GLib.markup_escape_text(mode[:1] or " ")
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, margin_start=6,
                          use_markup=True, tooltip_text=f"Double-click to message {nick}")
        label.set_markup(f"<span foreground='{c['green']}' weight='bold'>{rank}</span> "
                         f"<span foreground='{colour}'>{GLib.markup_escape_text(nick)}</span>")
        row = Gtk.ListBoxRow(child=label)
        row.nick = nick
        return row

    def scroll_to_end(self) -> None:
        def scroll() -> bool:
            text = self.view.get_buffer()
            text.move_mark(text.get_mark("end"), text.get_end_iter())
            self.view.scroll_to_mark(text.get_mark("end"), 0, False, 0, 1)
            return False
        GLib.idle_add(scroll)

    def sort_rows(self, a: BufferRow, b: BufferRow) -> int:
        order = self.client.order
        return (order.index(a.key) if a.key in order else 999) - (order.index(b.key) if b.key in order else 999)

    # ── events ────────────────────────────────────────────────────────────
    def on_row_selected(self, _list: Gtk.ListBox, row: BufferRow | None) -> None:
        if row and row.key != self.client.active_key:
            self.client.switch(row.key)
            self.entry.grab_focus()

    def on_entry_activate(self, entry: Gtk.Entry) -> None:
        line = entry.get_text()
        entry.set_text("")
        if line.strip():
            self.history.append(line)
            del self.history[:-200]
        self.history_pos = len(self.history)
        self.client.submit(line)

    def on_entry_key(self, _ctl, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self.complete()
            return True
        self.completion = None
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and self.history:
            step = -1 if keyval == Gdk.KEY_Up else 1
            self.history_pos = max(0, min(len(self.history), self.history_pos + step))
            text = self.history[self.history_pos] if self.history_pos < len(self.history) else ""
            self.entry.set_text(text)
            self.entry.set_position(-1)
            return True
        return False

    def complete(self) -> None:
        # Repeated Tabs cycle through matching nicks; any other key resets.
        value, cursor = self.entry.get_text(), self.entry.get_position()
        if self.completion:
            start, matches, i = self.completion
            i = (i + 1) % len(matches)
        else:
            start = value[:cursor].rfind(" ") + 1
            stem = value[start:cursor]
            if not stem:
                return
            nicks = sorted(self.client.active.users, key=str.lower)
            matches = [n for n in nicks if n.lower().startswith(stem.lower())]
            if not matches:
                return
            i = 0
        completion = matches[i] + (": " if start == 0 else " ")
        rest = value[cursor:]
        self.entry.set_text(value[:start] + completion + rest)
        self.entry.set_position(start + len(completion))
        self.completion = (start, matches, i)

    def on_window_key(self, _ctl, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if state & Gdk.ModifierType.ALT_MASK and Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
            self.client.jump(keyval - Gdk.KEY_1)
            return True
        return False

    def tag_at(self, x: float, y: float) -> tuple[str, str] | None:
        """The link under the pointer, as ("url" | "channel", text)."""
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.view.get_iter_at_location(bx, by)
        if not ok:
            return None
        for name, kind in (("link", "url"), ("link-channel", "channel")):
            tag = self.tags.lookup(name)
            if it.has_tag(tag):
                start, end = it.copy(), it.copy()
                if not start.starts_tag(tag):
                    start.backward_to_tag_toggle(tag)
                end.forward_to_tag_toggle(tag)
                return kind, start.get_text(end).strip()
        return None

    def on_view_click(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        if self.view.get_buffer().get_has_selection():
            return  # the user was selecting text
        hit = self.tag_at(x, y)
        if hit and hit[0] == "url":
            Gtk.UriLauncher(uri=hit[1]).launch(self, None, None, None)
        elif hit and hit[0] == "channel":
            self.client.command(f"join {hit[1]}")

    def on_view_motion(self, _ctl, x: float, y: float) -> None:
        self.view.set_cursor_from_name("pointer" if self.tag_at(x, y) else "text")

    def on_close_request(self, *_args) -> bool:
        self.client.quit("")
        return True

    # ── dialogs ───────────────────────────────────────────────────────────
    def ask(self, heading: str, body: str, initial: str, action: str,
            validate: Callable[[str], str | None], done: Callable[[str], None], cancel: Callable[[], None]) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        entry = Gtk.Entry(text=initial, activates_default=True)
        error = Gtk.Label(wrap=True, xalign=0, css_classes=["error"], visible=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(entry)
        box.append(error)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", action)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def on_response(_d, response: str) -> None:
            if response != "ok":
                cancel()
                return
            value = entry.get_text().strip()
            problem = validate(value)
            if problem:
                error.set_label(problem)
                error.set_visible(True)
                GLib.idle_add(lambda: (dialog.present(self), False)[1])
                return
            done(value)
        dialog.connect("response", on_response)
        dialog.present(self)
        entry.grab_focus()

    def ask_nick(self) -> None:
        def done(nick: str) -> None:
            self.client.nick = nick
            self.client.connect()
            self.entry.grab_focus()
        self.ask("Welcome to SorceryNet", "Pick a nick. If it's registered to you, identify after connecting with /ns IDENTIFY.",
                 self.client.nick, "Connect", lambda n: None if NICK_RE.match(n) else NICK_RULES,
                 done, self.app.quit)

    def ask_join(self) -> None:
        def done(name: str) -> None:
            self.client.command(f"join {name}")
        self.ask("Join a channel", "", "#", "Join",
                 lambda n: None if n.strip("#") and " " not in n else "Enter a channel name, like #wormwoodden.",
                 done, lambda: None)

    def start(self) -> None:
        if self.client.nick:
            self.client.connect()
            self.entry.grab_focus()
        else:
            self.ask_nick()


class SorceryApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.window: Window | None = None
        self.css = Gtk.CssProvider()
        self.monitor: Gio.FileMonitor | None = None
        self.theme_timer = 0

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.apply_theme()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # Omarchy swaps ~/.local/state/omarchy/current/theme when the theme changes.
        self.monitor = Gio.File.new_for_path(str(THEME_STATE)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self.monitor.connect("changed", lambda *_: self.theme_changed())

        for name, fn, param in [("quit", lambda *_: self.window and self.window.client.quit(""), None),
                                ("show-buffer", self.show_buffer, GLib.VariantType.new("s"))]:
            action = Gio.SimpleAction.new(name, param)
            action.connect("activate", fn)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("win.next", ["<Control>Page_Down", "<Control>Tab"])
        self.set_accels_for_action("win.prev", ["<Control>Page_Up", "<Control><Shift>Tab"])
        self.set_accels_for_action("win.close-buffer", ["<Control>w"])

    def do_activate(self) -> None:
        if not self.window:
            self.window = Window(self)
            self.window.present()
            self.window.start()
        else:
            self.window.present()

    def apply_theme(self) -> None:
        colours = load_theme()
        self.css.load_from_string(theme_css(colours))
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_LIGHT if colours.get("mode") == "light" else Adw.ColorScheme.FORCE_DARK)

    def theme_changed(self) -> None:
        if self.theme_timer:  # a theme change touches many files; react once
            GLib.source_remove(self.theme_timer)

        def reload() -> bool:
            self.theme_timer = 0
            self.apply_theme()
            if self.window:
                self.window.reload_theme()
            return False
        self.theme_timer = GLib.timeout_add(500, reload)

    def show_buffer(self, _action, key: GLib.Variant) -> None:
        if self.window:
            self.window.present()
            self.window.client.switch(key.get_string())


def main() -> None:
    sys.exit(SorceryApp().run(sys.argv[:1]))


if __name__ == "__main__":
    main()
