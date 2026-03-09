"""Image cache: download QQ CDN images to local disk for base64 injection.

Downloaded files are stored under the directory configured by
``[BOT_CONFIG] image_cache_dir`` in ``config.toml``, with filenames derived
from a SHA-256 hash of the URL so duplicate downloads are avoided.  The
framework's ``_append_image_messages`` will read these local files and
encode them as base64 data-URLs automatically.
"""

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import List

import httpx

from qq_social_bot_app.intelligence.utils import bot_config

logger = logging.getLogger(__name__)

# Images older than this (seconds) are eligible for cleanup.
_DEFAULT_MAX_AGE = 24 * 3600  # 24 hours

# Timeout for each individual image download.
_DOWNLOAD_TIMEOUT = 30.0


def _get_cache_dir() -> Path:
    return Path(bot_config.get_image_cache_dir())


def _ensure_cache_dir() -> Path:
    d = _get_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


# The framework derives MIME type from the file extension as ``image/{ext}``.
# The API only accepts image/jpeg, image/png, image/gif, image/webp — so we
# must use extensions that map to those exact MIME types (notably .jpeg, NOT .jpg).
_CONTENT_TYPE_TO_EXT = {
    'image/jpeg': '.jpeg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
}


def _url_to_filename(url: str, content_type: str = '') -> str:
    """Derive a stable filename from the URL (hash + extension).

    *content_type* (from the HTTP response) is preferred for determining the
    extension.  Falls back to URL path inspection, then defaults to ``.jpeg``.
    """
    h = hashlib.sha256(url.encode()).hexdigest()[:16]

    # 1. Try Content-Type header
    if content_type:
        base_ct = content_type.split(';')[0].strip().lower()
        ext = _CONTENT_TYPE_TO_EXT.get(base_ct)
        if ext:
            return f'{h}{ext}'

    # 2. Guess from URL path
    path_part = url.split('?')[0].lower()
    for candidate in ('.png', '.jpeg', '.gif', '.webp'):
        if path_part.endswith(candidate):
            return f'{h}{candidate}'
    # .jpg in the URL → use .jpeg for correct MIME mapping
    if path_part.endswith('.jpg'):
        return f'{h}.jpeg'

    # 3. Default — QQ CDN images are almost always JPEG
    return f'{h}.jpeg'


async def download_images(urls: List[str]) -> List[str]:
    """Download a list of image URLs and return their local file paths.

    Already-cached images are returned immediately without re-downloading.
    Failed downloads are silently skipped (logged as warnings).
    """
    if not urls:
        return []

    cache_dir = _ensure_cache_dir()
    local_paths: List[str] = []

    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        for url in urls:
            # Try cache with URL-only filename first (no Content-Type yet)
            preliminary = _url_to_filename(url)
            preliminary_path = cache_dir / preliminary
            if preliminary_path.exists():
                logger.debug('Image cache hit: %s', preliminary_path)
                local_paths.append(str(preliminary_path))
                continue

            try:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get('content-type', '')
                filename = _url_to_filename(url, content_type)
                local_path = cache_dir / filename
                local_path.write_bytes(resp.content)
                logger.info('Downloaded image: %s -> %s', url[:80], local_path)
                local_paths.append(str(local_path))
            except Exception:
                logger.warning('Failed to download image: %s', url[:120], exc_info=True)

    return local_paths


def cleanup_cache(max_age: float = _DEFAULT_MAX_AGE) -> int:
    """Remove cached images older than *max_age* seconds.  Returns count of deleted files."""
    cache_dir = _get_cache_dir()
    if not cache_dir.exists():
        return 0
    now = time.time()
    removed = 0
    for entry in cache_dir.iterdir():
        if entry.is_file() and (now - entry.stat().st_mtime) > max_age:
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        logger.info('Image cache cleanup: removed %d file(s)', removed)
    return removed
