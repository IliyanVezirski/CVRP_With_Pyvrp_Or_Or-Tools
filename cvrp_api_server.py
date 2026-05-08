"""
HTTP API server for running CVRP optimisation from another program.

Endpoints:
  GET  /health
  POST /solve

POST /solve accepts either a JSON list of customer records or an object with a
list under one of these keys: customers, clients, orders, data, items, records.
The record fields are mapped by config.InputConfig JSON settings.
"""

from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from config import get_config
from input_handler import InputHandler
from main import run_optimization


logger = logging.getLogger(__name__)


def _normalise_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "/solve").strip()
    return endpoint if endpoint.startswith("/") else f"/{endpoint}"


def _build_public_base_url(api_config, host: str, port: int) -> str:
    configured_url = (getattr(api_config, "api_public_url", "") or "").strip().rstrip("/")
    if configured_url:
        return configured_url

    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    return f"http://{display_host}:{port}"


class CVRPApiHandler(BaseHTTPRequestHandler):
    server_version = "CVRPApi/1.0"

    def do_GET(self):
        api_config = get_config().api
        health_endpoint = _normalise_endpoint(getattr(api_config, "health_endpoint", "/health")).rstrip("/")
        if self.path.rstrip("/") == health_endpoint:
            host = self.server.server_address[0]
            port = self.server.server_address[1]
            public_url = _build_public_base_url(api_config, host, port)
            api_endpoint = _normalise_endpoint(getattr(api_config, "api_endpoint", "/solve"))
            self._send_json(200, {
                "status": "ok",
                "listen_url": f"http://{host}:{port}",
                "public_url": public_url,
                "solve_url": f"{public_url}{api_endpoint}",
            })
            return

        self._send_json(404, {"status": "error", "error": "Unknown endpoint"})

    def do_POST(self):
        api_config = get_config().api
        api_endpoint = _normalise_endpoint(getattr(api_config, "api_endpoint", "/solve")).rstrip("/")
        if self.path.rstrip("/") != api_endpoint:
            self._send_json(404, {"status": "error", "error": "Unknown endpoint"})
            return

        try:
            payload = self._read_json_body()
            input_data = InputHandler().load_data_from_json_records(payload)
            result = run_optimization(input_data_override=input_data)
            self._send_json(200, result)
        except Exception as exc:
            logger.exception("CVRP API request failed")
            self._send_json(500, {"status": "error", "error": str(exc)})

    def log_message(self, fmt: str, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Empty request body")

        raw = self.rfile.read(length)
        charset = "utf-8"
        content_type = self.headers.get("Content-Type", "")
        if "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()

        return json.loads(raw.decode(charset))

    def _send_json(self, status_code: int, payload: Dict[str, Any]):
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def run_server(host: str | None = None, port: int | None = None):
    api_config = get_config().api
    host = host or getattr(api_config, "api_host", "0.0.0.0")
    port = port or int(getattr(api_config, "api_port", 8088))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    server = ThreadingHTTPServer((host, port), CVRPApiHandler)
    public_url = _build_public_base_url(api_config, host, port)
    api_endpoint = _normalise_endpoint(getattr(api_config, "api_endpoint", "/solve"))
    logger.info("CVRP API server listening on http://%s:%s", host, port)
    logger.info("Public/base URL: %s", public_url)
    logger.info("POST customer JSON to %s%s", public_url, api_endpoint)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping CVRP API server")
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="CVRP POST API server")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
