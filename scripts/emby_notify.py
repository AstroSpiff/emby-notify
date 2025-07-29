import os
import json
import re
import requests
from datetime import datetime, timedelta
from dateutil import tz

# --- Parametri da env ---
EMBY_SERVER_URL = os.environ['EMBY_SERVER_URL'].rstrip('/')
EMBY_API_KEY    = os.environ['EMBY_API_KEY']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID   = os.environ['TELEGRAM_CHAT_ID']
TMDB_API_KEY       = os.environ['TMDB_API_KEY']

CACHE_FILE = 'data/cache.json'

# --- Helpers ---

def parse_emby_date(dt_str):
    """Converti '2024-12-13T07:00:05.0000000' in datetime UTC."""
    # rimuovi 'Z' finale
    s = dt_str.rstrip('Z')
    # tronca microsecondi a max 6 cifre
    if '.' in s:
        main, frac = s.split('.', 1)
        frac = re.match(r'(\d{1,6})', frac).group(1)
        s = f"{main}.{frac}"
    return datetime.fromisoformat(s)

def get_movie_info_tmdb(title):
    """Prende locandina e trama da TMDb."""
    search = requests.get(
        'https://api.themoviedb.org/3/search/movie',
        params={'api_key': TMDB_API_KEY, 'query': title}
    ).json().get('results', [])
    if not search:
        return None, None
    movie = search[0]
    details = requests.get(
        f"https://api.themoviedb.org/3/movie/{movie['id']}",
        params={'api_key': TMDB_API_KEY}
    ).json()
    poster = details.get('poster_path')
    if poster:
        poster = f"https://image.tmdb.org/t/p/w500{poster}"
    return poster, details.get('overview')

def send_telegram(text, photo_url=None):
    """Invia testo (e foto opzionale) via Bot API con requests."""
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    if photo_url:
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'caption': text,
            'parse_mode': 'Markdown'
        }
        requests.post(base + 'sendPhoto', data=data, params={'photo': photo_url})
    else:
        json_payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': 'Markdown'
        }
        requests.post(base + 'sendMessage', json=json_payload)

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_cache(items):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

# --- Main ---

def process():
    # Carica cache e lista correntemente su Emby
    old_items = load_cache()
    resp = requests.get(f"{EMBY_SERVER_URL}/emby/Items", params={
        'api_key': EMBY_API_KEY,
        'Recursive': True,
        'IncludeItemTypes': 'Movie',
        'Fields': 'MediaSources,DateCreated'
    }).json()
    all_items = resp.get('Items', [])

    now = datetime.utcnow()
    cutoff = now - timedelta(days=1)

    # costruisci mappa titolo->set(risoluzioni)
    def build_map(items):
        d = {}
        for i in items:
            title = i.get('Name')
            dt = parse_emby_date(i['DateCreated'])
            # estrai risoluzione: primo VideoStream disponibile
            vs = i.get('MediaSources', [{}])[0].get('VideoStreams', [])
            if vs:
                res = f"{vs[0].get('Height')}p"
            else:
                res = 'Unknown'
            d.setdefault(title, set()).add((res, dt))
        return d

    old_map = build_map(old_items)
    new_map = build_map(all_items)

    # notifiche
    for title, infos in new_map.items():
        # filtra solo le versioni create <24h
        recent = {res for res, dt in infos if dt >= cutoff}
        if not recent:
            continue

        old_res = {r for r, _ in old_map.get(title, set())}
        if title not in old_map:
            # FILM NUOVO
            poster, plot = get_movie_info_tmdb(title)
            txt = f"*Nuovo film:* _{title}_\nRisoluzioni: {', '.join(sorted(recent))}"
            if plot:
                txt += f"\n\n{plot}"
            send_telegram(txt, photo_url=poster)
        else:
            # AGGIORNAMENTO (nuove risoluzioni)
            added = recent - old_res
            if added:
                poster, _ = get_movie_info_tmdb(title)
                txt = f"*Aggiornato:* _{title}_\nNuove risoluzioni: {', '.join(sorted(added))}"
                send_telegram(txt, photo_url=poster)

    # salva l'intera lista per il prossimo giro
    save_cache(all_items)

if __name__ == '__main__':
    process()
