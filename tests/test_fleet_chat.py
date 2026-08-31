from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.testclient import TestClient

from skdashboard.dashboard import create_app
from skdashboard.fleet_chat import FRESHNESS_TTL_SECONDS, MAX_MESSAGES, REDACTED, fleet_chat
from skdashboard.read_only import create_read_only_app

LAN_ORIGIN = "https://10.0.0.139:7778"
READ_HEADERS = {"Authorization": "Bearer fleet-read", "Origin": LAN_ORIGIN}


def _mail_dir(home: Path) -> Path:
    path = home / "coordination" / "skmail.d"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record(
    index: int,
    *,
    sender: str = "pi-night-api-chiap03-c0f1d7a2",
    host: str = "chiap03",
    body: object = "safe",
) -> dict:
    ts = datetime(2026, 8, 31, tzinfo=timezone.utc) + timedelta(seconds=index)
    return {
        "ts": ts.isoformat(),
        "from": sender,
        "to": ["jarvis", "lumina"],
        "re": f"message {index}",
        "body": body,
        "priority": "high" if index % 2 else "low",
        "host": host,
    }


def _write_records(home: Path, name: str, records: list[object]) -> None:
    payload = "\n".join(json.dumps(record) for record in records) + "\n"
    (_mail_dir(home) / name).write_text(payload, encoding="utf-8")


def _authorizer(bearer: str, capability: str, target: str) -> bool:
    return (
        bearer == "fleet-read"
        and capability == "skdashboard.read"
        and target == "/api/v1/fleet-chat"
    )


def test_projection_is_newest_400_and_verifies_writer_identity(tmp_path: Path) -> None:
    sender = "pi-night-api-chiap03-c0f1d7a2"
    _write_records(
        tmp_path,
        f"{sender}@chiap03.jsonl",
        [_record(index) for index in range(MAX_MESSAGES + 5)],
    )

    projection = fleet_chat(tmp_path)

    assert projection["total"] == MAX_MESSAGES
    assert projection["source_total"] == MAX_MESSAGES + 5
    assert projection["freshness"]["truncated"] is True
    assert projection["messages"][0]["subject"] == "message 5"
    assert projection["messages"][-1]["subject"] == "message 404"
    assert projection["messages"][0] == {
        "ts": "2026-08-31T00:00:05Z",
        "from": sender,
        "agent": sender,
        "sender_verified": True,
        "to": ["jarvis", "lumina"],
        "subject": "message 5",
        "body": "safe",
        "redacted": False,
        "priority": "urgent",
        "host": "chiap03",
        "kind": "worker",
        "lane": "night-api",
        "card": "c0f1d7a2",
    }
    assert projection["source"] == "skmail.d"
    assert projection["read_only"] is True


def test_projection_orders_mixed_iso_widths_by_instant(tmp_path: Path) -> None:
    zero = _record(0, sender="lumina", host="chiap08")
    zero["re"] = "zero"
    zero["ts"] = "2026-08-31T00:00:00Z"
    zero["to"] = ["zero-channel"]
    later = _record(1, sender="lumina", host="chiap08")
    later["re"] = "later"
    later["ts"] = "2026-08-31T00:00:00.100000Z"
    later["to"] = ["later-channel"]
    _write_records(tmp_path, "lumina@chiap08.jsonl", [later, zero])

    projection = fleet_chat(
        tmp_path, now=datetime(2026, 8, 31, 0, 0, 1, tzinfo=timezone.utc)
    )

    assert [message["subject"] for message in projection["messages"]] == ["zero", "later"]
    assert projection["freshness"]["observed_at"] == later["ts"]
    assert [channel["name"] for channel in projection["channels"]] == [
        "later-channel",
        "zero-channel",
    ]


def test_freshness_reports_stale_and_future_observations(tmp_path: Path) -> None:
    record = _record(0, sender="lumina", host="chiap08")
    _write_records(tmp_path, "lumina@chiap08.jsonl", [record])
    observed = datetime.fromisoformat(record["ts"])

    stale = fleet_chat(
        tmp_path, now=observed + timedelta(seconds=FRESHNESS_TTL_SECONDS + 1)
    )["freshness"]
    assert stale["truth_state"] == "stale"
    assert stale["age_seconds"] == FRESHNESS_TTL_SECONDS + 1
    assert stale["ttl_seconds"] == FRESHNESS_TTL_SECONDS
    assert stale["future_seconds"] == 0

    future = fleet_chat(tmp_path, now=observed - timedelta(seconds=10))["freshness"]
    assert future["truth_state"] == "unavailable"
    assert future["age_seconds"] == 0
    assert future["future_seconds"] == 10


