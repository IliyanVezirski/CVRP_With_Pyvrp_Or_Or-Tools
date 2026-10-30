"""Read pre-deferral production configs without editing their saved settings."""
import sys
import types
from dataclasses import asdict

from config_compat import compile_external_config, reload_config_module
from customer_deferral import DEFERRAL_FIELDS, validate_deferral_config


LEGACY = '''from dataclasses import dataclass
@dataclass
class CVRPConfig:
    solver_type: str = "pyvrp_experimental"
    allow_customer_skipping: bool = True
    enable_priority_dropping: bool = True
    drop_volume_weight: float = 0.7
    drop_closeness_weight: float = 0.3
    distance_penalty_disjunction: int = 45000
    time_limit_seconds: int = 360
@dataclass
class APIConfig:
    api_port: int = 8085
    web_gui_endpoint: str = "/danone"
'''


def test_old_config_gets_legacy_policy_without_changing_saved_values(tmp_path):
    path = tmp_path / "config.py"
    path.write_text(LEGACY, encoding="utf-8")
    original = path.read_bytes()
    module = types.ModuleType("legacy_deferral_config_test")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    try:
        for _ in range(2):
            reload_config_module(module)
            cfg = module.CVRPConfig()
            validate_deferral_config(cfg)
            assert DEFERRAL_FIELDS <= asdict(cfg).keys()
            assert cfg.customer_dropping_strategy == "legacy"
            assert cfg.solver_type == "pyvrp_experimental"
            assert cfg.drop_volume_weight == 0.7
            assert cfg.drop_closeness_weight == 0.3
            assert cfg.enable_priority_dropping
            assert cfg.time_limit_seconds == 360
            assert module.APIConfig().api_port == 8085
            assert module.APIConfig().web_gui_endpoint == "/danone"
            assert path.read_bytes() == original
    finally:
        sys.modules.pop(module.__name__, None)


def test_existing_geographic_settings_are_never_overwritten():
    source = LEGACY.replace('    time_limit_seconds: int = 360', '''    time_limit_seconds: int = 360
    customer_dropping_strategy: str = "geographic"
    drop_geo_reference_km: float = 35.0
    drop_geo_strength: float = 2.0
    drop_repair_max_ejections: int = 4
    drop_repair_budget_fraction: float = 0.2''')
    scope = {}
    exec(compile_external_config(source, "saved.py"), scope)
    cfg = scope['CVRPConfig']()
    validate_deferral_config(cfg)
    assert cfg.customer_dropping_strategy == "geographic"
    assert cfg.drop_geo_reference_km == 35
    assert cfg.drop_geo_strength == 2
    assert cfg.drop_repair_max_ejections == 4
    assert cfg.drop_repair_budget_fraction == 0.2
