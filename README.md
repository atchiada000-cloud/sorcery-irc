# Sorcery

A small custom IRC client that only connects to SorceryNet
(`irc.sorcery.net:6697`, TLS).

- **Desktop app** (`sorcery_irc/gui.py`): GTK4 + libadwaita, run with the
  system Python. Colours come from the current Omarchy theme and follow
  theme changes live. Launch "Sorcery" from the app launcher, or `sorcery-gui`.
  Reconnects on its own after a drop, and notifies you of private messages
  and mentions.
- **Terminal app** (`sorcery_irc/app.py`): Python + Textual, run with
  `sorcery`. Follows the terminal's colours.

Both share the IRC logic in `core.py` / `irc.py` and the settings (last nick,
channels to rejoin) in `~/.config/sorcery-irc/config.json`.
Type `/help` inside for commands and keys.

Setup on a new machine:

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
