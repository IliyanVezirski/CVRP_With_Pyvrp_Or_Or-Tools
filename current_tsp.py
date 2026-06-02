"""Current-driver TSP route service.

This module is intentionally separate from the CVRP solvers. It receives one
driver's current GPS position and a list of remaining customers, orders those
customers as an open TSP route, generates the same individual HTML map used by
normal routes, and optionally uploads that map with the driver ID in pData2[].
"""

from __future__ import annotations

import html
import logging
import math
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import MainConfig, RoutingEngine, VehicleConfig, VehicleType, get_config
from cvrp_solver import Route
from input_handler import (
    Customer,
    GPSParser,
    _safe_delivery_comment,
    choose_time_window_for_arrival,
    customer_time_windows_minutes,
    format_time_windows_minutes,
    parse_time_value_to_minutes,
    safe_parse_time_window_values,
    time_windows_to_seconds,
)
from osrm_client import DistanceMatrix, OSRMClient
from output_handler import InteractiveMapGenerator, OutputHandler


logger = logging.getLogger(__name__)

_CUSTOMER_LIST_KEYS = ("customers", "clients", "orders", "data", "items", "records")
_DRIVER_ID_KEYS = ("driver_id", "driverId", "id_driver", "IdDriver", "id_shofior", "IdShofior")
_DRIVER_NAME_KEYS = ("driver_name", "driverName", "name_driver", "DriverName")
_DRIVER_LOCATION_KEYS = ("driver_location", "current_location", "currentLocation", "location", "gps", "GPS")
_END_LOCATION_KEYS = (
    "end_location",
    "endLocation",
    "final_location",
    "finalLocation",
    "finish_location",
    "finishLocation",
    "end_gps",
    "EndGPS",
)
_CUSTOMER_ID_KEYS = ("id", "customer_id", "client_id", "IdCust", "ID", "id_cust")
_CUSTOMER_NAME_KEYS = ("name", "customer_name", "client_name", "CustName", "ClientName")
_CUSTOMER_ORDER_KEYS = (
    "document",
    "document_no",
    "doc_no",
    "IdDoc",
    "Document",
    "DocNo",
    "order",
    "order_no",
    "order_number",
    "NomerPorachka",
)
_CUSTOMER_PLAS_DOC_KEYS = ("plas_doc", "plasDoc", "IdPlasDoc", "id_plas_doc", "plas_document")
_CUSTOMER_SOURCE_SKLD_KEYS = ("source_id_skld", "IdSkld", "id_skld", "warehouse_id", "sklad")
_CUSTOMER_GPS_KEYS = ("coordinates", "coords", "gps", "GPS", "Gps", "location")
_CUSTOMER_QUANTITY_KEYS = (
    "quantity",
    "qty",
    "volume",
    "Volume",
    "stacks",
    "Stack",
    "kolichestvo",
    "Количество",
)
_CUSTOMER_TURNOVER_KEYS = ("turnover", "revenue", "sales", "amount", "sum", "oborot", "Оборот")
_CUSTOMER_WORK_TIME_KEYS = (
    "work_time",
    "working_time",
    "working_hours",
    "WorkTime",
    "WorkHours",
    "TimeWindow",
    "RabotnoVreme",
    "Работно време",
)
_CUSTOMER_COMMENT_KEYS = ("comment", "delivery_comment", "DeliveryComment", "note", "remark")


