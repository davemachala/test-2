from flask import Flask, render_template_string
import requests
import csv
import io
from datetime import datetime, timedelta

app = Flask(__name__)

# --- NASTAVENÍ ---
TRIP_ID_LIKE = "-CZTRAINT-EC-221" 
CILOVA_STANICE_ID = "-SR70ST-333120" 
NAZEV_CILE = ""

ZNAME_STANICE = {
    "-SR70ST-333120": "Červenka",
    "33605": "Olomouc hl.n.",
    "-SR70ST-34534": "Olomouc hl.n.",
}

def prelozit_id(stanice_id):
    return ZNAME_STANICE.get(stanice_id, f"Stanice {stanice_id}")

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

        info = {"nazev": "IC 521", "zpozdeni": 0, "aktualni_stanice_nazev": "Na startu", "cilova_stanice": NAZEV_CILE, "ocekavany_prijezd": "?", "posledni_cas": ""}
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

        if posledni_projeta_id:
            info['aktualni_stanice_nazev'] = "V cíli!" if posledni_projeta_id == CILOVA_STANICE_ID else prelozit_id(posledni_projeta_id)
            
        if not nasel_cil: info['ocekavany_prijezd'] = "Cíl nenalezen"
        return info

    except Exception as e: return {"error": str(e)}

HTML = """
<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kdy budu na Července?</title>
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
        .location { font-weight: bold; color: #333; }
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
            <div class="label">Přijedu asi v:</div>
            <div class="big-time">{{ data.ocekavany_prijezd }}</div>
            <div class="label">{{ data.cilova_stanice }}</div>
            <div>
                <span class="badge {% if data.zpozdeni > 5 %}red{% else %}green{% endif %}">
                    {% if data.zpozdeni <= 0 %}Jede včas 👍{% else %}Zpoždění {{ data.zpozdeni }} min ⚠️{% endif %}
                </span>
            </div>
            <div class="footer">
                📍 Aktuálně: <span class="location">{{ data.aktualni_stanice_nazev }}</span><br>
                <span style="font-size:0.8em; color:#aaa;">(Poslední info: {{ data.posledni_cas }})</span>
            </div>
        {% endif %}
    </div>
    <script>setTimeout(function(){ window.location.reload(1); }, 60000);</script>
</body>
</html>
"""

@app.route('/')
def home():
    data = ziskej_data_jrutil()
    return render_template_string(HTML, data=data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
