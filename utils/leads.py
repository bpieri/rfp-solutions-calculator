"""
Lead capture for the RFP calculator email gate.

Flow:  visitor enters email in the calculator  ->  contact is created/updated
in systeme.io (custom fields + tags)  ->  tags trigger systeme.io automation
rules (welcome sequence, weekly quote list)  ->  calculator unlocks.

Design rules
------------
* FAIL OPEN: if systeme.io is unreachable the visitor is still let in and the
  lead is written to a local backup CSV. A flaky API must never cost a lead.
* The signed "return token" lets a visitor bookmark the calculator and skip the
  gate next time. It is a convenience, not security (anyone can type a fake
  email into any gate); the goal is a clean list, not a vault.
* All endpoint shapes below follow systeme.io's public API (base
  https://api.systeme.io/api, auth header X-API-Key). Run the self-test first:
      python -m utils.leads fields
      python -m utils.leads selftest you@yourdomain.com
  If anything errors, the message tells you exactly which call failed.
"""

import base64
import csv
import hashlib
import hmac
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE = "https://api.systeme.io/api"
TIMEOUT_SECONDS = 8

# Custom contact-field SLUGS in systeme.io (Contacts -> Contact fields).
# Create these fields once, then confirm slugs with:  python -m utils.leads fields
FIELD_SLUGS = {
    "first_name": "first_name",      # built-in
    "company": "company",
    "citygate": "citygate",
    "monthly_dth": "monthly_dth",
    "term_months": "term_months",
    "lead_source": "lead_source",
}

TAG_WEEKLY = "weekly-quote"
TAG_DAILY = "daily-newsletter"
TAG_CALCULATOR = "calculator-lead"
TAG_QUOTE_REQUESTED = "quote-requested"
TAG_FREE_EMAIL = "free-email-domain"
CITYGATE_TAGS = {"SoCalGas": "citygate-socal", "PG&E": "citygate-pge", "Piedmont": "citygate-piedmont"}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com",
    "proton.me", "protonmail.com", "live.com", "msn.com", "me.com", "gmx.com",
}

BACKUP_PATH = Path(__file__).resolve().parent.parent / "data" / "leads_backup.csv"


