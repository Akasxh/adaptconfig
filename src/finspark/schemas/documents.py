"""Schemas for document upload and parsing."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from finspark.schemas.common import DocType, FileType


class ExtractRule(BaseModel):
    """Pull a value out of an endpoint's response and stash it in the chain context.

    json_path uses dotted-key notation (e.g., "data.access_token", "items.0.id").
    save_as is the key under which the value lives in the run-context dict; later
    endpoints reference it via inject templates like "{{access_token}}".
    """

    json_path: str
    save_as: str


class InjectRule(BaseModel):
    """Place a value from chain context into this endpoint's request.

    template is a Mustache-style string ("Bearer {{access_token}}"). location is
    where it goes in the outgoing request: header, query, path, or body.
    target_field is the key in that location to write to.
    """

    template: str
    location: str = "header"  # header | query | path | body
    target_field: str = ""


class ExtractedEndpoint(BaseModel):
    """An API endpoint extracted from a document."""

    id: str = ""  # Stable identifier for chain references (e.g., "oauth_token"). Auto-assigned if blank.
    path: str
    method: str = "GET"
    description: str = ""
    parameters: list[dict[str, str]] = []
    is_mandatory: bool = True
    depends_on: list[str] = []  # IDs of endpoints whose output this one needs
    extract: list[ExtractRule] = []  # Values to pull out of this endpoint's response
    inject: list[InjectRule] = []  # Values to plug into this endpoint's request from context


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
    base_url: str = ""  # Real API base URL (e.g., https://api.example.com/v1) when extractable.
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
