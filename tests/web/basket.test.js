// Unit tests for web/js/basket.js.  Run: node --test tests/web/
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const B = require(path.join(__dirname, "../../web/js/basket.js"));

// 3 shops, 3 items. prices: {code: [[premiseIndex, price, daysOld]]}
const district = {
  premises: [[1, "A"], [2, "B"], [3, "C"]],
  prices: {
    10: [[0, 5.0, 1], [1, 4.0, 2], [2, 6.0, 0]],
    20: [[0, 2.0, 1], [1, 3.0, 2], [2, 2.5, 30]],
    30: [[0, 9.0, 1], [2, 8.0, 0]], // shop B lacks item 30
  },
};
const idx = B.indexDistrict(district);

test("rankStores puts full coverage first, then cheapest", () => {
  const rows = B.rankStores(idx, 3, [{ code: 10, qty: 1 }, { code: 20, qty: 2 }, { code: 30, qty: 1 }]);
  // A = 5 + 4 + 9 = 18 ; C = 6 + 5 + 8 = 19 ; B = 4 + 6 = 10 but missing one item
  assert.deepEqual(rows.map(r => r.pidx), [0, 2, 1]);
  assert.equal(rows[0].total, 18);
  assert.equal(rows[2].have, 2);
  assert.deepEqual(rows[2].missing, [30]);
  assert.equal(rows[1].maxDays, 30);
});

test("summarise compares against the median full-coverage shop", () => {
  const rows = B.rankStores(idx, 3, [{ code: 10, qty: 1 }, { code: 20, qty: 2 }, { code: 30, qty: 1 }]);
  const s = B.summarise(rows, 3);
  assert.equal(s.kind, "save");
  assert.equal(s.median, 18.5);
  assert.equal(s.save, 0.5);
  assert.equal(s.max, 19);
});

test("bestSplit picks the cheapest covering pair", () => {
  const basket = [{ code: 10, qty: 1 }, { code: 20, qty: 2 }, { code: 30, qty: 1 }];
  const rows = B.rankStores(idx, 3, basket);
  const sp = B.bestSplit(idx, 3, basket, rows);
  // Best pair: B for item 10 (4), A for item 20 (2x2=4), C for item 30 (8) -> needs 3 shops.
  // Pairs: A+B = 4+4+9 = 17 ; A+C = 5+4+8 = 17 ; B+C = 4+5+8 = 17. Tie -> first found (A+B).
  assert.equal(sp.have, 3);
  assert.equal(sp.total, 17);
  assert.equal(sp.saving, 1);
  assert.equal(sp.kind, "save");
});

test("bestSplit reports cover when no single shop has everything", () => {
  const d = { premises: [[1], [2]], prices: { 1: [[0, 1]], 2: [[1, 1]] } };
  const i = B.indexDistrict(d);
  const basket = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const rows = B.rankStores(i, 2, basket);
  assert.equal(B.summarise(rows, 2).kind, "none");
  assert.equal(B.bestSplit(i, 2, basket, rows).kind, "cover");
});

test("cheapestFor sorts by price", () => {
  assert.deepEqual(B.cheapestFor(idx, 10).map(x => x.pidx), [1, 0, 2]);
  assert.deepEqual(B.cheapestFor(idx, 99), []);
});

test("median", () => {
  assert.equal(B.median([3, 1, 2]), 2);
  assert.equal(B.median([4, 1, 2, 3]), 2.5);
  assert.equal(B.median([]), null);
});

// ---------- Phase 1: any brand, 1-3 stop plans, averages, pack sizes ----------

test("priceAt takes the cheapest accepted item when 'any brand' is on", () => {
  // item 40 is another brand of item 10, only sold at shop B and cheaper there.
  const d = { premises: [[1], [2]], prices: { 10: [[0, 5.0, 0], [1, 6.0, 0]], 40: [[1, 3.5, 4]] } };
  const i = B.indexDistrict(d);
  assert.deepEqual(B.priceAt(i, { code: 10, qty: 1 }, 1), { p: 6.0, d: 0, code: 10 });
  assert.deepEqual(B.priceAt(i, { code: 10, qty: 1, alt: [40] }, 1), { p: 3.5, d: 4, code: 40 });
  const rows = B.rankStores(i, 2, [{ code: 10, qty: 2, alt: [40] }]);
  assert.equal(rows[0].pidx, 1);
  assert.equal(rows[0].total, 7);
  assert.equal(rows[0].lines[10].code, 40);
});

