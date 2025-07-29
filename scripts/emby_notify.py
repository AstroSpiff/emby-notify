#!/usr/bin/env python3
# scripts/emby_notify.py

import os
import re
import asyncio
import requests
from dateutil import parser as dateparser
from telegram import Bot  # è asincrono
from typing import Optional

# ── CONFIG ────────────────────────────────────────────────────────────────────

EMBY_SERVER_URL   = os.environ['EMBY_SERVER_URL']
EMBY_API_KEY      = os.environ['EMBY_API_KEY']
TELEGRAM_BOT_TOKEN= os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID  = int(os.environ['TELEGRAM_CHAT_ID'])
TMDB_API_KEY      = os.environ['TMDB_API_KEY']
TRAKT_API_KEY     = os.environ['TRAKT_API_KEY']

# ── UTILITY ───────────────────────────────────────────────────────────────────

def extract_resolution_from_filename(filename: str) -> Optional[str]:
    m = re.search(r'(\d{3,4}p)', filename)
    return m.group(1) if m else None

def tmdb_get_movie(movie_id: int, language: str = 'it-IT') -> dict:
    url = f'https://api.themoviedb.org/3/movie/{movie_id}'
    params = {'api_key': TMDB_API_KEY, 'language': language}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def trakt_get_rating(slug: str) -> Optional[float]:
    url = f'https://api.trakt.tv/movies/{slug}/ratings'
    headers = {'Content-Type': 'application/json',
               'trakt-api-version': '2',
               'trakt-api-key': TRAKT_API_KEY}
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    # trakt restituisce percentuale, es: 66.3
    return round(data.get('rating', 0) / 10, 1)

# ── LOGICA PRINCIPALE ────────────────────────────────────────────────────────

def process():
    # 1) Chiamo Emby per vedere se c'è un nuovo file .strm
    r = requests.get(f'{EMBY_SERVER_URL}/emby/Items?Recursive=true&IncludeItemTypes=Video&Fields=Path&ApiKey={EMBY_API_KEY}', timeout=10)
    r.raise_for_status()
    items = r.json().get('Items', [])

    # per semplicità, prendo il primo .strm non ancora notificato
    for item in items:
        if item['Path'].endswith('.strm'):
            filename = os.path.basename(item['Path'])
            resolution = extract_resolution_from_filename(filename) or 'Unknown'
            title_slug = os.path.splitext(filename)[0]
            # recupero info TMDB: qui suppongo tu abbia mappato slug→tmdb_id altrove
            tmdb = tmdb_get_movie(movie_id=12345, language='it-IT')  # <-- sostituisci 12345 con l’ID corretto
            year = dateparser.parse(tmdb['release_date']).year
            title = tmdb['title']
            overview = tmdb.get('overview') or tmdb_get_movie(12345, language='en-US').get('overview', '')
            slug = tmdb['imdb_id'] or tmdb['id']
            rating = trakt_get_rating(slug) or 0.0

            # costruisco il messaggio in Markdown
            caption = (
                f"*Nuovo film*\n\n"
                f"*{title}* ({year})\n\n"
                f"*Risoluzioni:* {resolution}\n\n"
                f"{overview}\n\n"
                f"[Trakt ⭐ **{rating}**](https://trakt.tv/movies/{slug})"
            )
            cover_url = f"https://image.tmdb.org/t/p/w500{tmdb['poster_path']}"
            return cover_url, caption

    return None, None

# ── INVIO TELEGRAM (API ASINCRONA) ────────────────────────────────────────────

async def send_telegram(photo_url: str, caption: str):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_photo(
        chat_id=TELEGRAM_CHAT_ID,
        photo=photo_url,
        caption=caption,
        parse_mode='Markdown'
    )

# ── ENTRYPOINT ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    cover, message = process()
    if cover and message:
        # scipy.run invoca correttamente la coroutine
        asyncio.run(send_telegram(cover, message))
    else:
        print("Nessuna novità da notificare.")
