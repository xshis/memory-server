from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - depends on optional extra
    cv2 = None
    np = None

from kb import vision


@unittest.skipUnless(cv2 is not None, "需要安装 OpenCV：pip install -e '.[vision]'")
class VisionTest(unittest.TestCase):
    def test_classify_synthetic_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            document = np.full((600, 800, 3), 255, np.uint8)
            for y in range(40, 560, 36):
                cv2.line(document, (40, y), (760, y), (0, 0, 0), 2)
            document_path = root / "document.png"
            cv2.imwrite(str(document_path), document)
            self.assertEqual(vision.classify_image(document_path), "文档截图")

            landscape = np.zeros((600, 800, 3), np.uint8)
            for y in range(600):
                landscape[y, :] = (int(255 * y / 600), 160, 220)
            landscape_path = root / "landscape.jpg"
            cv2.imwrite(str(landscape_path), landscape)
            self.assertEqual(vision.classify_image(landscape_path), "风景")

            gray = np.full((600, 800, 3), 128, np.uint8)
            gray_path = root / "gray.png"
            cv2.imwrite(str(gray_path), gray)
            self.assertEqual(vision.classify_image(gray_path), "其他")

            metrics = vision.describe(document_path)
            self.assertEqual(metrics["category"], "文档截图")
            self.assertIn("brightness", metrics)

    def test_unreadable_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.png"
            path.write_text("not an image", encoding="utf-8")
            self.assertEqual(vision.classify_image(path), "其他")


if __name__ == "__main__":
    unittest.main()
