import { useEffect, useState } from 'react';
import { discussionPath } from './discussions';
import type { Draft, Handoff, Requirement, Session } from './discussions';

const verdictLabels: Record<string, string> = { pass: '通过', revise: '退回修订', needs_input: '需要补充' };

function requirements(draft?: Draft | null): Requirement[] {
  return Array.isArray(draft?.content.requirements) ? draft.content.requirements : [];
}

export function DiscussionSpec({ session, snapshots, handoff, locate, itemRef, busy, confirm, requestChanges }: {
  session: Session | null;
  snapshots: Record<string, Draft>;
  handoff: Handoff | null;
  locate: (seq: number) => void;
  itemRef: { value: string } | null;
  busy: boolean;
  confirm?: () => void;
  requestChanges?: (reason: string) => void;
}) {
  const [selection, select] = useState<string | null>(null);
  const [comparison, setComparison] = useState(false);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [view, setView] = useState<'draft' | 'reviews'>('draft');
  const [editingReason, setEditingReason] = useState(false);
  const [reason, setReason] = useState('');
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const drafts = session?.drafts ?? [];
  const active = session?.active_draft;
  const selected = drafts.find((draft) => draft.draft_id === selection) ?? active ?? drafts.at(-1);
  const draft = active?.draft_id === selected?.draft_id ? active : selected && snapshots[selected.draft_id];
  const prior = selected && drafts.filter((item) => item.version < selected.version && snapshots[item.draft_id]).at(-1);
  const oldItems = requirements(prior && snapshots[prior.draft_id]);
  const items = requirements(draft);
  const reviews = session?.reviews.filter((review) => review.draft_id === selected?.draft_id) ?? [];
  const current = !!selected && selected.draft_id === active?.draft_id;
  const receipt = handoff?.draft_id === selected?.draft_id ? handoff : null;
  const exportUrl = current && session?.status === 'completed' ? session.export_url ?? receipt?.export_url : null;
  const verdict = reviews.at(-1)?.verdict ?? draft?.review_verdict;
  const comparing = comparison && !!prior && !!draft;
  const scorecard = session?.scorecard ?? null;

  useEffect(() => {
    setEditingReason(false);
    setReason('');
    setExportError('');
  }, [selected?.draft_id, requestChanges !== undefined]);

  useEffect(() => {
    if (!highlighted || view !== 'draft') return;
    const target = document.getElementById(`live-requirement-${highlighted}`);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'nearest' });
  }, [highlighted, view, selected?.draft_id]);

  useEffect(() => {
    if (!itemRef) return;
    const path = /^\/requirements\/(\d+)(?:\/|$)/.exec(itemRef.value);
    const id = path ? requirements(active)[Number(path[1])]?.id : itemRef.value;
    const targetId = id && requirements(active).some((item) => item.id === id) ? id : null;
    select(null);
    setView('draft');
    setComparison(false);
    setHighlighted(targetId);
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(targetId ? `live-requirement-${targetId}` : 'live-spec-title');
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: 'nearest' });
    });
    return () => cancelAnimationFrame(frame);
  // A new reference requests navigation. Polls should not return focus to an old target.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemRef]);

  async function exportDraft() {
    if (!session || !selected) return;
    setExporting(true);
    setExportError('');
    try {
      const url = `/api/v1${discussionPath(session.id)}/export?draft_id=${encodeURIComponent(selected.draft_id)}`;
      const response = await fetch(url);
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        const issue = detail?.error?.issues?.[0];
        throw new Error([detail?.error?.message ?? `导出失败（HTTP ${response.status}）`,
          issue && `${issue.path} ${issue.message}`].filter(Boolean).join('。'));
      }
      const blob = await response.blob();
      const href = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = 'design-spec.json';
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch (cause) {
      setExportError(cause instanceof Error ? cause.message : '导出失败。');
    } finally {
      setExporting(false);
    }
  }

  return <aside className="spec" aria-label="共识规格">
    <header className="pane-title"><div><h2 id="live-spec-title" tabIndex={-1}>共识规格</h2><p>需求、评审与签收</p></div>
      {selected && <span className="count">v{selected.version}</span>}
    </header>
    {!selected ? <div className="spec-empty"><span aria-hidden="true">▤</span><h3>还没有规格草稿</h3><p>共识形成后，需求和验收条件会显示在这里。</p></div> : <>
    <div className="spec-navigation">
      <div className="spec-tabs" aria-label="规格视图">
        <button aria-pressed={view === 'draft'} onClick={() => setView('draft')}>规格正文</button>
        <button aria-pressed={view === 'reviews'} onClick={() => setView('reviews')}>评审记录 <span>{reviews.length}</span></button>
      </div>
      <div className="versions">{drafts.map((item) => <button key={item.draft_id} aria-pressed={selected.draft_id === item.draft_id}
        onClick={() => { select(item.current ? null : item.draft_id); setComparison(false); setHighlighted(null); }}>v{item.version}{item.current ? ' 当前' : ''}</button>)}</div>
    </div>
    <div className="spec-content">
      <div className="spec-meta"><span>输入 v{selected.input_version} · {selected.open_issue_ids.length} 个待处理问题</span>
        {session?.status === 'completed' && current ? <span className="verdict pass">已签收</span>
          : verdict && <span className={`verdict ${verdict}`}>{verdictLabels[verdict] ?? verdict}</span>}
      </div>
      {(receipt || exportUrl) && <div className="handoff">
        {receipt?.content_hash && <p>正文哈希 <code>{receipt.content_hash}</code></p>}
        {exportUrl && <a href={exportUrl}>下载签收规格 JSON</a>}
      </div>}
      {view === 'draft' && <>
      {draft?.content.product_name && <h3 className="spec-product">{draft.content.product_name}</h3>}
      {!draft && <p className="draft-context">本次会话未加载此版本的正文，可切换到评审记录查看结论。</p>}
      {draft && prior && <button className="compare" aria-expanded={comparison} onClick={() => setComparison(!comparison)}>
        {comparison ? '收起版本差异' : `比较 v${prior.version} 与 v${draft.version}`}</button>}
      {items.map((item) => {
        const old = oldItems.find((candidate) => candidate.id === item.id);
        const oldCriteria = old?.acceptance_criteria ?? [];
        const nextCriteria = item.acceptance_criteria ?? [];
        const changed = !!old && JSON.stringify(oldCriteria) !== JSON.stringify(nextCriteria);
        const changedText = !!old && old.text !== item.text;
        const changedPriority = !!old && old.priority !== item.priority;
        return <section key={item.id} id={`live-requirement-${item.id}`} tabIndex={-1}
          className={`requirement${highlighted === item.id ? ' highlight' : ''}`}>
          <header><strong>{item.id}</strong><span>{comparing && changedPriority && <><s>{old?.priority}</s> → </>}{item.priority}{comparing && (!old ? ' · 新增' : (changed || changedText || changedPriority) && ' · 已修改')}</span></header>
          {comparing && changedText && <p className="removed"><s>{old?.text}</s></p>}
          <h3>{item.text}</h3>
          {comparing && changed && <ul className="removed">{oldCriteria.map((criterion, index) => <li key={index}><s>{criterion}</s></li>)}</ul>}
          {nextCriteria.length > 0 && <><p className="section-label">验收条件</p><ul className={comparing && changed ? 'added' : ''}>
            {nextCriteria.map((criterion, index) => <li key={index}>{criterion}</li>)}
          </ul></>}
          {!!item.source_seqs?.length && <div className="references"><span>来源</span>{item.source_seqs.map((seq) =>
            <button key={seq} onClick={() => locate(seq)} aria-label={`定位来源消息 ${seq}`}>#{seq}</button>)}</div>}
        </section>;
      })}
      {comparing && oldItems.filter((item) => !items.some((current) => current.id === item.id)).map((item) =>
        <section className="requirement" key={item.id}><header><strong>{item.id}</strong><span>已移除</span></header><p className="removed"><s>{item.text}</s></p></section>)}
      {scorecard && <section className="scorecard">
        <p className="section-label">方案评分（加权）</p>
        <ol>{scorecard.options.map((option) => <li key={option.option_id}
          className={option.option_id === scorecard.chosen_option_id ? 'chosen' : ''}>
          <strong>{option.option_id}</strong> {option.title} · {option.total.toFixed(2)} 分
          {option.option_id === scorecard.chosen_option_id && <span className="tag">已采纳</span>}
        </li>)}</ol>
        {scorecard.rationale && <p className="scorecard-note">{scorecard.rationale}</p>}
      </section>}
      {draft && ((draft.disagreements?.length ?? 0) > 0 || (draft.alternatives?.length ?? 0) > 0) &&
        <section className="divergence">
          {(draft.disagreements?.length ?? 0) > 0 && <>
            <p className="section-label">保留的分歧</p>
            <ul>{draft.disagreements!.map((item, index) => <li key={index}>{item}</li>)}</ul>
          </>}
          {(draft.alternatives?.length ?? 0) > 0 && <>
            <p className="section-label">备选方案</p>
            <ul>{draft.alternatives!.map((item, index) => <li key={index}>{item}</li>)}</ul>
          </>}
        </section>}
      {draft && <div className="spec-export">
        <button type="button" onClick={() => void exportDraft()} disabled={exporting || busy}>
          {exporting ? '正在导出…' : '导出 JSON'}
        </button>
        <span>可直接导入 FlowStudio</span>
        {exportError && <p role="alert">{exportError}</p>}
      </div>}
      {draft && <details className="draft-context"><summary>草稿正文 JSON</summary><pre>{JSON.stringify(draft.content, null, 2)}</pre></details>}
      </>}
      {view === 'reviews' && (reviews.length === 0 ? <p className="draft-context">此版本还没有评审记录。</p> : reviews.map((review) => <section key={review.review_id} className="requirement">
        <header><strong>独立评审</strong><span>{verdictLabels[review.verdict] ?? review.verdict}</span></header>
        {review.findings.map((finding, index) => <div className="review-finding" key={finding.finding_id ?? index}>
          {finding.target_item && (items.some((item) => item.id === finding.target_item)
            ? <button onClick={() => { setHighlighted(finding.target_item!); setView('draft'); }}>{finding.target_item}</button>
            : <strong>{finding.target_item}</strong>)}
          <p>{finding.summary ?? finding.message}</p>
          {finding.close_condition && <p>完成条件 · {finding.close_condition}</p>}
        </div>)}
      </section>))}
    </div>
    {current && (confirm || requestChanges) && <footer className="spec-actions">
      {editingReason && requestChanges && <form className="live-form" onSubmit={(event) => { event.preventDefault(); if (reason.trim()) requestChanges(reason.trim()); }}>
        <label>修订原因<textarea autoFocus required rows={2} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <button disabled={busy || !reason.trim()}>提交修订要求</button>
      </form>}
      <div className="action-row">
        {confirm && <button className="primary" disabled={busy} onClick={confirm}>签收 v{selected.version}</button>}
        {requestChanges && <button onClick={() => setEditingReason(!editingReason)} aria-expanded={editingReason}>{editingReason ? '收起修订说明' : '要求修订'}</button>}
      </div>
    </footer>}
    </>}
  </aside>;
}
