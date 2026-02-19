import requests
import json
import os
import sys
import time
from datetime import datetime

# Configuration
URL = "https://notams.aim.faa.gov/notamSearch/search"
STATE_FILE = "state.json"
HISTORY_FILE = "run_history.json"
ACTIVE_FILE = "active_notams.json"
EXPIRED_FILE = "expired_notams.json"

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

def get_all_notams():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    all_notams = []
    offset = 0
    batch_size = 30
    
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
            offset += len(current_batch)
            if len(current_batch) < batch_size:
                break
            time.sleep(1)
        except Exception as e:
            print(f"Error fetching page at offset {offset}: {e}")
            break
            
    return all_notams

def load_json(filepath, default_value):
    if not os.path.exists(filepath):
        return default_value
    with open(filepath, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return default_value

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def main():
    print("Fetching FULL data from FAA AIM (OIIX Only)...")
    
    # Load all our databases
    seen_ids = load_json(STATE_FILE, {})
    run_history = load_json(HISTORY_FILE, [])
    active_notams = load_json(ACTIVE_FILE, {})
    expired_notams = load_json(EXPIRED_FILE, {})
    
    notam_list = get_all_notams()
    
    if not notam_list:
        print("No valid data received. Skipping processing to protect state.")
        return

    current_time_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    current_dict = {}
    new_count = 0

    # Process the fresh NOTAMs
    for notam in notam_list:
        notam_id = notam.get("notamNumber")
        icao_id = notam.get("icaoId")
        raw_text = notam.get("icaoMessage") or "No text"

        if not notam_id:
            continue
            
        full_id = f"{icao_id} {notam_id}"
        
        # Save the complete raw JSON data for future bot features
        notam["last_seen_utc"] = current_time_str
        current_dict[full_id] = notam

        # Alert if we have never seen this ID before
        if full_id not in seen_ids:
            msg = f"🚀 **TEHRAN FIR ALERT (OIIX)**\n`{notam_id}`\n\n{raw_text}"
            send_telegram(msg)
            seen_ids[full_id] = current_time_str
            new_count += 1

    # Detect expired NOTAMs by comparing old active list to new active list
    removed_count = 0
    for old_id, old_data in active_notams.items():
        if old_id not in current_dict:
            # It vanished from the FAA feed so we archive it
            old_data["archived_utc"] = current_time_str
            expired_notams[old_id] = old_data
            removed_count += 1

    # Clean the state dictionary to only hold active IDs
    new_state = {}
    for cid in current_dict.keys():
        new_state[cid] = seen_ids.get(cid, current_time_str)

    # Save all our files
    save_json(STATE_FILE, new_state)
    save_json(ACTIVE_FILE, current_dict)
    save_json(EXPIRED_FILE, expired_notams)

    # Update run history
    run_record = {
        "time_utc": current_time_str,
        "total_active": len(current_dict),
        "new_added": new_count,
        "removed": removed_count
    }
    run_history.append(run_record)
    save_json(HISTORY_FILE, run_history[-250:])
    
    print(f"Stats: Total {len(current_dict)}, New {new_count}, Removed {removed_count}")

if __name__ == "__main__":
    main()
