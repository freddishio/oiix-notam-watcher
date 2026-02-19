import requests
import json
import os
import sys
import time
from datetime import datetime

# --- Configuration ---
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
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

def log_notam_to_file(notam_id, text):
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    entry = f"\n{'='*40}\nDATE: {timestamp}\nID: {notam_id}\n\n{text}\n{'='*40}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)

def get_all_notams():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    
    all_notams = []
    offset = 0
    batch_size = 30
    
    print("Starting fetch loop...")
    
    while True:
        payload = {
            "searchType": 0,
            "designatorsForLocation": "OIIX",
            "offset": offset,
            "notamsOnly": False,
            "radius": 10
        }
        
        try:
            response = requests.post(URL, data=payload, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if not data or "notamList" not in data:
                break
                
            current_batch = data["notamList"]
            if not current_batch:
                break
                
            all_notams.extend(current_batch)
            print(f"  Fetched {len(current_batch)} NOTAMs (Offset: {offset})")
            
            offset += len(current_batch)
            if len(current_batch) < batch_size:
                break
            
            time.sleep(1)
            
        except Exception as e:
            print(f"Error fetching page at offset {offset}: {e}")
            break
            
    return all_notams

def load_state():
    """
    Loads state. Handles migration from:
    1. Old List format: ["ID1", "ID2"]
    2. Dict with List: {"seen_ids": ["ID1"]}
    3. New Dict format: {"seen_ids": {"ID1": "Time1"}}
    """
    if not os.path.exists(STATE_FILE):
        return {}
        
    with open(STATE_FILE, "r") as f:
        try:
            data = json.load(f)
            
            # Case 1: Extremely old format (Just a list)
            if isinstance(data, list):
                # Convert to dict with dummy timestamp
                return {pid: "Legacy (Pre-Timestamp)" for pid in data}
            
            # Case 2: Dict format
            raw_seen = data.get("seen_ids", [])
            
            # Sub-case: seen_ids is a List (Previous version)
            if isinstance(raw_seen, list):
                return {pid: "Legacy (Pre-Timestamp)" for pid in raw_seen}
                
            # Sub-case: seen_ids is already a Dict (Target)
            if isinstance(raw_seen, dict):
                return raw_seen
                
            return {}
        except:
            return {}

def save_state(seen_ids_dict):
    """Saves state with timestamps and header info."""
    
    # Prune: Keep only the last 1000 entries to prevent file from getting huge
    # In Python 3.7+, dictionaries preserve insertion order, so this keeps the newest.
    if len(seen_ids_dict) > 1000:
        seen_ids_dict = dict(list(seen_ids_dict.items())[-1000:])
        
    state_data = {
        "last_run_utc": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        "seen_ids": seen_ids_dict
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state_data, f, indent=2)

def main():
    print("Fetching FULL data from FAA AIM (OIIX Only)...")
    
    # seen_ids is now a DICTIONARY: {"ID": "Timestamp"}
    seen_ids = load_state()
    notam_list = get_all_notams()
    print(f"Total NOTAMs retrieved: {len(notam_list)}")
    
    if not notam_list:
        print("No valid data received.")
        save_state(seen_ids) # Update last_run_utc anyway
        return

    new_count = 0
    current_time_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    for notam in notam_list:
        notam_id = notam.get("notamNumber")
        icao_id = notam.get("icaoId")
        raw_text = notam.get("icaoMessage") or "No text"

        if not notam_id:
            continue
            
        full_id = f"{icao_id} {notam_id}"

        # Check if ID is in the keys of the dictionary
        if full_id not in seen_ids:
            msg = f"🚀 **TEHRAN FIR ALERT (OIIX)**\n`{notam_id}`\n\n{raw_text}"
            send_telegram(msg)
            log_notam_to_file(full_id, raw_text)
            
            # Add to dict with current timestamp
            seen_ids[full_id] = current_time_str
            new_count += 1

    save_state(seen_ids)
    print(f"Success. Sent {new_count} notifications. Stats updated.")

if __name__ == "__main__":
    main()
