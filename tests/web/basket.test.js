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
  // Pairs: A+B = 4+4+9 = 17 ; A+C = 5+4+8 = 17 ; B+C = 4+5+8 = 17. Tie -> the first pair tried
  // (candidates follow the ranking A, C, B, so A+C).
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

test("planOptions drops a 2-stop plan that covers fewer items than 1 stop", () => {
  // Shop 0 sells items 1, 2, 3 at RM 1 each. Shop 1 sells item 1 at RM 2, shop 2 sells item 2 at RM 3.
  // The only 2-shop plan that uses both shops is 1 + 2: 2 items for RM 5. It used to be offered as "Save RM 0.00".
  const d = { premises: [[1], [2], [3]],
              prices: { 1: [[0, 1, 0], [1, 2, 0]], 2: [[0, 1, 0], [2, 3, 0]], 3: [[0, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }, { code: 3, qty: 1 }];
  const rows = B.rankStores(i, 3, bk);
  assert.deepEqual(B.bestPlan(i, 3, bk, rows, 2), { shops: [1, 2], total: 5, have: 2, assign: { 1: 1, 2: 2 } });
  const po = B.planOptions(i, 3, bk, rows, 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 3, 3, 0]]);
  assert.equal(po.rec, 1);
});

test("planOptions drops an extra stop that saves nothing", () => {
  // Shop 0 has both items for RM 2. Shops 1 and 2 have one each for RM 1: the same 2 items for the same RM 2.
  const d = { premises: [[1], [2], [3]], prices: { 1: [[0, 1, 0], [1, 1, 0]], 2: [[0, 1, 0], [2, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const rows = B.rankStores(i, 3, bk);
  assert.equal(B.bestPlan(i, 3, bk, rows, 2).total, 2);
  const po = B.planOptions(i, 3, bk, rows, 0);
  assert.deepEqual(po.options.map(o => o.k), [1]);
  assert.equal(po.rec, 1);
});

test("planOptions keeps a same-coverage plan that is at least RM 0.01 cheaper", () => {
  const d = { premises: [[1], [2]], prices: { 1: [[0, 1.0, 0], [1, 0.99, 0]], 2: [[0, 1.0, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const po = B.planOptions(i, 2, bk, B.rankStores(i, 2, bk), 0);
  assert.deepEqual(po.options.map(o => [o.k, o.total, o.saving]), [[1, 2, 0], [2, 1.99, 0.01]]);
  assert.equal(po.rec, 1);                   // saves under RM 1, so one stop is still recommended
});

test("planOptions drops a 3-stop plan that covers fewer items than the 2-stop plan", () => {
  // Shop 0 has all 4 items at RM 1; shop 1 has item 1 at RM 0.50; shops 2-4 have one dear item each.
  // Best 3 shops that all get used: 1 + 2 + 3, only 3 items.
  const d = { prices: { 1: [[0, 1, 0], [1, 0.5, 0]], 2: [[0, 1, 0], [2, 3, 0]],
                        3: [[0, 1, 0], [3, 3, 0]], 4: [[0, 1, 0], [4, 3, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3, 4].map(code => ({ code, qty: 1 }));
  const rows = B.rankStores(i, 5, bk);
  assert.equal(B.bestPlan(i, 5, bk, rows, 3).have, 3);
  const po = B.planOptions(i, 5, bk, rows, 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 4, 4, 0], [2, 4, 3.5, 0.5]]);
  assert.equal(po.rec, 1);                   // RM 0.50 is under the RM 1 minimum for an extra stop
});

test("planOptions drops a 3-stop plan that costs the same as the 2-stop plan", () => {
  // 2 stops (shops 0 + 1) buy all 3 items for RM 5. Shops 1 + 3 + 4 also buy all 3 for RM 5.
  const d = { prices: { 1: [[0, 2, 0], [1, 1, 0], [2, 1, 0]], 2: [[0, 2, 0], [3, 2, 0]], 3: [[0, 2, 0], [4, 2, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3].map(code => ({ code, qty: 1 }));
  const rows = B.rankStores(i, 5, bk);
  assert.deepEqual(B.bestPlan(i, 5, bk, rows, 3).shops, [1, 3, 4]);
  assert.equal(B.bestPlan(i, 5, bk, rows, 3).total, 5);
  const po = B.planOptions(i, 5, bk, rows, 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 3, 6, 0], [2, 3, 5, 1]]);
  assert.equal(po.rec, 2);
});

test("planOptions counts a RM 1.00 saving as RM 1 despite float maths", () => {
  // 8.11 - 7.11 is 0.9999999999999991 in floating point; the extra stop still saves RM 1.00.
  const d = { premises: [[1], [2]], prices: { 1: [[0, 3.61, 0], [1, 2.61, 0]], 2: [[0, 4.5, 0]] } };
  const i = B.indexDistrict(d), bk = [{ code: 1, qty: 1 }, { code: 2, qty: 1 }];
  const po = B.planOptions(i, 2, bk, B.rankStores(i, 2, bk), 0);
  assert.deepEqual(po.options.map(o => [o.k, o.total, o.saving]), [[1, 8.11, 0], [2, 7.11, 1]]);
  assert.equal(po.rec, 2);
});

test("planOptions can drop the 2-stop plan and keep the 3-stop plan", () => {
  // 0.3 kg of each item. Shops 1-3 each sell one item for RM 0.99 a kg, against RM 1 at shop 0.
  // 2 stops save RM 0.003, which rounds to nothing; 3 stops (shops 1-3) save RM 0.009, which rounds to RM 0.01.
  const d = { prices: { 1: [[0, 1, 0], [1, 0.99, 0]], 2: [[0, 1, 0], [2, 0.99, 0]], 3: [[0, 1, 0], [3, 0.99, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3].map(code => ({ code, qty: 0.3 }));
  const rows = B.rankStores(i, 4, bk);
  assert.equal(B.bestPlan(i, 4, bk, rows, 2).total, 0.9);
  const po = B.planOptions(i, 4, bk, rows, 0);
  // There is no 2-stop option, so the screen must look options up by k, not by position.
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 3, 0.9, 0], [3, 3, 0.89, 0.01]]);
  assert.equal(po.rec, 1);
});

test("planOptions keeps each plan that covers more items, with no saving figure", () => {
  // Shop 0 has items 1 and 2; shop 1 has item 3; shop 2 has item 4.
  const d = { prices: { 1: [[0, 1, 0]], 2: [[0, 1, 0]], 3: [[1, 1, 0]], 4: [[2, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3, 4].map(code => ({ code, qty: 1 }));
  const po = B.planOptions(i, 3, bk, B.rankStores(i, 3, bk), 5);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 2, 2, 0], [2, 3, 3, null], [3, 4, 4, null]]);
  assert.equal(po.rec, 3);                   // the most items wins, whatever one stop is worth
});

test("planOptions compares a 3-stop plan with a 2-stop plan that covers more than 1 stop", () => {
  // 1 stop (shop 0) buys 2 of the 3 items for RM 10; 2 stops (shops 0 + 1) buy all 3 for RM 11.
  // 3 stops also buy all 3, for RM 8 (item 1 at shop 2). Kept, and its saving is null: it covers more than 1 stop.
  const d = { prices: { 1: [[0, 5, 0], [2, 2, 0]], 2: [[0, 5, 0]], 3: [[1, 1, 0]] } };
  const i = B.indexDistrict(d), bk = [1, 2, 3].map(code => ({ code, qty: 1 }));
  const rows = B.rankStores(i, 3, bk);
  let po = B.planOptions(i, 3, bk, rows, 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total, o.saving]), [[1, 2, 10, 0], [2, 3, 11, null], [3, 3, 8, null]]);
  assert.equal(po.rec, 3);                   // RM 8 beats RM 11
  assert.equal(B.planOptions(i, 3, bk, rows, 5).rec, 2);   // 11 + 5 = 16 beats 8 + 10 = 18
  // With item 1 at RM 4.50, 3 stops still beat 2 stops (RM 10.50 vs RM 11), but by under RM 1.
  const d2 = { prices: { 1: [[0, 5, 0], [2, 4.5, 0]], 2: [[0, 5, 0]], 3: [[1, 1, 0]] } };
  const i2 = B.indexDistrict(d2);
  po = B.planOptions(i2, 3, bk, B.rankStores(i2, 3, bk), 0);
  assert.deepEqual(po.options.map(o => [o.k, o.have, o.total]), [[1, 2, 10], [2, 3, 11], [3, 3, 10.5]]);
  assert.equal(po.rec, 2);
});

test("planOptions with no rows or an empty basket", () => {
  assert.deepEqual(B.planOptions(idx, 3, [{ code: 10, qty: 1 }], [], 5), { options: [], rec: 0 });
  assert.deepEqual(B.planOptions(idx, 3, [], B.rankStores(idx, 3, []), 5), { options: [], rec: 0 });
});

test("sharedTotals compares two shops on the lines both of them price", () => {
  const basket = [{ code: 10, qty: 1 }, { code: 20, qty: 2 }, { code: 30, qty: 1 }];
  // Shop B (1) lacks item 30, so only items 10 and 20 are compared: A = 5 + 2x2, B = 4 + 2x3.
  assert.deepEqual(B.sharedTotals(idx, basket, 0, 1), { n: 2, a: 9, b: 10 });
  assert.deepEqual(B.sharedTotals(idx, basket, 1, 0), { n: 2, a: 10, b: 9 });
  assert.deepEqual(B.sharedTotals(idx, basket, 0, 2), { n: 3, a: 18, b: 19 });
  assert.deepEqual(B.sharedTotals(idx, basket, 2, 2), { n: 3, a: 19, b: 19 });
  assert.deepEqual(B.sharedTotals(idx, [{ code: 99, qty: 1 }], 0, 1), { n: 0, a: 0, b: 0 });
  assert.deepEqual(B.sharedTotals(idx, [], 0, 1), { n: 0, a: 0, b: 0 });
});

test("sharedTotals is any-brand aware and rounds to 2 decimals", () => {
  // Item 40 is another brand of item 10; shop 1 sells only item 40.
  const d = { premises: [[1], [2]], prices: { 10: [[0, 5.1, 0]], 40: [[1, 3.3, 2]], 20: [[0, 0.1, 0], [1, 0.2, 0]] } };
  const i = B.indexDistrict(d);
  assert.deepEqual(B.sharedTotals(i, [{ code: 10, qty: 1 }, { code: 20, qty: 3 }], 0, 1), { n: 1, a: 0.3, b: 0.6 });
  assert.deepEqual(B.sharedTotals(i, [{ code: 10, qty: 1, alt: [40] }, { code: 20, qty: 3 }], 0, 1),
                   { n: 2, a: 5.4, b: 3.9 });
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
