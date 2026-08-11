"""Helpers for computing Image metadata from image bytes or files on disk."""
import hashlib
import io
import mimetypes
from pathlib import Path
from typing import Optional

ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/tiff"}


def extract_image_metadata(content: bytes, mime_type: str) -> Optional[dict]:
    """
    Compute Image metadata (checksum, dimensions, size) from raw bytes.
    Returns dict with file_checksum, mime_type, image_width, image_height,
    file_size_bytes, or None if the bytes aren't a valid/allowed image.
    """
    if not content:
        return None
    if mime_type not in ALLOWED_MIME_TYPES:
        return None
    try:
        from PIL import Image as PILImage
    except ImportError:
        return None
    try:
        img = PILImage.open(io.BytesIO(content))
        width, height = img.size
    except Exception:
        width, height = 0, 0
    return {
        "file_checksum": hashlib.sha256(content).hexdigest(),
        "mime_type": mime_type[:50],
        "image_width": width,
        "image_height": height,
        "file_size_bytes": len(content),
    }


def read_image_metadata_from_path(path: Path) -> Optional[dict]:
    """Read an image from disk and return its Image metadata, or None if missing/invalid."""
    if not path.is_file():
        return None
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        return None
    return extract_image_metadata(path.read_bytes(), mime_type)


class CropError(ValueError):
    """A crop rectangle that cannot be applied to the given image."""


def crop_image_bytes(
    content: bytes,
    mime_type: str,
    box: tuple[int, int, int, int],
) -> bytes:
    """Crop ``content`` to ``box`` = (x, y, width, height) and re-encode it.

    Used by the editor crop tool (issue #77) to cut the marking out of a scan
    of a whole cover. Raises CropError for anything the caller should turn into
    a 400: an undecodable source, or a rectangle that is empty or reaches
    outside the image.

    The output keeps the source's format so the stored mime_type stays true.
    JPEG is re-encoded at quality 95 (settings.MARKING_IMAGE_QUALITY's value)
    because the crop becomes a catalog image and generation loss is visible on
    faint strikes.
    """
    if mime_type not in ALLOWED_MIME_TYPES:
        raise CropError("Unsupported image format.")
    try:
        from PIL import Image as PILImage
    except ImportError as exc:  # pragma: no cover - Pillow is a hard dependency
        raise CropError("Image processing is unavailable.") from exc

    x, y, width, height = (int(v) for v in box)
    if width <= 0 or height <= 0:
        raise CropError("Crop width and height must be greater than zero.")
    if x < 0 or y < 0:
        raise CropError("Crop origin must be inside the image.")

    try:
        img = PILImage.open(io.BytesIO(content))
        img.load()
    except Exception as exc:
        raise CropError("Could not read the source image.") from exc

    if x + width > img.width or y + height > img.height:
        raise CropError(
            f"Crop reaches outside the {img.width}x{img.height} source image."
        )

    cropped = img.crop((x, y, x + width, y + height))
    out = io.BytesIO()
    image_format = (img.format or "").upper() or _format_for_mime(mime_type)
    if image_format == "JPEG":
        # JPEG has no alpha; a PNG-sourced RGBA crop would fail to save.
        if cropped.mode not in ("RGB", "L"):
            cropped = cropped.convert("RGB")
        cropped.save(out, format="JPEG", quality=95)
    else:
        cropped.save(out, format=image_format)
    return out.getvalue()


def _format_for_mime(mime_type: str) -> str:
    if "png" in mime_type:
        return "PNG"
    if "tiff" in mime_type:
        return "TIFF"
    return "JPEG"
