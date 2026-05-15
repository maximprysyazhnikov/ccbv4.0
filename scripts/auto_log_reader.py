"""Автоматичний читач логів для CCBV3.8

Запуск (у робочій директорії проекту):
    python scripts/auto_log_reader.py --log-file logs/app.log

Особливості:
- Підтримує парсинг JSON-логів або простих текстових логів, які генеруються в проєкті.
- Слідкує за файлом (tail -F) і обробляє нові рядки в реальному часі.
- Збирає статистику: кількість WARN/ERROR, часті повідомлення, специфічні помилки (наприклад, "Error processing <SYMBOL>: 'volume'").
- Виводить зведення українською.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict
from typing import Optional

ERROR_PATTERN = re.compile(r"Error processing\s+([A-Z0-9]+):\s+'(?P<key>[^']+)'", re.IGNORECASE)
LEVEL_PATTERN = re.compile(r"-\s+(?P<logger>[^\s]+)\s+-\s+(?P<level>INFO|WARNING|ERROR|CRITICAL)\s+-\s+(?P<msg>.*)")
JSON_LEVELS = {"ERROR", "WARNING", "INFO", "CRITICAL", "DEBUG"}


def tail_f(path: str, sleep: float = 0.5):
    """Простий tail -F для файлу: читає додані рядки та обробляє ротацію файлу."""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:
                # перевірити ротацію (файл обнулено)
                try:
                    if os.path.getsize(path) < fh.tell():
                        fh.seek(0)
                except FileNotFoundError:
                    # файл тимчасово відсутній
                    time.sleep(sleep)
                    continue
                time.sleep(sleep)
                continue
            yield line


class LogStats:
    def __init__(self):
        self.counts = Counter()
        self.messages = Counter()
        self.symbol_errors = defaultdict(Counter)  # symbol -> Counter(key -> count)

    def process_line(self, line: str):
        line = line.strip()
        if not line:
            return
        # Спробувати JSON
        try:
            obj = json.loads(line)
            lvl = obj.get("level", "INFO")
            msg = obj.get("message", "")
        except Exception:
            # Не JSON — парсимо як звичайний лог lformat
            m = LEVEL_PATTERN.search(line)
            if m:
                lvl = m.group("level")
                msg = m.group("msg")
            else:
                # fallback, простий текст
                lvl = "INFO"
                msg = line

        self.counts[lvl] += 1
        self.messages[msg] += 1

        # спеціальні шаблони
        m = ERROR_PATTERN.search(msg)
        if m:
            symbol = m.group(1)
            key = m.group("key")
            self.symbol_errors[symbol][key] += 1

    def summary(self) -> str:
        lines = []
        lines.append("🔎 Зведення логів:")
        lines.append(f"  ✅ INFO: {self.counts['INFO']}")
        lines.append(f"  ⚠️ WARNING: {self.counts['WARNING']}")
        lines.append(f"  ❌ ERROR: {self.counts['ERROR']}")
        if self.symbol_errors:
            lines.append("\n📌 Спеціальні помилки по символах:")
            for sym, ctr in sorted(self.symbol_errors.items(), key=lambda x: -sum(x[1].values())):
                details = ", ".join(f"{k}: {v}" for k, v in ctr.items())
                lines.append(f"  - {sym}: {details}")
        # найчастіші повідомлення
        if self.messages:
            lines.append("\n🔥 Топ повідомлень:")
            for msg, cnt in self.messages.most_common(5):
                short = msg if len(msg) < 150 else msg[:147] + "..."
                lines.append(f"  - {cnt}× {short}")
        return "\n".join(lines)


def watch(log_file: str, interval: int = 30):
    stats = LogStats()
    print(f"Стартую моніторинг логу: {log_file}")

    # Якщо файл відсутній — зачекаємо
    while not os.path.exists(log_file):
        print(f"Чекаю появи {log_file}...")
        time.sleep(1)

    tail = tail_f(log_file)
    last_summary = time.time()
    try:
        for line in tail:
            stats.process_line(line)
            # миттєві повідомлення для ERROR/WARNING
            if "ERROR" in line or "WARNING" in line or "Error processing" in line:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Новий рядок: {line.strip()}")

            if time.time() - last_summary >= interval:
                print("\n" + stats.summary() + "\n")
                last_summary = time.time()
    except KeyboardInterrupt:
        print("Зупинено користувачем.")


def main():
    p = argparse.ArgumentParser(description="Автоматичний читач логів (українською)")
    p.add_argument("--log-file", default="logs/app.log", help="Шлях до файлу логу")
    p.add_argument("--interval", type=int, default=30, help="Інтервал зведення в секундах")
    args = p.parse_args()

    watch(args.log_file, interval=args.interval)


if __name__ == "__main__":
    main()
