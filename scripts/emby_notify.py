#!/usr/bin/env python3
import os
import json
import re
from datetime import datetime, timedelta
from collections import defaultdict

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
TIMEOUT_EMBY  = (5, 60)   # connect=5s, read=60s
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
    """(poster_url, trama) in italiano → fallback inglese."""
    try:
        r = session.get(
            'https://api.themoviedb.org/3/search/movie',
            params={'api_key': TMDB_API_KEY, 'query': title, 'language': 'it-IT'},
            headers=HEADERS, timeout=TIMEOUT_OTHER
        )
        r.raise_for_status()
        results = r.json().get('results', [])
        if not results:
            return None, None
        movie = results[0]
        d = session.get(
            f"https://api.themoviedb.org/3/movie/{movie['id']}",
            params={'api_key': TMDB_API_KEY, 'language': 'it-IT'},
            headers=HEADERS, timeout=TIMEOUT_OTHER
        )
        d.raise_for_status()
        details = d.json()
        overview = details.get('overview') or ''
        if not overview.strip():
            e = session.get(
                f"https://api.themoviedb.org/3/movie/{movie['id']}",
                params={'api_key': TMDB_API_KEY, 'language': 'en-US'},
                headers=HEADERS, timeout=TIMEOUT_OTHER
            )
            e.raise_for_status()
            overview = e.json().get('overview')
        poster = details.get('poster_path')
        if poster:
            poster = f"https://image.tmdb.org/t/p/w500{poster}"
        return poster, overview
    except Exception as e:
        print(f"⚠️ Errore TMDb per film '{title}': {e}")
        return None, None

def get_series_info_tmdb(title):
    """(poster_url, trama) di una serie TV in italiano → fallback inglese."""
    try:
        r = session.get(
            'https://api.themoviedb.org/3/search/tv',
            params={'api_key': TMDB_API_KEY, 'query': title, 'language': 'it-IT'},
            headers=HEADERS, timeout=TIMEOUT_OTHER
        )
        r.raise_for_status()
        results = r.json().get('results', [])
        if not results:
            return None, None
        show = results[0]
        d = session.get(
            f"https://api.themoviedb.org/3/tv/{show['id']}",
            params={'api_key': TMDB_API_KEY, 'language': 'it-IT'},
            headers=HEADERS, timeout=TIMEOUT_OTHER
        )
        d.raise_for_status()
        details = d.json()
        overview = details.get('overview') or ''
        if not overview.strip():
            e = session.get(
                f"https://api.themoviedb.org/3/tv/{show['id']}",
                params={'api_key': TMDB_API_KEY, 'language': 'en-US'},
                headers=HEADERS, timeout=TIMEOUT_OTHER
            )
            e.raise_for_status()
            overview = e.json().get('overview')
        poster = details.get('poster_path')
        if poster:
            poster = f"https://image.tmdb.org/t/p/w500{poster}"
        return poster, overview
    except Exception as e:
        print(f"⚠️ Errore TMDb per serie '{title}': {e}")
        return None, None

