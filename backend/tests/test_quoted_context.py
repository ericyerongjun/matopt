import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.quoted_context import PREFIX, quoted_context_markdown
from app.runtime import get_runtime


@pytest.mark.parametrize("fmt", ["markdown", "ipynb"])
@pytest.mark.parametrize("commands", ["", "/sympy-compute /python "])
async def test_quoted_conversation_exports_as_readable_passages(fmt, commands):
    rt = get_runtime()
    cid = rt.db.create_conversation(title="Quoted export check")["id"]
    text = commands + PREFIX + json.dumps({"version": 1, "quotes": [
        {"source": "assistant", "text": "First line\n<question>literal</question> $x^2$"},
        {"source": "user", "text": "Another passage"},
    ], "question": "Why is this true?"})
    rt.db.add_message(cid, "user", {"text": text, "attachments": []})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/conversations/{cid}/export", params={"format": fmt})
        assert response.status_code == 200
        exported = response.text if fmt == "markdown" else "\n".join(
            "".join(cell["source"]) for cell in response.json()["cells"])
    assert "> First line\n> &lt;question&gt;literal&lt;/question&gt; $x^2$" in exported
    assert "> Another passage\n\nWhy is this true?" in exported
    assert PREFIX not in exported and '"version"' not in exported
    if commands:
        assert commands.strip() in exported
    assert rt.db.list_messages(cid)[0]["content"]["text"] == text


@pytest.mark.parametrize("text", [
    "Explain <quoted_passage>literal</quoted_passage> without hiding the rest.",
    PREFIX + '{"version":2,"quotes":[],"question":"keep this"}',
    PREFIX + '{"version":1,"quotes":[{"source":"assistant","text":"quote"}],"question":"why?"}\nextra prose',
    PREFIX + '{"version":1,"quotes":[{"source":"assistant","text":"quote"}],"question":"why?","extra":"keep this"}',
    'ordinary prose ' + PREFIX + '{"version":1,"quotes":[{"source":"user","text":"quote"}],"question":"why?"}',
])
def test_unrecognised_quoted_text_is_never_dropped_from_exports(text):
    assert quoted_context_markdown(text) == text
