"""
Personal Dashboard backend for Andri.

Why Flask at all? Steam's Web API + wishlist endpoints block browser CORS, so the
browser can't call them directly — they go through here instead. Everything else
(weather, trains, news, internet ping) is called straight from the browser because
those APIs are CORS-friendly and need no key.

Run with:  PYTHONPATH=./libs python3 app.py   ->  http://localhost:5000
"""

import os
import random
import re
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory

load_dotenv()

app = Flask(__name__)

# --- config from .env / host env vars ---------------------------------------
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
STEAM_ID = os.getenv("STEAM_ID", "")
TRAIN_ORIGIN = os.getenv("TRAIN_ORIGIN", "Ruschein, Tschinas")

# Optional password protection. When DASH_PASSWORD is set (e.g. as an env var on
# the cloud host), every request needs HTTP Basic Auth. Left empty locally so the
# dashboard stays open on your own machine.
DASH_USER = os.getenv("DASH_USER", "andri")
DASH_PASSWORD = os.getenv("DASH_PASSWORD", "")

HERE = os.path.dirname(os.path.abspath(__file__))


@app.before_request
def _require_login():
    if request.path == "/healthz":
        return  # health check stays open (used by the keep-alive pinger)
    if not DASH_PASSWORD:
        return  # no password configured -> open (local use)
    auth = request.authorization
    if not auth or auth.username != DASH_USER or auth.password != DASH_PASSWORD:
        return Response(
            "Login required", 401,
            {"WWW-Authenticate": 'Basic realm="Andri Dashboard"'},
        )


@app.route("/healthz")
def healthz():
    # tiny always-200 endpoint so an uptime pinger can keep the free host awake
    return "ok", 200


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(HERE, "dashboard.html")


# Expose the train origin (and which services are configured) to the frontend
# so it doesn't have to guess. Handy for the "service reachable" status bar too.
@app.route("/api/config")
def config():
    return jsonify({
        "train_origin": TRAIN_ORIGIN,
        "steam_configured": bool(STEAM_API_KEY and STEAM_ID),
    })


# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------
def _appdetails(appid):
    """Fetch a single game's basic data (name + the canonical header_image URL).
    More reliable than guessing the CDN path — some apps (demos etc.) don't have
    a header.jpg at the usual location."""
    r = requests.get(
        "https://store.steampowered.com/api/appdetails",
        params={"appids": appid, "filters": "basic"},
        timeout=10, headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()
    entry = r.json().get(str(appid), {})
    return entry.get("data") if entry.get("success") else None


def _header_for(appid, data):
    return (data or {}).get("header_image") \
        or f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"


@app.route("/api/steam/recent")
def steam_recent():
    # GetRecentlyPlayedGames needs the Web API key (free from steamcommunity.com/dev/apikey)
    if not (STEAM_API_KEY and STEAM_ID):
        return jsonify({"error": "Steam API key missing (set STEAM_API_KEY in .env)"})
    try:
        url = "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v1/"
        r = requests.get(url, params={"key": STEAM_API_KEY, "steamid": STEAM_ID, "count": 5}, timeout=10)
        r.raise_for_status()
        games = r.json().get("response", {}).get("games", [])
        out = []
        for g in games:
            appid = g.get("appid")
            # pull the real header image so demos / odd apps still show artwork
            data = None
            try:
                data = _appdetails(appid)
            except Exception:
                pass
            out.append({
                "name": g.get("name"),
                "appid": appid,
                "hours_total": round(g.get("playtime_forever", 0) / 60, 1),
                "hours_2weeks": round(g.get("playtime_2weeks", 0) / 60, 1),
                "header": _header_for(appid, data),
                "url": f"https://store.steampowered.com/app/{appid}",
            })
        return jsonify({"games": out})
    except Exception as e:
        return jsonify({"error": str(e)})


def _wishlist_prices(appids):
    """Batch-fetch price/discount for every wishlist appid.
    Returns {appid: {discount, price}} only for items that have a price block."""
    prices = {}
    for i in range(0, len(appids), 50):
        chunk = appids[i:i + 50]
        try:
            r = requests.get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": ",".join(chunk), "cc": "ch", "l": "english",
                        "filters": "price_overview"},
                timeout=15, headers={"User-Agent": "Mozilla/5.0"},
            )
            r.raise_for_status()
            for appid, entry in (r.json() or {}).items():
                data = entry.get("data") if isinstance(entry, dict) else None
                po = data.get("price_overview") if isinstance(data, dict) else None
                if po:
                    prices[appid] = {"discount": po.get("discount_percent", 0),
                                     "price": po.get("final_formatted", "")}
        except Exception:
            pass
    return prices


_wl_rot = 0  # rotation offset so we cycle through discounted games across refreshes


