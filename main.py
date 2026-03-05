import requests
import json
import os
import sys
import time
import subprocess
import tempfile
import re
from collections import deque
from datetime import datetime, timezone, timedelta
from shapely.geometry import Point, shape

URL = "https://notams.aim.faa.gov/notamSearch/search"
STATE_FILE = "state.json"
HISTORY_FILE = "run_history.json"
AI_BUFFER_FILE = "ai_buffer.json"

PLANE_STATE_FILE = "plane_state.json"
PLANE_HISTORY_FILE = "plane_history.json"
PLANE_ARCHIVE_FILE = "plane_archive.json"
ICAO_AIRLINES_FILE = "icao_airlines.json"

ACTIVE_RAW_FILE = "active_notams_raw.json"
ACTIVE_DECODED_FILE = "active_notams_decoded.json"
ACTIVE_AI_FILE = "active_notams_ai_decoded.json"

EXPIRED_RAW_FILE = "expired_notams_raw.json"
EXPIRED_DECODED_FILE = "expired_notams_decoded.json"
EXPIRED_AI_FILE = "expired_notams_ai_decoded.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("Error: Telegram secrets are missing.")
    sys.exit(1)

API_KEYS = [
    os.environ.get("GEMINI_API_KEY_F92"),
    os.environ.get("GEMINI_API_KEY_F1"),
    os.environ.get("GEMINI_API_KEY_FRED"),
    os.environ.get("GEMINI_API_KEY_APHR"),
    os.environ.get("GEMINI_API_KEY_MARZI"),
    os.environ.get("GEMINI_API_KEY_REZA"),
    os.environ.get("GEMINI_API_KEY_MARY")
]

ACTIVE_KEYS = deque([k for k in API_KEYS if k and k.strip()])

ICAO_DICT = {
    "ACFT": "Aircraft", "AD": "Aerodrome", "ALTN": "Alternate", "AMSL": "Above Mean Sea Level",
    "APCH": "Approach", "APP": "Approach Control", "ARR": "Arrival", "ATC": "Air Traffic Control",
    "AUTH": "Authorized", "AVBL": "Available", "AWY": "Airway", "BCN": "Beacon",
    "BFR": "Before", "BLW": "Below", "BTN": "Between", "CAT": "Category",
    "CLSD": "Closed", "COORD": "Coordinates", "CTC": "Contact", "CTR": "Control Zone",
    "DCT": "Direct", "DEG": "Degrees", "DEP": "Departure", "DLY": "Daily",
    "DTHR": "Displaced Threshold", "ELEV": "Elevation", "EST": "Estimated",
    "ETA": "Estimated Time of Arrival", "ETD": "Estimated Time of Departure",
    "EXC": "Except", "FCST": "Forecast", "FIR": "Flight Information Region",
    "FL": "Flight Level", "FLT": "Flight", "FLW": "Following", "FM": "From",
    "FREQ": "Frequency", "GND": "Ground", "HEL": "Helicopter", "HR": "Hours",
    "IAP": "Instrument Approach Procedure", "ICAO": "International Civil Aviation Organization",
    "IFR": "Instrument Flight Rules", "INFO": "Information", "INOP": "Inoperative",
    "INT": "Intersection", "LDG": "Landing", "LLZ": "Localizer", "LOC": "Localizer",
    "LVL": "Level", "MAINT": "Maintenance", "MAX": "Maximum", "MIN": "Minimum",
    "MNM": "Minimum", "NAVD": "Navigation Device", "NM": "Nautical Miles",
    "NOTAM": "Notice to Airmen", "OBSC": "Obscured", "OBST": "Obstacle",
    "OP": "Operation", "OPR": "Operating", "OPS": "Operations", "OVR": "Over",
    "PAPI": "Precision Approach Path Indicator", "RDO": "Radio", "REF": "Reference",
    "REQ": "Required", "RTF": "Radiotelephone", "RWY": "Runway", "SFC": "Surface",
    "SID": "Standard Instrument Departure", "SR": "Sunrise", "SS": "Sunset",
    "STAR": "Standard Terminal Arrival", "SVC": "Service", "TEMPO": "Temporary",
    "TFC": "Traffic", "TKOF": "Takeoff", "TWR": "Tower", "TWY": "Taxiway",
    "U/S": "Unserviceable", "UNL": "Unlimited", "VFR": "Visual Flight Rules",
    "VIP": "Very Important Person", "VOR": "VHF Omnidirectional Radio Range",
    "WI": "Within", "WIP": "Work In Progress", "WX": "Weather"
}

tehran_tz = timezone(timedelta(hours=3, minutes=30))

def parse_and_convert_time(time_str):
    if len(time_str) >= 10 and time_str[:10].isdigit():
        y = int("20" + time_str[0:2])
        m = int(time_str[2:4])
        d = int(time_str[4:6])
        h = int(time_str[6:8])
        minute = int(time_str[8:10])
        try:
            dt_utc = datetime(y, m, d, h, minute, tzinfo=timezone.utc)
            dt_teh = dt_utc.astimezone(tehran_tz)
            return dt_utc, dt_teh
        except ValueError:
            return None, None
    return None, None

