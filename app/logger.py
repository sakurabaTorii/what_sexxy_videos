# -*- coding: utf-8 -*-
"""
アプリ用の簡易ログ（NDJSON: 1行=1JSON）。

- 例外が起きてもアプリ本体の動作を妨げない
- 秘密情報（APIキー等）は書かない
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _default_log_path() -> str:
    """
    環境変数で上書き可能。

    未設定の場合は「このリポジトリ直下」の logs/app.ndjson に固定する。
    （python gui.py をどのカレントディレクトリから実行しても同じ場所に出るようにする）
    """
    env = os.environ.get("APP_LOG_PATH")
    if env:
        return env
    repo_root = Path(__file__).resolve().parents[1]  # app/logger.py -> repo root
    return str(repo_root / "logs" / "app.ndjson")


def log_event(event: str, data: dict[str, Any] | None = None) -> None:
    """
    event: 短いイベント名（例: frame_result_empty）
    data: 付随情報（PIIや秘密情報は入れない）
    """
    payload = {
        "ts": int(time.time() * 1000),
        "event": event,
        "data": data or {},
    }
    path = _default_log_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # ログ失敗はアプリ本体に影響させないが、原因究明のため stderr にだけ出す（秘密情報は含めない）
        try:
            import sys
            print(f"[app-log] failed to write: {path} event={event}", file=sys.stderr)
        except Exception:
            pass
        return

