"""
Document ORM model.

Stores metadata and extracted content for uploaded documents (BRD, API specs, PDFs).
The raw parsed content lives in `parsed_content` JSONB; the original file is stored
externally (object storage) and referenced via `storage_key`.
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finspark.models.base import Base, JSONBType, SoftDeleteMixin, TimestampMixin, uuid_pk


class Document(TimestampMixin, SoftDeleteMixin, Base):
    """Uploaded document with extracted structured content."""

    __tablename__ = "finspark_documents"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        comment="Owning tenant (not FK — tenant table may be in separate schema)",
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="docx | pdf | openapi_json | openapi_yaml | unknown",
    )
    storage_key: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        comment="Object-storage key; null until file is persisted",
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_content: Mapped[dict] = mapped_column(
        JSONBType(),
        nullable=False,
        default=dict,
        server_default="{}",
        comment="Structured output from the document parser",
    )
    parse_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        comment="pending | processing | done | failed",
    )
    word_count: Mapped[int] = mapped_column(nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        Index("ix_finspark_documents_tenant_id", "tenant_id"),
        Index("ix_finspark_documents_doc_type", "doc_type"),
        Index("ix_finspark_documents_parse_status", "parse_status"),
        CheckConstraint(
            "doc_type IN ('docx','pdf','openapi_json','openapi_yaml','unknown')",
            name="ck_finspark_documents_doc_type",
        ),
        CheckConstraint(
            "parse_status IN ('pending','processing','done','failed')",
            name="ck_finspark_documents_parse_status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Document id={self.id} tenant={self.tenant_id} "
            f"filename={self.filename!r} status={self.parse_status}>"
        )
