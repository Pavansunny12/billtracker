from http.server import BaseHTTPRequestHandler
import json
import os
import hmac
import urllib.request
import urllib.error
import base64

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Pavansunny12/billtracker")
GITHUB_FILE_PATH = os.environ.get("GITHUB_FILE_PATH", "state.json")

# Shared secret that must match the SYNC_SECRET env var on Vercel.
# Sent by the app as the X-Sync-Secret header. Fail closed: if it is not
# configured, every request is rejected so a misconfigured deploy can never
# serve or accept state.
SYNC_SECRET = os.environ.get("SYNC_SECRET", "")

# Refuse absurdly large bodies before reading them into memory.
MAX_BODY_BYTES = 2 * 1024 * 1024


def is_authorized(headers):
    """True only when the request carries the configured sync secret."""
    if not SYNC_SECRET:
        return False
    provided = headers.get("X-Sync-Secret") or ""
    return hmac.compare_digest(provided, SYNC_SECRET)


def validate_state(data):
    """Structural check of a posted state. Returns (True, "") or (False, reason)."""
    if not isinstance(data, dict):
        return False, "state must be an object"
    months = data.get("months")
    if not isinstance(months, list):
        return False, "months must be an array"
    if len(months) > 120:
        return False, "too many months"
    for month in months:
        if not isinstance(month, dict):
            return False, "month must be an object"
        if not isinstance(month.get("id"), str) or not isinstance(month.get("label"), str):
            return False, "month needs string id and label"
        members = month.get("members")
        if not isinstance(members, list):
            return False, "members must be an array"
        if len(members) > 500:
            return False, "too many members"
        for member in members:
            if not isinstance(member, dict):
                return False, "member must be an object"
            if not isinstance(member.get("id"), str) or not isinstance(member.get("name"), str):
                return False, "member needs string id and name"
            for key in ("due", "received"):
                value = member.get(key)
                if value is None or value == "":
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return False, "member.%s must be a number" % key
                if number < 0 or number > 1000000:
                    return False, "member.%s out of range" % key
    rev = data.get("rev", 0)
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        return False, "rev must be a non-negative integer"
    return True, ""


def current_rev(state_data):
    """Revision of the stored state; 0 when there is no stored state yet."""
    if not isinstance(state_data, dict):
        return 0
    rev = state_data.get("rev", 0)
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        return 0
    return rev


def github_request(method, data=None):
    if not GITHUB_TOKEN:
        return None, "GITHUB_TOKEN not configured"

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "VercelServerless-BillCollect",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(url, headers=headers, method=method)
    if data is not None:
        req.data = json.dumps(data).encode("utf-8")

    try:
        with urllib.request.urlopen(req) as response:
            body = response.read().decode("utf-8")
            return json.loads(body), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            err_json = json.loads(body)
            message = err_json.get("message", e.reason)
        except Exception:
            message = e.reason
        return None, f"GitHub HTTP {e.code}: {message}"
    except Exception as e:
        return None, str(e)


def decode_state_file(result):
    """Extract the state dict from a GitHub contents response."""
    content_b64 = result.get("content", "").replace("\n", "").strip()
    content_bytes = base64.b64decode(content_b64)
    return json.loads(content_bytes.decode("utf-8"))


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _require_auth(self):
        if not is_authorized(self.headers):
            self._send_json({"ok": False, "error": "unauthorized"}, 401)
            return False
        return True

    def do_GET(self):
        if not self._require_auth():
            return
        if not GITHUB_TOKEN:
            self._send_json({"ok": False, "error": "GITHUB_TOKEN environment variable not set on Vercel."}, 400)
            return

        result, err = github_request("GET")
        if err:
            if "HTTP 404" in err:
                self._send_json({"ok": True, "state": None, "message": "State file not found. Starting fresh."})
            else:
                self._send_json({"ok": False, "error": err}, 500)
            return

        try:
            state_data = decode_state_file(result)
            self._send_json({"ok": True, "state": state_data})
        except Exception as e:
            self._send_json({"ok": False, "error": f"Failed to parse content: {str(e)}"}, 500)

    def do_POST(self):
        if not self._require_auth():
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length > MAX_BODY_BYTES:
            self._send_json({"ok": False, "error": "body too large"}, 413)
            return

        try:
            body = self.rfile.read(length)
            state_data = json.loads(body.decode("utf-8"))
        except Exception as e:
            self._send_json({"ok": False, "error": f"Invalid JSON body: {str(e)}"}, 400)
            return

        ok, reason = validate_state(state_data)
        if not ok:
            self._send_json({"ok": False, "error": f"Invalid state: {reason}"}, 400)
            return

        if not GITHUB_TOKEN:
            self._send_json({"ok": False, "error": "GITHUB_TOKEN environment variable not set on Vercel."}, 400)
            return

        # Fetch current content to get SHA (required to update existing file)
        # and to compare revisions for conflict detection.
        result, err = github_request("GET")
        sha = None
        stored = None
        if err:
            if "HTTP 404" not in err:
                self._send_json({"ok": False, "error": f"Failed to read current state: {err}"}, 500)
                return
        else:
            if result and "sha" in result:
                sha = result["sha"]
            try:
                stored = decode_state_file(result)
            except Exception:
                stored = None

        if current_rev(stored) != current_rev(state_data):
            # Another device saved newer changes after this client last synced.
            # Refuse to overwrite; hand back the current state so the client
            # can reload instead of silently clobbering it.
            self._send_json({"ok": False, "error": "conflict", "state": stored}, 409)
            return

        new_rev = current_rev(stored) + 1
        state_data["rev"] = new_rev
        state_str = json.dumps(state_data, indent=2)
        state_b64 = base64.b64encode(state_str.encode("utf-8")).decode("utf-8")

        put_data = {
            "message": "Sync bill collect state",
            "content": state_b64
        }
        if sha:
            put_data["sha"] = sha

        res, put_err = github_request("PUT", put_data)
        if put_err:
            self._send_json({"ok": False, "error": f"Failed to commit state: {put_err}"}, 500)
        else:
            self._send_json({"ok": True, "rev": new_rev, "message": "State committed successfully to GitHub."})

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sync-Secret")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
