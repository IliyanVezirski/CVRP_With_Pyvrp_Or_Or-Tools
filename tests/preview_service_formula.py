"""Read-only localhost UI fixture: no production APIs or settings writes."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cvrp_api_server as api

cfg = deepcopy(api.get_config())
cfg.api.api_key = ""
cfg.api.web_gui_run_defaults_json = ""
cfg.vehicles = cfg.vehicles[:2]
for index, vehicle in enumerate(cfg.vehicles):
    vehicle.name = "Примерен бус " + str(index + 1)
    vehicle.service_time_mode = "fixed"
auth = SimpleNamespace(credential_store=SimpleNamespace(list_usernames=lambda: []))
with patch.object(api, "get_config", return_value=cfg), patch.object(api, "_get_web_auth_service", return_value=auth):
    payload = api._web_gui_config_payload("127.0.0.1", 18765, refresh_from_disk=False)
payload["run_status"] = {"running": False, "status": "idle"}
page = api._web_gui_html(cfg.api, "127.0.0.1", 18765)


class Preview(BaseHTTPRequestHandler):
    def do_GET(self):
        data = payload if self.path.endswith("/config") else {"status": "ok", "run_status": {"running": False}, "text": "", "lines": []}
        body = json.dumps(data, ensure_ascii=False).encode() if "/api/" in self.path else page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8" if "/api/" in self.path else "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_error(405, "Read-only preview")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("Read-only preview: http://127.0.0.1:18765", flush=True)
    HTTPServer(("127.0.0.1", 18765), Preview).serve_forever()
