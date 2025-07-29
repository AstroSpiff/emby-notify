#!/usr/bin/env python3
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
TRAKT_API_KEY      = os.environ['TRAKT_API_KEY']

CACHE_FILE = 'data/cache.json'
HEADERS    = {'Accept': 'application/json'}

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
    s = dt_str.rstrip('Z')
    if '.' in s:
        main, frac = s.split('.', 1)
        frac = re.match(r'(\d{1,6})', frac).group(1)
        s = f"{main}.{frac}"
    return datetime.fromisoformat(s)

def get_movie_info_tmdb(title):
    """Restituisce (poster_url, trama) in italiano (fallback inglese)."""
    try:
        # SEARCH in italiano
        r = session.get(
            'https://api.themoviedb.org/3/search/movie',
            params={'api_key': TMDB_API_KEY, 'query': title, 'language': 'it-IT'},
            headers=HEADERS,
            timeout=TIMEOUT_OTHER
        )
        r.raise_for_status()
        results = r.json().get('results', [])
        if not results:
            return None, None
        movie = results[0]

        # DETAILS in italiano
        d = session.get(
            f"https://api.themoviedb.org/3/movie/{movie['id']}",
            params={'api_key': TMDB_API_KEY, 'language': 'it-IT'},
            headers=HEADERS,
            timeout=TIMEOUT_OTHER
        )
        d.raise_for_status()
        details = d.json()
        overview = details.get('overview') or ''

        # se mancante, ripiega su inglese
        if not overview.strip():
            e = session.get(
                f"https://api.themoviedb.org/3/movie/{movie['id']}",
                params={'api_key': TMDB_API_KEY, 'language': 'en-US'},
                headers=HEADERS,
                timeout=TIMEOUT_OTHER
            )
            e.raise_for_status()
            overview = e.json().get('overview')

        poster = details.get('poster_path')
        if poster:
            poster = f"https://image.tmdb.org/t/p/w500{poster}"
        return poster, overview

    except Exception as e:
        print(f"⚠️ Errore TMDb per '{title}': {e}")
        return None, None

def get_rating_trakt(title):
    """Restituisce media voti Trakt (0–10) arrotondato a 1 decimale."""
    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_API_KEY
    }
    try:
        # search movie → estrai slug
        r = session.get(
            'https://api.trakt.tv/search/movie',
            params={'query': title, 'limit': 1},
            headers=headers,
            timeout=TIMEOUT_OTHER
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        slug = data[0]['movie']['ids']['slug']

        # request rating
        r2 = session.get(
            f'https://api.trakt.tv/movies/{slug}/ratings',
            headers=headers,
            timeout=TIMEOUT_OTHER
        )
        r2.raise_for_status()
        rating = r2.json().get('rating')
        return round(rating, 1) if rating is not None else None

    except Exception as e:
        print(f"⚠️ Errore Trakt per '{title}': {e}")
        return None

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
        try:
            with open(CACHE_FILE, encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    # file vuoto o solo whitespace
                    return []
                return json.loads(content)
        except json.JSONDecodeError:
            # cache corrotta: reinizializziamo in memoria
            print("⚠️ Cache corrotta, la reinizializzo come vuota")
            return []
    return []

def save_cache(items):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

# ─── MAIN ───────────────────────────────────────────────────────────────────────

def process():
    old_items = load_cache()

    # richiesta Emby (con Path incluso)
    try:
        resp = session.get(
            f"{EMBY_SERVER_URL}/emby/Items",
            params={
                'api_key': EMBY_API_KEY,
                'Recursive': True,
                'IncludeItemTypes': 'Movie',
                'Fields': 'MediaSources,DateCreated,Path'
            },
            headers=HEADERS,
            timeout=TIMEOUT_EMBY
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"⚠️ Errore Emby: {e}")
        return

    all_items = data.get('Items', [])
    now    = datetime.utcnow()
    cutoff = now - timedelta(days=1)

    def build_map(items):
        m = {}
        for i in items:
            title = i.get('Name')
            try:
                dt = parse_emby_date(i['DateCreated'])
            except Exception:
                continue

            # cerco risoluzione in Path (es. 720p, 1080p…)
            path = i.get('Path', '')
            match = re.search(r'(\d{3,4}p)', path)
            if match:
                res = match.group(1)
            else:
                vs   = i.get('MediaSources', [{}])[0].get('VideoStreams', [])
                res  = f"{vs[0].get('Height')}p" if vs else 'Unknown'

            m.setdefault(title, set()).add((res, dt))
        return m

    old_map = build_map(old_items)
    new_map = build_map(all_items)

    for title, infos in new_map.items():
        recent = {r for (r, dt) in infos if dt >= cutoff}
        if not recent:
            continue

        old_res = {r for (r, _) in old_map.get(title, set())}
        poster, plot = get_movie_info_tmdb(title)
        rating = get_rating_trakt(title)

        if title not in old_map:
            txt = f"*Nuovo film:* _{title}_"
            if rating is not None:
                txt += f" (⭐ {rating}/10)"
            txt += f"\nRisoluzioni: {', '.join(sorted(recent))}"
            if plot:
                txt += f"\n\n{plot}"
            send_telegram(txt, photo_url=poster)

        else:
            added = recent - old_res
            if added:
                txt = f"*Aggiornato:* _{title}_"
                if rating is not None:
                    txt += f" (⭐ {rating}/10)"
                txt += f"\nNuove risoluzioni: {', '.join(sorted(added))}"
                send_telegram(txt, photo_url=poster)

    save_cache(all_items)

if __name__ == '__main__':
    process()
