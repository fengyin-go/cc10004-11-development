"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import copy
from typing import Any

from app.seed import (
    BORROW_SEED_ROWS,
    DATASTORE_COLUMN_SEED,
    QUERY_SEED_META,
    SEED_ROWS,
)

# 借阅申请、授权决定、审计事件是跨模块的基础设施表，不当作业务模块展示。
INTERNAL_TABLES = frozenset({
    "borrow_requests",
    "access_decisions",
    "access_events",
    "datastore_meta",
})


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self.reset()

    def reset(self) -> None:
        """把仓库恢复到示例数据状态；测试和本地重置都走这里。"""
        self._tables = {
            name: copy.deepcopy(rows) for name, rows in SEED_ROWS.items()
        }
        for name, rows in BORROW_SEED_ROWS.items():
            self._tables[name] = copy.deepcopy(rows)
        self._apply_seed_meta()

    def _apply_seed_meta(self) -> None:
        """把治理字段合并进样例业务数据，集中在一处便于阅读。"""
        for row in self._tables.get("datastore", []):
            entry_id = int(row.get("id", 0))
            if entry_id in DATASTORE_COLUMN_SEED:
                row["字段列表"] = DATASTORE_COLUMN_SEED[entry_id]
        for meta in self._tables.get("datastore_meta", []):
            row = self.find("datastore", int(meta["id"]))
            if row is not None:
                for key, value in meta.items():
                    if key != "id":
                        row[key] = copy.deepcopy(value)
        for row in self._tables.get("query", []):
            meta = QUERY_SEED_META.get(int(row.get("id", 0)))
            if meta:
                row.update(copy.deepcopy(meta))

    def module_names(self) -> list[str]:
        return sorted(name for name in self._tables if name not in INTERNAL_TABLES)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
