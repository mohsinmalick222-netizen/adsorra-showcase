from ranking import AdTotals, rank_ads


def ad(i, spend, conv=0, clicks=0, imps=1000, rev=0.0):
    return AdTotals(ad_id=str(i), platform="meta", name=f"ad{i}", campaign_name="c", creative_url="",
                    spend=spend, impressions=imps, clicks=clicks, conversions=conv, revenue=rev)


def names(lst):
    return [a.name for a in lst]


def test_min_spend_filters_lucky_small_ads():
    ads = [ad(1, 5, conv=5), ad(2, 100, conv=10), ad(3, 80, conv=4)]  # ad1 has the best CPA but spent only $5
    out = rank_ads(ads, "CPA", min_spend=50)
    assert names(out["top"]) == ["ad2", "ad3"]


def test_cpa_lower_is_better_and_zero_conversions_is_worst():
    ads = [ad(1, 100, conv=10), ad(2, 100, conv=20), ad(3, 100, conv=0), ad(4, 300, conv=0), ad(5, 100, conv=5)]
    out = rank_ads(ads, "CPA", min_spend=0, top_n=2, bottom_n=2)
    assert names(out["top"]) == ["ad2", "ad1"]
    # worst first: no conversions and the highest spend is the very worst
    assert names(out["bottom"]) == ["ad4", "ad3"]


def test_roas_and_ctr_higher_is_better():
    ads = [ad(1, 100, rev=500, clicks=10), ad(2, 100, rev=200, clicks=90), ad(3, 100, rev=900, clicks=50)]
    assert names(rank_ads(ads, "ROAS", 0)["top"])[0] == "ad3"
    assert names(rank_ads(ads, "CTR", 0)["top"])[0] == "ad2"


def test_top_and_bottom_never_overlap_with_few_ads():
    ads = [ad(i, 100, conv=i) for i in range(1, 5)]  # 4 ads
    out = rank_ads(ads, "CPA", 0)
    assert len(out["top"]) == 3 and len(out["bottom"]) == 1
    assert not set(names(out["top"])) & set(names(out["bottom"]))


def test_empty_and_zero_spend_ads_are_ignored():
    assert rank_ads([], "CPA", 0) == {"top": [], "bottom": []}
    assert rank_ads([ad(1, 0)], "CPA", 0) == {"top": [], "bottom": []}
