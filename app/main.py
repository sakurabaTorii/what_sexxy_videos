"""動画内容解析 API"""
import os
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.video_analyzer import analyze_video, extract_frames_every_n_seconds

load_dotenv()

app = FastAPI(title="動画内容解析ツール", description="動画をアップロードしてAIに内容を解析してもらいます")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<p>static/index.html を配置してください</p>"


def _get_analyzer_config():
    """環境変数から Ollama の設定を取得"""
    return {
        "ollama_base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "ollama_model": os.environ.get("OLLAMA_MODEL", "llava"),
        "ollama_japanese_model": os.environ.get("OLLAMA_JAPANESE_MODEL") or None,
    }


@app.post("/api/analyze")
async def analyze_video_endpoint(file: UploadFile = File(...)):
    """アップロードされた動画を解析して内容の要約を返す"""
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(400, "動画ファイルをアップロードしてください")

    try:
        config = _get_analyzer_config()
    except HTTPException:
        raise

    suffix = Path(file.filename or "video").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        frames = extract_frames_every_n_seconds(tmp_path, interval_seconds=10.0)
        summary = analyze_video(frames, **config)
        return {"summary": summary, "frames_used": len(frames)}
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            503,
            f"Ollama に接続できません（{config.get('ollama_base_url', '')}）。Ollama が起動しているか確認してください。",
        ) from e
    except Exception as e:
        raise HTTPException(500, f"解析中にエラーが発生しました: {e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/api/analyze-path")
async def analyze_video_by_path(path: str):
    """
    サーバーから見えるパスで動画ファイルを指定して解析する。
    ローカル開発やCLI用途向け。LLM_BACKEND で OpenAI / Ollama を切り替え可能。
    """
    try:
        config = _get_analyzer_config()
    except HTTPException:
        raise

    resolved = Path(path).resolve()
    if not resolved.exists():
        raise HTTPException(404, f"ファイルが見つかりません: {path}")
    if not resolved.is_file():
        raise HTTPException(400, "ファイルを指定してください")

    try:
        frames = extract_frames_every_n_seconds(resolved, interval_seconds=10.0)
        summary = analyze_video(frames, **config)
        return {"summary": summary, "frames_used": len(frames)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            503,
            "Ollama に接続できません。Ollama が起動しているか確認してください。",
        ) from e
    except Exception as e:
        raise HTTPException(500, f"解析中にエラーが発生しました: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