def get_relative_string(dt_utc):
    now = datetime.now(timezone.utc)
    diff = dt_utc - now
    secs = diff.total_seconds()
    future = secs > 0
    secs = abs(secs)
    days = int(secs // 86400)
    hours = int((secs % 86400) // 3600)
    mins = int((secs % 3600) // 60)
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if mins > 0 or len(parts) == 0: parts.append(f"{mins}m")
    time_string = " ".join(parts)
    return f"in {time_string}" if future else f"{time_string} ago"

def translate_e_section(text):
    e_section = text
    if "E)" in text:
        parts = text.split("E)")
        raw_e = parts[1]
        if "F)" in raw_e: raw_e = raw_e.split("F)")[0]
        elif "G)" in raw_e: raw_e = raw_e.split("G)")[0]
        e_section = raw_e.strip()
    for abbr, full in ICAO_DICT.items():
        e_section = re.sub(rf'\b{abbr}\b', full, e_section)
    return e_section

def load_json(filepath, default_value):
    if not os.path.exists(filepath): return default_value
    with open(filepath, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return default_value

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def clean_iran_name(text):
    if not text: return text
    cleaned = re.sub(r'islamic\s+republic\s+of\s+iran', 'Iran', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'iran\s+\(islamic\s+republic\s+of\)', 'Iran', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'islamic\s+republic\s+iran', 'Iran', cleaned, flags=re.IGNORECASE)
    return cleaned

def update_faa_registry():
    now = time.time()
    airlines_data = load_json(ICAO_AIRLINES_FILE, {})
    last_check = airlines_data.get("_last_check", 0)
    if now - last_check < 86400 and len(airlines_data) > 1:
        return airlines_data
    print("Updating FAA ICAO Airline registry no AI calls here...")
    headers = {"User-Agent": "Mozilla/5.0"}
    url = "https://www.faa.gov/air_traffic/publications/atpubs/cnt_html/chap3_section_3.html"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        html_content = resp.text
        rows = re.split(r'<tr[^>]*>', html_content, flags=re.IGNORECASE)[1:]
        for row in rows:
            cols = re.findall(r'<td[^>]*>(.*?)</td>', row, flags=re.IGNORECASE | re.DOTALL)
            if len(cols) >= 3:
                code = re.sub(r'<[^>]+>', '', cols[0]).strip()
                company = re.sub(r'<[^>]+>', '', cols[1]).strip()
                country = re.sub(r'<[^>]+>', '', cols[2]).strip().title()
                country = clean_iran_name(country)
                if len(code) == 3 and code.isalpha():
                    if code not in airlines_data:
                        airlines_data[code] = {"formal_name": company, "country": country, "common_name": ""}
                    else:
                        airlines_data[code]["formal_name"] = company
                        airlines_data[code]["country"] = country
    except Exception as e:
        print(f"FAA Registry fetch failed: {e}")
    airlines_data["_last_check"] = time.time()
    save_json(ICAO_AIRLINES_FILE, airlines_data)
    return airlines_data

def translate_active_airlines(active_codes, airlines_data):
    global ACTIVE_KEYS
    to_translate = {}
    for code in active_codes:
        info = airlines_data.get(code)
        if info and not info.get("common_name"):
            to_translate[code] = info["formal_name"]
    if not to_translate or not ACTIVE_KEYS: return airlines_data
    print(f"On Demand AI Translation: Checking {len(to_translate)} active airlines...")
    prompt = "Here is a JSON dictionary of airline ICAO codes and their formal registered names. Return a JSON dictionary mapping the exact same ICAO codes to their most common everyday spoken airline name. For example 'IRAN NATIONAL AIRLINES CORP. (IRAN AIR)' becomes 'Iran Air'. Return ONLY valid JSON and nothing else.\n\n" + json.dumps(to_translate)
    data = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}}
    keys_tried = 0
    initial_key_count = len(ACTIVE_KEYS)
    success = False
    while keys_tried < initial_key_count and ACTIVE_KEYS and not success:
        current_key = ACTIVE_KEYS.popleft()
        ACTIVE_KEYS.append(current_key)
        keys_tried += 1
        try:
            ai_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={current_key}"
            resp = requests.post(ai_url, headers={'Content-Type': 'application/json'}, json=data, timeout=20)
            if resp.status_code == 429:
                ACTIVE_KEYS.pop()
                continue
            resp.raise_for_status()
            text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            if text.startswith("```json"): text = text[7:-3]
            elif text.startswith("```"): text = text[3:-3]
            common_names = json.loads(text.strip())
            for c, common in common_names.items():
                if c in airlines_data: airlines_data[c]["common_name"] = common
            success = True
            save_json(ICAO_AIRLINES_FILE, airlines_data)
            time.sleep(3)
        except Exception as e:
            if "429" in str(e): ACTIVE_KEYS.pop()
            continue
    return airlines_data

def get_ai_explanation(raw_text):
    global ACTIVE_KEYS
    if not ACTIVE_KEYS: return None
    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    headers = {'Content-Type': 'application/json'}
    prompt = f"""Read this aviation NOTAM:\n{raw_text}\nTask 1: Explain the NOTAM in very simple words for a general audience.\nTask 2: Assign the highest category. Choices are ONLY these exact strings:\n'First Level' (for United States international warnings, complete airspace closure, or major security events)\n'Second Level' (for military exercises, gun fire, or restricted airspace)\n'Third Level' (for routine aviation changes)\nReturn ONLY a valid JSON dictionary. No markdown, no code blocks. It must have exactly two keys: "explanation" and "highest_level"."""
    data = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json", "temperature": 0.2}}
    keys_tried = 0
    initial_key_count = len(ACTIVE_KEYS)
    while keys_tried < initial_key_count and ACTIVE_KEYS:
        current_key = ACTIVE_KEYS.popleft()
        ACTIVE_KEYS.append(current_key)
        keys_tried += 1
        key_failed_429 = False
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={current_key}"
            try:
                response = requests.post(url, headers=headers, json=data, timeout=30)
                if response.status_code == 404: continue
                if response.status_code == 429:
                    key_failed_429 = True
                    break
                response.raise_for_status()
                res_json = response.json()
                text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                if text.startswith("```json"): text = text[7:-3]
                elif text.startswith("```"): text = text[3:-3]
                time.sleep(3)
                return json.loads(text.strip())
            except Exception as e:
                if "429" in str(e): key_failed_429 = True
                break
        if key_failed_429: ACTIVE_KEYS.pop()
    time.sleep(3)
    return None

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

def decode_notam(raw_text):
    fd_in, temp_path_in = tempfile.mkstemp(text=True)
    fd_out, temp_path_out = tempfile.mkstemp(text=True)
    os.close(fd_in)
    os.close(fd_out)
    try:
        with open(temp_path_in, 'w', encoding='utf-8') as f: f.write(raw_text)
        js_code = """const fs = require('fs'); try { const notamDecoder = require('./notam-decoder.js'); const raw = fs.readFileSync(process.argv[1], 'utf8'); const decoded = notamDecoder.decode(raw); fs.writeFileSync(process.argv[2], JSON.stringify(decoded || {error: "Empty result"}), 'utf8'); } catch(e) { fs.writeFileSync(process.argv[2], JSON.stringify({error: e.toString()}), 'utf8'); }"""
        process = subprocess.run(['node', '-e', js_code, temp_path_in, temp_path_out], capture_output=True, text=True)
        with open(temp_path_out, 'r', encoding='utf-8') as f: output_data = f.read()
        if not output_data.strip(): return {"error": "Empty output"}
        return json.loads(output_data)
    except Exception as e:
        return {"error": f"PYTHON CRASH: {str(e)}"}
    finally:
        if os.path.exists(temp_path_in): os.remove(temp_path_in)
        if os.path.exists(temp_path_out): os.remove(temp_path_out)

def get_all_notams():
    headers = {"User-Agent": "Mozilla/5.0"}
    all_notams = []
    targets = ["OIIX", "KICZ"]
    for target in targets:
        offset = 0
        batch_size = 30
        while True:
            payload = {"searchType": 0, "designatorsForLocation": target, "offset": offset, "notamsOnly": False, "radius": 10}
            try:
                response = requests.post(URL, data=payload, headers=headers, timeout=30)
                response.raise_for_status()
                data = response.json()
                if not data or "notamList" not in data: break
                current_batch = data["notamList"]
                if not current_batch: break
                if target == "KICZ":
                    for n in current_batch:
                        msg_text = (n.get("icaoMessage") or "").upper()
                        if "IRAN" in msg_text or "OIIX" in msg_text or "TEHRAN" in msg_text: all_notams.append(n)
                else:
                    all_notams.extend(current_batch)
                offset += len(current_batch)
                if len(current_batch) < batch_size: break
                time.sleep(1)
            except Exception as e:
                break
    return all_notams

def extract_notam_details(raw_text, decoded_obj, notam_id):
    subject_text = "Unknown Subject"
    condition_text = "Unknown Condition"
    notam_type = "New NOTAM"
    traffic_list = "Unknown"
    map_links = []
    valid_from_str = "Unknown"
    valid_to_str = "Unknown"
    
    b_match = re.search(r'B\)\s*(\d{10})', raw_text)
    c_match = re.search(r'C\)\s*(\d{10}|PERM)(.*?)(\n|D\)|E\)|F\)|G\))', raw_text)
    if b_match:
        dt_utc, dt_teh = parse_and_convert_time(b_match.group(1))
        if dt_utc:
            rel = get_relative_string(dt_utc)
            status = "Started" if (dt_utc < datetime.now(timezone.utc)) else "Starts in"
            valid_from_str = f"{dt_teh.strftime('%Y/%m/%d %H:%M')} Tehran Time ({status if 'in' in rel else 'Started'} {rel.replace('in ', '')})"
    if c_match:
        val_c = c_match.group(1)
        if val_c == "PERM": valid_to_str = "Permanent"
        else:
            dt_utc, dt_teh = parse_and_convert_time(val_c)
            if dt_utc:
                rel = get_relative_string(dt_utc)
                status = "Expired" if (dt_utc < datetime.now(timezone.utc)) else "Expires in"
                est_tag = " (Estimated)" if "EST" in c_match.group(2) else ""
                valid_to_str = f"{dt_teh.strftime('%Y/%m/%d %H:%M')} Tehran Time ({status if 'in' in rel else 'Expired'} {rel.replace('in ', '')}){est_tag}"

    if decoded_obj and "qualification" in decoded_obj:
        header = decoded_obj.get("header", {})
        if isinstance(header, dict): notam_type = header.get("typeDesc", notam_type)
        qual_block = decoded_obj.get("qualification", {})
        if isinstance(qual_block, dict):
            t_data = qual_block.get("traffic", [])
            if isinstance(t_data, list) and len(t_data) > 0:
                traffic_list = ", ".join([t.get("description", "") for t in t_data if isinstance(t, dict)])
            code_block = qual_block.get("code", {})
            if isinstance(code_block, dict):
                subject_text = code_block.get("subject", subject_text)
                condition_text = code_block.get("modifier", condition_text)
            coords = qual_block.get("coordinates")
            content_block = decoded_obj.get("content", {})
            area = content_block.get("area")
            has_map = False
            if area and isinstance(area, list) and len(area) > 2: has_map = True
            elif coords and isinstance(coords, list) and len(coords) == 2 and isinstance(coords[0], list): has_map = True
            elif coords and isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)): has_map = True
            if has_map: map_links.append(f"🗺️ [Click to View Area on Map](https://raw.githack.com/freddishio/oiix-notam-watcher/main/index.html#{notam_id})")

    subject_text = re.sub(r'\s*\(.*?\)', '', subject_text).strip()
    condition_text = re.sub(r'\s*\(.*?\)', '', condition_text).strip()
    return notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links

