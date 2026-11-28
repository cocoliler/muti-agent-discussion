import { memo, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import type { InitialDraft, Message } from './discussions';
import type { DiscussionEvent, SpeechEvent } from './discussionTimeline';
import { ApiError, api, discussionPath } from './discussions';

type SpeechCardProps = { id: string; highlight: boolean; children: ReactNode };

function MessageCard({ id, highlight, children }: SpeechCardProps) {
  return <article id={id} tabIndex={-1} className={`message agent-card${highlight ? ' highlight' : ''}`}>{children}</article>;
}

function DecisionCard({ id, highlight, children }: SpeechCardProps) {
  return <article id={id} tabIndex={-1} className={`message decision-card${highlight ? ' highlight' : ''}`}>{children}</article>;
}

function SummaryCard({ id, highlight, children }: SpeechCardProps) {
  return <article id={id} tabIndex={-1} className={`message summary-card${highlight ? ' highlight' : ''}`}>{children}</article>;
}

const speechCards = { Message: MessageCard, Decision: DecisionCard, Summary: SummaryCard };

export function SpeechCard({ type, ...props }: SpeechCardProps & { type: SpeechEvent['type'] }) {
  const Card = speechCards[type];
  return <Card {...props} />;
}

export const ProgressEvents = memo(function ProgressEvents({ event, author, highlight, renderActions }: {
  event: Extract<DiscussionEvent, { type: 'Progress' }>;
  author: string;
  highlight: number | null;
  renderActions: (message: Message) => ReactNode;
}) {
  return <section className="progress-events" aria-label={`${author}的执行事件`}>
    <strong>{author} · 执行事件</strong>
    {event.messages.map((message) => <div key={message.seq} id={`message-${message.seq}`} tabIndex={-1}
      className={message.seq === highlight ? 'highlight' : undefined}>
      <span>#{message.seq}</span><div><p>{message.text}</p>{renderActions(message)}</div>
    </div>)}
  </section>;
});

export function ArtifactCard({ event, sessionId, open }: {
  event: Extract<DiscussionEvent, { type: 'Artifact' }>;
  sessionId: string;
  open: () => void;
}) {
  const { draft, signed } = event;
  const requirementCount = Array.isArray(draft.content.requirements) ? draft.content.requirements.length : undefined;
  return <section className="workbench-artifact" aria-label="规格成果">
    <div><span>规格成果 · v{draft.version} · {signed ? '已签收' : draft.review_verdict === 'revise' ? '建议修订' : draft.review_verdict === 'pass' ? '待签收' : '草稿'}
      {signed && requirementCount !== undefined && ` · ${requirementCount} 项需求`}</span>
      <strong>{draft.content.product_name || '共识规格'}</strong></div>
    <div className="action-row">
      <button type="button" onClick={open}>查看规格</button>
      {signed && <a href={`/api/v1${discussionPath(sessionId)}/export?draft_id=${encodeURIComponent(draft.draft_id)}`}>下载规格 JSON</a>}
    </div>
  </section>;
}

const outlineSections = [
  ['产品目标', 'goals'], ['目标用户', 'target_users'], ['主要场景', 'scenarios'],
  ['核心模块', 'core_modules'], ['模块关系', 'module_relations'], ['业务流程', 'business_flow'],
  ['系统边界', 'system_boundary'], ['首版范围', 'scope_in'], ['暂缓范围', 'scope_out'],
  ['外部依赖', 'external_dependencies'], ['约束', 'constraints'], ['假设', 'assumptions'],
  ['风险', 'risks'], ['待确认问题', 'open_questions'],
] as const;

export function InitialOutlineCard({ sessionId, draft, expanded, locate, discuss }: {
  sessionId: string;
  draft: InitialDraft;
  expanded: boolean;
  locate: (seq: number) => void;
  discuss?: (seq: number) => void;
}) {
  const { content } = draft;
  const agendaSeq = draft.source_seqs.at(-1);
  const outlineBody = useRef<HTMLDivElement>(null);
  const { draft_id: draftId, schema_version: schemaVersion } = draft;
  useEffect(() => {
    if (!outlineBody.current || !schemaVersion || typeof IntersectionObserver === 'undefined') return;
    const controller = new AbortController();
    let intersecting = false;
    let inFlight = false;
    let complete = false;
    let attempts = 0;
    let retryTimer: number | undefined;
    function reportVisible() {
      if (controller.signal.aborted || complete || inFlight || retryTimer !== undefined ||
        attempts >= 3 || !intersecting || document.visibilityState !== 'visible') return;
      inFlight = true;
      attempts += 1;
      void api(`${discussionPath(sessionId)}/working-drafts/${encodeURIComponent(draftId)}/visible`, {},
        draftId, controller.signal).then(() => { complete = true; }).catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status < 500 && error.status !== 429) {
          complete = true;
        } else if (attempts < 3) {
          retryTimer = window.setTimeout(() => {
            retryTimer = undefined;
            reportVisible();
          }, 500 * attempts);
        }
      }).finally(() => { inFlight = false; });
    }
    const observer = new IntersectionObserver(([entry]) => {
      intersecting = entry.isIntersecting && entry.intersectionRatio > 0;
      reportVisible();
    });
    observer.observe(outlineBody.current);
    document.addEventListener('visibilitychange', reportVisible);
    return () => {
      controller.abort();
      window.clearTimeout(retryTimer);
      observer.disconnect();
      document.removeEventListener('visibilitychange', reportVisible);
    };
  }, [sessionId, draftId, schemaVersion]);
  return <details className="workbench-artifact initial-outline" aria-label="初始设计框架" open={expanded || undefined}>
    <summary>初始设计框架 · 未评审 <strong>{content.product_name}</strong></summary>
    <div ref={outlineBody} className="initial-outline-body">
      <p>{content.positioning}</p>
      <div className="initial-outline-sections">{outlineSections.map(([label, key]) => {
        const entries = content[key];
        if (!entries?.length) return null;
        const List = key === 'business_flow' ? 'ol' : 'ul';
        return <section key={key}><h3>{label}</h3>
          <List>{entries.map((entry, index) => <li key={index}>{entry}</li>)}</List></section>;
      })}</div>
      <div className="references">
        {discuss && agendaSeq !== undefined && <button type="button" onClick={() => discuss(agendaSeq)}>基于框架讨论</button>}
        {draft.source_seqs.map((seq) => <button type="button" key={seq} onClick={() => locate(seq)}>来源 #{seq}</button>)}
      </div>
    </div>
  </details>;
}
