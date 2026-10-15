"""The worker's id and secret, kept across restarts so the controller sees the same worker."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Identity:
    worker_id: str
    secret: str
    controller_url: str

    @staticmethod
    def path(state_dir: Path) -> Path:
        return state_dir / "identity.json"

    @classmethod
    def load(cls, state_dir: Path) -> Identity | None:
        try:
            return cls(**json.loads(cls.path(state_dir).read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError, TypeError):
            return None

    def save(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        path = self.path(state_dir)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self)), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)  # holds the worker secret
        except OSError:
            pass
        os.replace(tmp, path)