def solve_current_tsp_route(payload: Any, config: Optional[MainConfig] = None) -> Dict[str, Any]:
    """Solve a current open TSP route for a single driver and return JSON data."""
    if not isinstance(payload, dict):
        raise ValueError("TSP заявката трябва да бъде JSON обект.")

    active_config = config or get_config()
    driver_id = _require_text(
        _first_present(payload, *_tsp_field_names(active_config, "tsp_driver_id_field", _DRIVER_ID_KEYS)),
        "driver_id",
    )
    driver_name = _clean_text(
        _first_present(payload, *_tsp_field_names(active_config, "tsp_driver_name_field", _DRIVER_NAME_KEYS))
    )
    start_location = _require_coords(
        _first_present(payload, *_tsp_field_names(active_config, "tsp_driver_location_field", _DRIVER_LOCATION_KEYS)),
        "driver_location",
    )
    end_location = _parse_coords(
        _first_present(payload, *_tsp_field_names(active_config, "tsp_end_location_field", _END_LOCATION_KEYS))
    )
    customers_payload = _extract_customer_records(payload, active_config)
    if not customers_payload:
        raise ValueError("TSP заявката няма клиенти.")

    customers, skipped = _parse_tsp_customers(customers_payload, active_config)
    if not customers:
        raise ValueError("Няма валидни TSP клиенти с GPS координати.")

    metric = _objective_metric(payload, active_config)
    service_time_minutes = _service_time_minutes(payload, active_config)
    start_time_minutes = _start_time_minutes(payload, active_config)
    use_time_windows = _tsp_use_time_windows(payload, active_config)
    wait_weight = _tsp_time_window_wait_weight(payload, active_config)
    late_weight = _tsp_time_window_late_weight(payload, active_config)
    two_opt_enabled = _tsp_two_opt_enabled(payload, active_config)
    two_opt_max_passes = _tsp_two_opt_max_passes(payload, active_config)
    vehicle_type = _vehicle_type(payload)
    vehicle_config = _vehicle_config_for_type(active_config, vehicle_type)

    matrix_locations = [start_location] + [c.coordinates for c in customers]
    end_node = None
    if end_location:
        matrix_locations.append(end_location)
        end_node = len(matrix_locations) - 1

    matrix = _build_distance_matrix(active_config, matrix_locations)
    ordered_customers, route_node_order = _solve_open_tsp_order(
        customers,
        matrix,
        metric=metric,
        start_time_minutes=start_time_minutes,
        service_time_minutes=service_time_minutes,
        use_time_windows=use_time_windows,
        wait_weight=wait_weight,
        late_weight=late_weight,
        two_opt_enabled=two_opt_enabled,
        two_opt_max_passes=two_opt_max_passes,
        end_node=end_node,
    )
    schedule_entries, total_distance_km, total_time_minutes = _build_schedule_entries(
        ordered_customers,
        route_node_order,
        matrix,
        start_time_minutes=start_time_minutes,
        service_time_minutes=service_time_minutes,
        use_time_windows=use_time_windows,
        end_node=end_node,
    )

    route = Route(
        vehicle_type=vehicle_type,
        vehicle_id=0,
        customers=ordered_customers,
        depot_location=start_location,
        end_location=end_location or (ordered_customers[-1].coordinates if ordered_customers else start_location),
        vehicle_name=driver_name or f"Шофьор {driver_id}",
        total_distance_km=total_distance_km,
        total_time_minutes=total_time_minutes,
        total_volume=sum(float(getattr(customer, "quantity", customer.volume) or 0) for customer in ordered_customers),
        schedule_entries=schedule_entries,
    )
    setattr(route, "driver_id", driver_id)
    setattr(route, "open_route", True)
    setattr(route, "total_turnover", sum(float(getattr(customer, "turnover", 0) or 0) for customer in ordered_customers))
    setattr(route, "tsp_metric", metric)

    map_file = None
    upload_summary = None
    if _tsp_generate_map_enabled(payload, active_config):
        map_file = _generate_tsp_map(active_config, route, driver_id)
        if _tsp_upload_map_enabled(payload, active_config):
            upload_summary = OutputHandler(active_config.output)._upload_route_maps(
                [{"file_path": map_file, "bus_id": driver_id}]
            )

    return {
        "status": "ok",
        "type": "current_tsp",
        "driver_id": driver_id,
        "driver_name": driver_name,
        "metric": metric,
        "tsp_settings": {
            "use_time_windows": use_time_windows,
            "wait_weight": wait_weight,
            "late_weight": late_weight,
            "two_opt_enabled": two_opt_enabled,
            "two_opt_max_passes": two_opt_max_passes,
            "service_time_minutes": service_time_minutes,
        },
        "start_location": _coords_dict(start_location),
        "end_location": _coords_dict(end_location) if end_location else None,
        "has_fixed_end_location": bool(end_location),
        "customers_count": len(ordered_customers),
        "skipped_customers": skipped,
        "total_distance_km": round(total_distance_km, 3),
        "total_time_minutes": round(total_time_minutes, 2),
        "total_quantity": round(float(getattr(route, "total_volume", 0) or 0), 2),
        "total_turnover": round(float(getattr(route, "total_turnover", 0) or 0), 2),
        "map_file": map_file,
        "map_upload": upload_summary,
        "delivery_order": [_schedule_entry_to_json(entry) for entry in schedule_entries],
    }


def _first_present(record: Dict[str, Any], *names: str) -> Any:
    lowered = {str(key).strip().lower(): key for key in record.keys()}
    for name in names:
        if name in record:
            return record.get(name)
        original = lowered.get(str(name).strip().lower())
        if original is not None:
            return record.get(original)
    return None


def _tsp_field_names(config: MainConfig, attr_name: str, defaults: Tuple[str, ...]) -> Tuple[str, ...]:
    api_config = getattr(config, "api", None)
    raw = getattr(api_config, attr_name, "") if api_config is not None else ""
    configured: List[str] = []
    if isinstance(raw, (list, tuple, set)):
        raw_parts = list(raw)
    else:
        raw_parts = re.split(r"[,;\n]+", str(raw or ""))

    seen = set()
    for name in [*raw_parts, *defaults]:
        text = str(name or "").strip()
        key = text.lower()
        if text and key not in seen:
            configured.append(text)
            seen.add(key)
    return tuple(configured)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _require_text(value: Any, field_name: str) -> str:
    text = _clean_text(value)
    if not text:
        raise ValueError(f"Липсва задължително поле: {field_name}")
    return text


