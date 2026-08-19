"""Mistral -> Document Intelligence: real OCR / structured extraction / Q&A
over a Moseisley-uploaded document. Guarded here:

  · extends the existing Mistral provider/`registry` (the same pattern
    `generate_with_x_search`/`transcribe_with_groq` established) rather
    than a parallel provider — `ocr_with_mistral` shares Mistral's
    credential resolution, kill switch, budget gate and paid-usage policy;
  · sources bytes from the SAME attachment pipeline Audio Intelligence uses
    (FileRef + owned storage) — never an arbitrary URL, never a BYOS
    reference, never Mistral's separate Files API (so no provider-side
    temporary file is ever created and no cleanup step is needed);
  · file type/size validation happens before a single byte reaches Mistral;
  · markdown/pages/tables are preserved exactly as Mistral returns them,
    never reparsed or fabricated;
  · structured extraction schemas are validated as plain JSON data;
  · document content is treated as untrusted DATA, never instructions,
    including in the document.ask grounding prompt;
  · upstream failures map to clean, structured, actionable states.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from backend.agents.orchestrator import (
    MANAGER_ONLY_TOOLS,
    TOOL_SCHEMAS,
    DocumentAskArgs,
    DocumentExtractArgs,
    DocumentReadArgs,
    _execute_tool,
)
from backend.core.models import LlmUsage, User
from backend.providers import document_intelligence as di
from backend.providers import usage_policy

MISTRAL_KEY = "mistral-secret-do-not-leak"
PDF_BYTES = b"%PDF-1.4 pretend this is a pdf"


def _ocr_response(*, pages: list[dict] | None = None, model: str = "mistral-ocr-latest",
                  pages_processed: int | None = 2, document_annotation: str | None = None) -> httpx.Response:
    if pages is None:
        pages = [
            {"index": 0, "markdown": "# Invoice\n\nTotal: $1,200"},
            {"index": 1, "markdown": "| Item | Qty |\n|---|---|\n| Widget | 3 |"},
        ]
    usage_info: dict = {}
    if pages_processed is not None:
        usage_info["pages_processed"] = pages_processed
    body: dict = {"model": model, "pages": pages, "usage_info": usage_info,
                 "document_annotation": document_annotation}
    return httpx.Response(200, json=body)


async def _uid(client, auth) -> str:
    return (await client.get("/api/me", headers=auth)).json()["id"]


async def _connect_mistral(client, headers, api_key: str = MISTRAL_KEY) -> None:
    r = await client.post("/api/providers", json={"provider": "mistral", "api_key": api_key},
                          headers=headers)
    assert r.status_code == 200, r.text


async def _allow_paid(client, headers) -> None:
    r = await client.put("/api/providers/policy", json={"policy": "paid_allowed"}, headers=headers)
    assert r.status_code == 200, r.text


async def _upload(client, headers, *, name: str = "invoice.pdf", content: bytes = PDF_BYTES,
                  mime_type: str = "application/pdf") -> str:
    r = await client.post("/api/files/upload", headers=headers, json={
        "path": f"docs/{name}", "content_base64": base64.b64encode(content).decode(),
        "title": name, "mime_type": mime_type,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _patch_mistral(monkeypatch, *, ocr=None, chat=None):
    """Route POSTs to api.mistral.ai by path — /ocr vs /chat/completions —
    to separate handlers. The test client's own ASGI calls pass through."""
    original_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        u = str(url)
        if "api.mistral.ai" not in u:
            return await original_post(self, url, **kwargs)
        if "/ocr" in u and ocr is not None:
            return ocr(u, kwargs.get("json") or {}, kwargs.get("headers") or {})
        if "/chat/completions" in u and chat is not None:
            return chat(u, kwargs.get("json") or {}, kwargs.get("headers") or {})
        raise AssertionError(f"unexpected mistral call: {u}")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


# ── 1/2/3. valid dispatch (PDF + image), the user's own credential ───────

