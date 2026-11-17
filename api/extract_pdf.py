"""Bill Collect — POST /api/extract_pdf (authenticated).

Extracts text from an uploaded AT&T bill PDF (pypdf). Unchanged
behaviour, except it now requires auth like every other endpoint:
either the agent key (X-Agent-Key, for Muse) or a user Bearer
token (Sunny in the browser; see api/_lib.py user_present TODO).

Previously this endpoint was open to the internet with CORS *,
so anyone could use Sunny's Vercel deployment to extract PDFs.
"""

try:
    from _lib import JsonHandler, require_auth
except ImportError:  # pragma: no cover - Vercel import fallback
    from api._lib import JsonHandler, require_auth

from io import BytesIO

from pypdf import PdfReader

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class handler(JsonHandler):
    def do_POST(self):
        try:
            _actor, err = require_auth(self._headers_dict())
            if err:
                self._json(401, err)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                length = 0
            if length <= 0:
                self._json(400, {"ok": False, "error": "No PDF file was uploaded."})
                return
            if length > MAX_UPLOAD_BYTES:
                self._json(413, {"ok": False, "error": "PDF is too large. Please upload a file under 20 MB."})
                return
            pdf_bytes = self.rfile.read(length)
            if not pdf_bytes.startswith(b"%PDF"):
                self._json(400, {"ok": False, "error": "Uploaded file does not look like a PDF."})
                return
            reader = PdfReader(BytesIO(pdf_bytes))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages).strip()
            if not text:
                self._json(422, {"ok": False, "error": "No text found. This may be a scanned image PDF that needs OCR."})
                return
            self._json(200, {"ok": True, "text": text, "pageCount": len(reader.pages)})
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace
            self._json(500, {"ok": False, "error": str(exc)[:200]})
