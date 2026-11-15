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
  [1589, "GULA PUTIH BERTAPIS KASAR (PELBAGAI JENAMA)", "1kg", "BARANGAN BERBUNGKUS", "GULA"],
  [1976, "MAGGI MI SEGERA PERISA KARI", "5 X 79g", "BARANGAN BERBUNGKUS", "MI SEGERA"],
  [1142, "SARDIN CAP ADABI (SOS TOMATO DENGAN CILI)", "425 g", "BARANGAN BERBUNGKUS", "IKAN DALAM TIN"],
  [1635, "NESTLE COFFEE-MATE", "450 g", "MINUMAN", "BAHAN-BAHAN MINUMAN"],
  [917, "TEPUNG GANDUM NGP (BERBUNGKUS, CAP SAUH)", "1kg", "BARANGAN BERBUNGKUS", "TEPUNG"],
  [1849, "TEPUNG NAIK SENDIRI CAP 'BLUE KEY'", "1kg", "BARANGAN BERBUNGKUS", "TEPUNG"],
  [1852, "DESA FRESH MILK (KOTAK)", "1 liter", "MINUMAN", "TERSEDIA MINUM"],
  [2060, "SUSU MARIGOLD HL", "946ML", "MINUMAN", "TERSEDIA MINUM"],
].map(r => ({ code: r[0], item: r[1], unit: r[2], item_group: r[3], item_category: r[4] }));
const COVER = { 1: 30, 2: 10, 1109: 25, 1445: 20, 1582: 15, 254: 20, 1939: 12, 1440: 22, 2060: 9, 1852: 5 };
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

test("parseQty reads a pack count after the amount", () => {
  const two = (line, words, n, unit) => assert.deepEqual(LP.parseQty(line), { words, n, unit, times: 2 }, line);
  two("beras 10kg x 2", ["beras"], 10, "kg");
  two("gula 1kg x2", ["gula"], 1, "kg");
  two("beras 10 kg X 2", ["beras"], 10, "kg");
  two("beras 10kgx2", ["beras"], 10, "kg");
  two("beras 10kg × 2", ["beras"], 10, "kg");
  two("beras 10kg 2x", ["beras"], 10, "kg");
  two("beras 10kg 2 x", ["beras"], 10, "kg");
  two("beras 10kg 2 beg", ["beras"], 10, "kg");
  two("beras 10kg x 2 beg", ["beras"], 10, "kg");
  two("minyak masak 5kg (2 botol)", ["minyak", "masak"], 5, "kg");
  two("telur 2 papan x 2", ["telur"], 2, "papan");
  two("eggs 30 x 2", ["eggs"], 30, null);
  // The count can also come first.
  two("2 x beras 10kg", ["beras"], 10, "kg");
  two("2x10kg beras", ["beras"], 10, "kg");
  two("2 beg beras 10kg", ["beras"], 10, "kg");
  two("100 plus 2 botol 1.5l", ["100plus"], 1.5, "l");
  // No count: unchanged, and a leading "x 2" keeps the next word.
  assert.deepEqual(LP.parseQty("x2 biji sawi"), { words: ["biji", "sawi"], n: 2, unit: "pek" });
  assert.deepEqual(LP.parseQty("beras 10kg."), { words: ["beras"], n: 10, unit: "kg" });
  assert.deepEqual(LP.parseQty("minyak - 5kg"), { words: ["minyak"], n: 5, unit: "kg" });
  assert.deepEqual(LP.parseQty("telur 2papan"), { words: ["telur"], n: 2, unit: "papan" });
});

test("parseQty reads fractions and halves", () => {
  const amt = (line, words, n) => assert.deepEqual(LP.parseQty(line), { words, n, unit: "kg" }, line);
  amt("bawang putih 1/2 kg", ["bawang", "putih"], 0.5);
  amt("1 1/2 kg ayam", ["ayam"], 1.5);
  amt("½ kg bawang", ["bawang"], 0.5);
  amt("ayam 1½kg", ["ayam"], 1.5);
  amt("cili ¼ kg", ["cili"], 0.25);
  amt("bawang ¾kg", ["bawang"], 0.75);
  amt("bawang 1/4kg", ["bawang"], 0.25);
  amt("bawang .5kg", ["bawang"], 0.5);
  amt("separuh kilo bawang", ["bawang"], 0.5);
  amt("half kg onion", ["onion"], 0.5);
  amt("dua kilo setengah ayam", ["ayam"], 2.5);
  amt("ayam 1 setengah kg", ["ayam"], 1.5);
  amt("sekilo setengah ayam", ["ayam"], 1.5);
});

