#!/usr/bin/env python3
"""
J.A.R.V.I.S. // Gemini Live Desktop App
Aplicativo nativo flutuante transparente para Linux com arraste fluido nativo
via startSystemMove (Wayland & X11) e controle por PyBridge.
"""
import os
import sys

# Garante permissão irrestrita de autoplay de áudio no Chromium / QWebEngine
sys.argv.extend([
    "--autoplay-policy=no-user-gesture-required",
    "--disable-web-security",
    "--enable-features=WebRTCPipeWireCapturer",
])

import time
import socket
import subprocess

from PySide6.QtCore import Qt, QPoint, QUrl
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QSystemTrayIcon, QMenu
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings

PORT = 8000
SERVER_URL = f"http://localhost:{PORT}/widget/?app=1"

def is_server_running():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def start_backend_if_needed():
    if is_server_running():
        return None

    app_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(app_dir, ".venv", "bin", "python")
    py_exec = venv_python if os.path.exists(venv_python) else sys.executable

    server_script = os.path.join(app_dir, "server.py")
    log_dir = os.path.join(app_dir, "monitoring", "logs")
    os.makedirs(log_dir, exist_ok=True)
    server_log = open(os.path.join(log_dir, "server.log"), "a", encoding="utf-8")

    proc = subprocess.Popen(
        [py_exec, server_script],
        cwd=app_dir,
        stdout=server_log,
        stderr=server_log
    )

    for _ in range(30):
        if is_server_running():
            break
        time.sleep(0.2)

    return proc

class TransparentWebEnginePage(QWebEnginePage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundColor(QColor(0, 0, 0, 0))
        self.featurePermissionRequested.connect(self.handle_feature_permission)

    def handle_feature_permission(self, security_origin, feature):
        self.setFeaturePermission(
            security_origin,
            feature,
            QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
        )

class GeminiLiveDesktopApp(QMainWindow):
    def __init__(self):
        super().__init__()

        # Janela Flutuante Always-On-Top e Transparente
        self.setWindowTitle("Gemini Live Assistant")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

        # Dimensões iniciais
        self.resize(560, 240)
        self.center_to_bottom_right()

        # Layout
        central_widget = QWidget(self)
        central_widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # WebEngine View
        self.web_view = QWebEngineView(central_widget)
        self.page = TransparentWebEnginePage(self.web_view)
        self.web_view.setPage(self.page)
        self.web_view.setStyleSheet("background: transparent;")

        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)

        # Conecta PyBridge para receber comandos de arraste, redimensionamento e fechar
        self.web_view.titleChanged.connect(self.on_pybridge_message)

        layout.addWidget(self.web_view)
        self.setCentralWidget(central_widget)

        # Carrega a interface
        self.web_view.load(QUrl(SERVER_URL))

        # Atalhos
        QShortcut(QKeySequence("Escape"), self, self.hide)
        QShortcut(QKeySequence("F5"), self, self.web_view.reload)

        # Bandeja do Sistema
        self.setup_tray_icon()

    def center_to_bottom_right(self):
        screen = QApplication.primaryScreen().availableGeometry()
        margin = 35
        x = screen.width() - self.width() - margin
        y = screen.height() - self.height() - margin
        self.move(x, y)

    def on_pybridge_message(self, title: str):
        """Recebe comandos de movimentação e redimensionamento emitidos pelo JavaScript."""
        if not title:
            return

        if title.startswith("PYBRIDGE_START_DRAG:"):
            # Wayland & X11: compositor nativo gerencia o arraste do mouse
            handle = self.windowHandle()
            if handle:
                handle.startSystemMove()

        elif title.startswith("PYBRIDGE_MOVE:"):
            try:
                # Formato: PYBRIDGE_MOVE:dx,dy:counter
                parts = title.split(":")[1].split(",")
                dx = int(parts[0])
                dy = int(parts[1])
                self.move(self.x() + dx, self.y() + dy)
            except Exception:
                pass

        elif title.startswith("PYBRIDGE_RESIZE:"):
            try:
                # Formato: PYBRIDGE_RESIZE:w,h:counter
                parts = title.split(":")[1].split(",")
                w = int(parts[0])
                h = int(parts[1])
                self.resize(w, h)
            except Exception:
                pass

        elif title.startswith("PYBRIDGE_HIDE:"):
            self.hide()

    def setup_tray_icon(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        
        menu = QMenu()
        show_action = menu.addAction("Mostrar / Ocultar")
        show_action.triggered.connect(self.toggle_visibility)

        reload_action = menu.addAction("Recarregar Interface")
        reload_action.triggered.connect(self.web_view.reload)

        menu.addSeparator()
        quit_action = menu.addAction("Sair")
        quit_action.triggered.connect(QApplication.instance().quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility()

def main():
    start_backend_if_needed()

    app = QApplication(sys.argv)
    app.setApplicationName("GeminiLiveDesktop")
    app.setQuitOnLastWindowClosed(False)

    window = GeminiLiveDesktopApp()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
