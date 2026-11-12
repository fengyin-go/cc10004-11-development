"""数据集管理业务规则：状态流转、字段校验与筛选口径都收在这里。

原有状态流转保持不变；新增的归属、列级可见性与借阅开关统一委托给
``app.services.access``，保证所有读取都经过同一套裁决。
"""
from __future__ import annotations

from typing import Any

from app.dataset_fields import dataset_columns
from app.errors import DomainError
from app.identity import Principal
from app.services import access
from app.store import store

MODULE = "datastore"
REQUIRED_FIELDS = ["数据集编号", "数据集名称", "来源表名"]
EDITABLE_FIELDS = [
    "数据集编号",
    "数据集名称",
    "来源表名",
    "字段列表",
    "更新策略",
    "数据采样比例",
    "生命周期天数",
    "缓存开关",
]
STATUS_ORDER = ["草稿", "待审核", "已生效", "已废弃"]
ACTION_RULES = {"提交审核": "待审核", "生效发布": "已生效", "废弃下线": "已废弃"}
NEGATIVE_ACTIONS = []


class DatastoreService:
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
        if status:
            rows = [row for row in rows if row.get("status") == status]
        # 先按当前身份裁剪，再在可读列的值上检索：隐藏字段不能通过检索被反推出来。
        masked = [access.mask_dataset_row(row, principal) for row in rows]
        if keyword:
            masked = [
                row for row in masked
                if any(
                    keyword in str(row.get(column, ""))
                    for column in row.get("_visible_columns", [])
                )
            ]
        total = len(masked)
        start = max(page - 1, 0) * size
        return masked[start:start + size], total

    def get_entry(self, entry_id: int, principal: Principal) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return access.mask_dataset_row(entry, principal)

    def get_permission(self, entry_id: int, principal: Principal) -> dict[str, Any]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise DomainError(404, f"数据集定义 {entry_id} 不存在或已归档")
        return access.permission_view(entry, principal)

    def create_entry(
        self, values: dict[str, Any], principal: Principal
    ) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        access_admin_or_maintainer(principal)
        rows = store.rows(MODULE)
        declared = dataset_columns({"字段列表": values.get("字段列表")})
        if "字段列表" in values and not declared:
            raise DomainError(400, "字段列表不能为空，列级授权需要可授权的字段")
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for key, value in values.items():
            if key in EDITABLE_FIELDS and key not in entry:
                entry[key] = value
        if declared:
            entry["字段列表"] = ", ".join(declared)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 登记人自动成为所有者，避免新建数据集落入“无所有者可随意修改”的状态。
        entry["owner_id"] = principal.id
        entry["公开字段"] = []
        entry["borrow_enabled"] = False
        entry["字段说明"] = values.get("字段说明") if isinstance(values.get("字段说明"), dict) else {}
        rows.append(entry)
        access.log_event("登记", actor=principal, dataset_id=int(entry["id"]), detail="登记数据集定义并成为所有者")
        return entry, []

    def run_action(
        self, entry_id: int, action: str, principal: Principal
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"数据集定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据集管理可执行范围"
        access.ensure_dataset_access_fields(entry)
        if entry.get("owner_id") is None:
            raise DomainError(
                403,
                "该样例数据集没有所有者，必须先由维护者认领，不能默认开放修改",
            )
        if not access.can_manage(entry, principal):
            raise DomainError(403, "只有数据集所有者或平台管理员可以变更状态")
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        access.log_event(
            "状态变更",
            actor=principal,
            dataset_id=entry_id,
            detail=f"{action} → {target}",
        )
        return access.mask_dataset_row(entry, principal), f"数据集定义已{action}"

    def export_entries(self, principal: Principal) -> dict[str, Any]:
        items, total = self.list_entries(principal, page=1, size=10000)
        return {"module": MODULE, "total": total, "items": items}


def access_admin_or_maintainer(principal: Principal) -> None:
    if not principal.authenticated:
        raise DomainError(401, "登记数据集定义需要先建立身份")
    if not (principal.is_maintainer or principal.is_platform_admin):
        raise DomainError(403, "只有维护者或平台管理员可以登记数据集定义")


service = DatastoreService()
