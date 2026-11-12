"""数据集管理接口：维护数据集定义，覆盖提交审核、生效发布、废弃下线等动作。

列表、明细、导出都经过 ``services.access`` 的列级裁决：未获准的字段不会出现在
响应里，字段名也不会出现。归属与借阅配置的接口放在 ``routers/borrow.py``。
"""
from __future__ import annotations

from typing import Any, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.errors import DomainError
from app.identity import Principal, current_principal
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.datastore import service

router = APIRouter(prefix="/api/datastore", tags=["数据集管理"])

LIST_FIELDS = ["数据集编号", "数据集名称", "来源表名", "字段列表", "更新策略", "数据采样比例", "生命周期天数", "缓存开关"]
STATUSES = ["草稿", "待审核", "已生效", "已废弃"]


def _to_http(error: DomainError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


@router.get("", response_model=PageResult[dict])
def list_entries(
    principal: Annotated[Principal, Depends(current_principal)],
    keyword: str | None = Query(default=None, description="按数据集编号或名称检索"),
    status: str | None = Query(default=None, description="草稿、待审核、已生效、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按数据集编号与状态过滤数据集管理列表；只返回当前身份可读的字段。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(principal, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    """导出数据集管理清单：字段可见性与列表接口一致，不会因为导出而放宽。"""
    return service.export_entries(principal)


@router.get("/{entry_id}/permission", response_model=dict)
def get_permission(
    entry_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict:
    """当前身份对某数据集的能力与列级范围；用于前端渲染可选项，不代替服务端授权。"""
    try:
        return service.get_permission(entry_id, principal)
    except DomainError as error:
        raise _to_http(error) from error


@router.get("/{entry_id}", response_model=dict)
def get_entry(
    entry_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict:
    """读取单条数据集定义明细；未获准字段会被裁掉，不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id, principal)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"数据集定义 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """登记一条数据集定义，登记人成为所有者；缺字段时说明原因而不是静默丢弃。"""
    try:
        entry, missing = service.create_entry(payload.values, principal)
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="数据集定义已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(
    entry_id: int,
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """执行提交审核、生效发布、废弃下线；无归属样例要先认领，越权会被拦下。"""
    action = str(payload.values.get("action") or "").strip()
    try:
        entry, message = service.run_action(entry_id, action, principal)
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
