"""Tests for P&L calculations."""
import pytest
from services.pnl import calc_pnl_usd, calc_rr_realized
from services.trade_engine import _close_pnl


class TestPnLCalculations:
    """Test P&L calculation functions."""
    
    def test_long_winning_trade(self):
        """Test P&L for winning LONG trade."""
        entry, sl, close = 100.0, 95.0, 110.0
        size_usd, fees_bps = 100.0, 10
        
        rr, pnl = calc_pnl_usd(entry, sl, close, "LONG", size_usd, fees_bps)
        
        # Risk = 100 - 95 = 5, Move = 110 - 100 = 10, RR = 10/5 = 2.0
        assert rr == 2.0
        # Risk_pct = 5/100 = 0.05, Risk_usd = 100 * 0.05 = 5
        # Fees = (10/10000) * 100 = 0.1
        # PnL = 2.0 * 5 - 0.1 = 9.9
        assert pnl is not None
        assert pnl > 0
    
    def test_long_losing_trade(self):
        """Test P&L for losing LONG trade."""
        entry, sl, close = 100.0, 95.0, 93.0
        size_usd, fees_bps = 100.0, 10
        
        rr, pnl = calc_pnl_usd(entry, sl, close, "LONG", size_usd, fees_bps)
        
        # Move = 93 - 100 = -7, Risk = 5, RR = -7/5 = -1.4
        assert rr is not None
        assert rr < 0
        assert pnl is not None
        assert pnl < 0
    
    def test_short_winning_trade(self):
        """Test P&L for winning SHORT trade."""
        entry, sl, close = 100.0, 105.0, 90.0
        size_usd, fees_bps = 100.0, 10
        
        rr, pnl = calc_pnl_usd(entry, sl, close, "SHORT", size_usd, fees_bps)
        
        # Risk = 105 - 100 = 5, Move = 100 - 90 = 10, RR = 10/5 = 2.0
        assert rr == 2.0
        assert pnl is not None
        assert pnl > 0
    
    def test_short_losing_trade(self):
        """Test P&L for losing SHORT trade."""
        entry, sl, close = 100.0, 105.0, 107.0
        size_usd, fees_bps = 100.0, 10
        
        rr, pnl = calc_pnl_usd(entry, sl, close, "SHORT", size_usd, fees_bps)
        
        assert rr is not None
        assert rr < 0
        assert pnl is not None
        assert pnl < 0
    
    def test_rr_realized_long(self):
        """Test RR realized calculation for LONG."""
        entry, sl, close = 100.0, 95.0, 110.0
        rr = calc_rr_realized(entry, sl, close, "LONG")
        assert rr == 2.0
    
    def test_rr_realized_short(self):
        """Test RR realized calculation for SHORT."""
        entry, sl, close = 100.0, 105.0, 90.0
        rr = calc_rr_realized(entry, sl, close, "SHORT")
        assert rr == 2.0
    
    def test_rr_realized_zero_risk(self):
        """Test RR realized with zero risk."""
        entry, sl, close = 100.0, 100.0, 110.0
        rr = calc_rr_realized(entry, sl, close, "LONG")
        assert rr is None
    
    def test_close_pnl_long(self):
        """Test _close_pnl for LONG trade."""
        direction = "LONG"
        entry, close = 100.0, 110.0
        size_usd, fees_bps = 100.0, 10
        
        pnl_usd, pnl_pct = _close_pnl(direction, entry, close, size_usd, fees_bps)
        
        # qty = 100 / 100 = 1.0
        # gross = (110 - 100) * 1.0 = 10.0
        # notional = 1.0 * 100 + 1.0 * 110 = 210
        # fees = (10/10000) * 210 = 0.21
        # pnl_usd = 10.0 - 0.21 = 9.79
        # pnl_pct = (9.79 / 100) * 100 = 9.79
        assert pnl_usd > 0
        assert pnl_pct > 0
    
    def test_close_pnl_short(self):
        """Test _close_pnl for SHORT trade."""
        direction = "SHORT"
        entry, close = 100.0, 90.0
        size_usd, fees_bps = 100.0, 10
        
        pnl_usd, pnl_pct = _close_pnl(direction, entry, close, size_usd, fees_bps)
        
        # qty = 100 / 100 = 1.0
        # gross = (100 - 90) * 1.0 = 10.0
        # notional = 1.0 * 100 + 1.0 * 90 = 190
        # fees = (10/10000) * 190 = 0.19
        # pnl_usd = 10.0 - 0.19 = 9.81
        assert pnl_usd > 0
        assert pnl_pct > 0
    
    def test_close_pnl_breakeven(self):
        """Test _close_pnl at breakeven."""
        direction = "LONG"
        entry, close = 100.0, 100.0
        size_usd, fees_bps = 100.0, 10
        
        pnl_usd, pnl_pct = _close_pnl(direction, entry, close, size_usd, fees_bps)
        
        # Fees still apply, so negative P&L
        assert pnl_usd < 0
        assert pnl_pct < 0
    
    def test_zero_fees(self):
        """Test P&L calculation with zero fees."""
        entry, sl, close = 100.0, 95.0, 110.0
        size_usd, fees_bps = 100.0, 0
        
        rr, pnl = calc_pnl_usd(entry, sl, close, "LONG", size_usd, fees_bps)
        
        assert rr == 2.0
        # Without fees, PnL should be exactly 2.0 * risk_usd
        # Risk_usd = 100 * 0.05 = 5
        # PnL = 2.0 * 5 = 10.0
        assert pnl == 10.0

    def test_partial_pnl_uses_partial_size(self):
        """Partial TP should book only the requested slice of position P&L."""
        entry, sl, close = 100.0, 95.0, 105.0
        rr, pnl = calc_pnl_usd(entry, sl, close, "LONG", 100.0, 0, partial_pct=0.5)

        assert rr == 1.0
        assert pnl == 2.5
