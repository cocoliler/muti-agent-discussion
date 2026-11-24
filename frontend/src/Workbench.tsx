import { useEffect, useRef, useState } from 'react';
import { ApiError, api, discussionPath, readMessages } from './discussions';
import type { Action, Decision, Draft, Finding, Handoff, Message, Session, Task } from './discussions';
import { DiscussionSpec } from './DiscussionSpec';
import './workbench.css';

const statusLabels: Record<string, string> = {
  clarifying: '澄清需求', discussing: '讨论中', reviewing: '评审中', awaiting_confirmation: '待签收',
  completed: '已签收', paused: '已暂停', canceled: '已取消',
};
const waitingLabels: Record<string, string> = {
  host_agenda: '等待主持人提交议题', host_decision: '等待主持人调度', role_results: '等待角色结果',
  user_input: '等待你补充信息或裁决问题', review: '等待独立评审', none: '当前没有待处理的调度',
};
const phaseLabels: Record<string, string> = {
  statements: '专业角色发言', challenge: '质疑', responses: '定向回应', integration: '整合', drafting: '成稿',
};
const agentPhaseLabels: Record<string, string> = {
  statements: '专业角色正在并行发言', challenge: '质疑者正在审视本轮发言', responses: '专业角色正在回应问题',
  integration: '记录员正在整合讨论', drafting: '记录员正在撰写规格',
};
const roleLabels: Record<string, string> = {
  user: '你', host: '主持人', product: '产品经理', research: '用户研究员', architect: '技术架构师',
  designer: '交互设计师', business: '商业分析', challenger: '质疑者', recorder: '记录员', reviewer: '独立评审',
};
const roleColors: Record<string, string> = {
  host: '#a99bff', product: '#f4ae76', research: '#75cbb8', architect: '#8caeff', designer: '#ec99c2',
  business: '#dbc38a', challenger: '#ff9e9f', recorder: '#80ccfa', reviewer: '#a9d894',
};
const participantLabels: Record<string, string> = {
  queued: '待执行', running: '进行中', done: '已发言', succeeded: '已完成', failed: '失败',
  pending: '待执行', ignored: '已忽略', abandoned: '已放弃', timed_out: '已超时',
};
const kindLabels: Record<string, string> = {
  user_input: '用户输入', agenda: '议题', clarification: '澄清', statement: '发言', challenge: '质疑',
  response: '回应', integration: '整合', draft: '草稿', review: '评审', decision: '决定', system: '系统',
};
const taskLabels = { ...kindLabels, statement: '专业角色发言', integration: '整合讨论结果', draft: '撰写规格草稿' } as Record<string, string>;
const budgets = [['rounds', '总轮次'], ['directed', '定向轮次'], ['revisions', '修订轮次'], ['dispatch_units', '派发单元']];
const storageKey = 'design-room.discussion';
const projectKey = 'design-room.project';
const defaultActions = [
  ['submit_agenda', '提交主持人议题'], ['propose', '提交主持人调度'],
  ['submit_output', '提交角色结果'], ['submit_review', '提交评审结论'], ['abandon_task', '放弃角色任务'],
] as const;

function Avatar({ role }: { role: string }) {
  const color = roleColors[role] ?? '#9aa5c2';
  return <span className="avatar" aria-hidden="true" style={{ color, backgroundColor: color + '22' }}>{(roleLabels[role] ?? role)[0]}</span>;
}

function readStorage(key: string) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function saveStorage(key: string, value: string | null) {
  try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key); }
  catch { /* The current session remains usable when storage is disabled. */ }
}

function describeError(error: unknown) {
  return error instanceof Error ? error.message : '无法连接讨论服务。';
}