def _require_coords(value: Any, field_name: str) -> Tuple[float, float]:
    coords = _parse_coords(value)
    if coords is None:
        raise ValueError(f"Невалидни или липсващи координати: {field_name}")
    return coords


def _parse_coords(value: Any) -> Optional[Tuple[float, float]]:
    if value is None:
        return None
    if isinstance(value, dict):
        lat = _first_present(value, "lat", "latitude", "Lat", "Latitude")
        lon = _first_present(value, "lon", "lng", "longitude", "Lon", "Lng", "Longitude")
        if lat is not None and lon is not None:
            try:
                coords = (float(lat), float(lon))
                return coords if _valid_coords(coords) else None
            except (TypeError, ValueError):
                return None
        value = _first_present(value, "gps", "GPS", "coordinates", "coords", "location")
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            coords = (float(value[0]), float(value[1]))
            return coords if _valid_coords(coords) else None
        except (TypeError, ValueError):
            return None
    return GPSParser.parse_gps_string(str(value))


def _valid_coords(coords: Tuple[float, float]) -> bool:
    return -90 <= coords[0] <= 90 and -180 <= coords[1] <= 180


def _extract_customer_records(payload: Dict[str, Any], config: MainConfig) -> List[Dict[str, Any]]:
    for key in _tsp_field_names(config, "tsp_customers_field", _CUSTOMER_LIST_KEYS):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _parse_tsp_customers(records: Iterable[Any], config: MainConfig) -> Tuple[List[Customer], List[Dict[str, Any]]]:
    customers: List[Customer] = []
    skipped: List[Dict[str, Any]] = []

    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            skipped.append({"index": index, "reason": "record is not an object"})
            continue

        customer_id = _clean_text(
            _first_present(record, *_tsp_field_names(config, "tsp_customer_id_field", _CUSTOMER_ID_KEYS))
        ) or str(index)
        name = _clean_text(
            _first_present(record, *_tsp_field_names(config, "tsp_customer_name_field", _CUSTOMER_NAME_KEYS))
        ) or customer_id
        coords = _parse_coords(
            _first_present(record, *_tsp_field_names(config, "tsp_customer_gps_field", _CUSTOMER_GPS_KEYS))
        )
        if coords is None:
            skipped.append({"index": index, "id": customer_id, "name": name, "reason": "missing or invalid GPS"})
            continue

        quantity = _parse_float(
            _first_present(record, *_tsp_field_names(config, "tsp_customer_quantity_field", _CUSTOMER_QUANTITY_KEYS)),
            default=0.0,
        )
        turnover = _parse_float(
            _first_present(record, *_tsp_field_names(config, "tsp_customer_turnover_field", _CUSTOMER_TURNOVER_KEYS)),
            default=0.0,
        )
        raw_work_time = _first_present(
            record,
            *_tsp_field_names(config, "tsp_customer_work_time_field", _CUSTOMER_WORK_TIME_KEYS),
        )
        time_windows = safe_parse_time_window_values(raw_work_time, f" за TSP клиент {customer_id}")
        tw_start, tw_end = time_windows[0] if time_windows else (None, None)
        comment = _safe_delivery_comment(
            _first_present(record, *_tsp_field_names(config, "tsp_customer_comment_field", _CUSTOMER_COMMENT_KEYS)),
            f" за TSP клиент {customer_id}",
        )
        order_no = _clean_text(
            _first_present(record, *_tsp_field_names(config, "tsp_customer_order_field", _CUSTOMER_ORDER_KEYS))
        )
        plas_doc = _clean_text(_first_present(record, *_CUSTOMER_PLAS_DOC_KEYS))
        source_id_skld = _clean_text(_first_present(record, *_CUSTOMER_SOURCE_SKLD_KEYS))

        customer = Customer(
            id=customer_id,
            name=name,
            coordinates=coords,
            volume=quantity,
            original_gps_data=f"{coords[0]:.6f},{coords[1]:.6f}",
            document=order_no,
            plas_doc=plas_doc,
            source_id_skld=source_id_skld,
            time_window_start_minutes=tw_start,
            time_window_end_minutes=tw_end,
            time_windows=time_windows,
            delivery_comment=comment,
            grouped_documents=[
                {
                    "customer_id": customer_id,
                    "document": order_no,
                    "order": order_no,
                    "plas_doc": plas_doc,
                    "source_id_skld": source_id_skld,
                    "volume": quantity,
                    "quantity": quantity,
                    "turnover": turnover,
                }
            ],
        )
        setattr(customer, "quantity", quantity)
        setattr(customer, "turnover", turnover)
        setattr(customer, "work_time_text", _clean_text(raw_work_time))
        setattr(customer, "order", order_no)
        setattr(customer, "order_no", order_no)
        customers.append(customer)

    return _group_tsp_customer_documents(customers, config), skipped


