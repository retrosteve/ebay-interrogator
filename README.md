# eBay Interrogator

A Python command-line tool for ranking complete, working Nintendo Switch OLED
(HEG-001) listings on eBay UK by estimated resale profit. It reads active
listings and sold comparisons from CSV files, and can search active listings
through the eBay Browse API to create a manual-review CSV draft. Sold
comparisons remain CSV-based; the tool does not purchase items.

## Getting started

Create and activate a virtual environment from the project directory:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

On macOS or Linux, use:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the project in editable mode:

```powershell
python -m pip install -e .
```

## Input files

The active listings CSV must have these columns:

`item_id,title,url,model,condition,price_gbp,shipping_gbp,includes_dock,includes_charger,includes_joycons,working`

The sold comparisons CSV must have these columns:

`model,condition,sold_price_gbp,shipping_gbp,sold_at,includes_dock,includes_charger,includes_joycons,working`

Use ISO dates (`YYYY-MM-DD`) for `sold_at`, GBP amounts without currency symbols,
and `true`/`false` values for bundle and working-condition columns. Comparable
sales are matched by model, condition, and complete working bundle.

## Scan listings

Set fee and postage estimates for your own seller account and parcel:

```powershell
python -m ebay_interrogator scan `
	--listings listings.csv `
	--comps sold-comparables.csv `
	--fee-percent 13 `
	--fixed-fee-gbp 0.30 `
	--outbound-postage-gbp 4.50 `
	--packaging-gbp 0.50 `
	--buffer-gbp 5 `
	--output candidates.csv
```

The resale estimate uses the lower quartile of matching sold prices, including
buyer-paid shipping, from the last 90 days. Listings with fewer than three valid
comparables are excluded by default. Fee and shipping estimates are user inputs,
not current eBay fee guidance.

## Browse active listings

After your developer account is approved, create Sandbox application keys in
the eBay Developer Portal. Use the VS Code **eBay Interrogator: Browse Sandbox**
launch profile to enter the Client ID, masked Client Secret, and search query.
It requests up to 20 active eBay UK results and writes the raw response to
`active-listings.json`, which is ignored by Git. It also writes
`active-listings-draft.csv`, an ignored manual-review draft.

The draft maps listing ID, title, URL, condition, and unambiguous GBP price and
shipping values. It adds `browse_category_id`, `triage_status`,
`triage_signals`, `description_status`, and `description_signals`. When a draft
CSV is requested, the command also fetches the seller-written description for
each result using the read-only Browse item endpoint, up to one additional
request per result. HTML is stripped for analysis; concise clues go into the
CSV rather than the full description. Rules flag explicit missing components,
bundle-only wording, and common power, screen, control, heat, liquid, or online
fault claims as `excluded`. This is phrase-based screening, not a semantic
review: wording may be missed or misinterpreted. Explicit wear or cosmetic
defects and missing standard bundle items (dock, charger, Joy-Cons, HDMI cable,
or Joy-Con grip/straps) are screened out. Box mentions are preference signals,
not hard exclusions. Description mentions of included components or working
condition are evidence only, not confirmation. All other results are
`verification_needed`, not deal recommendations. Verify the HEG-001 model,
condition, original accessories, and working condition before adding a listing
to the scanner's active-listings CSV. Browse triage only allows condition
statuses `New`, `Like New`, and `Open box`/`opened - never used`; other used,
missing, or unfamiliar statuses are excluded. The draft also includes `quality_score`
and `quality_signals`: a 0-100 ranking of positive seller evidence for
condition, included accessories, original box, and tested/working claims.
Excluded listings are not scored. The score is not a probability, condition
verification, or profit estimate; unsupported claims and missing information
do not prove good condition or a complete bundle.

The draft may prefill model from category and title evidence, but that is only a
hint to verify. Bundle contents and working status remain blank. Non-GBP prices
and ambiguous shipping options are also left blank. The scanner rejects
incomplete rows. The Browse API integration uses application OAuth credentials
from the launch prompt and makes no request until you start this profile.

For live inventory, first ensure your Production keyset is enabled for the Browse
API. Then use **eBay Interrogator: Browse Production (read-only)** and enter the
Production Client ID and masked Client Secret at its prompts. This profile makes
authentication and Browse search requests, plus read-only item-detail requests
when a draft CSV is requested; it does not create or change listings. It limits
results to eBay UK category `139971` (Video Game Consoles)
and sorts by lowest item-plus-shipping cost first, based on eBay's shipping
estimate. It writes `active-listings-production.json` and
`active-listings-production-draft.csv`, both ignored by Git. Do not enter
Production keys into the Sandbox profile.

The Browse command retrieves active listings only. Sold comparisons remain
CSV-based until eBay confirms access to a permitted sold-history source. Do not
put Production keys in the Sandbox profile or commit credentials to the
repository.

The VS Code Run and Debug profile defaults to the CSV files under `examples/`.
They contain synthetic demo values only, not live listings or real sold prices.
Replace the prompted file paths with your own CSVs when you are ready to scan.

## Run tests

```powershell
python -m unittest discover -s tests -v
```