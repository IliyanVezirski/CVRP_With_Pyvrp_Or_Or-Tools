"""Subprocess worker for current-driver TSP requests.

The API server uses this worker when a full CVRP optimisation is already
running. That keeps /tsp available without sharing the solver process.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Dict

from config import get_config
from current_tsp import solve_current_tsp_route


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def run_worker_from_files(input_path: str, output_path: str) -> int:
    try:
        with open(input_path, "r", encoding="utf-8") as fh:
            worker_request = json.load(fh)

        payload = worker_request.get("payload", worker_request)
        query = worker_request.get("query") if isinstance(worker_request, dict) else None

        from cvrp_api_server import _build_request_config_override

        config_override, applied_settings, ignored_settings = _build_request_config_override(payload, query)
        result = solve_current_tsp_route(payload, config_override or get_config())
        if applied_settings or ignored_settings:
            result["settings_overrides"] = applied_settings
            result["ignored_settings"] = ignored_settings

        _write_json(output_path, result)
        return 0
    except Exception as exc:
        traceback.print_exc()
        _write_json(
            output_path,
            {
                "status": "error",
                "type": "current_tsp",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CVRP current TSP subprocess worker")
    parser.add_argument("input_json")
    parser.add_argument("output_json")
    args = parser.parse_args(argv)
    return run_worker_from_files(args.input_json, args.output_json)


if __name__ == "__main__":
    sys.exit(main())
