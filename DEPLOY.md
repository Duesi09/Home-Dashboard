# Deploying the dashboard to Render (free, always-on)

This hosts the dashboard in the cloud so you can open it from any device without
keeping your PC on. Your `.env` is **not** uploaded — secrets go in Render's
settings instead.

## What you need
- A free **GitHub** account: https://github.com
- A free **Render** account: https://render.com (sign in with GitHub)

---

## Step 1 — Put the project on GitHub

From `~/dashboard` in your WSL terminal:

```bash
cd ~/dashboard
git init
git add .
git commit -m "Personal dashboard"
```

Create a new **empty** repo on GitHub (no README), call it `dashboard`, keep it
**Private**. Then copy the two commands GitHub shows under "…or push an existing
repository", they look like:

```bash
git remote add origin https://github.com/<your-username>/dashboard.git
git branch -M main
git push -u origin main
```

> `.gitignore` already keeps `.env`, `libs/`, `server.log` out of the repo, so
> your Steam key never lands on GitHub. ✅

---

## Step 2 — Deploy on Render

1. Render dashboard → **New +** → **Blueprint**.
2. Connect your GitHub and pick the `dashboard` repo.
3. Render reads `render.yaml` and proposes the service → click **Apply**.

(If you prefer not to use the Blueprint: New + → **Web Service** → pick the repo →
Runtime **Python**, Build `pip install -r requirements.txt`, Start
`gunicorn --bind 0.0.0.0:$PORT app:app`, Plan **Free**.)

---

## Step 3 — Set your secrets (important)

In the Render service → **Environment** → add:

| Key | Value |
|---|---|
| `STEAM_API_KEY` | your Steam Web API key |
| `STEAM_ID` | `76561199719091540` |
| `DASH_USER` | a username, e.g. `andri` |
| `DASH_PASSWORD` | **a password you choose** — this is what keeps it private |
| `TRAIN_ORIGIN` | `Ruschein, Tschinas` (already in the blueprint) |

Save → Render redeploys automatically.

> **Set `DASH_PASSWORD`!** Without it the URL is open to anyone. With it, the
> browser asks for the username + password the first time (and remembers it).

---

## Step 4 — Open it

Render gives you a URL like `https://andri-dashboard.onrender.com`.
Open it on your phone, tablet, any PC → enter your login → done. 🎉

---

## Good to know

- **Free tier sleeps** after ~15 min of no visits, then takes ~30–60s to wake on
  the next open. Totally fine for glancing; if you want it instant, upgrade that
  service to the paid plan (~$7/mo).
- **Keep it awake (optional, free):** create a free cron at https://cron-job.org
  that GETs your Render URL every ~10 min. (Uses a bit of your free hours but
  avoids the cold-start wait.)
- **Updating it later:** make changes locally, then
  `git add . && git commit -m "tweak" && git push` — Render redeploys on push.
- Everything works from the cloud: Steam, news (RSS), tech pick, weather, trains.
  The internet-speed widget measures *the device you're viewing on*, which is
  what you want.
