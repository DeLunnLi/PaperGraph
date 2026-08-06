
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from ...models.schemas import (
    PaperReaderChatRequest,
    PaperReaderChatResponse,
    PaperReaderFeedbackRequest,
    PaperReaderFeedbackResponse,
    PaperReaderHistoryItem,
    PaperReaderHistoryResponse,
    PaperReaderOpeningRequest,
    PaperReaderOpeningResponse,
)
from ..dependencies import get_database, get_db_path
from ..deps import require_user, check_rate_limit
from ...utils.common import route_errors
from ...services.reader.paper_reader_service import PaperReaderService

router = APIRouter(prefix="/ai", tags=["AI 分析"])


def get_paper_reader_service() -> PaperReaderService:
    db = get_database()
    return PaperReaderService(db=db)


@router.post("/paper-reader/opening", response_model=PaperReaderOpeningResponse)
async def paper_reader_opening(
    request: Request,
    body: PaperReaderOpeningRequest,
    background_tasks: BackgroundTasks,
    service: PaperReaderService = Depends(get_paper_reader_service),
    user: dict = Depends(require_user),
):
    check_rate_limit(request.client.host if request.client else "unknown", max_requests=20)
    with route_errors("paper_reader_opening"):
        result = await service.get_opening(
            paper_id=int(body.paper_id),
            user_id=user["user_id"],
            background_tasks=background_tasks,
        )
        return PaperReaderOpeningResponse(success=True, **result)

@router.post("/paper-reader/chat", response_model=PaperReaderChatResponse)
async def paper_reader_chat(
    request: Request,
    body: PaperReaderChatRequest,
    background_tasks: BackgroundTasks,
    service: PaperReaderService = Depends(get_paper_reader_service),
    user: dict = Depends(require_user),
):
    check_rate_limit(request.client.host if request.client else "unknown", max_requests=15)
    with route_errors("paper_reader_chat"):
        out = await service.process_chat(
            paper_id=int(body.paper_id),
            user_id=user["user_id"],
            messages=list(body.messages or []),
            user_message=body.user_message,
            background_tasks=background_tasks,
        )
        return PaperReaderChatResponse(
            success=True,
            reply=str(out.get("reply") or "").strip(),
            pdf_parsing=bool(out.get("pdf_parsing", False)),
            related_papers=list(out.get("related_papers") or []),
            citations=list(out.get("citations") or []),
            assistant_turn_id=out.get("assistant_turn_id"),
        )

@router.post("/paper-reader/feedback", response_model=PaperReaderFeedbackResponse)
async def paper_reader_feedback(
    body: PaperReaderFeedbackRequest,
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
):
    """Record 👍/👎/citation-click feedback and attribute it to the memories
    injected into that turn (utility adjusted by the LLM judge)."""
    from starlette.concurrency import run_in_threadpool

    from ...services.reader.reader_feedback_service import record_reader_feedback

    with route_errors("paper_reader_feedback"):
        out = await run_in_threadpool(
            record_reader_feedback,
            db_path=db_path,
            user_id=user["user_id"],
            paper_id=int(body.paper_id),
            turn_id=int(body.turn_id),
            signal=body.signal,
        )
        return PaperReaderFeedbackResponse(
            success=bool(out.get("success")),
            affected_memory_count=int(out.get("affected_memory_count") or 0),
        )

@router.get("/paper-reader/history", response_model=PaperReaderHistoryResponse)
async def paper_reader_history(
    paper_id: int = Query(..., ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
    service: PaperReaderService = Depends(get_paper_reader_service),
    user: dict = Depends(require_user),
):
    with route_errors("paper_reader_history"):
        turns = await service.get_history(paper_id=int(paper_id), limit=int(limit), user_id=user["user_id"])
        return PaperReaderHistoryResponse(
            success=True,
            paper_id=int(paper_id),
            turns=[PaperReaderHistoryItem(**t) for t in turns],
        )
