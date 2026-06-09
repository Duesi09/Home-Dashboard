# Personal Dashboard — Andri

Self-hosted purple bento dashboard. Flask serves one page at `http://localhost:5000`.

Widgets:
- **Weather** (Ruschein) — temp, today's high/low, rain/snow outlook, feels-like, humidity
- **Trains** — Ruschein, Tschinas → Bonaduz & Chur
- **Steam recent** — your last played games (needs API key)
- **Wishlist** — 3 of your wishlisted games, re-rolled every refresh
- **Internet** — live ping + download speed (Mbps) + sparkline
- **News** — Anthropic/Claude, Soulsborne, and gaming announcements in one card
- **Tech pick of the day** — a rotating cool gadget to buy

## Setup

This machine has no `venv`/`ensurepip`, so deps live in a local `libs/` folder
(no sudo, nothing touches system Python). It's already installed and running.

```bash
# (already done once — here for reference)
python3 -m pip install --target=./libs -r requirements.txt
cp .env.example .env
```

### Run / stop

```bash
./start.sh     # starts detached -> http://localhost:5000 (survives closing the terminal)
./stop.sh      # stops it
tail -f server.log   # watch logs
```

`start.sh` uses `setsid` so the server keeps running in the background after you
close the terminal. (Plain `PYTHONPATH=./libs python3 app.py` also works but dies
when the terminal closes.)

> If you later install `python3-venv` (`sudo apt install python3-venv`), the
> classic `venv` + `pip install -r requirements.txt` flow works too; just drop
> the `PYTHONPATH=./libs` prefix.

Fill in `.env`:

| Key | Where to get it |
|---|---|
| `STEAM_API_KEY` | https://steamcommunity.com/dev/apikey (needed for the "recently played" widget) |
| `STEAM_ID` | already set to your SteamID64 — the wishlist card works with just this |
| `TRAIN_ORIGIN` | defaults to `Ruschein, Tschinas` (change if no trains show up) |

Make sure your **Steam profile + game details are PUBLIC**
(Steam → Profile → Edit Profile → Privacy Settings).

## Notes

- **No keys?** No problem — weather, trains, news and internet stability all work
  out of the box. The Steam "recently played" card needs the free API key; the
  wishlist card only needs your (public) SteamID64. Both are already set.
- Why a backend? Steam blocks browser CORS, so its calls go through Flask.
  Everything else is called straight from the browser.
- Train stop wrong? If `Ruschein, Tschinas` shows nothing, try setting
  `TRAIN_ORIGIN=Ruschein` in `.env`, or look up the exact name at
  `http://transport.opendata.ch/v1/locations?query=Ruschein`.
- Internet sparkline survives reloads (cached in `localStorage`).
- Bottom status bar shows which services are reachable.

## About apache2

You mentioned apache2 — this dashboard runs on its own Flask server (port 5000)
instead, because the Steam widgets need a backend (CORS). If you'd rather reach
it on port 80 through Apache, enable the proxy modules and reverse-proxy to Flask:

```apache
# a2enmod proxy proxy_http  then in your site config:
ProxyPass        / http://127.0.0.1:5000/
ProxyPassReverse / http://127.0.0.1:5000/
```

Keep `python app.py` running in the background (e.g. a systemd service or `tmux`).
