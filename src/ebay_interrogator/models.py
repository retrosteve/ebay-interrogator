from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ActiveListing:
    item_id: str
    title: str
    url: str
    model: str
    condition: str
    price_gbp: Decimal
    shipping_gbp: Decimal
    includes_dock: bool
    includes_charger: bool
    includes_joycons: bool
    working: bool

    @property
    def landed_cost_gbp(self) -> Decimal:
        return self.price_gbp + self.shipping_gbp


@dataclass(frozen=True)
class SoldComparable:
    model: str
    condition: str
    sold_price_gbp: Decimal
    shipping_gbp: Decimal
    sold_at: date
    includes_dock: bool
    includes_charger: bool
    includes_joycons: bool
    working: bool

    @property
    def gross_sale_gbp(self) -> Decimal:
        return self.sold_price_gbp + self.shipping_gbp


@dataclass(frozen=True)
class DealAssessment:
    listing: ActiveListing
    comparable_count: int
    resale_estimate_gbp: Decimal
    estimated_fees_gbp: Decimal
    estimated_net_profit_gbp: Decimal

    @property
    def confidence(self) -> str:
        if self.comparable_count < 5:
            return "low"
        if self.comparable_count < 10:
            return "medium"
        return "high"
