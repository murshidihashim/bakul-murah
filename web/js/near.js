/* "Near me" maths. Pure functions, no DOM. Browser: window.Near. Node: require().
 *
 * The user's location is only an argument to these functions. Nothing here stores or sends it.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Near = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function km(aLat, aLon, bLat, bLon) {
    var r = Math.PI / 180, p1 = aLat * r, p2 = bLat * r;
    var h = Math.pow(Math.sin((p2 - p1) / 2), 2) + Math.cos(p1) * Math.cos(p2) * Math.pow(Math.sin((bLon - aLon) * r / 2), 2);
    return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  /** District slugs whose shops could be within radiusKm. areas: meta.areas ({state: [{slug, geo:[lat, lon, r]}]}). */
  function districtsWithin(areas, lat, lon, radiusKm) {
    radiusKm = Number(radiusKm);
    var out = [];
    Object.keys(areas).forEach(function (st) {
      areas[st].forEach(function (a) {
        if (!a.geo) return;
        var d = km(lat, lon, a.geo[0], a.geo[1]);
        if (d - a.geo[2] <= radiusKm) out.push({ slug: a.slug, km: d });
      });
    });
    out.sort(function (x, y) { return x.km - y.km; });
    return out;
  }

  /**
   * Postcode -> {lat, lon, exact, level}. table: {"50100": [lat, lon] or [lat, lon, accuracy]}.
   * An exact match has level 5. Otherwise the average of the postcodes that share the first 4, then 3,
   * then 2 digits (Malaysian postcodes are grouped by area); level is the number of digits shared.
   * Returns null for anything that is not 5 digits, or when no postcode shares even the first 2 digits.
   */
  function locatePostcode(pc, table) {
    pc = String(pc == null ? "" : pc).trim();
    if (!/^\d{5}$/.test(pc) || !table) return null;
    if (hasPoint(table[pc])) return { lat: table[pc][0], lon: table[pc][1], exact: true, level: 5 };
    var keys = Object.keys(table);
    for (var n = 4; n >= 2; n--) {
      var pre = pc.slice(0, n), la = 0, lo = 0, c = 0;
      keys.forEach(function (k) {
        var v = table[k];
        if (k.slice(0, n) === pre && hasPoint(v)) { la += v[0]; lo += v[1]; c++; }
      });
      if (c) return { lat: la / c, lon: lo / c, exact: false, level: n };
    }
    return null;
  }

  function hasPoint(v) { return !!v && v[0] != null && v[1] != null; }

  /** True when a premise row has a real location. Precision "district" means the point is only the district centre. */
  function isLocated(p) { return p[6] !== "district" && p[4] != null && p[5] != null; }

  /** A district file's centre: file.centre when present, else the mean of its shops' points. Null when unknown. */
  function fileCentre(f) {
    if (hasPoint(f.centre)) return f.centre;
    var la = 0, lo = 0, c = 0;
    (f.premises || []).forEach(function (p) { if (p[4] != null && p[5] != null) { la += p[4]; lo += p[5]; c++; } });
    return c ? [la / c, lo / c] : null;
  }

  var PREMISE_COLS = ["premise_code", "premise", "premise_type", "address", "lat", "lon", "geo_precision", "km", "district"];
  // With no located shop inside the radius, the nearest district centre is home only within the radius plus this.
  var HOME_EXTRA_KM = 5;

  /**
   * Merge the shops near the user from several district files into one district-shaped object,
   * so the basket maths works unchanged.
   * Located shops (a real point, precision not "district") count when within radiusKm, nearest first.
   * Unlocated shops (precision "district" or no point) are never counted inside the radius. They come after
   * the located shops, and only for the home district: the district of the nearest located shop or, when
   * there is none, the district whose centre is nearest, if that centre is within radiusKm + 5 km.
   * Premise rows: [code, name, type, address, lat, lon, precision, km (null when unlocated), districtName].
   * History, the price feed and the months come from the home district (else the first file).
   */
  function mergeNear(files, lat, lon, radiusKm) {
    radiusKm = Number(radiusKm);              // a radius read back as text must not become "5" + 5 = "55"
    var shops = [];
    files.forEach(function (f, fi) {
      (f.premises || []).forEach(function (p, pi) {
        if (!isLocated(p)) return;
        var d = km(lat, lon, p[4], p[5]);
        if (d <= radiusKm) shops.push({ f: fi, pi: pi, d: d, row: p });
      });
    });
    shops.sort(function (a, b) { return a.d - b.d || a.row[0] - b.row[0]; });
    // Home district (a file index, -1 for none): the nearest located shop's, else the nearest centre.
    var home = shops.length ? shops[0].f : -1;
    if (home < 0) {
      var bestKm = Infinity;
      files.forEach(function (f, fi) {
        var c = fileCentre(f), d = c ? km(lat, lon, c[0], c[1]) : Infinity;
        if (d <= radiusKm + HOME_EXTRA_KM && d < bestKm) { bestKm = d; home = fi; }
      });
    }
    if (home >= 0) (files[home].premises || []).forEach(function (p, pi) {
      if (!isLocated(p)) shops.push({ f: home, pi: pi, d: null, row: p });
    });
    var map = files.map(function () { return {}; }), seen = {}, premises = [], nLoc = 0, nUnloc = 0;
    shops.forEach(function (s) {
      if (seen[s.row[0]]) return;           // a shop listed in two files: keep the first (nearest)
      seen[s.row[0]] = true;
      map[s.f][s.pi] = premises.length;
      var base = s.row.slice(0, 7);
      while (base.length < 7) base.push(null);
      if (s.d == null) nUnloc++; else nLoc++;
      premises.push(base.concat([s.d == null ? null : Math.round(s.d * 10) / 10, files[s.f].district]));
    });
    var prices = {}, sm = {}, allSM = files.every(function (f) { return !!f.shop_months; });
    files.forEach(function (f, fi) {
      Object.keys(f.prices || {}).forEach(function (code) {
        f.prices[code].forEach(function (r) {
          var ni = map[fi][r[0]];
          if (ni != null) (prices[code] = prices[code] || []).push([ni, r[1], r[2]]);
        });
      });
      if (allSM) Object.keys(f.shop_months).forEach(function (code) {
        f.shop_months[code].forEach(function (r) {
          var ni = map[fi][r[0]];
          if (ni != null) (sm[code] = sm[code] || []).push([ni].concat(r.slice(1)));
        });
      });
    });
    var hf = home >= 0 ? files[home] : (files[0] || {});
    var districts = [];                       // nearest first: the order the premises are in
    premises.forEach(function (p) { if (districts.indexOf(p[8]) < 0) districts.push(p[8]); });
    return {
      state: hf.state, district: hf.district, months: hf.months, data_date: hf.data_date,
      synthetic: hf.synthetic,
      premise_cols: PREMISE_COLS.slice(),
      premises: premises, price_cols: hf.price_cols, prices: prices,
      history: hf.history, feed: hf.feed,
      avg_months: allSM ? hf.avg_months : undefined, shop_months: allSM ? sm : undefined,
      near: { radius: radiusKm, districts: districts, located: nLoc, unlocated: nUnloc,
              home: home >= 0 ? files[home].district : null }
    };
  }

  return { km: km, districtsWithin: districtsWithin, locatePostcode: locatePostcode, mergeNear: mergeNear };
});
