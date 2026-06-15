import hashlib
import mimetypes
from pathlib import Path

from PIL import Image as PILImage


MEDIA_ROOT = Path(__file__).resolve().parents[2] / 'backend' / 'media'


def _is_two_ascii_letters(value):
    return len(value) == 2 and all("A" <= ch <= "Z" for ch in value)


def catalog_region_abbrev(catalog_name):
    """Return the two-letter ASCC region abbrev from a catalog path.

    This intentionally mirrors ascc_data_munger's historical rule:
    REGION_ABBREV comes from the first two characters of the input CSV
    basename. The image extractor uses the same rule so hyphenated stems
    such as WV-ASCC-CTLG emit wv-438-3-1.png, not
    wv-ascc-ctlg-438-3-1.png.
    """
    stem = Path(str(catalog_name)).stem
    abbrev = stem[:2].upper()
    if not _is_two_ascii_letters(abbrev):
        raise ValueError(
            f"catalog name must start with a two-letter region abbrev: "
            f"{catalog_name!r}"
        )
    return abbrev


def region_media_slug(region_abbrev):
    """Return the media subdir and filename prefix for a region abbrev."""
    abbrev = str(region_abbrev or "").strip().upper()
    if not _is_two_ascii_letters(abbrev):
        raise ValueError(
            f"region abbrev must be exactly two ASCII letters: "
            f"{region_abbrev!r}"
        )
    return abbrev.lower()


def catalog_image_slug(catalog_name):
    """Return the media filename prefix for an ASCC catalog path."""
    return region_media_slug(catalog_region_abbrev(catalog_name))


def image_filename(region_slug, page, chunk, counter):
    """Return the canonical ASCC extracted-image filename."""
    slug = region_media_slug(region_slug)
    return f"{slug}-{int(page)}-{int(chunk)}-{int(counter)}.png"
