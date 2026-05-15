from __future__ import annotations
import os
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass

log = logging.getLogger(__name__)

@dataclass
class CircuitBreakerState:
    """Circuit breaker state for risk management"""
    is_active: bool = False
    reason: str = ""
    activated_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None

@dataclass
class RiskLimits:
    """Risk management limits and thresholds"""
    max_daily_drawdown_r: float = 0.05  # 5% in R
    max_weekly_drawdown_r: float = 0.10  # 10% in R
    max_consecutive_losses: int = 4
    min_win_rate: float = 0.35  # 35%
    max_correlation_threshold: float = 0.8  # 80%
    circuit_breaker_cooldown_hours: int = 24

class RiskManager:
    """Enhanced risk management system with circuit breakers"""

    def __init__(self, db_path: str = "storage/bot.db"):
        self.db_path = db_path
        self.circuit_breaker = CircuitBreakerState()
        self.limits = RiskLimits()
        self._load_limits()

    def _load_limits(self):
        """Load risk limits from settings"""
        try:
            from utils.settings import get_setting_float, get_setting_int
            self.limits.max_daily_drawdown_r = get_setting_float("max_daily_drawdown_r", 0.05)
            self.limits.max_weekly_drawdown_r = get_setting_float("max_weekly_drawdown_r", 0.10)
            self.limits.max_consecutive_losses = get_setting_int("max_consecutive_losses", 4)
            self.limits.min_win_rate = get_setting_float("min_win_rate", 0.35)
            self.limits.max_correlation_threshold = get_setting_float("max_correlation_threshold", 0.8)
            self.limits.circuit_breaker_cooldown_hours = get_setting_int("circuit_breaker_cooldown_hours", 24)
        except Exception as e:
            log.warning(f"Failed to load risk limits from settings: {e}")

    def check_circuit_breaker(self) -> CircuitBreakerState:
        """Check if circuit breaker should be activated"""
        if self.circuit_breaker.is_active:
            # Check if cooldown period has expired
            if self.circuit_breaker.cooldown_until and datetime.now() >= self.circuit_breaker.cooldown_until:
                self.circuit_breaker.is_active = False
                self.circuit_breaker.reason = ""
                self.circuit_breaker.activated_at = None
                self.circuit_breaker.cooldown_until = None
                log.info("Circuit breaker cooldown expired, trading resumed")
            return self.circuit_breaker

        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()

                # Check consecutive losses
                consec_losses = self._get_consecutive_losses(cur)
                if consec_losses >= self.limits.max_consecutive_losses:
                    self._activate_circuit_breaker(f"Consecutive losses: {consec_losses} >= {self.limits.max_consecutive_losses}")
                    return self.circuit_breaker

                # Check daily drawdown
                daily_rr = self._get_daily_rr(cur)
                if daily_rr <= -abs(self.limits.max_daily_drawdown_r):
                    self._activate_circuit_breaker(f"Daily drawdown: {daily_rr:.2f}R <= -{self.limits.max_daily_drawdown_r:.2f}R")
                    return self.circuit_breaker

                # Check weekly drawdown
                weekly_rr = self._get_weekly_rr(cur)
                if weekly_rr <= -abs(self.limits.max_weekly_drawdown_r):
                    self._activate_circuit_breaker(f"Weekly drawdown: {weekly_rr:.2f}R <= -{self.limits.max_weekly_drawdown_r:.2f}R")
                    return self.circuit_breaker

                # Check win rate
                win_rate = self._get_recent_win_rate(cur, window=20)
                if win_rate < self.limits.min_win_rate:
                    self._activate_circuit_breaker(f"Win rate: {win_rate:.1%} < {self.limits.min_win_rate:.1%}")
                    return self.circuit_breaker

        except Exception as e:
            log.error(f"Error checking circuit breaker: {e}")

        return self.circuit_breaker

    def _activate_circuit_breaker(self, reason: str):
        """Activate circuit breaker with cooldown"""
        self.circuit_breaker.is_active = True
        self.circuit_breaker.reason = reason
        self.circuit_breaker.activated_at = datetime.now()
        self.circuit_breaker.cooldown_until = datetime.now() + timedelta(hours=self.limits.circuit_breaker_cooldown_hours)
        log.warning(f"Circuit breaker activated: {reason}")

    def _get_consecutive_losses(self, cur: sqlite3.Cursor) -> int:
        """Get current consecutive losses count"""
        rows = cur.execute("""
            SELECT pnl_usd
            FROM trades
            WHERE status = 'CLOSED'
            ORDER BY closed_at DESC
            LIMIT ?
        """, (self.limits.max_consecutive_losses + 5,)).fetchall()

        count = 0
        for row in rows:
            if row[0] and row[0] < 0:  # Loss
                count += 1
            else:  # Win or break
                break
        return count

    def _get_daily_rr(self, cur: sqlite3.Cursor) -> float:
        """Get today's R return"""
        today_start = int((datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).timestamp())
        today_end = today_start + 86400

        rows = cur.execute("""
            SELECT rr
            FROM trades
            WHERE status = 'CLOSED' AND closed_at >= ? AND closed_at < ?
        """, (today_start, today_end)).fetchall()

        return sum(row[0] or 0 for row in rows)

    def _get_weekly_rr(self, cur: sqlite3.Cursor) -> float:
        """Get this week's R return"""
        now = datetime.now()
        week_start = now - timedelta(days=now.weekday())  # Monday
        week_start_ts = int(week_start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        week_end_ts = week_start_ts + (7 * 86400)

        rows = cur.execute("""
            SELECT rr
            FROM trades
            WHERE status = 'CLOSED' AND closed_at >= ? AND closed_at < ?
        """, (week_start_ts, week_end_ts)).fetchall()

        return sum(row[0] or 0 for row in rows)

    def _get_recent_win_rate(self, cur: sqlite3.Cursor, window: int = 20) -> float:
        """Get win rate for last N trades"""
        rows = cur.execute("""
            SELECT pnl_usd
            FROM trades
            WHERE status = 'CLOSED'
            ORDER BY closed_at DESC
            LIMIT ?
        """, (window,)).fetchall()

        if not rows:
            return 0.0

        wins = sum(1 for row in rows if row[0] and row[0] > 0)
        return wins / len(rows)

    def calculate_position_size(self, balance: float, risk_pct: float, entry: float, sl: float) -> float:
        """Calculate position size using fixed fractional risk management"""
        return fixed_fraction(balance, risk_pct, entry, sl)

    def get_risk_metrics(self) -> Dict[str, Any]:
        """Get comprehensive risk metrics"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()

                return {
                    "circuit_breaker_active": self.circuit_breaker.is_active,
                    "circuit_breaker_reason": self.circuit_breaker.reason,
                    "consecutive_losses": self._get_consecutive_losses(cur),
                    "daily_rr": self._get_daily_rr(cur),
                    "weekly_rr": self._get_weekly_rr(cur),
                    "win_rate_20": self._get_recent_win_rate(cur, 20),
                    "win_rate_50": self._get_recent_win_rate(cur, 50),
                    "limits": {
                        "max_daily_drawdown_r": self.limits.max_daily_drawdown_r,
                        "max_weekly_drawdown_r": self.limits.max_weekly_drawdown_r,
                        "max_consecutive_losses": self.limits.max_consecutive_losses,
                        "min_win_rate": self.limits.min_win_rate,
                    }
                }
        except Exception as e:
            log.error(f"Error getting risk metrics: {e}")
            return {"error": str(e)}


def fixed_fraction(balance: float, risk_pct: float, entry: float, sl: float) -> float:
    """Calculate position size using fixed fractional risk management"""
    risk_amount = balance * (risk_pct / 100.0)
    stop_distance = abs(entry - sl)

    if stop_distance <= 0:
        return 0.0

    position_size = risk_amount / stop_distance
    return max(position_size, 0.0)
