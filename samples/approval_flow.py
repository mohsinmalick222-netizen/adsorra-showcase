"""Human-in-the-loop campaign changes (excerpt from actions.py).

Campaign actions: Adsorra suggests a change, an agency owner approves it, then (and only then) it is sent
to the ad platform.

Suggestions come from the same rules as the dashboard's action plan (analytics.build_insights):
  "pause_ad" insight  -> pause that ad
  "scale_ad" insight  -> raise the daily budget behind that ad by RAISE_PCT
Rules: nothing is changed without an owner's approval; each suggestion is applied at most once (the row is
claimed before the platform is called); demo connections are simulated; file imports can't be changed;
errors are stored redacted."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from . import db
from .analytics import full_context
from .connectors import BudgetChange, ConnectorError, get_connector, redact
from .deps import CurrentUser
from .security import decrypt_credentials, encrypt_credentials

RAISE_PCT = 20
LOOKBACK_DAYS = 14
QUIET_DAYS = 7          # after an ad was paused/raised or a suggestion dismissed, don't suggest it again for a week
DEMO_NOTE = "Demo data: nothing was changed on the ad platform."

# public(), get_action_or_404() and _budget_result() are omitted from this excerpt.


def approve(user: CurrentUser, action_id: uuid.UUID, connector=None) -> dict:
    """Owner approved: claim the row, send the change to the platform, record what happened."""
    get_action_or_404(user, action_id)
    with db.cursor() as cur:
        cur.execute("""UPDATE ad_actions SET status = 'applying', decided_by = %s, decided_at = now()
                        WHERE id = %s AND agency_id = %s AND status = 'pending' RETURNING *""",
                    (user.id, action_id, user.agency_id))
        act = cur.fetchone()
        if act is None:
            raise HTTPException(409, "This suggestion was already decided")
        cur.execute("SELECT * FROM connections WHERE id = %s AND agency_id = %s", (act["connection_id"], user.agency_id))
        conn = cur.fetchone()
        cur.execute("SELECT external_ad_id, external_ref FROM ads WHERE id = %s AND agency_id = %s", (act["ad_id"], user.agency_id))
        ad = cur.fetchone()

    creds: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        if conn["source"] == "file":
            raise ConnectorError("This ad came from a file upload, so Adsorra can't change it. Change it on the platform.")
        if conn["is_demo"]:
            result = {"demo": True, "summary": DEMO_NOTE}
        else:
            connector = connector or get_connector(conn["platform"])
            if act["kind"] not in connector.supported_actions:
                raise ConnectorError(f"{connector.display_name}: this change is not available yet")
            creds = decrypt_credentials(conn["credentials_enc"])
            renewed = connector.refresh_credentials(creds)
            if renewed:
                creds = renewed
                with db.cursor() as cur:
                    cur.execute("UPDATE connections SET credentials_enc = %s WHERE id = %s",
                                (encrypt_credentials(creds), conn["id"]))
            if act["kind"] == "pause_ad":
                connector.pause_ad(creds, conn["external_account_id"], ad["external_ad_id"], ad["external_ref"])
                result = {"summary": "Ad paused"}
            else:
                pct = int((act["params"] or {}).get("pct", RAISE_PCT))
                result = _budget_result(connector.raise_budget(creds, conn["external_account_id"], ad["external_ad_id"],
                                                               ad["external_ref"], pct))
    except ConnectorError as exc:
        error = redact(str(exc), creds.values())
    except Exception as exc:  # the row must never stay stuck in 'applying'
        error = redact(f"{type(exc).__name__}: {exc}", creds.values())

    now = datetime.now(timezone.utc)
    with db.cursor() as cur:
        if error is None:
            cur.execute("UPDATE ad_actions SET status = 'applied', result = %s, applied_at = %s WHERE id = %s",
                        (Jsonb(result), now, action_id))
        else:
            cur.execute("UPDATE ad_actions SET status = 'failed', error = %s WHERE id = %s", (error[:500], action_id))
    return public(get_action_or_404(user, action_id))
