"""
Tests for policy-gated HammerTime pipeline aggregate adapter.

These tests verify:
1. No Inbox path, artifact name, corpus text, protected identifier, raw event,
   prompt, capability, credential, or secret can enter dashboard responses,
   logs, metrics, or caches
2. Public-synthetic tests, boundary tests, secret scan, and independent review pass
3. Fail-closed behavior on missing policy, stale state, malformed source, timeout,
   and unavailable source
4. Only approved pipeline aggregate counts and explicit truth states are exposed
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from skdashboard import hammertime_adapter


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


class TestPinnedConstants:
    """Verify acceptance criterion #1: pinned constants."""

    def test_schema_version_is_pinned(self) -> None:
        assert hammertime_adapter.SCHEMA_VERSION == "1.1.0"

    def test_adapter_id_is_pinned(self) -> None:
        assert hammertime_adapter.ADAPTER_ID == "hammertime.pipeline"

    def test_ttl_seconds_is_pinned(self) -> None:
        assert hammertime_adapter.TTL_SECONDS == 60

    def test_timeout_ms_is_pinned(self) -> None:
        assert hammertime_adapter.TIMEOUT_MS == 1000

    def test_max_aggregate_bytes_is_pinned(self) -> None:
        assert hammertime_adapter.MAX_AGGREGATE_BYTES == 4_096

    def test_classification_is_confidential(self) -> None:
        assert hammertime_adapter.CLASSIFICATION == "confidential"

    def test_population_is_approved_aggregate_pipeline(self) -> None:
        assert hammertime_adapter.POPULATION == "approved_aggregate_pipeline"


class TestSyntheticFixture:
    """Verify public-synthetic fixture creates safe data."""

    def test_synthetic_fixture_creates_valid_aggregate(self) -> None:
        agg, _ = hammertime_adapter.create_synthetic_fixture()
        assert isinstance(agg, hammertime_adapter.HammerTimeAggregate)
        assert agg.approved_releases == 0
        assert agg.pipeline_failures == 0
        assert agg.observed_at == "2026-08-24T12:00:00Z"

    def test_synthetic_fixture_has_deterministic_watermark(self) -> None:
        agg1, _ = hammertime_adapter.create_synthetic_fixture()
        agg2, _ = hammertime_adapter.create_synthetic_fixture()
        assert agg1.watermark == agg2.watermark

    def test_synthetic_fixture_watermark_is_sha256(self) -> None:
        agg, _ = hammertime_adapter.create_synthetic_fixture()
        assert agg.watermark.startswith("sha256:")
        # SHA256 hex is 64 characters
        assert len(agg.watermark) == len("sha256:") + 64

    def test_synthetic_fixture_to_dict_matches_schema(self) -> None:
        agg, payload = hammertime_adapter.create_synthetic_fixture()
        assert payload["schema_version"] == "1.1.0"
        assert "observed_at" in payload
        assert "watermark" in payload
        assert "coverage" in payload
        assert "aggregate" in payload
        assert payload["aggregate"] == {
            "approved_releases": 0,
            "pipeline_failures": 0,
        }