def _group_tsp_customer_documents(customers: List[Customer], config: MainConfig) -> List[Customer]:
    input_config = getattr(config, "input", None)
    if not bool(getattr(input_config, "enable_customer_document_grouping", True)):
        for customer in customers:
            customer.grouped_documents = _normalise_tsp_grouped_documents(customer)
            _refresh_tsp_group_fields(customer)
        return customers

    grouped: Dict[Tuple[str, float, float], Customer] = {}
    ordered: List[Customer] = []
    grouped_rows = 0

    for customer in customers:
        key = _tsp_group_key(customer)
        if key is None:
            customer.grouped_documents = _normalise_tsp_grouped_documents(customer)
            _refresh_tsp_group_fields(customer)
            ordered.append(customer)
            continue

        if key not in grouped:
            customer.grouped_documents = _normalise_tsp_grouped_documents(customer)
            _refresh_tsp_group_fields(customer)
            grouped[key] = customer
            ordered.append(customer)
            continue

        target = grouped[key]
        grouped_rows += 1
        target.grouped_documents = _merge_tsp_grouped_document_lists(
            _normalise_tsp_grouped_documents(target),
            _normalise_tsp_grouped_documents(customer),
        )
        target.delivery_comment = _join_unique_text(
            [getattr(target, "delivery_comment", ""), getattr(customer, "delivery_comment", "")],
            separator=" | ",
        )

        target_windows = customer_time_windows_minutes(target)
        customer_windows = customer_time_windows_minutes(customer)
        if not target_windows and customer_windows:
            target.time_window_start_minutes = customer.time_window_start_minutes
            target.time_window_end_minutes = customer.time_window_end_minutes
            target.time_windows = list(customer_windows)
        elif customer_windows and target_windows != customer_windows:
            logger.warning(
                "TSP customer %s has different time windows in grouped documents; keeping the first one.",
                customer.id,
            )

        _refresh_tsp_group_fields(target)

    if grouped_rows:
        logger.info("Grouped %s TSP document row(s) into existing customer stops.", grouped_rows)
    return ordered


def _tsp_group_key(customer: Customer) -> Optional[Tuple[str, float, float]]:
    customer_id = str(getattr(customer, "id", "") or "").strip()
    coords = getattr(customer, "coordinates", None)
    if not customer_id or not coords:
        return None
    return (customer_id.lower(), round(float(coords[0]), 6), round(float(coords[1]), 6))


def _normalise_tsp_grouped_documents(customer: Customer) -> List[Dict[str, object]]:
    documents = list(getattr(customer, "grouped_documents", None) or [])
    if not documents:
        quantity = _parse_float(getattr(customer, "quantity", getattr(customer, "volume", 0)), 0.0)
        documents = [
            {
                "customer_id": getattr(customer, "id", ""),
                "document": getattr(customer, "document", ""),
                "order": getattr(customer, "order_no", getattr(customer, "document", "")),
                "plas_doc": getattr(customer, "plas_doc", ""),
                "source_id_skld": getattr(customer, "source_id_skld", ""),
                "volume": quantity,
                "quantity": quantity,
                "turnover": _parse_float(getattr(customer, "turnover", 0), 0.0),
            }
        ]

    normalised: List[Dict[str, object]] = []
    for item in documents:
        if not isinstance(item, dict):
            continue
        document = str(item.get("document", item.get("order", "")) or "")
        order = str(item.get("order", document) or "")
        quantity = _parse_float(item.get("quantity", item.get("volume", 0)), 0.0)
        normalised.append(
            {
                "customer_id": str(item.get("customer_id", getattr(customer, "id", "")) or ""),
                "document": document,
                "order": order,
                "plas_doc": str(item.get("plas_doc", "") or ""),
                "source_id_skld": str(item.get("source_id_skld", "") or ""),
                "volume": quantity,
                "quantity": quantity,
                "turnover": _parse_float(item.get("turnover", 0), 0.0),
            }
        )
    return normalised


