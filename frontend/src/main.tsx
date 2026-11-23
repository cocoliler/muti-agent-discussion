import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Workbench } from './Workbench';
import './style.css';

const roles = {
  user: ['你', '我', '#9aa5c2'],
  host: ['主持人', '主', '#a99bff'],
  product: ['产品经理', '产', '#f4ae76'],
  research: ['用户研究员', '研', '#75cbb8'],
  architect: ['技术架构师', '技', '#8caeff'],
  designer: ['交互设计师', '设', '#ec99c2'],
  market: ['商业分析', '商', '#dbc38a'],
  critic: ['质疑者', '辩', '#ff9e9f'],
  scribe: ['记录员', '录', '#80ccfa'],
  reviewer: ['独立评审', '审', '#a9d894'],
} as const;

type Role = keyof typeof roles;
type Message = { seq: number; role: Role; kind: string; text: string; refs: number[]; requirement: string | null };
type Requirement = { id: string; text: string; priority: string; acceptance_criteria: string[] };
type Draft = {
  revision: number;
  verdict: 'REVISE' | 'PASS';
  sources: Record<string, number[]>;
  content: { requirements: Requirement[] };
};
type Session = { id: string; stage: number; messages: Message[]; drafts: Draft[] };

const phases = ['主持与开题', '并行发言', '质疑与回应', '草稿与评审', '定向修订通过', '用户签收'];
const actions = ['开始角色发言', '发起质疑', '生成草稿并送审', '执行定向修订', '确认并签收', '导出规格 JSON'];
const prompts = [
  '主持人已发布议题。接下来五位专业角色将给出各自观点。',
  '五位角色已完成发言。质疑者将针对架构师的预约冲突问题提问。',
  '架构与设计已回应。记录员将生成草稿 v1 并提交独立评审。',
  '评审退回 FR-02。请相关角色补齐并发场景的验收条件。',
  '草稿 v2 已通过评审。可比较改动并签收当前版本。',
  '已签收。下载符合 FlowStudio DesignSpecContent v1.0 的规格。',
];
const labels: Record<string, string> = {
  idea: '初始想法', agenda: '议题', challenge: '质疑', response: '定向回应', draft: '共识稿',
  revise: '退回修订', pass: '评审通过', confirmed: '用户签收',
};
const storageKey = 'design-room.current-session';

async function request(path: string, method = 'GET', body?: object): Promise<Session> {
  const response = await fetch('/api/demo/' + path, {
    method, headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(10000),
  });
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('本地服务返回了无效响应，请检查服务是否正常运行。');
  }
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '操作失败，请重试。');
  return result;
}

function Avatar({ role }: { role: Role }) {
  const [name, initial, color] = roles[role];
  return <span aria-hidden="true" title={name} className="avatar" style={{ color, backgroundColor: color + '22' }}>{initial}</span>;
}

