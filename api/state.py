from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.error
import base64

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Pavansunny12/billtracker")
GITHUB_FILE_PATH = os.environ.get("GITHUB_FILE_PATH", "state.json")

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


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
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
            content_b64 = result.get("content", "").replace("\n", "").strip()
            content_bytes = base64.b64decode(content_b64)
            state_data = json.loads(content_bytes.decode("utf-8"))
            self._send_json({"ok": True, "state": state_data})
        except Exception as e:
            self._send_json({"ok": False, "error": f"Failed to parse content: {str(e)}"}, 500)

    def do_POST(self):
        if not GITHUB_TOKEN:
            self._send_json({"ok": False, "error": "GITHUB_TOKEN environment variable not set on Vercel."}, 400)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            state_data = json.loads(body.decode("utf-8"))
        except Exception as e:
            self._send_json({"ok": False, "error": f"Invalid JSON body: {str(e)}"}, 400)
            return

        # Fetch current content to get SHA (required to update existing file)
        result, err = github_request("GET")
        sha = None
        if result and "sha" in result:
            sha = result["sha"]
            
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
            self._send_json({"ok": True, "message": "State committed successfully to GitHub."})

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
