"""FastAPI service.

Public:  POST /leads              a website form posts here; saved first, classified straight after
Team:    GET  /leads              the inbox, newest first, filter by temperature / status / needs_review
         POST /leads/{id}/status  move a lead along (contacted, won, lost, spam)
         POST /leads/classify-pending  classify leads that the website inserted directly with the anon key
Ops:     GET  /health
Team endpoints need `Authorization: Bearer <TEAM_API_KEY>`.
"""
import hmac
import logging
import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

from .classifier import Classifier, ClaudeClassifier
from .config import Settings
from .store import StoreError, SupabaseStore

log = logging.getLogger("lead_inbox")
STATUSES = ("contacted", "won", "lost", "spam")


class LeadIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    company: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, max_length=40)
    message: str = Field(..., min_length=1, max_length=5000)
    source: str = Field("website", max_length=60)
    website: Optional[str] = Field(None, description="Honeypot. Real visitors never see or fill it.")


class StatusIn(BaseModel):
    status: str = Field(..., pattern="^(contacted|won|lost|spam)$")


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = self.hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        return True


def create_app(settings: Settings, store: SupabaseStore, classifier: Classifier,
               rate_limit_per_minute: int = 5, allowed_origins=("*",)) -> FastAPI:
    app = FastAPI(title="AI lead inbox", version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=list(allowed_origins), allow_methods=["POST", "GET"],
                       allow_headers=["Content-Type", "Authorization"])
    limiter = RateLimiter(rate_limit_per_minute)

    def require_team(authorization: str = Header("")):
        token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        if not token or not hmac.compare_digest(token, settings.team_api_key):
            raise HTTPException(401, "Team API key required.", headers={"WWW-Authenticate": "Bearer"})

    def classify_and_store(lead_id: str, lead: dict) -> None:
        result = classifier.classify(lead)
        try:
            store.update_classification(lead_id, result)
        except StoreError:
            log.exception("Could not save classification for lead %s; it stays 'new' for classify-pending", lead_id)

    @app.post("/leads", status_code=201)
    def submit_lead(lead: LeadIn, request: Request, background: BackgroundTasks):
        if lead.website:  # honeypot filled: a bot. Pretend success, store nothing.
            log.info("Honeypot triggered; lead dropped")
            return {"ok": True}
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.allow(client_ip):
            raise HTTPException(429, "Too many submissions. Please try again in a minute.",
                                headers={"Retry-After": "60"})
        data = lead.model_dump(exclude={"website"})
        data["email"] = str(data["email"]).lower()
        try:
            row = store.insert_lead(data, None)
        except StoreError:
            log.exception("Saving lead failed")
            raise HTTPException(503, "We couldn't save your message just now. Please try again shortly.")
        background.add_task(classify_and_store, row["id"], data)
        # The visitor never sees the classification.
        return {"ok": True, "id": row["id"]}

    @app.get("/leads", dependencies=[Depends(require_team)])
    def list_leads(temperature: Optional[str] = Query(None, pattern="^(Hot|Warm|Cold)$"),
                   status: Optional[str] = Query(None, pattern="^(new|classified|contacted|won|lost|spam)$"),
                   needs_review: Optional[bool] = None,
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        try:
            leads = store.list_leads(temperature, status, needs_review, limit, offset)
        except StoreError:
            log.exception("Listing leads failed")
            raise HTTPException(503, "Lead store unavailable.")
        return {"count": len(leads), "leads": leads}

    @app.post("/leads/{lead_id}/status", dependencies=[Depends(require_team)])
    def set_status(lead_id: str, body: StatusIn):
        row = store.set_status(lead_id, body.status)
        if row is None:
            raise HTTPException(404, "Lead not found.")
        return row

    @app.post("/leads/classify-pending", dependencies=[Depends(require_team)])
    def classify_pending(limit: int = Query(20, ge=1, le=100)):
        done = []
        for lead in store.unclassified(limit):
            lead_id = lead.pop("id")
            result = classifier.classify(lead)
            store.update_classification(lead_id, result)
            done.append({"id": lead_id, "temperature": result.temperature, "needs_review": result.needs_review})
        return {"classified": len(done), "leads": done}

    @app.get("/health")
    def health():
        ok = False
        try:
            ok = store.ping()
        except Exception:
            log.exception("Health check could not reach Supabase")
        if not ok:
            raise HTTPException(503, "Supabase unreachable.")
        return {"ok": True}

    return app


def build_app() -> FastAPI:
    """uvicorn entry point: `uvicorn lead_inbox.app:build_app --factory`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    store = SupabaseStore(settings.supabase_url, settings.service_role_key)
    classifier = ClaudeClassifier(settings.business_name, settings.business_context, settings.llm_model)
    origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
    return create_app(settings, store, classifier,
                      rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "5")),
                      allowed_origins=origins)
