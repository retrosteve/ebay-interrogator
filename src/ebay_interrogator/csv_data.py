import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
import re
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
BROWSE_DRAFT_FIELDS = ACTIVE_CSV_FIELDS + [
    "browse_category_id",
    "triage_status",
    "triage_signals",
    "description_status",
    "description_signals",
    "quality_score",
    "quality_signals",
]
ACTIVE_FIELDS = set(ACTIVE_CSV_FIELDS)
TARGET_MODEL = "HEG-001"
TARGET_CONSOLE_CATEGORY_ID = "139971"
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


def _browse_condition_class(condition: str) -> str:
    normalized = condition.casefold().strip()
    normalized = re.sub(r"[\u2010-\u2015]", "-", normalized)
    normalized = " ".join(normalized.split())
    if normalized in {"new", "brand new"}:
        return "New"
    if normalized == "like new":
        return "Like New"
    if normalized in {
        "open box",
        "open box - never used",
        "opened - never used",
        "opened box - never used",
        "opened, never used",
        "new - open box",
    }:
        return "Open Box / never used"
    return ""


def is_browse_condition_eligible(condition: object) -> bool:
    return bool(_browse_condition_class(_browse_text(condition)))


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


def _browse_category_ids(summary: dict[str, object]) -> list[str]:
    leaf_ids = summary.get("leafCategoryIds", [])
    if isinstance(leaf_ids, list):
        category_ids = [str(value) for value in leaf_ids if value is not None]
        if category_ids:
            return category_ids

    categories = summary.get("categories", [])
    if not isinstance(categories, list):
        return []
    return [
        str(category["categoryId"])
        for category in categories
        if (
            isinstance(category, dict)
            and category.get("categoryId") is not None
        )
    ]


class _ListingDescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in {"script", "style"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.text_parts.append(data)


def _has_unnegated_phrase(text: str, phrase: str) -> bool:
    for match in re.finditer(rf"\b{re.escape(phrase)}\b", text):
        preceding_text = text[max(0, match.start() - 40):match.start()]
        if re.search(
            r"\b(?:no|not|never|without)\s+"
            r"(?:[\w'-]+\s+){0,3}$",
            preceding_text,
        ):
            continue
        return True
    return False


def _browse_description_evidence(
    summary: dict[str, object],
) -> tuple[str, list[str], list[str]]:
    description = summary.get("listingDescription")
    if not isinstance(description, str) or not description.strip():
        status = summary.get("listingDescriptionStatus")
        if status == "unavailable":
            return "unavailable", ["listing description unavailable"], []
        return "not_retrieved", ["listing description not retrieved"], []

    parser = _ListingDescriptionParser()
    parser.feed(description)
    text = " ".join(" ".join(parser.text_parts).split()).casefold()
    signals = ["seller description fetched"]
    exclusions = []
    components = (
        ("dock", r"(?:dock|docking station)"),
        (
            "charger",
            r"(?:charger|charging adapter|charging cable|charging lead|"
            r"power adapter|power supply|ac adapter|usb[ -]?c cable)",
        ),
        ("Joy-Cons", r"(?:joy[ -]?cons?|controllers?)"),
        ("HDMI cable", r"(?:hdmi cable|hdmi lead)"),
        (
            "Joy-Con accessories",
            r"(?:joy[ -]?con (?:grip|straps?)|wrist straps?)",
        ),
    )
    for label, component_pattern in components:
        missing_pattern = (
            rf"\b(?:no|without|missing|excluding)\s+(?:the\s+)?"
            rf"{component_pattern}\b(?!\s+drift\b)|"
            rf"\b{component_pattern}\s+(?:(?:is|are)\s+)?"
            r"(?:not included|missing|excluded|not supplied)\b"
        )
        if re.search(missing_pattern, text):
            signals.append(f"description says {label} missing")
            exclusions.append(
                f"description exclusion cue: missing {label}"
            )
        elif re.search(rf"\b{component_pattern}\b", text):
            signals.append(f"description mentions {label}")

    box_missing_pattern = (
        r"\b(?:no|without|missing)\s+(?:(?:original|retail)\s+)?box\b|"
        r"\bbox\s+(?:is\s+)?(?:not included|missing|not supplied)\b"
    )
    if re.search(box_missing_pattern, text):
        signals.append("description says box not included")
    elif re.search(
        r"\b(?:original|retail)\s+box\b|\bboxed\b|"
        r"\bbox\s+(?:is\s+)?included\b",
        text,
    ):
        signals.append("description mentions box")

