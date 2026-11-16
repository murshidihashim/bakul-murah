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
| `places_my.csv` | `etl/fetch_places.py` | Malaysian towns and villages (GeoNames gazetteer), each with its own point: used for town points |
| `places_my.json` | `etl/fetch_places.py` | When the place list was downloaded, row count, source, licence |

## premise_geo.csv

Columns: `premise_code, lat, lon, precision, query, district, state, postcode, tries, last_tried`.

`precision` says how much to trust the point:

| precision | Meaning | On screen |
|---|---|---|
| `name` | OpenStreetMap found the shop by its name | exact distance, e.g. "1.2 km" |
| `address` | OpenStreetMap found the shop's street address | exact distance |
| `postcode` | the shop's postcode: where shops OpenStreetMap found with it are, else its point in the postcode table (GeoNames) | approximate, e.g. "~2 km" |
| `town` | the town named in the address (where shops OpenStreetMap found that name it are, else the town's own point in the place list, else its point in the postcode table), or the town of a postcode that has one rough point for the whole town (the town's own point in the place list); see "Where a postcode or town is" | approximate |
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
is the average of its rows. A town's point is the average of its rows in that state, and its name
is written as most of its rows write it, then the first in sort order (81750's "Masai" and "masai"
rows give "Masai"), whatever the order of the rows.
GeoNames names George Town's postcodes (10000-10990) after the state, "Pulau Pinang"; so for a
shop of the island's districts (Timur Laut and Barat Daya), "GEORGE TOWN" (or "GEORGETOWN") means
those rows; on the mainland it is not where the shop is. When the table has its own "George Town"
rows, they are used instead.

These points are often far from the town: every row of the 2026 table has accuracy 3. Counted
on the data of 2026-11-16 (the 933 shops of the build that OpenStreetMap found): 2 or more of them
that agree (see "Where a postcode or town is") name 87 postcodes; 86 of these are in the table, and
for 41 of them the table's point (the average of the postcode's rows) lies more than 10 km from
those shops, for 10 more than 20 km. 98700 is 82 km from Limbang town (its point in the place
list), the "Seri Manjung" row 47 km from the shops OpenStreetMap found in Seri Manjung, 18300 20 km
from those in Gua Musang. So the run checks them against the shops OpenStreetMap found and the
place list (below, "Where a postcode or town is").

A name or postcode with two rows more than 20 km apart names several places (the average of two
places 35 km apart would be 18 km from both):
- a town name: only its rows within 10 km of where the district's shops are (see district_geo.csv;
  the search centre when nothing better is known) count. If those rows lie within 20 km of each
  other, their median is the town's point; if they still spread, or none is that near, the point
  of the shops OpenStreetMap found that name it, then the town's point in the place list, is used
  (see "Where a postcode or town is"), and when there is none the name is not used. (With 35 km,
  most such points in the real table were 9-46 km from the town; with 10 km, 91 of the 98 shops it
  places were within 5 km of shops OpenStreetMap found there.)
- a postcode: it is strong evidence (see below) only through the rows of its own place written
  right after it ("91000 TAWAU": only 91000's Tawau rows), when those lie within 20 km of each
  other; their median is the point. Otherwise it is weak evidence, at its usual point.
In the GeoNames table of 2026-11-16, 17 of 2,757 postcodes and 50 of 460 town names spread so.
The table is downloaded again when it is older than 90 days, or when `postcodes_my.json` (which
holds the download date) is missing or unreadable. File times are not used: in GitHub Actions
every file is as new as the checkout. If the download fails, the old table is kept.

## places_my.csv

Columns: `geonameid, name, asciiname, alt, lat, lon, code, state, population`, sorted by state, then
asciiname. These are the populated places of the GeoNames gazetteer for Malaysia (towns, villages and
parts of towns), each at its own point, so they are an independent check on the postcode table's
town points above. Abandoned, historical and destroyed places are left out. `alt` holds the place's
other Latin-script names joined by `|` ("Sri Manjung"), `code` is the GeoNames feature code (PPLA,
PPLA2... for state and district capitals, PPL for other places, PPLX for a part of a town), and
`state` is GeoNames' name of the state ("Pulau Pinang", "Kuala Lumpur", "Labuan", "Putrajaya";
compared with the shop's state the way `state_key()` does, which also reads "Penang" as Pulau Pinang
and "W.P. Kuala Lumpur" as Kuala Lumpur). The list is downloaded again after 90 days, like the
postcode table; if the download fails, the old list is kept.
`etl/build_dataset.py` uses it to check the shoppers' postcode table (see "The shoppers' postcode
table" below). Without a usable file the build checks that table against shops only, and its log
line and `geo.postcode_source` then leave the place list out.

`etl/geocode_premises.py` uses it (option `--places`, default this file) for the point of a town
written in an address, or of a postcode's town (see "Where a postcode or town is"). A town's point
in the list, G, is found like this:
- The places of the shop's state whose `name` or `asciiname` is the town's name (as in the postcode
  table, compared as upper-case words). Only when there is none: the places with it as an `alt`
  name (Pengkalan Hulu is an alt name of "Keruh", Jeli of "Kampung Jeli"). Places of W.P. Kuala
  Lumpur and W.P. Putrajaya shops' Selangor neighbour count only for strong evidence, as postcodes
  do (Cyberjaya in district Cyberjaya). An alt name that is short for another town of the postcode
  table, one with none of the town's postcodes, is that town: "Tasek Glugor" (alt "Gelugor" and
  "Tasek Gelugor") is TASEK GELUGOR (13300) on the mainland, not GELUGOR (11700) on Penang island.
