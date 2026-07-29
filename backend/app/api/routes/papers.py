
import logging


import anyio
from fastapi import APIRouter, Query, BackgroundTasks, Request, Depends, Header, HTTPException, UploadFile, File, Form
from starlette.concurrency import run_in_threadpool
from ...utils.common import route_errors, safe_http_500
from ...models.schemas import (
    DeletePaperResponse,
    LibraryCategoriesResponse,
    Paper,
    PapersResponse,
    ReadStatus,
    SavePapersRequest,
    SavePapersResponse,
    LocalPdfImportResponse,
    LibraryGraphResponse,
    UpdatePaperRequest,
    UpdatePaperResponse,
    DailyPapersRequest,
    DailyPapersResponse,
    DailyRecommendFeedbackRequest,
    DailyRecommendFeedbackResponse,
    ReadingCalendarItem,
    ReadingLogRequest,
    ReadingCalendarResponse,
)

from ...services.papers.papers_converters import api_paper_to_litpaper, litpaper_to_api_paper

from ...services.papers.papers_helpers import (
    daily_paper_identity_sig,
)
from ...services.graph.graph_service import build_library_graph
from ...services.papers.papers_library_service import (
    build_library_pdf_response_service,
    delete_paper_by_id,
    ensure_paper_pdf,
    get_library as get_library_service,
    get_paper_by_id,
    list_library_categories as list_library_categories_service,
    save_papers as save_papers_service,
    update_paper_by_id,
)
from ...services.papers.local_pdf_import import (
    cleanup_temp_file,
    import_local_pdf,
    save_upload_to_temp,
)
from ...services.daily.daily_auto_refresh import get_daily_compute_lock
from ...services.daily.daily_service import (
    compute_daily_papers as compute_daily_service,
    read_daily_cached_or_204 as get_daily_cached_or_204_service,
    record_user_daily_feedback as record_daily_feedback_service,
)
from ...settings import get_settings
from ...services.auth.user_service import create_pdf_access_ticket, get_user_from_token, verify_pdf_access_ticket
from ..dependencies import get_database, get_db_path, get_searcher
from ..deps import require_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/papers", tags=["文献管理"])

class DailyServices:
    def __init__(self, db_path=Depends(get_db_path), searcher=Depends(get_searcher)):
        self.db_path = db_path
        self.searcher = searcher

