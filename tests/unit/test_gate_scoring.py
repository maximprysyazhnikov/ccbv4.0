"""Tests for gate scoring system."""
import pytest
from services.analyzer_core import evaluate_gate


class TestGateScoring:
    """Test gate evaluation function."""
    
    def test_perfect_score_all_pass(self, sample_indicators):
        """Test that all indicators passing gives perfect score."""
        result = evaluate_gate(sample_indicators, "LONG")
        
        assert result["score"] == 12
        assert result["total"] == 12
        assert len(result["reasons"]) == 0
    
    def test_failed_indicator_reduces_score(self):
        """Test that failed indicators reduce score."""
        indicators = {
            "ok": True,
            "ema50": 98.0,
            "ema200": 99.0,  # EMA50 < EMA200, fails trend for LONG
            "atr_pct": 0.005,
            "rsi": 55.0,
            "adx": 20.0,
            "bbw": 0.02,
            "rel_vol": 1.5,
            "vwap_dist": 0.002,
            "ema50_slope": 0.001,
            "price_rel_ema50": 2.0,
            "price_rel_ema200": 5.0,
            "_series": {
                "ema50": [95.0, 96.0, 97.0, 98.0],
                "ema200": [94.0, 94.5, 95.0, 99.0],
            },
        }
        
        result = evaluate_gate(indicators, "LONG")
        
        assert result["score"] < 12
        assert "weak_trend" in result["reasons"]
    
    def test_low_atr_fails(self):
        """Test that low ATR fails."""
        indicators = {
            "ok": True,
            "ema50": 98.0,
            "ema200": 95.0,
            "atr_pct": 0.001,  # Below threshold
            "rsi": 55.0,
            "adx": 20.0,
            "bbw": 0.02,
            "rel_vol": 1.5,
            "vwap_dist": 0.002,
            "ema50_slope": 0.001,
            "price_rel_ema50": 2.0,
            "price_rel_ema200": 5.0,
            "_series": {
                "ema50": [95.0, 96.0, 97.0, 98.0],
                "ema200": [94.0, 94.5, 95.0, 95.0],
            },
        }
        
        result = evaluate_gate(indicators, "LONG")
        
        assert "low_atr" in result["reasons"]
    
    def test_rsi_fail_for_long(self):
        """Test that low RSI fails for LONG."""
        indicators = {
            "ok": True,
            "ema50": 98.0,
            "ema200": 95.0,
            "atr_pct": 0.005,
            "rsi": 45.0,  # Below threshold for LONG
            "adx": 20.0,
            "bbw": 0.02,
            "rel_vol": 1.5,
            "vwap_dist": 0.002,
            "ema50_slope": 0.001,
            "price_rel_ema50": 2.0,
            "price_rel_ema200": 5.0,
            "_series": {
                "ema50": [95.0, 96.0, 97.0, 98.0],
                "ema200": [94.0, 94.5, 95.0, 95.0],
            },
        }
        
        result = evaluate_gate(indicators, "LONG")
        
        assert "rsi_fail" in result["reasons"]
    
    def test_rsi_fail_for_short(self):
        """Test that high RSI fails for SHORT."""
        indicators = {
            "ok": True,
            "ema50": 95.0,
            "ema200": 98.0,  # EMA50 < EMA200 for SHORT trend
            "atr_pct": 0.005,
            "rsi": 55.0,  # Above threshold for SHORT
            "adx": 20.0,
            "bbw": 0.02,
            "rel_vol": 1.5,
            "vwap_dist": 0.002,
            "ema50_slope": -0.001,
            "price_rel_ema50": -2.0,
            "price_rel_ema200": -5.0,
            "_series": {
                "ema50": [98.0, 97.0, 96.0, 95.0],
                "ema200": [99.0, 98.5, 98.0, 98.0],
            },
        }
        
        result = evaluate_gate(indicators, "SHORT")
        
        assert "rsi_fail" in result["reasons"]
    
    def test_failed_indicator_dict(self):
        """Test that failed indicator dict returns zero score."""
        indicators = {"ok": False, "reason": "no_data"}
        
        result = evaluate_gate(indicators, "LONG")
        
        assert result["score"] == 0
        assert result["total"] == 12
        assert "ind_failed" in result["reasons"] or "no_data" in result["reasons"]
    
    def test_custom_config_overrides(self, sample_indicators):
        """Test that custom config overrides defaults."""
        # Lower ATR threshold
        cfg = {"ATR_MIN": 0.001}
        
        indicators = {
            **sample_indicators,
            "atr_pct": 0.0015,  # Would fail with default 0.004, passes with 0.001
        }
        
        result = evaluate_gate(indicators, "LONG", cfg)
        
        # Should pass ATR check with custom config
        assert "low_atr" not in result["reasons"]
    
    def test_short_direction_evaluation(self, sample_indicators):
        """Test gate evaluation for SHORT direction."""
        # Reverse indicators for SHORT
        indicators = {
            "ok": True,
            "ema50": 95.0,
            "ema200": 98.0,
            "atr_pct": 0.005,
            "rsi": 45.0,  # Good for SHORT
            "adx": 20.0,
            "bbw": 0.02,
            "rel_vol": 1.5,
            "vwap_dist": 0.002,
            "ema50_slope": -0.001,
            "price_rel_ema50": -2.0,
            "price_rel_ema200": -5.0,
            "_series": {
                "ema50": [98.0, 97.0, 96.0, 95.0],
                "ema200": [99.0, 98.5, 98.0, 98.0],
            },
        }
        
        result = evaluate_gate(indicators, "SHORT")
        
        assert result["score"] >= 8  # Should pass minimum threshold
        assert result["total"] == 12
