#!/usr/bin/env python3
import os
import json
import requests
from pathlib import Path
from datetime import datetime

# ————————————————
# 📌 Variabili d’ambiente (da GitHub Secrets)
# ————————————————
EMBY_SERVER_URL   = os.getenv('EMBY_SERVER_URL')
EMBY_API_KEY      = os.getenv('EMBY_API_KEY')
TELEGRAM_BOT_TOKEN= os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID  = os.getenv('TELEGRAM_CHAT_ID')
TRAKT_API_KEY     = os.getenv('TRAKT_API_KEY')
OMDB_API_KEY      = os.getenv('OMDB_API_KEY')

# ————————————————
# 📁 Cache file
# ————————————————
CACHE_PATH = Path('data/cache.json')
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

def load_cache():
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding='utf-8'))
    return {}

def save_cache(cache):
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding='utf-8')

# ————————————————
# 🎞️ Emby: prendi tutti i Movie ed Episode, incluse le sorgenti media
# ————————————————
def fetch_emby_items():
    headers = {'X-Emby-Token': EMBY_API_KEY}
    params = {
        'IncludeItemTypes': 'Movie,Episode',
        'Fields': 'MediaSources,Overview,ProductionYear,Name,ParentIndexNumber,IndexNumber,SeriesName,PrimaryImageTag',
        'Recursive': 'true',
        'Limit': 100
    }
    r = requests.get(f"{EMBY_SERVER_URL}/emby/Items", headers=headers, params=params)
    r.raise_for_status()
    return r.json().get('Items', [])

# ————————————————
# 🔑 Chiave unica: ItemId + MediaSourceId
# ————————————————
def build_key(item, src):
    return f"{item['Id']}__{src['Id']}"

# ————————————————
# 🔊 Formatta canali audio
# ————————————————
def format_audio(ch):
    if ch == 2: return '2.0'
    if ch == 6: return '5.1'
    if ch == 8: return '7.1'
    return f"{max(ch-1,1)}.1"

# ————————————————
# ⭐️ Prendi valutazioni da Trakt e IMDb (OMDb)
# ————————————————
def get_ratings(title, year):
    trakt_rating = 'N/A'
    imdb_rating = 'N/A'
    try:
        headers = {
            'Content-Type': 'application/json',
            'trakt-api-version': '2',
            'trakt-api-key': TRAKT_API_KEY
        }
        q = requests.utils.quote(title)
        r = requests.get(f"https://api.trakt.tv/search/movie?query={q}&year={year}", headers=headers)
        if r.ok and r.json():
            trakt_rating = r.json()[0].get('score', 'N/A')
    except:
        pass

    try:
        q = requests.utils.quote(title)
        r = requests.get(f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={q}&y={year}")
        data = r.json()
        imdb_rating = data.get('imdbRating', 'N/A')
    except:
        pass

    return trakt_rating, imdb_rating

# ————————————————
# 📲 Invia foto + didascalia a Telegram
# ————————————————
def send_telegram(photo_url, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {
        'chat_id': TELEGRAM_CHAT_ID,
        'caption': caption,
        'parse_mode': 'HTML'
    }
    files = {'photo': requests.get(photo_url).content} if photo_url else None
    r = requests.post(url, data=data, files=files)
    if not r.ok:
        print("Errore invio Telegram:", r.text)

# ————————————————
# 🔄 Processo principale: confronta cache e notifica
# ————————————————
def process():
    cache = load_cache()
    new_cache = {}
    items = fetch_emby_items()

    for item in items:
        for src in item.get('MediaSources', []):
            key = build_key(item, src)
            entry = {
                'Name': item.get('Name'),
                'Year': item.get('ProductionYear'),
                'SourceId': src.get('Id'),
                'Height': src.get('Height'),
                'Channels': src.get('Channels'),
                'BitRate': src.get('BitRate')
            }
            new_cache[key] = entry
            if cache.get(key) != entry:
                notify(item, src)

    save_cache(new_cache)

# ————————————————
# 🔔 Crea la didascalia e invia la notifica
# ————————————————
def notify(item, src):
    title  = item.get('Name')
    year   = item.get('ProductionYear','')
    is_series = bool(item.get('SeriesName'))
    season = item.get('ParentIndexNumber')
    episode= item.get('IndexNumber')
    height = src.get('Height')
    ch     = src.get('Channels',2)
    audio  = format_audio(ch)
    overview = item.get('Overview','')[:400]
    tag    = item.get('PrimaryImageTag')
    photo_url = f"{EMBY_SERVER_URL}/emby/Items/{item['Id']}/Images/Primary?tag={tag}" if tag else None

    trakt_rating, imdb_rating = get_ratings(title, year)

    caption  = f"<b>Nuovo contenuto</b>\n"
    caption += f"🎬 <b>{title}</b> ({year})\n"
    if is_series:
        caption += f"Stagione {season}, Episodio {episode}\n"
    caption += f"📽 Risoluzione: {height}p\n"
    caption += f"🔊 Audio: {audio}\n\n"
    caption += f"📝 {overview}...\n\n"
    caption += f"⭐ Trakt: {trakt_rating}\n"
    caption += f"⭐ IMDb: {imdb_rating}"

    send_telegram(photo_url, caption)

if __name__ == '__main__':
    process()
