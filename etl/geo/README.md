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
`postcode` is the address's postcode, never a lot, title or box number; empty when it has none.
It is worked out again from the address every time the file is written, for every shop in the
build (rows kept from older runs too); with the GeoNames table, a postcode of the shop's own state
comes first. A shop that is not in the build has no address to read: its postcode is kept only
if GeoNames has it in the shop's state (with no table it is left as it is).
`tries` counts finished lookups for the shop; `last_tried` is the date of the last one (YYYY-MM-DD).
Files from before these two columns existed still load: missing means 0 and never.

## district_geo.csv

Columns: `key, lat, lon, tries, shared` (more columns may be added; scripts keep them).
`key` is `State|District` as written in PriceCatcher, e.g. `W.P. Kuala Lumpur|Kepong`.
The point comes from searching `<district>, <state without "W.P.">, Malaysia`, e.g.
`Kepong, Kuala Lumpur, Malaysia`. An empty point means OpenStreetMap had no answer.

When two or more districts of one state share a centre (within 0.5 km), that point is usually a
fallback answer (the state centre), not the district. Such centres get `shared` = 1 and are
searched again once (`tries` goes to 1). The mark stays until an answer moves the point more
than 0.5 km, so a district left alone on the old point (after the others got their own) is still
searched again and still not trusted. With the postcode table, a new answer more than 50 km from
every GeoNames place of the district's state (for W.P. Kuala Lumpur and W.P. Putrajaya, Selangor
places count too) is not used: the old point stays (an empty one for a new district), and the
run's summary counts it in `district_rejected`.

Where a district's shops are is judged only from shops OpenStreetMap found (precision `name` or
`address`): a postcode or town point from an earlier run may be the wrong one, and must not vouch
for itself. A centre more than 35 km from the median of those shops is not trusted: it is the
middle of a large district far from its town, or a wrong answer. The checks below then use that
median alone. To place the search box for a shop with no postcode or town, a centre that is not
trusted, empty, or more than 35 km from the median of the district's located shops (found ones
first, else postcode and town points, as Limbang's at its town) is replaced by that median, if
the district has at least 2 such shops.

## postcodes_my.csv

Columns: `postcode, place, state, lat, lon, accuracy`, sorted by postcode then place.
`postcode` is a 5-digit string, `lat`/`lon` have 5 decimals, `accuracy` is GeoNames' own score
(1 = estimated ... 6 = centroid of addresses). A postcode can have more than one row: its point
is the average of its rows. A town's point is the average of its rows in that state; a town name
with two rows in one state more than 20 km apart names several places and is not used (the
average of two places 35 km apart would be 18 km from both).
The table is downloaded again when it is older than 90 days, or when `postcodes_my.json` (which
holds the download date) is missing or unreadable. File times are not used: in GitHub Actions
every file is as new as the checkout. If the download fails, the old table is kept.

## How a shop gets its point

1. Anchor, without any search: the shop's postcode point, else a town named in its address
   (same state; the longest name wins; names under 4 letters are ignored), else the district centre.
   - Numbers after LOT, PLOT, PT, PTD, PTB, NO, HS(D), HS(M), GRN, GERAN, GM, P.O. BOX, PETI SURAT
     (P/S), TB or LEASE are lot, title or box numbers, not postcodes ("PTD 52100" is not
     Kepong's 52100). With the table, a 5-digit group in such a run is still a postcode when
     GeoNames has it (in the shop's state) and one of that state's GeoNames places follows it
     directly: "NO 10 - 81100 JOHOR BAHRU", "LOT 5 & 47500 SUBANG JAYA".
   - A town name right after JALAN, JLN, LORONG, LRG, PERSIARAN, LEBUH or LEBUHRAYA is a road
     name, not where the shop is. So is a town joined by a dash to another GeoNames place, a
     route such as "KUCHING-SERIAN", or to words right after a street word ("JALAN KCH-SERIAN",
     "JALAN JELI-DABONG"); "CYBERSQUARE - CYBERJAYA" is not a route. A place whose own name
     starts with one of those street words is a road too, and never used.
   - The state's own name ("MELAKA", "PULAU PINANG" or "PENANG", "KUALA LUMPUR" in a W.P. Kuala
     Lumpur address, ...) is not a town, unless the district is named after it (Melaka Tengah).
   - A postcode or town counts only if GeoNames has it in the shop's state. Shops in W.P. Kuala
     Lumpur and W.P. Putrajaya may also have Selangor ones, as strong evidence only (see below):
     Cyberjaya ("63000, CYBERJAYA") is in Selangor in GeoNames but in W.P. Putrajaya in
     PriceCatcher, while a weak Selangor town for a KL shop is often a road ("JALAN GENTING KLANG").
   - It must also lie near where the district's shops are: the district centre (when trusted, see
     above) or the median of the shops OpenStreetMap found. Two levels of evidence:
     - strong: the postcode's own GeoNames place written right after it ("91000 TAWAU",
       "26800 KUALA ROMPIN"), or a town whose name is or contains the district's name
       ("KUALA ROMPIN" in district Rompin). It may lie up to 80 km away, but only when nothing
       in the address passes the normal 35 km check.
     - weak, everything else: within 35 km.
     Otherwise the next postcode or town in the address is tried, then the district centre.
     (If the table spells a state in a way the script does not know, the run says so; that
     state's postcodes then count only when the distance check can be made.)
   - A retry may replace an old postcode or town point more than 35 km from where the district's
     shops are with a less precise one: a wrong point is worse than an honest "district".
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
  point for a less precise one, except a postcode or town point that fails the checks above.
- The last line of the run is a JSON summary. `looked_up` (new shops) and `retried` count shops
  that reached OpenStreetMap, even if a search then failed (`errors`); `skipped` counts shops
  not sent because calls had stopped, new shops and shops due for a retry alike (they keep their
  old row, or an anchor with `tries` 0). `district_rejected` counts district answers not used
  because they lie outside the district's state.

## Sources and licences

- Shop and district points: OpenStreetMap, via the Nominatim search service.
  © OpenStreetMap contributors, licence ODbL 1.0: https://www.openstreetmap.org/copyright.
  We follow the Nominatim usage policy: at most 1 request a second, a clear User-Agent,
  results cached here, and credit shown in the app.
- Postcodes: GeoNames postal code data (https://download.geonames.org/export/zip/MY.zip),
  licence CC BY 4.0: https://creativecommons.org/licenses/by/4.0/. Credit: GeoNames, https://www.geonames.org/.
- Shop names and addresses: PriceCatcher, KPDN via OpenDOSM, licence CC BY 4.0.
