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

// ---------- locatePostcode ----------

// Values may be [lat, lon] or [lat, lon, accuracy].
const pcs = { "40100": [3.07, 101.52, 4], "40150": [3.09, 101.54], "47500": [3.04, 101.58, 6], "01000": [6.43, 100.19, 4] };
const near = (a, b) => Math.abs(a - b) < 1e-9;

test("locatePostcode: an exact match is level 5", () => {
  assert.deepEqual(N.locatePostcode("40100", pcs), { lat: 3.07, lon: 101.52, exact: true, level: 5 });
  assert.deepEqual(N.locatePostcode(" 40150 ", pcs), { lat: 3.09, lon: 101.54, exact: true, level: 5 });
  assert.deepEqual(N.locatePostcode("01000", pcs), { lat: 6.43, lon: 100.19, exact: true, level: 5 });
});

test("locatePostcode: else the average of postcodes sharing 4, then 3, then 2 digits", () => {
  const four = N.locatePostcode("40155", pcs);          // shares "4015" with 40150 only
  assert.deepEqual(four, { lat: 3.09, lon: 101.54, exact: false, level: 4 });
  const three = N.locatePostcode("40170", pcs);         // shares "401" with 40100 and 40150
  assert.equal(three.exact, false);
  assert.equal(three.level, 3);
  assert.ok(near(three.lat, 3.08) && near(three.lon, 101.53), JSON.stringify(three));
  const two = N.locatePostcode("47999", pcs);           // shares only "47" with 47500
  assert.deepEqual(two, { lat: 3.04, lon: 101.58, exact: false, level: 2 });
  const two2 = N.locatePostcode("40999", pcs);          // shares only "40" with 40100 and 40150
  assert.equal(two2.level, 2);
  assert.ok(near(two2.lat, 3.08) && near(two2.lon, 101.53), JSON.stringify(two2));
  // A leading zero is kept: "01999" shares "01" with 01000.
  assert.deepEqual(N.locatePostcode("01999", pcs), { lat: 6.43, lon: 100.19, exact: false, level: 2 });
});

test("locatePostcode: null when nothing shares 2 digits, or the input is not a postcode", () => {
  assert.equal(N.locatePostcode("41000", pcs), null);
  assert.equal(N.locatePostcode("99999", pcs), null);
  for (const bad of ["4010", "401000", "4o100", "", "  ", null, undefined]) assert.equal(N.locatePostcode(bad, pcs), null, String(bad));
  assert.equal(N.locatePostcode("40100", null), null);
  assert.equal(N.locatePostcode("40100", {}), null);
});

test("locatePostcode: a group with a far-off point is placed in its main cluster, not between them", () => {
  // Three Johor Bahru postcodes and two wrong ones in Sabah: 80000 must land in Johor Bahru, not in the sea.
  const t = { "80300": [1.4716, 103.767], "80350": [1.5016, 103.742], "80400": [1.4787, 103.7749],
              "80838": [5.2804, 115.2455], "80883": [5.2786, 115.2424] };
  const hit = N.locatePostcode("80000", t);
  assert.equal(hit.level, 2);
  assert.equal(hit.exact, false);
  assert.ok(near(hit.lat, (1.4716 + 1.5016 + 1.4787) / 3) && near(hit.lon, (103.767 + 103.742 + 103.7749) / 3), JSON.stringify(hit));
  assert.ok(N.km(hit.lat, hit.lon, 1.4927, 103.7414) < 5, "within 5 km of Johor Bahru");
  // A group of one far point and one near point still gives a real place, never the middle between them.
  const two = N.locatePostcode("12999", { "12000": [5.4037, 100.3692], "12085": [6.1294, 102.3155] });
  const d0 = N.km(two.lat, two.lon, 5.4037, 100.3692), d1 = N.km(two.lat, two.lon, 6.1294, 102.3155);
  assert.ok(Math.min(d0, d1) < 1e-6, JSON.stringify(two));
});

test("locatePostcode skips postcodes that have no point", () => {
  const t = { "40100": null, "40150": [3.09, 101.54] };
  assert.deepEqual(N.locatePostcode("40100", t), { lat: 3.09, lon: 101.54, exact: false, level: 3 });
});

// ---------- mergeNear ----------

// The user stands at 3.0, 101.5. One hundredth of a degree is about 1.1 km.
const LAT = 3.0, LON = 101.5;

