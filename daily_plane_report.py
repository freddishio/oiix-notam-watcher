#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any
from urllib import parse, request

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))
PLANE_HISTORY_FILE = Path("plane_history.json")
REPORT_STATE_FILE = Path("daily_plane_report_state.json")
PLANES_URL = "https://freddishio.github.io/oiix-notam-watcher/planes.html"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

MAX_TELEGRAM_CHARS = 3900
MAX_ROUTE_LOOKUPS = int(os.environ.get("MAX_ROUTE_LOOKUPS", "40"))


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def parse_entry_time(entry: dict[str, Any]) -> datetime | None:
    ts = entry.get("timestamp")
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), timezone.utc)
        except Exception:
            pass

    raw = str(entry.get("time_utc") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y/%m/%d %H:%M:%S UTC"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def default_report_date(now_tehran: datetime) -> date:
    # GitHub scheduled workflows sometimes run a few minutes late.
    # If the run slips just after midnight Tehran time, report the day that just ended.
    if now_tehran.hour == 0 and now_tehran.minute <= 45:
        return (now_tehran - timedelta(days=1)).date()
    return now_tehran.date()


def parse_report_date(argv: list[str]) -> tuple[date, bool]:
    force = "--force" in argv or os.environ.get("FORCE_REPORT") == "1"
    explicit = os.environ.get("REPORT_DATE")

    cleaned = []
    for arg in argv[1:]:
        if arg == "--force":
            continue
        cleaned.append(arg)

    if cleaned:
        explicit = cleaned[0]

    if explicit:
        return datetime.strptime(explicit, "%Y-%m-%d").date(), force

    return default_report_date(datetime.now(TEHRAN_TZ)), force


def clean_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.lower() in {"unknown", "unknown flight", "unknown type", "unknown reg", "unknown airline", "unknown location", "n/a", "none", "null"}:
        return ""
    return text


def add_value(target: set[str], value: Any) -> None:
    cleaned = clean_value(value)
    if cleaned:
        target.add(cleaned)


def first_value(*values: Any) -> str:
    for value in values:
        cleaned = clean_value(value)
        if cleaned:
            return cleaned
    return ""


def get_nested(data: dict[str, Any], *path: str) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def route_from_plane_record(plane: dict[str, Any]) -> tuple[str, str]:
    departure = first_value(
        plane.get("departure"),
        plane.get("origin"),
        plane.get("from"),
        plane.get("from_airport"),
        plane.get("origin_airport"),
        get_nested(plane, "route", "departure"),
        get_nested(plane, "route", "origin"),
        get_nested(plane, "airport", "origin", "name"),
        get_nested(plane, "airport", "origin", "code", "iata"),
        get_nested(plane, "airport", "origin", "code", "icao"),
    )
    destination = first_value(
        plane.get("destination"),
        plane.get("dest"),
        plane.get("to"),
        plane.get("to_airport"),
        plane.get("destination_airport"),
        get_nested(plane, "route", "destination"),
        get_nested(plane, "route", "dest"),
        get_nested(plane, "airport", "destination", "name"),
        get_nested(plane, "airport", "destination", "code", "iata"),
        get_nested(plane, "airport", "destination", "code", "icao"),
    )
    return departure, destination


def aircraft_key(plane: dict[str, Any]) -> str:
    reg = clean_value(plane.get("reg"))
    if reg:
        return f"REG:{reg.upper()}"

    fr24_id = clean_value(plane.get("id"))
    if fr24_id:
        return f"FR24:{fr24_id}"

    callsign = clean_value(plane.get("callsign"))
    ac_type = clean_value(plane.get("type"))
    if callsign or ac_type:
        return f"CALLSIGN:{callsign}|TYPE:{ac_type}"

    return f"UNKNOWN:{json.dumps(plane, sort_keys=True)[:120]}"


def as_float(value: Any) -> float | None:
    try:
        if value in (None, "", "N/A"):
            return None
        return float(value)
    except Exception:
        return None


def fmt_num(value: float | None) -> str:
    if value is None:
        return "Unknown"
    if abs(value - round(value)) < 0.001:
        return str(int(round(value)))
    return f"{value:.1f}"


def fmt_values(values: set[str], fallback: str = "Unknown") -> str:
    if not values:
        return fallback
    return ", ".join(sorted(values))


def fmt_time(dt: datetime | None) -> str:
    if not dt:
        return "Unknown"
    return dt.astimezone(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M:%S Tehran")


def airport_label(block: Any) -> str:
    if not isinstance(block, dict):
        return ""

    code = block.get("code") if isinstance(block.get("code"), dict) else {}
    iata = clean_value(code.get("iata"))
    icao = clean_value(code.get("icao"))
    name = clean_value(block.get("name"))
    city = clean_value(get_nested(block, "position", "region", "city"))
    country = clean_value(get_nested(block, "position", "country", "name"))

    parts = []
    if iata or icao:
        parts.append("/".join([p for p in (iata, icao) if p]))
    if name:
        parts.append(name)
    elif city:
        parts.append(city)
    if country:
        parts.append(country)

    return " - ".join(parts)


def fetch_fr24_route(fr24_id: str) -> tuple[str, str]:
    if not fr24_id:
        return "", ""

    url = "https://data-live.flightradar24.com/clickhandler/?" + parse.urlencode({"flight": fr24_id})
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.flightradar24.com/",
        },
    )

    try:
        with request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return "", ""

    airport = data.get("airport", {})
    departure = airport_label(airport.get("origin"))
    destination = airport_label(airport.get("destination"))
    return departure, destination