def _merge_tsp_grouped_document_lists(
    left: List[Dict[str, object]],
    right: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    merged: List[Dict[str, object]] = []
    positions: Dict[str, Dict[str, object]] = {}

    for item in [*left, *right]:
        document = str(item.get("document", "") or "")
        plas_doc = str(item.get("plas_doc", "") or "")
        key = plas_doc or document
        if not key:
            key = f"__row_{len(merged)}"

        if key in positions:
            existing = positions[key]
            existing["volume"] = _parse_float(existing.get("volume"), 0.0) + _parse_float(item.get("volume"), 0.0)
            existing["quantity"] = _parse_float(existing.get("quantity"), 0.0) + _parse_float(item.get("quantity"), 0.0)
            existing["turnover"] = _parse_float(existing.get("turnover"), 0.0) + _parse_float(item.get("turnover"), 0.0)
            if not existing.get("document") and document:
                existing["document"] = document
            if not existing.get("order") and item.get("order"):
                existing["order"] = str(item.get("order", ""))
            if not existing.get("plas_doc") and plas_doc:
                existing["plas_doc"] = plas_doc
            if not existing.get("source_id_skld") and item.get("source_id_skld"):
                existing["source_id_skld"] = str(item.get("source_id_skld", ""))
            continue

        copied = dict(item)
        copied["volume"] = _parse_float(copied.get("volume"), 0.0)
        copied["quantity"] = _parse_float(copied.get("quantity"), 0.0)
        copied["turnover"] = _parse_float(copied.get("turnover"), 0.0)
        positions[key] = copied
        merged.append(copied)

    return merged


def _refresh_tsp_group_fields(customer: Customer) -> None:
    documents = _normalise_tsp_grouped_documents(customer)
    customer.grouped_documents = documents

    total_quantity = sum(_parse_float(item.get("quantity", item.get("volume", 0)), 0.0) for item in documents)
    total_turnover = sum(_parse_float(item.get("turnover", 0), 0.0) for item in documents)
    order_text = _join_unique_text([item.get("order") or item.get("document") for item in documents])
    document_text = _join_unique_text([item.get("document") or item.get("order") for item in documents])

    customer.volume = total_quantity
    customer.document = document_text
    customer.plas_doc = _join_unique_text([item.get("plas_doc") for item in documents])
    customer.source_id_skld = _join_unique_text([item.get("source_id_skld") for item in documents])
    setattr(customer, "quantity", total_quantity)
    setattr(customer, "turnover", total_turnover)
    setattr(customer, "order", order_text)
    setattr(customer, "order_no", order_text)

    windows = customer_time_windows_minutes(customer)
    if windows:
        setattr(customer, "work_time_text", format_time_windows_minutes(windows))


def _join_unique_text(values: Iterable[Any], separator: str = "; ") -> str:
    seen = set()
    result: List[str] = []
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return separator.join(result)


def _parse_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def _objective_metric(payload: Dict[str, Any], config: MainConfig) -> str:
    metric = _clean_text(
        _first_present(payload, "metric", "objective", "objective_metric", "optimize_by", "tsp_metric", "tsp_optimize_by")
    )
    if not metric:
        api_config = getattr(config, "api", None)
        metric = str(getattr(api_config, "tsp_objective_metric", "") or "")
    if not metric:
        metric = str(getattr(config.cvrp, "objective_metric", "time") or "time")
    metric = metric.lower()
    return "time" if metric in {"time", "duration", "fastest", "shortest_time"} else "distance"


def _vehicle_type(payload: Dict[str, Any]) -> VehicleType:
    raw = (_clean_text(_first_present(payload, "vehicle_type", "vehicleType", "bus_type")) or "internal_bus").lower()
    for vehicle_type in VehicleType:
        if vehicle_type.value == raw:
            return vehicle_type
    return VehicleType.INTERNAL_BUS


def _vehicle_config_for_type(config: MainConfig, vehicle_type: VehicleType) -> Optional[VehicleConfig]:
    for vehicle_config in getattr(config, "vehicles", []) or []:
        if getattr(vehicle_config, "vehicle_type", None) == vehicle_type:
            return vehicle_config
    return None


def _service_time_minutes(payload: Dict[str, Any], config: MainConfig) -> float:
    value = _first_present(payload, "service_time_minutes", "service_minutes", "serviceTimeMinutes")
    if value is not None:
        return max(0.0, _parse_float(value, default=0.0))
    api_config = getattr(config, "api", None)
    if api_config is not None and hasattr(api_config, "tsp_default_service_time_minutes"):
        return max(0.0, _parse_float(getattr(api_config, "tsp_default_service_time_minutes", 8), default=8.0))
    vehicle_config = _vehicle_config_for_type(config, _vehicle_type(payload))
    if vehicle_config:
        return float(getattr(vehicle_config, "service_time_minutes", 15) or 15)
    return float(getattr(config.cvrp, "service_time_minutes", 15) or 15)


def _tsp_payload_bool(payload: Dict[str, Any], default: bool, *names: str) -> bool:
    value = _first_present(payload, *names)
    if value is None:
        return default
    return _payload_bool({"value": value}, "value", default=default)


def _tsp_payload_float(payload: Dict[str, Any], default: float, *names: str, minimum: float = 0.0) -> float:
    value = _first_present(payload, *names)
    parsed = _parse_float(value, default) if value is not None else default
    return max(minimum, parsed)


def _tsp_payload_int(payload: Dict[str, Any], default: int, *names: str, minimum: int = 0) -> int:
    value = _first_present(payload, *names)
    parsed = int(round(_parse_float(value, default))) if value is not None else int(default)
    return max(minimum, parsed)


def _tsp_use_time_windows(payload: Dict[str, Any], config: MainConfig) -> bool:
    api_config = getattr(config, "api", None)
    default = bool(getattr(api_config, "tsp_use_time_windows", True))
    return _tsp_payload_bool(payload, default, "use_time_windows", "time_windows", "tsp_use_time_windows")


def _tsp_time_window_wait_weight(payload: Dict[str, Any], config: MainConfig) -> float:
    api_config = getattr(config, "api", None)
    default = float(getattr(api_config, "tsp_time_window_wait_weight", 1.0) or 1.0)
    return _tsp_payload_float(
        payload,
        default,
        "wait_weight",
        "time_window_wait_weight",
        "tsp_wait_weight",
        "tsp_time_window_wait_weight",
    )


def _tsp_time_window_late_weight(payload: Dict[str, Any], config: MainConfig) -> float:
    api_config = getattr(config, "api", None)
    default = float(getattr(api_config, "tsp_time_window_late_weight", 20.0) or 20.0)
    return _tsp_payload_float(
        payload,
        default,
        "late_weight",
        "time_window_late_weight",
        "tsp_late_weight",
        "tsp_time_window_late_weight",
    )


def _tsp_two_opt_enabled(payload: Dict[str, Any], config: MainConfig) -> bool:
    api_config = getattr(config, "api", None)
    default = bool(getattr(api_config, "tsp_enable_two_opt", True))
    return _tsp_payload_bool(payload, default, "enable_two_opt", "two_opt", "tsp_enable_two_opt", "tsp_two_opt")


def _tsp_two_opt_max_passes(payload: Dict[str, Any], config: MainConfig) -> int:
    api_config = getattr(config, "api", None)
    default = int(getattr(api_config, "tsp_two_opt_max_passes", 30) or 30)
    return _tsp_payload_int(payload, default, "two_opt_max_passes", "tsp_two_opt_max_passes", minimum=0)


def _tsp_generate_map_enabled(payload: Dict[str, Any], config: MainConfig) -> bool:
    api_config = getattr(config, "api", None)
    default = bool(getattr(api_config, "tsp_generate_html_map", True))
    value = _first_present(
        payload,
        "generate_map",
        "generate_html_map",
        "generate_local_html_map",
        "local_html_map",
        "tsp_generate_map",
        "tsp_local_html",
    )
    if value is None:
        return default
    return _payload_bool({"value": value}, "value", default=default)


def _tsp_upload_map_enabled(payload: Dict[str, Any], config: MainConfig) -> bool:
    api_config = getattr(config, "api", None)
    default = bool(getattr(api_config, "tsp_upload_html_map", True))
    value = _first_present(payload, "upload_map", "upload_html_map", "tsp_upload_map", "tsp_upload_html_map")
    if value is None:
        return default
    return _payload_bool({"value": value}, "value", default=default)


def _start_time_minutes(payload: Dict[str, Any], config: MainConfig) -> int:
    value = _first_present(payload, "start_time", "current_time", "departure_time", "startTime")
    if value is not None:
        parsed = parse_time_value_to_minutes(value)
        if parsed is not None:
            return int(parsed)
    now = datetime.now()
    return now.hour * 60 + now.minute


def _build_distance_matrix(config: MainConfig, locations: List[Tuple[float, float]]) -> DistanceMatrix:
    routing_engine = getattr(getattr(config, "routing", None), "engine", RoutingEngine.OSRM)
    is_valhalla = getattr(routing_engine, "value", routing_engine) == RoutingEngine.VALHALLA.value

    if is_valhalla:
        try:
            from valhalla_client import ValhallaClient

            client = ValhallaClient(getattr(config, "valhalla", None))
            try:
                if client.check_server_status():
                    return client.get_distance_matrix(locations)
            finally:
                client.close()
            logger.warning("Valhalla не е достъпна за текущ TSP; fallback към OSRM.")
        except Exception as exc:
            logger.warning("Valhalla TSP матрицата не успя (%s); fallback към OSRM.", exc)

    client = OSRMClient(getattr(config, "osrm", None))
    try:
        return client.get_distance_matrix(locations)
    finally:
        client.close()


def _solve_open_tsp_order(
    customers: List[Customer],
    matrix: DistanceMatrix,
    metric: str,
    start_time_minutes: int,
    service_time_minutes: float,
    use_time_windows: bool,
    wait_weight: float,
    late_weight: float,
    two_opt_enabled: bool,
    two_opt_max_passes: int,
    end_node: Optional[int] = None,
) -> Tuple[List[Customer], List[int]]:
    if not customers:
        return [], []

    unvisited = set(range(1, len(customers) + 1))
    current = 0
    current_clock_seconds = start_time_minutes * 60
    order: List[int] = []

    while unvisited:
        best_node = min(
            unvisited,
            key=lambda node: _greedy_step_score(
                current,
                node,
                customers[node - 1],
                matrix,
                metric,
                current_clock_seconds,
                use_time_windows,
                wait_weight,
                late_weight,
            ),
        )
        order.append(best_node)
        current_clock_seconds = _arrival_after_service_seconds(
            current,
            best_node,
            customers[best_node - 1],
            matrix,
            current_clock_seconds,
            service_time_minutes,
            use_time_windows,
        )
        unvisited.remove(best_node)
        current = best_node

    if two_opt_enabled:
        order = _two_opt_open_route(order, matrix, metric, end_node=end_node, max_passes=two_opt_max_passes)
    return [customers[node - 1] for node in order], order


def _greedy_step_score(
    current_node: int,
    next_node: int,
    customer: Customer,
    matrix: DistanceMatrix,
    metric: str,
    current_clock_seconds: float,
    use_time_windows: bool,
    wait_weight: float,
    late_weight: float,
) -> float:
    base = _matrix_cost(matrix, current_node, next_node, metric)
    travel_seconds = _safe_matrix_value(matrix.durations, current_node, next_node)
    arrival_seconds = current_clock_seconds + travel_seconds
    windows = time_windows_to_seconds(customer_time_windows_minutes(customer)) if use_time_windows else []
    if not windows:
        return base

    _, wait_seconds, _, status = choose_time_window_for_arrival(arrival_seconds, windows)
    late_seconds = max(0.0, arrival_seconds - windows[-1][1]) if status == "След работно време" else 0.0
    return base + wait_seconds * wait_weight + late_seconds * late_weight


def _arrival_after_service_seconds(
    current_node: int,
    next_node: int,
    customer: Customer,
    matrix: DistanceMatrix,
    current_clock_seconds: float,
    service_time_minutes: float,
    use_time_windows: bool,
) -> float:
    travel_seconds = _safe_matrix_value(matrix.durations, current_node, next_node)
    arrival_seconds = current_clock_seconds + travel_seconds
    windows = time_windows_to_seconds(customer_time_windows_minutes(customer)) if use_time_windows else []
    if windows:
        _, wait_seconds, _, _ = choose_time_window_for_arrival(arrival_seconds, windows)
        arrival_seconds += wait_seconds
    return arrival_seconds + service_time_minutes * 60


def _two_opt_open_route(
    order: List[int],
    matrix: DistanceMatrix,
    metric: str,
    end_node: Optional[int] = None,
    max_passes: int = 30,
) -> List[int]:
    if len(order) < 4 or max_passes <= 0:
        return order

    best = list(order)
    best_cost = _open_route_cost(best, matrix, metric, end_node=end_node)
    improved = True
    passes = 0
    while improved and passes < max_passes:
        improved = False
        passes += 1
        for i in range(len(best) - 2):
            for j in range(i + 2, len(best)):
                candidate = best[:i] + list(reversed(best[i : j + 1])) + best[j + 1 :]
                candidate_cost = _open_route_cost(candidate, matrix, metric, end_node=end_node)
                if candidate_cost + 0.001 < best_cost:
                    best = candidate
                    best_cost = candidate_cost
                    improved = True
                    break
            if improved:
                break
    return best


def _open_route_cost(
    order: List[int],
    matrix: DistanceMatrix,
    metric: str,
    end_node: Optional[int] = None,
) -> float:
    if not order:
        return 0.0
    cost = _matrix_cost(matrix, 0, order[0], metric)
    for left, right in zip(order, order[1:]):
        cost += _matrix_cost(matrix, left, right, metric)
    if end_node is not None:
        cost += _matrix_cost(matrix, order[-1], end_node, metric)
    return cost


def _matrix_cost(matrix: DistanceMatrix, from_node: int, to_node: int, metric: str) -> float:
    values = matrix.durations if metric == "time" else matrix.distances
    return _safe_matrix_value(values, from_node, to_node)


def _safe_matrix_value(values: List[List[float]], from_node: int, to_node: int) -> float:
    try:
        value = float(values[from_node][to_node])
        if math.isfinite(value):
            return value
    except (IndexError, TypeError, ValueError):
        pass
    return 10**12


def _build_schedule_entries(
    customers: List[Customer],
    route_node_order: List[int],
    matrix: DistanceMatrix,
    start_time_minutes: int,
    service_time_minutes: float,
    use_time_windows: bool,
    end_node: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], float, float]:
    entries: List[Dict[str, Any]] = []
    current_node = 0
    current_clock_seconds = float(start_time_minutes) * 60
    total_time_seconds = 0.0
    cumulative_distance_m = 0.0
    previous_stop_name = "Текуща локация"

    for stop_index, node in enumerate(route_node_order, start=1):
        customer = customers[stop_index - 1]
        distance_m = _safe_matrix_value(matrix.distances, current_node, node)
        travel_seconds = _safe_matrix_value(matrix.durations, current_node, node)
        cumulative_distance_m += distance_m

        raw_arrival_seconds = current_clock_seconds + travel_seconds
        windows = time_windows_to_seconds(customer_time_windows_minutes(customer)) if use_time_windows else []
        _, wait_seconds, window_index, status = choose_time_window_for_arrival(raw_arrival_seconds, windows)
        if status and len(windows) > 1 and window_index >= 0:
            status = f"{status} (прозорец {window_index + 1})"
        arrival_after_wait_seconds = raw_arrival_seconds + wait_seconds

        current_clock_seconds = arrival_after_wait_seconds + service_time_minutes * 60
        total_time_seconds = current_clock_seconds - start_time_minutes * 60

        entries.append(
            {
                "customer": customer,
                "index": stop_index,
                "previous_stop_name": previous_stop_name,
                "distance_from_previous": distance_m / 1000,
                "cumulative_distance": cumulative_distance_m / 1000,
                "travel_time_minutes": travel_seconds / 60,
                "service_time_minutes": service_time_minutes,
                "wait_minutes": wait_seconds / 60,
                "total_time_for_step": (travel_seconds + wait_seconds + service_time_minutes * 60) / 60,
                "cumulative_time": total_time_seconds / 60,
                "start_time_minutes": start_time_minutes,
                "arrival_time_minutes": arrival_after_wait_seconds / 60,
                "total_time_with_start": current_clock_seconds / 60,
                "time_window_text": _format_customer_time_window(customer),
                "time_window_status": status,
                "delivery_comment": getattr(customer, "delivery_comment", ""),
                "quantity": getattr(customer, "quantity", customer.volume),
                "turnover": getattr(customer, "turnover", 0),
                "order": getattr(customer, "order_no", "") or getattr(customer, "document", ""),
            }
        )
        current_node = node
        previous_stop_name = customer.name

    if entries and end_node is not None:
        distance_m = _safe_matrix_value(matrix.distances, current_node, end_node)
        travel_seconds = _safe_matrix_value(matrix.durations, current_node, end_node)
        cumulative_distance_m += distance_m
        total_time_seconds += travel_seconds

    return entries, cumulative_distance_m / 1000, total_time_seconds / 60


