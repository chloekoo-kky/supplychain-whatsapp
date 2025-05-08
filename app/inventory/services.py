import requests
from django.conf import settings


def send_whatsapp_notification(to_number, message):
    url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message}
    }
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"[WHATSAPP ERROR] {e}")
