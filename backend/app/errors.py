"""可读的领域错误：服务层抛状态码与说明，路由层统一转成 JSON。"""
from __future__ import annotations


class DomainError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