function Demo({ openWorkbench }: { openWorkbench: () => void }) {
  const [session, setSession] = useState<Session | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState<number | null>(null);
  const [comparison, setComparison] = useState(false);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [focusedRequirement, setFocusedRequirement] = useState('');
  const pendingId = useRef<string | null>(null);
  const authoritativeSession = useRef<string | null>(null);
  const messages = useRef<HTMLDivElement>(null);
  const stage = session?.stage ?? -1;
  const currentDraft = session?.drafts.at(-1);
  const draft = session?.drafts.find((item) => item.revision === revision) ?? currentDraft;

  useEffect(() => {
    async function restore() {
      try {
        const id = localStorage.getItem(storageKey);
        if (id) {
          authoritativeSession.current = id;
          setSession(await request(id));
        }
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '无法恢复会话。');
      } finally {
        setBusy(false);
      }
    }
    void restore();
  }, []);

  useEffect(() => {
    const stream = messages.current;
    if (stream) stream.scrollTop = stream.scrollHeight;
  }, [session?.id, stage]);

  async function start() {
    pendingId.current ??= crypto.randomUUID();
    const id = pendingId.current;
    await act(async () => {
      const next = await request(id, 'PUT');
      setSession(next);
      authoritativeSession.current = id;
      pendingId.current = null;
      setRevision(null);
      setComparison(false);
      setHighlight(null);
      setFocusedRequirement('');
      try { localStorage.setItem(storageKey, id); }
      catch { setError('会话已保存到本地服务，浏览器未能保存恢复入口。'); }
    });
  }

  async function act(operation: () => Promise<void>) {
    setBusy(true);
    setError('');
    try { await operation(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '无法连接本地服务。'); }
    finally { setBusy(false); }
  }

  function next() {
    if (!session) return;
    if (stage >= 4 && draft?.revision !== currentDraft?.revision) {
      setRevision(null);
      setComparison(false);
      return;
    }
    if (stage === 5) {
      window.location.assign('/api/demo/' + session.id + '/export');
      return;
    }
    void act(async () => {
      setSession(await (stage === 4
        ? request(session.id + '/confirm', 'POST', { revision: currentDraft!.revision })
        : request(session.id + '/advance', 'POST', { expected_stage: stage })));
      setRevision(null);
      setComparison(false);
      setHighlight(null);
    });
  }

  function locate(seq: number) {
    setHighlight(seq);
    const target = document.getElementById('message-' + seq);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function locateRequirement(id: string) {
    setFocusedRequirement(id);
    setRevision(null);
    requestAnimationFrame(() => {
      const target = document.getElementById('requirement-' + id);
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }

  return <div className="shell">
    <header className="topbar">
      <div className="brand"><span className="brand-icon" aria-hidden="true">✳</span><div><strong>Design Room</strong><small>多 Agent 需求工作台</small></div></div>
      <div className="top-actions"><span className="mode"><i />模拟模式</span>
        <button onClick={openWorkbench}>讨论工作台</button>
        <button disabled={busy} onClick={() => void start()}>{session ? '新建演示' : '启动演示'}</button>
      </div>
    </header>

    <div className="workspace">
      <aside className="rail" aria-label="讨论流程和参与者">
        <p className="section-label">演示项目</p>
        <div className="project"><span aria-hidden="true">▦</span><div><strong>内部会议室预约</strong><small>需求讨论与评审</small></div></div>
        <h2>讨论流程</h2>
        <ol className="phases">{phases.map((phase, index) => <li key={phase}
          className={index === stage ? 'active' : index < stage ? 'done' : ''} aria-current={index === stage ? 'step' : undefined}>
          <span>{index < stage ? '✓' : index + 1}</span>{phase}
        </li>)}</ol>
        <h2>参与角色</h2>
        <ul className="roles">{(Object.keys(roles) as Role[]).filter((role) => role !== 'user').map((role) => <li key={role}><Avatar role={role} />{roles[role][0]}</li>)}</ul>
      </aside>

      <main className="discussion">
        <header className="pane-title"><div><h1>项目讨论</h1><p>内部会议室预约</p></div>{stage >= 0 && <span className="count">{session!.messages.length} 条消息</span>}</header>
        <div ref={messages} className="thread" aria-label="讨论消息">
          {session ? session.messages.map((message) => <article tabIndex={-1} id={'message-' + message.seq} key={message.seq}
            className={'message ' + (highlight === message.seq ? 'highlight ' : '') + 'event-' + message.kind}>
            <Avatar role={message.role} />
            <div className="message-body"><header><strong>{roles[message.role][0]}</strong>
              {labels[message.kind] && <span className="event-label">{labels[message.kind]}</span>}
              <span className="sequence">#{message.seq}</span>
            </header>
            <p>{message.text}</p>
            {(message.refs.length > 0 || message.requirement) && <div className="references">
              {message.refs.map((seq) => <button key={seq} aria-label={'定位消息 ' + seq} onClick={() => locate(seq)}>#{seq}</button>)}
              {draft && message.requirement && <button onClick={() => locateRequirement(message.requirement!)}>{message.requirement}</button>}
            </div>}</div>
          </article>) : <section className="welcome">
            <span className="welcome-icon" aria-hidden="true">✳</span>
            <h2>从项目想法到可评审的规格</h2>
            <p>逐步查看 Agent 发言、提出质疑、修订需求，并导出 FlowStudio 兼容 JSON。</p>
            <blockquote>做一个内部会议室预约工具，首版先解决查空闲和预约。</blockquote>
            <button className="primary" disabled={busy} onClick={() => void start()}>{busy ? '正在加载…' : '启动会议室演示'}</button>
          </section>}
        </div>
        {error && <div className="error" role="alert">{error}{!session && authoritativeSession.current && <button onClick={() => {
          const id = authoritativeSession.current!;
          void act(async () => setSession(await request(id)));
        }}>重试恢复</button>}</div>}
        {session && <footer className="controls"><p role="status">{prompts[stage]}</p><button className="primary" disabled={busy} onClick={next}>
          {busy ? '处理中…' : stage >= 4 && draft?.revision !== currentDraft?.revision ? '查看当前版本' : actions[stage]}
        </button></footer>}
      </main>

      <aside className="spec" aria-label="共识规格">
        <header className="pane-title"><div><h2>共识规格</h2><p>FlowStudio · DesignSpecContent 1.0</p></div></header>
        {draft ? <div className="spec-content">
          <div className="versions">{session!.drafts.map((item) => <button key={item.revision}
            aria-pressed={draft.revision === item.revision} onClick={() => { setRevision(item.revision); setComparison(false); }}>草稿 v{item.revision}</button>)}</div>
          <div className={'verdict ' + (draft.verdict === 'PASS' ? 'pass' : 'revise')}>
            {draft.verdict === 'REVISE' ? '评审退回' : stage === 5 ? '已签收' : '评审通过 · 待签收'}
          </div>
          {draft.revision === 2 && <button className="compare" onClick={() => setComparison(!comparison)} aria-expanded={comparison}>{comparison ? '收起版本差异' : '比较 v1 与 v2'}</button>}
          {draft.content.requirements.map((requirement) => {
            const before = session!.drafts[0].content.requirements.find((item) => item.id === requirement.id)!;
            const changed = before.acceptance_criteria.join() !== requirement.acceptance_criteria.join();
            return <section key={requirement.id} id={'requirement-' + requirement.id} tabIndex={-1} className={'requirement ' + (focusedRequirement === requirement.id ? 'highlight' : '')}>
              <header><strong>{requirement.id}</strong><span>{requirement.priority}</span></header>
              <h3>{requirement.text}</h3>
              <p className="section-label">{comparison && changed ? 'v2 验收条件' : '验收条件'}</p>
              {comparison && changed && <p className="removed"><s>{before.acceptance_criteria.join('；')}</s></p>}
              <ul className={comparison && changed ? 'added' : ''}>{requirement.acceptance_criteria.map((criterion) => <li key={criterion}>{criterion}</li>)}</ul>
              <div className="references"><span>来源</span>{draft.sources[requirement.id].map((seq) => <button key={seq} onClick={() => locate(seq)} aria-label={'定位来源消息 ' + seq}>#{seq}</button>)}</div>
            </section>;
          })}
        </div> : <div className="spec-empty"><span aria-hidden="true">▤</span><h3>等待第一版草稿</h3><p>记录员整理讨论后，这里将显示需求、验收条件和来源。</p></div>}
      </aside>
    </div>
  </div>;
}

function App() {
  const [demo, setDemo] = useState(false);
  return demo ? <Demo openWorkbench={() => setDemo(false)} /> : <Workbench openDemo={() => setDemo(true)} />;
}

createRoot(document.getElementById('root')!).render(<App />);
