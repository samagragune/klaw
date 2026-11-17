# Klaw dashboard

The Klaw website: landing page (`src/app/(site)`), documentation (`src/app/(site)/docs`,
content in `src/components/docs-content.tsx`), sign-in (`src/app/(auth)`), and the operator
console (`src/app/(console)`).

It is a static Next.js app. In the browser it talks directly to the Klaw supervisor the user
points it at (default `http://127.0.0.1:8000`, changeable on the sign-in page).

```bash
npm ci
npm run dev        # http://localhost:3000
npm run build
```

| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_KLAW_API_URL` | Default supervisor address (optional) |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | Enables the Google button (the supervisor must list the same ID in `GOOGLE_CLIENT_IDS`) |

Never set `NEXT_PUBLIC_KLAW_TOKEN` on a deployment: `NEXT_PUBLIC_` values are visible to every visitor.