def build_aircraft_summaries(history: list[dict[str, Any]], target_day: date) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}

    for entry in history:
        entry_dt = parse_entry_time(entry)
        if not entry_dt:
            continue

        if entry_dt.astimezone(TEHRAN_TZ).date() != target_day:
            continue

        planes = entry.get("planes", [])
        if not isinstance(planes, list):
            continue

        for plane in planes:
            if not isinstance(plane, dict):
                continue

            key = aircraft_key(plane)
            item = summaries.setdefault(
                key,
                {
                    "ids": set(),
                    "callsigns": set(),
                    "flights": set(),
                    "types": set(),
                    "regs": set(),
                    "airlines": set(),
                    "countries": set(),
                    "departures": set(),
                    "destinations": set(),
                    "observations": 0,
                    "first_seen": None,
                    "last_seen": None,
                    "first_position": None,
                    "last_position": None,
                    "latest_track": None,
                    "latest_alt": None,
                    "latest_speed": None,
                    "min_alt": None,
                    "max_alt": None,
                    "max_speed": None,
                },
            )

            add_value(item["ids"], plane.get("id"))
            add_value(item["callsigns"], plane.get("callsign"))
            add_value(item["flights"], plane.get("flight"))
            add_value(item["types"], plane.get("type"))
            add_value(item["regs"], plane.get("reg"))
            add_value(item["airlines"], plane.get("airline"))
            add_value(item["countries"], plane.get("country"))

            departure, destination = route_from_plane_record(plane)
            add_value(item["departures"], departure)
            add_value(item["destinations"], destination)

            item["observations"] += 1

            lat = as_float(plane.get("lat"))
            lon = as_float(plane.get("lon"))
            position = (lat, lon) if lat is not None and lon is not None else None

            if item["first_seen"] is None or entry_dt < item["first_seen"]:
                item["first_seen"] = entry_dt
                item["first_position"] = position

            if item["last_seen"] is None or entry_dt >= item["last_seen"]:
                item["last_seen"] = entry_dt
                item["last_position"] = position
                item["latest_track"] = plane.get("track")
                item["latest_alt"] = as_float(plane.get("alt"))
                item["latest_speed"] = as_float(plane.get("speed"))

            alt = as_float(plane.get("alt"))
            if alt is not None:
                item["min_alt"] = alt if item["min_alt"] is None else min(item["min_alt"], alt)
                item["max_alt"] = alt if item["max_alt"] is None else max(item["max_alt"], alt)

            speed = as_float(plane.get("speed"))
            if speed is not None:
                item["max_speed"] = speed if item["max_speed"] is None else max(item["max_speed"], speed)

    return summaries


def enrich_missing_routes(summaries: dict[str, dict[str, Any]]) -> None:
    lookups_done = 0

    ordered = sorted(
        summaries.values(),
        key=lambda item: item["first_seen"] or datetime.max.replace(tzinfo=timezone.utc),
    )

    for item in ordered:
        if item["departures"] and item["destinations"]:
            continue
        if lookups_done >= MAX_ROUTE_LOOKUPS:
            break

        ids = sorted(item["ids"])
        if not ids:
            continue

        departure, destination = fetch_fr24_route(ids[-1])
        lookups_done += 1

        add_value(item["departures"], departure)
        add_value(item["destinations"], destination)

        # Keep this gentle; Flightradar24 is not a stable official API.
        time.sleep(0.8)