function files2() {
  const one = { state: "A", district: "One", months: ["m1"], data_date: "d1", synthetic: false,
    premise_cols: ["premise_code", "premise", "premise_type", "address", "lat", "lon", "geo_precision"],
    centre: [3.001, 101.501],
    premises: [[11, "Near1", "T", "x", 3.000, 101.505, "address"],   // 0.6 km
               [12, "Far1", "T", "x", 3.100, 101.500, "address"],    // 11 km: outside
               [13, "Unloc1", "T", "x", 3.001, 101.501, "district"], // district centre, 0.2 km away
               [14, "NoPoint1", "T", "x", null, null, null]],
    price_cols: ["premise_index", "price", "days_old"],
    prices: { 1: [[0, 5, 0], [1, 4, 0], [2, 3, 0], [3, 7, 2]] },
    shop_months: { 1: [[0, 5, 5, 5], [1, 4, 4, 4], [2, 3, 3, 3]] },
    avg_months: ["a", "b", "c"], history: { 1: [5] }, feed: { rises: [] } };
  const two = { state: "A", district: "Two", months: ["m2"], data_date: "d2", synthetic: false,
    premise_cols: [], centre: [3.002, 101.500],
    premises: [[21, "Near2", "T", "x", 3.010, 101.500, "postcode"],  // 1.1 km
               [11, "Near1 again", "T", "x", 3.000, 101.505, "address"],
               [22, "Unloc2", "T", "x", 3.002, 101.500, "district"]], // inside the radius, but not home
    prices: { 1: [[0, 6, 1], [1, 5, 0], [2, 1, 0]], 2: [[0, 2, 0], [2, 1, 0]] },
    shop_months: { 2: [[0, 2, null, 2], [2, 1, 1, 1]] },
    avg_months: ["a", "b", "c"], history: {}, feed: null };
  return [one, two];
}

test("mergeNear never counts a district-precision shop inside the radius", () => {
  const m = N.mergeNear(files2(), LAT, LON, 5);
  // Unloc1's point (the district centre) is 0.2 km away, yet it is not a located shop.
  assert.deepEqual(m.premises.map(p => p[1]), ["Near1", "Near2", "Unloc1", "NoPoint1"]);
  assert.deepEqual(m.premises.map(p => p[7]), [0.6, 1.1, null, null]);
  assert.deepEqual(m.premises.map(p => p[8]), ["One", "Two", "One", "One"]);
  assert.deepEqual(m.premises[2], [13, "Unloc1", "T", "x", 3.001, 101.501, "district", null, "One"]);
  assert.deepEqual(m.premises[3], [14, "NoPoint1", "T", "x", null, null, null, null, "One"]);
  // Unloc2 is in district Two, which is not the home district, so it is not listed at all.
  assert.ok(!m.premises.some(p => p[0] === 22));
  assert.deepEqual(m.near, { radius: 5, districts: ["One", "Two"], located: 2, unlocated: 2, home: "One" });
  assert.deepEqual(m.premise_cols,
    ["premise_code", "premise", "premise_type", "address", "lat", "lon", "geo_precision", "km", "district"]);
});

test("mergeNear counts name, address, postcode and town shops as located, district shops never", () => {
  // This file has no prices key at all.
  const f = { district: "T", centre: [3.0, 101.5], premises: [
    [91, "By name", "T", "x", 3.001, 101.5, "name"], [92, "By street", "T", "x", 3.002, 101.5, "address"],
    [93, "By postcode", "T", "x", 3.003, 101.5, "postcode"], [94, "By town", "T", "x", 3.004, 101.5, "town"],
    [95, "Unknown", "T", "x", 3.0, 101.5, "district"]] };
  const m = N.mergeNear([f], LAT, LON, 5);
  assert.deepEqual(m.premises.map(p => [p[0], p[6], p[7]]),
    [[91, "name", 0.1], [92, "address", 0.2], [93, "postcode", 0.3], [94, "town", 0.4], [95, "district", null]]);
  assert.deepEqual(m.near, { radius: 5, districts: ["T"], located: 4, unlocated: 1, home: "T" });
  assert.deepEqual(m.prices, {});
});

test("mergeNear leaves its input files unchanged", () => {
  // The screen keeps loaded files and merges them again whenever the radius changes.
  const files = files2(), before = JSON.stringify(files);
  const m5 = N.mergeNear(files, LAT, LON, 5);
  const m20 = N.mergeNear(files, LAT, LON, 20);
  assert.equal(JSON.stringify(files), before);
  assert.deepEqual(N.mergeNear(files, LAT, LON, 5), m5);
  assert.deepEqual(m20.premises.map(p => p[1]), ["Near1", "Near2", "Far1", "Unloc1", "NoPoint1"]);   // Far1 is 11 km away
  assert.deepEqual(m20.near, { radius: 20, districts: ["One", "Two"], located: 3, unlocated: 2, home: "One" });
});

