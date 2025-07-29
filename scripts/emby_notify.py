#!/usr/bin/env python3
import os
import json
from datetime import datetime, timedelta
import requests
import telegram

# --- CONFIGURAZIONE DA ENV ---
EMBY_SERVER_URL   = os.getenv("EMBY_SERVER_URL")
EMBY_API_KEY      = os.getenv("EMBY_API_KEY")
EMBY_USER_ID      = os.getenv("EMBY_USER_ID")      # assicurati di averlo a disposizione
TELEGRAM_BOT_TOKEN= os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
TMDB_API_KEY      = os.getenv("TMDB_API_KEY")

CACHE_PATH = "data/cache.json"


def load_cache():
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def fetch_emby_items():
    url = f"{EMBY_SERVER_URL}/emby/Users/{EMBY_USER_ID}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": "Movie",
        "IsHidden": "false",
        # chiediamo anche DateAdded e MediaSources
        "Fields": "DateAdded,MediaSources"
    }
    headers = {"X-Emby-Token": EMBY_API_KEY}
    r = requests.get(url, params=params, headers=headers)
    r.raise_for_status()
    return r.json().get("Items", [])


def get_tmdb_info(title, year):
    """Cerca il film su TMDB e restituisce (poster_url, overview)."""
    search_url = "https://api.themoviedb.org/3/search/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "query": title,
        "year": year or "",
        "language": "it-IT"
    }
    r = requests.get(search_url, params=params)
    if not r.ok:
        return None, None
    data = r.json().get("results") or []
    if not data:
        return None, None
    m = data[0]
    poster = m.get("poster_path")
    poster_url = f"https://image.tmdb.org/t/p/w500{poster}" if poster else None
    overview = m.get("overview")
    return poster_url, overview


def main():
    cache = load_cache()
    cutoff = datetime.utcnow() - timedelta(hours=24)

    new_movies = []
    updated_movies = []

    items = fetch_emby_items()
    for item in items:
        item_id = item["Id"]
        # Emby ti restituisce la data in ISO con 'Z'
        date_added = item.get("DateAdded")
        if not date_added:
            continue
        dt_added = datetime.fromisoformat(date_added.rstrip("Z"))

        # lista di ID dei media sources (una per risoluzione/versione)
        media_sources = item.get("MediaSources", [])
        src_ids = [src["Id"] for src in media_sources]

        if item_id not in cache:
            # nuovo film
            if dt_added > cutoff:
                new_movies.append((item, media_sources))
            # aggiungo comunque al cache
            cache[item_id] = src_ids
        else:
            # già visto: controllo se ci sono nuove versioni
            old_src = set(cache[item_id])
            added = [s for s in media_sources if s["Id"] not in old_src]
            if added:
                updated_movies.append((item, added))
                # aggiorno il cache
                cache[item_id] = src_ids

    save_cache(cache)

    # inizializzo Telegram
    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

    # notifichiamo i nuovi film
    for item, media_sources in new_movies:
        title = item.get("Name")
        year  = item.get("ProductionYear")
        poster_url, overview = get_tmdb_info(title, year)

        caption = f"🎬 *Nuovo film:* {title} ({year})"
        if overview:
            caption += f"\n\n_{overview}_"

        if poster_url:
            bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=poster_url,
                caption=caption,
                parse_mode="Markdown"
            )
        else:
            bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=caption,
                parse_mode="Markdown"
            )

    # notifichiamo gli aggiornamenti di risoluzione
    for item, added_sources in updated_movies:
        title = item.get("Name")
        year  = item.get("ProductionYear")
        qualities = sorted({ f"{s.get('Width')}p" for s in added_sources })

        text = (
            f"🔄 *{title}* ({year})\n"
            f"Nuove risoluzioni aggiunte: {', '.join(qualities)}"
        )
        bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="Markdown"
        )


if __name__ == "__main__":
    main()
