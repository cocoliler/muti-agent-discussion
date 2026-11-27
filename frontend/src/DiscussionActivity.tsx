import { useEffect, useMemo, useState } from 'react';
import { api, discussionPath } from './discussions';
import type { ActivityAttempt, ActivityPage, Session } from './discussions';
import type { DiscussionEvent, DisplayMode } from './discussionTimeline';

const attentionStates = new Set(['failed', 'timed_out', 'abandoned']);
const activeStates = new Set(['running', 'queued', 'pending']);

function ToolCallRow({ event, labels, result, locate }: {
  event: Extract<DiscussionEvent, { type: 'ToolCall' }>;
  labels: { status: Record<string, string>; task: Record<string, string> };
  result?: number;
  locate: (seq: number) => void;
}) {
  const { attempt } = event;
  const status = attempt.status.toLowerCase();
  return <li className="activity-attempt">
    <span>{labels.task[attempt.task_kind] ?? attempt.task_kind} · 第 {attempt.attempt_no} 次执行</span>
    <strong data-activity-status={status}>{labels.status[status] ?? status}</strong>
    {attempt.elapsed_seconds !== null && <span>{attempt.elapsed_seconds.toFixed(1)} 秒</span>}
    {attempt.started_at && <time dateTime={attempt.started_at}>{new Date(attempt.started_at).toLocaleString()}</time>}
    {attempt.error_code && <code>{attempt.error_code}</code>}
    {status === 'succeeded' && result !== undefined && <button type="button" onClick={() => locate(result)}>查看结果 #{result}</button>}
  </li>;
}

function groupAttempts(attempts: ActivityAttempt[]) {
  const groups: ActivityAttempt[][] = [];
  for (const attempt of attempts) {
    const latest = groups.at(-1);
    if (latest?.[0].role === attempt.role) latest.push(attempt);
    else groups.push([attempt]);
  }
  return groups;
}

export function DiscussionActivity({ session, mode, revision, labels, openRole, results, locate }: {
  session: Session;
  mode: DisplayMode;
  revision: number;
  labels: { role: Record<string, string>; status: Record<string, string>; task: Record<string, string> };
  openRole: (role: string) => void;
  results: ReadonlyMap<string, number>;
  locate: (seq: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [latest, setLatest] = useState<ActivityPage | null>(null);
  const [older, setOlder] = useState<ActivityPage | null>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [historyError, setHistoryError] = useState(false);
  const readsActivity = expanded || mode !== 'compact';
  const current = session.participants.filter((person) => person.role !== 'user');
  const roles = mode === 'compact' ? current.filter((person) => activeStates.has(person.status) || attentionStates.has(person.status)) : current;
  const running = current.filter((person) => person.status === 'running').length;
  const failures = current.filter((person) => attentionStates.has(person.status)).length;
  const status = [
    session.status === 'awaiting_confirmation' ? '等待确认' : session.status === 'clarifying' && session.waiting_for === 'user_input' ? '等待补充' : '',
    running > 0 ? `${running} 个 Agent 执行中` : '', failures > 0 ? `${failures} 个 Agent 执行异常` : '',
  ].filter(Boolean).join(' · ');
  const groups = useMemo(() => {
    const items = new Map((older?.items ?? []).map((attempt) => [attempt.id, attempt]));
    for (const attempt of latest?.items ?? []) items.set(attempt.id, attempt);
    return groupAttempts([...items.values()].sort((a, b) => b.id - a.id));
  }, [latest, older]);

  useEffect(() => {
    if (!readsActivity) return;
    const controller = new AbortController();
    void api<ActivityPage>(`${discussionPath(session.id)}/activity`, undefined, undefined, controller.signal)
      .then((page) => { if (!controller.signal.aborted) { setLatest(page); setError(''); } })
      .catch(() => { if (!controller.signal.aborted) setError('暂时无法读取执行记录。'); });
    return () => controller.abort();
  }, [readsActivity, revision, session.id, retry]);

  useEffect(() => {
    if (before === null) return;
    const controller = new AbortController();
    void api<ActivityPage>(`${discussionPath(session.id)}/activity?before=${before}`, undefined, undefined, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setOlder((previous) => ({ ...page, items: [...(previous?.items ?? []), ...page.items] }));
        setBefore(null);
      }).catch(() => { if (!controller.signal.aborted) { setHistoryError(true); setBefore(null); } });
    return () => controller.abort();
  }, [before, session.id]);

  const next = older ? older.next_cursor : latest?.next_cursor;
  const lastAttempt = latest?.items[0];
  return <section className="discussion-activity" aria-label="Agent 执行活动">
    <div className="activity-heading">
      <strong>执行活动</strong>
      <span role="status">{status}</span>
      {session.agent_mode === 'simulation' && <span className="activity-mode">模拟模式</span>}
    </div>
    {!!roles.length && <ul className="activity-roles">{roles.map((person) => <li key={person.role}>
      <button type="button" onClick={() => openRole(person.role)}>
        <span>{labels.role[person.role] ?? person.role}</span>
        <strong data-activity-status={person.status}>{labels.status[person.status] ?? person.status}</strong>
      </button>
    </li>)}</ul>}
    {mode === 'standard' && !expanded && !error && lastAttempt && <ul className="activity-preview" aria-label="最近一次执行">
      <ToolCallRow event={{ type: 'ToolCall', attempt: lastAttempt }} labels={labels}
        result={results.get(lastAttempt.logical_task_id)} locate={locate} />
    </ul>}
    <details open={mode === 'detailed' || undefined} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>查看执行记录</summary>
      {error ? <p role="alert">{error} <button type="button" onClick={() => setRetry((value) => value + 1)}>重试</button></p>
        : !latest ? <p role="status">正在读取执行记录…</p>
          : !groups.length ? <p>还没有调用记录。</p>
            : <div className="activity-history">{groups.map((group) => <section key={group.at(-1)!.id}>
              <h3>{labels.role[group[0].role] ?? group[0].role}</h3>
              <ul>{group.map((attempt) => <ToolCallRow key={attempt.id} event={{ type: 'ToolCall', attempt }} labels={labels}
                result={results.get(attempt.logical_task_id)} locate={locate} />)}</ul>
            </section>)}</div>}
      {historyError && <p role="alert">暂时无法读取更早的记录。</p>}
      {next != null && <button type="button" disabled={before !== null} onClick={() => { setHistoryError(false); setBefore(next); }}>查看更早记录</button>}
    </details>
  </section>;
}