test("mergeNear re-indexes prices and shop_months and drops shops it does not keep", () => {
  const m = N.mergeNear(files2(), LAT, LON, 5);
  // New indexes: Near1 0, Near2 1, Unloc1 2, NoPoint1 3. Far1, Unloc2 and the second Near1 are dropped.
  assert.deepEqual(m.prices[1], [[0, 5, 0], [2, 3, 0], [3, 7, 2], [1, 6, 1]]);
  assert.deepEqual(m.prices[2], [[1, 2, 0]]);
  assert.deepEqual(m.shop_months[1], [[0, 5, 5, 5], [2, 3, 3, 3]]);
  assert.deepEqual(m.shop_months[2], [[1, 2, null, 2]]);
  // History, feed and months come from the home district (One).
  assert.equal(m.district, "One");
  assert.deepEqual(m.history, { 1: [5] });
  assert.deepEqual(m.feed, { rises: [] });
  assert.deepEqual(m.months, ["m1"]);
  assert.equal(m.data_date, "d1");
  assert.deepEqual(m.avg_months, ["a", "b", "c"]);
  assert.deepEqual(m.price_cols, ["premise_index", "price", "days_old"]);
});

test("mergeNear keeps a shop listed in two files once", () => {
  const m = N.mergeNear(files2(), LAT, LON, 5);
  assert.equal(m.premises.filter(p => p[0] === 11).length, 1);
  assert.equal(m.premises[0][1], "Near1");               // the first listing, from district One
  // A home-district shop with no location that another file locates: listed once, as located.
  const h = { district: "H", premises: [[81, "Dup (H)", "T", "x", 3.0, 101.5, "district"],
                                       [82, "H shop", "T", "x", 3.001, 101.5, "name"]],
              prices: { 1: [[0, 9, 0], [1, 4, 0]] } };
  const k = { district: "K", premises: [[81, "Dup (K)", "T", "x", 3.004, 101.5, "address"]], prices: { 1: [[0, 3, 1]] } };
  const m2 = N.mergeNear([h, k], LAT, LON, 5);
  assert.deepEqual(m2.premises.map(p => [p[0], p[1], p[7], p[8]]), [[82, "H shop", 0.1, "H"], [81, "Dup (K)", 0.4, "K"]]);
  assert.deepEqual(m2.near, { radius: 5, districts: ["H", "K"], located: 2, unlocated: 0, home: "H" });
  assert.deepEqual(m2.prices[1], [[0, 4, 0], [1, 3, 1]]);
});

test("mergeNear: the home district is the nearest located shop's district", () => {
  // The user stands on Inner's centre, but the nearest located shop is in Outer.
  const inner = { district: "Inner", centre: [3.0, 101.5], history: "inner", feed: "inner",
    premises: [[31, "Inner shop", "T", "x", 3.030, 101.5, "name"], [32, "Inner unloc", "T", "x", 3.0, 101.5, "district"]],
    prices: { 1: [[0, 2, 0], [1, 1, 0]] } };
  const outer = { district: "Outer", centre: [3.06, 101.5], history: "outer", feed: "outer",
    premises: [[41, "Outer shop", "T", "x", 3.010, 101.5, "address"], [42, "Outer unloc", "T", "x", 3.06, 101.5, "district"]],
    prices: { 1: [[0, 3, 0], [1, 4, 0]] } };
  const m = N.mergeNear([inner, outer], LAT, LON, 5);
  assert.deepEqual(m.premises.map(p => p[1]), ["Outer shop", "Inner shop", "Outer unloc"]);
  assert.deepEqual(m.near, { radius: 5, districts: ["Outer", "Inner"], located: 2, unlocated: 1, home: "Outer" });
  assert.equal(m.district, "Outer");
  assert.equal(m.history, "outer");
  assert.equal(m.feed, "outer");
  assert.deepEqual(m.prices[1], [[1, 2, 0], [0, 3, 0], [2, 4, 0]]);
});

test("mergeNear: with no located shop, the home district is the nearest centre within radius + 5 km", () => {
  // P gives its centre (2.2 km away) although its shops sit 22 km away. Q has no centre: the mean of its points (3.3 km).
  const P = () => ({ district: "P", centre: [3.02, 101.5], history: "P",
    premises: [[51, "P far shop", "T", "x", 3.2, 101.5, "address"], [52, "P unloc", "T", "x", 3.2, 101.5, "district"]],
    prices: { 1: [[0, 1, 0], [1, 2, 0]] } });
  const Q = () => ({ district: "Q", history: "Q",
    premises: [[61, "Q unloc", "T", "x", 3.03, 101.5, "district"], [62, "Q unloc 2", "T", "x", 3.03, 101.5, "district"]],
    prices: { 1: [[1, 3, 0]] } });
  let m = N.mergeNear([Q(), P()], LAT, LON, 3);
  assert.deepEqual(m.premises.map(p => [p[1], p[7], p[8]]), [["P unloc", null, "P"]]);
  assert.deepEqual(m.near, { radius: 3, districts: ["P"], located: 0, unlocated: 1, home: "P" });
  assert.equal(m.history, "P");
  assert.deepEqual(m.prices, { 1: [[0, 2, 0]] });
  // Without its centre, P's mean point is 22 km away, so Q (3.3 km) is home.
  const p2 = P(); delete p2.centre;
  m = N.mergeNear([p2, Q()], LAT, LON, 3);
  assert.deepEqual(m.premises.map(p => p[1]), ["Q unloc", "Q unloc 2"]);
  assert.deepEqual(m.near, { radius: 3, districts: ["Q"], located: 0, unlocated: 2, home: "Q" });
  assert.deepEqual(m.prices, { 1: [[1, 3, 0]] });
  // Q's centre at 7.8 km is within 3 + 5 km; at 8.2 km it is not, and nothing is listed.
  assert.equal(N.mergeNear([Q()], 2.96, LON, 3).near.home, "Q");
  m = N.mergeNear([Q()], 2.956, LON, 3);
  assert.deepEqual(m.premises, []);
  assert.deepEqual(m.near, { radius: 3, districts: [], located: 0, unlocated: 0, home: null });
  assert.equal(m.history, "Q");                          // falls back to the first file
});

