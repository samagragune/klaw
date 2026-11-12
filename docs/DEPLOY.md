# Running it for free: dashboard on Vercel, supervisor on your machine

The supervisor stores everything in one SQLite file (`data/reliability.db`)
— no database service. It runs wherever you start it; the dashboard is a
static site that can live on Vercel's free tier and talk to it.

## 1. Run the supervisor

```bash
cd backend
python -m app.seed                                  # first time: creates the database file
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`.env` at the repo root needs `SECRET_KEY` (and `GEMINI_API_KEY` +
`SUPERVISOR_LLM_PROVIDER=gemini` if you want output review). Add your Vercel
URL to `CORS_ORIGINS`, e.g.
`CORS_ORIGINS=http://localhost:3000,https://klaw.vercel.app`.

## 2. Make it reachable from the website

The dashboard runs in your browser, so the browser must reach the supervisor:

* **Same machine:** use `http://localhost:8000`. Chrome may ask to let the
  site access your local network — allow it.
* **Any device (phone, a colleague, agents on other machines):** expose it
  with a free tunnel, e.g. Cloudflare's no-account quick tunnel:
  `cloudflared tunnel --url http://localhost:8000` prints an
  `https://….trycloudflare.com` address. It changes each time the tunnel
  restarts.

## 3. Deploy the dashboard on Vercel (free)

1. vercel.com → sign in with GitHub → **Add New → Project** → import this
   repository.
2. **Root Directory:** `dashboard`. Framework: Next.js.
3. Optional environment variable `NEXT_PUBLIC_RELIABILITY_API_URL`: the
   default supervisor address. You can change it any time on the sign-in page
   (**Supervisor: … change**), so a new tunnel address never needs a rebuild.
   Never set `NEXT_PUBLIC_RELIABILITY_TOKEN` on a deployment.

## 4. Sign in and connect agents

Open the Vercel URL → check the supervisor address under the sign-in form →
**Create account**. Then **Integrations** → create a key → run the one
command it shows on each machine whose agents you want supervised.

## What this setup means

* Supervision is up while your machine and the supervisor are running. When
  it is off, agents keep working unsupervised: Claude Code hooks queue events
  on their own machine and deliver them later; SDK apps queue in memory.
* The data is one file. Back it up by copying `data/reliability.db`.
* SQLite handles a handful of concurrent agents comfortably (tested with five
  at once); for many busy agents, point `DATABASE_URL` at Postgres.

Paid always-on hosting remains possible: `backend/Dockerfile`, `railway.json`
and `fly.toml` are kept for that, and the backend accepts hosted Postgres
URLs as given.
