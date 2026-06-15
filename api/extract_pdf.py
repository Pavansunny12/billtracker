from http.server import BaseHTTPRequestHandler
from io import BytesIO
import json

from pypdf import PdfReader


MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self._send_json({"ok": False, "error": "No PDF file was uploaded."}, 400)
                return
            if length > MAX_UPLOAD_BYTES:
                self._send_json({"ok": False, "error": "PDF is too large. Please upload a file under 20 MB."}, 413)
                return

            pdf_bytes = self.rfile.read(length)
            if not pdf_bytes.startswith(b"%PDF"):
                self._send_json({"ok": False, "error": "Uploaded file does not look like a PDF."}, 400)
                return

            reader = PdfReader(BytesIO(pdf_bytes))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages).strip()
            if not text:
                self._send_json(
                    {
                        "ok": False,
                        "error": "No text found. This may be a scanned image PDF that needs OCR.",
                    },
                    422,
                )
                return

            self._send_json({"ok": True, "text": text, "pageCount": len(reader.pages)})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
