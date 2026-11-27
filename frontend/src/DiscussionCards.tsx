import { memo } from 'react';
import type { ReactNode } from 'react';
import type { Message } from './discussions';
import type { DiscussionEvent, SpeechEvent } from './discussionTimeline';
import { discussionPath } from './discussions';

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
