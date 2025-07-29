#!/usr/bin/env python3
import os
import json
import requests
from pathlib import Path

# ————————————————
# 📌 Variabili d’ambiente (da GitHub Secrets)
# ————————————————
EMBY_SERVER_URL    = os.getenv('EMBY_SERVER_URL')
EMBY_API_KEY       = os.getenv('EMBY_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')
TRAKT_API_KEY      = os.getenv('TRAKT_API_KEY')
OMDB_API_KEY       = os.getenv('OMDB_API_KEY')

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
# 🎞️ Prendo tutti i Movie ed Episode da Emby
# ————————————————
def fetch_emby_items():
    headers = {'X-Emby-Token': EMBY_API_KEY}
    params = {
        'IncludeItemTypes': 'Movie,Episode',
        'Recursive': 'true',
        'Limit': 100,
        'Fields': 'MediaSources,Overview,ProductionYear,Name,ParentIndexNumber,IndexNumber,SeriesName,PrimaryImageTag'
    }
    r = requests.get(f"{EMBY_SERVER_URL}/emby/Items", headers=headers, params=params)
    r.raise_for_status()
    return r.json().get('Items', [])

# ————————————————
# 🔑 Chiave unica: combina ItemId + MediaSourceId
# ————————————————
def build_key(item, src):
    return f"{item['Id']}__{src['Id']}"

# ————————————————
# 🔊 Audio format helper
# ————————————————
def format_audio(ch):
    if ch == 2: return '2.0'
    if ch == 6: return '5.1'
    if ch == 8: return '7.1'
    return f"{max(ch-1,1)}.1"

# ————————————————
# ⭐️ Recupera valutazioni Trakt e IMDb (via OMDb)
# ————————————————
def get_ratings(title, year):
    trakt_rating = 'N/A'
    imdb_rating = 'N/A'
    # — Trakt —
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
    # — IMDb via OMDb —
    try:
        q = requests.utils.quote(title)
        r = requests.get(f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={q}&y={year}")
        data = r.json()
        imdb_rating = data.get('imdbRating', 'N/A')
    except:
        pass
    return trakt_rating, imdb_rating

# ————————————————
# 📲 Invia notifica Telegram con foto + caption HTML
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
# 🔄 Processo principale: confronta cache e raggruppa per film
# ————————————————
def process():
    old_cache = load_cache()
    old_keys = set(old_cache.keys())
    new_cache = {}

    items = fetch_emby_items()
    for item in items:
        # Dati comuni
        title      = item.get('Name')
        year       = item.get('ProductionYear','')
        is_series  = bool(item.get('SeriesName'))
        season     = item.get('ParentIndexNumber')
        episode    = item.get('IndexNumber')
        overview   = item.get('Overview','')[:400]
        tag        = item.get('PrimaryImageTag')
        poster_url = (f"{EMBY_SERVER_URL}/emby/Items/{item['Id']}/Images/Primary?tag={tag}"
                      if tag else None)
        trakt_rating, imdb_rating = get_ratings(title, year)

        # Raccogli le chiavi di tutte le sorgenti media per questo item
        item_keys = []
        src_map   = {}
        for src in item.get('MediaSources', []):
            key = build_key(item, src)
            item_keys.append(key)
            src_map[key] = src
            # Prepara il nuovo cache entry
            new_cache[key] = {
                'Name':        title,
                'Year':        year,
                'SourceId':    src.get('Id'),
                'Height':      src.get('Height'),
                'Channels':    src.get('Channels'),
                'BitRate':     src.get('BitRate')
            }

        # Trova quali sorgenti sono nuove
        new_keys = [k for k in item_keys if k not in old_keys]
        if not new_keys:
            continue

        # Determina se è un film/episodio del tutto nuovo, o un aggiornamento
        old_keys_item = [k for k in item_keys if k in old_keys]
        is_update     = bool(old_keys_item)

        # Costruisci la lista "1080p (2.0), 2160p (5.1)"
        ra_list = []
        for k in new_keys:
            src = src_map[k]
            h   = src.get('Height')
            ch  = src.get('Channels', 2)
            ra_list.append(f"{h}p ({format_audio(ch)})")
        ra_str = ", ".join(ra_list)

        # Prepara caption
        header = "<b>Aggiornamento</b>" if is_update else "<b>Nuovo</b>"
        caption  = f"{header}\n"
        caption += f"🎬 <b>{title}</b> ({year})\n"
        if is_series:
            caption += f"Stagione {season}, Episodio {episode}\n"
        caption += f"📽 Risoluzioni: {ra_str}\n\n"
        caption += f"📝 {overview}...\n\n"
        caption += f"⭐ Trakt: {trakt_rating}\n"
        caption += f"⭐ IMDb: {imdb_rating}"

        send_telegram(poster_url, caption)

    # Salva cache aggiornata
    save_cache(new_cache)

if __name__ == '__main__':
    process()
