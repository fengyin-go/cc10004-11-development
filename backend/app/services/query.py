"""交互查询业务规则：状态流转、字段校验与筛选口径都收在这里。

查询执行前必须经过 ``services.access`` 的列级准入：客户端提交的返回字段只能是
当前身份可读的字段；空返回字段按“可读字段”补齐，而不是按数据集完整定义补齐。
已完成查询保存当时的准入证据，再次读取结果时按当前范围重新裁剪。
"""
from __future__ import annotations

from typing import Any

from app.dataset_fields import split_columns
from app.errors import DomainError
from app.identity import Principal
from app.services import access
from app.store import store

MODULE = "query"
REQUIRED_FIELDS = ["语句编号", "查询表达式", "目标数据集"]
STATUS_ORDER = ["草稿", "待执行", "执行中", "已完成", "已超时"]
ACTION_RULES = {"提交执行": "待执行", "启动查询": "执行中", "终止查询": "已超时", "保存结果": "已完成"}
NEGATIVE_ACTIONS = []


class QueryService:
    def list_entries(
        self,
        principal: Principal,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("语句编号", ""))
                or keyword in str(row.get("查询表达式", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        rows = [row for row in rows if _can_view(row, principal)]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [_mask(row, principal) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int, principal: Principal) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None or not _can_view(entry, principal):
            return None
        return _mask(entry, principal)

    def create_entry(
        self, values: dict[str, Any], principal: Principal
    ) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        if not principal.authenticated:
            raise DomainError(401, "登记查询语句需要先建立身份")
        dataset = access.resolve_dataset(values.get("目标数据集"))
        if dataset is None:
            raise DomainError(404, f"目标数据集「{values.get('目标数据集')}」不存在或未公开")
        requested = split_columns(values.get("返回字段"))
        # 登记时先做一次准入检查，不把注定越权的语句留在待执行队列里。
        access.admit_query(
            principal,
            dataset_identifier=values.get("目标数据集"),
            requested_columns_value=requested,
        )
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for key, value in values.items():
            if key not in entry and key not in {"action", "返回字段"}:
                entry[key] = value
        entry["返回字段"] = "、".join(requested) if requested else "（按当前可读字段）"
        entry["dataset_id"] = int(dataset["id"])
        entry["created_by"] = principal.id
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["admission"] = None
        entry["admitted_columns"] = []
        entry["result_rows"] = []
        rows.append(entry)
        access.log_event(
            "登记查询",
            actor=principal,
            dataset_id=int(dataset["id"]),
            subject_id=principal.id,
            columns=requested,
            query_id=int(entry["id"]),
            detail=f"目标数据集={dataset.get('数据集编号')}",
        )
        return entry, []
    def run_action(
        self, entry_id: int, action: str, principal: Principal
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None or not _can_view(entry, principal):
            raise DomainError(404, f"查询语句 {entry_id} 不存在或已归档")
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于交互查询可执行范围"
        if entry.get("status") == "已完成":
            raise DomainError(409, "已完成查询保留当时准入证据，不能再次执行或改状态")
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        dataset = entry.get("dataset_id")
        if dataset is None:
            resolved = access.resolve_dataset(entry.get("目标数据集"))
            if resolved is None:
                raise DomainError(404, "查询未绑定有效的数据集，无法执行")
            entry["dataset_id"] = int(resolved["id"])
        raw_fields = str(entry.get("返回字段") or "").strip()
        requested = [] if raw_fields.startswith("（") else split_columns(raw_fields)
        # 任何会真正读取数据的动作都按“此刻”的范围重新裁决。
        if action in ("提交执行", "启动查询", "保存结果"):
            if principal.id != entry.get("created_by") and not principal.is_platform_admin:
                raise DomainError(403, "只有查询创建者可以执行该查询")
            evidence = access.admit_query(
                principal,
                dataset_identifier=entry.get("dataset_id"),
                requested_columns_value=requested,
                query_id=entry_id,
                revalidate=entry.get("admission") is not None,
            )
            entry["admission"] = evidence
            entry["admitted_columns"] = evidence["admitted_columns"]
            if action == "保存结果":
                entry["result_rows"] = _result_rows(entry, evidence)

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        access.log_event(
            "查询动作",
            actor=principal,
            dataset_id=entry.get("dataset_id"),
            subject_id=principal.id,
            columns=entry.get("admitted_columns") or [],
            query_id=entry_id,
            outcome="成功",
            detail=f"{action} → {target}",
        )
        return _mask(entry, principal), f"查询语句已{action}"

    def result(self, entry_id: int, principal: Principal) -> dict[str, Any]:
        entry = store.find(MODULE, entry_id)
        if entry is None or not _can_view(entry, principal):
            raise DomainError(404, f"查询语句 {entry_id} 不存在或已归档")
        if entry.get("status") != "已完成" or not entry.get("admission"):
            raise DomainError(409, "查询尚未完成，没有可读取的结果与准入证据")
        return access.completed_query_result(entry, principal)

    def export_entries(self, principal: Principal) -> dict[str, Any]:
        items, total = self.list_entries(principal, page=1, size=10000)
        return {"module": MODULE, "total": total, "items": items}


def _can_view(entry: dict[str, Any], principal: Principal) -> bool:
    """查询语句只对创建者、平台管理员和目标数据集所有者可见。"""
    if principal.is_platform_admin:
        return True
    if not principal.authenticated:
        return False
    if entry.get("created_by") == principal.id:
        return True
    dataset_id = entry.get("dataset_id")
    if dataset_id is None:
        return False
    dataset = store.find("datastore", int(dataset_id))
    return bool(dataset) and access.can_manage(dataset, principal)


def _mask(entry: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """返回字段只保留当前身份可读的部分；证据字段对创建者可见。"""
    result = dict(entry)
    admitted = split_columns(entry.get("admitted_columns"))
    if admitted:
        result["返回字段"] = "、".join(admitted)
    result.pop("result_rows", None)
    if not (principal.is_platform_admin or entry.get("created_by") == principal.id):
        result.pop("admission", None)
    return result


def _result_rows(entry: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """示例数据没有真实数据源，用字段名占位生成可验证的结果行。

    只生成准入字段的值，不附带字段列表、来源表等元数据，避免从结果里
    反推整份数据集定义。
    """
    rows = []
    for index, column in enumerate(evidence["admitted_columns"], start=1):
        rows.append({column: f"{column}-样例值{index}"})
    return rows


service = QueryService()
