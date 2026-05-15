import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from services.autopost_bridge import _parse

BASIC = """
🤖 Autopost plan BTCUSDT [1h]
Dir: LONG | RR≈2.42
Entry: 45000.0 | SL: 44000.0 | TP: 47000.0
"""

EMOJI_SHORT = """
🤖 Autopost plan ETHUSDT [1h]
🔴 SHORT | RR:1.80
Entry: 2000.0 | SL: 2050.0 | TP: 1900.0
"""

SEPARATE_TOKENS = """
🤖 Autopost plan BNBUSDT [4h]
Direction: BUY
RR=2.00
Entry: 300.0 | SL: 290.0 | TP: 360.0
"""

INVALID = """
🤖 Autopost plan AAAA [1h]
Entry: 1.0 | SL: 0.9 | TP: 1.1
"""

cases = [
    (BASIC, True, "LONG", 2.42),
    (EMOJI_SHORT, True, "SHORT", 1.8),
    (SEPARATE_TOKENS, True, "LONG", 2.0),
    (INVALID, False, None, None),
]

for i, (txt, should, exp_dir, exp_rr) in enumerate(cases, 1):
    p = _parse(txt)
    ok = (p is not None) if should else (p is None)
    print(f"case {i}: parsed={'YES' if p is not None else 'NO'} expected={'YES' if should else 'NO'}")
    if p and should:
        print(f"  dir={p['direction']} (exp={exp_dir}), rr={p['rr']} (exp={exp_rr})")
        assert p['direction'] == exp_dir
        assert abs(p['rr'] - exp_rr) < 1e-6
print('smoke tests passed')
