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

CACHE_FILE    = 'data/cache.json'
HEADERS       = {'Accept': 'application/json'}
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
session.mount('http://', HTTPAdapter(max_retries=retries))
session.mount('https://', HTTPAdapter(max_retries=retries))

# ─── HELPERS ────────────────────────────────────────────────────────────────────

def parse_emby_date(dt_str):
    s = dt_str.rstrip('Z')
    if '.' in s:
        main, frac = s.split('.', 1)
        frac = re.match(r'(\d{1,6})', frac).group(1)
        s = f"{main}.{frac}"
    return datetime.fromisoformat(s)

def get_movie_info_tmdb(title):
    try:
        r = session.get(
            'https://api.themoviedb.org/3/search/movie',
            params={'api_key': TMDB_API_KEY, 'query': title, 'language': 'it-IT'},
            headers=HEADERS, timeout=TIMEOUT_OTHER
        )
        r.raise_for_status()
        results = r.json().get('results', [])
        if not results: return None, None
        m = results[0]
        d = session.get(
            f"https://api.themoviedb.org/3/movie/{m['id']}",
            params={'api_key': TMDB_API_KEY, 'language': 'it-IT'},
            headers=HEADERS, timeout=TIMEOUT_OTHER
        )
        d.raise_for_status()
        details = d.json()
        over = details.get('overview','') or ''
        if not over.strip():
            e = session.get(
                f"https://api.themoviedb.org/3/movie/{m['id']}",
                params={'api_key': TMDB_API_KEY, 'language': 'en-US'},
                headers=HEADERS, timeout=TIMEOUT_OTHER
            )
            e.raise_for_status()
            over = e.json().get('overview','')
        poster = details.get('poster_path')
        if poster:
            poster = f"https://image.tmdb.org/t/p/w500{poster}"
        return poster, over
    except Exception:
        return None, None

def get_series_info_tmdb(title):
    try:
        r = session.get(
            'https://api.themoviedb.org/3/search/tv',
            params={'api_key': TMDB_API_KEY, 'query': title, 'language': 'it-IT'},
            headers=HEADERS, timeout=TIMEOUT_OTHER
        )
        r.raise_for_status()
        results = r.json().get('results', [])
        if not results: return None, None
        s = results[0]
        d = session.get(
            f"https://api.themoviedb.org/3/tv/{s['id']}",
            params={'api_key': TMDB_API_KEY, 'language': 'it-IT'},
            headers=HEADERS, timeout=TIMEOUT_OTHER
        )
        d.raise_for_status()
        details = d.json()
        over = details.get('overview','') or ''
        if not over.strip():
            e = session.get(
                f"https://api.themoviedb.org/3/tv/{s['id']}",
                params={'api_key': TMDB_API_KEY, 'language': 'en-US'},
                headers=HEADERS, timeout=TIMEOUT_OTHER
            )
            e.raise_for_status()
            over = e.json().get('overview','')
        poster = details.get('poster_path')
        if poster:
            poster = f"https://image.tmdb.org/t/p/w500{poster}"
        return poster, over
    except Exception:
        return None, None

def get_trakt_rating(title, kind='movie'):
    hdr = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_API_KEY
    }
    ep = 'movie' if kind == 'movie' else 'show'
    try:
        r = session.get(
            f"https://api.trakt.tv/search/{ep}",
            params={'query': title, 'limit': 1},
            headers=hdr, timeout=TIMEOUT_OTHER
        )
        r.raise_for_status()
        data = r.json()
        if not data: return None
        slug = data[0][ep]['ids']['slug']
        r2 = session.get(
            f"https://api.trakt.tv/{ep}s/{slug}/ratings",
            headers=hdr, timeout=TIMEOUT_OTHER
        )
        r2.raise_for_status()
        rating = r2.json().get('rating')
        return round(rating,1) if rating is not None else None
    except Exception:
        return None

def send_telegram(text, photo_url=None):
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'parse_mode': 'Markdown'}
    try:
        if photo_url:
            data = payload.copy()
            data.update({'caption': text})
            resp = session.post(
                base + 'sendPhoto',
                params={'photo': photo_url},
                data=data,
                timeout=TIMEOUT_OTHER
            )
        else:
            payload['text'] = text
            resp = session.post(
                base + 'sendMessage',
                json=payload,
                timeout=TIMEOUT_OTHER
            )
        if not resp.ok:
            print("⚠️ Telegram error:", resp.text)
    except Exception as e:
        print("⚠️ Telegram exception:", e)

# ─── CACHE ──────────────────────────────────────────────────────────────────────

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            c = json.load(open(CACHE_FILE, encoding='utf-8'))
            if isinstance(c, dict) and 'movie_ids' in c and 'episode_ids' in c:
                return c
        except Exception:
            pass
    return {'movie_ids': [], 'episode_ids': []}

def save_cache(c):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, indent=2)

# ─── MAIN ───────────────────────────────────────────────────────────────────────

def process():
    cache = load_cache()
    old_movies   = set(cache['movie_ids'])
    old_episodes = set(cache['episode_ids'])

    # cutoff 48h in UTC, con Z finale
    cutoff_dt     = datetime.utcnow() - timedelta(hours=48)
    cutoff_iso_z  = cutoff_dt.replace(microsecond=0).isoformat() + 'Z'

    # fetch solo items creati dopo cutoff
    try:
        resp = session.get(
            f"{EMBY_SERVER_URL}/emby/Items",
            params={
                'api_key': EMBY_API_KEY,
                'IncludeItemTypes': 'Movie,Episode',
                'Fields': 'DateCreated,Id,Name,SeriesName,ParentIndexNumber,IndexNumber,Path',
                'MinDateCreated': cutoff_iso_z,
                'Limit': 200
            },
            headers=HEADERS,
            timeout=TIMEOUT_EMBY
        )
        resp.raise_for_status()
        items = resp.json().get('Items', [])
    except Exception as e:
        print("⚠️ Errore Emby fetch:", e)
        return

    for i in items:
        try:
            dt = parse_emby_date(i['DateCreated'])
        except Exception:
            continue
        if dt < cutoff_dt:
            continue

        if i.get('Type') == 'Movie':
            mid = i['Id']
            if mid not in old_movies:
                poster, plot = get_movie_info_tmdb(i['Name'])
                rating = get_trakt_rating(i['Name'], 'movie')
                txt = f"*Nuovo film:* _{i['Name']}_"
                if rating: txt += f" (⭐ {rating}/10)"
                send_telegram(txt, photo_url=poster)
                old_movies.add(mid)

        elif i.get('Type') == 'Episode':
            eid    = i['Id']
            series = i.get('SeriesName', 'Unknown')
            season = i.get('ParentIndexNumber')
            epnum  = i.get('IndexNumber')
            if eid not in old_episodes:
                poster, plot = get_series_info_tmdb(series)
                rating = get_trakt_rating(series, 'series')
                # controllo se è il primo episodio notificato di quella serie
                first = not any(jsid for jsid in old_episodes if jsid.startswith(series + "|"))
                tag = "Nuova Serie TV" if first else "Aggiornamento Serie TV"
                txt = f"*{tag}:* _{series}_\nS{season}E{epnum}"
                if rating: txt += f" (⭐ {rating}/10)"
                send_telegram(txt, photo_url=poster)
                # salvo in cache come "serie|id" per distinguerli
                old_episodes.add(f"{series}|{eid}")

    # aggiorno e salvo cache
    cache['movie_ids']   = list(old_movies)
    cache['episode_ids'] = list(old_episodes)
    save_cache(cache)

if __name__ == '__main__':
    process()
