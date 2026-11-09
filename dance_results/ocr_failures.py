"""Bounded, best-effort diagnostics alongside live OCR; never a decision input."""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import time
from uuid import uuid4


def failure_details(observation=None, error=None):
    """Read existing diagnostics without altering fields, confidence or decisions."""
    observation = observation or {}
    result = observation.get('result_ocr') or {}
    fields = (result.get('fields') or observation.get('ocr_fields') or
              (observation.get('selection') or {}).get('fields') or {})
    reasons = []
    if error is not None:
        reasons.append('recognition_exception: ' + type(error).__name__)
    if observation.get('screen_type') in ('unknown', 'error'):
        reasons.append('screen_unrecognized')
    # These are the existing automatic-review confidence requirements. This
    # diagnostic check does not gate or change the monitor's handling of a frame.
    for name, field in fields.items():
        if field.get('value') is None:
            reasons.append(name + ': missing')
        elif field.get('confidence', 0) < .95:
            reasons.append(name + ': low_confidence')
        if field.get('reason'):
            reasons.append(name + ': ' + str(field['reason']))
    reasons.extend(result.get('errors') or [])
    temporal = result.get('temporal_validation') or {}
    if temporal.get('status') == 'pending_review':
        reasons.extend(temporal.get('reasons') or ['temporal_ocr_uncertainty'])
    return {
        'reason': sorted(set(reasons)),
        'screen_type_guess': observation.get('screen_type'),
        'confidence': {name: field.get('confidence') for name, field in fields.items()},
        'fields': {name: field.get('value') for name, field in fields.items()},
    }


class FailureCapture:
    def __init__(self, directory, *, interval=5, duplicate_seconds=30,
                 max_files=500, clock=time.monotonic):
        self.directory = Path(directory)
        self.interval = interval
        self.duplicate_seconds = duplicate_seconds
        self.max_files = max_files
        self.clock = clock
        self.last_attempt = float('-inf')
        self.last_saved = float('-inf')
        self.last_reason = None

    def capture(self, image, observation=None, error=None):
        """At most one PNG/JSON pair per interval; all failures stay local here."""
        try:
            details = failure_details(observation, error)
            if not details['reason'] or image is None:
                return False
            now = self.clock()
            reason = tuple(details['reason'])
            if now - self.last_attempt < self.interval:
                return False
            if reason == self.last_reason and now - self.last_saved < self.duplicate_seconds:
                return False
            # Also rate-limit failed writes (full disk, denied directory, etc.).
            self.last_attempt = now
            self.directory.mkdir(parents=True, exist_ok=True)
            if sum(1 for path in self.directory.iterdir() if path.is_file()) + 2 > self.max_files:
                return False
            timestamp = datetime.now(timezone.utc)
            stem = timestamp.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex
            details['timestamp'] = timestamp.isoformat()
            details['image'] = stem + '.png'
            metadata = json.dumps(details, ensure_ascii=False, indent=2, allow_nan=False)
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            # Serialize both first; exclusively create so existing evidence is
            # never overwritten. Partial writes count toward the same file cap.
            with (self.directory / (stem + '.png')).open('xb') as stream:
                stream.write(buffer.getvalue())
            with (self.directory / (stem + '.json')).open('x', encoding='utf-8') as stream:
                stream.write(metadata + '\n')
            self.last_reason = reason
            self.last_saved = now
            return True
        except Exception:
            return False
