import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, TypeVar

from ebay_interrogator.models import (
    ActiveListing,
    DealAssessment,
    SoldComparable,
)


class CsvDataError(ValueError):
    """Raised when an input CSV is missing or contains invalid data."""


T = TypeVar("T")

ACTIVE_CSV_FIELDS = [
    "item_id",
    "title",
    "url",
    "model",
    "condition",
    "price_gbp",
    "shipping_gbp",
    "includes_dock",
    "includes_charger",
    "includes_joycons",
    "working",
]
ACTIVE_FIELDS = set(ACTIVE_CSV_FIELDS)
SOLD_FIELDS = {
    "model",
    "condition",
    "sold_price_gbp",
    "shipping_gbp",
    "sold_at",
    "includes_dock",
    "includes_charger",
    "includes_joycons",
    "working",
}
OUTPUT_FIELDS = [
    "item_id",
    "title",
    "url",
    "condition",
    "asking_price_gbp",
    "inbound_shipping_gbp",
    "resale_estimate_gbp",
    "estimated_fees_gbp",
    "estimated_net_profit_gbp",
    "comparable_count",
    "confidence",
]


def _read_rows(
    path: Path,
    required_fields: set[str],
) -> list[tuple[int, dict[str, str]]]:
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            if reader.fieldnames is None:
                raise CsvDataError(f"{path}: missing CSV header")
            missing = required_fields - set(reader.fieldnames)
            if missing:
                fields = ", ".join(sorted(missing))
                raise CsvDataError(
                    f"{path}: missing required columns: {fields}"
                )
            rows = []
            for line_number, row in enumerate(reader, start=2):
                clean_row = {
                    key: value or ""
                    for key, value in row.items()
                    if key
                }
                rows.append((line_number, clean_row))
            return rows
    except OSError as error:
        raise CsvDataError(f"{path}: {error}") from error


def _text(
    row: dict[str, str],
    field: str,
    line_number: int,
    path: Path,
) -> str:
    value = row.get(field, "").strip()
    if not value:
        raise CsvDataError(f"{path}:{line_number}: {field} cannot be empty")
    return value


def _money(
    row: dict[str, str],
    field: str,
    line_number: int,
    path: Path,
) -> Decimal:
    value = _text(row, field, line_number, path)
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise CsvDataError(
            f"{path}:{line_number}: {field} must be a number"
        ) from error
    if not amount.is_finite() or amount < 0:
        raise CsvDataError(
            f"{path}:{line_number}: {field} must be a non-negative amount"
        )
    return amount


def _boolean(
    row: dict[str, str],
    field: str,
    line_number: int,
    path: Path,
) -> bool:
    value = _text(row, field, line_number, path).casefold()
    if value in {"true", "yes", "1"}:
        return True
    if value in {"false", "no", "0"}:
        return False
    raise CsvDataError(
        f"{path}:{line_number}: {field} must be true/false, yes/no, or 1/0"
    )


def _browse_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _browse_money(value: object) -> str:
    if not isinstance(value, dict) or value.get("currency") != "GBP":
        return ""
    amount_value = value.get("value")
    if amount_value is None or isinstance(amount_value, bool):
        return ""
    try:
        amount = Decimal(str(amount_value))
    except InvalidOperation:
        return ""
    if not amount.is_finite() or amount < 0:
        return ""
    return format(amount, "f")


def _browse_shipping(options: object) -> str:
    if not isinstance(options, list) or len(options) != 1:
        return ""
    option = options[0]
    if not isinstance(option, dict):
        return ""
    return _browse_money(option.get("shippingCost"))


