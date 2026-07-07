"""
HTTP API server for running CVRP optimisation from another program.

Endpoints:
  GET  /health
  POST /solve
  GET  /run
  POST /run
  POST /tsp

POST /solve accepts either a JSON list of customer records or an object with a
list under one of these keys: customers, clients, orders, data, items, records.
The record fields are mapped by config.InputConfig JSON settings.

GET/POST /run starts the optimiser with the configured input source and returns
immediately. Optional settings in JSON body or query string override the
configuration for that request only.

POST /tsp orders the current route for one driver from a current GPS location
and uses the same endpoint in visible and hidden API server modes.
"""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import datetime
import html
import hmac
import importlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import config as config_module
from config import (
    MainConfig,
    RoutingEngine,
    CenterZoneConfig,
    TrafficZoneConfig,
    VehicleConfig,
    VehicleType,
    get_config,
)
from input_handler import InputHandler
from main import run_optimization
from current_tsp import _parse_tsp_truck_profiles, solve_current_tsp_route
from tsp_daily_report import (
    generate_tsp_daily_excel_report,
    record_tsp_result,
    tsp_daily_report_path,
    tsp_history_file,
)


logger = logging.getLogger(__name__)
_TRIGGER_COMMANDS = {"run", "start", "trigger", "solve_config", "start_program"}
_SHUTDOWN_COMMANDS = {"shutdown", "stop", "stop_program", "exit", "quit"}
_REQUEST_BODY_LOG_LIMIT = 100_000
_SENSITIVE_HEADER_NAMES = {"authorization", "x-cvrp-api-key", "api-key", "x-api-key"}
_FORM_JSON_FIELD_NAMES = ("pData", "payload", "json", "data", "body")
_RUN_LOCK = threading.Lock()
_TSP_REPORT_SCHEDULER_LOCK = threading.Lock()
_TSP_REPORT_SCHEDULER_STARTED = False
_TSP_REPORT_SCHEDULER_LAST_ATTEMPT: Dict[str, str] = {}
_CONFIG_RELOAD_LOCK = threading.Lock()
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
    "stop_requested": False,
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
    "api",
}
_SPECIAL_SETTING_KEYS = {
    "depots",
    "depot_locations",
    "main_depot",
    "depot_location",
    "center_location",
    "center_zone",
    "center_zones",
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
    "json_delivery_comment_field": ("input", "json_delivery_comment_field"),
    "delivery_comment_field": ("input", "json_delivery_comment_field"),
    "enable_customer_document_grouping": ("input", "enable_customer_document_grouping"),
    "group_customer_documents": ("input", "enable_customer_document_grouping"),
    "tsp_default_service_time_minutes": ("api", "tsp_default_service_time_minutes"),
    "tsp_service_time_minutes": ("api", "tsp_default_service_time_minutes"),
    "tsp_objective_metric": ("api", "tsp_objective_metric"),
    "tsp_metric": ("api", "tsp_objective_metric"),
    "tsp_optimize_by": ("api", "tsp_objective_metric"),
    "tsp_use_time_windows": ("api", "tsp_use_time_windows"),
    "tsp_time_windows": ("api", "tsp_use_time_windows"),
    "tsp_time_window_wait_weight": ("api", "tsp_time_window_wait_weight"),
    "tsp_wait_weight": ("api", "tsp_time_window_wait_weight"),
    "tsp_time_window_late_weight": ("api", "tsp_time_window_late_weight"),
    "tsp_late_weight": ("api", "tsp_time_window_late_weight"),
    "tsp_enable_two_opt": ("api", "tsp_enable_two_opt"),
    "tsp_two_opt": ("api", "tsp_enable_two_opt"),
    "tsp_two_opt_max_passes": ("api", "tsp_two_opt_max_passes"),
    "tsp_response_format": ("api", "tsp_response_format"),
    "tsp_return_format": ("api", "tsp_response_format"),
    "tsp_generate_html_map": ("api", "tsp_generate_html_map"),
    "tsp_generate_map": ("api", "tsp_generate_html_map"),
    "tsp_generate_local_html": ("api", "tsp_generate_html_map"),
    "tsp_local_html": ("api", "tsp_generate_html_map"),
    "tsp_local_html_map": ("api", "tsp_generate_html_map"),
    "tsp_upload_html_map": ("api", "tsp_upload_html_map"),
    "tsp_upload_map": ("api", "tsp_upload_html_map"),
    "tsp_worker_timeout": ("api", "tsp_worker_timeout_seconds"),
    "tsp_worker_timeout_seconds": ("api", "tsp_worker_timeout_seconds"),
    "tsp_truck_profiles": ("api", "tsp_valhalla_truck_profiles"),
    "tsp_valhalla_truck_profiles": ("api", "tsp_valhalla_truck_profiles"),
    "tsp_truck_driver_ids": ("api", "tsp_valhalla_truck_driver_ids"),
    "tsp_valhalla_truck_driver_ids": ("api", "tsp_valhalla_truck_driver_ids"),
    "tsp_truck_height": ("api", "tsp_valhalla_truck_height"),
    "tsp_truck_width": ("api", "tsp_valhalla_truck_width"),
    "tsp_truck_length": ("api", "tsp_valhalla_truck_length"),
    "tsp_truck_weight": ("api", "tsp_valhalla_truck_weight"),
    "tsp_truck_axle_load": ("api", "tsp_valhalla_truck_axle_load"),
    "tsp_truck_axle_count": ("api", "tsp_valhalla_truck_axle_count"),
    "tsp_truck_hazmat": ("api", "tsp_valhalla_truck_hazmat"),
    "tsp_truck_hgv_no_access_penalty": ("api", "tsp_valhalla_truck_hgv_no_access_penalty"),
    "tsp_daily_report": ("api", "tsp_daily_report_enabled"),
    "tsp_daily_report_enabled": ("api", "tsp_daily_report_enabled"),
    "tsp_daily_report_time": ("api", "tsp_daily_report_time"),
    "tsp_daily_report_output_dir": ("api", "tsp_daily_report_output_dir"),
    "tsp_daily_report_history_file": ("api", "tsp_daily_report_history_file"),
    "tsp_daily_report_include_details": ("api", "tsp_daily_report_include_details"),
    "map_output_file": ("output", "map_output_file"),
    "routes_output_dir": ("output", "routes_output_dir"),
    "route_maps_upload_mode": ("output", "route_maps_upload_mode"),
    "route_maps_upload_url": ("output", "route_maps_upload_url"),
    "route_maps_upload_token": ("output", "route_maps_upload_token"),
    "route_maps_upload_token_field": ("output", "route_maps_upload_token_field"),
    "route_maps_upload_file_field": ("output", "route_maps_upload_file_field"),
    "route_maps_upload_bus_id_field": ("output", "route_maps_upload_bus_id_field"),
    "route_maps_upload_timeout": ("output", "route_maps_upload_timeout_seconds"),
    "route_maps_upload_timeout_seconds": ("output", "route_maps_upload_timeout_seconds"),
    "upload_route_maps": ("output", "route_maps_upload_mode"),
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
    "center_zones": ("locations", "center_zones"),
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
    "json_delivery_comment_field": "json_delivery_comment_field",
    "delivery_comment_field": "delivery_comment_field",
    "enable_customer_document_grouping": "enable_customer_document_grouping",
    "group_customer_documents": "group_customer_documents",
    "tsp_default_service_time_minutes": "tsp_default_service_time_minutes",
    "tsp_service_time_minutes": "tsp_service_time_minutes",
    "tsp_objective_metric": "tsp_objective_metric",
    "tsp_metric": "tsp_metric",
    "tsp_optimize_by": "tsp_optimize_by",
    "tsp_use_time_windows": "tsp_use_time_windows",
    "tsp_time_windows": "tsp_time_windows",
    "tsp_time_window_wait_weight": "tsp_time_window_wait_weight",
    "tsp_wait_weight": "tsp_wait_weight",
    "tsp_time_window_late_weight": "tsp_time_window_late_weight",
    "tsp_late_weight": "tsp_late_weight",
    "tsp_enable_two_opt": "tsp_enable_two_opt",
    "tsp_two_opt": "tsp_two_opt",
    "tsp_two_opt_max_passes": "tsp_two_opt_max_passes",
    "tsp_response_format": "tsp_response_format",
    "tsp_return_format": "tsp_return_format",
    "tsp_generate_html_map": "tsp_generate_html_map",
    "tsp_generate_map": "tsp_generate_map",
    "tsp_generate_local_html": "tsp_generate_local_html",
    "tsp_local_html": "tsp_local_html",
    "tsp_local_html_map": "tsp_local_html_map",
    "tsp_upload_html_map": "tsp_upload_html_map",
    "tsp_upload_map": "tsp_upload_map",
    "tsp_worker_timeout": "tsp_worker_timeout",
    "tsp_worker_timeout_seconds": "tsp_worker_timeout_seconds",
    "tsp_truck_profiles": "tsp_truck_profiles",
    "tsp_truck_driver_ids": "tsp_truck_driver_ids",
    "tsp_truck_height": "tsp_truck_height",
    "tsp_truck_width": "tsp_truck_width",
    "tsp_truck_length": "tsp_truck_length",
    "tsp_truck_weight": "tsp_truck_weight",
    "tsp_truck_axle_load": "tsp_truck_axle_load",
    "tsp_truck_axle_count": "tsp_truck_axle_count",
    "tsp_truck_hazmat": "tsp_truck_hazmat",
    "tsp_truck_hgv_no_access_penalty": "tsp_truck_hgv_no_access_penalty",
    "tsp_daily_report": "tsp_daily_report",
    "tsp_daily_report_enabled": "tsp_daily_report_enabled",
    "tsp_daily_report_time": "tsp_daily_report_time",
    "tsp_daily_report_output_dir": "tsp_daily_report_output_dir",
    "tsp_daily_report_history_file": "tsp_daily_report_history_file",
    "tsp_daily_report_include_details": "tsp_daily_report_include_details",
    "map_output_file": "map_output_file",
    "routes_output_dir": "routes_output_dir",
    "route_maps_upload_mode": "route_maps_upload_mode",
    "route_maps_upload_url": "route_maps_upload_url",
    "route_maps_upload_token": "route_maps_upload_token",
    "route_maps_upload_token_field": "route_maps_upload_token_field",
    "route_maps_upload_file_field": "route_maps_upload_file_field",
    "route_maps_upload_bus_id_field": "route_maps_upload_bus_id_field",
    "route_maps_upload_timeout": "route_maps_upload_timeout",
    "route_maps_upload_timeout_seconds": "route_maps_upload_timeout_seconds",
    "upload_route_maps": "upload_route_maps",
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
    "center_zones": "center_zones",
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


def _api_commands_reference(
    public_url: str,
    solve_endpoint: str,
    trigger_endpoint: str,
    health_endpoint: str,
    tsp_endpoint: str,
    tsp_report_endpoint: str,
    shutdown_endpoint: str,
) -> Dict[str, Any]:
    tsp_command = {
        "method": "POST",
        "url": f"{public_url}{tsp_endpoint}",
        "description": "Подрежда текущ маршрут за един шофьор от текуща GPS позиция, optional крайна точка и списък клиенти. TSP service time и HTML картите имат отделни default настройки в GUI.",
        "body_example": {
            "driver_id": "1004501001",
            "driver_location": "42.6977,23.3219",
            "end_location": "42.7000,23.4000",
            "service_time_minutes": 8,
            "customers": [
                {
                    "id": "1",
                    "name": "Клиент",
                    "document": "0004384359",
                    "gps": "42.6629,23.37682",
                    "work_time": "08:00-13:00",
                    "turnover": 120.50,
                    "quantity": 5,
                    "comment": "Обади се 10 мин преди доставка",
                }
            ],
        },
        "gui_defaults": {
            "service_time_minutes": "api.tsp_default_service_time_minutes, ако body не подаде service_time_minutes",
            "objective_metric": "api.tsp_objective_metric, ако body не подаде metric/objective",
            "use_time_windows": "api.tsp_use_time_windows",
            "time_window_wait_weight": "api.tsp_time_window_wait_weight",
            "time_window_late_weight": "api.tsp_time_window_late_weight",
            "enable_two_opt": "api.tsp_enable_two_opt",
            "two_opt_max_passes": "api.tsp_two_opt_max_passes",
            "response_format": "api.tsp_response_format: json връща JSON, html връща HTML body на картата",
            "generate_local_html_map": "api.tsp_generate_html_map",
            "upload_html_map": "api.tsp_upload_html_map",
            "worker_timeout_seconds": "api.tsp_worker_timeout_seconds, когато /tsp се изпълнява в отделен процес",
        },
        "settings_examples": [
            {
                "description": "Спира локалното HTML генериране само за TSP, без да пипа CVRP route картите.",
                "settings": {"tsp_generate_map": False},
            },
            {
                "description": "Пуска локалното HTML генериране само за TSP.",
                "settings": {"tsp_generate_map": True},
            },
        ],
    }

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
                f"{public_url}{trigger_endpoint}?route_maps_upload_mode=effect_upload",
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
                        "excel_output_dir": r"D:\CVRP_Output\Run1",
                        "route_maps_upload_mode": "effect_upload",
                        "route_maps_upload_url": "https://YOUR-UPLOAD-SERVER/upload-files.php",
                        "route_maps_upload_token": "YOUR_UPLOAD_TOKEN",
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
                        "WorkTime": "08:00-13:00",
                        "DeliveryComment": "Обади се 10 мин преди доставка",
                    }
                ],
            },
        },
        "tsp_post": tsp_command,
        "current_tsp_post": tsp_command,
        "tsp_daily_report": {
            "methods": ["GET", "POST"],
            "url": f"{public_url}{tsp_report_endpoint}",
            "description": "Генерира Excel отчет за всички TSP маршрути за дадена дата. Query/body date е optional във формат YYYY-MM-DD.",
            "query_examples": [
                f"{public_url}{tsp_report_endpoint}",
                f"{public_url}{tsp_report_endpoint}?date=2026-05-29",
            ],
        },
        "shutdown": {
            "methods": ["GET", "POST"],
            "url": f"{public_url}{shutdown_endpoint}",
            "description": "Спира API сървъра/програмата. Ако има активен subprocess run, прави опит да го спре преди изход.",
            "query_examples": [
                f"{public_url}{shutdown_endpoint}",
                f"{public_url}{solve_endpoint}?cmd=shutdown",
            ],
        },
        "trigger_commands": sorted(_TRIGGER_COMMANDS),
        "shutdown_commands": sorted(_SHUTDOWN_COMMANDS),
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