    description_risk_terms = (
        "untested",
        "not working",
        "does not work",
        "doesn't work",
        "faulty",
        "broken",
        "for parts",
        "spares",
        "damaged",
        "cracked",
        "scratched",
        "scratches",
        "scuffed",
        "scuffs",
        "moderate wear",
        "heavy wear",
        "missing rubber grips",
        "missing thumb grips",
        "grade b",
        "grade c",
        "screen burn",
        "black screen",
        "dead pixel",
        "dead pixels",
        "won't turn on",
        "will not turn on",
        "doesn't turn on",
        "does not turn on",
        "won't charge",
        "will not charge",
        "doesn't charge",
        "does not charge",
        "not charging",
        "charging issue",
        "analog stick drift",
        "joystick drift",
        "stick drift",
        "joy-con drift",
        "joycon drift",
        "buttons not working",
        "button not working",
        "buttons not responding",
        "button not responding",
        "overheating",
        "water damage",
        "liquid damage",
        "banned from online",
        "banned online",
        "online ban",
        "console ban",
        "tablet only",
        "console only",
        "handheld only",
    )
    for term in description_risk_terms:
        if _has_unnegated_phrase(text, term):
            signals.append(f"description risk cue: {term}")
            exclusions.append(f"description exclusion cue: {term}")

    if re.search(r"\b(tested|working|works perfectly)\b", text):
        signals.append("description mentions tested/working")
    if len(signals) == 1:
        signals.append("no automatic bundle or fault claim detected")
    return "available", signals, exclusions


def _browse_quality_score(
    summary: dict[str, object],
    triage_status: str,
) -> tuple[str, str]:
    if triage_status == "excluded":
        return "", "not scored: excluded by hard triage gate"

    description = summary.get("listingDescription")
    if not isinstance(description, str) or not description.strip():
        return "0", "no description evidence; verify manually"

    parser = _ListingDescriptionParser()
    parser.feed(description)
    text = " ".join(" ".join(parser.text_parts).split()).casefold()
    score = 0
    signals = []
    condition_claims = (
        (40, ("like new", "like-new"), "like new"),
        (40, ("mint condition", "pristine"), "mint/pristine"),
        (35, ("excellent condition",), "excellent condition"),
        (30, ("very good condition",), "very good condition"),
        (30, ("great condition",), "great condition"),
        (20, ("good condition",), "good condition"),
    )
    for points, phrases, label in condition_claims:
        if any(_has_unnegated_phrase(text, phrase) for phrase in phrases):
            score += points
            signals.append(f"seller claims {label} (+{points}; unverified)")
            break

    included_components = (
        (12, "dock", r"(?:dock|docking station)"),
        (
            12,
            "charger",
            r"(?:charger|charging adapter|charging cable|charging lead|"
            r"power adapter|power supply|ac adapter|usb[ -]?c cable)",
        ),
        (12, "Joy-Cons", r"(?:joy[ -]?cons?|controllers?)"),
        (5, "HDMI cable", r"(?:hdmi cable|hdmi lead)"),
        (
            5,
            "Joy-Con accessories",
            r"(?:joy[ -]?con (?:grip|straps?)|wrist straps?)",
        ),
    )
    inclusion_prefix = (
        r"\b(?:includes?|including|comes with|come with|supplied with|"
        r"complete with)\b[^.;]{0,80}\b"
    )
    for points, label, component_pattern in included_components:
        inclusion_pattern = (
            inclusion_prefix
            + component_pattern
            + r"\b|\b"
            + component_pattern
            + r"\s+(?:(?:is|are)\s+)?(?:included|supplied|provided)\b|"
            + r"\b"
            + component_pattern
            + r"\b[^.;]{0,50}\b(?:included|supplied|provided)\b"
        )
        if re.search(inclusion_pattern, text):
            score += points
            signals.append(f"seller claims {label} included (+{points})")

    box_pattern = (
        r"\b(?:original|retail)\s+box\b[^.;]{0,30}"
        r"\b(?:included|supplied|provided)\b|"
        r"\b(?:includes?|comes with|supplied with)\b[^.;]{0,50}"
        r"\b(?:original|retail)\s+box\b|"
        r"\bin original packaging\b"
    )
    if re.search(box_pattern, text):
        score += 10
        signals.append("seller claims original box included (+10)")

    working_phrases = (
        "tested working",
        "tested fully working",
        "tested and working",
        "fully working",
        "works perfectly",
    )
    if any(
        _has_unnegated_phrase(text, phrase) for phrase in working_phrases
    ):
        score += 6
        signals.append("seller claims tested/working (+6; unverified)")

    if not signals:
        signals.append("no positive quality evidence found")
    return str(min(score, 100)), "; ".join(signals)


def _browse_triage(summary: dict[str, object]) -> dict[str, str]:
    title = _browse_text(summary.get("title"))
    normalized_title = title.casefold()
    normalized_condition = _browse_text(summary.get("condition")).casefold()
    eligible_condition = _browse_condition_class(normalized_condition)
    normalized_details = f"{normalized_title} {normalized_condition}"
    category_ids = _browse_category_ids(summary)
    target_category = TARGET_CONSOLE_CATEGORY_ID in category_ids
    has_model = re.search(r"\bheg[ -]?001\b", title, re.IGNORECASE) is not None
    has_console_wording = re.search(
        r"\b(console|handheld)\b", title, re.IGNORECASE
    ) is not None

