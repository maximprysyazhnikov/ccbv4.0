from services.analyzer_core import evaluate_gate
from tests.conftest import sample_indicators

data = sample_indicators()
res = evaluate_gate(data, 'LONG')
print(res)
print('reasons:', res.get('reasons'))