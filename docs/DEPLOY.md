# Deploying: Vercel (dashboard) + Railway or Fly (backend)

The dashboard is a static-heavy Next.js app; the backend is one FastAPI
process with Postgres. Deploy the backend first — the dashboard needs its URL
at build time.

## 1. Backend on Railway

1. New project → **Deploy from GitHub repo** → this repository. Railway reads
   `railway.json` and builds `backend/Dockerfile`.
2. Add a **PostgreSQL** service to the project. Railway injects `DATABASE_URL`
   into the backend (`postgres://` is converted to the async driver
   automatically).
3. Backend → **Variables**:

   | Variable | Value |
   |---|---|
   | `RELIABILITY_ENV` | `production` |
   | `SECRET_KEY` | output of `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `CORS_ORIGINS` | your Vercel URL, e.g. `https://reliability.vercel.app` (add it after step 2 below) |
   | `SUPERVISOR_LLM_PROVIDER` | `gemini` (optional) |
   | `GEMINI_API_KEY` | your key (optional) |
   | `SUPERVISOR_MODEL` | `gemini-3.5-flash` |
   | `SUPERVISOR_FAST_MODEL` | `gemini-3.5-flash-lite` |
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (reference variable) |

4. **Settings → Networking → Generate domain.** Check `https://<domain>/health`.

### Or: backend on Fly.io

`fly.toml` is ready. `fly launch --no-deploy --copy-config`, then
`fly postgres create` + `fly postgres attach`, set the secrets from the table
above with `fly secrets set`, and `fly deploy`.

## 2. Dashboard on Vercel

1. **Add New → Project** → import the repository.
2. **Root Directory:** `dashboard`. Framework preset: Next.js (auto-detected).
3. **Environment variable:** `NEXT_PUBLIC_RELIABILITY_API_URL` = the backend
   URL from step 1 (no trailing slash). Do **not** set
   `NEXT_PUBLIC_RELIABILITY_TOKEN` — anything `NEXT_PUBLIC_` is visible to
   every visitor.
4. Deploy, then put the Vercel URL into the backend's `CORS_ORIGINS` and
   redeploy the backend.

## 3. First sign-in

Open the Vercel URL → **Create account**. Each account is an isolated
workspace. Once the accounts you need exist, set `ALLOW_SIGNUP=false` on the
backend if the deployment should be private.

## 4. Connect agents to the deployed supervisor

Integrations → create a key → follow the setup steps shown (they include the
deployed URL). Claude Code machines need outbound HTTPS to the backend; nothing
inbound.

## Notes

* One backend instance: the in-process event bus only fans out within one
  process. For more, add Redis and set `EVENT_BUS=redis`, `REDIS_URL`.
* Schema changes use `create_all`; add Alembic before relying on in-place
  upgrades of a database holding data you need.
* WebSockets (live dashboard updates) work on Railway and Fly without extra
  configuration.
