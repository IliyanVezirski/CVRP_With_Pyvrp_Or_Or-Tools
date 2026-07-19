"""Shared bus number formatting for reports, maps, uploads, and setData."""

from __future__ import annotations

import re
from typing import Any, Hashable, List, Optional, Sequence, Tuple


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


def _explicit_vehicle_key(route: Any) -> Optional[str]:
    """Return the solver-provided physical vehicle key, when present."""
    value = getattr(route, "vehicle_key", None)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def route_vehicle_key(route: Any, fallback_index: Optional[int] = None) -> str:
    """Return the public physical-vehicle key without merging legacy routes.

    Older solvers do not set ``vehicle_key``.  Those routes must remain separate,
    so their fallback key includes their flat route index.
    """
    explicit = _explicit_vehicle_key(route)
    if explicit is not None:
        return explicit
    if fallback_index is None:
        return ""
    return f"route:{fallback_index + 1}"


def _group_token(route: Any, route_index: int) -> Tuple[str, Hashable]:
    explicit = _explicit_vehicle_key(route)
    if explicit is not None:
        return ("vehicle", explicit)
    return ("route", route_index)


def route_trip_number(route: Any) -> int:
    """Return a one-based course number, defaulting to the legacy first course."""
    return max(1, _parse_int(getattr(route, "trip_number", 1), 1))


def route_trip_count(route: Any, routes: Optional[Sequence[Any]] = None) -> int:
    """Return the number of courses for this physical vehicle."""
    declared = max(1, _parse_int(getattr(route, "trip_count", 1), 1))
    explicit = _explicit_vehicle_key(route)
    if explicit is None or not routes:
        return declared
    actual = sum(1 for candidate in routes if _explicit_vehicle_key(candidate) == explicit)
    return max(declared, actual, route_trip_number(route))


def route_identifier(route: Any, fallback_route_number: int) -> str:
    """Return the stable route/course ID used in labels and template contexts."""
    value = getattr(route, "route_id", None)
    if value is None or not str(value).strip():
        return str(fallback_route_number)
    return str(value).strip()


def is_multi_trip_route(route: Any, routes: Optional[Sequence[Any]] = None) -> bool:
    return route_trip_count(route, routes) > 1 or route_trip_number(route) > 1


def count_physical_vehicles(routes: Optional[Sequence[Any]]) -> int:
    """Count physical vehicles while treating every legacy route as independent."""
    return len({_group_token(route, idx) for idx, route in enumerate(routes or [])})


def order_routes_for_output(routes: Optional[Sequence[Any]]) -> List[Any]:
    """Return physical vehicles together, courses ordered, center vehicles last.

    With legacy single-course routes this is byte-for-byte the previous stable
    ordering: non-center routes first and center routes last.
    """
    indexed_routes = list(enumerate(routes or []))
    first_index = {}
    representative = {}
    for idx, route in indexed_routes:
        token = _group_token(route, idx)
        first_index.setdefault(token, idx)
        representative.setdefault(token, route)

    indexed_routes.sort(
        key=lambda item: (
            1 if _is_center_route(representative[_group_token(item[1], item[0])]) else 0,
            first_index[_group_token(item[1], item[0])],
            route_trip_number(item[1]),
            item[0],
        )
    )
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


def _reserved_center_bus_numbers(
    output_config: Any,
    routes: Optional[Sequence[Any]],
) -> set[str]:
    """Return configured center IDs occupied by output courses.

    Center numbering has its own range starting at ``center_bus_numbering_start_id``.
    Non-center courses skip the actually occupied part of that range, so adding
    enough second/third courses can never produce the same public ID as a center
    course.
    """
    if not routes:
        return set()
    start_id = _parse_int(
        getattr(output_config, "center_bus_numbering_start_id", 1004501015),
        1004501015,
    )
    center_course_count = sum(1 for route in routes if _is_center_route(route))
    return {str(start_id + offset) for offset in range(center_course_count)}


def _non_center_course_bus_number(
    output_config: Any,
    occurrence: int,
    routes: Optional[Sequence[Any]],
) -> str:
    """Format a non-center course ID while avoiding the center course range."""
    reserved = _reserved_center_bus_numbers(output_config, routes)
    target_occurrence = max(1, occurrence)
    available_count = 0
    sequence_index = 0
    while True:
        candidate = _sequential_bus_number(output_config, sequence_index)
        if candidate not in reserved:
            available_count += 1
            if available_count == target_occurrence:
                return candidate
        sequence_index += 1


def format_route_bus_number(
    output_config: Any,
    route_index: int,
    route: Any = None,
    routes: Optional[Sequence[Any]] = None,
) -> str:
    """Return the business-facing ID for one output course.

    When center numbering is enabled:
    - every CENTER_BUS course gets start_id, start_id + 1, start_id + 2...
    - every other course gets the normal sequence from ...01 upward;
    - non-center IDs skip occupied center IDs, preventing collisions.

    ``vehicle_key`` deliberately does not affect this number.  It remains the
    physical-vehicle grouping key, while each reload/course is a separate
    business output.  Legacy single-course routes keep their previous IDs.
    """
    route = route if route is not None else _route_from_list(routes, route_index)
    enabled = _truthy(getattr(output_config, "center_bus_numbering_enabled", False))
    if not enabled:
        return _sequential_bus_number(output_config, route_index)

    if route is None:
        return _sequential_bus_number(output_config, route_index)

    # Current-TSP creates one standalone route whose externally supplied driver
    # ID must remain unchanged.  A physical CVRP vehicle with multiple courses,
    # however, must receive a distinct generated ID for each course.
    driver_id = str(getattr(route, "driver_id", "") or "").strip()
    if driver_id and not is_multi_trip_route(route, routes):
        return driver_id

    start_id = _parse_int(getattr(output_config, "center_bus_numbering_start_id", 1004501015), 1004501015)
    if _is_center_route(route):
        occurrence = _occurrence_in_routes(route, route_index, routes, want_center=True)
        return str(start_id + occurrence - 1)

    occurrence = _occurrence_in_routes(route, route_index, routes, want_center=False)
    return _non_center_course_bus_number(output_config, occurrence, routes)
