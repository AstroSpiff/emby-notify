import os
import requests
import json
from datetime import datetime
from pathlib import Path
# Forzare riattivazione
# === Lettura variabili ambiente (da GitHub Secrets) ===
EMBY_SERVER = os.getenv("EMBY_SERVER_URL")
EMBY_API_KEY = os.getenv("EMBY_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TRAKT_API_KEY = os.getenv("TRAKT_API_KEY")
OMDB_API_KEY = os.getenv("OMDB_API_KEY")

CACHE_FILE = Path("data/cache.json")
CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

def load_cache():
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"items": {}}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

def get_emby_items():
    url = f"{EMBY_SERVER}/emby/Items"
    params = {
        "SortBy": "DateCreated",
        "SortOrder": "Descending",
        "Limit": 50,
        "IncludeItemTypes": "Movie,Series,Episode",
        "Fields": "Overview,ProductionYear,CommunityRating,MediaStreams,PrimaryImageTag,ParentIndexNumber,IndexNumber,SeriesName",
        "api_key": EMBY_API_KEY
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json().get("Items", [])

def format_audio(channels):
    if channels == 2:
        return "2.0"
    elif channels == 6:
        return "5.1"
    elif channels == 8:
        return "7.1"
    else:
        return f"{channels-1}.1"

def get_ratings(title, year):
    trakt_rating = "N/A"
    imdb_rating = "N/A"

    try:
        headers = {"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": TRAKT_API_KEY}
        r = requests.get(f"https://api.trakt.tv/search/movie?query={title}&year={year}", headers=headers)
        if r.status_code == 200 and r.json():
            trakt_rating = r.json()[0].get("score", "N/A")
    except:
        pass

    try:
        r = requests.get(f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={title}&y={year}")
        if r.status_code == 200:
            data = r.json()
            imdb_rating = data.get("imdbRating", "N/A")
    except:
        pass

    return trakt_rating, imdb_rating

def send_telegram(photo_url, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": caption,
        "parse_mode": "HTML"
    }
    files = {"photo": requests.get(photo_url).content} if photo_url else None
    requests.post(url, data=data, files=files)

def main():
    cache = load_cache()
    new_cache = {"items": {}}
    items = get_emby_items()

    for item in items:
        item_id = item["Id"]
        title = item.get("Name")
        year = item.get("ProductionYear", "")
        overview = (item.get("Overview", "")[:400] + "...") if item.get("Overview") else "Nessuna trama disponibile."
        rating_emby = item.get("CommunityRating", "N/A")
        is_series = item.get("Type") in ["Series", "Episode"]

        audio_channels = next((s.get("Channels") for s in item.get("MediaStreams", []) if s.get("Type") == "Audio"), 2)
        audio_str = format_audio(audio_channels)
        video_stream = next((s for s in item.get("MediaStreams", []) if s.get("Type") == "Video"), {})
        resolution = f"{video_stream.get('Width', 1920)}x{video_stream.get('Height', 1080)}"

        stagione = f"Stagione {item.get('ParentIndexNumber')}" if "ParentIndexNumber" in item else ""
        episodi = f"Episodio {item.get('IndexNumber')}" if "IndexNumber" in item else ""

        trakt_rating, imdb_rating = get_ratings(title, year)

        poster_url = f"{EMBY_SERVER}/emby/Items/{item_id}/Images/Primary?api_key={EMBY_API_KEY}"

        if item_id not in cache["items"]:
            status = "Nuovo"
        else:
            status = "Aggiornamento"

        new_cache["items"][item_id] = {"last_check": datetime.utcnow().isoformat()}

        caption = f"<b>{status}</b>\n🎬 <b>{title}</b> ({year})\n"
        if is_series:
            caption += f"{stagione}\n{episodi}\n"
        caption += f"📽 Risoluzione: {resolution}\n🔊 Audio: {audio_str}\n\n"
        caption += f"📝 {overview}\n\n"
        caption += f"⭐ Trakt: {trakt_rating}\n⭐ IMDb: {imdb_rating}"

        send_telegram(poster_url, caption)

    save_cache(new_cache)

if __name__ == "__main__":
    main()
