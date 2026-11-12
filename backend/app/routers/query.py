"""交互查询接口：维护查询语句，覆盖提交执行、启动查询、终止查询、保存结果等动作。

返回字段在执行时按服务端裁决收窄；前端隐藏列不能代替授权，直接调用接口提交
越权字段会被 403 拒绝，并留下拒绝审计。
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.errors import DomainError
from app.identity import Principal, current_principal
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.query import service

router = APIRouter(prefix="/api/query", tags=["交互查询"])

LIST_FIELDS = ["语句编号", "查询表达式", "目标数据集", "返回字段", "排序条件", "分页大小", "最大行数", "超时秒数"]
STATUSES = ["草稿", "待执行", "执行中", "已完成", "已超时"]


def _to_http(error: DomainError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


@router.get("", response_model=PageResult[dict])
def list_entries(
    principal: Annotated[Principal, Depends(current_principal)],
    keyword: str | None = Query(default=None, description="按语句编号检索"),
    status: str | None = Query(default=None, description="草稿、待执行、执行中、已完成、已超时"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按语句编号与状态过滤交互查询列表；只看自己的查询或自己数据集上的查询。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(principal, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    return service.export_entries(principal)


@router.get("/{entry_id}/result", response_model=dict)
def get_result(
    entry_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict:
    """已完成查询的结果：按“完成时准入字段 ∩ 当前可读字段”返回，证据保留。"""
    try:
        return service.result(entry_id, principal)
    except DomainError as error:
        raise _to_http(error) from error


@router.get("/{entry_id}", response_model=dict)
def get_entry(
    entry_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict:
    """读取单条查询语句明细；不存在或无权查看时给出可读的错误说明。"""
    entry = service.get_entry(entry_id, principal)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"查询语句 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """登记一条查询语句；目标数据集与返回字段在登记时先做一次列级准入。"""
    try:
        entry, missing = service.create_entry(payload.values, principal)
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="查询语句已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(
    entry_id: int,
    payload: EntryPayload,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ActionResult:
    """执行提交执行、启动查询、终止查询、保存结果；执行时按最新范围重新裁决。"""
    action = str(payload.values.get("action") or "").strip()
    try:
        entry, message = service.run_action(entry_id, action, principal)
    except DomainError as error:
        return ActionResult(ok=False, message=error.message)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
