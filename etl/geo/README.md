# etl/geo: shop locations and the postcode table

These files let the app show shops near the shopper. They are made by scripts and committed
back by the weekly GitHub Action, so each run only does new work. Do not edit them by hand.
The shopper's own location is never stored here or anywhere else: it stays in their browser.

| File | Made by | What it holds |
|---|---|---|
| `premise_geo.csv` | `etl/geocode_premises.py` | One map point per PriceCatcher shop |
| `district_geo.csv` | `etl/geocode_premises.py` | One centre per district, used as a search anchor |
| `postcodes_my.csv` | `etl/fetch_postcodes.py` | Malaysian postcodes with a place name and a point |
| `postcodes_my.json` | `etl/fetch_postcodes.py` | When the postcode table was downloaded, row count, source, licence |

## premise_geo.csv

Columns: `premise_code, lat, lon, precision, query, district, state, postcode, tries, last_tried`.

`precision` says how much to trust the point:

| precision | Meaning | On screen |
|---|---|---|
| `name` | OpenStreetMap found the shop by its name | exact distance, e.g. "1.2 km" |
| `address` | OpenStreetMap found the shop's street address | exact distance |
| `postcode` | the centre of the shop's postcode (GeoNames) | approximate, e.g. "~2 km" |
| `town` | the centre of the town named in the address (GeoNames) | approximate |
| `district` | no real location: the point is the district centre | only listed for the shopper's home district, never counted inside the radius |

`query` is the search text that gave the point (or the postcode, town or district used).
`tries` counts finished lookups for the shop; `last_tried` is the date of the last one (YYYY-MM-DD).
Files from before these two columns existed still load: missing means 0 and never.

## district_geo.csv

Columns: `key, lat, lon, tries` (more columns may be added; scripts keep them).
`key` is `State|District` as written in PriceCatcher, e.g. `W.P. Kuala Lumpur|Kepong`.
The point comes from searching `<district>, <state without "W.P.">, Malaysia`, e.g.
`Kepong, Kuala Lumpur, Malaysia`. An empty point means OpenStreetMap had no answer.

When two or more districts of one state share a centre (within 0.5 km), that point is usually a
fallback answer (the state centre), not the district. Such centres are searched again once
(`tries` goes to 1). While a centre is still shared or empty, the median point of that district's
located shops (name, address, postcode or town precision) is used instead, if it has at least 2.

## postcodes_my.csv

Columns: `postcode, place, state, lat, lon, accuracy`, sorted by postcode then place.
`postcode` is a 5-digit string, `lat`/`lon` have 5 decimals, `accuracy` is GeoNames' own score
(1 = estimated ... 6 = centroid of addresses). A postcode can have more than one row: its point
is the average of its rows. A town's point is the average of its rows in that state.
The table is downloaded again when it is older than 90 days (`postcodes_my.json` holds the date,
because a git checkout resets file times). If the download fails, the old table is kept.

## How a shop gets its point

1. Anchor, without any search: the shop's postcode point, else a town named in its address
   (same state; the longest name wins; names under 4 letters are ignored), else the district centre.
2. Up to 3 OpenStreetMap searches in a box around the anchor (0.15 degrees around a postcode or
   town, 0.3 around a district centre), stopping at the first good hit:
   `<shop name>, <town or district>`, then the cleaned address, then the shop name alone.
   A hit counts only within 10 km of a postcode or town anchor, or 25 km of a district centre.
3. No good hit: the anchor itself, with precision `postcode`, `town` or `district`.

## Errors and retries

- An error (network, timeout, HTTP error, unreadable reply) is never saved as an answer.
  A shop that had a row keeps it unchanged. A new shop gets its anchor with `tries` 0, so the
  next run looks it up again.
- After 10 errors in a row the run stops calling OpenStreetMap (a circuit breaker). It also stops
  when its time budget is used up (`--max-minutes`, 40 by default in the workflow). On HTTP 429 or
  503 it waits as long as the reply's Retry-After asks, up to 120 seconds.
- Each run looks up new shops first. Then it retries weak points: precision `district`, then
  `town`, then `postcode`, for shops with fewer than 2 tries and none in the last 20 days,
  until the time runs out (`--retry` caps the number, 3000 by default). A retry never swaps a
  point for a less precise one.

## Sources and licences

- Shop and district points: OpenStreetMap, via the Nominatim search service.
  © OpenStreetMap contributors, licence ODbL 1.0: https://www.openstreetmap.org/copyright.
  We follow the Nominatim usage policy: at most 1 request a second, a clear User-Agent,
  results cached here, and credit shown in the app.
- Postcodes: GeoNames postal code data (https://download.geonames.org/export/zip/MY.zip),
  licence CC BY 4.0: https://creativecommons.org/licenses/by/4.0/. Credit: GeoNames, https://www.geonames.org/.
- Shop names and addresses: PriceCatcher, KPDN via OpenDOSM, licence CC BY 4.0.
