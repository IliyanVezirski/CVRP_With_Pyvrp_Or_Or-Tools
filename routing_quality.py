"""Compact, per-direction provenance for matrix values and Excel diagnostics.

The report switch controls presentation, not tracking. Old caches stay unknown;
neither plausible numbers nor a later geometry request prove their provenance.
"""
import math
from collections import Counter


UNKNOWN = "unknown|unknown|unknown"
OSRM = "osrm|osrm|"
VALHALLA = "valhalla|valhalla|"
ROAD_SOURCES = {"osrm", "valhalla"}
SOURCE_LABELS = {
    "osrm": "OSRM", "valhalla": "Valhalla",
    "approx": "Приблизително", "derived": "Изчислено от времето",
    "unknown": "Неизвестен произход",
}
REASON_LABELS = {
    "matrix_route_recovery": "Липсващата клетка е възстановена с реален маршрут от Valhalla",
    "recovery_budget": "Достигнато е времето за допълнителни проверки на липсващи пътища",
    "": "", "unknown": "Няма запазен произход (стар кеш или друг източник)",
    "no_route": "Не е намерен път", "timeout": "Сървърът не отговори навреме",
    "request_error": "Неуспешна заявка към сървъра",
    "invalid_value": "Липсваща или невалидна стойност в отговора",
    "distance_from_duration": "Липсва разстояние; изчислено е от времето",
    "size_limit": "Превишен размер за заявки към пътната услуга",
    "server_estimate": "Пътната услуга е върнала приблизителни данни",
    "match_estimate": "Стойности от GPS траектория, не отделен път за тази посока",
    "lookup_failed": "Отсечката не може да се свърже еднозначно с матрицата",
    "matrix_unavailable": "Матрицата от решението не е налична за проверка",
}


def quality_grid(rows, columns=None, value=UNKNOWN):
    return [[value] * (rows if columns is None else columns) for _ in range(rows)]


def quality_code(distance_source, duration_source, reason=""):
    return "|".join((distance_source, duration_source, reason))


def quality_parts(code):
    parts = str(code or UNKNOWN).split("|")
    if len(parts) != 3 or any(source not in SOURCE_LABELS for source in parts[:2]):
        return UNKNOWN.split("|")
    return parts


def quality_at(matrix, i, j):
    try:
        code = matrix.quality[i][j]
        return "|".join(quality_parts(code))
    except (AttributeError, IndexError, TypeError):
        return UNKNOWN


def quality_kind(code):
    distance, duration, _ = quality_parts(code)
    if "approx" in (distance, duration) or "derived" in (distance, duration):
        return "approximate"
    return "road" if {distance, duration} <= ROAD_SOURCES else "unknown"


def numeric(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


class RoutingResponseError(ValueError):
    def __init__(self, message, reason="invalid_value"):
        super().__init__(message)
        self.reason = reason


def error_reason(exc):
    # No raw URLs, tokens or response bodies are copied to the report/cache.
    import requests
    if isinstance(exc, RoutingResponseError):
        return exc.reason
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    return "request_error"


def matrix_quality_counts(matrix):
    counts = Counter(road=0, approximate=0, unknown=0, total=0)
    if matrix is not None:
        for i, row in enumerate(matrix.distances):
            for j in range(len(row)):
                if i != j:
                    try:
                        valid_values = numeric(row[j]) is not None and numeric(matrix.durations[i][j]) is not None
                    except (AttributeError, IndexError, TypeError):
                        valid_values = False
                    counts[quality_kind(quality_at(matrix, i, j)) if valid_values else "unknown"] += 1
                    counts["total"] += 1
    return dict(counts)


def _coords(value):
    try:
        return tuple(float(part) for part in value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _customer_key(customer):
    return (str(customer.id), str(getattr(customer, "document", "")), _coords(customer.coordinates))


def route_quality_edges(solution, matrix, matrix_customers=None):
    """Use the original matrix ordering, including depots, not nearest-point guesses.

    Each returned route retains its object identity after output reordering.
    Duplicate/ambiguous input visits are reported as unknown, never guessed.
    """
    locations = [_coords(loc) for loc in getattr(matrix, "locations", [])]
    customers = list(matrix_customers or [])
    depot_count = len(locations) - len(customers) if customers else len(locations)
    customer_indexes = {}
    if customers and depot_count >= 0:
        for index, customer in enumerate(customers, depot_count):
            if _coords(customer.coordinates) == locations[index]:
                customer_indexes.setdefault(_customer_key(customer), []).append(index)

    def index_for(customer, coords):
        if customer is not None and customers:
            indexes = customer_indexes.get(_customer_key(customer), [])
        else:
            indexes = [i for i, loc in enumerate(locations)
                       if loc == _coords(coords) and (customer is not None or i < depot_count)]
        return indexes[0] if len(indexes) == 1 else None

    for route_index, route in enumerate(solution.routes):
        if not route.customers:
            continue
        start = getattr(route, "depot_location", None)
        end = getattr(route, "end_location", None) or start
        stops = [(None, start, "Депо (старт)")]
        stops.extend((customer, customer.coordinates, customer.name) for customer in route.customers)
        stops.append((None, end, "Депо / крайна точка"))
        for position, (origin, target) in enumerate(zip(stops, stops[1:]), 1):
            i, j = index_for(origin[0], origin[1]), index_for(target[0], target[1])
            code = quality_code("unknown", "unknown", "matrix_unavailable" if matrix is None else "lookup_failed")
            distance = duration = None
            if i is not None and j is not None:
                code = quality_at(matrix, i, j)
                try:
                    distance = numeric(matrix.distances[i][j])
                    duration = numeric(matrix.durations[i][j])
                except (IndexError, TypeError):
                    pass
                if distance is None or duration is None:
                    ds, ts, _ = quality_parts(code)
                    code = quality_code(ds if distance is not None else "unknown",
                                        ts if duration is not None else "unknown", "invalid_value")
            yield {
                "route_index": route_index, "route": route, "position": position,
                "from_id": getattr(origin[0], "id", ""), "from_name": origin[2],
                "to_id": getattr(target[0], "id", ""), "to_name": target[2],
                "from_coords": origin[1], "to_coords": target[1],
                "distance_km": distance / 1000 if distance is not None else None,
                "duration_minutes": duration / 60 if duration is not None else None,
                "quality": code,
            }
