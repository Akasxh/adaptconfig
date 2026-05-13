"""Adapter-specific mock API response generators.

Each adapter produces deterministic, hash-seeded responses that mirror
real API schemas from Indian fintech providers. Responses are keyed by
adapter_name + endpoint_path so the simulator routes correctly.
"""

import hashlib
from typing import Any


def _seed_from(value: str) -> int:
    """Deterministic seed from any string input."""
    return int(hashlib.md5(value.encode(), usedforsecurity=False).hexdigest(), 16)  # noqa: S324


def generate_mock_response(
    adapter_name: str,
    endpoint_path: str,
    request_payload: dict[str, Any],
    base_url: str = "",
) -> dict[str, Any]:
    """Route to the correct adapter mock generator.

    Matches by adapter display name first, then falls back to base_url
    pattern matching (useful when adapter_name is a UUID from the DB).
    """
    generators: dict[str, type[_AdapterMock]] = {
        "CIBIL Credit Bureau": _CIBILMock,
        "Aadhaar eKYC Provider": _KYCMock,
        "GST Verification Service": _GSTMock,
        "Payment Gateway": _PaymentMock,
        "Fraud Detection Engine": _FraudMock,
        "SMS Gateway": _SMSMock,
        "Account Aggregator (AA Framework)": _AccountAggregatorMock,
        "Email Notification Gateway": _EmailMock,
        "SkyLend Loan Origination": _SkyLendMock,
    }
    generator_cls = generators.get(adapter_name)

    # Fallback: match by base_url pattern when adapter_name is a UUID
    if generator_cls is None and base_url:
        generator_cls = _match_by_base_url(base_url)

    if generator_cls is None:
        return _default_response(adapter_name, endpoint_path)
    return generator_cls.respond(endpoint_path, request_payload)


def _match_by_base_url(base_url: str) -> type | None:
    """Match adapter mock generator by base URL pattern.

    Defined here as a forward declaration; the actual pattern list
    references classes defined below. Uses late binding via a dict
    built on first call.
    """
    url = base_url.lower()
    # Lazy import pattern — classes defined below in this module
    patterns: list[tuple[str, str]] = [
        ("cibil", "_CIBILMock"),
        ("ekyc", "_KYCMock"),
        ("kyc", "_KYCMock"),
        ("gst", "_GSTMock"),
        ("payment", "_PaymentMock"),
        ("fraud", "_FraudMock"),
        ("sms", "_SMSMock"),
        ("account-aggregator", "_AccountAggregatorMock"),
        ("email", "_EmailMock"),
        ("skylend", "_SkyLendMock"),
    ]
    import sys

    module = sys.modules[__name__]
    for pattern, cls_name in patterns:
        if pattern in url:
            return getattr(module, cls_name, None)
    return None


def _default_response(adapter_name: str, endpoint_path: str) -> dict[str, Any]:
    return {
        "status": "success",
        "code": 200,
        "adapter": adapter_name,
        "endpoint": endpoint_path,
        "message": f"Mock response for {endpoint_path}",
        "timestamp": "2025-03-26T10:00:00+05:30",
    }


class _AdapterMock:
    """Base class — subclasses implement respond()."""

    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# CIBIL Credit Bureau
# ---------------------------------------------------------------------------


