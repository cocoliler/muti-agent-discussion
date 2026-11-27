import type { Finding, Requirement } from './discussions';

export function ReviewFindings({ findings, requirements, openRequirement }: {
  findings: Finding[];
  requirements?: Requirement[];
  openRequirement?: (id: string) => void;
}) {
  return <ol className="review-findings">{findings.map((finding, index) => {
    const target = finding.target_item;
    const explanation = (finding.summary ?? finding.message)?.trim();
    const condition = finding.close_condition?.trim();
    return <li className="review-finding" key={finding.finding_id ?? index}>
      {target && (openRequirement && requirements?.some((item) => item.id === target)
        ? <button type="button" onClick={() => openRequirement(target)}>{target}</button>
        : <strong>{target}</strong>)}
      {explanation && explanation !== condition && <p>{explanation}</p>}
      {condition && <p><strong>完成条件</strong> · {condition}</p>}
    </li>;
  })}</ol>;
}
