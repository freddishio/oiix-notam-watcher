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
    cleaned = re.sub(r'islamic\s+republic\s+of\s+iran', 'IRAN', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'iran\s+\(islamic\s+republic\s+of\)', 'IRAN', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'islamic\s+republic\s+iran', 'IRAN', cleaned, flags=re.IGNORECASE)
    if cleaned.strip().lower() == 'iran':
        return 'IRAN'
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
    json_data_string = json.dumps(history_rendered).replace("</", "<\\/")
    html = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <title>Iran Airspace Live | OIIX FIR Aircraft Dashboard</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <meta name="theme-color" content="#07111f" />
    <meta name="description" content="Live aircraft dashboard for Iranian airspace inside the OIIX FIR boundary." />

    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css"/>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

    <style>
        :root {
            --bg: #06101e;
            --bg-2: #0a1728;
            --panel: rgba(9, 20, 36, 0.74);
            --panel-strong: rgba(10, 23, 40, 0.92);
            --panel-soft: rgba(255, 255, 255, 0.055);
            --line: rgba(255, 255, 255, 0.12);
            --line-strong: rgba(255, 255, 255, 0.2);
            --text: #f7fbff;
            --muted: #9eb1ca;
            --muted-2: #6f83a0;
            --gold: #ffd166;
            --cyan: #32d5ff;
            --blue: #61a5ff;
            --green: #3cff9b;
            --red: #ff5370;
            --orange: #ff9f43;
            --shadow: 0 24px 80px rgba(0, 0, 0, 0.42);
            --shadow-soft: 0 18px 48px rgba(0, 0, 0, 0.28);
            --radius-xl: 28px;
            --radius-lg: 22px;
            --radius-md: 16px;
            --radius-sm: 12px;
            --safe-bottom: env(safe-area-inset-bottom, 0px);
        }

        * {
            box-sizing: border-box;
        }

        html, body {
            width: 100%;
            height: 100%;
            padding: 0;
            margin: 0;
            background:
                radial-gradient(circle at 18% 12%, rgba(50, 213, 255, 0.18), transparent 30%),
                radial-gradient(circle at 88% 18%, rgba(255, 209, 102, 0.12), transparent 25%),
                linear-gradient(135deg, #030812 0%, #06101e 42%, #07172a 100%);
            color: var(--text);
            font-family: "Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            overflow: hidden;
        }

        button, input, select {
            font: inherit;
        }

        button {
            border: 0;
        }

        #app {
            position: fixed;
            inset: 0;
            overflow: hidden;
        }

        #map {
            position: absolute;
            inset: 0;
            z-index: 1;
            background: #050b14;
        }

        .leaflet-control-attribution {
            background: rgba(5, 12, 22, 0.76) !important;
            color: rgba(255,255,255,0.66) !important;
            backdrop-filter: blur(14px);
            border-radius: 12px 0 0 0;
        }

        .leaflet-control-attribution a {
            color: rgba(118, 214, 255, 0.95) !important;
        }

        .leaflet-control-zoom {
            border: 1px solid var(--line) !important;
            border-radius: 16px !important;
            overflow: hidden;
            box-shadow: var(--shadow-soft);
            backdrop-filter: blur(18px);
        }

        .leaflet-control-zoom a {
            width: 40px !important;
            height: 40px !important;
            line-height: 40px !important;
            background: rgba(9, 20, 36, 0.82) !important;
            color: var(--text) !important;
            border-bottom: 1px solid var(--line) !important;
        }

        .leaflet-control-zoom a:hover {
            background: rgba(50, 213, 255, 0.16) !important;
        }

        .map-vignette {
            position: absolute;
            inset: 0;
            pointer-events: none;
            z-index: 2;
            background:
                linear-gradient(90deg, rgba(3, 8, 18, 0.88) 0%, rgba(3, 8, 18, 0.36) 28%, rgba(3, 8, 18, 0.1) 55%, rgba(3, 8, 18, 0.58) 100%),
                radial-gradient(circle at center, transparent 42%, rgba(0, 0, 0, 0.42) 100%);
        }

        .noise {
            position: absolute;
            inset: 0;
            pointer-events: none;
            z-index: 3;
            opacity: 0.12;
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 220 220' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='220' height='220' filter='url(%23n)' opacity='.35'/%3E%3C/svg%3E");
            mix-blend-mode: screen;
        }

        .topbar {
            position: absolute;
            top: 18px;
            left: 18px;
            right: 18px;
            z-index: 12;
            display: grid;
            grid-template-columns: minmax(260px, 450px) minmax(220px, 1fr) auto;
            gap: 14px;
            align-items: stretch;
            pointer-events: none;
        }

        .brand-card,
        .search-card,
        .status-pill,
        .panel,
        .timeline-card,
        .mobile-sheet {
            background: var(--panel);
            border: 1px solid var(--line);
            box-shadow: var(--shadow);
            backdrop-filter: blur(22px) saturate(1.18);
            -webkit-backdrop-filter: blur(22px) saturate(1.18);
        }

        .brand-card {
            min-height: 76px;
            border-radius: var(--radius-xl);
            padding: 16px 18px;
            display: flex;
            align-items: center;
            gap: 14px;
            pointer-events: auto;
            overflow: hidden;
            position: relative;
        }

        .brand-card::before {
            content: "";
            position: absolute;
            inset: -1px;
            background: linear-gradient(135deg, rgba(50, 213, 255, 0.22), transparent 42%, rgba(255, 209, 102, 0.18));
            pointer-events: none;
            opacity: 0.9;
            mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
            -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
            padding: 1px;
            mask-composite: exclude;
            -webkit-mask-composite: xor;
            border-radius: inherit;
        }

        .brand-icon {
            width: 48px;
            height: 48px;
            flex: 0 0 48px;
            border-radius: 18px;
            display: grid;
            place-items: center;
            background:
                radial-gradient(circle at 28% 24%, rgba(255,255,255,0.32), transparent 30%),
                linear-gradient(135deg, rgba(50, 213, 255, 0.96), rgba(97, 165, 255, 0.78));
            box-shadow: 0 16px 36px rgba(50, 213, 255, 0.22);
            color: #06101e;
            font-size: 21px;
        }

        .brand-copy h1 {
            margin: 0;
            font-size: 18px;
            line-height: 1.1;
            letter-spacing: -0.04em;
            font-weight: 900;
        }

        .brand-copy p {
            margin: 6px 0 0;
            color: var(--muted);
            font-size: 12px;
            font-weight: 650;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }

        .search-card {
            min-height: 76px;
            border-radius: var(--radius-xl);
            padding: 12px;
            display: grid;
            grid-template-columns: minmax(160px, 1fr) 150px 150px 48px;
            gap: 10px;
            align-items: center;
            pointer-events: auto;
        }

        .field {
            position: relative;
            height: 48px;
        }

        .field i {
            position: absolute;
            left: 15px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--muted-2);
            font-size: 14px;
            z-index: 2;
        }

        .field input,
        .field select {
            width: 100%;
            height: 48px;
            border-radius: 17px;
            border: 1px solid rgba(255,255,255,0.1);
            background: rgba(255,255,255,0.07);
            color: var(--text);
            outline: 0;
            padding: 0 14px 0 42px;
            font-weight: 650;
            transition: 160ms ease;
        }

        .field select {
            appearance: none;
            cursor: pointer;
        }

        .field input:focus,
        .field select:focus {
            border-color: rgba(50, 213, 255, 0.62);
            background: rgba(50, 213, 255, 0.085);
            box-shadow: 0 0 0 4px rgba(50, 213, 255, 0.12);
        }

        .field input::placeholder {
            color: rgba(158, 177, 202, 0.7);
        }

        .field select option {
            background: #0a1728;
            color: #fff;
        }

        .icon-button {
            height: 48px;
            width: 48px;
            border-radius: 17px;
            display: grid;
            place-items: center;
            background: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.1);
            color: var(--text);
            cursor: pointer;
            transition: 180ms ease;
        }

        .icon-button:hover {
            transform: translateY(-1px);
            background: rgba(50, 213, 255, 0.14);
            border-color: rgba(50, 213, 255, 0.35);
        }

        .status-pill {
            min-height: 76px;
            border-radius: var(--radius-xl);
            padding: 14px 17px;
            display: flex;
            align-items: center;
            gap: 12px;
            pointer-events: auto;
            min-width: 210px;
        }

        .pulse {
            width: 46px;
            height: 46px;
            border-radius: 50%;
            display: grid;
            place-items: center;
            background: rgba(60, 255, 155, 0.1);
            border: 1px solid rgba(60, 255, 155, 0.26);
            color: var(--green);
            position: relative;
            flex: 0 0 46px;
        }

        .pulse::before {
            content: "";
            position: absolute;
            inset: -4px;
            border-radius: 50%;
            border: 1px solid rgba(60, 255, 155, 0.42);
            animation: ping 1.9s ease-out infinite;
        }

        .pulse.no-traffic {
            background: rgba(255, 83, 112, 0.11);
            border-color: rgba(255, 83, 112, 0.3);
            color: var(--red);
        }

        .pulse.no-traffic::before {
            border-color: rgba(255, 83, 112, 0.42);
        }

        @keyframes ping {
            0% { transform: scale(0.9); opacity: 0.9; }
            100% { transform: scale(1.35); opacity: 0; }
        }

        .status-copy strong {
            display: block;
            font-size: 13px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .status-copy span {
            display: block;
            margin-top: 4px;
            color: var(--muted);
            font-size: 12px;
            white-space: nowrap;
        }

        .left-panel {
            position: absolute;
            left: 18px;
            top: 112px;
            bottom: 96px;
            z-index: 11;
            width: 420px;
            max-width: calc(100vw - 36px);
            display: flex;
            flex-direction: column;
            gap: 14px;
            pointer-events: none;
        }

        .panel {
            border-radius: var(--radius-xl);
            pointer-events: auto;
            overflow: hidden;
        }

        .stats-panel {
            padding: 16px;
        }

        .stat-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
        }

        .stat-tile {
            min-height: 102px;
            border-radius: 20px;
            padding: 15px;
            background:
                linear-gradient(135deg, rgba(255,255,255,0.09), rgba(255,255,255,0.035));
            border: 1px solid rgba(255,255,255,0.09);
            position: relative;
            overflow: hidden;
        }

        .stat-tile::after {
            content: "";
            position: absolute;
            right: -22px;
            bottom: -24px;
            width: 92px;
            height: 92px;
            border-radius: 50%;
            background: rgba(50, 213, 255, 0.08);
        }

        .stat-tile.gold::after { background: rgba(255, 209, 102, 0.12); }
        .stat-tile.green::after { background: rgba(60, 255, 155, 0.10); }
        .stat-tile.orange::after { background: rgba(255, 159, 67, 0.10); }

        .stat-icon {
            width: 34px;
            height: 34px;
            border-radius: 13px;
            display: grid;
            place-items: center;
            background: rgba(50, 213, 255, 0.12);
            color: var(--cyan);
            margin-bottom: 12px;
        }

        .stat-tile.gold .stat-icon {
            background: rgba(255, 209, 102, 0.13);
            color: var(--gold);
        }

        .stat-tile.green .stat-icon {
            background: rgba(60, 255, 155, 0.12);
            color: var(--green);
        }

        .stat-tile.orange .stat-icon {
            background: rgba(255, 159, 67, 0.13);
            color: var(--orange);
        }

        .stat-value {
            font-family: "JetBrains Mono", monospace;
            font-weight: 800;
            font-size: 25px;
            letter-spacing: -0.04em;
            line-height: 1;
        }

        .stat-label {
            margin-top: 7px;
            font-size: 12px;
            color: var(--muted);
            font-weight: 700;
        }

        .list-panel {
            flex: 1;
            min-height: 0;
            display: flex;
            flex-direction: column;
        }

        .panel-header {
            padding: 18px 18px 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }

        .panel-title {
            display: flex;
            align-items: center;
            gap: 10px;
            min-width: 0;
        }

        .panel-title i {
            color: var(--cyan);
        }

        .panel-title h2 {
            margin: 0;
            font-size: 15px;
            font-weight: 900;
            letter-spacing: -0.02em;
        }

        .panel-subtitle {
            margin-top: 3px;
            color: var(--muted);
            font-size: 12px;
            font-weight: 650;
        }

        .count-chip {
            flex: 0 0 auto;
            padding: 7px 10px;
            border-radius: 999px;
            background: rgba(50, 213, 255, 0.11);
            border: 1px solid rgba(50, 213, 255, 0.2);
            color: var(--cyan);
            font-size: 12px;
            font-family: "JetBrains Mono", monospace;
            font-weight: 800;
        }

        .plane-list {
            padding: 12px;
            overflow: auto;
            min-height: 0;
            scrollbar-width: thin;
            scrollbar-color: rgba(255,255,255,0.18) transparent;
        }

        .plane-card {
            border-radius: 20px;
            padding: 14px;
            background:
                linear-gradient(135deg, rgba(255,255,255,0.095), rgba(255,255,255,0.035));
            border: 1px solid rgba(255,255,255,0.09);
            margin-bottom: 10px;
            cursor: pointer;
            transition: 180ms ease;
            position: relative;
            overflow: hidden;
        }

        .plane-card:hover,
        .plane-card.active {
            transform: translateY(-2px);
            border-color: rgba(50, 213, 255, 0.38);
            background:
                linear-gradient(135deg, rgba(50, 213, 255, 0.13), rgba(255,255,255,0.05));
            box-shadow: 0 16px 40px rgba(0,0,0,0.24);
        }

        .plane-card::before {
            content: "";
            position: absolute;
            left: 0;
            top: 14px;
            bottom: 14px;
            width: 3px;
            border-radius: 99px;
            background: linear-gradient(var(--cyan), var(--blue));
            opacity: 0.9;
        }

        .plane-card-main {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
        }

        .plane-title {
            min-width: 0;
        }

        .plane-title strong {
            display: block;
            font-size: 14px;
            font-weight: 900;
            letter-spacing: -0.02em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .plane-title span {
            display: block;
            margin-top: 4px;
            color: var(--muted);
            font-size: 12px;
            font-weight: 650;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .plane-badge {
            flex: 0 0 auto;
            padding: 7px 9px;
            border-radius: 999px;
            background: rgba(255, 209, 102, 0.12);
            border: 1px solid rgba(255, 209, 102, 0.18);
            color: var(--gold);
            font-family: "JetBrains Mono", monospace;
            font-size: 11px;
            font-weight: 800;
        }

        .plane-meta {
            margin-top: 12px;
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px;
        }

        .meta-item {
            min-width: 0;
            padding: 9px 10px;
            border-radius: 14px;
            background: rgba(0,0,0,0.18);
            border: 1px solid rgba(255,255,255,0.055);
        }

        .meta-item small {
            display: block;
            color: var(--muted-2);
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .meta-item span {
            display: block;
            margin-top: 4px;
            font-size: 12px;
            font-weight: 800;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .timeline-card {
            position: absolute;
            left: 18px;
            right: 18px;
            bottom: 18px;
            z-index: 12;
            min-height: 62px;
            border-radius: var(--radius-xl);
            padding: 12px 16px;
            display: grid;
            grid-template-columns: 210px 1fr 170px;
            gap: 14px;
            align-items: center;
            pointer-events: auto;
        }

        .timeline-label strong {
            display: block;
            font-size: 12px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .timeline-label span {
            display: block;
            margin-top: 5px;
            color: var(--muted);
            font-size: 12px;
            font-weight: 650;
        }

        .timeline-range {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .timeline-range button {
            flex: 0 0 auto;
        }

        input[type="range"] {
            width: 100%;
            accent-color: var(--cyan);
            cursor: pointer;
        }

        .time-chip {
            padding: 10px 12px;
            border-radius: 16px;
            background: rgba(255,255,255,0.07);
            border: 1px solid rgba(255,255,255,0.08);
            color: var(--text);
            font-family: "JetBrains Mono", monospace;
            font-size: 12px;
            font-weight: 800;
            text-align: center;
        }

        .mobile-open {
            display: none;
            position: absolute;
            right: 16px;
            bottom: calc(92px + var(--safe-bottom));
            z-index: 14;
            width: 58px;
            height: 58px;
            border-radius: 22px;
            background: linear-gradient(135deg, rgba(50, 213, 255, 0.96), rgba(97, 165, 255, 0.86));
            color: #06101e;
            box-shadow: 0 20px 54px rgba(50, 213, 255, 0.26);
            cursor: pointer;
        }

        .mobile-sheet {
            display: none;
        }

        .empty-state {
            min-height: 230px;
            display: grid;
            place-items: center;
            padding: 24px;
            text-align: center;
            color: var(--muted);
        }

        .empty-state i {
            font-size: 36px;
            color: var(--muted-2);
            margin-bottom: 12px;
        }

        .empty-state strong {
            display: block;
            color: var(--text);
            font-size: 15px;
            margin-bottom: 6px;
        }

        .plane-marker {
            width: 34px !important;
            height: 34px !important;
            margin-left: -17px !important;
            margin-top: -17px !important;
        }

        .plane-marker-inner {
            width: 34px;
            height: 34px;
            display: grid;
            place-items: center;
            border-radius: 50%;
            background: rgba(6, 16, 30, 0.76);
            border: 1px solid rgba(50, 213, 255, 0.45);
            box-shadow:
                0 0 0 5px rgba(50, 213, 255, 0.12),
                0 12px 26px rgba(0,0,0,0.42);
            backdrop-filter: blur(12px);
            color: var(--cyan);
        }

        .plane-marker-inner i {
            transform: rotate(var(--heading));
            filter: drop-shadow(0 0 8px rgba(50, 213, 255, 0.72));
        }

        .plane-marker.selected .plane-marker-inner {
            color: var(--gold);
            border-color: rgba(255, 209, 102, 0.75);
            box-shadow:
                0 0 0 7px rgba(255, 209, 102, 0.14),
                0 18px 34px rgba(0,0,0,0.48);
        }

        .leaflet-popup-content-wrapper {
            background: rgba(8, 19, 34, 0.92);
            color: var(--text);
            border: 1px solid rgba(255,255,255,0.14);
            border-radius: 20px;
            box-shadow: var(--shadow-soft);
            backdrop-filter: blur(20px);
        }

        .leaflet-popup-tip {
            background: rgba(8, 19, 34, 0.92);
        }

        .leaflet-popup-content {
            margin: 14px 15px;
            min-width: 230px;
        }

        .popup-title {
            display: flex;
            align-items: center;
            gap: 9px;
            font-weight: 900;
            font-size: 15px;
            margin-bottom: 10px;
        }

        .popup-title i {
            color: var(--gold);
        }

        .popup-grid {
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 7px 12px;
            font-size: 12px;
        }

        .popup-grid b {
            color: var(--muted);
            font-weight: 800;
        }

        .popup-grid span {
            color: var(--text);
            font-weight: 700;
        }

        @media (max-width: 1120px) {
            .topbar {
                grid-template-columns: 1fr;
                max-width: 470px;
                right: auto;
            }

            .search-card {
                grid-template-columns: 1fr 1fr 48px;
            }

            .search-card .field:first-child {
                grid-column: 1 / -1;
            }

            .status-pill {
                display: none;
            }

            .left-panel {
                top: 250px;
            }

            .timeline-card {
                grid-template-columns: 165px 1fr;
            }

            .time-chip {
                display: none;
            }
        }

        @media (max-width: 760px) {
            body {
                overflow: hidden;
            }

            .map-vignette {
                background:
                    linear-gradient(180deg, rgba(3,8,18,0.72) 0%, rgba(3,8,18,0.16) 28%, rgba(3,8,18,0.08) 58%, rgba(3,8,18,0.72) 100%);
            }

            .topbar {
                top: 12px;
                left: 12px;
                right: 12px;
                display: block;
                max-width: none;
            }

            .brand-card {
                min-height: 68px;
                border-radius: 23px;
                padding: 13px 14px;
            }

            .brand-icon {
                width: 42px;
                height: 42px;
                flex-basis: 42px;
                border-radius: 16px;
                font-size: 18px;
            }

            .brand-copy h1 {
                font-size: 15px;
            }

            .brand-copy p {
                font-size: 10px;
            }

            .search-card,
            .status-pill,
            .left-panel {
                display: none;
            }

            .timeline-card {
                left: 12px;
                right: 12px;
                bottom: calc(12px + var(--safe-bottom));
                grid-template-columns: 1fr;
                gap: 8px;
                padding: 12px;
                border-radius: 22px;
            }

            .timeline-label {
                display: flex;
                justify-content: space-between;
                gap: 8px;
                align-items: center;
            }

            .timeline-label span {
                margin-top: 0;
                text-align: right;
            }

            .timeline-range {
                gap: 8px;
            }

            .timeline-range .icon-button {
                width: 42px;
                height: 42px;
                border-radius: 16px;
            }

            .mobile-open {
                display: grid;
                place-items: center;
            }

            .mobile-sheet {
                display: flex;
                flex-direction: column;
                position: absolute;
                z-index: 30;
                left: 0;
                right: 0;
                bottom: 0;
                height: min(82vh, 720px);
                border-radius: 28px 28px 0 0;
                border-bottom: 0;
                transform: translateY(calc(100% - 72px));
                transition: transform 260ms cubic-bezier(.2,.8,.2,1);
                overflow: hidden;
                padding-bottom: var(--safe-bottom);
            }

            .mobile-sheet.open {
                transform: translateY(0);
            }

            .sheet-handle {
                width: 46px;
                height: 5px;
                border-radius: 99px;
                background: rgba(255,255,255,0.24);
                margin: 12px auto 8px;
            }

            .sheet-top {
                padding: 0 14px 12px;
                display: grid;
                grid-template-columns: 1fr 44px;
                gap: 10px;
                align-items: center;
                border-bottom: 1px solid rgba(255,255,255,0.08);
            }

            .sheet-top-title strong {
                display: block;
                font-size: 15px;
                font-weight: 900;
            }

            .sheet-top-title span {
                display: block;
                margin-top: 4px;
                color: var(--muted);
                font-size: 12px;
                font-weight: 650;
            }

            .sheet-content {
                min-height: 0;
                overflow: auto;
                padding: 12px;
            }

            .mobile-filters {
                display: grid;
                gap: 10px;
                margin-bottom: 12px;
            }

            .mobile-stats {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px;
                margin-bottom: 12px;
            }

            .mobile-stats .stat-tile {
                min-height: 90px;
            }

            .plane-meta {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
    </style>
</head>
<body>
    <div id="app">
        <div id="map"></div>
        <div class="map-vignette"></div>
        <div class="noise"></div>

        <header class="topbar" aria-label="Dashboard controls">
            <section class="brand-card">
                <div class="brand-icon"><i class="fa-solid fa-plane-up"></i></div>
                <div class="brand-copy">
                    <h1>Iran Airspace Monitor</h1>
                    <p>OIIX FIR aircraft intelligence dashboard</p>
                </div>
            </section>

            <section class="search-card">
                <label class="field">
                    <i class="fa-solid fa-magnifying-glass"></i>
                    <input id="searchInput" type="search" placeholder="Search callsign, airline, registration..." autocomplete="off" />
                </label>

                <label class="field">
                    <i class="fa-solid fa-building-flag"></i>
                    <select id="airlineFilter" aria-label="Filter by airline">
                        <option value="">All airlines</option>
                    </select>
                </label>

                <label class="field">
                    <i class="fa-solid fa-globe"></i>
                    <select id="countryFilter" aria-label="Filter by country">
                        <option value="">All countries</option>
                    </select>
                </label>

                <button class="icon-button" id="resetFilters" title="Reset filters" type="button">
                    <i class="fa-solid fa-rotate-left"></i>
                </button>
            </section>

            <section class="status-pill">
                <div class="pulse" id="trafficPulse"><i class="fa-solid fa-satellite-dish"></i></div>
                <div class="status-copy">
                    <strong id="trafficStatus">Live snapshot</strong>
                    <span id="lastUpdateText">Loading...</span>
                </div>
            </section>
        </header>

        <aside class="left-panel">
            <section class="panel stats-panel">
                <div class="stat-grid" id="desktopStats"></div>
            </section>

            <section class="panel list-panel">
                <div class="panel-header">
                    <div class="panel-title">
                        <i class="fa-solid fa-plane-circle-check"></i>
                        <div>
                            <h2>Aircraft in selected snapshot</h2>
                            <div class="panel-subtitle" id="snapshotSubtitle">Loading aircraft...</div>
                        </div>
                    </div>
                    <div class="count-chip" id="resultCount">0</div>
                </div>
                <div class="plane-list" id="planeList"></div>
            </section>
        </aside>

        <section class="timeline-card">
            <div class="timeline-label">
                <strong><i class="fa-solid fa-clock-rotate-left"></i> History replay</strong>
                <span id="timelineRangeLabel">Latest snapshot</span>
            </div>
            <div class="timeline-range">
                <button class="icon-button" id="prevSnapshot" title="Previous snapshot" type="button">
                    <i class="fa-solid fa-chevron-left"></i>
                </button>
                <input id="snapshotSlider" type="range" min="0" max="0" value="0" aria-label="Snapshot timeline" />
                <button class="icon-button" id="nextSnapshot" title="Next snapshot" type="button">
                    <i class="fa-solid fa-chevron-right"></i>
                </button>
            </div>
            <div class="time-chip" id="snapshotTimeChip">--</div>
        </section>

        <button class="mobile-open" id="mobileOpen" type="button" title="Open aircraft panel">
            <i class="fa-solid fa-chart-simple"></i>
        </button>

        <section class="mobile-sheet" id="mobileSheet">
            <div class="sheet-handle"></div>
            <div class="sheet-top">
                <div class="sheet-top-title">
                    <strong>Aircraft Dashboard</strong>
                    <span id="mobileSubtitle">Live OIIX FIR snapshot</span>
                </div>
                <button class="icon-button" id="mobileClose" type="button">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>
            <div class="sheet-content">
                <div class="mobile-filters">
                    <label class="field">
                        <i class="fa-solid fa-magnifying-glass"></i>
                        <input id="mobileSearchInput" type="search" placeholder="Search aircraft..." autocomplete="off" />
                    </label>
                    <label class="field">
                        <i class="fa-solid fa-building-flag"></i>
                        <select id="mobileAirlineFilter" aria-label="Mobile filter by airline">
                            <option value="">All airlines</option>
                        </select>
                    </label>
                    <label class="field">
                        <i class="fa-solid fa-globe"></i>
                        <select id="mobileCountryFilter" aria-label="Mobile filter by country">
                            <option value="">All countries</option>
                        </select>
                    </label>
                </div>
                <div class="mobile-stats" id="mobileStats"></div>
                <div class="plane-list" id="mobilePlaneList"></div>
            </div>
        </section>

        <script id="history-data" type="application/json">__PLANE_HISTORY_JSON__</script>
    </div>

    <script>
        const rawHistory = JSON.parse(document.getElementById("history-data").textContent || "[]");

        const state = {
            history: Array.isArray(rawHistory) ? rawHistory.filter(Boolean) : [],
            snapshotIndex: 0,
            selectedPlaneKey: null,
            search: "",
            airline: "",
            country: "",
            markers: new Map()
        };

        const elements = {
            searchInput: document.getElementById("searchInput"),
            mobileSearchInput: document.getElementById("mobileSearchInput"),
            airlineFilter: document.getElementById("airlineFilter"),
            mobileAirlineFilter: document.getElementById("mobileAirlineFilter"),
            countryFilter: document.getElementById("countryFilter"),
            mobileCountryFilter: document.getElementById("mobileCountryFilter"),
            resetFilters: document.getElementById("resetFilters"),
            planeList: document.getElementById("planeList"),
            mobilePlaneList: document.getElementById("mobilePlaneList"),
            desktopStats: document.getElementById("desktopStats"),
            mobileStats: document.getElementById("mobileStats"),
            resultCount: document.getElementById("resultCount"),
            snapshotSubtitle: document.getElementById("snapshotSubtitle"),
            mobileSubtitle: document.getElementById("mobileSubtitle"),
            snapshotSlider: document.getElementById("snapshotSlider"),
            prevSnapshot: document.getElementById("prevSnapshot"),
            nextSnapshot: document.getElementById("nextSnapshot"),
            snapshotTimeChip: document.getElementById("snapshotTimeChip"),
            timelineRangeLabel: document.getElementById("timelineRangeLabel"),
            trafficPulse: document.getElementById("trafficPulse"),
            trafficStatus: document.getElementById("trafficStatus"),
            lastUpdateText: document.getElementById("lastUpdateText"),
            mobileOpen: document.getElementById("mobileOpen"),
            mobileClose: document.getElementById("mobileClose"),
            mobileSheet: document.getElementById("mobileSheet")
        };

        state.history.sort((a, b) => Number(a.timestamp || 0) - Number(b.timestamp || 0));
        state.snapshotIndex = Math.max(0, state.history.length - 1);

        const map = L.map("map", {
            zoomControl: true,
            preferCanvas: true
        }).setView([32.4, 53.7], 5);

        L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
            attribution: '&copy; OpenStreetMap &copy; CARTO',
            subdomains: "abcd",
            maxZoom: 20
        }).addTo(map);

        const markerLayer = L.layerGroup().addTo(map);
        const trackLayer = L.layerGroup().addTo(map);

        const iranBounds = L.latLngBounds(
            L.latLng(24.0, 43.0),
            L.latLng(40.5, 64.8)
        );

        map.fitBounds(iranBounds, { padding: [40, 40], animate: false });

        function safe(value, fallback = "Unknown") {
            if (value === null || value === undefined) return fallback;
            const text = String(value).trim();
            if (!text || ["unknown", "unknown flight", "unknown type", "unknown reg", "unknown airline", "unknown location", "n/a", "none", "null"].includes(text.toLowerCase())) {
                return fallback;
            }
            return text;
        }

        function safeRaw(value) {
            const text = safe(value, "");
            return text === "Unknown" ? "" : text;
        }

        function numberOrNull(value) {
            const n = Number(value);
            return Number.isFinite(n) ? n : null;
        }

        function fmtNumber(value, fallback = "Unknown") {
            const n = numberOrNull(value);
            if (n === null) return fallback;
            return Math.round(n).toLocaleString("en-US");
        }

        function parseUTC(value) {
            if (!value) return null;
            const clean = String(value).replace(" UTC", "Z").replace(" ", "T");
            const d = new Date(clean);
            return Number.isNaN(d.getTime()) ? null : d;
        }

        function formatDateTime(value) {
            const d = parseUTC(value);
            if (!d) return "Unknown time";
            return d.toLocaleString(undefined, {
                year: "numeric",
                month: "short",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                hour12: false
            });
        }

        function formatShortTime(value) {
            const d = parseUTC(value);
            if (!d) return "--";
            return d.toLocaleString(undefined, {
                month: "short",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                hour12: false
            });
        }

        function planeKey(plane) {
            const reg = safeRaw(plane.reg);
            if (reg) return "REG:" + reg.toUpperCase();

            const id = safeRaw(plane.id);
            if (id) return "ID:" + id;

            const callsign = safeRaw(plane.callsign);
            const type = safeRaw(plane.type);
            return "FALLBACK:" + callsign + "|" + type + "|" + safeRaw(plane.lat) + "|" + safeRaw(plane.lon);
        }

        function getRoute(plane) {
            const route = plane.route || {};
            const dep = safeRaw(plane.departure) || safeRaw(plane.origin) || safeRaw(plane.from) || safeRaw(route.departure) || safeRaw(route.origin);
            const dst = safeRaw(plane.destination) || safeRaw(plane.dest) || safeRaw(plane.to) || safeRaw(route.destination) || safeRaw(route.dest);
            return {
                departure: dep || "Not available",
                destination: dst || "Not available"
            };
        }

        function getCurrentSnapshot() {
            return state.history[state.snapshotIndex] || { planes: [], count: 0, time_utc: "" };
        }

        function getCurrentPlanes() {
            const snapshot = getCurrentSnapshot();
            return Array.isArray(snapshot.planes) ? snapshot.planes.filter(p => p && numberOrNull(p.lat) !== null && numberOrNull(p.lon) !== null) : [];
        }

        function buildUniqueSeen() {
            const seen = new Set();
            for (const snapshot of state.history) {
                const planes = Array.isArray(snapshot.planes) ? snapshot.planes : [];
                for (const plane of planes) {
                    seen.add(planeKey(plane));
                }
            }
            return seen.size;
        }

        function averageAltitude(planes) {
            const values = planes.map(p => numberOrNull(p.alt)).filter(v => v !== null);
            if (!values.length) return "0";
            return fmtNumber(values.reduce((a, b) => a + b, 0) / values.length);
        }

        function fastestSpeed(planes) {
            const values = planes.map(p => numberOrNull(p.speed)).filter(v => v !== null);
            if (!values.length) return "0";
            return fmtNumber(Math.max(...values));
        }

        function filterPlanes(planes) {
            const q = state.search.trim().toLowerCase();
            return planes.filter(plane => {
                const haystack = [
                    plane.callsign,
                    plane.flight,
                    plane.type,
                    plane.reg,
                    plane.airline,
                    plane.country,
                    plane.id,
                    getRoute(plane).departure,
                    getRoute(plane).destination
                ].map(v => safe(v, "")).join(" ").toLowerCase();

                const matchesSearch = !q || haystack.includes(q);
                const matchesAirline = !state.airline || safe(plane.airline, "") === state.airline;
                const matchesCountry = !state.country || safe(plane.country, "") === state.country;

                return matchesSearch && matchesAirline && matchesCountry;
            });
        }

        function uniqueOptions(field) {
            const values = new Set();
            for (const snapshot of state.history) {
                const planes = Array.isArray(snapshot.planes) ? snapshot.planes : [];
                for (const plane of planes) {
                    const v = safeRaw(plane[field]);
                    if (v) values.add(v);
                }
            }
            return [...values].sort((a, b) => a.localeCompare(b));
        }

        function fillSelect(select, values, label) {
            select.innerHTML = "";
            const defaultOption = document.createElement("option");
            defaultOption.value = "";
            defaultOption.textContent = label;
            select.appendChild(defaultOption);

            for (const value of values) {
                const option = document.createElement("option");
                option.value = value;
                option.textContent = value;
                select.appendChild(option);
            }
        }

        function syncFiltersToUI() {
            elements.searchInput.value = state.search;
            elements.mobileSearchInput.value = state.search;
            elements.airlineFilter.value = state.airline;
            elements.mobileAirlineFilter.value = state.airline;
            elements.countryFilter.value = state.country;
            elements.mobileCountryFilter.value = state.country;
        }

        function setFiltersFromUI(source) {
            if (source === "mobile") {
                state.search = elements.mobileSearchInput.value;
                state.airline = elements.mobileAirlineFilter.value;
                state.country = elements.mobileCountryFilter.value;
            } else {
                state.search = elements.searchInput.value;
                state.airline = elements.airlineFilter.value;
                state.country = elements.countryFilter.value;
            }
            syncFiltersToUI();
            render();
        }

        function statTile(icon, value, label, extraClass = "") {
            return `
                <div class="stat-tile ${extraClass}">
                    <div class="stat-icon"><i class="${icon}"></i></div>
                    <div class="stat-value">${value}</div>
                    <div class="stat-label">${label}</div>
                </div>
            `;
        }

        function renderStats(planes) {
            const snapshot = getCurrentSnapshot();
            const html = [
                statTile("fa-solid fa-plane", planes.length, "Aircraft now", "green"),
                statTile("fa-solid fa-fingerprint", buildUniqueSeen(), "Unique in history", "gold"),
                statTile("fa-solid fa-gauge-high", fastestSpeed(planes), "Top speed kt", "orange"),
                statTile("fa-solid fa-arrow-up-long", averageAltitude(planes), "Avg altitude ft")
            ].join("");

            elements.desktopStats.innerHTML = html;
            elements.mobileStats.innerHTML = html;

            const hasTraffic = planes.length > 0;
            elements.trafficPulse.classList.toggle("no-traffic", !hasTraffic);
            elements.trafficStatus.textContent = hasTraffic ? "Traffic detected" : "No aircraft visible";
            elements.lastUpdateText.textContent = formatShortTime(snapshot.time_utc);
        }

        function popupHTML(plane) {
            const route = getRoute(plane);
            return `
                <div class="popup-title">
                    <i class="fa-solid fa-plane-up"></i>
                    <span>${safe(plane.callsign, "No callsign")}</span>
                </div>
                <div class="popup-grid">
                    <b>Flight</b><span>${safe(plane.flight)}</span>
                    <b>Airline</b><span>${safe(plane.airline)}</span>
                    <b>Country</b><span>${safe(plane.country)}</span>
                    <b>Reg / Type</b><span>${safe(plane.reg)} / ${safe(plane.type)}</span>
                    <b>Route</b><span>${route.departure} → ${route.destination}</span>
                    <b>Altitude</b><span>${fmtNumber(plane.alt)} ft</span>
                    <b>Speed</b><span>${fmtNumber(plane.speed)} kt</span>
                    <b>Track</b><span>${fmtNumber(plane.track)}°</span>
                </div>
            `;
        }

        function markerIcon(plane, selected) {
            const heading = numberOrNull(plane.track) || 0;
            return L.divIcon({
                className: `plane-marker ${selected ? "selected" : ""}`,
                html: `<div class="plane-marker-inner" style="--heading:${heading}deg"><i class="fa-solid fa-plane"></i></div>`,
                iconSize: [34, 34],
                iconAnchor: [17, 17]
            });
        }

        function drawTrackForPlane(key) {
            trackLayer.clearLayers();
            if (!key) return;

            const points = [];
            for (const snapshot of state.history) {
                const planes = Array.isArray(snapshot.planes) ? snapshot.planes : [];
                const plane = planes.find(p => planeKey(p) === key);
                if (!plane) continue;
                const lat = numberOrNull(plane.lat);
                const lon = numberOrNull(plane.lon);
                if (lat !== null && lon !== null) points.push([lat, lon]);
            }

            if (points.length >= 2) {
                L.polyline(points, {
                    color: "#ffd166",
                    weight: 3,
                    opacity: 0.86,
                    lineCap: "round",
                    lineJoin: "round"
                }).addTo(trackLayer);
            }
        }

        function renderMarkers(planes) {
            markerLayer.clearLayers();
            state.markers.clear();

            const bounds = [];

            for (const plane of planes) {
                const lat = numberOrNull(plane.lat);
                const lon = numberOrNull(plane.lon);
                if (lat === null || lon === null) continue;

                const key = planeKey(plane);
                const marker = L.marker([lat, lon], {
                    icon: markerIcon(plane, key === state.selectedPlaneKey),
                    riseOnHover: true
                });

                marker.bindPopup(popupHTML(plane), {
                    closeButton: true,
                    autoPan: true,
                    maxWidth: 320
                });

                marker.on("click", () => {
                    state.selectedPlaneKey = key;
                    drawTrackForPlane(key);
                    render();
                    marker.openPopup();
                });

                marker.addTo(markerLayer);
                state.markers.set(key, marker);
                bounds.push([lat, lon]);
            }

            if (state.selectedPlaneKey) {
                drawTrackForPlane(state.selectedPlaneKey);
            } else {
                trackLayer.clearLayers();
            }

            if (bounds.length && bounds.length < 8) {
                map.fitBounds(bounds, {
                    paddingTopLeft: [460, 120],
                    paddingBottomRight: [70, 110],
                    maxZoom: 7,
                    animate: true
                });
            } else if (bounds.length >= 8) {
                map.fitBounds(bounds, {
                    paddingTopLeft: [460, 120],
                    paddingBottomRight: [70, 110],
                    maxZoom: 6,
                    animate: true
                });
            }
        }

        function planeCardHTML(plane, active) {
            const route = getRoute(plane);
            return `
                <article class="plane-card ${active ? "active" : ""}" data-plane-key="${planeKey(plane)}">
                    <div class="plane-card-main">
                        <div class="plane-title">
                            <strong>${safe(plane.callsign, "No callsign")} · ${safe(plane.reg, "No reg")}</strong>
                            <span>${safe(plane.airline)} · ${safe(plane.country)}</span>
                        </div>
                        <div class="plane-badge">${safe(plane.type, "TYPE")}</div>
                    </div>

                    <div class="plane-meta">
                        <div class="meta-item">
                            <small>Flight</small>
                            <span>${safe(plane.flight)}</span>
                        </div>
                        <div class="meta-item">
                            <small>Altitude</small>
                            <span>${fmtNumber(plane.alt)} ft</span>
                        </div>
                        <div class="meta-item">
                            <small>Speed</small>
                            <span>${fmtNumber(plane.speed)} kt</span>
                        </div>
                        <div class="meta-item">
                            <small>Track</small>
                            <span>${fmtNumber(plane.track)}°</span>
                        </div>
                        <div class="meta-item">
                            <small>From</small>
                            <span>${route.departure}</span>
                        </div>
                        <div class="meta-item">
                            <small>To</small>
                            <span>${route.destination}</span>
                        </div>
                    </div>
                </article>
            `;
        }

        function bindPlaneCards(container) {
            container.querySelectorAll(".plane-card").forEach(card => {
                card.addEventListener("click", () => {
                    const key = card.getAttribute("data-plane-key");
                    state.selectedPlaneKey = key;
                    drawTrackForPlane(key);
                    render();

                    const marker = state.markers.get(key);
                    if (marker) {
                        map.setView(marker.getLatLng(), Math.max(map.getZoom(), 7), { animate: true });
                        marker.openPopup();
                    }

                    if (window.innerWidth <= 760) {
                        elements.mobileSheet.classList.remove("open");
                    }
                });
            });
        }

        function renderLists(planes) {
            const filtered = filterPlanes(planes);
            elements.resultCount.textContent = filtered.length;

            if (!filtered.length) {
                const empty = `
                    <div class="empty-state">
                        <div>
                            <i class="fa-regular fa-radar"></i>
                            <strong>No aircraft match this view</strong>
                            <span>Try clearing filters or move the timeline to another snapshot.</span>
                        </div>
                    </div>
                `;
                elements.planeList.innerHTML = empty;
                elements.mobilePlaneList.innerHTML = empty;
                return;
            }

            const cards = filtered.map(plane => planeCardHTML(plane, planeKey(plane) === state.selectedPlaneKey)).join("");
            elements.planeList.innerHTML = cards;
            elements.mobilePlaneList.innerHTML = cards;
            bindPlaneCards(elements.planeList);
            bindPlaneCards(elements.mobilePlaneList);
        }

        function renderTimeline() {
            const snapshot = getCurrentSnapshot();
            elements.snapshotSlider.max = Math.max(0, state.history.length - 1);
            elements.snapshotSlider.value = state.snapshotIndex;
            elements.snapshotTimeChip.textContent = formatShortTime(snapshot.time_utc);
            elements.timelineRangeLabel.textContent = state.history.length > 1
                ? `${state.snapshotIndex + 1} / ${state.history.length}`
                : "Latest snapshot";

            const subtitle = `${formatDateTime(snapshot.time_utc)} · ${getCurrentPlanes().length} aircraft`;
            elements.snapshotSubtitle.textContent = subtitle;
            elements.mobileSubtitle.textContent = subtitle;
        }

        function render() {
            const planes = getCurrentPlanes();

            if (state.selectedPlaneKey && !planes.some(p => planeKey(p) === state.selectedPlaneKey)) {
                state.selectedPlaneKey = null;
                trackLayer.clearLayers();
            }

            renderStats(planes);
            renderMarkers(filterPlanes(planes));
            renderLists(planes);
            renderTimeline();
            syncFiltersToUI();
        }

        function initControls() {
            fillSelect(elements.airlineFilter, uniqueOptions("airline"), "All airlines");
            fillSelect(elements.mobileAirlineFilter, uniqueOptions("airline"), "All airlines");
            fillSelect(elements.countryFilter, uniqueOptions("country"), "All countries");
            fillSelect(elements.mobileCountryFilter, uniqueOptions("country"), "All countries");

            elements.searchInput.addEventListener("input", () => setFiltersFromUI("desktop"));
            elements.mobileSearchInput.addEventListener("input", () => setFiltersFromUI("mobile"));
            elements.airlineFilter.addEventListener("change", () => setFiltersFromUI("desktop"));
            elements.mobileAirlineFilter.addEventListener("change", () => setFiltersFromUI("mobile"));
            elements.countryFilter.addEventListener("change", () => setFiltersFromUI("desktop"));
            elements.mobileCountryFilter.addEventListener("change", () => setFiltersFromUI("mobile"));

            elements.resetFilters.addEventListener("click", () => {
                state.search = "";
                state.airline = "";
                state.country = "";
                state.selectedPlaneKey = null;
                trackLayer.clearLayers();
                render();
            });

            elements.snapshotSlider.addEventListener("input", () => {
                state.snapshotIndex = Number(elements.snapshotSlider.value);
                state.selectedPlaneKey = null;
                trackLayer.clearLayers();
                render();
            });

            elements.prevSnapshot.addEventListener("click", () => {
                state.snapshotIndex = Math.max(0, state.snapshotIndex - 1);
                state.selectedPlaneKey = null;
                trackLayer.clearLayers();
                render();
            });

            elements.nextSnapshot.addEventListener("click", () => {
                state.snapshotIndex = Math.min(state.history.length - 1, state.snapshotIndex + 1);
                state.selectedPlaneKey = null;
                trackLayer.clearLayers();
                render();
            });

            elements.mobileOpen.addEventListener("click", () => {
                elements.mobileSheet.classList.add("open");
            });

            elements.mobileClose.addEventListener("click", () => {
                elements.mobileSheet.classList.remove("open");
            });

            elements.mobileSheet.addEventListener("click", (event) => {
                if (event.target.classList.contains("sheet-handle")) {
                    elements.mobileSheet.classList.toggle("open");
                }
            });

            window.addEventListener("keydown", (event) => {
                if (event.key === "Escape") {
                    state.selectedPlaneKey = null;
                    elements.mobileSheet.classList.remove("open");
                    trackLayer.clearLayers();
                    render();
                }
            });
        }

        function initEmptyStateIfNeeded() {
            if (state.history.length) return;

            elements.desktopStats.innerHTML = [
                statTile("fa-solid fa-plane", "0", "Aircraft now", "green"),
                statTile("fa-solid fa-fingerprint", "0", "Unique in history", "gold"),
                statTile("fa-solid fa-gauge-high", "0", "Top speed kt", "orange"),
                statTile("fa-solid fa-arrow-up-long", "0", "Avg altitude ft")
            ].join("");
            elements.mobileStats.innerHTML = elements.desktopStats.innerHTML;

            const empty = `
                <div class="empty-state">
                    <div>
                        <i class="fa-solid fa-satellite"></i>
                        <strong>No aircraft history available</strong>
                        <span>The dashboard will populate after the next successful bot run.</span>
                    </div>
                </div>
            `;
            elements.planeList.innerHTML = empty;
            elements.mobilePlaneList.innerHTML = empty;
            elements.resultCount.textContent = "0";
            elements.snapshotSubtitle.textContent = "No data loaded";
            elements.mobileSubtitle.textContent = "No data loaded";
            elements.lastUpdateText.textContent = "No data";
            elements.trafficStatus.textContent = "Waiting for data";
            elements.trafficPulse.classList.add("no-traffic");
        }

        initControls();

        if (state.history.length) {
            render();
        } else {
            initEmptyStateIfNeeded();
        }

        setTimeout(() => {
            map.invalidateSize();
        }, 250);
    </script>
</body>
</html>
"""
    html = html.replace("__PLANE_HISTORY_JSON__", json_data_string)
    with open("planes.html", "w", encoding="utf-8") as f:
        f.write(html)


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
    
    plane_state = load_json(PLANE_STATE_FILE, {"previous_count": -1, "airspace_status": "CLOSED"})
    # Ensure airspace_status exists in older state files
    if "airspace_status" not in plane_state:
        plane_state["airspace_status"] = "CLOSED"

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
    
    current_status = plane_state.get("airspace_status", "CLOSED")
    
    if current_status == "CLOSED":
        if current_count >= 3:
            # send_telegram(f"✅ **AIRSPACE UPDATE:** Planes are being seen in Iranian Airspace again. (Count: {current_count})")
            if current_count >= 10:
                plane_state["airspace_status"] = "OPEN"
            else:
                plane_state["airspace_status"] = "WARNING"

    elif current_status == "WARNING":
        if current_count >= 10:
            plane_state["airspace_status"] = "OPEN"
        elif current_count == 0:
            plane_state["airspace_status"] = "CLOSED"
            # send_telegram("🚨 **CRITICAL WARNING:** Iranian Airspace is completely CLEARED. Current commercial planes detected inside the OIIX FIR boundary: 0.")

    elif current_status == "OPEN":
        if current_count == 0:
            plane_state["airspace_status"] = "CLOSED"
            # send_telegram("🚨 **CRITICAL WARNING:** Iranian Airspace is completely CLEARED. Current commercial planes detected inside the OIIX FIR boundary: 0.")
        elif current_count < 10:
            plane_state["airspace_status"] = "WARNING"
            # send_telegram(f"⚠️ **AIRSPACE ALERT:** The number of planes has dropped below 10 (Currently {current_count}). This might be a sign of airspace clearing.")
            
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
