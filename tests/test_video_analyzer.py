import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from app.video_analyzer import extract_frames_every_n_seconds


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


class FrameExtractionTests(unittest.TestCase):
    def test_extract_frames_every_n_seconds_uses_video_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "sample.mp4"
            _write_sample_video(video_path)

            frames = extract_frames_every_n_seconds(video_path, interval_seconds=1.0)

        self.assertEqual(len(frames), 3)
        self.assertTrue(all(frame.startswith(b"\xff\xd8") for frame in frames))


    def test_extract_frames_every_n_seconds_respects_max_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "sample.mp4"
            _write_sample_video(video_path, frame_count=30, fps=10.0)

            frames = extract_frames_every_n_seconds(video_path, interval_seconds=0.5, max_frames=2)

        self.assertEqual(len(frames), 2)


    def test_extract_frames_every_n_seconds_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                extract_frames_every_n_seconds(Path(tmp) / "missing.mp4")


if __name__ == "__main__":
    unittest.main()
