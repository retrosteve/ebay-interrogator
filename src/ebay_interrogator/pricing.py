from datetime import date, timedelta
from decimal import Decimal
from math import ceil
from typing import Iterable, Optional

from ebay_interrogator.matching import (
    is_complete_working_listing,
    matches_comparable,
)
from ebay_interrogator.models import (
    ActiveListing,
    DealAssessment,
    SoldComparable,
)


def assess_listing(
    listing: ActiveListing,
    comparables: Iterable[SoldComparable],
    *,
    fee_percent: Decimal,
    fixed_fee_gbp: Decimal,
    outbound_postage_gbp: Decimal,
    packaging_gbp: Decimal,
    uncertainty_buffer_gbp: Decimal,
    minimum_comparables: int = 3,
    lookback_days: int = 90,
    as_of: Optional[date] = None,
) -> Optional[DealAssessment]:
    if not is_complete_working_listing(listing):
        return None
    if minimum_comparables < 1 or lookback_days < 1:
        raise ValueError(
            "minimum_comparables and lookback_days must be positive"
        )

    today = as_of or date.today()
    cutoff = today - timedelta(days=lookback_days)
    valid_comparables = [
        comparable
        for comparable in comparables
        if cutoff <= comparable.sold_at <= today
        and matches_comparable(listing, comparable)
    ]
    if len(valid_comparables) < minimum_comparables:
        return None

    sale_values = sorted(item.gross_sale_gbp for item in valid_comparables)
    lower_quartile_index = max(0, ceil(len(sale_values) * 0.25) - 1)
    resale_estimate = sale_values[lower_quartile_index]
    fees = resale_estimate * fee_percent / Decimal("100") + fixed_fee_gbp
    net_profit = (
        resale_estimate
        - fees
        - listing.landed_cost_gbp
        - outbound_postage_gbp
        - packaging_gbp
        - uncertainty_buffer_gbp
    )

    return DealAssessment(
        listing=listing,
        comparable_count=len(valid_comparables),
        resale_estimate_gbp=resale_estimate,
        estimated_fees_gbp=fees,
        estimated_net_profit_gbp=net_profit,
    )
