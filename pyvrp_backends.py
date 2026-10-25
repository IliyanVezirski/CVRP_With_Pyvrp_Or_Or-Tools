"""Immutable identities for isolated PyVRP runtimes; never substitute versions."""
from dataclasses import dataclass

from pyvrp_next_version import PYVRP_NEXT_VERSION


@dataclass(frozen=True)
class PyVRPBackend:
    public: str
    protocol: str
    version: str
    prefix: str
    directory: str
    script: str
    executable: str
    path_setting: str
    source_commit: str = ""


STABLE = PyVRPBackend(
    "pyvrp_experimental", "pyvrp_next", PYVRP_NEXT_VERSION, "0.14",
    "pyvrp-next", "pyvrp_next_worker.py", "CVRP_PyVRP_Next_Worker.exe",
    "pyvrp_next_worker_path",
)
ALPHA = PyVRPBackend(
    "pyvrp_one", "pyvrp_one", "1.0.0a0", "1.0",
    "pyvrp-one", "pyvrp_one_worker.py", "CVRP_PyVRP_One_Worker.exe",
    "pyvrp_one_worker_path", "2c1b582e7ea9bb4cc8cd986f784d4c25b9ebdd1b",
)


def backend_for(config):
    return ALPHA if getattr(config, "solver_type", "") == ALPHA.public else STABLE
