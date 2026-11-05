// Unit tests for web/js/listparse.js.  Run: node --test tests/web/listparse.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");
const LP = require(path.join(__dirname, "../../web/js/listparse.js"));

// A small catalogue with real PriceCatcher names and units.
const ITEMS = [
  [1, "AYAM BERSIH - STANDARD", "1kg", "BARANGAN SEGAR", "AYAM"],
  [2, "AYAM BERSIH - SUPER", "1kg", "BARANGAN SEGAR", "AYAM"],
  [1109, "TELUR AYAM GRED A", "30 biji", "BARANGAN SEGAR", "TELUR"],
  [118, "TELUR AYAM GRED A (BERAT 65.0 GM HINGGA 69.9 GM SEBIJI)", "10 biji", "BARANGAN SEGAR", "TELUR"],
  [1110, "TELUR AYAM GRED B", "30 biji", "BARANGAN SEGAR", "TELUR"],
  [1445, "BERAS CAP FAIZA EMAS (SST5%)", "10 kg", "BARANGAN BERBUNGKUS", "BERAS"],
  [1582, "BERAS CAP JASMINE (SST5%)", "10 kg", "BARANGAN BERBUNGKUS", "BERAS"],
  [1492, "BERAS PULUT THAILAND (BIASA) PELBAGAI JENAMA", "1kg", "BARANGAN BERBUNGKUS", "BERAS"],
  [254, "MINYAK MASAK SEBATIAN CAP RED EAGLE", "5 kg", "BARANGAN BERBUNGKUS", "MINYAK DAN LEMAK"],
  [1598, "MINYAK MASAK SEBATIAN CAP RED EAGLE", "2 kg", "BARANGAN BERBUNGKUS", "MINYAK DAN LEMAK"],
  [1939, "MINYAK MASAK TULEN CAP SAJI", "5 kg", "BARANGAN BERBUNGKUS", "MINYAK DAN LEMAK"],
  [918, "MINYAK MASAK PAKET (PELBAGAI JENAMA)", "1kg", "BARANGAN BERBUNGKUS", "MINYAK DAN LEMAK"],
  [1440, "BAWANG BESAR IMPORT (INDIA)", "1kg", "BARANGAN KERING", "BAWANG"],
  [1564, "BAWANG PUTIH IMPORT (CHINA)", "1kg", "BARANGAN KERING", "BAWANG"],
  [2114, "MILO (PAKET)", "900 g", "MINUMAN", "BAHAN-BAHAN MINUMAN"],
  [1355, "100 PLUS (ORIGINAL)", "1.5 liter", "MINUMAN", "TERSEDIA MINUM"],
  [206, "MARJERIN PLANTA", "480 g", "BARANGAN BERBUNGKUS", "SAPUAN (SPREADS)"],
  [205, "MARJERIN DAISY", "480 g", "BARANGAN BERBUNGKUS", "SAPUAN (SPREADS)"],
  [1559, "KANGKUNG", "1kg", "BARANGAN SEGAR", "SAYUR-SAYURAN"],
  [47, "IKAN CENCARU (ANTARA 4 HINGGA 6 EKOR SEKILOGRAM)", "1kg", "BARANGAN SEGAR", "BAHAN LAUT"],
  [1476, "IKAN KEMBUNG (ANTARA 8 HINGGA 12 EKOR SEKILOGRAM)", "1kg", "BARANGAN SEGAR", "BAHAN LAUT"],
].map(r => ({ code: r[0], item: r[1], unit: r[2], item_group: r[3], item_category: r[4] }));
const COVER = { 1: 30, 2: 10, 1109: 25, 1445: 20, 1582: 15, 254: 20, 1939: 12, 1440: 22 };
const cat = new LP.Catalogue(ITEMS, c => COVER[c] || 1);
const m = s => cat.matchLine(s);

test("splitLines handles WhatsApp-style lists", () => {
  assert.deepEqual(LP.splitLines("Barang dapur:\n- beras 10kg\n• telur, gula\n1. minyak RM29.90\n[ ] susu\n\n2) milo"),
                   ["beras 10kg", "telur", "gula", "minyak", "susu", "milo"]);
  assert.deepEqual(LP.splitLines("minyak 1,5 kg"), ["minyak 1.5 kg"]);
});

