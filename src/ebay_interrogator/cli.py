import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import List, Optional

from ebay_interrogator.csv_data import (
    CsvDataError,
    read_active_listings,
    read_sold_comparables,
    write_assessments,
)
from ebay_interrogator.ebay_api import EbayApiError, EbayBrowseClient
from ebay_interrogator.models import DealAssessment
from ebay_interrogator.pricing import assess_listing


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ebay-interrogator",
        description=(
            "Rank complete Nintendo Switch OLED HEG-001 listings "
            "by estimated profit."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser(
        "scan",
        help="analyze listing and sold-comparable CSV files",
    )
    scan.add_argument(
        "--listings", type=Path, required=True, help="active listings CSV"
    )
    scan.add_argument(
        "--comps", type=Path, required=True, help="sold comparables CSV"
    )
    scan.add_argument(
        "--fee-percent",
        type=Decimal,
        required=True,
        help="estimated marketplace fee percentage",
    )
    scan.add_argument("--fixed-fee-gbp", type=Decimal, default=Decimal("0"))
    scan.add_argument("--outbound-postage-gbp", type=Decimal, required=True)
    scan.add_argument("--packaging-gbp", type=Decimal, default=Decimal("0"))
    scan.add_argument("--buffer-gbp", type=Decimal, default=Decimal("0"))
    scan.add_argument(
        "--minimum-profit-gbp", type=Decimal, default=Decimal("0")
    )
    scan.add_argument("--minimum-comparables", type=int, default=3)
    scan.add_argument("--lookback-days", type=int, default=90)
    scan.add_argument(
        "--output", type=Path, help="write ranked results to this CSV path"
    )
    browse = subparsers.add_parser(
        "browse",
        help="search active eBay listings and save the raw API response",
    )
    browse.add_argument(
        "--query", required=True, help="eBay listing search text"
    )
    browse.add_argument(
        "--environment",
        choices=("sandbox", "production"),
        default="sandbox",
    )
    browse.add_argument("--marketplace", default="EBAY_GB")
    browse.add_argument("--limit", type=int, default=50)
    browse.add_argument("--offset", type=int, default=0)
    browse.add_argument(
        "--output",
        type=Path,
        default=Path("active-listings.json"),
    )
    return parser


def _validate_options(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    if not Decimal("0") <= args.fee_percent <= Decimal("100"):
        parser.error("--fee-percent must be between 0 and 100")
    cost_options = (
        "fixed_fee_gbp",
        "outbound_postage_gbp",
        "packaging_gbp",
        "buffer_gbp",
        "minimum_profit_gbp",
    )
    for name in cost_options:
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
    if args.minimum_comparables < 1:
        parser.error("--minimum-comparables must be at least 1")
    if args.lookback_days < 1:
        parser.error("--lookback-days must be at least 1")


def _print_assessments(assessments: List[DealAssessment]) -> None:
    if not assessments:
        print("No qualifying listings found.")
        return
    for assessment in assessments:
        listing = assessment.listing
        print(
            f"GBP {assessment.estimated_net_profit_gbp:.2f} net | "
            f"{assessment.confidence} confidence | "
            f"{assessment.comparable_count} sold comps"
        )
        print(f"  {listing.title}")
        print(
            f"  Asking GBP {listing.price_gbp:.2f} + "
            f"shipping GBP {listing.shipping_gbp:.2f}"
        )
        print(
            f"  Estimated resale GBP {assessment.resale_estimate_gbp:.2f}; "
            f"fees GBP {assessment.estimated_fees_gbp:.2f}"
        )
        print(f"  {listing.url}")


def _browse(args: argparse.Namespace) -> int:
    client = EbayBrowseClient.from_environment(args.environment)
    response = client.search_items(
        args.query,
        marketplace=args.marketplace,
        limit=args.limit,
        offset=args.offset,
    )
    args.output.write_text(
        json.dumps(response, indent=2),
        encoding="utf-8",
    )
    summaries = response.get("itemSummaries", [])
    item_count = len(summaries) if isinstance(summaries, list) else 0
    print(f"Retrieved {item_count} active listing(s).")
    print(f"Saved raw Browse API response to {args.output}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "browse":
            return _browse(args)

        _validate_options(parser, args)
        listings = read_active_listings(args.listings)
        comparables = read_sold_comparables(args.comps)
        assessments = [
            assessment
            for listing in listings
            if (
                assessment := assess_listing(
                    listing,
                    comparables,
                    fee_percent=args.fee_percent,
                    fixed_fee_gbp=args.fixed_fee_gbp,
                    outbound_postage_gbp=args.outbound_postage_gbp,
                    packaging_gbp=args.packaging_gbp,
                    uncertainty_buffer_gbp=args.buffer_gbp,
                    minimum_comparables=args.minimum_comparables,
                    lookback_days=args.lookback_days,
                )
            ) is not None
            and assessment.estimated_net_profit_gbp >= args.minimum_profit_gbp
        ]
        assessments.sort(
            key=lambda result: result.estimated_net_profit_gbp,
            reverse=True,
        )
        _print_assessments(assessments)
        if args.output:
            write_assessments(args.output, assessments)
            print(f"Wrote {len(assessments)} result(s) to {args.output}")
    except (CsvDataError, EbayApiError, OSError) as error:
        print(error, file=sys.stderr)
        return 2
    return 0
