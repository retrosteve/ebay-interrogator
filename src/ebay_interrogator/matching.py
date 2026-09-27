from ebay_interrogator.models import ActiveListing, SoldComparable


TARGET_MODEL = "HEG-001"


def is_complete_working_listing(listing: ActiveListing) -> bool:
    return (
        listing.model.strip().upper() == TARGET_MODEL
        and listing.includes_dock
        and listing.includes_charger
        and listing.includes_joycons
        and listing.working
    )


def matches_comparable(
    listing: ActiveListing,
    comparable: SoldComparable,
) -> bool:
    return (
        comparable.model.strip().upper() == TARGET_MODEL
        and comparable.model.strip().upper() == listing.model.strip().upper()
        and comparable.condition.strip().casefold()
        == listing.condition.strip().casefold()
        and comparable.includes_dock
        and comparable.includes_charger
        and comparable.includes_joycons
        and comparable.working
    )
