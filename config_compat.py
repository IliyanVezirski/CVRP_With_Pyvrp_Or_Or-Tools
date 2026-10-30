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
    # Add new solver choices/fields in memory only. Existing saved values win.
    tree = ast.parse(source)
    additions = {
        "CVRPConfig": {
            "pyvrp_0141_worker_path": ('str', ''),
            # Older installations predate geographic deferral. Keep their
            # original policy; supply only the missing schema in memory.
            "customer_dropping_strategy": ('str', 'legacy'),
            "drop_geo_reference_km": ('float', 20.0),
            "drop_geo_strength": ('float', 1.0),
            "drop_repair_max_ejections": ('int', 6),
            "drop_repair_budget_fraction": ('float', 0.15),
        },
        "APIConfig": {"tsp_solver_type": ('str', 'pyvrp_experimental')},
        "InputConfig": {"excluded_document_ids": ('str', '')},
        "SetDataConfig": {"enable_excluded_set_data_upload": ('bool', True)},
    }
    for node in reversed(tree.body):
        if isinstance(node, ast.ClassDef) and node.name in additions:
            fields = {item.target.id for item in node.body
                      if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
            missing = {k:v for k,v in additions[node.name].items() if k not in fields}
            if missing:
                lines = source.splitlines(keepends=True)
                before = ''.join(lines[:node.end_lineno]).rstrip('\r\n')
                extra = ''.join(f'\n    {k}: {v[0]} = {v[1]!r}' for k,v in missing.items())
                source = before + extra + '\n' + ''.join(lines[node.end_lineno:])
    source += '\n' + '\n'.join([
        'if "CVRP_SOLVER_TYPES" in globals() and "pyvrp_0141" not in CVRP_SOLVER_TYPES:',
        '    CVRP_SOLVER_TYPES = tuple(CVRP_SOLVER_TYPES) + ("pyvrp_0141",)',
        'if "PYVRP_SOLVER_TYPES" in globals():',
        '    PYVRP_SOLVER_TYPES = frozenset(PYVRP_SOLVER_TYPES) | {"pyvrp_0141"}',
    ]) + '\n'
    # Runtime-only schema upgrade; deployment never rewrites saved settings.
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "APIConfig":
            fields = {item.target.id for item in node.body
                      if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
            if "tsp_search_seconds" not in fields:
                lines = source.splitlines(keepends=True)
                before = "".join(lines[:node.end_lineno]).rstrip("\r\n")
                source = before + '\n    tsp_search_seconds: float = 10.0\n' + "".join(lines[node.end_lineno:])
            break
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
    from profile_runtime import apply_identity
    if hasattr(module,'get_config'): apply_identity(module.get_config())
    return module
