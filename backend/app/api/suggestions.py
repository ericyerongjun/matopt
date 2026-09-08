"""GET /api/suggestions — starter prompts for the welcome screen."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.llm.base import ChatOptions
from app.llm.registry import providers

router = APIRouter(prefix="/api", tags=["suggestions"])

FALLBACK = [
    "Solve $x^3 - 6x^2 + 11x - 6 = 0$ and verify the roots numerically",
    "Plot $f(x)=\\sin(x)/x$ and its Taylor approximations on $[-10, 10]$",
    "Simulate 10,000 draws to estimate $\\mathbb{E}[\\max(X,Y)]$ for standard normals",
    "Explain gradient descent and show it converging on a quadratic bowl",
]

PROMPT = (
    "Generate {count} short, diverse example tasks a user might ask an agent that can run Python, "
    "plot, analyse data and read PDFs, covering mathematics, statistics, machine learning and computer science. "
    "Each under 100 characters, in {language}. Wrap any math in $...$. Return ONLY a JSON array of strings."
)


@router.get("/suggestions")
async def suggestions(
    count: int = Query(4, ge=1, le=8),
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    language: str = Query("English"),
):
    try:
        spec, m = providers.resolve(provider, model)
        p = providers.get(spec.id)
        raw = await p.complete_text(
            [{"role": "user", "content": PROMPT.format(count=count, language=language)}],
            ChatOptions(model=m.id, reasoning="off", max_tokens=400),
        )
        items = _parse_list(raw)
        if items:
            return {"suggestions": items[:count]}
    except ValueError:
        return {"suggestions": FALLBACK[:count]}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"suggestions": FALLBACK[:count]}


def _parse_list(raw: str) -> list[str]:
    raw = raw.strip()
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end != -1:
        try:
            data = json.loads(raw[start:end + 1])
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [line.strip(" -•\t\"'") for line in raw.splitlines() if line.strip()]
