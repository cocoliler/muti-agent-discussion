import type { ActivityAttempt, Draft, Message, Session } from './discussions';

export type DisplayMode = 'compact' | 'standard' | 'detailed';

export type SpeechEvent =
  | { type: 'Message'; message: Message }
  | { type: 'Decision'; message: Message }
  | { type: 'Summary'; message: Message };

export type DiscussionEvent = SpeechEvent
  | { type: 'Thinking'; text: string }
  | { type: 'Progress'; messages: Message[] }
  | { type: 'ToolCall'; attempt: ActivityAttempt }
  | { type: 'Artifact'; draft: Draft; signed: boolean };

export type TimelineEntry = SpeechEvent | Extract<DiscussionEvent, { type: 'Progress' }>;

export const displayModes: Record<DisplayMode, string> = { compact: '简洁', standard: '标准', detailed: '详细' };

function isProgress(message: Message) {
  return message.kind === 'system' || (message.kind === 'decision' && message.role === 'host' && (
    message.text === '提交候选稿送审' || /^(点名回应|开定向补充轮)：/.test(message.text)
  ));
}

export function visibleMessages(messages: Message[], mode: DisplayMode, reveal: number | null) {
  if (mode !== 'compact') return messages;
  return messages.filter((message) => message.seq === reveal || message.role === 'user' ||
    (!isProgress(message) && ['decision', 'draft', 'review', 'integration', 'clarification', 'challenge'].includes(message.kind)));
}

export function timelineEntries(messages: Message[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  for (const message of messages) {
    if (isProgress(message)) {
      const last = entries.at(-1);
      if (last?.type === 'Progress' && last.messages[0].role === message.role && last.messages[0].round_no === message.round_no) {
        last.messages.push(message);
      } else {
        entries.push({ type: 'Progress', messages: [message] });
      }
    } else if (message.kind === 'decision') {
      entries.push({ type: 'Decision', message });
    } else if (message.kind === 'integration') {
      entries.push({ type: 'Summary', message });
    } else {
      entries.push({ type: 'Message', message });
    }
  }
  return entries;
}

export function discussionArtifact(session: Session | null): Extract<DiscussionEvent, { type: 'Artifact' }> | null {
  const draft = session?.active_draft;
  if (!draft) return null;
  const signed = session.drafts.some((version) => version.draft_id === draft.draft_id && version.signed) || session.status === 'completed';
  return { type: 'Artifact', draft, signed };
}