class _CIBILMock(_AdapterMock):
    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        pan = str(payload.get("pan_number", "XXXXX0000X"))
        seed = _seed_from(pan)
        score = 300 + (seed % 600)

        # Auth endpoints feed downstream chain calls with access_token.
        if "/oauth" in endpoint_path or "/token" in endpoint_path or "/auth" in endpoint_path:
            return cls._oauth_token(seed)
        if "/bulk" in endpoint_path:
            return cls._bulk_inquiry(seed, pan)
        if "/credit-report" in endpoint_path or "/reports" in endpoint_path:
            return cls._credit_report(seed, score, pan)
        # Default: credit score (also stamps enquiry_id so chains can fetch the report)
        return cls._credit_score(seed, score)

    @classmethod
    def _oauth_token(cls, seed: int) -> dict[str, Any]:
        return {
            "token_type": "Bearer",
            "access_token": f"cibil-mock-{seed % 10**8:08x}",
            "expires_in": 3600,
            "scope": "credit_score credit_report",
        }

    @classmethod
    def _credit_score(cls, seed: int, score: int) -> dict[str, Any]:
        return {
            "status": "success",
            "enquiry_id": f"ENQ{seed % 9999999:07d}",
            "credit_score": score,
            "score_version": "CIBIL_V3",
            "credit_rank": "1" if score > 750 else ("2" if score > 650 else "5"),
            "account_summary": {
                "total_accounts": (seed % 8) + 1,
                "active_accounts": (seed % 4) + 1,
                "closed_accounts": seed % 4,
                "delinquent_accounts": seed % 2,
                "overdue_amount_inr": seed % 50000,
            },
            "enquiry_summary": {
                "total_enquiries_6m": seed % 5,
                "total_enquiries_12m": seed % 8,
                "last_enquiry_date": "2025-01-15",
            },
            "report_date": "2025-03-01",
            "control_number": f"CIBIL{seed % 9999999:07d}",
        }

    @classmethod
    def _credit_report(cls, seed: int, score: int, pan: str) -> dict[str, Any]:
        base = cls._credit_score(seed, score)
        num_accounts = (seed % 5) + 1
        base["report_id"] = f"RPT{seed % 9999999:07d}"
        base["accounts"] = [
            {
                "account_type": [
                    "Home Loan",
                    "Personal Loan",
                    "Credit Card",
                    "Auto Loan",
                    "Business Loan",
                ][i % 5],
                "institution": ["SBI", "HDFC", "ICICI", "Axis", "Kotak"][i % 5],
                "account_number": f"XXXX{(seed + i) % 9999:04d}",
                "opened_date": f"20{18 + (i % 5)}-0{(i % 9) + 1}-01",
                "sanctioned_amount": ((seed + i) % 50 + 1) * 100000,
                "current_balance": ((seed + i) % 30) * 100000,
                "status": "Active" if i < num_accounts - 1 else "Closed",
                "payment_history": "".join(
                    ["0" if (seed + i + j) % 3 != 0 else "X" for j in range(12)]
                ),
            }
            for i in range(num_accounts)
        ]
        return base

    @classmethod
    def _bulk_inquiry(cls, seed: int, pan: str) -> dict[str, Any]:
        return {
            "status": "completed",
            "batch_id": f"BATCH{seed % 9999999:07d}",
            "results": [
                {
                    "pan": pan,
                    "credit_score": 300 + (seed % 600),
                    "enquiry_id": f"ENQ{seed % 9999999:07d}",
                    "status": "success",
                }
            ],
            "total_records": 1,
            "processed": 1,
            "failed": 0,
        }


# ---------------------------------------------------------------------------
# KYC Provider — Aadhaar eKYC
# ---------------------------------------------------------------------------


