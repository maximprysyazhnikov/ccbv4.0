#!/usr/bin/env python3
"""
PHASE 3: GATE LOGIC VALIDATION
Тестування та валідація gate logic критеріїв
"""
import json
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import logging

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GateLogicValidator:
    """Валідатор gate logic критеріїв"""

    def __init__(self, db_path: str = "storage/bot.db"):
        self.db_path = db_path
        self.results = {}

    def connect_db(self) -> sqlite3.Connection:
        """Підключення до бази даних"""
        return sqlite3.connect(self.db_path)

    def get_trades_data(self) -> pd.DataFrame:
        """Отримання даних про трейди з БД"""
        conn = self.connect_db()
        try:
            query = """
            SELECT
                id, symbol, direction, entry, sl, tp, pnl, rr_realized,
                opened_at, closed_at, status, gate_score, gate_total, gate_pct
            FROM trades
            WHERE status = 'CLOSED'
            ORDER BY opened_at DESC
            """
            df = pd.read_sql_query(query, conn)
            logger.info(f"Завантажено {len(df)} закритих трейдів")
            return df
        finally:
            conn.close()

    def analyze_gate_criteria(self) -> Dict[str, Any]:
        """Аналіз поточних gate logic критеріїв на основі збережених даних"""
        logger.info("🔍 Аналіз gate logic критеріїв на основі історичних даних...")

        # Отримання даних про трейди з gate scores
        trades_df = self.get_trades_data()
        if trades_df.empty:
            return {"error": "Немає даних про трейди"}

        # Фільтруємо трейди з gate scores
        trades_with_gate = trades_df.dropna(subset=['gate_score', 'gate_total'])
        
        if trades_with_gate.empty:
            return {"error": "Немає трейдів з gate scores"}

        logger.info(f"Знайдено {len(trades_with_gate)} трейдів з gate scores")

        # Аналіз gate scores vs P&L
        analysis_results = []

        for _, trade in trades_with_gate.iterrows():
            analysis_results.append({
                'symbol': trade['symbol'],
                'direction': trade['direction'],
                'pnl': trade.get('pnl', 0),
                'rr_realized': trade.get('rr_realized'),
                'gate_score': trade.get('gate_score', 0),
                'gate_total': trade.get('gate_total', 12),
                'gate_pct': trade.get('gate_pct', 0),
                'gate_passed': trade.get('gate_score', 0) >= 8  # 70% threshold
            })

        # Статистика
        if analysis_results:
            df_results = pd.DataFrame(analysis_results)

            # Розрахунок метрик
            total_trades = len(df_results)
            passed_gates = df_results['gate_passed'].sum()
            pass_rate = passed_gates / total_trades if total_trades > 0 else 0

            # Кореляція gate score з P&L
            correlation = df_results['gate_score'].corr(df_results['pnl']) if len(df_results) > 1 else 0
            
            # Аналіз по символам
            symbol_analysis = df_results.groupby('symbol').agg({
                'gate_score': 'mean',
                'pnl': 'mean',
                'gate_passed': 'mean'
            }).round(3)

            analysis = {
                'total_analyzed': total_trades,
                'gate_pass_rate': pass_rate,
                'correlation_gate_pnl': correlation,
                'symbol_analysis': symbol_analysis.to_dict(),
                'recommendations': self._generate_recommendations_from_data(df_results)
            }
        else:
            analysis = {"error": "Не вдалося проаналізувати дані"}

        self.results['gate_analysis'] = analysis
        return analysis

    def _analyze_criteria_effectiveness(self, df_results: pd.DataFrame) -> Dict[str, Any]:
        """Аналіз ефективності окремих критеріїв (якщо будуть дані)"""
        return {"note": "Аналіз критеріїв потребує детальніших даних про індикатори"}

    def _generate_recommendations_from_data(self, df_results: pd.DataFrame) -> List[str]:
        """Генерація рекомендацій на основі аналізу даних"""
        recommendations = []

        # Аналіз кореляції
        correlation = df_results['gate_score'].corr(df_results['pnl']) if len(df_results) > 1 else 0
        if correlation < 0.1:
            recommendations.append("Низька кореляція gate score з P&L - критерії можуть бути неефективними")
        elif correlation > 0.5:
            recommendations.append("Висока кореляція gate score з P&L - критерії працюють добре")

        # Аналіз pass rate
        pass_rate = df_results['gate_passed'].mean()
        if pass_rate < 0.3:
            recommendations.append("Занадто низький gate pass rate - критерії надто строрі")
        elif pass_rate > 0.8:
            recommendations.append("Занадто високий gate pass rate - критерії надто м'які")

        # Аналіз прибутковості по gate scores
        high_gate_trades = df_results[df_results['gate_score'] >= 8]
        low_gate_trades = df_results[df_results['gate_score'] < 8]
        
        if not high_gate_trades.empty and not low_gate_trades.empty:
            high_gate_pnl = high_gate_trades['pnl'].mean()
            low_gate_pnl = low_gate_trades['pnl'].mean()
            
            if high_gate_pnl > low_gate_pnl:
                recommendations.append("Трейди з високим gate score більш прибуткові - критерії ефективні")
            else:
                recommendations.append("Трейди з низьким gate score більш прибуткові - переглянути критерії")

        # Рекомендації щодо покращення
        recommendations.append("Додати L/S Ratio як 13-й критерій gate logic")
        recommendations.append("Провести backtesting різних gate threshold значень")
        recommendations.append("Оптимізувати ваги окремих критеріїв")

        return recommendations

    def create_backtesting_framework(self) -> Dict[str, Any]:
        """Створення фреймворку для backtesting gate logic"""
        logger.info("🔧 Створення backtesting framework...")

        # Шаблон для backtesting
        backtest_template = {
            'description': 'Gate Logic Backtesting Framework',
            'parameters': {
                'symbols': ['BTCUSDT', 'ETHUSDT', 'BNBUSDT'],
                'timeframes': ['5m', '15m', '1h'],
                'date_range': {
                    'start': (datetime.now() - timedelta(days=30)).isoformat(),
                    'end': datetime.now().isoformat()
                },
                'gate_thresholds': [0.4, 0.5, 0.6, 0.7, 0.8],
                'criteria_weights': {
                    'trend': 1.0,
                    'volatility': 1.0,
                    'momentum': 1.0,
                    'volume': 0.8,
                    'support_resistance': 0.9
                }
            },
            'metrics': [
                'win_rate',
                'profit_factor',
                'sharpe_ratio',
                'max_drawdown',
                'total_return',
                'avg_trade_duration'
            ],
            'output_format': {
                'summary_report': 'backtest_summary.json',
                'detailed_results': 'backtest_detailed.csv',
                'charts': 'backtest_charts.png'
            }
        }

        # Збереження шаблону
        template_path = Path('backtest_template.json')
        with open(template_path, 'w', encoding='utf-8') as f:
            json.dump(backtest_template, f, indent=2, ensure_ascii=False)

        logger.info(f"Шаблон backtesting збережено в {template_path}")

        return backtest_template

    def run_validation(self) -> Dict[str, Any]:
        """Запуск повної валідації gate logic"""
        logger.info("🚀 ЗАПУСК ФАЗИ 3: GATE LOGIC ВАЛІДАЦІЯ")

        results = {
            'timestamp': datetime.now().isoformat(),
            'phase': 'gate_logic_validation',
            'status': 'running'
        }

        try:
            # 1. Аналіз поточних критеріїв
            gate_analysis = self.analyze_gate_criteria()
            results['gate_analysis'] = gate_analysis

            # 2. Створення backtesting framework
            backtest_framework = self.create_backtesting_framework()
            results['backtest_framework'] = backtest_framework

            # 3. Генерація звіту
            report = self.generate_report(results)
            results['report'] = report

            results['status'] = 'completed'
            logger.info("✅ Фаза 3 завершена успішно")

        except Exception as e:
            logger.error(f"❌ Помилка валідації: {e}")
            results['status'] = 'failed'
            results['error'] = str(e)

        # Збереження результатів
        self.save_results(results)

        return results

    def generate_report(self, results: Dict[str, Any]) -> str:
        """Генерація текстового звіту"""
        report = []
        report.append("# 📊 PHASE 3: GATE LOGIC VALIDATION REPORT")
        report.append(f"**Timestamp:** {results['timestamp']}")
        report.append("")

        if 'gate_analysis' in results and 'error' not in results['gate_analysis']:
            analysis = results['gate_analysis']
            report.append("## 🔍 GATE LOGIC ANALYSIS")
            report.append(f"- **Analyzed trades:** {analysis.get('total_analyzed', 0)}")
            report.append(f"- **Gate pass rate:** {analysis.get('gate_pass_rate', 0):.1%}")
            report.append(f"- **Correlation gate-P&L:** {analysis.get('correlation_gate_pnl', 0):.3f}")
            report.append("")

            if 'reason_failures' in analysis:
                report.append("### ❌ TOP FAILURE REASONS")
                for reason, count in list(analysis['reason_failures'].items())[:5]:
                    report.append(f"- **{reason}:** {count} times")
                report.append("")

            if 'recommendations' in analysis:
                report.append("### 💡 RECOMMENDATIONS")
                for rec in analysis['recommendations']:
                    report.append(f"- {rec}")
                report.append("")

        if 'backtest_framework' in results:
            report.append("## 🔧 BACKTESTING FRAMEWORK")
            report.append("✅ Created backtesting template with parameters:")
            framework = results['backtest_framework']
            report.append(f"- Symbols: {', '.join(framework['parameters']['symbols'])}")
            report.append(f"- Thresholds: {framework['parameters']['gate_thresholds']}")
            report.append(f"- Metrics: {', '.join(framework['metrics'])}")
            report.append("")

        report.append("## 🎯 NEXT STEPS")
        report.append("1. **Implement L/S Ratio** as 13th gate criterion")
        report.append("2. **Run backtesting** with historical data")
        report.append("3. **Optimize gate thresholds** based on results")
        report.append("4. **A/B test** different criteria combinations")

        return "\n".join(report)

    def save_results(self, results: Dict[str, Any]) -> None:
        """Збереження результатів в файл"""
        output_file = Path('phase3_gate_validation.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Результати збережено в {output_file}")

        # Також зберегти текстовий звіт
        report_file = Path('phase3_gate_report.md')
        if 'report' in results:
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(results['report'])

            logger.info(f"Звіт збережено в {report_file}")


def main():
    """Основна функція"""
    validator = GateLogicValidator()
    results = validator.run_validation()

    # Вивід основних результатів
    print("\n" + "="*60)
    print("PHASE 3: GATE LOGIC VALIDATION RESULTS")
    print("="*60)

    if results['status'] == 'completed':
        analysis = results.get('gate_analysis', {})
        if 'error' not in analysis:
            print(f"✅ Analyzed {analysis.get('total_analyzed', 0)} trades")
            print(f"📊 Gate pass rate: {analysis.get('gate_pass_rate', 0):.1%}")
            print(f"📈 Correlation gate-P&L: {analysis.get('correlation_gate_pnl', 0):.3f}")
        else:
            print(f"❌ Analysis error: {analysis.get('error')}")
    else:
        print(f"❌ Validation failed: {results.get('error', 'Unknown error')}")

    print("\n📄 Detailed results saved to phase3_gate_validation.json")
    print("📄 Report saved to phase3_gate_report.md")


if __name__ == "__main__":
    main()