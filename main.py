import requests
import json
import os
import sys
import time
import subprocess
import tempfile
import re
import urllib.parse
from datetime import datetime, timezone, timedelta

URL = "https://notams.aim.faa.gov/notamSearch/search"
STATE_FILE = "state.json"
HISTORY_FILE = "run_history.json"
AI_BUFFER_FILE = "ai_buffer.json"

ACTIVE_RAW_FILE = "active_notams_raw.json"
ACTIVE_DECODED_FILE = "active_notams_decoded.json"
ACTIVE_AI_FILE = "active_notams_ai_decoded.json"

EXPIRED_RAW_FILE = "expired_notams_raw.json"
EXPIRED_DECODED_FILE = "expired_notams_decoded.json"
EXPIRED_AI_FILE = "expired_notams_ai_decoded.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("Error: Telegram secrets are missing.")
    sys.exit(1)

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

FALLBACK_SUBJECTS = {
    "AF": "Flight information region", "AR": "ATS route", "RD": "Danger area",
    "WM": "Missile, gun or rocket firing", "NV": "VOR", "OA": "Aeronautical information service",
    "ML": "Military operating area", "RT": "Temporary restricted area",
    "WE": "Exercises", "RO": "Overflying", "RM": "Terminal control area"
}

FALLBACK_CONDITIONS = {
    "XX": "Plain language", "CA": "Activated", "LC": "Closed", "CH": "Changed",
    "AS": "Unserviceable", "AH": "Hours of service are now", "CD": "Deactivated",
    "CN": "Cancelled", "CS": "Installed", "CT": "On test"
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
    if future:
        return f"in {time_string}"
    else:
        return f"{time_string} ago"

def translate_e_section(text):
    e_section = text
    if "E)" in text:
        parts = text.split("E)")
        raw_e = parts[1]
        if "F)" in raw_e:
            raw_e = raw_e.split("F)")[0]
        elif "G)" in raw_e:
            raw_e = raw_e.split("G)")[0]
        e_section = raw_e.strip()
    
    for abbr, full in ICAO_DICT.items():
        e_section = re.sub(rf'\b{abbr}\b', full, e_section)
        
    return e_section

def get_ai_explanation(raw_text):
    if not GEMINI_API_KEY:
        return None
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = f"""Read this aviation NOTAM:
{raw_text}

Task 1: Explain the NOTAM in very simple words for a general audience.
Task 2: Assign the highest category. Choices are ONLY these exact strings:
'First Level' (for complete airspace closure or major security events)
'Second Level' (for military exercises, gun fire, or restricted airspace)
'Third Level' (for routine aviation changes)

Return ONLY a valid JSON dictionary. No markdown, no code blocks. It must have exactly two keys: "explanation" and "highest_level".
"""
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.2
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        res_json = response.json()
        text = res_json['candidates'][0]['content']['parts'][0]['text']
        return json.loads(text.strip())
    except Exception as e:
        print(f"AI REST API Error: {e}")
        return None

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

def decode_notam(raw_text):
    fd_in, temp_path_in = tempfile.mkstemp(text=True)
    fd_out, temp_path_out = tempfile.mkstemp(text=True)
    
    os.close(fd_in)
    os.close(fd_out)
    
    try:
        with open(temp_path_in, 'w', encoding='utf-8') as f:
            f.write(raw_text)
            
        js_code = """
        const fs = require('fs');
        try {
            const notamDecoder = require('./notam-decoder.js');
            const raw = fs.readFileSync(process.argv[1], 'utf8');
            const decoded = notamDecoder.decode(raw);
            fs.writeFileSync(process.argv[2], JSON.stringify(decoded || {error: "Empty result"}), 'utf8');
        } catch(e) {
            fs.writeFileSync(process.argv[2], JSON.stringify({error: e.toString()}), 'utf8');
        }
        """
            
        process = subprocess.run(
            ['node', '-e', js_code, temp_path_in, temp_path_out], 
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
        return {"error": f"PYTHON CRASH: {str(e)}"}
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

def generate_map_html(decoded_dict):
    features_js = "var markers = {};\n"
    for full_id, data in decoded_dict.items():
        if "error" in data: continue
            
        qual = data.get("qualification", {})
        coords = qual.get("coordinates")
        content = data.get("content", {})
        area = content.get("area")
        
        notam_id_only = full_id.split()[-1]
        
        subject = qual.get('code', {}).get('subject', 'Unknown Subject')
        subject = subject.replace("'", "\\'")
        popup_text = f"<b>{notam_id_only}</b><br>{subject}"
        
        if area and isinstance(area, list) and len(area) > 2:
            js_coords = []
            for pt in area:
                if isinstance(pt, list) and len(pt) == 2:
                    js_coords.append([pt[0], pt[1]])
            if js_coords:
                features_js += f"markers['{notam_id_only}'] = L.polygon({js_coords}, {{color: '#ff0000', weight: 2, fillOpacity: 0.2}}).addTo(map).bindPopup('{popup_text}');\n"
                
        elif coords and isinstance(coords, list) and len(coords) == 2 and isinstance(coords[0], list):
            lat, lng = coords[0][0], coords[0][1]
            rad_nm = coords[1].get("radius", 0)
            if rad_nm:
                rad_meters = rad_nm * 1852
                features_js += f"markers['{notam_id_only}'] = L.circle([{lat}, {lng}], {{color: '#ff9900', radius: {rad_meters}, weight: 2}}).addTo(map).bindPopup('{popup_text}');\n"
                
        elif coords and isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
            lat, lng = coords[0], coords[1]
            features_js += f"markers['{notam_id_only}'] = L.marker([{lat}, {lng}]).addTo(map).bindPopup('{popup_text}');\n"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>OIIX NOTAM Live Map</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{ padding: 0; margin: 0; }}
        #map {{ height: 100vh; width: 100vw; }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var map = L.map('map').setView([32.4279, 53.6880], 5);
        L.tileLayer('http://{{s}}.google.com/vt/lyrs=m&x={{x}}&y={{y}}&z={{z}}',{{
            maxZoom: 20,
            subdomains:['mt0','mt1','mt2','mt3'],
            attribution: 'Map data © Google'
        }}).addTo(map);
        
        {features_js}
        
        setTimeout(function() {{
            var hash = window.location.hash.substring(1);
            if (hash && markers[hash]) {{
                var layer = markers[hash];
                if (layer.getBounds) {{
                    map.fitBounds(layer.getBounds(), {{padding: [50, 50]}});
                }} else {{
                    map.setView(layer.getLatLng(), 8);
                }}
                layer.openPopup();
            }}
        }}, 500);
    </script>
</body>
</html>"""
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

def format_telegram_message(notam_id, notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links, pyramid_levels, ai_explanation, raw_text, is_update=False):
    msg_parts = []
    if is_update:
        msg_parts.append(f"🔄 **AI UPDATE FOR NOTAM {notam_id}**")
    else:
        msg_parts.append(f"🚀 **TEHRAN FIR ALERT (OIIX)**\n`{notam_id}` • {notam_type}")
        
    msg_parts.extend([
        f"",
        f"📅 **From:** {valid_from_str}",
        f"📅 **To:** {valid_to_str}\n",
        f"🏷️ **Subject:** {subject_text}",
        f"⚠️ **Condition:** {condition_text}",
        f"✈️ **Traffic:** {traffic_list}\n"
    ])
    
    if map_links and not is_update: # To avoid map spam on updates
        msg_parts.extend(map_links)
        msg_parts.append("")
        
    msg_parts.append(f"📊 **Categories:** {pyramid_levels}")
    msg_parts.append(f"🤖 **AI Simple Explanation:**\n{ai_explanation}\n")
    
    if not is_update:
        msg_parts.append(f"**Raw Text:**\n`{raw_text}`")
        
    return "\n".join(msg_parts)

def main():
    print("Fetching FULL data from FAA AIM (OIIX Only)...")
    
    seen_ids = load_json(STATE_FILE, {})
    run_history = load_json(HISTORY_FILE, [])
    ai_buffer = load_json(AI_BUFFER_FILE, [])
    
    active_notams_raw = load_json(ACTIVE_RAW_FILE, {})
    active_notams_decoded = load_json(ACTIVE_DECODED_FILE, {})
    active_notams_ai = load_json(ACTIVE_AI_FILE, {})
    
    expired_notams_raw = load_json(EXPIRED_RAW_FILE, {})
    expired_notams_decoded = load_json(EXPIRED_DECODED_FILE, {})
    expired_notams_ai = load_json(EXPIRED_AI_FILE, {})
    
    notam_list = get_all_notams()
    
    if not notam_list:
        print("No valid data received. Skipping processing to protect state.")
        return

    current_time_str = datetime.utcnow().strftime('%Y/%m/%d %H:%M:%S')
    current_raw_dict = {}
    current_decoded_dict = {}
    current_ai_dict = {}
    new_count = 0
    
    # Track the ones that need an AI update later
    new_ai_buffer = []

    # 1. PROCESS ALL FRESH DATA AND SEND INITIAL MESSAGES
    for notam in notam_list:
        notam_id = notam.get("notamNumber")
        icao_id = notam.get("icaoId")
        raw_text = notam.get("icaoMessage") or "No text"

        if not notam_id: continue
            
        full_id = f"{icao_id} {notam_id}"
        
        notam["last_seen_utc"] = current_time_str
        current_raw_dict[full_id] = notam

        if full_id in active_notams_decoded and "error" not in active_notams_decoded[full_id]:
            decoded_obj = active_notams_decoded[full_id]
            decoded_obj["last_seen_utc"] = current_time_str
            current_decoded_dict[full_id] = decoded_obj
        else:
            decoded_obj = decode_notam(raw_text)
            if decoded_obj:
                decoded_obj["last_seen_utc"] = current_time_str
                current_decoded_dict[full_id] = decoded_obj

        # Load existing AI data if we have it
        if full_id in active_notams_ai and "error" not in active_notams_ai[full_id]:
            ai_data = active_notams_ai[full_id]
            ai_data["last_seen_utc"] = current_time_str
            current_ai_dict[full_id] = ai_data

        if full_id not in seen_ids:
            # THIS IS A BRAND NEW NOTAM
            ai_data = get_ai_explanation(raw_text)
            
            if ai_data and "highest_level" in ai_data:
                ai_data["last_seen_utc"] = current_time_str
                current_ai_dict[full_id] = ai_data
            else:
                # API failed or is busy. Push to buffer.
                new_ai_buffer.append(full_id)
            
            # Formatting Data (Time, maps, subjects, etc.)
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
                    status = "Starts" if (dt_utc > datetime.now(timezone.utc)) else "Started"
                    valid_from_str = f"{dt_teh.strftime('%Y/%m/%d %H:%M')} Tehran Time ({status} {rel})"

            if c_match:
                val_c = c_match.group(1)
                if val_c == "PERM":
                    valid_to_str = "Permanent"
                else:
                    dt_utc, dt_teh = parse_and_convert_time(val_c)
                    if dt_utc:
                        rel = get_relative_string(dt_utc)
                        status = "Expires" if (dt_utc > datetime.now(timezone.utc)) else "Expired"
                        est_tag = " (Estimated)" if "EST" in c_match.group(2) else ""
                        valid_to_str = f"{dt_teh.strftime('%Y/%m/%d %H:%M')} Tehran Time ({status} {rel}){est_tag}"

            if decoded_obj and "qualification" in decoded_obj:
                header = decoded_obj.get("header", {})
                if isinstance(header, dict):
                    notam_type = header.get("typeDesc", notam_type)
                    
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
                    
                    if area and isinstance(area, list) and len(area) > 2:
                        map_links.append(f"🗺️ [View Highlighted Region on Custom Map](https://raw.githack.com/freddishio/oiix-notam-watcher/main/index.html#{notam_id})")
                    elif coords and isinstance(coords, list) and len(coords) == 2 and isinstance(coords[0], list):
                        map_links.append(f"🗺️ [View Circular Area on Custom Map](https://raw.githack.com/freddishio/oiix-notam-watcher/main/index.html#{notam_id})")
                    elif coords and isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
                        lat = coords[0]
                        lng = coords[1]
                        map_links.append(f"📍 [View Pin on Google Maps](https://www.google.com/maps/place/{lat},{lng}/@{lat},{lng},6z)")
                        map_links.append(f"🗺️ [View Location on Custom Map](https://raw.githack.com/freddishio/oiix-notam-watcher/main/index.html#{notam_id})")

            if "Unknown" in subject_text or "Unknown" in condition_text:
                q_match = re.search(r'Q\)\s*[A-Z]{4}/Q([A-Z]{2})([A-Z]{2})', raw_text)
                if q_match:
                    sub_code = q_match.group(1)
                    mod_code = q_match.group(2)
                    if "Unknown" in subject_text:
                        subject_text = FALLBACK_SUBJECTS.get(sub_code, f"Code {sub_code}")
                    if "Unknown" in condition_text:
                        condition_text = FALLBACK_CONDITIONS.get(mod_code, f"Code {mod_code}")

            subject_text = re.sub(r'\s*\(.*?\)', '', subject_text).strip()
            condition_text = re.sub(r'\s*\(.*?\)', '', condition_text).strip()

            # Format the output based on AI success or failure
            if full_id in new_ai_buffer:
                pyramid_levels = "⏳ *Pending AI Analysis*"
                ai_explanation = "⏳ AI is currently unavailable. The system has saved this NOTAM in a buffer and will automatically notify you with the explanation and priority level once the AI becomes available."
            else:
                lvl = ai_data.get("highest_level", "Third Level")
                if "First" in lvl:
                    pyramid_levels = "First Level, Second Level, Third Level"
                elif "Second" in lvl:
                    pyramid_levels = "Second Level, Third Level"
                else:
                    pyramid_levels = "Third Level"
                
                ai_explanation = ai_data.get("explanation", translate_e_section(raw_text))

            msg = format_telegram_message(notam_id, notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links, pyramid_levels, ai_explanation, raw_text)
            
            send_telegram(msg)
            seen_ids[full_id] = current_time_str
            new_count += 1
            
            # Strict 10-second pause to prevent getting blocked by the AI
            time.sleep(10)

    # 2. PROCESS THE BUFFER FOR ANY MISSED AI ANALYSES
    for buf_id in ai_buffer:
        if buf_id in current_raw_dict and buf_id not in current_ai_dict:
            # It's still active and we still don't have AI info for it
            raw_text = current_raw_dict[buf_id].get("icaoMessage", "")
            notam_id = current_raw_dict[buf_id].get("notamNumber")
            
            print(f"Retrying AI for buffered NOTAM: {buf_id}")
            ai_data = get_ai_explanation(raw_text)
            
            if ai_data and "highest_level" in ai_data:
                ai_data["last_seen_utc"] = current_time_str
                current_ai_dict[buf_id] = ai_data
                
                lvl = ai_data.get("highest_level", "Third Level")
                if "First" in lvl:
                    pyramid_levels = "First Level, Second Level, Third Level"
                elif "Second" in lvl:
                    pyramid_levels = "Second Level, Third Level"
                else:
                    pyramid_levels = "Third Level"
                
                ai_explanation = ai_data.get("explanation", "")
                
                # Send the update to Telegram
                msg = format_telegram_message(notam_id, "", "", "", "", "", "", [], pyramid_levels, ai_explanation, raw_text, is_update=True)
                send_telegram(msg)
                
                time.sleep(10)
            else:
                # Failed again, put it back in the queue for next time
                new_ai_buffer.append(buf_id)
                time.sleep(10)

    # 3. CLEANUP EXPIRED ITEMS
    removed_count = 0
    for old_id, old_data in active_notams_raw.items():
        if old_id not in current_raw_dict:
            old_data["archived_utc"] = current_time_str
            newly_expired_raw[old_id] = old_data
            
            if old_id in active_notams_decoded:
                dec_data = active_notams_decoded[old_id]
                dec_data["archived_utc"] = current_time_str
                newly_expired_decoded[old_id] = dec_data
                
            if old_id in active_notams_ai:
                ai_ex_data = active_notams_ai[old_id]
                ai_ex_data["archived_utc"] = current_time_str
                newly_expired_ai[old_id] = ai_ex_data
                
            removed_count += 1
            
    expired_notams_raw = {**newly_expired_raw, **expired_notams_raw}
    expired_notams_decoded = {**newly_expired_decoded, **expired_notams_decoded}
    expired_notams_ai = {**newly_expired_ai, **expired_notams_ai}

    new_state = {}
    for cid in current_raw_dict.keys():
        new_state[cid] = seen_ids.get(cid, current_time_str)

    # Save all databases
    save_json(STATE_FILE, new_state)
    save_json(ACTIVE_RAW_FILE, current_raw_dict)
    save_json(ACTIVE_DECODED_FILE, current_decoded_dict)
    save_json(ACTIVE_AI_FILE, current_ai_dict)
    save_json(EXPIRED_RAW_FILE, expired_notams_raw)
    save_json(EXPIRED_DECODED_FILE, expired_notams_decoded)
    save_json(EXPIRED_AI_FILE, expired_notams_ai)
    
    # Save the new buffer queue
    save_json(AI_BUFFER_FILE, new_ai_buffer)

    generate_map_html(current_decoded_dict)

    run_record = {
        "time_utc": current_time_str,
        "total_active": len(current_raw_dict),
        "new_added": new_count,
        "removed": removed_count
    }
    
    run_history.insert(0, run_record)
    save_json(HISTORY_FILE, run_history[:250])
    
    print(f"Stats: Total {len(current_raw_dict)}, New {new_count}, Removed {removed_count}, Buffered {len(new_ai_buffer)}")

if __name__ == "__main__":
    main()
