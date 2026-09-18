"""Display-only quantity terminology; never changes solver values or API keys."""


def volume_unit_label(output_config=None, *, short=False):
    if output_config is None:
        from config import get_config
        output_config = getattr(get_config(), "output", None)
    label = " ".join(str(getattr(output_config, "volume_unit_label", "") or "").split())
    if not label or label.casefold() in {"стекове", "ст.", "ст"}:
        return "ст." if short else "стекове"
    return label


def validate_volume_unit_label(value):
    label = " ".join(str(value or "").split())
    if not label or len(label) > 40:
        raise ValueError("Наименованието на количеството трябва да е от 1 до 40 знака.")
    return label


def validate_context_volume_label(value):
    label = " ".join(str(value or "").split())
    if len(label) > 40:
        raise ValueError("Надписът за количество трябва да е до 40 знака.")
    return label


def _context_unit(output_config, field, short):
    if output_config is None:
        from config import get_config
        output_config = getattr(get_config(), "output", None)
    # Retain the original setting keys so saved profiles remain compatible.
    return (validate_context_volume_label(getattr(output_config, field, ""))
            or volume_unit_label(output_config, short=short))


def total_volume_unit_label(output_config=None, *, short=False):
    """Unit for vehicle capacities, route loads and aggregated quantities."""
    return _context_unit(output_config, "driver_total_volume_label", short)


def customer_volume_unit_label(output_config=None, *, short=False):
    """Unit for one customer's quantity, in every display/export surface."""
    return _context_unit(output_config, "driver_customer_volume_label", short)
