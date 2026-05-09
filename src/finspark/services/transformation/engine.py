"""Core transformation engine for field mapping pipelines."""

import logging
from dataclasses import dataclass, field
from typing import Any

from finspark.services.transformation.builtins import BUILTIN_TRANSFORMS
from finspark.services.transformation.sandbox import ExpressionSandbox

logger = logging.getLogger(__name__)


@dataclass
class FieldResult:
    source_field: str
    target_field: str
    status: str  # "success" | "error" | "skipped"
    original_value: Any
    transformed_value: Any
    error: str | None = None


@dataclass
class TransformResult:
    payload: dict[str, Any]
    field_results: list[FieldResult]
    success: bool
    errors: list[str] = field(default_factory=list)


class TransformationEngine:
    """Applies field-level transformations to source payloads."""

    def __init__(self) -> None:
        self._sandbox = ExpressionSandbox()

    def transform(
        self,
        source_payload: dict[str, Any],
        field_mappings: list[dict[str, Any]],
    ) -> TransformResult:
        payload: dict[str, Any] = {}
        field_results: list[FieldResult] = []
        errors: list[str] = []

        for mapping in field_mappings:
            result = self._process_mapping(mapping, source_payload)
            field_results.append(result)
            if result.status == "error":
                errors.append(
                    f"{result.source_field}->{result.target_field}: {result.error}"
                )
            if result.status != "error":
                payload[result.target_field] = result.transformed_value

        return TransformResult(
            payload=payload,
            field_results=field_results,
            success=len(errors) == 0,
            errors=errors,
        )

    def transform_value(self, value: Any, transform_name: str, **kwargs: Any) -> Any:
        if transform_name == "custom":
            expression = kwargs.get("expression", "")
            context = kwargs.get("context")
            return self._sandbox.evaluate(expression, value, context)

        if transform_name not in BUILTIN_TRANSFORMS:
            raise ValueError(f"Unknown transform: {transform_name!r}")

        return BUILTIN_TRANSFORMS[transform_name](value, **kwargs)

    def chain_transforms(self, value: Any, transforms: list[str]) -> Any:
        result = value
        for name in transforms:
            result = self.transform_value(result, name)
        return result

    def _process_mapping(
        self,
        mapping: dict[str, Any],
        source_payload: dict[str, Any],
    ) -> FieldResult:
        source_field = mapping["source_field"]
        target_field = mapping["target_field"]
        transform_name = mapping.get("transformation")
        default_value = mapping.get("default_value")

        if source_field not in source_payload:
            if default_value is not None:
                return FieldResult(
                    source_field=source_field,
                    target_field=target_field,
                    status="skipped",
                    original_value=None,
                    transformed_value=default_value,
                )
            return FieldResult(
                source_field=source_field,
                target_field=target_field,
                status="error",
                original_value=None,
                transformed_value=None,
                error=f"Source field '{source_field}' not found and no default",
            )

        original = source_payload[source_field]

        if not transform_name:
            return FieldResult(
                source_field=source_field,
                target_field=target_field,
                status="success",
                original_value=original,
                transformed_value=original,
            )

        try:
            kwargs: dict[str, Any] = {}
            if transform_name == "custom":
                kwargs["expression"] = mapping.get("custom_expression", "value")
                kwargs["context"] = source_payload
            transformed = self.transform_value(original, transform_name, **kwargs)
            return FieldResult(
                source_field=source_field,
                target_field=target_field,
                status="success",
                original_value=original,
                transformed_value=transformed,
            )
        except Exception as exc:
            return FieldResult(
                source_field=source_field,
                target_field=target_field,
                status="error",
                original_value=original,
                transformed_value=None,
                error=str(exc),
            )
