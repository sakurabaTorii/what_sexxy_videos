import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

import app.main as main


def _write_sample_video(path: Path, *, frame_count: int = 15, fps: float = 5.0) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 64))
    if not writer.isOpened():
        raise RuntimeError("sample video writer could not be opened")
    try:
        for i in range(frame_count):
            frame = np.full((64, 64, 3), (i * 10) % 256, dtype=np.uint8)
            writer.write(frame)
    finally:
        writer.release()


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_analyze_video = main.analyze_video

    def tearDown(self) -> None:
        main.analyze_video = self._original_analyze_video

    def test_index_serves_web_ui(self) -> None:
        client = TestClient(main.app)

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("動画内容解析", response.text)

    def test_analyze_upload_returns_summary_without_ollama_in_test(self) -> None:
        main.analyze_video = lambda frames, **_config: f"fake summary ({len(frames)} frames)"
        client = TestClient(main.app)

        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "sample.mp4"
            _write_sample_video(video_path)
            with video_path.open("rb") as file:
                response = client.post(
                    "/api/analyze",
                    files={"file": ("sample.mp4", file, "video/mp4")},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"summary": "fake summary (1 frames)", "frames_used": 1})

    def test_analyze_upload_rejects_non_video(self) -> None:
        client = TestClient(main.app)

        response = client.post(
            "/api/analyze",
            files={"file": ("note.txt", b"not a video", "text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "動画ファイルをアップロードしてください")


if __name__ == "__main__":
    unittest.main()
