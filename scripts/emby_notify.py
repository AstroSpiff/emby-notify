#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import requests
from dateutil import parser

# ─ CONFIG ─────────────────────────────────────────────────────────────────────

EMBY_SERVER_URL   = os.environ['EMBY_SERVER_URL'].rstrip('/')
EMBY_API_KEY      = os.environ['EMBY_API_KEY']
TELEGRAM_BOT_TOKEN= os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID  = os.environ['TELEGRAM_CHAT_ID']
TMDB_API_KEY      = os.environ['TMDB_API_KEY']
TRAKT_API_KEY     = os.environ['TRAKT_API_KEY']

# Dove salviamo gli ID già notificati
STATE_FILE = os.path.join(os.path.dirname(__file__), 'processed.json')


# ─ UTILITIES ──────────────────────────────────────────────────────────────────

def load_processed():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_processed(ids):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(ids), f)

def get_emby_items():
    """Recupera tutti i video da Emby."""
    url = f"{EMBY_SERVER_URL}/emby/Items"
    params = {
        'Recursive': 'true',
        'IncludeItemTypes': 'Video',
        'Fields': 'Path'
    }
    headers = {'X-Emby-Token': EMBY_API_KEY}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json().get('Items', [])

def parse_resolutions(path):
    """Estrae risoluzioni dal path (es. 480p, 1080p, 2160p)."""
    matches = re.findall(r'\b(\d{3,4}p)\b', path, flags=re.IGNORECASE)
    unique = sorted(set(matches), key=lambda x: int(x.lower()[:-1]))
    return unique

def get_tmdb_info(query):
    """Cerca il film su TMDB, ritorna {id,title,year,overview}."""
    # 1) SEARCH
    url_s = 'https://api.themoviedb.org/3/search/movie'
    params = {'api_key': TMDB_API_KEY, 'query': query}
    r = requests.get(url_s, params=params, timeout=10); r.raise_for_status()
    results = r.json().get('results', [])
    if not results:
        return None

    movie = results[0]
    movie_id = movie['id']
    year = movie.get('release_date','')[:4]

    # 2) DETAILS in italiano
    url_d = f"https://api.themoviedb.org/3/movie/{movie_id}"
    params_it = {'api_key': TMDB_API_KEY, 'language': 'it'}
    r2 = requests.get(url_d, params=params_it, timeout=10); r2.raise_for_status()
    det = r2.json()
    overview = det.get('overview','').strip()

    # fallback inglese
    if not overview:
        r3 = requests.get(url_d, params={'api_key': TMDB_API_KEY, 'language': 'en'}, timeout=10)
        r3.raise_for_status()
        overview = r3.json().get('overview','').strip()

    return {
        'id': movie_id,
        'title': movie.get('title','').strip(),
        'year': year,
        'overview': overview
    }

def get_trakt_rating(tmdb_id, title, year):
    """
    Cerca su Trakt via TMDB id, ritorna (score, url_pagina).
    Score in scala 1–10, una cifra decimale.
    """
    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_API_KEY
    }

    # Provo lookup by tmdb
    url_lookup = f"https://api.trakt.tv/search/tmdb/{tmdb_id}?type=movie"
    r = requests.get(url_lookup, headers=headers, timeout=10); r.raise_for_status()
    arr = r.json()

    if not arr:
        # fallback search generico
        url_s = "https://api.trakt.tv/search/movie"
        r2 = requests.get(url_s, headers=headers, params={'query': title, 'year': year}, timeout=10)
        r2.raise_for_status()
        arr = r2.json()
        if not arr:
            return None, None

    # prendo il primo
    trakt_obj = arr[0].get('movie') or arr[0]
    slug = trakt_obj['ids']['slug']

    # prendo il voto
    url_r = f"https://api.trakt.tv/movies/{slug}/ratings"
    r3 = requests.get(url_r, headers=headers, timeout=10); r3.raise_for_status()
    rating_pct = r3.json().get('rating')  # es. 63.23 => 63%
    if rating_pct is None:
        return None, f"https://trakt.tv/movies/{slug}"

    score = round(rating_pct/10, 1)  # 6.3
    page = f"https://trakt.tv/movies/{slug}"
    return score, page


def send_telegram(cover_url, message):
    """
    Invia la copertina e il messaggio formattato a Telegram.
    Usiamo direttamente le API Bot di Telegram via requests.
    """
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    # scarico l'immagine in memoria
    img_resp = requests.get(cover_url, headers={'X-Emby-Token': EMBY_API_KEY}, timeout=10)
    img_resp.raise_for_status()

    files = {
        'photo': ('cover.jpg', img_resp.content)
    }
    data = {
        'chat_id': TELEGRAM_CHAT_ID,
        'caption': message,
        'parse_mode': 'Markdown'
    }
    r = requests.post(api_url, data=data, files=files, timeout=10)
    r.raise_for_status()


# ─ PROCESS ────────────────────────────────────────────────────────────────────

def process():
    processed = load_processed()
    items = get_emby_items()
    # solo quelli ancora non notificati
    new_items = [i for i in items if i['Id'] not in processed]
    if not new_items:
        return None, None

    # ne prendo il primo
    item = new_items[0]
    processed.add(item['Id'])
    save_processed(processed)

    path = item.get('Path','')
    filename = os.path.splitext(os.path.basename(path))[0]

    # estraggo risoluzioni
    resolutions = parse_resolutions(path)

    # prendo info TMDB
    tmdb = get_tmdb_info(filename)
    if tmdb:
        title = tmdb['title']
        year  = tmdb['year']
        overview = tmdb['overview']
    else:
        title = filename
        year  = ''
        overview = ''

    # rating su Trakt
    score, trakt_url = (None, None)
    if tmdb:
        score, trakt_url = get_trakt_rating(tmdb['id'], title, year)

    # compongo il messaggio in Markdown
    lines = []
    lines.append('*Nuovo film*')
    lines.append('')
    if year:
        lines.append(f"**{title} ({year})**")
    else:
        lines.append(f"**{title}**")
    lines.append('')
    if resolutions:
        lines.append(f"Risoluzioni: {', '.join(resolutions)}")
        lines.append('')
    if overview:
        lines.append(overview)
        lines.append('')
    if score is not None and trakt_url:
        lines.append(f"[Trakt]({trakt_url}) ⭐ **{score}**")

    message = "\n".join(lines)

    # URL copertina primaria Emby
    cover_url = f"{EMBY_SERVER_URL}/emby/Items/{item['Id']}/Images/Primary"

    return cover_url, message


# ─ MAIN ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    cover, msg = process()
    if cover and msg:
        send_telegram(cover, msg)
