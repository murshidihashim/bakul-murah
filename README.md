# Bakul Murah

**Where is my whole grocery list cheapest near me?** A free website for Malaysian shoppers.
No account, no tracking, works on a cheap phone, in Bahasa Melayu and English.

Live site: https://murshidihashim.github.io/bakul-murah/

## What it does

- **Your list, your shops.** Paste a list the way you write it in WhatsApp ("beras 10kg, telur 2 papan,
  minyak masak 5kg, ayam 2 ekor"), open a text file, or read a screenshot. You check each match before it is added.
- **Near me.** Use your location or type a postcode, pick 3, 5, 10 or 20 km. Shops across district borders,
  with the distance to each. Your location stays in the page: it is never saved or sent.
- **Whole-basket answer.** Shops ranked by the total for your list, with how many of your items each has.
- **1, 2 or 3 stops.** The cheapest plan for each, and a recommendation based on what an extra stop is worth to you.
- **Any brand.** For rice, cooking oil, sardines, flour and similar, each shop is priced with its cheapest brand.
- **Cheapest on average.** Which shop was cheapest for your list over the last 3 months, not just today.
- **Cheaper pack tips**, 12-month price history per item, and a "what went up this month" feed.
- **Saved lists and share links** (the link holds only item codes and amounts).

## Where the data comes from

- **Prices:** PriceCatcher, collected by KPDN and published by DOSM on OpenDOSM, licence
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Source:
  https://open.dosm.gov.my/data-catalogue/pricecatcher. About 2,150 surveyed shops (supermarkets, mini markets,
  kedai runcit, wet markets, hypermarkets) and about 340 items. Rebuilt every Monday.
- **Shop locations:** looked up from each shop's name and address with OpenStreetMap Nominatim,
  © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). Some are approximate
  (postcode or district centre); those show "around <district>" instead of a distance.

Bakul Murah is an independent project. It is not affiliated with or endorsed by KPDN, DOSM or any retailer.
Prices are collected for price monitoring and can be days or weeks old; shelf prices and promotions differ.
Always check in store.

## Privacy

No accounts, no cookies, no analytics, no third-party scripts or fonts. Your lists, district, language and theme
are kept in your browser on your device. Location and postcode are used only inside the page and never stored or
sent. Screenshots are read on your phone (Tesseract, served from this site) and never uploaded.

## How it is built

Static files in `web/` (plain JavaScript, no build step). The GitHub Action `refresh-and-deploy` downloads
PriceCatcher, builds small JSON files per district (`etl/build_dataset.py`), adds shop locations
(`etl/geocode_premises.py`, cached in `etl/geo/`), runs the tests and publishes `web/` to GitHub Pages.

| Path | What |
|---|---|
| `web/js/basket.js` | basket totals, 1/2/3-stop plans, averages, price per kg |
| `web/js/listparse.js` | BM/English shopping-list reader |
| `web/js/near.js` | distances, nearby shops across districts, postcodes |
| `web/js/app.js` | the page |
| `web/vendor/tesseract/` | screenshot reader (Apache-2.0) |
| `etl/` | data download, build, shop locations, tests |

### Run it yourself

```
pip install -r requirements.txt
python etl/fetch_pricecatcher.py --months 12
python etl/build_dataset.py --months 12
python -m http.server -d web 8000      # then open http://localhost:8000
```

Offline sample instead of real data: `python etl/make_synthetic.py` then `python etl/build_dataset.py --raw data/sample`.

### Tests

```
python -m pytest etl/tests -q
node --test tests/web/basket.test.js tests/web/listparse.test.js tests/web/near.test.js
node tests/web/e2e.js        # Playwright, needs built data in web/data
```
