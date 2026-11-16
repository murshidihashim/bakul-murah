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

  // Money is added up in whole sen, one basket line at a time, so every total is the sum of the lines shown:
  // 0.5 kg at RM 4.99 is RM 2.50 on its line and in the total. Prices in the data are whole sen.
  function sen(x) { return Math.round(x * 100); }
  function lineSen(p, qty) { return Math.round(sen(p) * qty); }
  /** What one basket line costs (price x amount), rounded to the sen: the figure shown on its line. */
  function lineTotal(p, qty) { return lineSen(p, qty) / 100; }

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
          total += lineSen(e.p, it.qty); have++; lines[it.code] = e;
          if (e.d > maxDays) maxDays = e.d;
        } else missing.push(it.code);
      }
      if (have > 0) rows.push({ pidx: p, total: total / 100, have: have, n: basket.length,
                                missing: missing, maxDays: maxDays, lines: lines });
    }
    rows.sort(function (a, b) { return (b.have - a.have) || (a.total - b.total) || (a.pidx - b.pidx); });
    return rows;
  }

  /**
   * Like-for-like totals for two shops: only the basket lines that both shops price (any brand aware).
   * Returns {n: lines both price, a: shop pA's total for them, b: shop pB's total for them}.
   */
  function sharedTotals(idx, basket, pA, pB) {
    var n = 0, a = 0, b = 0;
    for (var i = 0; i < basket.length; i++) {
      var ea = priceAt(idx, basket[i], pA), eb = priceAt(idx, basket[i], pB);
      if (ea && eb) { n++; a += lineSen(ea.p, basket[i].qty); b += lineSen(eb.p, basket[i].qty); }
    }
    return { n: n, a: a / 100, b: b / 100 };
  }

  /**
   * Headline numbers. Compares shops that have every item.
   * median is the typical total of those shops, rounded to the sen (with an even count it is the mean of the
   * two middle totals, so no shop need cost exactly that). save = median - best, from the two figures shown.
   * Returns {kind: "save"|"only"|"none"|"empty", best, median, max, save, fullCount}
   */
  function summarise(rows, n) {
    if (!rows.length || !n) return { kind: "empty" };
    var full = rows.filter(function (r) { return r.have === n; });
    if (!full.length) return { kind: "none", best: rows[0], fullCount: 0 };
    if (full.length === 1) return { kind: "only", best: full[0], fullCount: 1 };
    var totals = full.map(function (r) { return sen(r.total); });
    var med = Math.round(median(totals));
    return { kind: "save", best: full[0], median: med / 100, max: Math.max.apply(null, totals) / 100,
             save: (med - sen(full[0].total)) / 100, fullCount: full.length };
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
    // price[l][c] = line total in sen at candidate c (Infinity when not sold there)
    var price = [];
    for (var l = 0; l < L; l++) {
      var row = new Array(C);
      for (var c = 0; c < C; c++) {
        var e = priceAt(idx, basket[l], cand[c]);
        row[c] = e ? lineSen(e.p, basket[l].qty) : Infinity;
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
      if (!best || have > best.have || (have === best.have && total < best.total)) {
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
    return { shops: shops, total: best.total / 100, have: best.have, assign: assign };
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

  /** The value of one extra stop (RM): a number of 0 or more, else the default RM 5. */
  function stopValue(stopCost) {
    var c = stopCost == null || stopCost === "" ? NaN : Number(stopCost);
    return isFinite(c) && c >= 0 ? c : 5;
  }
  // An extra stop must save at least RM 1, whatever the shopper says a stop is worth.
  var MIN_EXTRA_SEN = 100;
  /**
   * True when plan o (more stops than plan r) should be recommended over r: it covers more items, or the
   * same items for more than (extra stops x stop value) less and at least RM 1 less. Fewer stops win ties.
   * Compared in whole sen, so float noise (8.11 - 7.11 = 0.9999...) never decides.
   */
  function beats(o, r, cost) {
    if (o.have !== r.have) return o.have > r.have;
    var save = sen(r.total) - sen(o.total);
    return save > (o.k - r.k) * sen(cost) && save >= MIN_EXTRA_SEN;
  }

  /**
   * Plans for 1, 2 and 3 stops, and which one to recommend.
   * stopCost: what the shopper says one extra stop is worth (RM).
   * A plan with more stops is kept only when it covers more items than the best kept plan with fewer
   * stops, or the same number of items for at least RM 0.01 less. Other plans are not returned, so an
   * extra stop is never offered when it buys less or saves nothing. Look options up by k, not by position.
   * Recommends the plan that covers the most items; among those, the lowest total + (stops - 1) x stopCost,
   * where each extra stop must also save at least RM 1 (see beats).
   * Returns {options: [{k, shops, total, have, assign, saving}], rec: k}
   * saving: RM saved against the 1-stop plan when the plan covers the same items, else null (covers more).
   */
  function planOptions(idx, nPremises, basket, rows, stopCost, maxShops) {
    if (!rows || !rows.length || !basket.length) return { options: [], rec: 0 };
    var r0 = rows[0], assign1 = {};
    basket.forEach(function (it) { if (r0.lines[it.code]) assign1[it.code] = r0.pidx; });
    var options = [{ k: 1, shops: [r0.pidx], total: r0.total, have: r0.have, assign: assign1 }];
    [2, 3].forEach(function (k) {
      var pl = bestPlan(idx, nPremises, basket, rows, k, maxShops);
      // Each kept plan beats the ones before it, so the last kept plan is the best with fewer stops.
      var last = options[options.length - 1];
      if (pl && (pl.have > last.have || (pl.have === last.have && sen(last.total) - sen(pl.total) >= 1))) {
        pl.k = k; options.push(pl);
      }
    });
    options.forEach(function (o) { o.saving = o.have === r0.have ? (sen(r0.total) - sen(o.total)) / 100 : null; });
    var cost = stopValue(stopCost), rec = options[0];
    options.forEach(function (o) { if (beats(o, rec, cost)) rec = o; });
    return { options: options, rec: rec.k };
  }

  function optionK(po, k) { return (po.options || []).filter(function (o) { return o.k === k; })[0] || null; }

  /**
   * The plan card's message for the k-stop option, worded against the recommended plan (po.rec) with the
   * same rule planOptions uses to recommend (beats, including the RM 1 minimum). So a message never argues
   * for fewer shops while more are recommended, and never states a comparison that is false.
   * n: the number of items on the list. Returns {key, vars} for the screen's labels, or null.
   * vars.x is an amount in RM (whole sen), vars.c the value of an extra stop, vars.k a number of shops.
   *   k < rec, rec covers more:  planFewerCover {k: rec, h, n}
   *   k < rec, same items:       planFewer {k: rec, x: how much more the recommended plan saves}
   *   k > rec:                   planMoreNot {x, c} (x <= extra stops x c) or planMoreTiny {x} (under RM 1)
   *   k = rec = 1:               planOneLow / planOneLow3 {x, c}, or planOneTiny / planOneTiny3 {x} (under RM 1),
   *                              about the 2-stop plan (else the 3-stop plan); planOne when there is neither
   *   k = rec > 1:               planCover {h, n} when it covers more than 1 stop, else planSaveRec {x, c}
   *                              (planSaveRec0 {x} when an extra stop is worth RM 0)
   */
  function planNote(po, k, stopCost, n) {
    var o = po && optionK(po, k), r = po && optionK(po, po.rec), one = po && po.options && po.options[0];
    if (!o || !r || !one) return null;
    var c = stopValue(stopCost), cs = sen(c), x;
    if (k < r.k) {
      if (r.have > o.have) return { key: "planFewerCover", vars: { k: r.k, h: r.have, n: n } };
      return { key: "planFewer", vars: { k: r.k, x: (sen(o.total) - sen(r.total)) / 100 } };
    }
    if (k > r.k) {
      // Not recommended, so it covers the same items as the recommended plan (more items would have won).
      x = sen(r.total) - sen(o.total);
      return x <= (k - r.k) * cs ? { key: "planMoreNot", vars: { x: x / 100, c: c } }
                                 : { key: "planMoreTiny", vars: { x: x / 100 } };
    }
    if (k === 1) {
      var alt = optionK(po, 2) || optionK(po, 3);
      if (!alt || alt.have !== o.have) return { key: "planOne", vars: {} };
      x = sen(o.total) - sen(alt.total);
      var three = alt.k === 3 ? "3" : "";
      return x <= (alt.k - 1) * cs ? { key: "planOneLow" + three, vars: { x: x / 100, c: c } }
                                   : { key: "planOneTiny" + three, vars: { x: x / 100 } };
    }
    if (o.have > one.have) return { key: "planCover", vars: { h: o.have, n: n } };
    x = (sen(one.total) - sen(o.total)) / 100;
    return c > 0 ? { key: "planSaveRec", vars: { x: x, c: c } } : { key: "planSaveRec0", vars: { x: x } };
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
          // Monthly typical prices can be half sen, so round each line's amount (not the price) to the sen.
          if (bv != null) { sum += bv; cnt++; perMonth[mo] += sen(bv * basket[i].qty); } else fullMonth[mo] = false;
        }
        if (cnt) { total += sen((sum / cnt) * basket[i].qty); have++; }
      }
      if (have) {
        rows.push({ pidx: p, total: total / 100, have: have, n: basket.length, cheapestMonths: 0, fullMonths: 0 });
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

  /** Whole days from ISO date a ("2026-11-05") to ISO date b; NaN when either is not a date. */
  function daysBetween(a, b) { return Math.round((Date.parse(b) - Date.parse(a)) / 864e5); }
  /**
   * How old a price is today. days_old in the data counts back from the data date, not from today, so add
   * the days since the data date (never fewer than none: a clock set before the data date adds nothing).
   * today: the shopper's date as "YYYY-MM-DD".
   */
  function ageToday(daysOld, dataDate, today) {
    var gap = dataDate && today ? daysBetween(dataDate, today) : 0;
    return (Number(daysOld) || 0) + (gap > 0 ? gap : 0);
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
           sharedTotals: sharedTotals, lineTotal: lineTotal,
           summarise: summarise, bestPlan: bestPlan, bestSplit: bestSplit, planOptions: planOptions,
           planNote: planNote, stopValue: stopValue, ageToday: ageToday,
           averageRank: averageRank, parseUnit: parseUnit, unitPrice: unitPrice,
           cheapestFor: cheapestFor, median: median, round2: round2 };
});