def test_invalid_records_are_counted_without_raw_payload_and_secrets_are_redacted(
    tmp_path: Path,
) -> None:
    sender = "lumina"
    safe = _record(
        10,
        sender=sender,
        host="chiap08",
        body={
            "password": "hunter" + "2",  # pragma: allowlist secret  # gitleaks:allow
            "nested": {
                "api_key": "sk-" + "abcdefghijklmnop",  # pragma: allowlist secret  # gitleaks:allow
                "note": "Bearer " + "abcdefghijkl" + " protected payload=sealed-value",
                "key": "key-material",
            },
        },
    )
    safe["re"] = "credential=" + "visible-no-more"
    bad_sender = _record(11, sender="jarvis", host="chiap08")
    bad_host = _record(12, sender=sender, host="chiap04")
    bad_timestamp = _record(13, sender=sender, host="chiap08")
    bad_timestamp["ts"] = "not-a-time"
    bad_recipients = _record(14, sender=sender, host="chiap08")
    bad_recipients["to"] = {"protected_payload": "do-not-" + "return"}
    _write_records(
        tmp_path,
        "lumina@chiap08.jsonl",
        [safe, bad_sender, bad_host, bad_timestamp, bad_recipients],
    )
    (_mail_dir(tmp_path) / "lumina@chiap08.sync-conflict.jsonl").write_text(
        "RAW_INVALID_SECRET\n", encoding="utf-8"
    )
    with (_mail_dir(tmp_path) / "lumina@chiap08.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("RAW_INVALID_SECRET\n")

    projection = fleet_chat(tmp_path)
    serialized = json.dumps(projection)

    assert projection["total"] == 1
    assert projection["partial"] is True
    assert projection["freshness"]["truth_state"] == "stale"
    assert {error["code"] for error in projection["errors"]} == {
        "INVALID_JSON",
        "INVALID_RECIPIENTS",
        "INVALID_TIMESTAMP",
        "INVALID_WRITER_FILE",
        "WRITER_HOST_MISMATCH",
        "WRITER_IDENTITY_MISMATCH",
    }
    assert projection["invalid_records"] == 6
    assert projection["messages"][0]["redacted"] is True
    assert "[REDACTED]" in serialized
    for secret in (
        "hunter" + "2",
        "sk-" + "abcdefghijklmnop",
        "abcdefghijkl",
        "visible-no-more",
        "sealed-value",
        "key-material",
        "do-not-return",
        "RAW_INVALID_SECRET",
    ):
        assert secret not in serialized


def test_recipient_identities_are_validated_and_secret_shapes_are_redacted(
    tmp_path: Path,
) -> None:
    safe = _record(1, sender="lumina", host="chiap08")
    safe["to"] = ["Lumina", "password=" + "visible-value"]
    invalid = _record(2, sender="lumina", host="chiap08")
    invalid["to"] = ["not/a/recipient"]
    _write_records(tmp_path, "lumina@chiap08.jsonl", [safe, invalid])

    projection = fleet_chat(tmp_path)
    serialized = json.dumps(projection)

    assert projection["messages"][0]["to"] == ["lumina", REDACTED]
    assert projection["messages"][0]["redacted"] is True
    assert {channel["name"] for channel in projection["channels"]} == {"lumina", REDACTED}
    assert "visible-value" not in serialized
    assert {error["code"] for error in projection["errors"]} == {"INVALID_RECIPIENTS"}


def test_api_is_read_only_authorized_and_same_origin(tmp_path: Path) -> None:
    _write_records(tmp_path, "lumina@chiap08.jsonl", [_record(1, sender="lumina", host="chiap08")])
    client = TestClient(create_app(tmp_path, control_plane_authorizer=_authorizer))

    assert client.get("/api/v1/fleet-chat").status_code == 401
    assert (
        client.get(
            "/api/v1/fleet-chat",
            headers={"Authorization": "Bearer wrong", "Origin": LAN_ORIGIN},
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/fleet-chat",
            headers={"Authorization": "Bearer fleet-read", "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    response = client.get("/api/v1/fleet-chat", headers=READ_HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["total"] == 1
    assert client.post("/api/v1/fleet-chat", headers=READ_HEADERS).status_code == 405
    assert client.post("/api/v1/fleet-chat/send", headers=READ_HEADERS).status_code == 404


def test_authenticated_read_only_runtime_serves_page_asset_and_projection(tmp_path: Path) -> None:
    _write_records(tmp_path, "lumina@chiap08.jsonl", [_record(1, sender="lumina", host="chiap08")])
    client = TestClient(
        create_read_only_app(tmp_path, authorizer=_authorizer),
        base_url=LAN_ORIGIN,
    )

    page = client.get("/fleet-chat")
    assert page.status_code == 200
    assert 'aria-label="Fleet chat transcript"' in page.text
    assert 'for="fc-filter"' in page.text
    assert client.get("/static/js/fleet_chat.js").status_code == 200
    assert client.get("/api/v1/fleet-chat", headers=READ_HEADERS).status_code == 200


def test_transcript_declares_responsive_and_partial_state_contract() -> None:
    root = Path(__file__).parents[1] / "src" / "skdashboard" / "static"
    html = (root / "fleet_chat.html").read_text(encoding="utf-8")
    javascript = (root / "js" / "fleet_chat.js").read_text(encoding="utf-8")
    css = (root / "css" / "board.css").read_text(encoding="utf-8")

    assert 'name="viewport"' in html
    assert 'id="fc-status"' in html
    assert "Partial authorized projection." in javascript
    assert "Authorized projection stale." in javascript
    assert "Authorized projection unavailable." in javascript
    assert "invalid records excluded" in javascript
    assert "messages.slice(-400)" in javascript
    assert "@media (max-width:900px)" in css
    assert ".fc-wrap{grid-template-columns:1fr;}" in css
