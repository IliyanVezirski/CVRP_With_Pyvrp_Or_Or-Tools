"""Optional-customer policy. Geography is a priority, not a forecast of tomorrow."""
from __future__ import annotations

import logging
import math
import re

logger = logging.getLogger(__name__)
GEOGRAPHIC_CAPABILITY = "geographic_deferral_v1"
DEFERRAL_LABELS = {"legacy": "Обем и близост до депо", "geographic": "Географски приоритет"}
DEFERRAL_FIELDS = {
    "customer_dropping_strategy", "drop_geo_reference_km", "drop_geo_strength",
    "drop_repair_max_ejections", "drop_repair_budget_fraction",
}


def geographic_enabled(config):
    return getattr(config, "customer_dropping_strategy", "legacy") == "geographic"


def validate_deferral_config(config):
    mode = getattr(config, "customer_dropping_strategy", "legacy")
    if mode not in {"legacy", "geographic"}:
        raise ValueError("Начин на пропускане: legacy (обем и близост) или geographic (географски приоритет).")
    if not geographic_enabled(config):
        return
    if config.solver_type not in {"pyvrp_experimental", "pyvrp_one"}:
        raise ValueError("Новото географско пропускане изисква PyVRP 0.14 или 1.x.")
    for name, low, high in (
        ("drop_geo_reference_km", 1, 1000),
        ("drop_geo_strength", 0, 10),
        ("drop_repair_max_ejections", 1, 12),
        ("drop_repair_budget_fraction", 0.01, 0.30),
        ("distance_penalty_disjunction", 1, 100000000),
    ):
        value = float(getattr(config, name))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{name} трябва да бъде между {low} и {high}.")
        if name == "drop_repair_max_ejections" and not value.is_integer():
            raise ValueError("Максималният брой групово заменени клиенти трябва да е цяло число.")


def prepare_customer_depots(customers, config):
    """Resolve source warehouses once in parent, before the IPC pickle boundary.

    Missing source uses the configured main depot (logged). Unknown explicit
    IDs fail rather than silently using an unrelated closest depot. A grouped
    visit containing several warehouses receives the highest radial priority.
    """
    from config import get_named_depots
    from setdata_client import _parse_depot_id_skld_map

    named = get_named_depots(config.locations)
    mappings = {
        "Главно депо": str(config.set_data.set_data_id_skld),
        "Враца": str(config.set_data.set_data_vratza_id_skld),
    }
    mappings.update(_parse_depot_id_skld_map(config.set_data.set_data_depot_id_skld_map))
    by_id = {}
    for name, stock in mappings.items():
        coords = named.get(name)
        if not coords or not stock:
            continue
        coords = tuple(coords)
        if stock in by_id and by_id[stock] != coords:
            raise ValueError(f"Склад {stock} сочи към различни депа; коригирайте Депо → IdSkld.")
        by_id[stock] = coords
    missing = multi = 0
    for customer in customers:
        sources = [getattr(customer, "source_id_skld", "")]
        sources += [doc.get("source_id_skld", "") for doc in
                    (getattr(customer, "grouped_documents", None) or [])]
        ids = {v.strip() for raw in sources for v in re.split(r"[;,\s]+", str(raw or "")) if v.strip()}
        unknown = ids - by_id.keys()
        if unknown:
            raise ValueError("Няма депо за складов код: " + ", ".join(sorted(unknown)))
        depots = sorted({by_id[stock] for stock in ids})
        if not depots:
            depots = [tuple(config.locations.depot_location)]
            missing += 1
        multi += len(depots) > 1
        customer._deferral_depots = depots
    logger.info("Geographic deferral: main-depot fallback=%s; multi-warehouse visits=%s", missing, multi)


def geographic_penalties(customers, depots, matrix, config):
    """Stable quadratic road-distance priority; volume does not lower protection.

    d is the mean of outbound/inbound distance, in km. Base*(1+s*(d/r)^2).
    This deliberately allows one distant visit to outweigh several nearby ones.
    """
    validate_deferral_config(config)
    indices = {tuple(coords): idx for idx, coords in enumerate(depots)}
    penalties = []
    for offset, customer in enumerate(customers):
        own = getattr(customer, "_deferral_depots", None)
        if not own:
            # Direct/synthetic single-depot use is unambiguous. Production
            # multi-depot requests must have been resolved by the parent.
            if len(indices) != 1:
                raise ValueError("Географският режим няма определен изходен склад за клиент.")
            own = list(indices)
        if any(tuple(coords) not in indices for coords in own):
            raise ValueError("Изходното депо на клиент липсва в пътната матрица; активирайте бус от него.")
        client_idx = len(depots) + offset
        values = []
        for coords in own:
            depot_idx = indices[tuple(coords)]
            legs = (float(matrix.distances[depot_idx][client_idx]),
                    float(matrix.distances[client_idx][depot_idx]))
            if any(not math.isfinite(v) or v < 0 or v >= 1e9 for v in legs):
                raise ValueError("Невалидно/недостижимо разстояние за географски приоритет.")
            values.append(sum(legs) / 2000)
        distance = max(values)
        prize = round(config.distance_penalty_disjunction *
                      (1 + config.drop_geo_strength * (distance / config.drop_geo_reference_km) ** 2))
        if prize > 1e12:
            raise ValueError("Прекалено голям географски приоритет; намалете силата или увеличете мащаба.")
        customer._deferral_distance_km = distance
        customer._deferral_prize = max(1, prize)
        penalties.append(max(1, prize))
    logger.info("Geographic deferral: %s visits, prize range=%s..%s (not tomorrow cost)",
                len(customers), min(penalties, default=0), max(penalties, default=0))
    return penalties


def solution_selection_key(solution, config):
    if geographic_enabled(config):
        score = float(getattr(solution, "deferral_selection_score", float("inf")))
        return (score,)
    if str(config.objective_metric).lower() in {"time", "duration", "fastest", "shortest_time"}:
        return (len(solution.dropped_customers), solution.fitness_score)
    return (solution.fitness_score,)
