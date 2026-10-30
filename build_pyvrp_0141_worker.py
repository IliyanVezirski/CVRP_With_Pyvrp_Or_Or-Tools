"""Build the independent 0.14.1 sidecar with the shared packaging recipe."""
import build_pyvrp_next_worker as build
from pyvrp_backends import PATCH_0141

def main():
    build.PYVRP_NEXT_VERSION = PATCH_0141.version
    build.WORKER_BACKEND = PATCH_0141.protocol
    build.DIST_DIR = build.DIST_ROOT / PATCH_0141.directory
    build.BUILD_DIR = build.BUILD_ROOT / PATCH_0141.directory
    build.SPEC_DIR = build.BUILD_DIR / "spec"
    build.WORKER_SCRIPT = build.PROJECT_DIR / PATCH_0141.script
    build.WORKER_NAME = "CVRP_PyVRP_0141_Worker"
    build.WORKER_PACKAGE_DIR = build.DIST_DIR / build.WORKER_NAME
    return build.main()

if __name__ == "__main__":
    raise SystemExit(main())
