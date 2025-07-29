#!/usr/bin/env python3
import os
import json
import re
import pathlib
import requests
from datetime import datetime, timedelta, timezone
from dateutil import parser
from telegram import Bot

# --- CONFIG DA ENV ---
EMBY_SERVER_URL    = os.environ['EMBY_SERVER_URL']
EMBY_API_KEY       = os.environ['EMBY_API_KEY']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID   = os.environ['TELEGRAM_CHAT_ID']
TMDB_API_KEY       = os.environ['TMDB_API_KEY']
TRAKT_API_KEY      = os.environ['TRAKT_API_KEY']

# --- FILTRO 24 ORE ---
CUTOFF = datetime.now(timezone.utc) - timedelta(days=1)

# --- PERCORSO CACHE ---
CACHE_FILE = pathlib.Path(__file__).parent.parent / 'data' / 'cache.json'

def load_cache():
    try:
        return set(json.load(open(CACHE_FILE)))
    except Exception:
        return set()

def save_cache(cache_ids):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(list(cache_ids), f)

def parse_resolutions(path):
    # trova tutte le occorrenze tipo "720p", "1080p"...
    found = re.findall(r'(\d{3,4}p)', path)
    # uniche e ordinate per numero
    uniq = sorted(set(found), key=lambda s: int(s[:-1]))
    return uniq

def get_tmdb_data(tmdb_id):
    # prova prima in italiano, poi in inglese
    base = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
    for lang in ('it-IT', 'en-US'):
        resp = requests.get(base, params={
            'api_key': TMDB_API_KEY,
            'language': lang
        }, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('overview'):
                return data
    return {}

def get_trakt_info(tmdb_id):
    url = f"https://api.trakt.tv/movies/{tmdb_id}"
    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_API_KEY
    }
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code == 200:
        d = resp.json()
        slug   = d.get('ids', {}).get('slug', '')
        rating = d.get('rating', 0.0)
        return slug, rating
    return '', 0.0

def main():
    bot   = Bot(token=TELEGRAM_BOT_TOKEN)
    cache = load_cache()

    params = {
        'IncludeItemTypes': 'Movie',
        'Recursive': 'true',
        'Fields': 'DateCreated,Path,ProviderIds',
        'SortBy': 'DateCreated',
        'SortOrder': 'Descending'
    }
    headers = {'X-Emby-Token': EMBY_API_KEY}
    url     = f"{EMBY_SERVER_URL.rstrip('/')}/Items"
    items   = requests.get(url, params=params, headers=headers, timeout=10).json().get('Items', [])

    for item in items:
        dt = parser.parse(item['DateCreated'])
        if dt < CUTOFF:
            break

        item_id = item['Id']
        if item_id in cache:
            continue
        cache.add(item_id)

        path = item.get('Path','') or ''
        ress = parse_resolutions(path)
        res_str = ", ".join(ress) if ress else "Unknown"

        tmdb_id = item.get('ProviderIds',{}).get('Tmdb')
        if not tmdb_id:
            continue

        tmdb = get_tmdb_data(tmdb_id)
        title = tmdb.get('title') or item.get('Name','')
        plot  = tmdb.get('overview','')
        poster_path = tmdb.get('poster_path')
        poster_url  = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

        release_date = tmdb.get('release_date','')
        year = release_date.split('-')[0] if release_date else ''

        slug, rating = get_trakt_info(tmdb_id)
        trakt_url = f"https://trakt.tv/movies/{slug}" if slug else ''
        vote      = f"{rating:.1f}"

        message = []
        message.append("Nuovo film")
        message.append("")  # linea vuota
        message.append(f"*{title}* ({year})")
        message.append("")  # linea vuota
        message.append(f"Risoluzioni: {res_str}")
        message.append("")  # linea vuota
        message.append(plot)
        message.append("")  # linea vuota
        if trakt_url:
            message.append(f"[Trakt]({trakt_url}) ⭐ *{vote}*")

        msg_text = "\n".join(message)

        if poster_url:
            bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=poster_url,
                caption=msg_text,
                parse_mode='Markdown'
            )
        else:
            bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg_text,
                parse_mode='Markdown'
            )

    save_cache(cache)

if __name__ == '__main__':
    main()
