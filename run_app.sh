#!/usr/bin/env bash
# Launcher para o App Desktop do Gemini Live // JARVIS
cd "$(dirname "$0")"

if [ -n "$WAYLAND_DISPLAY" ]; then
    export QT_QPA_PLATFORM="wayland;xcb"
fi

exec .venv/bin/python app.py
