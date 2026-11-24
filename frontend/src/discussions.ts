export type Message = {
  seq: number;
  role: string;
  kind: string;
  text: string;
  reply_to_seq: number | null;
  source_seqs: number[];
  item_refs: string[];
  issue_ids: string[];
  task_id?: string | null;
};

export type Requirement = {
  id: string;
  text: string;
  priority?: string;
  acceptance_criteria?: string[];
  source_seqs?: number[];
};

export type DraftVersion = {
  draft_id: string;
  version: number;
  input_version: number;
  open_issue_ids: string[];
};

export type Draft = DraftVersion & {
  review_verdict: string | null;
  content: { product_name?: string; requirements?: Requirement[]; [key: string]: unknown };
};

export type Finding = { code?: string; path?: string; message?: string; finding_id?: string;
  target_item?: string; summary?: string; severity?: string; close_condition?: string; responsible_role?: string };

export type Session = {
  id: string;
  project_id: string;
  status: string;
  input_version: number;
  state_version: number;
  round_no: number;
  round_kind: string | null;
  phase: string | null;
  topic_id: string | null;
  waiting_for: string;
  last_seq: number;
  participants: { role: string; status: string; attempt_no: number | null; error_code?: string | null }[];
  pending_tasks?: Task[];
  agent_mode?: 'simulation' | 'real' | 'off';
  active_draft: Draft | null;
  drafts: (DraftVersion & { current: boolean })[];
  reviews: { review_id: string; draft_id: string; verdict: string; findings: Finding[] }[];
  open_issues: { issue_id: string; summary: string; severity: string; status: string; source_seqs: number[] }[];
  budget: Record<string, number>;
};

export type Task = {
  task_id: string;
  session_id: string;
  role: string;
  kind: string;
  input_version: number;
  snapshot_ref: string;
  round_no: number;
  topic_id: string;
  phase?: string;
  message_seqs?: number[];
  issue_ids?: string[];
};

export type Action = 'pause' | 'resume' | 'cancel' | 'extend_quota' | 'retry' | 'run_agents' | 'abandon_task' |
  'require_changes' | 'resolve_issues' | 'submit_output' | 'submit_review' | 'submit_agenda' | 'propose';

export type Handoff = { draft_id: string; confirmed_at: string; content_hash: string | null; export_url: string | null };

export type Decision = {
  decision: { ok: boolean; code: string; message: string };
  session: Session | null;
  tasks?: Task[];
  handoff?: Handoff | null;
  result?: { findings?: Finding[] };
};

type MessagePage = { items: Message[]; has_more: boolean; next_cursor: string | null };

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) { super(message); }
}

export const discussionPath = (id: string) => `/discussions/${encodeURIComponent(id)}`;

export async function api<T>(path: string, body?: object, key?: string, signal?: AbortSignal): Promise<T> {
  const timeout = AbortSignal.timeout(10000);
  const response = await fetch(`/api/v1${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json', 'Idempotency-Key': key! },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  const result = response.headers.get('content-type')?.includes('application/json') ? await response.json() : null;
  if (!response.ok) {
    const details = result?.error?.issues ?? result?.detail;
    const issues = Array.isArray(details)
      ? details.map((issue) => `${issue.path ?? issue.loc?.join('.') ?? ''} ${issue.message ?? issue.msg ?? ''}`.trim()).filter(Boolean)
      : [];
    const message = result?.error?.message ?? (typeof result?.detail === 'string' ? result.detail : `请求失败（HTTP ${response.status}）`);
    throw new ApiError([message, ...issues].join('。'), response.status, result?.error?.code);
  }
  if (result === null) throw new Error('讨论服务返回了无效响应。');
  return result as T;
}

export async function readMessages(id: string, after: number, signal: AbortSignal): Promise<Message[]> {
  const messages: Message[] = [];
  let cursor = after;
  while (true) {
    const page = await api<MessagePage>(`${discussionPath(id)}/messages?after_seq=${cursor}&limit=100`, undefined, undefined, signal);
    messages.push(...page.items);
    if (!page.has_more) return messages;
    const next = Number(page.next_cursor);
    if (!Number.isInteger(next) || next <= cursor) throw new Error('讨论服务返回了无效消息游标。');
    cursor = next;
  }
}
