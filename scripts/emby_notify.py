#!/usr/bin/env python3
# scripts/emby_notify.py

import os
import re
import json
import asyncio
import logging
from pathlib import Path

import requests
from dateutil.parser import parse as parse_date
from telegram import Bot

# ──────────────────────────────────────────────────────────────────────────────
# Configurazione logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# ──────────────────────────────────────────────────────────────────────────────
# Variabili d’ambiente
EMBY_SERVER_URL    = os.environ['EMBY_SERVER_URL'].rstrip('/')
EMBY_API_KEY       = os.environ['EMBY_API_KEY']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID   = os.environ['TELEGRAM_CHAT_ID']
TMDB_API_KEY       = os.environ['TMDB_API_KEY']
TRAKT_API_KEY      = os.environ['TRAKT_API_KEY']

CACHE_PATH = Path(__file__).parent / "cache.json"

# ──────────────────────────────────────────────────────────────────────────────
def load_cache():
    if CACHE_PATH.exists():
        return set(json.loads(CACHE_PATH.read_text()))
    return set()

def save_cache(seen):
    CACHE_PATH.write_text(json.dumps(list(seen), indent=2))

# ──────────────────────────────────────────────────────────────────────────────
def get_emby_items():
    """Chiama Emby e restaura la lista di tutti i video (.strm)."""
    url = f"{EMBY_SERVER_URL}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": "Video",
        "Fields": "Path"
    }
    headers = {"X-Emby-Token": EMBY_API_KEY}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    r.raise_for_status()
    return [i for i in r.json().get("Items", []) if i.get("Path", "").endswith(".strm")]

def extract_resolutions(path):
    """Dalla stringa del file estrae tutte le risoluzioni tipo '720p', '1080p'."""
    name = os.path.basename(path)
    res = re.findall(r"\b(\d{3,4}p)\b", name)
    return sorted(set(res), key=lambda x: int(x.rstrip("p")))

# ──────────────────────────────────────────────────────────────────────────────
def tmdb_search_movie(title):
    """Cerco il film su TMDB e ritorno il primo risultato."""
    url = "https://api.themoviedb.org/3/search/movie"
    r = requests.get(url, params={"api_key": TMDB_API_KEY, "query": title}, timeout=10)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None

def tmdb_get_details(movie_id, lang):
    """Recupero i dettagli (overview, release_date, poster) in lingua specifica."""
    url = f"https://api.themoviedb.org/3/movie/{movie_id}"
    r = requests.get(url, params={"api_key": TMDB_API_KEY, "language": lang}, timeout=10)
    r.raise_for_status()
    return r.json()

# ──────────────────────────────────────────────────────────────────────────────
def trakt_get_rating(tmdb_id):
    """Recupera da Trakt il voto percentuale e costruisce URL e voto scala 1–10."""
    url = f"https://api.trakt.tv/movies/{tmdb_id}"
    headers = {
        "Content-Type": "application/json",
        "trakt-api-key": TRAKT_API_KEY,
        "trakt-api-version": "2"
    }
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    percent = data.get("rating", 0.0)      # es. 66.3
    vote10 = round(percent / 10.0, 1)     # es. 6.6
    slug = data.get("ids", {}).get("slug")
    link = f"https://trakt.tv/movies/{slug}" if slug else None
    return vote10, link

# ──────────────────────────────────────────────────────────────────────────────
def build_message(title, year, resolutions, overview, trakt_vote, trakt_link):
    res_line = "Risoluzioni: " + ", ".join(resolutions) if resolutions else ""
    msg = (
        f"**{title} ({year})**\n\n"
        f"{res_line}\n\n"
        f"{overview}\n\n"
        f"[Trakt]({trakt_link}) ⭐ **{trakt_vote}**"
    )
    return msg

# ──────────────────────────────────────────────────────────────────────────────
def process():
    seen = load_cache()
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    items = get_emby_items()
    new_items = [i for i in items if i["Id"] not in seen]

    logging.info(f"Trovati {len(items)} strm, di cui {len(new_items)} nuovi")

    for item in new_items:
        path = item["Path"]
        resolutions = extract_resolutions(path)

        # prendo il titolo base dal filename (senza estensione e resoluzioni)
        base = os.path.splitext(os.path.basename(path))[0]
        title = re.sub(r"\b\d{3,4}p\b", "", base).replace(".", " ").strip()

        tmdb = tmdb_search_movie(title)
        if not tmdb:
            logging.warning(f"Nessun risultato TMDB per '{title}'")
            seen.add(item["Id"])
            continue

        movie_id = tmdb["id"]
        # preferisco italian, altrimenti english
        det_it = tmdb_get_details(movie_id, "it-IT")
        overview = det_it.get("overview") or tmdb_get_details(movie_id, "en-US").get("overview", "")
        year = parse_date(det_it.get("release_date", "")).year if det_it.get("release_date") else ""
        poster = det_it.get("poster_path")
        cover_url = f"https://image.tmdb.org/t/p/w500{poster}" if poster else None

        trak_vote, trak_link = trakt_get_rating(movie_id)

        msg = build_message(title, year, resolutions, overview, trak_vote, trak_link)

        # invio a Telegram (async)
        logging.info(f"Invio notifica per '{title}' ({year})")
        asyncio.get_event_loop().run_until_complete(
            bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=cover_url,
                caption=msg,
                parse_mode="Markdown"
            )
        )

        seen.add(item["Id"])

    save_cache(seen)

# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        process()
    except Exception as e:
        logging.exception("Errore durante l’esecuzione")
        raise
