import os
import sys

base_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.abspath(os.path.join(base_dir, ".."))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

import logging
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from PySide6.QtCore import QUrl, QSize
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from PySide6.QtWebChannel import QWebChannel

from core.task_queue import TaskQueue
from shell.qt_bridge import QtBridge

from logging.handlers import RotatingFileHandler

log_dir = os.path.join(os.getenv("LOCALAPPDATA", os.path.expanduser("~")), "Origin")
try:
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "origin.log")
    handlers = [RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", handlers=handlers)
except Exception:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

logger = logging.getLogger(__name__)

class QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

def start_local_server(directory: str) -> tuple[ThreadingHTTPServer, int]:
    handler_class = lambda *args, **kwargs: QuietHTTPRequestHandler(*args, directory=directory, **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Local static HTTP server running at http://127.0.0.1:{port}")
    return server, port

class CustomWebPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        logger.info(f"[JS Console L{level}] {source_id}:{line_number} -> {message}")
        super().javaScriptConsoleMessage(level, message, line_number, source_id)

class OriginMainWindow(QMainWindow):
    def __init__(self, public_dir: str):
        super().__init__()
        self.setWindowTitle("Origin — Tải video & audio YouTube chất lượng tốt nhất")
        self.setMinimumSize(QSize(1024, 720))
        
        self.task_queue = TaskQueue(max_concurrent=4)
        self.bridge = QtBridge(self.task_queue, parent=self)
        
        self.http_server, self.http_port = start_local_server(public_dir)
        
        self.web_view = QWebEngineView(self)
        self.web_page = CustomWebPage(self.web_view)
        self.web_view.setPage(self.web_page)
        
        settings = self.web_page.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        
        self.web_channel = QWebChannel(self.web_page)
        self.web_channel.registerObject("qtBridge", self.bridge)
        self.web_page.setWebChannel(self.web_channel)
        
        self.web_page.loadFinished.connect(self._on_load_finished)
        
        target_url = QUrl(f"http://127.0.0.1:{self.http_port}/")
        logger.info(f"Loading UI from local server: {target_url.toString()}")
        self.web_view.load(target_url)
        
        self.setCentralWidget(self.web_view)

    def _on_load_finished(self, success: bool):
        if success:
            logger.info("UI loaded successfully into QWebEngineView.")
        else:
            logger.error("Failed to load UI in QWebEngineView.")

    def closeEvent(self, event):
        if self.task_queue.has_active_tasks():
            reply = QMessageBox.question(
                self,
                "Xác nhận đóng ứng dụng",
                "Đang có tác vụ đang được tải hoặc ghép file.\nBạn có chắc chắn muốn hủy tất cả tác vụ và đóng ứng dụng không?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return

        logger.info("Closing application. Shutting down server & task queue executor...")
        threading.Thread(target=self.http_server.shutdown, daemon=True).start()
        self.bridge.shutdown()
        self.task_queue.shutdown()
        event.accept()

def main():
    app = QApplication(sys.argv)
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    proj_dir = os.path.abspath(os.path.join(current_dir, ".."))
    public_dir = os.path.join(proj_dir, ".output", "public")
    
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        public_dir = os.path.join(meipass, ".output", "public")
        
    index_html = os.path.join(public_dir, "index.html")
    if not os.path.exists(index_html):
        print(f"Error: Static UI index.html not found at {index_html}")
        print("Run python build_static_ui.py first.")
        sys.exit(1)
        
    window = OriginMainWindow(public_dir)
    window.show()
    window.raise_()
    window.activateWindow()
    if os.name == "nt":
        try:
            import ctypes
            hwnd = int(window.winId())
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