def _normalise_tsp_response_format(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"html", "text/html", "file", "html_file", "map", "map_html"}:
        return "html"
    return "json"


def _request_tsp_response_format(payload: Any, query: Optional[Dict[str, list[str]]], api_config: Any) -> str:
    if _request_bool_option(payload, query, {"return_json", "json_response", "response_json"}):
        return "json"
    response_type = _request_text_option(
        payload,
        query,
        {"response", "response_type", "return_type", "format", "tsp_response_format"},
    )
    if response_type:
        return _normalise_tsp_response_format(response_type)
    return _normalise_tsp_response_format(getattr(api_config, "tsp_response_format", "json"))


def _request_wants_tsp_html_response(payload: Any, query: Optional[Dict[str, list[str]]], api_config: Any) -> bool:
    return _request_tsp_response_format(payload, query, api_config) == "html"


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
    if section_name == "output" and field_name == "route_maps_upload_mode":
        if isinstance(value, bool):
            return "effect_upload" if value else "disabled"
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return "effect_upload"
        if text in {"0", "false", "no", "off", "none"}:
            return "disabled"
        return text or "disabled"
    if field_name in {"start_location", "end_location", "tsp_depot_location", "depot_location", "center_location", "vratza_depot_location", "city_center_coords", "center_coords"}:
        return _parse_coords(value)
    if field_name == "center_zone_polygon":
        return _parse_coords_list(value)
    if field_name in {"parallel_first_solution_strategies", "parallel_local_search_metaheuristics"}:
        return _parse_string_list(value)
    if field_name == "center_zones":
        if isinstance(value, str):
            value = json.loads(value) if value.strip() else []
        return [_center_zone_from_payload(item) for item in (value or [])]
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


def _vehicle_type_list_from_payload(value: Any) -> list[str]:
    vehicle_types = []
    for item in _parse_string_list(value):
        text = item.strip().lower()
        matched = False
        for vehicle_type in VehicleType:
            if text in {vehicle_type.value.lower(), vehicle_type.name.lower()}:
                vehicle_types.append(vehicle_type.value)
                matched = True
                break
        if not matched and text:
            vehicle_types.append(text)
    return vehicle_types


