"""Supabase access through its PostgREST API (the same endpoint supabase-js and supabase-py use).

The service role key is used server-side only. It bypasses Row Level Security, which is why it
must never reach a browser; the website gets the anon key, which RLS limits to inserts.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from .classifier import Classification

log = logging.getLogger(__name__)
TEAM_COLUMNS = ("id,created_at,name,email,company,phone,message,source,status,temperature,score,"
                "reason,suggested_next_step,needs_review,classified_by,classified_at")


class StoreError(RuntimeError):
    pass


class SupabaseStore:
    def __init__(self, url: str, service_role_key: str, client: Optional[httpx.Client] = None):
        self.base = f"{url.rstrip('/')}/rest/v1"
        self.http = client or httpx.Client(timeout=10.0)
        self.headers = {"apikey": service_role_key, "Authorization": f"Bearer {service_role_key}",
                        "Content-Type": "application/json"}

    def _check(self, response: httpx.Response, action: str):
        if response.status_code >= 400:
            # PostgREST error bodies carry code/message/details; never include the key.
            raise StoreError(f"{action} failed: HTTP {response.status_code} {response.text[:300]}")
        return response.json() if response.content else None

    def insert_lead(self, lead: dict, result: Optional[Classification]) -> dict:
        row = dict(lead)
        if result is not None:
            row.update(classification_columns(result))
        response = self.http.post(f"{self.base}/leads", json=row,
                                  headers={**self.headers, "Prefer": "return=representation"})
        return self._check(response, "insert lead")[0]

    def update_classification(self, lead_id: str, result: Classification) -> dict:
        response = self.http.patch(f"{self.base}/leads", params={"id": f"eq.{lead_id}"},
                                   json=classification_columns(result),
                                   headers={**self.headers, "Prefer": "return=representation"})
        rows = self._check(response, "update lead")
        if not rows:
            raise StoreError(f"lead {lead_id} not found")
        return rows[0]

    def set_status(self, lead_id: str, status: str) -> Optional[dict]:
        response = self.http.patch(f"{self.base}/leads", params={"id": f"eq.{lead_id}"}, json={"status": status},
                                   headers={**self.headers, "Prefer": "return=representation"})
        rows = self._check(response, "update status")
        return rows[0] if rows else None

    def list_leads(self, temperature: Optional[str] = None, status: Optional[str] = None,
                   needs_review: Optional[bool] = None, limit: int = 50, offset: int = 0) -> List[dict]:
        params = {"select": TEAM_COLUMNS, "order": "created_at.desc", "limit": str(limit), "offset": str(offset)}
        if temperature:
            params["temperature"] = f"eq.{temperature}"
        if status:
            params["status"] = f"eq.{status}"
        if needs_review is not None:
            params["needs_review"] = f"is.{str(needs_review).lower()}"
        return self._check(self.http.get(f"{self.base}/leads", params=params, headers=self.headers), "list leads")

    def unclassified(self, limit: int = 20) -> List[dict]:
        params = {"select": "id,name,email,company,phone,message,source", "status": "eq.new",
                  "temperature": "is.null", "order": "created_at.asc", "limit": str(limit)}
        return self._check(self.http.get(f"{self.base}/leads", params=params, headers=self.headers), "list new leads")

    def ping(self) -> bool:
        response = self.http.get(f"{self.base}/leads", params={"select": "id", "limit": "1"}, headers=self.headers)
        return response.status_code == 200


def classification_columns(result: Classification) -> dict:
    return {
        "status": "spam" if result.is_spam else "classified",
        "temperature": result.temperature,
        "score": result.score,
        "reason": result.reason,
        "suggested_next_step": result.suggested_next_step,
        "needs_review": result.needs_review,
        "classified_by": result.classified_by,
        "classified_at": datetime.now(timezone.utc).isoformat(),
        "classification_error": result.error,
    }
