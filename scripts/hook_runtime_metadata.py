# PyInstaller runtime hook — run before any user imports
#
# Fix 1: patch importlib.metadata.version() with a narrow safe wrapper:
#         only returns "0.0.0" when the module code exists in the bundle
#         but has no distribution metadata; re-raises PackageNotFoundError
#         for packages that are genuinely absent so that lightning_utilities
#         RequirementCache() correctly marks them as unavailable.
# Fix 2: nagisa bare imports — add nagisa package dir to sys.path.

import sys
import os
import importlib.metadata as _imeta
import importlib.util as _ilu

# ── Fix 1: narrow importlib.metadata.version ─────────────────────────────────
_orig_version = _imeta.version


def _safe_version(package_name: str) -> str:
    try:
        return _orig_version(package_name)
    except _imeta.PackageNotFoundError:
        # Only pretend the package is version "0.0.0" when its module code
        # is actually present in the bundle (e.g. bundled without dist-info).
        # If find_spec() finds nothing, the package is genuinely absent —
        # re-raise so RequirementCache() reports it as unavailable.
        try:
            spec = _ilu.find_spec(package_name)
        except ModuleNotFoundError:
            spec = None
        if spec is not None:
            return "0.0.0"
        raise


_imeta.version = _safe_version

# ── Fix 2: nagisa bare imports ────────────────────────────────────────────────
try:
    _nagisa_spec = _ilu.find_spec("nagisa")
    if _nagisa_spec and _nagisa_spec.submodule_search_locations:
        _nagisa_dir = list(_nagisa_spec.submodule_search_locations)[0]
        if _nagisa_dir not in sys.path:
            sys.path.insert(0, _nagisa_dir)
except Exception:
    pass
