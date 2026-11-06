"""Creative ranking, ported from the Apps Script prototype.

Ads below the client's minimum spend are ignored (so a lucky $5 ad cannot win). The rest are
ranked by the client's goal: lowest CPA, highest ROAS or highest CTR. Ads that spent money
with no conversions count as the worst on CPA.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

INF = float("inf")


@dataclass
class AdTotals:
    ad_id: str
    platform: str
    name: str
    campaign_name: str
    creative_url: str
    spend: float = 0.0
    impressions: int = 0
    clicks: int = 0
    conversions: float = 0.0
    revenue: float = 0.0

    @property
    def cpa(self) -> float | None:
        return self.spend / self.conversions if self.conversions else None

    @property
    def roas(self) -> float:
        return self.revenue / self.spend if self.spend else 0.0

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ad_id": self.ad_id, "platform": self.platform, "name": self.name,
            "campaign_name": self.campaign_name, "creative_url": self.creative_url,
            "spend": round(self.spend, 2), "impressions": self.impressions, "clicks": self.clicks,
            "conversions": round(self.conversions, 2), "revenue": round(self.revenue, 2),
            "cpa": None if self.cpa is None else round(self.cpa, 2),
            "roas": round(self.roas, 2), "ctr": round(self.ctr, 4),
        }


def _sort_key(goal: str):
    goal = goal.upper()

    def key(a: AdTotals) -> tuple[float, float]:
        if goal == "ROAS":
            return (-a.roas, -a.spend)
        if goal == "CTR":
            return (-a.ctr, -a.spend)
        # CPA: no conversions = worst; among those, higher spend is worse
        cpa = a.cpa
        return (INF, a.spend) if cpa is None else (cpa, -a.spend)

    return key


def rank_ads(ads: list[AdTotals], goal: str, min_spend: float,
             top_n: int = 3, bottom_n: int = 3) -> dict[str, list[AdTotals]]:
    eligible = [a for a in ads if a.spend >= min_spend and a.spend > 0]
    ranked = sorted(eligible, key=_sort_key(goal))
    top = ranked[:top_n]
    bottom = list(reversed(ranked[max(top_n, len(ranked) - bottom_n):]))  # worst first, never overlaps top
    return {"top": top, "bottom": bottom}
