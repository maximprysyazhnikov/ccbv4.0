from services.autopost.formatting import format_message_text

def test_mode_line_scalping():
    txt = format_message_text(
        symbol="FOGOUSDT",
        direction="SHORT",
        timeframe="5m",
        entry=0.03539,
        sl=0.035514,
        tp=0.034877,
        rr_t=3.5,
        ind_sum={},
        gate_score=10,
        gate_total=14,
        trade_mode="scalping",
    )
    assert "СКАЛЬП" in txt


def test_mode_line_standard():
    txt = format_message_text(
        symbol="SOLUSDT",
        direction="LONG",
        timeframe="5m",
        entry=118.46,
        sl=118.05,
        tp=120.18,
        rr_t=3.5,
        ind_sum={},
        gate_score=11,
        gate_total=14,
        trade_mode="standard",
    )
    assert "КЛАСИЧНИЙ" in txt
