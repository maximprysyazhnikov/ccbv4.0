# tests/test_panel_ui.py
"""
Automated UI/UX test for panel buttons.
Tests that all buttons generate correct callback_data and that apply_panel_action works.
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram_bot.panel import (
    panel_keyboard, apply_panel_action,
    TF_OPTIONS, AP_RR_OPTIONS, LOCALE_OPTIONS,
    SCALP_SL_OPTIONS, SCALP_TP_OPTIONS, SLIPPAGE_OPTIONS
)
from utils.user_settings import get_user_settings, set_user_settings, ensure_user_row

TEST_USER_ID = 999999999


class TestPanelKeyboard:
    """Test that panel keyboard generates correct buttons."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test user."""
        ensure_user_row(TEST_USER_ID)
        # Reset to defaults
        set_user_settings(
            TEST_USER_ID,
            scalping_mode=0,
            scalping_sl_pct=0.3,
            scalping_tp_pct=0.9,
            slippage_pct=0.05,
            autopost=0,
            timeframe="15m",
            autopost_tf="15m",
            autopost_rr=1.5,
            locale="uk",
            daily_tracker=0,
            winrate_tracker=0,
            monitored_symbols=""
        )
        yield
    
    def test_keyboard_generation(self):
        """Test that keyboard generates without errors."""
        kb = panel_keyboard(TEST_USER_ID)
        assert kb is not None
        assert kb.inline_keyboard is not None
        assert len(kb.inline_keyboard) > 0
    
    def test_all_buttons_have_callback_data(self):
        """Test that all buttons have valid callback_data."""
        kb = panel_keyboard(TEST_USER_ID)
        for row in kb.inline_keyboard:
            for btn in row:
                # Either callback_data or url should exist
                assert btn.callback_data is not None or btn.url is not None
                if btn.callback_data:
                    assert len(btn.callback_data) < 64, f"callback_data too long: {btn.callback_data}"
                    assert (
                        btn.callback_data.startswith("panel:")
                        or btn.callback_data.startswith("orders:")
                        or btn.callback_data.startswith("metals_kpi:")
                    )
    
    def test_scalping_buttons_exist(self):
        """Test that scalping buttons exist."""
        kb = panel_keyboard(TEST_USER_ID)
        all_callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        
        # Check scalping toggle
        assert any("toggle_scalping" in cb for cb in all_callbacks), "Scalping toggle missing"
        
        # Check SL options
        for sl in SCALP_SL_OPTIONS:
            assert any(f"set_scalp_sl:{sl}" in cb for cb in all_callbacks), f"SL {sl} missing"
        
        # Check TP options
        for tp in SCALP_TP_OPTIONS:
            assert any(f"set_scalp_tp:{tp}" in cb for cb in all_callbacks), f"TP {tp} missing"
        
        # Check slippage options
        for slip in SLIPPAGE_OPTIONS:
            assert any(f"set_slippage:{slip}" in cb for cb in all_callbacks), f"Slippage {slip} missing"
    
    def test_symbols_button_exists(self):
        """Test that symbols button exists."""
        kb = panel_keyboard(TEST_USER_ID)
        all_callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert any("edit_symbols" in cb for cb in all_callbacks), "Symbols button missing"
    
    def test_timeframe_buttons_exist(self):
        """Test that timeframe buttons exist."""
        kb = panel_keyboard(TEST_USER_ID)
        all_callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        
        for tf in TF_OPTIONS:
            assert any(f"set_tf:{tf}" in cb for cb in all_callbacks), f"TF {tf} missing"
            assert any(f"set_ap_tf:{tf}" in cb for cb in all_callbacks), f"AP TF {tf} missing"


