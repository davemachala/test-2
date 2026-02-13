from flask import Flask, render_template_string
import requests
import csv
import io
import os
from datetime import datetime, timedelta

app = Flask(__name__)

# --- NASTAVENÍ ---
TRIP_ID_LIKE = "-CZTRAINT-EC-221" 
CILOVA_STANICE_ID = "-SR70ST-333120" # Červenka
START_STANICE_NAZEV = "Praha hl.n."  # Výchozí stanice pro 0 % na progress baru
SOUBOR_DATA = "data.csv"
DOBA_JIZDY_Z_UNICOVA = 7 # minut

# Globální proměnné
STANICE_DB = {} # { "343624": {"nazev": "Olomouc", "lat":..., "lon":...} }
NAZEV_CILE = "Červenka" 
START_STANICE_ID = None # Najdeme automaticky podle názvu

def nacti_stanice_z_csv():
    """Načte data a pokusí se najít ID pro Prahu (start) a Olomouc (cíl)."""
    global STANICE_DB, NAZEV_CILE, START_STANICE_ID
    print(f"Načítám data ze souboru: {SOUBOR_DATA}...")
    
    if not os.path.exists(SOUBOR_DATA):
        print("⚠️ POZOR: Soubor s daty nebyl nalezen!")
        return

    try:
        with open(SOUBOR_DATA, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ev_cislo = row.get('Evidenční číslo')
                nazev = row.get('Název')
                
                if ev_cislo:
                    try:
                        # Konverze GPS formátu: "N49,724°" -> 49.724
                        raw_lat = row.get('GPS N (DEG)', '').replace('N', '').replace('°', '').replace(',', '.')
                        raw_lon = row.get('GPS E (DEG)', '').replace('E', '').replace('°', '').replace(',', '.')
                        lat = float(raw_lat) if raw_lat else None
                        lon = float(raw_lon) if raw_lon else None
                    except ValueError:
                        lat, lon = None, None

                    STANICE_DB[ev_cislo] = {
                        "nazev": nazev,
                        "lat": lat,
                        "lon": lon
                    }
                    
                    # Hledáme ID pro startovní stanici (Praha hl.n.)
                    if nazev == START_STANICE_NAZEV:
                        START_STANICE_ID = ev_cislo

        # Aktualizace názvu cíle
        cil_cislo = CILOVA_STANICE_ID.replace("-SR70ST-", "")
        if cil_cislo in STANICE_DB:
            NAZEV_CILE = STANICE_DB[cil_cislo]['nazev']
            
        print(f"✅ Načteno {len(STANICE_DB)} stanic.")
        print(f"📍 Start: {START_STANICE_NAZEV} (ID: {START_STANICE_ID}) -> Cíl: {NAZEV_CILE}")
        
    except Exception as e:
        print(f"❌ Chyba při načítání CSV: {e}")

def ziskej_info_o_stanici(stanice_id):
    clean_id = stanice_id.replace("-SR70ST-", "")
    return STANICE_DB.get(clean_id, {"nazev": clean_id, "lat": None, "lon": None})

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
        if not rows: return {"error": "Vlak dnes zatím v datech není."}

        # Základní struktura odpovědi
        info = {
            "nazev": "Pendolino", 
            "zpozdeni": 0, 
            "aktualni_stanice_nazev": "Na startu",
            "aktualni_lat": None, "aktualni_lon": None,
            "cilova_stanice": NAZEV_CILE, 
            "cilova_lat": None, "cilova_lon": None,
            "ocekavany_prijezd": "?", 
            "posledni_cas": "",
            "progress_percent": 0, 
            "cas_odjezdu_auta": "?",
            "minuty_do_startu": 999
        }
        
        # GPS cíle pro Waze
        cil_data = ziskej_info_o_stanici(CILOVA_STANICE_ID)
        info['cilova_lat'] = cil_data['lat']
        info['cilova_lon'] = cil_data['lon']

        # --- LOGIKA PRŮCHODU TRASOU ---
        posledni_index = 0
        index_start = 0
        index_cil = len(rows) - 1
        nasel_cil = False
        
        # Hledáme indexy startu a cíle v poli zastávek
        for i, row in enumerate(rows):
            clean_stop_id = row.get('stopid', '').replace("-SR70ST-", "")
            
            # Kde je Praha?
            if clean_stop_id == START_STANICE_ID:
                index_start = i
            
            # Kde je Olomouc?
            if row.get('stopid') == CILOVA_STANICE_ID:
                index_cil = i
                nasel_cil = True
                
                # --- VÝPOČET PŘÍJEZDU A ODJEZDU AUTA ---
                # Zde používáme přesný datetime objekt, nelepíme stringy
                sched_str = row.get('shouldarriveat') # "2023-XX-XX HH:MM:SS"
                if sched_str:
                    try:
                        dt_plan = datetime.strptime(sched_str, "%Y-%m-%d %H:%M:%S")
                        
                        # Zjistíme aktuální zpoždění z poslední projeté stanice (níže v kódu se upřesní, ale default je 0)
                        # Pro přesnost projdeme řádky znovu a najdeme poslední real čas
                        aktualni_zpozdeni_min = 0
                        for r in rows:
                            real = r.get('arrivedat') or r.get('departedat')
                            sched = r.get('shouldarriveat') or r.get('shoulddepartat')
                            if real:
                                try:
                                    diff = datetime.strptime(real, "%Y-%m-%d %H:%M:%S") - datetime.strptime(sched, "%Y-%m-%d %H:%M:%S")
                                    aktualni_zpozdeni_min = round(diff.total_seconds() / 60)
                                except: pass
                        
                        info['zpozdeni'] = aktualni_zpozdeni_min
                        
                        # Predikce příjezdu
                        dt_real_prijezd = dt_plan + timedelta(minutes=aktualni_zpozdeni_min)
                        info['ocekavany_prijezd'] = dt_real_prijezd.strftime("%H:%M")
                        
                        # Kdy vyrazit z Uničova
                        dt_odjezd_auta = dt_real_prijezd - timedelta(minutes=DOBA_JIZDY_Z_UNICOVA)
                        info['cas_odjezdu_auta'] = dt_odjezd_auta.strftime("%H:%M")
                        
                        # Odpočet
                        nowbase = datetime.now()
                        now = nowbase + timedelta(hours=1)
                        info['minuty_do_startu'] = int((dt_odjezd_auta - now).total_seconds() / 60)
                        
                    except Exception as e:
                        print(f"Chyba výpočtu času: {e}")

            # Kde je vlak teď? (Poslední stanice, co má vyplněný reálný čas)
            real_time = row.get('arrivedat') or row.get('departedat')
            if real_time:
                posledni_index = i
                info['posledni_cas'] = real_time.split(" ")[1][:5]
                stop_id = row.get('stopid')
                
                # Data o aktuální stanici
                st_data = ziskej_info_o_stanici(stop_id)
                info['aktualni_stanice_nazev'] = st_data['nazev']
                info['aktualni_lat'] = st_data['lat']
                info['aktualni_lon'] = st_data['lon']
                
                if stop_id == CILOVA_STANICE_ID:
                    info['aktualni_stanice_nazev'] = f"V cíli! ({st_data['nazev']})"

        # --- VÝPOČET PROGRESS BARU ---
        # Počítáme jen úsek Praha (index_start) -> Olomouc (index_cil)
        delka_trasy = index_cil - index_start
        ujeto = posledni_index - index_start
        
        if delka_trasy > 0:
            pct = int((ujeto / delka_trasy) * 100)
        else:
            pct = 0
            
        # Ošetření mezí (aby to nešlo do mínusu nebo přes 100%)
        if pct < 0: pct = 0
        if pct > 100: pct = 100
        
        info['progress_percent'] = pct

        if not nasel_cil: 
            info['ocekavany_prijezd'] = "Cíl nenalezen"
            
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
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; text-align: center; background: #eef2f3; color: #333; padding: 10px; }
        .card { background: white; max-width: 450px; margin: 10px auto; padding: 25px; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); }
        h1 { margin-top: 0; font-size: 1.4em; color: #555; }
        
        .big-time { font-size: 3.8em; font-weight: 800; color: #2c3e50; line-height: 1; margin: 10px 0; }
        .label { text-transform: uppercase; font-size: 0.75em; letter-spacing: 1.5px; color: #95a5a6; font-weight: 700; margin-top: 15px; }
        
        /* Zpoždění badge */
        .badge { display: inline-block; padding: 5px 12px; border-radius: 12px; font-weight: bold; color: white; font-size: 0.9em; margin-bottom: 10px; }
        .green { background: #27ae60; }
        .orange { background: #f39c12; }
        .red { background: #c0392b; animation: pulse 2s infinite; }

        /* Progress Bar */
        .progress-wrapper { margin: 25px 0; }
        .progress-labels { display: flex; justify-content: space-between; font-size: 0.8em; color: #7f8c8d; margin-bottom: 5px; font-weight: 600; }
        .progress-container { background: #ecf0f1; border-radius: 10px; height: 12px; width: 100%; overflow: hidden; position: relative; }
        .progress-bar { height: 100%; background: linear-gradient(90deg, #3498db, #2980b9); width: 0%; transition: width 1s ease-in-out; }
        
        /* Sekce pro řidiče - zvýrazněná */
        .driver-box { background: #fff8e1; border: 2px solid #ffe082; border-radius: 16px; padding: 20px; margin-top: 25px; box-shadow: 0 4px 10px rgba(0,0,0,0.05); }
        .driver-time { font-size: 2.5em; font-weight: 900; color: #f57f17; line-height: 1; margin: 5px 0; }
        .driver-status { font-weight: bold; margin-top: 5px; }
        
        /* Tlačítka */
        .btn-waze { display: flex; align-items: center; justify-content: center; background: #33ccff; color: white; text-decoration: none; padding: 12px; border-radius: 50px; font-weight: bold; margin-top: 15px; box-shadow: 0 4px 15px rgba(51, 204, 255, 0.4); transition: transform 0.2s; }
        .btn-waze:hover { transform: scale(1.02); }
        .btn-waze img { height: 20px; margin-right: 10px; }
        
        .footer { margin-top: 25px; padding-top: 15px; border-top: 1px solid #f0f0f0; font-size: 0.9em; color: #7f8c8d; }
        .map-link { color: #3498db; text-decoration: none; font-weight: 600; }

        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.6; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="card">
        {% if data.error %}
            <h1 style="color:#c0392b">⚠️ Chyba dat</h1>
            <p>{{ data.error }}</p>
        {% else %}
            <h1>🚄 Valašský Expres EC 221</h1>
            
            <div class="progress-wrapper">
                <div class="progress-labels">
                    <span>Praha hl.n.</span>
                    <span>{{ data.progress_percent }} %</span>
                    <span>Červenka</span>
                </div>
                <div class="progress-container">
                    <div class="progress-bar" style="width: {{ data.progress_percent }}%;"></div>
                </div>
            </div>

            <div class="label">Očekávaný příjezd</div>
            <div class="big-time">{{ data.ocekavany_prijezd }}</div>
            
            <div>
                <span class="badge {% if data.zpozdeni > 10 %}red{% elif data.zpozdeni > 0 %}orange{% else %}green{% endif %}">
                    {% if data.zpozdeni <= 0 %}Včas 👍{% else %}Zpoždění {{ data.zpozdeni }} min{% endif %}
                </span>
            </div>

            {% if data.cas_odjezdu_auta != "?" %}
            <div class="driver-box">
                <div class="label" style="color: #f57f17; margin-top:0;">🚗 Odjezd z Uničova</div>
                <div class="driver-time">{{ data.cas_odjezdu_auta }}</div>
                
                {% if data.minuty_do_startu < 0 %}
                    <div class="driver-status" style="color: #c0392b;">🚨 POZDĚ! Měli jste vyjet před {{ data.minuty_do_startu|abs }} min</div>
                {% elif data.minuty_do_startu < 20 %}
                    <div class="driver-status" style="color: #e67e22;">🟠 Startujte, zbývá {{ data.minuty_do_startu }} min</div>
                {% else %}
                    <div class="driver-status" style="color: #27ae60;">🟢 Pohoda, máte {{ data.minuty_do_startu }} min čas</div>
                {% endif %}

                {% if data.cilova_lat %}
                    <a href="https://waze.com/ul?ll={{ data.cilova_lat }},{{ data.cilova_lon }}&navigate=yes" target="_blank" class="btn-waze">
                        📍 Spustit Waze navigaci
                    </a>
                {% endif %}
            </div>
            {% endif %}

            <div class="footer">
                <div class="label" style="margin-top:0">Aktuální poloha vlaku</div>
                <strong style="font-size: 1.1em; color: #333;">{{ data.aktualni_stanice_nazev }}</strong>
                <br>
                <span style="font-size: 0.85em;">(Naposledy viděn v {{ data.posledni_cas }})</span>
                
                {% if data.aktualni_lat %}
                    <br><br>
                    <a href="https://www.google.com/maps/search/?api=1&query={{ data.aktualni_lat }},{{ data.aktualni_lon }}" target="_blank" class="map-link">
                        🗺️ Ukázat vlak na mapě
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