class _KYCMock(_AdapterMock):
    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if "/verify/pan" in endpoint_path:
            return cls._pan_verify(payload)
        if "/digilocker" in endpoint_path:
            return cls._digilocker(payload)
        # Default: Aadhaar verification
        return cls._aadhaar_verify(payload)

    @classmethod
    def _aadhaar_verify(cls, payload: dict[str, Any]) -> dict[str, Any]:
        aadhaar = str(payload.get("aadhaar_number", "000000000000"))
        seed = _seed_from(aadhaar)
        name = payload.get("customer_name", "Rajesh Kumar")
        return {
            "verification_status": "verified",
            "name": name,
            "dob": "1990-05-15",
            "gender": "M" if seed % 2 == 0 else "F",
            "address": {
                "house": f"{seed % 999 + 1}",
                "street": "MG Road",
                "locality": "Koramangala",
                "city": "Bengaluru",
                "state": "Karnataka",
                "pincode": f"{560000 + seed % 100:06d}",
            },
            "mobile_linked": True,
            "face_match_score": round(0.85 + (seed % 15) / 100, 2),
            "reference_id": f"KYC{seed % 9999999:07d}",
            "timestamp": "2025-03-01T10:00:00+05:30",
        }

    @classmethod
    def _pan_verify(cls, payload: dict[str, Any]) -> dict[str, Any]:
        pan = str(payload.get("pan_number", "ABCDE1234F"))
        seed = _seed_from(pan)
        name = payload.get("customer_name", "Rajesh Kumar")
        return {
            "verification_status": "verified",
            "pan_number": pan,
            "pan_status": "VALID",
            "name_on_pan": name.upper(),
            "pan_type": "Individual" if pan[3] == "P" else "Company",
            "aadhaar_seeding_status": "Linked" if seed % 3 != 0 else "Not Linked",
            "last_updated": "2025-01-15",
            "reference_id": f"PAN{seed % 9999999:07d}",
            "timestamp": "2025-03-01T10:00:00+05:30",
        }

    @classmethod
    def _digilocker(cls, payload: dict[str, Any]) -> dict[str, Any]:
        aadhaar = str(payload.get("aadhaar_number", "000000000000"))
        seed = _seed_from(aadhaar)
        return {
            "status": "success",
            "document_type": "driving_license",
            "document_number": f"DL-{2010 + seed % 14}-{seed % 9999999:07d}",
            "name": payload.get("customer_name", "Rajesh Kumar"),
            "document_expiry": "2030-12-31",
            "issuing_authority": "RTO Bengaluru",
            "consent_artefact_id": f"CONSENT{seed % 9999999:07d}",
            "reference_id": f"DGL{seed % 9999999:07d}",
            "timestamp": "2025-03-01T10:00:00+05:30",
        }


# ---------------------------------------------------------------------------
# GST Verification
# ---------------------------------------------------------------------------


class _GSTMock(_AdapterMock):
    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if "/returns" in endpoint_path:
            return cls._returns_status(payload)
        if "/profile" in endpoint_path:
            return cls._profile(payload)
        # Default: GSTIN verification
        return cls._gstin_verify(payload)

    @classmethod
    def _gstin_verify(cls, payload: dict[str, Any]) -> dict[str, Any]:
        gstin = str(payload.get("gstin", "29ABCDE1234F1ZK"))
        state_code = gstin[:2]
        states = {
            "27": "Maharashtra",
            "29": "Karnataka",
            "07": "Delhi",
            "33": "Tamil Nadu",
            "06": "Haryana",
            "09": "Uttar Pradesh",
        }
        return {
            "gstin": gstin,
            "legal_name": "SANDBOX ENTERPRISES PVT LTD",
            "trade_name": "SANDBOX ENT",
            "registration_date": "01/04/2018",
            "taxpayer_type": "Regular",
            "status": "Active",
            "state": states.get(state_code, "Karnataka"),
            "constitution": "Private Limited Company",
            "nature_of_business": ["Wholesale Business", "Retail Business"],
            "last_updated": "2025-01-01",
        }

    @classmethod
    def _returns_status(cls, payload: dict[str, Any]) -> dict[str, Any]:
        gstin = str(payload.get("gstin", "29ABCDE1234F1ZK"))
        fy = str(payload.get("financial_year", "2024-25"))
        return {
            "gstin": gstin,
            "financial_year": fy,
            "filings": [
                {
                    "period": "Apr 2025",
                    "return_type": "GSTR3B",
                    "filing_date": "20-05-2025",
                    "status": "Filed",
                    "mode": "ONLINE",
                },
                {
                    "period": "Mar 2025",
                    "return_type": "GSTR3B",
                    "filing_date": "21-04-2025",
                    "status": "Filed",
                    "mode": "ONLINE",
                },
                {
                    "period": "Feb 2025",
                    "return_type": "GSTR3B",
                    "filing_date": "20-03-2025",
                    "status": "Filed",
                    "mode": "ONLINE",
                },
            ],
            "compliance_rating": "Good",
        }

    @classmethod
    def _profile(cls, payload: dict[str, Any]) -> dict[str, Any]:
        gstin = str(payload.get("gstin", "29ABCDE1234F1ZK"))
        seed = _seed_from(gstin)
        return {
            "gstin": gstin,
            "legal_name": "SANDBOX ENTERPRISES PVT LTD",
            "trade_name": "SANDBOX ENT",
            "constitution": "Private Limited Company",
            "registration_date": "01/04/2018",
            "status": "Active",
            "principal_place": "Bengaluru, Karnataka",
            "additional_places": 2,
            "annual_turnover_slab": "Rs. 1.5 Cr - 5 Cr",
            "hsn_summary": [
                {"hsn_code": "9983", "description": "Other professional services", "tax_rate": 18},
                {"hsn_code": "8471", "description": "Data processing machines", "tax_rate": 18},
            ],
            "igst_balance": seed % 500000,
            "cgst_balance": seed % 250000,
            "sgst_balance": seed % 250000,
        }