def _center_zone_from_payload(payload: Any) -> CenterZoneConfig:
    if isinstance(payload, CenterZoneConfig):
        return payload
    if not isinstance(payload, dict):
        raise ValueError(f"center_zones трябва да съдържа JSON обекти: {payload!r}")

    raw_penalties = payload.get("vehicle_penalties", payload.get("penalties", {})) or {}
    vehicle_penalties: dict[str, float] = {}
    if isinstance(raw_penalties, dict):
        for key, raw_value in raw_penalties.items():
            vehicle_key = _vehicle_type_list_from_payload([key])
            if vehicle_key:
                vehicle_penalties[vehicle_key[0]] = float(raw_value or 0)

    priority_types = _vehicle_type_list_from_payload(
        payload.get("priority_vehicle_types", payload.get("priority_buses", payload.get("priority", ["center_bus"])))
    )
    restricted_types = _vehicle_type_list_from_payload(
        payload.get(
            "restricted_vehicle_types",
            payload.get("restricted_buses", payload.get("restricted", ["internal_bus", "external_bus", "special_bus", "vratza_bus"])),
        )
    )
    if not vehicle_penalties:
        vehicle_penalties = {vehicle_type: 40000.0 for vehicle_type in restricted_types}
    center_payload = payload.get("center_coords", payload.get("center", payload.get("location", None)))
    if center_payload is None and any(key in payload for key in ("lat", "latitude", "lon", "lng", "longitude")):
        center_payload = payload
    polygon_payload = payload.get("polygon", payload.get("path", []))
    if isinstance(polygon_payload, str) and ";" in polygon_payload:
        polygon_payload = [part.strip() for part in polygon_payload.split(";") if part.strip()]

    return CenterZoneConfig(
        name=str(payload.get("name", payload.get("label", "Center zone"))),
        mode=str(payload.get("mode", "circle") or "circle").strip().lower(),
        center_coords=_parse_coords(center_payload),
        radius_km=float(payload.get("radius_km", payload.get("radius", 1.0)) or 0),
        polygon=_parse_coords_list(polygon_payload),
        enabled=_parse_bool(payload.get("enabled", True)),
        enable_priority=_parse_bool(payload.get("enable_priority", True)),
        enable_restrictions=_parse_bool(payload.get("enable_restrictions", True)),
        priority_vehicle_types=priority_types,
        restricted_vehicle_types=restricted_types,
        discount_priority_vehicle=float(
            payload.get("discount_priority_vehicle", payload.get("discount", payload.get("discount_center_bus", 0.9))) or 1.0
        ),
        priority_vehicle_outside_penalty=float(
            payload.get(
                "priority_vehicle_outside_penalty",
                payload.get("outside_penalty", payload.get("center_bus_outside_penalty", 0.0)),
            ) or 0.0
        ),
        vehicle_penalties=vehicle_penalties,
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
    applied: list[str] = []
    if "zones" in value:
        locations.center_zones = [_center_zone_from_payload(item) for item in (value.get("zones") or [])]
        applied.append("locations.center_zones")
    if "center_zones" in value:
        locations.center_zones = [_center_zone_from_payload(item) for item in (value.get("center_zones") or [])]
        applied.append("locations.center_zones")
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
    for key, raw_value in value.items():
        if key in {"zones", "center_zones"}:
            continue
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
    if key == "center_zones":
        config.locations.center_zones = [_center_zone_from_payload(item) for item in (value or [])]
        return ["locations.center_zones"]
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


_WEB_GUI_CVRP_FIELDS = {
    "solver_type",
    "objective_metric",
    "time_limit_seconds",
    "allow_customer_skipping",
    "enable_parallel_solving",
    "num_workers",
    "first_solution_strategy",
    "local_search_metaheuristic",
    "lns_time_limit_seconds",
    "lns_num_nodes",
    "lns_num_arcs",
    "use_full_propagation",
    "search_lambda_coefficient",
    "global_start_time_minutes",
    "enable_customer_time_windows",
    "pyvrp_seed",
    "pyvrp_seed_base",
    "pyvrp_num_neighbours",
    "pyvrp_ils_no_improvement",
    "pyvrp_ils_history_length",
    "pyvrp_use_extended_operators",
    "pyvrp_min_perturbations",
    "pyvrp_max_perturbations",
    "pyvrp_display_progress",
}
_WEB_GUI_API_FIELDS = {
    "web_gui_enabled",
    "web_gui_endpoint",
    "web_gui_title",
    "web_gui_users",
    "web_gui_public_host",
    "web_gui_public_url",
}


def _web_gui_endpoint(api_config) -> str:
    return _normalise_path(_normalise_endpoint(getattr(api_config, "web_gui_endpoint", "/ui")))


def _web_gui_public_url(api_config, host: str, port: int) -> str:
    explicit_url = str(getattr(api_config, "web_gui_public_url", "") or "").strip().rstrip("/")
    if explicit_url:
        return explicit_url

    endpoint = _web_gui_endpoint(api_config)
    public_host = str(getattr(api_config, "web_gui_public_host", "") or "").strip()
    if public_host:
        parsed = urlparse(public_host if "://" in public_host else f"http://{public_host}")
        scheme = parsed.scheme or "http"
        netloc = parsed.netloc or parsed.path.strip("/")
        path_prefix = parsed.path.rstrip("/") if parsed.netloc else ""
        has_port = "]:" in netloc if netloc.startswith("[") else ":" in netloc
        if netloc and not has_port and not (scheme == "http" and port == 80) and not (scheme == "https" and port == 443):
            netloc = f"{netloc}:{port}"
        base = f"{scheme}://{netloc}{path_prefix}".rstrip("/")
        return f"{base}{endpoint}"

    return f"{_build_public_base_url(api_config, host, port)}{endpoint}"


def _web_gui_enabled(api_config) -> bool:
    return bool(getattr(api_config, "web_gui_enabled", True))


def _is_web_gui_path(path: str, api_config) -> bool:
    if not _web_gui_enabled(api_config):
        return False
    base = _web_gui_endpoint(api_config)
    path = _normalise_path(path)
    return path == base or path.startswith(f"{base}/")


def _web_gui_subpath(path: str, api_config) -> str:
    base = _web_gui_endpoint(api_config)
    path = _normalise_path(path)
    if path == base:
        return "/"
    return path[len(base):] or "/"


def _parse_web_gui_users(raw_value: Any) -> Dict[str, str]:
    users: Dict[str, str] = {}
    raw_text = str(raw_value or "").replace(";", "\n")
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        username, password = line.split(":", 1)
        username = username.strip()
        password = password.strip()
        if username:
            users[username] = password
    return users


def _extract_basic_auth(headers) -> tuple[str, str]:
    auth_header = str(headers.get("Authorization", "") or "").strip()
    if not auth_header.lower().startswith("basic "):
        return "", ""
    encoded = auth_header.split(" ", 1)[1].strip()
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return "", ""
    if ":" not in decoded:
        return "", ""
    username, password = decoded.split(":", 1)
    return username, password


def _web_gui_auth_error(api_config, headers) -> Optional[str]:
    users = _parse_web_gui_users(getattr(api_config, "web_gui_users", ""))
    if not users:
        return "No web GUI users are configured"

    username, password = _extract_basic_auth(headers)
    if not username:
        return "Missing web GUI login"

    expected_password = users.get(username)
    if expected_password is None:
        return "Invalid web GUI login"
    if hmac.compare_digest(str(expected_password), str(password)):
        return None
    return "Invalid web GUI login"


def _coords_to_text(coords: Any) -> str:
    if not coords:
        return ""
    try:
        return f"{float(coords[0])}, {float(coords[1])}"
    except Exception:
        return ""


def _vehicle_to_web_dict(vehicle: VehicleConfig) -> Dict[str, Any]:
    return {
        "vehicle_type": getattr(getattr(vehicle, "vehicle_type", None), "value", str(getattr(vehicle, "vehicle_type", ""))),
        "name": str(getattr(vehicle, "name", "") or ""),
        "enabled": bool(getattr(vehicle, "enabled", True)),
        "count": int(getattr(vehicle, "count", 0) or 0),
        "capacity": int(getattr(vehicle, "capacity", 0) or 0),
        "fixed_cost": int(getattr(vehicle, "fixed_cost", 0) or 0),
        "max_distance_km": getattr(vehicle, "max_distance_km", None),
        "max_time_hours": int(getattr(vehicle, "max_time_hours", 8) or 8),
        "service_time_minutes": int(getattr(vehicle, "service_time_minutes", 8) or 8),
        "max_customers_per_route": getattr(vehicle, "max_customers_per_route", None),
        "start_location": _coords_to_text(getattr(vehicle, "start_location", None)),
        "end_location": _coords_to_text(getattr(vehicle, "end_location", None)),
        "start_time_minutes": int(getattr(vehicle, "start_time_minutes", 480) or 480),
    }


def _reload_config_from_disk():
    """Reload config.py so the web GUI reflects desktop GUI changes."""
    global MainConfig, RoutingEngine, CenterZoneConfig, TrafficZoneConfig, VehicleConfig, VehicleType, get_config

    with _CONFIG_RELOAD_LOCK:
        module = importlib.reload(config_module)
        MainConfig = module.MainConfig
        RoutingEngine = module.RoutingEngine
        CenterZoneConfig = module.CenterZoneConfig
        TrafficZoneConfig = module.TrafficZoneConfig
        VehicleConfig = module.VehicleConfig
        VehicleType = module.VehicleType
        get_config = module.get_config
        return module.get_config()


def _web_gui_config_payload(host: str, port: int, refresh_from_disk: bool = True) -> Dict[str, Any]:
    cfg = _reload_config_from_disk() if refresh_from_disk else get_config()
    logger.info("Web GUI config loaded: %s vehicles from config.py", len(cfg.vehicles or []))
    api_config = cfg.api
    web_endpoint = _web_gui_endpoint(api_config)
    web_url = _web_gui_public_url(api_config, host, port)
    cvrp = cfg.cvrp
    api_users = _parse_web_gui_users(getattr(api_config, "web_gui_users", ""))

    return {
        "status": "ok",
        "app_name": str(getattr(api_config, "web_gui_title", "CVRP Optimizer") or "CVRP Optimizer"),
        "web_gui": {
            "enabled": bool(getattr(api_config, "web_gui_enabled", True)),
            "endpoint": web_endpoint,
            "url": web_url,
            "users": sorted(api_users.keys()),
        },
        "api": {
            "public_url": _build_public_base_url(api_config, host, port),
            "run_endpoint": _normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run")),
            "health_endpoint": _normalise_endpoint(getattr(api_config, "health_endpoint", "/health")),
        },
        "cvrp": {
            field_name: getattr(cvrp, field_name)
            for field_name in sorted(_WEB_GUI_CVRP_FIELDS)
            if hasattr(cvrp, field_name)
        },
        "vehicles": [_vehicle_to_web_dict(vehicle) for vehicle in (cfg.vehicles or [])],
        "vehicle_types": [
            vehicle_type.value
            for vehicle_type in VehicleType
            if vehicle_type not in (VehicleType.WAREHOUSE, VehicleType.DISABLED)
        ],
        "run_status": _run_status_snapshot(),
    }


def _python_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float):
        return repr(float(value))
    return json.dumps(str(value), ensure_ascii=False)


def _tuple_literal(coords: Any) -> str:
    if not coords:
        return "None"
    try:
        return f"({float(coords[0])}, {float(coords[1])})"
    except Exception:
        return "None"


def _replace_class_field_literal(content: str, class_name: str, field_name: str, value: Any) -> str:
    class_pattern = rf'(class\s+{re.escape(class_name)}\s*:\s*.*?)(?=\n@dataclass|\nclass\s+|\Z)'

    def replace_in_class(match):
        class_block = match.group(1)
        field_pattern = rf'(?m)^(\s*{re.escape(field_name)}\s*:\s*[^=\n]+=\s*)(.*?)(\s*(?:#.*)?$)'
        return re.sub(
            field_pattern,
            lambda field_match: f"{field_match.group(1)}{_python_literal(value)}{field_match.group(3)}",
            class_block,
            count=1,
        )

    return re.sub(class_pattern, replace_in_class, content, count=1, flags=re.S)


def _optional_int_literal(value: Any) -> str:
    if value in (None, ""):
        return "None"
    return str(int(value))


def _vehicle_config_literal(vehicle: VehicleConfig) -> str:
    vehicle_type = getattr(vehicle, "vehicle_type", VehicleType.INTERNAL_BUS)
    if not isinstance(vehicle_type, VehicleType):
        vehicle_type = _parse_vehicle_type(vehicle_type)

    return "\n".join([
        "            VehicleConfig(",
        f"                vehicle_type=VehicleType.{vehicle_type.name},",
        f"                capacity={int(getattr(vehicle, 'capacity', 0) or 0)},",
        f"                count={int(getattr(vehicle, 'count', 0) or 0)},",
        f"                name={_python_literal(getattr(vehicle, 'name', ''))},",
        f"                fixed_cost={int(getattr(vehicle, 'fixed_cost', 0) or 0)},",
        f"                max_distance_km={_optional_int_literal(getattr(vehicle, 'max_distance_km', None))},",
        f"                max_time_hours={int(getattr(vehicle, 'max_time_hours', 8) or 8)},",
        f"                service_time_minutes={int(getattr(vehicle, 'service_time_minutes', 8) or 8)},",
        f"                enabled={'True' if bool(getattr(vehicle, 'enabled', True)) else 'False'},",
        f"                max_customers_per_route={_optional_int_literal(getattr(vehicle, 'max_customers_per_route', None))},",
        f"                start_location={_tuple_literal(getattr(vehicle, 'start_location', None))},",
        f"                end_location={_tuple_literal(getattr(vehicle, 'end_location', None))},",
        f"                start_time_minutes={int(getattr(vehicle, 'start_time_minutes', 480) or 480)},",
        f"                tsp_depot_location={_tuple_literal(getattr(vehicle, 'tsp_depot_location', None) or getattr(vehicle, 'start_location', None))}",
        "            ),",
    ])


