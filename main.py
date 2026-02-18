import requests
import json
import os
import sys

# --- Configuration ---
# We query the main Tehran airports (OIIE, OIII) using the US Gov API.
# This works without a key and captures most FIR-level warnings.
URL = "https://aviationweather.gov/api/data/notam?ids=OIIE,OIII&format=json"
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
    # AWC requires a User-Agent to look like a browser/app
    headers = {
        "User-Agent": "Student-Notam-Bot/1.0 (educational use)"
    }
    try:
        response = requests.get(URL, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"API Error: {e}")
        return []

def main():
    print("Fetching data from AviationWeather.gov...")
    
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
    current_notams = get_notams()
    
    if not current_notams:
        print("No data received (or empty list).")
        # Create empty state file if needed
        if not os.path.exists(STATE_FILE):
             with open(STATE_FILE, "w") as f:
                json.dump([], f)
        return

    new_ids = []
    sent_count = 0

    # 3. Process
    for notam in current_notams:
        # AWC API structure:
        # [{"id": "A1234/26", "rawText": "..."}]
        
        # Use the ID provided by the API (e.g., 'A0023/26')
        notam_id = notam.get("id")
        raw_text = notam.get("rawText") or str(notam)

        if not notam_id:
            continue

        # Check if new
        if notam_id not in seen_ids:
            # Clean up text for Telegram
            msg = f"🚨 **NOTAM: {notam_id}**\n\n`{raw_text}`"
            
            send_telegram(msg)
            
            seen_ids.append(notam_id)
            new_ids.append(notam_id)
            sent_count += 1

    # 4. Save state
    # Keep file size small
    with open(STATE_FILE, "w") as f:
        json.dump(seen_ids[-200:], f)

    print(f"Success. Sent {sent_count} notifications.")

if __name__ == "__main__":
    main()
