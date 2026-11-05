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
   * Postcode -> {lat, lon, exact}. Uses the shop postcodes in the data; when a postcode is not there,
   * the average of postcodes that share its first 4, then 3 digits (Malaysian postcodes are grouped by area).
   */
  function locatePostcode(pc, table) {
    pc = String(pc || "").trim();
    if (!/^\d{5}$/.test(pc) || !table) return null;
    if (table[pc]) return { lat: table[pc][0], lon: table[pc][1], exact: true };
    for (var n = 4; n >= 3; n--) {
      var pre = pc.slice(0, n), la = 0, lo = 0, c = 0;
      Object.keys(table).forEach(function (k) { if (k.slice(0, n) === pre) { la += table[k][0]; lo += table[k][1]; c++; } });
      if (c) return { lat: la / c, lon: lo / c, exact: false };
    }
    return null;
  }

  /**
   * Merge the shops within radiusKm from several district files into one district-shaped object,
   * so the basket maths works unchanged. Shops are sorted nearest first.
   * Each premise row gets two extra values: [... , km, districtName].
   * History and the price-rise feed come from the district of the nearest shop.
   */
  function mergeNear(files, lat, lon, radiusKm) {
    var shops = [];
    files.forEach(function (f, fi) {
      f.premises.forEach(function (p, pi) {
        if (p[4] == null) return;
        var d = km(lat, lon, p[4], p[5]);
        if (d <= radiusKm) shops.push({ f: fi, pi: pi, d: d, row: p });
      });
    });
    shops.sort(function (a, b) { return a.d - b.d || a.row[0] - b.row[0]; });
    var map = files.map(function () { return {}; }), seen = {}, premises = [];
    shops.forEach(function (s) {
      if (seen[s.row[0]]) return;           // a shop listed in two files: keep the first (nearest)
      seen[s.row[0]] = true;
      map[s.f][s.pi] = premises.length;
      var base = s.row.slice(0, 7);
      while (base.length < 7) base.push(null);
      premises.push(base.concat([Math.round(s.d * 10) / 10, files[s.f].district]));
    });
    var prices = {}, sm = {}, allSM = files.every(function (f) { return !!f.shop_months; });
    files.forEach(function (f, fi) {
      Object.keys(f.prices).forEach(function (code) {
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
    var home = shops.length ? files[shops[0].f] : files[0];
    var districts = [];
    premises.forEach(function (p) { if (districts.indexOf(p[8]) < 0) districts.push(p[8]); });
    return {
      state: home.state, district: home.district, months: home.months, data_date: home.data_date,
      synthetic: home.synthetic,
      premise_cols: (home.premise_cols || []).slice(0, 7).concat(["km", "district"]),
      premises: premises, price_cols: home.price_cols, prices: prices,
      history: home.history, feed: home.feed,
      avg_months: allSM ? home.avg_months : undefined, shop_months: allSM ? sm : undefined,
      near: { radius: radiusKm, districts: districts }
    };
  }

  return { km: km, districtsWithin: districtsWithin, locatePostcode: locatePostcode, mergeNear: mergeNear };
});