    target_mismatch_terms = (
        "not oled",
        "not an oled",
        "non-oled",
        "non oled",
    )
    risk_terms = (
        "worn",
        "moderate wear",
        "heavy wear",
        "scratched",
        "scratches",
        "scuffed",
        "scuffs",
        "missing rubber grips",
        "missing thumb grips",
        "grade b",
        "grade c",
        "faulty",
        "broken",
        "for parts",
        "spares",
        "repair",
        "not working",
        "damaged",
        "cracked",
        "screen burn",
        "joy-con drift",
        "joycon drift",
    )
    disqualifying_risk_terms = (
        "worn",
        "moderate wear",
        "heavy wear",
        "scratched",
        "scratches",
        "scuffed",
        "scuffs",
        "missing rubber grips",
        "missing thumb grips",
        "grade b",
        "grade c",
        "faulty",
        "broken",
        "for parts",
        "spares",
        "repair",
        "not working",
        "damaged",
        "cracked",
        "screen burn",
        "joy-con drift",
        "joycon drift",
    )
    target_mismatch_flags = [
        term for term in target_mismatch_terms if term in normalized_title
    ]
    bundle_exclusion_terms = (
        "tablet only",
        "unit only",
        "console only",
        "screen only",
        "no cable",
        "no cables",
        "no lead",
        "no leads",
        "without cable",
        "without lead",
    )
    accessory_terms = (
        "screw set",
        "screws",
        "replacement part",
        "screen only",
        "shell only",
        "parts only",
    )
    risk_flags = [term for term in risk_terms if term in normalized_details]
    disqualifying_risk_flags = [
        term for term in disqualifying_risk_terms
        if term in normalized_details
    ]
    bundle_exclusion_flags = [
        term for term in bundle_exclusion_terms if term in normalized_title
    ]
    accessory_flags = [
        term for term in accessory_terms if term in normalized_title
    ]
    description_status, description_signals, description_exclusions = (
        _browse_description_evidence(summary)
    )

    signals = []
    if target_category:
        signals.append(f"target console category {TARGET_CONSOLE_CATEGORY_ID}")
    elif category_ids:
        signals.append(f"other category {category_ids[0]}")
    else:
        signals.append("category unavailable")
    if has_model:
        signals.append(f"{TARGET_MODEL} appears in title")
    else:
        signals.append("model code not explicit in title")
    if has_console_wording:
        signals.append("console/handheld wording in title")
    if eligible_condition:
        signals.append(f"eligible condition: {eligible_condition}")
    else:
        signals.append(
            "exclusion cue: condition is not New or Open Box/never used"
        )
    for term in target_mismatch_flags:
        signals.append(f"target mismatch cue: {term}")
    for term in risk_flags:
        signals.append(f"risk cue: {term}")
    for term in disqualifying_risk_flags:
        signals.append(f"exclusion cue: {term}")
    for term in bundle_exclusion_flags:
        signals.append(f"exclusion cue: {term}")
    for term in accessory_flags:
        signals.append(f"accessory cue: {term}")
    signals.extend(description_signals)
    signals.extend(description_exclusions)

    if not eligible_condition:
        status = "excluded"
    elif target_mismatch_flags or bundle_exclusion_flags:
        status = "excluded"
    elif accessory_flags:
        status = "excluded"
    elif disqualifying_risk_flags:
        status = "excluded"
    elif description_exclusions:
        status = "excluded"
    else:
        status = "verification_needed"
        signals.append("verify complete dock, charger, and Joy-Con bundle")
        signals.append("verify working condition")
        signals.append(f"verify {TARGET_MODEL} against listing details")

    quality_score, quality_signals = _browse_quality_score(summary, status)
    inferred_model = (
        TARGET_MODEL if target_category and has_model and has_console_wording
        and not target_mismatch_flags else ""
    )
    return {
        "model": inferred_model,
        "browse_category_id": category_ids[0] if category_ids else "",
        "triage_status": status,
        "triage_signals": "; ".join(signals),
        "description_status": description_status,
        "description_signals": "; ".join(description_signals),
        "quality_score": quality_score,
        "quality_signals": quality_signals,
    }


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
            writer = csv.DictWriter(csv_file, fieldnames=BROWSE_DRAFT_FIELDS)
            writer.writeheader()
            for summary in summaries:
                triage = _browse_triage(summary)
                writer.writerow(
                    {
                        "item_id": _browse_text(summary.get("itemId")),
                        "title": _browse_text(summary.get("title")),
                        "url": _browse_text(summary.get("itemWebUrl")),
                        "model": triage["model"],
                        "condition": _browse_text(summary.get("condition")),
                        "price_gbp": _browse_money(summary.get("price")),
                        "shipping_gbp": _browse_shipping(
                            summary.get("shippingOptions")
                        ),
                        "includes_dock": "",
                        "includes_charger": "",
                        "includes_joycons": "",
                        "working": "",
                        "browse_category_id": triage["browse_category_id"],
                        "triage_status": triage["triage_status"],
                        "triage_signals": triage["triage_signals"],
                        "description_status": triage["description_status"],
                        "description_signals": triage[
                            "description_signals"
                        ],
                        "quality_score": triage["quality_score"],
                        "quality_signals": triage["quality_signals"],
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