test("parseQty: a plain number before the amount", () => {
  // At the start (or after filler words) it is the pack count.
  assert.deepEqual(LP.parseQty("2 beras 10kg"), { words: ["beras"], n: 10, unit: "kg", times: 2 });
  assert.deepEqual(LP.parseQty("nak 2 beras 10kg"), { words: ["nak", "beras"], n: 10, unit: "kg", times: 2 });
  // With "x" it is the pack count wherever it is.
  assert.deepEqual(LP.parseQty("beras 2 x 10kg"), { words: ["beras"], n: 10, unit: "kg", times: 2 });
  // After the name it may be part of the name ("Super 5", "5%"), so matchLine decides.
  assert.deepEqual(LP.parseQty("beras 2 10kg"), { words: ["beras"], n: 10, unit: "kg", count: 2 });
  assert.deepEqual(LP.parseQty("beras super 5 10kg"), { words: ["beras", "super"], n: 10, unit: "kg", count: 5 });
  assert.deepEqual(LP.parseQty("beras 5% 10kg"), { words: ["beras"], n: 10, unit: "kg", count: 5 });
  // Not a pack count at all: a word again, and the weight is the amount (was 123 g).
  assert.deepEqual(LP.parseQty("dutch lady 123 850g"), { words: ["dutch", "lady", "123"], n: 850, unit: "g" });
  // After the amount a plain number stays a word: it can be a name ("super 5") or a price.
  assert.deepEqual(LP.parseQty("beras 10kg super 5"), { words: ["beras", "super", "5"], n: 10, unit: "kg" });
  assert.deepEqual(LP.parseQty("beras 10kg 26"), { words: ["beras", "26"], n: 10, unit: "kg" });
});

test("parseQty: decimal commas, x stuck to the unit, a stray x", () => {
  assert.deepEqual(LP.parseQty("minyak 1,5 kg"), { words: ["minyak"], n: 1.5, unit: "kg" });   // was 5 kg
  assert.deepEqual(LP.parseQty("beras 10kgx 2"), { words: ["beras"], n: 10, unit: "kg", times: 2 });
  assert.deepEqual(LP.parseQty("susu 1 kotakx2"), { words: ["susu"], n: 1, unit: "pek", times: 2 });
  for (const line of ["x beras 10kg", "beras 10kg x", "beras x 10kg"]) {
    assert.deepEqual(LP.parseQty(line), { words: ["beras"], n: 10, unit: "kg" }, line);
  }
  assert.deepEqual(LP.parseQty("milo x 2 pek"), { words: ["milo"], n: 2, unit: "pek" });   // was "pek" left as a word
});

test("a pack count multiplies the amount and keeps any brand on", () => {
  let r = m("beras 10kg x 2");
  assert.equal(r.code, 1445); assert.equal(r.qty, 2); assert.equal(r.flex, true); assert.deepEqual(r.alt, [1582]);
  for (const line of ["beras 10kg 2 beg", "2 x beras 10kg", "2 beg beras 10kg", "beras 10kg × 2"]) {
    r = m(line);
    assert.equal(r.qty, 2, line); assert.equal(r.flex, true, line);
  }
  r = m("gula 1kg x2");
  assert.equal(r.code, 1589); assert.equal(r.qty, 2);
  r = m("minyak 5kg x 2");
  assert.equal(r.code, 254); assert.equal(r.qty, 2); assert.equal(r.flex, true);
  r = m("minyak masak 5kg x 2");
  assert.equal(r.code, 254); assert.equal(r.qty, 2); assert.equal(r.flex, true);
  r = m("minyak masak 2kg x 3");
  assert.equal(r.code, 1598); assert.equal(r.qty, 3);
  r = m("telur 2 papan x 2");
  assert.equal(r.code, 1109); assert.equal(r.qty, 4);
  r = m("eggs 30 x 2");                         // was 30 trays
  assert.equal(r.code, 1109); assert.equal(r.qty, 2);
  r = m("milo 1kg x 2");                        // two of the 900 g pack, not three
  assert.equal(r.code, 2114); assert.equal(r.qty, 2);
  r = m("maggi 5 x 79g");                       // the pack itself is 5 x 79g
  assert.equal(r.code, 1976); assert.equal(r.qty, 1);
  r = m("100 plus 2 botol 1.5l");
  assert.equal(r.code, 1355); assert.equal(r.qty, 2);
  r = m("2 tin sardin 425g");
  assert.equal(r.code, 1142); assert.equal(r.qty, 2);
  r = m("beras 10kg.");                         // was 10 bags
  assert.equal(r.qty, 1); assert.equal(r.flex, true);
  r = m("minyak - 5kg");
  assert.equal(r.code, 254); assert.equal(r.flex, true);
});

