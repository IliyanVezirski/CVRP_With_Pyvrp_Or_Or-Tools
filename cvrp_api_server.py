"""
HTTP API server for running CVRP optimisation from another program.

Endpoints:
  GET  /health
  POST /solve
  GET  /run
  POST /run

POST /solve accepts either a JSON list of customer records or an object with a
list under one of these keys: customers, clients, orders, data, items, records.
The record fields are mapped by config.InputConfig JSON settings.

GET/POST /run starts the optimiser with the configured input source and returns
immediately. Optional settings in JSON body or query string override the
configuration for that request only.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import datetime
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from config import (
    MainConfig,
    RoutingEngine,
    TrafficZoneConfig,
    VehicleConfig,
    VehicleType,
    get_config,
)
from input_handler import InputHandler
from main import run_optimization


logger = logging.getLogger(__name__)
_TRIGGER_COMMANDS = {"run", "start", "trigger", "solve_config", "start_program"}
_RUN_LOCK = threading.Lock()
_RUN_STATUS: Dict[str, Any] = {
    "running": False,
    "status": "idle",
    "run_id": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "result": None,
    "settings_overrides": [],
    "ignored_settings": [],
    "callback_url": None,
    "notification": None,
}

_SETTINGS_CONTAINER_KEYS = ("settings", "config", "options", "overrides")
_JSON_RECORD_KEYS = {"customers", "clients", "orders", "data", "items", "records"}
_ALLOWED_SETTING_SECTIONS = {
    "cvrp",
    "routing",
    "osrm",
    "valhalla",
    "input",
    "warehouse",
    "locations",
    "output",
    "set_data",
}
_SPECIAL_SETTING_KEYS = {
    "depots",
    "depot_locations",
    "main_depot",
    "depot_location",
    "center_location",
    "center_zone",
    "traffic_zones",
    "city_traffic",
    "vratza_depot",
    "vratza_depot_location",
}
_TOP_LEVEL_SETTING_ALIASES = {
    "solver": ("cvrp", "solver_type"),
    "solver_type": ("cvrp", "solver_type"),
    "objective": ("cvrp", "objective_metric"),
    "objective_metric": ("cvrp", "objective_metric"),
    "optimize_by": ("cvrp", "objective_metric"),
    "optimization_objective": ("cvrp", "objective_metric"),
    "time_limit": ("cvrp", "time_limit_seconds"),
    "time_limit_seconds": ("cvrp", "time_limit_seconds"),
    "parallel": ("cvrp", "enable_parallel_solving"),
    "enable_parallel_solving": ("cvrp", "enable_parallel_solving"),
    "workers": ("cvrp", "num_workers"),
    "num_workers": ("cvrp", "num_workers"),
    "pyvrp_seed": ("cvrp", "pyvrp_seed"),
    "pyvrp_seed_base": ("cvrp", "pyvrp_seed_base"),
    "pyvrp_num_neighbours": ("cvrp", "pyvrp_num_neighbours"),
    "enable_customer_time_windows": ("cvrp", "enable_customer_time_windows"),
    "routing_engine": ("routing", "engine"),
    "engine": ("routing", "engine"),
    "departure_time": ("routing", "departure_time"),
    "osrm_url": ("osrm", "base_url"),
    "osrm_base_url": ("osrm", "base_url"),
    "osrm_profile": ("osrm", "profile"),
    "osrm_timeout": ("osrm", "timeout_seconds"),
    "osrm_chunk_size": ("osrm", "chunk_size"),
    "input_source": ("input", "input_source"),
    "date": ("input", "json_override_date"),
    "json_override_date": ("input", "json_override_date"),
    "sklad": ("input", "json_sklad"),
    "json_sklad": ("input", "json_sklad"),
    "done_flag": ("input", "json_done_flag"),
    "json_done_flag": ("input", "json_done_flag"),
    "map_output_file": ("output", "map_output_file"),
    "routes_output_dir": ("output", "routes_output_dir"),
    "excel_output_dir": ("output", "excel_output_dir"),
    "csv_output_file": ("output", "csv_output_file"),
    "charts_output_dir": ("output", "charts_output_dir"),
    "map_provider": ("output", "map_provider"),
    "enable_excel_output": ("output", "enable_excel_output"),
    "enable_csv_output": ("output", "enable_csv_output"),
    "enable_charts": ("output", "enable_charts"),
    "enable_interactive_map": ("output", "enable_interactive_map"),
    "enable_set_data_upload": ("set_data", "enable_set_data_upload"),
    "set_data_upload": ("set_data", "enable_set_data_upload"),
    "set_data_url": ("set_data", "set_data_url"),
    "set_data_method": ("set_data", "set_data_http_method"),
    "set_data_http_method": ("set_data", "set_data_http_method"),
    "set_data_done_flag": ("set_data", "set_data_done_flag"),
    "set_data_unserved_done_flag": ("set_data", "set_data_unserved_done_flag"),
    "unserved_done_flag": ("set_data", "set_data_unserved_done_flag"),
    "set_data_timeout": ("set_data", "set_data_timeout_seconds"),
    "set_data_timeout_seconds": ("set_data", "set_data_timeout_seconds"),
    "main_depot": ("locations", "depot_location"),
    "depot_location": ("locations", "depot_location"),
    "center_location": ("locations", "center_location"),
    "vratza_depot": ("locations", "vratza_depot_location"),
    "vratza_depot_location": ("locations", "vratza_depot_location"),
    "center_zone_mode": ("locations", "center_zone_mode"),
    "center_zone_radius": ("locations", "center_zone_radius_km"),
    "center_zone_radius_km": ("locations", "center_zone_radius_km"),
    "city_traffic_center": ("locations", "city_center_coords"),
    "city_traffic_radius": ("locations", "city_traffic_radius_km"),
    "city_traffic_radius_km": ("locations", "city_traffic_radius_km"),
    "city_traffic_multiplier": ("locations", "city_traffic_duration_multiplier"),
    "traffic_multiplier": ("locations", "city_traffic_duration_multiplier"),
}
_QUERY_SETTING_ALIASES = {
    "solver": "solver",
    "solver_type": "solver_type",
    "objective": "objective",
    "objective_metric": "objective_metric",
    "optimize_by": "optimize_by",
    "optimization_objective": "optimization_objective",
    "time_limit": "time_limit",
    "time_limit_seconds": "time_limit_seconds",
    "parallel": "parallel",
    "workers": "workers",
    "num_workers": "num_workers",
    "pyvrp_seed": "pyvrp_seed",
    "pyvrp_seed_base": "pyvrp_seed_base",
    "routing_engine": "routing_engine",
    "engine": "engine",
    "osrm_url": "osrm_url",
    "osrm_base_url": "osrm_base_url",
    "osrm_profile": "osrm_profile",
    "osrm_timeout": "osrm_timeout",
    "osrm_chunk_size": "osrm_chunk_size",
    "date": "date",
    "json_override_date": "json_override_date",
    "sklad": "sklad",
    "json_sklad": "json_sklad",
    "done_flag": "done_flag",
    "json_done_flag": "json_done_flag",
    "map_output_file": "map_output_file",
    "routes_output_dir": "routes_output_dir",
    "excel_output_dir": "excel_output_dir",
    "csv_output_file": "csv_output_file",
    "charts_output_dir": "charts_output_dir",
    "map_provider": "map_provider",
    "enable_excel_output": "enable_excel_output",
    "enable_csv_output": "enable_csv_output",
    "enable_charts": "enable_charts",
    "enable_interactive_map": "enable_interactive_map",
    "enable_set_data_upload": "enable_set_data_upload",
    "set_data_upload": "set_data_upload",
    "set_data_url": "set_data_url",
    "set_data_method": "set_data_method",
    "set_data_http_method": "set_data_http_method",
    "set_data_done_flag": "set_data_done_flag",
    "set_data_unserved_done_flag": "set_data_unserved_done_flag",
    "unserved_done_flag": "unserved_done_flag",
    "set_data_timeout": "set_data_timeout",
    "set_data_timeout_seconds": "set_data_timeout_seconds",
    "main_depot": "main_depot",
    "depot_location": "depot_location",
    "center_location": "center_location",
    "vratza_depot": "vratza_depot",
    "vratza_depot_location": "vratza_depot_location",
    "center_zone_mode": "center_zone_mode",
    "center_zone_radius": "center_zone_radius",
    "center_zone_radius_km": "center_zone_radius_km",
    "city_traffic_center": "city_traffic_center",
    "city_traffic_radius": "city_traffic_radius",
    "city_traffic_radius_km": "city_traffic_radius_km",
    "city_traffic_multiplier": "city_traffic_multiplier",
    "traffic_multiplier": "traffic_multiplier",
}


def _normalise_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "/solve").strip()
    return endpoint if endpoint.startswith("/") else f"/{endpoint}"


def _detect_machine_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]
            if address and not address.startswith("127.") and not address.startswith("169.254."):
                return address
    except OSError:
        pass

    return "127.0.0.1"


def _display_host_for_api(host: str) -> str:
    host = str(host or "").strip()
    if host in {"", "0.0.0.0", "::"}:
        return _detect_machine_ipv4()
    return host


def _build_public_base_url(api_config, host: str, port: int) -> str:
    configured_url = (getattr(api_config, "api_public_url", "") or "").strip().rstrip("/")
    if configured_url:
        return configured_url

    display_host = _display_host_for_api(host)
    return f"http://{display_host}:{port}"


def _api_logs_dir() -> str:
    logs_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


def _configure_api_logging() -> None:
    logs_dir = _api_logs_dir()
    log_file = os.path.join(logs_dir, "cvrp_api_server.log")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    existing_files = {
        os.path.normcase(getattr(handler, "baseFilename", ""))
        for handler in root_logger.handlers
        if getattr(handler, "baseFilename", "")
    }
    if os.path.normcase(log_file) not in existing_files:
        handler = logging.FileHandler(log_file, "a", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        root_logger.addHandler(handler)

    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def _persist_run_status(status: Optional[Dict[str, Any]] = None) -> None:
    try:
        status_payload = deepcopy(status) if status is not None else _run_status_snapshot()
        status_file = os.path.join(_api_logs_dir(), "api_run_status.json")
        tmp_file = f"{status_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as fh:
            json.dump(status_payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_file, status_file)
    except Exception as exc:
        logger.warning("Could not persist API run status: %s", exc)


def _child_python_executable() -> str:
    executable = sys.executable
    if os.path.basename(executable).lower() == "pythonw.exe":
        python_exe = os.path.join(os.path.dirname(executable), "python.exe")
        if os.path.exists(python_exe):
            return python_exe
    return executable


def _configured_run_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]

    main_py = os.path.join(os.getcwd(), "main.py")
    return [_child_python_executable(), main_py]


def _run_status_result_for_process(exit_code: Optional[int], command: list[str]) -> Dict[str, Any]:
    return {
        "execution_mode": "subprocess",
        "exit_code": exit_code,
        "command": command,
    }


def _hidden_process_kwargs() -> Dict[str, Any]:
    if os.name != "nt":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def _api_commands_reference(public_url: str, solve_endpoint: str, trigger_endpoint: str, health_endpoint: str) -> Dict[str, Any]:
    return {
        "health": {
            "method": "GET",
            "url": f"{public_url}{health_endpoint}",
            "description": "Статус, URL-и, текущ run и settings_schema.",
        },
        "run_get": {
            "method": "GET",
            "url": f"{public_url}{trigger_endpoint}",
            "description": "Стартира оптимизацията с текущия input_source и config.",
            "query_examples": [
                f"{public_url}{trigger_endpoint}?solver=pyvrp&objective=time&time_limit=180",
                f"{public_url}{trigger_endpoint}?output.enable_excel_output=true&set_data.enable_set_data_upload=false",
                f"{public_url}{trigger_endpoint}?callback_url=https://example.com/cvrp-finished",
            ],
        },
        "run_post": {
            "method": "POST",
            "url": f"{public_url}{trigger_endpoint}",
            "description": "Стартира оптимизацията с текущия input_source и optional settings body. С return_result=true връща пълния JSON резултат директно. С callback_url връща веднага 202 и праща POST известие след края.",
            "body_example": {
                "return_result": True,
                "callback_url": "https://example.com/cvrp-finished",
                "settings": {
                    "solver_type": "pyvrp",
                    "objective_metric": "time",
                    "time_limit_seconds": 180,
                    "output": {
                        "enable_excel_output": True,
                        "excel_output_dir": r"H:\Hell_Bizant_files\Bizant_with_vratza",
                    },
                    "vehicles": [
                        {
                            "vehicle_type": "internal_bus",
                            "start_depot_name": "main",
                            "end_location": [42.7000, 23.4000],
                        }
                    ],
                    "set_data": {
                        "enable_set_data_upload": False,
                        "set_data_done_flag": "1973",
                        "set_data_unserved_done_flag": "1975",
                    },
                }
            },
        },
        "solve_post": {
            "method": "POST",
            "url": f"{public_url}{solve_endpoint}",
            "description": "Приема клиенти като JSON array или wrapper с customers/clients/orders/data/items/records.",
            "body_example": {
                "settings": {"solver_type": "or_tools", "objective_metric": "distance", "output.map_provider": "google"},
                "customers": [
                    {
                        "IdCust": "1",
                        "CustName": "Клиент",
                        "GPS": "42.6977,23.3219",
                        "Volume": 10,
                        "WorkTime": "08:00 - 16:00",
                    }
                ],
            },
        },
        "trigger_commands": sorted(_TRIGGER_COMMANDS),
        "auth": "Ако api_key е зададен: X-CVRP-API-Key или Authorization: Bearer.",
    }


def _settings_schema_reference(config: MainConfig) -> Dict[str, Any]:
    schema: Dict[str, Any] = {}
    for section_name in sorted(_ALLOWED_SETTING_SECTIONS):
        section_obj = getattr(config, section_name, None)
        if is_dataclass(section_obj):
            schema[section_name] = [field.name for field in fields(section_obj)]

    schema["vehicles"] = [field.name for field in fields(VehicleConfig)]
    schema["replace_vehicles"] = "Списък със същия формат като vehicles, но подменя всички бусове."
    schema["vehicle_counts"] = "Обект {vehicle_type: count}, напр. {\"internal_bus\": 7}."
    schema["remote_location_helpers"] = sorted(_SPECIAL_SETTING_KEYS)
    schema["aliases"] = sorted(_TOP_LEVEL_SETTING_ALIASES.keys())
    schema["notes"] = [
        "Всички settings са временни за конкретната заявка и не променят config.py.",
        "JSON може да използва вложени секции или точкова нотация, напр. output.excel_output_dir.",
        "vehicles поддържа end_location или end_depot_name. Ако липсва, маршрутът завършва в стартовото депо.",
    ]
    return schema


def _request_bool_option(payload: Any, query: Optional[Dict[str, list[str]]], names: set[str]) -> bool:
    if query:
        for name in names:
            values = query.get(name)
            if values:
                return _parse_bool(values[-1])

    if isinstance(payload, dict):
        for name in names:
            if name in payload:
                return _parse_bool(payload.get(name))
        for container_name in ("options", "request"):
            container = payload.get(container_name)
            if isinstance(container, dict):
                for name in names:
                    if name in container:
                        return _parse_bool(container.get(name))

    return False


def _request_text_option(payload: Any, query: Optional[Dict[str, list[str]]], names: set[str]) -> str:
    if query:
        for name in names:
            values = query.get(name)
            if values:
                return str(values[-1] or "").strip()

    if isinstance(payload, dict):
        for name in names:
            if name in payload:
                return str(payload.get(name) or "").strip()
        for container_name in ("options", "request"):
            container = payload.get(container_name)
            if isinstance(container, dict):
                for name in names:
                    if name in container:
                        return str(container.get(name) or "").strip()

    return ""


def _request_wants_inline_result(payload: Any, query: Optional[Dict[str, list[str]]]) -> bool:
    return _request_bool_option(
        payload,
        query,
        {"return_result", "return_json", "wait", "sync", "include_result"},
    )


def _request_callback_url(payload: Any, query: Optional[Dict[str, list[str]]]) -> str:
    return _request_text_option(
        payload,
        query,
        {"callback_url", "notify_url", "webhook_url", "callback"},
    )


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalise_path(path: str) -> str:
    stripped = (path or "/").rstrip("/")
    return stripped or "/"


def _merge_nested_settings(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_nested_settings(target[key], value)
        else:
            target[key] = value
    return target


def _expand_dotted_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    expanded: Dict[str, Any] = {}
    for key, value in (settings or {}).items():
        if isinstance(key, str) and "." in key:
            section, field_name = key.split(".", 1)
            if section in _ALLOWED_SETTING_SECTIONS:
                expanded.setdefault(section, {})[field_name] = value
                continue
        if isinstance(value, dict) and isinstance(expanded.get(key), dict):
            _merge_nested_settings(expanded[key], value)
            continue
        expanded[key] = value
    return expanded


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if text in {"0", "false", "no", "n", "off", "не"}:
        return False
    raise ValueError(f"Невалидна bool стойност: {value!r}")


def _parse_coords(value: Any) -> Optional[tuple[float, float]]:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for nested_key in ("location", "coords", "coordinates", "center", "center_coords"):
            if nested_key in value:
                return _parse_coords(value.get(nested_key))
        lat = value.get("lat", value.get("latitude"))
        lon = value.get("lon", value.get("lng", value.get("longitude")))
        return (float(lat), float(lon))
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
        if len(parts) != 2:
            raise ValueError(f"Координатите трябва да са 'lat, lon': {value!r}")
        return (float(parts[0]), float(parts[1]))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (float(value[0]), float(value[1]))
    raise ValueError(f"Невалидни координати: {value!r}")


def _parse_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [
        part.strip()
        for part in str(value).replace(",", "\n").splitlines()
        if part.strip()
    ]


def _parse_coords_list(value: Any) -> list[tuple[float, float]]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [_parse_coords(line) for line in value.splitlines() if line.strip()]
    if isinstance(value, list):
        return [_parse_coords(item) for item in value if item not in (None, "")]
    raise ValueError(f"Невалиден списък с координати: {value!r}")


def _parse_vehicle_type(value: Any) -> VehicleType:
    if isinstance(value, VehicleType):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("vehicle_type е задължителен за vehicle override")
    normalized = text.lower()
    for vehicle_type in VehicleType:
        if normalized in {vehicle_type.value.lower(), vehicle_type.name.lower()}:
            return vehicle_type
    raise ValueError(f"Невалиден vehicle_type: {value!r}")


def _parse_routing_engine(value: Any) -> RoutingEngine:
    if isinstance(value, RoutingEngine):
        return value
    text = str(value or "").strip().lower()
    for engine in RoutingEngine:
        if text in {engine.value.lower(), engine.name.lower()}:
            return engine
    raise ValueError(f"Невалиден routing engine: {value!r}")


def _coerce_setting_value(section_name: str, field_name: str, value: Any, current_value: Any) -> Any:
    if value == "" and field_name in {
        "pyvrp_seed",
        "max_distance_km",
        "max_customers_per_route",
        "start_location",
        "end_location",
        "tsp_depot_location",
        "sheet_name",
    }:
        return None

    if section_name == "routing" and field_name == "engine":
        return _parse_routing_engine(value)
    if section_name == "vehicles" and field_name == "vehicle_type":
        return _parse_vehicle_type(value)
    if field_name in {"start_location", "end_location", "tsp_depot_location", "depot_location", "center_location", "vratza_depot_location", "city_center_coords", "center_coords"}:
        return _parse_coords(value)
    if field_name == "center_zone_polygon":
        return _parse_coords_list(value)
    if field_name in {"parallel_first_solution_strategies", "parallel_local_search_metaheuristics"}:
        return _parse_string_list(value)
    if field_name == "traffic_zones":
        return [_traffic_zone_from_payload(item) for item in (value or [])]
    if field_name == "depot_locations" and isinstance(value, dict):
        return {str(name): _parse_coords(coords) for name, coords in value.items()}

    if isinstance(current_value, RoutingEngine):
        return _parse_routing_engine(value)
    if isinstance(current_value, VehicleType):
        return _parse_vehicle_type(value)
    if isinstance(current_value, bool):
        return _parse_bool(value)
    if isinstance(current_value, int) and not isinstance(current_value, bool):
        return int(value)
    if isinstance(current_value, float):
        return float(value)
    if isinstance(current_value, tuple):
        return _parse_coords(value)
    if isinstance(current_value, list):
        return value if isinstance(value, list) else _parse_string_list(value)

    if current_value is None and field_name in {
        "pyvrp_seed",
        "max_distance_km",
        "max_customers_per_route",
        "end_location",
        "start_location",
        "tsp_depot_location",
    }:
        if field_name in {"start_location", "end_location", "tsp_depot_location"}:
            return _parse_coords(value)
        return None if value in (None, "") else int(value)

    return value


def _traffic_zone_from_payload(payload: Any) -> TrafficZoneConfig:
    if isinstance(payload, TrafficZoneConfig):
        return payload
    if not isinstance(payload, dict):
        raise ValueError(f"traffic_zones трябва да съдържа обекти: {payload!r}")
    return TrafficZoneConfig(
        name=str(payload.get("name", "Traffic zone")),
        center_coords=_parse_coords(payload.get("center_coords", payload.get("center", payload))),
        radius_km=float(payload.get("radius_km", payload.get("radius", 0)) or 0),
        duration_multiplier=float(payload.get("duration_multiplier", payload.get("multiplier", payload.get("delay_multiplier", 1.0))) or 1.0),
        enabled=_parse_bool(payload.get("enabled", True)),
    )


def _depot_role(value: Any) -> str:
    text = str(value or "").strip().lower()
    role_map = {
        "main": "main",
        "default": "main",
        "depot": "main",
        "base": "main",
        "главно": "main",
        "главно депо": "main",
        "основно": "main",
        "основно депо": "main",
        "center": "center",
        "centre": "center",
        "център": "center",
        "център депо": "center",
        "vratza": "vratza",
        "vratsa": "vratza",
        "враца": "vratza",
        "враца депо": "vratza",
    }
    return role_map.get(text, "")


def _named_depots(config: MainConfig) -> Dict[str, tuple[float, float]]:
    locations = config.locations
    depots: Dict[str, tuple[float, float]] = {
        "main": locations.depot_location,
        "depot": locations.depot_location,
        "главно депо": locations.depot_location,
        "основно депо": locations.depot_location,
        "center": locations.center_location,
        "център": locations.center_location,
        "vratza": locations.vratza_depot_location,
        "vratsa": locations.vratza_depot_location,
        "враца": locations.vratza_depot_location,
    }
    for name, coords in (getattr(locations, "depot_locations", {}) or {}).items():
        depots[str(name).strip().lower()] = _parse_coords(coords)
    return depots


def _resolve_depot_reference(config: MainConfig, value: Any) -> Optional[tuple[float, float]]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        named = _named_depots(config).get(value.strip().lower())
        if named:
            return named
    return _parse_coords(value)


def _depot_entry_name_and_coords(entry: Any) -> tuple[str, tuple[float, float], str]:
    if isinstance(entry, dict):
        name = str(entry.get("name", entry.get("id", entry.get("label", ""))) or "").strip()
        role = _depot_role(entry.get("role", entry.get("type", name)))
        coords = _parse_coords(entry)
        return name, coords, role
    raise ValueError(f"Депото трябва да е JSON обект: {entry!r}")


def _apply_single_depot(config: MainConfig, name: str, coords: tuple[float, float], role: str = "") -> list[str]:
    locations = config.locations
    applied: list[str] = []
    role = role or _depot_role(name)
    if role == "main":
        locations.depot_location = coords
        applied.append("locations.depot_location")
    elif role == "center":
        locations.center_location = coords
        applied.append("locations.center_location")
    elif role == "vratza":
        locations.vratza_depot_location = coords
        applied.append("locations.vratza_depot_location")
    else:
        if not name:
            raise ValueError("Допълнително депо без role трябва да има name")
        depot_locations = dict(getattr(locations, "depot_locations", {}) or {})
        depot_locations[name] = coords
        locations.depot_locations = depot_locations
        applied.append(f"locations.depot_locations.{name}")
    return applied


def _apply_depots(config: MainConfig, value: Any) -> list[str]:
    applied: list[str] = []
    if isinstance(value, dict):
        for name, coords_payload in value.items():
            if isinstance(coords_payload, dict) and any(key in coords_payload for key in ("location", "coords", "coordinates", "center", "lat", "latitude")):
                entry_name = str(coords_payload.get("name", name))
                role = _depot_role(coords_payload.get("role", coords_payload.get("type", name)))
                coords = _parse_coords(coords_payload)
            else:
                entry_name = str(name)
                role = _depot_role(name)
                coords = _parse_coords(coords_payload)
            applied.extend(_apply_single_depot(config, entry_name, coords, role))
        return applied

    if isinstance(value, list):
        for entry in value:
            name, coords, role = _depot_entry_name_and_coords(entry)
            applied.extend(_apply_single_depot(config, name, coords, role))
        return applied

    raise ValueError("depots/depot_locations трябва да е JSON обект или списък")


def _apply_center_zone(config: MainConfig, value: Any) -> list[str]:
    if not isinstance(value, dict):
        raise ValueError("center_zone трябва да е JSON обект")
    locations = config.locations
    mapping = {
        "mode": "center_zone_mode",
        "radius": "center_zone_radius_km",
        "radius_km": "center_zone_radius_km",
        "polygon": "center_zone_polygon",
        "center": "center_location",
        "center_location": "center_location",
        "enabled": "enable_center_zone_restrictions",
        "enable_priority": "enable_center_zone_priority",
        "enable_restrictions": "enable_center_zone_restrictions",
        "center_bus_outside_penalty": "center_bus_outside_center_penalty",
        "internal_bus_penalty": "internal_bus_center_penalty",
        "external_bus_penalty": "external_bus_center_penalty",
        "special_bus_penalty": "special_bus_center_penalty",
        "vratza_bus_penalty": "vratza_bus_center_penalty",
        "discount_center_bus": "discount_center_bus",
    }
    applied: list[str] = []
    for key, raw_value in value.items():
        field_name = mapping.get(key, key if hasattr(locations, key) else "")
        if not field_name or not hasattr(locations, field_name):
            continue
        current_value = getattr(locations, field_name)
        setattr(locations, field_name, _coerce_setting_value("locations", field_name, raw_value, current_value))
        applied.append(f"locations.{field_name}")
    return applied


def _apply_city_traffic(config: MainConfig, value: Any) -> list[str]:
    if not isinstance(value, dict):
        raise ValueError("city_traffic трябва да е JSON обект")
    locations = config.locations
    mapping = {
        "enabled": "enable_city_traffic_adjustment",
        "center": "city_center_coords",
        "center_coords": "city_center_coords",
        "radius": "city_traffic_radius_km",
        "radius_km": "city_traffic_radius_km",
        "multiplier": "city_traffic_duration_multiplier",
        "duration_multiplier": "city_traffic_duration_multiplier",
    }
    applied: list[str] = []
    for key, raw_value in value.items():
        field_name = mapping.get(key, key if hasattr(locations, key) else "")
        if not field_name or not hasattr(locations, field_name):
            continue
        current_value = getattr(locations, field_name)
        setattr(locations, field_name, _coerce_setting_value("locations", field_name, raw_value, current_value))
        applied.append(f"locations.{field_name}")
    return applied


def _apply_special_location_setting(config: MainConfig, key: str, value: Any) -> Optional[list[str]]:
    if key in {"depots", "depot_locations"}:
        return _apply_depots(config, value)
    if key == "center_zone":
        return _apply_center_zone(config, value)
    if key == "traffic_zones":
        config.locations.traffic_zones = [_traffic_zone_from_payload(item) for item in (value or [])]
        return ["locations.traffic_zones"]
    if key == "city_traffic":
        return _apply_city_traffic(config, value)
    return None


def _apply_dataclass_section(section_obj: Any, section_name: str, values: Dict[str, Any]) -> tuple[list[str], list[str]]:
    applied: list[str] = []
    ignored: list[str] = []
    if not is_dataclass(section_obj):
        return applied, [section_name]

    allowed_fields = {field.name for field in fields(section_obj)}
    for field_name, value in (values or {}).items():
        if field_name not in allowed_fields:
            ignored.append(f"{section_name}.{field_name}")
            continue
        current_value = getattr(section_obj, field_name)
        setattr(section_obj, field_name, _coerce_setting_value(section_name, field_name, value, current_value))
        applied.append(f"{section_name}.{field_name}")
    return applied, ignored


def _vehicle_from_payload(config: MainConfig, payload: Dict[str, Any], template: Optional[VehicleConfig] = None) -> VehicleConfig:
    if not isinstance(payload, dict):
        raise ValueError("vehicles трябва да е списък от JSON обекти")

    payload = dict(payload)
    for alias in ("start_depot", "start_depot_name", "depot", "depot_name"):
        if alias in payload and payload.get(alias):
            payload["start_location"] = _resolve_depot_reference(config, payload.get(alias))
    for alias in ("end_depot", "end_depot_name", "final_depot", "final_depot_name"):
        if alias in payload and payload.get(alias):
            payload["end_location"] = _resolve_depot_reference(config, payload.get(alias))
    for alias in ("tsp_depot", "tsp_depot_name"):
        if alias in payload and payload.get(alias):
            payload["tsp_depot_location"] = _resolve_depot_reference(config, payload.get(alias))

    vehicle_type = _parse_vehicle_type(payload.get("vehicle_type", getattr(template, "vehicle_type", None)))
    vehicle = deepcopy(template) if template is not None else VehicleConfig(vehicle_type=vehicle_type, capacity=320, count=1)
    vehicle.vehicle_type = vehicle_type

    allowed_fields = {field.name for field in fields(VehicleConfig)}
    for field_name, value in payload.items():
        if field_name not in allowed_fields:
            continue
        if field_name in {"start_location", "end_location", "tsp_depot_location"} and isinstance(value, str):
            value = _resolve_depot_reference(config, value)
        current_value = getattr(vehicle, field_name)
        setattr(vehicle, field_name, _coerce_setting_value("vehicles", field_name, value, current_value))

    return vehicle


def _patch_vehicles(config: MainConfig, vehicle_items: Any, replace: bool = False) -> list[str]:
    if not isinstance(vehicle_items, list):
        raise ValueError("vehicles/replace_vehicles трябва да бъде JSON списък")

    existing = list(config.vehicles or [])
    by_type = {
        getattr(vehicle.vehicle_type, "value", str(vehicle.vehicle_type)): vehicle
        for vehicle in existing
    }

    if replace:
        config.vehicles = [_vehicle_from_payload(config, item) for item in vehicle_items]
        return ["vehicles.replace"]

    patched_by_type = {key: deepcopy(value) for key, value in by_type.items()}
    order = [getattr(vehicle.vehicle_type, "value", str(vehicle.vehicle_type)) for vehicle in existing]
    applied: list[str] = []

    for item in vehicle_items:
        vehicle_type = _parse_vehicle_type((item or {}).get("vehicle_type"))
        key = vehicle_type.value
        patched_by_type[key] = _vehicle_from_payload(config, item, patched_by_type.get(key))
        if key not in order:
            order.append(key)
        applied.append(f"vehicles.{key}")

    config.vehicles = [patched_by_type[key] for key in order if key in patched_by_type]
    return applied


def _apply_vehicle_counts(config: MainConfig, counts: Any) -> list[str]:
    if not isinstance(counts, dict):
        raise ValueError("vehicle_counts трябва да бъде JSON обект")
    vehicles = list(config.vehicles or [])
    applied: list[str] = []
    for raw_type, raw_count in counts.items():
        vehicle_type = _parse_vehicle_type(raw_type)
        for vehicle in vehicles:
            if vehicle.vehicle_type == vehicle_type:
                vehicle.count = int(raw_count)
                applied.append(f"vehicles.{vehicle_type.value}.count")
                break
    return applied


def _extract_payload_settings(payload: Any) -> Dict[str, Any]:
    settings: Dict[str, Any] = {}
    if not isinstance(payload, dict):
        return settings

    for key in _SETTINGS_CONTAINER_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            _merge_nested_settings(settings, deepcopy(value))

    for key, value in payload.items():
        if key in _SETTINGS_CONTAINER_KEYS or key in _JSON_RECORD_KEYS:
            continue
        if key in _ALLOWED_SETTING_SECTIONS or key in _SPECIAL_SETTING_KEYS or key in {"vehicles", "replace_vehicles", "vehicle_counts"}:
            settings[key] = deepcopy(value)
        elif key in _TOP_LEVEL_SETTING_ALIASES:
            settings[key] = deepcopy(value)
    return settings


def _extract_query_settings(query: Dict[str, list[str]]) -> Dict[str, Any]:
    settings: Dict[str, Any] = {}
    raw_settings = query.get("settings")
    if raw_settings:
        try:
            parsed = json.loads(raw_settings[0])
            if isinstance(parsed, dict):
                _merge_nested_settings(settings, parsed)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Невалиден JSON в query параметър settings: {exc}") from exc

    for query_key, alias in _QUERY_SETTING_ALIASES.items():
        values = query.get(query_key)
        if values:
            settings[alias] = values[-1]

    for key, values in query.items():
        if "." not in key or not values:
            continue
        section, field_name = key.split(".", 1)
        if section in _ALLOWED_SETTING_SECTIONS:
            settings.setdefault(section, {})[field_name] = values[-1]

    return settings


def _apply_settings_override(config: MainConfig, settings: Dict[str, Any]) -> tuple[list[str], list[str]]:
    applied: list[str] = []
    ignored: list[str] = []

    def apply_one(key: str, value: Any):
        try:
            special_applied = _apply_special_location_setting(config, key, value)
            if special_applied is not None:
                applied.extend(special_applied)
            elif key in _ALLOWED_SETTING_SECTIONS:
                if not isinstance(value, dict):
                    ignored.append(key)
                else:
                    section_obj = getattr(config, key)
                    section_applied, section_ignored = _apply_dataclass_section(section_obj, key, value)
                    applied.extend(section_applied)
                    ignored.extend(section_ignored)
            elif key == "vehicles":
                applied.extend(_patch_vehicles(config, value, replace=False))
            elif key == "replace_vehicles":
                applied.extend(_patch_vehicles(config, value, replace=True))
            elif key == "vehicle_counts":
                applied.extend(_apply_vehicle_counts(config, value))
            elif key in _TOP_LEVEL_SETTING_ALIASES:
                section_name, field_name = _TOP_LEVEL_SETTING_ALIASES[key]
                section_obj = getattr(config, section_name)
                current_value = getattr(section_obj, field_name)
                setattr(section_obj, field_name, _coerce_setting_value(section_name, field_name, value, current_value))
                applied.append(f"{section_name}.{field_name}")
            else:
                ignored.append(key)
        except Exception as exc:
            ignored.append(f"{key}: {exc}")

    # Apply location/depot shortcuts before vehicles so vehicle start_depot_name can resolve names.
    for key, value in (settings or {}).items():
        if key == "locations" or key in _SPECIAL_SETTING_KEYS or key in _TOP_LEVEL_SETTING_ALIASES and _TOP_LEVEL_SETTING_ALIASES[key][0] == "locations":
            apply_one(key, value)

    for key, value in (settings or {}).items():
        if key == "locations" or key in _SPECIAL_SETTING_KEYS or key in _TOP_LEVEL_SETTING_ALIASES and _TOP_LEVEL_SETTING_ALIASES[key][0] == "locations":
            continue
        apply_one(key, value)

    return applied, ignored


def _build_request_config_override(payload: Any = None, query: Optional[Dict[str, list[str]]] = None) -> tuple[Optional[MainConfig], list[str], list[str]]:
    settings: Dict[str, Any] = {}
    _merge_nested_settings(settings, _extract_payload_settings(payload))
    if query:
        _merge_nested_settings(settings, _extract_query_settings(query))
    settings = _expand_dotted_settings(settings)

    if not settings:
        return None, [], []

    request_config = deepcopy(get_config())
    applied, ignored = _apply_settings_override(request_config, settings)
    return request_config if applied else None, applied, ignored


def _auth_error(api_config, query: Optional[Dict[str, list[str]]], headers) -> Optional[str]:
    expected = str(getattr(api_config, "api_key", "") or "").strip()
    if not expected:
        return None

    provided = str(headers.get("X-CVRP-API-Key", "") or "").strip()
    auth_header = str(headers.get("Authorization", "") or "").strip()
    if not provided and auth_header.lower().startswith("bearer "):
        provided = auth_header.split(" ", 1)[1].strip()
    if not provided and query:
        values = query.get("api_key") or query.get("key")
        if values:
            provided = str(values[-1] or "").strip()

    if provided == expected:
        return None
    return "Missing or invalid API key"


def _summarise_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": result.get("status", "ok"),
        "execution_time_seconds": result.get("execution_time_seconds"),
        "routes_count": result.get("routes_count"),
        "dropped_customers_count": result.get("dropped_customers_count"),
        "total_distance_km": result.get("total_distance_km"),
        "total_time_minutes": result.get("total_time_minutes"),
        "output_files": result.get("output_files", {}),
    }


def _run_status_snapshot() -> Dict[str, Any]:
    with _RUN_LOCK:
        return deepcopy(_RUN_STATUS)


def _post_completion_callback(callback_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    callback_url = str(callback_url or "").strip()
    if not callback_url:
        return {"attempted": False}

    notification = {
        "attempted": True,
        "url": callback_url,
        "sent_at": _now_iso(),
        "status_code": None,
        "error": None,
    }
    try:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            callback_url,
            data=raw,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            notification["status_code"] = int(getattr(response, "status", 0) or 0)
        logger.info("API completion callback sent to %s", callback_url)
    except Exception as exc:
        notification["error"] = str(exc)
        logger.error("API completion callback to %s failed: %s", callback_url, exc)

    return notification


def _default_run_worker(run_id: str, config_override: Optional[MainConfig], callback_url: str = ""):
    try:
        logger.info("API trigger run %s started", run_id)
        result = run_optimization(config_override=config_override)
        finished_at = _now_iso()
        result_summary = _summarise_result(result)
        with _RUN_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "status": "completed",
                    "finished_at": finished_at,
                    "error": None,
                    "result": result_summary,
                }
            )
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)
        notification = _post_completion_callback(
            callback_url,
            {
                "status": "completed",
                "success": True,
                "run_id": run_id,
                "finished_at": finished_at,
                "result": result_summary,
            },
        )
        with _RUN_LOCK:
            _RUN_STATUS["notification"] = notification
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)
        logger.info("API trigger run %s completed", run_id)
    except Exception as exc:
        logger.exception("API trigger run %s failed", run_id)
        finished_at = _now_iso()
        with _RUN_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "status": "failed",
                    "finished_at": finished_at,
                    "error": str(exc),
                    "result": None,
                }
            )
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)
        notification = _post_completion_callback(
            callback_url,
            {
                "status": "failed",
                "success": False,
                "run_id": run_id,
                "finished_at": finished_at,
                "error": str(exc),
            },
        )
        with _RUN_LOCK:
            _RUN_STATUS["notification"] = notification
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)


def _default_run_subprocess_worker(run_id: str, callback_url: str = ""):
    command = _configured_run_command()
    stdout_path = os.path.join(_api_logs_dir(), f"api_run_{run_id}.log")
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop("_MEIPASS2", None)

    process = None
    try:
        logger.info("API trigger run %s starting subprocess: %s", run_id, command)
        with open(stdout_path, "a", encoding="utf-8", errors="replace") as stdout_log:
            stdout_log.write(f"\n===== API run {run_id} started {_now_iso()} =====\n")
            stdout_log.write("Command: " + " ".join(command) + "\n")
            stdout_log.flush()
            process = subprocess.Popen(
                command,
                cwd=os.getcwd(),
                env=env,
                stdout=stdout_log,
                stderr=subprocess.STDOUT,
                **_hidden_process_kwargs(),
            )
            with _RUN_LOCK:
                _RUN_STATUS.update(
                    {
                        "process_id": process.pid,
                        "execution_mode": "subprocess",
                        "process_log": stdout_path,
                    }
                )
                status_snapshot = deepcopy(_RUN_STATUS)
            _persist_run_status(status_snapshot)
            exit_code = process.wait()
            stdout_log.write(f"===== API run {run_id} finished {_now_iso()} exit_code={exit_code} =====\n")

        finished_at = _now_iso()
        success = exit_code == 0
        result_summary = _run_status_result_for_process(exit_code, command)
        with _RUN_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "status": "completed" if success else "failed",
                    "finished_at": finished_at,
                    "error": None if success else f"CVRP process exited with code {exit_code}",
                    "result": result_summary if success else None,
                }
            )
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)

        notification = _post_completion_callback(
            callback_url,
            {
                "status": "completed" if success else "failed",
                "success": success,
                "run_id": run_id,
                "finished_at": finished_at,
                "exit_code": exit_code,
                "process_log": stdout_path,
            },
        )
        with _RUN_LOCK:
            _RUN_STATUS["notification"] = notification
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)
        logger.info("API trigger run %s subprocess finished with code %s", run_id, exit_code)
    except Exception as exc:
        logger.exception("API trigger run %s subprocess failed", run_id)
        finished_at = _now_iso()
        with _RUN_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "status": "failed",
                    "finished_at": finished_at,
                    "error": str(exc),
                    "result": None,
                }
            )
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)
        notification = _post_completion_callback(
            callback_url,
            {
                "status": "failed",
                "success": False,
                "run_id": run_id,
                "finished_at": finished_at,
                "error": str(exc),
                "process_log": stdout_path,
            },
        )
        with _RUN_LOCK:
            _RUN_STATUS["notification"] = notification
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)


def _start_default_run(
    config_override: Optional[MainConfig] = None,
    applied_settings: Optional[list[str]] = None,
    ignored_settings: Optional[list[str]] = None,
    callback_url: str = "",
    use_subprocess: bool = False,
) -> tuple[bool, Dict[str, Any]]:
    with _RUN_LOCK:
        if _RUN_STATUS.get("running"):
            return False, deepcopy(_RUN_STATUS)

        run_id = uuid.uuid4().hex[:12]
        _RUN_STATUS.update(
            {
                "running": True,
                "status": "running",
                "run_id": run_id,
                "started_at": _now_iso(),
                "finished_at": None,
                "error": None,
                "result": None,
                "settings_overrides": applied_settings or [],
                "ignored_settings": ignored_settings or [],
                "callback_url": callback_url or None,
                "notification": None,
                "execution_mode": "subprocess" if use_subprocess else "in_process",
            }
        )
        status_snapshot = deepcopy(_RUN_STATUS)
    _persist_run_status(status_snapshot)

    thread = threading.Thread(
        target=_default_run_subprocess_worker if use_subprocess else _default_run_worker,
        args=(run_id, callback_url) if use_subprocess else (run_id, config_override, callback_url),
        daemon=False,
        name=f"cvrp-run-{run_id}",
    )
    thread.start()
    return True, _run_status_snapshot()


def _run_inline(
    config_override: Optional[MainConfig] = None,
    applied_settings: Optional[list[str]] = None,
    ignored_settings: Optional[list[str]] = None,
) -> tuple[bool, Dict[str, Any], Optional[Dict[str, Any]]]:
    with _RUN_LOCK:
        if _RUN_STATUS.get("running"):
            return False, deepcopy(_RUN_STATUS), None

        run_id = uuid.uuid4().hex[:12]
        _RUN_STATUS.update(
            {
                "running": True,
                "status": "running",
                "run_id": run_id,
                "started_at": _now_iso(),
                "finished_at": None,
                "error": None,
                "result": None,
                "settings_overrides": applied_settings or [],
                "ignored_settings": ignored_settings or [],
                "callback_url": None,
                "notification": None,
            }
        )

    try:
        logger.info("API inline run %s started", run_id)
        result = run_optimization(config_override=config_override)
        with _RUN_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "status": "completed",
                    "finished_at": _now_iso(),
                    "error": None,
                    "result": _summarise_result(result),
                }
            )
        logger.info("API inline run %s completed", run_id)
        return True, _run_status_snapshot(), result
    except Exception as exc:
        logger.exception("API inline run %s failed", run_id)
        with _RUN_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "status": "failed",
                    "finished_at": _now_iso(),
                    "error": str(exc),
                    "result": None,
                }
            )
        raise


def _command_from_query(query: Dict[str, list[str]]) -> str:
    for key in ("cmd", "command", "action"):
        values = query.get(key)
        if values:
            return str(values[0] or "").strip().lower()
    return ""


def _command_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("cmd", "command", "action"):
        if key in payload:
            return str(payload.get(key) or "").strip().lower()
    return ""


def _is_trigger_command(query: Dict[str, list[str]], payload: Any = None) -> bool:
    return _command_from_query(query) in _TRIGGER_COMMANDS or _command_from_payload(payload) in _TRIGGER_COMMANDS


class CVRPApiHandler(BaseHTTPRequestHandler):
    server_version = "CVRPApi/1.0"

    def do_GET(self):
        api_config = get_config().api
        parsed = urlparse(self.path)
        path = _normalise_path(parsed.path)
        query = parse_qs(parsed.query)
        health_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "health_endpoint", "/health")))
        trigger_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run")))
        if path == health_endpoint:
            host = self.server.server_address[0]
            port = self.server.server_address[1]
            public_url = _build_public_base_url(api_config, host, port)
            api_endpoint = _normalise_endpoint(getattr(api_config, "api_endpoint", "/solve"))
            trigger_endpoint_url = _normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run"))
            health_endpoint_url = _normalise_endpoint(getattr(api_config, "health_endpoint", "/health"))
            self._send_json(200, {
                "status": "ok",
                "listen_url": f"http://{host}:{port}",
                "public_url": public_url,
                "solve_url": f"{public_url}{api_endpoint}",
                "trigger_url": f"{public_url}{trigger_endpoint_url}",
                "run_status": _run_status_snapshot(),
                "commands": _api_commands_reference(public_url, api_endpoint, trigger_endpoint_url, health_endpoint_url),
                "settings_schema": _settings_schema_reference(get_config()),
            })
            return

        if path == trigger_endpoint or _is_trigger_command(query):
            if (error := _auth_error(api_config, query, self.headers)):
                self._send_json(401, {"status": "error", "error": error})
                return
            self._handle_trigger_run(query=query, allow_inline_result=False)
            return

        self._send_json(404, {"status": "error", "error": "Unknown endpoint"})

    def do_POST(self):
        api_config = get_config().api
        parsed = urlparse(self.path)
        path = _normalise_path(parsed.path)
        query = parse_qs(parsed.query)
        api_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "api_endpoint", "/solve")))
        trigger_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run")))

        try:
            payload = self._read_json_body(required=False)
            if path == trigger_endpoint:
                if (error := _auth_error(api_config, query, self.headers)):
                    self._send_json(401, {"status": "error", "error": error})
                    return
                self._handle_trigger_run(query=query, payload=payload, allow_inline_result=True)
                return

            if path != api_endpoint:
                self._send_json(404, {"status": "error", "error": "Unknown endpoint"})
                return

            if _is_trigger_command(query, payload):
                if (error := _auth_error(api_config, query, self.headers)):
                    self._send_json(401, {"status": "error", "error": error})
                    return
                self._handle_trigger_run(query=query, payload=payload, allow_inline_result=True)
                return
            if (error := _auth_error(api_config, query, self.headers)):
                self._send_json(401, {"status": "error", "error": error})
                return
            if payload is None:
                raise ValueError("Empty request body")
            config_override, applied_settings, ignored_settings = _build_request_config_override(payload, query)
            active_config = config_override or get_config()
            input_data = InputHandler(main_config=active_config).load_data_from_json_records(payload)
            result = run_optimization(input_data_override=input_data, config_override=config_override)
            if applied_settings or ignored_settings:
                result["settings_overrides"] = applied_settings
                result["ignored_settings"] = ignored_settings
            self._send_json(200, result)
        except Exception as exc:
            logger.exception("CVRP API request failed")
            self._send_json(500, {"status": "error", "error": str(exc)})

    def log_message(self, fmt: str, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _handle_trigger_run(
        self,
        query: Optional[Dict[str, list[str]]] = None,
        payload: Any = None,
        allow_inline_result: bool = False,
    ):
        try:
            config_override, applied_settings, ignored_settings = _build_request_config_override(payload, query)
        except Exception as exc:
            self._send_json(400, {"status": "error", "error": str(exc)})
            return

        if allow_inline_result and _request_wants_inline_result(payload, query):
            try:
                started, status, result = _run_inline(config_override, applied_settings, ignored_settings)
            except Exception as exc:
                self._send_json(500, {
                    "status": "error",
                    "error": str(exc),
                    "run": _run_status_snapshot(),
                    "settings_overrides": applied_settings,
                    "ignored_settings": ignored_settings,
                })
                return
            if started and result is not None:
                result["run"] = status
                if applied_settings or ignored_settings:
                    result["settings_overrides"] = applied_settings
                    result["ignored_settings"] = ignored_settings
                self._send_json(200, result)
                return

            self._send_json(409, {
                "status": "already_running",
                "message": "CVRP optimisation is already running.",
                "run": status,
            })
            return

        callback_url = _request_callback_url(payload, query)
        use_subprocess = config_override is None and not applied_settings and not ignored_settings
        started, status = _start_default_run(
            config_override,
            applied_settings,
            ignored_settings,
            callback_url,
            use_subprocess=use_subprocess,
        )
        if started:
            self._send_json(202, {
                "status": "started",
                "message": "CVRP optimisation started with configured input source.",
                "settings_overrides": applied_settings,
                "ignored_settings": ignored_settings,
                "callback_url": callback_url or None,
                "run": status,
            })
            return

        self._send_json(409, {
            "status": "already_running",
            "message": "CVRP optimisation is already running.",
            "run": status,
        })

    def _read_json_body(self, required: bool = True) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            if required:
                raise ValueError("Empty request body")
            return None

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

    _configure_api_logging()

    server = ThreadingHTTPServer((host, port), CVRPApiHandler)
    public_url = _build_public_base_url(api_config, host, port)
    api_endpoint = _normalise_endpoint(getattr(api_config, "api_endpoint", "/solve"))
    trigger_endpoint = _normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run"))
    logger.info("CVRP API server listening on http://%s:%s", host, port)
    logger.info("Public/base URL: %s", public_url)
    logger.info("POST customer JSON to %s%s", public_url, api_endpoint)
    logger.info("Trigger configured run with GET/POST %s%s", public_url, trigger_endpoint)
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
