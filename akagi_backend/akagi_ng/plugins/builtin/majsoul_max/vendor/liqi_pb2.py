from __future__ import annotations

from contextvars import ContextVar

from google.protobuf.message import Message

from akagi_ng.bridge.majsoul.liqi import LiqiProto

_current_proto: ContextVar[LiqiProto | None] = ContextVar("majsoul_max_liqi_proto", default=None)


def set_lqi_proto(proto: LiqiProto) -> None:
    _current_proto.set(proto)


def clear_lqi_proto() -> None:
    _current_proto.set(None)


def __getattr__(name: str) -> type[Message]:
    proto = _current_proto.get()
    if proto is None:
        raise RuntimeError("MajsoulMax Liqi context is not active")
    message_class = proto.get_message_class(name)
    if message_class is None:
        raise AttributeError(name)
    return message_class