test("fractions give part of a kilo", () => {
  let r = m("bawang putih 1/2 kg");
  assert.equal(r.code, 1564); assert.equal(r.qty, 0.5);
  r = m("1 1/2 kg ayam");
  assert.equal(r.code, 1); assert.equal(r.qty, 1.5);
  r = m("½ kg bawang");
  assert.equal(r.code, 1440); assert.equal(r.qty, 0.5);
  r = m("dua kilo setengah ayam");
  assert.equal(r.code, 1); assert.equal(r.qty, 2.5);
});

test("eggs written as N x M", () => {
  for (const line of ["telur 2 x 30", "2 x 30 telur", "eggs 2 x 30", "telur 30 x 2"]) {
    const r = m(line);                          // two trays (was 50, or 30)
    assert.equal(r.code, 1109, line); assert.equal(r.qty, 2, line); assert.deepEqual(r.want, { amt: 60, base: "pc" }, line);
  }
  const r = m("telur 3 x 10");                  // three packs of 10
  assert.equal(r.code, 118); assert.equal(r.qty, 3);
});

test("quantities are whole packs, or half kilos for items sold by the kilo", () => {
  assert.equal(m("ayam 0.7").qty, 0.5);
  assert.equal(m("ayam 1.3").qty, 1.5);
  assert.equal(m("ayam 1,5 kg").qty, 1.5);      // decimal comma, also when the line skips splitLines
  assert.equal(m("milo 1.5").qty, 2);
  assert.equal(m("100 plus 1.5l x 2").qty, 2);
  assert.equal(m("100plus x2").code, 1355); assert.equal(m("100plus x2").qty, 2);
  // A stray or stuck "x" does not switch off any brand.
  for (const [line, qty] of [["x beras 10kg", 1], ["beras 10kg x", 1], ["beras x 10kg", 1], ["beras 10kgx 2", 2]]) {
    const r = m(line);
    assert.equal(r.code, 1445, line); assert.equal(r.flex, true, line); assert.equal(r.qty, qty, line);
  }
});

// Real item names that have numbers in them, and an item whose unit cannot be read.
const NAMED = new LP.Catalogue([
  [2068, "BERAS CAP JASMINE SUPER 5 SPECIAL (IMPORT)", "10 kg", "BARANGAN BERBUNGKUS", "BERAS"],
  [904, "BERAS SUPER CAP RAMBUTAN 5% (IMPORT)", "10 kg", "BARANGAN BERBUNGKUS", "BERAS"],
  [1582, "BERAS CAP JASMINE (SST5%)", "10 kg", "BARANGAN BERBUNGKUS", "BERAS"],
  [1904, "DUMEX DUGRO 3 (1-3 TAHUN) (PELBAGAI PERISA)", "850g", "SUSU DAN BARANGAN BAYI", "SUSU BAYI"],
  [2001, "DUMEX DUPRO 2 (RUMUSAN SUSULAN)", "850g", "SUSU DAN BARANGAN BAYI", "SUSU BAYI"],
  [2009, "DUTCH LADY 123 (PELBAGAI PERISA)", "850g", "SUSU DAN BARANGAN BAYI", "SUSU BAYI"],
  [2010, "DUTCH LADY 456 (PELBAGAI PERISA)", "850g", "SUSU DAN BARANGAN BAYI", "SUSU BAYI"],
  [2114, "MILO (PAKET)", "900 g", "MINUMAN", "BAHAN-BAHAN MINUMAN"],
  [2019, "NESCAFE 3 IN 1 ORIGINAL (AROMATIC & BALANCED)", "25 x 18g", "MINUMAN", "BAHAN-BAHAN MINUMAN"],
  [929, "NESCAFE CLASSIC (PAKET)", "200 g", "MINUMAN", "BAHAN-BAHAN MINUMAN"],
  [2032, "PETPET GOLD+", "M64, L54, XL42", "SUSU DAN BARANGAN BAYI", "LAMPIN PAKAI BUANG"],
].map(r => ({ code: r[0], item: r[1], unit: r[2], item_group: r[3], item_category: r[4] })), c => ({ 1582: 9, 2010: 5 })[c] || 1);
const nm = s => NAMED.matchLine(s);

