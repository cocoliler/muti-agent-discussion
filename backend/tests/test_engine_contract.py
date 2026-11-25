"""引擎契约测试（T3/T4）：需要先克隆仓库，本地存在 ``discussion_orchestration`` 包。

它验证三件事：

1. 编解码对真实 ``DiscussionSession`` 深度相等（含 tuple/set/枚举键）；
2. ``seq`` 确实由引擎生成、仓储只做镜像；
3. 存库 → 换新连接读回 → 继续推进成功（重启续跑）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
for path in (BACKEND, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

try:  # pragma: no cover - 仓库内成立
    import discussion_orchestration as orch

    ENGINE_OK = True
except ImportError:  # pragma: no cover
    orch = None
    ENGINE_OK = False

from discussions import codec  # noqa: E402
from discussions.repository import DiscussionRepository  # noqa: E402

SKIP_REASON = "未找到 discussion_orchestration 包：请在仓库根目录运行 python -m unittest"


@unittest.skipUnless(ENGINE_OK, SKIP_REASON)
class EngineContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = orch.DiscussionEngine()
        self.session = self.engine.create_session(
            session_id="s-engine-1",
            project_id="p-engine-1",
            idea="做一个内部会议室预约工具，首版先解决查空闲和预约。",
        )

    def test_roundtrip_real_session(self) -> None:
        restored = codec.loads(codec.dumps(self.session), orch.DiscussionSession)
        self.assertEqual(restored, self.session)
        self.assertIsInstance(restored.messages[0].source_seqs, tuple)
        self.assertIsInstance(restored.challenged_topics, set)

    def test_seq_is_generated_by_engine(self) -> None:
        self.assertEqual(self.session.last_seq, 1)
        self.assertEqual(self.session.next_seq(), 2)
        agenda = orch.Agenda(
            summary="明确首版范围与并发冲突处理",
            items=(orch.AgendaItem(topic_id="T1", title="预约与冲突", question="如何避免重复预约？"),),
        )
        decision = self.engine.submit_agenda(self.session, agenda)
        self.assertTrue(decision.ok, getattr(decision, "code", ""))
        self.assertGreater(self.session.last_seq, 1)

    def test_state_survives_restart_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "engine.sqlite3"
            repo = DiscussionRepository(db)
            repo.init_schema()
            repo.create_session(self.session)

            reopened = DiscussionRepository(db)
            restored, meta = reopened.load_state(self.session.session_id)
            self.assertEqual(restored, self.session)
            self.assertEqual(meta["last_seq"], 1)

            agenda = orch.Agenda(
                summary="明确首版范围与并发冲突处理",
                items=(orch.AgendaItem(topic_id="T1", title="预约与冲突", question="如何避免重复预约？"),),
            )
            decision = self.engine.submit_agenda(restored, agenda)
            self.assertTrue(decision.ok, getattr(decision, "code", ""))
            self.assertTrue(decision.tasks)

            meta2 = reopened.save_after_decision(restored, meta["state_version"])
            self.assertEqual(meta2["state_version"], 2)
            items, _ = reopened.list_messages_after(restored.session_id, 0, 50)
            self.assertEqual([item["seq"] for item in items], list(range(1, restored.last_seq + 1)))

    def test_reload_does_not_lose_naming_state(self) -> None:
        restored = codec.loads(codec.dumps(self.session), orch.DiscussionSession)
        self.assertEqual(restored.named_responses, self.session.named_responses)
        self.assertEqual(restored.dispatch_units, self.session.dispatch_units)


if __name__ == "__main__":
    unittest.main(verbosity=2)
