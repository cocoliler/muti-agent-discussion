import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api, deleteDiscussion, discussionPath, readMessages } from './discussions';
import type { Action, ClarificationAnswer, Decision, Finding, Handoff, Message, Session, Task } from './discussions';
import { DiscussionSpec } from './DiscussionSpec';
import { ClarificationForm } from './ClarificationForm';
import { DiscussionMessageText } from './DiscussionMessageText';
import { LocalDiscussionHistory } from './LocalDiscussionHistory';
import './workbench.css';

const statusLabels: Record<string, string> = {
  clarifying: '澄清需求', discussing: '讨论中', reviewing: '评审中', awaiting_confirmation: '待人工审阅',
  completed: '已签收', paused: '已暂停', canceled: '已取消',
};
const waitingLabels: Record<string, string> = {
  host_agenda: '等待主持人提交议题', host_decision: '等待主持人调度', role_results: '等待角色结果',
  user_input: '等待你补充信息', review: '等待独立评审', none: '当前没有待处理的调度',
};
const phaseLabels: Record<string, string> = {
  statements: '专业角色发言', challenge: '质疑', responses: '定向回应', integration: '整合', drafting: '成稿',
};
const agentPhaseLabels: Record<string, string> = {
  statements: '专业角色正在并行发言', challenge: '质疑者正在审视本轮发言', responses: '专业角色正在回应问题',
  stances: '专业角色正在独立比较方案', integration: '记录员正在整合讨论', drafting: '记录员正在撰写规格',
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
  response: '回应', stance: '方案立场', integration: '整合', draft: '草稿', review: '评审', decision: '决定', system: '系统',
};
const taskLabels = { ...kindLabels, statement: '专业角色发言', integration: '整合讨论结果', scoring: '方案比较与评分', draft: '撰写规格草稿' } as Record<string, string>;
const storageKey = 'design-room.discussion';
const projectKey = 'design-room.project';
const specVisibilityKey = 'design-room.spec-visibility';
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

function previousAnswers(text: string): ClarificationAnswer[] {
  const parts = text.trim().split(/\n\n(?=\d+\. )/);
  const parsed = parts.map((part) => /^\d+\. (.+?)\n回答：([\s\S]+)$/s.exec(part));
  return parsed.every((part) => part?.[1].trim() && part[2].trim())
    ? parsed.map((part) => ({ question: part![1].trim(), answer: part![2].trim(), options: [] }))
    : [{ question: '你想补充什么？', answer: text, options: [] }];
}

function distanceFromThreadBottom(list: HTMLDivElement) {
  return list.scrollHeight - list.scrollTop - list.clientHeight;
}

function discussionRoundStarts(messages: Message[]) {
  const starts = new Map<number, Message>();
  for (const message of messages) {
    if (message.round_no !== undefined && !starts.has(message.round_no)) starts.set(message.round_no, message);
  }
  return [...starts.entries()];
}

