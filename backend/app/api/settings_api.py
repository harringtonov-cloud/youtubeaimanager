from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import get_config, secrets_status
from ..db import get_db
from ..events import log_event
from ..security import require_admin
from ..settings_store import AppSettings, get_settings, update_settings

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_admin)])


@router.get("")
def read_settings(db: Session = Depends(get_db)):
    cfg = get_config()
    return {
        "settings": get_settings(db).model_dump(),
        "secrets": secrets_status(),  # booleans only
        "env": {
            "llm_base_url": cfg.llm_base_url,
            "llm_model": cfg.llm_model,
            "llm_fallback_model": cfg.llm_fallback_model or None,
            "tts_provider": cfg.tts_provider,
            "storage_backend": cfg.storage_backend,
            "timezone": cfg.timezone,
            "backend_public_url": cfg.backend_public_url,
            "oauth_redirect_uri": cfg.oauth_redirect_uri,
        },
    }


@router.put("")
def write_settings(patch: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    current = get_settings(db)

    # Turning auto-publish ON is gated by a live preflight (YouTube token, LLM, worker, storage...)
    if patch.get("auto_publish_enabled") is True and not current.auto_publish_enabled:
        from ..services import preflight

        report = preflight.run_checks(db)
        if not report["ready"]:
            raise HTTPException(
                status_code=409,
                detail={"message": "Auto-publish cannot be enabled until all blocking checks pass", "report": report},
            )
        log_event("settings", "auto-publish ENABLED by owner after successful preflight")

    try:
        updated = update_settings(db, patch)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False))
    if patch.get("auto_publish_enabled") is False and current.auto_publish_enabled:
        log_event("settings", "auto-publish DISABLED by owner", level="WARNING")
    return {"settings": updated.model_dump()}


@router.get("/schema")
def settings_schema():
    return AppSettings.model_json_schema()
