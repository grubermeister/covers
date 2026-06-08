import hashlib
import mimetypes
from pathlib import Path

from PIL import Image as PILImage


MEDIA_ROOT = Path(__file__).resolve().parents[2] / 'backend' / 'media'
