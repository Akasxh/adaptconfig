"""Field mapping engine - intelligently maps source fields to target adapter fields."""

import json
import re
from typing import Any

from rapidfuzz import fuzz, process

from finspark.schemas.configurations import FieldMapping

# Domain-specific field synonyms for Indian fintech
FIELD_SYNONYMS: dict[str, list[str]] = {
    "pan_number": ["pan", "pan_no", "pan_card", "permanent_account_number", "pan_id", "applicant_pan"],
    "aadhaar_number": ["aadhaar", "aadhaar_no", "aadhar", "uid", "aadhaar_id", "applicant_aadhaar"],
    "gstin": ["gst_number", "gst_no", "gst_id", "gst_in"],
    "mobile_number": ["mobile", "phone", "phone_number", "mobile_no", "contact_number", "cell", "applicant_mobile"],
    "email_address": ["email", "email_id", "mail", "email_addr", "applicant_email"],
    "full_name": ["name", "applicant_name", "customer_name", "borrower_name", "full_name"],
    "date_of_birth": ["dob", "birth_date", "date_of_birth", "birthdate", "applicant_dob"],
    "address": ["address", "residential_address", "current_address", "permanent_address"],
    "loan_amount": ["amount", "loan_amount", "principal", "requested_amount", "sanctioned_amount", "requested_loan_amount"],
    "loan_type": ["loan_type", "product_type", "loan_product_type", "product_code"],
    "account_number": ["account_no", "acct_number", "bank_account", "account_num"],
    "ifsc_code": ["ifsc", "ifsc_code", "bank_code", "branch_code"],
    "credit_score": ["score", "cibil_score", "credit_score", "bureau_score", "score_range"],
    "reference_id": ["ref_id", "reference", "ref_number", "reference_number", "txn_id"],
    "customer_id": ["cust_id", "customer_id", "client_id", "borrower_id"],
    "consent_id": ["consent", "consent_id", "consent_handle"],
    "applicants": ["applicants", "applicant_list", "batch_applicants"],
}

# Reverse map for quick lookup
_SYNONYM_REVERSE: dict[str, str] = {}
for canonical, synonyms in FIELD_SYNONYMS.items():
    _SYNONYM_REVERSE[canonical.lower()] = canonical
    for syn in synonyms:
        _SYNONYM_REVERSE[syn.lower()] = canonical


class FieldMapper:
    """Maps source document fields to target adapter fields using fuzzy + semantic matching."""

    def __init__(self, confidence_threshold: float = 0.6) -> None:
        self.confidence_threshold = confidence_threshold

    def map_fields(
        self,
        source_fields: list[dict[str, str]],
        target_fields: list[dict[str, str]],
    ) -> list[FieldMapping]:
        """Map source fields to target fields using multi-strategy matching."""
        mappings: list[FieldMapping] = []
        used_targets: set[str] = set()

        for source in source_fields:
            source_name = source.get("name", "")
            best_match = self._find_best_match(source_name, target_fields, used_targets)

            if best_match:
                target_name, confidence = best_match
                transformation = self._suggest_transformation(
                    source.get("type", "string"),
                    self._get_field_type(target_name, target_fields),
                )
                mappings.append(
                    FieldMapping(
                        source_field=source_name,
                        target_field=target_name,
                        transformation=transformation,
                        confidence=round(confidence, 2),
                        is_confirmed=confidence > 0.9,
                    )
                )
                used_targets.add(target_name)
            else:
                mappings.append(
                    FieldMapping(
                        source_field=source_name,
                        target_field="",
                        confidence=0.0,
                        is_confirmed=False,
                    )
                )

        return mappings

    def _find_best_match(
        self,
        source_name: str,
        target_fields: list[dict[str, str]],
        used_targets: set[str],
    ) -> tuple[str, float] | None:
        """Find the best matching target field for a source field."""
        available_targets = [t["name"] for t in target_fields if t["name"] not in used_targets]
        if not available_targets:
            return None

        # Strategy 1: Exact synonym match
        canonical = _SYNONYM_REVERSE.get(source_name.lower())
        if canonical:
            for target in available_targets:
                target_canonical = _SYNONYM_REVERSE.get(target.lower())
                if target_canonical == canonical:
                    return (target, 1.0)

        # Strategy 2: Fuzzy string matching
        result = process.extractOne(
            source_name.lower(),
            [t.lower() for t in available_targets],
            scorer=fuzz.token_sort_ratio,
        )
        if result and result[1] >= self.confidence_threshold * 100:
            # Find original case target name
            idx = [t.lower() for t in available_targets].index(result[0])
            return (available_targets[idx], result[1] / 100.0)

        # Strategy 3: Partial token matching
        source_tokens = set(re.split(r"[_\s]", source_name.lower()))
        best_score = 0.0
        best_target = None

        for target in available_targets:
            target_tokens = set(re.split(r"[_\s]", target.lower()))
            if source_tokens and target_tokens:
                overlap = len(source_tokens & target_tokens)
                total = len(source_tokens | target_tokens)
                score = overlap / total if total > 0 else 0.0
                if score > best_score and score >= self.confidence_threshold:
                    best_score = score
                    best_target = target

        if best_target:
            return (best_target, best_score)

        return None

    def _suggest_transformation(self, source_type: str, target_type: str) -> str | None:
        """Suggest a transformation rule based on type differences."""
        if source_type == target_type:
            return None

        type_transforms = {
            ("string", "number"): "parse_number",
            ("number", "string"): "to_string",
            ("string", "date"): "parse_date",
            ("date", "string"): "format_date",
            ("string", "boolean"): "parse_boolean",
            ("string", "email"): "validate_email",
            ("string", "phone"): "normalize_phone",
        }

        return type_transforms.get((source_type, target_type))

    @staticmethod
    def _get_field_type(field_name: str, fields: list[dict[str, str]]) -> str:
        for f in fields:
            if f["name"] == field_name:
                return f.get("type", "string")
        return "string"