test("parseQty reads numbers, units and BM words", () => {
  assert.deepEqual(LP.parseQty("beras 10kg"), { words: ["beras"], n: 10, unit: "kg" });
  assert.deepEqual(LP.parseQty("telur 2 papan"), { words: ["telur"], n: 2, unit: "papan" });
  assert.deepEqual(LP.parseQty("2 x milo"), { words: ["milo"], n: 2, unit: "pek" });
  assert.deepEqual(LP.parseQty("milo x2"), { words: ["milo"], n: 2, unit: "pek" });
  assert.deepEqual(LP.parseQty("sekilo ayam"), { words: ["ayam"], n: 1, unit: "kg" });
  assert.deepEqual(LP.parseQty("setengah kilo bawang"), { words: ["bawang"], n: 0.5, unit: "kg" });
  assert.deepEqual(LP.parseQty("dua tin sardin"), { words: ["sardin"], n: 2, unit: "pek" });
  assert.deepEqual(LP.parseQty("100 plus 2 botol"), { words: ["100plus"], n: 2, unit: "pek" });
});

test("common staples match the right item and amount", () => {
  let r = m("beras 10kg");
  assert.equal(r.code, 1445); assert.equal(r.qty, 1); assert.equal(r.flex, true);
  assert.deepEqual(r.alt, [1582]);              // pulut (1 kg) is not rice for this purpose
  r = m("telur 2 papan");
  assert.equal(r.code, 1109); assert.equal(r.qty, 2); assert.equal(r.note, "");
  r = m("telur 20 biji");
  assert.equal(r.code, 118); assert.equal(r.qty, 2);
  r = m("eggs 30");
  assert.equal(r.code, 1109); assert.equal(r.qty, 1);
  r = m("ayam 2 ekor");
  assert.equal(r.code, 1); assert.equal(r.qty, 3); assert.equal(r.note, "ekor");
  r = m("chicken 1.5kg");
  assert.equal(r.code, 1); assert.equal(r.qty, 1.5);
  r = m("bawang 500g");
  assert.equal(r.code, 1440); assert.equal(r.qty, 0.5);
  r = m("bawang putih");
  assert.equal(r.code, 1564);
});

test("pack sizes: exact, multiples, close enough", () => {
  let r = m("minyak masak 10kg");
  assert.equal(r.code, 254); assert.equal(r.qty, 2); assert.equal(r.flex, true);
  assert.ok(!r.alt.includes(918));              // subsidised packet only when asked for
  r = m("minyak masak 2kg");
  assert.equal(r.code, 1598); assert.equal(r.qty, 1);
  r = m("milo 1kg");                            // 900 g pack is close enough
  assert.equal(r.code, 2114); assert.equal(r.qty, 1);
  r = m("minyak paket");
  assert.equal(r.code, 918);
});

test("brand, grade and typo handling", () => {
  let r = m("minyak saji 5kg");
  assert.equal(r.code, 1939); assert.equal(r.flex, false);
  r = m("telor gred b");
  assert.equal(r.code, 1110);
  r = m("Planta");
  assert.equal(r.code, 206); assert.equal(r.flex, false);
  r = m("marjerin");
  assert.equal(r.flex, true);
  r = m("bawng besar");
  assert.equal(r.code, 1440);
  r = m("100 plus 2 botol");
  assert.equal(r.code, 1355); assert.equal(r.qty, 2);
});

test("vague, unknown and filler lines", () => {
  let r = m("ikan");
  assert.equal(r.status, "pick"); assert.ok([47, 1476].includes(r.code));
  r = m("roti gardenia");
  assert.equal(r.status, "none"); assert.equal(r.code, null);
  r = m("barang dapur");
  assert.equal(r.status, "none");
  r = m("kembung");
  assert.equal(r.code, 1476); assert.equal(r.status, "ok");
});

// Against the full real item list, when the built data is present.
const META = path.join(__dirname, "../../web/data/meta.json");
test("every concept finds at least one real item", { skip: !fs.existsSync(META) }, () => {
  const meta = JSON.parse(fs.readFileSync(META, "utf8"));
  if (meta.synthetic) return;
  const items = meta.items.map(r => ({ code: r[0], item: r[1], unit: r[2], item_group: r[3], item_category: r[4] }));
  const full = new LP.Catalogue(items);
  const empty = LP.CONCEPTS.filter(c => !full.conceptItems(c).length).map(c => c.id);
  assert.deepEqual(empty, []);
});
