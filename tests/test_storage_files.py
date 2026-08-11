"""Architecture update: StorageAdapter, files metadata, BYOS references, search,
optional embeddings."""
from __future__ import annotations

import base64

import pytest

from tests.conftest import auth_headers


async def test_local_storage_roundtrip(tmp_path):
    from backend.storage.base import StorageError
    from backend.storage.local import LocalFilesystemStorage

    storage = LocalFilesystemStorage(str(tmp_path))
    st = await storage.write("a/b/report.txt", b"hello world")
    assert st.size_bytes == 11 and st.checksum
    assert await storage.read("a/b/report.txt") == b"hello world"
    assert await storage.list("a") == ["a/b/report.txt"]
    stat = await storage.stat("a/b/report.txt")
    assert stat.size_bytes == 11
    await storage.delete("a/b/report.txt")
    with pytest.raises(StorageError):
        await storage.read("a/b/report.txt")
    # path traversal blocked
    with pytest.raises(StorageError):
        await storage.read("../../etc/passwd")
    assert await storage.health_check()


async def test_file_upload_download_delete(client, auth):
    content = base64.b64encode(b"quarterly numbers").decode()
    resp = await client.post("/api/files/upload", json={
        "path": "reports/q3.txt", "content_base64": content, "mime_type": "text/plain",
    }, headers=auth)
    assert resp.status_code == 200, resp.text
    ref = resp.json()
    assert ref["size_bytes"] == 17
    assert ref["path"].startswith("users/")  # per-user prefix isolation

    listing = (await client.get("/api/files", headers=auth)).json()
    assert len(listing) == 1

    dl = (await client.get(f"/api/files/{ref['id']}/content", headers=auth)).json()
    assert base64.b64decode(dl["content_base64"]) == b"quarterly numbers"

    resp = await client.delete(f"/api/files/{ref['id']}", headers=auth)
    assert resp.json()["ok"] is True
    assert (await client.get("/api/files", headers=auth)).json() == []


async def test_file_tenancy_and_path_safety(client, auth):
    content = base64.b64encode(b"secret").decode()
    ref = (await client.post("/api/files/upload", json={
        "path": "private.txt", "content_base64": content,
    }, headers=auth)).json()
    h_b = await auth_headers(client, "fileb@example.com")
    assert (await client.get("/api/files", headers=h_b)).json() == []
    resp = await client.get(f"/api/files/{ref['id']}/content", headers=h_b)
    assert resp.status_code == 404
    # traversal rejected
    resp = await client.post("/api/files/upload", json={
        "path": "../escape.txt", "content_base64": content,
    }, headers=auth)
    assert resp.status_code == 400


async def test_byos_external_reference(client, auth):
    conn = (await client.post("/api/integrations", json={
        "integration_type": "s3", "name": "My Bucket",
        "configuration": {"bucket": "my-bucket", "endpoint_url": "https://minio.local"},
        "secret_headers": {"access_key_id": "AK", "secret_access_key": "SK"},
    }, headers=auth)).json()
    ref = (await client.post("/api/files/register-external", json={
        "connection_id": conn["id"], "path": "contracts/2026.pdf",
        "mime_type": "application/pdf", "size_bytes": 12345,
    }, headers=auth)).json()
    assert ref["storage_provider"] == f"byos:{conn['id']}"
    # the original stays external: Moseisley.sh holds metadata only
    resp = await client.get(f"/api/files/{ref['id']}/content", headers=auth)
    assert resp.status_code == 400


async def test_search_across_state(client, auth):
    await client.put("/api/documents", json={
        "path": "/projects/dental.md", "content_md": "# Dental AI receptionist project",
    }, headers=auth)
    result = (await client.get("/api/search", params={"q": "dental"}, headers=auth)).json()
    assert any("dental" in d["path"] for d in result["documents"])
    # tenancy
    h_b = await auth_headers(client, "searchb@example.com")
    result_b = (await client.get("/api/search", params={"q": "dental"}, headers=h_b)).json()
    assert result_b["documents"] == []


async def test_embeddings_optional_and_deterministic(client, auth, db_session):
    from tests.conftest import setup_mock_provider

    await setup_mock_provider(client, auth)
    me = (await client.get("/api/me", headers=auth)).json()
    from backend.providers.embeddings import resolve_embeddings

    provider = await resolve_embeddings(db_session, me["id"])
    v1 = await provider.embed(["hello"])
    v2 = await provider.embed(["hello"])
    assert v1 == v2 and len(v1[0]) == 8
    assert provider.model == "mock-embed-1"
