import requests
import json
import os
import sys
import time
import subprocess
import tempfile
import re
import urllib.parse
from collections import deque
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
    global ACTIVE_KEYS
    if not ACTIVE_KEYS:
        return None
        
    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
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
                
                if response.status_code == 404:
                    continue
                    
                if response.status_code == 429:
                    print("Key rate limited. Ejecting from rotation...")
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
                print(f"Error with {model}: {e}")
                if "429" in str(e):
                    key_failed_429 = True
                break
                
        if key_failed_429:
            ACTIVE_KEYS.pop()
            
    time.sleep(3)
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
        process = subprocess.run(['node', '-e', js_code, temp_path_in, temp_path_out], capture_output=True, text=True)
        with open(temp_path_out, 'r', encoding='utf-8') as f:
            output_data = f.read()
        if not output_data.strip():
            return {"error": "Empty output", "stdout": process.stdout.strip(), "stderr": process.stderr.strip()}
        return json.loads(output_data)
    except Exception as e:
        return {"error": f"PYTHON CRASH: {str(e)}"}
    finally:
        if os.path.exists(temp_path_in): os.remove(temp_path_in)
        if os.path.exists(temp_path_out): os.remove(temp_path_out)

def get_all_notams():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    all_notams = []
    offset = 0
    batch_size = 30
    while True:
        payload = {"searchType": 0, "designatorsForLocation": "OIIX", "offset": offset, "notamsOnly": False, "radius": 10}
        try:
            response = requests.post(URL, data=payload, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            if not data or "notamList" not in data: break
            current_batch = data["notamList"]
            if not current_batch: break
            all_notams.extend(current_batch)
            offset += len(current_batch)
            if len(current_batch) < batch_size: break
            time.sleep(1)
        except Exception as e:
            print(f"Error fetching page at offset {offset}: {e}")
            break
    return all_notams

def load_json(filepath, default_value):
    if not os.path.exists(filepath): return default_value
    with open(filepath, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return default_value

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

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
            if "ago" in rel:
                valid_from_str = f"{dt_teh.strftime('%Y/%m/%d %H:%M')} Tehran Time (Started {rel})"
            else:
                valid_from_str = f"{dt_teh.strftime('%Y/%m/%d %H:%M')} Tehran Time ({status} {rel.replace('in ', '')})"

    if c_match:
        val_c = c_match.group(1)
        if val_c == "PERM":
            valid_to_str = "Permanent"
        else:
            dt_utc, dt_teh = parse_and_convert_time(val_c)
            if dt_utc:
                rel = get_relative_string(dt_utc)
                status = "Expired" if (dt_utc < datetime.now(timezone.utc)) else "Expires in"
                est_tag = " (Estimated)" if "EST" in c_match.group(2) else ""
                if "ago" in rel:
                    valid_to_str = f"{dt_teh.strftime('%Y/%m/%d %H:%M')} Tehran Time (Expired {rel}){est_tag}"
                else:
                    valid_to_str = f"{dt_teh.strftime('%Y/%m/%d %H:%M')} Tehran Time ({status} {rel.replace('in ', '')}){est_tag}"

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
            
            has_map = False
            if area and isinstance(area, list) and len(area) > 2:
                has_map = True
            elif coords and isinstance(coords, list) and len(coords) == 2 and isinstance(coords[0], list):
                has_map = True
            elif coords and isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
                has_map = True
                
            if has_map:
                map_links.append(f"🗺️ [Click to View Area on Map](https://raw.githack.com/freddishio/oiix-notam-watcher/main/index.html#{notam_id})")

    if "Unknown" in subject_text or "Unknown" in condition_text:
        q_match = re.search(r'Q\)\s*[A-Z]{4}/Q([A-Z]{2})([A-Z]{2})', raw_text)
        if q_match:
            sub_code = q_match.group(1)
            mod_code = q_match.group(2)
            if "Unknown" in subject_text: subject_text = FALLBACK_SUBJECTS.get(sub_code, f"Code {sub_code}")
            if "Unknown" in condition_text: condition_text = FALLBACK_CONDITIONS.get(mod_code, f"Code {mod_code}")

    subject_text = re.sub(r'\s*\(.*?\)', '', subject_text).strip()
    condition_text = re.sub(r'\s*\(.*?\)', '', condition_text).strip()

    return notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links

def generate_map_html(decoded_dict, ai_dict, raw_dict):
    features_js = "var markers = {};\n"
    for full_id, data in decoded_dict.items():
        if "error" in data: continue
        
        raw_text = raw_dict.get(full_id, {}).get("icaoMessage", "")
        ai_data = ai_dict.get(full_id, {})
        
        qual = data.get("qualification", {})
        coords = qual.get("coordinates")
        content = data.get("content", {})
        area = content.get("area")
        notam_id_only = full_id.split()[-1]
        
        subject = qual.get('code', {}).get('subject', 'Unknown Subject').replace("'", "\\'")
        
        lvl = ai_data.get("highest_level", "Third Level")
        if not lvl or "Third" in lvl:
            color = "#00ff00"
            lvl_str = "3️⃣ Third"
        elif "Second" in lvl:
            color = "#ffa500"
            lvl_str = "2️⃣ Second"
        elif "First" in lvl:
            color = "#ff0000"
            lvl_str = "1️⃣ First"
        else:
            color = "#00ff00"
            lvl_str = "⏳ Pending"
            
        b_match = re.search(r'B\)\s*(\d{10})', raw_text)
        c_match = re.search(r'C\)\s*(\d{10}|PERM)(.*?)(\n|D\)|E\)|F\)|G\))', raw_text)
        valid_from_str = "Unknown"
        valid_to_str = "Unknown"
        if b_match:
            dt_utc, dt_teh = parse_and_convert_time(b_match.group(1))
            if dt_teh: valid_from_str = dt_teh.strftime('%Y/%m/%d %H:%M')
        if c_match:
            val_c = c_match.group(1)
            if val_c == "PERM":
                valid_to_str = "Permanent"
            else:
                dt_utc, dt_teh = parse_and_convert_time(val_c)
                if dt_teh: valid_to_str = dt_teh.strftime('%Y/%m/%d %H:%M')
                
        popup_text = f"<b>NOTAM Number:</b> {notam_id_only}<br>"
        popup_text += f"<b>NOTAM Subject:</b> {subject}<br>"
        popup_text += f"<b>Importance level:</b> {lvl_str}<br>"
        popup_text += f"<b>NOTAM time:</b> {valid_from_str} to {valid_to_str}"
        
        if area and isinstance(area, list) and len(area) > 2:
            js_coords = [[pt[0], pt[1]] for pt in area if isinstance(pt, list) and len(pt) == 2]
            if js_coords: features_js += f"markers['{notam_id_only}'] = L.polygon({js_coords}, {{color: '{color}', weight: 2, fillOpacity: 0.3}}).addTo(map).bindPopup('{popup_text}');\n"
        elif coords and isinstance(coords, list) and len(coords) == 2 and isinstance(coords[0], list):
            lat, lng = coords[0][0], coords[0][1]
            rad_meters = coords[1].get("radius", 0) * 1852
            if rad_meters: features_js += f"markers['{notam_id_only}'] = L.circle([{lat}, {lng}], {{color: '{color}', radius: {rad_meters}, weight: 2, fillOpacity: 0.3}}).addTo(map).bindPopup('{popup_text}');\n"
        elif coords and isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
            lat, lng = coords[0], coords[1]
            features_js += f"markers['{notam_id_only}'] = L.circleMarker([{lat}, {lng}], {{color: '{color}', radius: 8, weight: 2, fillOpacity: 0.8}}).addTo(map).bindPopup('{popup_text}');\n"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>OIIX NOTAM Live Map</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{ padding: 0; margin: 0; font-family: Arial, sans-serif; }}
        #map {{ height: 100vh; width: 100vw; }}
        #error-modal {{
            display: none;
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: white;
            padding: 30px;
            border: 3px solid #ff3333;
            border-radius: 12px;
            z-index: 9999;
            text-align: center;
            box-shadow: 0px 0px 20px rgba(0,0,0,0.5);
            font-size: 17px;
            width: 80%;
            max-width: 400px;
        }}
        #error-modal img {{
            max-width: 100%;
            height: auto;
            max-height: 200px;
            border-radius: 8px;
            margin-bottom: 15px;
        }}
        #error-modal button {{
            margin-top: 20px;
            padding: 8px 20px;
            font-size: 16px;
            cursor: pointer;
            background: #333;
            color: white;
            border: none;
            border-radius: 5px;
        }}
    </style>
