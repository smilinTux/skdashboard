from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from skdashboard.control_plane_client import (
    ControlPlaneClient,
    ControlPlaneClientError,
    canonical_manifest_hash,
    canonical_manifest_signature_bytes,
)
from skdashboard.control_plane_discovery import (
    CONTRACT_VERSION,
    SCHEMA_VERSION,
    control_plane_profile,
    validate_control_plane_profile,
)
from skdashboard.control_plane_fixture import (
    BEARER,
    MANIFEST,
    MANIFEST_SHA256,
    ORIGIN,
    create_fixture_app,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures/f0c63c2a/skworld.module.control-plane.example.json"
)
FIXTURE_SHA256 = "0674bd9aefff700cc27ab216c647cee604c1307e6c5ef1051eef68a58d5c2ae1"


def test_generated_client_accepts_hash_pinned_shared_fixture() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    approved_hash = canonical_manifest_hash(manifest)
    manifest["manifest_sha256"] = approved_hash
    ControlPlaneClient._validate_manifest(manifest, ORIGIN, approved_hash)


def test_exact_v13_profile_is_origin_relative_and_capability_only() -> None:
    validate_control_plane_profile(MANIFEST)
    profile = control_plane_profile()
    assert MANIFEST["schemaVersion"] == SCHEMA_VERSION == "1.3"
    assert profile["contractVersion"] == CONTRACT_VERSION == "1.1.0"
    assert profile["basePath"] == "/api/v1"
    assert profile["openapi"] == "/api/v1/openapi.json"
    assert profile["events"] == {
        "path": "/api/v1/events",
        "mediaType": "text/event-stream",
        "resume": "cursor",
    }
    assert set(profile["commands"]["capabilities"].values()) == {
        "skdashboard.actions.preview",
        "skdashboard.actions.authorize",
    }
    assert ORIGIN not in repr(profile)
    assert "Bearer" + " " not in repr(MANIFEST)


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        (("schemaVersion",), "1.2"),
        (("auth", "audience"), "other"),
        (("controlPlane", "contractVersion"), "2.0.0"),
        (("controlPlane", "openapi"), "https://other.test/openapi.json"),
        (("controlPlane", "openapi"), "/api/v1/../private"),
    ],
)
def test_client_rejects_unsupported_version_audience_and_origin_substitution(
    mutation: tuple[str, ...], value: str
) -> None:
    manifest = copy.deepcopy(MANIFEST)
    target = manifest
    for key in mutation[:-1]:
        target = target[key]
    target[mutation[-1]] = value

    async def handler(_request):
        return httpx.Response(200, json=manifest)

    async def run() -> None:
        with pytest.raises(ControlPlaneClientError):
            await ControlPlaneClient.discover(
                ORIGIN + "/.well-known/skworld-module.json",
                BEARER,
                transport=httpx.MockTransport(handler),
                manifest_sha256=MANIFEST_SHA256,
            )

    asyncio.run(run())


def test_discovered_openapi_and_deprecation_locations_are_live() -> None:
    async def run() -> None:
        transport = httpx.ASGITransport(app=create_fixture_app())
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            openapi = await client.get(MANIFEST["controlPlane"]["openapi"])
            policy = await client.get(MANIFEST["controlPlane"]["deprecation"]["policy"])
        assert openapi.status_code == 200
        assert openapi.json()["openapi"] == "3.1.0"
        assert policy.status_code == 200
        assert policy.json()["unsupported_version"] == "fail_closed"
        assert policy.json()["sunset_header_required"] is True

    asyncio.run(run())


def test_client_verifies_real_capauth_signature_and_rejects_mutations() -> None:
    crypto = pytest.importorskip("capauth.crypto")
    models = pytest.importorskip("capauth.models")
    backend = crypto.get_backend(crypto.CryptoBackendType.PGPY)
    bundle = backend.generate_keypair(
        "f0c63c2a dashboard test",
        "f0c63c2a-dashboard@skworld.invalid",
        "pw",
        models.Algorithm.ED25519,
    )
    manifest = copy.deepcopy(MANIFEST)
    manifest["signature"] = {
        "suite_id": "ed25519-v1",
        "value": backend.sign(
            canonical_manifest_signature_bytes(manifest), bundle.private_armor, "pw"
        ),
        "signer_fpr": bundle.fingerprint,
    }
    approved_hash = canonical_manifest_hash(manifest)
    manifest["manifest_sha256"] = approved_hash

    def verifier(data: bytes, signature: str) -> bool:
        return backend.verify(data, signature, bundle.public_armor)

    ControlPlaneClient._validate_manifest(
        manifest,
        ORIGIN,
        approved_hash,
        signature_verifier=verifier,
    )

    malformed = copy.deepcopy(manifest)
    malformed["signature"] = {"alg": "capauth", "value": "not-a-signature"}
    malformed["manifest_sha256"] = canonical_manifest_hash(malformed)
    with pytest.raises(ControlPlaneClientError, match="signature"):
        ControlPlaneClient._validate_manifest(
            malformed,
            ORIGIN,
            canonical_manifest_hash(malformed),
            signature_verifier=verifier,
        )

    wrong_algorithm = copy.deepcopy(manifest)
    wrong_algorithm["signature"]["alg"] = "other"
    wrong_algorithm["manifest_sha256"] = canonical_manifest_hash(wrong_algorithm)
    with pytest.raises(ControlPlaneClientError, match="signature"):
        ControlPlaneClient._validate_manifest(
            wrong_algorithm,
            ORIGIN,
            canonical_manifest_hash(wrong_algorithm),
            signature_verifier=verifier,
        )

    tampered = copy.deepcopy(manifest)
    tampered["name"] = "tampered synthetic module"
    tampered["manifest_sha256"] = canonical_manifest_hash(tampered)
    with pytest.raises(ControlPlaneClientError, match="signature"):
        ControlPlaneClient._validate_manifest(
            tampered,
            ORIGIN,
            canonical_manifest_hash(tampered),
            signature_verifier=verifier,
        )

    wrong_audience = copy.deepcopy(manifest)
    wrong_audience["auth"]["audience"] = "other"
    wrong_audience["manifest_sha256"] = canonical_manifest_hash(wrong_audience)
    with pytest.raises(ControlPlaneClientError, match="audience"):
        ControlPlaneClient._validate_manifest(
            wrong_audience,
            ORIGIN,
            canonical_manifest_hash(wrong_audience),
            signature_verifier=verifier,
        )
