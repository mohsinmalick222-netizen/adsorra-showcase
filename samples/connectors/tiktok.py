"""TikTok Ads connector (Business API, synchronous ad-level daily report). Not yet run against a live account.

One long-lived access token per advertiser account; the advertiser id is the account id."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from .base import AccountInfo, BudgetChange, Connector, ConnectorError, StandardAdRow, date_chunks, raised, redact
from .common import call, dec, read, to_int

BASE = "https://business-api.tiktok.com/open_api/v1.3"
AUTH_CODES = {40001, 40102, 40104, 40105}     # invalid/expired token or no permission
RATE_CODES = {40100}


def _msg(body: Any) -> str | None:
    return f"{body.get('code')} {body.get('message', '')}".strip() if isinstance(body, dict) else None


class TikTokConnector(Connector):
    platform = "tiktok"
    display_name = "TikTok Ads"
    beta = True
    supported_actions = ("pause_ad", "raise_budget")
    notes = ("Needs an access token with Reporting access for the advertiser account "
             "(and Ad Management access to allow approved pauses and budget changes).")
    account_label = "Advertiser ID"
    account_placeholder = "7000000000000000000"
    verify_hint = "Check the advertiser id and that the token was approved for that advertiser."

    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=45)

    def _get(self, path: str, token: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = call(self._http, "TikTok", "GET", f"{BASE}{path}", [token], headers={"Access-Token": token}, params=params)
        body = read(resp, "TikTok", [token], _msg)
        code = body.get("code", 0) if isinstance(body, dict) else -1
        if code != 0:
            kind = "auth" if code in AUTH_CODES else "rate_limit" if code in RATE_CODES else "other"
            raise ConnectorError(redact(f"TikTok error: {_msg(body)}", [token]), kind)
        return body.get("data") or {}

    def _post(self, path: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = call(self._http, "TikTok", "POST", f"{BASE}{path}", [token], headers={"Access-Token": token}, json=body)
        data = read(resp, "TikTok", [token], _msg)
        code = data.get("code", 0) if isinstance(data, dict) else -1
        if code != 0:
            kind = "auth" if code in AUTH_CODES else "rate_limit" if code in RATE_CODES else "other"
            raise ConnectorError(redact(f"TikTok error: {_msg(data)}", [token]), kind)
        return data.get("data") or {}

    def list_accounts(self, credentials: dict[str, Any]) -> list[AccountInfo]:
        return []   # a TikTok token is checked against one advertiser instead (verify_account)

    def verify_account(self, credentials: dict[str, Any], account_id: str) -> AccountInfo | None:
        data = self._get("/advertiser/info/", credentials["access_token"],
                         {"advertiser_ids": json.dumps([account_id]), "fields": json.dumps(["advertiser_id", "name"])})
        for a in data.get("list", []):
            if str(a.get("advertiser_id")) == account_id:
                return AccountInfo(id=account_id, name=a.get("name", ""))
        return None

    def fetch_ad_metrics(self, credentials: dict[str, Any], account_id: str, since: date, until: date,
                         options: dict[str, Any]) -> list[StandardAdRow]:
        token = credentials["access_token"]
        value_metric = (options.get("conversion_action") or "").strip()   # optional: a TikTok value metric to use as revenue
        metrics = ["ad_name", "campaign_name", "spend", "impressions", "clicks", "conversion"] + ([value_metric] if value_metric else [])
        rows: list[StandardAdRow] = []
        for start, end in date_chunks(since, until, 30):
            page = 1
            while True:
                data = self._get("/report/integrated/get/", token, {
                    "advertiser_id": account_id, "report_type": "BASIC", "data_level": "AUCTION_AD",
                    "dimensions": json.dumps(["ad_id", "stat_time_day"]), "metrics": json.dumps(metrics),
                    "start_date": start.isoformat(), "end_date": end.isoformat(), "page": page, "page_size": 1000})
                for r in data.get("list", []):
                    d, m = r.get("dimensions") or {}, r.get("metrics") or {}
                    if not d.get("ad_id"):
                        continue
                    rows.append(StandardAdRow(
                        date=datetime.fromisoformat(str(d["stat_time_day"]).replace(" ", "T")).date(),
                        external_ad_id=str(d["ad_id"]), ad_name=m.get("ad_name") or f"Ad {d['ad_id']}",
                        campaign_name=m.get("campaign_name", ""), creative_url="",
                        spend=dec(m.get("spend")), impressions=to_int(m.get("impressions")), clicks=to_int(m.get("clicks")),
                        conversions=dec(m.get("conversion")), revenue=dec(m.get(value_metric)) if value_metric else dec(0)))
                total = int((data.get("page_info") or {}).get("total_page") or 1)
                if page >= total:
                    break
                page += 1
        return rows

    # ----- Changes (only after a person approves them in Adsorra) ------------------------
    def _ad(self, token: str, advertiser: str, ad_id: str) -> dict[str, Any]:
        data = self._get("/ad/get/", token, {"advertiser_id": advertiser, "filtering": json.dumps({"ad_ids": [ad_id]}),
                                              "fields": json.dumps(["ad_id", "ad_name", "adgroup_id"])})
        found = data.get("list") or []
        if not found:
            raise ConnectorError("TikTok could not find this ad any more", "other")
        return found[0]

    def pause_ad(self, credentials: dict[str, Any], account_id: str, ad_id: str, ref: str) -> dict[str, Any]:
        token = credentials["access_token"]
        ad = self._ad(token, account_id, ad_id)
        self._post("/ad/status/update/", token, {"advertiser_id": account_id, "ad_ids": [ad_id], "operation_status": "DISABLE"})
        return {"name": ad.get("ad_name", "")}

    def raise_budget(self, credentials: dict[str, Any], account_id: str, ad_id: str, ref: str, pct: int) -> BudgetChange:
        token = credentials["access_token"]
        group_id = str(self._ad(token, account_id, ad_id).get("adgroup_id", ""))
        data = self._get("/adgroup/get/", token, {"advertiser_id": account_id, "filtering": json.dumps({"adgroup_ids": [group_id]}),
                                                   "fields": json.dumps(["adgroup_id", "adgroup_name", "budget", "budget_mode"])})
        group = (data.get("list") or [{}])[0]
        if group.get("budget_mode") not in ("BUDGET_MODE_DAY", "BUDGET_MODE_DYNAMIC_DAILY_BUDGET") or not group.get("budget"):
            raise ConnectorError("This ad group has no daily budget of its own (the campaign or a lifetime budget controls it). "
                                 "Change it in TikTok Ads Manager.", "other")
        old = Decimal(str(group["budget"]))
        new = raised(old, pct)
        self._post("/adgroup/budget/update/", token, {"advertiser_id": account_id, "budget": [{"adgroup_id": group_id, "budget": float(new)}]})
        return BudgetChange(level="ad group", name=group.get("adgroup_name", ""), old=old, new=new)
