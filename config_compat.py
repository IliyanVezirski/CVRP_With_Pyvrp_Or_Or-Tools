"""Read legacy config schemas without changing their saved settings."""
import ast
from pathlib import Path

VEHICLE_SCHEMA_DEFAULTS = {
    "physical_bus_schedules": ("list", None),
    "service_time_mode": ("str", "fixed"),
    "service_base_seconds": ("float", 0.0),
    "service_seconds_per_unit": ("float", 12.0),
    "service_quantity_units": ("float", 1.0),
    "service_max_seconds": ("float", 900.0),
}


def ensure_vehicle_schema(source: str) -> str:
    """Add missing field declarations, preserving all existing source values."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "VehicleConfig":
            continue
        existing = {item.target.id for item in node.body
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
        missing = [name for name in VEHICLE_SCHEMA_DEFAULTS if name not in existing]
        if not missing:
            return source
        newline = "\r\n" if "\r\n" in source else "\n"
        indent = " " * (node.col_offset + 4)
        additions = "".join(
            f"{indent}{name}: {VEHICLE_SCHEMA_DEFAULTS[name][0]} = {VEHICLE_SCHEMA_DEFAULTS[name][1]!r}{newline}"
            for name in missing
        )
        before = "".join(lines[:node.end_lineno])
        if not before.endswith(("\n", "\r")):
            before += newline
        return before + additions + "".join(lines[node.end_lineno:])
    return source


def compile_external_config(source: str, filename: str):
    source = ensure_vehicle_schema(source)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "InputConfig":
            existing = {item.target.id for item in node.body
                        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
            if "excluded_customer_ids" not in existing:
                lines = source.splitlines(keepends=True)
                before = "".join(lines[:node.end_lineno]).rstrip("\r\n")
                source = before + '\n    excluded_customer_ids: str = ""\n' + "".join(lines[node.end_lineno:])
            break
    return compile(source, filename, "exec")


def reload_config_module(module):
    """Reload in place so existing import references stay valid; never write disk."""
    path = Path(module.__file__)
    exec(compile_external_config(path.read_text(encoding="utf-8-sig"), str(path)), module.__dict__)
    return module
