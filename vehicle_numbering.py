"""Shared bus number formatting for reports, maps, uploads, and setData."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence


CENTER_BUS_VALUE = "center_bus"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "none"}
    return bool(value)


def _route_vehicle_type(route: Any) -> str:
    vehicle_type = getattr(route, "vehicle_type", "")
    return str(getattr(vehicle_type, "value", vehicle_type) or "").strip().lower()


def _is_center_route(route: Any) -> bool:
    return _route_vehicle_type(route) == CENTER_BUS_VALUE


def order_routes_for_output(routes: Optional[Sequence[Any]]) -> List[Any]:
    """Returns routes in the user-facing order: non-center first, center last."""
    indexed_routes = list(enumerate(routes or []))
    indexed_routes.sort(key=lambda item: (1 if _is_center_route(item[1]) else 0, item[0]))
    return [route for _, route in indexed_routes]


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _sequential_bus_number(output_config: Any, route_index: int) -> str:
    prefix = str(getattr(output_config, "excel_bus_number_prefix", "10045010") or "")
    digits = _parse_int(getattr(output_config, "excel_bus_number_digits", 2), 2)
    return f"{prefix}{route_index + 1:0{max(1, digits)}d}"


def _route_from_list(routes: Optional[Sequence[Any]], route_index: int) -> Any:
    if routes is None:
        return None
    try:
        if 0 <= route_index < len(routes):
            return routes[route_index]
    except Exception:
        return None
    return None


def _occurrence_from_name(route: Any) -> Optional[int]:
    name = str(getattr(route, "vehicle_name", "") or "").strip()
    if not name:
        return None
    match = re.search(r"(\d+)\s*$", name)
    if not match:
        return None
    return max(1, _parse_int(match.group(1), 1))


def _occurrence_in_routes(
    route: Any,
    route_index: int,
    routes: Optional[Sequence[Any]],
    want_center: bool,
) -> int:
    if routes:
        count = 0
        for idx, candidate in enumerate(routes):
            if idx > route_index:
                break
            if _is_center_route(candidate) == want_center:
                count += 1
        if count > 0:
            return count

    occurrence = _occurrence_from_name(route)
    if occurrence is not None:
        return occurrence

    return max(1, route_index + 1)


def format_route_bus_number(
    output_config: Any,
    route_index: int,
    route: Any = None,
    routes: Optional[Sequence[Any]] = None,
) -> str:
    """Returns the business-facing bus ID for a route.

    When center numbering is enabled:
    - CENTER_BUS routes get start_id, start_id + 1, start_id + 2...
    - all other routes keep the normal sequence from ...01 upward.

    When disabled, the old sequential prefix/digits rule is used.
    """
    enabled = _truthy(getattr(output_config, "center_bus_numbering_enabled", False))
    if not enabled:
        return _sequential_bus_number(output_config, route_index)

    route = route if route is not None else _route_from_list(routes, route_index)
    if route is None:
        return _sequential_bus_number(output_config, route_index)

    driver_id = str(getattr(route, "driver_id", "") or "").strip()
    if driver_id:
        return driver_id

    start_id = _parse_int(getattr(output_config, "center_bus_numbering_start_id", 1004501015), 1004501015)
    if _is_center_route(route):
        occurrence = _occurrence_in_routes(route, route_index, routes, want_center=True)
        return str(start_id + occurrence - 1)

    occurrence = _occurrence_in_routes(route, route_index, routes, want_center=False)
    return _sequential_bus_number(output_config, occurrence - 1)
