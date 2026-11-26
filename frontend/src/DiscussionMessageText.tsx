import type { ReactNode } from 'react';

const headings = [
  '首版架构建议', '架构建议', '首版范围', '核心建议', '主要建议', '建议', '结论', '优先级',
  '成本假设', '成本估算', '技术约束', '主要约束', '关键约束', '依赖', '故障场景', '失败场景',
  '风险与取舍', '关键风险', '主要风险', '风险', '取舍', '边界', '处理方式', '降级方案',
  '目标用户', '用户场景', '使用场景', '用户痛点', '研究发现', '研究建议', '依据', '假设',
  '用户旅程', '交互建议', '设计建议', '核心交互', '空状态', '下一步', '验证方法', '验收条件',
  '商业建议', '商业分析', '验证计划', '待确认', '待补充',
];

const sectionPattern = new RegExp(
  String.raw`(?:^|(?<=[。！？；;]))[ \t]*(?:\*\*(?<boldLabel>[^*\r\n]{1,24})\*\*[：:]|(?:#{2,4}[ \t]+)?(?<plainLabel>${headings.join('|')})[：:])`
    + String.raw`|^[ \t]*(?:\*\*(?<boldLine>[^*\r\n]+)\*\*[ \t]*[：:]?|#{1,6}[ \t]+(?<markdownHeading>[^\r\n]+?))[ \t]*(?=\r?$)`,
  'gm',
);

function sections(text: string) {
  const matches = [...text.matchAll(sectionPattern)];
  return [
    { heading: '', text: text.slice(0, matches[0]?.index ?? text.length) },
    ...matches.map((match, index) => {
      const { boldLabel, plainLabel, boldLine, markdownHeading } = match.groups!;
      return {
        heading: (boldLabel ?? plainLabel ?? boldLine ?? markdownHeading).trim(),
        text: text.slice(match.index! + match[0].length, matches[index + 1]?.index ?? text.length),
      };
    }),
  ].filter((section) => section.heading || section.text.trim());
}

function paragraphs(text: string) {
  const sentences = text.match(/(?:\*\*[^*\n]+\*\*|[^。！？；])+[。！？；]*|[。！？；]+/g) ?? [];
  const result: string[] = [];
  let line = '';
  for (const sentence of sentences) {
    if (line.trim().length >= 95 && (line + sentence).trim().length > 155) {
      result.push(line.trim());
      line = '';
    }
    line += sentence;
  }
  if (line.trim()) result.push(line.trim());
  return result;
}

function blocks(text: string) {
  const result: { kind: 'paragraph' | 'ul' | 'ol'; lines: { text: string; number?: number }[] }[] = [];
  for (const line of text.split(/\r?\n/)) {
    const content = line.trim();
    if (!content) continue;
    const bullet = /^[-*•]\s+(.+)$/.exec(content);
    const numbered = /^(\d+)(?:[.)]\s+|、\s*)(.+)$/.exec(content);
    const kind = bullet ? 'ul' : numbered ? 'ol' : 'paragraph';
    const lines = bullet ? [{ text: bullet[1] }]
      : numbered ? [{ text: numbered[2], number: Number(numbered[1]) }]
      : paragraphs(content).map((text) => ({ text }));
    const previous = result.at(-1);
    if (kind !== 'paragraph' && previous?.kind === kind) previous.lines.push(...lines);
    else result.push({ kind, lines });
  }
  return result;
}

export function formatInlineText(text: string) {
  return text.split(/\*\*([^*\n]+)\*\*/).map((part, index) => (
    index % 2 === 0 ? part : <strong key={index}>{part}</strong>
  ));
}

export function DiscussionMessageText({ text, formatted }: { text: string; formatted: boolean }) {
  if (!formatted) return <p>{text}</p>;

  return <div className="discussion-message-text">{sections(text).map((section, sectionIndex) => <div key={sectionIndex}>
    {section.heading && <h3>{formatInlineText(section.heading)}</h3>}
    {blocks(section.text).map(({ kind, lines }, index) => {
      if (kind === 'paragraph') return <div key={index}>{lines.map((line, lineIndex) => <p key={lineIndex}>{formatInlineText(line.text)}</p>)}</div>;
      const List = kind;
      return <List key={index}>{lines.map((line, lineIndex) => <li key={lineIndex} value={line.number}>{formatInlineText(line.text)}</li>)}</List>;
    })}
  </div>)}</div>;
}

export function AgentMessageText({ text, plainSummary, references }: {
  text: string;
  plainSummary?: string | null;
  references?: ReactNode;
}) {
  const clean = text.trim();
  const summary = plainSummary?.trim();
  if ((!summary || summary === clean) && clean.length <= 160 && !references) {
    return <DiscussionMessageText text={text} formatted />;
  }
  const opening = (sections(clean)[0]?.text.trim() || clean).replace(/\*\*/g, '');
  const firstLine = opening.split(/\r?\n/)[0];
  const firstSentence = firstLine.match(/^.+?(?:[。！？!?]|\.(?=\s|$))/)?.[0] ?? firstLine;
  const picked = /^收敛评分：[\s\S]*?。采纳 ([^。]+)。/.exec(clean)?.[1];
  const pickedTitle = picked && clean.split(picked + '「')[1]?.split('」')[0];
  const preview = summary || (pickedTitle ? `本轮暂选「${pickedTitle}」。` : clean.length <= 160 ? clean : firstSentence);
  const limit = Array.from(preview);
  const excerpt = limit.length > 160 ? limit.slice(0, 157).join('') + '…' : preview;
  const hasMoreText = excerpt !== clean;
  return <>
    <p className="agent-message-preview">{formatInlineText(excerpt)}</p>
    {(hasMoreText || references) && <details className="agent-message-details">
      <summary>{hasMoreText ? '查看发言详情' : '查看引用与关联'}</summary>
      {hasMoreText && <DiscussionMessageText text={text} formatted />}
      {references}
    </details>}
  </>;
}
