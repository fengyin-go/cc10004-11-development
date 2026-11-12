"""列级借阅的可观察流程与边界测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import app.clock as clock
from app.identity import DEMO_PRINCIPALS

OWNER = "owner-alpha"
BORROWER = "borrower-gamma"
ADMIN = "platform-admin"


def _set_time(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(clock, "now_utc", lambda: moment)


def test_owner_borrower_public_have_distinct_capabilities(client, actor):
    owner_view = actor["as"](OWNER).get("/api/datastore/3/permission").json()
    borrower_view = actor["as"](BORROWER).get("/api/datastore/3/permission").json()
    public_view = actor["as"](None).get("/api/datastore/3/permission").json()

    assert owner_view["can_manage"] is True
    assert owner_view["readable_columns"] == owner_view["all_columns"]
    assert borrower_view["can_manage"] is False
    assert borrower_view["readable_columns"] == ["数据集编号", "数据集名称"]
    # 公开浏览者只有显式公开的字段，不能借用借阅者的获准列
    assert public_view["readable_columns"] == ["数据集名称"]
    assert public_view["viewer_role"] == "公开浏览者"
    assert public_view["can_borrow"] is False


def test_ownerless_seed_is_readable_but_not_mutable(client, actor):
    admin = actor["as"](ADMIN)
    public = actor["as"](None)
    maintainer = actor["as"](OWNER)

    # 无所有者样例仍可浏览，兼容原有样例数据
    assert public.get("/api/datastore/1").status_code == 200
    assert "数据集编号" in public.get("/api/datastore/1").json()

    # 但没有任何来访者能直接修改它
    for client_ in (public, maintainer, actor["as"](BORROWER)):
        result = client_.post("/api/datastore/1/actions", json={"values": {"action": "提交审核"}})
        assert result.status_code == 200
        assert result.json()["ok"] is False
        assert "认领" in result.json()["message"]

    # 只有维护者可以认领，认领后才有所有者
    claimed = maintainer.post("/api/datastore/1/access/claim", json={"values": {}})
    assert claimed.status_code == 200
    assert claimed.json()["ok"] is True
    assert claimed.json()["entry"]["owner_id"] == OWNER
    after = maintainer.post("/api/datastore/1/actions", json={"values": {"action": "提交审核"}})
    assert after.json()["ok"] is True

    # 平台管理员可以，不需要先认领
    admin.post("/api/datastore/2/access/claim", json={"values": {"owner_id": OWNER}})
    admin_result = admin.post("/api/datastore/2/actions", json={"values": {"action": "提交审核"}})
    assert admin_result.json()["ok"] is True


def test_client_cannot_self_declare_owner_or_bypass_columns(client, actor):
    # 借阅者直接提交越权返回字段：服务端拒绝，并留下拒绝记录
    denied = actor["as"](BORROWER).post(
        "/api/datastore/3/access/claim", json={"values": {}}
    )
    assert denied.status_code == 200
    assert denied.json()["ok"] is False

    denied = actor["as"](BORROWER).post(
        "/api/query",
        json={
            "values": {
                "语句编号": "QUER-BAD",
                "查询表达式": "SELECT 缓存开关",
                "目标数据集": "DATA-0003",
                "返回字段": "缓存开关",
            }
        },
    )
    assert denied.status_code == 200
    assert denied.json()["ok"] is False
    assert "不在当前身份的可读范围" in denied.json()["message"]
    events = actor["as"](OWNER).get(
        "/api/datastore/3/access/events", params={"action": "查询拒绝"}
    ).json()
    assert events["total"] >= 1

    # 列表/导出也不会返回未授权字段（字段名也不出现）
    listing = actor["as"](BORROWER).get("/api/datastore").json()
    row = next(item for item in listing["items"] if item["id"] == 3)
    assert "缓存开关" not in row
    exported = actor["as"](BORROWER).get("/api/datastore/export").json()
    exported_row = next(item for item in exported["items"] if item["id"] == 3)
    assert "缓存开关" not in exported_row


def test_apply_approve_expiry_and_revoke_are_observable(client, actor, monkeypatch):
    owner = actor["as"](OWNER)
    borrower = actor["as"](BORROWER)
    now = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
    _set_time(monkeypatch, now)

    # 1) 申请
    request = borrower.post(
        "/api/datastore/3/access/requests",
        json={"values": {"columns": ["字段列表", "更新策略"], "purpose": "报表取数"}},
    ).json()["entry"]
    assert request["status"] == "待审批"
    owner_events = owner.get("/api/datastore/3/access/events", params={"action": "申请"}).json()
    assert owner_events["total"] >= 1

    # 2) 批准，带到期时间，只批其中一列
    decided = owner.post(
        f"/api/datastore/access/requests/{request['id']}/decision",
        json={
            "values": {
                "decision": "批准",
                "columns": ["字段列表"],
                "expires_at": (now + timedelta(hours=1)).isoformat(),
            }
        },
    ).json()
    assert decided["ok"] is True
    assert decided["entry"]["status"] == "已批准"
    assert decided["entry"]["expires_at"].startswith("2026-11-12T09:00")

    # 3) 到期前可读，到期后立即收窄
    before = borrower.get("/api/datastore/3/permission").json()
    assert {"字段列表", "数据集编号", "数据集名称"} <= set(before["readable_columns"])
    _set_time(monkeypatch, now + timedelta(hours=2))
    after = borrower.get("/api/datastore/3/permission").json()
    assert "字段列表" not in after["readable_columns"]
    expired = owner.get(
        "/api/datastore/3/access/events", params={"action": "授权到期"}
    ).json()
    assert expired["total"] >= 1

    # 4) 显式撤销，写审计并可查决定链
    revoked = owner.post(
        "/api/datastore/3/access/revoke",
        json={"values": {"subject_id": BORROWER, "columns": ["数据集编号"], "reason": "项目结束"}},
    ).json()
    assert revoked["ok"] is True
    assert revoked["entry"]["current_revision"] >= 1
    borrower_after = borrower.get("/api/datastore/3/permission").json()
    assert "数据集编号" not in borrower_after["readable_columns"]
    decisions = owner.get("/api/datastore/3/access/decisions").json()
    assert any(item["decision_type"] == "撤销" for item in decisions["items"])
    events = owner.get("/api/datastore/3/access/events").json()
    actions = {item["action"] for item in events["items"]}
    assert {"申请", "批准", "授权到期", "撤销"} <= actions


def test_narrowed_scope_applies_to_new_queries_and_not_completed_evidence(
    client, actor, monkeypatch
):
    owner = actor["as"](OWNER)
    borrower = actor["as"](BORROWER)
    now = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
    _set_time(monkeypatch, now)

    query = borrower.post(
        "/api/query",
        json={
            "values": {
                "语句编号": "QUER-OK",
                "查询表达式": "SELECT 数据集编号, 数据集名称",
                "目标数据集": "DATA-0003",
                "返回字段": "数据集编号, 数据集名称",
            }
        },
    ).json()["entry"]
    for action in ("提交执行", "启动查询", "保存结果"):
        result = borrower.post(
            f"/api/query/{query['id']}/actions", json={"values": {"action": action}}
        )
        assert result.json()["ok"] is True
    result_before = borrower.get(f"/api/query/{query['id']}/result").json()
    assert result_before["served_columns"] == ["数据集编号", "数据集名称"]
    evidence = result_before["evidence"]
    assert evidence["admitted_columns"] == ["数据集编号", "数据集名称"]
    assert evidence["digest"]

    # 收窄范围后：已完成查询保留当时的准入证据，但结果不能再用旧证据扩大读取
    owner.post(
        "/api/datastore/3/access/revoke",
        json={"values": {"subject_id": BORROWER, "columns": ["数据集编号"], "reason": "范围收窄"}},
    )
    result_after = borrower.get(f"/api/query/{query['id']}/result").json()
    assert result_after["admitted_columns"] == ["数据集编号", "数据集名称"]
    assert result_after["served_columns"] == ["数据集名称"]
    assert result_after["withheld_columns"] == ["数据集编号"]
    assert result_after["evidence"] == evidence

    # 新查询立即按新范围裁决
    denied = borrower.post(
        "/api/query",
        json={
            "values": {
                "语句编号": "QUER-NEW",
                "查询表达式": "SELECT 数据集编号",
                "目标数据集": "DATA-0003",
                "返回字段": "数据集编号",
            }
        },
    ).json()
    assert denied["ok"] is False
    assert "数据集编号" in denied["message"]


def test_conflict_priority_and_stale_client_after_revoke(client, actor, monkeypatch):
    owner = actor["as"](OWNER)
    borrower = actor["as"](BORROWER)
    now = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
    _set_time(monkeypatch, now)

    # 低优先级直接授权 + 高优先级批准，两决定都保留
    low = owner.post(
        "/api/datastore/3/access/decisions",
        json={
            "values": {
                "subject_id": BORROWER,
                "columns": ["更新策略"],
                "priority": 10,
                "expires_at": (now + timedelta(days=7)).isoformat(),
            }
        },
    ).json()
    assert low["ok"] is True
    high = owner.post(
        "/api/datastore/3/access/decisions",
        json={"values": {"subject_id": BORROWER, "columns": ["更新策略"], "priority": 80}},
    ).json()
    assert high["ok"] is True

    view = borrower.get("/api/datastore/3/permission").json()
    conflict = next(item for item in view["conflicts"] if item["column"] == "更新策略")
    assert conflict["winner"]["decision_id"] == high["entry"]["id"]
    assert len(conflict["candidates"]) == 2

    # 撤销后，旧客户端拿旧版本继续追加请求会被 409 拦下
    revision_before = view["current_revision"]
    revoked = owner.post(
        "/api/datastore/3/access/revoke",
        json={"values": {"subject_id": BORROWER, "columns": ["更新策略"], "reason": "口径收回"}},
    ).json()
    assert revoked["ok"] is True
    stale_grant = owner.post(
        "/api/datastore/3/access/decisions",
        json={
            "values": {
                "subject_id": BORROWER,
                "columns": ["更新策略"],
                "expected_revision": revision_before,
            }
        },
    ).json()
    assert stale_grant["ok"] is False
    assert "版本" in stale_grant["message"]
    current_view = borrower.get("/api/datastore/3/permission").json()
    assert "更新策略" not in current_view["readable_columns"]

    # 撤销之后，旧客户端带旧版本继续追加申请会被 409 拦下
    stale_apply = borrower.post(
        "/api/datastore/3/access/requests",
        json={
            "values": {
                "columns": ["更新策略"],
                "purpose": "旧页面追加",
                "expected_revision": revision_before,
            }
        },
    ).json()
    assert stale_apply["ok"] is False
    assert "版本" in stale_apply["message"]

    # 新版本重新申请并获批是允许的：撤销只阻止旧版本的追加
    new_request = borrower.post(
        "/api/datastore/3/access/requests",
        json={"values": {"columns": ["更新策略"], "purpose": "重新按新口径申请"}},
    ).json()["entry"]
    new_decision = owner.post(
        f"/api/datastore/access/requests/{new_request['id']}/decision",
        json={"values": {"decision": "批准"}},
    ).json()
    assert new_decision["ok"] is True
    refreshed = borrower.get("/api/datastore/3/permission").json()
    assert "更新策略" in refreshed["readable_columns"]

    # 撤销之前创建的申请在撤销时立即失效；即使客户端把旧申请恢复成待审批，
    # 批准时也会再次被版本屏障拦下。
    old_request = borrower.post(
        "/api/datastore/3/access/requests",
        json={"values": {"columns": ["生命周期天数"], "purpose": "旧版本追加"}},
    ).json()["entry"]
    owner.post(
        "/api/datastore/3/access/revoke",
        json={"values": {"subject_id": BORROWER, "columns": ["生命周期天数"], "reason": "收紧"}},
    )
    stale_decision = owner.post(
        f"/api/datastore/access/requests/{old_request['id']}/decision",
        json={"values": {"decision": "批准"}},
    ).json()
    assert stale_decision["ok"] is False
    assert "已失效" in stale_decision["message"]

    from app.store import store

    pending_row = store.find("borrow_requests", int(old_request["id"]))
    pending_row["status"] = "待审批"
    barrier_result = owner.post(
        f"/api/datastore/access/requests/{old_request['id']}/decision",
        json={"values": {"decision": "批准"}},
    ).json()
    assert barrier_result["ok"] is False
    assert "显式撤销" in barrier_result["message"]




def test_unauthorized_identities_are_treated_as_public_viewers(client, actor):
    unknown = actor["as"]("someone-else")
    me = unknown.get("/api/datastore/access/me").json()
    assert me["id"] == "anonymous"
    assert me["authenticated"] is False

    claim = unknown.post("/api/datastore/1/access/claim", json={"values": {}}).json()
    assert claim["ok"] is False
    action = unknown.post(
        "/api/datastore/1/actions", json={"values": {"action": "提交审核"}}
    ).json()
    assert action["ok"] is False


def test_keyword_search_does_not_leak_hidden_values(client, actor):
    """检索口径只作用于当前可读字段，未授权字段的值不能用来反推。"""
    borrower = actor["as"](BORROWER)
    public = actor["as"](None)
    owner = actor["as"](OWNER)

    # 公开浏览者只读“数据集名称”，用仅在“数据集编号”上出现的值检索不应命中
    hidden = public.get("/api/datastore", params={"keyword": "DATA-0003"}).json()
    assert hidden["total"] == 0
    visible = public.get("/api/datastore", params={"keyword": "数据集管理样例3"}).json()
    assert visible["total"] == 1
    # 所有者能读到数据集编号，同样的关键词可以命中
    owner_hit = owner.get("/api/datastore", params={"keyword": "DATA-0003"}).json()
    assert owner_hit["total"] == 1
    # 借阅者获准了数据集编号，也能命中
    borrower_hit = borrower.get("/api/datastore", params={"keyword": "DATA-0003"}).json()
    assert borrower_hit["total"] == 1


def test_owner_cannot_grant_more_than_requested_and_public_reads_stay_public(client, actor):
    owner = actor["as"](OWNER)
    public = actor["as"](None)
    request = public.get("/api/datastore/access/requests")
    # 公开浏览者没有任何申请，也不能借审批接口提交
    assert request.status_code == 200
    assert request.json()["total"] == 0
    denied = public.post(
        "/api/datastore/3/access/requests",
        json={"values": {"columns": ["数据集名称"]}},
    ).json()
    assert denied["ok"] is False

    pending = actor["as"](BORROWER).post(
        "/api/datastore/3/access/requests",
        json={"values": {"columns": ["数据集名称"], "purpose": "只读字段核对"}},
    ).json()["entry"]
    overreach = owner.post(
        f"/api/datastore/access/requests/{pending['id']}/decision",
        json={"values": {"decision": "批准", "columns": ["缓存开关"]}},
    ).json()
    assert overreach["ok"] is False
    assert "超出申请范围" in overreach["message"]


def test_dataset_without_columns_cannot_be_borrowed(client, actor):
    owner = actor["as"](OWNER)
    created = owner.post(
        "/api/datastore",
        json={
            "values": {
                "数据集编号": "DATA-0009",
                "数据集名称": "空字段数据集",
                "来源表名": "src_empty",
                "字段列表": "",
            }
        },
    )
    assert created.status_code == 200
    assert created.json()["ok"] is False
    assert "字段列表" in created.json()["message"]


def test_events_are_scoped_to_dataset_or_subject(client, actor):
    admin = actor["as"](ADMIN)
    borrower = actor["as"](BORROWER)
    owner = actor["as"](OWNER)
    borrower.post(
        "/api/datastore/3/access/requests",
        json={"values": {"columns": ["数据集名称"], "purpose": "范围测试"}},
    )
    admin_events = admin.get("/api/datastore/access/events", params={"dataset_id": 3}).json()
    assert admin_events["total"] >= 1
    owner_events = owner.get("/api/datastore/access/events", params={"dataset_id": 1}).json()
    # 所有者看不到别人数据集上的事件
    assert owner_events["total"] == 0
    other_events = borrower.get(
        "/api/datastore/access/events", params={"dataset_id": 999}
    ).json()
    assert other_events["total"] == 0

