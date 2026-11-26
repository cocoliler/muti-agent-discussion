import { useEffect, useState } from 'react';
import { api, discussionPath } from './discussions';
import type { Draft, Handoff, Requirement, Session } from './discussions';

const verdictLabels: Record<string, string> = { pass: '通过', revise: '退回修订', needs_input: '需要补充' };

function requirements(draft?: Draft | null): Requirement[] {
  return Array.isArray(draft?.content.requirements) ? draft.content.requirements : [];
}

export function DiscussionSpec({ session, collapsed, onCollapse, handoff, locate, itemRef, busy, confirm, requestChanges }: {
  session: Session | null;
  collapsed: boolean;
  onCollapse: () => void;
  handoff: Handoff | null;
  locate: (seq: number) => void;
  itemRef: { value: string } | null;
  busy: boolean;
  confirm?: () => void;
  requestChanges?: (reason: string) => void;
}) {
  const [selection, select] = useState<string | null>(null);
  const [history, setHistory] = useState<Record<string, Draft>>({});
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyRetry, retryHistory] = useState(0);
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
  const draft = active?.draft_id === selected?.draft_id ? active : selected && history[selected.draft_id];
  const prior = selected && (draft?.parent_id
    ? drafts.find((item) => item.draft_id === draft.parent_id)
    : drafts.filter((item) => item.version < selected.version).at(-1));
  const priorDraft = prior && (active?.draft_id === prior.draft_id ? active : history[prior.draft_id]);
  const oldItems = requirements(priorDraft);
  const items = requirements(draft);
  const reviews = session?.reviews.filter((review) => review.draft_id === selected?.draft_id) ?? [];
  const current = !!selected && selected.draft_id === active?.draft_id;
  const receipt = handoff?.draft_id === selected?.draft_id ? handoff : null;
  const signed = !!drafts.find((item) => item.draft_id === selected?.draft_id)?.signed
    || (current && session?.status === 'completed');
  const exportUrl = signed && session?.export_url
    ? `/api/v1${discussionPath(session.id)}/export?draft_id=${encodeURIComponent(selected!.draft_id)}`
    : receipt?.export_url;
  const verdict = reviews.at(-1)?.verdict ?? draft?.review_verdict;
  const deliveryLabels = {
    pending_review: '导入预检通过，待评审',
    blocked_review: '导入预检通过，评审阻断仍待处理',
    invalid: '导入预检未通过',
    ready: '已通过 FlowStudio 导入预检',
  };
  const deliveryLabel = draft?.delivery_status ? deliveryLabels[draft.delivery_status] : '等待导入预检';
  const canUseReviewReasons = verdict === 'revise';
  const comparing = comparison && !!priorDraft && !!draft;
  const scorecard = draft?.scorecard;
  const draftToLoad = !draft ? selected?.draft_id : comparison && !priorDraft ? prior?.draft_id : undefined;
  const sessionId = session?.id;

  useEffect(() => {
    if (!sessionId || !draftToLoad) return;
    const controller = new AbortController();
    setHistoryError(null);
    void api<Draft>(`${discussionPath(sessionId)}/drafts/${encodeURIComponent(draftToLoad)}`,
      undefined, undefined, controller.signal).then((result) => {
        if (!controller.signal.aborted) setHistory((current) => ({ ...current, [result.draft_id]: result }));
      }).catch(() => {
        if (!controller.signal.aborted) setHistoryError(draftToLoad);
      });
    return () => controller.abort();
  }, [sessionId, draftToLoad, historyRetry]);

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

  return <aside id="live-spec-panel" className="spec" aria-label="共识规格" hidden={collapsed}>
    <header className="pane-title"><div><h2 id="live-spec-title" tabIndex={-1}>共识规格</h2><p>需求、评审与签收</p></div>
      <div className="pane-actions">
        {selected && <span className="count">v{selected.version}</span>}
        <button type="button" className="spec-toggle" aria-controls="live-spec-panel" aria-expanded
          onClick={onCollapse}>收起</button>
      </div>
    </header>
    {!selected ? <div className="spec-empty"><span aria-hidden="true">▤</span><h3>还没有规格草稿</h3><p>共识形成后，需求和验收条件会显示在这里。</p></div> : <>
    <div className="spec-navigation">
      <div className="spec-tabs" aria-label="规格视图">
        <button aria-pressed={view === 'draft'} onClick={() => setView('draft')}>规格正文</button>
        <button aria-pressed={view === 'reviews'} onClick={() => setView('reviews')}>评审记录 <span>{reviews.length}</span></button>
      </div>
      <div className="versions">{drafts.map((item) => <button key={item.draft_id} aria-pressed={selected.draft_id === item.draft_id}
        onClick={() => { select(item.current ? null : item.draft_id); setComparison(false); setHighlighted(null); }}>v{item.version}{item.current ? ' 当前' : item.signed ? ' 已签收' : ''}</button>)}</div>
    </div>
    <div className="spec-content">
      <div className="spec-meta"><span>输入 v{selected.input_version} · {selected.open_issue_ids.length} 个待处理问题</span>
        {signed ? <span className="verdict pass">{verdict === 'revise' ? '已签收 · 建议修订' : '已签收'}</span>
          : verdict && <span className={`verdict ${verdict}`}>{verdictLabels[verdict] ?? verdict}</span>}
      </div>
      {(receipt || exportUrl) && <div className="handoff">
        {receipt?.content_hash && <p>正文哈希 <code>{receipt.content_hash}</code></p>}
        {exportUrl && <a href={exportUrl}>下载签收规格 JSON</a>}
      </div>}
      {view === 'draft' && <>
      {draft?.content.product_name && <h3 className="spec-product">{draft.content.product_name}</h3>}
      {draftToLoad && (historyError === draftToLoad
        ? <p className="draft-context" role="alert">暂时无法读取历史版本。 <button type="button"
            onClick={() => retryHistory((attempt) => attempt + 1)}>重试</button></p>
        : <p className="draft-context" role="status">正在读取{draft ? '对比版本' : '历史规格'}…</p>)}
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
        {scorecard.stances && Object.keys(scorecard.stances).length > 0 && <details className="draft-context">
          <summary>查看各角色立场</summary>
          {Object.entries(scorecard.stances).map(([role, stances]) => <div key={role}>
            <strong>{{ product: '产品经理', research: '用户研究员', architect: '技术架构师',
              designer: '用户体验设计师', business: '商业分析' }[role] ?? role}</strong>
            <ul>{stances.map((stance) => <li key={stance.option_id}>
              {stance.option_id} · {{ support: '赞同', oppose: '反对', conditional: '有条件赞同' }[stance.position] ?? stance.position}
              {' · '}{stance.reason}
              {stance.source_seqs.length > 0 && <div className="references">
                {stance.source_seqs.map((seq) => <button key={seq} onClick={() => locate(seq)}>#{seq}</button>)}
              </div>}
            </li>)}</ul>
          </div>)}
        </details>}
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
      {draft && [draft.agreements, draft.assumptions, draft.risks].some((items) => items?.length) &&
        <details className="draft-context">
          <summary>共识、假设与风险</summary>
          {([['共识', draft.agreements], ['待验证假设', draft.assumptions], ['风险', draft.risks]] as const)
            .filter(([, items]) => items?.length).map(([title, items]) => <div key={title}>
              <p className="section-label">{title}</p>
              <ul>{items!.map((item, index) => <li key={index}>{item}</li>)}</ul>
            </div>)}
        </details>}
      {draft && <div className="spec-export">
        <button type="button" onClick={() => void exportDraft()} disabled={exporting || busy || draft.delivery_status === 'invalid'}>
          {exporting ? '正在导出…' : draft.delivery_status === 'ready' ? '导出 JSON' : '导出预览 JSON'}
        </button>
        <span>{deliveryLabel}</span>
        {!!draft.delivery_errors?.length && <details className="draft-context">
          <summary>查看预检问题</summary>
          <ul>{draft.delivery_errors.map((issue, index) => <li key={index}>{issue.path} {issue.message}</li>)}</ul>
        </details>}
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
      {canUseReviewReasons && <p>{signed
        ? '可按本版评审意见继续修订，已签收版本会保留。'
        : '可直接签收并保留评审意见，也可退回后按评审意见修订。'}</p>}
      {editingReason && requestChanges && <form className="live-form" onSubmit={(event) => { event.preventDefault(); if (canUseReviewReasons || reason.trim()) requestChanges(reason.trim()); }}>
        <label>{canUseReviewReasons ? '补充修订意见（可选）' : '修订原因'}<textarea autoFocus required={!canUseReviewReasons} rows={2} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <button disabled={busy || (!canUseReviewReasons && !reason.trim())}>确认退回 v{selected.version}</button>
      </form>}
      <div className="action-row">
        {confirm && <button className="primary" disabled={busy} onClick={confirm}>
          {canUseReviewReasons ? '直接签收' : '签收'} v{selected.version}
        </button>}
        {requestChanges && <button onClick={() => setEditingReason(!editingReason)} aria-expanded={editingReason}>{editingReason ? '收起修订说明' : '退回修订'}</button>}
      </div>
    </footer>}
    </>}
  </aside>;
}