def fetch_iran_planes():
    print("Fetching active aircraft locations over 16 high resolution quadrants from Flightradar24...")
    bounds_list = [
        "37.94,45.71/8", "37.94,50.71/8", "37.94,55.71/8", "37.94,60.71/8",
        "34.34,45.71/8", "34.34,50.71/8", "34.34,55.71/8", "34.34,60.71/8",
        "30.74,45.71/8", "30.74,50.71/8", "30.74,55.71/8", "30.74,60.71/8",
        "27.14,45.71/8", "27.14,50.71/8", "27.14,55.71/8", "27.14,60.71/8"
    ]
    api_bounds = []
    for coord_str in bounds_list:
        parts = coord_str.replace("/8","").split(",")
        lat = float(parts[0])
        lon = float(parts[1])
        api_bounds.append(f"{lat+2.5:.2f},{lat-2.5:.2f},{lon-3.2:.2f},{lon+3.2:.2f}")
    
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.flightradar24.com/"}
    all_fr24_data = {}
    for b in api_bounds:
        fr24_url = f"https://data-cloud.flightradar24.com/zones/fcgi/feed.js?bounds={b}&estimated=1"
        try:
            response = requests.get(fr24_url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            for key, value in data.items():
                if key not in ["full_count", "version", "stats"]: all_fr24_data[key] = value
        except Exception as e:
            pass

    geo_url = "https://raw.githubusercontent.com/vatsimnetwork/vatspy-data-project/master/Boundaries.geojson"
    oiix_polygon = None
    try:
        geo_resp = requests.get(geo_url, timeout=15)
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
        for feature in geo_data.get("features", []):
            props = feature.get("properties", {})
            if props.get("id") == "OIIX" or "Tehran" in props.get("FIRname", ""):
                oiix_polygon = shape(feature["geometry"])
                break
    except: pass
    if not oiix_polygon: return []

    icao_airlines = update_faa_registry()
    temp_planes = []
    active_codes = set()
    
    for key, value in all_fr24_data.items():
        if isinstance(value, list) and len(value) > 2:
            lat = float(value[1])
            lon = float(value[2])
            track = value[3] if len(value) > 3 else "N/A"
            alt = value[4] if len(value) > 4 else "N/A"
            speed = value[5] if len(value) > 5 else "N/A"
            ac_type = value[8] if len(value) > 8 and value[8] else "Unknown Type"
            reg = value[9] if len(value) > 9 and value[9] else "Unknown Reg"
            flight_id = value[13] if len(value) > 13 and value[13] else "Unknown Flight"
            callsign = value[16] if len(value) > 16 and value[16] else "Unknown Callsign"
            
            plane_point = Point(lon, lat)
            if oiix_polygon.contains(plane_point):
                code = callsign[:3] if callsign != "Unknown Callsign" else ""
                if len(code) == 3 and code.isalpha(): active_codes.add(code)
                temp_planes.append({
                    "id": key, "callsign": callsign, "code": code, "flight": flight_id, "type": ac_type, "reg": reg,
                    "category": "Commercial", "alt": alt, "track": track, "speed": speed, "lat": lat, "lon": lon
                })
                
    icao_airlines = translate_active_airlines(list(active_codes), icao_airlines)
    
    iran_planes = []
    for p in temp_planes:
        code = p.pop("code")
        info = icao_airlines.get(code, {})
        common = info.get("common_name")
        formal = info.get("formal_name", code)
        airline_name = common if common else formal
        if not airline_name: airline_name = "Unknown Airline"
        airline_country = info.get("country", "Unknown Location")
        airline_country = clean_iran_name(airline_country)
        p["airline"] = airline_name
        p["country"] = airline_country
        iran_planes.append(p)
    return iran_planes

def generate_planes_html(history_rendered):
    json_data_string = json.dumps(history_rendered)
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Active Aircraft Over Iran Dashboard</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;800&family=Roboto+Mono:wght@500;700&display=swap" rel="stylesheet">
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        :root {{ --primary: #ffcc00; --bg-glass: rgba(15, 18, 25, 0.85); --bg-solid: #0f1219; --border: rgba(255, 255, 255, 0.15); --text-main: #f8f9fa; --text-muted: #9aa5b1; }}
        body {{ padding: 0; margin: 0; font-family: 'Inter', sans-serif; overflow: hidden; background: #000; }}
        #map {{ height: 100vh; width: 100vw; z-index: 1; }}
        
        .glass-panel {{ background: var(--bg-glass); backdrop-filter: blur(25px); -webkit-backdrop-filter: blur(25px); border: 1px solid var(--border); box-shadow: 0 16px 40px rgba(0, 0, 0, 0.8); color: var(--text-main); }}

        #loading {{ position: fixed; inset: 0; background: rgba(0, 0, 0, 0.85); backdrop-filter: blur(10px); color: var(--primary); display: flex; align-items: center; justify-content: center; font-size: 24px; font-weight: 800; letter-spacing: 1px; z-index: 9999; transition: opacity 0.3s ease; }}
        
        #top-banner {{ position: absolute; top: 20px; left: 50%; transform: translateX(-50%); padding: 12px 28px; border-radius: 30px; z-index: 1000; white-space: nowrap; display: flex; align-items: center; gap: 15px; box-shadow: 0 8px 30px rgba(0,0,0,0.6); }}
        #top-banner span.indicator {{ display: inline-block; width: 10px; height: 10px; background: #00ff00; border-radius: 50%; box-shadow: 0 0 12px #00ff00; }}
        
        .time-display-group {{ display: flex; flex-direction: column; align-items: center; gap: 4px; }}
        #banner-time-tehran {{ font-size: 15px; font-weight: 800; color: #fff; }}
        #banner-time-local {{ font-size: 12px; font-weight: 600; color: var(--primary); }}

        #sidebar-wrapper {{ position: absolute; top: 80px; left: 0; z-index: 1000; display: flex; align-items: flex-start; transition: transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1); transform: translateX(-360px); height: calc(100vh - 140px); pointer-events: none; }}
        #sidebar-content {{ width: 360px; height: 100%; border-radius: 0 16px 16px 0; padding: 24px; overflow-y: auto; box-sizing: border-box; flex-shrink: 0; pointer-events: auto; }}
        #sidebar-content::-webkit-scrollbar {{ width: 6px; }} #sidebar-content::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.2); border-radius: 4px; }}
        
        #sidebar-toggle {{ width: 44px; height: 64px; margin-top: 20px; background: var(--bg-glass); backdrop-filter: blur(20px); border: 1px solid var(--border); border-left: none; border-radius: 0 12px 12px 0; color: var(--primary); cursor: pointer; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 18px; box-shadow: 6px 0 20px rgba(0,0,0,0.5); user-select: none; flex-shrink: 0; pointer-events: auto; }}
        .expanded {{ transform: translateX(0) !important; }}

        .panel-title {{ margin: 0 0 20px 0; font-size: 22px; font-weight: 800; border-bottom: 1px solid var(--border); padding-bottom: 12px; letter-spacing: -0.5px; }}
        .stat-box {{ background: rgba(0,0,0,0.5); padding: 18px; border-radius: 12px; margin-bottom: 20px; text-align: center; border: 1px solid rgba(255,255,255,0.06); }}
        .stat-num {{ font-size: 42px; font-weight: 800; color: var(--primary); font-family: 'Roboto Mono', monospace; line-height: 1; margin-top: 10px; text-shadow: 0 2px 10px rgba(255,204,0,0.3); }}
        .section-title {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1.5px; margin: 25px 0 12px 0; font-weight: 800; }}
        
        #clear-filter-btn {{ display: none; width: 100%; background: #ff4444; color: white; border: none; padding: 12px; border-radius: 8px; font-weight: 800; cursor: pointer; margin-bottom: 15px; font-family: 'Inter', sans-serif; transition: 0.2s; box-shadow: 0 4px 15px rgba(255,68,68,0.3); }}
        #clear-filter-btn:hover {{ background: #ff2222; transform: translateY(-2px); }}

        .data-list {{ list-style: none; padding: 0; margin: 0; }}
        .data-list li {{ padding: 10px 14px; margin-bottom: 6px; border: 1px solid transparent; border-radius: 8px; display: flex; align-items: center; justify-content: space-between; font-size: 14px; font-weight: 600; color: #e2e8f0; cursor: pointer; transition: all 0.2s; background: rgba(255,255,255,0.03); }}
        .data-list li:hover {{ background: rgba(255,204,0,0.1); border-color: rgba(255,204,0,0.3); }}
        .data-list li.active-filter {{ background: rgba(255,204,0,0.2); border-color: var(--primary); box-shadow: 0 0 10px rgba(255,204,0,0.2); }}
        .data-list li img {{ filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5)); }}
        .flex-left {{ display: flex; align-items: center; gap: 10px; }}
        .count-badge {{ background: rgba(0,0,0,0.6); border: 1px solid var(--border); padding: 4px 10px; border-radius: 12px; font-family: 'Roboto Mono', monospace; color: var(--primary); font-weight: 800; font-size: 13px; }}

        #time-shift-btn {{ position: absolute; bottom: 40px; left: 50%; transform: translateX(-50%); padding: 16px 36px; border-radius: 40px; cursor: pointer; font-size: 16px; font-weight: 800; z-index: 1000; border: 2px solid var(--primary); color: white; transition: all 0.2s ease; letter-spacing: 0.5px; box-shadow: 0 8px 25px rgba(0,0,0,0.6); }}
        #time-shift-btn:hover {{ background: var(--primary); color: #000; box-shadow: 0 0 25px rgba(255,204,0,0.5); }}

        #time-dock {{ display: none; position: absolute; bottom: 30px; left: 50%; transform: translateX(-50%); width: 95%; max-width: 800px; border-radius: 20px; padding: 25px 35px; box-sizing: border-box; z-index: 1100; }}
        .dock-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; position: relative; }}
        .dock-header h3 {{ margin: 0; font-size: 18px; font-weight: 800; color: var(--primary); width: 100%; text-align: center; text-transform: uppercase; letter-spacing: 1px; }}
        .close-btn {{ position: absolute; right: 0; background: none; border: none; color: var(--text-muted); font-size: 28px; cursor: pointer; padding: 0; line-height: 1; transition: color 0.2s; }} .close-btn:hover {{ color: white; }}
        
        .time-grid {{ display: grid; grid-template-columns: auto 1fr auto; gap: 20px; align-items: center; background: rgba(0,0,0,0.3); padding: 18px; border-radius: 15px; margin-bottom: 25px; border: 1px solid rgba(255,255,255,0.05); }}
        .tz-toggle {{ display: flex; background: rgba(0,0,0,0.6); border-radius: 10px; overflow: hidden; border: 1px solid var(--border); }}
        .tz-toggle button {{ padding: 12px 18px; border: none; background: transparent; color: var(--text-muted); cursor: pointer; font-weight: 800; font-size: 13px; transition: 0.3s; font-family: 'Inter', sans-serif; }}
        .tz-toggle button.active {{ background: var(--primary); color: #000; }}
        
        .time-inputs {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; justify-content: center; }}
        .dock-controls-select {{ background: rgba(0,0,0,0.6); color: white; border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; font-size: 14px; font-family: 'Inter', sans-serif; font-weight: 700; outline: none; cursor: pointer; transition: 0.2s; }}
        .dock-controls-select:hover {{ border-color: var(--primary); }}
        .custom-input {{ background: rgba(0,0,0,0.6); color: var(--primary); border: 1px solid var(--border); border-radius: 10px; padding: 11px 16px; font-size: 14px; font-family: 'Roboto Mono', monospace; font-weight: 700; outline: none; cursor: pointer; }}
        .custom-input::-webkit-calendar-picker-indicator {{ filter: invert(1) sepia(100%) saturate(10000%) hue-rotate(10deg); cursor: pointer; }}
        
        .playback-controls {{ display: flex; gap: 10px; align-items: center; }}
        .control-btn {{ background: rgba(255,255,255,0.1); border: 1px solid var(--border); color: #fff; padding: 12px 20px; border-radius: 10px; cursor: pointer; font-weight: 800; transition: 0.2s; font-family: 'Inter', sans-serif; width: 100px; text-align: center; }}
        .control-btn:hover {{ background: rgba(255,255,255,0.2); }}
        .control-btn.playing {{ background: var(--primary); color: #000; border-color: var(--primary); box-shadow: 0 0 15px rgba(255,204,0,0.4); }}

        .slider-container {{ position: relative; width: 100%; margin-top: 10px; }}
        input[type=range] {{ width: 100%; height: 8px; background: rgba(255,255,255,0.2); border-radius: 4px; outline: none; -webkit-appearance: none; accent-color: var(--primary); cursor: pointer; }}
        input[type=range]::-webkit-slider-thumb {{ -webkit-appearance: none; width: 24px; height: 24px; background: var(--primary); border-radius: 50%; cursor: pointer; box-shadow: 0 0 15px rgba(255,204,0,0.8); transition: transform 0.1s; }}
        input[type=range]::-webkit-slider-thumb:hover {{ transform: scale(1.15); }}
        
        #slider-tooltip {{ position: absolute; top: -50px; background: var(--primary); color: #000; padding: 8px 16px; border-radius: 8px; font-size: 14px; font-weight: 800; transform: translateX(-50%); pointer-events: none; display: none; white-space: nowrap; box-shadow: 0 8px 20px rgba(0,0,0,0.5); font-family: 'Roboto Mono', monospace; z-index: 10; }}
        #slider-tooltip::after {{ content: ''; position: absolute; bottom: -6px; left: 50%; transform: translateX(-50%); border-width: 6px 6px 0; border-style: solid; border-color: var(--primary) transparent transparent transparent; }}

        .leaflet-tooltip.plane-tooltip {{ background: var(--bg-solid) !important; border: 1px solid var(--border) !important; color: var(--primary) !important; font-family: 'Roboto Mono', monospace; font-weight: 800; font-size: 14px; border-radius: 8px; padding: 6px 10px; box-shadow: 0 6px 15px rgba(0,0,0,0.6); }}
        .leaflet-tooltip-top:before {{ border-top-color: var(--border) !important; bottom: -6px !important; border-width: 6px 6px 0 !important; }}
        
        .leaflet-popup-content-wrapper {{ background: var(--bg-solid); color: var(--text-main); border: 1px solid var(--border); border-radius: 16px; padding: 0; overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.9); }}
        .leaflet-popup-tip {{ background: var(--bg-solid); width: 20px; height: 20px; margin: -10px auto 0; }}
        .leaflet-popup-content {{ margin: 0 !important; width: 320px !important; }}
        
        .popup-header {{ background: rgba(255, 204, 0, 0.08); padding: 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 16px; }}
        .popup-callsign {{ font-size: 24px; font-weight: 800; font-family: 'Roboto Mono', monospace; color: var(--primary); letter-spacing: 1px; line-height: 1.2; }}
        .popup-airline {{ font-size: 14px; color: var(--text-muted); font-weight: 600; margin-top: 6px; }}
        .popup-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; padding: 24px; }}
        .popup-stat {{ display: flex; flex-direction: column; }} .popup-stat label {{ font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 800; margin-bottom: 6px; letter-spacing: 0.5px; }} .popup-stat span {{ font-size: 16px; font-weight: 700; font-family: 'Roboto Mono', monospace; color: #fff; }}
        
        .leaflet-control-zoom {{ border: none !important; margin-right: 20px !important; margin-bottom: 20px !important; box-shadow: 0 8px 20px rgba(0,0,0,0.6) !important; }} .leaflet-control-zoom a {{ background: var(--bg-glass) !important; color: var(--primary) !important; border: 1px solid var(--border) !important; font-weight: 800 !important; backdrop-filter: blur(10px); }} .leaflet-control-zoom a:hover {{ background: var(--primary) !important; color: #000 !important; }}

        @media (max-width: 900px) {{
            .time-grid {{ grid-template-columns: 1fr; gap: 15px; justify-items: center; }}
        }}
        @media (max-width: 768px) {{
            #top-banner {{ padding: 10px 18px; top: 10px; width: 85%; justify-content: center; }}
            #sidebar-wrapper {{ transform: translateX(-320px); }}
            #sidebar-content {{ width: 320px; padding: 18px; }}
            .expanded {{ transform: translateX(0) !important; }}
            #time-dock {{ width: 95%; padding: 20px; }}
            .time-inputs {{ flex-direction: column; width: 100%; }} .dock-controls-select, .custom-input {{ width: 100%; text-align: center; }}
            #time-shift-btn {{ bottom: 20px; width: 60%; }}
        }}
    </style>
</head>
<body>
    <div id="loading">Syncing Radar Data...</div>
    <div id="top-banner" class="glass-panel">
        <span class="indicator"></span>
        <div class="time-display-group">
            <span id="banner-time-tehran">Snapshot Time...</span>
            <span id="banner-time-local"></span>
        </div>
    </div>
    
    <div id="sidebar-wrapper">
        <div id="sidebar-content" class="glass-panel">
            <h3 class="panel-title">Airspace Analytics</h3>
            <div class="stat-box"><div style="font-size: 12px; font-weight: 800; color: #a0aab2; text-transform: uppercase; letter-spacing: 1px;">Active Commercial Traffic</div><div class="stat-num" id="plane-count">0</div></div>
            
            <button id="clear-filter-btn">Clear Filter</button>

            <div class="section-title">Registration Countries</div>
            <ul id="country-list" class="data-list"></ul>
            <div class="section-title">Operating Airlines</div>
            <ul id="airline-list" class="data-list"></ul>
        </div>
        <div id="sidebar-toggle">>></div>
    </div>

    <button id="time-shift-btn" class="glass-panel">🕰️ Time Shift</button>

    <div id="time-dock" class="glass-panel">
        <div class="dock-header">
            <h3>Time Shift Controls</h3>
            <button class="close-btn" id="close-dock-btn">&times;</button>
        </div>
        
        <div class="time-grid">
            <div class="tz-toggle">
                <button id="btn-tz-tehran" class="active">Tehran Time</button>
                <button id="btn-tz-local">Local Time</button>
            </div>
            
            <div class="time-inputs">
                <select id="hourSelect" class="dock-controls-select"></select>
                <select id="minuteSelect" class="dock-controls-select"></select>
                <span style="color:var(--text-muted); font-weight:800; font-size:12px; margin: 0 10px;">OR</span>
                <input type="datetime-local" id="exactTimePicker" class="custom-input">
            </div>

            <div class="playback-controls">
                <button id="playBtn" class="control-btn">▶ Play</button>
                <select id="speedSelect" class="dock-controls-select">
                    <option value="0.5">0.5x</option>
                    <option value="1" selected>1.0x</option>
                    <option value="1.5">1.5x</option>
                    <option value="2">2.0x</option>
                    <option value="3">3.0x</option>
                    <option value="4">4.0x</option>
                </select>
            </div>
        </div>

        <div class="slider-container">
            <div id="slider-tooltip"></div>
            <input type="range" id="modalSlider" min="0" max="0" value="0">
        </div>
    </div>

    <div id="map"></div>

    <script>
        const flightHistory = {json_data_string};
        var map = L.map('map', {{ center: [32.4279, 53.6880], zoom: 6, zoomSnap: 0.5, zoomControl: false }});
        L.control.zoom({{ position: 'bottomright' }}).addTo(map);
        map.createPane('firPane'); map.getPane('firPane').style.zIndex = 390; 
        
        var CartoDB_DarkMatter = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{ attribution: '&copy; OpenStreetMap', subdomains: 'abcd', maxZoom: 20 }}).addTo(map);
        var googleStreets = L.tileLayer('https://{{s}}.google.com/vt/lyrs=m&x={{x}}&y={{y}}&z={{z}}',{{maxZoom: 20, subdomains:['mt0','mt1','mt2','mt3']}});
        var googleHybrid = L.tileLayer('https://{{s}}.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}',{{maxZoom: 20, subdomains:['mt0','mt1','mt2','mt3']}});
        var googleSat = L.tileLayer('https://{{s}}.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}',{{maxZoom: 20, subdomains:['mt0','mt1','mt2','mt3']}});
        
        L.control.layers({{ "Dark Tracker (Default)": CartoDB_DarkMatter, "Standard Map": googleStreets, "Hybrid (Sat + Borders)": googleHybrid, "Satellite": googleSat }}, {{}}).addTo(map);

        var firLayer = L.geoJSON(null, {{
            pane: 'firPane',
            style: function(feature) {{
                var p = feature.properties;
                var isIran = (p.id === "OIIX" || (p.FIRname && p.FIRname.indexOf("Tehran") !== -1));
                return {{ color: isIran ? "#0055ff" : "#00ff00", weight: 2, fillOpacity: isIran ? 0.15 : 0.0 }};
            }}
        }}).addTo(map);
        fetch('https://raw.githubusercontent.com/vatsimnetwork/vatspy-data-project/master/Boundaries.geojson').then(r => r.json()).then(d => firLayer.addData(d));

        var planeLayerGroup = L.layerGroup().addTo(map);
        
        const bannerTehran = document.getElementById("banner-time-tehran");
        const bannerLocal = document.getElementById("banner-time-local");
        const countDisplay = document.getElementById("plane-count");
        const airlineList = document.getElementById("airline-list");
        const countryList = document.getElementById("country-list");
        const loading = document.getElementById("loading");
        const clearFilterBtn = document.getElementById("clear-filter-btn");
        
        const sidebarWrapper = document.getElementById("sidebar-wrapper");
        const sidebarToggle = document.getElementById("sidebar-toggle");
        
        let activeFilter = null; 

        if (window.innerWidth >= 768) {{ sidebarWrapper.classList.add("expanded"); sidebarToggle.innerText = "<<"; }}
        sidebarToggle.onclick = function() {{
            sidebarWrapper.classList.toggle("expanded");
            sidebarToggle.innerText = sidebarWrapper.classList.contains("expanded") ? "<<" : ">>";
        }};
        
        const timeShiftBtn = document.getElementById("time-shift-btn");
        const timeDock = document.getElementById("time-dock");
        const closeDockBtn = document.getElementById("close-dock-btn");
        const hSelect = document.getElementById("hourSelect");
        const mSelect = document.getElementById("minuteSelect");
        const exactPicker = document.getElementById("exactTimePicker");
        const modalSlider = document.getElementById("modalSlider");
        const sliderTooltip = document.getElementById("slider-tooltip");
        const playBtn = document.getElementById("playBtn");
        const speedSelect = document.getElementById("speedSelect");
        
        const btnTzTehran = document.getElementById("btn-tz-tehran");
        const btnTzLocal = document.getElementById("btn-tz-local");
        let tzMode = "tehran";

        btnTzTehran.onclick = () => {{ tzMode = "tehran"; btnTzTehran.classList.add("active"); btnTzLocal.classList.remove("active"); }};
        btnTzLocal.onclick = () => {{ tzMode = "local"; btnTzLocal.classList.add("active"); btnTzTehran.classList.remove("active"); }};
        
        timeShiftBtn.onclick = function() {{ timeShiftBtn.style.display = "none"; timeDock.style.display = "block"; }}
        closeDockBtn.onclick = function() {{ timeDock.style.display = "none"; timeShiftBtn.style.display = "block"; }}
        
        function getTwemojiUrl(iso) {{
            if (!iso) return '';
            const codePoints = [...iso.toUpperCase()].map(c => (c.codePointAt(0) + 127397).toString(16));
            return `https://raw.githubusercontent.com/twitter/twemoji/master/assets/svg/${{codePoints.join('-')}}.svg`;
        }}

        const countriesMap = {{
            "united arab emirates": "AE", "qatar": "QA", "turkey": "TR", "saudi arabia": "SA", "kuwait": "KW", "oman": "OM",
            "bahrain": "BH", "iraq": "IQ", "pakistan": "PK", "india": "IN", "afghanistan": "AF", "germany": "DE",
            "france": "FR", "united kingdom": "GB", "russia": "RU", "china": "CN", "united states": "US", "switzerland": "CH",
            "netherlands": "NL", "italy": "IT", "spain": "ES", "egypt": "EG", "jordan": "JO", "lebanon": "LB", "syria": "SY",
            "belgium": "BE", "austria": "AT", "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI", "poland": "PL",
            "greece": "GR", "ireland": "IE", "portugal": "PT", "canada": "CA", "australia": "AU", "japan": "JP", "south korea": "KR",
            "singapore": "SG", "malaysia": "MY", "indonesia": "ID", "thailand": "TH", "vietnam": "VN", "philippines": "PH",
            "azerbaijan": "AZ", "armenia": "AM", "georgia": "GE", "kazakhstan": "KZ", "uzbekistan": "UZ", "turkmenistan": "TM",
            "tajikistan": "TM", "kyrgyzstan": "KG", "sri lanka": "LK", "bangladesh": "LK", "nepal": "NP", "maldives": "MV",
            "yemen": "YE", "israel": "IL", "cyprus": "IL", "morocco": "MA", "algeria": "MA", "tunisia": "TN", "libya": "LY",
            "sudan": "SD", "ethiopia": "SD", "kenya": "KE", "somalia": "SO", "djibouti": "SO", "eritrea": "DJ", "rwanda": "ER"
        }};

        function getFlagHTML(country) {{
            const c = country.toLowerCase();
            let iso = countriesMap[c];
            if (c === "iran" || c.includes("iran")) iso = "IR";
            if(iso) return `<img src="${{getTwemojiUrl(iso)}}" style="width:26px; height:auto; vertical-align:middle; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));">`;
            return `<span style="font-size:22px; vertical-align:middle; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));">🏳️</span>`;
        }}

        function setFilter(type, value) {{
            activeFilter = {{ type, value }};
            renderPlanes(modalSlider.value);
        }}

        clearFilterBtn.onclick = function() {{
            activeFilter = null;
            renderPlanes(modalSlider.value);
        }};

        function renderPlanes(index) {{
            planeLayerGroup.clearLayers();
            const record = flightHistory[index];
            
            const dateUTC = new Date(record.timestamp * 1000);
            const opts = {{ year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }};
            const localTimeStr = dateUTC.toLocaleString('en-US', opts);
            const tehranTimeStr = dateUTC.toLocaleString('en-US', {{ ...opts, timeZone: 'Asia/Tehran' }});

            bannerTehran.innerText = tehranTimeStr + " Tehran Time";
            bannerLocal.innerText = localTimeStr + " Local Time";
            
            const airlines = {{}}; const countries = {{}};
            let filteredCount = 0;
            
            record.planes.forEach(plane => {{
                airlines[plane.airline] = (airlines[plane.airline] || 0) + 1;
                countries[plane.country] = (countries[plane.country] || 0) + 1;
                
                if (activeFilter) {{
                    if (activeFilter.type === 'country' && plane.country !== activeFilter.value) return;
                    if (activeFilter.type === 'airline' && plane.airline !== activeFilter.value) return;
                }}
                
                filteredCount++;
                
                let popupHTML = `<div class="fr24-popup"><div class="popup-header">${{getFlagHTML(plane.country)}}<div><div class="popup-callsign">${{plane.callsign}}</div><div class="popup-airline">${{plane.airline}}</div></div></div><div class="popup-grid"><div class="popup-stat"><label>Flight</label><span>${{plane.flight}}</span></div><div class="popup-stat"><label>Reg</label><span>${{plane.reg}}</span></div><div class="popup-stat"><label>Type</label><span>${{plane.type}}</span></div><div class="popup-stat"><label>Category</label><span>${{plane.category}}</span></div><div class="popup-stat"><label>Altitude</label><span>${{plane.alt}} ft</span></div><div class="popup-stat"><label>Speed</label><span>${{plane.speed}} kts</span></div><div class="popup-stat"><label>Track</label><span>${{plane.track}}°</span></div></div></div>`;
                let marker = L.circleMarker([plane.lat, plane.lon], {{ color: '#ffcc00', radius: 6, weight: 2, fillOpacity: 0.8 }});
                marker.bindPopup(popupHTML, {{minWidth: 320, maxWidth: 320, className: 'custom-popup-wrapper'}});
                marker.bindTooltip(plane.callsign, {{direction: 'top', className: 'plane-tooltip', offset: [0, -10]}});
                marker.addTo(planeLayerGroup);
            }});
            
            if (activeFilter) {{
                countDisplay.innerText = filteredCount + " / " + record.count;
                clearFilterBtn.style.display = 'block';
                clearFilterBtn.innerText = "✖ Clear Filter (" + activeFilter.value + ")";
            }} else {{
                countDisplay.innerText = record.count;
                clearFilterBtn.style.display = 'none';
            }}
            
            airlineList.innerHTML = "";
            Object.entries(airlines).sort((a,b) => b[1] - a[1]).forEach(([airline, count]) => {{
                if(airline && airline !== "Unknown Airline") {{
                    let li = document.createElement("li");
                    if (activeFilter && activeFilter.type === 'airline' && activeFilter.value === airline) li.classList.add('active-filter');
                    li.innerHTML = `<div class="flex-left"><span>${{airline}}</span></div><div class="count-badge">${{count}}</div>`;
                    li.onclick = () => setFilter('airline', airline);
                    airlineList.appendChild(li);
                }}
            }});
            
            countryList.innerHTML = "";
            Object.entries(countries).sort((a,b) => b[1] - a[1]).forEach(([country, count]) => {{
                if(country && country !== "Unknown Location") {{
                    let li = document.createElement("li");
                    if (activeFilter && activeFilter.type === 'country' && activeFilter.value === country) li.classList.add('active-filter');
                    li.innerHTML = `<div class="flex-left">${{getFlagHTML(country)}} <span>${{country}}</span></div><div class="count-badge">${{count}}</div>`;
                    li.onclick = () => setFilter('country', country);
                    countryList.appendChild(li);
                }}
            }});
        }}

        let playInterval;
        let isPlaying = false;

        function startPlayback() {{
            if(playInterval) clearInterval(playInterval);
            let speed = parseFloat(speedSelect.value);
            let delay = 1000 / speed;
            
            playInterval = setInterval(() => {{
                let val = parseInt(modalSlider.value) + 1;
                if(val > parseInt(modalSlider.max)) val = parseInt(modalSlider.min);
                modalSlider.value = val;
                syncSelectsToIndex(val);
                renderPlanes(val);
                
                let percent = modalSlider.max > 0 ? (val / modalSlider.max) * 100 : 0;
                sliderTooltip.style.left = `calc(${{percent}}% + (${{10 - percent * 0.2}}px))`;
            }}, delay);
            playBtn.innerHTML = "⏸ Pause";
            playBtn.classList.add("playing");
            isPlaying = true;
        }}

        function stopPlayback() {{
            clearInterval(playInterval);
            playBtn.innerHTML = "▶ Play";
            playBtn.classList.remove("playing");
            isPlaying = false;
        }}

        playBtn.onclick = () => {{ if(isPlaying) stopPlayback(); else startPlayback(); }};
        speedSelect.onchange = () => {{ if(isPlaying) startPlayback(); }};

        if (flightHistory.length > 0) {{
            modalSlider.max = flightHistory.length - 1;
            const timeMap = {{}};
            const latestTime = flightHistory[flightHistory.length - 1].timestamp;
            
            const fmt = (d) => d.getFullYear() + "-" + String(d.getMonth()+1).padStart(2,'0') + "-" + String(d.getDate()).padStart(2,'0') + "T" + String(d.getHours()).padStart(2,'0') + ":" + String(d.getMinutes()).padStart(2,'0');
            exactPicker.min = fmt(new Date(flightHistory[0].timestamp * 1000));
            exactPicker.max = fmt(new Date(latestTime * 1000));

            flightHistory.forEach((record, index) => {{
                let diffSecs = latestTime - record.timestamp;
                let h = Math.floor(diffSecs / 3600);
                let m = Math.floor((diffSecs % 3600) / 60);
                if(!timeMap[h]) timeMap[h] = [];
                timeMap[h].push({{m: m, index: index}});
            }});
            
            Object.keys(timeMap).sort((a,b) => a-b).forEach(h => {{
                let opt = document.createElement("option"); opt.value = h; opt.innerText = h == 0 ? "Under 1 hour ago" : h + " hours ago"; hSelect.appendChild(opt);
            }});
            
            function syncSelectsToIndex(idx) {{
                let record = flightHistory[idx];
                let diffSecs = latestTime - record.timestamp;
                let h = Math.floor(diffSecs / 3600);
                hSelect.value = h;
                mSelect.innerHTML = "";
                timeMap[h].sort((a,b) => a.m - b.m).forEach(item => {{
                    let opt = document.createElement("option"); opt.value = item.index; opt.innerText = item.m + " minutes ago"; mSelect.appendChild(opt);
                }});
                mSelect.value = idx;
                let mVal = Math.floor((diffSecs % 3600) / 60);
                sliderTooltip.innerText = h + "h " + mVal + "m ago";
                
                if(tzMode === "local") {{
                    exactPicker.value = fmt(new Date(record.timestamp * 1000));
                }} else {{
                    let d = new Date((record.timestamp + (3.5 * 3600)) * 1000);
                    exactPicker.value = fmt(d);
                }}
            }}

            hSelect.addEventListener('change', function() {{
                mSelect.innerHTML = "";
                timeMap[this.value].sort((a,b) => a.m - b.m).forEach(item => {{ let opt = document.createElement("option"); opt.value = item.index; opt.innerText = item.m + " minutes ago"; mSelect.appendChild(opt); }});
                let targetIdx = mSelect.options[0].value;
                modalSlider.value = targetIdx;
                syncSelectsToIndex(targetIdx);
                renderPlanes(targetIdx);
            }});

            mSelect.addEventListener('change', function() {{ modalSlider.value = this.value; syncSelectsToIndex(this.value); renderPlanes(this.value); }});

            exactPicker.addEventListener('change', (e) => {{
                if(!e.target.value) return;
                let selectedUnix = 0;
                if(tzMode === "local") {{
                    selectedUnix = new Date(e.target.value).getTime() / 1000;
                }} else {{
                    let d = new Date(e.target.value + "Z");
                    selectedUnix = (d.getTime() / 1000) - (3.5 * 3600);
                }}
                
                let closestIdx = 0;
                let minDiff = Infinity;
                flightHistory.forEach((record, idx) => {{
                    let diff = Math.abs(record.timestamp - selectedUnix);
                    if(diff < minDiff) {{ minDiff = diff; closestIdx = idx; }}
                }});
                modalSlider.value = closestIdx;
                syncSelectsToIndex(closestIdx);
                renderPlanes(closestIdx);
            }});

            modalSlider.addEventListener("input", function() {{
                sliderTooltip.style.display = "block";
                const val = this.value, min = this.min ? this.min : 0, max = this.max ? this.max : 100;
                let newVal = max > min ? Number(((val - min) * 100) / (max - min)) : 0;
                sliderTooltip.style.left = `calc(${{newVal}}% + (${{10 - newVal * 0.2}}px))`;
                syncSelectsToIndex(this.value);
                renderPlanes(this.value);
            }});
            
            modalSlider.addEventListener("change", function() {{ sliderTooltip.style.display = "none"; renderPlanes(this.value); }});
            
            if(hSelect.options.length > 0) {{ let lastIdx = flightHistory.length - 1; modalSlider.value = lastIdx; syncSelectsToIndex(lastIdx); renderPlanes(lastIdx); }}
        }} else {{ bannerTehran.innerText = "No temporal data available."; loading.style.display = "none"; }}

        window.onload = () => {{ setTimeout(() => {{ loading.style.opacity = "0"; setTimeout(() => {{ loading.style.display = "none"; }}, 300); }}, 500); }};
    </script>
</body>
</html>"""
    with open("planes.html", "w", encoding="utf-8") as f: f.write(html)

def format_telegram_message(notam_id, notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links, pyramid_levels, ai_explanation, raw_text, is_update=False):
    importance_str = "⏳ Pending"
    if "First" in pyramid_levels: importance_str = "1️⃣ First"
    elif "Second" in pyramid_levels: importance_str = "2️⃣ Second"
    elif "Third" in pyramid_levels: importance_str = "3️⃣ Third"

    msg_parts = []
    if is_update: msg_parts.append("⚠️ *This NOTAM is not new and has been sent before. The bot is sending it again because the AI explanation has now been provided.*")
    msg_parts.append(f"🚀 **TEHRAN FIR NOTAM ALERT (OIIX)**")
    msg_parts.append(f"NOTAM Number: {notam_id} • {notam_type}")
    msg_parts.append(f"🚨 Importance level: {importance_str}")
    msg_parts.append("------------------------------------")
    msg_parts.append(f"🏷️ Subject: {subject_text}")
    msg_parts.append(f"⚠️ Condition: {condition_text}")
    msg_parts.append(f"✈️ Traffic: {traffic_list}")
    msg_parts.append("------------------------------------")
    msg_parts.append("🤖 NOTAM Explanation (Internal Decoder Fallback):" if "Pending" in pyramid_levels else "🤖 NOTAM Explanation (Generated by AI):")
    msg_parts.append(f"{ai_explanation}")
    msg_parts.append("------------------------------------")
    msg_parts.append(f"📅 From: {valid_from_str}")
    msg_parts.append(f"📅 To: {valid_to_str}")
    if map_links and not is_update: 
        msg_parts.append("------------------------------------")
        for link in map_links: msg_parts.append(link)
    msg_parts.extend(["------------------------------------", "NOTAM Raw Text:", f"`{raw_text}`"])
    return "\n".join(msg_parts)

def main():
    print("Fetching FULL data from FAA AIM OIIX Only...")
    seen_ids = load_json(STATE_FILE, {})
    run_history = load_json(HISTORY_FILE, [])
    ai_buffer = load_json(AI_BUFFER_FILE, [])
    
    active_notams_raw = load_json(ACTIVE_RAW_FILE, {})
    active_notams_decoded = load_json(ACTIVE_DECODED_FILE, {})
    active_notams_ai = load_json(ACTIVE_AI_FILE, {})
    
    expired_notams_raw = load_json(EXPIRED_RAW_FILE, {})
    expired_notams_decoded = load_json(EXPIRED_DECODED_FILE, {})
    expired_notams_ai = load_json(EXPIRED_AI_FILE, {})
    
    plane_state = load_json(PLANE_STATE_FILE, {"previous_count": -1})
    notam_list = get_all_notams()
    
    current_planes = fetch_iran_planes()
    current_count = len(current_planes)
    
    dt_utc = datetime.now(timezone.utc)
    current_timestamp = int(dt_utc.timestamp())
    current_time_utc_str = dt_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
    
    new_record = {"timestamp": current_timestamp, "time_utc": current_time_utc_str, "count": current_count, "planes": current_planes}
    plane_history = load_json(PLANE_HISTORY_FILE, [])
    plane_history.append(new_record)
    
    three_weeks_ago = current_timestamp - 1814400 
    keep_history, archive_history = [], []
    for record in plane_history:
        if record.get("timestamp", 0) >= three_weeks_ago: 
            keep_history.append(record)
        else: 
            archive_history.append(record)
            
    save_json(PLANE_HISTORY_FILE, keep_history)
    if archive_history:
        existing_archive = load_json(PLANE_ARCHIVE_FILE, [])
        existing_archive.extend(archive_history)
        save_json(PLANE_ARCHIVE_FILE, existing_archive)
        
    two_weeks_ago = current_timestamp - 1209600 
    history_2weeks = [r for r in keep_history if r.get("timestamp", 0) >= two_weeks_ago]
    generate_planes_html(history_2weeks)
    
    prev_count = plane_state.get("previous_count", -1)
    if prev_count != -1:
        if prev_count <= 5 and current_count > 5:
            send_telegram("✅ **AIRSPACE UPDATE:** Iranian Airspace is now OPEN. Normal commercial traffic is resuming.")
        elif prev_count > 5 and current_count <= 5:
            if current_count == 0:
                send_telegram("🚨 **CRITICAL WARNING:** Iranian Airspace is actively clearing. Current commercial planes detected inside the OIIX FIR boundary: 0.")
            else:
                send_telegram(f"⚠️ **AIRSPACE ALERT:** Extreme drop in commercial traffic detected. Only {current_count} planes currently inside the OIIX FIR boundary.")
        elif prev_count > 0 and current_count == 0:
            send_telegram("🚨 **CRITICAL WARNING:** Iranian Airspace is completely CLEARED. Current commercial planes detected inside the OIIX FIR boundary: 0.")
        
    plane_state["previous_count"] = current_count
    save_json(PLANE_STATE_FILE, plane_state)
    
    if not notam_list:
        print("No valid data received. Skipping processing to protect state.")
        return

    current_raw_dict, current_decoded_dict, current_ai_dict = {}, {}, {}
    new_count = 0
    new_ai_buffer = []

    for notam in notam_list:
        notam_id = notam.get("notamNumber")
        icao_id = notam.get("icaoId")
        if not notam_id: 
            continue
        full_id = f"{icao_id} {notam_id}"
        notam["last_seen_utc"] = current_time_utc_str
        current_raw_dict[full_id] = notam
        if full_id in active_notams_decoded and "error" not in active_notams_decoded[full_id]:
            current_decoded_dict[full_id] = active_notams_decoded[full_id]
        if full_id in active_notams_ai and "error" not in active_notams_ai[full_id]:
            current_ai_dict[full_id] = active_notams_ai[full_id]

    for buf_id in list(set(ai_buffer)):
        if buf_id in current_raw_dict and buf_id not in current_ai_dict:
            raw_text = current_raw_dict[buf_id].get("icaoMessage", "")
            notam_id = current_raw_dict[buf_id].get("notamNumber")
            ai_data = get_ai_explanation(raw_text)
            if ai_data and "highest_level" in ai_data:
                ai_data["last_seen_utc"] = current_time_utc_str
                current_ai_dict[buf_id] = ai_data
                lvl = ai_data.get("highest_level", "Third Level")
                ai_explanation = ai_data.get("explanation", "")
                notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links = extract_notam_details(raw_text, current_decoded_dict.get(buf_id, {}), notam_id)
                msg = format_telegram_message(notam_id, notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links, lvl, ai_explanation, raw_text, is_update=True)
                send_telegram(msg)
            else:
                new_ai_buffer.append(buf_id)

    for full_id, notam in current_raw_dict.items():
        if full_id not in current_decoded_dict:
            raw_text = notam.get("icaoMessage", "")
            decoded_obj = decode_notam(raw_text)
            if decoded_obj:
                decoded_obj["last_seen_utc"] = current_time_utc_str
                current_decoded_dict[full_id] = decoded_obj

        if full_id not in seen_ids:
            raw_text = notam.get("icaoMessage", "")
            notam_id = notam.get("notamNumber")
            internal_translation = translate_e_section(raw_text)
            ai_data = get_ai_explanation(raw_text)
            if ai_data and "highest_level" in ai_data:
                ai_data["last_seen_utc"] = current_time_utc_str
                current_ai_dict[full_id] = ai_data
                lvl = ai_data.get("highest_level", "Third Level")
                ai_explanation = ai_data.get("explanation", internal_translation)
            else:
                new_ai_buffer.append(full_id)
                lvl = "Pending"
                ai_explanation = f"{internal_translation}\n\n*(Will automatically update when AI is available)*"

            notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links = extract_notam_details(raw_text, current_decoded_dict.get(full_id, {}), notam_id)
            msg = format_telegram_message(notam_id, notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links, lvl, ai_explanation, raw_text, is_update=False)
            send_telegram(msg)
            seen_ids[full_id] = current_time_utc_str
            new_count += 1

    removed_count = 0
    newly_expired_raw, newly_expired_decoded, newly_expired_ai = {}, {}, {}
    for old_id, old_data in active_notams_raw.items():
        if old_id not in current_raw_dict:
            old_data["archived_utc"] = current_time_utc_str
            newly_expired_raw[old_id] = old_data
            if old_id in active_notams_decoded:
                dec_data = active_notams_decoded[old_id]
                dec_data["archived_utc"] = current_time_utc_str
                newly_expired_decoded[old_id] = dec_data
            if old_id in active_notams_ai:
                ai_ex_data = active_notams_ai[old_id]
                ai_ex_data["archived_utc"] = current_time_utc_str
                newly_expired_ai[old_id] = ai_ex_data
            removed_count += 1
            
    expired_notams_raw = {**newly_expired_raw, **expired_notams_raw}
    expired_notams_decoded = {**newly_expired_decoded, **expired_notams_decoded}
    expired_notams_ai = {**newly_expired_ai, **expired_notams_ai}

    new_state = {}
    for cid in current_raw_dict.keys(): 
        new_state[cid] = seen_ids.get(cid, current_time_utc_str)

    save_json(STATE_FILE, new_state)
    save_json(AI_BUFFER_FILE, new_ai_buffer)
    save_json(ACTIVE_RAW_FILE, current_raw_dict)
    save_json(ACTIVE_DECODED_FILE, current_decoded_dict)
    save_json(ACTIVE_AI_FILE, current_ai_dict)
    save_json(EXPIRED_RAW_FILE, expired_notams_raw)
    save_json(EXPIRED_DECODED_FILE, expired_notams_decoded)
    save_json(EXPIRED_AI_FILE, expired_notams_ai)

    run_record = {"time_utc": current_time_utc_str, "total_active": len(current_raw_dict), "new_added": new_count, "removed": removed_count, "buffered_ai": len(new_ai_buffer)}
    run_history.insert(0, run_record)
    save_json(HISTORY_FILE, run_history[:250])

if __name__ == "__main__":
    main()