# ---------------------------------------------------------------------------
# Payment Gateway (Razorpay-style)
# ---------------------------------------------------------------------------


class _PaymentMock(_AdapterMock):
    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import time

        ts = int(time.time())
        seed = _seed_from(str(payload.get("reference_id", str(ts))))

        if "/payments/create" in endpoint_path or endpoint_path == "/payments":
            amount = payload.get("amount", payload.get("loan_amount", 500000))
            return {
                "status": "success",
                "order_id": f"order_{seed % 99999999:08d}",
                "payment_id": f"pay_{seed % 99999999:08d}",
                "amount": amount,
                "currency": "INR",
                "payment_status": "created",
                "receipt": f"rcpt_{seed % 999999:06d}",
                "method": None,
                "created_at": ts,
            }
        if "/transfers" in endpoint_path:
            return {
                "status": "success",
                "transfer_id": f"trf_{seed % 99999999:08d}",
                "account_number": payload.get("account_number", "XXXX7890"),
                "ifsc_code": payload.get("ifsc_code", "SBIN0001234"),
                "beneficiary_name": payload.get("beneficiary_name", "Rajesh Kumar"),
                "amount": payload.get("amount", 500000),
                "payment_mode": payload.get("payment_mode", "NEFT"),
                "utr_number": f"NEFT{seed % 99999999:08d}",
                "transfer_status": "processed",
                "created_at": ts,
            }
        if "/refunds" in endpoint_path:
            return {
                "status": "success",
                "refund_id": f"rfnd_{seed % 99999999:08d}",
                "payment_id": payload.get("reference_id", f"pay_{seed % 99999999:08d}"),
                "amount": payload.get("amount", 500000),
                "refund_status": "processed",
                "speed_processed": "normal",
                "created_at": ts,
            }
        if "/payments/" in endpoint_path:
            return {
                "status": "success",
                "payment_id": f"pay_{seed % 99999999:08d}",
                "order_id": f"order_{seed % 99999999:08d}",
                "amount": 500000,
                "currency": "INR",
                "payment_status": "captured",
                "method": "upi",
                "vpa": "customer@upi",
                "bank": "HDFC",
                "captured_at": ts,
            }
        # Generic payment response
        return {
            "status": "success",
            "payment_id": f"pay_{seed % 99999999:08d}",
            "amount": payload.get("amount", 500000),
            "currency": "INR",
            "payment_status": "created",
            "created_at": ts,
        }


# ---------------------------------------------------------------------------
# Fraud Detection Engine
# ---------------------------------------------------------------------------


