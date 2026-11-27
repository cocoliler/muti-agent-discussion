import { useEffect, useRef, useState } from 'react';
import { api, discussionPath } from './discussions';
import type { IssueDetail } from './discussions';

const statuses: Record<string, string> = { open: '待处理', answered: '已回应', user_decided: '用户已裁决', closed: '已关闭' };

export function IssueReferenceDialog({ sessionId, issueId, related, onClose, locate }: {
  sessionId: string;
  issueId: string;
  related: number[];
  onClose: () => void;
  locate: (seq: number) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [issue, setIssue] = useState<IssueDetail | null>(null);
  const [error, setError] = useState('');

  useEffect(() => { if (!dialog.current?.open) dialog.current?.showModal(); }, []);

  useEffect(() => {
    const controller = new AbortController();
    void api<IssueDetail>(`${discussionPath(sessionId)}/issues/${encodeURIComponent(issueId)}`, undefined, undefined, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setIssue(result); })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '无法读取问题详情'); });
    return () => controller.abort();
  }, [sessionId, issueId]);

  function jump(seq: number) {
    dialog.current?.close();
    locate(seq);
  }

  return <dialog ref={dialog} className="task-dialog issue-dialog" aria-labelledby="issue-reference-title" onClose={onClose}>
    <header className="task-dialog-header">
      <div><h2 id="issue-reference-title">问题 {issueId}</h2>
        {issue && <p>{statuses[issue.status] ?? issue.status} · 输入 v{issue.input_version}</p>}
      </div>
      <button type="button" onClick={() => dialog.current?.close()}>关闭</button>
    </header>
    {issue ? <>
      <p className="issue-summary">{issue.summary}</p>
      {issue.closure_condition && <><h3>关闭条件</h3><p>{issue.closure_condition}</p></>}
      {!!issue.source_seqs.length && <><h3>来源消息</h3><div className="references">{issue.source_seqs.map((seq) =>
        <button key={seq} type="button" onClick={() => jump(seq)}>来源 #{seq}</button>)}</div></>}
      <div className="references">
        {issue.response_seq !== null && <button type="button" onClick={() => jump(issue.response_seq!)}>查看回应 #{issue.response_seq}</button>}
        {issue.closed_by_seq !== null && <button type="button" onClick={() => jump(issue.closed_by_seq!)}>查看关闭记录 #{issue.closed_by_seq}</button>}
      </div>
    </> : error ? <p className="issue-summary" role="alert">{error}</p> : <p className="issue-summary" role="status">正在读取问题…</p>}
    {error && !!related.length && <><h3>关联消息</h3><div className="references">{related.map((seq) =>
      <button key={seq} type="button" onClick={() => jump(seq)}>消息 #{seq}</button>)}</div></>}
  </dialog>;
}
