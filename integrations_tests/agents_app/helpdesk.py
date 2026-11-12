"""A small but realistic OpenAI Agents SDK application: an IT helpdesk.

Written for this repository (no external code). It uses the SDK the way
production apps do: a triage agent that hands off to specialists, function
tools with real side effects on in-memory state, a dependency that is down,
a sensitive operation (refunds), and an input guardrail.

The supervisor is NOT referenced here - the app is supervised from outside,
exactly as an existing application would be.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from agents import (
    Agent,
    GuardrailFunctionOutput,
    RunContextWrapper,
    function_tool,
    input_guardrail,
)


@dataclass
class HelpdeskContext:
    customer_id: str = "C-1042"
    refunds: list[tuple[str, float]] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)


INVOICES = {
    "INV-3001": {"amount": 49.00, "status": "paid", "item": "Pro plan, October"},
    "INV-3002": {"amount": 49.00, "status": "paid", "item": "Pro plan, November"},
    "INV-3003": {"amount": 120.00, "status": "overdue", "item": "Team seats x3"},
}

SERVICES = {"email": "operational", "vpn": "degraded", "wiki": "operational"}


@function_tool
def lookup_invoice(invoice_id: str) -> str:
    """Look up an invoice by id (e.g. INV-3001)."""
    invoice = INVOICES.get(invoice_id.upper())
    if invoice is None:
        return f"No invoice {invoice_id}."
    return f"{invoice_id}: {invoice['item']}, ${invoice['amount']:.2f}, {invoice['status']}"


@function_tool
def issue_refund(ctx: RunContextWrapper[HelpdeskContext], invoice_id: str, amount: float) -> str:
    """Refund an amount against an invoice. Moves real money."""
    invoice = INVOICES.get(invoice_id.upper())
    if invoice is None:
        return f"No invoice {invoice_id}; nothing refunded."
    if amount > invoice["amount"]:
        return f"Refund of ${amount:.2f} exceeds the invoice total ${invoice['amount']:.2f}."
    ctx.context.refunds.append((invoice_id.upper(), amount))
    return f"Refunded ${amount:.2f} on {invoice_id.upper()}."


@function_tool
def service_status(service: str) -> str:
    """Current status of an internal service (email, vpn, wiki, sso)."""
    if service.lower() == "sso":
        # The status page for SSO is itself down - a dependency failure.
        raise ConnectionError("status.internal returned 503 Service Unavailable")
    return f"{service}: {SERVICES.get(service.lower(), 'unknown service')}"


@function_tool
def open_ticket(ctx: RunContextWrapper[HelpdeskContext], summary: str) -> str:
    """Open a support ticket for the engineering team."""
    ticket = f"T-{random.randint(5000, 5999)}"
    ctx.context.tickets.append(ticket)
    return f"Opened {ticket}: {summary}"


@input_guardrail
async def no_credentials(ctx, agent, user_input) -> GuardrailFunctionOutput:
    """Refuse requests that ask the helpdesk to reveal or reset passwords."""
    text = user_input if isinstance(user_input, str) else str(user_input)
    tripped = any(w in text.lower() for w in ("password", "passcode", "2fa code", "mfa code"))
    return GuardrailFunctionOutput(output_info={"credentials_request": tripped}, tripwire_triggered=tripped)


billing_agent = Agent[HelpdeskContext](
    name="Billing agent",
    handoff_description="Invoices, charges and refunds.",
    instructions=(
        "You handle billing. Look invoices up before answering. Only refund when the "
        "customer explicitly asks, and never more than the invoice total."
    ),
    tools=[lookup_invoice, issue_refund],
)

support_agent = Agent[HelpdeskContext](
    name="Support agent",
    handoff_description="Service outages, access problems, technical issues.",
    instructions=(
        "You handle technical support. Check service status before answering. If a "
        "service status check fails, say so and open a ticket rather than guessing."
    ),
    tools=[service_status, open_ticket],
)

triage_agent = Agent[HelpdeskContext](
    name="Triage agent",
    instructions=(
        "You are the helpdesk front door. Hand billing questions to the Billing agent "
        "and technical questions to the Support agent. Do not answer them yourself."
    ),
    handoffs=[billing_agent, support_agent],
    input_guardrails=[no_credentials],
)
