import json
import os
from datetime import datetime, timezone, timedelta

def fix_time_string(t_str):
    if not isinstance(t_str, str): 
        return t_str
    if "Tehran Time" in t_str:
        try:
            clean_str = t_str.replace(" Tehran Time", "").strip()
            dt = datetime.strptime(clean_str, "%Y/%m/%d %H:%M:%S")
            dt_utc = dt - timedelta(hours=3, minutes=30)
            return dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
        except ValueError:
            pass
    return t_str

def process_dict(d):
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, str):
                d[k] = fix_time_string(v)
            elif isinstance(v, (dict, list)):
                process_dict(v)
    elif isinstance(d, list):
        for i in range(len(d)):
            if isinstance(d[i], str):
                d[i] = fix_time_string(d[i])
            elif isinstance(d[i], (dict, list)):
                process_dict(d[i])

def run_fixer():
    files_to_fix = [
        "run_history.json", "state.json",
        "active_notams_raw.json", "active_notams_decoded.json", "active_notams_ai_decoded.json",
        "expired_notams_raw.json", "expired_notams_decoded.json", "expired_notams_ai_decoded.json",
        "plane_history.json", "plane_archive.json"
    ]

    for file in files_to_fix:
        if os.path.exists(file):
            with open(file, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    continue
            
            process_dict(data)
            
            with open(file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"Successfully fixed time formatting in {file}")

if __name__ == "__main__":
    run_fixer()
