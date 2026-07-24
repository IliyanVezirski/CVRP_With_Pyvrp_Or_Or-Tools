"""Isolated Python 3.12 worker for the Rust ``vrp-cli`` bindings.

This module is intentionally dependency-light.  The main application builds
the pragmatic problem in its normal Python runtime and this worker only calls
the compiled Rust extension, which currently has no Python 3.14 wheel.
"""

from __future__ import annotations

import argparse
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any, Dict


PROTOCOL_VERSION = 1
SOLVER_BACKEND = "vrp"


def _metadata(**extra: Any) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "solver_backend": SOLVER_BACKEND,
        "solver_version": version("vrp-cli"),
        **extra,
    }


def _read_json(path: str) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file_handle:
        payload = json.load(file_handle)
    if not isinstance(payload, dict):
        raise ValueError("VRP-Rust worker request must be a JSON object.")
    return payload


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False, indent=2)


def run_worker(input_path: str, output_path: str) -> int:
    try:
        import vrp_cli

        request = _read_json(input_path)
        problem = request.get("problem")
        matrices = request.get("matrices")
        solver_config = request.get("solver_config")
        if not isinstance(problem, dict):
            raise ValueError("VRP-Rust request is missing the pragmatic problem.")
        if not isinstance(matrices, list) or not all(isinstance(item, dict) for item in matrices):
            raise ValueError("VRP-Rust request matrices must be a JSON array of objects.")
        if not isinstance(solver_config, dict):
            raise ValueError("VRP-Rust request is missing solver_config.")

        raw_solution = vrp_cli.solve_pragmatic(
            json.dumps(problem, ensure_ascii=False, separators=(",", ":")),
            [
                json.dumps(matrix, ensure_ascii=False, separators=(",", ":"))
                for matrix in matrices
            ],
            json.dumps(solver_config, ensure_ascii=False, separators=(",", ":")),
        )
        solution = json.loads(raw_solution)
        if not isinstance(solution, dict):
            raise ValueError("VRP-Rust returned a non-object solution.")

        _write_json(
            output_path,
            _metadata(ok=True, solution=solution),
        )
        return 0
    except Exception as exc:
        _write_json(
            output_path,
            _metadata(ok=False, error=f"{type(exc).__name__}: {exc}"),
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated VRP-Rust worker")
    parser.add_argument("input_json", nargs="?")
    parser.add_argument("output_json", nargs="?")
    parser.add_argument("--input", dest="input_option")
    parser.add_argument("--output", dest="output_option")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        try:
            import vrp_cli  # noqa: F401

            print(json.dumps(_metadata(ok=True, kind="self_check"), ensure_ascii=False))
            return 0
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "solver_backend": SOLVER_BACKEND,
                        "ok": False,
                        "kind": "self_check",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                )
            )
            return 1

    input_path = args.input_option or args.input_json
    output_path = args.output_option or args.output_json
    if not input_path or not output_path:
        parser.error("both --input and --output are required")
    return run_worker(input_path, output_path)


if __name__ == "__main__":
    raise SystemExit(main())
