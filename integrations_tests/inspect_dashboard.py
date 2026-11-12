"""Inspect every dashboard page in a real (headless) browser.

Signs in through the login page like a user would, visits every route with
whatever live data the backend holds, and reports per page: console errors,
failed API requests, suspicious rendered text ("undefined", "NaN",
"[object Object]"), and a screenshot.

    python integrations_tests/inspect_dashboard.py --dashboard http://localhost:3100 \
        --api http://127.0.0.1:8000 --email you@x.com --password ... --out shots/
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

SUSPICIOUS = re.compile(r"\bundefined\b|\bNaN\b|\[object Object\]|Invalid Date")

STATIC_PAGES = [
    "/dashboard", "/agents", "/activity", "/monitor", "/tasks", "/approvals",
    "/incidents", "/interventions", "/reliability", "/supervisor", "/integrations",
    "/policies", "/settings",
]


def api_get(api: str, token: str, path: str):
    req = urllib.request.Request(api + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def detail_pages(api: str, token: str) -> list[tuple[str, str]]:
    """One detail page per platform and kind, chosen from live data."""
    pages: list[tuple[str, str]] = []
    sessions = api_get(api, token, "/v1/executions?limit=200")
    seen: set[str] = set()
    for s in sessions:
        if s["platform"] not in seen and s["event_count"] > 3:
            seen.add(s["platform"])
            pages.append((f"/executions/{s['id']}", f"session ({s['platform']})"))
    agents = api_get(api, token, "/v1/agents")
    if agents:
        pages.append((f"/agents/{agents[0]['id']}", f"agent ({agents[0]['platform']})"))
    incidents = api_get(api, token, "/v1/incidents")
    if incidents:
        pages.append((f"/incidents/{incidents[0]['id']}", f"incident ({incidents[0]['category']})"))
    return pages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard", required=True)
    parser.add_argument("--api", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    problems = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        console: list[str] = []
        failed: list[str] = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error",) else None)
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
        page.on("response", lambda r: failed.append(f"{r.status} {r.request.method} {r.url}")
                if r.status >= 400 and "/v1/" in r.url else None)

        # --- sign in through the real login page -------------------------
        page.goto(f"{args.dashboard}/login", wait_until="networkidle")
        page.fill('input[type="email"]', args.email)
        page.fill('input[type="password"]', args.password)
        page.screenshot(path=str(out / "00-login.png"))
        page.click('button[type="submit"]')
        page.wait_for_url("**/dashboard", timeout=20000)
        token = page.evaluate("() => localStorage.getItem('reliability.token')")
        print(f"signed in: token {'present' if token else 'MISSING'}")

        routes = [(r, r.strip("/")) for r in STATIC_PAGES] + detail_pages(args.api, token)
        for i, (route, label) in enumerate(routes, start=1):
            console.clear()
            failed.clear()
            page.goto(f"{args.dashboard}{route}", wait_until="networkidle")
            page.wait_for_timeout(2500)  # live data + websocket
            text = page.inner_text("main") if page.query_selector("main") else page.inner_text("body")
            odd = sorted(set(SUSPICIOUS.findall(text)))
            shot = out / f"{i:02d}-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}.png"
            page.screenshot(path=str(shot), full_page=True)
            status = "ok" if not (console or failed or odd) else "ISSUES"
            problems += status != "ok"
            heading = text.strip().splitlines()[0] if text.strip() else "(empty)"
            print(f"[{status}] {route:<52} {heading[:40]!r} chars={len(text)}")
            for c in console[:5]:
                print(f"        console  {c[:200]}")
            for f in failed[:5]:
                print(f"        http     {f[:200]}")
            if odd:
                print(f"        text     suspicious: {odd}")
        browser.close()
    print(f"\n{len(routes)} pages inspected, {problems} with issues")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
