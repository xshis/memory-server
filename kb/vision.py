from __future__ import annotations

from pathlib import Path

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff",
}


def available() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_available() -> None:
    if not available():
        raise RuntimeError(
            "图片整理需要 OpenCV。安装：.venv/bin/python -m pip install -e '.[vision]'"
        )


def classify_image(path: str | Path) -> str:
    """用 OpenCV 做轻量分类：人物 / 风景 / 文档截图 / 其他。

    这是启发式分类，不保证语义准确；适合把图片先按大类粗分。
    """
    import cv2

    image = cv2.imread(str(path))
    if image is None or image.size == 0:
        return "其他"

    height, width = image.shape[:2]
    if height < 8 or width < 8:
        return "其他"

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    if cascade_path.exists():
        cascade = cv2.CascadeClassifier(str(cascade_path))
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(max(24, width // 20), max(24, height // 20)),
        )
        if len(faces) > 0:
            return "人物"

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    brightness = float(hsv[:, :, 2].mean())
    saturation = float(hsv[:, :, 1].mean())
    edges = cv2.Canny(gray, 80, 160)
    edge_density = float((edges > 0).mean())

    if brightness >= 170 and saturation <= 60:
        return "文档截图"
    if saturation >= 55:
        return "风景"
    if saturation >= 25 and (edge_density >= 0.05 or brightness < 160):
        return "风景"
    return "其他"


def describe(path: str | Path) -> dict[str, object]:
    """返回分类和几个可解释指标，方便调试。"""
    import cv2

    image = cv2.imread(str(path))
    if image is None or image.size == 0:
        return {"category": "其他", "error": "无法读取图片"}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    edges = cv2.Canny(gray, 80, 160)
    return {
        "category": classify_image(path),
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "brightness": round(float(hsv[:, :, 2].mean()), 1),
        "saturation": round(float(hsv[:, :, 1].mean()), 1),
        "edge_density": round(float((edges > 0).mean()), 4),
    }