@router.get("/graph/library", response_model=LibraryGraphResponse)
def library_graph(
    limit: int = Query(default=200, ge=1, le=1000),
    category: str | None = Query(default=None),
    include_authors: bool = Query(default=False),
    include_keywords: bool = Query(default=False),
    relation_edge_limit: int = Query(default=400, ge=0, le=5000),
    focus_paper_id: int | None = Query(default=None, ge=1),
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    with route_errors("library_graph"):
        return build_library_graph(
            db=db,
            limit=int(limit),
            category=category,
            include_authors=bool(include_authors),
            include_keywords=bool(include_keywords),
            relation_edge_limit=int(relation_edge_limit),
            focus_paper_id=focus_paper_id,
            user_id=user["user_id"],
        )

@router.get("/library/categories", response_model=LibraryCategoriesResponse)
def list_library_categories(db=Depends(get_database), user: dict = Depends(require_user)):
    return list_library_categories_service(db=db, user_id=user["user_id"])

@router.get("/library", response_model=PapersResponse)
def get_library(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    q: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    read_status: ReadStatus | None = None,
    tags: str | None = Query(default=None, description="逗号分隔标签"),
    category: str | None = Query(default=None, description="领域筛选"),
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    return get_library_service(
        db=db,
        litpaper_to_api_paper_fn=litpaper_to_api_paper,
        limit=limit,
        offset=offset,
        q=q,
        year_from=year_from,
        year_to=year_to,
        read_status=read_status,
        tags=tags,
        category=category,
        user_id=user["user_id"],
    )

@router.post("/save", response_model=SavePapersResponse)
async def save_papers(
    request: SavePapersRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    with route_errors("save_papers"):
        # save_papers runs synchronous LLM/HTTP/PDF work (classify, Tavily, KG,
        # download) — run it off the event loop so other requests stay responsive.
        return await run_in_threadpool(
            save_papers_service,
            db=db,
            request=request,
            background_tasks=background_tasks,
            api_to_lit_fn=api_paper_to_litpaper,
            litpaper_to_api_paper_fn=litpaper_to_api_paper,
            user_id=user["user_id"],
        )

@router.post("/import-pdf", response_model=LocalPdfImportResponse)
async def import_pdf_to_library(
    file: UploadFile = File(...),
    category: str | None = Form(default=None),
    auto_enrich: bool = Form(default=True),
    auto_classify: bool = Form(default=True),
    db=Depends(get_database),
    searcher=Depends(get_searcher),
    user: dict = Depends(require_user),
):
    filename = (file.filename or "paper.pdf").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="仅支持 PDF 文件")
    temp_path: str | None = None
    try:
        temp_path = await anyio.to_thread.run_sync(
            save_upload_to_temp,
            file.file,
            filename,
            get_settings().data_dir,
        )
        result = await import_local_pdf(
            temp_path=temp_path,
            original_filename=filename,
            category=category,
            auto_enrich=bool(auto_enrich),
            auto_classify=bool(auto_classify),
            user_id=user["user_id"],
            db=db,
            searcher=searcher,
        )
        temp_path = None
        return LocalPdfImportResponse(
            success=True,
            message=(
                "PDF 已导入文献库"
                if result.added
                else "已匹配现有文献并关联 PDF"
                if result.pdf_attached
                else "文献与 PDF 已存在，已保留原有文件"
            ),
            paper=litpaper_to_api_paper(result.paper),
            added=result.added,
            pdf_attached=result.pdf_attached,
            metadata_source=result.metadata_source,
            detected_doi=result.extracted.doi,
            detected_arxiv_id=result.extracted.arxiv_id,
            page_count=result.extracted.page_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_http_500("import_pdf_to_library", exc)
    finally:
        cleanup_temp_file(temp_path)
        await file.close()


@router.get("/daily")
async def daily_papers_get(db_path=Depends(get_db_path), user: dict = Depends(require_user)):
    logger.info("HTTP GET /api/papers/daily")
    return await get_daily_cached_or_204_service(db_path=db_path, user_id=user["user_id"])

@router.post("/daily", response_model=DailyPapersResponse)
async def daily_papers(
    body: DailyPapersRequest,
    services: DailyServices = Depends(),
    settings=Depends(get_settings),
    user: dict = Depends(require_user),
):
    logger.info(
        "HTTP POST /api/papers/daily force_refresh=%s",
        getattr(body, "force_refresh", False),
    )
    lock = get_daily_compute_lock()
    async with lock:
        try:
            with anyio.fail_after(180.0):
                resp = await compute_daily_service(
                body=body, db_path=services.db_path, searcher=services.searcher,
                daily_paper_identity_sig_fn=daily_paper_identity_sig,
                daily_arxiv_cs_categories=settings.get_daily_arxiv_cs_categories(),
                papergraph_to_api_fn=litpaper_to_api_paper, logger=logger,
                user_id=user["user_id"],
            )
        except TimeoutError:
            err_msg = "每日论文计算超时（>180s），请稍后重试或缩小范围"
            raise HTTPException(status_code=504, detail=err_msg)
        except HTTPException:
            raise
        except Exception as e:
            raise safe_http_500("daily_papers", e)
        else:
            return resp

@router.post("/reading/log")
def log_reading_session(body: ReadingLogRequest, db_path=Depends(get_db_path), user: dict = Depends(require_user)):
    from ...services.reading_log.log import append_session
    append_session(db_path, paper_id=int(body.paper_id), user_id=user["user_id"], duration_sec=int(body.duration_sec),
                   client_ts=int(body.client_ts) if body.client_ts is not None else None)
    return {"success": True}

@router.get("/reading/calendar", response_model=ReadingCalendarResponse)
def reading_calendar(days: int = Query(default=180, ge=7, le=366), db_path=Depends(get_db_path), user: dict = Depends(require_user)):
    from ...services.reading_log.log import list_daily_aggregate
    items = list_daily_aggregate(db_path, user_id=user["user_id"], days=int(days))
    return ReadingCalendarResponse(success=True, days=int(days),
                                   items=[ReadingCalendarItem(**x) for x in items])

@router.post("/{paper_id}/ensure-pdf", response_model=Paper)
async def ensure_paper_library_pdf(
    paper_id: int,
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    return await anyio.to_thread.run_sync(
        lambda: ensure_paper_pdf(
            db=db,
            paper_id=paper_id,
            user_id=user["user_id"],
            litpaper_to_api_paper_fn=litpaper_to_api_paper,
        )
    )


@router.post("/{paper_id}/pdf-ticket")
async def create_paper_pdf_ticket(
    paper_id: int,
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    if not db.get_paper_by_id(paper_id, user_id=user["user_id"]):
        raise HTTPException(status_code=404, detail="文献不存在")
    return {
        "success": True,
        "ticket": create_pdf_access_ticket(user_id=user["user_id"], paper_id=paper_id),
        "expires_in": 300,
    }


@router.get("/{paper_id}/library-pdf")
async def get_paper_library_pdf(
    paper_id: int,
    request: Request,
    ticket: str | None = Query(default=None, min_length=20, max_length=1024),
    authorization: str | None = Header(default=None),
    db_path=Depends(get_db_path),
):
    ticket_data = verify_pdf_access_ticket(ticket or "", paper_id=paper_id)
    user_id = ticket_data["user_id"] if ticket_data else None
    if user_id is None and authorization:
        raw_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else authorization.strip()
        user = get_user_from_token(raw_token)
        user_id = int(user["user_id"]) if user else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="PDF 访问票据无效或已过期")
    return build_library_pdf_response_service(
        paper_id=paper_id,
        user_id=user_id,
        request=request,
        db_path=db_path,
        logger_obj=logger,
    )

@router.get("/{paper_id}", response_model=Paper)
def get_paper(paper_id: int, db=Depends(get_database), user: dict = Depends(require_user)):
    return get_paper_by_id(db=db, paper_id=paper_id, user_id=user["user_id"], litpaper_to_api_paper_fn=litpaper_to_api_paper)

@router.put("/{paper_id}", response_model=UpdatePaperResponse)
def update_paper(paper_id: int, body: UpdatePaperRequest, db=Depends(get_database), user: dict = Depends(require_user)):
    return update_paper_by_id(db=db, paper_id=paper_id, user_id=user["user_id"], body=body)

@router.delete("/{paper_id}", response_model=DeletePaperResponse)
def delete_paper(paper_id: int, db=Depends(get_database), user: dict = Depends(require_user)):
    return delete_paper_by_id(db=db, paper_id=paper_id, user_id=user["user_id"])

@router.post("/daily/feedback", response_model=DailyRecommendFeedbackResponse)
async def record_daily_recommend_feedback(
    body: DailyRecommendFeedbackRequest,
    db_path=Depends(get_db_path),
    user: dict = Depends(require_user),
):
    with route_errors("record_daily_recommend_feedback"):
        return await record_daily_feedback_service(body=body, db_path=db_path, user_id=user["user_id"])
