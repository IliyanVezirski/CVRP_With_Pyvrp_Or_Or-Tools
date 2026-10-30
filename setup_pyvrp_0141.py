"""Install only .venv-pyvrp-0141; never upgrade the 0.13/0.14.0 runtimes."""
import setup_pyvrp_next as setup
from pyvrp_backends import PATCH_0141

def main(argv=None):
    setup.PYVRP_NEXT_VERSION = PATCH_0141.version
    setup.NEXT_VENV_DIR = setup.PROJECT_DIR / ".venv-pyvrp-0141"
    setup.BUILD_SCRIPT = "build_pyvrp_0141_worker.py"
    return setup.main(argv)

if __name__ == "__main__":
    raise SystemExit(main())