def _replace_vehicles_list_literal(content: str, vehicles: list[VehicleConfig]) -> str:
    if not vehicles:
        return content
    vehicles_block = "\n".join(_vehicle_config_literal(vehicle) for vehicle in vehicles)
    pattern = r'(def _create_default_vehicles\(self\)\s*->\s*List\[VehicleConfig\]:.*?return\s*)\[.*?\n        \]'
    return re.sub(pattern, lambda match: f"{match.group(1)}[\n{vehicles_block}\n        ]", content, count=1, flags=re.S)


def _backup_config_py(config_path: str) -> str:
    backup_dir = os.path.join(os.path.dirname(config_path), "config_backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"config_web_gui_{timestamp}.py")
    shutil.copy2(config_path, backup_path)
    return backup_path


def _persist_web_gui_config(config_obj: MainConfig) -> str:
    config_path = os.path.abspath(config_module.__file__)
    with open(config_path, "r", encoding="utf-8") as file_handle:
        content = file_handle.read()

    backup_path = _backup_config_py(config_path)

    for field_name in sorted(_WEB_GUI_API_FIELDS):
        if hasattr(config_obj.api, field_name):
            content = _replace_class_field_literal(content, "APIConfig", field_name, getattr(config_obj.api, field_name))
    for field_name in sorted(_WEB_GUI_CVRP_FIELDS):
        if hasattr(config_obj.cvrp, field_name):
            content = _replace_class_field_literal(content, "CVRPConfig", field_name, getattr(config_obj.cvrp, field_name))
    content = _replace_vehicles_list_literal(content, list(config_obj.vehicles or []))

    tmp_path = f"{config_path}.webgui.tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as file_handle:
        file_handle.write(content)
    os.replace(tmp_path, config_path)
    return backup_path


def _apply_web_gui_config_save(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Web GUI save body must be a JSON object")

    config_obj = deepcopy(get_config())
    applied: list[str] = []

    app_name = payload.get("app_name")
    if app_name is not None:
        config_obj.api.web_gui_title = str(app_name or "").strip() or "CVRP Optimizer"
        applied.append("api.web_gui_title")

    api_payload = payload.get("api")
    if isinstance(api_payload, dict):
        for field_name, raw_value in api_payload.items():
            if field_name not in _WEB_GUI_API_FIELDS or not hasattr(config_obj.api, field_name):
                continue
            current_value = getattr(config_obj.api, field_name)
            setattr(config_obj.api, field_name, _coerce_setting_value("api", field_name, raw_value, current_value))
            applied.append(f"api.{field_name}")

    cvrp_payload = payload.get("cvrp")
    if isinstance(cvrp_payload, dict):
        for field_name, raw_value in cvrp_payload.items():
            if field_name not in _WEB_GUI_CVRP_FIELDS or not hasattr(config_obj.cvrp, field_name):
                continue
            if raw_value is None and field_name != "pyvrp_seed":
                continue
            current_value = getattr(config_obj.cvrp, field_name)
            setattr(config_obj.cvrp, field_name, _coerce_setting_value("cvrp", field_name, raw_value, current_value))
            applied.append(f"cvrp.{field_name}")

    if "vehicles" in payload:
        vehicle_items = payload.get("vehicles")
        if not isinstance(vehicle_items, list):
            raise ValueError("vehicles must be a JSON list")
        normalised_items = []
        existing_vehicles = list(config_obj.vehicles or [])
        for item in vehicle_items:
            item = dict(item or {})
            if not item.get("start_location") and not item.get("start_depot_name") and not item.get("depot"):
                item["start_location"] = config_obj.locations.depot_location
            normalised_items.append(item)
        vehicles: list[VehicleConfig] = []
        for index, item in enumerate(normalised_items):
            raw_original_index = item.pop("_original_index", index)
            try:
                original_index = int(raw_original_index)
            except (TypeError, ValueError):
                original_index = index
            template = existing_vehicles[original_index] if 0 <= original_index < len(existing_vehicles) else None
            vehicles.append(_vehicle_from_payload(config_obj, item, template))
        config_obj.vehicles = vehicles
        applied.append("vehicles.replace")

    backup_path = _persist_web_gui_config(config_obj)
    config_module.config_manager.config = config_obj
    return {
        "status": "saved",
        "applied": applied,
        "backup_file": backup_path,
    }


def _tail_text_file(path: str, max_chars: int = 50000, max_lines: int = 250) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as file_handle:
            file_handle.seek(0, os.SEEK_END)
            size = file_handle.tell()
            file_handle.seek(max(0, size - max_chars))
            text = file_handle.read().decode("utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > max_lines:
            text = "\n".join(lines[-max_lines:])
        return text
    except Exception as exc:
        return f"Could not read {path}: {exc}"


def _web_gui_logs_payload() -> Dict[str, Any]:
    logs_dir = _api_logs_dir()
    run_status = _run_status_snapshot()
    run_id = str(run_status.get("run_id") or "").strip()
    files = {
        "api": os.path.join(logs_dir, "cvrp_api_server.log"),
    }
    if run_id:
        files["run"] = os.path.join(logs_dir, f"api_run_{run_id}.log")
    return {
        "status": "ok",
        "run_status": run_status,
        "logs": {
            name: {
                "path": path,
                "tail": _tail_text_file(path),
            }
            for name, path in files.items()
        },
    }


def _web_gui_html(api_config, host: str, port: int) -> str:
    base_path = _web_gui_endpoint(api_config)
    data_json = json.dumps({
        "apiBase": f"{base_path}/api",
        "title": str(getattr(api_config, "web_gui_title", "CVRP Optimizer") or "CVRP Optimizer"),
    }, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="bg">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(str(getattr(api_config, "web_gui_title", "CVRP Optimizer") or "CVRP Optimizer"))}</title>
  <style>
    :root {{ --bg:#f3f5f8; --surface:#fff; --line:#d8dee8; --text:#172033; --muted:#64748b; --accent:#2563eb; --ok:#15803d; --bad:#b91c1c; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--text); }}
    header {{ background:var(--surface); border-bottom:1px solid var(--line); padding:16px 22px; display:flex; gap:16px; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:5; }}
    h1 {{ margin:0; font-size:21px; font-weight:700; }}
    h2 {{ margin:0 0 12px; font-size:16px; }}
    .muted {{ color:var(--muted); font-size:12px; }}
    main {{ padding:18px 22px 28px; display:grid; grid-template-columns: 1fr; gap:16px; }}
    section {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:16px; box-shadow:0 1px 2px rgba(15,23,42,.04); }}
    label {{ display:block; font-size:12px; color:var(--muted); margin:10px 0 4px; }}
    input, select {{ width:100%; padding:8px 10px; border:1px solid #cbd5e1; border-radius:6px; background:#fff; font:inherit; }}
    input[type="checkbox"] {{ width:auto; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
    button {{ border:1px solid #cbd5e1; background:#fff; border-radius:6px; padding:8px 12px; cursor:pointer; font-weight:600; }}
    button.primary {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
    button.danger {{ color:var(--bad); }}
    .pill {{ display:inline-block; padding:4px 8px; border-radius:999px; background:#e2e8f0; font-size:12px; }}
    .pill.ok {{ background:#dcfce7; color:var(--ok); }}
    .pill.bad {{ background:#fee2e2; color:var(--bad); }}
    .vehicles-wrap {{ border:1px solid #e5e7eb; border-radius:8px; background:#f8fafc; padding:10px; overflow-x:auto; }}
    .vehicle-list {{ display:flex; flex-direction:column; gap:10px; }}
    .vehicle-row {{ display:flex; flex-wrap:wrap; gap:10px 12px; align-items:end; background:#fff; border:1px solid #dbe3ef; border-radius:8px; padding:12px; width:max-content; max-width:100%; }}
    .vehicle-field {{ flex:0 0 auto; }}
    .vehicle-field.type {{ width:126px; }}
    .vehicle-field.name {{ width:170px; }}
    .vehicle-field.active {{ width:84px; }}
    .vehicle-field.small {{ width:78px; }}
    .vehicle-field.medium {{ width:96px; }}
    .vehicle-field.service {{ width:108px; }}
    .vehicle-field.gps {{ width:215px; }}
    .vehicle-field label {{ margin:0 0 4px; font-size:11px; font-weight:700; color:#475569; }}
    .vehicle-field input, .vehicle-field select {{ min-width:0; padding:7px 8px; }}
    .vehicle-field.checkbox {{ display:flex; gap:8px; align-items:center; min-height:58px; }}
    .vehicle-field.checkbox label {{ margin:0; }}
    .vehicle-actions {{ display:flex; align-items:end; min-height:58px; flex:0 0 82px; }}
    @media (max-width: 760px) {{ .vehicle-row {{ width:100%; }} .vehicle-field.type, .vehicle-field.name, .vehicle-field.active, .vehicle-field.small, .vehicle-field.medium, .vehicle-field.service, .vehicle-field.gps, .vehicle-actions {{ width:100%; flex-basis:100%; }} }}
    .logs-panel {{ max-width:1200px; }}
    pre {{ white-space:pre; overflow:auto; width:100%; max-width:100%; max-height:300px; padding:12px; background:#0f172a; color:#dbeafe; border-radius:6px; font:11px/1.35 Consolas, "Courier New", monospace; }}
    .full {{ grid-column:1 / -1; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1 id="pageTitle">CVRP Optimizer</h1>
      <div class="muted" id="serverLine">Зареждам...</div>
    </div>
    <div class="actions">
      <span id="statusPill" class="pill">status</span>
      <button onclick="loadAll()">Обнови</button>
      <button class="primary" onclick="saveConfig()">Запази постоянно</button>
      <button onclick="startRun()">Стартирай</button>
      <button class="danger" onclick="stopRun()">Спри run</button>
      <button class="danger" onclick="shutdownProgram()">Спри API</button>
    </div>
  </header>
  <main>
    <section>
      <h2>Превозни средства</h2>
      <div class="actions" style="margin-bottom:10px">
        <button onclick="addVehicle()">Добави бус</button>
        <span class="muted">Промените влизат в config.py само след "Запази постоянно".</span>
      </div>
      <div class="vehicles-wrap">
        <div id="vehiclesBody" class="vehicle-list"></div>
      </div>
    </section>
    <section class="full logs-panel">
      <h2>Прогрес и логове</h2>
      <div class="actions" style="margin-bottom:10px">
        <button onclick="loadLogs()">Обнови последните редове</button>
        <span class="muted" id="lastSaved"></span>
      </div>
      <div class="muted" id="runDetails" style="margin-bottom:10px"></div>
      <pre id="logsBox">Няма заредени логове.</pre>
    </section>
  </main>
  <script>
    const bootstrap = {data_json};
    const pagePath = window.location.pathname.replace(/\/$/, "");
    bootstrap.apiBase = (pagePath || "{base_path}") + "/api";
    let state = null;
    let vehicleTypes = ["internal_bus", "center_bus", "external_bus", "special_bus", "vratza_bus"];
    let logsTimer = null;

    function $(id) {{ return document.getElementById(id); }}
    function value(id) {{ return $(id).value; }}
    function intOrNull(v) {{ if (v === "" || v === null || v === undefined) return null; const n = Number(v); return Number.isFinite(n) ? Math.round(n) : null; }}
    function numberOrNull(v) {{ if (v === "" || v === null || v === undefined) return null; const n = Number(v); return Number.isFinite(n) ? n : null; }}

    async function apiFetch(path, options={{}}) {{
      const response = await fetch(bootstrap.apiBase + path, Object.assign({{cache:"no-store"}}, options));
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }}

    function setStatus(runStatus) {{
      const running = !!(runStatus && runStatus.running);
      $("statusPill").textContent = running ? "работи" : "idle";
      $("statusPill").className = "pill " + (running ? "ok" : "");
      $("runDetails").textContent = runStatus
        ? `status=${{runStatus.status || "idle"}} | run_id=${{runStatus.run_id || "-"}} | start=${{runStatus.started_at || "-"}} | finish=${{runStatus.finished_at || "-"}} | error=${{runStatus.error || "-"}}`
        : "";
      setLogsLive(running);
    }}

    function setLogsLive(active) {{
      if (active && !logsTimer) {{
        logsTimer = setInterval(() => loadLogs(false), 2000);
      }} else if (!active && logsTimer) {{
        clearInterval(logsTimer);
        logsTimer = null;
      }}
    }}

    function renderConfig(data) {{
      state = data;
      vehicleTypes = data.vehicle_types || vehicleTypes;
      $("pageTitle").textContent = data.app_name || bootstrap.title;
      $("serverLine").textContent = (data.web_gui && data.web_gui.url ? data.web_gui.url : "") + " | users: " + ((data.web_gui && data.web_gui.users || []).join(", ") || "-");
      renderVehicles((data.vehicles || []).map((v, i) => Object.assign({{_original_index:i}}, v)));
      setStatus(data.run_status || {{}});
    }}

    function renderVehicles(vehicles) {{
      const tbody = $("vehiclesBody");
      tbody.innerHTML = "";
      vehicles.forEach((vehicle, index) => tbody.appendChild(vehicleRow(vehicle, index)));
    }}

    function vehicleRow(vehicle, index) {{
      const row = document.createElement("div");
      row.className = "vehicle-row";
      row.dataset.index = index;
      row.dataset.originalIndex = vehicle._original_index ?? "";
      row.innerHTML = `
        <div class="vehicle-field type"><label>Тип</label><select data-field="vehicle_type">${{vehicleTypes.map(t => `<option value="${{t}}">${{t}}</option>`).join("")}}</select></div>
        <div class="vehicle-field name"><label>Име</label><input data-field="name"></div>
        <div class="vehicle-field checkbox active"><input data-field="enabled" type="checkbox"><label>Активен</label></div>
        <div class="vehicle-field small"><label>Брой</label><input data-field="count" type="number" min="0"></div>
        <div class="vehicle-field medium"><label>Капацитет</label><input data-field="capacity" type="number" min="0"></div>
        <div class="vehicle-field medium"><label>Цена</label><input data-field="fixed_cost" type="number"></div>
        <div class="vehicle-field medium"><label>Макс. часове</label><input data-field="max_time_hours" type="number"></div>
        <div class="vehicle-field service"><label>Обслужване</label><input data-field="service_time_minutes" type="number"></div>
        <div class="vehicle-field gps"><label>Старт GPS</label><input data-field="start_location" placeholder="lat, lon"></div>
        <div class="vehicle-field gps"><label>Край GPS</label><input data-field="end_location" placeholder="празно = депо"></div>
        <div class="vehicle-actions"><button class="danger" onclick="this.closest('.vehicle-row').remove()">Изтрий</button></div>
      `;
      for (const [key, val] of Object.entries(vehicle)) {{
        const input = row.querySelector(`[data-field="${{key}}"]`);
        if (!input) continue;
        if (input.type === "checkbox") input.checked = !!val;
        else input.value = val ?? "";
      }}
      return row;
    }}

    function addVehicle() {{
      const tbody = $("vehiclesBody");
      tbody.appendChild(vehicleRow({{
        vehicle_type:"internal_bus", name:"", enabled:true, count:1, capacity:320, fixed_cost:0,
        max_time_hours:8, service_time_minutes:8, start_location:"", end_location:"", _original_index:""
      }}, tbody.children.length));
    }}

    function collectVehicles() {{
      return Array.from($("vehiclesBody").querySelectorAll(".vehicle-row")).map(row => {{
        const get = name => row.querySelector(`[data-field="${{name}}"]`);
        return {{
          _original_index: row.dataset.originalIndex,
          vehicle_type: get("vehicle_type").value,
          name: get("name").value,
          enabled: get("enabled").checked,
          count: intOrNull(get("count").value) || 0,
          capacity: intOrNull(get("capacity").value) || 0,
          fixed_cost: intOrNull(get("fixed_cost").value) || 0,
          max_time_hours: intOrNull(get("max_time_hours").value) || 8,
          service_time_minutes: intOrNull(get("service_time_minutes").value) || 8,
          start_location: get("start_location").value,
          end_location: get("end_location").value || null,
        }};
      }});
    }}

    function collectPayload() {{
      return {{
        vehicles: collectVehicles(),
      }};
    }}

    async function loadAll() {{
      const data = await apiFetch("/config");
      renderConfig(data);
      await loadLogs(false);
    }}

    async function saveConfig() {{
      const result = await apiFetch("/config", {{
        method:"POST",
        headers:{{"Content-Type":"application/json"}},
        body:JSON.stringify(collectPayload())
      }});
      $("lastSaved").textContent = "Запазено: " + new Date().toLocaleTimeString() + " | backup: " + (result.backup_file || "");
      await loadAll();
    }}

    async function startRun() {{
      const result = await apiFetch("/run", {{method:"POST"}});
      setStatus(result.run || {{}});
      setLogsLive(true);
      await loadLogs();
    }}

    async function stopRun() {{
      if (!confirm("Да спра ли само активния run? API сървърът ще остане включен.")) return;
      const result = await apiFetch("/stop-run", {{method:"POST"}});
      $("lastSaved").textContent = result.message || "Заявено е спиране на активния run.";
      setStatus(result.run || {{}});
      await loadLogs(false);
    }}

    async function shutdownProgram() {{
      if (!confirm("Сигурен ли си, че искаш да спреш API сървъра и активния run, ако има такъв?")) return;
      const result = await apiFetch("/shutdown", {{method:"POST"}});
      $("lastSaved").textContent = result.message || "Спирането е заявено.";
      setStatus({{status:"shutting_down", running:false}});
    }}

    async function loadLogs(showErrors=true) {{
      try {{
        const data = await apiFetch("/logs");
        setStatus(data.run_status || {{}});
        const parts = [];
        for (const [name, info] of Object.entries(data.logs || {{}})) {{
          parts.push("===== " + name + " | " + info.path + " =====\\n" + (info.tail || ""));
        }}
        const logsBox = $("logsBox");
        logsBox.textContent = parts.join("\\n\\n") || "Няма логове.";
        logsBox.scrollTop = logsBox.scrollHeight;
        logsBox.scrollLeft = 0;
      }} catch (err) {{
        if (showErrors) $("logsBox").textContent = String(err);
      }}
    }}

    loadAll().catch(err => {{
      $("serverLine").textContent = String(err);
      $("logsBox").textContent = String(err);
    }});
    setInterval(() => apiFetch("/status").then(d => {{
      setStatus(d.run_status || {{}});
    }}).catch(() => null), 5000);
  </script>
</body>
</html>"""


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


def _api_health_payload(api_config, host: str, port: int) -> Dict[str, Any]:
    public_url = _build_public_base_url(api_config, host, port)
    api_endpoint = _normalise_endpoint(getattr(api_config, "api_endpoint", "/solve"))
    trigger_endpoint = _normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run"))
    health_endpoint = _normalise_endpoint(getattr(api_config, "health_endpoint", "/health"))
    tsp_endpoint = _normalise_endpoint(getattr(api_config, "tsp_endpoint", "/tsp"))
    tsp_report_endpoint = _normalise_endpoint(getattr(api_config, "tsp_report_endpoint", "/tsp-report"))
    shutdown_endpoint = _normalise_endpoint(getattr(api_config, "shutdown_endpoint", "/shutdown"))
    run_status = _run_status_snapshot()
    cvrp_running = bool(run_status.get("running"))
    endpoints = {
        "health": f"{public_url}{health_endpoint}",
        "solve": f"{public_url}{api_endpoint}",
        "run": f"{public_url}{trigger_endpoint}",
        "tsp": f"{public_url}{tsp_endpoint}",
        "tsp_report": f"{public_url}{tsp_report_endpoint}",
        "shutdown": f"{public_url}{shutdown_endpoint}",
        "web_gui": _web_gui_public_url(api_config, host, port) if _web_gui_enabled(api_config) else None,
    }

    return {
        "status": "ok",
        "service": {
            "name": "CVRP Optimizer API",
            "server_time": _now_iso(),
            "listen_url": f"http://{host}:{port}",
            "public_url": public_url,
        },
        "endpoints": endpoints,
        "capabilities": {
            "solve": {
                "method": "POST",
                "description": "Пълен CVRP run с клиенти в JSON body.",
            },
            "run": {
                "methods": ["GET", "POST"],
                "description": "Стартира оптимизация с текущите настройки/input source.",
                "single_active_run": True,
            },
            "tsp": {
                "method": "POST",
                "description": "Текущ маршрут за един шофьор от текуща GPS позиция.",
                "available_while_cvrp_running": True,
                "execution_now": "separate_process" if cvrp_running else "inline",
                "execution_when_cvrp_running": "separate_process",
                "default_service_time_minutes": getattr(api_config, "tsp_default_service_time_minutes", 8),
                "objective_metric": getattr(api_config, "tsp_objective_metric", "time"),
                "use_time_windows": bool(getattr(api_config, "tsp_use_time_windows", True)),
                "time_window_wait_weight": float(getattr(api_config, "tsp_time_window_wait_weight", 1.0) or 1.0),
                "time_window_late_weight": float(getattr(api_config, "tsp_time_window_late_weight", 20.0) or 20.0),
                "enable_two_opt": bool(getattr(api_config, "tsp_enable_two_opt", True)),
                "two_opt_max_passes": int(getattr(api_config, "tsp_two_opt_max_passes", 30) or 30),
                "response_format": _normalise_tsp_response_format(getattr(api_config, "tsp_response_format", "json")),
                "generate_local_html_map": bool(getattr(api_config, "tsp_generate_html_map", True)),
                "upload_html_map": bool(getattr(api_config, "tsp_upload_html_map", True)),
                "worker_timeout_seconds": int(getattr(api_config, "tsp_worker_timeout_seconds", 30) or 30),
                "valhalla_truck": {
                    "profiles": [
                        {
                            "name": profile.get("name", ""),
                            "driver_ids": [
                                item.strip()
                                for item in re.split(r"[,;\s]+", str(profile.get("ids", "") or ""))
                                if item.strip()
                            ],
                            "height": profile.get("height"),
                            "width": profile.get("width"),
                            "length": profile.get("length"),
                            "weight": profile.get("weight"),
                            "axle_load": profile.get("axle_load"),
                            "axle_count": profile.get("axle_count"),
                            "hazmat": bool(profile.get("hazmat", False)),
                            "hgv_no_access_penalty": profile.get("hgv_no_access_penalty"),
                        }
                        for profile in _parse_tsp_truck_profiles(getattr(api_config, "tsp_valhalla_truck_profiles", ""))
                    ],
                    "enabled_for_driver_ids": [
                        item.strip()
                        for item in re.split(r"[,;\s]+", str(getattr(api_config, "tsp_valhalla_truck_driver_ids", "") or ""))
                        if item.strip()
                    ],
                    "height": getattr(api_config, "tsp_valhalla_truck_height", 3.5),
                    "width": getattr(api_config, "tsp_valhalla_truck_width", 2.5),
                    "length": getattr(api_config, "tsp_valhalla_truck_length", 7.0),
                    "weight": getattr(api_config, "tsp_valhalla_truck_weight", 10.0),
                    "axle_load": getattr(api_config, "tsp_valhalla_truck_axle_load", 9.0),
                    "axle_count": getattr(api_config, "tsp_valhalla_truck_axle_count", 2),
                    "hazmat": bool(getattr(api_config, "tsp_valhalla_truck_hazmat", False)),
                    "hgv_no_access_penalty": getattr(api_config, "tsp_valhalla_truck_hgv_no_access_penalty", 43200),
                    "setting": "api.tsp_valhalla_truck_driver_ids",
                },
                "local_html_setting": "api.tsp_generate_html_map",
                "local_html_aliases": ["tsp_generate_map", "tsp_generate_local_html", "tsp_local_html"],
            },
            "tsp_daily_report": {
                "enabled": bool(getattr(api_config, "tsp_daily_report_enabled", False)),
                "time": getattr(api_config, "tsp_daily_report_time", "18:00"),
                "endpoint": endpoints["tsp_report"],
                "today_report_file": tsp_daily_report_path(get_config()),
                "include_details": bool(getattr(api_config, "tsp_daily_report_include_details", True)),
            },
        },
        "current_run": run_status,
        "commands": _api_commands_reference(
            public_url,
            api_endpoint,
            trigger_endpoint,
            health_endpoint,
            tsp_endpoint,
            tsp_report_endpoint,
            shutdown_endpoint,
        ),
        "settings_schema": _settings_schema_reference(get_config()),
        # Backward-compatible flat fields for older callers.
        "listen_url": f"http://{host}:{port}",
        "public_url": public_url,
        "solve_url": endpoints["solve"],
        "trigger_url": endpoints["run"],
        "tsp_url": endpoints["tsp"],
        "tsp_report_url": endpoints["tsp_report"],
        "shutdown_url": endpoints["shutdown"],
        "run_status": run_status,
    }


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
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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
                stop_requested_after_start = bool(_RUN_STATUS.get("stop_requested")) and _RUN_STATUS.get("run_id") == run_id
            _persist_run_status(status_snapshot)
            if stop_requested_after_start:
                stop_result = _terminate_process_tree(process.pid)
                logger.info("Run %s stop was requested before PID was available. Stop result: %s", run_id, stop_result)
            exit_code = process.wait()
            stdout_log.write(f"===== API run {run_id} finished {_now_iso()} exit_code={exit_code} =====\n")

        finished_at = _now_iso()
        with _RUN_LOCK:
            stop_requested = bool(_RUN_STATUS.get("stop_requested")) and _RUN_STATUS.get("run_id") == run_id
        success = exit_code == 0 and not stop_requested
        result_summary = _run_status_result_for_process(exit_code, command)
        with _RUN_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "status": "stopped" if stop_requested else ("completed" if success else "failed"),
                    "finished_at": finished_at,
                    "error": "Run stopped by user request" if stop_requested else (None if success else f"CVRP process exited with code {exit_code}"),
                    "result": result_summary if success else None,
                    "process_id": None,
                }
            )
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)

        notification = _post_completion_callback(
            callback_url,
            {
                "status": "stopped" if stop_requested else ("completed" if success else "failed"),
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
                    "process_id": None,
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


def _cvrp_run_is_active() -> bool:
    with _RUN_LOCK:
        return bool(_RUN_STATUS.get("running"))


def _tsp_worker_command(input_path: str, output_path: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--tsp-worker", input_path, output_path]

    worker_py = os.path.join(os.getcwd(), "tsp_worker.py")
    return [_child_python_executable(), worker_py, input_path, output_path]


def _run_tsp_in_subprocess(payload: Dict[str, Any], query: Optional[Dict[str, list[str]]] = None) -> Dict[str, Any]:
    api_config = get_config().api
    timeout_seconds = int(getattr(api_config, "tsp_worker_timeout_seconds", 30) or 30)
    timeout_seconds = max(1, timeout_seconds)
    tsp_run_id = uuid.uuid4().hex[:12]
    logs_dir = _api_logs_dir()
    input_path = os.path.join(logs_dir, f"api_tsp_{tsp_run_id}_input.json")
    output_path = os.path.join(logs_dir, f"api_tsp_{tsp_run_id}_result.json")
    stdout_path = os.path.join(logs_dir, f"api_tsp_{tsp_run_id}.log")
    command = _tsp_worker_command(input_path, output_path)

    with open(input_path, "w", encoding="utf-8") as fh:
        json.dump({"payload": payload, "query": query or {}}, fh, ensure_ascii=False, indent=2)

    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("_MEIPASS2", None)

    try:
        logger.info("TSP worker %s starting subprocess: %s", tsp_run_id, command)
        with open(stdout_path, "a", encoding="utf-8", errors="replace") as stdout_log:
            stdout_log.write(f"\n===== TSP worker {tsp_run_id} started {_now_iso()} =====\n")
            stdout_log.write("Command: " + " ".join(command) + "\n")
            stdout_log.flush()
            completed = subprocess.run(
                command,
                cwd=os.getcwd(),
                env=env,
                stdout=stdout_log,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                **_hidden_process_kwargs(),
            )
            stdout_log.write(
                f"===== TSP worker {tsp_run_id} finished {_now_iso()} exit_code={completed.returncode} =====\n"
            )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"TSP worker timed out after {timeout_seconds} seconds. Log: {stdout_path}") from exc

    if completed.returncode != 0:
        error = f"TSP worker exited with code {completed.returncode}. Log: {stdout_path}"
        if os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8") as fh:
                    worker_result = json.load(fh)
                if worker_result.get("error"):
                    error = f"{worker_result.get('error')} Log: {stdout_path}"
            except Exception:
                pass
        raise RuntimeError(error)

    if not os.path.exists(output_path):
        raise RuntimeError(f"TSP worker did not create result file. Log: {stdout_path}")

    with open(output_path, "r", encoding="utf-8") as fh:
        result = json.load(fh)

    result["tsp_process"] = {
        "mode": "subprocess",
        "reason": "cvrp_run_active",
        "run_id": tsp_run_id,
        "process_log": stdout_path,
        "exit_code": completed.returncode,
    }
    return result


def _execute_tsp_request(payload: Dict[str, Any], query: Optional[Dict[str, list[str]]] = None) -> Dict[str, Any]:
    if _cvrp_run_is_active():
        result = _run_tsp_in_subprocess(payload, query)
        _record_tsp_response(payload, query, result)
        return result

    config_override, applied_settings, ignored_settings = _build_request_config_override(payload, query)
    result = solve_current_tsp_route(payload, config_override or get_config())
    if applied_settings or ignored_settings:
        result["settings_overrides"] = applied_settings
        result["ignored_settings"] = ignored_settings
    result["tsp_process"] = {
        "mode": "inline",
        "reason": "cvrp_idle",
    }
    _record_tsp_response(payload, query, result, config_override)
    return result


def _record_tsp_response(
    payload: Dict[str, Any],
    query: Optional[Dict[str, list[str]]],
    result: Dict[str, Any],
    config_override: Optional[MainConfig] = None,
) -> None:
    try:
        active_config = config_override
        if active_config is None:
            active_config, _, _ = _build_request_config_override(payload, query)
        record_tsp_result(active_config or get_config(), result, payload)
    except Exception as exc:
        logger.warning("Could not record TSP history: %s", exc)


def _parse_tsp_report_time(raw: Any) -> Optional[tuple[int, int]]:
    text = str(raw or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (TypeError, ValueError):
        pass
    return None


def _today_tsp_report_due(api_config) -> bool:
    parsed = _parse_tsp_report_time(getattr(api_config, "tsp_daily_report_time", "18:00"))
    if not parsed:
        return False

    now = datetime.now()
    hour, minute = parsed
    return (now.hour, now.minute) >= (hour, minute)


def _generate_tsp_report_response(config: MainConfig, report_date: Optional[str] = None, source: str = "manual") -> Dict[str, Any]:
    report_file = generate_tsp_daily_excel_report(config, report_date)
    return {
        "status": "ok",
        "type": "tsp_daily_report",
        "source": source,
        "date": report_date or datetime.now().date().isoformat(),
        "report_file": report_file,
    }


def _tsp_report_scheduler_loop() -> None:
    global _TSP_REPORT_SCHEDULER_LAST_ATTEMPT

    while True:
        try:
            config = get_config()
            api_config = config.api
            if bool(getattr(api_config, "tsp_daily_report_enabled", False)) and _today_tsp_report_due(api_config):
                today = datetime.now().date().isoformat()
                report_path = tsp_daily_report_path(config, today)
                history_path = tsp_history_file(config)
                report_mtime = os.path.getmtime(report_path) if os.path.exists(report_path) else 0
                history_mtime = os.path.getmtime(history_path) if os.path.exists(history_path) else 0
                refresh_key = f"{today}:{history_mtime}"
                if _TSP_REPORT_SCHEDULER_LAST_ATTEMPT.get(today) != refresh_key and (
                    not os.path.exists(report_path) or history_mtime > report_mtime
                ):
                    report_file = generate_tsp_daily_excel_report(config, today)
                    _TSP_REPORT_SCHEDULER_LAST_ATTEMPT[today] = refresh_key
                    logger.info("Generated scheduled TSP daily report: %s", report_file)
        except Exception as exc:
            logger.error("Scheduled TSP daily report failed: %s", exc, exc_info=True)

        time.sleep(30)


def _start_tsp_report_scheduler() -> None:
    global _TSP_REPORT_SCHEDULER_STARTED

    with _TSP_REPORT_SCHEDULER_LOCK:
        if _TSP_REPORT_SCHEDULER_STARTED:
            return
        thread = threading.Thread(target=_tsp_report_scheduler_loop, name="TSPDailyReportScheduler", daemon=True)
        thread.start()
        _TSP_REPORT_SCHEDULER_STARTED = True


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
                "process_id": None,
                "process_log": None,
                "stop_requested": False,
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
                "process_id": None,
                "process_log": None,
                "stop_requested": False,
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


def _is_shutdown_command(query: Dict[str, list[str]], payload: Any = None) -> bool:
    return _command_from_query(query) in _SHUTDOWN_COMMANDS or _command_from_payload(payload) in _SHUTDOWN_COMMANDS


def _terminate_process_tree(pid: Any) -> Dict[str, Any]:
    try:
        process_id = int(pid or 0)
    except (TypeError, ValueError):
        process_id = 0

    if process_id <= 0:
        return {"attempted": False, "reason": "no_process_id"}
    if process_id == os.getpid():
        return {"attempted": False, "reason": "own_process"}

    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(process_id), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            return {
                "attempted": True,
                "pid": process_id,
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }

        os.kill(process_id, 15)
        return {"attempted": True, "pid": process_id, "returncode": 0}
    except Exception as exc:
        return {"attempted": True, "pid": process_id, "error": str(exc)}


def _active_run_process_id() -> Optional[int]:
    with _RUN_LOCK:
        process_id = _RUN_STATUS.get("process_id")
    try:
        return int(process_id) if process_id else None
    except (TypeError, ValueError):
        return None


def _stop_active_run() -> Dict[str, Any]:
    with _RUN_LOCK:
        if not _RUN_STATUS.get("running"):
            return {
                "status": "idle",
                "message": "No active CVRP run.",
                "run": deepcopy(_RUN_STATUS),
                "process_stop": {"attempted": False, "reason": "no_active_run"},
            }

        process_id = _RUN_STATUS.get("process_id")
        execution_mode = str(_RUN_STATUS.get("execution_mode") or "")
        _RUN_STATUS.update(
            {
                "status": "stopping",
                "stop_requested": True,
                "error": "Run stop requested",
            }
        )
        status_snapshot = deepcopy(_RUN_STATUS)
    _persist_run_status(status_snapshot)

    process_stop = _terminate_process_tree(process_id)
    if not process_stop.get("attempted") and execution_mode != "subprocess":
        message = "Stop requested, but this run is not a subprocess and cannot be force-stopped safely."
    elif not process_stop.get("attempted"):
        message = "Stop requested. The process ID is not available yet; it will be stopped when available."
    else:
        message = "Stop requested for active CVRP run. API server remains active."

    logger.info("Active CVRP run stop requested. Result: %s", process_stop)
    return {
        "status": "stopping" if status_snapshot.get("running") else "stopped",
        "message": message,
        "run": _run_status_snapshot(),
        "process_stop": process_stop,
    }


def _schedule_program_shutdown(server, delay_seconds: float = 0.35) -> None:
    def shutdown_worker() -> None:
        time.sleep(delay_seconds)
        process_stop = _terminate_process_tree(_active_run_process_id())
        if process_stop.get("attempted"):
            logger.info("Shutdown requested: active run process stop result: %s", process_stop)
        try:
            server.shutdown()
        except Exception as exc:
            logger.warning("Server shutdown failed before process exit: %s", exc)
        time.sleep(0.25)
        logger.info("Exiting CVRP API process after shutdown request")
        os._exit(0)

    threading.Thread(target=shutdown_worker, name="CVRPApiShutdown", daemon=True).start()


def _request_report_date(payload: Any = None, query: Optional[Dict[str, list[str]]] = None) -> Optional[str]:
    raw_value = None
    if isinstance(payload, dict):
        raw_value = payload.get("report_date") or payload.get("date")
    if raw_value is None and query:
        values = query.get("report_date") or query.get("date")
        if values:
            raw_value = values[-1]
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("Невалидна дата за TSP отчет. Използвай YYYY-MM-DD.") from exc


class CVRPApiHandler(BaseHTTPRequestHandler):
    server_version = "CVRPApi/1.0"

    def do_GET(self):
        api_config = get_config().api
        parsed = urlparse(self.path)
        path = _normalise_path(parsed.path)
        query = parse_qs(parsed.query)
        health_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "health_endpoint", "/health")))
        trigger_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run")))
        tsp_report_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "tsp_report_endpoint", "/tsp-report")))
        shutdown_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "shutdown_endpoint", "/shutdown")))
        if _is_web_gui_path(path, api_config):
            self._handle_web_gui_get(path, query, api_config)
            return

        if path == health_endpoint:
            host = self.server.server_address[0]
            port = self.server.server_address[1]
            self._send_json(200, _api_health_payload(api_config, host, port))
            return

        if path == shutdown_endpoint or _is_shutdown_command(query):
            if (error := _auth_error(api_config, query, self.headers)):
                self._send_json(401, {"status": "error", "error": error})
                return
            active_process_id = _active_run_process_id()
            self._send_json(200, {
                "status": "shutting_down",
                "message": "CVRP API server/program shutdown scheduled.",
                "active_run_process_id": active_process_id,
                "will_attempt_to_stop_active_run_process": bool(active_process_id),
            })
            _schedule_program_shutdown(self.server)
            return

        if path == tsp_report_endpoint:
            if (error := _auth_error(api_config, query, self.headers)):
                self._send_json(401, {"status": "error", "error": error})
                return
            try:
                report_date = _request_report_date(query=query)
                self._send_json(200, _generate_tsp_report_response(get_config(), report_date, source="api"))
            except Exception as exc:
                logger.exception("TSP report request failed")
                self._send_json(500, {"status": "error", "error": str(exc)})
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
        tsp_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "tsp_endpoint", "/tsp")))
        tsp_report_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "tsp_report_endpoint", "/tsp-report")))
        shutdown_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "shutdown_endpoint", "/shutdown")))

        if _is_web_gui_path(path, api_config):
            self._handle_web_gui_post(path, query, api_config)
            return

        try:
            payload = self._read_json_body(required=False)
            if path == shutdown_endpoint or _is_shutdown_command(query, payload):
                if (error := _auth_error(api_config, query, self.headers)):
                    self._send_json(401, {"status": "error", "error": error})
                    return
                active_process_id = _active_run_process_id()
                self._send_json(200, {
                    "status": "shutting_down",
                    "message": "CVRP API server/program shutdown scheduled.",
                    "active_run_process_id": active_process_id,
                    "will_attempt_to_stop_active_run_process": bool(active_process_id),
                })
                _schedule_program_shutdown(self.server)
                return

            if path == tsp_report_endpoint:
                if (error := _auth_error(api_config, query, self.headers)):
                    self._send_json(401, {"status": "error", "error": error})
                    return
                report_date = _request_report_date(payload, query)
                self._send_json(200, _generate_tsp_report_response(get_config(), report_date, source="api"))
                return

            if path == tsp_endpoint:
                if (error := _auth_error(api_config, query, self.headers)):
                    self._send_json(401, {"status": "error", "error": error})
                    return
                if payload is None:
                    raise ValueError("Empty request body")
                wants_html_response = _request_wants_tsp_html_response(payload, query, api_config)
                tsp_payload = dict(payload) if isinstance(payload, dict) else payload
                if wants_html_response:
                    if not isinstance(tsp_payload, dict):
                        raise ValueError("TSP заявката трябва да бъде JSON обект.")
                    tsp_payload["_return_html_response"] = True
                result = _execute_tsp_request(tsp_payload, query)
                if wants_html_response:
                    if self._send_tsp_html_response(result):
                        return
                    self._send_json(500, {
                        "status": "error",
                        "error": "TSP HTML response was requested, but no HTML map was generated.",
                        "map_file": result.get("map_file"),
                    })
                    return
                self._send_json(200, result)
                return

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
        except json.JSONDecodeError as exc:
            logger.exception("Invalid JSON request body")
            self._send_json(400, {
                "status": "error",
                "error": "Invalid JSON request body",
                "detail": str(exc),
            })
        except ValueError as exc:
            logger.exception("Bad CVRP API request")
            self._send_json(400, {"status": "error", "error": str(exc)})
        except Exception as exc:
            logger.exception("CVRP API request failed")
            self._send_json(500, {"status": "error", "error": str(exc)})

    def _handle_web_gui_get(self, path: str, query: Dict[str, list[str]], api_config) -> None:
        if (error := _web_gui_auth_error(api_config, self.headers)):
            self._send_web_auth_required(error)
            return

        subpath = _web_gui_subpath(path, api_config)
        host = self.server.server_address[0]
        port = self.server.server_address[1]

        if subpath in {"/", ""}:
            self._send_html(200, _web_gui_html(api_config, host, port))
            return
        if subpath == "/api/config":
            self._send_json(200, _web_gui_config_payload(host, port))
            return
        if subpath == "/api/status":
            self._send_json(200, {
                "status": "ok",
                "run_status": _run_status_snapshot(),
                "server_time": _now_iso(),
            })
            return
        if subpath == "/api/logs":
            self._send_json(200, _web_gui_logs_payload())
            return

        self._send_json(404, {"status": "error", "error": "Unknown web GUI endpoint"})

    def _handle_web_gui_post(self, path: str, query: Dict[str, list[str]], api_config) -> None:
        if (error := _web_gui_auth_error(api_config, self.headers)):
            self._send_web_auth_required(error)
            return

        subpath = _web_gui_subpath(path, api_config)
        if subpath == "/api/run":
            self._handle_trigger_run(query=query, payload=None, allow_inline_result=False)
            return
        if subpath == "/api/stop-run":
            self._send_json(200, _stop_active_run())
            return
        if subpath == "/api/shutdown":
            active_process_id = _active_run_process_id()
            self._send_json(200, {
                "status": "shutting_down",
                "message": "CVRP API server/program shutdown scheduled.",
                "active_run_process_id": active_process_id,
                "will_attempt_to_stop_active_run_process": bool(active_process_id),
            })
            _schedule_program_shutdown(self.server)
            return

        try:
            payload = self._read_json_body(required=True)
            if subpath == "/api/config":
                result = _apply_web_gui_config_save(payload)
                host = self.server.server_address[0]
                port = self.server.server_address[1]
                result["config"] = _web_gui_config_payload(host, port)
                self._send_json(200, result)
                return

            self._send_json(404, {"status": "error", "error": "Unknown web GUI endpoint"})
        except json.JSONDecodeError as exc:
            logger.exception("Invalid web GUI JSON request body")
            self._send_json(400, {"status": "error", "error": "Invalid JSON request body", "detail": str(exc)})
        except Exception as exc:
            logger.exception("Web GUI request failed")
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
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length <= 0:
            self._log_incoming_post_body("", 0)
            if required:
                raise ValueError("Empty request body")
            return None

        raw = self.rfile.read(length)
        charset = "utf-8"
        content_type = self.headers.get("Content-Type", "")
        if "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()

        try:
            body_text = raw.decode(charset)
        except UnicodeDecodeError:
            body_text = raw.decode(charset, errors="replace")

        self._log_incoming_post_body(body_text, length)
        if self._is_form_urlencoded_request(content_type, body_text):
            return self._read_form_json_body(body_text)

        return json.loads(body_text)

    def _is_form_urlencoded_request(self, content_type: str, body_text: str) -> bool:
        content_type = (content_type or "").lower()
        if "application/x-www-form-urlencoded" in content_type:
            return True
        stripped = body_text.lstrip()
        return any(stripped.startswith(f"{name}=") for name in _FORM_JSON_FIELD_NAMES)

    def _read_form_json_body(self, body_text: str) -> Any:
        form_data = parse_qs(
            body_text,
            keep_blank_values=True,
            strict_parsing=False,
            encoding="utf-8",
            errors="replace",
        )
        lowered_keys = {str(key).strip().lower(): key for key in form_data.keys()}

        for field_name in _FORM_JSON_FIELD_NAMES:
            key = field_name if field_name in form_data else lowered_keys.get(field_name.lower())
            if key is None:
                continue

            values = form_data.get(key) or []
            json_text = next((str(value).strip() for value in values if str(value).strip()), "")
            if not json_text:
                raise ValueError(f"Form field {key} is empty")

            logger.info(
                "Decoded form JSON field: remote=%s path=%s field=%s decoded_body=%s",
                self.client_address[0] if self.client_address else "",
                self.path,
                key,
                self._body_for_log(json_text),
            )
            return json.loads(json_text)

        logger.info(
            "Form request did not contain a JSON payload field: remote=%s path=%s fields=%s",
            self.client_address[0] if self.client_address else "",
            self.path,
            sorted(form_data.keys()),
        )
        raise ValueError("Form request body must contain JSON in pData field")

    def _safe_request_headers(self) -> Dict[str, str]:
        safe_headers: Dict[str, str] = {}
        for name, value in self.headers.items():
            if name.lower() in _SENSITIVE_HEADER_NAMES:
                safe_headers[name] = "***"
            else:
                safe_headers[name] = value
        return safe_headers

    def _body_for_log(self, body_text: str) -> str:
        if len(body_text) <= _REQUEST_BODY_LOG_LIMIT:
            return body_text
        omitted = len(body_text) - _REQUEST_BODY_LOG_LIMIT
        return f"{body_text[:_REQUEST_BODY_LOG_LIMIT]}\n...<truncated {omitted} chars>"

    def _log_incoming_post_body(self, body_text: str, content_length: int) -> None:
        logger.info(
            "Incoming POST request: remote=%s path=%s content_type=%r content_length=%s headers=%s body=%s",
            self.client_address[0] if self.client_address else "",
            self.path,
            self.headers.get("Content-Type", ""),
            content_length,
            self._safe_request_headers(),
            self._body_for_log(body_text),
        )

    def _send_json(self, status_code: int, payload: Dict[str, Any]):
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, status_code: int, html_text: str):
        raw = str(html_text or "").encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_web_auth_required(self, message: str):
        raw = json.dumps({"status": "unauthorized", "error": message}, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="CVRP Web GUI"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_tsp_html_response(self, result: Dict[str, Any]) -> bool:
        html_text = str((result or {}).get("map_html") or "")
        if html_text:
            raw = html_text.encode("utf-8")
            map_file = str((result or {}).get("map_file") or "").strip()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-CVRP-TSP-Response", "html")
            if map_file:
                self.send_header("X-CVRP-TSP-Map-File", os.path.basename(map_file))
            self.end_headers()
            self.wfile.write(raw)
            return True

        map_file = str((result or {}).get("map_file") or "").strip()
        if not map_file or not os.path.isfile(map_file):
            return False

        with open(map_file, "rb") as file_handle:
            raw = file_handle.read()

        filename = os.path.basename(map_file)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-CVRP-TSP-Response", "html")
        self.send_header("X-CVRP-TSP-Map-File", filename)
        self.end_headers()
        self.wfile.write(raw)
        return True


def run_server(host: str | None = None, port: int | None = None):
    api_config = get_config().api
    host = host or getattr(api_config, "api_host", "0.0.0.0")
    port = port or int(getattr(api_config, "api_port", 8088))

    _configure_api_logging()

    server = ThreadingHTTPServer((host, port), CVRPApiHandler)
    public_url = _build_public_base_url(api_config, host, port)
    api_endpoint = _normalise_endpoint(getattr(api_config, "api_endpoint", "/solve"))
    trigger_endpoint = _normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run"))
    tsp_endpoint = _normalise_endpoint(getattr(api_config, "tsp_endpoint", "/tsp"))
    tsp_report_endpoint = _normalise_endpoint(getattr(api_config, "tsp_report_endpoint", "/tsp-report"))
    shutdown_endpoint = _normalise_endpoint(getattr(api_config, "shutdown_endpoint", "/shutdown"))
    logger.info("CVRP API server listening on http://%s:%s", host, port)
    logger.info("Public/base URL: %s", public_url)
    logger.info("POST customer JSON to %s%s", public_url, api_endpoint)
    logger.info("Trigger configured run with GET/POST %s%s", public_url, trigger_endpoint)
    logger.info("POST current driver TSP JSON to %s%s", public_url, tsp_endpoint)
    logger.info("TSP daily report endpoint: %s%s", public_url, tsp_report_endpoint)
    logger.info("Shutdown endpoint: %s%s", public_url, shutdown_endpoint)
    _start_tsp_report_scheduler()
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
