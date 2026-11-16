"""Shared last-used local player name; runtime JSON, never catalog metadata."""
import json
import os
from pathlib import Path
import tempfile
import threading

from .library_service import LibraryError


class PlayerSettings:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def get(self):
        with self.lock:
            if not self.path.exists():
                return 'TC'
            return json.loads(self.path.read_text(encoding='utf8'))['player_name']

    def set(self, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise LibraryError('本人暱稱須為 1–64 字。')
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf8',
                    dir=self.path.parent, prefix='player-settings-', suffix='.tmp', delete=False) as temporary:
                json.dump(dict(player_name=name.strip()), temporary, ensure_ascii=False, indent=2)
            try:
                os.replace(temporary.name, self.path)
            finally:
                Path(temporary.name).unlink(missing_ok=True)
        return self.get()
