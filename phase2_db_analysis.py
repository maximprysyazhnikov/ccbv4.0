#!/usr/bin/env python3
"""
PHASE 2: DATA ANALYSIS - Comprehensive Database Audit
CCBV3.8 Trading Bot Analysis
"""

import sqlite3
import os
import json
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import statistics

class DatabaseAnalyzer:
    def __init__(self, db_path="storage/bot.db"):
        self.db_path = db_path
        self.conn = None
        self.results = {}
        self.closed_statuses = ("CLOSED", "WIN", "LOSS", "TP", "SL")

    def _closed_status_sql(self) -> str:
        values = ",".join(f"'{status}'" for status in self.closed_statuses)
        return f"UPPER(COALESCE(status, '')) IN ({values})"

    @staticmethod
    def _normalize_ts_sql(column: str) -> str:
        return f"""
            CASE
                WHEN {column} IS NULL OR TRIM(CAST({column} AS TEXT)) = '' THEN NULL
                WHEN CAST({column} AS TEXT) GLOB '[0-9]*' THEN datetime(CAST({column} AS INTEGER), 'unixepoch')
                ELSE datetime({column})
            END
        """

    def connect(self):
        """Establish database connection"""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            print("✅ Database connection established")
            return True
        except Exception as e:
            print(f"❌ Database connection failed: {e}")
            return False

    def get_table_info(self):
        """Get comprehensive table information"""
        print("\n📊 ANALYZING DATABASE STRUCTURE...")

        tables = {}
        cursor = self.conn.cursor()

        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = [row[0] for row in cursor.fetchall()]

        for table_name in table_names:
            # Get column info
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()

            # Get row count
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = cursor.fetchone()[0]

            # Get table size estimate
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 1")
            sample = cursor.fetchone()
            if sample:
                col_count = len(sample)
            else:
                col_count = len(columns)

            tables[table_name] = {
                'columns': [{'name': col[1], 'type': col[2], 'nullable': not col[3], 'default': col[4]}
                           for col in columns],
                'row_count': row_count,
                'column_count': len(columns)
            }

        self.results['tables'] = tables
        print(f"✅ Analyzed {len(tables)} tables")
        return tables

    def analyze_trades_table(self):
        """Deep analysis of trades table - the core of the system"""
        print("\n📈 ANALYZING TRADES TABLE...")

        cursor = self.conn.cursor()
        trades_analysis = {}
        closed_status_sql = self._closed_status_sql()
        opened_at_expr = self._normalize_ts_sql("opened_at")

        # Basic counts
        cursor.execute("SELECT COUNT(*) FROM trades")
        total_trades = cursor.fetchone()[0]

        cursor.execute(f"SELECT COUNT(*) FROM trades WHERE {closed_status_sql}")
        closed_trades = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
        open_trades = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COALESCE(status, 'NULL') as status, COUNT(*) as count
            FROM trades
            GROUP BY COALESCE(status, 'NULL')
            ORDER BY count DESC
        """)
        status_breakdown = [{'status': row[0], 'count': row[1]} for row in cursor.fetchall()]

        trades_analysis['counts'] = {
            'total': total_trades,
            'closed': closed_trades,
            'open': open_trades
        }
        trades_analysis['status_breakdown'] = status_breakdown

        # Win/Loss analysis
        if closed_trades > 0:
            cursor.execute(f"""
                SELECT
                    COUNT(CASE WHEN pnl_usd > 0 THEN 1 END) as wins,
                    COUNT(CASE WHEN pnl_usd < 0 THEN 1 END) as losses,
                    COUNT(CASE WHEN pnl_usd = 0 THEN 1 END) as breakeven,
                    AVG(CASE WHEN pnl_usd > 0 THEN pnl_usd END) as avg_win,
                    AVG(CASE WHEN pnl_usd < 0 THEN pnl_usd END) as avg_loss,
                    SUM(pnl_usd) as total_pnl
                FROM trades WHERE {closed_status_sql}
            """)
            wl_row = cursor.fetchone()

            win_rate = (wl_row[0] / closed_trades) * 100 if closed_trades > 0 else 0
            profit_factor = abs(wl_row[3] / wl_row[4]) if wl_row[4] and wl_row[4] != 0 else 0

            trades_analysis['win_loss'] = {
                'wins': wl_row[0],
                'losses': wl_row[1],
                'breakeven': wl_row[2],
                'win_rate_pct': round(win_rate, 2),
                'avg_win': round(wl_row[3] or 0, 4),
                'avg_loss': round(wl_row[4] or 0, 4),
                'profit_factor': round(profit_factor, 2),
                'total_pnl': round(wl_row[5] or 0, 4)
            }

        # Symbol analysis
        cursor.execute(f"""
            SELECT symbol, COUNT(*) as count,
                   SUM(pnl_usd) as pnl,
                   AVG(pnl_usd) as avg_pnl
            FROM trades WHERE {closed_status_sql}
            GROUP BY symbol
            ORDER BY count DESC
        """)
        symbol_stats = cursor.fetchall()

        trades_analysis['symbols'] = [
            {
                'symbol': row[0],
                'count': row[1],
                'total_pnl': round(row[2] or 0, 4),
                'avg_pnl': round(row[3] or 0, 4)
            } for row in symbol_stats
        ]

        # Trade mode analysis
        cursor.execute(f"""
            SELECT trade_mode, COUNT(*) as count,
                   SUM(pnl_usd) as pnl,
                   AVG(pnl_usd) as avg_pnl
            FROM trades WHERE {closed_status_sql}
            GROUP BY trade_mode
        """)
        mode_stats = cursor.fetchall()

        trades_analysis['modes'] = [
            {
                'mode': row[0] or 'standard',
                'count': row[1],
                'total_pnl': round(row[2] or 0, 4),
                'avg_pnl': round(row[3] or 0, 4)
            } for row in mode_stats
        ]

        # RR analysis
        cursor.execute(f"""
            SELECT AVG(rr) as avg_rr, MIN(rr) as min_rr, MAX(rr) as max_rr
            FROM trades WHERE {closed_status_sql} AND rr IS NOT NULL
        """)
        rr_stats = cursor.fetchone()

        if rr_stats[0]:
            trades_analysis['rr_stats'] = {
                'avg_rr': round(rr_stats[0], 2),
                'min_rr': round(rr_stats[1], 2),
                'max_rr': round(rr_stats[2], 2)
            }

        # Time analysis
        cursor.execute(f"""
            SELECT
                strftime('%Y-%m-%d', {opened_at_expr}) as date,
                COUNT(*) as count,
                SUM(pnl_usd) as pnl
            FROM trades
            WHERE opened_at IS NOT NULL
            GROUP BY date
            ORDER BY date DESC
            LIMIT 30
        """)
        time_stats = cursor.fetchall()

        trades_analysis['daily_stats'] = [
            {
                'date': row[0],
                'count': row[1],
                'pnl': round(row[2] or 0, 4)
            } for row in time_stats
        ]

        self.results['trades_analysis'] = trades_analysis
        print(f"✅ Analyzed {total_trades} trades ({closed_trades} closed, {open_trades} open)")
        return trades_analysis

    def analyze_data_integrity(self):
        """Check data integrity and consistency"""
        print("\n🔍 ANALYZING DATA INTEGRITY...")

        cursor = self.conn.cursor()
        integrity_issues = []
        allowed_statuses = ",".join(f"'{status}'" for status in ("OPEN", *self.closed_statuses))

        # Check for NULL values in critical fields
        critical_fields = [
            ('trades', 'symbol'),
            ('trades', 'direction'),
            ('trades', 'entry'),
            ('trades', 'status')
        ]

        for table, field in critical_fields:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {field} IS NULL")
            null_count = cursor.fetchone()[0]
            if null_count > 0:
                integrity_issues.append({
                    'table': table,
                    'field': field,
                    'null_count': null_count,
                    'severity': 'HIGH' if field in ['symbol', 'status'] else 'MEDIUM'
                })

        # Check for invalid directions
        cursor.execute("SELECT COUNT(*) FROM trades WHERE direction NOT IN ('LONG', 'SHORT')")
        invalid_directions = cursor.fetchone()[0]
        if invalid_directions > 0:
            integrity_issues.append({
                'table': 'trades',
                'field': 'direction',
                'invalid_count': invalid_directions,
                'severity': 'HIGH'
            })

        # Check for invalid statuses
        cursor.execute(
            f"SELECT COUNT(*) FROM trades WHERE UPPER(COALESCE(status,'')) NOT IN ({allowed_statuses})"
        )
        invalid_statuses = cursor.fetchone()[0]
        if invalid_statuses > 0:
            integrity_issues.append({
                'table': 'trades',
                'field': 'status',
                'invalid_count': invalid_statuses,
                'severity': 'HIGH'
            })

        # Check for trades with entry but no exit
        cursor.execute("""
            SELECT COUNT(*) FROM trades
            WHERE entry IS NOT NULL
            AND (sl IS NULL OR tp IS NULL)
            AND status = 'CLOSED'
        """)
        missing_sl_tp = cursor.fetchone()[0]
        if missing_sl_tp > 0:
            integrity_issues.append({
                'table': 'trades',
                'issue': 'CLOSED trades missing SL/TP',
                'count': missing_sl_tp,
                'severity': 'MEDIUM'
            })

        self.results['integrity_issues'] = integrity_issues
        print(f"✅ Found {len(integrity_issues)} data integrity issues")
        return integrity_issues

    def calculate_key_metrics(self):
        """Calculate key trading performance metrics"""
        print("\n📊 CALCULATING KEY METRICS...")

        cursor = self.conn.cursor()
        metrics = {}
        closed_status_sql = self._closed_status_sql()
        closed_at_expr = self._normalize_ts_sql("closed_at")

        # Overall performance
        cursor.execute(f"""
            SELECT
                COUNT(*) as total_trades,
                COUNT(CASE WHEN pnl_usd > 0 THEN 1 END) as winning_trades,
                COUNT(CASE WHEN pnl_usd < 0 THEN 1 END) as losing_trades,
                SUM(pnl_usd) as total_pnl,
                AVG(pnl_usd) as avg_pnl,
                MAX(pnl_usd) as best_trade,
                MIN(pnl_usd) as worst_trade
            FROM trades WHERE {closed_status_sql}
        """)
        perf_row = cursor.fetchone()

        if perf_row[0] > 0:
            win_rate = (perf_row[1] / perf_row[0]) * 100
            metrics['overall_performance'] = {
                'total_trades': perf_row[0],
                'winning_trades': perf_row[1],
                'losing_trades': perf_row[2],
                'win_rate_pct': round(win_rate, 2),
                'total_pnl': round(perf_row[3] or 0, 4),
                'avg_pnl_per_trade': round(perf_row[4] or 0, 4),
                'best_trade': round(perf_row[5] or 0, 4),
                'worst_trade': round(perf_row[6] or 0, 4)
            }

        # Sharpe-like ratio (simplified)
        if perf_row[0] > 1:
            cursor.execute(f"SELECT pnl_usd FROM trades WHERE {closed_status_sql} AND pnl_usd IS NOT NULL")
            pnls = [row[0] for row in cursor.fetchall()]

            if len(pnls) > 1:
                avg_return = statistics.mean(pnls)
                std_dev = statistics.stdev(pnls) if len(pnls) > 1 else 0

                if std_dev > 0:
                    sharpe_ratio = avg_return / std_dev
                    metrics['risk_metrics'] = {
                        'avg_return': round(avg_return, 6),
                        'std_dev': round(std_dev, 6),
                        'sharpe_ratio': round(sharpe_ratio, 4)
                    }

        # Consecutive wins/losses
        cursor.execute(f"""
            SELECT pnl_usd > 0 as is_win
            FROM trades
            WHERE {closed_status_sql} AND pnl_usd IS NOT NULL
            ORDER BY {closed_at_expr}, id
        """)
        win_sequence = [row[0] for row in cursor.fetchall()]

        if win_sequence:
            current_streak = 0
            max_win_streak = 0
            max_loss_streak = 0
            temp_win = 0
            temp_loss = 0

            for is_win in win_sequence:
                if is_win:
                    temp_win += 1
                    temp_loss = 0
                    max_win_streak = max(max_win_streak, temp_win)
                    if temp_win > current_streak:
                        current_streak = temp_win
                else:
                    temp_loss += 1
                    temp_win = 0
                    max_loss_streak = max(max_loss_streak, temp_loss)

            metrics['streaks'] = {
                'current_streak': current_streak,
                'max_win_streak': max_win_streak,
                'max_loss_streak': max_loss_streak
            }

        self.results['key_metrics'] = metrics
        print("✅ Calculated key performance metrics")
        return metrics

    def generate_report(self):
        """Generate comprehensive analysis report"""
        print("\n📋 GENERATING ANALYSIS REPORT...")

        report = {
            'timestamp': datetime.now().isoformat(),
            'database_path': self.db_path,
            'summary': {},
            'recommendations': []
        }

        # Summary stats
        if 'tables' in self.results:
            report['summary']['tables_count'] = len(self.results['tables'])
            total_rows = sum(table['row_count'] for table in self.results['tables'].values())
            report['summary']['total_rows'] = total_rows

        if 'trades_analysis' in self.results:
            ta = self.results['trades_analysis']
            report['summary']['total_trades'] = ta['counts']['total']
            report['summary']['closed_trades'] = ta['counts']['closed']
            report['summary']['win_rate'] = ta.get('win_loss', {}).get('win_rate_pct', 0)

        # Generate recommendations
        if 'key_metrics' in self.results and 'overall_performance' in self.results['key_metrics']:
            perf = self.results['key_metrics']['overall_performance']
            win_rate = perf['win_rate_pct']

            if win_rate < 30:
                report['recommendations'].append({
                    'priority': 'HIGH',
                    'category': 'PERFORMANCE',
                    'issue': f'Low win rate: {win_rate}%',
                    'recommendation': 'Review gate logic and entry criteria'
                })

            if perf['total_pnl'] < 0:
                report['recommendations'].append({
                    'priority': 'HIGH',
                    'category': 'PROFITABILITY',
                    'issue': f'Negative total P&L: ${perf["total_pnl"]}',
                    'recommendation': 'Implement stricter risk management'
                })

        if 'integrity_issues' in self.results:
            high_issues = [i for i in self.results['integrity_issues'] if i.get('severity') == 'HIGH']
            if high_issues:
                report['recommendations'].append({
                    'priority': 'HIGH',
                    'category': 'DATA_INTEGRITY',
                    'issue': f'{len(high_issues)} critical data integrity issues',
                    'recommendation': 'Fix data validation and cleanup corrupted records'
                })

        # Save detailed results
        report.update(self.results)

        # Save to file
        with open('analysis_results.json', 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print("✅ Analysis report saved to analysis_results.json")
        return report

    def run_full_analysis(self):
        """Run complete database analysis"""
        print("🚀 STARTING COMPREHENSIVE DATABASE ANALYSIS")
        print("=" * 50)

        if not self.connect():
            return None

        try:
            self.get_table_info()
            self.analyze_trades_table()
            self.analyze_data_integrity()
            self.calculate_key_metrics()
            report = self.generate_report()

            print("\n" + "=" * 50)
            print("✅ DATABASE ANALYSIS COMPLETED")
            return report

        finally:
            if self.conn:
                self.conn.close()

def main():
    analyzer = DatabaseAnalyzer()
    results = analyzer.run_full_analysis()

    if results:
        print("\n📊 QUICK SUMMARY:")
        print(f"   Tables: {results.get('summary', {}).get('tables_count', 0)}")
        print(f"   Total Rows: {results.get('summary', {}).get('total_rows', 0)}")
        print(f"   Total Trades: {results.get('summary', {}).get('total_trades', 0)}")
        print(f"   Win Rate: {results.get('summary', {}).get('win_rate', 0):.2f}%")
if __name__ == "__main__":
    main()
