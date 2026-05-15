"""Tests for Risk/Reward calculations."""
import pytest
import math
from services.autopost import _compute_rr_num
from telegram_bot.handlers import _compute_rr_num as handlers_rr_num


class TestRRCalculations:
    """Test RR calculation functions."""
    
    def test_long_rr_calculation(self):
        """Test LONG position RR calculation."""
        entry, sl, tp = 100.0, 95.0, 115.0
        # Risk = entry - sl = 100 - 95 = 5
        # Reward = tp - entry = 115 - 100 = 15
        # RR = 15 / 5 = 3.0
        rr = _compute_rr_num("LONG", entry, sl, tp)
        assert rr == 3.0
    
    def test_short_rr_calculation(self):
        """Test SHORT position RR calculation."""
        entry, sl, tp = 100.0, 105.0, 85.0
        # Risk = sl - entry = 105 - 100 = 5
        # Reward = entry - tp = 100 - 85 = 15
        # RR = 15 / 5 = 3.0
        rr = _compute_rr_num("SHORT", entry, sl, tp)
        assert rr == 3.0
    
    def test_long_rr_handlers(self):
        """Test handlers RR calculation for LONG."""
        entry, sl, tp = 100.0, 95.0, 115.0
        rr = handlers_rr_num("LONG", entry, sl, tp)
        assert rr == 3.0
    
    def test_short_rr_handlers(self):
        """Test handlers RR calculation for SHORT."""
        entry, sl, tp = 100.0, 105.0, 85.0
        rr = handlers_rr_num("SHORT", entry, sl, tp)
        assert rr == 3.0
    
    def test_zero_risk_returns_none(self):
        """Test that zero risk returns None."""
        entry, sl, tp = 100.0, 100.0, 115.0
        rr = _compute_rr_num("LONG", entry, sl, tp)
        assert rr is None
    
    def test_zero_reward_returns_none(self):
        """Test that zero reward returns None."""
        entry, sl, tp = 100.0, 95.0, 100.0
        rr = _compute_rr_num("LONG", entry, sl, tp)
        assert rr is None
    
    def test_negative_risk_returns_none(self):
        """Test that negative risk (sl > entry for LONG) returns None."""
        entry, sl, tp = 100.0, 105.0, 115.0
        rr = _compute_rr_num("LONG", entry, sl, tp)
        assert rr is None
    
    def test_negative_reward_returns_none(self):
        """Test that negative reward (tp < entry for LONG) returns None."""
        entry, sl, tp = 100.0, 95.0, 90.0
        rr = _compute_rr_num("LONG", entry, sl, tp)
        assert rr is None
    
    def test_nan_values_return_none(self):
        """Test that NaN values return None."""
        rr = _compute_rr_num("LONG", math.nan, 95.0, 115.0)
        assert rr is None
        
        rr = _compute_rr_num("LONG", 100.0, math.nan, 115.0)
        assert rr is None
        
        rr = _compute_rr_num("LONG", 100.0, 95.0, math.nan)
        assert rr is None
    
    def test_invalid_direction_returns_none(self):
        """Test that invalid direction returns None."""
        rr = _compute_rr_num("INVALID", 100.0, 95.0, 115.0)
        assert rr is None
    
    def test_rr_one_to_one(self):
        """Test 1:1 RR ratio."""
        entry, sl, tp = 100.0, 95.0, 105.0
        rr = _compute_rr_num("LONG", entry, sl, tp)
        assert rr == 1.0
    
    def test_rr_two_to_one(self):
        """Test 2:1 RR ratio."""
        entry, sl, tp = 100.0, 95.0, 110.0
        rr = _compute_rr_num("LONG", entry, sl, tp)
        assert rr == 2.0
    
    def test_rr_fractional(self):
        """Test fractional RR ratio."""
        entry, sl, tp = 100.0, 95.0, 107.5
        rr = _compute_rr_num("LONG", entry, sl, tp)
        assert rr == 1.5