export function Workbench({ openDemo }: { openDemo: () => void }) {
  const [id, setId] = useState(() => readStorage(storageKey));
  const [projectId, setProjectId] = useState(() => readStorage(projectKey) ?? crypto.randomUUID());
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
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
  const [specCollapsed, setSpecCollapsed] = useState(() => readStorage(specVisibilityKey) === 'collapsed');
  const revealSpec = useRef<HTMLButtonElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [readError, setReadError] = useState('');
  const [findings, setFindings] = useState<Finding[]>([]);
  const [answerToEdit, setAnswerToEdit] = useState<number | null>(null);
  const [action, setAction] = useState<Action | ''>('');
  const [payload, setPayload] = useState('');
  const [agendaDraft, setAgendaDraft] = useState<{ version: number; text: string } | null>(null);
  const thread = useRef<HTMLDivElement>(null);
  const [canJumpToBottom, setCanJumpToBottom] = useState(false);
  const [activeRound, setActiveRound] = useState<number | null>(null);
  const roundStarts = useMemo(() => discussionRoundStarts(messages), [messages]);
  const projectIdeaField = useRef<HTMLTextAreaElement>(null);
  const sessionRead = useRef<AbortController | null>(null);
  const shouldScroll = useRef(true);
  const refresh = useRef<() => Promise<void>>(async () => {});
  const pending = useRef<{ signature: string; key: string } | null>(null);

  const updateThreadPosition = useCallback((list: HTMLDivElement) => {
    const distance = distanceFromThreadBottom(list);
    setCanJumpToBottom(distance > 24);
    if (distance < 2) {
      setActiveRound(roundStarts.at(-1)?.[0] ?? null);
      return;
    }
    const top = list.getBoundingClientRect().top + 28;
    let visible = roundStarts[0]?.[0] ?? null;
    for (const [round, message] of roundStarts) {
      const target = document.getElementById(`message-${message.seq}`);
      if (target && target.getBoundingClientRect().top > top) break;
      if (target) visible = round;
    }
    setActiveRound(visible);
  }, [roundStarts]);

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
    const list = thread.current;
    if (!list) return;
    if (shouldScroll.current) {
      const clarification = list.querySelector(':scope > .clarification-card:not([hidden])');
      list.scrollTop = clarification
        ? list.scrollTop + clarification.getBoundingClientRect().top - list.getBoundingClientRect().top - 12
        : list.scrollHeight;
    }
    updateThreadPosition(list);
  }, [messages.length, updateThreadPosition]);

  useEffect(() => {
    const list = thread.current;
    if (!list) return;
    const observer = new ResizeObserver(() => updateThreadPosition(list));
    observer.observe(list);
    return () => observer.disconnect();
  }, [updateThreadPosition]);

  useEffect(() => {
    if (taskSelection && !taskDialog.current?.open) taskDialog.current?.showModal();
  }, [taskSelection]);

  function selectSession(nextId: string | null) {
    sessionRead.current?.abort();
    saveStorage(storageKey, nextId);
    setId(nextId);
    setSession(null);
    setMessages([]);
    setTasks([]);
    setTaskSelection(null);
    setHandoff(null);
    setError('');
    setReadError('');
    setFindings([]);
    setAnswerToEdit(null);
    setIdea('');
    setText('');
    setReply(null);
    setAction('');
    setPayload('');
    setAgendaDraft(null);
    setSubstantive(true);
    setHighlight(null);
    setItemRef(null);
    shouldScroll.current = true;
    setCanJumpToBottom(false);
    setActiveRound(null);
    pending.current = null;
    if (!nextId) requestAnimationFrame(() => projectIdeaField.current?.focus());
  }

  function setSpecVisibility(collapsed: boolean) {
    setSpecCollapsed(collapsed);
    saveStorage(specVisibilityKey, collapsed ? 'collapsed' : null);
  }

  function collapseSpec() {
    setSpecVisibility(true);
    requestAnimationFrame(() => revealSpec.current?.focus());
  }

  function showSpec() {
    setSpecVisibility(false);
    requestAnimationFrame(() => {
      const title = document.getElementById('live-spec-title');
      title?.focus({ preventScroll: true });
      title?.scrollIntoView({ block: 'nearest' });
    });
  }

  function showReference(value: string) {
    setSpecVisibility(false);
    setItemRef({ value });
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

  function jumpToRound(seq: number) {
    const list = thread.current;
    const target = document.getElementById(`message-${seq}`);
    if (!list || !target) return;
    shouldScroll.current = false;
    list.scrollTop += target.getBoundingClientRect().top - list.getBoundingClientRect().top - 12;
    setHighlight(seq);
    target.focus({ preventScroll: true });
    updateThreadPosition(list);
  }

  function jumpToBottom() {
    const list = thread.current;
    if (!list) return;
    shouldScroll.current = true;
    list.scrollTop = list.scrollHeight;
    list.focus({ preventScroll: true });
    updateThreadPosition(list);
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
  const supersededAnswers = new Set(session?.superseded_answer_seqs ?? []);
  const promptsBySeq = new Set(messages.filter((message) => message.kind === 'clarification').map((message) => message.seq));
  const latestAnswers = new Map<number, Message>();
  for (const message of messages) {
    if (message.role === 'user' && message.kind === 'user_input' && message.substantive !== false && message.reply_to_seq !== null
        && promptsBySeq.has(message.reply_to_seq)) latestAnswers.set(message.reply_to_seq, message);
  }
  const editableAnswers = session?.superseded_answer_seqs && !terminal && !paused
    ? [...latestAnswers.values()].filter((message) => !supersededAnswers.has(message.seq)) : [];
  const editMessage = editableAnswers.find((message) => message.seq === answerToEdit);
  const editAnswers = editMessage ? session?.clarification_answers?.[editMessage.seq] ?? previousAnswers(editMessage.text) : [];

  function reanswer(seq: number) {
    setError('');
    setAnswerToEdit(seq);
    requestAnimationFrame(() => {
      const heading = document.querySelector<HTMLElement>('#clarification-edit h2');
      heading?.focus({ preventScroll: true });
      heading?.scrollIntoView({ block: 'nearest' });
    });
  }
  const currentTasks = session ? (session.pending_tasks ?? tasks).filter((task) => task.input_version === session.input_version && task.round_no === session.round_no) : [];
  const selectedTask = taskSelection?.task;
  const selectedPerson = session?.participants.find((person) => person.role === taskSelection?.role);
  const relatedMessages = taskSelection ? messages.filter((message) => selectedTask
    ? message.task_id === selectedTask.task_id || selectedTask.message_seqs?.includes(message.seq)
    : message.role === taskSelection.role).slice(-5) : [];
  const activeDraft = session?.active_draft;
  const canConfirm = session?.status === 'awaiting_confirmation' && activeDraft != null
    && (activeDraft.review_verdict === 'pass' || activeDraft.review_verdict === 'revise')
    && activeDraft.input_version === session.input_version;
  const awaitingClarification = session?.status === 'clarifying' && session.waiting_for === 'user_input';
  const clarificationMessage = awaitingClarification ? messages.filter((message) => message.kind === 'clarification'
    && (message.input_version === undefined || message.input_version === session.input_version)).at(-1) : undefined;
  const legacyReview = session?.reviews.at(-1);
  const legacyQuestions = clarificationMessage?.role === 'reviewer' && legacyReview?.verdict === 'needs_input'
    ? legacyReview.findings.find((finding) => finding.finding_id === `${legacyReview.review_id}:questions`)?.summary
    : clarificationMessage?.role === 'host' ? clarificationMessage.text.replace(/^需要用户补充关键事实：/, '') : undefined;
  const clarificationQuestions = session?.clarification?.questions ?? legacyQuestions?.split('；').map((part) => part.trim()).filter(Boolean) ?? [];
  const clarificationSeq = session?.clarification?.message_seq ?? clarificationMessage?.seq ?? null;
  const clarificationKey = awaitingClarification ? `${session.id}:${session.input_version}:${clarificationSeq ?? 0}` : null;
  const hostWorking = session?.participants.some((person) => person.role === 'host' && person.status === 'running');
  const waitingForAgenda = session?.waiting_for === 'host_agenda' && !paused;
  const agentsEnabled = session?.agent_mode === 'simulation' || session?.agent_mode === 'real';
  const autoExchange = agentsEnabled && (session?.waiting_for === 'role_results' || session?.waiting_for === 'review' || session?.waiting_for === 'host_decision');
  const isWorkingRole = (role: string) => waitingForAgenda ? role === 'host'
    : session?.waiting_for === 'review' ? role === 'reviewer' : currentTasks.some((task) => task.role === role);
  const agentRunning = hostWorking || session?.participants.some((person) => isWorkingRole(person.role) && person.status === 'running');
  const failedAgent = session?.participants.find((person) => person.status === 'failed' && (
    isWorkingRole(person.role)));
  const projectIdea = messages.find((message) => message.kind === 'user_input' && message.substantive !== false)?.text;
  const latestIdea = messages.filter((message) => message.kind === 'user_input' && message.substantive !== false).at(-1)?.text.trim() ?? '';
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
          : session.waiting_for === 'host_decision' ? '等待下一步调度'
          : currentTasks.some((task) => task.kind === 'scoring') ? '主持人正在比较备选方案'
            : agentPhaseLabels[session.phase ?? ''] ?? 'Agent 正在处理')
        : session?.status === 'awaiting_confirmation' ? (activeDraft?.review_verdict === 'revise' ? '建议修订，可签收或退回' : '待签收或退回')
          : session && waitingLabels[session.waiting_for];

  return <div className="shell workbench-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-icon" aria-hidden="true">✳</span><div><strong>Design Room</strong><small>多 Agent 需求工作台</small></div></div>
      <div className="top-actions"><button disabled={busy} onClick={openDemo}>会议室演示</button></div>
    </header>
    <div className={`workspace live-workspace${specCollapsed ? ' spec-collapsed' : ''}`}>
      <aside className="rail" aria-label="讨论状态和参与者">
        <div className="rail-toolbar">
          <h2>当前会话</h2>
          {id && <button type="button" className="primary new-chat" disabled={busy}
            onClick={() => selectSession(null)}>＋ 新聊天</button>}
        </div>
        <div className="session-status"><strong>{projectIdea || (session ? '项目讨论' : '新讨论')}</strong>
          {session && <p>{statusLabels[session.status] ?? session.status} · 输入 v{session.input_version}{session.round_no > 0 && ` · 第 ${session.round_no} 轮`}</p>}</div>
        <LocalDiscussionHistory activeId={id} activeStatus={session?.status} labels={statusLabels} disabled={busy}
          activeDiscussion={session?.id === id && session ? {
            id: session.id, project_id: session.project_id, title: projectIdea?.slice(0, 120) || '项目讨论',
            status: session.status, round_no: session.round_no, updated_at: '',
          } : undefined}
          onOpen={(discussion) => {
            setProjectId(discussion.project_id);
            saveStorage(projectKey, discussion.project_id);
            selectSession(discussion.id);
          }}
          onDelete={async (discussion) => {
            setBusy(true);
            try {
              await deleteDiscussion(discussion.id);
              selectSession(null);
            } finally {
              setBusy(false);
            }
          }} />
        {session && <><h2>参与角色</h2><ul className="roles">{session.participants.map((person) => {
          const task = currentTasks.find((item) => item.role === person.role);
          const name = roleLabels[person.role] ?? person.role;
          return <li key={person.role}>
            <button className="role-task" aria-haspopup="dialog" aria-label={`查看${name}详情`} onClick={() => setTaskSelection({ role: person.role, task })}>
              <Avatar role={person.role} /><span>{name}<small><i data-status={person.status} />{participantLabels[person.status] ?? person.status}{person.attempt_no != null && ` · 尝试 ${person.attempt_no}`}</small></span>
              <span className="task-chevron" aria-hidden="true">›</span>
            </button>
          </li>;
        })}</ul></>}
        {session && !terminal && <div className="sidebar-tools">
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
          {(session || specCollapsed) && <div className="pane-actions">
            {session && <span className="count">{session.last_seq} 条消息</span>}
            {specCollapsed
              ? <button type="button" className="spec-toggle" ref={revealSpec} aria-controls="live-spec-panel"
                  aria-expanded={false} onClick={showSpec}>展开规格</button>
              : <a href="#live-spec-title">查看规格</a>}
          </div>}</header>
        <div className="thread-pane">
          {id && roundStarts.some(([round]) => round > 0) && <nav className="round-nav" aria-label="聊天轮次">
            {roundStarts.map(([round, message]) => {
              const label = round === 0 ? '讨论开场' : `第 ${round} 轮`;
              return <button key={round} type="button" aria-label={`跳到${label}`} title={label}
                aria-controls="discussion-thread" aria-current={activeRound === round ? 'location' : undefined}
                onClick={() => jumpToRound(message.seq)}><span aria-hidden="true">{round || '始'}</span></button>;
            })}
          </nav>}
          <div ref={thread} id="discussion-thread" className="thread" role="region" aria-label="讨论消息" tabIndex={-1}
            onScroll={(event) => {
              const list = event.currentTarget;
              shouldScroll.current = distanceFromThreadBottom(list) < 80;
              updateThreadPosition(list);
            }}>
            {id ? messages.map((message) => <article key={message.seq} id={`message-${message.seq}`} tabIndex={-1}
              className={`message${highlight === message.seq ? ' highlight' : ''}`}>
              <Avatar role={message.role} /><div className="message-body"><header><strong>{roleLabels[message.role] ?? message.role}</strong>
                <span className="event-label">{supersededAnswers.has(message.seq) ? '答案已替换' : message.substantive === false ? '随手备注' : kindLabels[message.kind] ?? message.kind}</span><span className="sequence">#{message.seq}</span>
                {editableAnswers.includes(message) && editMessage !== message && <button className="message-edit" disabled={busy}
                  onClick={() => reanswer(message.seq)}>修改回答</button>}
              </header>
                {session && editMessage === message ? <ClarificationForm
                  questions={editAnswers.map((answer) => answer.question)} initialAnswers={editAnswers}
                  options={Object.fromEntries(editAnswers.map((answer) => [answer.question, answer.options]))} busy={busy} error={error}
                  onCancel={() => {
                    setError('');
                    setAnswerToEdit(null);
                    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`#message-${message.seq} .message-edit`)?.focus());
                  }}
                  onSubmit={(text, answers) => command('/messages', {
                    text, reply_to_seq: message.reply_to_seq, replaces_answer_seq: message.seq,
                    clarification_answers: answers, substantive: true, input_version: session.input_version,
                  }, () => { setAnswerToEdit(null); shouldScroll.current = true; })} />
                : <>{message.role === 'user' && !!session?.clarification_answers?.[message.seq]?.length
                  ? <div className="clarification-replies">{session.clarification_answers[message.seq].map((entry, index) => <div key={index}>
                      <DiscussionMessageText text={entry.question} formatted />
                      <p><strong>回答</strong> {entry.answer}</p>
                    </div>)}</div>
                  : <DiscussionMessageText text={message.text} formatted={message.role !== 'user' && message.kind !== 'system'} />}
                  <div className="references">
                  {[...new Set([...(message.source_seqs ?? []), ...(message.reply_to_seq ? [message.reply_to_seq] : [])])].map((seq) =>
                    <button key={seq} onClick={() => locate(seq)} aria-label={`定位消息 ${seq}`}>#{seq}</button>)}
                  {message.item_refs?.map((item) => <button key={item} onClick={() => showReference(item)}>{item}</button>)}
                  {message.issue_ids?.map((issue) => <span key={issue}>{issue}</span>)}
                  {session && !terminal && !paused && <button onClick={() => setReply(message.seq)}>回复 #{message.seq}</button>}
                </div></>}
              </div></article>) : <form className="welcome live-form" onSubmit={(event) => { event.preventDefault(); create(); }}>
              <h2>你想做什么产品？</h2>
              <label>项目想法<textarea ref={projectIdeaField} required maxLength={4000} rows={4} placeholder="例如：做一个内部会议室预约工具，首版支持查空闲和预约。" value={idea} onChange={(event) => setIdea(event.target.value)} /></label>
              <details className="project-settings"><summary>项目设置</summary><label>项目 ID（UUID）<input value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label></details>
              <p className="model-disclosure">真实模型模式会将项目输入和任务所需的讨论内容发送给 DeepSeek。请勿填写密钥或未经授权的客户资料。</p>
              <button className="primary" disabled={busy || !idea.trim() || !projectId.trim()}>创建讨论</button>
            </form>}
            {id && !session && !readError && <p role="status">正在恢复讨论…</p>}
            {session && clarificationKey && <ClarificationForm key={clarificationKey}
              questions={clarificationQuestions} options={session.clarification?.options} busy={busy} error={error} hidden={!!editMessage}
              onSubmit={(answer, answers) => command('/messages', {
                text: answer, reply_to_seq: clarificationSeq, substantive: true, input_version: session.input_version,
                ...(session.superseded_answer_seqs && clarificationSeq ? { clarification_answers: answers } : {}),
              })} />}
            {session && nextStep && !clarificationKey && !editMessage && <section className="next-step" aria-labelledby="next-step-title">
              <div className="next-step-heading"><span className="step-symbol" aria-hidden="true">{terminal ? '✓' : '↳'}</span><div>
                <h2 id="next-step-title" role="status">{nextStep}</h2>
                {agentsEnabled && (waitingForAgenda || autoExchange) && <p>{session.agent_mode === 'simulation' ? '模拟 Agent' : '模型 Agent'}生成的发言会自动显示在讨论中。</p>}
                {failedAgent?.error_code === 'AGENT_NO_PROGRESS' && <p>这次执行没有推进待处理任务，已停止自动重复调用。</p>}
                {failedAgent?.error_code === 'PROVIDER_CONFIG' && <p>请检查后端的 DeepSeek 密钥和模型名配置。</p>}
                {failedAgent?.diagnostic && <p>{/^missing at (?:source_seqs|findings\/\d+\/source_seqs)$/.test(failedAgent.diagnostic)
                  ? `${roleLabels[failedAgent.role] ?? failedAgent.role}的输出缺少消息引用，重试会重新核对引用。`
                  : failedAgent.diagnostic.startsWith('requirement_changes')
                    ? '记录员的需求变更记录未通过校验，重试会重新核对旧需求的去向。'
                    : `输出校验失败：${failedAgent.diagnostic}`}</p>}
                {session.waiting_for === 'role_results' && !paused && !autoExchange && <p>收到角色结果后，会显示在讨论中。</p>}
              </div></div>
              {!paused && agentsEnabled && (waitingForAgenda || autoExchange) && !agentRunning && <button disabled={busy} onClick={() => act('run_agents')}>{failedAgent ? '重试 Agent' : '继续执行 Agent'}</button>}
              {session.status === 'awaiting_confirmation' && <a href="#live-spec-title" onClick={(event) => { event.preventDefault(); showSpec(); }}>查看待审阅规格</a>}
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
          {id && canJumpToBottom && <button type="button" className="jump-to-bottom" aria-label="跳到最新消息"
            title="跳到最新消息" aria-controls="discussion-thread" onClick={jumpToBottom} />}
        </div>
        {((error && !clarificationKey && !editMessage) || readError || findings.length > 0) && <div className="error workbench-error" role="alert">
          {error && !clarificationKey && !editMessage && <p>{error}</p>}{readError && <p>{readError} {id && <button onClick={() => void refresh.current()}>重新读取</button>}</p>}
          {findings.length > 0 && <ul>{findings.map((finding, index) => <li key={index}>
            {finding.path ? <button onClick={() => showReference(finding.path!)}>{finding.path}</button> : finding.code} {finding.message}
          </li>)}</ul>}
        </div>}
        {session && !terminal && <footer className="workbench-controls">
          <form className="discussion-composer" onSubmit={(event) => {
            event.preventDefault();
            if (paused || !text.trim()) return;
            command('/messages', { text: text.trim(), reply_to_seq: reply, substantive, input_version: session.input_version }, () => { setText(''); setReply(null); });
          }}>
            {!paused && <>
              {reply !== null && <div className="composer-reply">
                <button type="button" onClick={() => locate(reply)}>回复 #{reply}</button>
                <button type="button" aria-label="取消回复" onClick={() => setReply(null)}>×</button>
              </div>}
              <textarea aria-label="讨论消息" placeholder="输入讨论消息…" rows={1} required maxLength={8000}
                value={text} onChange={(event) => setText(event.target.value)} />
            </>}
            <div className="composer-toolbar">
              {!paused && <label className="composer-context">
                <input type="checkbox" checked={substantive} onChange={(event) => setSubstantive(event.target.checked)} />
                更新需求上下文
              </label>}
              <div className="composer-actions">
                <button type="button" disabled={busy} onClick={() => act(paused ? 'resume' : 'pause')}>{paused ? '恢复讨论' : '暂停讨论'}</button>
                <button type="button" disabled={busy} onClick={() => { if (window.confirm('取消后，此会话将结束。')) act('cancel'); }}>取消讨论</button>
              </div>
              {!paused && <button type="submit" className="primary composer-send" aria-label="发送" title="发送" disabled={busy || !text.trim()}>
                <span aria-hidden="true">↑</span>
              </button>}
            </div>
          </form>
        </footer>}
      </main>
      <DiscussionSpec key={id} collapsed={specCollapsed} onCollapse={collapseSpec} session={session} handoff={handoff} locate={locate} itemRef={itemRef} busy={busy}
        confirm={canConfirm ? () => command('/confirm', {
          draft_id: activeDraft.draft_id, input_version: session.input_version, spec_expected_revision: 0,
        }) : undefined}
        requestChanges={activeDraft && (session?.status === 'awaiting_confirmation' || session?.status === 'completed') ? (reason) => act('require_changes', { reason, draft_id: activeDraft?.draft_id }) : undefined} />
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
        {selectedPerson?.diagnostic && <div><dt>校验详情</dt><dd>{selectedPerson.diagnostic}</dd></div>}
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
