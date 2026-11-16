# Bakul Murah

**Where is my whole grocery list cheapest near me?** A free website for Malaysian shoppers.
No account, no tracking, works on a cheap phone, in Bahasa Melayu and English.

Live site: https://murshidihashim.github.io/bakul-murah/

## What it does

- **Your list, your shops.** Paste a list the way you write it in WhatsApp ("beras 10kg x 2, telur 2 papan,
  minyak masak 5kg, ayam 2 ekor, bawang putih 1/2 kg"), open a text file, or read a screenshot. You check each
  match before it is added; lines the reader is unsure of are marked "Check this".
- **Near me first.** On a first visit the app asks where you are: use your location or type a postcode, and pick
  3, 5, 10 or 20 km. Shops across district borders, with the distance to each. Your location and postcode stay on
  your phone and are never saved or sent; the app only downloads the price files of the districts around you
  (see [Privacy](#privacy)). Or choose a district.
- **Whole-list answer.** Shops ranked by the total for your list. "Cheapest" only goes to a shop that has every
  item; otherwise the top shop is marked "Most items", and shops missing items are compared on the items they have.
- **1, 2 or 3 shops.** A 2- or 3-shop plan is only offered when it is really cheaper or covers more items, and the
  app recommends one based on what an extra trip is worth to you.
- **Any brand.** For rice, cooking oil, sardines, flour and similar, each shop is priced with its cheapest brand.
- **Cheapest over 3 months**, cheaper pack tips (price per kg), 12-month price history, and a price-changes feed.
- **Saved lists and share links** (the link holds only item codes and amounts). Opening a shared link asks before
  replacing your list, or adds to it.
- **Honest about the data.** Every result says where the prices come from and how recent they are, each shop shows
  how old its prices are, thin areas are flagged, and a warning appears if the data is ever more than 14 days old.

## Where the data comes from

- **Prices:** PriceCatcher, collected by KPDN and published by DOSM on OpenDOSM, licence
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Source:
  https://open.dosm.gov.my/data-catalogue/pricecatcher. About 2,150 surveyed shops (supermarkets, mini markets,
  kedai runcit, wet markets, hypermarkets) and about 340 items. Rebuilt every Monday.
  Bakul Murah changes the data in these ways: prices older than 30 days are left out of rankings; prices far from
  an item's national typical price (below 0.4x or above 2.5x, usually a wrong pack size) are set aside; same-day
  duplicates are merged; monthly typical (median) prices are calculated per shop, district and Malaysia.
- **Shop locations:** looked up from each shop's name and address with OpenStreetMap Nominatim,
  © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). Each shop is placed at street or
  shop level where possible ("1.2 km"), else at its postcode or town ("~2 km"). A town's point comes from shops
  OpenStreetMap found there, else from the GeoNames list of towns and villages, which puts each town at its own
  point; the postcode table's points are often 10-80 km off, so they are checked against both, and a postcode whose
  town has only one rough point in that table takes the town's point from the list. A shop whose address names a
  smaller place far from that town (a village 16 km out) does not get the town's point. Shops that can only be placed
  in their district are listed as "location unknown" and are never counted inside the radius. Details:
  [etl/geo/README.md](etl/geo/README.md).
- **Postcode locations:** [GeoNames](https://www.geonames.org/) postal codes for Malaysia, CC BY 4.0, checked
  against the shops OpenStreetMap found and against the GeoNames list of towns and villages (also CC BY 4.0),
  where each town has its own point. A postcode with 2 or more of those shops close together is placed where
  they are. A postcode more than 10 km from its town's point in that list is moved there when its town's
  GeoNames points are one rough point for the whole town, when it lies far from the town's other postcodes, or
  when its only shop agrees with the town's point, but only when that point is nearer the town's shops than its
  own. The town's shops are those of its postcodes, else those whose address names the town, unless GeoNames
  gives their postcode to another town only ("84600 PAGOH, MUAR" is a shop in Pagoh). The town's point counts
  only when the town's shops are within 35 km of it, or, for a town without such shops, when the list has only
  one place of that name and it is notable (it has a population or is a seat of government). Old and new
  spellings, such as Tanjong and Tanjung, are one name.
  A postcode still more than 30 km from the other postcodes of its town (when those are not all on one point),
  or more than 15 km from those of a compact town, is left out; the app then places it by the postcodes that
  share its first digits. Postcodes moved to the town's point do not make a town compact.
  A rural postcode that GeoNames names after its post town goes to that town: 21610, the village of Merchang,
  is placed in Marang, 18 km away, and the 8 postcodes of Pekan that lie 12 to 42 km from the town go to it.
  Nothing in these lists tells such a delivery area from a wrong point.
  The exact rules and the known misses are in `etl/geo/README.md`.

Bakul Murah is an independent project. It is not affiliated with or endorsed by KPDN, DOSM or any retailer.
Prices are collected for price monitoring and can be days or weeks old; shelf prices and promotions differ.
Always check in store.

## Privacy

No accounts, no cookies, no analytics, no third-party scripts or fonts. Your lists and settings (such as district,
radius, language and theme) are kept in your browser on your device. Your location and postcode never leave your
phone and are never saved. To find shops near you, the app downloads the price files of the districts around you,
so the site's host (GitHub Pages) can see which districts' files were loaded, as when you choose a district.
A typed postcode gives the app only your area, not your exact place. So with a postcode, every distance is shown as
approximate ("~2 km"), also to shops placed at street level. Screenshots are read on your phone (Tesseract, served
from this site) and never uploaded. The screenshot reader keeps only its English language file in your browser, so
it starts faster next time.

## How it is built

Static files in `web/` (plain JavaScript, no build step). The GitHub Action `refresh-and-deploy` downloads
PriceCatcher and the GeoNames postcode and place lists, builds small JSON files per district (`etl/build_dataset.py`), adds
shop locations (`etl/geocode_premises.py`, cached in `etl/geo/`), runs the tests and data checks
(`etl/check_data.py`), and publishes `web/` to GitHub Pages. If a check fails, the last good site stays live.

| Path | What |
|---|---|
| `web/js/basket.js` | basket totals, 1/2/3-shop plans, averages, price per kg |
| `web/js/listparse.js` | BM/English shopping-list reader |
| `web/js/near.js` | distances, nearby shops across districts, postcodes |
| `web/js/app.js` | the page |
| `web/vendor/tesseract/` | screenshot reader: tesseract.js (Apache-2.0). Inside it: Leptonica, libjpeg, libpng, zlib, libtiff, libwebp, giflib, OpenLibm, the Emscripten runtime and a few small JavaScript modules; their licences are in `THIRD-PARTY-NOTICES.txt` there |
| `etl/` | data download, build, shop locations, postcodes, checks, tests |

### Run it yourself

```
pip install -r requirements.txt
python etl/fetch_pricecatcher.py --months 12
python etl/fetch_postcodes.py
python etl/fetch_places.py
python etl/build_dataset.py --months 12
python -m http.server -d web 8000      # then open http://localhost:8000
```

Offline sample instead of real data: `python etl/make_synthetic.py` then `python etl/build_dataset.py --raw data/sample`.

### Tests

```
python -m pytest etl/tests -q
node --test tests/web/basket.test.js tests/web/listparse.test.js tests/web/near.test.js
node tests/web/e2e.js        # Playwright, needs a real build in web/data (the sample has fewer items, so some list checks fail on it)
```
