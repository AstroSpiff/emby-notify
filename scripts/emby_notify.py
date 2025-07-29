import os
import json
import re
import sys
import requests
from datetime import datetime, timedelta
from dateutil import tz

# --- Config da env ---
EMBY_SERVER_URL    = os.environ['EMBY_SERVER_URL'].rstrip('/')
EMBY_API_KEY       = os.environ['EMBY_API_KEY']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID   = os.environ['TELEGRAM_CHAT_ID']
TMDB_API_KEY       = os.environ['TMDB_API_KEY']

CACHE_FILE = 'data/cache.json'
HEADERS = {'Accept': 'application/json'}
TIMEOUT = 10  # secondi

# --- Helpers ---

def parse_emby_date(dt_str):
    """Converti stringa Emby (7 decimali) in datetime."""
    s = dt_str.rstrip('Z')
    if '.' in s:
        main, frac = s.split('.', 1)
        frac = re.match(r'(\d{1,6})', frac).group(1)
        s = f"{main}.{frac}"
    return datetime.fromisoformat(s)

def get_movie_info_tmdb(title):
    try:
        r = requests.get(
            'https://api.themoviedb.org/3/search/movie',
            params={'api_key': TMDB_API_KEY, 'query': title},
            headers=HEADERS, timeout=TIMEOUT
        )
        r.raise_for_status()
        results = r.json().get('results', [])
        if not results:
            return None, None
        movie = results[0]
        d = requests.get(
            f"https://api.themoviedb.org/3/movie/{movie['id']}",
            params={'api_key': TMDB_API_KEY},
            headers=HEADERS, timeout=TIMEOUT
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
            data = {'chat_id': TELEGRAM_CHAT_ID,
                    'caption': text, 'parse_mode': 'Markdown'}
            resp = requests.post(
                base + 'sendPhoto',
                data=data,
                params={'photo': photo_url},
                timeout=TIMEOUT
            )
        else:
            payload = {'chat_id': TELEGRAM_CHAT_ID,
                       'text': text, 'parse_mode': 'Markdown'}
            resp = requests.post(
                base + 'sendMessage',
                json=payload,
                timeout=TIMEOUT
            )
        if not resp.ok:
            print(f"Errore invio Telegram: {resp.status_code} {resp.text}")
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

# --- Main process ---

def process():
    old_items = load_cache()

    # Chiamata Emby
    try:
        resp = requests.get(
            f"{EMBY_SERVER_URL}/emby/Items",
            params={
                'api_key': EMBY_API_KEY,
                'Recursive': True,
                'IncludeItemTypes': 'Movie',
                'Fields': 'MediaSources,DateCreated'
            },
            headers=HEADERS,
            timeout=TIMEOUT
        )
        if resp.status_code != 200:
            print(f"❌ Emby API status {resp.status_code}: {resp.text}")
            sys.exit(1)
        data = resp.json()
    except Exception as e:
        print(f"❌ Errore chiamata Emby: {e}")
        sys.exit(1)

    all_items = data.get('Items', [])
    now = datetime.utcnow()
    cutoff = now - timedelta(days=1)

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

    for title, infos in new_map.items():
        recent = {r for (r, dt) in infos if dt >= cutoff}
        if not recent:
            continue

        old_res = {r for (r, _) in old_map.get(title, set())}
        if title not in old_map:
            # nuovo film
            poster, plot = get_movie_info_tmdb(title)
            txt = f"*Nuovo film:* _{title}_\nRisoluzioni: {', '.join(sorted(recent))}"
            if plot:
                txt += f"\n\n{plot}"
            send_telegram(txt, photo_url=poster)
        else:
            # aggiornamento risoluzioni
            added = recent - old_res
            if added:
                poster, _ = get_movie_info_tmdb(title)
                txt = f"*Aggiornato:* _{title}_\nNuove risoluzioni: {', '.join(sorted(added))}"
                send_telegram(txt, photo_url=poster)

    save_cache(all_items)


if __name__ == '__main__':
    process()
