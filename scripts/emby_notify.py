import os
import json
import re
from datetime import datetime, timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─── CONFIG ────────────────────────────────────────────────────────────────────
EMBY_SERVER_URL    = os.environ['EMBY_SERVER_URL'].rstrip('/')
EMBY_API_KEY       = os.environ['EMBY_API_KEY']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID   = os.environ['TELEGRAM_CHAT_ID']
TMDB_API_KEY       = os.environ['TMDB_API_KEY']

CACHE_FILE = 'data/cache.json'

HEADERS = {'Accept': 'application/json'}
# timeout: (connect, read)
TIMEOUT_EMBY  = (5, 30)
TIMEOUT_OTHER = 10

# ─── SESSION CON RETRY ───────────────────────────────────────────────────────────
session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=0.3,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)
adapter = HTTPAdapter(max_retries=retries)
session.mount('http://', adapter)
session.mount('https://', adapter)

# ─── HELPERS ────────────────────────────────────────────────────────────────────

def parse_emby_date(dt_str):
    """Converti DateCreated Emby (7 decimali) in datetime."""
    s = dt_str.rstrip('Z')
    if '.' in s:
        main, frac = s.split('.', 1)
        # prendi fino a 6 cifre decimali, per ISO compatibile
        frac = re.match(r'(\d{1,6})', frac).group(1)
        s = f"{main}.{frac}"
    return datetime.fromisoformat(s)

def get_movie_info_tmdb(title):
    try:
        r = session.get(
            'https://api.themoviedb.org/3/search/movie',
            params={'api_key': TMDB_API_KEY, 'query': title},
            headers=HEADERS,
            timeout=TIMEOUT_OTHER
        )
        r.raise_for_status()
        results = r.json().get('results', [])
        if not results:
            return None, None
        movie = results[0]
        d = session.get(
            f"https://api.themoviedb.org/3/movie/{movie['id']}",
            params={'api_key': TMDB_API_KEY},
            headers=HEADERS,
            timeout=TIMEOUT_OTHER
        )
        d.raise_for_status()
        details = d.json()
        poster = details.get('poster_path')
        if poster:
            poster = f"https://image.tmdb.org/t/p/w500{poster}"
        return poster, details.get('overview')
    except Exception as e:
        print(f"⚠️ Errore TMDb per '{title}': {e}")
        return None, None

def send_telegram(text, photo_url=None):
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    try:
        if photo_url:
            data = {
                'chat_id': TELEGRAM_CHAT_ID,
                'caption': text,
                'parse_mode': 'Markdown'
            }
            resp = session.post(
                base + 'sendPhoto',
                params={'photo': photo_url},
                data=data,
                timeout=TIMEOUT_OTHER
            )
        else:
            payload = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': text,
                'parse_mode': 'Markdown'
            }
            resp = session.post(
                base + 'sendMessage',
                json=payload,
                timeout=TIMEOUT_OTHER
            )
        if not resp.ok:
            print(f"⚠️ Errore invio Telegram: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"⚠️ Exception invio Telegram: {e}")

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding='utf-8') as f:
            return json.load(f)
    return []

def save_cache(items):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

# ─── MAIN ───────────────────────────────────────────────────────────────────────

def process():
    old_items = load_cache()

    # 1) Chiamata Emby con retry+timeout
    try:
        resp = session.get(
            f"{EMBY_SERVER_URL}/emby/Items",
            params={
                'api_key': EMBY_API_KEY,
                'Recursive': True,
                'IncludeItemTypes': 'Movie',
                'Fields': 'MediaSources,DateCreated'
            },
            headers=HEADERS,
            timeout=TIMEOUT_EMBY
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"⚠️ Errore Emby: {e}")
        return   # esci “soft” senza fallire tutto il workflow

    all_items = data.get('Items', [])
    now = datetime.utcnow()
    cutoff = now - timedelta(days=1)

    # costruisci mappa titolo → set of (risol., datetime)
    def build_map(items):
        m = {}
        for i in items:
            title = i.get('Name')
            try:
                dt = parse_emby_date(i['DateCreated'])
            except Exception:
                continue
            vs = i.get('MediaSources', [{}])[0].get('VideoStreams', [])
            res = f"{vs[0].get('Height')}p" if vs else 'Unknown'
            m.setdefault(title, set()).add((res, dt))
        return m

    old_map = build_map(old_items)
    new_map = build_map(all_items)

    # ciclo sui film nuovi/aggiornati nelle ultime 24h
    for title, infos in new_map.items():
        recent = {r for (r, dt) in infos if dt >= cutoff}
        if not recent:
            continue

        old_res = {r for (r, _) in old_map.get(title, set())}
        if title not in old_map:
            # → nuovo film
            poster, plot = get_movie_info_tmdb(title)
            txt = f"*Nuovo film:* _{title}_\nRisoluzioni: {', '.join(sorted(recent))}"
            if plot:
                txt += f"\n\n{plot}"
            send_telegram(txt, photo_url=poster)
        else:
            # → film già presente, guardo risoluzioni nuove
            added = recent - old_res
            if added:
                poster, _ = get_movie_info_tmdb(title)
                txt = f"*Aggiornato:* _{title}_\nNuove risoluzioni: {', '.join(sorted(added))}"
                send_telegram(txt, photo_url=poster)

    save_cache(all_items)

if __name__ == '__main__':
    process()
