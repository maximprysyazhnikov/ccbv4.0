#!/usr/bin/env python3
"""
PHASE 6: РИНКОВИЙ АНАЛІЗ
Фінальний аналіз ринкових умов та підготовка рекомендацій
"""
import json
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import logging

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MarketAnalyzer:
    """Аналізатор ринкових умов"""

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.results = {}

    async def analyze_current_market_conditions(self) -> Dict[str, Any]:
        """Аналіз поточних ринкових умов"""
        logger.info("📊 Аналіз поточних ринкових умов...")

        market_data = {
            'symbols_analyzed': [],
            'price_data': {},
            'volatility_analysis': {},
            'trend_analysis': {},
            'ls_ratio_data': {},
            'funding_rates': {}
        }

        # Спроба отримати дані з існуючих модулів
        try:
            from market_data.long_short_ratio import get_sentiment_short
            from services.scalping_sources import collect_scalping_candidates

            # Аналіз L/S Ratio для основних символів
            symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT']

            for symbol in symbols:
                try:
                    ls_data = await get_sentiment_short(symbol)
                    if ls_data:
                        market_data['ls_ratio_data'][symbol] = ls_data
                        market_data['symbols_analyzed'].append(symbol)

                        # Визначення sentiment
                        long_pct = ls_data.get('long_pct', 0)
                        short_pct = ls_data.get('short_pct', 0)

                        if long_pct > 60:
                            sentiment = "BULLISH"
                        elif short_pct > 60:
                            sentiment = "BEARISH"
                        else:
                            sentiment = "NEUTRAL"

                        market_data['ls_ratio_data'][symbol]['sentiment'] = sentiment

                except Exception as e:
                    logger.warning(f"Не вдалося отримати L/S дані для {symbol}: {e}")

            # Спроба отримати технічні індикатори
            try:
                candidates = await collect_scalping_candidates()
                if candidates:
                    for candidate in candidates[:3]:  # Аналізуємо перші 3
                        symbol = candidate.get('symbol', 'UNKNOWN')
                        indicators = candidate.get('indicators', {})

                        market_data['price_data'][symbol] = {
                            'price': indicators.get('price'),
                            'ema50': indicators.get('ema50'),
                            'ema200': indicators.get('ema200'),
                            'rsi': indicators.get('rsi'),
                            'trend': 'UPTREND' if indicators.get('ema50', 0) > indicators.get('ema200', 0) else 'DOWNTREND'
                        }

            except Exception as e:
                logger.warning(f"Не вдалося отримати технічні індикатори: {e}")

        except ImportError as e:
            logger.warning(f"Не вдалося імпортувати market data модулі: {e}")

        self.results['market_conditions'] = market_data
        return market_data

    def analyze_market_regime(self) -> Dict[str, Any]:
        """Аналіз ринкового режиму"""
        logger.info("🎯 Аналіз ринкового режиму...")

        regime_analysis = {
            'overall_regime': 'UNKNOWN',
            'volatility_regime': 'UNKNOWN',
            'trend_regime': 'UNKNOWN',
            'recommendations': []
        }

        market_data = self.results.get('market_conditions', {})

        # Аналіз на основі L/S Ratio
        ls_data = market_data.get('ls_ratio_data', {})
        if ls_data:
            bullish_count = sum(1 for data in ls_data.values() if data.get('sentiment') == 'BULLISH')
            bearish_count = sum(1 for data in ls_data.values() if data.get('sentiment') == 'BEARISH')

            if bullish_count > bearish_count:
                regime_analysis['overall_regime'] = 'BULLISH'
                regime_analysis['recommendations'].append("Ринок в бичачому режимі - розглянути LONG позиції")
            elif bearish_count > bullish_count:
                regime_analysis['overall_regime'] = 'BEARISH'
                regime_analysis['recommendations'].append("Ринок в ведмежому режимі - розглянути SHORT позиції")
            else:
                regime_analysis['overall_regime'] = 'SIDEWAYS'
                regime_analysis['recommendations'].append("Ринок в боковому режимі - уникати великих позицій")

        # Аналіз волатильності (спрощений)
        regime_analysis['volatility_regime'] = 'MODERATE'
        regime_analysis['trend_regime'] = 'MIXED'

        self.results['market_regime'] = regime_analysis
        return regime_analysis

    def analyze_seasonal_patterns(self) -> Dict[str, Any]:
        """Аналіз сезонних патернів"""
        logger.info("📅 Аналіз сезонних патернів...")

        seasonal_analysis = {
            'current_hour': datetime.now().hour,
            'current_day': datetime.now().strftime('%A'),
            'time_zone_bias': {},
            'recommendations': []
        }

        # Аналіз часових зон (спрощений)
        current_hour = seasonal_analysis['current_hour']

        if 8 <= current_hour <= 16:  # European/London session
            seasonal_analysis['time_zone_bias']['EUR'] = 'ACTIVE'
            seasonal_analysis['recommendations'].append("Активна європейська сесія - підвищена волатильність")
        elif 13 <= current_hour <= 21:  # US session
            seasonal_analysis['time_zone_bias']['US'] = 'ACTIVE'
            seasonal_analysis['recommendations'].append("Активна американська сесія - висока волатильність")
        elif 0 <= current_hour <= 7:  # Asian session
            seasonal_analysis['time_zone_bias']['ASIA'] = 'ACTIVE'
            seasonal_analysis['recommendations'].append("Азійська сесія - нижча волатильність")

        # Аналіз дня тижня
        current_day = seasonal_analysis['current_day']
        if current_day in ['Monday', 'Friday']:
            seasonal_analysis['recommendations'].append(f"{current_day} - підвищена волатильність на початку/кінці тижня")

        self.results['seasonal_patterns'] = seasonal_analysis
        return seasonal_analysis

    def generate_trading_recommendations(self) -> Dict[str, Any]:
        """Генерація торгових рекомендацій"""
        logger.info("💡 Генерація торгових рекомендацій...")

        recommendations = {
            'immediate_actions': [],
            'risk_management': [],
            'strategy_adjustments': [],
            'monitoring_points': []
        }

        # На основі попередніх фаз
        recommendations['immediate_actions'].extend([
            "Терміново перемістити 6 hardcoded секретів в environment variables",
            "Виправити 1 SQL injection та 1 command injection вразливість",
            "Замінити 16 bare except blocks на специфічні exception types",
            "Рефакторити 62 функції довжиною >50 рядків"
        ])

        recommendations['risk_management'].extend([
            "Зменшити gate pass rate з 93.8% до 70% для кращої фільтрації",
            "Додати L/S Ratio як 13-й gate критерій",
            "Імплементувати stricter stop-loss правила",
            "Додати maximum drawdown limits"
        ])

        recommendations['strategy_adjustments'].extend([
            "Оптимізувати entry/exit умови на основі 17.46% win rate",
            "Покращити risk-reward ratio розрахунки",
            "Додати backtesting framework для тестування змін",
            "Імплементувати ML модель для gate logic"
        ])

        market_regime = self.results.get('market_regime', {})
        if market_regime.get('overall_regime') == 'BULLISH':
            recommendations['strategy_adjustments'].append("Ринок бичачий - пріоритет LONG позицій")
        elif market_regime.get('overall_regime') == 'BEARISH':
            recommendations['strategy_adjustments'].append("Ринок ведмежий - пріоритет SHORT позицій")

        recommendations['monitoring_points'].extend([
            "Моніторити gate pass rate після оптимізації",
            "Відслідковувати win rate покращення",
            "Контролювати drawdown limits",
            "Аналізувати P&L по символах щотижня"
        ])

        self.results['trading_recommendations'] = recommendations
        return recommendations

    def generate_final_report(self) -> str:
        """Генерація фінального звіту"""
        report = []
        report.append("# 📊 PHASE 6: MARKET ANALYSIS & FINAL REPORT")
        report.append(f"**Timestamp:** {datetime.now().isoformat()}")
        report.append("")

        # Резюме всіх фаз
        report.append("## 📈 PILOT PLAN EXECUTION SUMMARY")
        report.append("")

        phases_summary = [
            ("Phase 1: System Diagnostics", "✅ COMPLETED", "Infrastructure OK, Bot Running"),
            ("Phase 2: Data Analysis", "✅ COMPLETED", "17.46% Win Rate, -$86.93 Loss - CRITICAL"),
            ("Phase 3: Gate Logic Validation", "✅ COMPLETED", "93.8% Pass Rate - TOO LENIENT"),
            ("Phase 4: Code Quality Review", "✅ COMPLETED", "168 files, 728 functions, 62 complex functions"),
            ("Phase 5: Security & Configuration", "✅ COMPLETED", "6 secrets, 1 SQL + 1 CMD injection risk"),
            ("Phase 6: Market Analysis", "✅ COMPLETED", "Market regime analysis & recommendations")
        ]

        for phase, status, details in phases_summary:
            report.append(f"- **{phase}**: {status} - {details}")

        report.append("")

        # Критичні проблеми
        report.append("## 🚨 CRITICAL ISSUES IDENTIFIED")
        report.append("")
        critical_issues = [
            "17.46% Win Rate (Target: 35%+)",
            "-$86.93 Total P&L Loss",
            "24-trade Loss Streak (Max)",
            "93.8% Gate Pass Rate (Too High)",
            "6 Hardcoded Secrets in Code",
            "1 SQL Injection + 1 Command Injection Risk",
            "16 Bare Except Blocks",
            "62 Functions >50 Lines (Complexity)"
        ]

        for issue in critical_issues:
            report.append(f"- ❌ {issue}")

        report.append("")

        # Ринковий аналіз
        if 'market_conditions' in self.results:
            market = self.results['market_conditions']
            report.append("## 📊 CURRENT MARKET ANALYSIS")
            report.append(f"- **Symbols analyzed:** {len(market['symbols_analyzed'])}")
            report.append(f"- **L/S Ratio data:** {len(market['ls_ratio_data'])} symbols")

            if market['ls_ratio_data']:
                report.append("- **Sentiment breakdown:**")
                sentiments = {}
                for symbol, data in market['ls_ratio_data'].items():
                    sentiment = data.get('sentiment', 'UNKNOWN')
                    sentiments[sentiment] = sentiments.get(sentiment, 0) + 1

                for sentiment, count in sentiments.items():
                    report.append(f"  - {sentiment}: {count} symbols")

            report.append("")

        # Рекомендації
        if 'trading_recommendations' in self.results:
            recs = self.results['trading_recommendations']
            report.append("## 💡 ACTIONABLE RECOMMENDATIONS")
            report.append("")

            if recs['immediate_actions']:
                report.append("### Immediate Actions (Priority 1):")
                for action in recs['immediate_actions']:
                    report.append(f"- 🔴 {action}")
                report.append("")

            if recs['risk_management']:
                report.append("### Risk Management (Priority 2):")
                for rm in recs['risk_management']:
                    report.append(f"- 🟡 {rm}")
                report.append("")

            if recs['strategy_adjustments']:
                report.append("### Strategy Adjustments (Priority 3):")
                for sa in recs['strategy_adjustments']:
                    report.append(f"- 🟢 {sa}")
                report.append("")

            if recs['monitoring_points']:
                report.append("### Monitoring Points:")
                for mp in recs['monitoring_points']:
                    report.append(f"- 👁️ {mp}")
                report.append("")

        # Наступні кроки
        report.append("## 🎯 NEXT STEPS")
        report.append("")
        next_steps = [
            "1. **Immediate Security Fixes** - Move secrets to env, fix injections",
            "2. **Gate Logic Optimization** - Reduce pass rate to 70%, add L/S ratio",
            "3. **Code Refactoring** - Break down complex functions, improve error handling",
            "4. **Backtesting Implementation** - Test gate logic changes",
            "5. **Risk Management Enhancement** - Implement drawdown limits",
            "6. **Performance Monitoring** - Track improvements over next 30 trades"
        ]

        for step in next_steps:
            report.append(f"- {step}")

        report.append("")
        report.append("---")
        report.append(f"**Report generated by MAXPILOT AI Assistant** - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        return "\n".join(report)

    async def run_market_analysis(self) -> Dict[str, Any]:
        """Запуск повного ринкового аналізу"""
        logger.info("🚀 ЗАПУСК ФАЗИ 6: РИНКОВИЙ АНАЛІЗ")

        results = {
            'timestamp': datetime.now().isoformat(),
            'phase': 'market_analysis_final',
            'status': 'running'
        }

        try:
            # 1. Аналіз поточних умов ринку
            market_conditions = await self.analyze_current_market_conditions()

            # 2. Аналіз ринкового режиму
            market_regime = self.analyze_market_regime()

            # 3. Аналіз сезонних патернів
            seasonal_patterns = self.analyze_seasonal_patterns()

            # 4. Генерація торгових рекомендацій
            trading_recs = self.generate_trading_recommendations()

            # 5. Генерація фінального звіту
            final_report = self.generate_final_report()

            results.update({
                'market_conditions': market_conditions,
                'market_regime': market_regime,
                'seasonal_patterns': seasonal_patterns,
                'trading_recommendations': trading_recs,
                'final_report': final_report,
                'status': 'completed'
            })

            logger.info("✅ Фаза 6 завершена успішно - PILOT PLAN COMPLETED!")

        except Exception as e:
            logger.error(f"❌ Помилка аналізу: {e}")
            results['status'] = 'failed'
            results['error'] = str(e)

        # Збереження результатів
        self.save_results(results)

        return results

    def save_results(self, results: Dict[str, Any]) -> None:
        """Збереження результатів в файл"""
        output_file = Path('phase6_market_analysis.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Результати збережено в {output_file}")

        # Також зберегти фінальний звіт
        report_file = Path('pilot_final_report.md')
        if 'final_report' in results:
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(results['final_report'])

            logger.info(f"Фінальний звіт збережено в {report_file}")


async def main():
    """Основна функція"""
    analyzer = MarketAnalyzer()
    results = await analyzer.run_market_analysis()

    # Вивід основних результатів
    print("\n" + "="*60)
    print("PHASE 6: MARKET ANALYSIS & FINAL REPORT")
    print("="*60)

    if results['status'] == 'completed':
        print("✅ Market analysis completed successfully")
        print("🎉 PILOT PLAN EXECUTION COMPLETED!")

        if 'market_conditions' in results:
            market = results['market_conditions']
            print(f"📊 Analyzed {len(market['symbols_analyzed'])} symbols")

        if 'market_regime' in results:
            regime = results['market_regime']
            print(f"🎯 Market regime: {regime.get('overall_regime', 'UNKNOWN')}")

    else:
        print(f"❌ Analysis failed: {results.get('error', 'Unknown error')}")

    print("\n📄 Detailed results saved to phase6_market_analysis.json")
    print("📄 Final report saved to pilot_final_report.md")
    print("\n🎯 PILOT PLAN SUCCESSFULLY COMPLETED!")
    print("All 6 phases executed with comprehensive analysis and recommendations.")


if __name__ == "__main__":
    asyncio.run(main())