test("a number between the name and the amount: part of the name, or the pack count", () => {
  let r = nm("beras super 5 10kg");              // one bag of the Super 5 rice (was 5 bags)
  assert.ok([2068, 904].includes(r.code)); assert.equal(r.qty, 1); assert.ok(!r.alt.includes(1582));
  for (const line of ["beras 5% 10kg", "beras 10kg super 5"]) {
    r = nm(line);
    assert.ok([2068, 904].includes(r.code), line); assert.equal(r.qty, 1, line);
  }
  r = nm("beras 2 10kg");                        // no rice has a 2 in its name: two bags, any brand
  assert.equal(r.qty, 2); assert.equal(r.flex, true); assert.deepEqual(r.want, { amt: 20000, base: "g" });
  r = nm("2 beras super 5 10kg");                // a count at the start, then the name
  assert.ok([2068, 904].includes(r.code)); assert.equal(r.qty, 2);
  r = nm("dugro 3 850g");                        // one tin of Dugro 3 (was 3 tins)
  assert.equal(r.code, 1904); assert.equal(r.qty, 1);
  r = nm("susu bayi dugro 3 850g");
  assert.equal(r.code, 1904); assert.equal(r.qty, 1);
  r = nm("dutch lady 123 850g");                 // was Dutch Lady 456, for 123 g
  assert.equal(r.code, 2009); assert.equal(r.qty, 1); assert.deepEqual(r.want, { amt: 850, base: "g" });
  r = nm("susu bayi 2 tin 850g");                // a pack word makes it a count
  assert.equal(r.qty, 2);
  r = nm("milo 2 1kg");                          // two of the 900 g pack
  assert.equal(r.code, 2114); assert.equal(r.qty, 2);
});

test("'3 in 1' stays a name, and unknown pack sizes give whole packs", () => {
  assert.equal(nm("nescafe 3 in 1").code, 2019);
  assert.equal(nm("nescafe").code, 929);
  let r = nm("nescafe 3in1 x 2");
  assert.equal(r.code, 2019); assert.equal(r.qty, 2);
  r = nm("kopi 3 in 1 2 pek");
  assert.equal(r.code, 2019); assert.equal(r.qty, 2); assert.equal(r.want, null); assert.deepEqual(r.size, { amt: 450, base: "g" });
  r = nm("lampin 1/2");                          // diaper sizes are not a pack size: whole packs (was 0.5)
  assert.equal(r.code, 2032); assert.equal(r.qty, 1); assert.equal(r.size, null); assert.equal(r.want, null);
  r = nm("pampers 2 pek");
  assert.equal(r.code, 2032); assert.equal(r.qty, 2);
});

test("hyphenated names match like the catalogue", () => {
  assert.equal(m("coffee-mate").code, 1635);
  assert.equal(m("coffee-mate 900g").qty, 2);
  assert.equal(m("self-raising flour").code, 1849);   // not plain flour
  assert.equal(m("flour").code, 917);
});

