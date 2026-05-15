# utils/captcha.py
"""Simple emoji captcha for anti-bot protection."""
from __future__ import annotations
import random
import sqlite3
import logging
from typing import Optional, Tuple
from utils.db import get_conn

log = logging.getLogger("captcha")

# Емодзі групи для капчі
EMOJI_GROUPS = {
    "фрукти": ["🍎", "🍊", "🍋", "🍇", "🍉", "🍓", "🍑", "🍒"],
    "тварини": ["🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼"],
    "транспорт": ["🚗", "🚕", "🚙", "🚌", "🚎", "🏎️", "🚓", "🚑"],
    "погода": ["☀️", "🌙", "⭐", "🌈", "☁️", "⛈️", "❄️", "🔥"],
    "спорт": ["⚽", "🏀", "🏈", "⚾", "🎾", "🏐", "🎱", "🏓"],
    "їжа": ["🍕", "🍔", "🍟", "🌭", "🍿", "🧀", "🥚", "🍳"],
}

# Декой емодзі (не підходять до жодної групи)
DECOY_EMOJIS = ["💎", "🎸", "🎭", "🎪", "🎨", "🎯", "🎲", "🎮", "📱", "💻", "⌚", "📷", "🔑", "💡", "🔔", "📚"]


def _ensure_captcha_table() -> None:
    """Створює таблицю для верифікації, якщо не існує."""
    with get_conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS user_verified (
            user_id INTEGER PRIMARY KEY,
            verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)


def is_user_verified(user_id: int) -> bool:
    """Перевіряє чи користувач пройшов капчу."""
    _ensure_captcha_table()
    with get_conn() as c:
        row = c.execute(
            "SELECT verified FROM user_verified WHERE user_id = ?", 
            (user_id,)
        ).fetchone()
        return bool(row and row[0])


def set_user_verified(user_id: int, verified: bool = True) -> None:
    """Встановлює статус верифікації користувача."""
    _ensure_captcha_table()
    with get_conn() as c:
        c.execute("""
        INSERT INTO user_verified (user_id, verified) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET verified = excluded.verified
        """, (user_id, int(verified)))


def generate_captcha() -> Tuple[str, str, list]:
    """
    Генерує капчу.
    Повертає: (питання, правильна_відповідь, список_кнопок)
    """
    # Вибираємо випадкову групу
    group_name = random.choice(list(EMOJI_GROUPS.keys()))
    group_emojis = EMOJI_GROUPS[group_name]
    
    # Правильний емодзі
    correct_emoji = random.choice(group_emojis)
    
    # Генеруємо 5 декой емодзі (які не з цієї групи)
    other_emojis = []
    for name, emojis in EMOJI_GROUPS.items():
        if name != group_name:
            other_emojis.extend(emojis)
    other_emojis.extend(DECOY_EMOJIS)
    
    decoys = random.sample(other_emojis, 5)
    
    # Всі варіанти (1 правильний + 5 декой)
    all_options = [correct_emoji] + decoys
    random.shuffle(all_options)
    
    # Формуємо питання
    questions = {
        "фрукти": "🍎 Вибери ФРУКТ:",
        "тварини": "🐾 Вибери ТВАРИНУ:",
        "транспорт": "🚗 Вибери ТРАНСПОРТ:",
        "погода": "🌤️ Вибери ПОГОДУ:",
        "спорт": "⚽ Вибери СПОРТ:",
        "їжа": "🍽️ Вибери ЇЖУ:",
    }
    question = questions.get(group_name, f"Вибери {group_name}:")
    
    return question, correct_emoji, all_options


def create_captcha_keyboard(options: list):
    """Створює inline клавіатуру для капчі."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    # 2 ряди по 3 кнопки
    buttons = []
    row = []
    for i, emoji in enumerate(options):
        row.append(InlineKeyboardButton(emoji, callback_data=f"captcha:{emoji}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    return InlineKeyboardMarkup(buttons)
