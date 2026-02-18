import requests
import json
import os
import sys
from datetime import datetime

# --- Configuration ---
# We use the AVWX API which is reliable for OIIX (Tehran FIR)
API_URL = "https://avwx.rest/api/notam/OIIX"
STATE_FILE = "state.json"

# Secrets
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
AVWX_TOKEN = os.environ.get("AVWX_TOKEN")

# Validations
if not TELEGRAM_TOKEN or not CHAT_ID:
    print("Error: Telegram secrets missing.")
    sys.exit(1)

if not AVWX_TOKEN:
    print("Error: AVWX_TOKEN is missing. Please add it to GitHub Secrets.")
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
    """Fetches OIIX NOTAMs from AVWX API."""
    headers = {
        "Authorization": f"Bearer {AVWX_TOKEN}"
    }
    try:
        response = requests.get(API_URL, headers=headers, timeout=20)
        
        if response.status_code == 403 or response.status_code == 401:
            print("Error: AVWX Token is invalid or unauthorized.")
            return []
            
        response.raise_for_status()
        return response.json() # AVWX returns a list of NOTAM objects directly
    except Exception as e:
        print(f"Error fetching data: {e}")
        return []

def main():
    print("Fetching OIIX NOTAMs from AVWX...")
    
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
        print("No NOTAMs found (or API error).")
        return

    new_ids = []
    sent_count = 0

    # 3. Process
    # AVWX returns a list. Each item usually has 'sanitized' or 'raw' fields.
    for notam in current_notams:
        # Construct a unique ID. AVWX usually has an internal 'station' and 'number'
        # But looking at the response, we often rely on the raw NOTAM ID embedded in text
        # Or we can hash the content. However, AVWX usually provides an index.
        
        # Let's trust the text content as unique if no ID field exists
        raw_text = notam.get("raw") or notam.get("body")
        
        # Try to find the NOTAM ID (e.g., A0123/25) inside the text
        # Usually it's the first word or line.
        # We will use the 'sanitized' unique handle if available, otherwise raw text hash.
        # For simplicity, let's use the first 15 chars of raw text as a "key" if needed
        # BUT AVWX usually sends newest first.
        
        # Unique ID strategy: Use the raw text itself as the unique key to prevent dupes
        # (Since NOTAM IDs get reused over years, text is safer for short term)
        unique_key = raw_text[:50] # First 50 chars usually contain ID and Type
        
        if unique_key not in seen_ids:
            # It's new!
            msg = f"🚨 **New OIIX NOTAM**\n\n{raw_text}"
            send_telegram(msg)
            
            seen_ids.append(unique_key)
            new_ids.append(unique_key)
            sent_count += 1

    # 4. Save state
    # Keep list from growing forever
    with open(STATE_FILE, "w") as f:
        json.dump(seen_ids[-200:], f)

    print(f"Success. Sent {sent_count} new notifications.")

if __name__ == "__main__":
    main()
