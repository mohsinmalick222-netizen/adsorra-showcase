"""The contract every ad platform connector follows.

A connector knows how to talk to ONE platform and must return data in the standard shape
below. Nothing outside this package should know platform-specific field names, so adding a
platform means adding one file here and registering it, with no change to the database,
the ranking, the API or the dashboard.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

_TOKEN_PARAM = re.compile(r"(access_token|client_secret|refresh_token|developer_token|token)=[^&\s\"']+", re.I)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-~+/]+=*", re.I)


def redact(text: str, secrets: Iterable[Any] = ()) -> str:
    """Remove tokens from any text that might be logged, stored or shown to a user."""
    out = _TOKEN_PARAM.sub(lambda m: f"{m.group(1)}=HIDDEN", str(text))
    out = _BEARER.sub(r"\1HIDDEN", out)
    for s in secrets:
        if isinstance(s, str) and len(s) >= 8:
            out = out.replace(s, "HIDDEN")
    return out


def date_chunks(since: date, until: date, days: int):
    """Split since..until (inclusive) into windows of at most `days` days. Platforms cap the range of one report."""
    from datetime import timedelta
    start = since
    while start <= until:
        end = min(start + timedelta(days=days - 1), until)
        yield start, end
        start = end + timedelta(days=1)


class ConnectorError(Exception):
    """kind: 'auth' = connection needs the client to log in again or fix permissions,
    'rate_limit' = try again later, 'other' = anything else."""

    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class AccountInfo:
    id: str
    name: str


@dataclass(frozen=True)
class StandardAdRow:
    """One ad on one day, in the same shape for every platform."""
    date: date
    external_ad_id: str
    ad_name: str
    campaign_name: str
    creative_url: str
    spend: Decimal
    impressions: int
    clicks: int
    conversions: Decimal
    revenue: Decimal
    external_ref: str = ""     # opaque ids a platform needs later to change this ad (e.g. ad group); never shown


@dataclass(frozen=True)
class BudgetChange:
    level: str          # "ad set", "ad group", "campaign"
    name: str
    old: Decimal        # daily budget in the account's currency, before and after
    new: Decimal
    currency: str = ""


def raised(old: Decimal, pct: int, step: Decimal = Decimal("0.01")) -> Decimal:
    """old + pct%, rounded to the platform's smallest step, and always at least one step higher."""
    new = (old * (100 + pct) / 100 / step).to_integral_value(ROUND_HALF_UP) * step
    return max(new, old + step)


class Connector(ABC):
    platform: str = ""
    display_name: str = ""
    implemented: bool = True
    notes: str = ""
    beta: bool = False                      # True = written from the platform docs, not yet run against a live account
    # What the agency must paste in when the client hands over access. `secret` fields are shown as password boxes.
    credential_fields: list[dict[str, Any]] = [
        {"key": "access_token", "label": "Access token", "secret": True, "optional": False, "help": ""}]
    account_label: str = "Ad account ID"
    account_placeholder: str = ""
    verify_hint: str = ""                   # shown when the credentials cannot see the account

    def normalize_account_id(self, raw: str) -> str:
        return raw.strip()

    @abstractmethod
    def list_accounts(self, credentials: dict[str, Any]) -> list[AccountInfo]:
        """Ad accounts these credentials can read. Used to verify a connection when it is added."""

    def verify_account(self, credentials: dict[str, Any], account_id: str) -> AccountInfo | None:
        """Proof that these credentials can read THIS account (None = cannot). Platforms whose tokens are
        tied to one account override this; the default looks the account up in list_accounts."""
        return next((a for a in self.list_accounts(credentials) if a.id == account_id), None)

    @abstractmethod
    def fetch_ad_metrics(
        self,
        credentials: dict[str, Any],
        account_id: str,
        since: date,
        until: date,
        options: dict[str, Any],
    ) -> list[StandardAdRow]:
        """Ad-level daily metrics for since..until inclusive. `options` carries per-connection
        settings such as conversion_action."""

    def refresh_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """Return new credentials if the access token was renewed, else None."""
        return None

    # --- Changing ads (only ever called after a person clicks Approve). A platform that supports it overrides these. ---
    supported_actions: tuple[str, ...] = ()     # subset of ("pause_ad", "raise_budget")

    def pause_ad(self, credentials: dict[str, Any], account_id: str, ad_id: str, ref: str) -> dict[str, Any]:
        """Pause one ad. Returns {"name": ...} describing what was paused."""
        raise ConnectorError(f"{self.display_name}: pausing ads from Adsorra is not available yet", "other")

    def raise_budget(self, credentials: dict[str, Any], account_id: str, ad_id: str, ref: str, pct: int) -> BudgetChange:
        """Raise the daily budget that pays for this ad (its ad set / ad group, or its campaign) by pct percent."""
        raise ConnectorError(f"{self.display_name}: changing budgets from Adsorra is not available yet", "other")

    # --- "Connect with <platform>" (OAuth). A platform that supports it overrides these three. ---
    @property
    def oauth_ready(self) -> bool:
        """True when this platform's app credentials are configured on the server."""
        return False

    def oauth_authorize_url(self, state: str, redirect_uri: str) -> str:
        raise NotImplementedError(f"{self.display_name}: OAuth connect is not built yet")

    def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Turn the one-time `code` into stored credentials (must include `access_token`)."""
        raise NotImplementedError(f"{self.display_name}: OAuth connect is not built yet")
