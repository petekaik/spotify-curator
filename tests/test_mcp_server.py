"""MCP server: tool surface, tuning round trip, error mapping and HTTP auth."""

from unittest.mock import patch

import pytest
from mcp.client import Client
from starlette.testclient import TestClient

from src.discovery import ranking
from src.mcp_server import http_app, server

EXPECTED_TOOLS = {
    "get_status",
    "refresh_profile",
    "discover",
    "create_mood_playlist",
    "create_weekly_playlists",
    "get_reports",
    "get_tuning",
    "update_tuning",
}


@pytest.fixture
def tuning_file(tmp_path, monkeypatch):
    path = tmp_path / "tuning.json"
    monkeypatch.setattr(ranking, "TUNING_PATH", path)
    return path


@pytest.mark.asyncio
async def test_lists_all_tools_with_hints():
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    assert tools["discover"].annotations.read_only_hint is True
    assert tools["create_mood_playlist"].annotations.read_only_hint is False
    mood_schema = tools["create_mood_playlist"].input_schema["properties"]["mood"]
    assert "calming" in str(mood_schema)


@pytest.mark.asyncio
async def test_tuning_round_trip(tuning_file):
    async with Client(server) as client:
        r = await client.call_tool(
            "update_tuning",
            {"changes": {"mainstream_penalty": -0.5, "mood_tags": {"focus": "post-rock"}}},
        )
        assert not r.is_error
        got = (await client.call_tool("get_tuning", {})).structured_content
    assert got["tuning"]["mainstream_penalty"] == -0.5
    assert got["tuning"]["mood_tags"]["focus"] == "post-rock"
    assert got["tuning"]["mood_tags"]["calming"] == "ambient"  # untouched moods keep defaults
    assert tuning_file.exists()


@pytest.mark.asyncio
async def test_invalid_tuning_is_an_mcp_error(tuning_file):
    async with Client(server) as client:
        r = await client.call_tool("update_tuning", {"changes": {"mainstream_penalty": 5}})
    assert r.is_error
    assert "mainstream_penalty" in r.content[0].text
    assert not tuning_file.exists()


@pytest.mark.asyncio
async def test_tool_error_dict_becomes_mcp_error():
    with patch(
        "src.hermes_skill.tools.spotify_curator_status",
        return_value={"error": "not_authenticated", "details": "run auth"},
    ):
        async with Client(server) as client:
            r = await client.call_tool("get_status", {})
    assert r.is_error
    assert "not_authenticated" in r.content[0].text


def test_http_refuses_public_bind_without_token():
    with pytest.raises(SystemExit, match="SPOTIFY_CURATOR_MCP_TOKEN"):
        http_app("0.0.0.0", None)


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_http_localhost_without_token_accepts_local_and_blocks_rebinding():
    with TestClient(http_app("127.0.0.1", None)) as c:
        local = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "127.0.0.1:8788"})
        assert local.status_code == 200
        evil = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Host": "evil.example:8788"})
        assert evil.status_code == 421


def test_http_requires_bearer_token_except_health():
    with TestClient(http_app("0.0.0.0", "s3cret")) as c:
        assert c.get("/health").status_code == 200
        assert c.post("/mcp", json={}).status_code == 401
        assert c.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "Authorization": "Bearer s3cret",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert ok.status_code != 401