async def test_valid_pdf_ocr_dispatch_uses_the_users_credential(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def ocr(url, body, headers):
        seen["url"], seen["body"], seen["headers"] = url, body, headers
        return _ocr_response()

    _patch_mistral(monkeypatch, ocr=ocr)
    result = await di.read(db_session, await _uid(client, auth), file_id)
    assert seen["url"] == "https://api.mistral.ai/v1/ocr"
    assert seen["headers"]["Authorization"] == f"Bearer {MISTRAL_KEY}"
    assert seen["body"]["document"]["type"] == "document_url"
    assert seen["body"]["document"]["document_url"].startswith("data:application/pdf;base64,")
    assert result["provider"] == "mistral"
    assert "Total: $1,200" in result["markdown"]


async def test_valid_png_ocr_dispatch(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth, name="scan.png", content=b"\x89PNG fake",
                            mime_type="image/png")
    seen = {}

    def ocr(url, body, headers):
        seen["body"] = body
        return _ocr_response(pages=[{"index": 0, "markdown": "scanned text"}])

    _patch_mistral(monkeypatch, ocr=ocr)
    result = await di.read(db_session, await _uid(client, auth), file_id)
    assert seen["body"]["document"]["type"] == "image_url"
    assert seen["body"]["document"]["image_url"].startswith("data:image/png;base64,")
    assert result["markdown"] == "scanned text"


# ── 4. missing connection ────────────────────────────────────────────────

async def test_missing_connection_is_provider_not_connected(client, auth, db_session):
    file_id = await _upload(client, auth)
    with pytest.raises(di.ProviderNotConnected):
        await di.read(db_session, await _uid(client, auth), file_id)


# ── 5. tenant credential isolation ───────────────────────────────────────

async def test_tenant_credential_isolation(client, db_session, monkeypatch):
    from tests.conftest import auth_headers

    auth_a = await auth_headers(client, "doc-tenant-a@example.com")
    auth_b = await auth_headers(client, "doc-tenant-b@example.com")
    await _connect_mistral(client, auth_a, "mistral-tenant-a-key")
    await _allow_paid(client, auth_a)
    await _connect_mistral(client, auth_b, "mistral-tenant-b-key")
    await _allow_paid(client, auth_b)
    file_a = await _upload(client, auth_a)
    file_b = await _upload(client, auth_b)

    seen_keys = []

    def ocr(url, body, headers):
        seen_keys.append(headers["Authorization"])
        return _ocr_response()

    _patch_mistral(monkeypatch, ocr=ocr)
    await di.read(db_session, await _uid(client, auth_a), file_a)
    assert seen_keys == ["Bearer mistral-tenant-a-key"]
    await di.read(db_session, await _uid(client, auth_b), file_b)
    assert seen_keys == ["Bearer mistral-tenant-a-key", "Bearer mistral-tenant-b-key"]


# ── 6. tenant file isolation ─────────────────────────────────────────────

async def test_attachment_tenant_isolation(client, db_session, monkeypatch):
    from tests.conftest import auth_headers

    auth_a = await auth_headers(client, "doc-file-a@example.com")
    auth_b = await auth_headers(client, "doc-file-b@example.com")
    await _connect_mistral(client, auth_b, "mistral-b-key")
    await _allow_paid(client, auth_b)
    file_a = await _upload(client, auth_a)

    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: _ocr_response())
    with pytest.raises(di.AttachmentNotFound):
        await di.read(db_session, await _uid(client, auth_b), file_a)


# ── 7/8. file type/size validation ───────────────────────────────────────

