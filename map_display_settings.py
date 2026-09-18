"""Display-only map annotations, compatible with preserved runtime configs."""

from display_units import total_volume_unit_label, customer_volume_unit_label, validate_context_volume_label

DEFAULT_MANDATORY_CUSTOMER_COMMENT = "Задължителен клиент — не пропускайте доставката."


def validate_driver_volume_label(value):
    return validate_context_volume_label(value)


def driver_total_volume_label(output_config):
    return (validate_driver_volume_label(getattr(output_config, "driver_total_volume_label", ""))
            or total_volume_unit_label(output_config).capitalize())


def driver_customer_volume_label(output_config, *, short=False):
    return customer_volume_unit_label(output_config, short=short)


def mandatory_customer_comment(output_config):
    value = getattr(output_config, "mandatory_customer_comment", None)
    return str(value or DEFAULT_MANDATORY_CUSTOMER_COMMENT).strip() or DEFAULT_MANDATORY_CUSTOMER_COMMENT


def validate_mandatory_customer_comment(value):
    text = str(value or "").strip()
    if not text or len(text) > 200:
        raise ValueError("Текстът за задължителен клиент трябва да е от 1 до 200 знака.")
    return text
