"""Helpers shared by the platform connectors: safe HTTP calls, error mapping, token refresh, number parsing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable

import httpx

from .base import Connector, ConnectorError, redact


def dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else 0))
    except InvalidOperation:
        return Decimal(0)


def to_int(value: Any) -> int:
    try:
        return int(Decimal(str(value if value not in (None, "") else 0)))
    except InvalidOperation:
        return 0


def call(http: httpx.Client, name: str, method: str, url: str, secrets: Iterable[Any], **kw: Any) -> httpx.Response:
    """One HTTP request. Network problems become ConnectorError with every secret scrubbed from the text."""
    try:
        return http.request(method, url, **kw)
    except httpx.HTTPError as exc:
        raise ConnectorError(redact(f"Could not reach {name}: {exc}", list(secrets)), "other") from None


def read(resp: httpx.Response, name: str, secrets: Iterable[Any],
         message: Callable[[Any], str | None] | None = None,
         auth_statuses: tuple[int, ...] = (401, 403)) -> Any:
    """Parse a JSON reply and turn failures into ConnectorError: 401/403 = 'auth' (client must fix access),
    429 = 'rate_limit', anything else = 'other'. `message` pulls the platform's own error text from the body."""
    secrets = list(secrets)
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.status_code < 400 and body is not None:
        return body
    text = None
    if message and body is not None:
        try:
            text = message(body)
        except Exception:  # an odd error shape must not hide the real problem
            text = None
    text = text or f"HTTP {resp.status_code}"
    kind = "auth" if resp.status_code in auth_statuses else "rate_limit" if resp.status_code == 429 else "other"
    if resp.status_code < 400:
        raise ConnectorError(f"{name} returned an unreadable response", "other")
    raise ConnectorError(redact(f"{name} error: {text}", secrets), kind)


class RefreshingConnector(Connector):
    """For platforms whose pasted credentials are a long-lived refresh token plus app keys. Short-lived access
    tokens are fetched on demand and stored back (encrypted) so the 30-minute worker does not ask every time."""
    token_url = ""
    token_name = ""
    SKEW = timedelta(minutes=2)

    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=30)

    def _refresh_form(self, creds: dict[str, Any]) -> dict[str, str]:
        raise NotImplementedError

    def _valid(self, creds: dict[str, Any]) -> bool:
        raw, tok = creds.get("expires_at"), creds.get("access_token")
        if not tok or not raw:
            return False
        try:
            return datetime.fromisoformat(raw) - datetime.now(timezone.utc) > self.SKEW
        except ValueError:
            return False

    def _renew(self, creds: dict[str, Any]) -> dict[str, Any]:
        secrets = [v for v in creds.values() if isinstance(v, str)]
        resp = call(self._http, self.display_name, "POST", self.token_url, secrets, data=self._refresh_form(creds))
        body = read(resp, self.display_name, secrets,
                    lambda b: f"{b.get('error')}: {b.get('error_description', '')}".strip(": ") if isinstance(b, dict) else None,
                    auth_statuses=(400, 401, 403))
        if not isinstance(body, dict) or not body.get("access_token"):
            raise ConnectorError(f"{self.display_name} did not accept the login details", "auth")
        out = dict(creds)
        out["access_token"] = body["access_token"]
        out["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=int(body.get("expires_in") or 1800))).isoformat()
        if body.get("refresh_token"):          # some platforms rotate it; the new one must be kept
            out["refresh_token"] = body["refresh_token"]
        return out

    def refresh_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        return None if self._valid(credentials) else self._renew(credentials)

    def _token(self, credentials: dict[str, Any]) -> str:
        """A usable access token right now (renewed in memory when needed, e.g. while verifying a new connection)."""
        return credentials["access_token"] if self._valid(credentials) else self._renew(credentials)["access_token"]