def get_rating_trakt(title, kind='movie'):
    """Voto medio Trakt (0–10) arrotondato a 1 decimale per film o serie."""
    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_API_KEY
    }
    endpoint = 'movie' if kind=='movie' else 'show'
    search_url = f"https://api.trakt.tv/search/{endpoint}"
    ids_key = 'movie' if kind=='movie' else 'show'
    try:
        r = session.get(
            search_url,
            params={'query': title, 'limit': 1},
            headers=headers, timeout=TIMEOUT_OTHER
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        slug = data[0][ids_key]['ids']['slug']
        r2 = session.get(
            f"https://api.trakt.tv/{ids_key}s/{slug}/ratings",
            headers=headers, timeout=TIMEOUT_OTHER
        )
        r2.raise_for_status()
        rating = r2.json().get('rating')
        return round(rating,1) if rating is not None else None
    except Exception as e:
        print(f"⚠️ Errore Trakt per {kind} '{title}': {e}")
        return None

def send_telegram(text, photo_url=None):
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    try:
        if photo_url:
            data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': text, 'parse_mode': 'Markdown'}
            resp = session.post(base + 'sendPhoto', params={'photo': photo_url}, data=data, timeout=TIMEOUT_OTHER)
        else:
            payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'}
            resp = session.post(base + 'sendMessage', json=payload, timeout=TIMEOUT_OTHER)
        if not resp.ok:
            print(f"⚠️ Errore invio Telegram: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"⚠️ Exception invio Telegram: {e}")

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding='utf-8') as f:
                txt = f.read().strip()
                return json.loads(txt) if txt else []
        except json.JSONDecodeError:
            print("⚠️ Cache corrotta, reinizializzo a vuoto")
            return []
    return []

def save_cache(items):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def build_movie_map(items):
    m = {}
    for i in items:
        if i.get('Type') != 'Movie': continue
        title = i.get('Name')
        try:
            dt = parse_emby_date(i['DateCreated'])
        except: continue
        path = i.get('Path','') or ''
        match = re.search(r'(\d{3,4}p)', path)
        if match:
            res = match.group(1)
        else:
            vs = i.get('MediaSources',[{}])[0].get('VideoStreams',[])
            res = f"{vs[0].get('Height')}p" if vs else 'Unknown'
        m.setdefault(title, set()).add((res, dt))
    return m

def build_series_map(items):
    m = {}
    for i in items:
        if i.get('Type') != 'Episode': continue
        title = i.get('SeriesName')
        try:
            dt = parse_emby_date(i['DateCreated'])
        except: continue
        s = i.get('ParentIndexNumber'); e = i.get('IndexNumber')
        if title is None or s is None or e is None: continue
        m.setdefault(title, set()).add((s, e, dt))
    return m

def process():
    old_items         = load_cache()
    old_movies        = [i for i in old_items if i.get('Type')=='Movie']
    old_episodes      = [i for i in old_items if i.get('Type')=='Episode']

    # ─── Fetch FILM (tutta la libreria) ───────────────────
    try:
        r = session.get(
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
        r.raise_for_status()
        new_movies = r.json().get('Items', [])
    except Exception as e:
        print(f"⚠️ Errore Emby (Movie fetch): {e}")
        new_movies = []

    # ─── Fetch EPISODI recenti via /Items/Latest ──────────
    try:
        r2 = session.get(
            f"{EMBY_SERVER_URL}/emby/Items/Latest",
            params={
                'api_key': EMBY_API_KEY,
                'IncludeItemTypes': 'Episode',
                'Fields': 'DateCreated,SeriesName,ParentIndexNumber,IndexNumber',
                'Limit': 200
            },
            headers=HEADERS,
            timeout=TIMEOUT_EMBY
        )
        r2.raise_for_status()
        new_episodes = r2.json().get('Items', [])
    except Exception as e:
        print(f"⚠️ Errore Emby (Episode fetch): {e}")
        new_episodes = []

    now    = datetime.utcnow()
    cutoff = now - timedelta(days=1)

    # ─── Mappe per confronto ───────────────────────────────
    old_movie_map  = build_movie_map(old_movies)
    new_movie_map  = build_movie_map(new_movies)
    old_series_map = build_series_map(old_episodes)
    new_series_map = build_series_map(new_episodes)

    # ─── NOTIFICHE FILM ───────────────────────────────────
    for title, infos in new_movie_map.items():
        recent = {r for (r, dt) in infos if dt >= cutoff}
        if not recent: continue
        old_res = {r for (r, _) in old_movie_map.get(title,())}
        poster, plot = get_movie_info_tmdb(title)
        rating = get_rating_trakt(title, kind='movie')
        if title not in old_movie_map:
            txt = f"*Nuovo film:* _{title}_"
            if rating: txt += f" (⭐ {rating}/10)"
            txt += f"\nRisoluzioni: {', '.join(sorted(recent))}"
            if plot: txt += f"\n\n{plot}"
            send_telegram(txt, photo_url=poster)
        else:
            added = recent - old_res
            if added:
                txt = f"*Aggiornamento film:* _{title}_"
                if rating: txt += f" (⭐ {rating}/10)"
                txt += f"\nNuove risoluzioni: {', '.join(sorted(added))}"
                send_telegram(txt, photo_url=poster)

    # ─── NOTIFICHE SERIE TV ───────────────────────────────
    for title, infos in new_series_map.items():
        recent_eps = {(s,e) for (s,e,dt) in infos if dt >= cutoff}
        if not recent_eps: continue
        old_eps = {(s,e) for (s,e,_) in old_series_map.get(title,())}
        poster, plot = get_series_info_tmdb(title)
        rating = get_rating_trakt(title, kind='series')
        if title not in old_series_map:
            txt = f"*Nuova Serie TV:* _{title}_"
            if rating: txt += f" (⭐ {rating}/10)"
            eps_list = sorted(recent_eps)
            txt += "\nEpisodi: " + ", ".join(f"S{s}E{e}" for s,e in eps_list)
            if plot: txt += f"\n\n{plot}"
            send_telegram(txt, photo_url=poster)
        else:
            added = recent_eps - old_eps
            if added:
                seasons = defaultdict(list)
                for s,e in added: seasons[s].append(e)
                txt = f"*Aggiornamento Serie TV:* _{title}_"
                if rating: txt += f" (⭐ {rating}/10)"
                txt += "\nNuovi episodi:"
                for s in sorted(seasons):
                    eps = sorted(seasons[s])
                    if len(eps)==1:
                        txt += f"\nS{s}E{eps[0]}"
                    else:
                        nums = ", ".join(str(e) for e in eps)
                        txt += f"\nStagione {s}: episodi {nums}"
                send_telegram(txt, photo_url=poster)

    # ─── Aggiorna cache ───────────────────────────────────
    # Manteniamo tutti i film e uniamo gli episodi vecchi/nuovi
    cache_eps = {
        (i.get('SeriesName'), i.get('ParentIndexNumber'), i.get('IndexNumber')): i
        for i in old_episodes
    }
    for ep in new_episodes:
        key = (ep.get('SeriesName'), ep.get('ParentIndexNumber'), ep.get('IndexNumber'))
        cache_eps[key] = ep

    new_cache = new_movies + list(cache_eps.values())
    save_cache(new_cache)

if __name__ == '__main__':
    process()
