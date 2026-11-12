"""演示用身份解析。

身份建立方式留给实现方：当前版本用 ``X-Actor-Id`` 请求头映射到内置账号，
未提供或无法识别的身份一律按“公开浏览者”处理。生产环境应替换为经过校验
的登录票据，不能信任客户端自报的归属。

账号只用来区分“谁在操作”，数据集归属始终从服务端数据仓库读取，客户端
不能通过自报角色获得所有者能力。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Header

ANONYMOUS_ID = "anonymous"


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    roles: tuple[str, ...]
    kind: str = "person"

    @property
    def authenticated(self) -> bool:
        return self.id != ANONYMOUS_ID

    @property
    def is_platform_admin(self) -> bool:
        return "platform_admin" in self.roles

    @property
    def is_maintainer(self) -> bool:
        return "maintainer" in self.roles

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "roles": list(self.roles),
            "authenticated": self.authenticated,
            "kind": self.kind,
        }


ANONYMOUS = Principal(
    id=ANONYMOUS_ID,
    name="公开浏览者",
    roles=("public",),
    kind="public",
)
SYSTEM = Principal(
    id="system",
    name="系统",
    roles=("system",),
    kind="system",
)

DEMO_PRINCIPALS: dict[str, Principal] = {
    "platform-admin": Principal(
        id="platform-admin",
        name="平台管理员",
        roles=("authenticated", "platform_admin"),
    ),
    "owner-alpha": Principal(
        id="owner-alpha",
        name="数据集维护者 Alpha",
        roles=("authenticated", "maintainer"),
    ),
    "owner-beta": Principal(
        id="owner-beta",
        name="数据集维护者 Beta",
        roles=("authenticated", "maintainer"),
    ),
    "borrower-gamma": Principal(
        id="borrower-gamma",
        name="使用方 Gamma",
        roles=("authenticated", "consumer"),
    ),
}


def resolve_principal(actor_id: str | None) -> Principal:
    """只认内置账号；未知身份降级为公开浏览者，不获得任何写权限。"""
    if not actor_id or actor_id.strip() == ANONYMOUS_ID:
        return ANONYMOUS
    return DEMO_PRINCIPALS.get(actor_id.strip(), ANONYMOUS)


def current_principal(
    x_actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
) -> Principal:
    return resolve_principal(x_actor_id)


def demo_principals() -> list[dict[str, object]]:
    return [principal.as_dict() for principal in DEMO_PRINCIPALS.values()]
