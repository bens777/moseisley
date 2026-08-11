"""Self-hosted authentication flows (architecture update): registration, login,
verification, password reset, logout/session invalidation, rate limiting."""
from __future__ import annotations

from backend.core.email import MemoryEmailProvider
from tests.conftest import TEST_PASSWORD


async def test_register_login_logout_invalidates_session(client):
    resp = await client.post("/api/auth/register",
                             json={"email": "new@example.com", "password": TEST_PASSWORD})
    assert resp.status_code == 201
    resp = await client.post("/api/auth/login",
                             data={"username": "new@example.com", "password": TEST_PASSWORD})
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/me", headers=headers)).status_code == 200

    # logout deletes the DB session token → old token is dead (real invalidation)
    resp = await client.post("/api/auth/logout", headers=headers)
    assert resp.status_code in (200, 204)
    assert (await client.get("/api/me", headers=headers)).status_code == 401


async def test_wrong_password_rejected(client):
    await client.post("/api/auth/register",
                      json={"email": "wp@example.com", "password": TEST_PASSWORD})
    resp = await client.post("/api/auth/login",
                             data={"username": "wp@example.com", "password": "wrong-password"})
    assert resp.status_code == 400


async def test_short_password_rejected(client):
    resp = await client.post("/api/auth/register",
                             json={"email": "sp@example.com", "password": "short"})
    assert resp.status_code == 400


async def test_verification_email_sent_and_verify(client):
    await client.post("/api/auth/register",
                      json={"email": "verify@example.com", "password": TEST_PASSWORD})
    mails = [m for m in MemoryEmailProvider.sent if m["to"] == "verify@example.com"]
    assert mails and "Verify" in mails[0]["subject"]
    token = mails[0]["body"].strip().split("\n")[-1]
    resp = await client.post("/api/auth/verify", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["is_verified"] is True


def _reset_token_from_mail(mail: dict) -> str:
    """Extract the token from the reset link in the plain-text body."""
    import re

    m = re.search(r"/reset-password\?token=([^\s\"<]+)", mail["body"])
    assert m, mail["body"]
    return m.group(1)


async def test_forgot_and_reset_password(client):
    await client.post("/api/auth/register",
                      json={"email": "fp@example.com", "password": TEST_PASSWORD})
    resp = await client.post("/api/auth/forgot-password", json={"email": "fp@example.com"})
    assert resp.status_code == 202
    mails = [m for m in MemoryEmailProvider.sent
             if m["to"] == "fp@example.com" and m["subject"] == "Reset your moseisley.sh password"]
    assert mails
    # email carries a link (text + html button), never a password
    assert "/reset-password?token=" in mails[-1]["body"]
    assert mails[-1]["html"] and "Reset password" in mails[-1]["html"]
    assert TEST_PASSWORD not in mails[-1]["body"]
    token = _reset_token_from_mail(mails[-1])
    resp = await client.post("/api/auth/reset-password",
                             json={"token": token, "password": "new-secure-password-1"})
    assert resp.status_code == 200
    # old password no longer works, new one does
    bad = await client.post("/api/auth/login",
                            data={"username": "fp@example.com", "password": TEST_PASSWORD})
    assert bad.status_code == 400
    good = await client.post("/api/auth/login",
                             data={"username": "fp@example.com", "password": "new-secure-password-1"})
    assert good.status_code == 200


async def test_reset_token_single_use_and_invalidates_others(client):
    await client.post("/api/auth/register",
                      json={"email": "su@example.com", "password": TEST_PASSWORD})
    await client.post("/api/auth/forgot-password", json={"email": "su@example.com"})
    await client.post("/api/auth/forgot-password", json={"email": "su@example.com"})
    mails = [m for m in MemoryEmailProvider.sent
             if m["to"] == "su@example.com" and "Reset" in m["subject"]]
    first, second = _reset_token_from_mail(mails[-2]), _reset_token_from_mail(mails[-1])
    resp = await client.post("/api/auth/reset-password",
                             json={"token": second, "password": "after-reset-password-1"})
    assert resp.status_code == 200
    # same token cannot be reused
    resp = await client.post("/api/auth/reset-password",
                             json={"token": second, "password": "another-password-xyz1"})
    assert resp.status_code == 400
    # and the other outstanding token died with the password change
    resp = await client.post("/api/auth/reset-password",
                             json={"token": first, "password": "another-password-xyz1"})
    assert resp.status_code == 400


async def test_forgot_password_never_reveals_account_existence(client):
    """Same response whether or not the account exists (§security)."""
    await client.post("/api/auth/register",
                      json={"email": "exists@example.com", "password": TEST_PASSWORD})
    r_exists = await client.post("/api/auth/forgot-password", json={"email": "exists@example.com"})
    r_missing = await client.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
    assert r_exists.status_code == r_missing.status_code == 202
    assert r_exists.json() == r_missing.json()
    # no email was sent for the nonexistent account
    assert not any(m["to"] == "ghost@example.com" for m in MemoryEmailProvider.sent)


async def test_password_never_stored_plain(client, db_session):
    await client.post("/api/auth/register",
                      json={"email": "hash@example.com", "password": TEST_PASSWORD})
    from sqlalchemy import select

    from backend.core.models import User

    user = (await db_session.execute(
        select(User).where(User.email == "hash@example.com"))).scalar_one()
    assert TEST_PASSWORD not in user.hashed_password
    assert user.hashed_password.startswith("$argon2") or user.hashed_password.startswith("$2b")


async def test_auth_rate_limiting(client):
    for _ in range(10):
        await client.post("/api/auth/login",
                          data={"username": "rl@example.com", "password": "bad"})
    resp = await client.post("/api/auth/login",
                             data={"username": "rl@example.com", "password": "bad"})
    assert resp.status_code == 429
