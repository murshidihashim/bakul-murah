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
| `places_my.csv` | `etl/fetch_places.py` | Malaysian towns and villages (GeoNames gazetteer), each with its own point |
| `places_my.json` | `etl/fetch_places.py` | When the place list was downloaded, row count, source, licence |

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

Columns: `key, lat, lon, tries, shared, last_tried` (more columns may be added; scripts keep them).

| Column | Meaning |
|---|---|
| `key` | `State\|District` as written in PriceCatcher, e.g. `W.P. Kuala Lumpur\|Kepong` |
| `lat`, `lon` | the district centre; empty when OpenStreetMap had no answer |
| `tries` | how many times the centre was searched |
| `shared` | 1 when the point is (or was) shared with another district of the state; blank otherwise |
| `last_tried` | date of the last search (YYYY-MM-DD); empty in rows from before this column |

The point comes from searching `<district>, <state without "W.P.">, Malaysia`, e.g.
`Kepong, Kuala Lumpur, Malaysia`.

When two or more districts of one state share a centre (within 0.5 km), that point is usually a
fallback answer (the state centre), not the district. Such centres get `shared` = 1 and are
searched again once (`tries` goes to 1). The mark stays until an answer moves the point more
than 0.5 km, so a district left alone on the old point (after the others got their own) is still
searched again and still not trusted. `etl/build_dataset.py` never uses a marked point, nor one
within 0.5 km of another district's point in the same state (the only test for older files
without the column), as a district centre.

With the postcode table, a new answer more than 50 km from every GeoNames place of the district's
state (for W.P. Kuala Lumpur and W.P. Putrajaya, Selangor places count too) is not used: the old
point stays, and the run's summary counts it in `district_rejected`. A district that has no row
yet gets none (as after an error), so the next run asks again. A row with an empty point is
searched again when its `last_tried` is empty or more than 20 days old (the same day rule as
shops: asked exactly 20 days ago is not due yet, 21 days ago is; there is no limit on tries, as a
district with no point leaves its new shops with no anchor at all, and it costs one search per
three weeks). `district_requeried` counts both kinds of new search.

Where a district's shops are is judged only from shops OpenStreetMap found (precision `name` or
`address`): a postcode or town point from an earlier run may be the wrong one, and must not vouch
for itself. A centre more than 35 km from the median of those shops is not trusted: it is the
middle of a large district far from its town, or a wrong answer. The checks below then use that
median alone.

To place the search box for a shop with no postcode or town, a centre that is shared or empty is
replaced by the median of the district's located shops (found ones first, else postcode and town
points), if it has at least 2. A centre of its own is replaced by such a median only when the
median is more than 35 km from it, and only shops that may move it count: those found by
OpenStreetMap (2 or more), or else postcode and town points within 35 km of the centre or whose
place is named after the district (the GeoNames place of the postcode, or the town, contains the
district's name: Limbang's "98700 LIMBANG"). So strong evidence for another town (Kalabakan's
shops at "91000 TAWAU", 60 km away) does not move Kalabakan's searches to Tawau. For a postcode
GeoNames does not have, or with no postcode table, the shop's own address decides: it names the
district right after the postcode ("98700 LIMBANG").

## postcodes_my.csv

Columns: `postcode, place, state, lat, lon, accuracy`, sorted by postcode then place.
`postcode` is a 5-digit string, `lat`/`lon` have 5 decimals, `accuracy` is GeoNames' own score
(1 = estimated ... 6 = centroid of addresses). A postcode can have more than one row: its point
is the average of its rows. A town's point is the average of its rows in that state.
GeoNames names George Town's postcodes (10000-10990) after the state, "Pulau Pinang"; so for a
Pulau Pinang shop, "GEORGE TOWN" (or "GEORGETOWN") means those rows. When the table has its own
"George Town" rows, they are used instead.

These points are often far from the town: every row of the 2026 table has accuracy 3, and in it
41 of 86 postcodes that OpenStreetMap found 2 or more shops for lay more than 10 km from those
shops (98700 is 81 km from Limbang town, the "Seri Manjung" row 47 km from Seri Manjung, 18300
18 km from Gua Musang). So the run checks them against the shops OpenStreetMap found (below,
"Where a postcode or town is").

