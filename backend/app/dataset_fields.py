"""数据集字段解析：字段列表是逗号分隔的文本，也允许数组形式。

字段是授权的最小单位，解析结果会在每次请求时重新从数据仓库读取，
避免把某次快照当成长期有效的权限。
"""
from __future__ import annotations

import re
from typing import Any

_SEPARATORS = re.compile(r"[,，、;；\n|]+")


def split_columns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = _SEPARATORS.split(str(value))
    columns: list[str] = []
    for item in raw:
        name = item.strip()
        if name and name not in columns:
            columns.append(name)
    return columns


def dataset_columns(dataset: dict[str, Any]) -> list[str]:
    """数据集声明的全部字段；没有字段列表的记录视为没有可授权字段。"""
    return split_columns(dataset.get("字段列表"))


def public_columns(dataset: dict[str, Any]) -> list[str]:
    """允许公开浏览的字段，必须是数据集字段的子集。"""
    declared = dataset_columns(dataset)
    candidates = split_columns(dataset.get("公开字段"))
    return [column for column in candidates if column in declared]

