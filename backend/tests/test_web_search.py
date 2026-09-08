from app.agent.monitor import inspect_tool_call, next_step_options
from app.agent.tools.web_search import _format_hits, _safe_url


def test_safe_url_rejects_local():
    assert _safe_url("https://example.com/a")
    assert not _safe_url("http://127.0.0.1/x")
    assert not _safe_url("file:///etc/passwd")
    assert not _safe_url("https://192.168.0.4/admin")
    assert not _safe_url("https://172.16.1.9/admin")


def test_format_hits():
    text = _format_hits([{"title": "A", "href": "https://ex.com", "body": "hello"}])
    assert "A" in text and "https://ex.com" in text


def test_monitor_blocks_destructive_bash():
    v = inspect_tool_call("bash", {"command": "rm -rf /"})
    assert v.level == "block"
    v = inspect_tool_call("python", {"code": "print(1+1)"})
    assert v.level == "ok"


def test_next_step_options_from_plan():
    opts = next_step_options(
        [{"content": "Fit a GLM", "status": "pending"}, {"content": "Done", "status": "completed"}],
        "",
    )
    assert opts == ["Fit a GLM"]