A name or postcode with two rows more than 20 km apart names several places (the average of two
places 35 km apart would be 18 km from both):
- a town name: only its rows within 10 km of where the district's shops are (see district_geo.csv;
  the search centre when nothing better is known) count. If those rows lie within 20 km of each
  other, their median is the town's point; if they still spread, or none is that near, the point
  of the shops OpenStreetMap found that name it is used (see "Where a postcode or town is"), and
  when there is none the name is not used. (With 35 km, most such points in the real table were
  9-46 km from the town; with 10 km, 91 of the 98 shops it places were within 5 km of shops
  OpenStreetMap found there.)
- a postcode: it is strong evidence (see below) only through the rows of its own place written
  right after it ("91000 TAWAU": only 91000's Tawau rows), when those lie within 20 km of each
  other; their median is the point. Otherwise it is weak evidence, at its usual point.
In the GeoNames table of 2026-11-16, 17 of 2,757 postcodes and 50 of 460 town names spread so.
The table is downloaded again when it is older than 90 days, or when `postcodes_my.json` (which
holds the download date) is missing or unreadable. File times are not used: in GitHub Actions
every file is as new as the checkout. If the download fails, the old table is kept.

## places_my.csv

Columns: `geonameid, name, asciiname, alt, lat, lon, code, state, population`, sorted by state, then
name. These are the populated places of the GeoNames gazetteer for Malaysia (towns, villages and
parts of towns), each at its own point, so they are an independent check on the postcode table's
town points above. Abandoned, historical and destroyed places are left out. `alt` holds the place's
other Latin-script names joined by `|` ("Sri Manjung"), `code` is the GeoNames feature code (PPLA,
PPLA2... for state and district capitals, PPL for other places, PPLX for a part of a town), and
`state` is GeoNames' name of the state ("Penang"; compare states with `state_key()`). The list is
downloaded again after 90 days, like the postcode table; if the download fails, the old list is kept.

## How a shop gets its point

1. Anchor, without any search: the shop's postcode point, else a town named in its address
   (same state; names under 4 letters are ignored), else the district centre. Of several towns,
   the longest name wins, but a name whose GeoNames rows lie far apart (a postal town for a wide
   area, see above) comes after the others ("BENTA, KUALA LIPIS": Benta), and a town written after
   the district's own town is the wider area, not where the shop is ("97100 SEBAUH, BINTULU" in
   district Sebauh: Bintulu is the division).
   - Numbers after LOT, PLOT, PT, PTD, PTB, NO, HS(D), HS(M), GRN, GERAN, GM, P.O. BOX, PETI SURAT
     (P/S), TB or LEASE are lot, title or box numbers, not postcodes ("PTD 52100" is not
     Kepong's 52100). With the table, a 5-digit group in such a run is still a postcode when
     GeoNames has it (in the shop's state) and one of that state's GeoNames places follows it
     directly: "NO 10 - 81100 JOHOR BAHRU", "LOT 5 & 47500 SUBANG JAYA".
   - A town name may be written as in GeoNames or, for a name of 2 or more words, as one word of
     at least 6 letters ("GEORGETOWN" is George Town). The rules below apply to both.
   - A town name right after JALAN, JLN, LORONG, LRG, PERSIARAN, LEBUH or LEBUHRAYA is a road
     name, not where the shop is. So is a town right after such a road town, with a space, slash
     or dash between ("JLN KUCHING SERIAN", "JLN KUCHING /SERIAN": Serian is part of the road's
     name). A town at one end of a dash is a route's end, not where the shop is, when the other
     side is a GeoNames place ("KUCHING-SERIAN") or follows a street word ("JALAN KCH-SERIAN",
     "JALAN JELI-DABONG"); and, for two letter words, also when ROAD, HIGHWAY, EXPRESSWAY,
     BY-PASS, BYPASS or LEBUHRAYA follows ("KUCHING-SAMARAHAN ROAD"), a milestone ("KM 12",
     "BATU 10,", "MILE 5", "10 MILE") comes right before the town at the dash ("KM 12 KUCHING-..."),
     or the other side is a town written short (KCH, KL, JB, KB, PJ, KK, KT) or the last word(s)
     of a longer GeoNames name that no other name uses (SAMARAHAN from KOTA SAMARAHAN; not JAYA,
     BARU or BESAR, which end many names, nor a state's name or MALAYSIA). "CYBERSQUARE -
     CYBERJAYA", "SEKSYEN 13-KETEREH", "TAMAN SRI JAYA-JITRA", "JITRA-KEDAH" and
     "KM 5, TAMAN SERI-MACHANG" are not routes. A place whose own name starts with one of those
     street words is a road too, and never used.
   - The state's own name ("MELAKA", "PULAU PINANG" or "PENANG", "KUALA LUMPUR" in a W.P. Kuala
     Lumpur address, ...) is not a town, unless the district is named after it (Melaka Tengah),
     and even then only when no other town is written ("TAMAN TEKNOLOGI CHENG, MELAKA": Cheng).
   - A postcode or town counts only if GeoNames has it in the shop's state. Shops in W.P. Kuala
     Lumpur and W.P. Putrajaya may also have Selangor ones, as strong evidence only (see below):
     Cyberjaya ("63000, CYBERJAYA") is in Selangor in GeoNames but in W.P. Putrajaya in
     PriceCatcher, while a weak Selangor town for a KL shop is often a road ("JALAN GENTING KLANG").
   - Its point is where shops OpenStreetMap found say it is, when they do (see "Where a postcode or
     town is"), else its GeoNames point. For a town name whose GeoNames rows lie far apart, its rows
     near where the district's shops are come first (see postcodes_my.csv above), then the shops.
   - It must also lie near where the district's shops are: the district centre (when trusted, see
     above) or the median of the shops OpenStreetMap found. Two levels of evidence:
     - strong: the postcode's own GeoNames place written right after it ("91000 TAWAU",
       "26800 KUALA ROMPIN"), or a town whose name is or contains the district's name
       ("KUALA ROMPIN" in district Rompin). When found shops vouch for its point, it may lie up
       to 80 km away, but only when nothing in the address passes the normal 35 km check.
     - weak, everything else: within 35 km.
     With only its GeoNames point, the district's own town (a town named after the district, or
     a postcode whose own place that is) counts only within 15 km. GeoNames often puts such a
     town, and its postcode, whose area is wide, in the middle of that area: in the 2026 table all
     20 such points 15-35 km from the district's shops were 15-30 km from the town (Segamat 29 km,
     Song 28, Simunjan 23, Kuala Rompin 18, Papar 17), and beyond 35 km Sebauh's was 76 km off
     and Tatau's 39; other towns 15-35 km out were right for 19 of 26 (Ketereh, Kuala Berang).
     Otherwise the next postcode or town in the address is tried, then the district centre.
     (If the table spells a state in a way the script does not know, the run says so; that
     state's postcodes then count only when the distance check can be made.)
2. Up to 3 OpenStreetMap searches in a box around the anchor (0.15 degrees around a postcode or
   town, 0.3 around a district centre), stopping at the first good hit:
   `<shop name>, <town or district>`, then the cleaned address, then the shop name alone.
   A hit counts only within 10 km of a postcode or town anchor, or 25 km of a district centre.
3. No good hit: the anchor itself, with precision `postcode`, `town` or `district`. But a shop
   that already has a postcode or town point for the same postcode or town keeps that point (it
   may have come from OpenStreetMap: the first run's postcode points did, and they lie a median
   1.7 km from where the shops OpenStreetMap found with that postcode are), and no point is
   swapped for a less precise one, unless the old point is wrong (see "Wrong old points").

## Where a postcode or town is

Every run first looks at the shops of the build that OpenStreetMap found (precision `name` or
`address`). Each one names a postcode (the one the postcode column would get) and a town (the
first one step 1 would pick from its address). When 2 or more shops of a state name the same
postcode, or the same town, and most of them lie within 10 km of their median, the median of
those is where that postcode or town is: it replaces the GeoNames point everywhere (anchors,
offline points, the checks). In the 2026 data this places 87 postcodes and 126 towns. Against
two public gazetteers, 52 of 56 such towns were within 5 km of the town, against 29 of their
GeoNames points (14 were more than 10 km off). A town name whose GeoNames rows lie far apart (25
of the 126: big towns such as Kuching, or a postal town for a whole district such as Jasin) is
the exception: shops that name it are spread over its area (at Jasin and Marang their median is
15 km from the town), so its GeoNames rows near the district's shops come first, and the found
shops only when there are none. One found shop alone neither vouches for a point nor rules one out.

## Wrong old points

An old postcode or town point is wrong when found shops put its postcode or town (the row's
`query`; not a town whose GeoNames rows spread, see above) more than 10 km away (Kalabakan's
first-run "91000" rows, 77 km from Tawau, where the shops OpenStreetMap found with 91000 are),
or, when no found shop says where it is, when it lies
more than 35 km from where the district's shops are (80 km when its place is named after the
district: Limbang's "98700" rows at the town, 38 km from the middle of the district), e.g. a lot
number read as another state's postcode. GeoNames' own point never makes an old point wrong.
Without the postcode table nothing is judged wrong. A wrong point is replaced: before any search
(below) when today's anchor is a postcode or town at least as precise, else on the retry, by
whatever it finds (a wrong point is worse than an honest "district").

## Before any search

Every run, after the district centres are checked and before it searches for any shop (so also
when searches stop early, or the time budget is 0), each shop of the build whose row has no real
location (precision `district`, or any value other than name, address, postcode or town, or no
point) gets today's anchor (step 1). When that is a postcode or a town, the row takes it: `precision`, the
point, `query` (the postcode or town) and `postcode`. So does a postcode or town row whose point
is wrong (see "Wrong old points"), when today's anchor is a postcode or town at least as precise;
this also corrects rows given a GeoNames point by an earlier run once found shops say where that
postcode or town is, even rows that will not be searched again. `tries` and `last_tried` stay as
they were, so OpenStreetMap is still asked on the usual schedule (below). This never makes a row
less precise, and rows of shops not in the build are not touched. The run's summary counts these
rows in `upgraded_offline` and `corrected_offline`.

## Errors and retries

- An error (network, timeout, HTTP error, unreadable reply) is never saved as an answer.
  A shop that had a row keeps it unchanged. A new shop gets its anchor with `tries` 0, so the
  next run looks it up again.
- After 10 errors in a row the run stops calling OpenStreetMap (a circuit breaker). It also stops
  when its time budget is used up (`--max-minutes`, 40 by default in the workflow). On HTTP 429 or
  503 it waits as long as the reply's Retry-After asks, up to 120 seconds.
- Each run looks up new shops first. Then it retries weak points: precision `district`, then
  `town`, then `postcode` (a row given a postcode or town before any search in this run waits
  until the district rows are done), for shops with fewer than 2 tries and the last more than 20
  days ago (exactly 20 days ago is not due yet), until the time runs out (`--retry` caps the
  number, 3000 by default). A retry that finds nothing keeps an old postcode or town point for
  the same postcode or town, and never swaps a point for a less precise one, unless the old
  point is wrong (see "Wrong old points").
- The last line of the run is a JSON summary. `looked_up` (new shops) and `retried` count shops
  that reached OpenStreetMap, even if a search then failed (`errors`); `skipped` counts shops
  not sent because calls had stopped, new shops and shops due for a retry alike (they keep their
  old row, or an anchor with `tries` 0). `upgraded_offline` counts rows that got a postcode or
  town before any search, `corrected_offline` rows whose wrong point was replaced then (above).
  `unvouched_far` counts postcode or town points saved from GeoNames alone (no found shop vouches
  for them) that lie more than 15 km from where the district's shops are: worth a look by hand.
  `district_rejected` counts district answers not used because they lie outside the district's
  state; `district_requeried` counts districts searched again.

## Sources and licences

- Shop and district points: OpenStreetMap, via the Nominatim search service.
  © OpenStreetMap contributors, licence ODbL 1.0: https://www.openstreetmap.org/copyright.
  We follow the Nominatim usage policy: at most 1 request a second, a clear User-Agent,
  results cached here, and credit shown in the app.
- Postcodes: GeoNames postal code data (https://download.geonames.org/export/zip/MY.zip),
  licence CC BY 4.0: https://creativecommons.org/licenses/by/4.0/. Credit: GeoNames, https://www.geonames.org/.
- Places: GeoNames gazetteer (https://download.geonames.org/export/dump/MY.zip), licence CC BY 4.0.
  Credit: GeoNames, https://www.geonames.org/.
- Shop names and addresses: PriceCatcher, KPDN via OpenDOSM, licence CC BY 4.0.
