import uuid

from lead_inbox.classifier import Classification
from lead_inbox.store import StoreError, classification_columns


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.fail_insert = False
        self.fail_update = False

    def insert_lead(self, lead, result):
        if self.fail_insert:
            raise StoreError("db down")
        row = {"id": str(uuid.uuid4()), "status": "new", "temperature": None, "needs_review": False, **lead}
        if result:
            row.update(classification_columns(result))
        self.rows[row["id"]] = row
        return row

    def update_classification(self, lead_id, result):
        if self.fail_update:
            raise StoreError("db down")
        self.rows[lead_id].update(classification_columns(result))
        return self.rows[lead_id]

    def set_status(self, lead_id, status):
        if lead_id not in self.rows:
            return None
        self.rows[lead_id]["status"] = status
        return self.rows[lead_id]

    def list_leads(self, temperature=None, status=None, needs_review=None, limit=50, offset=0):
        rows = [r for r in self.rows.values()
                if (temperature is None or r["temperature"] == temperature)
                and (status is None or r["status"] == status)
                and (needs_review is None or r["needs_review"] == needs_review)]
        return rows[offset:offset + limit]

    def unclassified(self, limit=20):
        return [dict(r) for r in self.rows.values() if r["status"] == "new" and r["temperature"] is None][:limit]

    def ping(self):
        return True


class FakeClassifier:
    def __init__(self, result=None):
        self.seen = []
        self.result = result or Classification("Hot", 85, "Wants 20 desks next month.", "Call today.",
                                               classified_by="fake-classifier")

    def classify(self, lead):
        self.seen.append(lead)
        return self.result
