"""PaymentProvider abstraction (§76, §127).

Agents NEVER call a payment provider directly — only Treasury's execute path does,
and only after the deterministic policy decision. Real-money execution is
feature-flagged (REAL_PAYMENTS_ENABLED) and OFF by default; the simulated provider
is the V0.1 default and moves no money.
"""
from __future__ import annotations

import uuid

from backend.core.config import get_settings
from backend.core.models import SpendIntent


class PaymentError(Exception):
    pass


class PaymentProvider:
    provider_name = "base"

    async def authorize(self, spend_intent: SpendIntent) -> str:
        """Return an authorization reference or raise PaymentError."""
        raise NotImplementedError

    async def execute(self, spend_intent: SpendIntent) -> tuple[str, str]:
        """Return (status, provider_ref). status: SUCCESS | FAILED | UNKNOWN (§112)."""
        raise NotImplementedError

    async def status(self, transaction_ref: str) -> str:
        raise NotImplementedError


class SimulatedPaymentProvider(PaymentProvider):
    """Deterministic sandbox: authorizes and 'executes' without moving any money."""

    provider_name = "simulated"

    async def authorize(self, spend_intent: SpendIntent) -> str:
        return f"sim-auth-{uuid.uuid4().hex[:12]}"

    async def execute(self, spend_intent: SpendIntent) -> tuple[str, str]:
        return "SUCCESS", f"sim-tx-{uuid.uuid4().hex[:12]}"

    async def status(self, transaction_ref: str) -> str:
        return "SUCCESS"


class StripeTestPaymentProvider(PaymentProvider):
    """Stripe test-mode skeleton (§78, §127).

    Requires STRIPE_API_KEY (test key) and REAL_PAYMENTS_ENABLED=true even to run in
    test mode. Uses PaymentIntents in test mode; never stores card data (§75).
    External blocker: Stripe account + Issuing eligibility for real agent spending.
    """

    provider_name = "stripe_test"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def authorize(self, spend_intent: SpendIntent) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.stripe.com/v1/payment_intents",
                auth=(self.api_key, ""),
                data={
                    "amount": spend_intent.amount_cents,
                    "currency": spend_intent.currency.lower(),
                    "capture_method": "manual",
                    "description": f"Moseisley.sh spend intent {spend_intent.id}",
                    "automatic_payment_methods[enabled]": "true",
                    "automatic_payment_methods[allow_redirects]": "never",
                },
            )
        if resp.status_code != 200:
            raise PaymentError(f"stripe returned {resp.status_code}")
        return resp.json()["id"]

    async def execute(self, spend_intent: SpendIntent) -> tuple[str, str]:
        try:
            ref = await self.authorize(spend_intent)
            return "UNKNOWN", ref  # capture requires a payment method — §112: report honestly
        except PaymentError:
            return "FAILED", ""

    async def status(self, transaction_ref: str) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"https://api.stripe.com/v1/payment_intents/{transaction_ref}",
                                    auth=(self.api_key, ""))
        if resp.status_code != 200:
            return "UNKNOWN"
        s = resp.json().get("status", "")
        return {"succeeded": "SUCCESS", "canceled": "FAILED"}.get(s, "UNKNOWN")


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.payment_provider == "stripe_test" and settings.real_payments_enabled:
        if not settings.stripe_api_key:
            raise PaymentError("stripe_test selected but STRIPE_API_KEY missing")
        return StripeTestPaymentProvider(settings.stripe_api_key)
    return SimulatedPaymentProvider()