// 4 shops, 3 items: each item is cheapest at a different shop.
const d4 = {
  premises: [[1], [2], [3], [4]],
  prices: {
    1: [[0, 10, 0], [1, 4, 0], [2, 10, 0], [3, 9, 0]],
    2: [[0, 10, 0], [1, 10, 0], [2, 4, 0], [3, 9, 0]],
    3: [[0, 10, 0], [1, 10, 0], [2, 10, 0], [3, 4, 0]],
  },
};
const i4 = B.indexDistrict(d4);
const bask3 = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }, { code: 3, qty: 1 }];

test("bestPlan finds the cheapest 3-shop combination", () => {
  const rows = B.rankStores(i4, 4, bask3);
  assert.equal(rows[0].pidx, 3);            // single best: 9 + 9 + 4 = 22
  assert.equal(rows[0].total, 22);
  const p3 = B.bestPlan(i4, 4, bask3, rows, 3);
  assert.deepEqual(p3.shops.slice().sort(), [1, 2, 3]);
  assert.equal(p3.total, 12);
  assert.deepEqual(p3.assign, { 1: 1, 2: 2, 3: 3 });
  const p2 = B.bestPlan(i4, 4, bask3, rows, 2);
  assert.equal(p2.total, 17);               // shop B or C (4) plus D (9 + 4)
});

test("planOptions recommends extra stops only when they beat the stop value", () => {
  const rows = B.rankStores(i4, 4, bask3);
  let po = B.planOptions(i4, 4, bask3, rows, 2);
  assert.deepEqual(po.options.map(o => [o.k, o.total, o.saving]), [[1, 22, 0], [2, 17, 5], [3, 12, 10]]);
  assert.equal(po.rec, 3);                   // 12 + 2x2 = 16 beats 17 + 2 and 22
  po = B.planOptions(i4, 4, bask3, rows, 5);
  assert.equal(po.rec, 1);                   // 22 vs 17 + 5 = 22 vs 12 + 10 = 22: ties keep fewer stops
  po = B.planOptions(i4, 4, bask3, rows, 6);
  assert.equal(po.rec, 1);                   // 22 vs 17 + 6 = 23 vs 12 + 12 = 24
});

test("planOptions prefers a plan that covers more items", () => {
  const d = { premises: [[1], [2]], prices: { 1: [[0, 1, 0]], 2: [[1, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const po = B.planOptions(i, 2, bk, B.rankStores(i, 2, bk), 50);
  assert.equal(po.rec, 2);
  assert.equal(po.options[1].saving, null);
});

test("averageRank uses each shop's monthly prices and counts cheapest months", () => {
  // Shop 0 is cheap now but dear before; shop 1 is steady.
  const sm = { 1: [[0, 9, 9, 3], [1, 5, 5, 5]], 2: [[0, 2, 2, 2], [1, 2, 2, null]] };
  const rows = B.averageRank(sm, 2, [{ code: 1, qty: 1 }, { code: 2, qty: 2 }], 3);
  assert.equal(rows[0].pidx, 1);             // 5 + 2x2 = 9
  assert.equal(rows[0].total, 9);
  assert.equal(rows[1].total, 11);           // 7 + 2x2
  assert.equal(rows[0].cheapestMonths, 2);   // months 1 and 2; month 3 shop 1 lacks item 2
  assert.equal(rows[0].fullMonths, 2);
  assert.equal(rows[1].cheapestMonths, 1);
  assert.equal(rows[1].fullMonths, 3);
});

test("parseUnit and unitPrice read PriceCatcher units", () => {
  assert.deepEqual(B.parseUnit("10 kg"), { amt: 10000, base: "g" });
  assert.deepEqual(B.parseUnit("1kg"), { amt: 1000, base: "g" });
  assert.deepEqual(B.parseUnit("+-350g"), { amt: 350, base: "g" });
  assert.deepEqual(B.parseUnit("1.5 liter"), { amt: 1500, base: "ml" });
  assert.deepEqual(B.parseUnit("946ML"), { amt: 946, base: "ml" });
  assert.deepEqual(B.parseUnit("5 X 79g"), { amt: 395, base: "g" });
  assert.deepEqual(B.parseUnit("30 biji"), { amt: 30, base: "pc" });
  assert.equal(B.parseUnit("S80, M60, L58, XL50"), null);
  assert.deepEqual(B.unitPrice(29.9, "5 kg"), { v: 5.98, per: "kg" });
  assert.equal(B.unitPrice(15, "30 biji").v, 0.5);
});
