"""Document upload and parsing routes."""

import asyncio
import logging
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import (
    get_audit_service,
    get_document_parser,
    get_tenant_context,
    require_role,
)
from finspark.core import events
from finspark.core.audit import AuditService
from finspark.core.config import settings

logger = logging.getLogger(__name__)
from finspark.core.database import get_db
from finspark.models.document import Document
from finspark.schemas.common import APIResponse, DocType, TenantContext
from finspark.schemas.documents import (
    DocumentDetailResponse,
    DocumentUploadResponse,
    ParsedDocumentResult,
)
from finspark.services.parsing.document_parser import DocumentParser
from finspark.services.webhook_delivery import deliver_event

router = APIRouter(prefix="/documents", tags=["Documents"])

ALLOWED_EXTENSIONS = {".docx", ".pdf", ".yaml", ".yml", ".json"}


@router.post("/upload", response_model=APIResponse[DocumentUploadResponse])
async def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    doc_type: str = "brd",
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
    parser: DocumentParser = Depends(get_document_parser),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[DocumentUploadResponse]:
    """Upload and parse a document (BRD, SOW, API spec)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Sanitize filename to prevent path traversal
    safe_name = PurePosixPath(file.filename).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {suffix}. Allowed: {ALLOWED_EXTENSIONS}",
        )

    # Validate doc_type against the DocType enum
    try:
        DocType(doc_type)
    except ValueError:
        valid = [e.value for e in DocType]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid doc_type '{doc_type}'. Allowed: {valid}",
        )

    # Validate file size before writing to disk
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    file_bytes = await file.read()
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum upload size of {settings.max_upload_size_mb} MB",
        )

    # Save file
    upload_dir = settings.upload_dir / tenant.tenant_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / safe_name

    def _write_file(path: Path, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)

    await asyncio.to_thread(_write_file, file_path, file_bytes)

    # Create document record
    doc = Document(
        tenant_id=tenant.tenant_id,
        filename=safe_name,
        file_type=suffix.lstrip("."),
        file_size=file_path.stat().st_size,
        doc_type=doc_type,
        status="parsing",
    )
    db.add(doc)
    await db.flush()

    # Parse document — try LLM first for all doc types, fallback to regex
    try:
        use_llm = settings.ai_enabled and bool(settings.gemini_api_key)

        file_ext = suffix.lstrip(".")
        if file_ext in ("yaml", "yml", "json"):
            raw_text = file_bytes.decode("utf-8", errors="replace")
        else:
            raw_text = ""

        result = None
        if use_llm:
            try:
                from finspark.services.llm.client import get_llm_client

                llm_client = get_llm_client()
                # Get raw text for binary docs via regex parser first
                if not raw_text:
                    regex_result = await asyncio.to_thread(parser.parse, file_path, doc_type=doc_type)
                    raw_text = regex_result.summary

                result = await parser.parse_with_llm(raw_text, safe_name, llm_client)
                logger.info("document_parsed_via_llm filename=%s", safe_name)
            except Exception:
                logger.warning("LLM parsing failed for %s, falling back to regex", safe_name, exc_info=True)
                result = None

        if result is None:
            result = await asyncio.to_thread(parser.parse, file_path, doc_type=doc_type)

        doc.parsed_result = result.model_dump_json()
        doc.raw_text = result.summary[:5000]
        doc.status = "parsed"
    except Exception as e:
        doc.status = "failed"
        doc.error_message = str(e)

    await db.flush()

    webhook_data = {
        "tenant_id": tenant.tenant_id,
        "document_id": doc.id,
        "filename": doc.filename,
        "doc_type": doc_type,
    }
    # Always fire document.uploaded; fire document.parsed only on success
    await events.emit(events.DOCUMENT_UPLOADED, webhook_data)
    background_tasks.add_task(deliver_event, tenant.tenant_id, events.DOCUMENT_UPLOADED, webhook_data)
    if doc.status == "parsed":
        await events.emit(events.DOCUMENT_PARSED, webhook_data)
        background_tasks.add_task(deliver_event, tenant.tenant_id, events.DOCUMENT_PARSED, webhook_data)

    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="upload_document",
        resource_type="document",
        resource_id=doc.id,
        details={"filename": safe_name, "doc_type": doc_type, "status": doc.status},
    )

    return APIResponse(
        success=doc.status == "parsed",
        data=DocumentUploadResponse(
            id=doc.id,
            filename=doc.filename,
            file_type=suffix.lstrip("."),
            doc_type=doc_type,
            status=doc.status,
            created_at=doc.created_at,
        ),
        message=f"Document {doc.status}"
        if doc.status == "parsed"
        else doc.error_message or "Parsing failed",
    )


@router.get("/{document_id}", response_model=APIResponse[DocumentDetailResponse])
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[DocumentDetailResponse]:
    """Get document details and parsing results."""
    stmt = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    parsed = None
    if doc.parsed_result:
        parsed = ParsedDocumentResult.model_validate_json(doc.parsed_result)

    return APIResponse(
        data=DocumentDetailResponse(
            id=doc.id,
            filename=doc.filename,
            file_type=doc.file_type,
            doc_type=doc.doc_type,
            status=doc.status,
            parsed_result=parsed,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        ),
    )


@router.delete("/{document_id}", response_model=APIResponse[dict])
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = require_role("admin", "editor"),
    audit: AuditService = Depends(get_audit_service),
) -> APIResponse[dict]:
    """Delete a document and its uploaded file."""
    stmt = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant.tenant_id,
    )
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    filename = doc.filename
    file_path = settings.upload_dir / tenant.tenant_id / filename
    if file_path.exists():
        file_path.unlink()

    await db.delete(doc)
    await db.flush()

    await audit.log(
        tenant_id=tenant.tenant_id,
        actor=tenant.tenant_name,
        action="delete_document",
        resource_type="document",
        resource_id=document_id,
        details={"filename": filename},
    )

    return APIResponse(
        data={"id": document_id, "deleted": True},
        message=f"Document '{filename}' deleted",
    )


@router.get("/", response_model=APIResponse[list[DocumentUploadResponse]])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
    page: int | None = Query(None, ge=1, description="Page number (1-based). Omit for all results."),
    page_size: int | None = Query(None, ge=1, le=200, description="Items per page. Omit for all results."),
) -> APIResponse[list[DocumentUploadResponse]]:
    """List all documents for the current tenant."""
    stmt = (
        select(Document)
        .where(Document.tenant_id == tenant.tenant_id)
        .order_by(Document.created_at.desc())
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    docs = result.scalars().all()

    return APIResponse(
        data=[
            DocumentUploadResponse(
                id=d.id,
                filename=d.filename,
                file_type=d.file_type,
                doc_type=d.doc_type,
                status=d.status,
                created_at=d.created_at,
            )
            for d in docs
        ],
    )