class ConfigGenerator:
    """Generates integration configuration from parsed document + adapter."""

    def __init__(self) -> None:
        self.field_mapper = FieldMapper()

    def generate(
        self,
        parsed_result: dict[str, Any],
        adapter_version: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a complete integration configuration."""
        # Separate request fields (need mapping) from response fields (informational only)
        all_fields = parsed_result.get("fields", [])
        request_fields = [
            {"name": f.get("name", ""), "type": f.get("data_type", "string")}
            for f in all_fields
            if "request" in (f.get("source_section", "") or "").lower()
        ]
        # If no source_section info, use all fields as source
        if not request_fields:
            request_fields = [
                {"name": f.get("name", ""), "type": f.get("data_type", "string")}
                for f in all_fields
            ]
        source_fields = request_fields

        # Extract target fields from adapter schema
        target_fields = self._extract_adapter_fields(adapter_version)

        # Also extract from response schema for response field mapping
        response_target_fields = self._extract_response_fields(adapter_version)

        # Map request fields to adapter request schema
        mappings = self.field_mapper.map_fields(source_fields, target_fields)

        # Map response fields from document to adapter response schema
        response_doc_fields = [
            {"name": f.get("name", ""), "type": f.get("data_type", "string")}
            for f in all_fields
            if "response" in (f.get("source_section", "") or "").lower()
        ]
        if response_doc_fields and response_target_fields:
            response_mappings = self.field_mapper.map_fields(
                response_doc_fields, response_target_fields
            )
            mappings.extend(response_mappings)

        # Build configuration
        config = {
            "adapter_name": adapter_version.get("adapter_name", ""),
            "version": adapter_version.get("version", "v1"),
            "base_url": adapter_version.get("base_url", ""),
            "auth": {
                "type": adapter_version.get("auth_type", "api_key"),
                "credentials": {},  # Placeholder - filled by user
            },
            "endpoints": self._build_endpoint_configs(adapter_version),
            "field_mappings": [m.model_dump() for m in mappings],
            "transformation_rules": self._generate_transformations(mappings),
            "hooks": self._generate_default_hooks(),
            "retry_policy": {
                "max_retries": 3,
                "backoff_factor": 2,
                "retry_on_status": [429, 500, 502, 503],
            },
            "timeout_ms": 30000,
            "metadata": {
                "generated_from_document": True,
                "confidence_score": self._calculate_overall_confidence(mappings),
                "unmapped_fields": [m.source_field for m in mappings if not m.target_field],
            },
        }

        return config

    def _extract_adapter_fields(self, adapter_version: dict[str, Any]) -> list[dict[str, str]]:
        """Extract target fields from adapter request schema."""
        fields: list[dict[str, str]] = []
        schema = adapter_version.get("request_schema", {})

        if isinstance(schema, str):
            schema = json.loads(schema)

        properties = schema.get("properties", {})
        for name, prop in properties.items():
            fields.append(
                {
                    "name": name,
                    "type": prop.get("type", "string"),
                    "required": str(name in schema.get("required", [])),
                }
            )

        return fields

    def _extract_response_fields(self, adapter_version: dict[str, Any]) -> list[dict[str, str]]:
        """Extract target fields from adapter response schema."""
        fields: list[dict[str, str]] = []
        schema = adapter_version.get("response_schema", {})

        if isinstance(schema, str):
            schema = json.loads(schema)

        properties = schema.get("properties", {})
        for name, prop in properties.items():
            fields.append(
                {
                    "name": name,
                    "type": prop.get("type", "string"),
                    "required": "false",
                }
            )

        return fields

    def _build_endpoint_configs(self, adapter_version: dict[str, Any]) -> list[dict[str, Any]]:
        endpoints = adapter_version.get("endpoints", [])
        if isinstance(endpoints, str):
            endpoints = json.loads(endpoints)

        return [
            {
                "path": ep.get("path", ""),
                "method": ep.get("method", "POST"),
                "description": ep.get("description", ""),
                "enabled": True,
            }
            for ep in endpoints
        ]

    def _generate_transformations(self, mappings: list[FieldMapping]) -> list[dict[str, Any]]:
        rules = []
        for mapping in mappings:
            if mapping.transformation:
                rules.append(
                    {
                        "source": mapping.source_field,
                        "target": mapping.target_field,
                        "type": mapping.transformation,
                    }
                )
        return rules

    def _generate_default_hooks(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "log_request",
                "type": "pre_request",
                "handler": "audit_logger",
                "is_active": True,
                "order": 0,
            },
            {
                "name": "mask_pii",
                "type": "pre_request",
                "handler": "pii_masker",
                "is_active": True,
                "order": 1,
            },
            {
                "name": "validate_response",
                "type": "post_response",
                "handler": "schema_validator",
                "is_active": True,
                "order": 0,
            },
            {
                "name": "log_response",
                "type": "post_response",
                "handler": "audit_logger",
                "is_active": True,
                "order": 1,
            },
        ]

    @staticmethod
    def _calculate_overall_confidence(mappings: list[FieldMapping]) -> float:
        if not mappings:
            return 0.0
        total = sum(m.confidence for m in mappings)
        return round(total / len(mappings), 2)
