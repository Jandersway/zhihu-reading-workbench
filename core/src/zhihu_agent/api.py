from __future__ import annotations

from functools import lru_cache
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .agent import ZhihuAgent
from .config import Settings
from .corpus import Corpus
from .kimi import KimiClient, KimiError
from .sessions import RedisSessions


app = FastAPI(title="Zhihu Knowledge Agent", version="0.1.0")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64)


@lru_cache(maxsize=1)
def corpus_service() -> Corpus:
    return Corpus(Settings.from_env())


@lru_cache(maxsize=1)
def sessions_service() -> RedisSessions | None:
    url = Settings.from_env().redis_url
    return RedisSessions(url) if url else None


@app.get("/health")
def health() -> dict:
    try:
        corpus = corpus_service()
        sessions = sessions_service()
        if sessions:
            sessions.ping()
    except (OSError, ValueError, KimiError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=type(exc).__name__) from None
    return {
        "status": "ok",
        "articles": len(corpus.articles),
        "redis_sessions": sessions is not None,
        "kimi_configured": bool(Settings.from_env().kimi_api_key),
    }


@app.post("/ask")
async def ask(request: AskRequest) -> dict:
    try:
        settings = Settings.from_env()
        agent = ZhihuAgent(corpus_service(), KimiClient(settings.kimi_api_key, settings.kimi_base_url))
        sessions = sessions_service()
        session_id = request.session_id or str(uuid4())
        history = await run_in_threadpool(sessions.load, session_id) if sessions else []
        result = await run_in_threadpool(agent.ask, request.question, history=history)
        if sessions and result.completed:
            await run_in_threadpool(sessions.append_turn, session_id, request.question, result.answer)
        return {"session_id": session_id, **result.as_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except KimiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@app.get("/search")
def search(q: str, author: str = "", limit: int = 5) -> dict:
    if not 1 <= limit <= 8:
        raise HTTPException(status_code=400, detail="limit must be 1..8")
    return {"results": corpus_service().search(q, author=author, limit=limit)}


@app.get("/answers/{answer_id}")
def read_answer(answer_id: str, start: int = 0) -> dict:
    try:
        return corpus_service().read(answer_id, start=start)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="Answer not in the local index") from None