- A place more than 35 km from where the district's shops are (its trusted centre, or the median of
  the shops OpenStreetMap found there; see district_geo.csv) is another place of that name: Perak's
  "Sungai Siput" is 50 km from Sungai Siput (U). Except the district's own town (its name is or
  contains the district's) when it is the only place of that name: it may lie up to 80 km away,
  as large districts' centres do (Kudat 54 km, Sipitang 52, Limbang 45, Tatau 44, Kuala Rompin 43).
  Of the 60 such towns that shops OpenStreetMap found name, the list's point lies a median 1.9 km
  from those shops. And except a town that is the only place of the state called that, by any of
  its names, when the town's point in the postcode table lies within 15 km of where the district's
  shops are and the place within 35 km of that point: the table puts the town well inside the
  district, but its points are rough. Pengkalan Hulu's only place, "Keruh", is 35.05 km from the
  shops OpenStreetMap found in Hulu Perak (at Gerik); its table point is 14 km from them and 27 km
  from Keruh, which is the town. A table point that is itself far out does not vouch: Bukit Pasir's
  is 33 km from where Batu Pahat's shops are, and Lenggong's and Ajil's are 39-40 km from where
  their districts' shops are. Points this far (and the own town's) count only when nothing in the
  address lies within 35 km.
- When every place of the name is dropped as too far, and the town's point in the postcode table
  lies within 20 km of one of them, that table point is the far town's too (two points that close
  are one place), so it is not used either: the shop gets the next town in its address, else its
  district ("location unknown"), and an old town row within 10 km of that point is wrong. Bukit
  Pasir's table point is 13.7 km from the list's only Bukit Pasir, in Muar, 43.5 km from where Batu
  Pahat's shops are: shop 21036 ("NO 246 JALAN KLUANG,,TAMAN BUKIT PASIR," in Batu Pahat: a
  housing estate there) was at that point, about 35 km from where it is, and counted inside the
  radius; it is now "location unknown". It is the only shop of the 2026-11-16 data whose row this
  changes. Sungai Siput's table point, 14.9 km from where Kuala Kangsar's shops are, is 50 km from
  Perak's far "Sungai Siput", and is still used. (Lenggong's, Ajil's and Tawau's table points lie
  within 5 km of their far places too, but already more than 35 km from where those districts'
  shops are.) The rule cannot tell a far homonym from the shop's own town lying just beyond 35 km:
  Ajil's far place, 40.4 km out, is Ajil itself, so were where Hulu Terengganu's shops are 5 km
  nearer, Ajil's table point (34 km out then, 4.6 km from the town) would still not be used, and
  Ajil's four shops would stay "location unknown"; and hiding the found shops of Endau (Mersing),
  the list's Endau lies 35.1 km out (34.8 km with them, so its own point is used today), and the
  hidden shop 21941 gets its district instead of Endau's table point, 15 km from where it was
  found. Such shops are "location unknown", not counted inside the radius.
- Of the rest, the one nearest those points; but when two of them more than 10 km apart both lie
  within 15 km, there is no telling which, and the town gets no point from the list.
- A district with neither a trusted centre nor 2 found shops has nothing to check a place against:
  no point from the list.
