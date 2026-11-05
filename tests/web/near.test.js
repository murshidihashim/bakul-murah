// Unit tests for web/js/near.js.  Run: node --test tests/web/near.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const N = require(path.join(__dirname, "../../web/js/near.js"));
const B = require(path.join(__dirname, "../../web/js/basket.js"));

test("km matches known distances", () => {
  // KLCC to Shah Alam is about 25 km in a straight line.
  const d = N.km(3.1579, 101.7116, 3.0733, 101.5185);
  assert.ok(d > 22 && d < 25, String(d));
  assert.equal(N.km(3, 101, 3, 101), 0);
});

const areas = {
  A: [{ slug: "a--one", geo: [3.00, 101.50, 4] }, { slug: "a--two", geo: [3.10, 101.60, 3] }],
  B: [{ slug: "b--far", geo: [5.40, 100.30, 10] }, { slug: "b--nogeo" }],
};

test("districtsWithin picks districts that can reach the radius, nearest first", () => {
  const r = N.districtsWithin(areas, 3.0, 101.5, 5).map(x => x.slug);
  assert.deepEqual(r, ["a--one"]);
  assert.deepEqual(N.districtsWithin(areas, 3.0, 101.5, 20).map(x => x.slug), ["a--one", "a--two"]);
});

test("locatePostcode: exact, then area by prefix, else null", () => {
  const t = { "40100": [3.07, 101.52], "40150": [3.09, 101.54], "47500": [3.04, 101.58] };
  assert.deepEqual(N.locatePostcode("40100", t), { lat: 3.07, lon: 101.52, exact: true });
  const a = N.locatePostcode("40170", t);              // shares "401" with two postcodes
  assert.equal(a.exact, false);
  assert.ok(Math.abs(a.lat - 3.08) < 1e-9 && Math.abs(a.lon - 101.53) < 1e-9);
  assert.equal(N.locatePostcode("99999", t), null);
  assert.equal(N.locatePostcode("4010", t), null);
});

test("mergeNear keeps shops inside the radius from several districts, nearest first", () => {
  const one = { state: "A", district: "One", months: ["m"], data_date: "d", premise_cols: [],
    premises: [[11, "Near1", "T", "x", 3.000, 101.505, "address"],   // ~0.6 km
               [12, "Far1", "T", "x", 3.100, 101.500, "address"],    // ~11 km
               [13, "NoGeo", "T", "x", null, null, null]],
    prices: { 1: [[0, 5, 0], [1, 4, 0], [2, 3, 0]] }, shop_months: { 1: [[0, 5, 5, 5], [1, 4, 4, 4]] },
    avg_months: ["a", "b", "c"], history: { 1: [5] }, feed: { rises: [] } };
  const two = { state: "A", district: "Two", months: ["m"], data_date: "d", premise_cols: [],
    premises: [[21, "Near2", "T", "x", 3.010, 101.500, "postcode"],  // ~1.1 km
               [11, "Near1 again", "T", "x", 3.000, 101.505, "address"]],
    prices: { 1: [[0, 6, 1], [1, 5, 0]], 2: [[0, 2, 0]] }, shop_months: { 2: [[0, 2, null, 2]] },
    avg_months: ["a", "b", "c"], history: {}, feed: null };
  const m = N.mergeNear([one, two], 3.0, 101.5, 5);
  assert.deepEqual(m.premises.map(p => p[1]), ["Near1", "Near2"]);
  assert.deepEqual(m.premises.map(p => p[8]), ["One", "Two"]);
  assert.equal(m.premises[0][7], 0.6);
  assert.deepEqual(m.prices[1], [[0, 5, 0], [1, 6, 1]]);
  assert.deepEqual(m.prices[2], [[1, 2, 0]]);
  assert.deepEqual(m.shop_months[2], [[1, 2, null, 2]]);
  assert.equal(m.district, "One");                     // history and feed from the nearest shop's district
  assert.deepEqual(m.near.districts, ["One", "Two"]);
  // The basket maths works on the merged data unchanged.
  const rows = B.rankStores(B.indexDistrict(m), m.premises.length, [{ code: 1, qty: 1 }]);
  assert.equal(rows[0].pidx, 0);
});
