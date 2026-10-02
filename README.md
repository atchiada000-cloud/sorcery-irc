# Sorcery

A small IRC client made for one network: [SorceryNet](https://sorcery.net)
(`irc.sorcery.net:6697`, always over TLS). No server list, no setup — pick a
nick and you're in.

- Channels and private chats in a sidebar, with unread counts (red when someone
  mentions you or messages you privately)
- People list with ops and voice; double-click a name to chat privately
- Notifications for private messages and mentions
- Clickable links; click a channel in "Browse channels" to join it
- Tab completes nicks, Up/Down recalls what you typed
- Reconnects on its own after a drop and rejoins your channels
- Hides your password when you `/ns IDENTIFY` or `/ns REGISTER`

Type `/help` inside for every command and key.

## Download

Get the latest build from the [Releases](../../releases) page.

**Windows** — download `Sorcery-windows.zip`, unzip it, and run
`Sorcery\Sorcery.exe`. Windows may warn that the app is from an unknown
publisher (it isn't code-signed): click **More info → Run anyway**.

**macOS** (Apple Silicon) — download `Sorcery-macos.zip`, unzip it, and drag
**Sorcery** to Applications. The app isn't notarized by Apple, so the first
time, right-click it and choose **Open**, then **Open** again (or allow it in
System Settings → Privacy & Security).

**Linux** — run from source (below). On [Omarchy](https://omarchy.org) the GTK
version follows your theme and changes with it.

## Run from source

Needs Python 3.11+.

```sh
git clone <this repo> && cd sorcery-irc

# Qt desktop app (Linux, macOS, Windows)
python3 -m venv .venv && .venv/bin/pip install -r requirements-qt.txt
.venv/bin/python -m sorcery_irc.qt

# GTK desktop app (Linux; uses the system's PyGObject, GTK 4 and libadwaita)
python3 -m sorcery_irc.gui

# Terminal app
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m sorcery_irc
```

Settings (your last nick and the channels to rejoin) are kept in
`~/.config/sorcery-irc/` on Linux, `~/Library/Application Support/Sorcery/`
on macOS and `%APPDATA%\Sorcery\` on Windows.

## How it's put together

| File | What it is |
| --- | --- |
| `sorcery_irc/irc.py` | The connection: TLS to SorceryNet, line parsing |
| `sorcery_irc/core.py` | The client: windows, users, commands, server replies — no UI code |
| `sorcery_irc/net.py` | Runs the connection on a background thread for the desktop apps |
| `sorcery_irc/qt.py` | Qt desktop app (the macOS and Windows builds) |
| `sorcery_irc/gui.py` | GTK 4 / libadwaita desktop app for Linux |
| `sorcery_irc/app.py` | Terminal app (Textual) |
| `android/` | Android app (Kotlin, Jetpack Compose); can also connect to other IRC networks |

Builds for macOS and Windows are made by GitHub Actions
(`.github/workflows/build.yml`) whenever a `v*` tag is pushed.

## License

MIT — see [LICENSE](LICENSE).