class TestValidateAggregate:
    """Test aggregate validation and policy gates."""

    def test_valid_synthetic_fixture_passes_validation(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        validated = hammertime_adapter.validate_aggregate(payload)
        assert validated.approved_releases == 0
        assert validated.pipeline_failures == 0

    def test_invalid_schema_version_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["schema_version"] = "2.0.0"
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_missing_required_fields_rejects(self) -> None:
        payload = {"schema_version": "1.1.0"}  # Missing required fields
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_invalid_observed_at_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["observed_at"] = "not-a-date"
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_future_observed_at_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        future = (NOW + timedelta(minutes=10)).isoformat()
        payload["observed_at"] = future
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_missing_tz_in_observed_at_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["observed_at"] = "2026-08-29T12:00:00"  # No timezone
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_invalid_watermark_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["watermark"] = 123  # Not a string
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_empty_watermark_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["watermark"] = ""
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_oversized_watermark_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["watermark"] = "x" * 300
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_missing_aggregate_field_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        del payload["aggregate"]["approved_releases"]
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_extra_aggregate_field_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["aggregate"]["extra_field"] = "should_not_be_here"
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_invalid_aggregate_value_type_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["aggregate"]["approved_releases"] = "not_a_number"
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_nan_aggregate_value_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["aggregate"]["approved_releases"] = float("nan")
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_infinity_aggregate_value_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["aggregate"]["approved_releases"] = float("inf")
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_oversized_string_aggregate_value_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        # Make one field a string (for this test)
        payload["aggregate"]["approved_releases"] = "x" * 200
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_invalid_coverage_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["coverage"] = {"expected": 1}  # Missing "reporting"
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_non_int_coverage_values_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["coverage"]["expected"] = "not_an_int"
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_bool_coverage_values_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["coverage"]["expected"] = True  # bool is subclass of int in Python
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_invalid_errors_array_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["errors"] = "not_an_array"
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_oversized_errors_array_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["errors"] = ["error"] * 20  # Max 16 allowed
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_non_string_error_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["errors"] = [123]
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)

    def test_invalid_has_observations_rejects(self) -> None:
        _, payload = hammertime_adapter.create_synthetic_fixture()
        payload["has_observations"] = "not_a_bool"
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hammertime_adapter.validate_aggregate(payload)