# ---------------------------------------------------------------------------
# Config / secrets
# ---------------------------------------------------------------------------
def get_secret(name: str, default: str = "") -> str:
    """Environment variable first, then Streamlit secrets, else default."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st  # only available inside the app
        return str(st.secrets.get(name, default) or default)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Validation + signed return token
# ---------------------------------------------------------------------------
def is_valid_email(email: str) -> bool:
    return bool(email) and bool(EMAIL_RE.match(email.strip()))


def is_free_email(email: str) -> bool:
    return email.strip().lower().rsplit("@", 1)[-1] in FREE_EMAIL_DOMAINS


def make_token(email: str, secret: str) -> str:
    """URL-safe token: base64(email).hmac. Empty string if no secret configured."""
    if not secret or not email:
        return ""
    e = email.strip().lower()
    payload = base64.urlsafe_b64encode(e.encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), e.encode(), hashlib.sha256).hexdigest()[:20]
    return f"{payload}.{sig}"


def verify_token(token: str, secret: str):
    """Returns the email if the token is authentic, else None."""
    if not token or not secret or "." not in token:
        return None
    try:
        payload, sig = token.rsplit(".", 1)
        email = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    except Exception:
        return None
    expected = hmac.new(secret.encode(), email.encode(), hashlib.sha256).hexdigest()[:20]
    return email if hmac.compare_digest(sig, expected) else None


# ---------------------------------------------------------------------------
# Local backup (never lose a lead)
# ---------------------------------------------------------------------------
def backup_lead(row: dict) -> None:
    try:
        BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        new_file = not BACKUP_PATH.exists()
        with open(BACKUP_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    except Exception as exc:  # backup is best-effort
        print(f"[leads] backup failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# systeme.io client
# ---------------------------------------------------------------------------
class SystemeError(Exception):
    pass


class SystemeClient:
    def __init__(self, api_key: str, session=None):
        self.api_key = api_key
        self.session = session or requests.Session()
        self._tag_ids = {}

    def _request(self, method, path, json=None, params=None, merge_patch=False):
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/merge-patch+json" if merge_patch else "application/json",
        }
        return self.session.request(method, f"{API_BASE}{path}", headers=headers,
                                    json=json, params=params, timeout=TIMEOUT_SECONDS)

    @staticmethod
    def _items(payload):
        if isinstance(payload, dict):
            return payload.get("items") or payload.get("hydra:member") or []
        return payload if isinstance(payload, list) else []

    # -- contacts ----------------------------------------------------------
    def find_contact_id(self, email: str):
        r = self._request("GET", "/contacts", params={"email": email})
        if not r.ok:
            return None
        for item in self._items(r.json()):
            if str(item.get("email", "")).lower() == email.lower():
                return item.get("id")
        return None

    @staticmethod
    def _field_list(fields: dict) -> list:
        return [{"slug": slug, "value": str(value)} for slug, value in fields.items() if value not in (None, "")]

    def upsert_contact(self, email: str, fields: dict):
        """Create the contact (or update it if it already exists). Returns (id, created)."""
        body = {"email": email, "locale": "en", "fields": self._field_list(fields)}
        r = self._request("POST", "/contacts", json=body)
        if r.ok:
            return r.json().get("id"), True

        # Not created: most likely it already exists -> find it and patch fields.
        contact_id = self.find_contact_id(email)
        if contact_id:
            pr = self._request("PATCH", f"/contacts/{contact_id}",
                               json={"fields": self._field_list(fields)}, merge_patch=True)
            if not pr.ok:
                print(f"[leads] field update failed ({pr.status_code}): {pr.text[:200]}", file=sys.stderr)
            return contact_id, False

        # Maybe a custom field slug doesn't exist yet -> retry with email only.
        r2 = self._request("POST", "/contacts", json={"email": email, "locale": "en"})
        if r2.ok:
            print(f"[leads] created without custom fields (first call {r.status_code}: {r.text[:200]})",
                  file=sys.stderr)
            return r2.json().get("id"), True
        raise SystemeError(f"create contact failed: {r.status_code} {r.text[:300]}")

    # -- tags --------------------------------------------------------------
    def tag_id(self, name: str):
        if name in self._tag_ids:
            return self._tag_ids[name]
        params = {"limit": 100}
        for _ in range(10):  # page through existing tags
            r = self._request("GET", "/tags", params=params)
            if not r.ok:
                break
            payload = r.json()
            items = self._items(payload)
            for t in items:
                if str(t.get("name", "")).lower() == name.lower():
                    self._tag_ids[name] = t.get("id")
                    return t.get("id")
            if not (isinstance(payload, dict) and payload.get("hasMore") and items):
                break
            params["startingAfter"] = items[-1].get("id")
        r = self._request("POST", "/tags", json={"name": name})
        if r.ok:
            self._tag_ids[name] = r.json().get("id")
            return self._tag_ids[name]
        raise SystemeError(f"tag '{name}' not found and could not be created: {r.status_code} {r.text[:200]}")

    def add_tag(self, contact_id, tag_name: str) -> None:
        tid = self.tag_id(tag_name)
        r = self._request("POST", f"/contacts/{contact_id}/tags", json={"tagId": tid})
        # 4xx here usually means "already tagged" -> not an error for our purposes.
        if r.status_code >= 500:
            raise SystemeError(f"tagging failed: {r.status_code} {r.text[:200]}")

    # -- diagnostics ---------------------------------------------------------
    def list_fields(self):
        r = self._request("GET", "/contact_fields")
        if not r.ok:
            raise SystemeError(f"list fields failed: {r.status_code} {r.text[:200]}")
        return self._items(r.json())


# ---------------------------------------------------------------------------
# The one function the app calls
# ---------------------------------------------------------------------------
def submit_lead(email, first_name="", company="", citygate="", monthly_dth=None,
                term_months=None, source="direct", daily_optin=True, extra_tags=()):
    """
    Capture a lead. Always returns a dict and never raises:
      {"ok": True,  "mode": "systeme"}          -> contact + tags created in systeme.io
      {"ok": True,  "mode": "backup_only", ...} -> no API key configured (dev/demo)
      {"ok": False, "mode": "api_error", ...}   -> API failed; lead is in the backup CSV
    The caller should unlock the calculator in every case (fail open).
    """
    email = email.strip().lower()
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "email": email, "first_name": first_name, "company": company, "citygate": citygate,
        "monthly_dth": monthly_dth, "term_months": term_months, "source": source,
        "daily_optin": daily_optin, "extra_tags": "|".join(extra_tags),
    }
    backup_lead(row)

    api_key = get_secret("SYSTEMEIO_API_KEY")
    if not api_key:
        return {"ok": True, "mode": "backup_only", "error": "SYSTEMEIO_API_KEY not set"}

    try:
        client = SystemeClient(api_key)
        fields = {
            FIELD_SLUGS["first_name"]: first_name,
            FIELD_SLUGS["company"]: company,
            FIELD_SLUGS["citygate"]: citygate,
            FIELD_SLUGS["monthly_dth"]: monthly_dth,
            FIELD_SLUGS["term_months"]: term_months,
            FIELD_SLUGS["lead_source"]: f"calculator:{source}",
        }
        contact_id, created = client.upsert_contact(email, fields)
        if not contact_id:
            raise SystemeError("no contact id returned")

        tags = [TAG_CALCULATOR, TAG_WEEKLY]
        if CITYGATE_TAGS.get(citygate):
            tags.append(CITYGATE_TAGS[citygate])
        if daily_optin:
            tags.append(TAG_DAILY)
        if is_free_email(email):
            tags.append(TAG_FREE_EMAIL)
        tags.extend(extra_tags)
        for tag in tags:
            client.add_tag(contact_id, tag)
        return {"ok": True, "mode": "systeme", "created": created}
    except Exception as exc:  # fail open
        print(f"[leads] systeme.io error for {email.split('@')[-1]}: {exc}", file=sys.stderr)
        return {"ok": False, "mode": "api_error", "error": str(exc)}


def tag_contact_by_email(email: str, tag_name: str, fields: dict = None) -> dict:
    """Used for 'Request my live quote' -> tags the existing contact (hot lead)."""
    api_key = get_secret("SYSTEMEIO_API_KEY")
    if not api_key:
        return {"ok": True, "mode": "backup_only"}
    try:
        client = SystemeClient(api_key)
        contact_id, _ = client.upsert_contact(email.strip().lower(), fields or {})
        client.add_tag(contact_id, tag_name)
        return {"ok": True, "mode": "systeme"}
    except Exception as exc:
        print(f"[leads] tag error: {exc}", file=sys.stderr)
        return {"ok": False, "mode": "api_error", "error": str(exc)}


# ---------------------------------------------------------------------------
# CLI:  python -m utils.leads fields | tags | selftest you@domain.com
# ---------------------------------------------------------------------------
def _cli(argv):
    key = get_secret("SYSTEMEIO_API_KEY")
    if not key:
        print("Set SYSTEMEIO_API_KEY first (PowerShell:  $env:SYSTEMEIO_API_KEY='your-key')")
        return 1
    client = SystemeClient(key)
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "fields":
        print("Contact fields in your account (use the SLUG column in FIELD_SLUGS):")
        for f in client.list_fields():
            print(f"  slug={f.get('slug')!s:<22} label={f.get('fieldName') or f.get('name') or f.get('label')}")
        return 0
    if cmd == "tags":
        r = client._request("GET", "/tags", params={"limit": 100})
        for t in client._items(r.json()):
            print(f"  id={t.get('id')}  name={t.get('name')}")
        return 0
    if cmd == "selftest" and len(argv) > 2:
        result = submit_lead(argv[2], first_name="Self", company="Test Co", citygate="SoCalGas",
                             monthly_dth=500, term_months=12, source="selftest", daily_optin=True)
        print(result)
        return 0 if result.get("ok") and result.get("mode") == "systeme" else 2
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
