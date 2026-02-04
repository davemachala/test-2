from flask import Flask, render_template_string
import requests
import csv
import io
import os
from datetime import datetime, timedelta

app = Flask(__name__)

# --- NASTAVENÍ ---
TRIP_ID_LIKE = "-CZTRAINT-SC-507" 
CILOVA_STANICE_ID = "-SR70ST-343624" # Olomouc
NAZEV_CILE = "Olomouc hl.n." # Defaultní název, kdyby se nenačetl z CSV
SOUBOR_DATA = "data.csv" # Zde zadejte název vašeho souboru (přejmenujte ho nebo upravte toto)

# Globální slovník pro stanice { "343624": {"nazev": "Olomouc", "lat": 49.xxx, "lon": 17.xxx} }
STANICE_DB = {}

def nacti_stanice_z_csv():
    """Načte data ze souboru Číselník SR70."""
    global STANICE_DB, NAZEV_CILE
    print(f"Načítám data ze souboru: {SOUBOR_DATA}...")
    
    if not os.path.exists(SOUBOR_DATA):
        print("⚠️ POZOR: Soubor s daty nebyl nalezen! Aplikace pojede bez názvů stanic.")
        return

    try:
        with open(SOUBOR_DATA, mode='r', encoding='utf-8-sig') as f: # utf-8-sig ošetří BOM na začátku
            reader = csv.DictReader(f)
            for row in reader:
                # Klíč je Evidenční číslo (např. 343624)
                ev_cislo = row.get('Evidenční číslo')
                if ev_cislo:
                    # Zpracování GPS: "N49,724049°" -> 49.724049
                    try:
                        raw_lat = row.get('GPS N (DEG)', '').replace('N', '').replace('°', '').replace(',', '.')
                        raw_lon = row.get('GPS E (DEG)', '').replace('E', '').replace('°', '').replace(',', '.')
                        lat = float(raw_lat) if raw_lat else None
                        lon = float(raw_lon) if raw_lon else None
                    except ValueError:
                        lat, lon = None, None

                    STANICE_DB[ev_cislo] = {
                        "nazev": row.get('Název'),
                        "lat": lat,
                        "lon": lon
                    }
        
        # Pokud známe ID cíle, aktualizujeme jeho název z dat
        cil_cislo = CILOVA_STANICE_ID.replace("-SR70ST-", "")
        if cil_cislo in STANICE_DB:
            NAZEV_CILE = STANICE_DB[cil_cislo]['nazev']
            
        print(f"✅ Načteno {len(STANICE_DB)} stanic.")
        
    except Exception as e:
        print(f"❌ Chyba při načítání CSV: {e}")

def ziskej_info_o_stanici(stanice_id):
    """Vrátí dict s názvem a souřadnicemi pro dané ID (např. -SR70ST-343624)."""
    # Ořízneme prefix -SR70ST-, abychom dostali jen číslo
    clean_id = stanice_id.replace("-SR70ST-", "")
    
    if clean_id in STANICE_DB:
        return STANICE_DB[clean_id]
    else:
        return {"nazev": f"Stanice {clean_id}", "lat": None, "lon": None}