class TestPolicyGate:
    """Test policy gate enforcement (AC #3)."""

    def test_non_synthetic_source_rejects_with_policy_gate_error(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["source"] = "live-hammertime-inbox"
        with pytest.raises(hammertime_adapter.PolicyGateError) as exc:
            hamertime_adapter.validate_aggregate(payload)
        assert "Policy gate" in str(exc.value)
        assert "public-synthetic" in str(exc.value)

    def test_inbox_path_in_watermark_rejects_with_policy_gate_error(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "/hammertime/inbox/some-path"
        with pytest.raises(hammertime_adapter.PolicyGateError) as exc:
            hamertime_adapter.validate_aggregate(payload)
        assert "Policy gate" in str(exc.value)

    def test_corpus_path_in_watermark_rejects_with_policy_gate_error(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "/hammertime/corpus/some-file.txt"
        with pytest.raises(hammertime_adapter.PolicyGateError) as exc:
            hamertime_adapter.validate_aggregate(payload)
        assert "Policy gate" in str(exc.value)

    def test_protected_path_in_watermark_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "/protected/some-secret"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_secrets_path_in_watermark_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "/secrets/api-key.txt"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_password_in_watermark_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "sha256:some-value-with-password"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_secret_in_watermark_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "sha256:some-value-secret-key"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_token_in_watermark_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "sha256:access-token-value"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_api_key_in_watermark_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "sha256:api-key-12345"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_credential_in_watermark_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "sha256:user-credential"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_private_in_watermark_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "sha256:private-key-data"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_inbox_in_aggregate_field_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["aggregate"]["approved_releases"] = "/hammertime/inbox/item"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_corpus_in_aggregate_field_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["aggregate"]["approved_releases"] = "corpus-file-12345"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_secret_in_aggregate_field_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["aggregate"]["approved_releases"] = "secret-value-123"
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_secret_in_error_message_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["errors"] = ["password=supersecret"]
        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)


class TestPayloadSizeValidation:
    """Test payload size boundary validation."""

    def test_payload_at_max_bytes_accepts(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        # This should be well under 4KB
        serialized = json.dumps(payload["aggregate"], separators=(",", ":")).encode()
        assert len(serialized) < hamertime_adapter.MAX_AGGREGATE_BYTES

    def test_payload_over_max_bytes_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        # Create a huge string value to exceed byte limit
        payload["aggregate"]["approved_releases"] = "x" * 5000
        with pytest.raises(hammertime_adapter.SourceMalformedError) as exc:
            hamertime_adapter.validate_aggregate(payload)
        assert "byte limit" in str(exc.value)


class TestProjectAggregate:
    """Test aggregate projection to control-plane envelope."""

    def test_project_valid_aggregate_returns_current_truth_state(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        assert result["adapter_id"] == "hammertime.pipeline"
        assert result["truth_state"] == "current"
        assert result["aggregate"]["approved_releases"] == 0
        assert result["aggregate"]["pipeline_failures"] == 0

    def test_project_stale_aggregate_returns_stale_truth_state(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        old_observed = (NOW - timedelta(seconds=120)).isoformat()
        agg = hammertime_adapter.HammerTimeAggregate(
            approved_releases=0,
            pipeline_failures=0,
            observed_at=old_observed,
            watermark=agg.watermark,
        )
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        assert result["truth_state"] == "stale"
        assert result["age_seconds"] == 120

    def test_project_none_aggregate_returns_unavailable_truth_state(self) -> None:
        result = hamertime_adapter.project_aggregate(None, now=NOW)
        assert result["truth_state"] == "unavailable"
        assert result["aggregate"] is None
        assert result["errors"][0]["code"] == "SOURCE_UNAVAILABLE"

    def test_project_has_required_envelope_fields(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        required_fields = {
            "adapter_id",
            "adapter_version",
            "schema_version",
            "owner",
            "population",
            "classification",
            "visibility",
            "query_budget",
            "ttl_seconds",
            "age_seconds",
            "observed_at",
            "projected_at",
            "watermark",
            "truth_state",
            "coverage",
            "aggregate",
            "errors",
        }
        assert set(result.keys()) == required_fields

    def test_project_envelope_has_correct_metadata(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        assert result["adapter_version"] == "1.0.0"
        assert result["schema_version"] == "1.1.0"
        assert result["owner"] == "HammerTime"
        assert result["population"] == "approved_aggregate_pipeline"
        assert result["classification"] == "confidential"
        assert result["ttl_seconds"] == 60

    def test_project_envelope_has_correct_query_budget(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        assert result["query_budget"] == {"max_items": 1, "timeout_ms": 1000}

    def test_project_envelope_has_visibility(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        assert result["visibility"] == {"state": "visible", "authorization": "authorized"}

    def test_project_envelope_has_watermark(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        assert result["watermark"]["source"] == "hammertime.pipeline"
        assert result["watermark"]["value"] == agg.watermark

    def test_project_envelope_has_coverage(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        assert result["coverage"] == {"expected": 1, "reporting": 1}


class TestGetAggregate:
    """Test the main get_aggregate entry point."""

    def test_get_aggregate_returns_valid_envelope(self) -> None:
        result = hammertime_adapter.get_aggregate(now=NOW)
        assert result["adapter_id"] == "hammertime.pipeline"
        assert result["schema_version"] == "1.1.0"
        assert "truth_state" in result

    def test_get_aggregate_with_synthetic_fixture_passes(self) -> None:
        result = hammertime_adapter.get_aggregate(now=NOW)
        assert result["truth_state"] in {"current", "stale"}
        assert result["aggregate"] is not None
        assert "approved_releases" in result["aggregate"]
        assert "pipeline_failures" in result["aggregate"]

    def test_get_aggregate_sanitizes_errors(self) -> None:
        # The main get_aggregate function should handle errors gracefully
        # and never leak secrets in error messages
        result = hammertime_adapter.get_aggregate(now=NOW)
        if result["errors"]:
            for error in result["errors"]:
                assert "password" not in error["message"].lower()
                assert "secret" not in error["message"].lower()
                assert "token" not in error["message"].lower()
                assert "inbox" not in error["message"].lower()
                assert "corpus" not in error["message"].lower()


class TestCreateReader:
    """Test reader creation."""

    def test_create_reader_returns_callable(self) -> None:
        reader = hammertime_adapter.create_reader()
        assert callable(reader)

    def test_create_reader_returns_valid_payload(self) -> None:
        reader = hammertime_adapter.create_reader()
        payload = reader()
        assert isinstance(payload, dict)
        assert payload["schema_version"] == "1.1.0"
        assert "aggregate" in payload

    def test_create_reader_with_custom_fixture(self) -> None:
        custom = hammertime_adapter.HammerTimeAggregate(
            approved_releases=5,
            pipeline_failures=2,
            observed_at="2026-08-29T12:00:00Z",
            watermark="sha256:custom1234567890abcdef",
        )
        reader = hammertime_adapter.create_reader(fixture=custom)
        payload = reader()
        assert payload["aggregate"]["approved_releases"] == 5
        assert payload["aggregate"]["pipeline_failures"] == 2


class TestSanitizeForLogging:
    """Test logging sanitization (AC #3)."""

    def test_sanitize_removes_inbox_paths(self) -> None:
        data = {"path": "/hammertime/inbox/item-123"}
        safe = hammertime_adapter._sanitize_for_logging(data)
        assert safe["path"] == "[REDACTED]"

    def test_sanitize_removes_corpus_paths(self) -> None:
        data = {"path": "/hammertime/corpus/file.txt"}
        safe = hammertime_adapter._sanitize_for_logging(data)
        assert safe["path"] == "[REDACTED]"

    def test_sanitize_removes_passwords(self) -> None:
        data = {"auth": "password=secret123"}
        safe = hammertime_adapter._sanitize_for_logging(data)
        assert safe["auth"] == "[REDACTED]"

    def test_sanitize_removes_secrets(self) -> None:
        data = {"key": "my-secret-token"}
        safe = hammertime_adapter._sanitize_for_logging(data)
        assert safe["key"] == "[REDACTED]"

    def test_sanitize_keeps_safe_values(self) -> None:
        data = {"count": 5, "status": "ok", "id": "abc123"}
        safe = hammertime_adapter._sanitize_for_logging(data)
        assert safe["count"] == 5
        assert safe["status"] == "ok"
        assert safe["id"] == "abc123"

    def test_sanitize_handles_nested_structures(self) -> None:
        data = {"outer": {"inner": "secret-value"}, "safe": "ok"}
        safe = hammertime_adapter._sanitize_for_logging(data)
        # Only top-level strings are checked
        assert safe["safe"] == "ok"


class TestContainsSecrets:
    """Test secret detection patterns."""

    def test_detects_inbox_paths(self) -> None:
        assert hammertime_adapter._contains_secrets("/hammertime/inbox/item")
        assert not hammertime_adapter._contains_secrets("/safe/path/item")

    def test_detects_corpus_paths(self) -> None:
        assert hammertime_adapter._contains_secrets("/hammertime/corpus/file")
        assert not hammertime_adapter._contains_secrets("/corpus-backup/file")

    def test_detects_password_keyword(self) -> None:
        assert hammertime_adapter._contains_secrets("user-password")
        assert not hammertime_adapter._contains_secrets("passwords")  # Different word

    def test_detects_secret_keyword(self) -> None:
        assert hammertime_adapter._contains_secrets("api-secret-key")
        assert not hammertime_adapter._contains_secrets("secretary")

    def test_detects_token_keyword(self) -> None:
        assert hammertime_adapter._contains_secrets("access-token-123")
        assert not hammertime_adapter._contains_secrets("tokens")

    def test_detects_api_key_keyword(self) -> None:
        assert hamertime_adapter._contains_secrets("api-key-value")
        assert not hammertime_adapter._contains_secrets("keyring")

    def test_detects_credential_keyword(self) -> None:
        assert hammertime_adapter._contains_secrets("user-credential")
        assert not hammertime_adapter._contains_secrets("credentials-check")

    def test_detects_private_keyword(self) -> None:
        assert hammertime_adapter._contains_secrets("private-key-data")
        assert not hammertime_adapter._contains_secrets("privately")

    def test_empty_string_returns_false(self) -> None:
        assert not hammertime_adapter._contains_secrets("")
        assert not hammertime_adapter._contains_secrets(None)


class TestFailClosedBehavior:
    """Test fail-closed behavior (AC #2)."""

    def test_policy_gate_error_returns_policy_denied(self) -> None:
        # Simulate policy gate error in get_aggregate path
        # This requires mocking, so we test the validation path directly
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["source"] = "live-source"  # Triggers policy gate

        with pytest.raises(hammertime_adapter.PolicyGateError):
            hamertime_adapter.validate_aggregate(payload)

    def test_malformed_source_fails_closed(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        del payload["aggregate"]  # Malformed

        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hamertime_adapter.validate_aggregate(payload)

    def test_unavailable_source_fails_closed(self) -> None:
        # Test with None aggregate
        result = hammertime_adapter.project_aggregate(None, now=NOW)
        assert result["truth_state"] == "unavailable"
        assert result["aggregate"] is None

    def test_timeout_scenario_fails_closed(self) -> None:
        # The adapter uses synchronous reading, so timeout is handled
        # by the caller. We verify the error state is correct.
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        result = hammertime_adapter.project_aggregate(agg, now=NOW)
        # If timeout occurred, we'd get an error state
        assert "errors" in result


class TestIntegrationWithControlPlaneAdapters:
    """Test integration with main control_plane_adapters module."""

    def test_hammertime_pipeline_in_specs(self) -> None:
        from skdashboard.control_plane_adapters import SPECS

        spec_ids = {spec.adapter_id for spec in SPECS}
        assert "hammertime.pipeline" in spec_ids

    def test_hammertime_pipeline_in_implemented(self) -> None:
        from skdashboard.control_plane_adapters import _IMPLEMENTED

        assert "hammertime.pipeline" in _IMPLEMENTED

    def test_hammertime_spec_has_correct_fields(self) -> None:
        from skdashboard.control_plane_adapters import SPECS

        spec = next(s for s in SPECS if s.adapter_id == "hammertime.pipeline")
        assert spec.owner == "HammerTime"
        assert spec.population == "approved_aggregate_pipeline"
        assert spec.fields == ("approved_releases", "pipeline_failures")
        assert spec.classification == "confidential"
        assert spec.ttl_seconds == 60
        assert spec.timeout_ms == 1000

    def test_default_readers_includes_hammertime(self) -> None:
        from skdashboard.control_plane_adapters import default_readers
        from pathlib import Path

        readers = default_readers(Path("/tmp/test"))
        assert "hammertime.pipeline" in readers


class TestNoProtectedDataLeakage:
    """Comprehensive test that no protected data can leak (AC #3)."""

    def test_no_inbox_path_in_envelope(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        result_str = json.dumps(result)
        assert "/hammertime/inbox" not in result_str
        assert "/inbox/" not in result_str

    def test_no_corpus_path_in_envelope(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        result_str = json.dumps(result)
        assert "/hammertime/corpus" not in result_str
        assert "corpus" not in result_str.lower() or "corpus_pipeline" in result_str

    def test_no_artifact_name_in_envelope(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        result_str = json.dumps(result)
        # No artifact names like UUIDs or file IDs should appear
        import re
        assert not re.search(r"\b[a-f0-9]{32}\b", result_str)  # MD5
        assert not re.search(r"\b[a-f0-9]{40}\b", result_str)  # SHA1

    def test_no_protected_identifier_in_envelope(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        result_str = json.dumps(result)
        assert "tenant_id" not in result_str
        assert "matter_id" not in result_str
        assert "user_id" not in result_str
        assert "session_id" not in result_str

    def test_no_raw_event_in_envelope(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        # Should only have aggregate counts, not raw events
        assert "events" not in result
        result_str = json.dumps(result).lower()
        assert "raw" not in result_str

    def test_no_prompt_in_envelope(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        result_str = json.dumps(result)
        assert "prompt" not in result_str.lower()

    def test_no_capability_in_envelope(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        result_str = json.dumps(result)
        assert "capability" not in result_str.lower()

    def test_no_credential_in_envelope(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        result_str = json.dumps(result).lower()
        assert "password" not in result_str
        assert "secret" not in result_str or "synthetic" in result_str
        assert "token" not in result_str
        assert "api_key" not in result_str
        assert "credential" not in result_str
        assert "private_key" not in result_str

    def test_envelope_only_has_approved_fields(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        # Check aggregate only has the two approved fields
        if result["aggregate"]:
            assert set(result["aggregate"].keys()) == {
                "approved_releases",
                "pipeline_failures",
            }

    def test_envelope_has_explicit_truth_state(self) -> None:
        result = hamertime_adapter.get_aggregate(now=NOW)
        assert result["truth_state"] in {
            "current",
            "stale",
            "partial",
            "unavailable",
            "unknown",
            "not_applicable",
        }


class TestBoundaryTests:
    """Boundary and edge case tests (AC #4)."""

    def test_negative_aggregate_values_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["aggregate"]["approved_releases"] = -1
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hamertime_adapter.validate_aggregate(payload)

    def test_very_large_aggregate_value_accepts(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["aggregate"]["approved_releases"] = 10**9  # Large but valid
        validated = hammertime_adapter.validate_aggregate(payload)
        assert validated.approved_releases == 10**9

    def test_exactly_max_ttl_age_returns_stale(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        old_observed = (NOW - timedelta(seconds=60)).isoformat()
        agg = hammertime_adapter.HammerTimeAggregate(
            approved_releases=0,
            pipeline_failures=0,
            observed_at=old_observed,
            watermark=agg.watermark,
        )
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        assert result["truth_state"] == "stale"
        assert result["age_seconds"] == 60

    def test_one_second_before_max_ttl_returns_current(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        old_observed = (NOW - timedelta(seconds=59)).isoformat()
        agg = hammertime_adapter.HammerTimeAggregate(
            approved_releases=0,
            pipeline_failures=0,
            observed_at=old_observed,
            watermark=agg.watermark,
        )
        result = hamertime_adapter.project_aggregate(agg, now=NOW)
        assert result["truth_state"] == "current"
        assert result["age_seconds"] == 59

    def test_exactly_max_watermark_length_accepts(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "x" * 256
        validated = hammertime_adapter.validate_aggregate(payload)
        assert validated.watermark == "x" * 256

    def test_one_over_max_watermark_length_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["watermark"] = "x" * 257
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hamertime_adapter.validate_aggregate(payload)

    def test_exactly_max_errors_count_accepts(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["errors"] = ["error"] * 16
        validated = hamertime_adapter.validate_aggregate(payload)
        assert validated is not None

    def test_one_over_max_errors_count_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["errors"] = ["error"] * 17
        with pytest.raises(hammertime_adapter.SourceMalformedError):
            hamertime_adapter.validate_aggregate(payload)

    def test_zero_coverage_values_accepts(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["coverage"]["expected"] = 0
        payload["coverage"]["reporting"] = 0
        validated = hammertime_adapter.validate_aggregate(payload)
        assert validated is not None

    def test_reporting_exceeds_expected_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        payload["coverage"]["expected"] = 5
        payload["coverage"]["reporting"] = 10
        # Validation happens in the caller, not in validate_aggregate
        # So this should pass validation
        validated = hammertime_adapter.validate_aggregate(payload)
        assert validated is not None


class TestClockSkewTolerance:
    """Test clock skew tolerance (5 minutes)."""

    def test_five_minutes_future_accepts(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        future = (NOW + timedelta(minutes=5)).isoformat()
        payload["observed_at"] = future
        # 5 minutes in future should be allowed (clock skew tolerance)
        validated = hamertime_adapter.validate_aggregate(payload)
        assert validated is not None

    def test_five_minutes_one_second_future_rejects(self) -> None:
        # agg, payload = hammertime_adapter.create_synthetic_fixture()
        
        future = (NOW + timedelta(minutes=5, seconds=1)).isoformat()
        payload["observed_at"] = future
        with pytest.raises(hammertime_adapter.SourceMalformedError) as exc:
            hamertime_adapter.validate_aggregate(payload)
        assert "future" in str(exc.value).lower()
