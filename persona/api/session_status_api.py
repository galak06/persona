"""FastAPI route for browser-session (login) status.

  GET /api/v1/sessions -- FB/IG saved-session status for the active brand.

Mount in approval_api.py:
    from api.session_status_api import router as session_status_router
    app.include_router(session_status_router, prefix="/api/v1", tags=["sessions"])
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from lib.config import settings
from lib.session_status import session_status

router = APIRouter()


class SessionStatus(BaseModel):
    platform: str
    exists: bool
    last_saved: str | None
    login_command: str


class SessionStatusResponse(BaseModel):
    sessions: list[SessionStatus]


@router.get("/sessions", summary="FB/IG browser-session (login) status")
def get_session_status() -> SessionStatusResponse:
    assert settings.paths is not None
    sessions = session_status(settings.paths.brand_dir)
    return SessionStatusResponse(sessions=[SessionStatus(**s) for s in sessions])
