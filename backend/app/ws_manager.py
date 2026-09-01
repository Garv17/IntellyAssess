"""In-process registry of Live Monitor WebSocket connections, grouped by exam_id.

Each of the 4 uvicorn worker processes has its own instance of this registry — it
only knows about sockets accepted by *this* process. Cross-worker fan-out is handled
separately by backend/app/pubsub.py via Redis pub/sub.
"""

from __future__ import annotations

from fastapi import WebSocket

from app.logging_config import get_logger

log = get_logger(__name__)


class LiveMonitorConnections:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}

    async def connect(self, exam_id: str, ws: WebSocket) -> None:
        self._rooms.setdefault(exam_id, set()).add(ws)
        log.info(
            "live monitor socket connected",
            extra={"exam_id": exam_id, "room_size": len(self._rooms[exam_id])},
        )

    def disconnect(self, exam_id: str, ws: WebSocket) -> None:
        room = self._rooms.get(exam_id)
        if room is None:
            return
        room.discard(ws)
        log.info(
            "live monitor socket disconnected",
            extra={"exam_id": exam_id, "room_size": len(room)},
        )
        if not room:
            del self._rooms[exam_id]

    def has_connections(self, exam_id: str) -> bool:
        return bool(self._rooms.get(exam_id))

    async def broadcast(self, exam_id: str, payload: dict) -> None:
        room = self._rooms.get(exam_id)
        if not room:
            return
        dead: list[WebSocket] = []
        errors: list[str] = []
        for ws in list(room):
            try:
                await ws.send_json(payload)
            except Exception as exc:
                # Expected on a closed tab, so this is not an error — but it was
                # previously invisible, which made "the dashboard stopped
                # updating" impossible to tell from "nothing changed".
                # Collected rather than logged per socket: one broadcast to a
                # room whose browsers all went away would otherwise emit a line
                # each, all saying the same thing.
                errors.append(type(exc).__name__)
                dead.append(ws)
        if dead:
            log.info(
                "dropped dead live monitor sockets",
                extra={
                    "exam_id": exam_id,
                    "dropped": len(dead),
                    "remaining": len(room) - len(dead),
                    "error_types": sorted(set(errors)),
                },
            )
        for ws in dead:
            self.disconnect(exam_id, ws)


live_connections = LiveMonitorConnections()
