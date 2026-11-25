"""权衡分歧的收敛打分：五维加权、确定性排名。

打分只回答「多个备选方案里先做哪个」，不判断正文写得好不好，也不进入交付契约。
所有维度的原始分都是 0-5，且统一按「越高越好」记分：成本维度记「成本可控程度」，
风险维度记「风险可控程度」，因此权重全部正向。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .enums import DEFAULT_SCORE_WEIGHTS, ReasonCode, ScoreDimension
from .models import OptionScore

#: 允许的方案数量区间：少于两个不需要比较，过多说明议题还没收敛。
MIN_OPTIONS = 2
MAX_OPTIONS = 8
MIN_SCORE = 0
MAX_SCORE = 5


@dataclass(frozen=True)
class ScoredOption:
    """加权后的方案得分。``rank`` 从 1 开始，1 为最优。"""

    option_id: str
    title: str
    scores: Mapping[ScoreDimension, int]
    total: float
    rank: int


def normalize_weights(weights: Mapping[ScoreDimension, float] | None) -> dict[ScoreDimension, float] | None:
    """缺省取默认权重；提供时按默认值补齐并归一化。非法输入返回 None。"""

    merged = dict(DEFAULT_SCORE_WEIGHTS)
    if weights:
        for dimension, value in weights.items():
            if dimension not in DEFAULT_SCORE_WEIGHTS:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            if not 0 <= float(value) <= 1:
                return None
            merged[dimension] = float(value)
    total = sum(merged.values())
    if total <= 0:
        return None
    return {dimension: round(value / total, 6) for dimension, value in merged.items()}


def validate(options: tuple[OptionScore, ...], weights: Mapping[ScoreDimension, float] | None = None) -> tuple[ReasonCode, str] | None:
    """校验评分卡是否可用于排名；返回第一个问题。"""

    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        return (
            ReasonCode.INVALID_SCORECARD,
            f"方案数量需在 {MIN_OPTIONS}-{MAX_OPTIONS} 之间，当前 {len(options)} 个",
        )
    ids: set[str] = set()
    for index, option in enumerate(options):
        if not option.option_id.strip():
            return (ReasonCode.INVALID_SCORECARD, f"options/{index}/option_id 不能为空")
        if option.option_id in ids:
            return (ReasonCode.INVALID_SCORECARD, f"options/{index}/option_id 重复：{option.option_id}")
        ids.add(option.option_id)
        if not option.title.strip():
            return (ReasonCode.INVALID_SCORECARD, f"options/{index}/title 不能为空")
        missing = [dimension.value for dimension in ScoreDimension if dimension not in option.scores]
        extra = [str(key) for key in option.scores if key not in DEFAULT_SCORE_WEIGHTS]
        if missing or extra:
            return (
                ReasonCode.INVALID_SCORECARD,
                f"options/{index}/scores 必须且只能包含五个维度（缺少 {missing}，多余 {extra}）",
            )
        for dimension, value in option.scores.items():
            if isinstance(value, bool) or not isinstance(value, int) or not MIN_SCORE <= value <= MAX_SCORE:
                return (
                    ReasonCode.INVALID_SCORECARD,
                    f"options/{index}/scores/{dimension.value} 需为 {MIN_SCORE}-{MAX_SCORE} 的整数",
                )
    if normalize_weights(weights) is None:
        return (ReasonCode.INVALID_SCORECARD, "weights 只能是五个维度的 0-1 数值")
    return None


def rank_options(
    options: tuple[OptionScore, ...],
    weights: Mapping[ScoreDimension, float] | None = None,
) -> tuple[ScoredOption, ...]:
    """按加权总分降序排名；同分先看用户价值，再按 option_id 保证确定性。"""

    normalized = normalize_weights(weights) or dict(DEFAULT_SCORE_WEIGHTS)
    scored = [
        ScoredOption(
            option_id=option.option_id,
            title=option.title,
            scores=dict(option.scores),
            total=round(
                sum(normalized[dimension] * option.scores[dimension] for dimension in ScoreDimension),
                2,
            ),
            rank=0,
        )
        for option in options
    ]
    scored.sort(
        key=lambda item: (
            -item.total,
            -item.scores[ScoreDimension.USER_VALUE],
            item.option_id,
        )
    )
    return tuple(
        ScoredOption(
            option_id=item.option_id,
            title=item.title,
            scores=item.scores,
            total=item.total,
            rank=index + 1,
        )
        for index, item in enumerate(scored)
    )
