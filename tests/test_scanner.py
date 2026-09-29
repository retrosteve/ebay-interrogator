import csv
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ebay_interrogator.cli import main
from ebay_interrogator.csv_data import (
    CsvDataError,
    read_active_listings,
    write_browse_listing_draft,
)
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
    @patch("ebay_interrogator.cli.EbayBrowseClient.from_environment")
    def test_browse_command_writes_raw_and_review_csv(
        self,
        mock_from_environment,
    ) -> None:
        response = {
            "total": 1,
            "itemSummaries": [
                {
                    "itemId": "v1|123|0",
                    "title": "Switch OLED bundle",
                    "itemWebUrl": "https://example.test/item/123",
                    "condition": "New",
                    "price": {"value": "115.00", "currency": "GBP"},
                    "shippingOptions": [
                        {
                            "shippingCost": {
                                "value": "4.50",
                                "currency": "GBP",
                            }
                        }
                    ],
                }
            ],
        }
        mock_from_environment.return_value.search_items.return_value = response
        mock_client = mock_from_environment.return_value
        mock_client.add_item_descriptions.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "response.json"
            draft_path = root / "active-listings-draft.csv"
            exit_code = main(
                [
                    "browse",
                    "--query",
                    "Switch OLED",
                    "--category-id",
                    "139971",
                    "--sort",
                    "price",
                    "--output",
                    str(raw_path),
                    "--draft-csv",
                    str(draft_path),
                ]
            )

            self.assertEqual(exit_code, 0)
            mock_search_items = mock_from_environment.return_value.search_items
            mock_search_items.assert_called_once_with(
                "Switch OLED",
                marketplace="EBAY_GB",
                limit=50,
                offset=0,
                category_id="139971",
                sort="price",
            )
            mock_client.add_item_descriptions.assert_called_once_with(
                response,
                marketplace="EBAY_GB",
            )
            self.assertEqual(
                json.loads(raw_path.read_text(encoding="utf-8")),
                response,
            )
            with draft_path.open(newline="", encoding="utf-8") as csv_file:
                row = next(csv.DictReader(csv_file))
            self.assertEqual(row["item_id"], "v1|123|0")
            self.assertEqual(row["price_gbp"], "115.00")
            self.assertEqual(row["model"], "")

    @patch("ebay_interrogator.cli.EbayBrowseClient.from_environment")
    def test_skips_description_requests_for_used_listings(
        self,
        mock_from_environment,
    ) -> None:
        response = {
            "itemSummaries": [
                {
                    "itemId": "used-item",
                    "title": "Switch OLED console",
                    "condition": "Used",
                }
            ]
        }
        mock_client = mock_from_environment.return_value
        mock_client.search_items.return_value = response

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            draft_path = root / "active-listings-draft.csv"
            exit_code = main(
                [
                    "browse",
                    "--query",
                    "Switch OLED",
                    "--output",
                    str(root / "response.json"),
                    "--draft-csv",
                    str(draft_path),
                ]
            )

            self.assertEqual(exit_code, 0)
            mock_client.add_item_descriptions.assert_not_called()
            with draft_path.open(newline="", encoding="utf-8") as csv_file:
                row = next(csv.DictReader(csv_file))
            self.assertEqual(row["triage_status"], "excluded")

    def test_browse_draft_leaves_unverified_fields_blank(self) -> None:
        response = {
            "itemSummaries": [
                {
                    "itemId": "v1|123|0",
                    "title": "Switch OLED bundle",
                    "itemWebUrl": "https://example.test/item/123",
                    "condition": "Used",
                    "price": {"value": "115.00", "currency": "GBP"},
                    "shippingOptions": [
                        {
                            "shippingCost": {
                                "value": "4.50",
                                "currency": "GBP",
                            }
                        }
                    ],
                },
                {
                    "itemId": "v1|456|0",
                    "title": "Console listing",
                    "itemWebUrl": "https://example.test/item/456",
                    "condition": "Used",
                    "price": {"value": "99.00", "currency": "EUR"},
                    "shippingOptions": [
                        {
                            "shippingCost": {
                                "value": "4.00",
                                "currency": "GBP",
                            }
                        },
                        {
                            "shippingCost": {
                                "value": "6.00",
                                "currency": "GBP",
                            }
                        },
                    ],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active-listings-draft.csv"
            count = write_browse_listing_draft(path, response)
            with path.open(newline="", encoding="utf-8") as csv_file:
                rows = list(csv.DictReader(csv_file))

            self.assertEqual(count, 2)
            self.assertEqual(rows[0]["price_gbp"], "115.00")
            self.assertEqual(rows[0]["shipping_gbp"], "4.50")
            self.assertEqual(rows[0]["model"], "")
            self.assertEqual(rows[0]["includes_dock"], "")
            self.assertEqual(rows[0]["working"], "")
            self.assertEqual(rows[1]["price_gbp"], "")
            self.assertEqual(rows[1]["shipping_gbp"], "")
            with self.assertRaisesRegex(CsvDataError, "model cannot be empty"):
                read_active_listings(path)

    def test_browse_draft_triages_console_risk_and_accessory_signals(
        self,
    ) -> None:
        response = {
            "itemSummaries": [
                {
                    "itemId": "console",
                    "title": (
                        "Nintendo Switch OLED Model HEG-001 Handheld Console"
                    ),
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "worn",
                    "title": (
                        "Nintendo Switch OLED Model HEG-001 Console "
                        "with Worn JoyCons"
                    ),
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "tablet-only",
                    "title": "Nintendo Switch OLED console Tablet Only HEG001",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "unit-only",
                    "title": "Nintendo Switch OLED HEG-001 Console UNIT ONLY",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "console-only",
                    "title": "Nintendo Switch OLED HEG-001 Console Only",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "no-cable",
                    "title": (
                        "Nintendo Switch OLED HEG-001 Console With Dock "
                        "and Grip, No Cable"
                    ),
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "screen-only",
                    "title": (
                        "Nintendo Switch OLED Console HEG-001 "
                        "Tablet Screen Only Tested Working"
                    ),
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "not-oled",
                    "title": "Nintendo Switch (not Oled)",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "faulty-condition",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "condition": "For parts or not working",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "condition-used",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "condition": "Used",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "condition-like-new",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "condition": "Like New",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "condition-open-box",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "condition": "Open box",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "condition-opened-never-used",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "condition": "Opened - never used",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "condition-missing",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                },
                {
                    "itemId": "description-missing-dock",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": (
                        "<p>Dock not included. Charger included and "
                        "Joy-Cons included. Untested.</p>"
                        "<script>Dock included and working</script>"
                    ),
                },
                {
                    "itemId": "description-positive",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": (
                        "<p>Includes dock, AC charger and Joy-Cons. "
                        "Tested working.</p>"
                    ),
                },
                {
                    "itemId": "description-unavailable",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "unavailable",
                },
                {
                    "itemId": "description-negated-risk",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": (
                        "Not faulty, not broken, no Joy-Con drift."
                    ),
                },
                {
                    "itemId": "description-expanded-faults",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": (
                        "Will not charge. Black screen, buttons not "
                        "responding, overheating, and liquid damage."
                    ),
                },
                {
                    "itemId": "description-negated-expanded-faults",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": (
                        "No black screen or water damage. Not banned "
                        "online; no stick drift."
                    ),
                },
                {
                    "itemId": "description-tablet-only",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": "Tablet only; no power supply.",
                },
                {
                    "itemId": "description-no-charging-lead",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": (
                        "Tested fully working. Does not include any further "
                        "accessories or leads. No charging lead included."
                    ),
                },
                {
                    "itemId": "description-wear-and-missing-originals",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "condition": "Used - Grade B",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": (
                        "Moderate wear, scratches and missing rubber grips. "
                        "No HDMI cable. Joy-Con straps are missing."
                    ),
                },
                {
                    "itemId": "description-good-without-box",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": (
                        "Great condition, tested working. Dock, charger, "
                        "Joy-Cons and HDMI cable included. Box not included."
                    ),
                },
                {
                    "itemId": "description-good-boxed",
                    "title": "Nintendo Switch OLED HEG-001 Console",
                    "leafCategoryIds": ["139971"],
                    "listingDescriptionStatus": "available",
                    "listingDescription": (
                        "Very good condition, original box included."
                    ),
                },
                {
                    "itemId": "screws",
                    "title": (
                        "Nintendo Switch OLED (HEG-001) Console "
                        "Full Complete Screw Screws Set"
                    ),
                    "leafCategoryIds": ["171833"],
                },
            ]
        }
        for summary in response["itemSummaries"]:
            if summary["itemId"] != "condition-missing":
                summary.setdefault("condition", "New")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active-listings-draft.csv"
            write_browse_listing_draft(path, response)
            with path.open(newline="", encoding="utf-8") as csv_file:
                rows = {
                    row["item_id"]: row
                    for row in csv.DictReader(csv_file)
                }

        self.assertEqual(rows["console"]["model"], "HEG-001")
        self.assertEqual(
            rows["console"]["triage_status"], "verification_needed"
        )
        self.assertIn(
            "verify complete dock, charger, and Joy-Con bundle",
            rows["console"]["triage_signals"],
        )
        self.assertIn(
            "verify working condition",
            rows["console"]["triage_signals"],
        )
        self.assertEqual(rows["console"]["quality_score"], "0")
        self.assertIn(
            "no description evidence",
            rows["console"]["quality_signals"],
        )
        self.assertEqual(
            rows["worn"]["triage_status"], "excluded"
        )
        self.assertIn("exclusion cue: worn", rows["worn"]["triage_signals"])
        for item_id, cue in (
            ("tablet-only", "tablet only"),
            ("unit-only", "unit only"),
            ("console-only", "console only"),
            ("screen-only", "screen only"),
            ("no-cable", "no cable"),
        ):
            self.assertEqual(
                rows[item_id]["triage_status"], "excluded"
            )
            self.assertIn(
                f"exclusion cue: {cue}",
                rows[item_id]["triage_signals"],
            )
        self.assertEqual(rows["screws"]["model"], "")
        self.assertEqual(
            rows["screws"]["triage_status"], "excluded"
        )
        self.assertIn(
            "accessory cue: screws",
            rows["screws"]["triage_signals"],
        )
        self.assertEqual(
            rows["not-oled"]["triage_status"], "excluded"
        )
        self.assertIn(
            "target mismatch cue: not oled",
            rows["not-oled"]["triage_signals"],
        )
        self.assertEqual(rows["not-oled"]["model"], "")
        self.assertEqual(
            rows["faulty-condition"]["triage_status"], "excluded"
        )
        for item_id in (
            "condition-used",
            "condition-missing",
        ):
            self.assertEqual(rows[item_id]["triage_status"], "excluded")
        for item_id in (
            "condition-like-new",
            "condition-open-box",
            "condition-opened-never-used",
        ):
            self.assertEqual(
                rows[item_id]["triage_status"], "verification_needed"
            )
        self.assertIn(
            "eligible condition: Like New",
            rows["condition-like-new"]["triage_signals"],
        )
        self.assertIn(
            "eligible condition: New",
            rows["console"]["triage_signals"],
        )
        self.assertIn(
            "exclusion cue: not working",
            rows["faulty-condition"]["triage_signals"],
        )
        self.assertEqual(
            rows["description-missing-dock"]["triage_status"], "excluded"
        )
        self.assertIn(
            "description says dock missing",
            rows["description-missing-dock"]["description_signals"],
        )
        self.assertIn(
            "description exclusion cue: missing dock",
            rows["description-missing-dock"]["triage_signals"],
        )
        self.assertIn(
            "description risk cue: untested",
            rows["description-missing-dock"]["description_signals"],
        )
        self.assertIn(
            "description exclusion cue: untested",
            rows["description-missing-dock"]["triage_signals"],
        )
        self.assertNotIn(
            "description mentions dock",
            rows["description-missing-dock"]["description_signals"],
        )
        self.assertEqual(
            rows["description-positive"]["triage_status"],
            "verification_needed",
        )
        self.assertIn(
            "description mentions dock",
            rows["description-positive"]["description_signals"],
        )
        self.assertIn(
            "description mentions tested/working",
            rows["description-positive"]["description_signals"],
        )
        self.assertEqual(
            rows["description-positive"]["quality_score"], "42"
        )
        self.assertIn(
            "seller claims dock included (+12)",
            rows["description-positive"]["quality_signals"],
        )
        self.assertEqual(
            rows["description-unavailable"]["description_status"],
            "unavailable",
        )
        self.assertEqual(
            rows["description-negated-risk"]["triage_status"],
            "verification_needed",
        )
        self.assertNotIn(
            "description exclusion cue",
            rows["description-negated-risk"]["triage_signals"],
        )
        self.assertEqual(
            rows["description-expanded-faults"]["triage_status"],
            "excluded",
        )
        for cue in (
            "will not charge",
            "black screen",
            "buttons not responding",
            "overheating",
            "liquid damage",
        ):
            self.assertIn(
                f"description risk cue: {cue}",
                rows["description-expanded-faults"]["description_signals"],
            )
        self.assertEqual(
            rows["description-negated-expanded-faults"]["triage_status"],
            "verification_needed",
        )
        self.assertNotIn(
            "description exclusion cue",
            rows["description-negated-expanded-faults"]["triage_signals"],
        )
        self.assertEqual(
            rows["description-tablet-only"]["triage_status"], "excluded"
        )
        self.assertEqual(
            rows["description-no-charging-lead"]["triage_status"],
            "excluded",
        )
        self.assertIn(
            "description says charger missing",
            rows["description-no-charging-lead"]["description_signals"],
        )
        self.assertEqual(
            rows["description-wear-and-missing-originals"]["triage_status"],
            "excluded",
        )
        for cue in (
            "moderate wear",
            "scratches",
            "missing rubber grips",
        ):
            self.assertIn(
                f"description risk cue: {cue}",
                rows["description-wear-and-missing-originals"][
                    "description_signals"
                ],
            )
        for label in ("HDMI cable", "Joy-Con accessories"):
            self.assertIn(
                f"description says {label} missing",
                rows["description-wear-and-missing-originals"][
                    "description_signals"
                ],
            )
        self.assertEqual(
            rows["description-good-without-box"]["triage_status"],
            "verification_needed",
        )
        self.assertIn(
            "description says box not included",
            rows["description-good-without-box"]["description_signals"],
        )
        self.assertEqual(
            rows["description-good-boxed"]["triage_status"],
            "verification_needed",
        )
        self.assertIn(
            "description mentions box",
            rows["description-good-boxed"]["description_signals"],
        )
        self.assertEqual(
            rows["description-good-without-box"]["quality_score"], "77"
        )
        self.assertEqual(
            rows["description-good-boxed"]["quality_score"], "40"
        )
        self.assertEqual(rows["worn"]["quality_score"], "")
        for row in rows.values():
            self.assertEqual(row["includes_dock"], "")
            self.assertEqual(row["includes_charger"], "")
            self.assertEqual(row["includes_joycons"], "")
            self.assertEqual(row["working"], "")

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