async def test_unsupported_file_type_is_rejected(client, auth, db_session):
    await _connect_mistral(client, auth)
    file_id = await _upload(client, auth, name="notes.docx",
                            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    with pytest.raises(di.UnsupportedFileType):
        await di.read(db_session, await _uid(client, auth), file_id)


async def test_oversized_file_is_rejected(client, auth, db_session):
    """Moseisley's own upload endpoint already caps inline uploads at 10MB —
    the same ceiling as di.MAX_DOCUMENT_BYTES — so a real upload can never
    exceed it. This covers the defense-in-depth metadata re-check directly."""
    from backend.core.models import FileRef
    from backend.storage.factory import get_owned_storage

    await _connect_mistral(client, auth)
    storage = get_owned_storage()
    uid = await _uid(client, auth)
    await storage.write(f"users/{uid}/docs/huge.pdf", PDF_BYTES)
    ref = FileRef(user_id=uid, storage_provider=storage.provider_name,
                  path=f"users/{uid}/docs/huge.pdf", title="huge.pdf",
                  mime_type="application/pdf", size_bytes=di.MAX_DOCUMENT_BYTES + 1)
    db_session.add(ref)
    await db_session.commit()

    with pytest.raises(di.FileTooLarge):
        await di.read(db_session, uid, ref.id)


# ── 9. current OCR model selection ───────────────────────────────────────

async def test_current_ocr_model_is_used_and_reported(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def ocr(url, body, headers):
        seen["model"] = body["model"]
        return _ocr_response(model="mistral-ocr-latest")

    _patch_mistral(monkeypatch, ocr=ocr)
    result = await di.read(db_session, await _uid(client, auth), file_id)
    assert seen["model"] == di.DEFAULT_MODEL == "mistral-ocr-latest"
    assert result["model"] == "mistral-ocr-latest"


# ── 10/11/12/13. normalization: text, pages, tables, page refs ──────────

async def test_extracted_markdown_is_normalized_across_pages(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: _ocr_response())
    result = await di.read(db_session, await _uid(client, auth), file_id)
    assert "Total: $1,200" in result["markdown"]
    assert "Widget" in result["markdown"]


async def test_multiple_pages_are_preserved_with_indices(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: _ocr_response())
    result = await di.read(db_session, await _uid(client, auth), file_id)
    assert result["page_count"] == 2
    assert result["pages"][0]["page_number"] == 0
    assert result["pages"][1]["page_number"] == 1
    assert result["pages"][0]["markdown"] == "# Invoice\n\nTotal: $1,200"


async def test_table_markdown_is_preserved_verbatim(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: _ocr_response())
    result = await di.read(db_session, await _uid(client, auth), file_id)
    assert "| Item | Qty |" in result["pages"][1]["markdown"]
    assert "| Widget | 3 |" in result["pages"][1]["markdown"]


async def test_page_references_stay_zero_indexed_and_ungrounded_pages_are_never_invented(
        client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers:
                   _ocr_response(pages=[{"index": 4, "markdown": "only page in the response"}]))
    result = await di.read(db_session, await _uid(client, auth), file_id)
    assert result["pages"] == [{"page_number": 4, "markdown": "only page in the response"}]


# ── 14/15/16. structured extraction ───────────────────────────────────────

async def test_structured_extraction_with_fields(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def ocr(url, body, headers):
        seen["body"] = body
        return _ocr_response(document_annotation=json.dumps(
            {"invoice_number": "INV-42", "total": "1200", "due_date": None}))

    _patch_mistral(monkeypatch, ocr=ocr)
    result = await di.extract(db_session, await _uid(client, auth), file_id,
                              fields=["invoice_number", "total", "due_date"])
    fmt = seen["body"]["document_annotation_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"]["properties"].keys() == {"invoice_number", "total", "due_date"}
    assert fmt["json_schema"]["schema"]["required"] == ["invoice_number", "total", "due_date"]
    assert fmt["json_schema"]["strict"] is True
    assert result["fields"] == {"invoice_number": "INV-42", "total": "1200", "due_date": None}


async def test_structured_extraction_with_raw_schema(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    seen = {}

    def ocr(url, body, headers):
        seen["schema"] = body["document_annotation_format"]["json_schema"]["schema"]
        return _ocr_response(document_annotation=json.dumps({"name": "Jane Doe"}))

    _patch_mistral(monkeypatch, ocr=ocr)
    result = await di.extract(db_session, await _uid(client, auth), file_id, schema=schema)
    assert seen["schema"] == schema
    assert result["fields"] == {"name": "Jane Doe"}


async def test_malformed_structured_response_is_reported_not_crashed_on(
        client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers:
                   _ocr_response(document_annotation="not valid json"))
    with pytest.raises(di.StructuredExtractionFailed):
        await di.extract(db_session, await _uid(client, auth), file_id, fields=["name"])


async def test_extract_requires_fields_or_schema(client, auth, db_session):
    await _connect_mistral(client, auth)
    file_id = await _upload(client, auth)
    with pytest.raises(di.InvalidDocumentRequest):
        await di.extract(db_session, await _uid(client, auth), file_id)


async def test_schema_is_validated_as_data_not_executable(client, auth, db_session):
    await _connect_mistral(client, auth)
    file_id = await _upload(client, auth)
    with pytest.raises(di.InvalidDocumentRequest):
        await di.extract(db_session, await _uid(client, auth), file_id,
                         schema={"not": "a schema shape"})


# ── 17/18/19/20/21/22/23. structured, honest error states ────────────────

async def test_invalid_key_maps_to_provider_key_invalid(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: httpx.Response(401))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await di.read(db_session, await _uid(client, auth), file_id)
    assert di.error_detail(exc.value)["state"] == "provider_key_invalid"


async def test_rate_limit_maps_to_rate_limited(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: httpx.Response(429, text="slow down"))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await di.read(db_session, await _uid(client, auth), file_id)
    assert di.error_detail(exc.value)["state"] == "rate_limited"


async def test_quota_exhaustion_uses_the_body_text_heuristic(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers:
                   httpx.Response(429, text="monthly plan limit exceeded"))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await di.read(db_session, await _uid(client, auth), file_id)
    assert di.error_detail(exc.value)["state"] == "quota_exhausted"


async def test_provider_timeout_maps_to_provider_timeout(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    original_post = httpx.AsyncClient.post

    async def timing_out(self, url, **kwargs):
        if "api.mistral.ai" not in str(url):
            return await original_post(self, url, **kwargs)
        raise httpx.TimeoutException("no response")

    monkeypatch.setattr(httpx.AsyncClient, "post", timing_out)
    with pytest.raises(httpx.TimeoutException) as exc:
        await di.read(db_session, await _uid(client, auth), file_id)
    assert di.error_detail(exc.value)["state"] == "provider_timeout"


async def test_provider_unavailable_from_5xx(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: httpx.Response(503))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await di.read(db_session, await _uid(client, auth), file_id)
    assert di.error_detail(exc.value)["state"] == "provider_unavailable"


async def test_empty_ocr_result_is_a_distinct_honest_state(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers:
                   _ocr_response(pages=[{"index": 0, "markdown": ""}]))
    with pytest.raises(di.EmptyDocument) as exc:
        await di.read(db_session, await _uid(client, auth), file_id)
    assert di.error_detail(exc.value)["state"] == "empty_document"


async def test_malformed_provider_response_is_reported_not_crashed_on(
        client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: httpx.Response(200, text="not json"))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await di.read(db_session, await _uid(client, auth), file_id)
    assert di.error_detail(exc.value)["state"] == "ocr_failed"


# ── 24/25/26. no Factory fallback, BYOK usage accounting, cost honesty ───

async def test_no_factory_fallback_and_usage_is_recorded(client, auth, db_session, monkeypatch):
    from sqlalchemy import select as sa_select

    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: _ocr_response())
    await di.read(db_session, await _uid(client, auth), file_id)
    rows = (await db_session.execute(sa_select(LlmUsage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].provider == "mistral"
    assert rows[0].purpose == "documents"


async def test_cost_is_never_fabricated_only_page_usage_is_reported(
        client, auth, db_session, monkeypatch):
    from sqlalchemy import select as sa_select

    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: _ocr_response(pages_processed=2))
    result = await di.read(db_session, await _uid(client, auth), file_id)
    row = (await db_session.execute(sa_select(LlmUsage))).scalar_one()
    assert row.cost_source == "UNKNOWN"
    assert row.provider_reported_cost is None
    assert result["pages_processed"] == 2


# ── 27/28. secret + private-path non-leakage ───────────────────────────────

async def test_secret_never_appears_in_errors(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth, "super-secret-mistral-key")
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: httpx.Response(401))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await di.read(db_session, await _uid(client, auth), file_id)
    assert "super-secret-mistral-key" not in str(exc.value)
    detail = di.error_detail(exc.value)
    assert "super-secret-mistral-key" not in detail["message"]


async def test_attachment_error_never_leaks_the_storage_path(client, auth, db_session):
    await _connect_mistral(client, auth)
    with pytest.raises(di.AttachmentNotFound) as exc:
        await di.read(db_session, await _uid(client, auth), "not-a-real-file-id")
    detail = di.error_detail(exc.value)
    assert "users/" not in detail["message"]
    assert "/" not in detail["message"]


# ── 29/30. no provider Files API is ever used, so no cleanup is needed ───

async def test_no_provider_files_api_is_ever_called(client, auth, db_session, monkeypatch):
    """§13: the document travels inline as a base64 data: URL — never
    Mistral's separate Files API. This test fails loudly if that ever
    changes, since /v1/files would show up as an unexpected call."""
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    calls = []
    original_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        u = str(url)
        if "api.mistral.ai" in u:
            calls.append(u)
            if "/ocr" in u:
                return _ocr_response()
            raise AssertionError(f"unexpected mistral call: {u}")
        return await original_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    await di.read(db_session, await _uid(client, auth), file_id)
    assert calls == ["https://api.mistral.ai/v1/ocr"]
    assert not any("/files" in c for c in calls)


async def test_no_files_api_call_even_after_ocr_failure(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    calls = []
    original_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        u = str(url)
        if "api.mistral.ai" in u:
            calls.append(u)
            return httpx.Response(500)
        return await original_post(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(Exception):  # noqa: PT011, B017
        await di.read(db_session, await _uid(client, auth), file_id)
    assert calls == ["https://api.mistral.ai/v1/ocr"]


def test_no_files_api_client_code_exists_in_the_adapter():
    import inspect

    source = inspect.getsource(di)
    assert "mistral.ai/v1/files" not in source
    assert '"/files"' not in source
    assert "files.upload" not in source and "files.delete" not in source


# ── 31/32/33. tool registration, kill switch, policy ──────────────────────

def test_tools_are_registered_and_broadly_available():
    assert TOOL_SCHEMAS["document.read"] is DocumentReadArgs
    assert TOOL_SCHEMAS["document.extract"] is DocumentExtractArgs
    assert TOOL_SCHEMAS["document.ask"] is DocumentAskArgs
    assert "document.read" not in MANAGER_ONLY_TOOLS
    assert "document.extract" not in MANAGER_ONLY_TOOLS
    assert "document.ask" not in MANAGER_ONLY_TOOLS


async def test_execute_tool_reachable_from_orchestrator_role(
        client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: _ocr_response())
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    args = DocumentReadArgs(file_id=file_id)
    out = await _execute_tool(db_session, user, "document.read", args, "run-1",
                              role="orchestrator")
    assert out["provider"] == "mistral"
    assert "error" not in out


async def test_kill_switch_blocks_document_tools_like_any_other(client, auth, db_session):
    from backend.core import killswitch

    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    await killswitch.set_switch(db_session, uid, killswitch.PAUSE_ALL_AGENTS, True)
    await db_session.commit()

    args = DocumentReadArgs(file_id=file_id)
    with pytest.raises(killswitch.KillSwitchEngaged):
        await _execute_tool(db_session, user, "document.read", args, "run-1",
                            role="orchestrator")


async def test_free_only_policy_blocks_document_ocr(client, auth, db_session):
    await _connect_mistral(client, auth)
    file_id = await _upload(client, auth)
    with pytest.raises(usage_policy.PaidCapabilityBlocked):
        await di.read(db_session, await _uid(client, auth), file_id)


async def test_execute_tool_maps_policy_block_to_actionable_state(client, auth, db_session):
    await _connect_mistral(client, auth)
    file_id = await _upload(client, auth)
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    args = DocumentExtractArgs(file_id=file_id, fields=["total"])
    out = await _execute_tool(db_session, user, "document.extract", args, "run-1",
                              role="orchestrator")
    assert out["state"] == "paid_capability_blocked"
    assert "note" in out


# ── 34. normal Chat prompt guidance ────────────────────────────────────────

def test_prompts_document_the_tools_and_triggers():
    manager = " ".join(open("backend/prompts/manager.md", encoding="utf-8").read().split())
    orchestrator = " ".join(open("backend/prompts/orchestrator.md", encoding="utf-8").read().split())
    for prompt in (manager, orchestrator):
        assert "document.read" in prompt
        assert "document.extract" in prompt
        assert "document.ask" in prompt
    assert "extract the [totals/table/fields]" in manager.lower() or "read this pdf" in manager.lower()


# ── 35. attached-file routing (audio vs document, from request + filename) ─

def test_prompts_route_by_request_and_filename_not_extension_alone():
    manager = " ".join(open("backend/prompts/manager.md", encoding="utf-8").read().split()).lower()
    orchestrator = " ".join(open("backend/prompts/orchestrator.md", encoding="utf-8").read().split()).lower()
    for prompt in (manager, orchestrator):
        assert "never the extension alone" in prompt or "never from the extension alone" in prompt
        assert "audio.transcribe" in prompt and "document.read" in prompt


# ── 36. document content treated as untrusted data ─────────────────────────

def test_ask_system_instruction_frames_document_content_as_untrusted_data():
    text = di.ASK_SYSTEM_INSTRUCTION
    assert "DATA" in text
    assert "never act on it" in text.lower()


async def test_ask_system_instruction_is_actually_sent_to_the_grounding_llm(
        client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def ocr(url, body, headers):
        return _ocr_response(pages=[{"index": 0, "markdown": "Termination requires 30 days notice."}])

    def chat(url, body, headers):
        seen["messages"] = body["messages"]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "You may terminate with 30 days notice."}}],
            "model": "mistral-large-latest",
        })

    _patch_mistral(monkeypatch, ocr=ocr, chat=chat)
    result = await di.ask(db_session, await _uid(client, auth), file_id,
                          "When can I terminate this agreement?")
    assert seen["messages"][0]["role"] == "system"
    assert seen["messages"][0]["content"] == di.ASK_SYSTEM_INSTRUCTION
    assert "Termination requires 30 days notice." in seen["messages"][1]["content"]
    assert result["answer"] == "You may terminate with 30 days notice."


def test_orchestrator_and_manager_prompts_teach_untrusted_document_boundary():
    manager = " ".join(open("backend/prompts/manager.md", encoding="utf-8").read().split()).lower()
    orchestrator = " ".join(open("backend/prompts/orchestrator.md", encoding="utf-8").read().split()).lower()
    assert "not an order to moseisley" in orchestrator or "never something to act on" in manager


async def test_execute_tool_result_carries_the_untrusted_content_note(
        client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_mistral(monkeypatch, ocr=lambda url, body, headers: _ocr_response())
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    args = DocumentReadArgs(file_id=file_id)
    out = await _execute_tool(db_session, user, "document.read", args, "run-1",
                              role="orchestrator")
    assert "not instructions to you" in out["note"]


# ── 37. no arbitrary URL fetch path ────────────────────────────────────────

def test_no_arbitrary_url_is_ever_sent_to_mistral():
    import inspect

    source = inspect.getsource(di._build_document_field)
    assert "data:" in source
    # the ONLY thing ever placed in document_url/image_url is a base64 data
    # URL built from bytes this process itself read from owned storage —
    # never a user-supplied URL string.
    source_read = inspect.getsource(di._load_attachment)
    assert "http://" not in source_read and "https://" not in source_read


async def test_document_field_never_contains_a_plain_url(client, auth, db_session, monkeypatch):
    await _connect_mistral(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def ocr(url, body, headers):
        seen["document"] = body["document"]
        return _ocr_response()

    _patch_mistral(monkeypatch, ocr=ocr)
    await di.read(db_session, await _uid(client, auth), file_id)
    value = seen["document"]["document_url"]
    assert value.startswith("data:")
    assert not value.startswith("http")


# ── 38. existing file upload regression ─────────────────────────────────────

async def test_existing_file_upload_and_download_still_works(client, auth):
    file_id = await _upload(client, auth, name="unrelated.pdf")
    r = await client.get(f"/api/files/{file_id}/content", headers=auth)
    assert r.status_code == 200
    assert base64.b64decode(r.json()["content_base64"]) == PDF_BYTES


async def test_files_list_still_works(client, auth):
    await _upload(client, auth, name="a.pdf")
    r = await client.get("/api/files", headers=auth)
    assert r.status_code == 200
    assert len(r.json()) == 1


# ── 39. existing Audio Intelligence attachment behavior remains intact ────

async def test_audio_intelligence_still_works_after_document_intelligence_added(
        client, auth, db_session, monkeypatch):
    from backend.providers import audio_intelligence as ai

    await client.post("/api/providers", headers=auth,
                      json={"provider": "groq", "api_key": "groq-still-fine"})
    await _allow_paid(client, auth)
    r = await client.post("/api/files/upload", headers=auth, json={
        "path": "audio/still-fine.mp3",
        "content_base64": base64.b64encode(b"pretend mp3").decode(),
        "title": "still-fine.mp3", "mime_type": "audio/mpeg",
    })
    file_id = r.json()["id"]
    original_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        if "api.groq.com" not in str(url):
            return await original_post(self, url, **kwargs)
        return httpx.Response(200, json={"text": "still transcribing fine",
                                         "segments": [{"start": 0.0, "end": 1.0,
                                                      "text": "still transcribing fine"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert result["text"] == "still transcribing fine"
    assert result["provider"] == "groq"


def test_audio_and_document_tools_both_registered_without_collision():
    assert TOOL_SCHEMAS["audio.transcribe"] is not TOOL_SCHEMAS["document.read"]
    assert set(TOOL_SCHEMAS) >= {
        "audio.transcribe", "audio.translate",
        "document.read", "document.extract", "document.ask",
    }
