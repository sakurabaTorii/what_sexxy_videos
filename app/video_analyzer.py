"""動画からキーフレームを抽出し、AIで内容を解析するモジュール"""
import base64
import json
from pathlib import Path

import cv2
from openai import OpenAI

from app.position_reference import get_position_reference_for_prompt
from app.prompt_text import (
    get_report_template_ja,
    load_summary_personas,
    render_frame_analysis_prompt,
    render_summary_prompt,
    render_translate_to_ja_prompt,
)
from app.logger import log_event

# 解析用フレーム: 画質と最低解像度（認識精度向上）
JPEG_QUALITY_ANALYSIS = 95
MIN_SHORT_SIDE_PX = 720  # 短辺がこれ未満なら拡大してから送る（APIの負荷を抑えるため長辺は 1920 まで）


def _frame_to_jpeg_for_analysis(frame) -> bytes:
    """
    解析用にフレームを高画質JPEGに変換する。
    低解像度の場合は短辺を MIN_SHORT_SIDE_PX に合わせて拡大する。
    """
    h, w = frame.shape[:2]
    short = min(h, w)
    long_side = max(h, w)
    # 短辺を MIN_SHORT_PX 以上に（長辺は 1920 を超えないように）
    if short < MIN_SHORT_SIDE_PX:
        scale = MIN_SHORT_SIDE_PX / short
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        if max(new_w, new_h) > 1920:
            scale = 1920 / max(new_w, new_h)
            new_w = int(round(new_w * scale))
            new_h = int(round(new_h * scale))
        frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    ok, buf = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY_ANALYSIS]
    )
    if not ok:
        _, buf = cv2.imencode(".jpg", frame)
    return buf.tobytes()

def _format_as_report(text: str) -> str:
    """
    返答がJSONっぽいときは読みやすい日本語レポートに整形し、
    それ以外はそのまま返す。
    """
    s = (text or "").strip()
    if not s:
        return s
    if not (s.startswith("{") and s.endswith("}")):
        return s
    try:
        obj = json.loads(s)
    except Exception:
        return s

    character = obj.get("character") or obj.get("character_features") or "(記載なし)"
    background = obj.get("background") or obj.get("background_features") or "(記載なし)"
    sexual_presence = obj.get("sexual_presence") or obj.get("sexual_presence_yn") or "(記載なし)"
    sexual_details = obj.get("sexual_details") or "(記載なし)"
    if isinstance(character, list):
        character = "\n".join(str(x) for x in character)
    if isinstance(background, list):
        background = "\n".join(str(x) for x in background)

    return get_report_template_ja().format(
        character=character,
        background=background,
        sexual_presence=sexual_presence,
        sexual_details=sexual_details,
    ).strip()


def extract_key_frames(video_path: str | Path, max_frames: int = 8) -> list[bytes]:
    """
    動画から均等にキーフレームを最大 max_frames 枚抽出する。
    各フレームは JPEG バイト列で返す。
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"動画を開けません: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        raise ValueError("有効なフレーム数が取得できません")

    # 均等サンプリング（旧方式）
    if max_frames is None:
        raise ValueError("max_frames は None を指定できません（下位互換のため）")
    step = max(1, total_frames // max(1, max_frames))
    indices = [min(i * step, total_frames - 1) for i in range(max_frames)]

    frames_jpeg: list[bytes] = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue
        frames_jpeg.append(_frame_to_jpeg_for_analysis(frame))

    cap.release()
    if not frames_jpeg:
        raise ValueError("フレームを1枚も抽出できませんでした")
    return frames_jpeg


def extract_frames_every_n_seconds(
    video_path: str | Path,
    *,
    interval_seconds: float = 10.0,
    max_frames: int | None = 120,
) -> list[bytes]:
    """
    動画の再生時間ベースで interval_seconds ごとにフレームを抽出する。
    長い動画でも過剰に抽出しないよう max_frames で上限を設けられる（None で無制限）。
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"動画を開けません: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if total_frames <= 0:
        cap.release()
        raise ValueError("有効なフレーム数が取得できません")

    # FPS が取れない場合は均等抽出へフォールバック
    if fps <= 0:
        cap.release()
        return extract_key_frames(path, max_frames=8)

    step_frames = max(1, int(round(fps * max(0.1, interval_seconds))))
    indices = list(range(0, total_frames, step_frames))
    if max_frames is not None:
        indices = indices[: max(1, max_frames)]

    frames_jpeg: list[bytes] = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue
        frames_jpeg.append(_frame_to_jpeg_for_analysis(frame))

    cap.release()
    if not frames_jpeg:
        raise ValueError("フレームを1枚も抽出できませんでした")
    return frames_jpeg


def _ollama_client(base_url: str):
    """Ollama の OpenAI 互換 API (/v1/chat/completions) 用クライアント。"""
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    # 公式: base_url は末尾スラッシュ付き (https://docs.ollama.com/api/openai-compatibility)
    if not url.endswith("/"):
        url = url + "/"
    return OpenAI(base_url=url, api_key="ollama")


