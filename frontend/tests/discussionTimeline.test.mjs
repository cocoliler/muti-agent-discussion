import assert from 'node:assert/strict';
import test from 'node:test';
import { discussionArtifact, timelineEntries, visibleMessages } from '../src/discussionTimeline.ts';

const message = (seq, role, kind, text = '', round_no = 1) => ({ seq, role, kind, text, round_no });

test('display modes preserve the source records and a referenced item can be revealed', () => {
  const messages = [
    message(1, 'user', 'user_input'), message(2, 'architect', 'statement'),
    message(3, 'host', 'decision', '点名回应：architect'), message(4, 'challenger', 'challenge'),
    message(5, 'host', 'decision', '采纳方案 A'), message(6, 'host', 'integration'),
    message(7, 'reviewer', 'review'), message(8, 'reviewer', 'clarification'),
  ];
  assert.deepEqual(visibleMessages(messages, 'compact', null).map(({ seq }) => seq), [1, 4, 5, 6, 7, 8]);
  assert.deepEqual(visibleMessages(messages, 'compact', 2).map(({ seq }) => seq), [1, 2, 4, 5, 6, 7, 8]);
  assert.equal(visibleMessages(messages, 'standard', null), messages);
  assert.equal(visibleMessages(messages, 'detailed', null), messages);
  assert.equal(messages.length, 8);
});

test('execution events group only when their role, round and adjacency match', () => {
  const entries = timelineEntries([
    message(1, 'host', 'system'), message(2, 'host', 'decision', '提交候选稿送审'),
    message(3, 'host', 'system', '', 2), message(4, 'architect', 'system', '', 2),
    message(5, 'host', 'decision', '采纳方案 A'), message(6, 'host', 'integration'),
  ]);
  assert.deepEqual(entries.map((entry) => entry.type), ['Progress', 'Progress', 'Progress', 'Decision', 'Summary']);
  assert.deepEqual(entries[0].messages.map(({ seq }) => seq), [1, 2]);
});

test('compact mode shows specialist findings and requested analysis', () => {
  const messages = [
    message(1, 'product', 'statement'),
    { ...message(2, 'architect', 'statement'), issue_ids: ['I-1'] },
    { ...message(3, 'designer', 'statement'), task_id: 's:analysis:9' },
  ];
  assert.deepEqual(visibleMessages(messages, 'compact', null).map(({ seq }) => seq), [2, 3]);
});

test('a pending draft stays separate from a signed artifact', () => {
  const draft = { draft_id: 'd1', version: 1, content: {} };
  const session = { active_draft: draft, drafts: [{ draft_id: 'd1', signed: false }], status: 'awaiting_confirmation' };
  assert.equal(discussionArtifact(session).signed, false);
  assert.equal(discussionArtifact({ ...session, status: 'completed' }).signed, true);
  assert.equal(discussionArtifact(null), null);
});
