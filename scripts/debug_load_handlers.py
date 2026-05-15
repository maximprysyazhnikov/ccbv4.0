import importlib.util, os, traceback
path=os.path.abspath(os.path.join('telegram_bot','handlers.py'))
print('PATH', path)
try:
    spec=importlib.util.spec_from_file_location('handlers_file', path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print('Loaded OK, has _compute_rr_num?', hasattr(mod,'_compute_rr_num'))
except Exception:
    print('FAILED:')
    traceback.print_exc()
