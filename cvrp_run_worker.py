"""Subprocess worker for isolated CVRP runs started from the web GUI.

The web API writes request-specific output and setData settings to an input
JSON file, then launches this module in a separate process. Keeping the run in
its own process prevents temporary web settings from leaking into the normal
``/run`` or ``/solve`` API flows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Dict

from main import run_optimization


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    """Write a worker result using UTF-8 JSON."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False, indent=2)


def _read_worker_request(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file_handle:
        worker_request = json.load(file_handle)

    if not isinstance(worker_request, dict):
        raise ValueError("Web run worker input must be a JSON object")

    run_settings = worker_request.get("run_settings")
    if not isinstance(run_settings, dict):
        raise ValueError("Web run worker input must contain a run_settings JSON object")

    return worker_request


def run_worker_from_files(input_path: str, output_path: str) -> int:
    """Run one isolated web CVRP request described by two JSON file paths."""
    try:
        worker_request = _read_worker_request(input_path)

        # Imported lazily to avoid a circular import while cvrp_api_server is
        # launching this worker from its own process.
        from cvrp_api_server import _build_web_run_config_override

        config_override, applied_settings, ignored_settings = _build_web_run_config_override(worker_request)
        result = run_optimization(config_override=config_override)
        if not isinstance(result, dict):
            raise TypeError("run_optimization must return a JSON object")

        result["settings_overrides"] = list(applied_settings or [])
        result["ignored_settings"] = list(ignored_settings or [])
        _write_json(output_path, result)
        return 0
    except Exception as exc:
        traceback.print_exc()
        _write_json(
            output_path,
            {
                "status": "error",
                "type": "web_cvrp_run",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CVRP web run subprocess worker")
    parser.add_argument("input_json")
    parser.add_argument("output_json")
    args = parser.parse_args(argv)
    return run_worker_from_files(args.input_json, args.output_json)


if __name__ == "__main__":
    sys.exit(main())
