"""Shared per-visit service policy. Input overrides are expressed in minutes."""
from decimal import Decimal, ROUND_FLOOR
from math import isfinite


SERVICE_FIELDS = {
    "service_time_mode": "fixed",
    "service_base_seconds": 0.0,
    "service_seconds_per_unit": 12.0,
    "service_quantity_units": 1.0,
    "service_max_seconds": 900.0,
}
SERVICE_FORMULA_CAPABILITY = "service_time_formula_v1"


def service_settings(vehicle):
    return {key: getattr(vehicle, key, default) for key, default in SERVICE_FIELDS.items()}


def validate_service_settings(vehicle):
    settings = service_settings(vehicle)
    if settings["service_time_mode"] not in {"fixed", "formula"}:
        raise ValueError("Обслужване: избери Фиксирано или Формула.")
    for key, label in (
        ("service_base_seconds", "Начално време"),
        ("service_seconds_per_unit", "Секунди"),
        ("service_quantity_units", "За единици"),
        ("service_max_seconds", "Максимално време"),
    ):
        value = settings[key]
        try:
            number = float(value)
        except (ValueError, TypeError, OverflowError):
            raise ValueError(f"{label}: въведи валидно число.") from None
        if isinstance(value, bool) or not isfinite(number) or number < 0:
            raise ValueError(f"{label}: въведи крайно неотрицателно число.")
        if key == "service_quantity_units" and number == 0:
            raise ValueError("За единици: стойността трябва да е по-голяма от 0.")
    return settings


def service_signature(vehicle):
    settings = validate_service_settings(vehicle)
    if settings["service_time_mode"] == "fixed":
        return ("fixed", float(getattr(vehicle, "service_time_minutes", 15)))
    return tuple(settings.values())


def service_description(vehicle):
    if getattr(vehicle, "service_time_mode", "fixed") != "formula":
        return f"{float(getattr(vehicle, 'service_time_minutes', 15)):g} мин/клиент"
    s = service_settings(vehicle)
    return (f"Формула: {float(s['service_base_seconds']):g} + количество × "
            f"{float(s['service_seconds_per_unit']):g} / {float(s['service_quantity_units']):g}, "
            f"до {float(s['service_max_seconds']):g} сек")


def formula_seconds(quantity, vehicle):
    settings = validate_service_settings(vehicle)
    try:
        quantity = Decimal(str(quantity))
    except Exception:
        raise ValueError("Невалидно количество за формулата за обслужване.") from None
    if not quantity.is_finite() or quantity < 0:
        raise ValueError("Количеството за обслужване трябва да е неотрицателно.")
    base, seconds, units, maximum = (
        Decimal(str(settings[key])) for key in (
            "service_base_seconds", "service_seconds_per_unit",
            "service_quantity_units", "service_max_seconds",
        )
    )
    # Decimal avoids flooring 305.99999999999994 instead of the intended 306.
    return int(min(base + quantity * seconds / units, maximum).to_integral_value(rounding=ROUND_FLOOR))


def customer_service_seconds(customer, vehicle=None, default_minutes=15):
    """Explicit input (including zero) wins; the formula cap never clips it."""
    override = getattr(customer, "service_time_minutes", None)
    if override is not None and not isinstance(override, bool):
        try:
            value = float(override)
            if isfinite(value) and value >= 0:
                return value * 60
        except (ValueError, TypeError, OverflowError):
            pass
    if getattr(vehicle, "service_time_mode", "fixed") == "formula":
        return formula_seconds(getattr(customer, "volume", 0), vehicle)
    return float(getattr(vehicle, "service_time_minutes", default_minutes) or 0) * 60


def minimum_service_seconds(customers, vehicle):
    # Used only for safe upper bounds on trips, never an average service time.
    return min((customer_service_seconds(c, vehicle) for c in customers), default=0)
