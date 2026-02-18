import requests
import json
import os
import sys

# --- Configuration ---
# OIIX (FIR) is often restricted in free APIs. 
# We monitor OIIE (Tehran Imam Khomeini) which captures major FIR NOTAMs.
STATION = "OIIE" 
API_URL = f"https://avwx.rest/api/notam/{STATION}"
STATE_FILE = "state.json"

# Secrets
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
AVWX_TOKEN = os.environ.get("AVWX_TOKEN")

if not TELEGRAM_TOKEN or not CHAT_ID or not AVWX_TOKEN:
    print("Error: One or more secrets are missing.")
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
    headers = {
        "Authorization": f"Bearer {AVWX_TOKEN}",
        "User-Agent": "OIIX-Student-Bot/1.0"  # Important to avoid blocks
    }
    try:
        response = requests.get(API_URL, headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"API Error: {e}")
        return []

def main():
    print(f"Fetching NOTAMs for {STATION}...")
    
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
        print("No data received.")
        # Create empty state file if it doesn't exist so git doesn't error
        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w") as f:
                json.dump([], f)
        return

    new_ids = []
    sent_count = 0

    # 3. Process
    for notam in current_notams:
        # Use the raw text as a unique key since IDs can be inconsistent
        raw_text = notam.get("raw") or notam.get("body")
        unique_key = raw_text[:50] # First 50 chars as signature
        
        if unique_key not in seen_ids:
            msg = f"🚨 **New Tehran NOTAM (via {STATION})**\n\n{raw_text}"
            send_telegram(msg)
            
            seen_ids.append(unique_key)
            new_ids.append(unique_key)
            sent_count += 1

    # 4. Save state
    with open(STATE_FILE, "w") as f:
        json.dump(seen_ids[-200:], f)

    print(f"Success. Sent {sent_count} new notifications.")

if __name__ == "__main__":
    main()