class _FraudMock(_AdapterMock):
    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        customer_id = str(payload.get("customer_id", payload.get("reference_id", "unknown")))
        seed = _seed_from(customer_id)
        risk_score = round((seed % 100) / 100, 2)

        if "/verify/device" in endpoint_path:
            return {
                "status": "success",
                "device_id": payload.get("device_id", f"DEV{seed % 9999999:07d}"),
                "device_trust_score": round(0.5 + (seed % 50) / 100, 2),
                "device_fingerprint": f"fp_{seed % 99999999:08x}",
                "known_device": seed % 3 != 0,
                "device_age_days": seed % 365,
                "risk_flags": ["NEW_DEVICE"] if seed % 3 == 0 else [],
                "reference_id": f"DVC{seed % 9999999:07d}",
            }
        if "/verify/velocity" in endpoint_path:
            txn_count = seed % 20
            return {
                "status": "success",
                "customer_id": customer_id,
                "velocity_check": "pass" if txn_count < 10 else "fail",
                "transactions_1h": txn_count % 5,
                "transactions_24h": txn_count,
                "transactions_7d": txn_count * 3,
                "amount_24h": seed % 500000,
                "anomaly_detected": txn_count >= 10,
                "risk_flags": ["HIGH_VELOCITY"] if txn_count >= 10 else [],
                "reference_id": f"VEL{seed % 9999999:07d}",
            }
        # Default: fraud score
        return {
            "status": "success",
            "customer_id": customer_id,
            "fraud_score": risk_score,
            "risk_level": "low" if risk_score < 0.3 else ("medium" if risk_score < 0.7 else "high"),
            "risk_factors": cls._risk_factors(seed),
            "recommendation": "approve" if risk_score < 0.5 else "review",
            "transaction_amount": payload.get("transaction_amount", 0),
            "device_score": round(0.6 + (seed % 40) / 100, 2),
            "ip_risk": "low" if seed % 4 != 0 else "medium",
            "reference_id": f"FRD{seed % 9999999:07d}",
            "timestamp": "2025-03-01T10:00:00+05:30",
        }

    @staticmethod
    def _risk_factors(seed: int) -> list[dict[str, Any]]:
        all_factors = [
            {
                "factor": "new_account",
                "weight": 0.3,
                "description": "Account less than 30 days old",
            },
            {"factor": "high_amount", "weight": 0.2, "description": "Transaction above average"},
            {"factor": "new_device", "weight": 0.15, "description": "First time device"},
            {"factor": "unusual_time", "weight": 0.1, "description": "Transaction at unusual hour"},
            {
                "factor": "location_mismatch",
                "weight": 0.25,
                "description": "IP location differs from registered address",
            },
        ]
        count = (seed % 3) + 1
        return all_factors[:count]


# ---------------------------------------------------------------------------
# SMS Gateway
# ---------------------------------------------------------------------------


class _SMSMock(_AdapterMock):
    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        mobile = str(payload.get("mobile_number", payload.get("mobile", "9876543210")))
        seed = _seed_from(mobile)
        msg_id = f"MSG{seed % 9999999:07d}"

        if "/status" in endpoint_path:
            return {
                "status": "success",
                "message_id": payload.get("message_id", msg_id),
                "delivery_status": "DELIVERED",
                "delivered_at": "2025-03-01T10:05:00+05:30",
                "operator": ["Jio", "Airtel", "Vi", "BSNL"][seed % 4],
                "error_code": None,
            }
        if "/templates" in endpoint_path:
            return {
                "status": "success",
                "templates": [
                    {
                        "template_id": "1107161234567890123",
                        "name": "OTP Template",
                        "content": "Your OTP is {#var#}. Valid for 10 minutes.",
                        "status": "approved",
                    },
                    {
                        "template_id": "1107161234567890124",
                        "name": "Loan Disbursement",
                        "content": "Dear {#var#}, your loan of Rs.{#var#} has been disbursed.",
                        "status": "approved",
                    },
                    {
                        "template_id": "1107161234567890125",
                        "name": "EMI Reminder",
                        "content": "Dear {#var#}, your EMI of Rs.{#var#} is due on {#var#}.",
                        "status": "approved",
                    },
                ],
            }
        # Default: send SMS
        return {
            "status": "success",
            "message_id": msg_id,
            "mobile": mobile,
            "delivery_status": "ACCEPTED",
            "credits_used": 1,
            "dlt_entity_id": "1101FINSPARK",
            "submitted_at": "2025-03-01T10:00:00+05:30",
        }


# ---------------------------------------------------------------------------
# Account Aggregator (AA Framework)
# ---------------------------------------------------------------------------


