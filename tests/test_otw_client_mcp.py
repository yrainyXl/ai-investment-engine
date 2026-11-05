import pytest
from investment_engine.otw_client import OTWReadClient, OTWError


def test_mcp_transport_calls_only_otw_read_and_parses_json():
    client = OTWReadClient("https://otw.example", "TEST_TOKEN", transport="mcp")
    captured = {}
    client._request = lambda url, payload=None: (
        captured.update({"url": url, "payload": payload}) or
        {"result": {"isError": False, "content": [{"type": "text", "text": '{"strategies": []}'}]}}
    )
    assert client.get("/api/paper-trading/strategies") == {"strategies": []}
    assert captured["url"] == "https://otw.example/api/mcp"
    assert captured["payload"]["method"] == "tools/call"
    assert captured["payload"]["params"]["name"] == "otw_read"
    assert captured["payload"]["params"]["arguments"]["path"] == "/api/paper-trading/strategies"


def test_mcp_transport_rejects_tool_error():
    client = OTWReadClient("https://otw.example", "TEST_TOKEN", transport="mcp")
    client._request = lambda url, payload=None: {
        "result": {"isError": True, "content": [{"type": "text", "text": "denied"}]}
    }
    with pytest.raises(OTWError):
        client.get("/api/paper-trading/strategies")


def test_mcp_transport_rejects_invalid_json_content():
    client = OTWReadClient("https://otw.example", "TEST_TOKEN", transport="mcp")
    client._request = lambda url, payload=None: {
        "result": {"isError": False, "content": [{"type": "text", "text": "not-json"}]}
    }
    with pytest.raises(OTWError):
        client.get("/api/paper-trading/strategies")


def test_invalid_transport_is_rejected():
    with pytest.raises(OTWError):
        OTWReadClient("https://otw.example", "TEST_TOKEN", transport="shell")
