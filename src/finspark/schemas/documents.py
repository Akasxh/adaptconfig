"""Schemas for document upload and parsing."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from finspark.schemas.common import DocType, FileType


class ExtractedEndpoint(BaseModel):
    """An API endpoint extracted from a document."""

    path: str
    method: str = "GET"
    description: str = ""
    parameters: list[dict[str, str]] = []
    is_mandatory: bool = True


class ExtractedField(BaseModel):
    """A data field extracted from a document."""

    name: str
    data_type: str = "string"
    description: str = ""
    is_required: bool = True
    sample_value: str = ""
    source_section: str = ""


class ExtractedAuth(BaseModel):
    """Authentication requirements extracted from a document."""

    auth_type: str = "api_key"  # api_key, oauth2, certificate, basic
    details: dict[str, str] = {}


class ParsedDocumentResult(BaseModel):
    """Structured result from document parsing."""

    doc_type: DocType
    title: str = ""
    summary: str = ""
    services_identified: list[str] = []
    endpoints: list[ExtractedEndpoint] = []
    fields: list[ExtractedField] = []
    auth_requirements: list[ExtractedAuth] = []
    security_requirements: list[str] = []
    sla_requirements: dict[str, str] = {}
    sections: dict[str, str] = {}
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_entities: list[str] = []


class DocumentUploadResponse(BaseModel):
    """Response after uploading a document."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: FileType
    doc_type: DocType
    status: str
    created_at: datetime


class DocumentDetailResponse(BaseModel):
    """Full document detail with parsing results."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    doc_type: str
    status: str
    parsed_result: ParsedDocumentResult | None = None
    created_at: datetime
    updated_at: datetime