</head>
<body>
    <div id="error-modal">
        <img src="Panda.gif" alt="Waiting Panda">
        <br>
        ❌ The region for NOTAM <span id="error-notam" style="font-weight:bold;"></span> is not loaded yet ❌<br><br>
        🖥️ The server takes a few minutes to update the map 🖥️<br><br>
        <b>⌛ Please try refreshing the page after 3 minutes ⌛</b><br>
        <button onclick="document.getElementById('error-modal').style.display='none'">Close</button>
    </div>
    <div id="map"></div>
    <script>
        var googleStreets = L.tileLayer('https://{{s}}.google.com/vt/lyrs=m&x={{x}}&y={{y}}&z={{z}}',{{maxZoom: 20, subdomains:['mt0','mt1','mt2','mt3'], attribution: 'Map data © Google'}});
        var googleHybrid = L.tileLayer('https://{{s}}.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}',{{maxZoom: 20, subdomains:['mt0','mt1','mt2','mt3'], attribution: 'Map data © Google'}});
        var googleSat = L.tileLayer('https://{{s}}.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}',{{maxZoom: 20, subdomains:['mt0','mt1','mt2','mt3'], attribution: 'Map data © Google'}});
        var googleTerrain = L.tileLayer('https://{{s}}.google.com/vt/lyrs=p&x={{x}}&y={{y}}&z={{z}}',{{maxZoom: 20, subdomains:['mt0','mt1','mt2','mt3'], attribution: 'Map data © Google'}});
        
        var map = L.map('map', {{
            center: [32.4279, 53.6880],
            zoom: 5,
            zoomSnap: 0.5,
            layers: [googleStreets]
        }});
        
        var baseMaps = {{
            "Standard Map": googleStreets,
            "Satellite": googleSat,
            "Hybrid (Satellite + Borders/Roads)": googleHybrid,
            "Terrain": googleTerrain
        }};
        
        var firLayer = L.geoJSON(null, {{
            style: function(feature) {{
                var isIran = false;
                if (feature.properties) {{
                    var id = feature.properties.id || "";
                    var name = feature.properties.FIRname || "";
                    if (id === "OIIX" || name.indexOf("Tehran") !== -1) {{
                        isIran = true;
                    }}
                }}
                return {{
                    color: "#0055ff", 
                    weight: 2, 
                    fillOpacity: isIran ? 0.15 : 0.0
                }};
            }},
            onEachFeature: function(feature, layer) {{
                if (feature.properties && feature.properties.id) {{
                    layer.bindPopup("<b>FIR Airspace:</b> " + feature.properties.id);
                }}
            }}
        }});

        fetch('https://raw.githubusercontent.com/vatsimnetwork/vatspy-data-project/master/Boundaries.geojson')
            .then(res => {{
                if (!res.ok) throw new Error("Network response was not ok");
                return res.json();
            }})
            .then(data => firLayer.addData(data))
            .catch(err => console.error("Could not load FIR boundaries:", err));

        var overlayMaps = {{
            "Show Airspace Boundaries (FIR)": firLayer
        }};
        
        L.control.layers(baseMaps, overlayMaps).addTo(map);
        
        {features_js}
        
        setTimeout(function() {{
            var hash = window.location.hash.substring(1);
            if (hash) {{
                if (markers[hash]) {{
                    var layer = markers[hash];
                    if (layer.getBounds) {{
                        map.fitBounds(layer.getBounds(), {{padding: [150, 150], maxZoom: 6.5}});
                    }} else if (layer.getLatLng) {{
                        map.setView(layer.getLatLng(), 6.5);
                    }}
                    layer.openPopup();
                }} else {{
                    document.getElementById('error-notam').innerText = hash;
                    document.getElementById('error-modal').style.display = 'block';
                }}
            }}
        }}, 500);
    </script>