Without the file, or when it is unreadable, has no `name`, `asciiname`, `lat`, `lon` or `state`
column, or no row with a name, a state and a real point (rows with `nan`, `inf` or a point outside
-90..90 / -180..180 are left out), the run prints a warning and places towns as it did before the
list existed: every rule that needs the list is off (the place points, a postcode's town, far
towns, smaller places). The rules that need no list still apply (a wrong point is never kept, and
a written town's row is never turned into an unwritten postcode place's row): on the files of
2026-11-16, a run without the list and with no time for searches differs from such a run before
those rules in one row, 5304 (see "Wrong old points").

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
     BARU or BESAR, which end many names, nor a state's name or MALAYSIA, nor a name GeoNames does
     not have, such as George Town: "BUTTERWORTH-TOWN" is no route). "CYBERSQUARE -
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
     town is"); else, for a town, its own point in the place list (see places_my.csv above); else
     its point in the postcode table, but not when that is the point of a far town of that name
     (see places_my.csv above). For a town name whose postcode-table rows lie far apart, its
     rows near where the district's shops are come first (see postcodes_my.csv above), then the
     shops, then the place list. A postcode whose town has one rough point for all its postcodes
     gives the shop the town's own point in the place list instead, as a town (see "Where a
     postcode or town is").
   - It must also lie near where the district's shops are: the district centre (when trusted, see
     above) or the median of the shops OpenStreetMap found. Two levels of evidence:
     - strong: the postcode's own GeoNames place written right after it ("91000 TAWAU",
       "26800 KUALA ROMPIN"), or a town whose name is or contains the district's name
       ("KUALA ROMPIN" in district Rompin). When found shops vouch for its point, it may lie up
       to 80 km away, but only when nothing in the address passes the normal 35 km check.
     - weak, everything else: within 35 km.
     A town's point in the place list may lie further in two cases (see places_my.csv above), and
     then also counts only when nothing in the address passes the normal 35 km check: the
     district's own town, when it is the only place of that name, up to 80 km away; and a town
     that is the only place of the state called that, by any of its names, when the town's point
     in the postcode table lies within 15 km of where the district's shops are and the place within
     35 km of that point, so at most about 50 km away (Pengkalan Hulu: Keruh, 35.05 km).
     With only its postcode-table point, the district's own town (a town named after the district, or
     a postcode whose own place that is) counts only within 15 km. GeoNames often puts such a
     town, and its postcode, whose area is wide, in the middle of that area: in the 2026 table all
     20 such points 15-35 km from the district's shops were 15-30 km from the town (Segamat 29 km,
     Song 28, Simunjan 23, Kuala Rompin 18, Papar 17), and beyond 35 km Sebauh's was 76 km off
     and Tatau's 39; other towns 15-35 km out were right for 19 of 26 (Ketereh, Kuala Berang).
     Otherwise the next postcode or town in the address is tried, then the district centre.
     (If the table spells a state in a way the script does not know, the run says so; that
     state's postcodes then count only when the distance check can be made.)
   - A smaller place written in the address vetoes a far town (with the place list): no town
     anchor (a town written in the address, or a postcode's town, see "Where a postcode or town
     is") is used when the address also names a smaller place that rules it out, and the next
     evidence is tried, then the district centre. A smaller place is a place of the list in the
     shop's state whose name or ascii name is written as whole words (KG and KAMPONG read as
     KAMPUNG), of at least 4 letters, and no postal town: not a town of the postcode table, nor
     part of one written there, of the state's name or of the district's name ("KOTA PADAWAN" in
     district Kota Padawan); not made only of generic address words (TAMAN, KAMPUNG, KG, PEKAN,
     BANDAR, KOTA, BATU, BUKIT, SUNGAI, SIMPANG, CHABANG, JAYA, BARU, UTAMA, INDAH, PERDANA,
     street words; and words for the buildings, parts and features of a place that GeoNames also
     has as places of their own: KEDAI, BALAI, KILANG, PEJABAT, BANGUNAN, MEDAN, GERAI, MASJID,
     SURAU, SEKOLAH, STESEN, BAWAH, ATAS, DALAM, LUAR, HILIR, LAMA, PANTAI, LEMBAH, KOLAM, MELAYU,
     BAHAGIA, MAWAR: "KEDAI" is a place in Sarawak, so "KEDAI NO 5, KAPIT" would rule Kapit out,
     and "TINGKAT BAWAH" is a ground floor), numbers (BATU 10, SIMPANG EMPAT) and words found in 100
     or more names of the list (43 in the 2026 list: KAMPUNG PADANG, KAMPUNG GONG are no place); not
     a road (right after JALAN, LORONG...), a subdistrict (right after MUKIM or DAERAH) or a housing
     estate (TAMAN ..., or right after TAMAN or TMN: the same estate names are used all over the
     country). On the files of 2026-11-16 those added words and TMN change no anchor; 9 addresses
     no longer name a smaller place by them (KAMPUNG MELAYU, KAMPUNG PANTAI, KAMPUNG LEMBAH, KAMPUNG
     KOLAM, KAMPUNG SURAU, LUAR, TMN BERAPIT). It rules a town's point out when a place of that name
     lies within 35 km of where the district's shops are (or of the town's point, when that lies
     further out, as the district's own town may), and every place called that, by any of its
     names, with or without a leading KAMPUNG, lies more than 10 km from the town's point
     ("...KINARUT,PAPAR": Kinarut is 16.0 km from Papar; "PEKAN TRUSAN,LAWAS": Trusan is 17.1 km
     from Lawas town, which is 45 km from Lawas's district centre). For the district's own town (its
     name is or contains the district's) the place may lie within 45 km: an address that ends in
     the district's name may name only the district, and a large district's villages lie further
     out. Shop 5316 ("NO 63 & 64 ,JALAN BESAR NENASI,PEKAN, PAHANG") was at Pekan's point, 40.9 km
     from Nenasi, which is 37.4 km from where Pekan's shops are, and counted inside the radius; it
     is now "location unknown". With 40 km a 3 km move of where Pekan's shops are gave it Pekan
     back; 45 and 50 km change no other anchor on the files of 2026-11-16, and from 60 km a
     homonym 57 km out began to rule Dungun out ("KAMPUNG NYIOR", shop 17976).
     The smaller place's own point is not used as an anchor: hiding each found shop of 2026-11-16
     that names such a place, the nearest place of that name lay a median 1.0 km from it, but more
     than 10 km for 7 of 121 (5.8%: more than the 5% that would allow it), mostly another place of
     the same name ("KAMPUNG PENAMBANG" in Kota Bharu is not the list's, 28 km away; "KG WAKAF ZIN"
     is not its "Kampung Wakaf"). Hiding each found shop that names a smaller place in turn, the veto
     changed the anchor of 5 of the 41 whose anchor was a town: 4 were more than 10 km from that
     town (Tenang Station, Pauh Lima, Wakaf Tapai twice), the fifth 9.6 km (it writes TAWANG and
     lies 1.4 km from Tawang). On the files of 2026-11-16 a run with the veto differs from one
     without it in 13 rows: 10 town rows of the file become district rows (Nenasi above, Manik
     Urai, Kampung Awah, Beseri, Juasseh, Kampung Bakap, Kampung Gual Ipoh, Kampung Pangkal Chuit,
     Kampung Berdang, Kampung Jakar), 2 district rows of the file stay district rows instead of
     becoming town rows (Kinarut and Trusan above), and one moves 8.7 km within Permaisuri, to the
     town's own point near Kampung Saujana. Known limits, all on the side of "location unknown":
     a place whose name begins a longer name written there can still match ("KAMPUNG WAKAF" in "KG
     WAKAF ZIN", "BANDAR PERMAS" in "BANDAR PERMAS JAYA"); only the word right before a name is
     looked at, so a name in a road or building name still counts ("JALAN APAS BALUNG", "TINAGAT
     PLAZA", "JALAN MEDAN BESERI"; "JALAN BESAR NENASI" names Nenasi the same way), and so does a
     name right before a road word or a Sarawak land district ("JAKAR ROAD", "MUARA TUANG LAND
     DISTRICT", "SIMANGGANG TOWN DISTRICT"); and a name may be another place of that name within
     reach: shop 1455 ("PASIR MAS, KELANTAN,NO.C KG JAKAR, POHON TANJUNG") writes Kampung Jakar,
     whose two places lie 29-34 km out (likely other villages of that name), while its Pohon
     Tanjung (the list's "Kampong Pohon Tanjong", which the address does not match) is 6.7 km from
     its Pasir Mas row, which became a district row. On 2026-11-16 the first limit costs a found
     shop its anchor: 21765 ("KG WAKAF ZIN,,TAWANG") was found 7.1 km from Bachok's point, and
     only "KAMPUNG WAKAF", 22 km from that point, rules Bachok out; the TAWANG it writes, 1.4 km
     from where it was found, is 8.2 km from Bachok's point and rules nothing out. No row changes
     (the shop was found; with its own point hidden, as in the holdout above, Bachok's point lies
     10.9 km from Tawang). Of the rest, only Beseri changed a row (shop 20892 writes KANGAR, a town
     30 km from where its district's shops are, so its district is the likelier answer); BALUNG
     and TINAGAT change nothing for their two Tawau shops (the anchor is their found postcode, and
     the search text stays TAWAU); and of the 6 shops that write a list place right before a road
     word or LAND/TOWN DISTRICT, none loses a town by it (21650's Sarikei point is 9.3 km from one
     of the two Jakar places, 18209's Sri Aman point 1.7 km from Simanggang).
   - A written town beats an unwritten postcode place: when the address writes a postal town but
     none of its postcode's own place names in the table, that postcode does not give its town's
     point (with the place list, see "Where a postcode or town is"), and the written town's anchor
     is used instead: "45400 KUALA SELANGOR" (45400's place is Sekinchan, 24.6 km from where
     OpenStreetMap found that shop) gets Kuala Selangor, "TAMAN PAGOH JAYA, 84500 PAGOH" (84500's
     place is Panchor) Pagoh. And for a shop whose row is a town row for a town its address
     writes, such a postcode is not used at all, so a retry never turns that row into the
     postcode's row: '..., 34100 IPOH' (34100 is Selama's postcode, 35 km from where Kinta's shops
     are) keeps its "Ipoh" row, should 34100's point come within 35 km (this part needs no place
     list). On the files of 2026-11-16
     this changes the anchor of 5 shops and no row (they are found shops, or postcode rows that a
     town anchor never replaces), and it is a known limit: the rule cannot tell a postcode typo
     from an address that writes its district's main town after the right postcode. 4015 is not
     among them: the smaller place it writes, PASIR PENAMBANG (23.8 km from Sekinchan's point),
     already rules Sekinchan out (see above; without that veto, this rule moves its anchor from
     24.6 km to 1.5 km from where it was found). 20943's anchor moves from 2.6 to 5.1 km from its
     postcode row, the anchors of two found shops move further from where they were found: 20920
     ("85300 SEGAMAT": found 6.4 km from Labis, 85300's place, 27.0 km from Segamat) and 12742
     ("81800 JOHOR BAHRU": found 3.9 km from Ulu Tiram, 8.9 km from Johor Bahru's point); and those
     of two postcode rows move further from those rows, which need not be where the shops are:
     17976 ("JALAN BESAR KAMPUNG NYIOR,23100 DUNGUN": its row is 1.0 km from Paka, 23100's place,
     and 4.1 km from the list's Kampung Nyiur; Dungun is 14.6 km from it) and 18402 ("02100 KAKI
     BUKIT": 1.0 to 10.6 km; 02100's place is Padang Besar, the district's own town, so the written
     Kaki Bukit may be the better anchor). A new shop with such an address, or a retry search of
     one, is placed or searched around the written town.
2. Up to 3 OpenStreetMap searches in a box around the anchor (0.15 degrees around a postcode or
   town, 0.3 around a district centre), stopping at the first good hit:
   `<shop name>, <town or district>`, then the cleaned address, then the shop name alone.
   A hit counts only within 10 km of a postcode or town anchor, or 25 km of a district centre.
3. No good hit: the anchor itself, with precision `postcode`, `town` or `district`. But a shop
   that already has a postcode or town point for the same postcode or town keeps that point (it
   may have come from OpenStreetMap: the first run's postcode points did. Counted on 2026-11-16:
   of the 347 postcode rows in the file as committed before that day's run, 135 are of shops of
   the build whose postcode 2 or more agreeing found shops place; those rows lie a median 1.7 km
   from those shops, 118 of them within 5 km), and no point is swapped for a less precise one,
   unless the old point is wrong (see "Wrong old points").

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
Such found shops count for a shop only when they are all in its district, or their median lies
within 10 km of where the district's shops are: the shops that name SIBU are in Sibu town, 16-24 km
from Sibu Jaya, whose addresses name SIBU too (the division). When they do not count, the town's
point in the place list does not either (it is that same town); the shop gets the next town in its
address, or its district. When there are no near rows and no such found shops, the town's point in
the place list is used.

Where no found shops place a town, its own point in the place list (G, see places_my.csv) comes
before the postcode table's point. For a name whose rows spread, G comes after its rows near the
district's shops and after the found shops that name it (leaving out each found shop of such a
name in turn, on the data of 2026-11-16, the other found shops' point was a median 1.6 km from it
and G 2.0 km, so the found shops stay first, as for other names). And a postcode whose town has one
rough point in the table (all of the town's rows in its state lie within 10 km of each other:
Putatan's five postcodes share one point) is not placed at that point when it lies more than 10 km
from G and no found shops vouch for the postcode: the shop gets G, with precision `town` and the
town as `query`. G, not the point of found shops that name the town: those shops have the town's
other postcodes and may lie in another part of it (hiding the found shops of each such postcode,
G was nearer them for 98000 Miri, 2.5 km against 6.2, and 81750 Masai, 1.4 against 3.4). A town
whose rows spread more (Kuala Lumpur, Johor Bahru, Kuching, Kota Kinabalu ...) has a point for each
postcode, and those are kept.

How much this helps was measured on the data of 2026-11-16 by hiding found shops: for each of the
126 towns that 2 or more agreeing found shops name, those shops were treated as not found, and the
anchor each of them would then get was compared with their median. With the place list the
anchors lie a median 2.3 km from it (4.0 km with the postcode table alone); 71 of the 568 shops
more than 10 km (148 before), 5 more than 20 km (25 before), and 2 get no point (39 before). The
same with postcodes: of the 87 postcodes that 2 or more agreeing found shops name, take the 35 the
rule above can apply to (one place name, all its rows within 10 km of each other): for their 118
shops the median went from 11.0 to 2.5 km, 61 to 11 shops more than 10 km, and 3 to 0 with no
point. One town got worse by more than 2 km: Bayan Lepas (1.1 to 3.9 km:
the list puts this large suburb south of its shops, while its four postcode points, all different,
average close to them). "One rough point" within 5 km instead of 10 left Jerteh and Miri 16-17 km
off; 15 km and more began to replace good points (Langkawi's 07000 by 3 km, at 20 km Johor Bahru's
81300 by 14 km). For old rows the decided 5 km stays (see "Wrong old points"). The towns that stay
furthest off are those whose postcode rows spread more than 10 km while one of them is 17-23 km out
(44000 Kuala Kubu Baru, 84900 Tangkak, 35000 Tapah, 36000 Teluk Intan, 33300 Gerik): their postcode
points are kept, as for big towns.

A postcode whose own place the address does not write, while it writes another postal town, gives
no town point this way (see step 1 of "How a shop gets its point": a written town beats an
unwritten postcode place). Known limits, both decided to stay as they are:
- An address that writes the postcode's own place keeps that place's point even when the shop is
  elsewhere: 6437 ("98200 BATU NIAH") gets Niah's own point, 11.7 km from where OpenStreetMap found
  it (Batu Niah is a separate place of the list). 6437 is a found shop: no row is affected.
- Found shops come first: when 2 of them agree on a point, the town is placed there, even if the
  place list puts the town elsewhere. Jeli's two found shops (1446, "KG.BATU MELINTANG, JELI", and
  16467) lie west of the town, 8.9 km from the list's Jeli (under 10 km, so they agree with it), and
  Jeli's town rows sit at their median, about 9 km from the town.

## Wrong old points

An old postcode or town point is wrong when found shops put its postcode or town (the row's
`query`; not a town whose GeoNames rows spread, see above) more than 10 km away (Kalabakan's
first-run "91000" rows, 77 km from Tawau, where the shops OpenStreetMap found with 91000 are).
A town row is wrong, too, when it lies within 10 km of the found shops of a spread name that do not
count for its district (Sibu Jaya's shop at Sibu town, see above). When no found shop says where
the postcode or town is, with the place list: a town row more than 10 km from the town's own point
in the list, when that is where today's anchor puts the town (the old rows of Bagan Serai, Benta
and Julau, 19-27 km off), or within 10 km of the town's postcode-table point when that is a far
town's (shop 21036 at Bukit Pasir, see places_my.csv above), or anywhere a smaller place written in
the address rules its point out (see step 1 of "How a shop gets its point": Kinarut for a row at
Papar); a postcode row more than 10 km from its town's point, when today's anchor does not use the
postcode's point (one rough point for the town, see above; a row near the town's point may have
come from OpenStreetMap, and stays), but not when that town is not where the address says (the
address writes another town and not the postcode's place, or names a smaller place that rules the
town's point out: then that point judges no row). For an old row, one rough point means all the
town's rows within 5 km of each other: where they spread 5-10 km, the town's postcodes have points
of their own, and an old row far from the town may be OpenStreetMap's point for a village in the
postcode's area (shop 17730 in Kampung Anak Ikan, "22020 JERTEH": its row is 2.9 km from the
village, which is 20.5 km from Jerteh; it stays). Otherwise, when it lies more than 35 km from
where the district's shops are (80 km when its place is named after the district: Limbang's
"98700" rows at the town, 38 km from the middle of the district), e.g. a lot number read as
another state's postcode. But a town row within 10 km of the only place of the list called that
(by any of its names) is not judged by its own distance while the place list's own limits for
that place (see places_my.csv above), each 5 km wider, still hold: the place lies within 40 km of
where the district's shops are, or within 40 km of the town's point in the postcode table while
that point lies within 20 km of them. The row is at the town, and the town is about where the
list would still put it. Such a row lies at most 70 km from where the district's shops are (10 +
40 + 20; the first version of this rule, with 35 km for both, kept rows up to about 80 km out).
Without this, the place list's own limits decided old rows too: on the data of 2026-11-16, moving
where Hulu Perak's shops are by 1.1 km judged Pengkalan Hulu's four rows at Keruh wrong and sent
them back to the table's point, 27 km off (and the next move back sent them to Keruh again). With
it, moving where each district's shops are by 1.1, 2, 3 or 5 km in any of 8 directions makes 0, 0,
2 and 3 postcode or town rows newly wrong (0, 1, 3 and 10 with the first version; its 1 at 2 km was
21036, now corrected, and 5316 at Pekan, 41 km from Nenasi, is corrected too, see step 1 of "How a
shop gets its point"): the 81440 rows of 1325 and 16157, and at 5 km the 28000 row of 20745, by the
distance check. Counted on the rows a run with no time for searches leaves, from the files of
2026-11-16; such a run after the move turns no row of the shops that a smaller place rules out
back into a town row.
A new shop still gets the place list's point only within its limits. The postcode table's own
point never makes an old point wrong. Without the postcode table nothing is judged wrong.

A wrong point is never kept (a wrong point is worse than an honest "district"): before any search
(below) it takes today's anchor when that is a postcode or town at least as precise, and when the
shop has no postcode or town anchor today it becomes a district row at once (its tries and
last_tried stay, so a run with time still searches it on its schedule). On the files of 2026-11-16
a run with no time for searches makes 12 such rows district rows: Sibu Jaya's 5304 at Sibu town
(16.5 km off, judged by the found shops of Sibu), 21036 at Bukit Pasir's table point (the place
list; see places_my.csv above) and 10 town rows that a smaller place written in the address rules
out (see step 1 of "How a shop gets its point"); a second such run changes nothing, and no row is
left judged wrong. A wrong postcode row whose anchor today is a town written in the address (less
precise, and not its postcode's town) keeps its point until its retry, which replaces it by
whatever it finds, and for good once it has had its 2 lookups (no retry is left then): the rule
that a row never becomes less precise before any search comes first there (no such row on
2026-11-16).

## Before any search

Every run, after the district centres are checked and before it searches for any shop (so also
when searches stop early, or the time budget is 0), each shop of the build whose row has no real
location (precision `district`, or any value other than name, address, postcode or town, or no
point) gets today's anchor (step 1). When that is a postcode or a town, the row takes it: `precision`, the
point, `query` (the postcode or town) and `postcode`. So does a postcode or town row whose point
is wrong (see "Wrong old points"), when today's anchor is a postcode or town at least as precise;
this also corrects rows given a GeoNames point by an earlier run once found shops or the place
list say where that postcode or town is, even rows that will not be searched again. `tries` and
`last_tried` stay as they were, so OpenStreetMap is still asked on the usual schedule (below). This
never makes a row less precise, with two exceptions: a postcode row becomes a town row when today's
anchor is its postcode's town at the town's own point, because the table has one rough point for
that town (see "Where a postcode or town is"); and a wrong row whose shop has no postcode or town
anchor today becomes a district row (the district centre; with no centre, no point at all), as a
wrong point is never kept (see "Wrong old points"). Rows of shops not in the build are not touched. A
row that already holds today's anchor (the same point, precision, postcode or town, district and
state) is left as it is and not counted, even when it is judged wrong (found shops place the town
elsewhere, but a postcode's town takes its point from the place list): so the next run does not
count it again.
The run's summary counts these rows in `upgraded_offline` and `corrected_offline`.

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
  point is wrong (see "Wrong old points"). With the place list, a town row for the town of a
  postcode whose town has one rough point in the table (see "Where a postcode or town is"; the
  town is the postcode's place written right after it, else its only place name) counts as that
  postcode's row, whatever made it: a retry whose anchor is only that rough point keeps it
  (Pengkalan Hulu's 33100 row at Keruh, should where Hulu Perak's shops are move a little).
  Without the list this rule is off, as before the list existed. A town row for a town the
  address writes is never turned into the row of a postcode whose place the address does not
  write: for such a shop that postcode is not used at all, its anchor is the written town's (see
  step 1 of "How a shop gets its point"). This rule, and that a wrong point is never kept, need
  no list and apply without it too.
- The last line of the run is a JSON summary. `looked_up` (new shops) and `retried` count shops
  that reached OpenStreetMap, even if a search then failed (`errors`); `skipped` counts shops
  not sent because calls had stopped, new shops and shops due for a retry alike (they keep their
  old row, or an anchor with `tries` 0). `upgraded_offline` counts rows that got a postcode or
  town before any search, `corrected_offline` rows whose wrong point was replaced then (above).
  `unvouched_far` counts postcode or town points saved from GeoNames alone (no found shop vouches
  for them) that lie more than 15 km from where the district's shops are: worth a look by hand.
  `district_rejected` counts district answers not used because they lie outside the district's
  state; `district_requeried` counts districts searched again.
  From the place list (see "Where a postcode or town is"): `place_towns` counts shops whose row
  this run set to a town's own point in the list (a town written in the address), `place_postcodes`
  shops whose row it set there instead of their postcode's rough point, `place_corrected` old rows
  the list showed to be wrong and that were corrected before any search (see "Wrong old points").
  These three are counted from the rows as the run leaves them: a shop that a search later in the
  same run found, or that got another anchor, is not counted; one whose retry found nothing and
  wrote the same point and town again still is. `place_ambiguous` and `place_dropped` count the
  town names (once per district) this run looked at and the list could not place: two near places
  of that name, or all of them too far. A run with time for searches looks at more shops than one
  without, so these two can differ between runs on the same files. On the data of 2026-11-16,
  with no time for searches: 76, 6, 41, 2 and 7; 40 shops left "location unknown" (471 to 431;
  before the far-town, smaller-place and wrong-point rules, 54: 471 to 417).

## The shoppers' postcode table

When a shopper types a postcode, the app places them at that postcode's point in `meta.json`
(`geo.postcodes`, `{postcode: [lat, lon]}`). `etl/build_dataset.py` makes this table from
`postcodes_my.csv` and checks it against the shops OpenStreetMap found (precision `name` or
`address` in `premise_geo.csv`; every row counts, also shops that are not in this build) and against
the place list, `places_my.csv`. A place below is a place of the postcode table: its state and
place name ("Putatan", Sabah).

1. A postcode with 2 or more such shops, by their `postcode` column, that agree is placed at the
   median of those that agree. They agree when at least 2 of them, and more than half, lie within
   10 km of the median of them all (the rule the shop geocoder uses for its own postcode points).
   One exception: when only 2 shops agree and they lie more than 10 km apart, the middle of the two
   can be in neither town. If one of them is within 5 km of the GeoNames point, that shop backs the
   point: the postcode keeps it, and no later step moves it or leaves it out.
2. Each place can get the point of a town or village of that name in its state from the place list
   (name or asciiname; other names only when no name matches). Old and new Malay spellings are one
   name, in the postcode table, the place list and shop addresses alike: Tanjong and Tanjung, Telok
   and Teluk, Kampong and Kampung, Sungei and Sungai, Ayer and Air, Sri and Seri (the postcode table
   says Tanjong Piandang where the place list says Tanjung Piandang). Postcodes whose GeoNames place
   names differ only so are one place in every step (Tanjong Bungah and Tanjung Bungah, Penang).
   - Where the place's shops are: the median of its postcodes placed in step 1, else the shops found
     by name or address whose PriceCatcher address names the place, in the same state, when they
     agree as in step 1. The name must be whole words, outside any longer place name of the state:
     "13300 TASEK GELUGOR" names Tasek Gelugor, not Gelugor. A shop whose own postcode (its
     `postcode` column) GeoNames gives only to other places does not count: "84600 PAGOH, MUAR" is a
     shop in Pagoh that names Muar as its district. A shop with no postcode, with one of the place's
     own or with one GeoNames does not have still counts.
   - The place's point is then the place-list entry nearest to where its shops are, within 35 km.
     So a town of the same name elsewhere in the state does not count.
   - A place without such shops gets a point only when the list has exactly one entry by that name and
     that entry has a population or is a seat of government (code PPLA to PPLA4 or PPLC), and only
     when no other entry lists the name among its other names more than 10 km away. The place list's
     "Sungai Siput" (Perak, population 49,000) lies 48.5 km from the town, which it calls "Sungai
     Siput Utara". Two entries by name, or an entry that has the name only among its other names, give
     no point without shops. The list has 2 entries named Engkilili, 15.5 km apart, and 2 named Dalat,
     of which the one with a population lies 19 km from the villages of Dalat; it has Nabawan only as
     another name of Bandar Nabawan. Taking other names without shops would also move 11700 Gelugor
     25 km to Tasek Glugor, whose other name is Gelugor.
3. A postcode that step 1 did not place or keep goes to its place's point when its own point is more
   than 10 km from it and one of these holds:
   - all GeoNames rows of its place lie within 5 km of each other: one rough point for the whole
     place, often a point shared by its PO box postcodes, or the place's only row;
   - its point also lies more than 10 km from every other postcode of its place that lies within
     10 km of the place's point (or no other postcode lies that near): it is an outlier from where
     the town is. A town whose postcodes spread keeps its points otherwise. This also moves a rural
     postcode that GeoNames names after its post town: 21610 is the village of Merchang, 18 km from
     Marang, and goes to Marang's point. Nothing here tells such a delivery area from a wrong point;
   - its only shop found by name or address lies within 10 km of the place's point and more than
     10 km from its own point.
   A postcode with several places moves to a point all of them share (Alor Setar and Alor Star), or
   to the one point among theirs that its only shop agrees with, as in the last rule (73000 is listed
   under Johol and Tampin; its shop is in Tampin). A place name that GeoNames lists in 2 states counts
   only in the state where the place list has it (27000 Jerantut in Pahang and Selangor). A postcode
   moves only to a point that lies nearer to where its place's shops are than its own point does; at
   the same distance it stays. The entry nearest the shops can be a town of that name 35 km away.
   Only the shops of the place whose point it is count: 06400 is listed under Pendang and Pokok Sena,
   and goes to Pokok Sena's point, where its one shop is, though Pendang's shops lie next to its
   GeoNames point.
4. Any other postcode is left out when its point (the average of its rows) is more than 30 km from
   the median of the other postcodes of its place (at least 3 others, on at least 2 different points
   at the table's 4 decimals, each at its point after steps 1 and 3). Other postcodes that all share
   one point are a single guess, and that guess can be the wrong one, so they judge nothing. In a
   compact place, with at least 10 other postcodes of which 9 in 10 lie within 7.5 km of their median
   on at least 2 different points, a point more than 15 km from that median is left out too:
   Kota Kinabalu's city-centre postcode 88000 lay 18.4 km out, and the app showed shoppers there the
   shops of Putatan. For the 9 in 10, postcodes moved in step 3 count at their GeoNames points: on the
   place's point they are one guess, not a sign that the town is compact. Counted there, the 8 on
   Kuantan's point made Kuantan look compact and left out 25100, 26040 and 26070, 16.5 to 18.1 km
   from the others; their points lie 4 to 10 km from shops of the town (at Jaya Gading and Taman
   Tas). A postcode moved in step 3 is never left out. The app places a postcode that was left out
   from the postcodes that share its first 4, 3 or 2 digits.
5. Every other postcode keeps its GeoNames point, also one whose rows lie more than 20 km apart.

No postcode is added. `geo.postcode_checks` counts the postcodes placed from shops (`from_shops`),
those of them moved more than 2 km (`moved`), those moved to their place's point (`to_place`, only
when the build had a place list) and those left out (`set_aside`). The build prints the 10 largest
moves from shops, the 10 largest moves to a place's point, every postcode it left out and every
postcode a shop kept at its GeoNames point, with the reason. `etl/check_data.py` notes these
counts, and warns when more than 5% of the GeoNames postcodes were left out (a broken table or shop
cache).

With the tables and shop cache of 2026-11-16 (2,754 of 2,757 postcodes in the table):
- 84 postcodes are placed from shops, 66 of them moved more than 2 km (Bintulu 97000 by 120 km,
  Muar 84000 by 29 km).
- 2 keep their GeoNames point by the exception in step 1: 30200 Ipoh (one shop in Ipoh, one in
  Batu Gajah; the middle of the two was about 10 km from Ipoh's shops) and 98107 Lutong.
- 287 of 459 places (old and new spellings counted once) get a point from the place list: 76
  confirmed by their postcodes placed from shops, 62 by shops naming them and 149 by a single notable
  entry.
- 226 postcodes move to their place's point, 10 to 352 km (median 21 km): 76 whose place has one
  rough point (62 of them the place's only row, such as 98200 Niah, 136 km, and 34250 Tanjong
  Piandang, 29 km; and the PO boxes 88721 to 88725 Putatan, 99 km from the town), 112 outliers
  (93550 Kuching 352 km, 25608 Kuantan 163 km, 26060 Kuantan 106 km, 27000 Jerantut 67 km, and
  93100, 93582, 15608, 20582, 30632, 40517, 40612 and 70632, which a compact place used to leave
  out), 33 with no other postcode of their place near its point (98700 Limbang 82 km, 96807 and
  96809 Kapit 79 km) and 5 by their one shop (05050 Alor Setar, and 44300 Batang Kali, 06400 Pokok
  Sena, 73000 Tampin and 13300 Tasek Gelugor, which GeoNames lists under 2 places).
- 3 are left out: 73460 Pusat Bandar Palong, 79570 Nusajaya (which sat in Kedah) and 88000 Kota
  Kinabalu.
- Shops hidden one postcode at a time, for the 84 postcodes placed from shops: the table now puts
  them a median 2.9 km from their hidden shops (8.9 km before the place list); more than 10 km
  away: 18 (39 before). Hiding instead the shops that name each of 132 towns: 80 of the towns'
  1,985 postcodes more than 10 km from those shops (152 before), 10 more than 20 km (41 before).

Known misses, where the table or the app is still wrong and these rules cannot tell:
- 98050 and 98057 to 98059 Baram: the place list has no Baram. It has Marudi, the town that the
  addresses of 98050's shops name, but the postcode table names the district. 98057 to 98059 share
  a point 445 km from Marudi.
- 95800 Engkilili (86 km out), 89950 Nabawan (30 km) and 24300 Kerteh (23 km): fewer than 2 found
  shops name each town, and the place list has 2 entries named Engkilili and the other two only as
  other names (step 2).
- 88100 and 88400 Kota Kinabalu lie offshore but within 10 km of the city's point, and the app
  shows them the shops of Putatan. 21220 Kuala Terengganu, 93620 Kuching and 16010 Kota Bharu lie 12
  to 17 km from their city's point but within 10 km of another postcode near it, so they stay.
  10600, which GeoNames places in "Pulau Pinang" (the island's postcodes), lies on the mainland,
  12.9 km from the others, and the app shows Seberang Perai Tengah: the place list has no place of
  that name, and the compact rule leaves out only points more than 15 km out.
- 09800 Serdang (Kedah) lies 29 km from its one found shop, which is in Serdang: the place list has
  that town (1.9 km from the shop) under Perak, not Kedah. The place list has no Langkap,
  so 36700 stays 16 km from its one shop in Langkap. 86810, which GeoNames names after Mersing, its
  post town, stays there; its one shop is in Jemaluang, 18 km away. One shop alone moves nothing
  unless it agrees with a place point (step 3). 6 more postcodes have one found shop more than 15 km
  from their point, but there the shop is the odd one: 27030's is in Seremban, and those of 02700,
  34700, 45400, 77000 and 81440 lie 18 to 38 km from their town's entry in the place list.
- Rural postcodes that GeoNames names after their post town go to that town's point (step 3), also
  where their own point may be right: 21610, the village of Merchang, 18 km to Marang; the 8
  postcodes 26600 to 26680 of Pekan, 12 to 42 km; 26800 to 26820 Kuala Rompin, 14 to 22 km; 73400
  to 73480 Gemas, 10 to 13 km; 35900 and 35910 Tanjong Malim, 30 and 23 km (their points lie 9.4
  and 3.8 km from Slim River); 34510 Batu Kurau, 17 km (its point lies 0.7 km from Redang Panjang);
  and 31150 Ulu Kinta, 15 km into the hills (its point lies 2 km from Ipoh's). No shop says which
  point is right.
- Moved to the right town, but the shop cache has that town's shops elsewhere, or none near, so the
  app shows no home district or a wrong one: 96600 Julau (its 2 shops sit on the old GeoNames point,
  27 km away), 96850 Song (its shop is 25 km from the town), 98850 Lawas (its shops sit at a
  district point 45 km away) and 81930 Bandar Penawar (no surveyed shop lies within 20 km; it showed
  Kota Tinggi before) get none. For 06050 Bukit Kayu Hitam, 34100 and 34130 Selama and 45620 Batang
  Berjuntai, the nearest shop is another town's branch of a chain (in Arau, Seberang Perai Tengah
  and Kuala Lumpur), and for 86600 Paloh it is PASAR AWAM SEGAMAT, which the cache places at Chaah,
  18 km away: the app shows that shop's district. 08500 Kota Kuala Muda now shows Seberang Perai
  Utara, whose shop in Penaga, across the river, is its nearest (6 km).
- When a town's own shops are hidden (the test above), shops with no postcode that name the town
  only as their district or their road can still stand for it: "WAKAF LANAS, JALAN KOTA BHARU" (in
  Pasir Puteh) and "KG ARIL, PAUH LIMA, KOTA BHARU" would keep 15628 at its GeoNames point, 11 km
  from Kota Bharu. Skipping a name that follows JALAN would also drop "JALAN TASEK GELUGOR UTAMA",
  a shop in Tasek Gelugor, and keep 13300 14 km from its town, so the build does not.

## Sources and licences

- Shop and district points: OpenStreetMap, via the Nominatim search service.
  © OpenStreetMap contributors, licence ODbL 1.0: https://www.openstreetmap.org/copyright.
  We follow the Nominatim usage policy: at most 1 request a second, a clear User-Agent,
  results cached here, and credit shown in the app.
- Postcodes: GeoNames postal code data (https://download.geonames.org/export/zip/MY.zip),
  licence CC BY 4.0: https://creativecommons.org/licenses/by/4.0/. Credit: GeoNames, https://www.geonames.org/.
- Places (the towns' own points): GeoNames gazetteer (https://download.geonames.org/export/dump/MY.zip),
  licence CC BY 4.0: https://creativecommons.org/licenses/by/4.0/. Credit: GeoNames, https://www.geonames.org/.
- Shop names and addresses: PriceCatcher, KPDN via OpenDOSM, licence CC BY 4.0.
