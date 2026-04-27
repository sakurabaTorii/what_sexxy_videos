#!/usr/bin/env python3
"""
動画ファイルを指定して Ollama で内容を解析するCLI。
使用例: python cli.py path/to/video.mp4
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.video_analyzer import analyze_video, extract_frames_every_n_seconds

load_dotenv()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 1:
        print("使い方: python cli.py <動画ファイルのパス>", file=sys.stderr)
        sys.exit(1)

    video_path = Path(args[0]).resolve()
    if not video_path.exists():
        print(f"エラー: ファイルが見つかりません: {video_path}", file=sys.stderr)
        sys.exit(1)
    if not video_path.is_file():
        print(f"エラー: ファイルを指定してください: {video_path}", file=sys.stderr)
        sys.exit(1)

    config = {
        "ollama_base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "ollama_model": os.environ.get("OLLAMA_MODEL", "llava"),
        "ollama_japanese_model": os.environ.get("OLLAMA_JAPANESE_MODEL") or None,
    }

    print("フレームを抽出しています…", file=sys.stderr)
    try:
        frames = extract_frames_every_n_seconds(video_path, interval_seconds=10.0)
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"{len(frames)} フレームで解析中…", file=sys.stderr)
    try:
        summary = analyze_video(frames, **config)
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    print()
    print("--- 解析結果 ---")
    print(summary)


if __name__ == "__main__":
    main()
