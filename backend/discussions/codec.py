"""编解码层：引擎 dataclass <-> JSON。

规则（实施方案 §5）：

* 枚举按 ``.value`` 编码，按值还原；未知值抛 :class:`CodecError`。
* ``tuple`` -> JSON 数组 -> ``tuple``；``set`` -> 排序数组 -> ``set``。
  必须还原为不可变容器，否则 frozen dataclass 的 ``==`` / ``hash`` 会静默失效。
* ``dict`` 的枚举键按值编码，还原为枚举（如 ``"architect"`` -> ``Role.ARCHITECT``）。
* 缺失字段取 dataclass 默认值；无默认值则抛错（成员 1 增字段时前向兼容）。
* 未知字段收集到对象的 ``_codec_extra`` 属性，回写时原样带出，不丢数据。

本模块是唯一允许反射引擎模型的文件，字段变更的影响面收敛于此。
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from collections.abc import Mapping, MutableMapping
from enum import Enum
from typing import Any, get_args, get_origin, get_type_hints

CODEC_VERSION = 1

_EXTRA_ATTR = "_codec_extra"
_UNION_ORIGINS = {typing.Union, types.UnionType}


class CodecError(ValueError):
    """编解码失败，``path`` 指出出错字段。"""

    def __init__(self, message: str, path: str = "") -> None:
        super().__init__(f"{path}: {message}" if path else message)
        self.path = path


def _join(path: str, key: Any) -> str:
    return f"{path}.{key}" if path else str(key)


# --------------------------------------------------------------------------- 编码


def _encode_key(key: Any) -> str:
    if isinstance(key, Enum):
        return str(key.value)
    if isinstance(key, str):
        return key
    if isinstance(key, (int, float, bool)):
        return str(key)
    raise CodecError(f"不支持的键类型 {type(key).__name__}")


def _sort_key(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def encode(value: Any, path: str = "") -> Any:
    """把引擎对象转换为可 JSON 化的结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {}
        for field in dataclasses.fields(value):
            out[field.name] = encode(getattr(value, field.name), _join(path, field.name))
        extra = getattr(value, _EXTRA_ATTR, None)
        if isinstance(extra, dict):
            for key, raw in extra.items():
                out.setdefault(key, raw)
        return out
    if isinstance(value, dict):
        return {_encode_key(k): encode(v, _join(path, _encode_key(k))) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item, _join(path, index)) for index, item in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        return [encode(item, _join(path, index)) for index, item in enumerate(sorted(value, key=_sort_key))]
    raise CodecError(f"不支持编码的类型 {type(value).__name__}", path)


# --------------------------------------------------------------------------- 解码


def _hints(cls: type) -> dict[str, Any]:
    """解析类型注解；解析不了就报错 —— 静默降级会导致字段在无提示的情况下退化为 JSON 原值。"""
    try:
        return get_type_hints(cls)
    except Exception as exc:  # noqa: BLE001
        raise CodecError(f"无法解析 {cls.__name__} 的类型注解：{exc}") from exc


def _decode_union(args: tuple[Any, ...], value: Any, path: str) -> Any:
    if value is None and type(None) in args:
        return None
    errors: list[str] = []
    for arg in args:
        if arg is type(None):
            continue
        try:
            return decode(arg, value, path)
        except CodecError as exc:  # 尝试下一个分支
            errors.append(str(exc))
    raise CodecError(f"无法匹配联合类型（{'; '.join(errors) or '无候选'}）", path)


def _decode_dataclass(cls: type, value: Any, path: str) -> Any:
    if not isinstance(value, dict):
        raise CodecError(f"{cls.__name__} 需要对象，收到 {type(value).__name__}", path)

    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    known = {field.name for field in dataclasses.fields(cls)}

    for field in dataclasses.fields(cls):
        if field.name in value:
            kwargs[field.name] = decode(hints.get(field.name, Any), value[field.name], _join(path, field.name))
        elif field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING:
            continue
        else:
            raise CodecError("缺少必填字段", _join(path, field.name))

    for key, raw in value.items():
        if key not in known:
            unknown[key] = raw

    try:
        obj = cls(**kwargs)
    except TypeError as exc:
        raise CodecError(f"构造 {cls.__name__} 失败：{exc}", path) from exc

    if unknown and not hasattr(type(obj), "__slots__"):
        object.__setattr__(obj, _EXTRA_ATTR, unknown)
    return obj


