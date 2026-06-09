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
import time
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, send_from_directory

load_dotenv()

app = Flask(__name__)

# --- config from .env / host env vars ---------------------------------------
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
STEAM_ID = os.getenv("STEAM_ID", "")
TRAIN_ORIGIN = os.getenv("TRAIN_ORIGIN", "Ruschein, Tschinas")

HERE = os.path.dirname(os.path.abspath(__file__))


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
# Steam rate-limits / blocks shared cloud IPs hard (403 / 429). To survive that
# on a host like Render we: (1) send a real User-Agent (Steam 403s the default
# python-requests one), (2) retry 429/5xx with backoff, (3) cache results and
# serve the last good copy on error so a transient block doesn't blank the card.
_UA = {"User-Agent": "Mozilla/5.0 (personal-dashboard)"}
_cache = {}             # key -> (timestamp, data)   short-lived result cache
_appdetails_cache = {}  # appid -> data              permanent (names/art don't change)


def _steam_get(url, **kw):
    kw.setdefault("timeout", 12)
    kw.setdefault("headers", _UA)
    r = None
    for attempt in range(3):
        r = requests.get(url, **kw)
        if r.status_code in (429, 502, 503) and attempt < 2:
            time.sleep(0.8 * (attempt + 1))  # back off, then retry
            continue
        break
    r.raise_for_status()
    return r


def _cached(key, ttl, producer):
    """Fresh cache -> return it. Else run producer(): cache + return on success,
    or fall back to the last (stale) cached value on error so a transient Steam
    block doesn't blank the card. Raises only if there's no cache at all."""
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        data = producer()
        _cache[key] = (now, data)
        return data
    except Exception:
        if hit:
            return hit[1]
        raise


def _appdetails(appid):
    """Game name + canonical header_image. Cached permanently (never changes)."""
    appid = str(appid)
    if appid in _appdetails_cache:
        return _appdetails_cache[appid]
    r = _steam_get("https://store.steampowered.com/api/appdetails",
                   params={"appids": appid, "filters": "basic"})
    entry = r.json().get(appid, {})
    data = entry.get("data") if entry.get("success") else None
    if data:
        _appdetails_cache[appid] = data
    return data


def _header_for(appid, data):
    return (data or {}).get("header_image") \
        or f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"


def _fetch_recent():
    r = _steam_get("https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v1/",
                   params={"key": STEAM_API_KEY, "steamid": STEAM_ID, "count": 5})
    out = []
    for g in r.json().get("response", {}).get("games", []):
        appid = g.get("appid")
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
    return out


@app.route("/api/steam/recent")
def steam_recent():
    if not (STEAM_API_KEY and STEAM_ID):
        return jsonify({"error": "Steam API key missing (set STEAM_API_KEY in .env)"})
    try:
        return jsonify({"games": _cached("recent", 300, _fetch_recent)})  # 5 min cache
    except Exception as e:
        return jsonify({"error": str(e)})


def _wishlist_prices(appids):
    """Batch-fetch price/discount for every wishlist appid.
    Returns {appid: {discount, price}} only for items that have a price block."""
    prices = {}
    for i in range(0, len(appids), 50):
        chunk = appids[i:i + 50]
        try:
            r = _steam_get("https://store.steampowered.com/api/appdetails",
                           params={"appids": ",".join(chunk), "cc": "ch", "l": "english",
                                   "filters": "price_overview"})
            for appid, entry in (r.json() or {}).items():
                data = entry.get("data") if isinstance(entry, dict) else None
                po = data.get("price_overview") if isinstance(data, dict) else None
                if po:
                    prices[appid] = {"discount": po.get("discount_percent", 0),
                                     "price": po.get("final_formatted", "")}
        except Exception:
            pass
    return prices


def _fetch_wishlist_appids():
    r = _steam_get("https://api.steampowered.com/IWishlistService/GetWishlist/v1/",
                   params={"steamid": STEAM_ID})
    return [str(i["appid"]) for i in r.json().get("response", {}).get("items", [])]


_wl_rot = 0  # rotation offset so we cycle through discounted games across refreshes


@app.route("/api/steam/wishlist")
def steam_wishlist():
    # Shows 3 wishlist games. Rules:
    #   - discounted games sort to the TOP, highest discount first
    #   - if >3 are on sale, cycle through them on each refresh
    #   - otherwise fill the remaining slots with random non-discounted games
    # The heavy Steam calls are cached (so we don't trip rate limits); the random
    # rotation still runs per request.
    global _wl_rot
    if not STEAM_ID:
        return jsonify({"error": "Steam not configured (set STEAM_ID in .env)"})
    try:
        appids = _cached("wl_appids", 600, _fetch_wishlist_appids)   # 10 min
        if not appids:
            return jsonify({"games": []})  # empty or private wishlist
        prices = _cached("wl_prices", 300, lambda: _wishlist_prices(appids))  # 5 min
    except Exception as e:
        return jsonify({"error": str(e)})

    discounted = sorted(
        [a for a in appids if prices.get(a, {}).get("discount", 0) > 0],
        key=lambda a: prices[a]["discount"], reverse=True,
    )
    if len(discounted) >= 3:
        n = len(discounted)
        window = [discounted[(_wl_rot + i) % n] for i in range(3)]
        _wl_rot = (_wl_rot + 3) % n
        chosen = sorted(window, key=lambda a: prices[a]["discount"], reverse=True)
    else:
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
