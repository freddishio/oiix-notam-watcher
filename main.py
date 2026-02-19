import requests
import json
import os
import sys
from datetime import datetime

# --- Configuration ---
# SOURCE: FAA AIM (Official US Data)
# LOCATION: OIIX ONLY (Tehran Flight Information Region)
# This captures "Big Picture" risks: Missiles, Guns, Airway Closures.
URL = "https://notams.aim.faa.gov/notamSearch/search"
STATE_FILE = "state.json"
LOG_FILE = "notam_log.txt"

# Secrets
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("Error: Telegram secrets are missing.")
    sys.exit(1)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    requests.post(url, json=payload)

def log_notam_to_file(notam_id, text):
    """Appends the NOTAM text to a log file with a timestamp."""
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    entry = f"\n{'='*40}\nDATE: {timestamp}\nID: {notam_id}\n\n{text}\n{'='*40}\n"
    
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)

def get_notams():
    # FAA AIM API Request
    payload = {
        "searchType": 0,
        "designatorsForLocation": "OIIX",  # <--- ONLY OIIX
        "offset": 0,
        "notamsOnly": False,
        "radius": 10
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }

    try:
        response = requests.post(URL, data=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"FAA API Error: {e}")
        return None

def main():
    print("Fetching data from FAA AIM (OIIX Only)...")
    
    # 1. Load state
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                seen_ids = json.load(f)
            except:
                seen_ids = []
    else:
        seen_ids = []

    # 2. Fetch
    data = get_notams()
    
    if not data or "notamList" not in data:
        print("No valid data received.")
        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w") as f:
                json.dump([], f)
        return

    notam_list = data["notamList"]
    print(f"Received {len(notam_list)} NOTAMs from FAA.")

    new_ids = []
    sent_count = 0

    # 3. Process
    for notam in notam_list:
        notam_id = notam.get("notamNumber")
        icao_id = notam.get("icaoId")
        raw_text = notam.get("icaoMessage") or "No text"

        if not notam_id:
            continue
            
        full_id = f"{icao_id} {notam_id}"

        if full_id not in seen_ids:
            # Alert Icon for OIIX
            msg = f"🚀 **TEHRAN FIR ALERT (OIIX)**\n`{notam_id}`\n\n{raw_text}"
            
            # Send & Log
            send_telegram(msg)
            log_notam_to_file(full_id, raw_text)
            
            seen_ids.append(full_id)
            new_ids.append(full_id)
            sent_count += 1

    # 4. Save state
    if new_ids:
        updated_list = seen_ids + new_ids
        with open(STATE_FILE, "w") as f:
            json.dump(updated_list[-500:], f)
            
    print(f"Success. Sent and logged {sent_count} notifications.")

if __name__ == "__main__":
    main()
