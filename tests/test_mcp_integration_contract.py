import asyncio

from app.main import app
from mcp_server import server as mcp_server


TOOL_ROUTE_CONTRACTS = {
    "get_morning_briefing": {"method": "get", "path": "/briefing/today", "query": set()},
    "list_members": {"method": "get", "path": "/members/", "query": {"q", "skip", "limit"}},
    "log_visitor": {"method": "post", "path": "/visitors/", "query": set()},
    "log_care_event": {"method": "post", "path": "/care/", "query": set()},
    "mark_contacted": {"method": "post", "path": "/care/{care_id}/contact", "query": set()},
    "add_prayer_request": {"method": "post", "path": "/care/prayers/", "query": set()},
    "add_member_note": {"method": "post", "path": "/members/{member_id}/notes", "query": set()},
    "draft_message": {"method": "get", "path": "/members/{member_id}/draft/care", "query": {"situation"}},
    "tell_marge": {"method": "post", "path": "/chat/", "query": set()},
}


def _openapi_routes():
    schema = app.openapi()
    return schema["paths"]


def _query_params_for(paths, path, method):
    operation = paths[path][method]
    return {
        p["name"]
        for p in operation.get("parameters", [])
        if p.get("in") == "query"
    }


def test_tool_api_paths_exist_in_openapi_schema():
    paths = _openapi_routes()

    for tool_name, contract in TOOL_ROUTE_CONTRACTS.items():
        assert contract["path"] in paths, f"{tool_name} path missing: {contract['path']}"
        assert contract["method"] in paths[contract["path"]], (
            f"{tool_name} method missing: {contract['method'].upper()} {contract['path']}"
        )


def test_tool_query_parameter_contracts_match_openapi_schema():
    paths = _openapi_routes()

    for tool_name, contract in TOOL_ROUTE_CONTRACTS.items():
        if not contract["query"]:
            continue
        actual_query_params = _query_params_for(paths, contract["path"], contract["method"])
        missing = contract["query"] - actual_query_params
        assert not missing, (
            f"{tool_name} query params missing from schema for {contract['path']}: {sorted(missing)}"
        )


def test_find_member_calls_members_with_q_param(monkeypatch):
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return []

    monkeypatch.setattr(mcp_server, "_get", fake_get)

    mcp_server._find_member("Alice")

    assert captured["path"] == "/members/"
    assert captured["params"] == {"q": "Alice"}


def test_list_members_tool_calls_members_with_q_param(monkeypatch):
    captured = {}

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return []

    monkeypatch.setattr(mcp_server, "_get", fake_get)

    asyncio.run(mcp_server.call_tool("list_members", {"search": "Bob"}))

    assert captured["path"] == "/members/"
    assert captured["params"] == {"q": "Bob"}


def test_draft_message_tool_supplies_default_situation(monkeypatch):
    calls = []

    def fake_find_member(_name):
        return {"id": 42, "full_name": "Alice Smith"}

    def fake_get(path, params=None):
        calls.append((path, params))
        if path == "/members/42/draft/care":
            return {"draft": "Test"}
        return []

    monkeypatch.setattr(mcp_server, "_find_member", fake_find_member)
    monkeypatch.setattr(mcp_server, "_get", fake_get)

    asyncio.run(mcp_server.call_tool("draft_message", {"member_name": "Alice"}))

    assert ("/members/42/draft/care", {"situation": "general"}) in calls


def test_draft_message_tool_passes_explicit_situation(monkeypatch):
    calls = []

    def fake_find_member(_name):
        return {"id": 42, "full_name": "Alice Smith"}

    def fake_get(path, params=None):
        calls.append((path, params))
        if path == "/members/42/draft/care":
            return {"draft": "Test"}
        return []

    monkeypatch.setattr(mcp_server, "_find_member", fake_find_member)
    monkeypatch.setattr(mcp_server, "_get", fake_get)

    asyncio.run(
        mcp_server.call_tool(
            "draft_message",
            {"member_name": "Alice", "situation": "hospital"},
        )
    )

    assert ("/members/42/draft/care", {"situation": "hospital"}) in calls


def _http_status_error(status_code: int, json_body: dict | None = None, text: str = ""):
    request = mcp_server.httpx.Request("GET", "http://localhost/test")
    if json_body is not None:
        response = mcp_server.httpx.Response(status_code, request=request, json=json_body)
    else:
        response = mcp_server.httpx.Response(status_code, request=request, text=text)
    return mcp_server.httpx.HTTPStatusError("boom", request=request, response=response)


def test_error_normalization_for_404_has_actionable_prompt():
    msg = mcp_server._normalize_api_error(_http_status_error(404, {"detail": "Member not found"}))
    assert "couldn't find" in msg.lower()
    assert "list_members" in msg


def test_error_normalization_for_500_has_retry_prompt():
    msg = mcp_server._normalize_api_error(_http_status_error(500, text="internal server error"))
    assert "having trouble" in msg.lower()
    assert "try again" in msg.lower()
