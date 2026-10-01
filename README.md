# Sorcery

A small custom terminal IRC client that only connects to SorceryNet
(`irc.sorcery.net:6697`, TLS). Built with Python + Textual; follows the
terminal's colours, so it matches the current Omarchy theme.

Run: `sorcery` (launcher in `~/.local/bin`) or from the app launcher.
Settings (last nick, channels to rejoin) live in `~/.config/sorcery-irc/config.json`.
Type `/help` inside for commands and keys.

Setup on a new machine:

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
