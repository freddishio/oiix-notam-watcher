import requests
import json
import os
import sys
import time
import subprocess
import tempfile
from datetime import datetime

# Configuration
URL = "https://notams.aim.faa.gov/notamSearch/search"
STATE_FILE = "state.json"
HISTORY_FILE = "run_history.json"

# Separate databases for your future expansion
ACTIVE_RAW_FILE = "active_notams_raw.json"
ACTIVE_DECODED_FILE = "active_notams_decoded.json"
EXPIRED_RAW_FILE = "expired_notams_raw.json"
EXPIRED_DECODED_FILE = "expired_notams_decoded.json"

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

def create_wrapper():
    """Generates a Javascript bridge that writes directly to a file."""
    wrapper_code = """const fs = require('fs');
try {
    const notamDecoder = require('./notam-decoder.js');
    const inputPath = process.argv[2];
    const outputPath = process.argv[3];
    const rawNotam = fs.readFileSync(inputPath, 'utf8');
    let decoded = notamDecoder.decode(rawNotam);
    if (!decoded) { 
        decoded = {error: "Decoder returned empty result"}; 
    }
    fs.writeFileSync(outputPath, JSON.stringify(decoded), 'utf8');
} catch (e) {
    const outputPath = process.argv[3];
    fs.writeFileSync(outputPath, JSON.stringify({error: e.toString()}), 'utf8');
}"""
    with open("wrapper.js", "w", encoding="utf-8") as f:
        f.write(wrapper_code)

def decode_notam(raw_text):
    create_wrapper()
    
    fd_in, temp_path_in = tempfile.mkstemp(text=True)
    fd_out, temp_path_out = tempfile.mkstemp(text=True)
    
    os.close(fd_in)
    os.close(fd_out)
    
    try:
        with open(temp_path_in, 'w', encoding='utf-8') as f:
            f.write(raw_text)
            
        process = subprocess.run(
            ['node', 'wrapper.js', temp_path_in, temp_path_out], 
            capture_output=True, 
            text=True
        )
        
        with open(temp_path_out, 'r', encoding='utf-8') as f:
            output_data = f.read()
            
        if not output_data.strip():
            return {
                "error": "Empty output file from Node", 
                "stdout": process.stdout.strip(), 
                "stderr": process.stderr.strip()
            }
            
        return json.loads(output_data)
    except Exception as e:
        return {"error": f"Python Execution Error: {str(e)}"}
    finally:
        if os.path.exists(temp_path_in):
            os.remove(temp_path_in)
        if os.path.exists(temp_path_out):
            os.remove(temp_path_out)

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
    
    seen_ids = load_json(STATE_FILE, {})
    run_history = load_json(HISTORY_FILE, [])
    
    active_notams_raw = load_json(ACTIVE_RAW_FILE, {})
    active_notams_decoded = load_json(ACTIVE_DECODED_FILE, {})
    
    expired_notams_raw = load_json(EXPIRED_RAW_FILE, {})
    expired_notams_decoded = load_json(EXPIRED_DECODED_FILE, {})
    
    notam_list = get_all_notams()
    
    if not notam_list:
        print("No valid data received. Skipping processing to protect state.")
        return

    current_time_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    current_raw_dict = {}
    current_decoded_dict = {}
    new_count = 0

    for notam in notam_list:
        notam_id = notam.get("notamNumber")
        icao_id = notam.get("icaoId")
        raw_text = notam.get("icaoMessage") or "No text"

        if not notam_id:
            continue
            
        full_id = f"{icao_id} {notam_id}"
        
        notam["last_seen_utc"] = current_time_str
        current_raw_dict[full_id] = notam

        if full_id in active_notams_decoded:
            decoded_obj = active_notams_decoded[full_id]
            decoded_obj["last_seen_utc"] = current_time_str
            current_decoded_dict[full_id] = decoded_obj
        else:
            decoded_obj = decode_notam(raw_text)
            if decoded_obj:
                decoded_obj["last_seen_utc"] = current_time_str
                current_decoded_dict[full_id] = decoded_obj

        if full_id not in seen_ids:
            subject_text = "Unknown Subject"
            condition_text = "Unknown Condition"
            
            if decoded_obj and "qualification" in decoded_obj:
                qual = decoded_obj["qualification"]
                if isinstance(qual, dict):
                    subj = qual.get("subject")
                    cond = qual.get("condition")
                    
                    if isinstance(subj, dict):
                        subject_text = subj.get("subject", subject_text)
                    elif isinstance(subj, str):
                        subject_text = subj
                        
                    if isinstance(cond, dict):
                        condition_text = cond.get("condition", condition_text)
                    elif isinstance(cond, str):
                        condition_text = cond
            
            if decoded_obj and "error" in decoded_obj:
                error_msg = decoded_obj.get("error", "Failed to parse")
                msg = f"🚀 **TEHRAN FIR ALERT (OIIX)**\n`{notam_id}`\n\n**System Error:** {error_msg}\n\n**Raw Text:**\n`{raw_text}`"
            elif decoded_obj and "errors" in decoded_obj:
                error_list = ", ".join(decoded_obj["errors"])
                msg = f"🚀 **TEHRAN FIR ALERT (OIIX)**\n`{notam_id}`\n\n**Decoder Alert:** {error_list}\n\n**Raw Text:**\n`{raw_text}`"
            else:
                msg = f"🚀 **TEHRAN FIR ALERT (OIIX)**\n`{notam_id}`\n\n**Subject:** {subject_text}\n**Condition:** {condition_text}\n\n**Raw Text:**\n`{raw_text}`"
            
            send_telegram(msg)
            seen_ids[full_id] = current_time_str
            new_count += 1

    removed_count = 0
    newly_expired_raw = {}
    newly_expired_decoded = {}
    
    for old_id, old_data in active_notams_raw.items():
        if old_id not in current_raw_dict:
            old_data["archived_utc"] = current_time_str
            newly_expired_raw[old_id] = old_data
            
            if old_id in active_notams_decoded:
                dec_data = active_notams_decoded[old_id]
                dec_data["archived_utc"] = current_time_str
                newly_expired_decoded[old_id] = dec_data
                
            removed_count += 1
            
    expired_notams_raw = {**newly_expired_raw, **expired_notams_raw}
    expired_notams_decoded = {**newly_expired_decoded, **expired_notams_decoded}

    new_state = {}
    for cid in current_raw_dict.keys():
        new_state[cid] = seen_ids.get(cid, current_time_str)

    save_json(STATE_FILE, new_state)
    save_json(ACTIVE_RAW_FILE, current_raw_dict)
    save_json(ACTIVE_DECODED_FILE, current_decoded_dict)
    save_json(EXPIRED_RAW_FILE, expired_notams_raw)
    save_json(EXPIRED_DECODED_FILE, expired_notams_decoded)

    run_record = {
        "time_utc": current_time_str,
        "total_active": len(current_raw_dict),
        "new_added": new_count,
        "removed": removed_count
    }
    
    run_history.insert(0, run_record)
    save_json(HISTORY_FILE, run_history[:250])
    
    print(f"Stats: Total {len(current_raw_dict)}, New {new_count}, Removed {removed_count}")

if __name__ == "__main__":
    main()
