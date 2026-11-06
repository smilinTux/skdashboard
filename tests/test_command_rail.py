from pathlib import Path

import pytest

from skdashboard.command_rail import CommandRail, CommandRailError, CommandSpec, SQLiteCommandState


class Adapter:
    def preview(self, target, parameters):
        return {"target": target, "parameters": dict(parameters)}

    def execute(self, target, parameters):
        return {"changed": True, "target": target}

    def rollback(self, target, receipt):
        return {"rolled_back": target}


def rail(tmp_path):
    return CommandRail(SQLiteCommandState(tmp_path / "commands.db"),
                       (CommandSpec("restart-service", "service-owner", "service.restart", Adapter()),), lambda _: 4)


def test_closed_registry_preview_and_idempotency(tmp_path):
    r = rail(tmp_path)
    assert r.registry == ({"name": "restart-service", "owner": "service-owner", "scope": "service.restart"},)
    preview = r.dispatch(command="restart-service", target="svc", parameters={}, actor="alice",
                         idempotency_key="p", scope="service.restart", preview=True)
    assert preview["status"] == "preview"
    result = r.dispatch(command="restart-service", target="svc", parameters={}, actor="alice",
                        idempotency_key="k", scope="service.restart")
    assert result["status"] == "executed"
    assert r.dispatch(command="restart-service", target="svc", parameters={}, actor="alice",
                      idempotency_key="k", scope="service.restart") == result
    with pytest.raises(CommandRailError):
        r.dispatch(command="restart-service", target="svc", parameters={"x": 1}, actor="alice",
                   idempotency_key="k", scope="service.restart")


def test_denial_conflict_and_rollback(tmp_path):
    r = rail(tmp_path)
    with pytest.raises(CommandRailError):
        r.dispatch(command="shell", target="x", parameters={}, actor="a", idempotency_key="x", scope="shell")
    with pytest.raises(CommandRailError):
        r.dispatch(command="restart-service", target="x", parameters={}, actor="a", idempotency_key="x", scope="wrong")
    conflict = r.dispatch(command="restart-service", target="x", parameters={}, actor="a", idempotency_key="x",
                          scope="service.restart", expected_version=2)
    assert conflict["status"] == "conflict"
    receipt = r.dispatch(command="restart-service", target="x", parameters={}, actor="a", idempotency_key="y",
                         scope="service.restart")
    assert r.rollback(command="restart-service", target="x", receipt=receipt, actor="a", scope="service.restart.rollback")["rolled_back"] == "x"
