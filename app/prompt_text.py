# -*- coding: utf-8 -*-
"""
プロンプト本文は app/prompts/ 以下のテキスト／JSON を編集する。
コード側はプレースホルダ差し替えのみ行う。
"""
from __future__ import annotations

import json
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class PromptFileError(FileNotFoundError):
    """プロンプトファイルが見つからない。"""


def _read_text(name: str) -> str:
    path = _PROMPTS_DIR / name
    if not path.is_file():
        raise PromptFileError(f"プロンプトファイルがありません: {path}")
    return path.read_text(encoding="utf-8")


def get_report_template_ja() -> str:
    """JSON から整形するときの日本語レポート雛形（{character} 等のプレースホルダ付き）。"""
    return _read_text("report_template_ja.txt")


def render_frame_analysis_prompt(position_reference: str) -> str:
    """フレーム画像解析用（ビジョンモデル向け）。"""
    body = _read_text("frame_analysis_ja.txt")
    return body.replace("__POSITION_REFERENCE__", (position_reference or "").strip())


def render_translate_to_ja_prompt(english_summary: str) -> str:
    """英語要約を日本語へ（第2段階）。"""
    body = _read_text("translate_to_ja.txt")
    return body.replace("__ENGLISH_SUMMARY__", english_summary or "")


def render_summary_prompt(persona_instruction: str, combined_frame_results: str) -> str:
    """各フレーム解析結果から総評を出す。"""
    body = _read_text("summary_from_frames.txt")
    return (
        body.replace("__PERSONA_INSTRUCTION__", (persona_instruction or "").strip())
        .replace("__COMBINED_FRAME_RESULTS__", combined_frame_results or "")
    )


def load_summary_personas() -> dict[str, str]:
    """
    総評の人格キー → 指示文。
    app/prompts/summary_personas.json を編集する。
    """
    path = _PROMPTS_DIR / "summary_personas.json"
    if not path.is_file():
        raise PromptFileError(f"summary_personas.json がありません: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("summary_personas.json は空でないオブジェクトである必要があります")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip():
            out[k.strip()] = v.strip()
    if not out:
        raise ValueError("summary_personas.json に有効なエントリがありません")
    return out
