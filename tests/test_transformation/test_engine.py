"""Tests for the transformation engine."""

import pytest

from finspark.services.transformation.engine import TransformationEngine


@pytest.fixture
def engine() -> TransformationEngine:
    return TransformationEngine()


class TestBasicFieldMapping:
    def test_rename_without_transform(self, engine: TransformationEngine) -> None:
        result = engine.transform(
            {"old_name": "Rajesh"},
            [{"source_field": "old_name", "target_field": "new_name"}],
        )
        assert result.success
        assert result.payload == {"new_name": "Rajesh"}
        assert result.field_results[0].status == "success"

    def test_multiple_fields(self, engine: TransformationEngine) -> None:
        result = engine.transform(
            {"a": 1, "b": 2},
            [
                {"source_field": "a", "target_field": "x"},
                {"source_field": "b", "target_field": "y"},
            ],
        )
        assert result.success
        assert result.payload == {"x": 1, "y": 2}


class TestStringTransforms:
    def test_upper(self, engine: TransformationEngine) -> None:
        result = engine.transform(
            {"name": "rajesh"},
            [{"source_field": "name", "target_field": "name", "transformation": "upper"}],
        )
        assert result.payload["name"] == "RAJESH"

    def test_lower(self, engine: TransformationEngine) -> None:
        result = engine.transform(
            {"name": "RAJESH"},
            [{"source_field": "name", "target_field": "name", "transformation": "lower"}],
        )
        assert result.payload["name"] == "rajesh"

    def test_trim(self, engine: TransformationEngine) -> None:
        result = engine.transform(
            {"name": "  Rajesh  "},
            [{"source_field": "name", "target_field": "name", "transformation": "trim"}],
        )
        assert result.payload["name"] == "Rajesh"


