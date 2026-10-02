"""Sorcery for macOS and Windows (also runs on Linux) — a Qt SorceryNet client.

Same client as the GTK app (core.py), drawn with Qt so it can be packaged for
macOS and Windows. Run:  python -m sorcery_irc.qt   (needs requirements-qt.txt)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QMargins, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontDatabase, QIcon,
                           QKeySequence, QPalette, QShortcut, QTextBlockFormat, QTextCharFormat,
                           QTextCursor, QTextDocument)
from PySide6.QtWidgets import (QApplication, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QSizePolicy, QSplitter,
                               QSystemTrayIcon, QTextBrowser, QToolBar, QVBoxLayout, QWidget)

from .core import NICK_RE, NICK_RULES, RANKS, SERVER, Client, Line, nick_tag
from .irc import strip_formatting
from .net import NetThread
from .theme import NICK_PALETTE, load_theme

MAX_LINES = 3000
URL_RE = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]]")
ICON = Path(__file__).parent / "assets" / "icon.png"
KEY = Qt.ItemDataRole.UserRole


class Bridge(QObject):
    """Carries functions from the network thread to the UI thread."""

    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self.run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def run(self, fn: Callable[[], None]) -> None:
        fn()


class ChatInput(QLineEdit):
    """The message line: Tab completes nicks, Up/Down recall earlier messages."""

    def __init__(self, win: "Window") -> None:
        super().__init__()
        self.win = win
        self.history: list[str] = []
        self.history_pos = 0
        self.completion: tuple[int, list[str], int] | None = None

    def event(self, e: QEvent) -> bool:
        # Tab normally moves focus; catch it before QWidget does.
        if (e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Tab
                and not e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.complete()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:
        self.completion = None
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.history:
            step = -1 if e.key() == Qt.Key.Key_Up else 1
            self.history_pos = max(0, min(len(self.history), self.history_pos + step))
            self.setText(self.history[self.history_pos] if self.history_pos < len(self.history) else "")
            return
        super().keyPressEvent(e)

    def take(self) -> str:
        line = self.text()
        self.clear()
        if line.strip():
            self.history.append(line)
            del self.history[:-200]
        self.history_pos = len(self.history)
        return line

    def complete(self) -> None:
        # Repeated Tabs cycle through matching nicks; any other key resets.
        value, cursor = self.text(), self.cursorPosition()
        if self.completion:
            start, matches, i = self.completion
            i = (i + 1) % len(matches)
        else:
            start = value[:cursor].rfind(" ") + 1
            stem = value[start:cursor]
            if not stem:
                return
            nicks = sorted(self.win.client.active.users, key=str.lower)
            matches = [n for n in nicks if n.lower().startswith(stem.lower())]
            if not matches:
                return
            i = 0
        completion = matches[i] + (": " if start == 0 else " ")
        self.setText(value[:start] + completion + value[cursor:])
        self.setCursorPosition(start + len(completion))
        self.completion = (start, matches, i)


class Window(QMainWindow):
    def __init__(self, app: QApplication, smoke: bool = False) -> None:
        super().__init__()
        self.app = app
        self.colours = load_theme()
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.docs: dict[str, QTextDocument] = {}
        self.items: dict[str, QListWidgetItem] = {}
        self.shown_users: tuple = ()
        self.refresh_queued = False
        self.updating = False
        self.last_notified = ""
        self.tray: QSystemTrayIcon | None = None

        self.bridge = Bridge()
        self.net = NetThread(self.bridge.call.emit)
        self.client = Client(self, self.net)
        self.net.client = self.client

        self.setWindowTitle("Sorcery")
        self.setWindowIcon(QIcon(str(ICON)))
        self.resize(1100, 700)
        self.build()
        self.apply_style()
        if not smoke:
            self.make_tray()
        self.refresh()

    # ── layout ────────────────────────────────────────────────────────────
    def build(self) -> None:
        bar = QToolBar(movable=False, floatable=False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)
        for text, tip, fn in [("＋ Join", "Join a channel", self.ask_join),
                              ("Browse channels", "List SorceryNet's channels", lambda: self.client.command("list")),
                              ("Reconnect", "Reconnect to SorceryNet", self.client.reconnect),
                              ("Help", "Commands and keys", lambda: self.client.command("help"))]:
            action = QAction(text, self, toolTip=tip)
            action.triggered.connect(fn)
            bar.addAction(action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        self.people_action = QAction("People", self, checkable=True, checked=True, toolTip="Show who's here")
        self.people_action.toggled.connect(lambda _: self.refresh())
        bar.addAction(self.people_action)

        # Windows: server, channels and private chats.
        self.buffer_list = QListWidget()
        self.buffer_list.currentItemChanged.connect(self.on_buffer_selected)
        self.buffer_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.buffer_list.customContextMenuRequested.connect(self.buffer_menu)
        self.status = QLabel()
        self.status.setObjectName("status")
        left = QWidget(objectName="pane")
        lay = QVBoxLayout(left, contentsMargins=QMargins(6, 6, 6, 8), spacing=6)
        lay.addWidget(self.buffer_list)
        lay.addWidget(self.status)

        # Chat: topic, scrollback, input.
        self.topic = QLabel(objectName="topic", textFormat=Qt.TextFormat.PlainText)
        self.view = QTextBrowser(openLinks=False)
        self.view.anchorClicked.connect(self.on_link)
        self.entry = ChatInput(self)
        self.entry.returnPressed.connect(lambda: self.client.submit(self.entry.take()))
        middle = QWidget()
        lay = QVBoxLayout(middle, contentsMargins=QMargins(0, 0, 0, 8), spacing=6)
        lay.addWidget(self.topic)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.entry)

        # People in the current channel.
        self.user_title = QLabel(objectName="paneTitle")
        self.user_list = QListWidget()
        self.user_list.itemDoubleClicked.connect(lambda item: self.client.open_query(item.data(KEY)))
        self.users_pane = QWidget(objectName="pane")
        lay = QVBoxLayout(self.users_pane, contentsMargins=QMargins(6, 6, 6, 6), spacing=4)
        lay.addWidget(self.user_title)
        lay.addWidget(self.user_list)

        split = QSplitter(childrenCollapsible=False)
        split.addWidget(left)
        split.addWidget(middle)
        split.addWidget(self.users_pane)
        split.setStretchFactor(1, 1)
        split.setSizes([200, 720, 180])
        self.setCentralWidget(split)

        for keys, fn in [(["Ctrl+PgDown", "Ctrl+Tab"], lambda: self.client.cycle(1)),
                         (["Ctrl+PgUp", "Ctrl+Shift+Tab"], lambda: self.client.cycle(-1)),
                         (["Ctrl+W"], lambda: self.client.close_buffer(self.client.active_key)),
                         ([QKeySequence.StandardKey.Quit, "Ctrl+Q"], lambda: self.client.quit(""))]:
            for k in keys:
                QShortcut(QKeySequence(k), self, activated=fn)
        for n in range(1, 10):
            QShortcut(QKeySequence(f"Alt+{n}"), self, activated=lambda n=n: self.client.jump(n - 1))

    def apply_style(self) -> None:
        c = self.colours
        self.app.setStyle("Fusion")
        pal = QPalette()
        for role, key in [(QPalette.ColorRole.Window, "background"), (QPalette.ColorRole.WindowText, "foreground"),
                          (QPalette.ColorRole.Base, "background"), (QPalette.ColorRole.AlternateBase, "lighter_background"),
                          (QPalette.ColorRole.Text, "foreground"), (QPalette.ColorRole.Button, "lighter_background"),
                          (QPalette.ColorRole.ButtonText, "foreground"), (QPalette.ColorRole.Highlight, "accent"),
                          (QPalette.ColorRole.HighlightedText, "background"), (QPalette.ColorRole.ToolTipBase, "lighter_background"),
                          (QPalette.ColorRole.ToolTipText, "foreground"), (QPalette.ColorRole.Link, "accent"),
                          (QPalette.ColorRole.PlaceholderText, "light_foreground")]:
            pal.setColor(role, QColor(c[key]))
        self.app.setPalette(pal)
        self.setStyleSheet(f"""
            QToolBar {{ background: {c['dark_background']}; border: none; padding: 4px; spacing: 4px; }}
            QToolBar QToolButton {{ color: {c['foreground']}; padding: 4px 10px; border-radius: 6px; }}
            QToolBar QToolButton:hover {{ background: {c['lighter_background']}; }}
            QToolBar QToolButton:checked {{ background: {c['selection']}; }}
            #pane {{ background: {c['dark_background']}; }}
            #paneTitle {{ color: {c['light_foreground']}; font-weight: bold; padding: 4px 6px; }}
            #status {{ color: {c['light_foreground']}; padding: 0 6px; }}
            #topic {{ color: {c['light_foreground']}; padding: 8px 14px 0 14px; }}
            QListWidget {{ background: {c['dark_background']}; border: none; outline: none; }}
            QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
            QListWidget::item:selected {{ background: {c['selection']}; color: {c['foreground']}; }}
            QListWidget::item:hover:!selected {{ background: {c['lighter_background']}; }}
            QTextBrowser {{ background: {c['background']}; border: none; padding: 6px 10px; }}
            QLineEdit {{ background: {c['lighter_background']}; color: {c['foreground']};
                         border: 1px solid {c['muted']}; border-radius: 8px; padding: 8px 10px; margin: 0 10px; }}
            QLineEdit:focus {{ border-color: {c['accent']}; }}
            QSplitter::handle {{ background: {c['darker_background']}; width: 1px; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: {c['muted']}; border-radius: 3px; min-height: 30px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
        """)

    def make_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(str(ICON)), self)
        self.tray.setToolTip("Sorcery")
        menu = QMenu(self)
        menu.addAction("Show Sorcery", self.bring_forward)
        menu.addAction("Quit", lambda: self.client.quit(""))
        self.tray.setContextMenu(menu)
        self.tray.messageClicked.connect(lambda: self.bring_forward(self.last_notified))
        self.tray.activated.connect(lambda reason: reason == QSystemTrayIcon.ActivationReason.Trigger
                                    and self.bring_forward())
        self.tray.show()

    def bring_forward(self, key: str = "") -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if key:
            self.client.switch(key)

    # ── Frontend (called by the client) ───────────────────────────────────
    def doc_for(self, key: str) -> QTextDocument:
        if key not in self.docs:
            doc = QTextDocument(self)
            doc.setDefaultFont(self.mono)
            doc.setMaximumBlockCount(MAX_LINES)
            doc.setDocumentMargin(8)
            self.docs[key] = doc
        return self.docs[key]

    def char_format(self, tags: list[str]) -> QTextCharFormat:
        c = self.colours
        fmt = QTextCharFormat()
        colour = {"time": "light_foreground", "dim": "light_foreground", "error": "red", "hint": "yellow",
                  "green": "green", "yellow": "yellow", "magenta": "magenta", "cyan": "cyan",
                  "mine": "foreground", "link": "accent", "link-channel": "accent"}
        for tag in tags:
            if tag in colour:
                fmt.setForeground(QColor(c[colour[tag]]))
            elif tag.startswith("nick") and tag[4:].isdigit():
                fmt.setForeground(QColor(c[NICK_PALETTE[int(tag[4:])]]))
            if tag in ("bold", "error", "mine"):
                fmt.setFontWeight(QFont.Weight.Bold)
            if tag == "italic":
                fmt.setFontItalic(True)
            if tag == "link":
                fmt.setFontUnderline(True)
        return fmt

    def show_line(self, key: str, line: Line) -> None:
        doc = self.doc_for(key)
        active = key == self.client.active_key
        sb = self.view.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 40
        block = QTextBlockFormat()
        block.setBottomMargin(2)
        if any("highlight" in tags.split() for _, tags in line):
            block.setBackground(QColor(self.colours["selection"]))
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if doc.isEmpty():
            cursor.setBlockFormat(block)
        else:
            cursor.insertBlock(block)
        for text, tags in line:
            names = tags.split()
            if "link-channel" in names:
                fmt = self.char_format(names)
                fmt.setAnchor(True)
                fmt.setAnchorHref("join:" + text.strip())
                cursor.insertText(text, fmt)
                continue
            pos = 0
            for m in URL_RE.finditer(text):  # make web links clickable
                cursor.insertText(text[pos:m.start()], self.char_format(names))
                fmt = self.char_format(names + ["link"])
                fmt.setAnchor(True)
                fmt.setAnchorHref(m.group())
                cursor.insertText(m.group(), fmt)
                pos = m.end()
            cursor.insertText(text[pos:], self.char_format(names))
        if active and at_bottom:
            self.scroll_to_end()

    def clear_lines(self, key: str) -> None:
        self.doc_for(key).clear()

    def rename_buffer(self, old_key: str, new_key: str) -> None:
        if old_key in self.docs:
            self.docs[new_key] = self.docs.pop(old_key)
        if old_key in self.items:
            item = self.items.pop(old_key)
            item.setData(KEY, new_key)
            self.items[new_key] = item

    def changed(self) -> None:
        if not self.refresh_queued:  # many events arrive at once; redraw once
            self.refresh_queued = True
            QTimer.singleShot(0, self.refresh)

    def notify(self, key: str, title: str, body: str) -> None:
        if self.isActiveWindow() and key == self.client.active_key:
            return
        self.last_notified = key
        QApplication.alert(self)  # flash the taskbar button / bounce the dock icon
        if self.tray and self.tray.supportsMessages():
            self.tray.showMessage(title, body, QIcon(str(ICON)), 6000)

    def later(self, seconds: int, fn: Callable[[], None]) -> None:
        QTimer.singleShot(seconds * 1000, fn)

    def quit(self) -> None:
        # Give the QUIT line a moment to reach the server.
        QTimer.singleShot(300, self.app.quit)

    # ── redraw ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self.refresh_queued = False
        cl, c = self.client, self.colours
        buf = cl.active
        self.updating = True
        try:
            # Window list: update items in place; only reorder when the order changed.
            for key in list(self.items):
                if key not in cl.buffers:
                    self.buffer_list.takeItem(self.buffer_list.row(self.items.pop(key)))
            for key in cl.order:
                if key not in self.items:
                    item = QListWidgetItem()
                    item.setData(KEY, key)
                    self.items[key] = item
                    self.buffer_list.addItem(item)
            current = [self.buffer_list.item(i).data(KEY) for i in range(self.buffer_list.count())]
            if current != cl.order:
                for key in current:
                    self.buffer_list.takeItem(self.buffer_list.row(self.items[key]))
                for key in cl.order:
                    self.buffer_list.addItem(self.items[key])
            for key in cl.order:
                b, item = cl.buffers[key], self.items[key]
                item.setText(b.name + (f"   {b.unread}" if b.unread else ""))
                font = QFont()
                font.setBold(b.kind == "server" or b.unread > 0)
                item.setFont(font)
                colour = (c["red"] if b.highlight else c["accent"] if b.unread
                          else c["light_foreground"] if b.kind == "channel" and not b.joined else c["foreground"])
                item.setForeground(QColor(colour))
            self.buffer_list.setCurrentItem(self.items[cl.active_key])
        finally:
            self.updating = False

        doc = self.doc_for(cl.active_key)
        if self.view.document() is not doc:
            self.view.setDocument(doc)
            self.scroll_to_end()

        self.topic.setText(strip_formatting(buf.topic) if buf.topic else
                           (f"{cl.server_name} · TLS" if buf.kind == "server" else
                            "private chat" if buf.kind == "query" else ""))
        self.topic.setToolTip(self.topic.text())
        self.status.setText(f"● {cl.nick or '?'} · {cl.state}")
        self.status.setStyleSheet(f"color: {c['green'] if cl.registered else c['light_foreground']};")
        self.entry.setPlaceholderText(f"Message {buf.name}" if buf.kind != "server"
                                      else "Type /join #channel, or /help")
        self.setWindowTitle(f"{buf.name} — Sorcery")

        self.users_pane.setVisible(buf.kind == "channel" and self.people_action.isChecked())
        self.people_action.setEnabled(buf.kind == "channel")
        if buf.kind == "channel":
            users = tuple(sorted(buf.users.items(), key=lambda kv: (RANKS.get(kv[1][:1], 9), kv[0].lower())))
            self.user_title.setText(f"PEOPLE · {len(users)}")
            if users != self.shown_users:
                self.shown_users = users
                self.user_list.clear()
                for nick, mode in users:
                    item = QListWidgetItem(f"{mode[:1] or ' '} {nick}")
                    item.setData(KEY, nick)
                    item.setToolTip(f"Double-click to message {nick}")
                    item.setForeground(QColor(c[NICK_PALETTE[int(nick_tag(nick)[4:])]]))
                    self.user_list.addItem(item)

    def scroll_to_end(self) -> None:
        QTimer.singleShot(0, lambda: self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum()))

    # ── events ────────────────────────────────────────────────────────────
    def on_buffer_selected(self, item: QListWidgetItem | None, _prev) -> None:
        if not self.updating and item and item.data(KEY) != self.client.active_key:
            self.client.switch(item.data(KEY))
            self.entry.setFocus()

    def buffer_menu(self, pos) -> None:
        item = self.buffer_list.itemAt(pos)
        if not item or item.data(KEY) == SERVER.lower():
            return
        menu = QMenu(self)
        menu.addAction("Close", lambda: self.client.close_buffer(item.data(KEY)))
        menu.exec(self.buffer_list.mapToGlobal(pos))

    def on_link(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("join:"):
            self.client.command("join " + href[5:])
        else:
            QDesktopServices.openUrl(url)

    def closeEvent(self, event) -> None:
        if self.client.quitting:
            event.accept()
        else:
            event.ignore()
            self.client.quit("")

    # ── dialogs ───────────────────────────────────────────────────────────
    def ask_nick(self) -> bool:
        nick, note = self.client.nick, "Pick a nick. If it's registered to you, identify after connecting with /ns IDENTIFY."
        while True:
            nick, ok = QInputDialog.getText(self, "Welcome to SorceryNet", note, text=nick)
            if not ok:
                return False
            nick = nick.strip()
            if NICK_RE.match(nick):
                self.client.nick = nick
                return True
            note = NICK_RULES

    def ask_join(self) -> None:
        name, ok = QInputDialog.getText(self, "Join a channel", "Channel name:", text="#")
        if ok and name.strip("# "):
            self.client.command(f"join {name.strip()}")

    def start(self) -> None:
        if self.client.nick or self.ask_nick():
            self.client.connect()
            self.entry.setFocus()
        else:
            QTimer.singleShot(0, self.app.quit)


def main() -> None:
    smoke = "--smoke" in sys.argv  # build checks: open the window, check TLS setup, exit
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Sorcery")
    app.setApplicationDisplayName("Sorcery")
    app.setWindowIcon(QIcon(str(ICON)))
    win = Window(app, smoke=smoke)
    win.show()
    if smoke:
        from .irc import _tls_context
        _tls_context()
        assert ICON.exists(), f"missing {ICON}"
        QTimer.singleShot(1500, app.quit)
        code = app.exec()
        print("smoke ok")
        sys.exit(code)
    QTimer.singleShot(0, win.start)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
