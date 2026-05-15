"""Auto-post service package."""
from services.autopost.core import run_autopost_once
from services.autopost.persistence import mark_autopost_sent

# Provide _compute_rr_num on the package level for tests. Import it lazily from the
# top-level module file to avoid circular import between package and module.
try:
    import importlib.util
    import os
    ap_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'autopost.py'))
    spec = importlib.util.spec_from_file_location("autopost_file", ap_path)
    _autopost_file = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_autopost_file)
    _compute_rr_num = getattr(_autopost_file, '_compute_rr_num')
except Exception:
    # Fallback: don't fail import if we cannot load the helper (tests may handle it)
    _compute_rr_num = None

__all__ = ["run_autopost_once", "mark_autopost_sent", "_compute_rr_num"]