test("results carry the wanted amount and the chosen pack size", () => {
  let r = m("beras 10kg x 2");
  assert.deepEqual(r.want, { amt: 20000, base: "g" }); assert.deepEqual(r.size, { amt: 10000, base: "g" });
  r = m("telur 2 papan x 2");
  assert.deepEqual(r.want, { amt: 120, base: "pc" }); assert.deepEqual(r.size, { amt: 30, base: "pc" });
  r = m("bawang 500g");
  assert.deepEqual(r.want, { amt: 500, base: "g" }); assert.deepEqual(r.size, { amt: 1000, base: "g" });
  r = m("ayam 2 ekor");
  assert.deepEqual(r.want, { amt: 3000, base: "g" });
  r = m("minyak masak 10kg");
  assert.deepEqual(r.want, { amt: 10000, base: "g" }); assert.deepEqual(r.size, { amt: 5000, base: "g" });
  // The 900 g pack stands in for "1kg", so want is what the packs hold.
  r = m("milo 1kg");
  assert.deepEqual(r.want, { amt: 900, base: "g" }); assert.deepEqual(r.size, { amt: 900, base: "g" });
  // "About 1 litre" packs: two 946 ml cartons for 2 litres.
  r = m("susu 2 liter");
  assert.equal(r.code, 2060); assert.equal(r.qty, 2); assert.equal(r.flex, true);
  assert.deepEqual(r.want, { amt: 1892, base: "ml" }); assert.deepEqual(r.size, { amt: 946, base: "ml" });
  // A pack count or no amount at all: nothing to convert.
  r = m("100 plus 2 botol");
  assert.equal(r.want, null); assert.deepEqual(r.size, { amt: 1500, base: "ml" });
  r = m("bawang putih");
  assert.equal(r.want, null); assert.deepEqual(r.size, { amt: 1000, base: "g" });
  r = m("roti gardenia 400g");
  assert.equal(r.status, "none"); assert.equal(r.want, null); assert.equal(r.size, null);
});

test("switching item: packsFor(want, size) redoes the quantity", () => {
  // Same as the reader for the item it chose.
  for (const line of ["beras 10kg", "beras 10kg x 2", "telur 2 papan x 2", "telur 20 biji", "ayam 2 ekor", "chicken 1.5kg",
                      "bawang 500g", "minyak masak 10kg", "milo 1kg", "milo 1kg x 2", "susu 2 liter", "maggi 5 x 79g", "gula 1kg x2"]) {
    const r = m(line);
    assert.equal(LP.packsFor(r.want.amt, r.size), r.qty, line);
    assert.equal(LP.packsFor(r.want, r.size), r.qty, line);
  }
  // A 10 kg request moved to a 5 kg pack becomes 2 packs; 20 kg to 2 kg packs becomes 10.
  assert.equal(LP.packsFor(m("beras 10kg").want.amt, LP.parseUnit("5 kg")), 2);
  assert.equal(LP.packsFor(m("minyak masak 10kg x 2").want.amt, LP.parseUnit("2 kg")), 10);
  assert.equal(LP.packsFor(m("susu 2 liter").want.amt, LP.parseUnit("1 liter")), 2);
  // Different kinds of amount do not convert.
  assert.equal(LP.packsFor(m("telur 2 papan").want, LP.parseUnit("1kg")), null);
});

test("packsFor rounding", () => {
  const kg = { amt: 1000, base: "g" };
  assert.equal(LP.packsFor(500, kg), 0.5);       // per-kg items in 0.5 steps
  assert.equal(LP.packsFor(1200, kg), 1);
  assert.equal(LP.packsFor(1300, kg), 1.5);
  assert.equal(LP.packsFor(100, kg), 0.5);       // at least half a kilo
  const five = { amt: 5000, base: "g" };
  assert.equal(LP.packsFor(10000, five), 2);     // packs as whole numbers
  assert.equal(LP.packsFor(10100, five), 2);     // a little over still 2
  assert.equal(LP.packsFor(11000, five), 3);
  assert.equal(LP.packsFor(100, five), 1);       // at least one pack
  assert.equal(LP.packsFor(60, { amt: 30, base: "pc" }), 2);
  assert.equal(LP.packsFor(1e6, kg), 50);        // at most 50
  assert.equal(LP.packsFor(1e6, { amt: 500, base: "g" }), 50);
  assert.equal(LP.packsFor({ amt: 2000, base: "ml" }, { amt: 1000, base: "ml" }), 2);
  assert.equal(LP.packsFor({ amt: 2000, base: "g" }, { amt: 30, base: "pc" }), null);
  assert.equal(LP.packsFor(1000, null), null);
  assert.equal(LP.packsFor(0, kg), null);
  assert.equal(LP.packsFor(null, kg), null);
});