def decode(target: Any, value: Any, path: str = "") -> Any:
    """按目标类型把 JSON 结构还原为引擎对象。"""
    if target is Any or target is None or target is typing.Any:
        return value
    origin = get_origin(target)
    args = get_args(target)

    if origin in _UNION_ORIGINS:
        return _decode_union(args, value, path)
    if value is None:
        if target is type(None):
            return None
        raise CodecError("不能为 null", path)
    if origin in (list, typing.List):  # noqa: UP006 - 兼容 typing.List
        item_type = args[0] if args else Any
        if not isinstance(value, list):
            raise CodecError("需要数组", path)
        return [decode(item_type, item, _join(path, index)) for index, item in enumerate(value)]
    if origin in (tuple, typing.Tuple):  # noqa: UP006
        if not isinstance(value, list):
            raise CodecError("需要数组", path)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(decode(args[0], item, _join(path, index)) for index, item in enumerate(value))
        if not args:
            return tuple(value)
        if len(args) != len(value):
            raise CodecError(f"定长元组长度不符（期望 {len(args)}，实际 {len(value)}）", path)
        return tuple(decode(arg, item, _join(path, index)) for index, (arg, item) in enumerate(zip(args, value)))
    if origin in (set, frozenset, typing.Set, typing.FrozenSet):  # noqa: UP006
        item_type = args[0] if args else Any
        if not isinstance(value, list):
            raise CodecError("需要数组", path)
        return {decode(item_type, item, _join(path, index)) for index, item in enumerate(value)}
    if origin in (dict, Mapping, MutableMapping):
        key_type, value_type = (args + (Any, Any))[:2] if args else (Any, Any)
        if not isinstance(value, dict):
            raise CodecError("需要对象", path)
        out: dict[Any, Any] = {}
        for raw_key, raw_value in value.items():
            key = decode(key_type, raw_key, _join(path, raw_key))
            out[key] = decode(value_type, raw_value, _join(path, raw_key))
        return out
    if isinstance(target, type):
        if issubclass(target, Enum):
            try:
                return target(value)
            except ValueError as exc:
                raise CodecError(f"{target.__name__} 不接受取值 {value!r}", path) from exc
        if dataclasses.is_dataclass(target):
            return _decode_dataclass(target, value, path)
        if target is bool:
            if isinstance(value, bool):
                return value
            raise CodecError("需要布尔值", path)
        if target is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise CodecError("需要整数", path)
            return value
        if target is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CodecError("需要数字", path)
            return float(value)
        if target is str:
            if not isinstance(value, str):
                raise CodecError("需要字符串", path)
            return value

    return value


# --------------------------------------------------------------------------- 信封


def dumps(obj: Any) -> str:
    """带版本信封的序列化，供 ``session_state`` 落库。"""
    payload = {"codec_version": CODEC_VERSION, "data": encode(obj)}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(raw: str, target: type) -> Any:
    """按信封版本反序列化；版本高于本代码支持时拒绝。"""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodecError(f"状态不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict) or "data" not in payload:
        raise CodecError("状态缺少 codec 信封")
    version = payload.get("codec_version", CODEC_VERSION)
    if not isinstance(version, int) or version > CODEC_VERSION:
        raise CodecError(f"不支持的 codec_version={version!r}（本代码支持 {CODEC_VERSION}）")
    return decode(target, payload["data"])


# --------------------------------------------------------------------------- 引擎适配


def _engine() -> Any:
    """惰性导入引擎包，保证本模块可在未安装引擎时被静态导入。"""
    try:
        import discussion_orchestration as pkg
    except ImportError as exc:  # pragma: no cover - 仓库内总是存在
        raise CodecError("未找到 discussion_orchestration 包，请确认已在仓库根目录运行") from exc
    return pkg


def dump_session(session: Any) -> str:
    return dumps(session)


def load_session(raw: str) -> Any:
    return loads(raw, _engine().DiscussionSession)


def dump_output(output: Any) -> str:
    return dumps(output)


def load_output(raw: str) -> Any:
    return loads(raw, _engine().SpeakerOutput)


def dump_message_row(message: Any, session_id: str, created_at: str) -> tuple:
    """投影表行：字段顺序与 schema.sql 的 discussion_messages 一致。

    使用 ``getattr`` 兜底，引擎给 ``Message`` 增删可选字段时无需改本函数。
    """
    return (
        session_id,
        message.seq,
        message.role.value,
        message.kind.value,
        message.text,
        getattr(message, "topic_id", None),
        getattr(message, "task_id", None),
        getattr(message, "reply_to_seq", None),
        json.dumps(list(getattr(message, "source_seqs", ())), ensure_ascii=False),
        json.dumps(list(getattr(message, "item_refs", ())), ensure_ascii=False),
        json.dumps(list(getattr(message, "issue_ids", ())), ensure_ascii=False),
        getattr(message, "input_version", 1),
        getattr(message, "round_no", 0),
        created_at,
        json.dumps({
            key: value for key in (
                "basis", "verification", "agreements", "alternatives", "disagreements", "assumptions", "risks",
            ) if (value := getattr(message, key, None))
        } | ({"substantive": False} if getattr(message, "substantive", True) is False else {}), ensure_ascii=False),
    )
