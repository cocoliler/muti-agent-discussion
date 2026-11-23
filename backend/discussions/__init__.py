"""成员 3 · 后端 API 与持久化。

分层导入，互不牵连：

* ``codec``      — 引擎 dataclass 与 JSON 的双向转换
* ``repository`` — SQLite 事务、seq 镜像、CAS、幂等
* ``application``— 协调服务，把引擎决策接到仓储上
* ``routes``     — FastAPI 路由（需显式 ``install(app)`` 才生效）
"""

from .codec import CodecError, decode, dumps, encode, loads
from .errors import ApiError, decision_status, error_body
from .repository import DiscussionRepository, NotFound, VersionConflict

__all__ = [
    "ApiError",
    "CodecError",
    "DiscussionRepository",
    "NotFound",
    "VersionConflict",
    "decode",
    "decision_status",
    "dumps",
    "encode",
    "error_body",
    "loads",
]
