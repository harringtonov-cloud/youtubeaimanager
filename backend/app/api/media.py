from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from ..security import require_admin
from ..storage import LocalStorage, get_storage

router = APIRouter(prefix="/api/media", tags=["media"], dependencies=[Depends(require_admin)])


@router.get("/{key:path}")
def get_media(key: str):
    """Authenticated access to rendered videos / thumbnails (dashboard previews)."""
    storage = get_storage()
    if isinstance(storage, LocalStorage):
        try:
            path = storage.path_for(key)
        except ValueError:
            raise HTTPException(400, "invalid key")
        if not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type=mimetypes.guess_type(key)[0] or "application/octet-stream")
    if not storage.exists(key):
        raise HTTPException(404, "not found")
    return RedirectResponse(storage.url_for(key), status_code=302)
