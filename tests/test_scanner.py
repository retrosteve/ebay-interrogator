import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from ebay_interrogator.cli import main
from ebay_interrogator.csv_data import CsvDataError, read_active_listings
from ebay_interrogator.models import ActiveListing, SoldComparable
from ebay_interrogator.pricing import assess_listing


def make_listing(**overrides: object) -> ActiveListing:
    values: dict[str, object] = {
        "item_id": "123",
        "title": "Nintendo Switch OLED HEG-001 complete bundle",
        "url": "https://example.test/item/123",
        "model": "HEG-001",
        "condition": "Used - Good",
        "price_gbp": Decimal("120"),
        "shipping_gbp": Decimal("5"),
        "includes_dock": True,
        "includes_charger": True,
        "includes_joycons": True,
        "working": True,
    }
    values.update(overrides)
    return ActiveListing(**values)  # type: ignore[arg-type]


def make_comparable(sold_price: str, **overrides: object) -> SoldComparable:
    values: dict[str, object] = {
        "model": "HEG-001",
        "condition": "Used - Good",
        "sold_price_gbp": Decimal(sold_price),
        "shipping_gbp": Decimal("5"),
        "sold_at": date(2026, 9, 20),
        "includes_dock": True,
        "includes_charger": True,
        "includes_joycons": True,
        "working": True,
    }
    values.update(overrides)
    return SoldComparable(**values)  # type: ignore[arg-type]


class PricingTests(unittest.TestCase):
    def test_uses_lower_quartile_and_subtracts_costs(self) -> None:
        comps = [
            make_comparable(value)
            for value in ("200", "220", "180", "210")
        ]
        result = assess_listing(
            make_listing(),
            comps,
            fee_percent=Decimal("13"),
            fixed_fee_gbp=Decimal("0.30"),
            outbound_postage_gbp=Decimal("5"),
            packaging_gbp=Decimal("1"),
            uncertainty_buffer_gbp=Decimal("4"),
            as_of=date(2026, 9, 27),
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.resale_estimate_gbp, Decimal("185"))
        self.assertEqual(result.estimated_fees_gbp, Decimal("24.35"))
        self.assertEqual(result.estimated_net_profit_gbp, Decimal("25.65"))

    def test_excludes_incomplete_listings_and_comparables(self) -> None:
        result = assess_listing(
            make_listing(includes_dock=False),
            [make_comparable("200") for _ in range(3)],
            fee_percent=Decimal("13"),
            fixed_fee_gbp=Decimal("0"),
            outbound_postage_gbp=Decimal("5"),
            packaging_gbp=Decimal("0"),
            uncertainty_buffer_gbp=Decimal("0"),
        )
        self.assertIsNone(result)

        result = assess_listing(
            make_listing(),
            [make_comparable("200", includes_charger=False) for _ in range(3)],
            fee_percent=Decimal("13"),
            fixed_fee_gbp=Decimal("0"),
            outbound_postage_gbp=Decimal("5"),
            packaging_gbp=Decimal("0"),
            uncertainty_buffer_gbp=Decimal("0"),
        )
        self.assertIsNone(result)

    def test_requires_recent_condition_matched_comparables(self) -> None:
        comps = [
            make_comparable("200"),
            make_comparable("205", sold_at=date(2026, 1, 1)),
            make_comparable("210", condition="Used - Acceptable"),
        ]
        result = assess_listing(
            make_listing(),
            comps,
            fee_percent=Decimal("13"),
            fixed_fee_gbp=Decimal("0"),
            outbound_postage_gbp=Decimal("5"),
            packaging_gbp=Decimal("0"),
            uncertainty_buffer_gbp=Decimal("0"),
            as_of=date(2026, 9, 27),
        )
        self.assertIsNone(result)


class CsvAndCliTests(unittest.TestCase):
    def test_rejects_missing_csv_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "listings.csv"
            path.write_text("item_id,title\n1,Console\n", encoding="utf-8")
            with self.assertRaisesRegex(
                CsvDataError,
                "missing required columns",
            ):
                read_active_listings(path)

    def test_cli_ranks_candidates_and_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            listings_path = root / "listings.csv"
            comps_path = root / "comps.csv"
            output_path = root / "results.csv"
            listing_fields = [
                "item_id", "title", "url", "model", "condition",
                "price_gbp", "shipping_gbp", "includes_dock",
                "includes_charger", "includes_joycons", "working",
            ]
            comp_fields = [
                "model", "condition", "sold_price_gbp", "shipping_gbp",
                "sold_at", "includes_dock", "includes_charger",
                "includes_joycons", "working",
            ]
            with listings_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=listing_fields)
                writer.writeheader()
                for item_id, price in (("low", "100"), ("high", "110")):
                    writer.writerow(
                        {
                            "item_id": item_id,
                            "title": f"Console {item_id}",
                            "url": f"https://example.test/{item_id}",
                            "model": "HEG-001",
                            "condition": "Used - Good",
                            "price_gbp": price,
                            "shipping_gbp": "5",
                            "includes_dock": "true",
                            "includes_charger": "true",
                            "includes_joycons": "true",
                            "working": "true",
                        }
                    )
            with comps_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=comp_fields)
                writer.writeheader()
                for sold_price in ("200", "205", "210"):
                    writer.writerow(
                        {
                            "model": "HEG-001",
                            "condition": "Used - Good",
                            "sold_price_gbp": sold_price,
                            "shipping_gbp": "5",
                            "sold_at": (
                                date.today() - timedelta(days=1)
                            ).isoformat(),
                            "includes_dock": "true",
                            "includes_charger": "true",
                            "includes_joycons": "true",
                            "working": "true",
                        }
                    )

            exit_code = main(
                [
                    "scan",
                    "--listings", str(listings_path),
                    "--comps", str(comps_path),
                    "--fee-percent", "13",
                    "--outbound-postage-gbp", "5",
                    "--output", str(output_path),
                ]
            )
            self.assertEqual(exit_code, 0)
            with output_path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual([row["item_id"] for row in rows], ["low", "high"])


if __name__ == "__main__":
    unittest.main()