def ziskej_data_jrutil():
    dnes = datetime.now().strftime("%Y-%m-%d")
    url = "https://rt.jrutil.konarici.cz/api/stophistory"
    params = {"tripIdLike": TRIP_ID_LIKE, "fromDate": dnes, "toDate": dnes}
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200: return {"error": f"Server Error {r.status_code}"}
        
        f = io.StringIO(r.text)
        reader = csv.DictReader(f)
        rows = list(reader)
        if not rows: return {"error": "Vlak zatím nevyjel / data nejsou."}

        info = {
            "nazev": "Pendolino", 
            "zpozdeni": 0, 
            "aktualni_stanice_nazev": "Na startu",
            "aktualni_lat": None,
            "aktualni_lon": None,
            "cilova_stanice": NAZEV_CILE, 
            "ocekavany_prijezd": "?", 
            "posledni_cas": ""
        }
        
        posledni_projeta_id = None
        nasel_cil = False
        
        for row in rows:
            stop_id = row.get('stopid')
            real = row.get('arrivedat') or row.get('departedat')
            sched = row.get('shouldarriveat') or row.get('shoulddepartat')

            if real and real.strip():
                posledni_projeta_id = stop_id
                info['posledni_cas'] = real.split(" ")[1][:5]
                if sched:
                    try:
                        diff = datetime.strptime(real, "%Y-%m-%d %H:%M:%S") - datetime.strptime(sched, "%Y-%m-%d %H:%M:%S")
                        info['zpozdeni'] = round(diff.total_seconds() / 60)
                    except: pass

            if stop_id == CILOVA_STANICE_ID:
                nasel_cil = True
                if row.get('shouldarriveat'):
                    try:
                        dt = datetime.strptime(row.get('shouldarriveat'), "%Y-%m-%d %H:%M:%S") + timedelta(minutes=info['zpozdeni'])
                        info['ocekavany_prijezd'] = dt.strftime("%H:%M")
                    except: pass

        # Doplnění názvu a polohy poslední stanice
        if posledni_projeta_id:
            stanice_data = ziskej_info_o_stanici(posledni_projeta_id)
            
            if posledni_projeta_id == CILOVA_STANICE_ID:
                info['aktualni_stanice_nazev'] = "V cíli! (" + stanice_data['nazev'] + ")"
            else:
                info['aktualni_stanice_nazev'] = stanice_data['nazev']
            
            info['aktualni_lat'] = stanice_data['lat']
            info['aktualni_lon'] = stanice_data['lon']
            
        if not nasel_cil: info['ocekavany_prijezd'] = "Cíl nenalezen"
        return info

    except Exception as e: return {"error": str(e)}

HTML = """
<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kde je vlak?</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; text-align: center; background: #eef2f3; color: #333; padding: 20px; }
        .card { background: white; max-width: 400px; margin: 20px auto; padding: 30px; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); }
        h1 { margin-top: 0; font-size: 1.3em; color: #666; letter-spacing: 1px; }
        .big-time { font-size: 4.5em; font-weight: 800; color: #2c3e50; margin: 10px 0; line-height: 1; }
        .label { text-transform: uppercase; font-size: 0.8em; letter-spacing: 2px; color: #888; margin-top: 5px; }
        .badge { display: inline-block; margin-top: 15px; padding: 8px 16px; border-radius: 50px; font-weight: bold; color: white; font-size: 1.1em; }
        .green { background: #27ae60; box-shadow: 0 4px 15px rgba(39, 174, 96, 0.4); }
        .red { background: #e74c3c; box-shadow: 0 4px 15px rgba(231, 76, 60, 0.4); animation: pulse 2s infinite; }
        .footer { margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; color: #555; font-size: 0.9em; }
        .location { font-weight: bold; color: #333; font-size: 1.2em; display: block; margin-bottom: 5px;}
        .map-link { display: inline-block; margin-top: 5px; color: #3498db; text-decoration: none; font-weight: 600; border: 1px solid #3498db; padding: 5px 10px; border-radius: 5px; transition: 0.2s;}
        .map-link:hover { background: #3498db; color: white; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.8; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="card">
        {% if data.error %}
            <h1 style="color:#e74c3c">⚠️ Chyba</h1>
            <p>{{ data.error }}</p>
        {% else %}
            <h1>🚄 {{ data.nazev }}</h1>
            <div class="label">Očekávaný příjezd:</div>
            <div class="big-time">{{ data.ocekavany_prijezd }}</div>
            <div class="label">{{ data.cilova_stanice }}</div>
            <div>
                <span class="badge {% if data.zpozdeni > 5 %}red{% else %}green{% endif %}">
                    {% if data.zpozdeni <= 0 %}Jede včas 👍{% else %}Zpoždění {{ data.zpozdeni }} min ⚠️{% endif %}
                </span>
            </div>
            <div class="footer">
                <div class="label">Poslední známá poloha:</div>
                <span class="location">{{ data.aktualni_stanice_nazev }}</span>
                
                {% if data.aktualni_lat %}
                    <a href="https://www.google.com/maps/search/?api=1&query={{ data.aktualni_lat }},{{ data.aktualni_lon }}" target="_blank" class="map-link">
                        📍 Zobrazit na mapě
                    </a>
                {% endif %}
                
                <br><br>
                <span style="font-size:0.8em; color:#aaa;">(Čas průjezdu: {{ data.posledni_cas }})</span>
            </div>
        {% endif %}
    </div>
    <script>setTimeout(function(){ window.location.reload(1); }, 60000);</script>
</body>
</html>
"""

# Načtení dat při startu aplikace
nacti_stanice_z_csv()

@app.route('/')
def home():
    data = ziskej_data_jrutil()
    return render_template_string(HTML, data=data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