class TestParseNumber:
    def test_integer(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("42", "parse_number") == 42

    def test_float(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("3.14", "parse_number") == 3.14

    def test_comma_separated(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("1,00,000", "parse_number") == 100000

    def test_invalid(self, engine: TransformationEngine) -> None:
        with pytest.raises(ValueError):
            engine.transform_value("not_a_number", "parse_number")


class TestParseDate:
    def test_dd_slash_mm_yyyy(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("15/05/1990", "parse_date") == "1990-05-15"

    def test_dd_dash_mm_yyyy(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("15-05-1990", "parse_date") == "1990-05-15"

    def test_iso_format(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("1990-05-15", "parse_date") == "1990-05-15"

    def test_dd_slash_mm_yy(self, engine: TransformationEngine) -> None:
        result = engine.transform_value("15/05/90", "parse_date")
        assert result.endswith("-05-15")

    def test_mm_slash_dd_yyyy(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("05/15/1990", "parse_date") == "1990-05-15"

    def test_invalid(self, engine: TransformationEngine) -> None:
        with pytest.raises(ValueError, match="Unable to parse date"):
            engine.transform_value("not-a-date", "parse_date")


class TestNormalizePhone:
    def test_ten_digits(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("9876543210", "normalize_phone") == "+919876543210"

    def test_with_leading_zero(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("09876543210", "normalize_phone") == "+919876543210"

    def test_already_e164(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("+919876543210", "normalize_phone") == "+919876543210"

    def test_with_country_code_dash(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("91-9876543210", "normalize_phone") == "+919876543210"

    def test_invalid(self, engine: TransformationEngine) -> None:
        with pytest.raises(ValueError, match="Unable to normalize phone"):
            engine.transform_value("123", "normalize_phone")


class TestMasking:
    def test_mask_aadhaar(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("123456789012", "mask_aadhaar") == "XXXX-XXXX-9012"

    def test_mask_aadhaar_invalid(self, engine: TransformationEngine) -> None:
        with pytest.raises(ValueError, match="12 digits"):
            engine.transform_value("12345", "mask_aadhaar")

    def test_mask_pan(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("ABCDE1234F", "mask_pan") == "XXXXX****F"

    def test_mask_pan_invalid(self, engine: TransformationEngine) -> None:
        with pytest.raises(ValueError, match="10 characters"):
            engine.transform_value("ABC", "mask_pan")


class TestCurrencyConversions:
    def test_paise_to_rupees(self, engine: TransformationEngine) -> None:
        assert engine.transform_value(5000000, "paise_to_rupees") == 50000.00

    def test_rupees_to_paise(self, engine: TransformationEngine) -> None:
        assert engine.transform_value(50000.00, "rupees_to_paise") == 5000000

    def test_small_amounts(self, engine: TransformationEngine) -> None:
        assert engine.transform_value(100, "paise_to_rupees") == 1.00
        assert engine.transform_value(1.00, "rupees_to_paise") == 100


class TestChainedTransforms:
    def test_trim_then_upper(self, engine: TransformationEngine) -> None:
        assert engine.chain_transforms("  hello  ", ["trim", "upper"]) == "HELLO"

    def test_parse_number_then_to_string(self, engine: TransformationEngine) -> None:
        assert engine.chain_transforms("42", ["parse_number", "to_string"]) == "42"


class TestMissingFields:
    def test_missing_with_default(self, engine: TransformationEngine) -> None:
        result = engine.transform(
            {},
            [{"source_field": "missing", "target_field": "out", "default_value": "N/A"}],
        )
        assert result.payload["out"] == "N/A"
        assert result.field_results[0].status == "skipped"

    def test_missing_without_default(self, engine: TransformationEngine) -> None:
        result = engine.transform(
            {},
            [{"source_field": "missing", "target_field": "out"}],
        )
        assert not result.success
        assert result.field_results[0].status == "error"
        assert "not found" in result.errors[0]


class TestErrorHandling:
    def test_unknown_transform(self, engine: TransformationEngine) -> None:
        result = engine.transform(
            {"a": "x"},
            [{"source_field": "a", "target_field": "b", "transformation": "nonexistent"}],
        )
        assert not result.success
        assert result.field_results[0].status == "error"
        assert "Unknown transform" in result.errors[0]


class TestJsonTransforms:
    def test_to_json_string(self, engine: TransformationEngine) -> None:
        assert engine.transform_value({"key": "val"}, "to_json_string") == '{"key": "val"}'

    def test_from_json_string(self, engine: TransformationEngine) -> None:
        assert engine.transform_value('{"key": "val"}', "from_json_string") == {"key": "val"}


class TestBooleanParsing:
    def test_true_variants(self, engine: TransformationEngine) -> None:
        for v in ["true", "1", "yes", "on", "True", "YES"]:
            assert engine.transform_value(v, "parse_boolean") is True

    def test_false_variants(self, engine: TransformationEngine) -> None:
        for v in ["false", "0", "no", "off", "False", "NO"]:
            assert engine.transform_value(v, "parse_boolean") is False

    def test_bool_passthrough(self, engine: TransformationEngine) -> None:
        assert engine.transform_value(True, "parse_boolean") is True

    def test_invalid(self, engine: TransformationEngine) -> None:
        with pytest.raises(ValueError):
            engine.transform_value("maybe", "parse_boolean")


class TestEmailValidation:
    def test_valid(self, engine: TransformationEngine) -> None:
        assert engine.transform_value("a@b.com", "validate_email") == "a@b.com"

    def test_invalid(self, engine: TransformationEngine) -> None:
        with pytest.raises(ValueError, match="Invalid email"):
            engine.transform_value("not-an-email", "validate_email")


class TestFullPayloadTransformation:
    def test_realistic_fintech_payload(self, engine: TransformationEngine) -> None:
        source = {
            "applicant_name": "  rajesh kumar  ",
            "phone": "9876543210",
            "pan": "ABCDE1234F",
            "aadhaar": "123456789012",
            "loan_amount_paise": 5000000,
            "dob": "15/05/1990",
            "is_active": "yes",
            "email": "rajesh@example.com",
        }
        mappings = [
            {"source_field": "applicant_name", "target_field": "full_name", "transformation": "trim"},
            {"source_field": "phone", "target_field": "mobile", "transformation": "normalize_phone"},
            {"source_field": "pan", "target_field": "pan_masked", "transformation": "mask_pan"},
            {"source_field": "aadhaar", "target_field": "aadhaar_masked", "transformation": "mask_aadhaar"},
            {"source_field": "loan_amount_paise", "target_field": "loan_amount", "transformation": "paise_to_rupees"},
            {"source_field": "dob", "target_field": "date_of_birth", "transformation": "parse_date"},
            {"source_field": "is_active", "target_field": "active", "transformation": "parse_boolean"},
            {"source_field": "email", "target_field": "email_address", "transformation": "validate_email"},
        ]

        result = engine.transform(source, mappings)

        assert result.success
        assert len(result.errors) == 0
        assert result.payload["full_name"] == "rajesh kumar"
        assert result.payload["mobile"] == "+919876543210"
        assert result.payload["pan_masked"] == "XXXXX****F"
        assert result.payload["aadhaar_masked"] == "XXXX-XXXX-9012"
        assert result.payload["loan_amount"] == 50000.00
        assert result.payload["date_of_birth"] == "1990-05-15"
        assert result.payload["active"] is True
        assert result.payload["email_address"] == "rajesh@example.com"
