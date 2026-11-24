from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from .. import maintenance
from ..auth import Auth
from ..db import get_session
from ..models import Summary
from ..schemas import SummarizeIn, SummaryOut
from .service import summarize_entry

router = APIRouter(prefix="/api/entries", tags=["summaries"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/{entry_id}/summarize")
async def summarize(
    entry_id: int,
    _: Auth,
    session: Session,
    force: bool = Query(False),
    body: Annotated[SummarizeIn | None, Body()] = None,
):
    """Flux SSE : événements `status`, `delta`, `done`, `error`. Corps facultatif : type de résumé,
    question libre, mode approfondi (sans corps : type par défaut des réglages)."""
    options = body or SummarizeIn()

    async def event_stream():
        # Verrou de maintenance pendant toute la génération : un import ne peut pas remplacer l'article
        # entre sa lecture et l'enregistrement du résumé.
        try:
            async with maintenance.shared():
                async for event in summarize_entry(
                    session, entry_id, force=force, style=options.style, question=options.question, deep=options.deep
                ):
                    yield {"event": event.type, "data": json.dumps(event.data, ensure_ascii=False)}
        except maintenance.Busy as exc:
            yield {"event": "error", "data": json.dumps({"message": str(exc)}, ensure_ascii=False)}

    return EventSourceResponse(event_stream(), ping=15)


@router.get("/{entry_id}/summary", response_model=SummaryOut | None)
async def latest_summary(entry_id: int, _: Auth, session: Session) -> SummaryOut | None:
    summary = (
        await session.execute(
            select(Summary).where(Summary.entry_id == entry_id).order_by(Summary.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return SummaryOut.model_validate(summary, from_attributes=True) if summary else None


@router.delete("/{entry_id}/summary", status_code=204)
async def delete_summaries(entry_id: int, _: Auth, session: Session) -> None:
    rows = (await session.execute(select(Summary).where(Summary.entry_id == entry_id))).scalars().all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucun résumé")
    for s in rows:
        await session.delete(s)
    await session.commit()
