"""Small newline-delimited JSON protocol shared by Mutilate peers."""

from __future__ import annotations

import json
import socket
from typing import Any


SYNC_PORT = 19876
MAX_MESSAGE_BYTES = 1024 * 1024

MSG_READY = "CLIENT_READY"
MSG_STAGE_START = "STAGE_START"
MSG_STAGE_END = "STAGE_END"
MSG_PERF_DATA = "PERF_DATA"
MSG_ALL_DONE = "ALL_DONE"
MSG_ACK = "ACK"
MSG_ERROR = "ERROR"


class ProtocolError(RuntimeError):
    """Raised when a peer sends malformed or unexpected protocol data."""


class JsonLineConnection:
    """Send and receive framed JSON objects without losing buffered bytes."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buffer = bytearray()

    def send(self, message_type: str, data: dict[str, Any] | None = None) -> None:
        payload = {"type": message_type, "data": data or {}}
        self.sock.sendall(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")

    def receive(self) -> dict[str, Any]:
        while b"\n" not in self._buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("peer disconnected")
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                raise ProtocolError("protocol message exceeds the 1 MiB limit")

        encoded, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)
        try:
            message = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"invalid JSON message: {exc}") from exc
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ProtocolError("protocol message must contain a string type")
        data = message.get("data", {})
        if not isinstance(data, dict):
            raise ProtocolError("protocol message data must be an object")
        message["data"] = data
        return message
