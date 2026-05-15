import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

MODULES = [
    'services.autopost_bridge',
    'services.autopost',
    'services.autopost.core',
    'services.autopost.formatting',
    'services.autopost.scoring',
]

errors = []
for m in MODULES:
    try:
        mod = __import__(m, fromlist=['*'])
        print(f"Imported {m}")
    except Exception as e:
        errors.append((m, e))
        print(f"Failed import {m}: {e}")

# quick runtime checks
try:
    from services.autopost_bridge import _parse
    print("_parse smoke ok")
except Exception as e:
    errors.append(("_parse", e))

try:
    # services/autopost exists as both a module file and a package directory — import from file path to avoid ambiguity
    import importlib.util
    import os
    ap_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'services', 'autopost.py'))
    spec = importlib.util.spec_from_file_location("autopost_file", ap_path)
    ap_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ap_mod)
    _compute_rr_num = getattr(ap_mod, '_compute_rr_num')
    exp1 = ((120-100)/(100-90))
    exp2 = ((100-80)/(110-100))
    assert abs(_compute_rr_num('LONG', 100, 90, 120) - exp1) < 1e-9
    assert abs(_compute_rr_num('SHORT', 100, 110, 80) - exp2) < 1e-9
    print("_compute_rr_num smoke ok (from file)")
except Exception as e:
    errors.append(("_compute_rr_num", e))

if errors:
    print('Errors found:')
    for m,e in errors:
        print(m, e)
    raise SystemExit(1)

print('smoke import checks passed')
