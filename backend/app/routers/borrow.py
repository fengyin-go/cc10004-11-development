"""借阅治理接口：归属认领、借阅配置、申请、裁决、授权、撤销与审计。

路由层只搬运参数；所有归属判断、版本比较与字段裁决都在
``app.services.access`` 里，客户端身份不能自报所有者。
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.errors import DomainError
from app.identity import Principal, current_principal, demo_principals
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import access

router = APIRouter(prefix="/api/datastore", tags=["列级借阅"])


def _to_http(error: DomainError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


def _values(payload: EntryPayload) -> dict[str, Any]:
    return payload.values


# ---------------------------------------------------------------------------
# 身份与归属
# ---------------------------------------------------------------------------
@router.get("/access/principals", response_model=dict)
def list_principals() -> dict[str, Any]:
    """演示账号清单；真实部署应由登录会话提供身份，而不是让前端自选。"""
    return {"items": demo_principals()}


@router.get("/access/me", response_model=dict)
def whoami(principal: Annotated[Principal, Depends(current_principal)]) -> dict[str, Any]:
    return principal.as_dict()


@router.post("/{entry_id}/access/claim", response_model=ActionResult)
def claim_dataset(
    entry_id: int,
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """认领无所有者样例，或由平台管理员移交归属；认领后状态流转才可执行。"""
    try:
        values = _values(payload)
        dataset = access.claim_dataset(
            entry_id,
            principal,
            owner_id=(str(values["owner_id"]).strip() if values.get("owner_id") else None),
        )
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    return ActionResult(
        ok=True,
        message="数据集归属已确认",
        entry={"dataset_id": int(dataset["id"]), "owner_id": dataset.get("owner_id")},
    )


@router.post("/{entry_id}/access/config", response_model=ActionResult)
def configure_dataset(
    entry_id: int,
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """维护者配置公开字段与借阅开关；只有所有者或平台管理员可以执行。"""
    values = _values(payload)
    try:
        dataset = access.configure_dataset(
            entry_id,
            principal,
            borrow_enabled=values.get("borrow_enabled"),
            public_columns_value=values.get("public_columns"),
            owner_id=(str(values["owner_id"]).strip() if values.get("owner_id") else None),
        )
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    return ActionResult(
        ok=True,
        message="数据集借阅配置已更新",
        entry={
            "dataset_id": int(dataset["id"]),
            "owner_id": dataset.get("owner_id"),
            "public_columns": access.public_columns(dataset),
            "borrow_enabled": bool(dataset.get("borrow_enabled")),
        },
    )


@router.get("/{entry_id}/access/permission", response_model=dict)
def get_permission(
    entry_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    """当前身份的列级能力视图；前端用它渲染选项，真正的判断仍在服务端。"""
    try:
        entry = access._dataset(entry_id)
    except DomainError as error:
        raise _to_http(error) from error
    return access.permission_view(entry, principal)


# ---------------------------------------------------------------------------
# 申请与裁决
# ---------------------------------------------------------------------------
@router.get("/access/requests", response_model=PageResult[dict])
def list_requests(
    principal: Annotated[Principal, Depends(current_principal)],
    dataset_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """所有者看自己数据集上的申请，使用方只看自己提交的申请。"""
    items, total = access.list_requests(
        principal, dataset_id=dataset_id, status=status, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/{entry_id}/access/requests", response_model=ActionResult)
def apply_for_access(
    entry_id: int,
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """使用方按字段申请借阅；同一 client_token 重复提交返回同一条申请。"""
    values = _values(payload)
    try:
        request = access.apply_for_access(
            entry_id,
            principal,
            columns_value=values.get("columns"),
            purpose=str(values.get("purpose") or ""),
            client_token=(str(values["client_token"]).strip() if values.get("client_token") else None),
            expected_revision=values.get("expected_revision"),
        )
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    return ActionResult(ok=True, message="借阅申请已提交", entry=request)


@router.post("/access/requests/{request_id}/decision", response_model=ActionResult)
def decide_request(
    request_id: int,
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """批准或拒绝申请；批准可收回部分字段并设定到期时间。

    显式撤销之后提交的旧版本追加请求会被 409 拦下，无法再次扩大读取。
    """
    values = _values(payload)
    try:
        request = access.decide_request(
            request_id,
            principal,
            decision=str(values.get("decision") or ""),
            columns_value=values.get("columns"),
            expires_at=values.get("expires_at"),
            priority=values.get("priority"),
            expected_revision=values.get("expected_revision"),
            remark=str(values.get("remark") or ""),
        )
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    return ActionResult(ok=True, message=f"借阅申请已{request['status']}", entry=request)


@router.post("/access/requests/{request_id}/withdraw", response_model=ActionResult)
def withdraw_request(
    request_id: int,
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    try:
        request = access.withdraw_request(
            request_id, principal, reason=str(_values(payload).get("reason") or "")
        )
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    return ActionResult(ok=True, message="借阅申请已撤回", entry=request)


# ---------------------------------------------------------------------------
# 授权、撤销与决定查询
# ---------------------------------------------------------------------------
@router.post("/{entry_id}/access/decisions", response_model=ActionResult)
def grant_direct(
    entry_id: int,
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """维护者直接向使用方开放部分字段，可设到期时间与优先级。"""
    values = _values(payload)
    try:
        grant = access.grant_direct(
            entry_id,
            principal,
            subject_id=str(values.get("subject_id") or ""),
            columns_value=values.get("columns"),
            expires_at=values.get("expires_at"),
            priority=values.get("priority"),
            expected_revision=values.get("expected_revision"),
            remark=str(values.get("remark") or ""),
        )
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    return ActionResult(ok=True, message="列级授权已生效", entry=grant)


@router.post("/{entry_id}/access/revoke", response_model=ActionResult)
def revoke_access(
    entry_id: int,
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """显式撤销：立即收窄读取范围，旧版本申请不能靠追加请求恢复权限。"""
    values = _values(payload)
    try:
        result = access.revoke_access(
            entry_id,
            principal,
            subject_id=str(values.get("subject_id") or ""),
            columns_value=values.get("columns"),
            expected_revision=values.get("expected_revision"),
            reason=str(values.get("reason") or ""),
        )
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    return ActionResult(
        ok=True,
        message=f"显式撤销已执行，当前版本 {result['current_revision']}",
        entry=result,
    )


@router.get("/{entry_id}/access/decisions", response_model=dict)
def list_decisions(
    entry_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    subject_id: str | None = Query(default=None),
    include_inactive: bool = Query(default=True),
) -> dict[str, Any]:
    """所有者看全部决定；使用方只看与自己的决定，证据链都可追溯。"""
    try:
        decisions = access.list_decisions(
            entry_id,
            principal,
            subject_id=subject_id,
            include_inactive=include_inactive,
        )
    except DomainError as error:
        raise _to_http(error) from error
    return {"dataset_id": entry_id, "items": decisions, "total": len(decisions)}


@router.get("/{entry_id}/access/events", response_model=PageResult[dict])
def list_events(
    entry_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    subject_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """审计事件：申请、批准、拒绝、撤回、授权、到期、撤销、准入与拒绝都可查。"""
    items, total = access.list_events(
        principal,
        dataset_id=entry_id,
        subject_id=subject_id,
        action=action,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/access/events", response_model=PageResult[dict])
def list_all_events(
    principal: Annotated[Principal, Depends(current_principal)],
    dataset_id: int | None = Query(default=None),
    subject_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    items, total = access.list_events(
        principal,
        dataset_id=dataset_id,
        subject_id=subject_id,
        action=action,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)
