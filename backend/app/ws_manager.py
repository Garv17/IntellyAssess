"""In-process registry of Live Monitor WebSocket connections, grouped by exam_id.

Each of the 4 uvicorn worker processes has its own instance of this registry — it
only knows about sockets accepted by *this* process. Cross-worker fan-out is handled
separately by backend/app/pubsub.py via Redis pub/sub.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

log = logging.getLogger(__name__)


class LiveMonitorConnections:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}

    async def connect(self, exam_id: str, ws: WebSocket) -> None:
        self._rooms.setdefault(exam_id, set()).add(ws)

    def disconnect(self, exam_id: str, ws: WebSocket) -> None:
        room = self._rooms.get(exam_id)
        if room is None:
            return
        room.discard(ws)
        if not room:
            del self._rooms[exam_id]

    def has_connections(self, exam_id: str) -> bool:
        return bool(self._rooms.get(exam_id))

    async def broadcast(self, exam_id: str, payload: dict) -> None:
        room = self._rooms.get(exam_id)
        if not room:
            return
        dead: list[WebSocket] = []
        for ws in list(room):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(exam_id, ws)


live_connections = LiveMonitorConnections()
