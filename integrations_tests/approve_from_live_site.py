"""Live workflow: a real agent waits for approval; a human approves it on the
deployed website.

A real OpenAI Agents SDK run (helpdesk app) asks to issue a refund, which the
policy says needs approval. Meanwhile a browser signs in to the deployed
dashboard, opens Approvals, and clicks Approve - exactly what an operator does.
Then the record is checked: approved by the signed-in user, refund executed.

    python integrations_tests/approve_from_live_site.py --dashboard https://... --email ... --password ...
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from openai_agents_live import API, api, configure_provider, helpdesk_profile, record  # noqa: E402


def operator(dashboard: str, email: str, password: str, log: list[str], done: threading.Event) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        ctx.grant_permissions(["local-network-access"], origin=dashboard)
        page = ctx.new_page()
        page.goto(f"{dashboard}/login", wait_until="networkidle")
        page.fill('input[type="email"]', email)
        page.fill('input[type="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_url("**/dashboard", timeout=30000)
        page.goto(f"{dashboard}/approvals", wait_until="networkidle")
        log.append("operator signed in to the live site and opened Approvals")
        deadline = time.time() + 120
        while time.time() < deadline and not done.is_set():
            button = page.get_by_role("button", name="Approve")
            if button.count():
                page.get_by_placeholder("Note to the agent (optional)").first.fill("Approved from the live dashboard")
                page.screenshot(path=str(Path(args.out) / "approval-waiting.png"), full_page=True)
                button.first.click()
                log.append("operator clicked Approve on the live site")
                page.wait_for_timeout(2500)
                page.screenshot(path=str(Path(args.out) / "approval-done.png"), full_page=True)
                break
            page.wait_for_timeout(1000)
        browser.close()


async def main_async(args) -> int:
    configure_provider()
    app = helpdesk_profile()
    key = api("POST", "/v1/integrations", {"platform": "openai_agents", "name": "live-site-approval"})["key"]
    from reliability.integrations.openai_agents import AgentsSupervisor

    sup = AgentsSupervisor(app_name="helpdesk", api_url=API, integration_key=key)
    api("PUT", "/v1/policies", {"supervision_mode": "control", "name": "live-site",
                                "configuration": {"approval_required_tools": ["issue_refund"], "approval_timeout_seconds": 120}})
    log: list[str] = []
    done = threading.Event()
    thread = threading.Thread(target=operator, args=(args.dashboard, args.email, args.password, log, done), daemon=True)
    thread.start()
    session = f"live-approve-{int(time.time())}"
    ctx = app.context()
    result = await sup.run(app.triage, app.approval_prompt, context=ctx, session_id=session)
    done.set()
    thread.join(timeout=30)
    await asyncio.sleep(1.5)
    rec = record(session)
    approvals = rec.get("approvals", [])
    refunded = ctx.refunds
    for line in log:
        print("  ", line)
    print("   agent:", str(result.final_output)[:200])
    for a in approvals:
        print(f"   approval {a['status']} by {a['decided_by']} — note: {a['note']}")
    print("   refunds actually executed by the tool:", refunded)
    ok = any(a["status"] == "approved" and a["decided_by"] == args.email for a in approvals) and bool(refunded)
    api("PUT", "/v1/policies", {"supervision_mode": "control", "name": "live-site", "configuration": {}})
    sup.close()
    print(f"\n=== {'ok' if ok else 'FAIL'}: approval made on the deployed website reached the running agent")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--out", default=".")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))
