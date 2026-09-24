from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

SAFE_INT = (1 << 53) - 1
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
OPERATION_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class ProtocolError(ValueError):
    pass


def _reject_float(value: str):
    raise ProtocolError(f"floating point values are not allowed: {value}")


def _reject_constant(value: str):
    raise ProtocolError(f"non-finite number is not allowed: {value}")


def _object_no_dupes(pairs):
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ProtocolError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _validate_value(value: Any) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > SAFE_INT:
            raise ProtocolError("integer outside protocol-safe range; encode large IDs as strings")
        return
    if isinstance(value, list):
        for item in value:
            _validate_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("JSON object keys must be strings")
            _validate_value(item)
        return
    raise ProtocolError(f"unsupported JSON value: {type(value).__name__}")


def strict_json_loads(text: str) -> Any:
    try:
        value = json.loads(
            text,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_no_dupes,
        )
    except ProtocolError:
        raise
    except Exception as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    _validate_value(value)
    return value


def _json_string(value: str) -> bytes:
    out = bytearray(b'"')
    for ch in value:
        cp = ord(ch)
        if ch == '"':
            out.extend(b'\\"')
        elif ch == "\\":
            out.extend(b"\\\\")
        elif ch == "\b":
            out.extend(b"\\b")
        elif ch == "\t":
            out.extend(b"\\t")
        elif ch == "\n":
            out.extend(b"\\n")
        elif ch == "\f":
            out.extend(b"\\f")
        elif ch == "\r":
            out.extend(b"\\r")
        elif cp < 0x20:
            out.extend(f"\\u{cp:04x}".encode("ascii"))
        else:
            out.extend(ch.encode("utf-8"))
    out.extend(b'"')
    return bytes(out)


def _utf16_sort_key(value: str) -> tuple[int, ...]:
    raw = value.encode("utf-16-be")
    return tuple(int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2))


def canonical_json_bytes(value: Any) -> bytes:
    _validate_value(value)
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value).encode("ascii")
    if isinstance(value, str):
        return _json_string(value)
    if isinstance(value, list):
        return b"[" + b",".join(canonical_json_bytes(item) for item in value) + b"]"
    if isinstance(value, dict):
        parts = []
        for key in sorted(value, key=_utf16_sort_key):
            parts.append(_json_string(key) + b":" + canonical_json_bytes(value[key]))
        return b"{" + b",".join(parts) + b"}"
    raise ProtocolError(f"unsupported JSON value: {type(value).__name__}")


def digest_object(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def encode_payload_b64(value: Any) -> str:
    return base64.b64encode(canonical_json_bytes(value)).decode("ascii")


def decode_payload_b64(value: str) -> Any:
    try:
        raw = base64.b64decode(value, validate=True)
        text = raw.decode("utf-8")
    except Exception as exc:
        raise ProtocolError(f"invalid base64/UTF-8 payload: {exc}") from exc
    return strict_json_loads(text)


def validate_request_id(value: str) -> str:
    if not REQUEST_ID_RE.fullmatch(value):
        raise ProtocolError("invalid request_id")
    return value


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex


@dataclass(frozen=True)
class OperationEnvelope:
    operation: str
    input: dict[str, Any]
    preconditions: dict[str, Any]
    actor_claim: str | None = None

    def as_payload(self) -> dict[str, Any]:
        if not OPERATION_RE.fullmatch(self.operation):
            raise ProtocolError("invalid operation name")
        _validate_value(self.input)
        _validate_value(self.preconditions)
        payload: dict[str, Any] = {
            "kind": "operation_request",
            "schema_version": 1,
            "operation": self.operation,
            "input": self.input,
            "preconditions": self.preconditions,
        }
        if self.actor_claim is not None:
            if not isinstance(self.actor_claim, str) or not self.actor_claim:
                raise ProtocolError("actor_claim must be a non-empty string")
            payload["actor_claim"] = self.actor_claim
        return payload


def build_operation_payload(
    operation: str,
    input_value: dict[str, Any],
    *,
    preconditions: dict[str, Any] | None = None,
    actor_claim: str | None = None,
) -> dict[str, Any]:
    return OperationEnvelope(
        operation=operation,
        input=input_value,
        preconditions=preconditions or {},
        actor_claim=actor_claim,
    ).as_payload()
