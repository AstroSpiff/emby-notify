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

CACHE_FILE   = 'data/cache.json'
HEADERS      = {'Accept': 'application/json'}
TIMEOUT_EMBY = (5, 30)
TIMEOUT_OTHER= 10

# ─── SESSION CON RETRY ───────────────────────────────────────────────────────────
session = requests.Session()
retries = Retry(total=5, backoff_factor=0.3,
                status_forcelist=[500,502,503,504],
                allowed_methods=["GET","POST"])
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
    """(poster, trama) in italiano→fallback inglese."""
    try:
        r = session.get('https://api.themoviedb.org/3/search/movie',
                        params={'api_key':TMDB_API_KEY,'query':title,'language':'it-IT'},
                        headers=HEADERS, timeout=TIMEOUT_OTHER)
        r.raise_for_status()
        results = r.json().get('results',[])
        if not results: return None, None
        movie = results[0]
        d = session.get(f"https://api.themoviedb.org/3/movie/{movie['id']}",
                        params={'api_key':TMDB_API_KEY,'language':'it-IT'},
                        headers=HEADERS, timeout=TIMEOUT_OTHER)
        d.raise_for_status()
        details = d.json()
        overview = details.get('overview','') or ''
        if not overview.strip():
            e = session.get(f"https://api.themoviedb.org/3/movie/{movie['id']}",
                            params={'api_key':TMDB_API_KEY,'language':'en-US'},
                            headers=HEADERS, timeout=TIMEOUT_OTHER)
            e.raise_for_status()
            overview = e.json().get('overview','')
        poster = details.get('poster_path')
        if poster: poster = f"https://image.tmdb.org/t/p/w500{poster}"
        return poster, overview
    except Exception as e:
        print(f"⚠️ Errore TMDb film '{title}': {e}")
        return None, None

def get_series_info_tmdb(title):
    """(poster, trama) serie TV in italiano→fallback inglese."""
    try:
        r = session.get('https://api.themoviedb.org/3/search/tv',
                        params={'api_key':TMDB_API_KEY,'query':title,'language':'it-IT'},
                        headers=HEADERS, timeout=TIMEOUT_OTHER)
        r.raise_for_status()
        results = r.json().get('results',[])
        if not results: return None, None
        show = results[0]
        d = session.get(f"https://api.themoviedb.org/3/tv/{show['id']}",
                        params={'api_key':TMDB_API_KEY,'language':'it-IT'},
                        headers=HEADERS, timeout=TIMEOUT_OTHER)
        d.raise_for_status()
        details = d.json()
        overview = details.get('overview','') or ''
        if not overview.strip():
            e = session.get(f"https://api.themoviedb.org/3/tv/{show['id']}",
                            params={'api_key':TMDB_API_KEY,'language':'en-US'},
                            headers=HEADERS, timeout=TIMEOUT_OTHER)
            e.raise_for_status()
            overview = e.json().get('overview','')
        poster = details.get('poster_path')
        if poster: poster = f"https://image.tmdb.org/t/p/w500{poster}"
        return poster, overview
    except Exception as e:
        print(f"⚠️ Errore TMDb serie '{title}': {e}")
        return None, None

def get_trakt_rating(title, kind='movie'):
    """Voto medio Trakt (0–10) per film o serie."""
    hdr = {'Content-Type':'application/json',
           'trakt-api-version':'2',
           'trakt-api-key':TRAKT_API_KEY}
    ep = 'movie' if kind=='movie' else 'show'
    try:
        r = session.get(f"https://api.trakt.tv/search/{ep}",
                        params={'query':title,'limit':1},
                        headers=hdr, timeout=TIMEOUT_OTHER)
        r.raise_for_status()
        data = r.json()
        if not data: return None
        slug = data[0][ep]['ids']['slug']
        r2 = session.get(f"https://api.trakt.tv/{ep}s/{slug}/ratings",
                         headers=hdr, timeout=TIMEOUT_OTHER)
        r2.raise_for_status()
        rating = r2.json().get('rating')
        return round(rating,1) if rating is not None else None
    except Exception as e:
        print(f"⚠️ Errore Trakt {kind} '{title}': {e}")
        return None