def _format_customer_time_window(customer: Customer) -> str:
    return format_time_windows_minutes(customer_time_windows_minutes(customer))


def _generate_tsp_map(config: MainConfig, route: Route, driver_id: str) -> str:
    routes_dir = getattr(config.output, "routes_output_dir", "") or os.path.join(os.getcwd(), "output", "routes")
    os.makedirs(routes_dir, exist_ok=True)
    map_gen = InteractiveMapGenerator(config.output)
    route_map = map_gen.create_single_route_map(route, 1, route.depot_location)
    date_stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe_driver_id = _safe_filename_stem(driver_id, "driver")
    file_path = os.path.join(routes_dir, f"current_tsp_{safe_driver_id}_{date_stamp}.html")
    return map_gen.save_map(route_map, file_path)


def _safe_filename_stem(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return text or fallback


def _payload_bool(payload: Dict[str, Any], name: str, default: bool) -> bool:
    value = payload.get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "да"}


def _coords_dict(coords: Tuple[float, float]) -> Dict[str, float]:
    return {"lat": float(coords[0]), "lon": float(coords[1])}


def _schedule_entry_to_json(entry: Dict[str, Any]) -> Dict[str, Any]:
    customer = entry.get("customer")
    coords = getattr(customer, "coordinates", None) if customer is not None else None
    documents = _normalise_tsp_grouped_documents(customer) if customer is not None else []
    return {
        "sequence": int(entry.get("index", 0) or 0),
        "customer_id": getattr(customer, "id", "") if customer is not None else "",
        "customer_name": getattr(customer, "name", "") if customer is not None else "",
        "order": str(entry.get("order", "") or ""),
        "documents": documents,
        "coordinates": _coords_dict(coords) if coords else None,
        "quantity": round(_parse_float(entry.get("quantity"), 0.0), 2),
        "turnover": round(_parse_float(entry.get("turnover"), 0.0), 2),
        "arrival_time": _format_minutes(entry.get("arrival_time_minutes")),
        "departure_time": _format_minutes(entry.get("total_time_with_start")),
        "work_time": str(entry.get("time_window_text", "") or ""),
        "time_window_status": str(entry.get("time_window_status", "") or ""),
        "distance_from_previous_km": round(_parse_float(entry.get("distance_from_previous"), 0.0), 3),
        "cumulative_distance_km": round(_parse_float(entry.get("cumulative_distance"), 0.0), 3),
        "travel_time_minutes": round(_parse_float(entry.get("travel_time_minutes"), 0.0), 2),
        "wait_minutes": round(_parse_float(entry.get("wait_minutes"), 0.0), 2),
        "comment": html.unescape(str(entry.get("delivery_comment", "") or "")),
    }


def _format_minutes(value: Any) -> str:
    try:
        minutes = int(round(float(value)))
    except (TypeError, ValueError):
        return ""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
