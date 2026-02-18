import requests
import json
import os
import sys
from datetime import datetime

# --- Configuration ---
FIR_CODE = "OIIX"  # Tehran FIR
STATE_FILE = "state.json"

# Secrets from GitHub Environment
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("Error: Secrets not found!")
    sys.exit(1)

def send_telegram(message):
    """Sends a message to your Telegram via the Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Failed to send message: {e}")

def get_notams():
    """Fetches OIIX NOTAMs from AviationWeather.gov API."""
    # This is a public, free US government API that tracks global NOTAMs
    url = f"https://aviationweather.gov/api/data/notam?ids={FIR_CODE}&format=json"
    
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching data: {e}")
        return []

def main():
    print(f"Checking NOTAMs for {FIR_CODE}...")
    
    # 1. Load the list of NOTAMs we have already seen
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                seen_ids = json.load(f)
            except json.JSONDecodeError:
                seen_ids = []
    else:
        seen_ids = []

    # 2. Fetch current NOTAMs
    current_notams = get_notams()
    
    if not current_notams:
        print("No data received or API error.")
        return

    new_ids = []
    messages_sent = 0

    # 3. Process data
    for notam in current_notams:
        # The API usually returns an 'id' field like "A1234/26"
        # Sometimes it's nested, but usually top level in this API
        notam_id = notam.get("id") or notam.get("key")
        
        if not notam_id:
            continue

        # If we haven't seen this ID before, it's new!
        if notam_id not in seen_ids:
            raw_text = notam.get("rawText", "No text available")
            
            # Clean up the text a bit for readability
            msg = f"🚨 **New OIIX NOTAM**\n`{notam_id}`\n\n{raw_text}"
            
            send_telegram(msg)
            seen_ids.append(notam_id)
            new_ids.append(notam_id)
            messages_sent += 1

    # 4. Save the updated list back to the file
    # We only keep the last 500 IDs to keep the file small
    with open(STATE_FILE, "w") as f:
        json.dump(seen_ids[-500:], f)

    print(f"Done. Sent {messages_sent} notifications.")

if __name__ == "__main__":
    main()
