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
NAZEV_CILE = "Olomouc hl.n." # Defaultní název
SOUBOR_DATA = "data.csv"
DOBA_JIZDY_Z_UNICOVA = 40 # minut (cesta + parkování)

# Globální slovník pro stanice
STANICE_DB = {}

def nacti_stanice_z_csv():
    """Načte data ze souboru Číselník SR70."""
    global STANICE_DB, NAZEV_CILE
    print(f"Načítám data ze souboru: {SOUBOR_DATA}...")
    
    if not os.path.exists(SOUBOR_DATA):
        print("⚠️ POZOR: Soubor s daty nebyl nalezen! Aplikace pojede v omezeném režimu.")
        return

    try:
        with open(SOUBOR_DATA, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ev_cislo = row.get('Evidenční číslo')
                if ev_cislo:
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
        
        # Aktualizace názvu cíle, pokud ho známe
        cil_cislo = CILOVA_STANICE_ID.replace("-SR70ST-", "")
        if cil_cislo in STANICE_DB:
            NAZEV_CILE = STANICE_DB[cil_cislo]['nazev']
            
        print(f"✅ Načteno {len(STANICE_DB)} stanic.")
        
    except Exception as e:
        print(f"❌ Chyba při načítání CSV: {e}")

def ziskej_info_o_stanici(stanice_id):
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
            "cilova_lat": None,  # Pro Waze
            "cilova_lon": None,  # Pro Waze
            "ocekavany_prijezd": "?", 
            "posledni_cas": "",
            "progress_percent": 0, # Pro progress bar
            "cas_odjezdu_auta": "?",
            "minuty_do_startu": 999
        }
        
        # 1. Zjistíme GPS cíle pro Waze
        cil_data = ziskej_info_o_stanici(CILOVA_STANICE_ID)
        info['cilova_lat'] = cil_data['lat']
        info['cilova_lon'] = cil_data['lon']

        posledni_projeta_id = None
        nasel_cil = False
        
        # Pro výpočet progress baru
        total_stops = len(rows)
        passed_stops = 0

        for idx, row in enumerate(rows):
            stop_id = row.get('stopid')
            real = row.get('arrivedat') or row.get('departedat')
            sched = row.get('shouldarriveat') or row.get('shoulddepartat')

            # Pokud má stanice reálný čas, vlak jí projel/je v ní
            if real and real.strip():
                posledni_projeta_id = stop_id
                passed_stops = idx + 1 # +1 protože index začíná od 0
                info['posledni_cas'] = real.split(" ")[1][:5]
                
                # Výpočet zpoždění
                if sched:
                    try:
                        diff = datetime.strptime(real, "%Y-%m-%d %H:%M:%S") - datetime.strptime(sched, "%Y-%m-%d %H:%M:%S")
                        info['zpozdeni'] = round(diff.total_seconds() / 60)
                    except: pass

            # Hledáme cílovou stanici pro výpočet příjezdu
            if stop_id == CILOVA_STANICE_ID:
                nasel_cil = True
                if row.get('shouldarriveat'):
                    try:
                        dt_prijezd = datetime.strptime(row.get('shouldarriveat'), "%Y-%m-%d %H:%M:%S") + timedelta(minutes=info['zpozdeni'])
                        info['ocekavany_prijezd'] = dt_prijezd.strftime("%H:%M")
                        
                        # --- NOVÉ: Výpočet odjezdu auta ---
                        dt_odjezd_auta = dt_prijezd - timedelta(minutes=DOBA_JIZDY_Z_UNICOVA)
                        info['cas_odjezdu_auta'] = dt_odjezd_auta.strftime("%H:%M")
                        
                        # Kolik minut zbývá do odjezdu auta?
                        minuty_do_startu = (dt_odjezd_auta - datetime.now()).total_seconds() / 60
                        info['minuty_do_startu'] = int(minuty_do_startu)
                        
                    except: pass

        # Výpočet procent pro progress bar
        if total_stops > 1:
            # Jednoduchý výpočet podle počtu projetých zastávek
            info['progress_percent'] = int((passed_stops / total_stops) * 100)
            # Oříznutí na 100% max
            if info['progress_percent'] > 100: info['progress_percent'] = 100

        # Doplnění názvu a polohy poslední stanice
        if posledni_projeta_id:
            stanice_data = ziskej_info_o_stanici(posledni_projeta_id)
            if posledni_projeta_id == CILOVA_STANICE_ID:
                info['aktualni_stanice_nazev'] = "V cíli! (" + stanice_data['nazev'] + ")"
                info['progress_percent'] = 100
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
        .card { background: white; max-width: 420px; margin: 20px auto; padding: 25px; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); }
        h1 { margin-top: 0; font-size: 1.3em; color: #666; letter-spacing: 1px; }
        .big-time { font-size: 4em; font-weight: 800; color: #2c3e50; margin: 5px 0; line-height: 1; }
        .label { text-transform: uppercase; font-size: 0.75em; letter-spacing: 2px; color: #95a5a6; margin-top: 8px; font-weight: 600; }
        .badge { display: inline-block; margin-top: 10px; padding: 6px 14px; border-radius: 50px; font-weight: bold; color: white; font-size: 0.9em; }
        .green { background: #27ae60; }
        .red { background: #e74c3c; animation: pulse 2s infinite; }
        
        /* Progress Bar */
        .progress-container { background: #ecf0f1; border-radius: 10px; height: 10px; width: 100%; margin: 20px 0; overflow: hidden; position: relative; }
        .progress-bar { height: 100%; background: linear-gradient(90deg, #3498db, #2980b9); transition: width 1s ease-in-out; }
        
        /* Sekce pro řidiče */
        .driver-box { background: #fff3cd; border: 2px solid #ffeeba; border-radius: 15px; padding: 15px; margin-top: 25px; }
        .driver-time { font-size: 2.2em; font-weight: 800; color: #856404; }
        
        /* Tlačítka */
        .btn { display: inline-block; margin-top: 10px; padding: 10px 20px; border-radius: 8px; text-decoration: none; font-weight: bold; width: 80%; }
        .btn-waze { background: #33ccff; color: white; border-bottom: 4px solid #0099cc; }
        .btn-waze:active { border-bottom: 0; margin-top: 14px; }
        .btn-map { background: #ecf0f1; color: #333; font-size: 0.9em; width: auto; padding: 5px 10px; margin-top: 5px; }

        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="card">
        {% if data.error %}
            <h1 style="color:#e74c3c">⚠️ Chyba</h1>
            <p>{{ data.error }}</p>
        {% else %}
            <h1>🚄 {{ data.nazev }}</h1>
            
            <div class="label">Cílová stanice</div>
            <div style="font-weight: bold; font-size: 1.1em; margin-bottom: 10px;">{{ data.cilova_stanice }}</div>

            <div class="progress-container">
                <div class="progress-bar" style="width: {{ data.progress_percent }}%;"></div>
            </div>
            <div style="font-size: 0.8em; color: #7f8c8d; margin-top: -15px; margin-bottom: 15px;">
                Cesta z {{ data.progress_percent }} % hotová
            </div>

            <div class="label">Očekávaný příjezd</div>
            <div class="big-time">{{ data.ocekavany_prijezd }}</div>
            
            <div>
                <span class="badge {% if data.zpozdeni > 5 %}red{% else %}green{% endif %}">
                    {% if data.zpozdeni <= 0 %}Jede včas 👍{% else %}Zpoždění {{ data.zpozdeni }} min ⚠️{% endif %}
                </span>
            </div>

            {% if data.cas_odjezdu_auta != "?" %}
            <div class="driver-box">
                <div class="label" style="color: #856404;">🚗 Kdy vyrazit z Uničova</div>
                <div class="driver-time">{{ data.cas_odjezdu_auta }}</div>
                
                {% if data.minuty_do_startu < 0 %}
                    <div style="color: #c0392b; font-weight: bold; font-size: 0.9em;">🚨 UŽ JSTE MĚLI VYJET!</div>
                {% elif data.minuty_do_startu < 15 %}
                    <div style="color: #d35400; font-weight: bold; font-size: 0.9em;">🔑 Hledejte klíče!</div>
                {% else %}
                    <div style="color: #27ae60; font-weight: bold; font-size: 0.9em;">☕ Máte čas (ještě {{ data.minuty_do_startu }} min)</div>
                {% endif %}

                {% if data.cilova_lat %}
                    <a href="https://waze.com/ul?ll={{ data.cilova_lat }},{{ data.cilova_lon }}&navigate=yes" target="_blank" class="btn btn-waze">
                        📍 Navigovat na nádraží
                    </a>
                {% endif %}
            </div>
            {% endif %}

            <div style="margin-top: 30px; border-top: 1px solid #eee; padding-top: 15px;">
                <div class="label">Aktuální poloha vlaku</div>
                <span style="font-weight: bold; font-size: 1.1em; display:block;">{{ data.aktualni_stanice_nazev }}</span>
                <span style="font-size:0.8em; color:#aaa;">(Čas: {{ data.posledni_cas }})</span>
                
                {% if data.aktualni_lat %}
                    <br>
                    <a href="http://maps.google.com/maps?q={{ data.aktualni_lat }},{{ data.aktualni_lon }}" target="_blank" class="btn btn-map">
                        🗺️ Ukázat na mapě
                    </a>
                {% endif %}
            </div>
        {% endif %}
    </div>
    <script>setTimeout(function(){ window.location.reload(1); }, 60000);</script>
</body>
</html>
"""

# Načtení dat při startu
nacti_stanice_z_csv()

@app.route('/')
def home():
    data = ziskej_data_jrutil()
    return render_template_string(HTML, data=data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
