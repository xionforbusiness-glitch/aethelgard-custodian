import json
import urllib.request

ICAO_DAMASCUS = "OSDI"

def fetch_damascus_airport_metar():
    """Fetches official METAR aviation weather for Damascus International Airport (OSDI)."""
    url = f"https://aviationweather.gov/api/data/metar?ids={ICAO_DAMASCUS}&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": "AethelgardAirportRadar/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and isinstance(data, list) and len(data) > 0:
                return data[0]
    except Exception as e:
        print(f"⚠ Airport Radar METAR notice: {e}")
    return None

def format_damascus_flight_briefing():
    """Decodes OSDI METAR into a concise operational status report for airport duty."""
    metar = fetch_damascus_airport_metar()
    if not metar:
        return "✈️ *Damascus Airport (OSDI):* Live radar offline / standard operations presumed."

    flt_cat = metar.get("fltCat", "VFR")
    wdir = metar.get("wdir", 0)
    wspd = metar.get("wspd", 0)
    vis = metar.get("visib", "10+")
    temp = metar.get("temp", "--")
    dewp = metar.get("dewp", "--")
    raw = metar.get("rawOb", "")

    # Flight Category Status
    cat_emoji = "🟢" if flt_cat == "VFR" else ("🟡" if flt_cat == "MVFR" else "🔴")
    status_text = "Visual Operations (Normal)" if flt_cat == "VFR" else f"Instrument / Reduced Visibility ({flt_cat})"

    # Fog / Low Visibility Hazard Check
    hazard_warning = ""
    try:
        if isinstance(vis, (int, float)) and vis < 3:
            hazard_warning = "\n⚠️ *ADVISORY:* Low visibility / fog reported on Damascus runways. Ground handling delays expected."
        elif isinstance(temp, (int, float)) and isinstance(dewp, (int, float)) and (temp - dewp) <= 1:
            hazard_warning = "\n🌫️ *ADVISORY:* High humidity & near-zero dew point spread. Early morning ground fog risk."
    except Exception:
        pass

    briefing = (
        f"✈️ *Damascus Intl Airport (OSDI) Radar:*\n"
        f"• Status: {cat_emoji} *{flt_cat}* — {status_text}\n"
        f"• Wind: `{wdir}° at {wspd} knots` | Visibility: `{vis} statute mi / 9999m`\n"
        f"• Surface: Temp `{temp}°C` / Dewpoint `{dewp}°C`\n"
        f"• METAR: `{raw}`"
        f"{hazard_warning}"
    )
    return briefing