def send_telegram(txt, photo_url=None):
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    try:
        if photo_url:
            data = {'chat_id':TELEGRAM_CHAT_ID,'caption':txt,'parse_mode':'Markdown'}
            resp = session.post(base+'sendPhoto', params={'photo':photo_url},
                                data=data, timeout=TIMEOUT_OTHER)
        else:
            payload = {'chat_id':TELEGRAM_CHAT_ID,'text':txt,'parse_mode':'Markdown'}
            resp = session.post(base+'sendMessage', json=payload,
                                timeout=TIMEOUT_OTHER)
        if not resp.ok:
            print(f"⚠️ Telegram error: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"⚠️ Exception Telegram: {e}")

# ─── CACHE ──────────────────────────────────────────────────────────────────────

def load_cache():
    """Restituisce dict {movies: {titolo: [risoluzioni]}, episodes: {serie: {stagione: [episodi]}}}"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("⚠️ Cache corrotta: inizializzo vuota")
    return {'movies': {}, 'episodes': {}}

def save_cache(c):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE,'w',encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, indent=2)

# ─── MAIN ───────────────────────────────────────────────────────────────────────

def process():
    cache = load_cache()
    old_movies   = {t:set(rs) for t,rs in cache['movies'].items()}
    old_episodes = {s:{int(se):set(eps) for se,eps in seasons.items()}
                    for s,seasons in cache['episodes'].items()}

    # solo ultimi 48h
    now    = datetime.utcnow()
    cutoff = now - timedelta(hours=48)

    # fetch unificato
    try:
        r = session.get(f"{EMBY_SERVER_URL}/emby/Items/Latest",
                        params={
                          'api_key':EMBY_API_KEY,
                          'IncludeItemTypes':'Movie,Episode',
                          'Fields':'MediaSources,DateCreated,Path,SeriesName,ParentIndexNumber,IndexNumber',
                          'Limit':200
                        },
                        headers=HEADERS, timeout=TIMEOUT_EMBY)
        r.raise_for_status()
        items = r.json().get('Items',[])
    except Exception as e:
        print(f"⚠️ Errore Emby /Items/Latest: {e}")
        return

    # raccogli i nuovi film/episodi in 48h
    new_movies = defaultdict(set)
    new_episodes = defaultdict(lambda: defaultdict(set))

    for i in items:
        try:
            dt = parse_emby_date(i['DateCreated'])
        except:
            continue
        if dt < cutoff: 
            continue

        if i.get('Type')=='Movie':
            # estrai risoluzione
            path = i.get('Path','') or ''
            m = re.search(r'(\d{3,4}p)', path)
            if m:
                res = m.group(1)
            else:
                vs = i.get('MediaSources',[{}])[0].get('VideoStreams',[])
                res = f"{vs[0].get('Height')}p" if vs else 'Unknown'
            new_movies[i['Name']].add(res)

        elif i.get('Type')=='Episode':
            serie = i.get('SeriesName')
            s = i.get('ParentIndexNumber')
            e = i.get('IndexNumber')
            if serie and s is not None and e is not None:
                new_episodes[serie][str(s)].add(e)

    # ─── NOTIFICHE FILM ─────────────────────────────────────────────
    for title, rec_res in new_movies.items():
        old_res = old_movies.get(title, set())
        poster, plot = get_movie_info_tmdb(title)
        rating = get_trakt_rating(title,'movie')

        if title not in old_res:
            # Nuovo film
            txt = f"*Nuovo film:* _{title}_"
            if rating: txt += f" (⭐ {rating}/10)"
            txt += f"\nRisoluzioni: {', '.join(sorted(rec_res))}"
            if plot: txt += f"\n\n{plot}"
            send_telegram(txt, photo_url=poster)
        else:
            # Aggiornamento film
            added = rec_res - old_res
            if added:
                txt = f"*Aggiornamento film:* _{title}_"
                if rating: txt += f" (⭐ {rating}/10)"
                txt += f"\nNuove risoluzioni: {', '.join(sorted(added))}"
                send_telegram(txt, photo_url=poster)

        # aggiorna cache
        cache['movies'].setdefault(title, [])
        cache['movies'][title] = sorted(set(cache['movies'][title]) | rec_res)

    # ─── NOTIFICHE SERIE TV ───────────────────────────────────────────
    for serie, seasons in new_episodes.items():
        old_seasons = old_episodes.get(serie, {})
        poster, plot = get_series_info_tmdb(serie)
        rating = get_trakt_rating(serie,'series')

        if serie not in old_seasons:
            # Nuova Serie TV
            txt = f"*Nuova Serie TV:* _{serie}_"
            if rating: txt += f" (⭐ {rating}/10)"
            eps_list = []
            for se, eps in seasons.items():
                for ep in eps:
                    eps_list.append(f"S{se}E{ep}")
            txt += "\nEpisodi: " + ", ".join(sorted(eps_list))
            if plot: txt += f"\n\n{plot}"
            send_telegram(txt, photo_url=poster)
        else:
            # Aggiornamento Serie TV
            added_eps = {}
            for se, eps in seasons.items():
                old_eps = old_seasons.get(int(se), set())
                new_eps = eps - old_eps
                if new_eps:
                    added_eps[int(se)] = new_eps
            if added_eps:
                txt = f"*Aggiornamento Serie TV:* _{serie}_"
                if rating: txt += f" (⭐ {rating}/10)"
                txt += "\nNuovi episodi:"
                for se in sorted(added_eps):
                    eps = sorted(added_eps[se])
                    if len(eps)==1:
                        txt += f"\nS{se}E{eps[0]}"
                    else:
                        txt += f"\nStagione {se}: episodi {', '.join(str(e) for e in eps)}"
                send_telegram(txt, photo_url=poster)

        # aggiorna cache
        cache['episodes'].setdefault(serie, {})
        for se, eps in seasons.items():
            cache['episodes'][serie].setdefault(se, [])
            cache['episodes'][serie][se] = sorted(
                set(cache['episodes'][serie][se]) | eps
            )

    save_cache(cache)

if __name__ == '__main__':
    process()
