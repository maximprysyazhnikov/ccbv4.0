# get_chat_id.py
import os
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in the environment variables.")

url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

resp = requests.get(url)
data = resp.json()

print(data)  # повна відповідь для перевірки

if "result" in data and data["result"]:
    for update in data["result"]:
        chat = update.get("message", {}).get("chat", {})
        if chat:
            print("=== CHAT INFO ===")
            print("ID:", chat.get("id"))
            print("Title:", chat.get("title"))
            print("Type:", chat.get("type"))
else:
    print("❌ Порожньо. Напиши щось у групі і запусти ще раз.")
