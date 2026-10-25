"""Entry point for the isolated, pinned PyVRP 1.x alpha worker."""
from pyvrp_backends import ALPHA
from pyvrp_next_worker import main


if __name__ == "__main__":
    raise SystemExit(main(backend=ALPHA))
