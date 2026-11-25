import { useEffect, useRef, useState } from 'react';
import { api, type LocalDiscussion, type LocalDiscussionPage } from './discussions';

const updatedLabel = new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' });

export function LocalDiscussionHistory({ activeId, activeStatus, activeDiscussion, labels, disabled, onOpen, onDelete }: {
  activeId: string | null;
  activeStatus?: string;
  activeDiscussion?: LocalDiscussion;
  labels: Record<string, string>;
  disabled: boolean;
  onOpen: (discussion: LocalDiscussion) => void;
  onDelete: (discussion: LocalDiscussion) => Promise<void>;
}) {
  const [page, setPage] = useState<LocalDiscussionPage>({ items: [], next_cursor: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [deleteError, setDeleteError] = useState('');
  const request = useRef<AbortController | null>(null);

  async function load(cursor?: string) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError('');
    const query = new URLSearchParams({ limit: '20' });
    if (cursor) query.set('cursor', cursor);
    try {
      const next = await api<LocalDiscussionPage>(`/discussions?${query}`, undefined, undefined, controller.signal);
      if (controller.signal.aborted) return;
      setPage((current) => ({
        ...next,
        items: cursor ? [
          ...current.items,
          ...next.items.filter((item) => !current.items.some((old) => old.id === item.id)),
        ] : next.items,
      }));
    } catch {
      if (!controller.signal.aborted) setError('暂时无法读取聊天历史。');
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  useEffect(() => {
    setDeleteError('');
    void load();
    return () => request.current?.abort();
  }, [activeId]);

  async function remove(item: LocalDiscussion) {
    if (!window.confirm(`从聊天历史删除「${item.title}」？`)) return;
    setDeleteError('');
    try {
      await onDelete(item);
      setPage((current) => ({ ...current, items: current.items.filter((discussion) => discussion.id !== item.id) }));
    } catch {
      setDeleteError('删除失败，请重试。');
    }
  }

  const items = activeDiscussion && !page.items.some((item) => item.id === activeDiscussion.id)
    ? [activeDiscussion, ...page.items] : page.items;

  return <details className="local-discussions" open>
    <summary>聊天历史</summary>
    {items.length > 0 && <ul>{items.map((item) => {
      const current = activeId === item.id;
      const status = current && activeStatus ? activeStatus : item.status;
      return <li className="history-row" key={item.id}>
        <button className="history-open" type="button" disabled={disabled} aria-current={current ? 'true' : undefined}
          onClick={() => { if (!current) onOpen(item); }}>
          <span className="history-title" title={item.title}>{item.title}</span>
          <span className="history-meta">{labels[status] ?? status}{item.updated_at && <time dateTime={item.updated_at}>
            {updatedLabel.format(new Date(item.updated_at))}
          </time>}</span>
        </button>
        {current && <button className="history-delete" type="button" disabled={disabled}
          aria-label={`删除聊天：${item.title}`} onClick={() => void remove(item)}>删除</button>}
      </li>;
    })}</ul>}
    {loading && items.length === 0 && <p role="status">正在读取历史…</p>}
    {!loading && !error && items.length === 0 && <p>还没有历史讨论。</p>}
    {deleteError && <p role="alert">{deleteError}</p>}
    {error && <p role="alert">{error} <button type="button" onClick={() => void load()}>重试</button></p>}
    {page.next_cursor && !error && <button className="history-more" type="button" disabled={loading}
      onClick={() => void load(page.next_cursor!)}>{loading ? '读取中…' : '查看更多'}</button>}
  </details>;
}
