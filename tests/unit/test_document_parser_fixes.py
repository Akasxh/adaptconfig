"""Tests for document parser bug fixes (issue #72)."""

import pytest

from finspark.services.parsing.document_parser import DocumentParser


@pytest.fixture
def parser() -> DocumentParser:
    return DocumentParser()


class TestDocTypeNormalization:
    """Verify doc_type='auto' and invalid values don't crash Pydantic."""

    def test_parse_text_with_auto_doc_type(self, parser: DocumentParser) -> None:
        result = parser.parse_text("Sample BRD content with pan_number field", doc_type="auto")
        assert result.doc_type.value == "brd"

    def test_parse_text_with_invalid_doc_type(self, parser: DocumentParser) -> None:
        result = parser.parse_text("Some document text", doc_type="nonexistent_type")
        assert result.doc_type.value == "brd"

    def test_parse_text_with_valid_doc_type_unchanged(self, parser: DocumentParser) -> None:
        result = parser.parse_text("Some document text", doc_type="sow")
        assert result.doc_type.value == "sow"


class TestResolveRefRemoveprefix:
    """Verify _resolve_ref uses removeprefix instead of lstrip."""

    def test_resolve_ref_with_hash_prefix(self) -> None:
        spec = {
            "components": {
                "schemas": {
                    "Foo": {"type": "object", "properties": {"bar": {"type": "string"}}}
                }
            }
        }
        result = DocumentParser._resolve_ref("#/components/schemas/Foo", spec)
        assert result == {"type": "object", "properties": {"bar": {"type": "string"}}}

    def test_resolve_ref_with_hash_like_component_name(self) -> None:
        """lstrip('#/') would strip leading '#' and '/' characters from path parts.
        removeprefix only strips the exact '#/' prefix."""
        spec = {
            "#hash_component": {
                "data": {"value": 42}
            }
        }
        # With lstrip, "#hash_component" would be incorrectly stripped to "hash_component"
        # With removeprefix, "#/" is removed and "#hash_component" is looked up correctly
        result = DocumentParser._resolve_ref("#/#hash_component/data", spec)
        assert result == {"value": 42}

    def test_resolve_ref_non_local_returns_empty(self) -> None:
        result = DocumentParser._resolve_ref("http://example.com/schema", {})
        assert result == {}