export function Workbench({ openDemo }: { openDemo: () => void }) {
  const [id, setId] = useState(() => readStorage(storageKey));
  const [projectId, setProjectId] = useState(() => readStorage(projectKey) ?? crypto.randomUUID());
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [snapshots, setSnapshots] = useState<Record<string, Draft>>({});
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskSelection, setTaskSelection] = useState<{ role: string; task?: Task } | null>(null);
  const taskDialog = useRef<HTMLDialogElement>(null);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [idea, setIdea] = useState('');
  const [text, setText] = useState('');
  const [reply, setReply] = useState<number | null>(null);
  const [substantive, setSubstantive] = useState(true);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [itemRef, setItemRef] = useState<{ value: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [readError, setReadError] = useState('');
  const [findings, setFindings] = useState<Finding[]>([]);
  const [action, setAction] = useState<Action | ''>('');
  const [payload, setPayload] = useState('');
  const [selectedIssues, setSelectedIssues] = useState<string[]>([]);
  const [quota, setQuota] = useState({ rounds: 0, directed: 0, revisions: 0 });
  const [note, setNote] = useState('');
  const [agendaDraft, setAgendaDraft] = useState<{ version: number; text: string } | null>(null);
  const thread = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const projectIdeaField = useRef<HTMLTextAreaElement>(null);
  const sessionRead = useRef<AbortController | null>(null);
  const shouldScroll = useRef(true);
  const refresh = useRef<() => Promise<void>>(async () => {});
  const pending = useRef<{ signature: string; key: string } | null>(null);

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    sessionRead.current = controller;
    let cursor = 0;
    let inflight: Promise<void> | null = null;
    function sync() {
      if (controller.signal.aborted) return Promise.resolve();
      inflight ??= (async () => {
        try {
          const next = await api<Session>(discussionPath(id!), undefined, undefined, controller.signal);
          const batch = next.last_seq > cursor ? await readMessages(id!, cursor, controller.signal) : [];
          if (controller.signal.aborted) return;
          setSession((current) => current?.id === next.id && current.state_version > next.state_version ? current : next);
          if (batch.length) {
            cursor = batch[batch.length - 1].seq;
            setMessages((current) => [...current, ...batch]);
          }
          setReadError('');
        } catch (cause) {
          if (controller.signal.aborted) return;
          if (cause instanceof ApiError && cause.status === 404) {
            saveStorage(storageKey, null);
            setId(null);
            setSession(null);
            setMessages([]);
            setTaskSelection(null);
          }
          setReadError(describeError(cause));
        }
      })().finally(() => { inflight = null; });
      return inflight;
    }
    refresh.current = sync;
    void sync();
    const timer = window.setInterval(() => void sync(), 2500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [id]);

  useEffect(() => {
    const draft = session?.active_draft;
    if (draft) setSnapshots((current) => ({ ...current, [draft.draft_id]: draft }));
  }, [session?.active_draft]);

  useEffect(() => {
    if (thread.current && shouldScroll.current) thread.current.scrollTop = thread.current.scrollHeight;
  }, [messages.length]);

  useEffect(() => {
    if (taskSelection && !taskDialog.current?.open) taskDialog.current?.showModal();
  }, [taskSelection]);

  function clearSession() {
    sessionRead.current?.abort();
    saveStorage(storageKey, null);
    setId(null);
    setSession(null);
    setMessages([]);
    setSnapshots({});
    setTasks([]);
    setTaskSelection(null);
    setHandoff(null);
    setError('');
    setReadError('');
    setFindings([]);
    setIdea('');
    setText('');
    setReply(null);
    setAction('');
    setPayload('');
    setSelectedIssues([]);
    setNote('');
    setAgendaDraft(null);
    setQuota({ rounds: 0, directed: 0, revisions: 0 });
    setSubstantive(true);
    setHighlight(null);
    setItemRef(null);
    shouldScroll.current = true;
    pending.current = null;
    requestAnimationFrame(() => projectIdeaField.current?.focus());
  }

  async function write<T>(path: string, body: object, onResult: (result: T) => void) {
    setBusy(true);
    setError('');
    setFindings([]);
    const signature = JSON.stringify([path, body]);
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() };
    try {
      const result = await api<T>(path, body, pending.current.key);
      pending.current = null;
      onResult(result);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status < 500) pending.current = null;
      if (cause instanceof ApiError && (cause.code === 'REVISION_CONFLICT' || cause.status === 409)) await refresh.current();
      setError(describeError(cause));
    } finally { setBusy(false); }
  }

  function create() {
    const project = projectId.trim();
    void write<Session>(`/projects/${encodeURIComponent(project)}/discussions`, { idea: idea.trim() }, (next) => {
      setSession(next);
      setId(next.id);
      setIdea('');
      setReadError('');
      saveStorage(storageKey, next.id);
      saveStorage(projectKey, next.project_id);
    });
  }

  function command(endpoint: string, body: object, success?: () => void) {
    if (!session || busy) return;
    void write<Decision>(discussionPath(session.id) + endpoint, { ...body, expected_state_version: session.state_version }, (result) => {
      if (result.session) {
        const next = result.session;
        setSession((current) => current && current.state_version > next.state_version ? current : next);
      }
      if (!result.decision.ok) {
        setError(result.decision.message || result.decision.code);
        setFindings(result.result?.findings ?? []);
        return;
      }
      if ('type' in body && body.type === 'retry') setTasks(result.tasks ?? []);
      else if (result.tasks?.length) setTasks(result.tasks);
      else if ('payload' in body && body.payload && typeof body.payload === 'object' && 'task_id' in body.payload) {
        const taskId = body.payload.task_id;
        setTasks((current) => current.filter((task) => task.task_id !== taskId));
      }
      if (result.handoff) setHandoff(result.handoff);
      success?.();
      void refresh.current();
    });
  }

  function act(type: Action, data: object = {}, success?: () => void) {
    command('/actions', { type, payload: data }, success);
  }

  function locate(seq: number) {
    setHighlight(seq);
    const target = document.getElementById(`message-${seq}`);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'nearest' });
  }

  function editAction(type: Action | '', task?: Task) {
    setAction(type);
    setPayload(type === 'submit_agenda'
      ? JSON.stringify({ summary: '', items: [{ topic_id: 'T1', title: '', question: '' }] }, null, 2)
      : type === 'submit_review' && session?.active_draft
        ? JSON.stringify({ review_id: crypto.randomUUID(), session_id: session.id, draft_id: session.active_draft.draft_id,
          input_version: session.input_version, verdict: 'pass', findings: [] }, null, 2)
        : task ? JSON.stringify({ ...task, text: '' }, null, 2)
          : type === 'propose' ? JSON.stringify({ action: 'submit_draft', reason: '' }, null, 2)
            : type === 'abandon_task' ? JSON.stringify({ task_id: '', reason: '' }, null, 2) : '{}');
  }

  const terminal = session?.status === 'completed' || session?.status === 'canceled';
  const paused = session?.status === 'paused';
  const currentTasks = session ? (session.pending_tasks ?? tasks).filter((task) => task.input_version === session.input_version && task.round_no === session.round_no) : [];
  const selectedTask = taskSelection?.task;
  const selectedPerson = session?.participants.find((person) => person.role === taskSelection?.role);
  const relatedMessages = taskSelection ? messages.filter((message) => selectedTask
    ? message.task_id === selectedTask.task_id || selectedTask.message_seqs?.includes(message.seq)
    : message.role === taskSelection.role).slice(-5) : [];
  const displayedIssues = session?.open_issues ?? [];
  const selected = displayedIssues.filter((issue) => selectedIssues.includes(issue.issue_id)).map((issue) => issue.issue_id);
  const activeDraft = session?.active_draft;
  const canConfirm = session?.status === 'awaiting_confirmation' && activeDraft?.review_verdict === 'pass'
    && activeDraft.input_version === session.input_version;
  const hostWorking = session?.participants.some((person) => person.role === 'host' && person.status === 'running');
  const waitingForAgenda = session?.waiting_for === 'host_agenda' && !paused;
  const agentsEnabled = session?.agent_mode === 'simulation' || session?.agent_mode === 'real';
  const autoExchange = agentsEnabled && (session?.waiting_for === 'role_results' || session?.waiting_for === 'review' || session?.waiting_for === 'host_decision');
  const isWorkingRole = (role: string) => waitingForAgenda ? role === 'host'
    : session?.waiting_for === 'review' ? role === 'reviewer' : currentTasks.some((task) => task.role === role);
  const agentRunning = hostWorking || session?.participants.some((person) => isWorkingRole(person.role) && person.status === 'running');
  const failedAgent = session?.participants.find((person) => person.status === 'failed' && (
    isWorkingRole(person.role)));
  const projectIdea = messages.find((message) => message.kind === 'user_input')?.text;
  const latestIdea = messages.filter((message) => message.kind === 'user_input').at(-1)?.text.trim() ?? '';
  const editingAgenda = agendaDraft !== null && agendaDraft.version === session?.input_version;
  const agendaText = editingAgenda ? agendaDraft.text : latestIdea;
  const startDiscussion = () => {
    const question = agendaText.trim();
    if (!question) return;
    act('submit_agenda', {
      summary: question,
      items: [{ topic_id: 'T1', title: question.split(/\r?\n/, 1)[0].slice(0, 80), question }],
    }, () => setAgendaDraft(null));
  };
  const nextStep = paused ? '讨论已暂停' : terminal ? statusLabels[session!.status]
    : agentsEnabled && (waitingForAgenda || autoExchange) && failedAgent && !agentRunning ? '有 Agent 执行失败'
      : waitingForAgenda ? (agentsEnabled ? '主持人正在整理议题' : '准备开始讨论')
        : autoExchange ? (session.waiting_for === 'review' ? '独立评审正在检查规格'
          : session.waiting_for === 'host_decision' ? '等待下一步调度' : agentPhaseLabels[session.phase ?? ''] ?? 'Agent 正在处理')
        : session?.status === 'awaiting_confirmation' ? '待审阅并签收'
          : session && waitingLabels[session.waiting_for];

  return <div className="shell workbench-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-icon" aria-hidden="true">✳</span><div><strong>Design Room</strong><small>多 Agent 需求工作台</small></div></div>
      <div className="top-actions"><button disabled={busy} onClick={openDemo}>会议室演示</button></div>
    </header>
    <div className="workspace live-workspace">
      <aside className="rail" aria-label="讨论状态和参与者">
        <h2>当前会话</h2>
        <div className="session-status"><strong>{projectIdea || (session ? '项目讨论' : '新讨论')}</strong>
          {session && <p>{statusLabels[session.status] ?? session.status} · 输入 v{session.input_version}{session.round_no > 0 && ` · 第 ${session.round_no} 轮`}</p>}</div>
        {session && <><h2>参与角色</h2><ul className="roles">{session.participants.map((person) => {
          const task = currentTasks.find((item) => item.role === person.role);
          const name = roleLabels[person.role] ?? person.role;
          return <li key={person.role}>
            <button className="role-task" aria-haspopup="dialog" aria-label={`查看${name}详情`} onClick={() => setTaskSelection({ role: person.role, task })}>
              <Avatar role={person.role} /><span>{name}<small><i data-status={person.status} />{participantLabels[person.status] ?? person.status}{person.attempt_no != null && ` · 尝试 ${person.attempt_no}`}</small></span>
              <span className="task-chevron" aria-hidden="true">›</span>
            </button>
          </li>;
        })}</ul>
        <details className="budget-panel"><summary>讨论额度</summary><dl className="budget">{budgets.map(([key, name]) => <div key={key}><dt>{name}</dt><dd>剩余 {session.budget[`${key}_left`] ?? 0}</dd></div>)}</dl></details></>}
        {session && !terminal && <div className="sidebar-tools">
          <details><summary>人工决策与额度</summary>
            <div className="live-form">
              {displayedIssues.map((issue) => <div className="issue-choice" key={issue.issue_id}>
                <label className="check"><input type="checkbox" checked={selected.includes(issue.issue_id)} onChange={(event) =>
                  setSelectedIssues(event.target.checked ? [...selected, issue.issue_id] : selected.filter((key) => key !== issue.issue_id))} />
                  {issue.issue_id} · {issue.summary}{issue.severity === 'blocking' ? '（阻断）' : ''}</label>
                <div className="references">{issue.source_seqs?.map((seq) => <button key={seq} onClick={() => locate(seq)}>#{seq}</button>)}</div>
              </div>)}
              {displayedIssues.length > 0 && <>
                <label>裁决说明<textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} /></label>
                <div className="action-row">
                  {selected.length > 0 && <button disabled={busy || !note.trim() || paused} onClick={() => act('resolve_issues', { issue_ids: selected, note: note.trim() }, () => { setSelectedIssues([]); setNote(''); })}>裁决所选问题</button>}
                </div>
              </>}
              <form className="live-form sidebar-quota" onSubmit={(event) => { event.preventDefault(); act('extend_quota', quota, () => setQuota({ rounds: 0, directed: 0, revisions: 0 })); }}>
                {(['rounds', 'directed', 'revisions'] as const).map((key, index) => <label key={key}>{budgets[index][1]}<input type="number" min={0} max={1000} required value={quota[key]} onChange={(event) => setQuota({ ...quota, [key]: Number(event.target.value) })} /></label>)}
                <button disabled={busy || !Object.values(quota).some((value) => value > 0)}>追加额度</button>
              </form>
            </div>
          </details>
          {!paused && <details><summary>编排联调</summary>
            <div className="live-form"><p>{agentsEnabled ? '角色任务由 Agent 执行。联调时也可在这里手动提交结果。' : '可在这里手动提交议题和角色结果。'}</p>
              {!session.pending_tasks && <div className="action-row"><button disabled={busy} onClick={() => act('retry')}>读取待处理任务</button></div>}
              {currentTasks.map((task) => <div className="action-row" key={task.task_id}>
                <button aria-haspopup="dialog" onClick={() => setTaskSelection({ role: task.role, task })}>
                  {roleLabels[task.role] ?? task.role} · {taskLabels[task.kind] ?? task.kind}
                </button>
                <button disabled={busy} onClick={() => {
                  const { task_id, session_id, role, kind, input_version, snapshot_ref, round_no, topic_id } = task;
                  editAction('submit_output', { task_id, session_id, role, kind, input_version, snapshot_ref, round_no, topic_id });
                }}>填写结果</button>
              </div>)}
              <form className="live-form" onSubmit={(event) => {
                event.preventDefault();
                if (!action) return;
                let data: unknown;
                try { data = JSON.parse(payload); } catch { setError('请输入合法的 JSON 对象。'); return; }
                if (!data || typeof data !== 'object' || Array.isArray(data)) { setError('请输入合法的 JSON 对象。'); return; }
                act(action, data, () => setAction(''));
              }}>
                <label>联调操作<select value={action} onChange={(event) => editAction(event.target.value as Action | '')}>
                  <option value="">选择操作</option>{defaultActions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select></label>
                {action && <><label>请求内容<textarea className="json-input" rows={7} required value={payload} onChange={(event) => setPayload(event.target.value)} /></label>
                  <button disabled={busy}>提交操作</button></>}
              </form>
            </div>
          </details>}
        </div>}
      </aside>
      <main className="discussion">
        <header className="pane-title"><div><h1>项目讨论</h1>{session?.phase && <p>{phaseLabels[session.phase] ?? session.phase}</p>}</div>
          {id && <div className="pane-actions">
            {session && <><span className="count">{session.last_seq} 条消息</span><a href="#live-spec-title">查看规格</a></>}
            <button className="primary new-chat" disabled={busy} onClick={clearSession}>＋ 新聊天</button>
          </div>}</header>
        <div ref={thread} className="thread" aria-label="讨论消息" onScroll={(event) => {
          const element = event.currentTarget;
          shouldScroll.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
        }}>
          {id ? messages.map((message) => <article key={message.seq} id={`message-${message.seq}`} tabIndex={-1}
            className={`message${highlight === message.seq ? ' highlight' : ''}`}>
            <Avatar role={message.role} /><div className="message-body"><header><strong>{roleLabels[message.role] ?? message.role}</strong>
              <span className="event-label">{kindLabels[message.kind] ?? message.kind}</span><span className="sequence">#{message.seq}</span></header>
              <p>{message.text}</p><div className="references">
                {[...new Set([...(message.source_seqs ?? []), ...(message.reply_to_seq ? [message.reply_to_seq] : [])])].map((seq) =>
                  <button key={seq} onClick={() => locate(seq)} aria-label={`定位消息 ${seq}`}>#{seq}</button>)}
                {message.item_refs?.map((item) => <button key={item} onClick={() => setItemRef({ value: item })}>{item}</button>)}
                {message.issue_ids?.map((issue) => <span key={issue}>{issue}</span>)}
                {session && !terminal && !paused && <button onClick={() => setReply(message.seq)}>回复 #{message.seq}</button>}
              </div>
            </div></article>) : <form className="welcome live-form" onSubmit={(event) => { event.preventDefault(); create(); }}>
            <h2>你想做什么产品？</h2>
            <label>项目想法<textarea ref={projectIdeaField} required maxLength={4000} rows={4} placeholder="例如：做一个内部会议室预约工具，首版支持查空闲和预约。" value={idea} onChange={(event) => setIdea(event.target.value)} /></label>
            <details className="project-settings"><summary>项目设置</summary><label>项目 ID（UUID）<input value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label></details>
            <button className="primary" disabled={busy || !idea.trim() || !projectId.trim()}>创建讨论</button>
          </form>}
          {id && !session && !readError && <p role="status">正在恢复讨论…</p>}
          {session && nextStep && <section className="next-step" aria-labelledby="next-step-title">
            <div className="next-step-heading"><span className="step-symbol" aria-hidden="true">{terminal ? '✓' : '↳'}</span><div>
              <h2 id="next-step-title" role="status">{nextStep}</h2>
              {agentsEnabled && (waitingForAgenda || autoExchange) && <p>{session.agent_mode === 'simulation' ? '模拟 Agent' : '模型 Agent'}生成的发言会自动显示在讨论中。</p>}
              {failedAgent?.error_code === 'PROVIDER_CONFIG' && <p>请检查后端的 DeepSeek 密钥和模型名配置。</p>}
              {session.waiting_for === 'role_results' && !paused && !autoExchange && <p>收到角色结果后，会显示在讨论中。</p>}
            </div></div>
            {!paused && agentsEnabled && (waitingForAgenda || autoExchange) && !agentRunning && <button disabled={busy} onClick={() => act('run_agents')}>{failedAgent ? '重试 Agent' : '继续执行 Agent'}</button>}
            {session.waiting_for === 'user_input' && !paused && !terminal && <button onClick={() => composer.current?.focus()}>补充信息</button>}
            {canConfirm && <a href="#live-spec-title">查看待签收规格</a>}
            {waitingForAgenda && !agentsEnabled && <form className="live-form agenda-form" onSubmit={(event) => {
              event.preventDefault();
              startDiscussion();
            }}>
              {editingAgenda || !latestIdea ? <label>要讨论什么？<textarea autoFocus={editingAgenda} required rows={2} maxLength={8000}
                value={agendaText} onChange={(event) => setAgendaDraft({ version: session.input_version, text: event.target.value })} /></label>
                : <p className="agenda-preview">{latestIdea}</p>}
              <div className="action-row">
                <button className="primary" disabled={busy || !agendaText.trim()}>开始讨论</button>
                {latestIdea && (editingAgenda
                  ? <button type="button" disabled={busy} onClick={() => setAgendaDraft(null)}>取消修改</button>
                  : <button type="button" disabled={busy} onClick={() => setAgendaDraft({ version: session.input_version, text: latestIdea })}>修改议题</button>)}
              </div>
            </form>}
          </section>}
        </div>
        {(error || readError) && <div className="error workbench-error" role="alert">
          {error && <p>{error}</p>}{readError && <p>{readError} {id && <button onClick={() => void refresh.current()}>重新读取</button>}</p>}
          {findings.length > 0 && <ul>{findings.map((finding, index) => <li key={index}>
            {finding.path ? <button onClick={() => setItemRef({ value: finding.path! })}>{finding.path}</button> : finding.code} {finding.message}
          </li>)}</ul>}
        </div>}
        {session && !terminal && <footer className="workbench-controls">
          {!paused && <form className="live-form" onSubmit={(event) => {
            event.preventDefault();
            command('/messages', { text: text.trim(), reply_to_seq: reply, substantive, input_version: session.input_version }, () => { setText(''); setReply(null); });
          }}>
            {reply !== null && <div className="action-row"><button type="button" onClick={() => locate(reply)}>回复 #{reply}</button><button type="button" onClick={() => setReply(null)}>取消回复</button></div>}
            <label>{session.waiting_for === 'user_input' ? '补充信息' : '讨论消息'}<textarea ref={composer} rows={2} required maxLength={8000} value={text} onChange={(event) => setText(event.target.value)} /></label>
            <div className="action-row spread"><label className="check"><input type="checkbox" checked={substantive} onChange={(event) => setSubstantive(event.target.checked)} /> 更新需求上下文</label>
              <button className="primary" disabled={busy || !text.trim()}>发送</button></div>
          </form>}
          <div className="action-row">
            <button disabled={busy} onClick={() => act(paused ? 'resume' : 'pause')}>{paused ? '恢复讨论' : '暂停讨论'}</button>
            <button disabled={busy} onClick={() => { if (window.confirm('取消后，此会话将结束。')) act('cancel'); }}>取消讨论</button>
          </div>
        </footer>}
      </main>
      <DiscussionSpec key={id} session={session} snapshots={snapshots} handoff={handoff} locate={locate} itemRef={itemRef} busy={busy}
        confirm={canConfirm ? () => command('/confirm', {
          draft_id: activeDraft.draft_id, input_version: session.input_version, spec_expected_revision: 0,
        }) : undefined}
        requestChanges={session?.status === 'awaiting_confirmation' ? (reason) => act('require_changes', { reason }) : undefined} />
    </div>
    {session && taskSelection && <dialog className="task-dialog" ref={taskDialog} aria-labelledby="task-dialog-title" onClose={() => setTaskSelection(null)}>
      <header className="task-dialog-header">
        <Avatar role={taskSelection.role} />
        <div><h2 id="task-dialog-title">{roleLabels[taskSelection.role] ?? taskSelection.role} · 详情</h2>
          {selectedTask && <p>{taskLabels[selectedTask.kind] ?? selectedTask.kind}</p>}</div>
        <button onClick={() => taskDialog.current?.close()}>关闭</button>
      </header>
      <dl className="task-metadata">
        {selectedPerson && <div><dt>角色状态</dt><dd>{participantLabels[selectedPerson.status] ?? selectedPerson.status}</dd></div>}
        <div><dt>讨论轮次</dt><dd>第 {selectedTask?.round_no ?? session.round_no} 轮</dd></div>
        <div><dt>输入版本</dt><dd>v{selectedTask?.input_version ?? session.input_version}</dd></div>
        {(selectedTask?.topic_id ?? session.topic_id) && <div><dt>议题</dt><dd>{selectedTask?.topic_id ?? session.topic_id}</dd></div>}
        {selectedPerson?.attempt_no != null && <div><dt>执行尝试</dt><dd>第 {selectedPerson.attempt_no} 次</dd></div>}
        {selectedPerson?.error_code && <div><dt>失败原因</dt><dd>{selectedPerson.error_code}</dd></div>}
        {selectedTask && <div><dt>任务 ID</dt><dd><code>{selectedTask.task_id}</code></dd></div>}
        {!!selectedTask?.issue_ids?.length && <div><dt>关联问题</dt><dd>{selectedTask.issue_ids.join('、')}</dd></div>}
      </dl>
      {!selectedTask && <p className="task-detail-note">此角色当前没有待处理任务。</p>}
      <h3>{selectedTask ? '关联消息' : '角色近期消息'}</h3>
      {relatedMessages.length > 0 ? <ul className="task-message-list">{relatedMessages.map((message) => <li key={message.seq}>
        <button onClick={() => { taskDialog.current?.close(); requestAnimationFrame(() => locate(message.seq)); }}>
          <span>#{message.seq}</span><span>{message.text}</span>
        </button>
      </li>)}</ul> : <p className="task-detail-note">暂无消息。</p>}
    </dialog>}
  </div>;
}
