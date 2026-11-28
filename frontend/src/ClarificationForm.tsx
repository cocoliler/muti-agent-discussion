import { useId, useRef, useState } from 'react';
import { formatInlineText } from './DiscussionMessageText';
import type { ClarificationAnswer, InitialQuestionDetail } from './discussions';

function suggestedAnswers(question: string) {
  if (/中国大陆|地区|地域|市场范围|分发范围|region/i.test(question)) {
    return ['首版只做内部试用，暂不公开分发', '首版面向中国大陆用户', '首版仅面向中国大陆以外用户', '上线地区尚未确定，先按内部试用讨论'];
  }
  if (/目标人群|用户是谁|谁使用|谁会使用|目标用户|主要用户|target users/i.test(question)) {
    return ['同一组织内的员工或成员', '已有关系的小群体，例如家人、朋友或同学', '有共同需求的行业或兴趣社区', '面向普通用户，先招募小规模试用者'];
  }
  if (/具体动作|核心操作|使用场景|完成什么|如何使用|使用频率|场景是什么/.test(question)) {
    return ['完成一项日常高频操作，优先缩短完成时间', '多人协作，优先减少反复沟通和状态不一致', '查询或整理信息，优先解决难找和重复录入', '先验证一个场景，其他场景留待试用反馈'];
  }
  return ['沿用讨论中已确认的信息，继续推进', '先采用最小可行假设，后续安排验证', '暂不纳入首版，记录为后续需求', '仍需确认，先暂停与此问题相关的决定'];
}

type Answer = { choice: string | null; custom: string };

export function ClarificationForm({ questions, options, details, busy, error, initialAnswers, hidden, onCancel, onSubmit }: {
  questions: string[];
  options?: Record<string, string[]>;
  details?: Record<string, InitialQuestionDetail>;
  busy: boolean;
  error: string;
  initialAnswers?: ClarificationAnswer[];
  hidden?: boolean;
  onCancel?: () => void;
  onSubmit: (text: string, answers: ClarificationAnswer[]) => void;
}) {
  const customFields = useRef<Array<HTMLTextAreaElement | null>>([]);
  const titleId = useId();
  const prompts = questions.length ? questions : ['你想补充什么？'];
  const suggestions = prompts.map((question) => {
    const supplied = options && Object.hasOwn(options, question) ? options[question] : undefined;
    if (details && Object.hasOwn(details, question)) return supplied ?? [];
    return supplied?.length === 4 ? supplied : suggestedAnswers(question);
  });
  const [answers, setAnswers] = useState<Answer[]>(() => prompts.map((_, index) => {
    const answer = initialAnswers?.[index]?.answer;
    if (!answer) return { choice: null, custom: '' };
    return suggestions[index].includes(answer) ? { choice: answer, custom: '' } : { choice: '', custom: answer };
  }));
  const values = answers.map((answer) => answer.choice === '' ? answer.custom.trim() : answer.choice ?? '');
  const complete = values.every(Boolean);
  const text = questions.length
    ? questions.map((prompt, index) => `${index + 1}. ${prompt}\n回答：${values[index]}`).join('\n\n')
    : values[0];
  const tooLong = Array.from(text).length > 8000;

  function updateAnswer(questionIndex: number, update: Partial<Answer>) {
    setAnswers((previous) => previous.map((answer, index) => index === questionIndex ? { ...answer, ...update } : answer));
  }

  return <section className="clarification-card" id={onCancel ? 'clarification-edit' : undefined} hidden={hidden} aria-labelledby={titleId}>
    <h2 id={titleId} tabIndex={onCancel ? -1 : undefined}>{onCancel ? '重新回答' : '补充信息'}</h2>
    {onCancel && <p className="clarification-edit-note">提交后，Agent 会按修改后的答案重新展开讨论。</p>}
    <form className="live-form clarification-form" onSubmit={(event) => {
      event.preventDefault();
      if (complete && !tooLong && !busy) onSubmit(text, prompts.map((question, index) => ({
        question, answer: values[index], options: suggestions[index],
      })));
    }}>
      <div className="clarification-questions">
        {prompts.map((question, index) => {
          const current = answers[index];
          const group = `${titleId}-answer-${index}`;
          return <fieldset className="clarification-options" key={index} disabled={busy}>
            <legend>{prompts.length > 1 && `${index + 1}. `}{formatInlineText(question)}</legend>
            {details?.[question] && <p>{details[question].reason}
              {!!details[question].affected_modules.length && `（影响 ${details[question].affected_modules.join('、')}）`}
            </p>}
            {suggestions[index].map((suggestion, optionIndex) => <label className="clarification-option" key={suggestion}>
              <input type="radio" name={group} checked={current.choice === suggestion}
                onChange={() => updateAnswer(index, { choice: suggestion })} />
              <span>{suggestion}</span>
              {optionIndex === 0 && <small className="clarification-recommended">推荐</small>}
            </label>)}
            <label className="clarification-option">
              <input type="radio" name={group} checked={current.choice === ''} onChange={() => {
                updateAnswer(index, { choice: '' });
                requestAnimationFrame(() => customFields.current[index]?.focus());
              }} />
              <span>自由填写</span>
            </label>
            {current.choice === '' && <textarea ref={(element) => { customFields.current[index] = element; }} rows={2}
              aria-label={`第 ${index + 1} 题的自由回答`} placeholder="输入你的回答" value={current.custom}
              onChange={(event) => updateAnswer(index, { custom: event.target.value })} />}
          </fieldset>;
        })}
      </div>
      {(tooLong || error) && <p className="clarification-error" role="alert">{tooLong ? '总长度超过 8,000 字，请缩短回答。' : error}</p>}
      <div className="action-row clarification-actions">
        {onCancel && <button type="button" disabled={busy} onClick={onCancel}>取消修改</button>}
        <button className="primary" disabled={busy || !complete || tooLong}>
          {busy ? '提交中…' : onCancel ? '重新提交' : '提交并继续'}
        </button>
      </div>
    </form>
  </section>;
}