class _AccountAggregatorMock(_AdapterMock):
    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        vua = str(payload.get("customer_vua", payload.get("reference_id", "user@aa")))
        seed = _seed_from(vua)

        if "/consent/create" in endpoint_path:
            return {
                "status": "success",
                "consent_handle": f"CNS{seed % 9999999:07d}",
                "consent_status": "PENDING",
                "customer_vua": vua,
                "fi_types": payload.get("fi_types", ["DEPOSIT"]),
                "consent_expiry": "2026-03-01T00:00:00+05:30",
                "redirect_url": f"https://aa-provider.com/consent/CNS{seed % 9999999:07d}",
                "timestamp": "2025-03-01T10:00:00+05:30",
            }
        if "/consent/" in endpoint_path and "/status" in endpoint_path:
            return {
                "status": "success",
                "consent_handle": f"CNS{seed % 9999999:07d}",
                "consent_status": "APPROVED",
                "approved_at": "2025-03-01T10:05:00+05:30",
                "consent_expiry": "2026-03-01T00:00:00+05:30",
            }
        if "/fi/fetch" in endpoint_path:
            return {
                "status": "success",
                "session_id": f"SES{seed % 9999999:07d}",
                "consent_handle": f"CNS{seed % 9999999:07d}",
                "fi_data_ready": True,
                "fip_count": 2,
                "timestamp": "2025-03-01T10:10:00+05:30",
            }
        if "/fi/" in endpoint_path:
            return {
                "status": "success",
                "session_id": f"SES{seed % 9999999:07d}",
                "fi_data": [
                    {
                        "fip_id": "FIP_SBI",
                        "fi_type": "DEPOSIT",
                        "accounts": [
                            {
                                "account_type": "SAVINGS",
                                "account_number": f"XXXX{seed % 9999:04d}",
                                "branch": "Koramangala, Bengaluru",
                                "balance": round((seed % 500000) + 10000, 2),
                                "currency": "INR",
                                "holder_name": "RAJESH KUMAR",
                                "ifsc": "SBIN0001234",
                            }
                        ],
                    },
                    {
                        "fip_id": "FIP_HDFC",
                        "fi_type": "DEPOSIT",
                        "accounts": [
                            {
                                "account_type": "CURRENT",
                                "account_number": f"XXXX{(seed + 1) % 9999:04d}",
                                "branch": "MG Road, Bengaluru",
                                "balance": round((seed % 1000000) + 50000, 2),
                                "currency": "INR",
                                "holder_name": "RAJESH KUMAR",
                                "ifsc": "HDFC0001234",
                            }
                        ],
                    },
                ],
                "data_range": {
                    "from": "2024-03-01",
                    "to": "2025-03-01",
                },
            }
        # Generic AA response
        return {
            "status": "success",
            "consent_handle": f"CNS{seed % 9999999:07d}",
            "consent_status": "PENDING",
            "timestamp": "2025-03-01T10:00:00+05:30",
        }


# ---------------------------------------------------------------------------
# Email Notification Gateway
# ---------------------------------------------------------------------------


class _EmailMock(_AdapterMock):
    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        to = str(payload.get("to", payload.get("email_address", "user@example.com")))
        seed = _seed_from(to)
        email_id = f"EML{seed % 9999999:07d}"

        if "/status" in endpoint_path:
            return {
                "status": "success",
                "email_id": payload.get("email_id", email_id),
                "delivery_status": "DELIVERED",
                "delivered_at": "2025-03-01T10:02:00+05:30",
                "opened": seed % 2 == 0,
                "opened_at": "2025-03-01T10:15:00+05:30" if seed % 2 == 0 else None,
                "bounced": False,
            }
        if "/templates" in endpoint_path:
            return {
                "status": "success",
                "templates": [
                    {
                        "template_id": "tpl_welcome",
                        "name": "Welcome Email",
                        "subject": "Welcome to FinSpark",
                        "status": "active",
                    },
                    {
                        "template_id": "tpl_loan_approved",
                        "name": "Loan Approval",
                        "subject": "Your loan has been approved!",
                        "status": "active",
                    },
                    {
                        "template_id": "tpl_emi_reminder",
                        "name": "EMI Reminder",
                        "subject": "EMI due on {due_date}",
                        "status": "active",
                    },
                    {
                        "template_id": "tpl_kyc_pending",
                        "name": "KYC Pending",
                        "subject": "Complete your KYC verification",
                        "status": "active",
                    },
                ],
            }
        # Default: send email
        return {
            "status": "success",
            "email_id": email_id,
            "to": to,
            "subject": payload.get("subject", "Notification from FinSpark"),
            "delivery_status": "ACCEPTED",
            "submitted_at": "2025-03-01T10:00:00+05:30",
        }