def write_browse_listing_draft(
    path: Path,
    response: dict[str, object],
) -> int:
    summaries = response.get("itemSummaries", [])
    if not isinstance(summaries, list) or any(
        not isinstance(summary, dict) for summary in summaries
    ):
        raise CsvDataError(
            "Browse API response contains invalid item summaries"
        )

    try:
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=ACTIVE_CSV_FIELDS)
            writer.writeheader()
            for summary in summaries:
                writer.writerow(
                    {
                        "item_id": _browse_text(summary.get("itemId")),
                        "title": _browse_text(summary.get("title")),
                        "url": _browse_text(summary.get("itemWebUrl")),
                        "model": "",
                        "condition": _browse_text(summary.get("condition")),
                        "price_gbp": _browse_money(summary.get("price")),
                        "shipping_gbp": _browse_shipping(
                            summary.get("shippingOptions")
                        ),
                        "includes_dock": "",
                        "includes_charger": "",
                        "includes_joycons": "",
                        "working": "",
                    }
                )
    except OSError as error:
        raise CsvDataError(f"{path}: {error}") from error
    return len(summaries)


def _parse_rows(
    path: Path,
    required_fields: set[str],
    parser: Callable[[dict[str, str], int, Path], T],
) -> list[T]:
    return [
        parser(row, line_number, path)
        for line_number, row in _read_rows(path, required_fields)
    ]


def read_active_listings(path: Path) -> list[ActiveListing]:
    def parse(row: dict[str, str], line: int, source: Path) -> ActiveListing:
        return ActiveListing(
            item_id=_text(row, "item_id", line, source),
            title=_text(row, "title", line, source),
            url=_text(row, "url", line, source),
            model=_text(row, "model", line, source),
            condition=_text(row, "condition", line, source),
            price_gbp=_money(row, "price_gbp", line, source),
            shipping_gbp=_money(row, "shipping_gbp", line, source),
            includes_dock=_boolean(row, "includes_dock", line, source),
            includes_charger=_boolean(row, "includes_charger", line, source),
            includes_joycons=_boolean(row, "includes_joycons", line, source),
            working=_boolean(row, "working", line, source),
        )

    return _parse_rows(path, ACTIVE_FIELDS, parse)


def read_sold_comparables(path: Path) -> list[SoldComparable]:
    def parse(row: dict[str, str], line: int, source: Path) -> SoldComparable:
        sold_at_value = _text(row, "sold_at", line, source)
        try:
            sold_at = date.fromisoformat(sold_at_value)
        except ValueError as error:
            raise CsvDataError(
                f"{source}:{line}: sold_at must use YYYY-MM-DD"
            ) from error
        return SoldComparable(
            model=_text(row, "model", line, source),
            condition=_text(row, "condition", line, source),
            sold_price_gbp=_money(row, "sold_price_gbp", line, source),
            shipping_gbp=_money(row, "shipping_gbp", line, source),
            sold_at=sold_at,
            includes_dock=_boolean(row, "includes_dock", line, source),
            includes_charger=_boolean(row, "includes_charger", line, source),
            includes_joycons=_boolean(row, "includes_joycons", line, source),
            working=_boolean(row, "working", line, source),
        )

    return _parse_rows(path, SOLD_FIELDS, parse)


def write_assessments(path: Path, assessments: list[DealAssessment]) -> None:
    try:
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            for assessment in assessments:
                listing = assessment.listing
                writer.writerow(
                    {
                        "item_id": listing.item_id,
                        "title": listing.title,
                        "url": listing.url,
                        "condition": listing.condition,
                        "asking_price_gbp": f"{listing.price_gbp:.2f}",
                        "inbound_shipping_gbp": f"{listing.shipping_gbp:.2f}",
                        "resale_estimate_gbp": (
                            f"{assessment.resale_estimate_gbp:.2f}"
                        ),
                        "estimated_fees_gbp": (
                            f"{assessment.estimated_fees_gbp:.2f}"
                        ),
                        "estimated_net_profit_gbp": (
                            f"{assessment.estimated_net_profit_gbp:.2f}"
                        ),
                        "comparable_count": assessment.comparable_count,
                        "confidence": assessment.confidence,
                    }
                )
    except OSError as error:
        raise CsvDataError(f"{path}: {error}") from error
