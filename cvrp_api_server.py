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
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
import html
from http.cookies import SimpleCookie
import ipaddress
import importlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from web_gui_auth import (
    CredentialStore,
    CredentialStoreError,
    WebGUIAuthService,
    GENERIC_AUTH_FAILURE,
)

import config as config_module
from config import (
    MainConfig,
    RoutingEngine,
    CenterZoneConfig,
    TrafficZoneConfig,
    VehicleConfig,
    VehicleType,
    CVRP_SOLVER_TYPES,
    get_named_depots,
    get_config,
)
from input_handler import InputHandler, _loads_json_tolerant
from main import run_optimization
from current_tsp import _parse_tsp_truck_profiles, solve_current_tsp_route
from tsp_daily_report import (
    generate_tsp_daily_excel_report,
    record_tsp_result,
    tsp_daily_report_path,
    tsp_history_file,
)


logger = logging.getLogger(__name__)
_CURRENT_WEEK_SATURDAY_TOKEN = "current_week_saturday"
_API_RUN_DATE_OUTPUT_FIELDS = {
    "map_output_file",
    "routes_output_dir",
    "excel_output_dir",
    "warehouse_excel_file",
    "routes_excel_file",
    "efficiency_excel_file",
    "csv_output_file",
    "charts_output_dir",
}
_TRIGGER_COMMANDS = {"run", "start", "trigger", "solve_config", "start_program"}
_SHUTDOWN_COMMANDS = {"shutdown", "stop", "stop_program", "exit", "quit"}
_REQUEST_BODY_LOG_LIMIT = 100_000
_MAX_API_REQUEST_BODY_BYTES = 50 * 1024 * 1024
_MAX_WEB_REQUEST_BODY_BYTES = 1024 * 1024
_MAX_LOGIN_REQUEST_BODY_BYTES = 16 * 1024
_CLIENT_SOCKET_TIMEOUT_SECONDS = 30
_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-csrf-token",
    "x-cvrp-api-key",
    "api-key",
    "x-api-key",
}
_FORM_JSON_FIELD_NAMES = ("pData", "payload", "json", "data", "body")
_RUN_LOCK = threading.Lock()
_TSP_REPORT_SCHEDULER_LOCK = threading.Lock()
_TSP_REPORT_SCHEDULER_STARTED = False
_TSP_REPORT_SCHEDULER_LAST_ATTEMPT: Dict[str, str] = {}
_CONFIG_RELOAD_LOCK = threading.Lock()
_CONFIG_TRANSACTION_LOCK = threading.RLock()
_WEB_AUTH_LOCK = threading.Lock()
_WEB_AUTH_SERVICE: Optional[WebGUIAuthService] = None
_WEB_SESSION_COOKIE = "CVRP_WEB_SESSION"
_WEB_CSRF_COOKIE = "CVRP_WEB_CSRF"
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
    "source": None,
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
# Executable selection is installation-level configuration.  Allowing a
# request body/query parameter to replace it would turn the optimisation API
# into an arbitrary local process launcher.
_PROTECTED_REQUEST_SETTING_FIELDS = {
    ("cvrp", "pyvrp_next_worker_path"),
    ("cvrp", "vroom_worker_path"),
    ("cvrp", "vrp_worker_path"),
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
    "time_objective_include_waiting": ("cvrp", "time_objective_include_waiting"),
    "include_waiting_in_time_objective": ("cvrp", "time_objective_include_waiting"),
    "enable_multiple_trips": ("cvrp", "enable_multiple_trips"),
    "multiple_trips": ("cvrp", "enable_multiple_trips"),
    "multi_trip": ("cvrp", "enable_multiple_trips"),
    "time_limit": ("cvrp", "time_limit_seconds"),
    "time_limit_seconds": ("cvrp", "time_limit_seconds"),
    "parallel": ("cvrp", "enable_parallel_solving"),
    "enable_parallel_solving": ("cvrp", "enable_parallel_solving"),
    "workers": ("cvrp", "num_workers"),
    "num_workers": ("cvrp", "num_workers"),
    "pyvrp_seed": ("cvrp", "pyvrp_seed"),
    "pyvrp_seed_base": ("cvrp", "pyvrp_seed_base"),
    "pyvrp_num_neighbours": ("cvrp", "pyvrp_num_neighbours"),
    "pyvrp_next_worker_timeout_seconds": ("cvrp", "pyvrp_next_worker_timeout_seconds"),
    "pyvrp_next_fallback_to_stable": ("cvrp", "pyvrp_next_fallback_to_stable"),
    "vroom_worker_timeout_seconds": ("cvrp", "vroom_worker_timeout_seconds"),
    "vroom_threads": ("cvrp", "vroom_threads"),
    "vroom_exploration_level": ("cvrp", "vroom_exploration_level"),
    "vrp_worker_timeout_seconds": ("cvrp", "vrp_worker_timeout_seconds"),
    "vrp_threads": ("cvrp", "vrp_threads"),
    "vrp_max_generations": ("cvrp", "vrp_max_generations"),
    "vrp_log_progress": ("cvrp", "vrp_log_progress"),
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
    "json_service_time_field": ("input", "json_service_time_field"),
    "service_time_field": ("input", "json_service_time_field"),
    "json_mandatory_field": ("input", "json_mandatory_field"),
    "mandatory_field": ("input", "json_mandatory_field"),
    "required_field": ("input", "json_mandatory_field"),
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
    "time_objective_include_waiting": "time_objective_include_waiting",
    "include_waiting_in_time_objective": "include_waiting_in_time_objective",
    "time_limit": "time_limit",
    "time_limit_seconds": "time_limit_seconds",
    "parallel": "parallel",
    "workers": "workers",
    "num_workers": "num_workers",
    "pyvrp_seed": "pyvrp_seed",
    "pyvrp_seed_base": "pyvrp_seed_base",
    "pyvrp_next_worker_timeout_seconds": "pyvrp_next_worker_timeout_seconds",
    "pyvrp_next_fallback_to_stable": "pyvrp_next_fallback_to_stable",
    "vroom_worker_timeout_seconds": "vroom_worker_timeout_seconds",
    "vroom_threads": "vroom_threads",
    "vroom_exploration_level": "vroom_exploration_level",
    "vrp_worker_timeout_seconds": "vrp_worker_timeout_seconds",
    "vrp_threads": "vrp_threads",
    "vrp_max_generations": "vrp_max_generations",
    "vrp_log_progress": "vrp_log_progress",
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
    "json_service_time_field": "json_service_time_field",
    "service_time_field": "json_service_time_field",
    "json_mandatory_field": "json_mandatory_field",
    "mandatory_field": "json_mandatory_field",
    "required_field": "json_mandatory_field",
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
    saturday_trigger_endpoint: str,
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
                f"{public_url}{trigger_endpoint}?solver=pyvrp&objective=time&time_objective_include_waiting=true&time_limit=180",
                f"{public_url}{trigger_endpoint}?solver=vrp&objective=distance&vrp_threads=0&vrp_max_generations=1000000",
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
                    "time_objective_include_waiting": True,
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
        "run_saturday": {
            "methods": ["GET", "POST"],
            "url": f"{public_url}{saturday_trigger_endpoint}",
            "description": "Стартира run за съботата от текущата седмица със съботния префикс за бусове от GUI/config.py.",
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
                        "Mandatory": True,
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
            schema[section_name] = [
                field.name
                for field in fields(section_obj)
                if (section_name, field.name) not in _PROTECTED_REQUEST_SETTING_FIELDS
            ]

    schema["vehicles"] = [field.name for field in fields(VehicleConfig)]
    schema["solver_types"] = list(CVRP_SOLVER_TYPES)
    schema["replace_vehicles"] = "Списък със същия формат като vehicles, но подменя всички бусове."
    schema["vehicle_counts"] = "Обект {vehicle_type: count}, напр. {\"internal_bus\": 7}."
    schema["vehicle_counts_by_id"] = "Обект {config_id: count}; предпочитан при няколко реда с еднакъв vehicle_type."
    schema["remote_location_helpers"] = sorted(_SPECIAL_SETTING_KEYS)
    schema["aliases"] = sorted(_TOP_LEVEL_SETTING_ALIASES.keys())
    schema["notes"] = [
        "Всички settings са временни за конкретната заявка и не променят config.py.",
        "JSON може да използва вложени секции или точкова нотация, напр. output.excel_output_dir.",
        "vehicles поддържа end_location или end_depot_name. Ако липсва, маршрутът завършва в стартовото депо.",
        "pyvrp_next_worker_path, vroom_worker_path и vrp_worker_path са защитени инсталационни настройки и не могат да се override-ват през API заявка.",
        "VROOM 1.15 изисква enable_multiple_trips=false, защото няма linked reload/multi-trip модел.",
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


def _parse_solver_type(value: Any) -> str:
    solver_type = str(value or "").strip().lower()
    if solver_type not in CVRP_SOLVER_TYPES:
        raise ValueError(
            f"Невалиден solver_type: {value!r}. "
            f"Разрешени стойности: {', '.join(CVRP_SOLVER_TYPES)}"
        )
    return solver_type


def _coerce_setting_value(section_name: str, field_name: str, value: Any, current_value: Any) -> Any:
    if value == "" and field_name in {
        "pyvrp_seed",
        "max_distance_km",
        "max_customers_per_route",
        "max_customers_per_day",
        "start_location",
        "end_location",
        "tsp_depot_location",
        "reload_location",
        "sheet_name",
    }:
        return None

    if section_name == "routing" and field_name == "engine":
        return _parse_routing_engine(value)
    if section_name == "cvrp" and field_name == "solver_type":
        return _parse_solver_type(value)
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
    if field_name in {"start_location", "end_location", "tsp_depot_location", "reload_location", "depot_location", "center_location", "vratza_depot_location", "city_center_coords", "center_coords"}:
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
        "max_customers_per_day",
        "end_location",
        "start_location",
        "tsp_depot_location",
        "reload_location",
    }:
        if field_name in {"start_location", "end_location", "tsp_depot_location", "reload_location"}:
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
        show_on_map=_parse_bool(
            payload.get("show_on_map", payload.get("visible_on_map", False))
        ),
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
        show_on_map=_parse_bool(
            payload.get("show_on_map", payload.get("visible_on_map", True))
        ),
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


def _resolve_web_depot_name(config: MainConfig, value: Any) -> tuple[float, float]:
    """Resolve only a canonical configured depot name, never arbitrary GPS text."""
    requested = str(value or "").strip().casefold()
    for name, coords in get_named_depots(config.locations).items():
        if str(name).strip().casefold() == requested:
            return _parse_coords(coords)
    available = ", ".join(str(name) for name in get_named_depots(config.locations))
    raise ValueError(
        f"Непознато начално депо {value!r}. Избери едно от: {available or '-'}"
    )


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
        if (section_name, field_name) in _PROTECTED_REQUEST_SETTING_FIELDS:
            ignored.append(f"{section_name}.{field_name}: protected installation setting")
            continue
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
    for alias in ("reload_depot", "reload_depot_name"):
        if alias in payload and payload.get(alias):
            payload["reload_location"] = _resolve_depot_reference(config, payload.get(alias))

    vehicle_type = _parse_vehicle_type(payload.get("vehicle_type", getattr(template, "vehicle_type", None)))
    vehicle = deepcopy(template) if template is not None else VehicleConfig(vehicle_type=vehicle_type, capacity=320, count=1)
    vehicle.vehicle_type = vehicle_type

    allowed_fields = {field.name for field in fields(VehicleConfig)}
    for field_name, value in payload.items():
        if field_name not in allowed_fields:
            continue
        if field_name in {"start_location", "end_location", "tsp_depot_location", "reload_location"} and isinstance(value, str):
            value = _resolve_depot_reference(config, value)
        current_value = getattr(vehicle, field_name)
        setattr(vehicle, field_name, _coerce_setting_value("vehicles", field_name, value, current_value))

    return vehicle


def _validate_vehicle_configs(vehicles: list[VehicleConfig]) -> None:
    seen_config_ids: set[str] = set()
    for index, vehicle in enumerate(vehicles):
        label = str(getattr(vehicle, "name", "") or f"vehicle {index + 1}")
        config_id = str(getattr(vehicle, "config_id", "") or "").strip()
        if not config_id:
            vehicle_type = getattr(getattr(vehicle, "vehicle_type", None), "value", "vehicle")
            base_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(vehicle_type or "vehicle")).strip("_") or "vehicle"
            config_id = f"{base_id}_{index + 1}"
            suffix = 2
            while config_id in seen_config_ids:
                config_id = f"{base_id}_{index + 1}_{suffix}"
                suffix += 1
            vehicle.config_id = config_id
        if config_id in seen_config_ids:
            raise ValueError(f"Дублирано vehicle config_id: {config_id}")
        seen_config_ids.add(config_id)
        if int(getattr(vehicle, "count", 0) or 0) < 0:
            raise ValueError(f"{label}: count не може да е отрицателно")
        if float(getattr(vehicle, "capacity", 0) or 0) <= 0:
            raise ValueError(f"{label}: capacity трябва да е положително")
        if float(getattr(vehicle, "max_time_hours", 0) or 0) <= 0:
            raise ValueError(f"{label}: max_time_hours трябва да е положително")
        if int(getattr(vehicle, "reload_time_minutes", 0) or 0) < 0:
            raise ValueError(f"{label}: reload_time_minutes не може да е отрицателно")
        daily_limit = getattr(vehicle, "max_customers_per_day", None)
        if daily_limit is not None and int(daily_limit) < 1:
            raise ValueError(f"{label}: max_customers_per_day трябва да е положително")


def _patch_vehicles(config: MainConfig, vehicle_items: Any, replace: bool = False) -> list[str]:
    if not isinstance(vehicle_items, list):
        raise ValueError("vehicles/replace_vehicles трябва да бъде JSON списък")

    existing = [deepcopy(vehicle) for vehicle in (config.vehicles or [])]

    if replace:
        config.vehicles = [_vehicle_from_payload(config, item) for item in vehicle_items]
        _validate_vehicle_configs(config.vehicles)
        return ["vehicles.replace"]

    applied: list[str] = []

    for item in vehicle_items:
        if not isinstance(item, dict):
            raise ValueError("vehicles трябва да съдържа JSON обекти")
        requested_id = str(item.get("config_id", "") or "").strip()
        matched_indices = [
            index
            for index, vehicle in enumerate(existing)
            if requested_id and str(getattr(vehicle, "config_id", "") or "").strip() == requested_id
        ]
        if not matched_indices and not requested_id:
            vehicle_type = _parse_vehicle_type(item.get("vehicle_type"))
            matched_indices = [
                index for index, vehicle in enumerate(existing)
                if getattr(vehicle.vehicle_type, "value", str(vehicle.vehicle_type)) == vehicle_type.value
            ]

        if matched_indices:
            for index in matched_indices:
                existing[index] = _vehicle_from_payload(config, item, existing[index])
                identity = str(getattr(existing[index], "config_id", "") or requested_id or index)
                applied.append(f"vehicles.{identity}")
        else:
            vehicle = _vehicle_from_payload(config, item)
            existing.append(vehicle)
            identity = str(getattr(vehicle, "config_id", "") or len(existing) - 1)
            applied.append(f"vehicles.{identity}")

    config.vehicles = existing
    _validate_vehicle_configs(config.vehicles)
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
    return applied


def _apply_vehicle_counts_by_id(config: MainConfig, counts: Any) -> list[str]:
    if not isinstance(counts, dict):
        raise ValueError("vehicle_counts_by_id трябва да бъде JSON обект")
    vehicles = list(config.vehicles or [])
    applied: list[str] = []
    for raw_id, raw_count in counts.items():
        config_id = str(raw_id or "").strip()
        matched = False
        for vehicle in vehicles:
            if str(getattr(vehicle, "config_id", "") or "").strip() == config_id:
                vehicle.count = int(raw_count)
                applied.append(f"vehicles.{config_id}.count")
                matched = True
                break
        if not matched:
            raise ValueError(f"Няма VehicleConfig с config_id={config_id!r}")
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
        if key in _ALLOWED_SETTING_SECTIONS or key in _SPECIAL_SETTING_KEYS or key in {"vehicles", "replace_vehicles", "vehicle_counts", "vehicle_counts_by_id"}:
            settings[key] = deepcopy(value)
        elif key in _TOP_LEVEL_SETTING_ALIASES:
            settings[key] = deepcopy(value)
    return settings


def _resolve_dynamic_api_date(value: Any, today: Optional[date] = None) -> str:
    """Resolve API-only symbolic dates to the configured DD/MM/YYYY format."""
    text = str(value or "").strip()
    if text.lower() != _CURRENT_WEEK_SATURDAY_TOKEN:
        return text

    current_date = today or datetime.now().date()
    monday = current_date - timedelta(days=current_date.weekday())
    saturday = monday + timedelta(days=5)
    return saturday.strftime("%d/%m/%Y")


def _expand_api_run_date_output_tokens(config: MainConfig, applied: list[str]) -> None:
    """Expand date placeholders only in output settings supplied for this run."""
    try:
        run_date = datetime.strptime(
            str(getattr(config.input, "json_override_date", "") or "").strip(),
            "%d/%m/%Y",
        ).date()
    except ValueError:
        return

    replacements = {
        "{run_date}": run_date.isoformat(),
        "{run_date_compact}": run_date.strftime("%Y%m%d"),
    }
    applied_fields = {
        item.split(".", 1)[1]
        for item in applied
        if item.startswith("output.") and "." in item
    }
    for field_name in _API_RUN_DATE_OUTPUT_FIELDS & applied_fields:
        value = getattr(config.output, field_name, None)
        if not isinstance(value, str):
            continue
        expanded = value
        for token, replacement in replacements.items():
            expanded = expanded.replace(token, replacement)
        setattr(config.output, field_name, expanded)


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
            elif key == "vehicle_counts_by_id":
                applied.extend(_apply_vehicle_counts_by_id(config, value))
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

    # Solver selection is operationally significant: reject typos instead of
    # letting the runtime fall through to a different backend.
    cvrp_settings = settings.get("cvrp")
    if isinstance(cvrp_settings, dict) and "solver_type" in cvrp_settings:
        _parse_solver_type(cvrp_settings.get("solver_type"))
    for alias in ("solver", "solver_type"):
        if alias in settings:
            _parse_solver_type(settings.get(alias))

    request_config = deepcopy(get_config())
    applied, ignored = _apply_settings_override(request_config, settings)
    if "input.json_override_date" in applied:
        raw_date = getattr(request_config.input, "json_override_date", "")
        resolved_date = _resolve_dynamic_api_date(raw_date)
        is_current_week_saturday = (
            str(raw_date or "").strip().lower() == _CURRENT_WEEK_SATURDAY_TOKEN
        )
        if resolved_date != str(raw_date or "").strip():
            request_config.input.json_override_date = resolved_date
            logger.info(
                "Resolved API date token %r to current-week Saturday %s",
                raw_date,
                resolved_date,
            )
        if is_current_week_saturday:
            try:
                saturday_stamp = datetime.strptime(resolved_date, "%d/%m/%Y").strftime("%Y-%m-%d")
                setattr(request_config.output, "_api_run_date_stamp", f"{saturday_stamp}_събота")
            except ValueError:
                pass
    _expand_api_run_date_output_tokens(request_config, applied)
    return request_config if applied else None, applied, ignored


def _build_saturday_run_config_override(
    payload: Any = None,
    query: Optional[Dict[str, list[str]]] = None,
) -> tuple[MainConfig, list[str], list[str]]:
    """Build request-local settings for the dedicated Saturday run endpoint."""
    request_config, applied, ignored = _build_request_config_override(payload, query)
    if request_config is None:
        request_config = deepcopy(get_config())

    saturday_date = _resolve_dynamic_api_date(_CURRENT_WEEK_SATURDAY_TOKEN)
    saturday_stamp = datetime.strptime(saturday_date, "%d/%m/%Y").strftime("%Y-%m-%d")
    saturday_prefix = str(
        getattr(request_config.output, "saturday_excel_bus_number_prefix", "")
        or getattr(request_config.output, "excel_bus_number_prefix", "")
    ).strip()
    if not saturday_prefix:
        raise ValueError("Префиксът за събота не може да бъде празен.")

    try:
        saturday_digits = int(
            getattr(request_config.output, "saturday_excel_bus_number_digits", 1)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Цифрите за съботния префикс трябва да са цяло число.") from exc
    if saturday_digits < 1:
        raise ValueError("Цифрите за съботния префикс трябва да са поне 1.")

    request_config.input.json_override_date = saturday_date
    request_config.output.excel_bus_number_prefix = saturday_prefix
    request_config.output.excel_bus_number_digits = saturday_digits
    setattr(request_config.output, "_api_run_date_stamp", f"{saturday_stamp}_събота")

    for setting_name in (
        "input.json_override_date",
        "output.excel_bus_number_prefix",
        "output.excel_bus_number_digits",
    ):
        if setting_name not in applied:
            applied.append(setting_name)

    logger.info(
        "Prepared Saturday run: date=%s bus_prefix=%s digits=%s",
        saturday_date,
        saturday_prefix,
        saturday_digits,
    )
    return request_config, applied, ignored


_WEB_GUI_CVRP_FIELDS = {
    "solver_type",
    "objective_metric",
    "time_objective_include_waiting",
    "enable_multiple_trips",
    "time_limit_seconds",
    "allow_customer_skipping",
    "enable_parallel_solving",
    "num_workers",
    "parallel_first_solution_strategies",
    "parallel_local_search_metaheuristics",
    "first_solution_strategy",
    "local_search_metaheuristic",
    "lns_time_limit_seconds",
    "lns_num_nodes",
    "lns_num_arcs",
    "use_full_propagation",
    "search_lambda_coefficient",
    "log_search",
    "enable_start_time_tracking",
    "global_start_time_minutes",
    "enable_customer_time_windows",
    "pyvrp_seed",
    "pyvrp_seed_base",
    "pyvrp_num_neighbours",
    "pyvrp_weight_wait_time",
    "pyvrp_symmetric_proximity",
    "pyvrp_ils_no_improvement",
    "pyvrp_ils_history_length",
    "pyvrp_exhaustive_on_best",
    "pyvrp_use_extended_operators",
    "pyvrp_min_perturbations",
    "pyvrp_max_perturbations",
    "pyvrp_display_progress",
    "pyvrp_display_interval_seconds",
    "pyvrp_use_library_penalty_defaults",
    "pyvrp_penalty_solutions_between_updates",
    "pyvrp_penalty_increase",
    "pyvrp_penalty_decrease",
    "pyvrp_penalty_target_feasible",
    "pyvrp_penalty_feas_tolerance",
    "pyvrp_penalty_min",
    "pyvrp_penalty_max",
    "pyvrp_next_worker_timeout_seconds",
    "pyvrp_next_fallback_to_stable",
    "vroom_worker_timeout_seconds",
    "vroom_threads",
    "vroom_exploration_level",
    "vrp_worker_timeout_seconds",
    "vrp_threads",
    "vrp_max_generations",
    "vrp_log_progress",
}
_WEB_GUI_API_FIELDS = {
    "web_gui_enabled",
    "web_gui_endpoint",
    "web_gui_title",
    "web_gui_public_host",
    "web_gui_public_url",
}
_WEB_GUI_PERSIST_API_FIELDS = _WEB_GUI_API_FIELDS | {"web_gui_run_defaults_json"}

# The browser may override only operational output and setData settings.  This
# deliberately excludes solver, input, routing, vehicles and API settings.
_WEB_RUN_OUTPUT_FIELDS = {
    "enable_interactive_map",
    "map_output_file",
    "routes_output_dir",
    "route_maps_upload_mode",
    "route_maps_upload_url",
    "route_maps_upload_token",
    "route_maps_upload_token_field",
    "route_maps_upload_file_field",
    "route_maps_upload_bus_id_field",
    "route_maps_upload_timeout_seconds",
    "map_provider",
    "folium_tiles",
    "enable_excel_output",
    "excel_output_dir",
    "warehouse_excel_file",
    "routes_excel_file",
    "efficiency_excel_file",
    "excel_bus_number_prefix",
    "excel_bus_number_digits",
    "saturday_excel_bus_number_prefix",
    "saturday_excel_bus_number_digits",
    "center_bus_numbering_enabled",
    "center_bus_numbering_start_id",
    "enable_csv_output",
    "csv_output_file",
    "enable_charts",
    "charts_output_dir",
}
_WEB_RUN_SET_DATA_FIELDS = {
    "enable_set_data_upload",
    "set_data_url",
    "set_data_http_method",
    "set_data_command",
    "set_data_done_flag",
    "set_data_id_skld",
    "set_data_vratza_id_skld",
    "set_data_depot_id_skld_map",
    "set_data_id_grafik",
    "set_data_id_grafik_template",
    "set_data_bukva_template",
    "enable_unserved_set_data_upload",
    "set_data_unserved_done_flag",
    "set_data_unserved_id_grafik",
    "set_data_unserved_id_grafik_template",
    "set_data_unserved_bukva_template",
    "enable_make_group",
    "set_data_make_group_command",
    "set_data_timeout_seconds",
}
_WEB_RUN_CVRP_FIELDS = {
    "solver_type",
    "objective_metric",
    "time_objective_include_waiting",
    "enable_multiple_trips",
    "time_limit_seconds",
    "enable_parallel_solving",
    "num_workers",
    "parallel_first_solution_strategies",
    "parallel_local_search_metaheuristics",
    "first_solution_strategy",
    "local_search_metaheuristic",
    "lns_time_limit_seconds",
    "lns_num_nodes",
    "lns_num_arcs",
    "use_full_propagation",
    "search_lambda_coefficient",
    "log_search",
    "enable_start_time_tracking",
    "global_start_time_minutes",
    "pyvrp_seed",
    "pyvrp_seed_base",
    "pyvrp_num_neighbours",
    "pyvrp_weight_wait_time",
    "pyvrp_symmetric_proximity",
    "pyvrp_ils_no_improvement",
    "pyvrp_ils_history_length",
    "pyvrp_exhaustive_on_best",
    "pyvrp_use_extended_operators",
    "pyvrp_min_perturbations",
    "pyvrp_max_perturbations",
    "pyvrp_display_progress",
    "pyvrp_display_interval_seconds",
    "pyvrp_use_library_penalty_defaults",
    "pyvrp_penalty_solutions_between_updates",
    "pyvrp_penalty_increase",
    "pyvrp_penalty_decrease",
    "pyvrp_penalty_target_feasible",
    "pyvrp_penalty_feas_tolerance",
    "pyvrp_penalty_min",
    "pyvrp_penalty_max",
    "pyvrp_next_worker_timeout_seconds",
    "pyvrp_next_fallback_to_stable",
    "vroom_worker_timeout_seconds",
    "vroom_threads",
    "vroom_exploration_level",
    "vrp_worker_timeout_seconds",
    "vrp_threads",
    "vrp_max_generations",
    "vrp_log_progress",
}
_WEB_RUN_SECTION_FIELDS = {
    "cvrp": _WEB_RUN_CVRP_FIELDS,
    "output": _WEB_RUN_OUTPUT_FIELDS,
    "set_data": _WEB_RUN_SET_DATA_FIELDS,
}


def _web_gui_endpoint(api_config) -> str:
    configured = _normalise_path(_normalise_endpoint(getattr(api_config, "web_gui_endpoint", "/hell")))

    def is_safe(candidate: str) -> bool:
        if candidate == "/" or not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+", candidate):
            return False
        for field_name in (
            "api_endpoint",
            "trigger_endpoint",
            "tsp_endpoint",
            "tsp_report_endpoint",
            "shutdown_endpoint",
            "health_endpoint",
        ):
            reserved = _normalise_path(_normalise_endpoint(getattr(api_config, field_name, "")))
            if reserved == candidate or reserved.startswith(f"{candidate}/"):
                return False
        return True

    if is_safe(configured):
        return configured
    for fallback in ("/hell", "/web-gui", "/cvrp-ui"):
        if is_safe(fallback):
            return fallback
    return "/cvrp-web-interface"


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


def _web_auth_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(config_module.__file__))


def _get_web_auth_service(api_config) -> WebGUIAuthService:
    global _WEB_AUTH_SERVICE
    with _WEB_AUTH_LOCK:
        if _WEB_AUTH_SERVICE is None:
            store = CredentialStore(
                _web_auth_base_dir(),
                legacy_users=str(getattr(api_config, "web_gui_users", "") or ""),
            )
            _WEB_AUTH_SERVICE = WebGUIAuthService(store)
        return _WEB_AUTH_SERVICE


def _request_cookie(headers, name: str) -> str:
    raw_cookie = str(headers.get("Cookie", "") or "")
    if not raw_cookie:
        return ""
    try:
        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        morsel = cookie.get(name)
        return str(morsel.value if morsel else "")
    except Exception:
        return ""


def _web_session_identity(api_config, headers, require_csrf: bool = False):
    try:
        service = _get_web_auth_service(api_config)
        session_token = _request_cookie(headers, _WEB_SESSION_COOKIE)
        csrf_token = str(headers.get("X-CSRF-Token", "") or "")
        return service.authenticate_session(
            session_token,
            csrf_token=csrf_token,
            require_csrf=require_csrf,
        )
    except CredentialStoreError:
        logger.error("Web credential storage is unavailable", exc_info=True)
        return None


def _web_request_origin_is_valid(headers) -> bool:
    origin = str(headers.get("Origin", "") or "").strip()
    if not origin:
        return True
    parsed = urlparse(origin)
    request_host = str(headers.get("Host", "") or "").strip().lower()
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == request_host


def _parse_forwarded_ip(raw_value: Any) -> Optional[str]:
    value = str(raw_value or "").strip().strip('"')
    if not value or value.lower() == "unknown" or value.startswith("_"):
        return None
    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    else:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            if value.count(":") == 1 and "." in value:
                value = value.rsplit(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _web_login_client_ip(api_config, headers, client_address: Any) -> str:
    """Resolve a login client through explicitly trusted reverse proxies only."""
    remote_ip = _parse_forwarded_ip(client_address[0] if client_address else "") or ""
    trusted_networks = []
    for item in re.split(r"[,;\s]+", str(getattr(api_config, "web_gui_trusted_proxy_ips", "") or "")):
        if not item:
            continue
        try:
            trusted_networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid web_gui_trusted_proxy_ips entry: %r", item)

    def is_trusted(value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return any(address in network for network in trusted_networks)

    if not remote_ip or not is_trusted(remote_ip):
        return remote_ip

    forwarded_chain: list[str] = []
    forwarded = str(headers.get("Forwarded", "") or "")
    for element in forwarded.split(","):
        for parameter in element.split(";"):
            name, separator, raw_value = parameter.strip().partition("=")
            if separator and name.strip().lower() == "for":
                parsed_ip = _parse_forwarded_ip(raw_value)
                if parsed_ip:
                    forwarded_chain.append(parsed_ip)
                break
    if not forwarded_chain:
        forwarded_chain = [
            parsed_ip
            for parsed_ip in (
                _parse_forwarded_ip(item)
                for item in str(headers.get("X-Forwarded-For", "") or "").split(",")
            )
            if parsed_ip
        ]
    if not forwarded_chain:
        return remote_ip

    for candidate in reversed(forwarded_chain + [remote_ip]):
        if not is_trusted(candidate):
            return candidate
    return forwarded_chain[0]


def _web_cookie_is_secure(api_config, headers) -> bool:
    forwarded_proto = str(headers.get("X-Forwarded-Proto", "") or "").split(",", 1)[0].strip().lower()
    if forwarded_proto == "https":
        return True
    public_url = str(getattr(api_config, "web_gui_public_url", "") or "").strip().lower()
    return public_url.startswith("https://")


def _web_session_cookie_headers(api_config, headers, session=None, clear: bool = False) -> list[tuple[str, str]]:
    path = _web_gui_endpoint(api_config) or "/"
    secure = "; Secure" if _web_cookie_is_secure(api_config, headers) else ""
    if clear:
        return [
            ("Set-Cookie", f"{_WEB_SESSION_COOKIE}=; Path={path}; Max-Age=0; HttpOnly; SameSite=Strict{secure}"),
            ("Set-Cookie", f"{_WEB_CSRF_COOKIE}=; Path={path}; Max-Age=0; SameSite=Strict{secure}"),
        ]
    max_age = max(1, int(float(session.expires_at) - time.time()))
    return [
        ("Set-Cookie", f"{_WEB_SESSION_COOKIE}={session.token}; Path={path}; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}"),
        ("Set-Cookie", f"{_WEB_CSRF_COOKIE}={session.csrf_token}; Path={path}; Max-Age={max_age}; SameSite=Strict{secure}"),
    ]


def _validate_web_run_settings_shape(raw_settings: Any) -> Dict[str, Dict[str, Any]]:
    if raw_settings in (None, ""):
        return {}
    if not isinstance(raw_settings, dict):
        raise ValueError("run_settings must be a JSON object")

    unknown_sections = sorted(set(raw_settings) - set(_WEB_RUN_SECTION_FIELDS))
    if unknown_sections:
        raise ValueError(
            "Web run settings support only cvrp, output and set_data; disallowed sections: "
            + ", ".join(unknown_sections)
        )

    normalised: Dict[str, Dict[str, Any]] = {}
    for section_name, allowed_fields in _WEB_RUN_SECTION_FIELDS.items():
        section_payload = raw_settings.get(section_name)
        if section_payload is None:
            continue
        if not isinstance(section_payload, dict):
            raise ValueError(f"run_settings.{section_name} must be a JSON object")
        unknown_fields = sorted(set(section_payload) - allowed_fields)
        if unknown_fields:
            raise ValueError(
                f"Unsupported Web run fields in {section_name}: " + ", ".join(unknown_fields)
            )
        normalised[section_name] = deepcopy(section_payload)
    return normalised


def _extract_web_run_settings(payload: Any) -> Dict[str, Dict[str, Any]]:
    if payload in (None, ""):
        return {}
    if not isinstance(payload, dict):
        raise ValueError("Web run request body must be a JSON object")
    return _validate_web_run_settings_shape(payload.get("run_settings", {}))


def _stored_web_run_settings(api_config, strict: bool = False) -> Dict[str, Dict[str, Any]]:
    raw = str(getattr(api_config, "web_gui_run_defaults_json", "") or "").strip()
    if not raw:
        return {}
    try:
        return _validate_web_run_settings_shape(json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if strict:
            raise ValueError(f"Invalid saved Web run defaults: {exc}") from exc
        logger.error("Ignoring invalid saved Web run defaults: %s", exc)
        return {}


def _validate_web_run_config(config_obj: MainConfig) -> None:
    cvrp = config_obj.cvrp
    output = config_obj.output
    set_data = config_obj.set_data

    cvrp.solver_type = _parse_solver_type(getattr(cvrp, "solver_type", "pyvrp"))
    objective_metric = str(getattr(cvrp, "objective_metric", "distance") or "distance").strip().lower()
    if objective_metric not in {"time", "distance"}:
        raise ValueError("objective_metric must be time or distance")
    cvrp.objective_metric = objective_metric
    time_limit = int(getattr(cvrp, "time_limit_seconds", 0) or 0)
    if not 1 <= time_limit <= 86400:
        raise ValueError("time_limit_seconds must be between 1 and 86400")
    num_workers = int(getattr(cvrp, "num_workers", -1))
    if num_workers != -1 and not 1 <= num_workers <= 128:
        raise ValueError("num_workers must be -1 or between 1 and 128")
    for field_name in (
        "parallel_first_solution_strategies",
        "parallel_local_search_metaheuristics",
    ):
        values = getattr(cvrp, field_name, None)
        if not isinstance(values, list) or not values:
            raise ValueError(f"{field_name} must contain at least one strategy")
        if any(
            not isinstance(value, str)
            or not value.strip()
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", value.strip())
            for value in values
        ):
            raise ValueError(
                f"{field_name} accepts only non-empty OR-Tools strategy names"
            )

    pyvrp_num_neighbours = int(getattr(cvrp, "pyvrp_num_neighbours", 50))
    if not 1 <= pyvrp_num_neighbours <= 10000:
        raise ValueError("pyvrp_num_neighbours must be between 1 and 10000")
    pyvrp_weight_wait_time = float(getattr(cvrp, "pyvrp_weight_wait_time", 0.2))
    if not 0 <= pyvrp_weight_wait_time <= 1000:
        raise ValueError("pyvrp_weight_wait_time must be between 0 and 1000")
    pyvrp_ils_no_improvement = int(getattr(cvrp, "pyvrp_ils_no_improvement", 150000))
    if not 0 <= pyvrp_ils_no_improvement <= 1_000_000_000:
        raise ValueError("pyvrp_ils_no_improvement must be between 0 and 1000000000")
    pyvrp_ils_history_length = int(getattr(cvrp, "pyvrp_ils_history_length", 300))
    if not 1 <= pyvrp_ils_history_length <= 10_000_000:
        raise ValueError("pyvrp_ils_history_length must be between 1 and 10000000")
    min_perturbations = int(getattr(cvrp, "pyvrp_min_perturbations", 1))
    max_perturbations = int(getattr(cvrp, "pyvrp_max_perturbations", 25))
    if not 1 <= min_perturbations <= 1_000_000:
        raise ValueError("pyvrp_min_perturbations must be between 1 and 1000000")
    if not min_perturbations <= max_perturbations <= 1_000_000:
        raise ValueError("pyvrp_max_perturbations must be at least the minimum and at most 1000000")
    display_interval = float(getattr(cvrp, "pyvrp_display_interval_seconds", 5.0))
    if not 0.1 <= display_interval <= 3600:
        raise ValueError("pyvrp_display_interval_seconds must be between 0.1 and 3600")
    penalty_update_interval = int(getattr(cvrp, "pyvrp_penalty_solutions_between_updates", 500))
    if not 1 <= penalty_update_interval <= 1_000_000_000:
        raise ValueError("pyvrp_penalty_solutions_between_updates must be between 1 and 1000000000")
    for field_name, minimum, maximum in (
        ("pyvrp_penalty_increase", 1.0, 1000.0),
        ("pyvrp_penalty_decrease", 0.0, 1.0),
        ("pyvrp_penalty_target_feasible", 0.0, 1.0),
        ("pyvrp_penalty_feas_tolerance", 0.0, 1.0),
    ):
        value = float(getattr(cvrp, field_name))
        if not minimum <= value <= maximum:
            raise ValueError(f"{field_name} must be between {minimum:g} and {maximum:g}")
    penalty_min = float(getattr(cvrp, "pyvrp_penalty_min", 0.1))
    penalty_max = float(getattr(cvrp, "pyvrp_penalty_max", 100000.0))
    if not 0 <= penalty_min <= penalty_max <= 1_000_000_000:
        raise ValueError("PyVRP penalty min/max must satisfy 0 <= min <= max <= 1000000000")
    pyvrp_next_timeout = int(getattr(cvrp, "pyvrp_next_worker_timeout_seconds", 0) or 0)
    if not 0 <= pyvrp_next_timeout <= 86400:
        raise ValueError("pyvrp_next_worker_timeout_seconds must be between 0 and 86400")

    first_solution = str(getattr(cvrp, "first_solution_strategy", "AUTOMATIC") or "AUTOMATIC")
    if first_solution not in {
        "AUTOMATIC", "PATH_CHEAPEST_ARC", "SAVINGS", "SWEEP", "CHRISTOFIDES",
        "PARALLEL_CHEAPEST_INSERTION",
    }:
        raise ValueError("Unsupported OR-Tools first_solution_strategy")
    metaheuristic = str(getattr(cvrp, "local_search_metaheuristic", "AUTOMATIC") or "AUTOMATIC")
    if metaheuristic not in {"AUTOMATIC", "GUIDED_LOCAL_SEARCH", "SIMULATED_ANNEALING", "TABU_SEARCH"}:
        raise ValueError("Unsupported OR-Tools local_search_metaheuristic")
    lns_time_limit = float(getattr(cvrp, "lns_time_limit_seconds", 1.5))
    if not 0 <= lns_time_limit <= 86400:
        raise ValueError("lns_time_limit_seconds must be between 0 and 86400")
    for field_name in ("lns_num_nodes", "lns_num_arcs"):
        value = int(getattr(cvrp, field_name))
        if not 1 <= value <= 1_000_000:
            raise ValueError(f"{field_name} must be between 1 and 1000000")
    search_lambda = float(getattr(cvrp, "search_lambda_coefficient", 0.7))
    if not 0 <= search_lambda <= 1000:
        raise ValueError("search_lambda_coefficient must be between 0 and 1000")
    global_start = int(getattr(cvrp, "global_start_time_minutes", 480))
    if not 0 <= global_start <= 1439:
        raise ValueError("global_start_time_minutes must be between 0 and 1439")

    vroom_worker_timeout = int(getattr(cvrp, "vroom_worker_timeout_seconds", 0) or 0)
    if not 0 <= vroom_worker_timeout <= 86400:
        raise ValueError("vroom_worker_timeout_seconds must be between 0 and 86400")
    vroom_threads = int(getattr(cvrp, "vroom_threads", 0) or 0)
    if not 0 <= vroom_threads <= 256:
        raise ValueError("vroom_threads must be between 0 and 256")
    vroom_exploration = int(getattr(cvrp, "vroom_exploration_level", 5))
    if not 0 <= vroom_exploration <= 5:
        raise ValueError("vroom_exploration_level must be between 0 and 5")
    vrp_worker_timeout = int(getattr(cvrp, "vrp_worker_timeout_seconds", 0) or 0)
    if not 0 <= vrp_worker_timeout <= 86400:
        raise ValueError("vrp_worker_timeout_seconds must be between 0 and 86400")
    vrp_threads = int(getattr(cvrp, "vrp_threads", 0) or 0)
    if not 0 <= vrp_threads <= 256:
        raise ValueError("vrp_threads must be between 0 and 256")
    vrp_max_generations = int(getattr(cvrp, "vrp_max_generations", 1_000_000) or 0)
    if not 1 <= vrp_max_generations <= 1_000_000_000_000:
        raise ValueError("vrp_max_generations must be between 1 and 1000000000000")
    if cvrp.solver_type == "vroom" and bool(getattr(cvrp, "enable_multiple_trips", False)):
        raise ValueError(
            "VROOM 1.15 does not support linked multiple trips; disable "
            "enable_multiple_trips or choose PyVRP/OR-Tools/VRP-Rust"
        )

    upload_mode = str(getattr(output, "route_maps_upload_mode", "disabled") or "disabled").strip().lower()
    if upload_mode not in {"disabled", "legacy", "effect_upload"}:
        raise ValueError("route_maps_upload_mode must be disabled, legacy or effect_upload")
    output.route_maps_upload_mode = upload_mode

    map_provider = str(getattr(output, "map_provider", "osm") or "osm").strip().lower()
    if map_provider not in {"osm", "google"}:
        raise ValueError("map_provider must be osm or google")
    output.map_provider = map_provider

    method = str(getattr(set_data, "set_data_http_method", "GET") or "GET").strip().upper()
    if method not in {"GET", "POST"}:
        raise ValueError("set_data_http_method must be GET or POST")
    set_data.set_data_http_method = method

    for label, value in (
        ("route_maps_upload_timeout_seconds", getattr(output, "route_maps_upload_timeout_seconds", 60)),
        ("set_data_timeout_seconds", getattr(set_data, "set_data_timeout_seconds", 30)),
    ):
        numeric = int(value)
        if numeric < 1 or numeric > 3600:
            raise ValueError(f"{label} must be between 1 and 3600")

    def require_http_url(label: str, raw_url: Any) -> None:
        text = str(raw_url or "").strip()
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{label} must be a valid http/https URL")

    if upload_mode == "effect_upload":
        require_http_url("route_maps_upload_url", getattr(output, "route_maps_upload_url", ""))
    if bool(getattr(set_data, "enable_set_data_upload", False)):
        require_http_url("set_data_url", getattr(set_data, "set_data_url", ""))

    for label, raw_path, enabled in (
        ("map_output_file", getattr(output, "map_output_file", ""), bool(getattr(output, "enable_interactive_map", False))),
        ("routes_output_dir", getattr(output, "routes_output_dir", ""), bool(getattr(output, "enable_interactive_map", False))),
        ("excel_output_dir", getattr(output, "excel_output_dir", ""), bool(getattr(output, "enable_excel_output", False))),
        ("csv_output_file", getattr(output, "csv_output_file", ""), bool(getattr(output, "enable_csv_output", False))),
        ("charts_output_dir", getattr(output, "charts_output_dir", ""), bool(getattr(output, "enable_charts", False))),
    ):
        path_text = str(raw_path or "")
        if enabled and not path_text.strip():
            raise ValueError(f"{label} is required when its output is enabled")
        if "\x00" in path_text:
            raise ValueError(f"{label} contains an invalid NUL character")


def _apply_web_run_settings(config_obj: MainConfig, settings: Dict[str, Dict[str, Any]]) -> list[str]:
    applied: list[str] = []
    for section_name, section_payload in (settings or {}).items():
        section_obj = getattr(config_obj, section_name)
        for field_name, raw_value in section_payload.items():
            current_value = getattr(section_obj, field_name)
            setting_name = f"{section_name}.{field_name}"
            if isinstance(current_value, bool):
                if not isinstance(raw_value, bool):
                    raise ValueError(f"{setting_name} must be a JSON boolean")
            elif isinstance(current_value, int):
                if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                    raise ValueError(f"{setting_name} must be a JSON integer")
            elif isinstance(current_value, float):
                if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                    raise ValueError(f"{setting_name} must be a JSON number")
            elif isinstance(current_value, str):
                if not isinstance(raw_value, str):
                    raise ValueError(f"{setting_name} must be a JSON string")
            elif raw_value is not None and not isinstance(raw_value, type(current_value)):
                raise ValueError(f"{setting_name} has an invalid JSON type")
            value = _coerce_setting_value(section_name, field_name, raw_value, current_value)
            setattr(section_obj, field_name, value)
            applied.append(f"{section_name}.{field_name}")
    _validate_web_run_config(config_obj)
    return applied


def _merge_web_run_settings(*settings_items: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for settings in settings_items:
        for section_name, section_payload in (settings or {}).items():
            merged.setdefault(section_name, {}).update(deepcopy(section_payload))
    return merged


def _build_web_run_config_override(payload: Any = None) -> tuple[MainConfig, list[str], list[str]]:
    """Build an isolated config used exclusively by a Web GUI run."""
    base_config = get_config()
    request_settings = _extract_web_run_settings(payload)

    request_config = deepcopy(base_config)
    applied = _apply_web_run_settings(request_config, request_settings)
    return request_config, list(dict.fromkeys(applied)), []


def _web_run_settings_from_config(config_obj: MainConfig) -> Dict[str, Dict[str, Any]]:
    return {
        section_name: {
            field_name: deepcopy(getattr(getattr(config_obj, section_name), field_name))
            for field_name in sorted(allowed_fields)
            if hasattr(getattr(config_obj, section_name), field_name)
        }
        for section_name, allowed_fields in _WEB_RUN_SECTION_FIELDS.items()
    }


def _redact_web_run_settings(settings: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    redacted = deepcopy(settings)
    output = redacted.setdefault("output", {})
    token = output.pop("route_maps_upload_token", "")
    output["route_maps_upload_token_configured"] = bool(token)
    return redacted


def _canonical_web_run_settings(
    raw_settings: Dict[str, Dict[str, Any]],
    base_config: Optional[MainConfig] = None,
) -> Dict[str, Dict[str, Any]]:
    config_obj = deepcopy(base_config or get_config())
    _apply_web_run_settings(config_obj, raw_settings)
    canonical: Dict[str, Dict[str, Any]] = {}
    for section_name, section_payload in raw_settings.items():
        section_obj = getattr(config_obj, section_name)
        canonical[section_name] = {
            field_name: deepcopy(getattr(section_obj, field_name))
            for field_name in sorted(section_payload)
        }
    return canonical


def _coords_to_text(coords: Any) -> str:
    if not coords:
        return ""
    try:
        return f"{float(coords[0])}, {float(coords[1])}"
    except Exception:
        return ""


def _depot_name_for_coords(config_obj: MainConfig, coords: Any) -> str:
    target = coords or config_obj.locations.depot_location
    try:
        target_coords = _parse_coords(target)
    except ValueError:
        return ""
    for name, depot_coords in get_named_depots(config_obj.locations).items():
        try:
            parsed = _parse_coords(depot_coords)
        except ValueError:
            continue
        if abs(parsed[0] - target_coords[0]) <= 1e-8 and abs(parsed[1] - target_coords[1]) <= 1e-8:
            return str(name)
    return ""


def _vehicle_to_web_dict(vehicle: VehicleConfig, config_obj: Optional[MainConfig] = None) -> Dict[str, Any]:
    result = {
        "vehicle_type": getattr(getattr(vehicle, "vehicle_type", None), "value", str(getattr(vehicle, "vehicle_type", ""))),
        "name": str(getattr(vehicle, "name", "") or ""),
        "config_id": str(getattr(vehicle, "config_id", "") or ""),
        "enabled": bool(getattr(vehicle, "enabled", True)),
        "count": int(getattr(vehicle, "count", 0) or 0),
        "capacity": int(getattr(vehicle, "capacity", 0) or 0),
        "fixed_cost": int(getattr(vehicle, "fixed_cost", 0) or 0),
        "max_distance_km": getattr(vehicle, "max_distance_km", None),
        "max_time_hours": int(getattr(vehicle, "max_time_hours", 8) or 8),
        "service_time_minutes": int(getattr(vehicle, "service_time_minutes", 8) or 8),
        "max_customers_per_route": getattr(vehicle, "max_customers_per_route", None),
        "max_customers_per_day": getattr(vehicle, "max_customers_per_day", None),
        "end_location": _coords_to_text(getattr(vehicle, "end_location", None)),
        "reload_location": _coords_to_text(getattr(vehicle, "reload_location", None) or getattr(vehicle, "start_location", None)),
        "reload_time_minutes": int(getattr(vehicle, "reload_time_minutes", 30) or 0),
        "start_time_minutes": int(getattr(vehicle, "start_time_minutes", 480) or 480),
    }
    if config_obj is None:
        result["start_location"] = _coords_to_text(getattr(vehicle, "start_location", None))
    else:
        result["start_depot_name"] = _depot_name_for_coords(
            config_obj,
            getattr(vehicle, "start_location", None),
        )
    return result


def _reload_config_from_disk():
    """Reload config.py so the web GUI reflects desktop GUI changes."""
    global MainConfig, RoutingEngine, CenterZoneConfig, TrafficZoneConfig, VehicleConfig, VehicleType, CVRP_SOLVER_TYPES, get_named_depots, get_config

    with _CONFIG_RELOAD_LOCK:
        module = importlib.reload(config_module)
        MainConfig = module.MainConfig
        RoutingEngine = module.RoutingEngine
        CenterZoneConfig = module.CenterZoneConfig
        TrafficZoneConfig = module.TrafficZoneConfig
        VehicleConfig = module.VehicleConfig
        VehicleType = module.VehicleType
        CVRP_SOLVER_TYPES = module.CVRP_SOLVER_TYPES
        get_named_depots = module.get_named_depots
        get_config = module.get_config
        return module.get_config()


def _web_gui_config_payload(host: str, port: int, refresh_from_disk: bool = True) -> Dict[str, Any]:
    cfg = _reload_config_from_disk() if refresh_from_disk else get_config()
    logger.info("Web GUI config loaded: %s vehicles from config.py", len(cfg.vehicles or []))
    api_config = cfg.api
    web_endpoint = _web_gui_endpoint(api_config)
    web_url = _web_gui_public_url(api_config, host, port)
    cvrp = cfg.cvrp
    api_users = _get_web_auth_service(api_config).credential_store.list_usernames()
    global_web_run_settings = _web_run_settings_from_config(cfg)
    saved_web_run_settings = _stored_web_run_settings(api_config)

    return {
        "status": "ok",
        "app_name": str(getattr(api_config, "web_gui_title", "CVRP Optimizer") or "CVRP Optimizer"),
        "web_gui": {
            "enabled": bool(getattr(api_config, "web_gui_enabled", True)),
            "endpoint": web_endpoint,
            "url": web_url,
            "users": sorted(api_users),
            "authentication": "session_cookie_pbkdf2",
            "https_recommended": not str(web_url).lower().startswith("https://"),
        },
        "api": {
            "public_url": _build_public_base_url(api_config, host, port),
            "run_endpoint": _normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run")),
            "saturday_run_endpoint": _normalise_endpoint(getattr(api_config, "saturday_trigger_endpoint", "/run_saturday")),
            "health_endpoint": _normalise_endpoint(getattr(api_config, "health_endpoint", "/health")),
        },
        "cvrp": {
            field_name: getattr(cvrp, field_name)
            for field_name in sorted(_WEB_GUI_CVRP_FIELDS)
            if hasattr(cvrp, field_name)
        },
        "vehicles": [_vehicle_to_web_dict(vehicle, cfg) for vehicle in (cfg.vehicles or [])],
        "depots": [
            {
                "name": str(name),
                "coordinates": _coords_to_text(coords),
            }
            for name, coords in get_named_depots(cfg.locations).items()
        ],
        "vehicle_types": [
            vehicle_type.value
            for vehicle_type in VehicleType
            if vehicle_type not in (VehicleType.WAREHOUSE, VehicleType.DISABLED)
        ],
        "web_run_settings": _redact_web_run_settings(global_web_run_settings),
        "web_run_global_settings": _redact_web_run_settings(global_web_run_settings),
        "web_run_defaults_saved": bool(saved_web_run_settings),
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
        if field_name in {
            "parallel_first_solution_strategies",
            "parallel_local_search_metaheuristics",
        }:
            list_value = [str(item) for item in (value or [])]
            list_factory_pattern = (
                rf'(?ms)^(\s*{re.escape(field_name)}\s*:\s*[^=\n]+=\s*)'
                rf'field\(default_factory=lambda:\s*\[.*?^\s*\]\)'
                rf'(\s*(?:#.*)?$)'
            )
            replaced, count = re.subn(
                list_factory_pattern,
                lambda field_match: (
                    f"{field_match.group(1)}"
                    f"field(default_factory=lambda: {list_value!r})"
                    f"{field_match.group(2)}"
                ),
                class_block,
                count=1,
            )
            if count:
                return replaced
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
        f"                config_id={_python_literal(getattr(vehicle, 'config_id', ''))},",
        f"                fixed_cost={int(getattr(vehicle, 'fixed_cost', 0) or 0)},",
        f"                max_distance_km={_optional_int_literal(getattr(vehicle, 'max_distance_km', None))},",
        f"                max_time_hours={int(getattr(vehicle, 'max_time_hours', 8) or 8)},",
        f"                service_time_minutes={int(getattr(vehicle, 'service_time_minutes', 8) or 8)},",
        f"                enabled={'True' if bool(getattr(vehicle, 'enabled', True)) else 'False'},",
        f"                max_customers_per_route={_optional_int_literal(getattr(vehicle, 'max_customers_per_route', None))},",
        f"                max_customers_per_day={_optional_int_literal(getattr(vehicle, 'max_customers_per_day', None))},",
        f"                start_location={_tuple_literal(getattr(vehicle, 'start_location', None))},",
        f"                end_location={_tuple_literal(getattr(vehicle, 'end_location', None))},",
        f"                reload_location={_tuple_literal(getattr(vehicle, 'reload_location', None) or getattr(vehicle, 'start_location', None))},",
        f"                reload_time_minutes={int(getattr(vehicle, 'reload_time_minutes', 30) or 0)},",
        f"                start_time_minutes={int(getattr(vehicle, 'start_time_minutes', 480) or 480)},",
        f"                tsp_depot_location={_tuple_literal(getattr(vehicle, 'tsp_depot_location', None) or getattr(vehicle, 'start_location', None))}",
        "            ),",
    ])


def _replace_vehicles_list_literal(content: str, vehicles: list[VehicleConfig]) -> str:
    vehicles_block = "\n".join(_vehicle_config_literal(vehicle) for vehicle in vehicles)
    pattern = r'(def _create_default_vehicles\(self\)\s*->\s*List\[VehicleConfig\]:.*?return\s*)\[.*?\n        \]'
    return re.sub(pattern, lambda match: f"{match.group(1)}[\n{vehicles_block}\n        ]", content, count=1, flags=re.S)


def _backup_config_py(config_path: str) -> str:
    backup_dir = os.path.join(os.path.dirname(config_path), "config_backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = os.path.join(backup_dir, f"config_web_gui_{timestamp}.py")
    shutil.copy2(config_path, backup_path)
    return backup_path


def _persist_web_gui_config(
    config_obj: MainConfig,
    *,
    include_api: bool = True,
    include_cvrp: bool = True,
    include_vehicles: bool = True,
    include_output: bool = False,
    include_set_data: bool = False,
) -> str:
    """Atomically persists only the explicitly selected config.py sections."""
    config_path = os.path.abspath(config_module.__file__)
    with open(config_path, "r", encoding="utf-8") as file_handle:
        content = file_handle.read()

    if include_api:
        for field_name in sorted(_WEB_GUI_PERSIST_API_FIELDS):
            if hasattr(config_obj.api, field_name):
                content = _replace_class_field_literal(content, "APIConfig", field_name, getattr(config_obj.api, field_name))
    if include_cvrp:
        for field_name in sorted(_WEB_GUI_CVRP_FIELDS):
            if hasattr(config_obj.cvrp, field_name):
                content = _replace_class_field_literal(content, "CVRPConfig", field_name, getattr(config_obj.cvrp, field_name))
    if include_output:
        for field_name in sorted(_WEB_RUN_OUTPUT_FIELDS):
            if hasattr(config_obj.output, field_name):
                content = _replace_class_field_literal(content, "OutputConfig", field_name, getattr(config_obj.output, field_name))
    if include_set_data:
        for field_name in sorted(_WEB_RUN_SET_DATA_FIELDS):
            if hasattr(config_obj.set_data, field_name):
                content = _replace_class_field_literal(content, "SetDataConfig", field_name, getattr(config_obj.set_data, field_name))
    if include_vehicles:
        content = _replace_vehicles_list_literal(content, list(config_obj.vehicles or []))

    compile(content, config_path, "exec")
    backup_path = _backup_config_py(config_path)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".config_web_gui_",
        suffix=".tmp",
        dir=os.path.dirname(config_path),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(tmp_path, config_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return backup_path


def _apply_web_gui_config_save(payload: Any) -> Dict[str, Any]:
    with _CONFIG_TRANSACTION_LOCK:
        return _apply_web_gui_config_save_locked(payload)


def _apply_web_gui_config_save_locked(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Web GUI save body must be a JSON object")

    config_obj = deepcopy(get_config())
    applied: list[str] = []

    if "vehicles" in payload:
        raise ValueError(
            "Бусовете се записват отделно чрез Web действието 'Запази само бусовете'."
        )

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

    for section_name, allowed_fields in (
        ("output", _WEB_RUN_OUTPUT_FIELDS),
        ("set_data", _WEB_RUN_SET_DATA_FIELDS),
    ):
        section_payload = payload.get(section_name)
        if section_payload is None:
            continue
        if not isinstance(section_payload, dict):
            raise ValueError(f"{section_name} must be a JSON object")
        section_obj = getattr(config_obj, section_name)
        unknown_fields = sorted(set(section_payload) - allowed_fields)
        if unknown_fields:
            raise ValueError(
                f"Unsupported global Web fields in {section_name}: " + ", ".join(unknown_fields)
            )
        for field_name, raw_value in section_payload.items():
            current_value = getattr(section_obj, field_name)
            setattr(
                section_obj,
                field_name,
                _coerce_setting_value(section_name, field_name, raw_value, current_value),
            )
            applied.append(f"{section_name}.{field_name}")

    if bool(payload.get("clear_upload_token", False)):
        config_obj.output.route_maps_upload_token = ""
        applied.append("output.route_maps_upload_token.clear")

    saves_operational_settings = any(
        item.startswith("output.") or item.startswith("set_data.")
        for item in applied
    )
    if saves_operational_settings and str(
        getattr(config_obj.api, "web_gui_run_defaults_json", "") or ""
    ):
        # Old builds stored a Web-only preset.  Once the user explicitly saves
        # global settings it must not silently override them on future Web runs.
        config_obj.api.web_gui_run_defaults_json = ""
        applied.append("api.web_gui_run_defaults_json.clear")

    _validate_web_run_config(config_obj)

    if not applied:
        raise ValueError("Няма подадени общи настройки за записване")

    backup_path = _persist_web_gui_config(
        config_obj,
        include_api=any(item.startswith("api.") for item in applied),
        include_cvrp=any(item.startswith("cvrp.") for item in applied),
        include_vehicles=False,
        include_output=any(item.startswith("output.") for item in applied),
        include_set_data=any(item.startswith("set_data.") for item in applied),
    )
    config_module.config_manager.config = config_obj
    return {
        "status": "saved",
        "scope": "global_settings_excluding_vehicles",
        "applied": applied,
        "backup_file": backup_path,
    }


def _apply_web_gui_vehicles_save(payload: Any) -> Dict[str, Any]:
    with _CONFIG_TRANSACTION_LOCK:
        return _apply_web_gui_vehicles_save_locked(payload)


def _apply_web_gui_vehicles_save_locked(payload: Any) -> Dict[str, Any]:
    """Persist only VehicleConfig rows and leave every other setting untouched."""
    if not isinstance(payload, dict):
        raise ValueError("Web GUI vehicles body must be a JSON object")
    unknown = sorted(set(payload) - {"vehicles"})
    if unknown:
        raise ValueError(
            "Заявката за бусове приема само полето vehicles; непозволени полета: "
            + ", ".join(unknown)
        )

    vehicle_items = payload.get("vehicles")
    if not isinstance(vehicle_items, list):
        raise ValueError("vehicles must be a JSON list")

    config_obj = deepcopy(get_config())
    existing_vehicles = list(config_obj.vehicles or [])
    vehicles: list[VehicleConfig] = []
    for index, raw_item in enumerate(vehicle_items):
        if not isinstance(raw_item, dict):
            raise ValueError("vehicles трябва да съдържа само JSON обекти")
        item = dict(raw_item)
        if "start_location" in item:
            raise ValueError(
                "Началната точка в Web портала се задава само чрез start_depot_name, не чрез GPS."
            )
        start_depot_name = str(item.pop("start_depot_name", "") or "").strip()
        if not start_depot_name:
            raise ValueError(f"Бус {index + 1}: избери начално депо")
        item["start_location"] = _resolve_web_depot_name(config_obj, start_depot_name)
        raw_original_index = item.pop("_original_index", index)
        try:
            original_index = int(raw_original_index)
        except (TypeError, ValueError):
            original_index = index
        template = existing_vehicles[original_index] if 0 <= original_index < len(existing_vehicles) else None
        vehicles.append(_vehicle_from_payload(config_obj, item, template))

    _validate_vehicle_configs(vehicles)
    config_obj.vehicles = vehicles
    backup_path = _persist_web_gui_config(
        config_obj,
        include_api=False,
        include_cvrp=False,
        include_vehicles=True,
    )

    current_config = getattr(config_module.config_manager, "config", None)
    if current_config is None:
        config_module.config_manager.config = config_obj
    else:
        current_config.vehicles = deepcopy(vehicles)

    return {
        "status": "saved",
        "scope": "vehicles_only",
        "applied": ["vehicles.replace"],
        "vehicle_count": len(vehicles),
        "backup_file": backup_path,
    }


def _save_web_run_defaults(payload: Any) -> Dict[str, Any]:
    with _CONFIG_TRANSACTION_LOCK:
        return _save_web_run_defaults_locked(payload)


def _save_web_run_defaults_locked(payload: Any) -> Dict[str, Any]:
    """Persist a Web-only output/setData preset without changing global runs."""
    if not isinstance(payload, dict):
        raise ValueError("Web run defaults body must be a JSON object")

    config_obj = deepcopy(get_config())
    if bool(payload.get("clear", False)):
        config_obj.api.web_gui_run_defaults_json = ""
        applied: list[str] = ["api.web_gui_run_defaults_json.clear"]
    else:
        request_settings = _extract_web_run_settings(payload)
        if not request_settings:
            raise ValueError("run_settings cannot be empty when saving Web defaults")
        canonical = _canonical_web_run_settings(request_settings, base_config=config_obj)
        existing = _stored_web_run_settings(config_obj.api)

        existing_token = (existing.get("output") or {}).get("route_maps_upload_token")
        output_payload = canonical.setdefault("output", {})
        if bool(payload.get("clear_upload_token", False)):
            output_payload.pop("route_maps_upload_token", None)
        elif "route_maps_upload_token" not in output_payload and existing_token is not None:
            output_payload["route_maps_upload_token"] = existing_token

        config_obj.api.web_gui_run_defaults_json = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        applied = [
            f"web_defaults.{section_name}.{field_name}"
            for section_name, section_payload in canonical.items()
            for field_name in section_payload
        ]

    backup_path = _persist_web_gui_config(
        config_obj,
        include_api=True,
        include_cvrp=False,
        include_vehicles=False,
    )
    config_module.config_manager.config = config_obj
    return {
        "status": "saved",
        "scope": "web_gui_runs_only",
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


def _json_for_inline_script(value: Any) -> str:
    """Serialize JSON without allowing HTML to terminate an inline script."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _web_gui_login_html(api_config) -> str:
    base_path = _web_gui_endpoint(api_config)
    title = html.escape(str(getattr(api_config, "web_gui_title", "CVRP Optimizer") or "CVRP Optimizer"))
    storage_unavailable = False
    try:
        has_users = bool(_get_web_auth_service(api_config).credential_store.list_usernames())
    except CredentialStoreError:
        has_users = False
        storage_unavailable = True
    if storage_unavailable:
        setup_notice = (
            '<div class="setup">Защитеното хранилище е недостъпно. Възстанови или поправи '
            'data/web_gui_auth.json, после управлявай потребителите от Desktop Settings.</div>'
        )
    elif not has_users:
        setup_notice = (
            '<div class="setup">Няма настроен Web потребител. Отвори Desktop Settings → API/Web, '
            'добави потребител и после обнови тази страница.</div>'
        )
    else:
        setup_notice = ""
    transport_warning = "" if str(getattr(api_config, "web_gui_public_url", "") or "").lower().startswith("https://") else (
        '<div class="warning">Връзката е по HTTP. За достъп извън защитена локална мрежа използвай HTTPS reverse proxy.</div>'
    )
    return f"""<!doctype html>
<html lang="bg">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Вход · {title}</title>
  <style>
    :root {{ color-scheme:light; --ink:#172033; --muted:#64748b; --accent:#2563eb; --line:#d8dee8; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:24px; font-family:Segoe UI,Arial,sans-serif; color:var(--ink); background:radial-gradient(circle at top left,#dbeafe 0,transparent 34%),#f1f5f9; }}
    main {{ width:min(430px,100%); background:#fff; border:1px solid var(--line); border-radius:16px; padding:28px; box-shadow:0 20px 55px rgba(15,23,42,.12); }}
    h1 {{ margin:0 0 6px; font-size:24px; }} p {{ margin:0 0 20px; color:var(--muted); font-size:13px; }}
    label {{ display:block; margin:12px 0 5px; color:#475569; font-size:12px; font-weight:700; }}
    input {{ width:100%; padding:11px 12px; border:1px solid #cbd5e1; border-radius:8px; font:inherit; }}
    button {{ width:100%; margin-top:18px; padding:11px 14px; border:0; border-radius:8px; color:#fff; background:var(--accent); font-weight:700; cursor:pointer; }}
    button:disabled {{ opacity:.6; cursor:wait; }}
    .error {{ min-height:20px; margin-top:12px; color:#b91c1c; font-size:12px; font-weight:600; }}
    .warning {{ margin:16px 0 2px; padding:10px 12px; border-radius:8px; background:#fff7ed; color:#9a3412; font-size:11px; line-height:1.4; }}
    .setup {{ margin:0 0 16px; padding:11px 12px; border-radius:8px; background:#eff6ff; color:#1e40af; font-size:12px; line-height:1.45; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>Влез, за да стартираш рънове и да управляваш Web настройките.</p>
    {setup_notice}
    <form id="loginForm">
      <label for="username">Потребител</label>
      <input id="username" autocomplete="username" required autofocus>
      <label for="password">Парола</label>
      <input id="password" type="password" autocomplete="current-password" required>
      <button id="loginButton" type="submit">Вход</button>
      <div id="loginError" class="error"></div>
    </form>
    {transport_warning}
  </main>
  <script>
    const basePath = {_json_for_inline_script(base_path)};
    document.getElementById("loginForm").addEventListener("submit", async event => {{
      event.preventDefault();
      const button = document.getElementById("loginButton");
      const error = document.getElementById("loginError");
      button.disabled = true; error.textContent = "";
      try {{
        const response = await fetch(basePath + "/api/login", {{
          method:"POST", credentials:"same-origin", headers:{{"Content-Type":"application/json"}},
          body:JSON.stringify({{username:document.getElementById("username").value,password:document.getElementById("password").value}})
        }});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Неуспешен вход.");
        window.location.replace(basePath);
      }} catch (err) {{ error.textContent = String(err.message || err); }}
      finally {{ button.disabled = false; }}
    }});
  </script>
</body>
</html>"""


def _web_gui_html(api_config, host: str, port: int) -> str:
    base_path = _web_gui_endpoint(api_config)
    data_json = _json_for_inline_script({
        "apiBase": f"{base_path}/api",
        "title": str(getattr(api_config, "web_gui_title", "CVRP Optimizer") or "CVRP Optimizer"),
    })
    base_path_json = _json_for_inline_script(base_path)
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
    header {{ background:var(--surface); border-bottom:1px solid var(--line); padding:16px 22px; display:flex; flex-wrap:wrap; gap:12px 16px; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:5; }}
    h1 {{ margin:0; font-size:21px; font-weight:700; }}
    h2 {{ margin:0 0 12px; font-size:16px; }}
    .muted {{ color:var(--muted); font-size:12px; }}
    main {{ width:min(1580px, 100%); margin:0 auto; padding:18px 22px 28px; display:grid; grid-template-columns: 1fr; gap:16px; }}
    section {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:16px; box-shadow:0 1px 2px rgba(15,23,42,.04); }}
    label {{ display:block; font-size:12px; color:var(--muted); margin:10px 0 4px; }}
    input, select, textarea {{ width:100%; padding:8px 10px; border:1px solid #cbd5e1; border-radius:6px; background:#fff; font:inherit; }}
    textarea {{ resize:vertical; }}
    input[type="checkbox"] {{ width:auto; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; min-width:0; }}
    header > .actions {{ justify-content:flex-end; }}
    button {{ border:1px solid #cbd5e1; background:#fff; border-radius:6px; padding:8px 12px; cursor:pointer; font-weight:600; }}
    button.primary {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
    button.danger {{ color:var(--bad); }}
    .pill {{ display:inline-block; padding:4px 8px; border-radius:999px; background:#e2e8f0; font-size:12px; }}
    .pill.ok {{ background:#dcfce7; color:var(--ok); }}
    .pill.bad {{ background:#fee2e2; color:var(--bad); }}
    .vehicles-section {{ border-color:#bfdbfe; background:linear-gradient(180deg,#f8fbff 0,#fff 140px); }}
    .vehicles-wrap {{ border:1px solid #dbeafe; border-radius:12px; background:#eff6ff; padding:14px; overflow:hidden; }}
    .vehicle-list {{ display:flex; flex-direction:column; gap:16px; }}
    .vehicle-row {{ display:flex; flex-wrap:wrap; gap:10px 12px; align-items:end; background:#fff; border:1px solid #bfdbfe; border-left:6px solid #2563eb; border-radius:12px; padding:14px; width:100%; min-width:0; box-shadow:0 5px 15px rgba(15,23,42,.07); transition:opacity .15s, box-shadow .15s; }}
    .vehicle-row:nth-child(4n+2) {{ border-left-color:#7c3aed; }}
    .vehicle-row:nth-child(4n+3) {{ border-left-color:#0f766e; }}
    .vehicle-row:nth-child(4n+4) {{ border-left-color:#c2410c; }}
    .vehicle-row.inactive {{ opacity:.62; box-shadow:none; }}
    .vehicle-card-header {{ flex:1 0 100%; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px; padding-bottom:10px; margin-bottom:2px; border-bottom:1px solid #e2e8f0; }}
    .vehicle-card-identity {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; min-width:0; }}
    .vehicle-number {{ display:inline-flex; align-items:center; justify-content:center; min-width:62px; padding:5px 9px; border-radius:999px; background:#dbeafe; color:#1d4ed8; font-size:11px; font-weight:800; }}
    .vehicle-card-title {{ font-size:15px; color:#0f172a; overflow-wrap:anywhere; }}
    .vehicle-type-badge {{ display:inline-flex; padding:4px 8px; border-radius:6px; background:#f1f5f9; color:#475569; font:700 11px/1.2 Consolas,monospace; }}
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
    @media (max-width: 760px) {{ .vehicle-row {{ width:100%; }} .vehicle-field.type, .vehicle-field.name, .vehicle-field.active, .vehicle-field.small, .vehicle-field.medium, .vehicle-field.service, .vehicle-field.gps {{ width:100%; flex-basis:100%; }} .vehicle-card-header {{ align-items:flex-start; }} }}
    .logs-panel {{ max-width:1200px; }}
    .section-head {{ display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:14px; }}
    .section-head h2 {{ margin-bottom:4px; }}
    .scope-badge {{ display:inline-flex; align-items:center; gap:6px; padding:5px 9px; border-radius:999px; background:#dbeafe; color:#1d4ed8; font-size:11px; font-weight:700; }}
    .settings-grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px; }}
    .settings-card {{ border:1px solid #dbe3ef; border-radius:10px; padding:14px; background:#f8fafc; }}
    .solver-settings-card {{ grid-column:1 / -1; background:#eef6ff; border-color:#bfdbfe; }}
    .solver-settings-grid {{ display:grid; grid-template-columns:repeat(4,minmax(160px,1fr)); gap:10px 12px; margin-top:10px; }}
    .solver-settings-grid .wide {{ grid-column:1 / -1; }}
    .solver-fine-panel {{ margin-top:14px; padding:13px; border:1px solid #bfdbfe; border-radius:9px; background:#fff; }}
    .solver-fine-panel h4 {{ margin:0 0 4px; font-size:13px; color:#1e3a8a; }}
    .solver-fine-panel[hidden] {{ display:none; }}
    .settings-card h3 {{ margin:0 0 5px; font-size:14px; }}
    .field-grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:0 12px; }}
    .field-grid .wide {{ grid-column:1 / -1; }}
    .check-grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:8px 12px; margin:12px 0; }}
    .check-line {{ display:flex; align-items:center; gap:8px; padding:8px 9px; border:1px solid #e2e8f0; border-radius:7px; background:#fff; font-size:12px; }}
    .check-line label {{ margin:0; color:var(--text); }}
    details {{ margin-top:12px; border-top:1px solid #e2e8f0; padding-top:10px; }}
    summary {{ cursor:pointer; color:#334155; font-size:12px; font-weight:700; }}
    .run-actions {{ margin-top:14px; padding-top:14px; border-top:1px solid var(--line); }}
    .notice {{ padding:10px 12px; border-radius:8px; background:#eff6ff; color:#1e40af; font-size:12px; line-height:1.45; }}
    .notice.warning {{ background:#fff7ed; color:#9a3412; }}
    .form-status {{ min-height:18px; margin-top:10px; font-size:12px; font-weight:600; color:var(--muted); }}
    .form-status.ok {{ color:var(--ok); }}
    .form-status.bad {{ color:var(--bad); }}
    .secret-meta {{ margin-top:5px; font-size:11px; color:var(--muted); }}
    input:disabled, select:disabled {{ background:#eef2f7; color:#94a3b8; cursor:not-allowed; }}
    @media (max-width: 980px) {{ .settings-grid {{ grid-template-columns:1fr; }} .solver-settings-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media (max-width: 640px) {{ header {{ align-items:flex-start; flex-direction:column; }} main {{ padding:12px; }} .field-grid, .check-grid, .solver-settings-grid {{ grid-template-columns:1fr; }} }}
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
      <button onclick="saveGlobalSettings()">Запази настройките глобално</button>
      <button class="primary" onclick="startRun()">Стартирай без запис</button>
      <button class="danger" onclick="stopRun()">Спри run</button>
      <button class="danger" onclick="shutdownProgram()">Спри API</button>
      <button onclick="logout()">Изход</button>
    </div>
  </header>
  <main>
    <section class="vehicles-section">
      <div class="section-head">
        <div>
          <h2>Превозни средства</h2>
          <div class="muted">Бусовете са отделна глобална конфигурация. Всеки панел отдолу е самостоятелен тип/група превозни средства.</div>
        </div>
        <span class="scope-badge">Записват се отделно</span>
      </div>
      <div class="actions" style="margin-bottom:10px">
        <button onclick="addVehicle()">Добави бус</button>
        <button class="primary" onclick="saveVehicles()">Запази само бусовете глобално</button>
        <span class="muted">Този бутон не променя solver, output, setData или други настройки.</span>
      </div>
      <div class="vehicles-wrap">
        <div id="vehiclesBody" class="vehicle-list"></div>
      </div>
      <div id="vehicleSettingsStatus" class="form-status">Зареждане на бусовете…</div>
    </section>
    <section id="runSettingsSection">
      <div class="section-head">
        <div>
          <h2>Настройки за решаване и изпълнение</h2>
          <div class="muted">Една и съща форма може да се използва временно за текущия рън или да се запише глобално за цялата програма.</div>
        </div>
        <span class="scope-badge">Временен рън или глобален запис</span>
      </div>
      <div class="notice" style="margin-bottom:14px">
        <strong>„Стартирай без запис“</strong> използва стойностите само за този Web рън и не променя <code>config.py</code>.
        <strong>„Запази глобално“</strong> записва същите стойности в <code>config.py</code> и те важат за Desktop GUI, Web и API <code>/run</code>.
      </div>
      <div class="settings-grid">
        <div class="settings-card solver-settings-card">
          <h3>Решаване</h3>
          <div class="muted">Тези стойности също участват в временния старт; не е необходимо първо да ги записваш.</div>
          <div class="solver-settings-grid">
            <div><label for="solverType">Solver</label><select id="solverType" title="VRP-Rust е отделен експериментален sidecar; зоните влияят в distance режим, а pure time минимизира само продължителността. VROOM 1.15 не поддържа свързани повторни курсове."><option value="pyvrp">PyVRP 0.13</option><option value="pyvrp_experimental">PyVRP 0.14</option><option value="or_tools">OR-Tools</option><option value="vroom">VROOM 1.15</option><option value="vrp">VRP-Rust</option></select></div>
            <div><label for="objectiveMetric">Цел</label><select id="objectiveMetric" title="time = оптимизация по време; distance = оптимизация по километри."><option value="time">Време</option><option value="distance">Разстояние</option></select></div>
            <div><label for="solverTimeLimit">Лимит (сек.)</label><input id="solverTimeLimit" type="number" min="1" max="86400"></div>
            <div class="check-line"><input id="timeObjectiveIncludeWaiting" type="checkbox"><label for="timeObjectiveIncludeWaiting">Целият работен ден (само време)</label></div>
            <div class="check-line"><input id="multiTripEnabled" type="checkbox"><label for="multiTripEnabled">Разреши повторни курсове</label></div>
          </div>
          <div class="solver-fine-panel" data-solvers="pyvrp,pyvrp_experimental">
            <h4>PyVRP качество</h4>
            <div class="muted">Тези настройки работят и за 0.13, и за изолирания 0.14 worker.</div>
            <div class="solver-settings-grid">
              <div><label for="pyvrpSeed">Seed (празно = база)</label><input id="pyvrpSeed" type="number"></div>
              <div><label for="pyvrpSeedBase">Seed база</label><input id="pyvrpSeedBase" type="number"></div>
              <div><label for="pyvrpNumNeighbours">Съседи</label><input id="pyvrpNumNeighbours" type="number" min="1" max="10000"></div>
              <div><label for="pyvrpWeightWaitTime">Тежест на чакането</label><input id="pyvrpWeightWaitTime" type="number" min="0" max="1000" step="0.01"></div>
              <div><label for="pyvrpIlsNoImprovement">ILS без подобрение</label><input id="pyvrpIlsNoImprovement" type="number" min="0" max="1000000000"></div>
              <div><label for="pyvrpIlsHistoryLength">ILS история</label><input id="pyvrpIlsHistoryLength" type="number" min="1" max="10000000"></div>
              <div><label for="pyvrpMinPerturbations">Мин. perturbations</label><input id="pyvrpMinPerturbations" type="number" min="1" max="1000000"></div>
              <div><label for="pyvrpMaxPerturbations">Макс. perturbations</label><input id="pyvrpMaxPerturbations" type="number" min="1" max="1000000"></div>
              <div><label for="pyvrpDisplayInterval">Progress интервал (сек.)</label><input id="pyvrpDisplayInterval" type="number" min="0.1" max="3600" step="0.1"></div>
              <div class="check-line"><input id="pyvrpSymmetricProximity" type="checkbox"><label for="pyvrpSymmetricProximity">Симетрична proximity</label></div>
              <div class="check-line"><input id="pyvrpExhaustiveOnBest" type="checkbox"><label for="pyvrpExhaustiveOnBest">Exhaustive при best</label></div>
              <div class="check-line"><input id="pyvrpExtendedOperators" type="checkbox"><label for="pyvrpExtendedOperators">Разширени оператори</label></div>
              <div class="check-line"><input id="pyvrpDisplayProgress" type="checkbox"><label for="pyvrpDisplayProgress">Progress лог</label></div>
              <div class="check-line"><input id="pyvrpLibraryPenalties" type="checkbox"><label for="pyvrpLibraryPenalties">Library penalty defaults</label></div>
            </div>
            <div id="pyvrpPenaltyFields" class="solver-settings-grid">
              <div><label for="pyvrpPenaltyUpdateInterval">Решения между updates</label><input id="pyvrpPenaltyUpdateInterval" type="number" min="1" max="1000000000"></div>
              <div><label for="pyvrpPenaltyIncrease">Penalty увеличение</label><input id="pyvrpPenaltyIncrease" type="number" min="1" max="1000" step="0.01"></div>
              <div><label for="pyvrpPenaltyDecrease">Penalty намаление</label><input id="pyvrpPenaltyDecrease" type="number" min="0" max="1" step="0.01"></div>
              <div><label for="pyvrpPenaltyTarget">Target feasible</label><input id="pyvrpPenaltyTarget" type="number" min="0" max="1" step="0.01"></div>
              <div><label for="pyvrpPenaltyTolerance">Feasible tolerance</label><input id="pyvrpPenaltyTolerance" type="number" min="0" max="1" step="0.01"></div>
              <div><label for="pyvrpPenaltyMin">Минимална penalty</label><input id="pyvrpPenaltyMin" type="number" min="0" max="1000000000" step="0.1"></div>
              <div><label for="pyvrpPenaltyMax">Максимална penalty</label><input id="pyvrpPenaltyMax" type="number" min="0" max="1000000000" step="0.1"></div>
            </div>
          </div>
          <div class="solver-fine-panel" data-solvers="pyvrp_experimental">
            <h4>PyVRP 0.14 worker</h4>
            <div class="solver-settings-grid">
              <div><label for="pyvrpNextTimeout">Worker timeout (сек.)</label><input id="pyvrpNextTimeout" type="number" min="0" max="86400" title="0 = автоматично спрямо solver лимита."></div>
              <div class="check-line"><input id="pyvrpNextFallback" type="checkbox"><label for="pyvrpNextFallback">Fallback към PyVRP 0.13</label></div>
            </div>
          </div>
          <div class="solver-fine-panel" data-solvers="or_tools">
            <h4>OR-Tools качество</h4>
            <div class="solver-settings-grid">
              <div><label for="orFirstSolution">First solution</label><select id="orFirstSolution"><option>AUTOMATIC</option><option>PATH_CHEAPEST_ARC</option><option>SAVINGS</option><option>SWEEP</option><option>CHRISTOFIDES</option><option>PARALLEL_CHEAPEST_INSERTION</option></select></div>
              <div><label for="orMetaheuristic">Метаевристика</label><select id="orMetaheuristic"><option>AUTOMATIC</option><option>GUIDED_LOCAL_SEARCH</option><option>SIMULATED_ANNEALING</option><option>TABU_SEARCH</option></select></div>
              <div><label for="orLnsTimeLimit">LNS лимит</label><input id="orLnsTimeLimit" type="number" min="0" max="86400" step="0.1"></div>
              <div><label for="orLnsNodes">LNS близки възли</label><input id="orLnsNodes" type="number" min="1" max="1000000"></div>
              <div><label for="orLnsArcs">LNS скъпи ребра</label><input id="orLnsArcs" type="number" min="1" max="1000000"></div>
              <div><label for="orLambda">GLS lambda</label><input id="orLambda" type="number" min="0" max="1000" step="0.01"></div>
              <div><label for="orGlobalStart">Глобален старт (мин.)</label><input id="orGlobalStart" type="number" min="0" max="1439"></div>
              <div class="wide"><label for="orParallelFirstStrategies">Паралелни First solution стратегии</label><textarea id="orParallelFirstStrategies" rows="5" title="По една стратегия на ред или разделени със запетая."></textarea></div>
              <div class="wide"><label for="orParallelMetaheuristics">Паралелни метаевристики</label><textarea id="orParallelMetaheuristics" rows="5" title="По една метаевристика на ред или разделени със запетая."></textarea></div>
              <div class="check-line"><input id="orFullPropagation" type="checkbox"><label for="orFullPropagation">Full propagation</label></div>
              <div class="check-line"><input id="orLogSearch" type="checkbox"><label for="orLogSearch">Search лог</label></div>
              <div class="check-line"><input id="orStartTracking" type="checkbox"><label for="orStartTracking">Проследяване на старта</label></div>
            </div>
          </div>
          <div class="solver-fine-panel" data-solvers="vroom">
            <h4>VROOM 1.15</h4>
            <div class="solver-settings-grid">
              <div><label for="vroomWorkerTimeout">Worker timeout (сек.)</label><input id="vroomWorkerTimeout" type="number" min="0" max="86400" title="0 = автоматично спрямо solver лимита."></div>
              <div><label for="vroomThreads">Вътрешни threads</label><input id="vroomThreads" type="number" min="0" max="256" title="0 = автоматично всички логически ядра без едно."></div>
              <div><label for="vroomExploration">Exploration (0-5)</label><input id="vroomExploration" type="number" min="0" max="5" title="5 = максимално качество."></div>
            </div>
          </div>
          <div class="solver-fine-panel" data-solvers="vrp">
            <h4>VRP-Rust experimental</h4>
            <div class="solver-settings-grid">
              <div><label for="vrpWorkerTimeout">Worker timeout (сек.)</label><input id="vrpWorkerTimeout" type="number" min="0" max="86400" title="0 = автоматично спрямо solver лимита."></div>
              <div><label for="vrpThreads">Вътрешни threads</label><input id="vrpThreads" type="number" min="0" max="256" title="0 = автоматичен брой нишки."></div>
              <div><label for="vrpMaxGenerations">Макс. поколения</label><input id="vrpMaxGenerations" type="number" min="1" max="1000000000000" title="Времевият лимит също прекратява търсенето."></div>
              <div class="check-line"><input id="vrpLogProgress" type="checkbox"><label for="vrpLogProgress">Progress лог</label></div>
            </div>
          </div>
          <div class="solver-fine-panel" data-solvers="pyvrp,pyvrp_experimental,or_tools">
            <h4>Паралелно търсене</h4>
            <div class="solver-settings-grid">
              <div><label for="solverNumWorkers">Брой процеси</label><input id="solverNumWorkers" type="number" min="-1" max="256" title="-1 = всички ядра без едно."></div>
              <div class="check-line"><input id="solverParallelEnabled" type="checkbox"><label for="solverParallelEnabled">Външни паралелни workers</label></div>
            </div>
          </div>
        </div>
        <div class="settings-card">
          <h3>Изходни файлове</h3>
          <div class="muted">Избери какво да се генерира и къде да се запише.</div>
          <div class="check-grid">
            <div class="check-line"><input id="outMapEnabled" type="checkbox"><label for="outMapEnabled">HTML карти</label></div>
            <div class="check-line"><input id="outExcelEnabled" type="checkbox"><label for="outExcelEnabled">Excel отчети</label></div>
            <div class="check-line"><input id="outCsvEnabled" type="checkbox"><label for="outCsvEnabled">CSV маршрути</label></div>
            <div class="check-line"><input id="outChartsEnabled" type="checkbox"><label for="outChartsEnabled">Графики</label></div>
          </div>
          <div class="field-grid">
            <div class="wide"><label for="outMapFile">Обща HTML карта</label><input id="outMapFile"></div>
            <div class="wide"><label for="outRoutesDir">Папка за индивидуални route карти</label><input id="outRoutesDir"></div>
            <div class="wide"><label for="outExcelDir">Папка за Excel</label><input id="outExcelDir"></div>
            <div class="wide"><label for="outCsvFile">CSV файл</label><input id="outCsvFile"></div>
            <div class="wide"><label for="outChartsDir">Папка за графики</label><input id="outChartsDir"></div>
            <div><label for="outMapProvider">Карта</label><select id="outMapProvider"><option value="osm">OpenStreetMap</option><option value="google">Google</option></select></div>
            <div><label for="outFoliumTiles">OSM слой</label><input id="outFoliumTiles"></div>
          </div>
          <details>
            <summary>Качване на route карти</summary>
            <div class="field-grid">
              <div><label for="outUploadMode">Режим</label><select id="outUploadMode"><option value="disabled">Не качвай</option><option value="legacy">Legacy</option><option value="effect_upload">Effect upload</option></select></div>
              <div><label for="outUploadTimeout">Timeout (сек.)</label><input id="outUploadTimeout" type="number" min="1" max="3600"></div>
              <div class="wide"><label for="outUploadUrl">Upload URL</label><input id="outUploadUrl" type="url"></div>
              <div class="wide"><label for="outUploadToken">Нов upload token</label><input id="outUploadToken" type="password" autocomplete="new-password" placeholder="Празно = използвай запазения"></div>
              <div class="wide secret-meta" id="outUploadTokenState"></div>
              <div class="wide check-line"><input id="outClearUploadToken" type="checkbox"><label for="outClearUploadToken">Изчисти глобално запазения token при запис</label></div>
              <div><label for="outUploadTokenField">Token поле</label><input id="outUploadTokenField"></div>
              <div><label for="outUploadFileField">File поле</label><input id="outUploadFileField"></div>
              <div><label for="outUploadBusField">Bus ID поле</label><input id="outUploadBusField"></div>
            </div>
          </details>
          <details>
            <summary>Имена на Excel файловете</summary>
            <div class="field-grid">
              <div class="wide"><label for="outWarehouseExcel">Необслужени/склад</label><input id="outWarehouseExcel"></div>
              <div class="wide"><label for="outRoutesExcel">Маршрути</label><input id="outRoutesExcel"></div>
              <div class="wide"><label for="outEfficiencyExcel">Ефективност</label><input id="outEfficiencyExcel"></div>
              <div><label for="outSaturdayBusPrefix">Префикс за събота</label><input id="outSaturdayBusPrefix"></div>
              <div><label for="outSaturdayBusDigits">Цифри за събота</label><input id="outSaturdayBusDigits" type="number" min="1" max="9"></div>
            </div>
          </details>
        </div>
        <div class="settings-card">
          <h3>setData</h3>
          <div class="muted">Изпращането променя данни във външната система и винаги се потвърждава преди старт.</div>
          <div class="check-grid">
            <div class="check-line wide"><input id="setDataEnabled" type="checkbox"><label for="setDataEnabled">Изпращай обслужените клиенти</label></div>
            <div class="check-line"><input id="setDataUnserved" type="checkbox"><label for="setDataUnserved">Изпращай необслужените</label></div>
            <div class="check-line"><input id="setDataMakeGroup" type="checkbox"><label for="setDataMakeGroup">Изпрати makeGroup</label></div>
          </div>
          <div class="field-grid">
            <div class="wide"><label for="setDataUrl">setData URL</label><input id="setDataUrl" type="url"></div>
            <div><label for="setDataMethod">HTTP метод</label><select id="setDataMethod"><option value="GET">GET</option><option value="POST">POST</option></select></div>
            <div><label for="setDataTimeout">Timeout (сек.)</label><input id="setDataTimeout" type="number" min="1" max="3600"></div>
            <div><label for="setDataCommand">cmd</label><input id="setDataCommand"></div>
            <div><label for="setDataDoneFlag">DoneFlag</label><input id="setDataDoneFlag"></div>
            <div><label for="setDataMainSkld">Основен IdSkld</label><input id="setDataMainSkld"></div>
            <div><label for="setDataVratzaSkld">Враца IdSkld</label><input id="setDataVratzaSkld"></div>
            <div class="wide"><label for="setDataDepotMap">Депа → IdSkld</label><input id="setDataDepotMap" placeholder="Главно депо=106;Враца=128"></div>
          </div>
          <details>
            <summary>Шаблони за обслужени клиенти</summary>
            <div class="field-grid">
              <div><label for="setDataIdGrafik">IdGrafik</label><input id="setDataIdGrafik"></div>
              <div><label for="setDataIdGrafikTemplate">IdGrafik шаблон</label><input id="setDataIdGrafikTemplate"></div>
              <div class="wide"><label for="setDataBukvaTemplate">Bukva шаблон</label><input id="setDataBukvaTemplate"></div>
            </div>
          </details>
          <details>
            <summary>Необслужени клиенти и makeGroup</summary>
            <div class="field-grid">
              <div><label for="setDataUnservedDoneFlag">DoneFlag</label><input id="setDataUnservedDoneFlag"></div>
              <div><label for="setDataUnservedGrafik">IdGrafik</label><input id="setDataUnservedGrafik"></div>
              <div><label for="setDataUnservedGrafikTemplate">IdGrafik шаблон</label><input id="setDataUnservedGrafikTemplate"></div>
              <div><label for="setDataUnservedBukvaTemplate">Bukva шаблон</label><input id="setDataUnservedBukvaTemplate"></div>
              <div class="wide"><label for="setDataMakeGroupCommand">makeGroup cmd</label><input id="setDataMakeGroupCommand"></div>
            </div>
          </details>
          <div class="notice warning" style="margin-top:12px">При включено setData ще видиш потвърждение непосредствено преди старта.</div>
        </div>
      </div>
      <div class="actions run-actions">
        <button class="primary" onclick="startRun()">Стартирай без запис</button>
        <button onclick="saveGlobalSettings()">Запази глобално в config.py</button>
        <button onclick="useGlobalRunSettings()">Отхвърли промените и зареди глобалните</button>
      </div>
      <div id="runSettingsStatus" class="form-status">Зареждане на настройките…</div>
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
    const pagePath = window.location.pathname.replace(/\\/$/, "");
    const configuredBasePath = {base_path_json};
    bootstrap.apiBase = (pagePath || configuredBasePath) + "/api";
    let state = null;
    let vehicleTypes = ["internal_bus", "center_bus", "external_bus", "special_bus", "vratza_bus"];
    let depotOptions = [];
    let logsTimer = null;
    let globalRunSettings = null;
    let renderingRunSettings = false;
    let runSettingsDirty = false;
    let vehicleSettingsDirty = false;

    function $(id) {{ return document.getElementById(id); }}
    function value(id) {{ return $(id).value; }}
    function intOrNull(v) {{ if (v === "" || v === null || v === undefined) return null; const n = Number(v); return Number.isFinite(n) ? Math.round(n) : null; }}
    function numberOrNull(v) {{ if (v === "" || v === null || v === undefined) return null; const n = Number(v); return Number.isFinite(n) ? n : null; }}
    function requiredInt(id, label, minimum=1, maximum=3600) {{
      const raw = value(id).trim();
      const parsed = Number(raw);
      if (!raw || !Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {{
        throw new Error(label + " трябва да е цяло число между " + minimum + " и " + maximum + ".");
      }}
      return parsed;
    }}
    function requiredNumber(id, label, minimum, maximum) {{
      const raw = value(id).trim();
      const parsed = Number(raw);
      if (!raw || !Number.isFinite(parsed) || parsed < minimum || parsed > maximum) {{
        throw new Error(label + " трябва да е число между " + minimum + " и " + maximum + ".");
      }}
      return parsed;
    }}
    function requiredStringList(id, label) {{
      const items = value(id).split(/[,;\\r\\n]+/).map(item => item.trim()).filter(Boolean);
      if (!items.length) throw new Error(label + " трябва да съдържа поне една стойност.");
      return items;
    }}
    function bool(id) {{ return !!$(id).checked; }}
    function cookieValue(name) {{
      const prefix = name + "=";
      const item = document.cookie.split("; ").find(part => part.startsWith(prefix));
      return item ? decodeURIComponent(item.slice(prefix.length)) : "";
    }}

    async function apiFetch(path, options={{}}) {{
      const request = Object.assign({{cache:"no-store", credentials:"same-origin"}}, options);
      request.headers = Object.assign({{}}, options.headers || {{}});
      const method = String(request.method || "GET").toUpperCase();
      if (!{{GET:1, HEAD:1}}[method]) request.headers["X-CSRF-Token"] = cookieValue("CVRP_WEB_CSRF");
      const response = await fetch(bootstrap.apiBase + path, request);
      if (response.status === 401 || response.status === 403) {{
        window.location.replace(configuredBasePath + "/login");
        throw new Error(response.status === 401 ? "Сесията е изтекла." : "Сесията за сигурност е невалидна.");
      }}
      if (!response.ok) {{
        const text = await response.text();
        try {{ const data = JSON.parse(text); throw new Error(data.error || text); }} catch (err) {{ if (err instanceof SyntaxError) throw new Error(text); throw err; }}
      }}
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

    function put(id, value) {{
      const input = $(id);
      if (!input) return;
      if (input.type === "checkbox") input.checked = !!value;
      else input.value = value ?? "";
    }}

    function renderRunSettings(settings, saved=false) {{
      const cvrp = (settings && settings.cvrp) || {{}};
      const output = (settings && settings.output) || {{}};
      const setData = (settings && settings.set_data) || {{}};
      renderingRunSettings = true;
      put("solverType", cvrp.solver_type || "pyvrp");
      put("objectiveMetric", cvrp.objective_metric || "time");
      put("timeObjectiveIncludeWaiting", cvrp.time_objective_include_waiting !== false);
      put("multiTripEnabled", !!cvrp.enable_multiple_trips);
      put("solverTimeLimit", cvrp.time_limit_seconds || 360);
      put("solverParallelEnabled", !!cvrp.enable_parallel_solving);
      put("solverNumWorkers", cvrp.num_workers ?? -1);
      put("pyvrpSeed", cvrp.pyvrp_seed ?? "");
      put("pyvrpSeedBase", cvrp.pyvrp_seed_base ?? 42);
      put("pyvrpNumNeighbours", cvrp.pyvrp_num_neighbours ?? 50);
      put("pyvrpWeightWaitTime", cvrp.pyvrp_weight_wait_time ?? 0.2);
      put("pyvrpSymmetricProximity", cvrp.pyvrp_symmetric_proximity !== false);
      put("pyvrpIlsNoImprovement", cvrp.pyvrp_ils_no_improvement ?? 150000);
      put("pyvrpIlsHistoryLength", cvrp.pyvrp_ils_history_length ?? 300);
      put("pyvrpExhaustiveOnBest", cvrp.pyvrp_exhaustive_on_best !== false);
      put("pyvrpExtendedOperators", !!cvrp.pyvrp_use_extended_operators);
      put("pyvrpMinPerturbations", cvrp.pyvrp_min_perturbations ?? 1);
      put("pyvrpMaxPerturbations", cvrp.pyvrp_max_perturbations ?? 25);
      put("pyvrpDisplayProgress", !!cvrp.pyvrp_display_progress);
      put("pyvrpDisplayInterval", cvrp.pyvrp_display_interval_seconds ?? 5);
      put("pyvrpLibraryPenalties", cvrp.pyvrp_use_library_penalty_defaults !== false);
      put("pyvrpPenaltyUpdateInterval", cvrp.pyvrp_penalty_solutions_between_updates ?? 500);
      put("pyvrpPenaltyIncrease", cvrp.pyvrp_penalty_increase ?? 1.5);
      put("pyvrpPenaltyDecrease", cvrp.pyvrp_penalty_decrease ?? 0.9);
      put("pyvrpPenaltyTarget", cvrp.pyvrp_penalty_target_feasible ?? 0.65);
      put("pyvrpPenaltyTolerance", cvrp.pyvrp_penalty_feas_tolerance ?? 0.05);
      put("pyvrpPenaltyMin", cvrp.pyvrp_penalty_min ?? 0.1);
      put("pyvrpPenaltyMax", cvrp.pyvrp_penalty_max ?? 100000);
      put("pyvrpNextTimeout", cvrp.pyvrp_next_worker_timeout_seconds ?? 0);
      put("pyvrpNextFallback", !!cvrp.pyvrp_next_fallback_to_stable);
      put("orFirstSolution", cvrp.first_solution_strategy || "PARALLEL_CHEAPEST_INSERTION");
      put("orMetaheuristic", cvrp.local_search_metaheuristic || "GUIDED_LOCAL_SEARCH");
      put("orLnsTimeLimit", cvrp.lns_time_limit_seconds ?? 1.5);
      put("orLnsNodes", cvrp.lns_num_nodes ?? 160);
      put("orLnsArcs", cvrp.lns_num_arcs ?? 220);
      put("orFullPropagation", !!cvrp.use_full_propagation);
      put("orLambda", cvrp.search_lambda_coefficient ?? 0.7);
      put("orLogSearch", !!cvrp.log_search);
      put("orStartTracking", cvrp.enable_start_time_tracking !== false);
      put("orGlobalStart", cvrp.global_start_time_minutes ?? 480);
      put("orParallelFirstStrategies", (cvrp.parallel_first_solution_strategies || []).join("\\n"));
      put("orParallelMetaheuristics", (cvrp.parallel_local_search_metaheuristics || []).join("\\n"));
      put("vroomWorkerTimeout", cvrp.vroom_worker_timeout_seconds ?? 0);
      put("vroomThreads", cvrp.vroom_threads ?? 0);
      put("vroomExploration", cvrp.vroom_exploration_level ?? 5);
      put("vrpWorkerTimeout", cvrp.vrp_worker_timeout_seconds ?? 0);
      put("vrpThreads", cvrp.vrp_threads ?? 0);
      put("vrpMaxGenerations", cvrp.vrp_max_generations ?? 1000000);
      put("vrpLogProgress", cvrp.vrp_log_progress !== false);
      put("outMapEnabled", output.enable_interactive_map);
      put("outExcelEnabled", output.enable_excel_output);
      put("outCsvEnabled", output.enable_csv_output);
      put("outChartsEnabled", output.enable_charts);
      put("outMapFile", output.map_output_file);
      put("outRoutesDir", output.routes_output_dir);
      put("outExcelDir", output.excel_output_dir);
      put("outCsvFile", output.csv_output_file);
      put("outChartsDir", output.charts_output_dir);
      put("outMapProvider", output.map_provider || "osm");
      put("outFoliumTiles", output.folium_tiles);
      put("outUploadMode", output.route_maps_upload_mode || "disabled");
      put("outUploadUrl", output.route_maps_upload_url);
      put("outUploadTimeout", output.route_maps_upload_timeout_seconds);
      put("outUploadToken", "");
      put("outClearUploadToken", false);
      put("outUploadTokenField", output.route_maps_upload_token_field);
      put("outUploadFileField", output.route_maps_upload_file_field);
      put("outUploadBusField", output.route_maps_upload_bus_id_field);
      put("outWarehouseExcel", output.warehouse_excel_file);
      put("outRoutesExcel", output.routes_excel_file);
      put("outEfficiencyExcel", output.efficiency_excel_file);
      put("outSaturdayBusPrefix", output.saturday_excel_bus_number_prefix);
      put("outSaturdayBusDigits", output.saturday_excel_bus_number_digits);
      $("outUploadTokenState").textContent = output.route_maps_upload_token_configured
        ? "Има запазен token. Остави полето празно, за да го използваш."
        : "Няма запазен Web token; при нужда ще се използва глобалният token.";

      put("setDataEnabled", setData.enable_set_data_upload);
      put("setDataUnserved", setData.enable_unserved_set_data_upload);
      put("setDataMakeGroup", setData.enable_make_group);
      put("setDataUrl", setData.set_data_url);
      put("setDataMethod", setData.set_data_http_method || "GET");
      put("setDataTimeout", setData.set_data_timeout_seconds);
      put("setDataCommand", setData.set_data_command);
      put("setDataDoneFlag", setData.set_data_done_flag);
      put("setDataMainSkld", setData.set_data_id_skld);
      put("setDataVratzaSkld", setData.set_data_vratza_id_skld);
      put("setDataDepotMap", setData.set_data_depot_id_skld_map);
      put("setDataIdGrafik", setData.set_data_id_grafik);
      put("setDataIdGrafikTemplate", setData.set_data_id_grafik_template);
      put("setDataBukvaTemplate", setData.set_data_bukva_template);
      put("setDataUnservedDoneFlag", setData.set_data_unserved_done_flag);
      put("setDataUnservedGrafik", setData.set_data_unserved_id_grafik);
      put("setDataUnservedGrafikTemplate", setData.set_data_unserved_id_grafik_template);
      put("setDataUnservedBukvaTemplate", setData.set_data_unserved_bukva_template);
      put("setDataMakeGroupCommand", setData.set_data_make_group_command);
      updateTimeObjectiveFieldState();
      updateSolverFinePanels();
      updatePyvrpPenaltyFields();
      updateVehicleTripFields();
      updateRunFieldStates();
      renderingRunSettings = false;
      runSettingsDirty = false;
      setRunSettingsMessage("Заредени са текущите глобални стойности.", "ok");
    }}

    function setRunSettingsMessage(text, kind="") {{
      const target = $("runSettingsStatus");
      target.textContent = text;
      target.className = "form-status" + (kind ? " " + kind : "");
    }}

    function setDisabled(ids, disabled) {{ ids.forEach(id => {{ if ($(id)) $(id).disabled = disabled; }}); }}

    function updateSolverFinePanels() {{
      const selected = value("solverType");
      document.querySelectorAll("[data-solvers]").forEach(panel => {{
        const solvers = String(panel.dataset.solvers || "").split(",").map(item => item.trim());
        panel.hidden = !solvers.includes(selected);
      }});
    }}

    function updatePyvrpPenaltyFields() {{
      setDisabled([
        "pyvrpPenaltyUpdateInterval", "pyvrpPenaltyIncrease", "pyvrpPenaltyDecrease",
        "pyvrpPenaltyTarget", "pyvrpPenaltyTolerance", "pyvrpPenaltyMin", "pyvrpPenaltyMax"
      ], bool("pyvrpLibraryPenalties"));
    }}

    function updateRunFieldStates() {{
      const vroomSelected = value("solverType") === "vroom";
      updateSolverFinePanels();
      updatePyvrpPenaltyFields();
      $("multiTripEnabled").title = vroomSelected
        ? "VROOM 1.15 не поддържа свързани повторни курсове. Изключи опцията преди старт."
        : "Разрешава връщане и презареждане според възможностите на избрания solver.";
      setDisabled(["outMapFile", "outRoutesDir", "outMapProvider", "outFoliumTiles", "outUploadMode"], !bool("outMapEnabled"));
      setDisabled(["outExcelDir", "outWarehouseExcel", "outRoutesExcel", "outEfficiencyExcel", "outSaturdayBusPrefix", "outSaturdayBusDigits"], !bool("outExcelEnabled"));
      setDisabled(["outCsvFile"], !bool("outCsvEnabled"));
      setDisabled(["outChartsDir"], !bool("outChartsEnabled"));
      const uploadEnabled = bool("outMapEnabled") && value("outUploadMode") === "effect_upload";
      setDisabled(["outUploadUrl", "outUploadTimeout", "outUploadToken", "outClearUploadToken", "outUploadTokenField", "outUploadFileField", "outUploadBusField"], !uploadEnabled);
      const setDataEnabled = bool("setDataEnabled");
      setDisabled([
        "setDataUnserved", "setDataMakeGroup", "setDataUrl", "setDataMethod", "setDataTimeout", "setDataCommand",
        "setDataDoneFlag", "setDataMainSkld", "setDataVratzaSkld", "setDataDepotMap", "setDataIdGrafik",
        "setDataIdGrafikTemplate", "setDataBukvaTemplate", "setDataUnservedDoneFlag", "setDataUnservedGrafik",
        "setDataUnservedGrafikTemplate", "setDataUnservedBukvaTemplate", "setDataMakeGroupCommand"
      ], !setDataEnabled);
    }}

    function collectOutputSettings() {{
      const output = {{
        enable_interactive_map: bool("outMapEnabled"), map_output_file:value("outMapFile"), routes_output_dir:value("outRoutesDir"),
        enable_excel_output:bool("outExcelEnabled"), excel_output_dir:value("outExcelDir"),
        warehouse_excel_file:value("outWarehouseExcel"), routes_excel_file:value("outRoutesExcel"), efficiency_excel_file:value("outEfficiencyExcel"),
        saturday_excel_bus_number_prefix:value("outSaturdayBusPrefix"), saturday_excel_bus_number_digits:requiredInt("outSaturdayBusDigits", "Цифри за събота", 1, 9),
        enable_csv_output:bool("outCsvEnabled"), csv_output_file:value("outCsvFile"),
        enable_charts:bool("outChartsEnabled"), charts_output_dir:value("outChartsDir"),
        map_provider:value("outMapProvider"), folium_tiles:value("outFoliumTiles"),
        route_maps_upload_mode:value("outUploadMode"), route_maps_upload_url:value("outUploadUrl"),
        route_maps_upload_timeout_seconds:requiredInt("outUploadTimeout", "Upload timeout"), route_maps_upload_token_field:value("outUploadTokenField"),
        route_maps_upload_file_field:value("outUploadFileField"), route_maps_upload_bus_id_field:value("outUploadBusField")
      }};
      if (value("outUploadToken") !== "") output.route_maps_upload_token = value("outUploadToken");
      return output;
    }}

    function collectSetDataSettings() {{
      return {{
        enable_set_data_upload:bool("setDataEnabled"), set_data_url:value("setDataUrl"), set_data_http_method:value("setDataMethod"),
        set_data_command:value("setDataCommand"), set_data_done_flag:value("setDataDoneFlag"),
        set_data_id_skld:value("setDataMainSkld"), set_data_vratza_id_skld:value("setDataVratzaSkld"),
        set_data_depot_id_skld_map:value("setDataDepotMap"), set_data_id_grafik:value("setDataIdGrafik"),
        set_data_id_grafik_template:value("setDataIdGrafikTemplate"), set_data_bukva_template:value("setDataBukvaTemplate"),
        enable_unserved_set_data_upload:bool("setDataUnserved"), set_data_unserved_done_flag:value("setDataUnservedDoneFlag"),
        set_data_unserved_id_grafik:value("setDataUnservedGrafik"), set_data_unserved_id_grafik_template:value("setDataUnservedGrafikTemplate"),
        set_data_unserved_bukva_template:value("setDataUnservedBukvaTemplate"), enable_make_group:bool("setDataMakeGroup"),
        set_data_make_group_command:value("setDataMakeGroupCommand"), set_data_timeout_seconds:requiredInt("setDataTimeout", "setData timeout")
      }};
    }}

    function collectCvrpSettings() {{
      const pyvrpSeedRaw = value("pyvrpSeed").trim();
      const settings = {{
        solver_type:value("solverType"),
        objective_metric:value("objectiveMetric"),
        time_objective_include_waiting:bool("timeObjectiveIncludeWaiting"),
        enable_multiple_trips:bool("multiTripEnabled"),
        time_limit_seconds:requiredInt("solverTimeLimit", "Solver time limit"),
        enable_parallel_solving:bool("solverParallelEnabled"),
        num_workers:requiredInt("solverNumWorkers", "Брой solver workers", -1, 128),
        parallel_first_solution_strategies:requiredStringList("orParallelFirstStrategies", "Паралелни First solution стратегии"),
        parallel_local_search_metaheuristics:requiredStringList("orParallelMetaheuristics", "Паралелни метаевристики"),
        pyvrp_seed:pyvrpSeedRaw === "" ? null : requiredInt("pyvrpSeed", "PyVRP seed", -2147483648, 2147483647),
        pyvrp_seed_base:requiredInt("pyvrpSeedBase", "PyVRP seed база", -2147483648, 2147483647),
        pyvrp_num_neighbours:requiredInt("pyvrpNumNeighbours", "PyVRP съседи", 1, 10000),
        pyvrp_weight_wait_time:requiredNumber("pyvrpWeightWaitTime", "PyVRP тежест на чакането", 0, 1000),
        pyvrp_symmetric_proximity:bool("pyvrpSymmetricProximity"),
        pyvrp_ils_no_improvement:requiredInt("pyvrpIlsNoImprovement", "PyVRP ILS без подобрение", 0, 1000000000),
        pyvrp_ils_history_length:requiredInt("pyvrpIlsHistoryLength", "PyVRP ILS история", 1, 10000000),
        pyvrp_exhaustive_on_best:bool("pyvrpExhaustiveOnBest"),
        pyvrp_use_extended_operators:bool("pyvrpExtendedOperators"),
        pyvrp_min_perturbations:requiredInt("pyvrpMinPerturbations", "PyVRP минимални perturbations", 1, 1000000),
        pyvrp_max_perturbations:requiredInt("pyvrpMaxPerturbations", "PyVRP максимални perturbations", 1, 1000000),
        pyvrp_display_progress:bool("pyvrpDisplayProgress"),
        pyvrp_display_interval_seconds:requiredNumber("pyvrpDisplayInterval", "PyVRP progress интервал", 0.1, 3600),
        pyvrp_use_library_penalty_defaults:bool("pyvrpLibraryPenalties"),
        pyvrp_penalty_solutions_between_updates:requiredInt("pyvrpPenaltyUpdateInterval", "PyVRP penalty update интервал", 1, 1000000000),
        pyvrp_penalty_increase:requiredNumber("pyvrpPenaltyIncrease", "PyVRP penalty увеличение", 1, 1000),
        pyvrp_penalty_decrease:requiredNumber("pyvrpPenaltyDecrease", "PyVRP penalty намаление", 0, 1),
        pyvrp_penalty_target_feasible:requiredNumber("pyvrpPenaltyTarget", "PyVRP target feasible", 0, 1),
        pyvrp_penalty_feas_tolerance:requiredNumber("pyvrpPenaltyTolerance", "PyVRP feasible tolerance", 0, 1),
        pyvrp_penalty_min:requiredNumber("pyvrpPenaltyMin", "PyVRP минимална penalty", 0, 1000000000),
        pyvrp_penalty_max:requiredNumber("pyvrpPenaltyMax", "PyVRP максимална penalty", 0, 1000000000),
        pyvrp_next_worker_timeout_seconds:requiredInt("pyvrpNextTimeout", "PyVRP 0.14 worker timeout", 0, 86400),
        pyvrp_next_fallback_to_stable:bool("pyvrpNextFallback"),
        first_solution_strategy:value("orFirstSolution"),
        local_search_metaheuristic:value("orMetaheuristic"),
        lns_time_limit_seconds:requiredNumber("orLnsTimeLimit", "OR-Tools LNS лимит", 0, 86400),
        lns_num_nodes:requiredInt("orLnsNodes", "OR-Tools LNS възли", 1, 1000000),
        lns_num_arcs:requiredInt("orLnsArcs", "OR-Tools LNS ребра", 1, 1000000),
        use_full_propagation:bool("orFullPropagation"),
        search_lambda_coefficient:requiredNumber("orLambda", "OR-Tools GLS lambda", 0, 1000),
        log_search:bool("orLogSearch"),
        enable_start_time_tracking:bool("orStartTracking"),
        global_start_time_minutes:requiredInt("orGlobalStart", "Глобален старт", 0, 1439),
        vroom_worker_timeout_seconds:requiredInt("vroomWorkerTimeout", "VROOM timeout", 0, 86400),
        vroom_threads:requiredInt("vroomThreads", "VROOM threads", 0, 256),
        vroom_exploration_level:requiredInt("vroomExploration", "VROOM exploration", 0, 5),
        vrp_worker_timeout_seconds:requiredInt("vrpWorkerTimeout", "VRP-Rust timeout", 0, 86400),
        vrp_threads:requiredInt("vrpThreads", "VRP-Rust threads", 0, 256),
        vrp_max_generations:requiredInt("vrpMaxGenerations", "VRP-Rust maximum generations", 1, 1000000000000),
        vrp_log_progress:bool("vrpLogProgress")
      }};
      if (settings.num_workers === 0) throw new Error("Брой solver workers трябва да бъде -1 или положително число.");
      if (settings.pyvrp_min_perturbations > settings.pyvrp_max_perturbations) throw new Error("PyVRP максималните perturbations трябва да са поне колкото минималните.");
      if (settings.pyvrp_penalty_min > settings.pyvrp_penalty_max) throw new Error("PyVRP максималната penalty трябва да е поне колкото минималната.");
      return settings;
    }}

    function collectRunSettings() {{
      return {{cvrp:collectCvrpSettings(), output:collectOutputSettings(), set_data:collectSetDataSettings()}};
    }}

    function applyCommonState(data) {{
      state = data;
      vehicleTypes = data.vehicle_types || vehicleTypes;
      depotOptions = data.depots || [];
      $("pageTitle").textContent = data.app_name || bootstrap.title;
      $("serverLine").textContent = (data.web_gui && data.web_gui.url ? data.web_gui.url : "") + " | users: " + ((data.web_gui && data.web_gui.users || []).join(", ") || "-");
      setStatus(data.run_status || {{}});
    }}

    function renderVehicleState(data) {{
      applyCommonState(data);
      renderVehicles((data.vehicles || []).map((v, i) => Object.assign({{_original_index:i}}, v)));
      updateVehicleTripFields();
    }}

    function renderConfig(data) {{
      renderVehicleState(data);
      globalRunSettings = data.web_run_global_settings || null;
      renderRunSettings(data.web_run_global_settings || data.web_run_settings || {{}}, false);
    }}

    function renderVehicles(vehicles) {{
      const tbody = $("vehiclesBody");
      tbody.innerHTML = "";
      vehicles.forEach((vehicle, index) => tbody.appendChild(vehicleRow(vehicle, index)));
      renumberVehicleCards();
      vehicleSettingsDirty = false;
      setVehicleSettingsMessage("Заредени са " + vehicles.length + " конфигурации на превозни средства.", "ok");
    }}

    function setVehicleSettingsMessage(text, kind="") {{
      const target = $("vehicleSettingsStatus");
      target.textContent = text;
      target.className = "form-status" + (kind ? " " + kind : "");
    }}

    function markVehicleSettingsDirty() {{
      vehicleSettingsDirty = true;
      setVehicleSettingsMessage("Има незаписани промени по бусовете. Те не участват в „Запази глобално настройките“.");
    }}

    function updateVehicleTripFields() {{
      const disabled = !bool("multiTripEnabled");
      for (const row of $("vehiclesBody").querySelectorAll(".vehicle-row")) {{
        for (const field of ["reload_location", "reload_time_minutes"]) {{
          const input = row.querySelector(`[data-field="${{field}}"]`);
          if (input) input.disabled = disabled;
        }}
      }}
    }}

    function updateTimeObjectiveFieldState() {{
      const checkbox = $("timeObjectiveIncludeWaiting");
      const vroomSelected = value("solverType") === "vroom";
      checkbox.disabled = value("objectiveMetric") !== "time" || vroomSelected;
      checkbox.title = vroomSelected
        ? "VROOM отчита пълното време във fitness и спазва чакането, но native objective-ът му не може директно да цени waiting time."
        : (checkbox.disabled
          ? "Опцията важи само при цел time."
          : "Включва пътуване, обслужване, чакане и презареждане; fitness е само времето в секунди, а реалните метри остават за ограничения и отчети.");
    }}

    function vehicleRow(vehicle, index) {{
      const row = document.createElement("div");
      row.className = "vehicle-row";
      row.dataset.index = index;
      row.dataset.originalIndex = vehicle._original_index ?? "";
      row.innerHTML = `
        <div class="vehicle-card-header">
          <div class="vehicle-card-identity">
            <span class="vehicle-number"></span>
            <strong class="vehicle-card-title"></strong>
            <span class="vehicle-type-badge"></span>
          </div>
          <button class="danger" type="button" onclick="removeVehicle(this)">Изтрий този бус</button>
        </div>
        <div class="vehicle-field type"><label>Тип</label><select data-field="vehicle_type">${{vehicleTypes.map(t => `<option value="${{t}}">${{t}}</option>`).join("")}}</select></div>
        <div class="vehicle-field name"><label>Име</label><input data-field="name"></div>
        <div class="vehicle-field name"><label>Стабилно ID</label><input data-field="config_id"></div>
        <div class="vehicle-field checkbox active"><input data-field="enabled" type="checkbox"><label>Активен</label></div>
        <div class="vehicle-field small"><label>Брой</label><input data-field="count" type="number" min="0"></div>
        <div class="vehicle-field medium"><label>Капацитет</label><input data-field="capacity" type="number" min="0"></div>
        <div class="vehicle-field medium"><label>Цена</label><input data-field="fixed_cost" type="number"></div>
        <div class="vehicle-field medium"><label>Макс. часове</label><input data-field="max_time_hours" type="number"></div>
        <div class="vehicle-field medium"><label>Макс. км/ден</label><input data-field="max_distance_km" type="number" min="0" placeholder="без лимит"></div>
        <div class="vehicle-field service"><label>Клиенти/ден</label><input data-field="max_customers_per_day" type="number" min="1" placeholder="без лимит"></div>
        <div class="vehicle-field service"><label>Обслужване</label><input data-field="service_time_minutes" type="number"></div>
        <div class="vehicle-field medium"><label>Старт (мин)</label><input data-field="start_time_minutes" type="number" min="0"></div>
        <div class="vehicle-field gps"><label>Начално депо</label><select data-field="start_depot_name"></select></div>
        <div class="vehicle-field gps"><label>Край GPS</label><input data-field="end_location" placeholder="празно = депо"></div>
        <div class="vehicle-field gps"><label>Презареждане GPS</label><input data-field="reload_location" placeholder="празно = стартово депо"></div>
        <div class="vehicle-field service"><label>Презареждане (мин)</label><input data-field="reload_time_minutes" type="number" min="0"></div>
      `;
      const startDepotSelect = row.querySelector('[data-field="start_depot_name"]');
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "-- избери депо --";
      placeholder.disabled = true;
      startDepotSelect.appendChild(placeholder);
      depotOptions.forEach(depot => {{
        const option = document.createElement("option");
        option.value = depot.name;
        option.textContent = depot.name;
        option.title = depot.coordinates || "";
        startDepotSelect.appendChild(option);
      }});
      if (vehicle.start_depot_name && !depotOptions.some(depot => depot.name === vehicle.start_depot_name)) {{
        const missing = document.createElement("option");
        missing.value = vehicle.start_depot_name;
        missing.textContent = vehicle.start_depot_name + " (липсващо депо)";
        missing.disabled = true;
        startDepotSelect.appendChild(missing);
      }}
      for (const [key, val] of Object.entries(vehicle)) {{
        const input = row.querySelector(`[data-field="${{key}}"]`);
        if (!input) continue;
        if (input.type === "checkbox") input.checked = !!val;
        else input.value = val ?? "";
      }}
      for (const fieldName of ["name", "vehicle_type", "enabled"]) {{
        const input = row.querySelector(`[data-field="${{fieldName}}"]`);
        if (input) input.addEventListener("change", () => updateVehicleCard(row));
        if (input && input.type !== "checkbox") input.addEventListener("input", () => updateVehicleCard(row));
      }}
      row.addEventListener("input", markVehicleSettingsDirty);
      row.addEventListener("change", markVehicleSettingsDirty);
      updateVehicleCard(row);
      return row;
    }}

    function updateVehicleCard(row) {{
      const nameInput = row.querySelector('[data-field="name"]');
      const typeInput = row.querySelector('[data-field="vehicle_type"]');
      const enabledInput = row.querySelector('[data-field="enabled"]');
      row.querySelector(".vehicle-card-title").textContent = (nameInput && nameInput.value.trim()) || "Без име";
      row.querySelector(".vehicle-type-badge").textContent = (typeInput && typeInput.value) || "vehicle";
      row.classList.toggle("inactive", !!enabledInput && !enabledInput.checked);
    }}

    function renumberVehicleCards() {{
      Array.from($("vehiclesBody").querySelectorAll(".vehicle-row")).forEach((row, index) => {{
        row.dataset.index = index;
        row.querySelector(".vehicle-number").textContent = "Бус " + (index + 1);
        updateVehicleCard(row);
      }});
    }}

    function removeVehicle(button) {{
      const row = button.closest(".vehicle-row");
      const label = row.querySelector(".vehicle-card-title").textContent || "този бус";
      if (!confirm(`Да изтрия ли ${{label}} от списъка? Промяната се записва едва след „Запази само бусовете глобално“. `)) return;
      row.remove();
      renumberVehicleCards();
      markVehicleSettingsDirty();
    }}

    function addVehicle() {{
      const tbody = $("vehiclesBody");
      tbody.appendChild(vehicleRow({{
        vehicle_type:"internal_bus", name:"", config_id:"vehicle_" + Date.now(), enabled:true, count:1, capacity:320, fixed_cost:0,
        max_time_hours:8, max_distance_km:null, max_customers_per_day:null, service_time_minutes:8, start_time_minutes:480,
        start_depot_name:(depotOptions[0] && depotOptions[0].name) || "", end_location:"", reload_location:"", reload_time_minutes:30, _original_index:""
      }}, tbody.children.length));
      renumberVehicleCards();
      updateVehicleTripFields();
      markVehicleSettingsDirty();
    }}

    function collectVehicles() {{
      return Array.from($("vehiclesBody").querySelectorAll(".vehicle-row")).map(row => {{
        const get = name => row.querySelector(`[data-field="${{name}}"]`);
        return {{
          _original_index: row.dataset.originalIndex,
          vehicle_type: get("vehicle_type").value,
          name: get("name").value,
          config_id: get("config_id").value.trim(),
          enabled: get("enabled").checked,
          count: intOrNull(get("count").value) || 0,
          capacity: intOrNull(get("capacity").value) || 0,
          fixed_cost: intOrNull(get("fixed_cost").value) || 0,
          max_time_hours: intOrNull(get("max_time_hours").value) || 8,
          max_distance_km: intOrNull(get("max_distance_km").value),
          max_customers_per_day: intOrNull(get("max_customers_per_day").value),
          service_time_minutes: intOrNull(get("service_time_minutes").value) || 8,
          start_time_minutes: intOrNull(get("start_time_minutes").value) || 480,
          start_depot_name: get("start_depot_name").value,
          end_location: get("end_location").value || null,
          reload_location: get("reload_location").value || get("start_depot_name").value,
          reload_time_minutes: intOrNull(get("reload_time_minutes").value) ?? 30,
        }};
      }});
    }}

    function collectConfigPayload() {{
      const settings = collectRunSettings();
      settings.clear_upload_token = bool("outClearUploadToken");
      return settings;
    }}

    function collectVehiclePayload() {{
      return {{vehicles: collectVehicles()}};
    }}

    async function loadAll() {{
      const dirtyParts = [];
      if (runSettingsDirty) dirtyParts.push("настройките за решаване/изпълнение");
      if (vehicleSettingsDirty) dirtyParts.push("бусовете");
      if (state && dirtyParts.length && !confirm("Има незаписани промени в " + dirtyParts.join(" и ") + ". Да ги отхвърля и да обновя всичко?")) return;
      const data = await apiFetch("/config");
      renderConfig(data);
      await loadLogs(false);
    }}

    async function saveGlobalSettings() {{
      if (!confirm("Да запиша ли всички показани настройки глобално в config.py? Те ще важат за Desktop GUI, Web и външния API /run. Списъкът с бусове няма да бъде променен.")) return;
      try {{
        const result = await apiFetch("/config", {{
          method:"POST",
          headers:{{"Content-Type":"application/json"}},
          body:JSON.stringify(collectConfigPayload())
        }});
        $("lastSaved").textContent = "Глобалните настройки са запазени: " + new Date().toLocaleTimeString() + " | backup: " + (result.backup_file || "");
        if (result.config) {{
          applyCommonState(result.config);
          globalRunSettings = result.config.web_run_global_settings || null;
          renderRunSettings(globalRunSettings || result.config.web_run_settings || {{}}, false);
        }}
        setRunSettingsMessage("Настройките са записани глобално в config.py и вече важат навсякъде. Незапазените промени по бусовете са запазени във формата.", "ok");
        await loadLogs(false);
      }} catch (err) {{ setRunSettingsMessage(String(err.message || err), "bad"); }}
    }}

    async function saveConfig() {{ return saveGlobalSettings(); }}

    async function saveVehicles() {{
      if (!confirm("Да запиша ли само списъка с бусове в config.py? Solver-ът и всички други настройки няма да бъдат променени.")) return;
      try {{
        const result = await apiFetch("/vehicles", {{
          method:"POST",
          headers:{{"Content-Type":"application/json"}},
          body:JSON.stringify(collectVehiclePayload())
        }});
        $("lastSaved").textContent = "Бусовете са запазени отделно: " + new Date().toLocaleTimeString() + " | backup: " + (result.backup_file || "");
        if (result.config) renderVehicleState(result.config);
        setVehicleSettingsMessage("Бусовете са записани глобално. Незапазените настройки за текущия рън са запазени във формата.", "ok");
        await loadLogs(false);
      }} catch (err) {{ setVehicleSettingsMessage(String(err.message || err), "bad"); }}
    }}

    async function startRun() {{
      try {{
        const runSettings = collectRunSettings();
        if (runSettings.set_data.enable_set_data_upload && !confirm(
          "setData е ВКЛЮЧЕНО. Този рън ще изпраща реални промени към външната система. Да стартирам ли?"
        )) return;
        setRunSettingsMessage("Стартиране на временен Web рън без запис в config.py…");
        const result = await apiFetch("/run", {{
          method:"POST", headers:{{"Content-Type":"application/json"}},
          body:JSON.stringify({{run_settings:runSettings}})
        }});
        setStatus(result.run || {{}});
        setLogsLive(true);
        setRunSettingsMessage("Рънът е стартиран. Тези настройки важат само за него; config.py не е променен.", "ok");
        await loadLogs();
      }} catch (err) {{ setRunSettingsMessage(String(err.message || err), "bad"); }}
    }}

    function useGlobalRunSettings() {{
      if (!globalRunSettings) return;
      renderRunSettings(globalRunSettings, false);
      runSettingsDirty = false;
      setRunSettingsMessage("Промените са отхвърлени и глобалните стойности са заредени отново.", "ok");
    }}

    async function logout() {{
      try {{ await apiFetch("/logout", {{method:"POST"}}); }} catch (_) {{}}
      window.location.replace(pagePath + "/login");
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

    for (const eventName of ["input", "change"]) {{
      $("runSettingsSection").addEventListener(eventName, () => {{
        updateRunFieldStates();
        if (!renderingRunSettings) {{
          runSettingsDirty = true;
          setRunSettingsMessage("Променена форма — още не е записана. Избери временен старт или глобален запис.");
        }}
      }});
    }}
    $("multiTripEnabled").addEventListener("change", updateVehicleTripFields);
    $("objectiveMetric").addEventListener("change", updateTimeObjectiveFieldState);
    $("solverType").addEventListener("change", () => {{ updateRunFieldStates(); updateTimeObjectiveFieldState(); }});

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
    summary = {
        "status": result.get("status", "ok"),
        "execution_time_seconds": result.get("execution_time_seconds"),
        "routes_count": result.get("routes_count"),
        "total_trips": result.get("total_trips", result.get("routes_count")),
        "second_trips_count": result.get("second_trips_count", 0),
        "total_vehicles_used": result.get("total_vehicles_used"),
        "dropped_customers_count": result.get("dropped_customers_count"),
        "total_distance_km": result.get("total_distance_km"),
        "total_time_minutes": result.get("total_time_minutes"),
        "output_files": result.get("output_files", {}),
    }
    for key in (
        "solver_requested",
        "solver_used",
        "solver_backend",
        "solver_version",
        "solver_fallback_used",
        "solver_fallback_reason",
    ):
        if key in result:
            summary[key] = deepcopy(result.get(key))
    set_data_result = result.get("set_data")
    if isinstance(set_data_result, dict):
        summary["set_data"] = {
            key: deepcopy(set_data_result.get(key))
            for key in ("enabled", "attempted", "succeeded", "failed", "make_group", "errors")
            if key in set_data_result
        }
    return summary


def _run_status_snapshot() -> Dict[str, Any]:
    with _RUN_LOCK:
        return deepcopy(_RUN_STATUS)


def _api_health_payload(api_config, host: str, port: int) -> Dict[str, Any]:
    public_url = _build_public_base_url(api_config, host, port)
    api_endpoint = _normalise_endpoint(getattr(api_config, "api_endpoint", "/solve"))
    trigger_endpoint = _normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run"))
    saturday_trigger_endpoint = _normalise_endpoint(
        getattr(api_config, "saturday_trigger_endpoint", "/run_saturday")
    )
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
        "run_saturday": f"{public_url}{saturday_trigger_endpoint}",
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
            "run_saturday": {
                "methods": ["GET", "POST"],
                "description": "Съботен run с автоматична дата и отделен префикс за бусове.",
                "single_active_run": True,
                "bus_number_prefix": getattr(get_config().output, "saturday_excel_bus_number_prefix", ""),
                "bus_number_digits": getattr(get_config().output, "saturday_excel_bus_number_digits", 1),
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
            saturday_trigger_endpoint,
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
        "saturday_trigger_url": endpoints["run_saturday"],
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
                cwd=_web_auth_base_dir(),
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


def _web_run_subprocess_worker(run_id: str, worker_request: Dict[str, Any]) -> None:
    """Run one Web GUI job in a child process with an isolated config snapshot."""
    logs_dir = _api_logs_dir()
    input_path = os.path.join(logs_dir, f"web_run_{run_id}_input.json")
    output_path = os.path.join(logs_dir, f"web_run_{run_id}_result.json")
    stdout_path = os.path.join(logs_dir, f"api_run_{run_id}.log")
    command = _web_run_worker_command(input_path, output_path)
    process = None
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("_MEIPASS2", None)

    try:
        with open(input_path, "w", encoding="utf-8") as file_handle:
            json.dump(worker_request, file_handle, ensure_ascii=False, indent=2)

        logger.info("Web GUI run %s starting isolated worker", run_id)
        with open(stdout_path, "a", encoding="utf-8", errors="replace") as stdout_log:
            stdout_log.write(f"\n===== Web GUI run {run_id} started {_now_iso()} =====\n")
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
                _terminate_process_tree(process.pid)
            exit_code = process.wait()
            stdout_log.write(f"===== Web GUI run {run_id} finished {_now_iso()} exit_code={exit_code} =====\n")

        worker_result: Dict[str, Any] = {}
        if os.path.exists(output_path):
            with open(output_path, "r", encoding="utf-8") as file_handle:
                loaded_result = json.load(file_handle)
            if isinstance(loaded_result, dict):
                worker_result = loaded_result

        with _RUN_LOCK:
            stop_requested = bool(_RUN_STATUS.get("stop_requested")) and _RUN_STATUS.get("run_id") == run_id
        worker_error = str(worker_result.get("error") or "").strip()
        success = exit_code == 0 and not stop_requested and not worker_error
        finished_at = _now_iso()
        with _RUN_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "status": "stopped" if stop_requested else ("completed" if success else "failed"),
                    "finished_at": finished_at,
                    "error": (
                        "Run stopped by user request"
                        if stop_requested
                        else (None if success else (worker_error or f"Web run worker exited with code {exit_code}"))
                    ),
                    "result": _summarise_result(worker_result) if success else None,
                    "process_id": None,
                }
            )
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)
        logger.info("Web GUI run %s finished with code %s", run_id, exit_code)
    except Exception as exc:
        logger.exception("Web GUI run %s failed", run_id)
        with _RUN_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "status": "failed",
                    "finished_at": _now_iso(),
                    "error": str(exc),
                    "result": None,
                    "process_id": None,
                }
            )
            status_snapshot = deepcopy(_RUN_STATUS)
        _persist_run_status(status_snapshot)
    finally:
        for sensitive_path in (input_path, output_path):
            try:
                if os.path.exists(sensitive_path):
                    os.remove(sensitive_path)
            except OSError:
                logger.warning("Could not remove temporary Web run file: %s", sensitive_path)


def _cvrp_run_is_active() -> bool:
    with _RUN_LOCK:
        return bool(_RUN_STATUS.get("running"))


def _tsp_worker_command(input_path: str, output_path: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--tsp-worker", input_path, output_path]

    worker_py = os.path.join(os.getcwd(), "tsp_worker.py")
    return [_child_python_executable(), worker_py, input_path, output_path]


def _web_run_worker_command(input_path: str, output_path: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--web-run-worker", input_path, output_path]

    worker_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cvrp_run_worker.py")
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
                "source": "api",
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


def _start_web_run(payload: Any) -> tuple[bool, Dict[str, Any], list[str], list[str]]:
    """Start an isolated Web GUI run without touching the API/global config."""
    effective_config, applied_settings, ignored_settings = _build_web_run_config_override(payload)
    worker_request = {
        "run_settings": _web_run_settings_from_config(effective_config),
        "source": "web_gui",
    }

    with _RUN_LOCK:
        if _RUN_STATUS.get("running"):
            return False, deepcopy(_RUN_STATUS), applied_settings, ignored_settings

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
                "settings_overrides": applied_settings,
                "ignored_settings": ignored_settings,
                "callback_url": None,
                "notification": None,
                "execution_mode": "subprocess",
                "process_id": None,
                "process_log": None,
                "stop_requested": False,
                "source": "web_gui",
            }
        )
        status_snapshot = deepcopy(_RUN_STATUS)
    _persist_run_status(status_snapshot)

    thread = threading.Thread(
        target=_web_run_subprocess_worker,
        args=(run_id, worker_request),
        daemon=False,
        name=f"web-cvrp-run-{run_id}",
    )
    thread.start()
    return True, _run_status_snapshot(), applied_settings, ignored_settings


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
                "source": "api",
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


class _RequestBodyTooLarge(ValueError):
    pass


class CVRPApiHandler(BaseHTTPRequestHandler):
    server_version = "CVRPApi/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(_CLIENT_SOCKET_TIMEOUT_SECONDS)

    def do_GET(self):
        api_config = get_config().api
        parsed = urlparse(self.path)
        path = _normalise_path(parsed.path)
        query = parse_qs(parsed.query)
        health_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "health_endpoint", "/health")))
        trigger_endpoint = _normalise_path(_normalise_endpoint(getattr(api_config, "trigger_endpoint", "/run")))
        saturday_trigger_endpoint = _normalise_path(_normalise_endpoint(
            getattr(api_config, "saturday_trigger_endpoint", "/run_saturday")
        ))
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

        if path == saturday_trigger_endpoint:
            if (error := _auth_error(api_config, query, self.headers)):
                self._send_json(401, {"status": "error", "error": error})
                return
            self._handle_trigger_run(query=query, allow_inline_result=False, saturday_run=True)
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
        saturday_trigger_endpoint = _normalise_path(_normalise_endpoint(
            getattr(api_config, "saturday_trigger_endpoint", "/run_saturday")
        ))
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

            if path == saturday_trigger_endpoint:
                if (error := _auth_error(api_config, query, self.headers)):
                    self._send_json(401, {"status": "error", "error": error})
                    return
                self._handle_trigger_run(
                    query=query,
                    payload=payload,
                    allow_inline_result=True,
                    saturday_run=True,
                )
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
        except _RequestBodyTooLarge as exc:
            logger.warning("Rejected oversized CVRP API request: %s", exc)
            self._send_json(413, {"status": "error", "error": str(exc)})
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
        subpath = _web_gui_subpath(path, api_config)
        host = self.server.server_address[0]
        port = self.server.server_address[1]

        identity = _web_session_identity(api_config, self.headers, require_csrf=False)
        if subpath == "/login":
            if identity is not None:
                self._send_redirect(_web_gui_endpoint(api_config))
            else:
                self._send_html(200, _web_gui_login_html(api_config))
            return
        if identity is None:
            if subpath in {"/", ""}:
                self._send_redirect(f"{_web_gui_endpoint(api_config)}/login")
            else:
                self._send_json(401, {"status": "unauthorized", "error": "Web GUI session required"})
            return

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
        if subpath == "/api/session":
            self._send_json(200, {"status": "ok", "username": identity.username})
            return
        if subpath == "/api/logs":
            self._send_json(200, _web_gui_logs_payload())
            return

        self._send_json(404, {"status": "error", "error": "Unknown web GUI endpoint"})

    def _handle_web_gui_post(self, path: str, query: Dict[str, list[str]], api_config) -> None:
        subpath = _web_gui_subpath(path, api_config)
        if subpath == "/api/login":
            if not _web_request_origin_is_valid(self.headers):
                self._send_json(403, {"status": "forbidden", "error": "Invalid request origin"})
                return
            media_type = str(self.headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                self._send_json(415, {"status": "error", "error": "Login requires application/json"})
                return
            try:
                payload = self._read_json_body(required=True, max_bytes=_MAX_LOGIN_REQUEST_BODY_BYTES)
                if not isinstance(payload, dict):
                    raise ValueError("Login body must be a JSON object")
                service = _get_web_auth_service(api_config)
                result = service.login(
                    payload.get("username"),
                    payload.get("password"),
                    client_ip=_web_login_client_ip(api_config, self.headers, self.client_address),
                )
                if not result.success or result.session is None:
                    headers = []
                    status = 429 if result.retry_after_seconds else 401
                    if result.retry_after_seconds:
                        headers.append(("Retry-After", str(result.retry_after_seconds)))
                    self._send_json(status, {"status": "unauthorized", "error": GENERIC_AUTH_FAILURE}, extra_headers=headers)
                    return
                self._send_json(
                    200,
                    {"status": "ok", "username": result.session.username, "expires_at": result.session.expires_at},
                    extra_headers=_web_session_cookie_headers(api_config, self.headers, result.session),
                )
            except _RequestBodyTooLarge as exc:
                self._send_json(413, {"status": "error", "error": str(exc)})
            except CredentialStoreError:
                logger.error("Web credential storage is unavailable", exc_info=True)
                self._send_json(503, {
                    "status": "unavailable",
                    "error": "Web login storage is unavailable. Restore the credential file, then use Desktop Settings.",
                })
            except (json.JSONDecodeError, ValueError):
                self._send_json(400, {"status": "error", "error": "Invalid login request"})
            return

        if not _web_request_origin_is_valid(self.headers):
            self._send_json(403, {"status": "forbidden", "error": "Invalid request origin"})
            return
        try:
            service = _get_web_auth_service(api_config)
        except CredentialStoreError:
            logger.error("Web credential storage is unavailable", exc_info=True)
            self._send_json(503, {
                "status": "unavailable",
                "error": "Web login storage is unavailable. Restore the credential file, then use Desktop Settings.",
            })
            return
        session_token = _request_cookie(self.headers, _WEB_SESSION_COOKIE)
        session_identity = service.authenticate_session(session_token)
        if session_identity is None:
            self._send_json(
                401,
                {"status": "unauthorized", "error": "Web GUI session required"},
                extra_headers=_web_session_cookie_headers(api_config, self.headers, clear=True),
            )
            return
        identity = service.authenticate_session(
            session_token,
            csrf_token=str(self.headers.get("X-CSRF-Token", "") or ""),
            require_csrf=True,
        )
        if identity is None:
            service.logout(session_token)
            self._send_json(
                403,
                {"status": "forbidden", "error": "Valid CSRF token required"},
                extra_headers=_web_session_cookie_headers(api_config, self.headers, clear=True),
            )
            return
        if subpath == "/api/logout":
            service.logout(session_token)
            self._send_json(
                200,
                {"status": "ok", "message": "Logged out"},
                extra_headers=_web_session_cookie_headers(api_config, self.headers, clear=True),
            )
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
            if subpath == "/api/run":
                payload = self._read_json_body(required=False, max_bytes=_MAX_WEB_REQUEST_BODY_BYTES) or {}
                started, status, applied, ignored = _start_web_run(payload)
                if started:
                    self._send_json(202, {
                        "status": "started",
                        "message": "CVRP Web run started with isolated current settings.",
                        "scope": "web_gui_run_only",
                        "settings_overrides": applied,
                        "ignored_settings": ignored,
                        "run": status,
                    })
                else:
                    self._send_json(409, {
                        "status": "already_running",
                        "message": "CVRP optimisation is already running.",
                        "run": status,
                    })
                return

            payload = self._read_json_body(required=True, max_bytes=_MAX_WEB_REQUEST_BODY_BYTES)
            if subpath == "/api/config":
                result = _apply_web_gui_config_save(payload)
                host = self.server.server_address[0]
                port = self.server.server_address[1]
                result["config"] = _web_gui_config_payload(host, port)
                self._send_json(200, result)
                return
            if subpath == "/api/vehicles":
                result = _apply_web_gui_vehicles_save(payload)
                host = self.server.server_address[0]
                port = self.server.server_address[1]
                result["config"] = _web_gui_config_payload(host, port)
                self._send_json(200, result)
                return
            if subpath == "/api/run-defaults":
                result = _save_web_run_defaults(payload)
                host = self.server.server_address[0]
                port = self.server.server_address[1]
                result["config"] = _web_gui_config_payload(host, port)
                self._send_json(200, result)
                return

            self._send_json(404, {"status": "error", "error": "Unknown web GUI endpoint"})
        except _RequestBodyTooLarge as exc:
            logger.warning("Rejected oversized Web GUI request: %s", exc)
            self._send_json(413, {"status": "error", "error": str(exc)})
        except json.JSONDecodeError as exc:
            logger.exception("Invalid web GUI JSON request body")
            self._send_json(400, {"status": "error", "error": "Invalid JSON request body", "detail": str(exc)})
        except ValueError as exc:
            logger.warning("Invalid Web GUI request: %s", exc)
            self._send_json(400, {"status": "error", "error": str(exc)})
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
        saturday_run: bool = False,
    ):
        try:
            if saturday_run:
                config_override, applied_settings, ignored_settings = _build_saturday_run_config_override(payload, query)
            else:
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

    def _read_json_body(
        self,
        required: bool = True,
        max_bytes: int = _MAX_API_REQUEST_BODY_BYTES,
    ) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length > max_bytes:
            raise _RequestBodyTooLarge(
                f"Request body is too large ({length} bytes; limit is {max_bytes} bytes)"
            )

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

        return _loads_json_tolerant(body_text)

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
            return _loads_json_tolerant(json_text)

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
        def redact(value: Any) -> Any:
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    key_lower = str(key).lower()
                    if any(marker in key_lower for marker in ("password", "token", "secret", "authorization", "api_key", "web_gui_users")):
                        result[key] = "***"
                    else:
                        result[key] = redact(item)
                return result
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value

        safe_text = body_text
        try:
            safe_text = json.dumps(redact(json.loads(body_text)), ensure_ascii=False, separators=(",", ":"))
        except Exception:
            safe_text = re.sub(
                r'(?i)("(?:password|[^"\\]*(?:token|secret|api_key|web_gui_users)[^"\\]*)"\s*:\s*)"(?:\\.|[^"\\])*"',
                r'\1"***"',
                body_text,
            )
        if len(safe_text) <= _REQUEST_BODY_LOG_LIMIT:
            return safe_text
        omitted = len(safe_text) - _REQUEST_BODY_LOG_LIMIT
        return f"{safe_text[:_REQUEST_BODY_LOG_LIMIT]}\n...<truncated {omitted} chars>"

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

    def _send_security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )

    def _send_json(
        self,
        status_code: int,
        payload: Dict[str, Any],
        extra_headers: Optional[list[tuple[str, str]]] = None,
    ):
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self._send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(
        self,
        status_code: int,
        html_text: str,
        extra_headers: Optional[list[tuple[str, str]]] = None,
    ):
        raw = str(html_text or "").encode("utf-8")
        self.send_response(status_code)
        self._send_security_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def _send_redirect(self, location: str) -> None:
        self.send_response(303)
        self._send_security_headers()
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

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
    saturday_trigger_endpoint = _normalise_endpoint(
        getattr(api_config, "saturday_trigger_endpoint", "/run_saturday")
    )
    tsp_endpoint = _normalise_endpoint(getattr(api_config, "tsp_endpoint", "/tsp"))
    tsp_report_endpoint = _normalise_endpoint(getattr(api_config, "tsp_report_endpoint", "/tsp-report"))
    shutdown_endpoint = _normalise_endpoint(getattr(api_config, "shutdown_endpoint", "/shutdown"))
    logger.info("CVRP API server listening on http://%s:%s", host, port)
    logger.info("Public/base URL: %s", public_url)
    logger.info("POST customer JSON to %s%s", public_url, api_endpoint)
    logger.info("Trigger configured run with GET/POST %s%s", public_url, trigger_endpoint)
    logger.info("Trigger Saturday run with GET/POST %s%s", public_url, saturday_trigger_endpoint)
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
