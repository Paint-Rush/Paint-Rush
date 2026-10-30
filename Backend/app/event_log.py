## 인게임 이벤트 로그 기록
## jsonl 형식으로 기록

import json
import logging
import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / "logs" / "matches"
CLOSE_TIMEOUT = 5.0

logger = logging.getLogger(__name__)
pending: queue.SimpleQueue = queue.SimpleQueue()
sequences: dict[uuid.UUID, int] = {}
lock = threading.Lock()
writer: threading.Thread | None = None


def log_path(room_id: uuid.UUID) -> Path:
    return LOG_DIR / f"{uuid.UUID(str(room_id))}.jsonl"


def write_pending() -> None:
    stop = False
    while not stop:
        items = [pending.get()]
        while True:
            try:
                items.append(pending.get_nowait())
            except queue.Empty:
                break
        batches = {}
        for item in items:
            if item is None:
                stop = True
            else:
                batches.setdefault(item[0], []).append(item[1])
        for path, lines in batches.items():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write("".join(lines))
            except Exception:
                logger.exception("event log write failed: %s", path)


def log_event(room_id: uuid.UUID, event_type: str, data: dict | None = None) -> None:
    global writer
    try:
        with lock:
            sequence = sequences.get(room_id, 0) + 1
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "room_id": str(room_id),
                "sequence": sequence,
                "type": event_type,
                "data": data or {},
            }
            line = json.dumps(record, ensure_ascii=False) + "\n"
            path = log_path(room_id)
            sequences[room_id] = sequence
            if writer is None:
                writer = threading.Thread(target=write_pending, name="event-log-writer", daemon=True)
                writer.start()
            pending.put((path, line))
    except Exception:
        logger.exception("event log failed: %s %s", room_id, event_type)


def forget(room_id: uuid.UUID) -> None:
    with lock:
        sequences.pop(room_id, None)


def close(timeout: float = CLOSE_TIMEOUT) -> bool:
    global writer
    with lock:
        thread, writer = writer, None
        if thread:
            pending.put(None)
    if thread is None:
        return True
    thread.join(timeout)
    if thread.is_alive():
        logger.error("event log writer did not finish within %.1fs", timeout)
        return False
    return True