</body>
</html>"""
    with open("index.html", "w", encoding="utf-8") as f: f.write(html)

def format_telegram_message(notam_id, notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links, pyramid_levels, ai_explanation, raw_text, is_update=False):
    
    importance_str = "⏳ Pending"
    if "First" in pyramid_levels:
        importance_str = "1️⃣ First"
    elif "Second" in pyramid_levels:
        importance_str = "2️⃣ Second"
    elif "Third" in pyramid_levels:
        importance_str = "3️⃣ Third"

    msg_parts = []
    
    if is_update:
        msg_parts.append("⚠️ *This NOTAM is not new and has been sent before. The bot is sending it again because the AI explanation has now been provided.*")
        msg_parts.append(f"🔄 **AI UPDATE FOR NOTAM {notam_id}**")
    else:
        msg_parts.append(f"🚀 **TEHRAN FIR NOTAM ALERT (OIIX)**")
        
    msg_parts.extend([
        f"NOTAM Number: {notam_id} • {notam_type}",
        f"🚨 Importance level: {importance_str}",
        "------------------------------------",
        f"🏷️ Subject: {subject_text}",
        f"⚠️ Condition: {condition_text}",
        f"✈️ Traffic: {traffic_list}",
        "------------------------------------"
    ])
    
    if pyramid_levels == "Pending" or pyramid_levels == "⏳ Pending" or "⏳" in pyramid_levels:
        msg_parts.append("🤖 NOTAM Explanation (Internal Decoder Fallback):")
    else:
        msg_parts.append("🤖 NOTAM Explanation (Generated by AI):")
        
    msg_parts.append(f"{ai_explanation}")
    msg_parts.append("------------------------------------")
    msg_parts.append(f"📅 From: {valid_from_str}")
    msg_parts.append(f"📅 To: {valid_to_str}")
    
    if map_links and not is_update: 
        msg_parts.append("------------------------------------")
        for link in map_links:
            msg_parts.append(link)
        
    msg_parts.extend([
        "------------------------------------",
        "NOTAM Raw Text:",
        f"`{raw_text}`"
    ])
        
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
    new_ai_buffer = []

    for notam in notam_list:
        notam_id = notam.get("notamNumber")
        icao_id = notam.get("icaoId")
        if not notam_id: continue
        full_id = f"{icao_id} {notam_id}"
        notam["last_seen_utc"] = current_time_str
        current_raw_dict[full_id] = notam

        if full_id in active_notams_decoded and "error" not in active_notams_decoded[full_id]:
            current_decoded_dict[full_id] = active_notams_decoded[full_id]
        if full_id in active_notams_ai and "error" not in active_notams_ai[full_id]:
            current_ai_dict[full_id] = active_notams_ai[full_id]

    for buf_id in list(set(ai_buffer)):
        if buf_id in current_raw_dict and buf_id not in current_ai_dict:
            raw_text = current_raw_dict[buf_id].get("icaoMessage", "")
            notam_id = current_raw_dict[buf_id].get("notamNumber")
            
            print(f"Retrying AI for buffered NOTAM: {buf_id}")
            ai_data = get_ai_explanation(raw_text)
            
            if ai_data and "highest_level" in ai_data:
                ai_data["last_seen_utc"] = current_time_str
                current_ai_dict[buf_id] = ai_data
                
                lvl = ai_data.get("highest_level", "Third Level")
                pyramid_levels = lvl
                ai_explanation = ai_data.get("explanation", "")
                
                notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links = extract_notam_details(raw_text, current_decoded_dict.get(buf_id, {}), notam_id)
                
                msg = format_telegram_message(notam_id, notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links, pyramid_levels, ai_explanation, raw_text, is_update=True)
                send_telegram(msg)
            else:
                new_ai_buffer.append(buf_id)

    for full_id, notam in current_raw_dict.items():
        if full_id not in current_decoded_dict:
            raw_text = notam.get("icaoMessage", "")
            decoded_obj = decode_notam(raw_text)
            if decoded_obj:
                decoded_obj["last_seen_utc"] = current_time_str
                current_decoded_dict[full_id] = decoded_obj

        if full_id not in seen_ids:
            raw_text = notam.get("icaoMessage", "")
            notam_id = notam.get("notamNumber")
            internal_translation = translate_e_section(raw_text)
            
            print(f"Fetching AI for new NOTAM: {full_id}")
            ai_data = get_ai_explanation(raw_text)

            if ai_data and "highest_level" in ai_data:
                ai_data["last_seen_utc"] = current_time_str
                current_ai_dict[full_id] = ai_data
                
                lvl = ai_data.get("highest_level", "Third Level")
                pyramid_levels = lvl
                ai_explanation = ai_data.get("explanation", internal_translation)
            else:
                new_ai_buffer.append(full_id)
                pyramid_levels = "Pending"
                ai_explanation = f"{internal_translation}\n\n*(Will automatically update when AI is available)*"

            notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links = extract_notam_details(raw_text, current_decoded_dict.get(full_id, {}), notam_id)

            msg = format_telegram_message(notam_id, notam_type, valid_from_str, valid_to_str, subject_text, condition_text, traffic_list, map_links, pyramid_levels, ai_explanation, raw_text, is_update=False)
            send_telegram(msg)
            
            seen_ids[full_id] = current_time_str
            new_count += 1

    removed_count = 0
    newly_expired_raw = {}
    newly_expired_decoded = {}
    newly_expired_ai = {}
    
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

    save_json(STATE_FILE, new_state)
    save_json(AI_BUFFER_FILE, new_ai_buffer)
    save_json(ACTIVE_RAW_FILE, current_raw_dict)
    save_json(ACTIVE_DECODED_FILE, current_decoded_dict)
    save_json(ACTIVE_AI_FILE, current_ai_dict)
    save_json(EXPIRED_RAW_FILE, expired_notams_raw)
    save_json(EXPIRED_DECODED_FILE, expired_notams_decoded)
    save_json(EXPIRED_AI_FILE, expired_notams_ai)

    generate_map_html(current_decoded_dict, current_ai_dict, current_raw_dict)

    run_record = {
        "time_utc": current_time_str,
        "total_active": len(current_raw_dict),
        "new_added": new_count,
        "removed": removed_count,
        "buffered_ai": len(new_ai_buffer)
    }
    
    run_history.insert(0, run_record)
    save_json(HISTORY_FILE, run_history[:250])
    
    print(f"Stats: Total {len(current_raw_dict)}, New {new_count}, Removed {removed_count}, Buffered {len(new_ai_buffer)}")

if __name__ == "__main__":
    main()