@app.route("/api/steam/wishlist")
def steam_wishlist():
    # Shows 3 wishlist games. Rules:
    #   - discounted games sort to the TOP, highest discount first
    #   - if >3 are on sale, cycle through them on each refresh
    #   - otherwise fill the remaining slots with random non-discounted games
    # Needs only the SteamID64 + a public profile (no key).
    global _wl_rot
    if not STEAM_ID:
        return jsonify({"error": "Steam not configured (set STEAM_ID in .env)"})
    try:
        wl = requests.get(
            "https://api.steampowered.com/IWishlistService/GetWishlist/v1/",
            params={"steamid": STEAM_ID}, timeout=10,
        )
        wl.raise_for_status()
        items = wl.json().get("response", {}).get("items", [])
        appids = [str(i["appid"]) for i in items]
        if not appids:
            return jsonify({"games": []})  # empty or private wishlist

        prices = _wishlist_prices(appids)
        discounted = sorted(
            [a for a in appids if prices.get(a, {}).get("discount", 0) > 0],
            key=lambda a: prices[a]["discount"], reverse=True,
        )

        if len(discounted) >= 3:
            # more than 3 on sale -> page a window of 3 through the list each refresh
            n = len(discounted)
            window = [discounted[(_wl_rot + i) % n] for i in range(3)]
            _wl_rot = (_wl_rot + 3) % n
            chosen = sorted(window, key=lambda a: prices[a]["discount"], reverse=True)
        else:
            # discounted ones stay on top, random non-discounted fill the rest
            chosen = list(discounted)
            rest = [a for a in appids if a not in discounted]
            random.shuffle(rest)
            chosen += rest[:max(0, 3 - len(chosen))]

        games = []
        for appid in chosen:
            data = None
            try:
                data = _appdetails(appid)
            except Exception:
                pass
            p = prices.get(appid, {})
            games.append({
                "name": (data or {}).get("name") or f"App {appid}",
                "appid": appid,
                "header": _header_for(appid, data),
                "url": f"https://store.steampowered.com/app/{appid}",
                "discount": p.get("discount", 0),
                "price": p.get("price", ""),
            })
        return jsonify({"games": games, "total": len(appids), "on_sale": len(discounted)})
    except Exception as e:
        return jsonify({"error": str(e)})


# ---------------------------------------------------------------------------
# News feeds (RSS/Atom via backend)
# ---------------------------------------------------------------------------
# We use RSS instead of Reddit: Reddit blocks browser CORS *and* bot-filters
# requests (403 "Blocked"), so its feeds never load reliably. These outlet feeds
# are CORS-free (proxied here) and don't IP-block.
#   · Gaming news -> IGN (top gaming stories)
#   · IT news     -> The Register (sysadmin / IT / tech, perfect for Andri)
NEWS_FEEDS = [
    ("Gaming news", "IGN", "https://feeds.feedburner.com/ign/games-all", 2),
    ("IT news", "The Register", "https://www.theregister.com/headlines.atom", 3),
]

ATOM = "{http://www.w3.org/2005/Atom}"


def _rss_items(url, n):
    """Parse the first n entries of an RSS or Atom feed into {title, url, date}."""
    r = requests.get(url, timeout=10, headers={"User-Agent": "personal-dashboard/1.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    nodes = root.findall(".//item") or root.findall(f".//{ATOM}entry")  # RSS or Atom
    items = []
    for it in nodes[:n]:
        title = (it.findtext("title") or it.findtext(f"{ATOM}title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not link:  # Atom puts the URL in <link href="...">
            el = it.find(f"{ATOM}link")
            if el is not None:
                link = el.get("href", "")
        date = (it.findtext("pubDate") or it.findtext(f"{ATOM}updated")
                or it.findtext(f"{ATOM}published") or "").strip()
        items.append({"title": title, "url": link, "date": date})
    return items


@app.route("/api/feeds")
def feeds():
    out = []
    for label, source, url, n in NEWS_FEEDS:
        try:
            out.append({"label": label, "source": source, "items": _rss_items(url, n)})
        except Exception as e:
            out.append({"label": label, "source": source, "items": [], "error": str(e)})
    return jsonify({"sections": out})


# ---------------------------------------------------------------------------
# Tech pick of the day — real gadgets from Gadget Flow's RSS
# ---------------------------------------------------------------------------
@app.route("/api/techpick")
def techpick():
    # Gadget Flow lists actual buyable gadgets daily. We keep only real product
    # entries (their URLs contain /product/) so editorial/news posts are skipped.
    # The frontend picks one per day from this list.
    try:
        r = requests.get("https://thegadgetflow.com/feed/", timeout=12,
                         headers={"User-Agent": "personal-dashboard/1.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for it in root.findall(".//item"):
            link = (it.findtext("link") or "").strip()
            if "/product/" not in link:
                continue
            enc = it.find("enclosure")
            img = enc.get("url") if enc is not None else ""
            desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "")
            desc = re.sub(r"\s+", " ", desc).strip()
            items.append({
                "name": (it.findtext("title") or "").strip(),
                "url": link,
                "img": img,
                "desc": desc[:170],
            })
        return jsonify({"source": "Gadget Flow", "items": items})
    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    # Local run. host=0.0.0.0 so other devices on your home wifi can reach it too.
    # In the cloud, gunicorn runs the app instead (see Procfile) and sets $PORT.
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
