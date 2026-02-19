import requests
import json
import os
import sys

# --- Configuration ---
# Source: FAA AIM (Aeronautical Information Management) - The official source.
# We monitor OIIE and OIII (Tehran Airports) which capture FIR notifications.
URL = "https://notams.aim.faa.gov/notamSearch/search"
STATE_FILE = "state.json"

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

def get_notams():
    # The FAA AIM API expects a Form-Encoded POST request
    payload = {
        "searchType": 0,
        "designatorsForLocation": "OIIE,OIII",  # Monitoring both major airports
        "offset": 0,
        "notamsOnly": False,
        "radius": 10
    }
    
    # We must look like a real browser
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
    print("Fetching data from FAA AIM...")
    
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
        # Create empty file if needed
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
        # FAA AIM JSON Structure:
        # { "notamNumber": "A1234/26", "icaoId": "OIIE", "icaoMessage": "..." }
        
        notam_id = notam.get("notamNumber")
        icao_id = notam.get("icaoId")
        raw_text = notam.get("icaoMessage") or "No text"

        if not notam_id:
            continue
            
        # Combine ICAO + ID to ensure uniqueness (e.g. OIIE A0001/26)
        full_id = f"{icao_id} {notam_id}"

        if full_id not in seen_ids:
            # Clean up the message
            msg = f"🚨 **NOTAM: {icao_id} {notam_id}**\n\n`{raw_text}`"
            
            # Send (Short pause to avoid hitting Telegram limits if many)
            send_telegram(msg)
            
            seen_ids.append(full_id)
            new_ids.append(full_id)
            sent_count += 1

    # 4. Save state
    # Update the seen list with new ones
    if new_ids:
        # Keep only the last 300 to save space
        updated_list = seen_ids + new_ids
        with open(STATE_FILE, "w") as f:
            json.dump(updated_list[-300:], f)
            
    print(f"Success. Sent {sent_count} notifications.")

if __name__ == "__main__":
    main()
