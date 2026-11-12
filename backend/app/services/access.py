"""列级借阅与准入裁决。

权限规则集中在这里，路由层只做参数搬运。几个关键约定：

* 数据集归属只从仓库里的 ``owner_id`` 读取，请求头里的身份只用来回答“你是谁”，
  不能自报所有者。
* 一个数据集有且只有一个所有者。无所有者的样例数据必须由维护者显式认领，
  认领前除平台管理员外没有人可以修改它，公开浏览者也不会因此获得写权限。
* 授权的最小单位是字段。同一字段上存在多份有效授权时，先比 ``priority``，
  再比 ``revision``，最后比决定编号；冲突双方都留在决定表里可查。
* 显式撤销会推进版本号，并把当前有效授权的相应字段标记为已撤销。任何在撤销
  之前创建的申请都带着旧的 ``base_revision``，在撤销之后不能再被批准；即使
  客户端拿着旧版本再次提交追加授权，也会被 409 拦下。
* 每次读取都重新计算有效字段，不缓存授权快照；只有已完成的查询保留完成当时
  的准入证据，并且结果只能取当时准入字段与当前可读字段的交集，不能借旧证据
  扩大读取。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

import app.clock as clock
from app.dataset_fields import dataset_columns, public_columns as declared_public_columns, split_columns
from app.errors import DomainError
from app.identity import ANONYMOUS_ID, DEMO_PRINCIPALS, Principal, SYSTEM, resolve_principal
from app.store import store

DATASTORE = "datastore"
REQUESTS = "borrow_requests"
DECISIONS = "access_decisions"
EVENTS = "access_events"

DECISION_GRANT = "授权"
DECISION_REVOKE = "撤销"

SOURCE_DIRECT = "直接授权"
SOURCE_APPROVAL = "申请批准"
SOURCE_REVOKE = "显式撤销"

GRANT_ACTIVE = "有效"
GRANT_REVOKED = "已撤销"
GRANT_EXPIRED = "已过期"
REVOKE_EXECUTED = "已执行"

REQUEST_PENDING = "待审批"
REQUEST_APPROVED = "已批准"
REQUEST_REJECTED = "已拒绝"
REQUEST_WITHDRAWN = "已撤回"
REQUEST_STALE = "已失效"

ROLE_ADMIN = "平台管理员"
ROLE_OWNER = "所有者"
ROLE_BORROWER = "借阅者"
ROLE_PUBLIC = "公开浏览者"

REVOKE_PRIORITY = 100
DIRECT_PRIORITY = 60
APPROVAL_PRIORITY = 40


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    return clock.now_utc()


def iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as error:
            raise DomainError(400, f"时间格式「{value}」无法识别，请使用 ISO 8601") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def next_id(table: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in table), default=0) + 1


def _principal_by_id(subject_id: str) -> Principal:
    if subject_id == ANONYMOUS_ID:
        return resolve_principal(None)
    principal = DEMO_PRINCIPALS.get(subject_id)
    if principal is None:
        raise DomainError(400, f"使用方「{subject_id}」不是可识别的账号")
    return principal


def _principal_name(subject_id: str | None) -> str:
    if not subject_id:
        return ""
    if subject_id in DEMO_PRINCIPALS:
        return DEMO_PRINCIPALS[subject_id].name
    return subject_id


def _dataset(dataset_id: int) -> dict[str, Any]:
    dataset = store.find(DATASTORE, dataset_id)
    if dataset is None:
        raise DomainError(404, f"数据集 {dataset_id} 不存在或已归档")
    ensure_dataset_access_fields(dataset)
    return dataset


def _owner_id(dataset: dict[str, Any]) -> str | None:
    value = dataset.get("owner_id")
    return str(value) if value else None


def _is_owner(dataset: dict[str, Any], principal: Principal) -> bool:
    return bool(principal.authenticated) and _owner_id(dataset) == principal.id


def can_manage(dataset: dict[str, Any], principal: Principal) -> bool:
    return principal.is_platform_admin or _is_owner(dataset, principal)


def _require_manage(dataset: dict[str, Any], principal: Principal) -> None:
    if not can_manage(dataset, principal):
        raise DomainError(403, "只有数据集所有者或平台管理员可以执行该操作")


def _require_authenticated(principal: Principal) -> None:
    if not principal.authenticated:
        raise DomainError(401, "该操作需要先建立身份；公开浏览者只能查看公开字段")


# ---------------------------------------------------------------------------
# 审计事件
# ---------------------------------------------------------------------------
def log_event(
    action: str,
    *,
    actor: Principal | None = None,
    dataset_id: int | None = None,
    subject_id: str | None = None,
    columns: Iterable[str] | None = None,
    request_id: int | None = None,
    decision_id: int | None = None,
    revision: int | None = None,
    outcome: str = "成功",
    detail: str = "",
    query_id: int | None = None,
) -> dict[str, Any]:
    events = store.rows(EVENTS)
    now = utc_now()
    event = {
        "id": next_id(events),
        "at": iso_time(now),
        "action": action,
        "outcome": outcome,
        "actor_id": actor.id if actor else SYSTEM.id,
        "actor_name": actor.name if actor else SYSTEM.name,
        "dataset_id": dataset_id,
        "subject_id": subject_id,
        "columns": list(columns or []),
        "request_id": request_id,
        "decision_id": decision_id,
        "query_id": query_id,
        "revision": revision,
        "detail": detail,
    }
    events.append(event)
    return event


def list_events(
    principal: Principal,
    *,
    dataset_id: int | None = None,
    subject_id: str | None = None,
    action: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    events = list(store.rows(EVENTS))
    if dataset_id is not None:
        events = [event for event in events if event.get("dataset_id") == dataset_id]
    if subject_id:
        events = [event for event in events if event.get("subject_id") == subject_id]
    if action:
        events = [event for event in events if event.get("action") == action]
    if not principal.is_platform_admin:
        owned = [
            int(row["id"]) for row in store.rows(DATASTORE)
            if _owner_id(row) == principal.id
        ]
        events = [
            event for event in events
            if event.get("actor_id") == principal.id
            or event.get("subject_id") == principal.id
            or event.get("dataset_id") in owned
        ]
    events.sort(key=lambda event: int(event["id"]), reverse=True)
    total = len(events)
    start = max(page - 1, 0) * size
    return events[start:start + size], total


def _log_conflict_if_needed(
    dataset: dict[str, Any],
    subject_id: str,
    actor: Principal,
    *,
    revision: int,
) -> None:
    resolution = resolve_grants(int(dataset["id"]), subject_id)
    conflicts = [
        {
            "column": column,
            "winner_decision_id": info["winner"]["decision_id"],
            "loser_decision_ids": [
                item["decision_id"] for item in info["candidates"]
                if item["decision_id"] != info["winner"]["decision_id"]
            ],
        }
        for column, info in resolution["columns"].items()
        if len(info["candidates"]) > 1
    ]
    if conflicts:
        log_event(
            "授权冲突",
            actor=actor,
            dataset_id=int(dataset["id"]),
            subject_id=subject_id,
            columns=sorted(item["column"] for item in conflicts),
            revision=revision,
            outcome="按优先级裁决",
            detail=json.dumps(
                {
                    "规则": "priority 高者优先；同优先级 revision 新者优先；最后比较决定编号",
                    "冲突": conflicts,
                },
                ensure_ascii=False,
            ),
        )


# ---------------------------------------------------------------------------
# 到期与有效授权
# ---------------------------------------------------------------------------
def expire_due(now: datetime | None = None) -> int:
    """把已到期的授权落成“已过期”状态并记一次事件；重复调用不会重复记账。"""
    now = now or utc_now()
    changed = 0
    for decision in store.rows(DECISIONS):
        if decision.get("decision_type") != DECISION_GRANT:
            continue
        if decision.get("status") != GRANT_ACTIVE:
            continue
        expires_at = parse_time(decision.get("expires_at"))
        if expires_at is None or expires_at > now:
            continue
        decision["status"] = GRANT_EXPIRED
        decision["expired_at"] = iso_time(now)
        changed += 1
        log_event(
            "授权到期",
            actor=SYSTEM,
            dataset_id=int(decision["dataset_id"]),
            subject_id=decision.get("subject_id"),
            columns=decision.get("columns"),
            decision_id=int(decision["id"]),
            request_id=decision.get("request_id"),
            revision=int(decision.get("revision", 0)),
            detail="授权到期，读取范围立即收窄",
        )
    return changed


def _effective_grant_columns(decision: dict[str, Any], now: datetime) -> list[str]:
    if decision.get("decision_type") != DECISION_GRANT:
        return []
    if decision.get("status") != GRANT_ACTIVE:
        return []
    expires_at = parse_time(decision.get("expires_at"))
    if expires_at is not None and expires_at <= now:
        return []
    granted = set(split_columns(decision.get("columns")))
    revoked = set(split_columns(decision.get("revoked_columns")))
    return [column for column in decision.get("columns", []) if column in granted - revoked]


def _decision_candidates(
    dataset_id: int,
    subject_id: str,
    now: datetime,
) -> list[dict[str, Any]]:
    decisions = [
        decision for decision in store.rows(DECISIONS)
        if decision.get("dataset_id") == dataset_id
        and decision.get("subject_id") == subject_id
        and decision.get("decision_type") == DECISION_GRANT
    ]
    candidates: list[dict[str, Any]] = []
    for decision in decisions:
        effective = _effective_grant_columns(decision, now)
        if effective:
            candidates.append({"decision": decision, "columns": effective})
    return candidates


def resolve_grants(
    dataset_id: int,
    subject_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """按字段裁决有效借阅范围，并给出每一列的冲突与胜出证据。"""
    now = now or utc_now()
    expire_due(now)
    resolution: dict[str, Any] = {"columns": {}, "readable": [], "revision": current_revision(dataset_id, subject_id)}
    for candidate in _decision_candidates(dataset_id, subject_id, now):
        decision = candidate["decision"]
        for column in candidate["columns"]:
            info = resolution["columns"].setdefault(
                column,
                {"candidates": [], "winner": None},
            )
            info["candidates"].append({
                "decision_id": int(decision["id"]),
                "source": decision.get("source"),
                "priority": int(decision.get("priority", 0)),
                "revision": int(decision.get("revision", 0)),
                "expires_at": decision.get("expires_at"),
                "request_id": decision.get("request_id"),
            })
    for column, info in resolution["columns"].items():
        info["candidates"].sort(
            key=lambda item: (item["priority"], item["revision"], item["decision_id"]),
            reverse=True,
        )
        info["winner"] = info["candidates"][0]
    resolution["readable"] = sorted(resolution["columns"])
    return resolution


def current_revision(dataset_id: int, subject_id: str) -> int:
    revisions = [
        int(decision.get("revision", 0))
        for decision in store.rows(DECISIONS)
        if decision.get("dataset_id") == dataset_id
        and decision.get("subject_id") == subject_id
    ]
    return max(revisions, default=0)


def _has_revocation_after(
    dataset_id: int,
    subject_id: str,
    base_revision: int,
    columns: Iterable[str],
) -> dict[str, Any] | None:
    wanted = set(columns)
    for decision in store.rows(DECISIONS):
        if decision.get("dataset_id") != dataset_id:
            continue
        if decision.get("subject_id") != subject_id:
            continue
        if decision.get("decision_type") != DECISION_REVOKE:
            continue
        if int(decision.get("revision", 0)) <= base_revision:
            continue
        revoked = set(split_columns(decision.get("columns")))
        if wanted & revoked:
            return decision
    return None



# ---------------------------------------------------------------------------
# 能力与字段可见性
# ---------------------------------------------------------------------------
def _role(dataset: dict[str, Any], principal: Principal) -> str:
    if principal.is_platform_admin:
        return ROLE_ADMIN
    if _is_owner(dataset, principal):
        return ROLE_OWNER
    if _owner_id(dataset) is None:
        # 无所有者的样例数据：只读浏览，不因此获得写权限。
        return ROLE_PUBLIC
    if principal.authenticated and resolve_grants(int(dataset["id"]), principal.id)["readable"]:
        return ROLE_BORROWER
    return ROLE_PUBLIC


def permission_view(dataset: dict[str, Any], principal: Principal) -> dict[str, Any]:
    expire_due()
    dataset_id = int(dataset["id"])
    declared = dataset_columns(dataset)
    public = declared_public_columns(dataset)
    owner = _owner_id(dataset)
    manage = can_manage(dataset, principal)
    role = _role(dataset, principal)
    borrowed = resolve_grants(dataset_id, principal.id) if principal.authenticated else {
        "columns": {}, "readable": [], "revision": 0,
    }
    if manage:
        readable = list(declared)
        borrowed = {"columns": {}, "readable": [], "revision": current_revision(dataset_id, principal.id)}
    elif owner is None:
        # 兼容：未认领的样例数据按“只读公开”处理，保留原有浏览体验，
        # 但不给任何来访者写权限；认领后立即回到所有者 + 公开字段的管辖。
        readable = list(declared)
    else:
        readable = [column for column in declared if column in set(public) | set(borrowed["readable"])]

    conflicts = [
        {
            "column": column,
            "winner": info["winner"],
            "candidates": info["candidates"],
        }
        for column, info in borrowed["columns"].items()
        if len(info["candidates"]) > 1
    ]
    grant_details = {
        column: info["winner"]
        for column, info in borrowed["columns"].items()
    }
    requestable: list[str] = []
    if (
        principal.authenticated
        and not manage
        and owner is not None
        and bool(dataset.get("borrow_enabled"))
    ):
        # 申请借阅需要知道可以申请的字段名；字段值、字段说明和定义仍然不返回。
        requestable = list(declared)

    return {
        "dataset_id": dataset_id,
        "dataset_code": dataset.get("数据集编号") if (manage or "数据集编号" in readable) else None,
        "dataset_name": dataset.get("数据集名称") if (manage or "数据集名称" in readable) else None,
        "owner_id": owner,
        "owner_name": _principal_name(owner),
        "ownerless": owner is None,
        "borrow_enabled": bool(dataset.get("borrow_enabled")),
        "viewer": principal.as_dict(),
        "viewer_role": role,
        "is_owner": _is_owner(dataset, principal),
        "can_manage": manage,
        "can_borrow": bool(
            principal.authenticated
            and not manage
            and owner is not None
            and bool(dataset.get("borrow_enabled"))
        ),
        "all_columns": list(declared) if manage else None,
        "requestable_columns": requestable,
        "public_columns": public,
        "granted_columns": borrowed["readable"],
        "readable_columns": readable,
        "column_grants": grant_details,
        "conflicts": conflicts,
        "current_revision": current_revision(dataset_id, principal.id) if principal.authenticated else 0,
    }


def mask_dataset_row(row: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """只返回当前身份可读的字段；隐藏字段连字段名都不出现。"""
    expire_due()
    view = permission_view(row, principal)
    if view["can_manage"]:
        result = dict(row)
        result["owner_name"] = view["owner_name"]
        result["ownerless"] = view["ownerless"]
        result["borrow_enabled"] = view["borrow_enabled"]
        result["can_manage"] = True
        result["can_borrow"] = False
        result["_visible_columns"] = dataset_columns(row)
        result["_role"] = view["viewer_role"]
        result["_readable_columns"] = list(view["readable_columns"])
        result["_current_revision"] = view["current_revision"]
        return result
    declared = dataset_columns(row)
    visible = [column for column in declared if column in view["readable_columns"]]
    result: dict[str, Any] = {
        "id": row.get("id"),
        "status": row.get("status"),
        "pending": row.get("pending"),
        "abnormal": row.get("abnormal"),
        "owner_id": view["owner_id"],
        "owner_name": view["owner_name"],
        "ownerless": view["ownerless"],
        "borrow_enabled": view["borrow_enabled"],
        "can_manage": False,
        "can_borrow": view["can_borrow"],
        "_visible_columns": visible,
        "_role": view["viewer_role"],
        "_readable_columns": visible,
        "_current_revision": view["current_revision"],
    }
    for column in visible:
        if column in row:
            result[column] = row[column]
    descriptions = row.get("字段说明")
    if isinstance(descriptions, dict):
        visible_descriptions = {
            key: value for key, value in descriptions.items() if key in visible
        }
        if visible_descriptions:
            result["字段说明"] = visible_descriptions
    return result


def readable_columns(dataset: dict[str, Any], principal: Principal) -> list[str]:
    return list(permission_view(dataset, principal)["readable_columns"])


# ---------------------------------------------------------------------------
# 认领与配置
# ---------------------------------------------------------------------------
def claim_dataset(
    dataset_id: int,
    principal: Principal,
    *,
    owner_id: str | None = None,
) -> dict[str, Any]:
    dataset = _dataset(dataset_id)
    _require_authenticated(principal)
    if not (principal.is_maintainer or principal.is_platform_admin):
        raise DomainError(403, "只有维护者可以认领或接管数据集归属")
    current_owner = _owner_id(dataset)
    target_owner = owner_id or principal.id
    if target_owner == ANONYMOUS_ID:
        raise DomainError(400, "不能把数据集归属到公开浏览者")
    if target_owner not in DEMO_PRINCIPALS:
        raise DomainError(400, f"账号「{target_owner}」不存在")
    if current_owner is not None and not principal.is_platform_admin:
        raise DomainError(403, f"数据集已归属 {current_owner}，只有平台管理员可以移交")
    if current_owner is not None and current_owner == target_owner and owner_id is None:
        raise DomainError(409, "该数据集已经归属当前账号，无需重复认领")
    dataset["owner_id"] = target_owner
    log_event(
        "认领" if current_owner is None else "归属移交",
        actor=principal,
        dataset_id=dataset_id,
        subject_id=target_owner,
        detail=f"归属由 {current_owner or '无所有者'} 变更为 {target_owner}",
    )
    return dataset


def configure_dataset(
    dataset_id: int,
    principal: Principal,
    *,
    borrow_enabled: bool | None = None,
    public_columns_value: Any = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    dataset = _dataset(dataset_id)
    _require_manage(dataset, principal)
    changed: list[str] = []
    if owner_id is not None:
        if not principal.is_platform_admin:
            raise DomainError(403, "只有平台管理员可以移交数据集归属")
        if owner_id not in DEMO_PRINCIPALS:
            raise DomainError(400, f"账号「{owner_id}」不存在")
        dataset["owner_id"] = owner_id
        changed.append("owner_id")
    if public_columns_value is not None:
        declared = dataset_columns(dataset)
        requested = split_columns(public_columns_value)
        unknown = [column for column in requested if column not in declared]
        if unknown:
            raise DomainError(400, f"公开字段必须来自数据集字段列表：{'、'.join(unknown)}")
        dataset["公开字段"] = requested
        changed.append("公开字段")
    if borrow_enabled is not None:
        dataset["borrow_enabled"] = bool(borrow_enabled)
        changed.append("borrow_enabled")
    log_event(
        "配置",
        actor=principal,
        dataset_id=dataset_id,
        detail=f"更新项：{'、'.join(changed) or '无'}",
    )
    return dataset


# ---------------------------------------------------------------------------
# 申请
# ---------------------------------------------------------------------------
def apply_for_access(
    dataset_id: int,
    principal: Principal,
    *,
    columns_value: Any,
    purpose: str,
    client_token: str | None = None,
    expected_revision: Any = None,
) -> dict[str, Any]:
    expire_due()
    dataset = _dataset(dataset_id)
    _require_authenticated(principal)
    if _owner_id(dataset) is None:
        raise DomainError(409, "数据集尚未认领，没有可审批归属的维护者")
    if not bool(dataset.get("borrow_enabled")):
        raise DomainError(409, "该数据集当前未开放借阅申请")
    if _is_owner(dataset, principal):
        raise DomainError(409, "所有者已经拥有完整读取能力，无需借阅")
    current = current_revision(dataset_id, principal.id)
    if expected_revision not in (None, ""):
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as error:
            raise DomainError(400, "expected_revision 必须是整数") from error
        if expected != current:
            # 过期客户端基于撤销前的范围追加申请：显式撤销优先，要求先刷新。
            raise DomainError(
                409,
                f"授权版本已从 {expected} 变为 {current}，显式撤销优先，请刷新后重新申请",
            )
    columns = split_columns(columns_value)
    if not columns:
        raise DomainError(400, "至少选择一个申请字段")
    declared = dataset_columns(dataset)
    unknown = [column for column in columns if column not in declared]
    if unknown:
        raise DomainError(400, f"字段不在数据集定义中：{'、'.join(unknown)}")
    requests = store.rows(REQUESTS)
    if client_token:
        for request in requests:
            if (
                str(request.get("client_token") or "") == client_token
                and request.get("dataset_id") == dataset_id
                and request.get("requester_id") == principal.id
            ):
                return request
    request = {
        "id": next_id(requests),
        "dataset_id": dataset_id,
        "requester_id": principal.id,
        "requester_name": principal.name,
        "columns": columns,
        "purpose": str(purpose or "").strip(),
        "status": REQUEST_PENDING,
        "base_revision": current,
        "client_token": client_token,
        "created_at": iso_time(utc_now()),
        "decided_at": None,
        "decided_by": None,
        "decision_remark": None,
        "closed_at": None,
        "closed_by": None,
        "close_reason": None,
        "expires_at": None,
        "grant_decision_id": None,
    }
    requests.append(request)
    log_event(
        "申请",
        actor=principal,
        dataset_id=dataset_id,
        subject_id=principal.id,
        columns=columns,
        request_id=request["id"],
        revision=current,
        detail=request["purpose"],
    )
    return request


def _invalidate_stale_requests(
    dataset_id: int,
    subject_id: str,
    revoked_columns: Iterable[str],
    revision: int,
    actor: Principal,
) -> list[int]:
    """显式撤销后，覆盖相同字段的待审批申请直接失效，不能靠旧版本再次获批。"""
    wanted = set(revoked_columns)
    affected: list[int] = []
    for request in store.rows(REQUESTS):
        if request.get("dataset_id") != dataset_id:
            continue
        if request.get("requester_id") != subject_id:
            continue
        if request.get("status") != REQUEST_PENDING:
            continue
        if int(request.get("base_revision", 0)) >= revision:
            continue
        if not (set(split_columns(request.get("columns"))) & wanted):
            continue
        request["status"] = REQUEST_STALE
        request["closed_at"] = iso_time(utc_now())
        request["closed_by"] = actor.id
        request["close_reason"] = "授权范围已收窄，需按新范围重新申请"
        affected.append(int(request["id"]))
        log_event(
            "申请失效",
            actor=actor,
            dataset_id=dataset_id,
            subject_id=subject_id,
            columns=request.get("columns"),
            request_id=request["id"],
            revision=revision,
            detail=request["close_reason"],
        )
    return affected


# ---------------------------------------------------------------------------
# 批准 / 拒绝 / 撤回
# ---------------------------------------------------------------------------
def decide_request(
    request_id: int,
    principal: Principal,
    *,
    decision: str,
    columns_value: Any = None,
    expires_at: Any = None,
    priority: Any = None,
    expected_revision: Any = None,
    remark: str = "",
) -> dict[str, Any]:
    expire_due()
    requests = store.rows(REQUESTS)
    request = next((item for item in requests if int(item.get("id", 0)) == request_id), None)
    if request is None:
        raise DomainError(404, f"借阅申请 {request_id} 不存在")
    dataset = _dataset(int(request["dataset_id"]))
    _require_manage(dataset, principal)
    if request.get("status") != REQUEST_PENDING:
        raise DomainError(409, f"借阅申请当前状态为 {request.get('status')}，不能再裁决")
    dataset_id = int(request["dataset_id"])
    subject_id = str(request["requester_id"])
    base_revision = int(request.get("base_revision", 0))
    current = current_revision(dataset_id, subject_id)
    if expected_revision not in (None, ""):
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as error:
            raise DomainError(400, "expected_revision 必须是整数") from error
        if expected != current and decision.strip() == "批准":            raise DomainError(
                409,
                f"授权版本已从 {expected} 变为 {current}，旧客户端不能继续追加授权",
            )
    requested_columns = split_columns(request.get("columns"))

    action = str(decision or "").strip()
    if action not in ("批准", "拒绝"):
        raise DomainError(400, "裁决只接受「批准」或「拒绝」")
    now = utc_now()
    if action == "拒绝":
        request["status"] = REQUEST_REJECTED
        request["decided_at"] = iso_time(now)
        request["decided_by"] = principal.id
        request["decision_remark"] = str(remark or "").strip() or None
        log_event(
            "拒绝",
            actor=principal,
            dataset_id=dataset_id,
            subject_id=subject_id,
            columns=requested_columns,
            request_id=request_id,
            revision=current,
            detail=request["decision_remark"] or "",
        )
        return request

    # 批准是“扩大读取”的动作，必须过版本屏障：显式撤销优先于旧版本的追加。
    barrier = _has_revocation_after(dataset_id, subject_id, base_revision, requested_columns)
    if barrier is not None:
        request["status"] = REQUEST_STALE
        request["closed_at"] = iso_time(utc_now())
        request["closed_by"] = principal.id
        request["close_reason"] = (
            f"申请创建后发生显式撤销（决定 {barrier['id']}，版本 {barrier.get('revision')}），"
            "显式撤销优先，旧申请不能追加授权"
        )
        log_event(
            "拒绝陈旧追加",
            actor=principal,
            dataset_id=dataset_id,
            subject_id=subject_id,
            columns=requested_columns,
            request_id=request_id,
            decision_id=int(barrier["id"]),
            revision=current,
            outcome="已拦下",
            detail=request["close_reason"],
        )
        raise DomainError(409, request["close_reason"])

    columns = split_columns(columns_value) if columns_value not in (None, "") else requested_columns
    if not columns:
        raise DomainError(400, "批准时必须保留至少一个字段")
    extra = [column for column in columns if column not in requested_columns]
    if extra:
        raise DomainError(400, f"批准字段不能超出申请范围：{'、'.join(extra)}")
    declared = dataset_columns(dataset)
    unknown = [column for column in columns if column not in declared]
    if unknown:
        raise DomainError(400, f"字段不在数据集定义中：{'、'.join(unknown)}")
    if not bool(dataset.get("borrow_enabled")):
        raise DomainError(409, "数据集已关闭借阅申请，不能新增批准授权")

    expiry = parse_time(expires_at)
    if expiry is not None and expiry <= now:
        raise DomainError(400, "到期时间必须晚于当前时间")
    try:
        value = int(priority) if priority not in (None, "") else APPROVAL_PRIORITY
    except (TypeError, ValueError) as error:
        raise DomainError(400, "priority 必须是整数") from error
    if not 1 <= value < REVOKE_PRIORITY:
        raise DomainError(400, f"priority 取值范围为 1-{REVOKE_PRIORITY - 1}")

    revision = current + 1
    decisions = store.rows(DECISIONS)
    grant = {
        "id": next_id(decisions),
        "dataset_id": dataset_id,
        "subject_id": subject_id,
        "subject_name": _principal_name(subject_id),
        "decision_type": DECISION_GRANT,
        "source": SOURCE_APPROVAL,
        "status": GRANT_ACTIVE,
        "columns": columns,
        "revoked_columns": [],
        "priority": value,
        "revision": revision,
        "expires_at": iso_time(expiry) if expiry else None,
        "created_at": iso_time(now),
        "created_by": principal.id,
        "request_id": request_id,
        "revoked_at": None,
        "revoked_by": None,
        "revoke_reason": None,
    }
    decisions.append(grant)
    request["status"] = REQUEST_APPROVED
    request["decided_at"] = iso_time(now)
    request["decided_by"] = principal.id
    request["decision_remark"] = str(remark or "").strip() or None
    request["expires_at"] = grant["expires_at"]
    request["grant_decision_id"] = grant["id"]
    log_event(
        "批准",
        actor=principal,
        dataset_id=dataset_id,
        subject_id=subject_id,
        columns=columns,
        request_id=request_id,
        decision_id=grant["id"],
        revision=revision,
        detail=f"来源={SOURCE_APPROVAL}，优先级={value}，到期={grant['expires_at'] or '长期'}",
    )
    _log_conflict_if_needed(dataset, subject_id, principal, revision=revision)
    return request


def withdraw_request(request_id: int, principal: Principal, reason: str = "") -> dict[str, Any]:
    request = next(
        (item for item in store.rows(REQUESTS) if int(item.get("id", 0)) == request_id),
        None,
    )
    if request is None:
        raise DomainError(404, f"借阅申请 {request_id} 不存在")
    if request.get("requester_id") != principal.id:
        raise DomainError(403, "只能撤回自己提交的申请")
    if request.get("status") != REQUEST_PENDING:
        raise DomainError(409, f"借阅申请当前状态为 {request.get('status')}，不能撤回")
    request["status"] = REQUEST_WITHDRAWN
    request["closed_at"] = iso_time(utc_now())
    request["closed_by"] = principal.id
    request["close_reason"] = str(reason or "").strip() or "申请人撤回"
    log_event(
        "撤回",
        actor=principal,
        dataset_id=int(request["dataset_id"]),
        subject_id=principal.id,
        columns=request.get("columns"),
        request_id=request_id,
        detail=request["close_reason"],
    )
    return request


# ---------------------------------------------------------------------------
# 直接授权与显式撤销
# ---------------------------------------------------------------------------
def grant_direct(
    dataset_id: int,
    principal: Principal,
    *,
    subject_id: str,
    columns_value: Any,
    expires_at: Any = None,
    priority: Any = None,
    expected_revision: Any = None,
    remark: str = "",
) -> dict[str, Any]:
    expire_due()
    dataset = _dataset(dataset_id)
    _require_manage(dataset, principal)
    subject = _principal_by_id(str(subject_id or ""))
    if not subject.authenticated:
        raise DomainError(400, "不能向公开浏览者授予借阅字段")
    if subject.id == principal.id:
        raise DomainError(409, "不能向所有者自己重复授权")
    columns = split_columns(columns_value)
    if not columns:
        raise DomainError(400, "至少选择一个授权字段")
    declared = dataset_columns(dataset)
    unknown = [column for column in columns if column not in declared]
    if unknown:
        raise DomainError(400, f"字段不在数据集定义中：{'、'.join(unknown)}")
    now = utc_now()
    expiry = parse_time(expires_at)
    if expiry is not None and expiry <= now:
        raise DomainError(400, "到期时间必须晚于当前时间")
    current = current_revision(dataset_id, subject.id)
    if expected_revision not in (None, ""):
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as error:
            raise DomainError(400, "expected_revision 必须是整数") from error
        if expected != current:
            raise DomainError(409, f"授权版本已从 {expected} 变为 {current}，请先刷新再提交")
    try:
        value = int(priority) if priority not in (None, "") else DIRECT_PRIORITY
    except (TypeError, ValueError) as error:
        raise DomainError(400, "priority 必须是整数") from error
    if not 1 <= value < REVOKE_PRIORITY:
        raise DomainError(400, f"priority 取值范围为 1-{REVOKE_PRIORITY - 1}")

    revision = current + 1
    decisions = store.rows(DECISIONS)
    grant = {
        "id": next_id(decisions),
        "dataset_id": dataset_id,
        "subject_id": subject.id,
        "subject_name": subject.name,
        "decision_type": DECISION_GRANT,
        "source": SOURCE_DIRECT,
        "status": GRANT_ACTIVE,
        "columns": columns,
        "revoked_columns": [],
        "priority": value,
        "revision": revision,
        "expires_at": iso_time(expiry) if expiry else None,
        "created_at": iso_time(now),
        "created_by": principal.id,
        "request_id": None,
        "revoked_at": None,
        "revoked_by": None,
        "revoke_reason": None,
    }
    decisions.append(grant)
    log_event(
        "授权",
        actor=principal,
        dataset_id=dataset_id,
        subject_id=subject.id,
        columns=columns,
        decision_id=grant["id"],
        revision=revision,
        detail=f"来源={SOURCE_DIRECT}，优先级={value}，到期={grant['expires_at'] or '长期'}，备注={str(remark or '').strip()}",
    )
    _log_conflict_if_needed(dataset, subject.id, principal, revision=revision)
    return grant


def revoke_access(
    dataset_id: int,
    principal: Principal,
    *,
    subject_id: str,
    columns_value: Any = None,
    expected_revision: Any = None,
    reason: str = "",
) -> dict[str, Any]:
    """显式撤销：推进版本、标记旧授权字段、让旧申请失效。"""
    expire_due()
    dataset = _dataset(dataset_id)
    _require_manage(dataset, principal)
    subject = _principal_by_id(str(subject_id or ""))
    if not subject.authenticated:
        raise DomainError(400, "公开浏览者没有可撤销的借阅授权")
    if subject.id == principal.id:
        raise DomainError(409, "所有者不需要撤销自己的授权")
    declared = dataset_columns(dataset)
    requested = split_columns(columns_value)
    if not requested or requested in (["*"], ["全部"]):
        target_columns = sorted(
            {
                column
                for decision in _decision_candidates(dataset_id, subject.id, utc_now())
                for column in _effective_grant_columns(decision["decision"], utc_now())
            }
        )
        if not target_columns:
            target_columns = list(declared)
    else:
        target_columns = requested
    unknown = [column for column in target_columns if column not in declared]
    if unknown:
        raise DomainError(400, f"字段不在数据集定义中：{'、'.join(unknown)}")
    current = current_revision(dataset_id, subject.id)
    if expected_revision not in (None, ""):
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as error:
            raise DomainError(400, "expected_revision 必须是整数") from error
        if expected != current:
            # 撤销是收敛权限的安全动作，版本不一致时仍以服务端最新状态执行，
            # 但要在响应和审计里说明，避免旧客户端误以为撤销没有发生。
            reason_suffix = f"（客户端版本 {expected} 与服务端 {current} 不一致，已按服务端执行）"
            reason = (str(reason or "").strip() + reason_suffix).strip()
    now = utc_now()
    revision = current + 1
    wanted = set(target_columns)
    affected: list[dict[str, Any]] = []
    for decision in store.rows(DECISIONS):
        if decision.get("dataset_id") != dataset_id:
            continue
        if decision.get("subject_id") != subject.id:
            continue
        if decision.get("decision_type") != DECISION_GRANT:
            continue
        effective = set(_effective_grant_columns(decision, now))
        overlap = sorted(effective & wanted)
        if not overlap:
            continue
        revoked = set(split_columns(decision.get("revoked_columns")))
        decision["revoked_columns"] = sorted(revoked | set(overlap))
        remaining = effective - set(overlap)
        if remaining:
            decision["status"] = GRANT_ACTIVE
        else:
            decision["status"] = GRANT_REVOKED
        decision["revoked_at"] = iso_time(now)
        decision["revoked_by"] = principal.id
        decision["revoke_reason"] = str(reason or "").strip() or None
        affected.append({
            "decision_id": int(decision["id"]),
            "revoked_columns": overlap,
            "remaining_columns": sorted(remaining),
            "status": decision["status"],
        })

    decisions = store.rows(DECISIONS)
    revocation = {
        "id": next_id(decisions),
        "dataset_id": dataset_id,
        "subject_id": subject.id,
        "subject_name": subject.name,
        "decision_type": DECISION_REVOKE,
        "source": SOURCE_REVOKE,
        "status": REVOKE_EXECUTED,
        "columns": target_columns,
        "revoked_columns": [],
        "priority": REVOKE_PRIORITY,
        "revision": revision,
        "expires_at": None,
        "created_at": iso_time(now),
        "created_by": principal.id,
        "request_id": None,
        "affected_decisions": [item["decision_id"] for item in affected],
        "revoked_at": None,
        "revoked_by": None,
        "revoke_reason": str(reason or "").strip() or None,
    }
    decisions.append(revocation)
    stale_requests = _invalidate_stale_requests(
        dataset_id, subject.id, target_columns, revision, principal
    )
    log_event(
        "撤销",
        actor=principal,
        dataset_id=dataset_id,
        subject_id=subject.id,
        columns=target_columns,
        decision_id=revocation["id"],
        revision=revision,
        detail=(
            f"受影响授权={[item['decision_id'] for item in affected]}，"
            f"失效申请={stale_requests}，原因={revocation['revoke_reason'] or '未填写'}"
        ),
    )
    return {
        "revocation": revocation,
        "affected_decisions": affected,
        "invalidated_requests": stale_requests,
        "current_revision": revision,
    }


# ---------------------------------------------------------------------------
# 查询准入
# ---------------------------------------------------------------------------
def resolve_dataset(identifier: Any) -> dict[str, Any] | None:
    text = str(identifier or "").strip()
    if not text:
        return None
    rows = store.rows(DATASTORE)
    for row in rows:
        if str(row.get("数据集编号") or "") == text:
            return row
    for row in rows:
        if str(row.get("数据集名称") or "") == text:
            return row
    if text.isdigit():
        return store.find(DATASTORE, int(text))
    return None


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def admit_query(
    principal: Principal,
    *,
    dataset_identifier: Any,
    requested_columns_value: Any,
    query_id: int | None = None,
    revalidate: bool = False,
) -> dict[str, Any]:
    """返回查询准入证据；拒绝时抛 403，并记录拒绝原因，便于审计。"""
    expire_due()
    dataset = resolve_dataset(dataset_identifier)
    if dataset is None:
        log_event(
            "查询拒绝",
            actor=principal,
            subject_id=principal.id,
            query_id=query_id,
            outcome="拒绝",
            detail=f"目标数据集「{dataset_identifier}」不存在",
        )
        raise DomainError(404, f"目标数据集「{dataset_identifier}」不存在或未公开")
    dataset_id = int(dataset["id"])
    view = permission_view(dataset, principal)
    requested = split_columns(requested_columns_value)
    readable = list(view["readable_columns"])
    if not requested:
        requested = list(readable)
    denied = [column for column in requested if column not in readable]
    if denied:
        log_event(
            "查询拒绝",
            actor=principal,
            dataset_id=dataset_id,
            subject_id=principal.id,
            columns=denied,
            query_id=query_id,
            outcome="拒绝",
            detail=(
                f"请求字段超出当前读取范围；角色={view['viewer_role']}，"
                f"可读字段={readable}"
            ),
        )
        raise DomainError(
            403,
            f"字段 {'、'.join(denied)} 不在当前身份的可读范围内；"
            f"当前角色为{view['viewer_role']}",
        )
    if not requested:
        log_event(
            "查询拒绝",
            actor=principal,
            dataset_id=dataset_id,
            subject_id=principal.id,
            query_id=query_id,
            outcome="拒绝",
            detail="当前身份没有任何可读字段",
        )
        raise DomainError(403, "当前身份没有任何可读字段，无法建立查询")
    grants = view["column_grants"] if view["viewer_role"] == ROLE_BORROWER else {}
    # 只保留真正覆盖获准字段的胜出决定，作为本次准入证据。
    decision_ids = sorted({
        int(grants[column]["decision_id"])
        for column in requested
        if column in grants
    })
    evidence_payload = {
        "dataset_id": dataset_id,
        "subject_id": principal.id,
        "role": view["viewer_role"],
        "requested_columns": requested,
        "admitted_columns": requested,
        "public_columns": view["public_columns"],
        "granted_columns": view["granted_columns"],
        "decision_ids": decision_ids,
        "revision": view["current_revision"],
        "evaluated_at": iso_time(utc_now()),
    }
    evidence = dict(evidence_payload)
    evidence["digest"] = _digest(evidence_payload)
    evidence["revalidated"] = revalidate
    log_event(
        "查询准入",
        actor=principal,
        dataset_id=dataset_id,
        subject_id=principal.id,
        columns=requested,
        query_id=query_id,
        revision=view["current_revision"],
        detail=(
            f"角色={view['viewer_role']}，证据摘要={evidence['digest'][:16]}，"
            f"决定={decision_ids or '公开字段/所有者'}，到期={_nearest_expiry(grants, requested)}"
        ),
    )
    return evidence


def _nearest_expiry(grants: dict[str, Any], columns: Iterable[str]) -> str:
    expiries = [
        grants[column]["expires_at"] for column in columns
        if column in grants and grants[column].get("expires_at")
    ]
    if not expiries:
        return "长期" if grants else "不适用"
    return min(expiries)


def completed_query_result(
    entry: dict[str, Any],
    principal: Principal,
) -> dict[str, Any]:
    """已完成查询只能读取“当时准入字段 ∩ 当前可读字段”，证据本身保留。"""
    expire_due()
    admitted = split_columns(entry.get("admitted_columns"))
    dataset_id = entry.get("dataset_id")
    dataset = store.find(DATASTORE, int(dataset_id)) if dataset_id is not None else None
    if dataset is None:
        return {
            "query_id": entry.get("id"),
            "admitted_columns": admitted,
            "served_columns": [],
            "withheld_columns": admitted,
            "evidence": entry.get("admission"),
            "note": "目标数据集已不存在，准入证据保留但结果不可再读取",
        }
    current = readable_columns(dataset, principal)
    served = [column for column in admitted if column in current]
    withheld = [column for column in admitted if column not in current]
    rows = list(entry.get("result_rows") or [])
    if withheld:
        rows = [
            {key: value for key, value in row.items() if key in served}
            for row in rows
        ]
    if not served:
        log_event(
            "结果拒绝",
            actor=principal,
            dataset_id=int(dataset["id"]),
            subject_id=principal.id,
            columns=withheld,
            query_id=int(entry["id"]),
            outcome="拒绝",
            detail="当前读取范围已不含任何准入字段；历史准入证据保留，结果不再下发",
        )
    return {
        "query_id": entry.get("id"),
        "admitted_columns": admitted,
        "served_columns": served,
        "withheld_columns": withheld,
        "rows": rows,
        "evidence": entry.get("admission"),
        "note": (
            "结果按完成时准入字段与当前可读字段的交集返回，旧证据不能扩大读取"
            if withheld else "结果与完成时准入字段一致"
        ),
    }


# ---------------------------------------------------------------------------
# 查询 / 决定列表
# ---------------------------------------------------------------------------

def list_decisions(
    dataset_id: int,
    principal: Principal,
    *,
    subject_id: str | None = None,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    expire_due()
    dataset = _dataset(dataset_id)
    manage = can_manage(dataset, principal)
    if manage:
        decisions = [
            decision for decision in store.rows(DECISIONS)
            if decision.get("dataset_id") == dataset_id
        ]
        if subject_id:
            decisions = [d for d in decisions if d.get("subject_id") == subject_id]
    else:
        subject = principal.id if principal.authenticated else "__none__"
        decisions = [
            decision for decision in store.rows(DECISIONS)
            if decision.get("dataset_id") == dataset_id
            and decision.get("subject_id") == subject
        ]
    if not include_inactive:
        decisions = [d for d in decisions if d.get("status") == GRANT_ACTIVE]
    decisions.sort(key=lambda decision: int(decision["id"]), reverse=True)
    return [dict(decision) for decision in decisions]


def list_requests(
    principal: Principal,
    *,
    dataset_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    expire_due()
    requests = list(store.rows(REQUESTS))
    if dataset_id is not None:
        requests = [r for r in requests if int(r.get("dataset_id", 0)) == dataset_id]
    if status:
        requests = [r for r in requests if r.get("status") == status]
    if not principal.is_platform_admin:
        owned_ids = [
            int(row["id"]) for row in store.rows(DATASTORE)
            if _owner_id(row) == principal.id
        ]
        requests = [
            r for r in requests
            if r.get("requester_id") == principal.id
            or int(r.get("dataset_id", -1)) in owned_ids
        ]
    requests.sort(key=lambda r: int(r.get("id", 0)), reverse=True)
    total = len(requests)
    start = max(page - 1, 0) * size
    return requests[start:start + size], total


# ---------------------------------------------------------------------------
# 兼容旧模块的辅助函数
# ---------------------------------------------------------------------------
def ensure_dataset_access_fields(dataset: dict[str, Any]) -> None:
    if "owner_id" not in dataset:
        dataset["owner_id"] = None
    if "公开字段" not in dataset:
        dataset["公开字段"] = []
    if "borrow_enabled" not in dataset:
        dataset["borrow_enabled"] = False
    if not isinstance(dataset.get("字段说明"), dict):
        dataset["字段说明"] = {}

