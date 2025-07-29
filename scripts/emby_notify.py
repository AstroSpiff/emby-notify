#!/usr/bin/env python3
import os
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta

# ————————————————
# 📌 Variabili d’ambiente (da GitHub Secrets)
# ————————————————
EMBY_SERVER_URL    = os.getenv('EMBY_SERVER_URL')
EMBY_API_KEY       = os.getenv('EMBY_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')
TRAKT_API_KEY      = os.getenv('TRAKT_API_KEY')
OMDB_API_KEY       = os.getenv('OMDB_API_KEY')
TMDB_API_KEY       = os.getenv('TMDB_API_KEY')  # nuovo

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
# 🎞️ Fetch Emby items con DateCreated
# ————————————————
def fetch_emby_items():
    headers = {'X-Emby-Token': EMBY_API_KEY}
    params = {
        'IncludeItemTypes': 'Movie,Episode',
        'Fields': 'MediaSources,Overview,ProductionYear,Name,ParentIndexNumber,IndexNumber,SeriesName,PrimaryImageTag,DateCreated',
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
# 🔊 Formatta audio
# ————————————————
def format_audio(ch):
    if ch == 2: return '2.0'
    if ch == 6: return '5.1'
    if ch == 8: return '7.1'
    return f"{max(ch-1,1)}.1"

# ————————————————
# ⭐️ Valutazioni (Trakt + OMDb)
# ————————————————
def get_ratings(title, year):
    trakt, imdb = 'N/A','N/A'
    try:
        headers = {
            'Content-Type':'application/json',
            'trakt-api-version':'2',
            'trakt-api-key':TRAKT_API_KEY
        }
        q = requests.utils.quote(title)
        r = requests.get(f"https://api.trakt.tv/search/movie?query={q}&year={year}", headers=headers)
        if r.ok and r.json():
            trakt = r.json()[0].get('score','N/A')
    except: pass
    try:
        q = requests.utils.quote(title)
        r = requests.get(f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={q}&y={year}")
        data = r.json()
        imdb = data.get('imdbRating','N/A')
    except: pass
    return trakt, imdb

# ————————————————
# 🎥 TMDb: prendi poster + overview
# ————————————————
def get_tmdb_info(title, year, is_series, season=None, episode=None):
    base = "https://api.themoviedb.org/3"
    # search movie o tv
    kind = "tv" if is_series else "movie"
    params = {"api_key":TMDB_API_KEY, "query":title}
    if not is_series:
        params["year"] = year
    r = requests.get(f"{base}/search/{kind}", params=params)
    if not r.ok: return None, None
    results = r.json().get("results") or []
    if not results: return None, None
    tmdb_id = results[0]["id"]
    # dettagli
    det = requests.get(f"{base}/{kind}/{tmdb_id}", params={"api_key":TMDB_API_KEY})
    if not det.ok: return None, None
    data = det.json()
    poster = data.get("poster_path")
    overview = data.get("overview","")
    poster_url = f"https://image.tmdb.org/t/p/w500{poster}" if poster else None
    return poster_url, overview

# ————————————————
# 📲 Invia Telegram (photo o testo)
# ————————————————
def send_telegram(photo_url, caption):
    if photo_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        data = {'chat_id':TELEGRAM_CHAT_ID,'caption':caption,'parse_mode':'HTML'}
        files = {'photo':requests.get(photo_url).content}
        r = requests.post(url, data=data, files=files)
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {'chat_id':TELEGRAM_CHAT_ID,'text':caption,'parse_mode':'HTML'}
        r = requests.post(url, data=data)
    if not r.ok:
        print("Errore invio Telegram:", r.status_code, r.text)

# ————————————————
# 🔄 Processo: filtra ultime 24h, confronta cache, notifica
# ————————————————
def process():
    cache = load_cache()
    new_cache = {}
    cutoff = datetime.utcnow() - timedelta(hours=24)

    for item in fetch_emby_items():
        # data creazione
        dt = datetime.fromisoformat(item.get('DateCreated').rstrip('Z'))
        if dt < cutoff:
            continue  # salta tutto ciò non aggiunto nelle ultime 24h

        title = item.get('Name')
        year  = item.get('ProductionYear','')
        is_series = bool(item.get('SeriesName'))
        season    = item.get('ParentIndexNumber')
        episode   = item.get('IndexNumber')

        # TMDb info
        poster_tmdb, overview_tmdb = get_tmdb_info(title, year, is_series, season, episode)
        overview = overview_tmdb or item.get('Overview','')[:400]
        poster_url = poster_tmdb

        trakt, imdb = get_ratings(title, year)

        # processa ogni fonte media
        for src in item.get('MediaSources', []):
            key = build_key(item, src)
            entry = {
                'Name': title,
                'Year': year,
                'SourceId': src.get('Id'),
                'Height': src.get('Height'),
                'Channels': src.get('Channels'),
                'BitRate': src.get('BitRate')
            }
            new_cache[key] = entry
            if cache.get(key) == entry:
                continue  # già notificato

            # prepara lista risoluzioni per questo item
            # (se è la prima fonte, header Nuovo; altrimenti consideralo aggiornamento)
            is_update = any(k.startswith(item['Id']+"__") for k in cache)
            h = src.get('Height')
            ch = src.get('Channels',2)
            ra = f"{h}p ({format_audio(ch)})"

            header = "<b>Aggiornamento</b>" if is_update else "<b>Nuovo</b>"
            caption  = f"{header}\n🎬 <b>{title}</b> ({year})\n"
            if is_series:
                caption += f"Stagione {season}, Episodio {episode}\n"
            caption += f"📽 Risoluzione: {ra}\n\n"
            caption += f"📝 {overview}...\n\n"
            caption += f"⭐ Trakt: {trakt}\n⭐ IMDb: {imdb}"

            send_telegram(poster_url, caption)

    save_cache(new_cache)

if __name__ == '__main__':
    process()
