"""Client for sending solved route assignments back to Bizant cmd=setData."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from cvrp_solver import CVRPSolution
from vehicle_numbering import format_route_bus_number, order_routes_for_output


logger = logging.getLogger(__name__)


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return ""


def _normalise_url_with_params(base_url: str, params: Dict[str, str]) -> str:
    parts = urllib.parse.urlsplit(base_url)
    query_pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    merged = {key: value for key, value in query_pairs}
    merged.update(params)
    query = urllib.parse.urlencode(merged)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _format_bus_number(
    output_config: Any,
    route_index: int,
    route: Any = None,
    routes: Any = None,
) -> str:
    return format_route_bus_number(output_config, route_index, route=route, routes=routes)


def _format_bukva(template: str, context: Dict[str, Any]) -> str:
    try:
        return str(template or "").format_map(_SafeFormatDict(context))
    except Exception as exc:
        logger.warning("Грешка при форматиране на Bukva шаблон '%s': %s", template, exc)
        return str(context.get("bus_number", "")) + str(context.get("route_number", "")) + str(context.get("stop_number", ""))


def _resolve_unserved_done_flag(set_data: Any) -> str:
    unserved_done_flag = str(getattr(set_data, "set_data_unserved_done_flag", "") or "").strip()
    if unserved_done_flag:
        return unserved_done_flag
    return str(getattr(set_data, "set_data_done_flag", "") or "")


def _coords_match(left: Any, right: Any, tolerance: float = 0.0001) -> bool:
    if not left or not right:
        return False
    try:
        return abs(float(left[0]) - float(right[0])) <= tolerance and abs(float(left[1]) - float(right[1])) <= tolerance
    except (TypeError, ValueError, IndexError):
        return False


def _parse_depot_id_skld_map(raw: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in str(raw or "").replace("\n", ";").split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and value:
            result[name] = value
    return result


def _get_route_depot_name(route_depot: Any, config: Any) -> str:
    locations = getattr(config, "locations", None)
    if not locations:
        return ""

    depots = {
        "Главно депо": getattr(locations, "depot_location", None),
        "Център": getattr(locations, "center_location", None),
        "Враца": getattr(locations, "vratza_depot_location", None),
    }
    depots.update(getattr(locations, "depot_locations", {}) or {})

    for name, coords in depots.items():
        if _coords_match(route_depot, coords):
            return name
    return ""


def resolve_route_id_skld(route: Any, config: Any) -> str:
    """Returns IdSkld based on the route depot."""
    set_data = config.set_data
    locations = getattr(config, "locations", None)
    route_depot = getattr(route, "depot_location", None)
    depot_name = _get_route_depot_name(route_depot, config)
    depot_map = _parse_depot_id_skld_map(getattr(set_data, "set_data_depot_id_skld_map", ""))
    if depot_name and depot_name in depot_map:
        return depot_map[depot_name]

    vratza_depot = getattr(locations, "vratza_depot_location", None) if locations else None

    if _coords_match(route_depot, vratza_depot):
        return str(getattr(set_data, "set_data_vratza_id_skld", "106") or "")

    return str(getattr(set_data, "set_data_id_skld", "128") or "")


def build_set_data_rows(solution: CVRPSolution, config: Any) -> List[Dict[str, str]]:
    """Builds one setData parameter set for each served customer."""
    set_data = config.set_data
    output = config.output
    rows: List[Dict[str, str]] = []

    routes = order_routes_for_output(solution.routes)
    for route_index, route in enumerate(routes):
        route_number = route_index + 1
        bus_number = _format_bus_number(output, route_index, route, routes)
        vehicle_type = getattr(route.vehicle_type, "value", str(route.vehicle_type))
        vehicle_name = str(getattr(route, "vehicle_name", "") or "").strip()
        id_skld = resolve_route_id_skld(route, config)

        for stop_index, customer in enumerate(route.customers):
            stop_number = stop_index + 1
            for doc in _customer_set_data_documents(customer):
                id_plas_doc = _doc_id_plas_doc(customer, doc)
                customer_document = str(doc.get("document", "") or getattr(customer, "document", "") or "")
                context = {
                    "bus_number": bus_number,
                    "route_number": route_number,
                    "stop_number": stop_number,
                    "vehicle_type": vehicle_type,
                    "vehicle_name": vehicle_name,
                    "customer_id": customer.id,
                    "customer_document": customer_document,
                    "id_plas_doc": id_plas_doc,
                    "id_skld": id_skld,
                    "id_grafik": getattr(set_data, "set_data_id_grafik", ""),
                    "done_flag": getattr(set_data, "set_data_done_flag", ""),
                    "volume": doc.get("volume", customer.volume),
                }
                id_grafik_template = str(getattr(set_data, "set_data_id_grafik_template", "") or "").strip()
                id_grafik = (
                    _format_bukva(id_grafik_template, context)
                    if id_grafik_template
                    else str(getattr(set_data, "set_data_id_grafik", "") or "")
                )
                context["id_grafik"] = id_grafik
                rows.append(
                    {
                        "cmd": str(getattr(set_data, "set_data_command", "setData") or "setData"),
                        "IdPlasDoc": id_plas_doc,
                        "DoneFlag": str(getattr(set_data, "set_data_done_flag", "") or ""),
                        "IdSkld": id_skld,
                        "Bukva": _format_bukva(getattr(set_data, "set_data_bukva_template", ""), context),
                        "IdGrafik": id_grafik,
                    }
                )

    return rows


def _customer_set_data_documents(customer: Any) -> List[Dict[str, Any]]:
    documents = list(getattr(customer, "grouped_documents", None) or [])
    result: List[Dict[str, Any]] = []
    seen = set()
    if documents:
        for item in documents:
            if not isinstance(item, dict):
                continue
            document = str(item.get("document", "") or "")
            plas_doc = str(item.get("plas_doc", "") or "")
            key = plas_doc or document
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            result.append(
                {
                    "document": document,
                    "plas_doc": plas_doc,
                    "source_id_skld": str(item.get("source_id_skld", "") or ""),
                    "volume": item.get("volume", getattr(customer, "volume", "")),
                }
            )

    if result:
        return result

    return [
        {
            "document": str(getattr(customer, "document", "") or ""),
            "plas_doc": str(getattr(customer, "plas_doc", "") or ""),
            "source_id_skld": str(getattr(customer, "source_id_skld", "") or ""),
            "volume": getattr(customer, "volume", ""),
        }
    ]


def _doc_id_plas_doc(customer: Any, doc: Dict[str, Any]) -> str:
    return str(
        doc.get("plas_doc", "")
        or doc.get("document", "")
        or getattr(customer, "plas_doc", "")
        or getattr(customer, "document", "")
        or getattr(customer, "id", "")
        or ""
    )


def _unique_customers(customers: List[Any]) -> List[Any]:
    seen = set()
    result = []
    for customer in customers:
        grouped_docs = _customer_set_data_documents(customer)
        doc_key = "|".join(_doc_id_plas_doc(customer, doc) for doc in grouped_docs)
        key = str(doc_key or getattr(customer, "id", "") or id(customer))
        if key in seen:
            continue
        seen.add(key)
        result.append(customer)
    return result


def build_unserved_set_data_rows(customers: List[Any], config: Any) -> List[Dict[str, str]]:
    """Builds setData rows for unserved customers, using IdSkld from the original GET data."""
    set_data = config.set_data
    rows: List[Dict[str, str]] = []

    stop_number = 0
    for customer in _unique_customers(customers):
        for doc in _customer_set_data_documents(customer):
            stop_number += 1
            id_plas_doc = _doc_id_plas_doc(customer, doc)
            id_skld = str(
                doc.get("source_id_skld", "")
                or getattr(customer, "source_id_skld", "")
                or getattr(set_data, "set_data_id_skld", "")
                or ""
            )
            done_flag = _resolve_unserved_done_flag(set_data)
            customer_document = str(doc.get("document", "") or getattr(customer, "document", "") or "")
            context = {
                "bus_number": "",
                "route_number": 0,
                "stop_number": stop_number,
                "vehicle_type": "unserved",
                "customer_id": getattr(customer, "id", ""),
                "customer_document": customer_document,
                "id_plas_doc": id_plas_doc,
                "id_skld": id_skld,
                "id_grafik": getattr(set_data, "set_data_unserved_id_grafik", ""),
                "done_flag": done_flag,
                "volume": doc.get("volume", getattr(customer, "volume", "")),
            }
            id_grafik_template = str(getattr(set_data, "set_data_unserved_id_grafik_template", "") or "").strip()
            id_grafik = (
                _format_bukva(id_grafik_template, context)
                if id_grafik_template
                else str(getattr(set_data, "set_data_unserved_id_grafik", "") or "")
            )
            context["id_grafik"] = id_grafik
            rows.append(
                {
                    "cmd": str(getattr(set_data, "set_data_command", "setData") or "setData"),
                    "IdPlasDoc": id_plas_doc,
                    "DoneFlag": done_flag,
                    "IdSkld": id_skld,
                    "Bukva": _format_bukva(getattr(set_data, "set_data_unserved_bukva_template", ""), context),
                    "IdGrafik": id_grafik,
                }
            )

    return rows


def build_make_group_rows(set_data_rows: List[Dict[str, str]], set_data_config: Any) -> List[Dict[str, str]]:
    """Builds one makeGroup command for each unique IdSkld, preserving order."""
    command = str(getattr(set_data_config, "set_data_make_group_command", "makeGroup") or "makeGroup")
    seen = set()
    rows: List[Dict[str, str]] = []
    for row in set_data_rows:
        id_skld = str(row.get("IdSkld", "") or "")
        if not id_skld or id_skld in seen:
            continue
        seen.add(id_skld)
        rows.append({"cmd": command, "IdSkld": id_skld})
    return rows


def send_set_data_row(params: Dict[str, str], set_data_config: Any) -> Dict[str, Any]:
    """Sends a single setData request and returns a small result summary."""
    base_url = str(getattr(set_data_config, "set_data_url", "") or "").strip()
    if not base_url:
        raise ValueError("set_data_url не е зададен")

    timeout = int(getattr(set_data_config, "set_data_timeout_seconds", 30) or 30)
    method = str(getattr(set_data_config, "set_data_http_method", "GET") or "GET").upper()
    encoded = urllib.parse.urlencode(params).encode("utf-8")

    if method == "POST":
        req = urllib.request.Request(
            base_url,
            data=encoded,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            },
            method="POST",
        )
    else:
        req = urllib.request.Request(
            _normalise_url_with_params(base_url, params),
            headers={"Accept": "application/json, text/plain, */*"},
            method="GET",
        )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read(500).decode("utf-8", errors="replace")
        return {"status_code": response.status, "response_preview": body}


def upload_solution_set_data(solution: CVRPSolution, config: Any, warehouse_allocation: Any = None) -> Dict[str, Any]:
    """Uploads all served customers to Bizant setData when enabled."""
    set_data = getattr(config, "set_data", None)
    if not set_data or not getattr(set_data, "enable_set_data_upload", False):
        return {"enabled": False, "attempted": 0, "succeeded": 0, "failed": 0}

    served_rows = build_set_data_rows(solution, config)
    unserved_customers = list(getattr(solution, "dropped_customers", []) or [])
    if warehouse_allocation is not None:
        unserved_customers.extend(list(getattr(warehouse_allocation, "warehouse_customers", []) or []))
    unserved_rows = (
        build_unserved_set_data_rows(unserved_customers, config)
        if getattr(set_data, "enable_unserved_set_data_upload", True)
        else []
    )
    rows = served_rows + unserved_rows
    result = {
        "enabled": True,
        "attempted": len(rows),
        "succeeded": 0,
        "failed": 0,
        "served_attempted": len(served_rows),
        "unserved_attempted": len(unserved_rows),
        "errors": [],
    }
    logger.info("Изпращам %s setData заявки към Bizant", len(rows))

    for params in rows:
        try:
            response = send_set_data_row(params, set_data)
            result["succeeded"] += 1
            logger.debug("setData OK: %s -> %s", params, response)
        except Exception as exc:
            result["failed"] += 1
            error = {"IdPlasDoc": params.get("IdPlasDoc", ""), "error": str(exc)}
            result["errors"].append(error)
            logger.error("setData грешка за IdPlasDoc=%s: %s", params.get("IdPlasDoc", ""), exc)

    if len(result["errors"]) > 20:
        result["errors"] = result["errors"][:20]
        result["errors_truncated"] = True

    make_group_enabled = bool(getattr(set_data, "enable_make_group", True))
    make_group_rows = build_make_group_rows(rows, set_data) if make_group_enabled else []
    result["make_group"] = {"enabled": make_group_enabled, "attempted": 0, "succeeded": 0, "failed": 0, "errors": []}
    if not make_group_enabled:
        logger.info("makeGroup е изключен от настройките.")
    elif result["failed"] == 0:
        logger.info("Изпращам %s makeGroup заявки към Bizant", len(make_group_rows))
        for params in make_group_rows:
            result["make_group"]["attempted"] += 1
            try:
                response = send_set_data_row(params, set_data)
                result["make_group"]["succeeded"] += 1
                logger.debug("makeGroup OK: %s -> %s", params, response)
            except Exception as exc:
                result["make_group"]["failed"] += 1
                error = {"IdSkld": params.get("IdSkld", ""), "error": str(exc)}
                result["make_group"]["errors"].append(error)
                logger.error("makeGroup грешка за IdSkld=%s: %s", params.get("IdSkld", ""), exc)
    else:
        logger.warning("Пропускам makeGroup, защото има неуспешни setData заявки.")

    logger.info("setData резултат: %s", json.dumps(result, ensure_ascii=False))
    return result