def format_position(position: tuple[float | None, float | None] | None) -> str:
    if not position:
        return "Unknown"
    lat, lon = position
    if lat is None or lon is None:
        return "Unknown"
    return f"{lat:.4f}, {lon:.4f}"


def build_report_lines(target_day: date, summaries: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = [
        "🛩️ DAILY IRANIAN AIRSPACE AIRCRAFT REPORT",
        f"Date: {target_day.isoformat()} Tehran time",
        f"Individual aircraft seen: {len(summaries)}",
        f"Airspace map: {PLANES_URL}",
        "",
    ]

    if not summaries:
        lines.append("No aircraft were recorded inside the OIIX FIR boundary in plane_history.json for this Tehran date.")
        return lines

    sorted_items = sorted(
        summaries.values(),
        key=lambda item: (
            item["first_seen"] or datetime.max.replace(tzinfo=timezone.utc),
            fmt_values(item["regs"]),
            fmt_values(item["callsigns"]),
        ),
    )

    for index, item in enumerate(sorted_items, start=1):
        title_bits = [
            fmt_values(item["regs"], "No registration"),
            fmt_values(item["callsigns"], "No callsign"),
            fmt_values(item["types"], "Unknown type"),
        ]
        lines.append(f"{index}. {' | '.join(title_bits)}")
        lines.append(f"   Flight number(s): {fmt_values(item['flights'])}")
        lines.append(f"   Airline: {fmt_values(item['airlines'])}")
        lines.append(f"   Country of registration/operator: {fmt_values(item['countries'])}")
        lines.append(f"   Departure: {fmt_values(item['departures'], 'Unknown / not available in saved data')}")
        lines.append(f"   Destination: {fmt_values(item['destinations'], 'Unknown / not available in saved data')}")
        lines.append(f"   First seen: {fmt_time(item['first_seen'])}")
        lines.append(f"   Last seen: {fmt_time(item['last_seen'])}")
        lines.append(
            "   Altitude: "
            f"min {fmt_num(item['min_alt'])} ft, "
            f"max {fmt_num(item['max_alt'])} ft, "
            f"last {fmt_num(item['latest_alt'])} ft"
        )
        lines.append(
            "   Speed/track: "
            f"max {fmt_num(item['max_speed'])} kt, "
            f"last {fmt_num(item['latest_speed'])} kt, "
            f"track {clean_value(item['latest_track']) or 'Unknown'} deg"
        )
        lines.append(f"   Last position: {format_position(item['last_position'])}")
        lines.append(f"   Observations in history: {item['observations']}")
        lines.append("")

    return lines


def chunk_lines(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current = ""

    for line in lines:
        candidate = current + line + "\n"
        if len(candidate) > MAX_TELEGRAM_CHARS and current:
            chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current = candidate

    if current.strip():
        chunks.append(current.rstrip())

    return chunks


def send_telegram(text: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN and CHAT_ID are required.")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": False,
    }).encode("utf-8")

    req = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(req, timeout=20) as resp:
        resp.read()


def main() -> int:
    target_day, force = parse_report_date(sys.argv)
    state = load_json(REPORT_STATE_FILE, {})

    if state.get("last_sent_date") == target_day.isoformat() and not force:
        print(f"Daily plane report for {target_day.isoformat()} was already sent. Use --force to resend.")
        return 0

    history = load_json(PLANE_HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []

    summaries = build_aircraft_summaries(history, target_day)
    enrich_missing_routes(summaries)

    lines = build_report_lines(target_day, summaries)
    chunks = chunk_lines(lines)

    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"{chunk}\n\nPart {i}/{total}" for i, chunk in enumerate(chunks, start=1)]

    for chunk in chunks:
        send_telegram(chunk)
        time.sleep(1)

    state.update({
        "last_sent_date": target_day.isoformat(),
        "sent_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "aircraft_count": len(summaries),
        "message_parts": len(chunks),
    })
    save_json(REPORT_STATE_FILE, state)

    print(f"Sent daily plane report for {target_day.isoformat()} with {len(summaries)} individual aircraft.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