def _analyze_video_english_only(
    frames_jpeg: list[bytes],
    *,
    base_url: str,
    model: str,
) -> str:
    """
    第1段階のみ実行する: ビジョンモデルで英語の要約を取得し、日本語翻訳は行わず返す。
    GUI からはこの結果を「翻訳前」として一度表示する。
    """
    client = _ollama_client(base_url)

    # 出力は最初から日本語にする（翻訳不要）。4つの見出しと簡潔な記述だけを求める。
    position_ref = get_position_reference_for_prompt()
    prompt_english = render_frame_analysis_prompt(position_ref)
    content: list[dict] = [{"type": "text", "text": prompt_english}]
    for jpeg_bytes in frames_jpeg:
        b64 = base64.standard_b64encode(jpeg_bytes).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=3072,
            temperature=0.2,
        )
        english_summary = (response.choices[0].message.content or "").strip()
        if not english_summary:
            log_event(
                "ollama_frame_content_empty",
                {"model": model, "base_url": base_url},
            )
    except Exception as e:
        err_msg = str(e)
        if "404" in err_msg or "Not Found" in err_msg:
            raise RuntimeError(
                f"Ollama が 404 を返しました（モデル未取得の可能性が高いです）。\n\n"
                f"1) ビジョン用モデルを取得: ollama pull {model}\n"
                f"2) 取得済みモデル確認: ollama list\n"
                f"3) Ollama を最新版に: https://ollama.com/download\n"
                f"4) 起動確認: ブラウザで {base_url} を開く"
            ) from e
        log_event(
            "ollama_frame_error",
            {"model": model, "base_url": base_url, "msg": (err_msg[:300])},
        )
        raise

    return english_summary


def _translate_report_to_japanese(
    english_summary: str,
    *,
    base_url: str,
    japanese_model: str,
) -> str:
    """
    第2段階のみ実行する: 英語要約を日本語に翻訳し、レポート形式に整える。
    """
    client = _ollama_client(base_url)
    jm = japanese_model.strip()
    if not jm:
        return _format_as_report(english_summary)

    translate_prompt = render_translate_to_ja_prompt(english_summary)
    try:
        resp_ja = client.chat.completions.create(
            model=jm,
            messages=[{"role": "user", "content": translate_prompt}],
            max_tokens=3072,
        )
        ja_raw = (resp_ja.choices[0].message.content or "").strip()
        # まれに中身が空で返ってくる場合があるので、そのときは英語を残す
        if not ja_raw:
            return english_summary + "\n\n(日本語への翻訳結果が空だったため、英語の要約を表示しています。)"
        return _format_as_report(ja_raw)
    except Exception:
        # 日本語モデルが無いなどで失敗した場合は英語のまま返す
        return english_summary + "\n\n(日本語への翻訳に失敗しました。上記は英語の要約です。)"


def analyze_video_with_ollama(
    frames_jpeg: list[bytes],
    *,
    base_url: str = "http://localhost:11434",
    model: str = "llava",
    japanese_model: str | None = None,
) -> str:
    """
    Ollama で動画を解析する。OpenAI 互換の /v1/chat/completions を使用するため、
    /api/chat が 404 になる環境でも動作する。
    ビジョンモデルで英語要約 → japanese_model 指定時は自然な日本語に翻訳。
    """
    english_summary = _analyze_video_english_only(
        frames_jpeg,
        base_url=base_url,
        model=model,
    )

    if japanese_model and japanese_model.strip():
        return _translate_report_to_japanese(
            english_summary,
            base_url=base_url,
            japanese_model=japanese_model,
        )

    return _format_as_report(english_summary)


def analyze_video(
    frames_jpeg: list[bytes],
    *,
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "llava",
    ollama_japanese_model: str | None = None,
) -> str:
    """Ollama で動画フレームを解析する（英語要約→日本語翻訳の二段階）。"""
    return analyze_video_with_ollama(
        frames_jpeg,
        base_url=ollama_base_url,
        model=ollama_model,
        japanese_model=ollama_japanese_model,
    )


def get_summary_from_frame_results(
    frame_results: list[str],
    *,
    base_url: str = "http://localhost:11434",
    model: str | None = None,
    persona_key: str = "neutral",
) -> str:
    """
    各フレームの解析結果テキストをまとめ、Ollama で総評を生成する。
    主な登場要素と動画内の展開の割合を出力する。
    """
    if not frame_results:
        return "（解析結果がありません。先にフレーム解析を実行してください。）"
    client = _ollama_client(base_url)
    used_model = (model or "llama3.2").strip()
    combined = "\n\n---\n\n".join(
        f"【フレーム{i+1}】\n{(t or '').strip()}" for i, t in enumerate(frame_results)
    )
    pk = (persona_key or "neutral").strip()
    personas = load_summary_personas()
    neutral = personas.get("neutral") or next(iter(personas.values()))
    persona_instruction = personas.get(pk, neutral)
    prompt = render_summary_prompt(persona_instruction, combined)
    try:
        response = client.chat.completions.create(
            model=used_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
        summary_raw = (response.choices[0].message.content or "").strip()
        # 空のときはフォールバックモデルで1回だけ再試行（qwen3.5 等がコンテンツで空を返す対策）
        FALLBACK_SUMMARY_MODEL = "llama3.2"
        if not summary_raw and used_model != FALLBACK_SUMMARY_MODEL:
            try:
                response = client.chat.completions.create(
                    model=FALLBACK_SUMMARY_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=2048,
                )
                summary_raw = (response.choices[0].message.content or "").strip()
            except Exception:
                pass
        if not summary_raw:
            return "（総評テキストが空でした。モデル設定やプロンプトを確認してください。）"
        return summary_raw
    except Exception as e:
        err_msg = str(e)
        if "404" in err_msg or "Not Found" in err_msg:
            raise RuntimeError(
                f"Ollama の API に接続できません（404）。モデル '{used_model}' が存在するか確認してください。"
            ) from e
        raise
