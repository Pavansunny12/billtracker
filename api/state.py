from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send_response()

    def do_POST(self):
        self._send_response()

    def _send_response(self):
        body = json.dumps({"ok": False, "message": "Server sync not configured"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