test("parseUnit reads PriceCatcher units", () => {
  const u = (s, amt, base) => assert.deepEqual(LP.parseUnit(s), { amt, base }, s);
  u("10 kg", 10000, "g");
  u("1kg", 1000, "g");
  u("3.6 KG", 3600, "g");
  u("+-350g", 350, "g");
  u("+- 500g", 500, "g");
  u("26G", 26, "g");
  u("1.5 liter", 1500, "ml");
  u("946ML", 946, "ml");
  u("920 ML", 920, "ml");
  u("5 X 79g", 395, "g");
  u("25 x 18g", 450, "g");
  u("30 biji", 30, "pc");
  u("100 beg", 100, "pc");
  u("1 batang", 1, "pc");
  for (const s of ["M58, L48, XL42", "S80, M60, L58, XL50", "", null, undefined]) assert.equal(LP.parseUnit(s), null, String(s));
});

test("a pasted WhatsApp list with counts and fractions", () => {
  const rs = cat.matchText("Barang dapur:\n- beras 10kg x 2\n- gula 1kg x2, minyak masak 5kg x 2\n• telur 2 papan x 2\n" +
                           "1. bawang putih 1/2 kg RM5\n2) ½ kg bawang\n[ ] 1 1/2 kg ayam");
  assert.deepEqual(rs.map(r => [r.code, r.qty]), [[1445, 2], [1589, 2], [254, 2], [1109, 4], [1564, 0.5], [1440, 0.5], [1, 1.5]]);
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

test("counts and fractions against the real item list", { skip: !fs.existsSync(META) }, () => {
  const meta = JSON.parse(fs.readFileSync(META, "utf8"));
  if (meta.synthetic) return;
  const items = meta.items.map(r => ({ code: r[0], item: r[1], unit: r[2], item_group: r[3], item_category: r[4] }));
  const unitOf = new Map(items.map(it => [it.code, it.unit]));
  const full = new LP.Catalogue(items, c => (c * 7919) % 37);
  const qty = { "beras 10kg x 2": 2, "gula 1kg x2": 2, "minyak 5kg x 2": 2, "beras 10kg 2 beg": 2, "telur 2 papan x 2": 4,
                "bawang putih 1/2 kg": 0.5, "1 1/2 kg ayam": 1.5, "½ kg bawang": 0.5, "minyak masak 5kg x 2": 2,
                "telur 2 x 30": 2, "beras 2 10kg": 2, "milo 2 1kg": 2, "beras super 5 10kg": 1, "beras 5% 10kg": 1,
                "dugro 3 850g": 1, "dutch lady 123 850g": 1, "nescafe 3 in 1 x 2": 2, "100 plus 1.5l x 2": 2 };
  for (const line of Object.keys(qty)) assert.equal(full.matchLine(line).qty, qty[line], line);
  for (const line of ["beras 10kg x 2", "beras 10kg 2 beg", "minyak masak 5kg x 2", "beras 2 10kg"]) assert.equal(full.matchLine(line).flex, true, line);
  // Numbers that are part of a name pick that item.
  const name = line => (items.find(it => it.code === full.matchLine(line).code) || {}).item;
  assert.match(name("beras super 5 10kg"), /SUPER 5|5%/);
  assert.match(name("dugro 3 850g"), /DUGRO 3/);
  assert.match(name("dutch lady 123 850g"), /DUTCH LADY 123/);
  assert.match(name("nescafe 3 in 1"), /NESCAFE 3 IN 1/);
  assert.match(name("100 plus"), /100 PLUS/);
  // Every concept, many amounts: size is the item's own pack, and packsFor(want, size) gives qty back.
  const amounts = ["10kg", "1kg", "500g", "1.5kg", "250 g", "1 liter", "600ml", "2 papan", "20 biji", "2 ekor", "1/2 kg",
                   "10kg x 2", "500g x 2", "2 x 5kg", "1 papan x 2", "5 x 79g", "2 1kg", "2 x 30", "1,5 kg", "x 500g", "2"];
  LP.CONCEPTS.forEach(c => amounts.forEach(a => {
    const line = c.k[0].replace(/^!/, "") + " " + a, r = full.matchLine(line);
    if (r.code == null) return;
    assert.deepEqual(r.size, LP.parseUnit(unitOf.get(r.code)), line);
    if (r.want && r.size && r.want.base === r.size.base) assert.equal(LP.packsFor(r.want, r.size), r.qty, line);
  }));
});
