"""Bounded, process-local receipts for committed plays from either import path."""
from collections import deque
import threading


class RecordEventFeed:
    """Keep record IDs only; startup never reconstructs events from saved plays."""

    def __init__(self, capacity=20):
        self._records = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def publish(self, record_id):
        with self._lock:
            if record_id not in self._records:
                self._records.append(record_id)

    def snapshot(self):
        with self._lock:
            return list(self._records)