test("mergeNear pads old 4- and 6-element premise rows", () => {
  const old = { district: "Old", premise_cols: ["premise_code", "premise", "premise_type", "address"],
    premises: [[71, "Old A", "T", "addr A"], [72, "Old B", "T", "addr B", 3.001, 101.5]],
    prices: { 1: [[0, 2, 0], [1, 3, 0]] } };
  const m = N.mergeNear([old], LAT, LON, 5);
  assert.deepEqual(m.premises, [[72, "Old B", "T", "addr B", 3.001, 101.5, null, 0.1, "Old"],
                                [71, "Old A", "T", "addr A", null, null, null, null, "Old"]]);
  assert.equal(m.premise_cols.length, 9);
  assert.deepEqual(m.prices[1], [[1, 2, 0], [0, 3, 0]]);
  assert.equal(m.shop_months, undefined);                // old files have no monthly prices
  assert.equal(m.avg_months, undefined);
  assert.deepEqual(m.near, { radius: 5, districts: ["Old"], located: 1, unlocated: 1, home: "Old" });
});

test("mergeNear with no files returns an empty result", () => {
  const m = N.mergeNear([], LAT, LON, 5);
  assert.deepEqual(m.premises, []);
  assert.deepEqual(m.near, { radius: 5, districts: [], located: 0, unlocated: 0, home: null });
});

test("the basket maths works on the merged data", () => {
  const m = N.mergeNear(files2(), LAT, LON, 5);
  const idx = B.indexDistrict(m);
  let rows = B.rankStores(idx, m.premises.length, [{ code: 1, qty: 1 }]);
  // Item 1: Near1 5, Near2 6, Unloc1 3, NoPoint1 7.
  assert.deepEqual(rows.map(r => [m.premises[r.pidx][1], r.total]),
                   [["Unloc1", 3], ["Near1", 5], ["Near2", 6], ["NoPoint1", 7]]);
  rows = B.rankStores(idx, m.premises.length, [{ code: 1, qty: 1 }, { code: 2, qty: 2 }]);
  assert.equal(m.premises[rows[0].pidx][1], "Near2");   // the only shop with both items: 6 + 2x2
  assert.equal(rows[0].total, 10);
  const avg = B.averageRank(m.shop_months, m.premises.length, [{ code: 1, qty: 1 }], 3);
  assert.deepEqual(avg.map(r => [m.premises[r.pidx][1], r.total]), [["Unloc1", 3], ["Near1", 5]]);
});

test("mergeNear does not copy the user's location into its result", () => {
  const m = N.mergeNear(files2(), 3.0004321, 101.5004321, 5);
  const s = JSON.stringify(m);
  assert.ok(s.indexOf("3.0004321") < 0 && s.indexOf("101.5004321") < 0);
});

test("mergeNear and districtsWithin treat a radius given as text as a number", () => {
  // "3" + 5 would be "35" (km) if the radius stayed text; the home-district reach must stay 3 + 5 km.
  const Q = () => ({ district: "Q", premises: [[61, "Q unloc", "T", "x", 3.03, 101.5, "district"]], prices: {} });
  assert.deepEqual(N.mergeNear([Q()], 2.956, LON, "3").near.home, null);   // centre 8.2 km away
  assert.equal(N.mergeNear([Q()], 2.96, LON, "3").near.home, "Q");          // 7.8 km away
  assert.deepEqual(N.mergeNear(files2(), LAT, LON, "5").near,
    { radius: 5, districts: ["One", "Two"], located: 2, unlocated: 2, home: "One" });
  assert.deepEqual(N.districtsWithin(areas, 3.0, 101.5, "5").map(x => x.slug), ["a--one"]);
});
