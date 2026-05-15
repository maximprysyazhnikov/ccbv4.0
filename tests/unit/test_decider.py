"""Tests for signal decider/parser."""
import pytest
from gpt_decider.decider import decide_from_markdown


class TestDecider:
    """Test markdown decision parsing."""
    
    def test_valid_long_signal(self):
        """Test parsing valid LONG signal."""
        md = """
        Direction: LONG
        Entry: 100.0
        SL: 95.0
        TP: 115.0
        RR: 3.0
        Confidence: 80%
        """
        
        result = decide_from_markdown(md, rr_threshold=1.5, conf_threshold=75)
        
        assert result["ok"] is True
        assert result["direction"] == "LONG"
        assert result["entry"] == 100.0
        assert result["stop"] == 95.0
        assert result["tp"] == 115.0
        assert result["rr"] == 3.0
        assert result["confidence_pct"] == 80.0
    
    def test_valid_short_signal(self):
        """Test parsing valid SHORT signal."""
        md = """
        Direction: SHORT
        Entry: 100.0
        SL: 105.0
        TP: 85.0
        RR: 3.0
        Confidence: 85%
        """
        
        result = decide_from_markdown(md, rr_threshold=1.5, conf_threshold=75)
        
        assert result["ok"] is True
        assert result["direction"] == "SHORT"
        assert result["entry"] == 100.0
        assert result["stop"] == 105.0
        assert result["tp"] == 85.0
    
    def test_low_rr_fails(self):
        """Test that low RR fails threshold."""
        md = """
        Direction: LONG
        Entry: 100.0
        SL: 95.0
        TP: 102.0
        RR: 0.4
        Confidence: 80%
        """
        
        result = decide_from_markdown(md, rr_threshold=1.5, conf_threshold=75)
        
        assert result["ok"] is False
        assert "RR<1.5" in result["reason"]
    
    def test_low_confidence_fails(self):
        """Test that low confidence fails threshold."""
        md = """
        Direction: LONG
        Entry: 100.0
        SL: 95.0
        TP: 115.0
        RR: 3.0
        Confidence: 50%
        """
        
        result = decide_from_markdown(md, rr_threshold=1.5, conf_threshold=75)
        
        assert result["ok"] is False
        assert "Conf<75%" in result["reason"]
    
    def test_missing_rr_fails(self):
        """Test that missing RR fails."""
        md = """
        Direction: LONG
        Entry: 100.0
        SL: 95.0
        TP: 115.0
        Confidence: 80%
        """
        
        result = decide_from_markdown(md, rr_threshold=1.5, conf_threshold=75)
        
        assert result["ok"] is False
        assert "RR не знайдено" in result["reason"] or "RR" in result["reason"]
    
    def test_no_trade_direction_fails(self):
        """Test that NO_TRADE direction fails."""
        md = """
        Direction: NO_TRADE
        Entry: 100.0
        SL: 95.0
        TP: 115.0
        RR: 3.0
        Confidence: 80%
        """
        
        result = decide_from_markdown(md, rr_threshold=1.5, conf_threshold=75)
        
        assert result["ok"] is False
        assert "NO_TRADE" in result["reason"] or "direction" in result["reason"]
    
    def test_confidence_decimal_format(self):
        """Test confidence in decimal format (0.75)."""
        md = """
        Direction: LONG
        Entry: 100.0
        SL: 95.0
        TP: 115.0
        RR: 3.0
        Confidence: 0.80
        """
        
        result = decide_from_markdown(md, rr_threshold=1.5, conf_threshold=75)
        
        # Should convert 0.80 to 80%
        assert result["confidence_pct"] == 80.0
    
    def test_case_insensitive_parsing(self):
        """Test that parsing is case-insensitive."""
        md = """
        direction: long
        entry: 100.0
        stop-loss: 95.0
        take-profit: 115.0
        rr: 3.0
        confidence: 80%
        """
        
        result = decide_from_markdown(md, rr_threshold=1.5, conf_threshold=75)
        
        assert result["ok"] is True
        assert result["direction"] == "LONG"
        assert result["entry"] == 100.0
        assert result["stop"] == 95.0
        assert result["tp"] == 115.0
    
    def test_empty_markdown(self):
        """Test empty markdown returns failure."""
        result = decide_from_markdown("", rr_threshold=1.5, conf_threshold=75)
        
        assert result["ok"] is False
        assert result["direction"] is None or result["direction"] == "NO_TRADE"
    
    def test_partial_fields(self):
        """Test parsing with some missing fields."""
        md = """
        Direction: LONG
        Entry: 100.0
        RR: 3.0
        """
        
        result = decide_from_markdown(md, rr_threshold=1.5, conf_threshold=75)
        
        assert result["direction"] == "LONG"
        assert result["entry"] == 100.0
        assert result["rr"] == 3.0
        # Missing fields should be None
        assert result["stop"] is None or result["tp"] is None