class TestApplyPanelAction:
    """Test that apply_panel_action correctly updates settings."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test user."""
        ensure_user_row(TEST_USER_ID)
        set_user_settings(
            TEST_USER_ID,
            scalping_mode=0,
            scalping_sl_pct=0.3,
            scalping_tp_pct=0.9,
            slippage_pct=0.05,
            autopost=0,
            timeframe="15m",
            autopost_tf="15m",
            autopost_rr=1.5,
        )
        yield
    
    def test_toggle_autopost(self):
        """Test autopost toggle."""
        apply_panel_action(TEST_USER_ID, "toggle_autopost", "1")
        us = get_user_settings(TEST_USER_ID)
        assert us.get("autopost") == 1
        
        apply_panel_action(TEST_USER_ID, "toggle_autopost", "0")
        us = get_user_settings(TEST_USER_ID)
        assert us.get("autopost") == 0
    
    def test_toggle_scalping(self):
        """Test scalping toggle."""
        apply_panel_action(TEST_USER_ID, "toggle_scalping", "1")
        us = get_user_settings(TEST_USER_ID)
        assert us.get("scalping_mode") == 1
        
        apply_panel_action(TEST_USER_ID, "toggle_scalping", "0")
        us = get_user_settings(TEST_USER_ID)
        assert us.get("scalping_mode") == 0
    
    def test_set_scalp_sl(self):
        """Test scalping SL % change."""
        for sl in SCALP_SL_OPTIONS:
            apply_panel_action(TEST_USER_ID, "set_scalp_sl", str(sl))
            us = get_user_settings(TEST_USER_ID)
            assert abs(us.get("scalping_sl_pct") - sl) < 0.001, f"Expected SL {sl}, got {us.get('scalping_sl_pct')}"
    
    def test_set_scalp_tp(self):
        """Test scalping TP % change."""
        for tp in SCALP_TP_OPTIONS:
            apply_panel_action(TEST_USER_ID, "set_scalp_tp", str(tp))
            us = get_user_settings(TEST_USER_ID)
            assert abs(us.get("scalping_tp_pct") - tp) < 0.001, f"Expected TP {tp}, got {us.get('scalping_tp_pct')}"
    
    def test_set_slippage(self):
        """Test slippage % change."""
        for slip in SLIPPAGE_OPTIONS:
            apply_panel_action(TEST_USER_ID, "set_slippage", str(slip))
            us = get_user_settings(TEST_USER_ID)
            assert abs(us.get("slippage_pct") - slip) < 0.001, f"Expected Slip {slip}, got {us.get('slippage_pct')}"
    
    def test_set_timeframe(self):
        """Test timeframe change."""
        for tf in TF_OPTIONS:
            apply_panel_action(TEST_USER_ID, "set_tf", tf)
            us = get_user_settings(TEST_USER_ID)
            assert us.get("timeframe") == tf
    
    def test_set_autopost_tf(self):
        """Test autopost timeframe change."""
        for tf in TF_OPTIONS:
            apply_panel_action(TEST_USER_ID, "set_ap_tf", tf)
            us = get_user_settings(TEST_USER_ID)
            assert us.get("autopost_tf") == tf
    
    def test_set_autopost_rr(self):
        """Test autopost RR change."""
        for rr in AP_RR_OPTIONS:
            apply_panel_action(TEST_USER_ID, "set_ap_rr", str(rr))
            us = get_user_settings(TEST_USER_ID)
            assert abs(us.get("autopost_rr") - rr) < 0.001
    
    def test_set_locale(self):
        """Test locale change."""
        for loc in LOCALE_OPTIONS:
            apply_panel_action(TEST_USER_ID, "set_locale", loc)
            us = get_user_settings(TEST_USER_ID)
            assert us.get("locale") == loc
    
    def test_toggle_daily(self):
        """Test daily tracker toggle."""
        apply_panel_action(TEST_USER_ID, "toggle_daily", "1")
        us = get_user_settings(TEST_USER_ID)
        assert us.get("daily_tracker") == 1
    
    def test_toggle_winrate(self):
        """Test winrate tracker toggle."""
        apply_panel_action(TEST_USER_ID, "toggle_winrate", "1")
        us = get_user_settings(TEST_USER_ID)
        assert us.get("winrate_tracker") == 1


class TestKeyboardStateSync:
    """Test that keyboard reflects actual state after changes."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        ensure_user_row(TEST_USER_ID)
        set_user_settings(
            TEST_USER_ID,
            scalping_mode=0,
            scalping_sl_pct=0.3,
            scalping_tp_pct=0.9,
            slippage_pct=0.05,
        )
        yield
    
    def test_scalping_toggle_reflects_state(self):
        """Test that scalping button shows correct state."""
        # Initially OFF
        kb = panel_keyboard(TEST_USER_ID)
        scalp_btn = None
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data and "toggle_scalping" in btn.callback_data:
                    scalp_btn = btn
                    break
        
        assert scalp_btn is not None
        assert "OFF" in scalp_btn.text or "❌" in scalp_btn.text
        
        # Turn ON
        apply_panel_action(TEST_USER_ID, "toggle_scalping", "1")
        kb = panel_keyboard(TEST_USER_ID)
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data and "toggle_scalping" in btn.callback_data:
                    scalp_btn = btn
                    break
        
        assert "ON" in scalp_btn.text or "✅" in scalp_btn.text
    
    def test_sl_button_shows_checkmark(self):
        """Test that selected SL shows checkmark."""
        apply_panel_action(TEST_USER_ID, "set_scalp_sl", "0.5")
        kb = panel_keyboard(TEST_USER_ID)
        
        sl_btns = []
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data and "set_scalp_sl" in btn.callback_data:
                    sl_btns.append(btn)
        
        # Find the 0.5 button - should have checkmark
        for btn in sl_btns:
            if "0.5" in btn.callback_data:
                assert "✅" in btn.text, f"Expected checkmark for SL 0.5, got: {btn.text}"
            else:
                assert "✅" not in btn.text, f"Unexpected checkmark: {btn.text}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
