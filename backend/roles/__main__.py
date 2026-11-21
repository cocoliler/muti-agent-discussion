"""在仓库根运行 python -m backend.roles --help。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from pydantic import ValidationError

from .contracts import RoleRequest
from .provider import DeepSeekProvider
from .runner import RoleRunner
from .simulation import SimulationProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="执行一项需求讨论角色任务，输出 JSON 结果与尝试明细")
    parser.add_argument("request", type=Path, help="UTF-8 角色任务快照 JSON")
    parser.add_argument("--mode", choices=("simulation", "real"), default="simulation")
    parser.add_argument("--call-limit", type=int, default=2, help="本次调用可用的传输尝试数")
    parser.add_argument("--timeout", type=float, default=45, help="每次尝试的超时秒数")
    args = parser.parse_args()
    try:
        request = RoleRequest.model_validate_json(args.request.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        parser.exit(2, "无法读取 UTF-8 任务文件\n")
    except ValidationError as exc:
        paths = ["/".join(map(str, e["loc"])) or "/" for e in exc.errors(include_input=False)]
        parser.exit(2, "任务快照不符合契约 " + ", ".join(paths) + "\n")

    if args.mode == "simulation":
        provider = SimulationProvider()
    else:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        model = os.environ.get("DISCUSSION_MODEL", "").strip()
        if not key or not model:
            parser.exit(2, "REAL 模式需要 DEEPSEEK_API_KEY 和 DISCUSSION_MODEL\n")
        try:
            provider = DeepSeekProvider(api_key=key, model=model)
        except ValueError as exc:
            parser.exit(2, str(exc) + "\n")
    try:
        runner = RoleRunner(request.session_id, provider, mode=args.mode,
                            call_limit=args.call_limit, timeout_seconds=args.timeout)
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")
    result = asyncio.run(runner.run(request))
    sys.stdout.buffer.write((json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