# ---------------------------------------------------------------------------
# SkyLend Loan Origination (multi-step chain demo)
# ---------------------------------------------------------------------------


class _SkyLendMock(_AdapterMock):
    """Mocks for the seven SkyLend loan-origination endpoints.

    Every response intentionally contains the fields downstream chain
    steps extract — so the chain test panel runs all green end-to-end
    and shows real values flowing between nodes.
    """

    @classmethod
    def respond(cls, endpoint_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        pan = str(payload.get("pan_number") or payload.get("customer_id") or "ABCDE1234F")
        seed = _seed_from(pan)

        if "/oauth" in endpoint_path or "/token" in endpoint_path:
            return cls._oauth_token(seed)
        if "/verify-pan" in endpoint_path or "/customer/verify" in endpoint_path:
            return cls._verify_pan(seed, pan)
        if "/credit/inquiry" in endpoint_path and "/report" in endpoint_path:
            return cls._credit_report(seed)
        if "/credit/inquiry" in endpoint_path:
            return cls._credit_inquiry(seed)
        if "/risk/score" in endpoint_path:
            return cls._risk_score(seed)
        if "/loan/offer" in endpoint_path and "/status" in endpoint_path:
            return cls._offer_status(seed)
        if "/loan/offer" in endpoint_path:
            return cls._loan_offer(seed)
        return _default_response("SkyLend Loan Origination", endpoint_path)

    @classmethod
    def _oauth_token(cls, seed: int) -> dict[str, Any]:
        return {
            "access_token": f"sk-jwt-{seed % 10**10:010x}",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "customer credit loan",
        }

    @classmethod
    def _verify_pan(cls, seed: int, pan: str) -> dict[str, Any]:
        return {
            "customer_id": f"CUST{seed % 9999999:07d}",
            "verified": True,
            "match_score": round(0.85 + (seed % 15) / 100, 2),
            "bureau_name": "CIBIL",
            "pan": pan,
        }

    @classmethod
    def _credit_inquiry(cls, seed: int) -> dict[str, Any]:
        return {
            "inquiry_id": f"INQ{seed % 9999999:07d}",
            "bureau_score": 300 + (seed % 600),
            "bureau_name": "CIBIL",
            "reported_at": "2025-03-26T10:00:00+05:30",
        }

    @classmethod
    def _credit_report(cls, seed: int) -> dict[str, Any]:
        return {
            "report_url": f"https://reports.skylend.in/v2/{seed % 9999999:07d}.pdf",
            "generated_at": "2025-03-26T10:01:30+05:30",
            "total_accounts": (seed % 12) + 1,
            "active_accounts": (seed % 6) + 1,
            "delinquent_accounts": seed % 2,
        }

    @classmethod
    def _risk_score(cls, seed: int) -> dict[str, Any]:
        grades = ["A1", "A2", "B1", "B2", "C", "D"]
        idx = seed % len(grades)
        return {
            "risk_grade": grades[idx],
            "max_loan_amount_inr": ((seed % 50) + 10) * 100000,
            "recommended_tenor": [12, 24, 36, 48, 60][seed % 5],
            "decision_factors": {
                "bureau_weight": 0.55,
                "income_weight": 0.30,
                "tenure_weight": 0.15,
            },
        }

    @classmethod
    def _loan_offer(cls, seed: int) -> dict[str, Any]:
        return {
            "offer_id": f"OFFER{seed % 9999999:07d}",
            "offered_amount_inr": ((seed % 50) + 10) * 100000,
            "interest_rate": round(9.5 + (seed % 600) / 100, 2),
            "tenor_months": [12, 24, 36, 48, 60][seed % 5],
            "processing_fee_inr": ((seed % 5) + 1) * 500,
            "valid_until": "2025-04-26T23:59:59+05:30",
        }

    @classmethod
    def _offer_status(cls, seed: int) -> dict[str, Any]:
        statuses = ["pending", "accepted", "rejected", "expired"]
        return {
            "status": statuses[seed % len(statuses)],
            "accepted_at": "2025-03-27T11:15:00+05:30" if seed % 4 == 1 else None,
            "disbursed_at": None,
        }
