-- 成员 3 · 讨论工作台持久化结构（SQLite）
-- 依据：FLOWSTUDIO_DESIGN_INPUT.md §5、实施方案 §4
-- 所有语句幂等，可在进程启动时重复执行。

CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

-- 1) 会话主表：整包状态为唯一事实源，其余列为查询冗余
CREATE TABLE IF NOT EXISTS discussion_sessions (
  id               TEXT PRIMARY KEY,
  project_id       TEXT NOT NULL,
  status           TEXT NOT NULL,
  input_version    INTEGER NOT NULL DEFAULT 1,
  round_no         INTEGER NOT NULL DEFAULT 0,
  last_seq         INTEGER NOT NULL DEFAULT 0,
  active_draft_id  TEXT,
  config_snapshot  TEXT NOT NULL,
  session_state    TEXT NOT NULL,
  codec_version    INTEGER NOT NULL DEFAULT 1,
  state_version    INTEGER NOT NULL DEFAULT 1,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  deleted_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_disc_sessions_recent
  ON discussion_sessions(updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_disc_sessions_project
  ON discussion_sessions(project_id, created_at DESC);

-- 2) 消息投影表：after_seq 增量分页 + 引用校验 + 历史查看
CREATE TABLE IF NOT EXISTS discussion_messages (
  session_id    TEXT NOT NULL REFERENCES discussion_sessions(id),
  seq           INTEGER NOT NULL,
  role          TEXT NOT NULL,
  kind          TEXT NOT NULL,
  text          TEXT NOT NULL,
  topic_id      TEXT,
  task_id       TEXT,
  reply_to_seq  INTEGER,
  source_seqs   TEXT NOT NULL DEFAULT '[]',
  item_refs     TEXT NOT NULL DEFAULT '[]',
  issue_ids     TEXT NOT NULL DEFAULT '[]',
  input_version INTEGER NOT NULL,
  round_no      INTEGER NOT NULL,
  analysis      TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL,
  PRIMARY KEY (session_id, seq)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_disc_messages_kind
  ON discussion_messages(session_id, kind, seq);

-- 3) 调用尝试：同一逻辑任务只合并一份有效结果
CREATE TABLE IF NOT EXISTS role_attempts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id      TEXT NOT NULL REFERENCES discussion_sessions(id),
  logical_task_id TEXT NOT NULL,
  attempt_no      INTEGER NOT NULL DEFAULT 1,
  role            TEXT NOT NULL,
  task_kind       TEXT NOT NULL,
  snapshot_ref    TEXT NOT NULL,
  input_version   INTEGER NOT NULL,
  status          TEXT NOT NULL,
  usage           TEXT,
  error           TEXT,
  output          TEXT,
  started_at      TEXT,
  finished_at     TEXT,
  elapsed_seconds REAL,
  UNIQUE (session_id, logical_task_id, attempt_no)
);

CREATE INDEX IF NOT EXISTS idx_role_attempts_task
  ON role_attempts(session_id, logical_task_id, attempt_no);

-- 4) 草稿版本
CREATE TABLE IF NOT EXISTS draft_revisions (
  id             TEXT PRIMARY KEY,
  session_id     TEXT NOT NULL REFERENCES discussion_sessions(id),
  parent_id      TEXT REFERENCES draft_revisions(id),
  version        INTEGER NOT NULL,
  input_version  INTEGER NOT NULL,
  content        TEXT NOT NULL,
  source_map     TEXT NOT NULL DEFAULT '{}',
  open_issue_ids TEXT NOT NULL DEFAULT '[]',
  is_current     INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  UNIQUE (session_id, version)
);

-- 5) 评审结果
CREATE TABLE IF NOT EXISTS review_results (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL,
  draft_id      TEXT NOT NULL REFERENCES draft_revisions(id),
  input_version INTEGER NOT NULL,
  verdict       TEXT NOT NULL,
  findings      TEXT NOT NULL DEFAULT '[]',
  created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_results_draft
  ON review_results(draft_id, created_at);

-- 6) 签收与交接
CREATE TABLE IF NOT EXISTS spec_handoffs (
  session_id       TEXT PRIMARY KEY REFERENCES discussion_sessions(id),
  draft_id         TEXT NOT NULL,
  review_id        TEXT NOT NULL,
  input_version    INTEGER NOT NULL,
  confirmed_at     TEXT NOT NULL,
  handoff_status   TEXT NOT NULL DEFAULT 'confirmed',
  spec_revision_id TEXT,
  -- §6.2：首次保存 expected_revision=0，后续用读取到的目标版本
  spec_expected_revision INTEGER NOT NULL DEFAULT 0,
  -- 签收产物：spec_handoff.sign_off 生成的 DesignSpecContent 正文与规范化哈希
  content_hash     TEXT,
  content_json     TEXT,
  idempotency_key  TEXT
);

-- 7) 幂等键：scope = HTTP 方法 + 资源路径
CREATE TABLE IF NOT EXISTS idempotency_keys (
  scope         TEXT NOT NULL,
  key           TEXT NOT NULL,
  request_hash  TEXT NOT NULL,
  status_code   INTEGER,
  response_body TEXT,
  created_at    TEXT NOT NULL,
  PRIMARY KEY (scope, key)
);

-- 8) 动作审计
CREATE TABLE IF NOT EXISTS discussion_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id    TEXT NOT NULL,
  action        TEXT NOT NULL,
  payload       TEXT,
  decision_code TEXT NOT NULL,
  request_id    TEXT,
  created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_disc_events_session
  ON discussion_events(session_id, id DESC);

CREATE TABLE IF NOT EXISTS discussion_milestones (
  session_id         TEXT NOT NULL REFERENCES discussion_sessions(id),
  stage              TEXT NOT NULL,
  subject            TEXT NOT NULL,
  input_version      INTEGER NOT NULL,
  round_no           INTEGER NOT NULL,
  attempt_high_water INTEGER NOT NULL,
  created_at         TEXT NOT NULL,
  PRIMARY KEY (session_id, stage, subject)
) WITHOUT ROWID;

-- 9) 持久化执行请求及会话级租约
CREATE TABLE IF NOT EXISTS discussion_runs (
  session_id TEXT PRIMARY KEY REFERENCES discussion_sessions(id),
  mode       TEXT NOT NULL,
  generation INTEGER NOT NULL DEFAULT 1,
  owner      TEXT,
  expires_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_disc_runs_available ON discussion_runs(mode, expires_at);

-- Immutable copies of signed drafts. spec_handoffs remains the latest receipt.
CREATE TABLE IF NOT EXISTS spec_handoff_versions (
  session_id    TEXT NOT NULL REFERENCES discussion_sessions(id),
  draft_id      TEXT NOT NULL,
  review_id     TEXT NOT NULL,
  input_version INTEGER NOT NULL,
  confirmed_at  TEXT NOT NULL,
  content_hash  TEXT,
  content_json  TEXT,
  PRIMARY KEY (session_id, draft_id)
) WITHOUT ROWID;
