import requests
import os

def test_telegram_token():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/getMe"
    response = requests.get(url)
    print("Response Status Code:", response.status_code)
    print("Response JSON:", response.json())

if __name__ == "__main__":
    test_telegram_token()