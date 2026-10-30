"""Pinned 0.14.1 worker using the shared, rule-preserving model/validation."""
from pyvrp_backends import PATCH_0141
from pyvrp_next_worker import main

if __name__ == "__main__":
    raise SystemExit(main(backend=PATCH_0141))
