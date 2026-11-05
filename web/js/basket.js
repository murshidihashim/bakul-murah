/* Basket maths. Pure functions, no DOM. Works in the browser (window.Basket) and in Node (require). */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Basket = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /** Build item_code -> Map(premiseIndex -> {p: price, d: daysOld}) from a district file. */
  function indexDistrict(district) {
    var idx = new Map();
    var prices = district.prices || {};
    Object.keys(prices).forEach(function (code) {
      var m = new Map();
      prices[code].forEach(function (r) { m.set(r[0], { p: r[1], d: r[2] }); });
      idx.set(Number(code), m);
    });
    return idx;
  }

  function median(xs) {
    if (!xs.length) return null;
    var s = xs.slice().sort(function (a, b) { return a - b; });
    var h = Math.floor(s.length / 2);
    return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
  }

  function round2(x) { return Math.round(x * 100) / 100; }

  /** All item codes a basket line accepts: the chosen item plus "any brand" alternatives. */
  function lineCodes(line) { return line.alt && line.alt.length ? [line.code].concat(line.alt) : [line.code]; }

  /** Cheapest acceptable price for a line at one shop: {p, d, code} or null. */
  function priceAt(idx, line, p) {
    var codes = lineCodes(line), best = null;
    for (var i = 0; i < codes.length; i++) {
      var m = idx.get(codes[i]), e = m && m.get(p);
      if (e && (!best || e.p < best.p || (e.p === best.p && e.d < best.d))) best = { p: e.p, d: e.d, code: codes[i] };
    }
    return best;
  }

  /**
   * Rank shops for a basket.
   * basket: [{code, qty, alt?}]. Returns rows sorted by items covered (desc), then total (asc).
   * Each row: {pidx, total, have, n, missing:[code], maxDays, lines:{code: {p, d, code}}}
   */
  function rankStores(idx, nPremises, basket) {
    var rows = [];
    for (var p = 0; p < nPremises; p++) {
      var total = 0, have = 0, missing = [], maxDays = 0, lines = {};
      for (var i = 0; i < basket.length; i++) {
        var it = basket[i], e = priceAt(idx, it, p);
        if (e) {
          total += e.p * it.qty; have++; lines[it.code] = e;
          if (e.d > maxDays) maxDays = e.d;
        } else missing.push(it.code);
      }
      if (have > 0) rows.push({ pidx: p, total: round2(total), have: have, n: basket.length,
                                missing: missing, maxDays: maxDays, lines: lines });
    }
    rows.sort(function (a, b) { return (b.have - a.have) || (a.total - b.total) || (a.pidx - b.pidx); });
    return rows;
  }

  /**
   * Headline numbers. Compares shops that have every item.
   * Returns {kind: "save"|"only"|"none"|"empty", best, median, max, save, fullCount}
   */
  function summarise(rows, n) {
    if (!rows.length || !n) return { kind: "empty" };
    var full = rows.filter(function (r) { return r.have === n; });
    if (!full.length) return { kind: "none", best: rows[0], fullCount: 0 };
    if (full.length === 1) return { kind: "only", best: full[0], fullCount: 1 };
    var totals = full.map(function (r) { return r.total; });
    var med = median(totals);
    return { kind: "save", best: full[0], median: round2(med), max: round2(Math.max.apply(null, totals)),
             save: round2(med - full[0].total), fullCount: full.length };
  }

  /**
   * Best way to buy the basket across exactly k shops (k = 2 or 3).
   * Covers the most items first, then the lowest total. Each item goes to the cheapest of the k shops.
   * Candidates are the top `maxShops` rows of rankStores (default 60) to keep it fast on phones.
   * Returns null when no k-shop plan uses all k shops, else
   * {shops:[pidx], total, have, assign:{code: pidx}}
   */
  function bestPlan(idx, nPremises, basket, rows, k, maxShops) {
    if (!rows || rows.length < k || basket.length < k) return null;
    var cand = rows.slice(0, maxShops || 60).map(function (r) { return r.pidx; });
    var L = basket.length, C = cand.length;
    // price[l][c] = line total at candidate c (Infinity when not sold there)
    var price = [];
    for (var l = 0; l < L; l++) {
      var row = new Array(C);
      for (var c = 0; c < C; c++) {
        var e = priceAt(idx, basket[l], cand[c]);
        row[c] = e ? e.p * basket[l].qty : Infinity;
      }
      price.push(row);
    }
    var best = null, combo = [];
    function evaluate() {
      var total = 0, have = 0, used = new Array(k).fill(0), pick = new Array(L);
      for (var l = 0; l < L; l++) {
        var bv = Infinity, bj = -1;
        for (var j = 0; j < k; j++) { var v = price[l][combo[j]]; if (v < bv) { bv = v; bj = j; } }
        if (bj >= 0) { total += bv; have++; used[bj]++; pick[l] = bj; } else pick[l] = -1;
      }
      for (var j2 = 0; j2 < k; j2++) if (!used[j2]) return; // really fewer shops
      if (!best || have > best.have || (have === best.have && total < best.total - 1e-9)) {
        best = { combo: combo.slice(), total: total, have: have, pick: pick };
      }
    }
    (function walk(start, depth) {
      if (depth === k) { evaluate(); return; }
      for (var c = start; c <= C - (k - depth); c++) { combo[depth] = c; walk(c + 1, depth + 1); }
    })(0, 0);
    if (!best) return null;
    var shops = best.combo.map(function (c) { return cand[c]; }), assign = {};
    basket.forEach(function (it, l) { if (best.pick[l] >= 0) assign[it.code] = shops[best.pick[l]]; });
    return { shops: shops, total: round2(best.total), have: best.have, assign: assign };
  }

  /**
   * Best split of the basket between two shops (kept for the original card and tests).
   * Returns null when fewer than 2 shops, else
   * {a, b, total, have, assign:{code: pidx}, kind: "save"|"cover"|"none", saving}
   */
  function bestSplit(idx, nPremises, basket, rows, maxShops) {
    if (!rows || rows.length < 2 || basket.length < 2) return null;
    var pl = bestPlan(idx, nPremises, basket, rows, 2, maxShops || 200);
    if (!pl) return null;
    var best = { a: pl.shops[0], b: pl.shops[1], total: pl.total, have: pl.have, assign: pl.assign };
    var single = rows[0];
    if (best.have > single.have) { best.kind = "cover"; best.saving = null; }
    else {
      best.saving = round2(single.total - best.total);
      best.kind = best.saving >= 1 ? "save" : "none";
    }
    return best;
  }

  /**
   * Plans for 1, 2 and 3 stops, and which one to recommend.
   * stopCost: what the shopper says one extra stop is worth (RM).
   * Recommends the plan that covers the most items; among those, the lowest total + (stops - 1) x stopCost.
   * Returns {options: [{k, shops, total, have, assign, saving}], rec: k}
   */
  function planOptions(idx, nPremises, basket, rows, stopCost, maxShops) {
    if (!rows || !rows.length || !basket.length) return { options: [], rec: 0 };
    var r0 = rows[0], assign1 = {};
    basket.forEach(function (it) { if (r0.lines[it.code]) assign1[it.code] = r0.pidx; });
    var options = [{ k: 1, shops: [r0.pidx], total: r0.total, have: r0.have, assign: assign1 }];
    [2, 3].forEach(function (k) {
      var pl = bestPlan(idx, nPremises, basket, rows, k, maxShops);
      if (pl) { pl.k = k; options.push(pl); }
    });
    options.forEach(function (o) { o.saving = o.have === r0.have ? round2(r0.total - o.total) : null; });
    var cost = stopCost == null ? 5 : stopCost, rec = options[0];
    options.forEach(function (o) {
      var score = o.total + (o.k - 1) * cost, rs = rec.total + (rec.k - 1) * cost;
      // A plan with fewer stops wins ties, and an extra stop must save at least RM 1.
      if (o.have > rec.have || (o.have === rec.have && score < rs - 1e-9 && rec.total - o.total >= 1)) rec = o;
    });
    return { options: options, rec: rec.k };
  }

  /**
   * "Cheapest on average": rank shops by what the basket cost on average over recent months.
   * shopMonths: {code: [[pidx, m1, m2, ...]]} from the district file (monthly median per shop).
   * For each line, a shop's price is the mean of the months it had a price (cheapest accepted item per month).
   * Also counts in how many months each shop was the cheapest of the shops that had every item that month.
   * Returns rows sorted like rankStores: {pidx, total, have, n, cheapestMonths, fullMonths}
   */
  function averageRank(shopMonths, nPremises, basket, nMonths) {
    if (!shopMonths || !basket.length) return [];
    var mIdx = new Map();
    Object.keys(shopMonths).forEach(function (code) {
      var m = new Map();
      shopMonths[code].forEach(function (r) { m.set(r[0], r.slice(1)); });
      mIdx.set(Number(code), m);
    });
    var M = nMonths || 3, rows = [], monthTotals = [];
    for (var j = 0; j < M; j++) monthTotals.push([]);
    for (var p = 0; p < nPremises; p++) {
      var total = 0, have = 0, perMonth = new Array(M).fill(0), fullMonth = new Array(M).fill(true);
      for (var i = 0; i < basket.length; i++) {
        var codes = lineCodes(basket[i]), sum = 0, cnt = 0;
        for (var mo = 0; mo < M; mo++) {
          var bv = null;
          for (var c = 0; c < codes.length; c++) {
            var m = mIdx.get(codes[c]), v = m && m.get(p);
            if (v && v[mo] != null && (bv == null || v[mo] < bv)) bv = v[mo];
          }
          if (bv != null) { sum += bv; cnt++; perMonth[mo] += bv * basket[i].qty; } else fullMonth[mo] = false;
        }
        if (cnt) { total += (sum / cnt) * basket[i].qty; have++; }
      }
      if (have) {
        rows.push({ pidx: p, total: round2(total), have: have, n: basket.length, cheapestMonths: 0, fullMonths: 0 });
        for (var mo2 = 0; mo2 < M; mo2++) if (fullMonth[mo2]) monthTotals[mo2].push({ pidx: p, t: perMonth[mo2] });
      }
    }
    var byP = new Map(rows.map(function (r) { return [r.pidx, r]; }));
    monthTotals.forEach(function (list) {
      if (!list.length) return;
      list.forEach(function (x) { byP.get(x.pidx).fullMonths++; });
      var min = Math.min.apply(null, list.map(function (x) { return x.t; }));
      list.forEach(function (x) { if (x.t <= min + 1e-9) byP.get(x.pidx).cheapestMonths++; });
    });
    rows.sort(function (a, b) { return (b.have - a.have) || (a.total - b.total) || (a.pidx - b.pidx); });
    return rows;
  }

  /**
   * Pack size from a PriceCatcher unit string.
   * "10 kg" -> {amt: 10000, base: "g"}; "1.5 liter" -> {1500, "ml"}; "30 biji" -> {30, "pc"};
   * "5 X 79g" -> {395, "g"}; "+-350g" -> {350, "g"}. Unknown (e.g. diaper sizes) -> null.
   */
  function parseUnit(u) {
    if (!u) return null;
    var s = String(u).toLowerCase().replace(/\+-|±/g, "").replace(/,/g, ".").trim();
    var mult = 1, mm = s.match(/^(\d+)\s*x\s*(.+)$/);
    if (mm) { mult = Number(mm[1]); s = mm[2]; }
    var m = s.match(/^(\d+(?:\.\d+)?)\s*(kg|g|gm|liter|litre|l|ml|biji|beg|batang|pcs)$/);
    if (!m) return null;
    var n = Number(m[1]) * mult, unit = m[2];
    if (unit === "kg") return { amt: n * 1000, base: "g" };
    if (unit === "g" || unit === "gm") return { amt: n, base: "g" };
    if (unit === "liter" || unit === "litre" || unit === "l") return { amt: n * 1000, base: "ml" };
    if (unit === "ml") return { amt: n, base: "ml" };
    return { amt: n, base: "pc" };
  }

  /** Price per kg / per litre / per piece. Returns {v, per: "kg"|"l"|"pc"} or null. */
  function unitPrice(price, unit) {
    var u = typeof unit === "string" ? parseUnit(unit) : unit;
    if (!u || !u.amt || price == null) return null;
    if (u.base === "g") return { v: price / u.amt * 1000, per: "kg" };
    if (u.base === "ml") return { v: price / u.amt * 1000, per: "l" };
    return { v: price / u.amt, per: "pc" };
  }

  /** Cheapest shops for one item: [{pidx, p, d}] ascending by price. */
  function cheapestFor(idx, code, limit) {
    var m = idx.get(code);
    if (!m) return [];
    var out = [];
    m.forEach(function (v, k) { out.push({ pidx: k, p: v.p, d: v.d }); });
    out.sort(function (a, b) { return (a.p - b.p) || (a.d - b.d); });
    return limit ? out.slice(0, limit) : out;
  }

  return { indexDistrict: indexDistrict, lineCodes: lineCodes, priceAt: priceAt, rankStores: rankStores,
           summarise: summarise, bestPlan: bestPlan, bestSplit: bestSplit, planOptions: planOptions,
           averageRank: averageRank, parseUnit: parseUnit, unitPrice: unitPrice,
           cheapestFor: cheapestFor, median: median, round2: round2 };
});
