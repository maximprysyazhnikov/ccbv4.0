"""Запуск бота разом з монітором логів

Запуск:
    python scripts/run_and_monitor.py

Сценарій:
- Стартує `main.py` в підпроцесі
- Перенаправляє stdout/stderr підпроцесу в `logs/app.log`
- Паралельно запускає `scripts/auto_log_reader.py` для реального моніторингу та виводу зведень українською
"""
from __future__ import annotations
import os
import sys
import time
import threading
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT / "logs" / "app.log"

os.makedirs(LOG_FILE.parent, exist_ok=True)

def _forward_subproc_output(proc, log_path: Path):
    # Читає stdout підпроцесу і пише в лог
    with open(log_path, "a", encoding="utf-8", errors="ignore") as fh:
        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue
            try:
                decoded = line.decode("utf-8", errors="replace")
            except Exception:
                decoded = str(line)
            fh.write(decoded)
            fh.flush()
            # також виводимо в консоль для користувача
            print(decoded, end="")


def run():
    env = os.environ.copy()
    env["LOG_FILE"] = str(LOG_FILE)

    cmd = [sys.executable, str(ROOT / "main.py")]
    print(f"Стартую підпроцес: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        env=env,
    )

    # Запустимо поток який форвардить stdout у лог-файл
    t = threading.Thread(target=_forward_subproc_output, args=(proc, LOG_FILE), daemon=True)
    t.start()

    # Імпортуємо та запустимо монітор логів з нашого скрипта через завантаження по шляху
    import importlib.util
    spec = importlib.util.spec_from_file_location("auto_log_reader", str(ROOT / "scripts" / "auto_log_reader.py"))
    auto_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auto_mod)
    watch_thread = threading.Thread(target=auto_mod.watch, args=(str(LOG_FILE), 5), daemon=True)
    watch_thread.start()

    try:
        while proc.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Отримано SIGINT, зупиняю підпроцес...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("Підпроцес завершено.")

if __name__ == "__main__":
    run()
