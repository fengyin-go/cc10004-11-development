"""测试夹具：重置内存仓库，并提供带身份的测试客户端。

注意：``store`` 是进程内单例，每个用例开头调用 ``reset_state``，
避免不同用例之间串数据。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.clock as clock
from app.identity import DEMO_PRINCIPALS, resolve_principal
from app.main import app
from app.store import store


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    store.reset()
    monkeypatch.setattr(clock, "now_utc", clock.now_utc)


@pytest.fixture()
def actor(client: TestClient) -> dict[str, TestClient]:
    """按使用方 id 返回带 X-Actor-Id 的客户端。"""

    def _client(actor_id: str | None) -> TestClient:
        headers = {"X-Actor-Id": actor_id} if actor_id else {}
        return TestClient(app, headers=headers)

    return {"as": _client, "principal": resolve_principal, "accounts": DEMO_PRINCIPALS}
