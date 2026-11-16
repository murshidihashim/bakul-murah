/* Bakul Murah app. Plain JS, no build step. */
(function () {
  "use strict";
  var B = window.Basket, I18N = window.I18N, LP = window.ListParse, NEAR = window.Near;
  var $ = function (id) { return document.getElementById(id); };

  // ---------- storage (per-device conveniences only; never the shopper's location) ----------
  var store = {
    get: function (k, d) { try { var v = localStorage.getItem("bakul." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem("bakul." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  };

  // Saved values are checked when read: an older version, a bug or another page on this site may have left
  // something else there, and a bad value must never stop the page or reach the screen as markup.
  // A radius saved by an older version may be text or a value no longer offered.
  function okRadius(r) { r = Number(r); return [3, 5, 10, 20].indexOf(r) >= 0 ? r : 5; }
  function okCost(c) { c = Number(c); return [0, 2, 5, 10, 20].indexOf(c) >= 0 ? c : 5; }
  function okLang(l) { return l === "en" || l === "ms" ? l : ((navigator.language || "en").toLowerCase().indexOf("ms") === 0 ? "ms" : "en"); }
  function okTheme(x) { return x === "light" || x === "dark" ? x : null; }
  function okSlug(x) { return typeof x === "string" && /^[a-z0-9-]+$/.test(x) ? x : null; }
  function codeList(xs, not) {
    return (Array.isArray(xs) ? xs : []).map(Number).filter(function (c) { return c > 0 && c % 1 === 0 && c !== not; });
  }
  // One basket line: {code, qty, opts?, alt?, any?, own?}, or null when it is not a usable line.
  // own: the item the line had when the shopper switched 'Any brand OK' on (see toggleFlex), the line's or an option.
  function okLine(b) {
    if (!b || typeof b !== "object") return null;
    var code = Number(b.code); if (!(code > 0 && code % 1 === 0)) return null;
    var qty = Number(b.qty), line = { code: code, qty: qty > 0 && qty <= 50 ? qty : 1 };
    var opts = codeList(b.opts, code), alt = codeList(b.alt, code);
    if (!opts.length) opts = alt;
    if (opts.length) {
      line.opts = opts.slice(0, 30);
      if (b.any === "brand") line.any = "brand";
      if (alt.length) line.alt = alt.filter(function (c) { return line.opts.indexOf(c) >= 0; });
      if (line.alt && !line.alt.length) delete line.alt;
      var own = Number(b.own);
      if (line.alt && line.any === "brand" && (own === code || line.opts.indexOf(own) >= 0)) line.own = own;
    }
    return line;
  }
  // A list is never cut when it is read back: a long list the app saved must come back whole.
  function okBasket(v) {
    var out = [], seen = {};
    (Array.isArray(v) ? v : []).forEach(function (b) {
      var l = okLine(b);
      if (l && !seen[l.code]) { seen[l.code] = true; out.push(l); }
    });
    return out;
  }
  function okLists(v) {
    return (Array.isArray(v) ? v : []).filter(function (l) { return l && typeof l === "object"; }).map(function (l) {
      return { name: typeof l.name === "string" ? l.name.slice(0, 80) : "", at: typeof l.at === "string" ? l.at.slice(0, 10) : "",
               basket: okBasket(l.basket) };
    }).filter(function (l) { return l.basket.length; }).slice(0, 20);
  }

  var S = {
    lang: okLang(store.get("lang", null)),
    theme: okTheme(store.get("theme", null)),
    slug: okSlug(store.get("slug", null)),
    basket: okBasket(store.get("basket", [])),
    tab: "basket", dir: "rises", scope: "district", showAll: false,
    stopCost: okCost(store.get("stopCost", 5)), planK: null, planOpen: null, avgOpen: false, lists: okLists(store.get("lists", [])),
    // "Near me": the mode and radius are remembered; the location itself stays in memory only.
    // mode is null on a first visit and is set once the data has loaded ("near" when shop locations exist).
    mode: store.get("mode", null), radius: okRadius(store.get("radius", 5)), near: null, nearMsg: [], nearLoading: null, locTok: 0,
    meta: null, items: new Map(), d: null, idx: null, dlgCode: null, cat: null, matches: [], shared: null,
    seq: 0, retry: null, errKey: null, undo: null, toastBack: null, resultsInView: false
  };

  // ---------- helpers ----------
  function t(key, vars) {
    var s = (I18N[S.lang] && I18N[S.lang][key]) || I18N.en[key] || key;
    if (vars) Object.keys(vars).forEach(function (k) { s = s.split("{" + k + "}").join(vars[k]); });
    return s;
  }
  // English needs a singular for 1 ("1 shop"): the key with "1" added holds it (BM keeps the same words).
  function tn(key, n, vars) { return t(Number(n) === 1 && I18N.en[key + "1"] ? key + "1" : key, vars); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function rm(v) { return v == null ? "-" : v.toFixed(2); }
  var MONTHS = {
    en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    ms: ["Jan", "Feb", "Mac", "Apr", "Mei", "Jun", "Jul", "Ogo", "Sep", "Okt", "Nov", "Dis"]
  };
  function monLabel(ym, long) {
    var y = ym.slice(0, 4), m = Number(ym.slice(5, 7));
    return MONTHS[S.lang][m - 1] + " " + (long ? y : y.slice(2));
  }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  // "2026-11-05" -> "5 Nov 2026"
  function fmtDate(iso) {
    if (!iso || iso.length < 10) return iso || "";
    return Number(iso.slice(8, 10)) + " " + MONTHS[S.lang][Number(iso.slice(5, 7)) - 1] + " " + iso.slice(0, 4);
  }
  function todayISO() { var n = new Date(); return n.getFullYear() + "-" + pad2(n.getMonth() + 1) + "-" + pad2(n.getDate()); }
  function daysSince(iso) { return Math.round((Date.parse(todayISO()) - Date.parse(iso)) / 864e5); }
  function fmtQty(q) { return String(Math.round(q * 100) / 100); }
  function fmtAmt(amt, base) {
    if (base === "g") return amt >= 1000 ? fmtQty(amt / 1000) + " kg" : fmtQty(amt) + " g";
    if (base === "ml") return amt >= 1000 ? fmtQty(amt / 1000) + " l" : fmtQty(amt) + " ml";
    return fmtQty(amt) + " " + t("pcs");
  }
  function isPerKg(code) { var it = S.items.get(code); return !!it && /^1\s*kg$/i.test(it.unit.trim()); }
  // Amounts: per-kg fresh items move in 0.5 kg steps, everything else in whole packs.
  function normQty(code, q) {
    q = Number(q) || 1;
    return isPerKg(code) ? Math.max(0.5, Math.min(50, Math.round(q * 2) / 2)) : Math.max(1, Math.min(50, Math.round(q)));
  }
  function lineOf(code) { return S.basket.filter(function (b) { return b.code === code; })[0]; }
  function saveBasket() { store.set("basket", S.basket); }
  // Checked lines (okBasket) for items that exist in this data, with amounts in the steps the screen uses.
  function knownLines(lines) {
    return lines.filter(function (b) { return S.items.has(b.code); }).map(function (b) {
      b.qty = normQty(b.code, b.qty);
      if (b.opts) {
        b.opts = b.opts.filter(function (c) { return S.items.has(c); });
        if (b.alt) b.alt = b.alt.filter(function (c) { return S.items.has(c); });
        if (!b.opts.length) { delete b.opts; delete b.alt; delete b.any; }
        else if (b.alt && !b.alt.length) delete b.alt;
      }
      if (b.own != null && !(b.alt && (b.own === b.code || b.opts.indexOf(b.own) >= 0))) delete b.own;
      return b;
    });
  }
  function titleCase(s) {
    // Data is in capitals. Soften for reading, keep short words like "A" and units intact.
    return String(s || "").toLowerCase().replace(/(^|[\s(\/-])([a-z])/g, function (_, a, b) { return a + b.toUpperCase(); });
  }
  // Item names sometimes repeat the unit ("LOBAK PUTIH (1KG)" next to a "1kg" tag): drop the repeat.
  function cleanItemName(it) {
    var n = it.item, u = String(it.unit || "").replace(/\s+/g, "").toLowerCase();
    var m = n.match(/\s*\(([^()]*)\)\s*$/);
    if (m && m[1].replace(/\s+/g, "").toLowerCase() === u) n = n.slice(0, m.index);
    return n;
  }
  function itemTitle(code) { var it = S.items.get(code); return it ? titleCase(cleanItemName(it)) : "#" + code; }
  // Shop names are in capitals too; keep brand acronyms (AEON, TF, KK...) and anything with digits.
  var KEEP_CAPS = /^(AEON|BIG|TF|KK|HM|NSK|ST|CS|SDN|BHD|KFC|UTC|PJ|KL|JB|TTDI|USJ\d*|SS\d+)$/;
  function titleShop(name) {
    return String(name || "").split(/(\s+|[()\/,&.-])/).map(function (w) {
      if (!/[A-Za-z]/.test(w)) return w;
      if (KEEP_CAPS.test(w.toUpperCase()) || /\d/.test(w) || !/[AEIOU]/i.test(w)) return w.toUpperCase();
      return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
    }).join("");
  }
  var TYPES = { "Hypermarket": "typeHyper", "Pasar Raya / Supermarket": "typeSuper", "Pasar Mini": "typeMini",
                "Kedai Runcit": "typeRuncit", "Pasar Basah": "typeWet" };
  function typeLabel(x) { return TYPES[x] ? t(TYPES[x]) : x; }
  function premise(p) {
    var r = S.d.premises[p];
    return { code: r[0], name: titleShop(r[1]), type: typeLabel(r[2]), addr: r[3], prec: r[6], km: r[7], district: r[8] };
  }
  function areaPhrase() { return S.d && S.d.near ? t("nearYou", { r: S.d.near.radius }) : t("inDistrict", { d: S.d ? S.d.district : "" }); }
  // Distance for a shop in "near me" mode: exact ("1.2 km"), approximate ("~2 km") or unknown.
  // Exact only from a GPS position to a shop placed by name or address: a postcode is an area, not a point.
  function distKind(p) {
    var x = premise(p);
    if (x.km == null || x.prec === "district") return "unknown";
    return x.prec === "postcode" || x.prec === "town" || (S.d.near && S.d.near.fromPostcode) ? "approx" : "exact";
  }
  function distText(p) {
    if (!S.d || !S.d.near) return "";
    var x = premise(p), k = distKind(p);
    if (k === "unknown") return t("locUnknown", { d: x.district || S.d.district });
    if (k === "approx") return t("kmApprox", { k: Math.max(1, Math.round(x.km)) });
    return t("kmAway", { k: x.km.toFixed(1) });
  }
  // The same as markup. Screen readers may skip the "~", so an approximate distance also says "about" in words.
  function distHtml(p) {
    var s = distText(p); if (!s) return "";
    if (distKind(p) !== "approx") return esc(s);
    return '<span aria-hidden="true">' + esc(s) + '</span><span class="sr-only">' +
      esc(t("kmApproxSr", { k: Math.max(1, Math.round(premise(p).km)) })) + "</span>";
  }
  function distTag(p) {
    var s = distHtml(p); if (!s) return "";
    var k = distKind(p);
    return '<span class="dist' + (k === "exact" ? "" : " " + k) + '">' + s + "</span>";
  }
  // days_old in the data counts back from the data date; the shopper needs the age as of today.
  function dataDate() { return (S.d && S.d.data_date) || (S.meta && S.meta.data_date) || null; }
  function ageOf(daysOld) { return B.ageToday ? B.ageToday(daysOld, dataDate(), todayISO()) : daysOld; }
  function freshTag(maxDays) {
    var a = ageOf(maxDays);
    if (a <= 7) return '<span class="fresh">' + esc(t("freshWeek")) + "</span>";
    return '<span class="fresh' + (a > 14 ? " old" : "") + '">' + esc(t("freshDays", { d: a })) + "</span>";
  }
  // ---------- keyboard focus ----------
  // Re-rendering replaces markup, which drops keyboard focus. The focused control is remembered by a key (its id,
  // or its data-* keys inside the nearest element with an id) and the same control is focused again afterwards.
  var KEY_ATTRS = ["qty", "d", "rm", "flex", "swap", "hist", "k", "add", "load", "del", "mi", "mq", "mf"];
  function focusKey(el) {
    el = el || document.activeElement;
    if (!el || el === document.body || !el.getAttribute) return null;
    if (el.id) return "#" + el.id;
    if (el.tagName === "SUMMARY" && el.parentNode && el.parentNode.id) return "#" + el.parentNode.id + " > summary";
    var sel = "";
    KEY_ATTRS.forEach(function (k) {
      var v = el.getAttribute("data-" + k);
      if (v != null) sel += "[data-" + k + '="' + String(v).replace(/["\\]/g, "\\$&") + '"]';
    });
    if (!sel) return null;
    var box = el.parentNode && el.parentNode.closest ? el.parentNode.closest("[id]") : null;
    return (box ? "#" + box.id + " " : "") + sel;
  }
  function shown(el) { return !!el && document.contains(el) && el.getClientRects().length > 0; }
  function focusEl(el, noScroll) {
    if (!shown(el) || el.disabled) return false;
    if (noScroll) { try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); } } else el.focus();
    return document.activeElement === el;
  }
  function focusLost() { var a = document.activeElement; return !a || a === document.body || !shown(a); }
  // The control is rebuilt where it was, so it is focused again without scrolling: a tap near the top or bottom of
  // a phone screen must not move the page under the shopper's finger.
  function refocus(key) { if (key && focusLost()) { try { focusEl(document.querySelector(key), true); } catch (e) { /* not a valid key */ } } }
  // The page's own scroll jumps follow the shopper's reduced-motion setting.
  function smooth() { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"; }

  // ---------- toast ----------
  // A modal dialog makes the rest of the page inert, so while one is open the toast is shown inside it.
  function placeToast() {
    var el = $("toast"), host = document.body;
    document.querySelectorAll("dialog").forEach(function (d) { if (d.open) host = d; });
    if (el.parentNode !== host) host.appendChild(el);
  }
  // A toast with Undo stays while the pointer is over it or keyboard focus is in it.
  var TOAST = { t: null, ms: 0, hover: false, focus: false };
  function toastTimer() {
    clearTimeout(TOAST.t);
    if (!TOAST.hover && !TOAST.focus && $("toast").classList.contains("on")) TOAST.t = setTimeout(function () { hideToast(); }, TOAST.ms);
  }
  // used: the shopper tapped Undo (else the Undo offered, if any, has gone unused: see undoGone).
  function hideToast(keepFocus, used) {
    var el = $("toast"), btn = $("toastAct"), had = el.contains(document.activeElement), u = S.undo;
    clearTimeout(TOAST.t);
    el.classList.remove("on");
    btn.hidden = true; S.undo = null;
    var back = S.toastBack; S.toastBack = null; TOAST.focus = false; TOAST.hover = false;
    // Never leave keyboard focus on a button that is no longer shown.
    if (had && !keepFocus && back) focusEl(back());
    if (!used) undoGone(u);
  }
  /** msg; optional action (label, fn); back: function giving the place for focus when the shopper leaves Undo. */
  function toast(msg, actLabel, actFn, back) {
    var el = $("toast"), btn = $("toastAct"), u = S.undo;
    placeToast();
    $("toastMsg").textContent = msg;
    btn.hidden = !actFn; btn.textContent = actLabel || "";
    S.undo = actFn || null; S.toastBack = actFn ? (back || null) : null;
    el.classList.add("on");
    TOAST.ms = actFn ? 10000 : 1800;
    toastTimer();
    undoGone(u);   // an Undo still offered is replaced by this message
  }
  // After an action that offers Undo: keyboard users are taken to Undo (Tab or Esc there goes back);
  // everyone else gets focus at the next sensible place straight away, without scrolling the page.
  function afterUndoable(byKeyboard, back) {
    if (byKeyboard && focusEl($("toastAct"))) return;
    focusEl(back(), !byKeyboard);
  }
  // A click made with the keyboard (Enter or Space) has no click count.
  function byKey(e) { return !!e && e.detail === 0; }
  // The error box keeps its label key, so a switch of language translates it too.
  function showError(key, retryFn) {
    S.retry = retryFn || null; S.errKey = key;
    $("errMsg").textContent = t(key);
    $("errRetry").hidden = !retryFn;
    $("errBox").hidden = false;
  }
  function hideError() { $("errBox").hidden = true; S.retry = null; S.errKey = null; }

  function loadData(key) {
    return new Promise(function (resolve, reject) {
      window.PCDATA = window.PCDATA || {};
      if (window.PCDATA[key]) return resolve(window.PCDATA[key]);
      var s = document.createElement("script");
      s.src = "data/" + key + ".js";
      s.onload = function () { window.PCDATA[key] ? resolve(window.PCDATA[key]) : reject(new Error(key)); };
      s.onerror = function () { s.remove(); reject(new Error("load " + key)); };
      document.head.appendChild(s);
    });
  }

  // ---------- theme & language ----------
  function applyTheme() {
    var root = document.documentElement;
    if (S.theme) root.setAttribute("data-theme", S.theme); else root.removeAttribute("data-theme");
    // The button is a switch for dark mode: it says whether dark mode is on (set here or by the phone).
    $("themeBtn").setAttribute("aria-pressed", String(!!isDark()));
  }
  function isDark() {
    if (S.theme) return S.theme === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function applyLang() {
    document.documentElement.lang = S.lang;
    document.querySelectorAll("[data-i18n]").forEach(function (n) { n.textContent = t(n.getAttribute("data-i18n")); });
    document.querySelectorAll("[data-i18n-ph]").forEach(function (n) { n.placeholder = t(n.getAttribute("data-i18n-ph")); });
    document.querySelectorAll("[data-i18n-aria]").forEach(function (n) { n.setAttribute("aria-label", t(n.getAttribute("data-i18n-aria"))); });
    document.querySelectorAll("[data-lang]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.lang === S.lang)); });
    if (S.errKey) $("errMsg").textContent = t(S.errKey);
  }

  // ---------- where: district ----------
  function fillStates() {
    var sel = $("stateSel"), areas = S.meta.areas, found = "";
    Object.keys(areas).forEach(function (st) { areas[st].forEach(function (a) { if (a.slug === S.slug) found = st; }); });
    sel.innerHTML = '<option value="">' + esc(t("statePh")) + "</option>" +
      Object.keys(areas).map(function (st) { return '<option value="' + esc(st) + '">' + esc(st) + "</option>"; }).join("");
    sel.value = found;
    fillDistrictOptions();
  }
  // Returns the district to load for the chosen state ("" when no state is chosen yet).
  function fillDistrictOptions() {
    var st = $("stateSel").value, list = st ? (S.meta.areas[st] || []) : [], sel = $("districtSel");
    if (!list.length) {
      sel.innerHTML = '<option value="">' + esc(t("districtPh")) + "</option>";
      sel.disabled = true;
      return "";
    }
    sel.disabled = false;
    sel.innerHTML = list.map(function (a) { return '<option value="' + esc(a.slug) + '">' + esc(a.district) + "</option>"; }).join("");
    var pick = list.some(function (a) { return a.slug === S.slug; }) ? S.slug
      : list.slice().sort(function (a, b) { return b.premises - a.premises; })[0].slug;
    sel.value = pick;
    return pick;
  }
  function knownSlug(slug) {
    var ok = false;
    if (!S.meta || !slug) return false;
    Object.keys(S.meta.areas).forEach(function (st) { S.meta.areas[st].forEach(function (a) { if (a.slug === slug) ok = true; }); });
    return ok;
  }
  function clearArea() { S.d = null; S.idx = null; S.showAll = false; }
  function selectDistrict(slug) {
    if (!slug) { ++S.seq; clearArea(); renderAll(); return Promise.resolve(); }
    S.slug = slug; S.showAll = false; S.planK = null;
    if (S.mode === "near") { if (S.near) loadNear(); return Promise.resolve(); }
    var seq = ++S.seq;
    $("whereNote").textContent = t("loadingPrices");
    return loadData("d/" + slug).then(function (d) {
      if (seq !== S.seq) return;            // a newer choice was made meanwhile
      hideError();
      S.d = d; S.idx = B.indexDistrict(d); store.set("slug", slug);   // remembered once it has loaded
      renderAll();
    }).catch(function (e) {
      if (seq !== S.seq) return;
      console.error(e);
      // Never leave the previous district's results under the new district's name.
      clearArea(); renderAll();
      showError("loadFail", function () { selectDistrict(slug); });
    });
  }

  // ---------- search ----------
  function norm(s) { return s.toLowerCase().normalize("NFKD").replace(/[^\w\s.%]/g, " "); }
  // Older data (v1) has no national shop counts per item.
  function hasShopCounts() { return !!(S.meta && (S.meta.item_cols || []).indexOf("shops") >= 0); }
  // The prices of one item in the area shown: {m: Map(shop index -> {p, d}), home: null}, or null when none.
  // Near me: only shops with a known place, as in the headline (never counted inside the radius). When only shops
  // whose place is unknown price the item, theirs, with home set to their district, so the screen names it.
  function itemPrices(code) {
    var m = S.idx && S.idx.get(code); if (!m || !m.size) return null;
    if (!S.d || !S.d.near) return { m: m, home: null };
    var loc = new Map();
    m.forEach(function (v, p) { if (!unlocated(p)) loc.set(p, v); });
    return loc.size ? { m: loc, home: null } : { m: m, home: S.d.near.home || S.d.district };
  }
  // How widely an item is priced: shops in the chosen area, or in Malaysia before an area is chosen.
  // Without national shop counts, the months with a national price stand in, so the search and the list
  // reader still prefer items that are priced regularly (never shown as a number of shops).
  function coverage(code) {
    if (S.idx) { var ip = itemPrices(code); return ip ? ip.m.size : 0; }
    var it = S.items.get(code); if (!it) return 0;
    if (hasShopCounts()) return it.shops || 0;
    var h = S.meta && S.meta.national_history && S.meta.national_history[code], n = 0;
    if (h) h.forEach(function (v) { if (v != null) n++; });
    return n;
  }
  // How widely an item is priced, for ranking: the search, the list reader, the name of an any-brand line and the
  // pack tips. Near me: shops inside the radius always come first, so an item priced there beats one priced only by
  // shops whose place is unknown (outside the radius); those only break ties. Elsewhere the same as coverage().
  function reach(code) {
    if (!S.idx || !S.d || !S.d.near) return coverage(code);
    var m = S.idx.get(code), loc = 0, unk = 0;
    if (m) m.forEach(function (v, p) { if (unlocated(p)) unk++; else loc++; });
    return loc + unk / (unk + 1);
  }
  function areaMedian(code) {
    var ip = itemPrices(code); if (!ip) return null;
    var xs = []; ip.m.forEach(function (v) { xs.push(v.p); }); return B.median(xs);
  }
  // Where the item's figures come from, when that is not the area shown: the home district (Near me), else null.
  function itemHome(code) { var ip = itemPrices(code); return ip ? ip.home : null; }
  function nationalLatest(code) {
    var h = S.meta && S.meta.national_history && S.meta.national_history[code], v = null;
    if (h) h.forEach(function (x) { if (x != null) v = x; });
    return v;
  }
  // Typical price shown next to an item, with the place it applies to.
  function typicalText(code) {
    if (!S.d) { var n = nationalLatest(code); return n == null ? "" : t("typicalMy", { p: rm(n) }); }
    var med = areaMedian(code), home = itemHome(code);
    if (med == null) return t("notHere");
    return tn(home ? "lineInfoIn" : "lineInfo", coverage(code), { p: rm(med), n: coverage(code), d: home });
  }
  // The same for a list line. A line whose own item is not priced here, while a brand (or grade or size) it accepts
  // is priced by a shop the headline and the totals count (claimPriced), says that the totals count those: they price
  // the line at its cheapest accepted item in each shop. Only then: with nothing accepted priced by such a shop, the
  // line is not counted, and says only 'not priced here'.
  function lineText(b) {
    var s = typicalText(b.code);
    if (!S.d || areaMedian(b.code) != null || !(b.alt && b.alt.length)) return s;
    var counted = b.alt.some(function (c) { return claimPriced(c); });
    return counted ? t(b.any === "brand" ? "notHereBrands" : "notHereGrades") : s;
  }
  // Whether a shop that the headline and the totals count (claimRows) prices an item: any shop of a district. Near me:
  // a shop with a known place (inside the radius); a shop whose place is unknown only when no shop with a known place
  // prices anything on the list (the headline then counts the home district's shops, and names that district).
  function claimPriced(code) {
    if (!coverage(code)) return false;
    if (pricedInPlace(code)) return true;
    return !S.basket.some(function (x) { return B.lineCodes(x).some(function (c) { return pricedInPlace(c); }); });
  }
  // A shop of the place shown prices the item: in Near me one with a known place, else any shop of the district.
  function pricedInPlace(code) {
    var m = S.idx && S.idx.get(code), yes = false;
    if (m) m.forEach(function (v, p) { if (!unlocated(p)) yes = true; });
    return yes;
  }
  function search(q) {
    var toks = norm(q).split(/\s+/).filter(Boolean);
    if (!toks.length) return [];
    var out = [];
    S.items.forEach(function (it) {
      var hay = it._hay, ok = true;
      for (var i = 0; i < toks.length; i++) if (hay.indexOf(toks[i]) < 0) { ok = false; break; }
      if (!ok) return;
      var score = reach(it.code) * 2 + (hay.indexOf(toks[0]) === 0 ? 50 : 0);
      out.push({ it: it, score: score });
    });
    out.sort(function (a, b) { return b.score - a.score || a.it.item.localeCompare(b.it.item); });
    return out.slice(0, 8).map(function (r) { return r.it; });
  }
  var CHECK_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  function renderResults() {
    var q = $("searchIn").value, ul = $("results");
    var res = search(q);
    ul.innerHTML = res.map(function (it) {
      var cov = coverage(it.code), inB = inBasket(it.code);
      var side;
      if (S.d) {
        var home = itemHome(it.code);
        side = cov ? "RM " + rm(areaMedian(it.code)) + "<small>" + esc(tn(home ? "shopsHaveIn" : "shopsHave", cov, { n: cov, d: home })) + "</small>"
                   : "<small>" + t("notHere") + "</small>";
      }
      else side = nationalLatest(it.code) != null ? "RM " + rm(nationalLatest(it.code)) + "<small>" +
        (hasShopCounts() ? tn("shopsMy", cov, { n: cov }) : t("typicalMyShort")) + "</small>" : "";
      // A plain list of buttons; an item already on the list says so in words as well as with the tick.
      return '<li><button type="button" class="res' + (S.d && !cov ? " dim" : "") + (inB ? " in" : "") + '" data-add="' + it.code + '">' +
        '<span class="res-main"><span class="res-name">' + esc(titleCase(cleanItemName(it))) + "</span>" +
        '<span class="res-sub"><span class="unit">' + esc(it.unit) + "</span> · " + esc(titleCase(it.item_category || it.item_group)) + "</span></span>" +
        '<span class="res-side">' + side + "</span>" + (inB ? '<span class="sr-only">' + esc(t("inList")) + "</span>" : "") +
        '<span class="plus" aria-hidden="true">' + (inB ? CHECK_SVG : "+") + "</span></button></li>";
    }).join("");
    ul.hidden = !res.length;
    return res.length;
  }

  // ---------- basket ----------
  function inBasket(code) { return S.basket.some(function (b) { return b.code === code; }); }
  /** Add an item. o: {qty, opts: [alternative codes], flex: bool, brand: bool}. Adds to the amount if already listed. */
  function addItem(code, quiet, o) {
    o = o || {};
    var ex = lineOf(code);
    if (ex) {
      if (!o.qty) return false;
      ex.qty = normQty(code, ex.qty + o.qty); saveBasket(); return true;
    }
    var line = { code: code, qty: normQty(code, o.qty || 1) };
    if (o.opts && o.opts.length) {
      line.opts = o.opts.slice(0, 30);
      if (o.brand) line.any = "brand";
      if (o.flex) line.alt = line.opts.slice();
    }
    S.basket.push(line); saveBasket();
    if (!quiet) toast(t("added", { x: itemTitle(code) }));
    return true;
  }
  function removeItem(code) {
    S.basket = S.basket.filter(function (b) { return b.code !== code; }); saveBasket();
  }
  // The control of one kind (data-rm, data-hist...) in the row now at position i, or the last row; the list heading
  // when the list is empty.
  function rowControl(i, kind) {
    var els = document.querySelectorAll("#basketList [data-" + kind + "]");
    return els.length ? els[Math.min(i, els.length - 1)] : $("listH");
  }
  function removeWithUndo(code, byKeyboard) {
    var at = -1, line = null;
    S.basket.forEach(function (b, i) { if (b.code === code) { at = i; line = b; } });
    if (!line) return;
    var back = function () { return rowControl(at, "rm"); };
    // Undo puts the same line back (its amount and switches) where it was. While Undo is offered, no other line is
    // renamed to its item (holds, see followArea), so it can always come back. The line may follow a place chosen
    // meanwhile, so it is found again by its code after the render.
    var undo = function () {
      if (inBasket(code)) return;
      S.basket.splice(Math.min(at, S.basket.length), 0, line); saveBasket(); renderAll();
      focusEl(document.querySelector('#basketList [data-rm="' + line.code + '"]'));
    };
    undo.holds = code;
    removeItem(code);
    toast(t("removed", { x: itemTitle(code) }), t("undo"), undo, back);   // before the render, so the hold applies to it
    renderAll();
    afterUndoable(byKeyboard, back);
  }
  function setQty(code, q) {
    S.basket.forEach(function (b) { if (b.code === code) b.qty = normQty(code, q); });
    saveBasket();
  }
  // 'Any brand OK' on: the line keeps the item it has (own), though it may then be named after a brand priced here
  // (followArea). Off: it goes back to that item, unless another line has it now or Undo can still bring back a line
  // with it. Keyboard focus follows the line (followedKey); renderAll must follow.
  function toggleFlex(code) {
    var b = lineOf(code); if (!b || !b.opts) return;
    if (b.alt && b.alt.length) {
      var own = b.own;
      delete b.alt; delete b.own;
      if (own != null && own !== b.code && b.opts.indexOf(own) >= 0 && !lineOf(own) && !(S.undo && S.undo.holds === own)) renameLine(b, own);
    } else { b.alt = b.opts.slice(); if (b.any === "brand") b.own = b.code; }
    saveBasket();
  }

  // Cheaper pack of the same product in this area (e.g. 5 kg vs 2 kg of the same oil), by typical price per kg/l.
  // Near me: both typical prices come from the same shops' area (inside the radius, or both from the home district
  // when only shops whose place is unknown price them), never one from each.
  function cheaperPack(b) {
    if (!S.idx || (b.alt && b.alt.length)) return null;
    var it = S.items.get(b.code), u = B.parseUnit(it.unit), med = areaMedian(b.code), home = itemHome(b.code);
    if (!u || med == null || u.base === "pc") return null;
    var mine = B.unitPrice(med, u), best = null;
    S.items.forEach(function (o) {
      if (o.code === b.code || o.item !== it.item || coverage(o.code) < 2 || itemHome(o.code) !== home) return;
      var ou = B.parseUnit(o.unit); if (!ou || ou.base !== u.base) return;
      var up = B.unitPrice(areaMedian(o.code), ou);
      if (up && up.v < mine.v * 0.97 && (!best || up.v < best.up.v)) best = { o: o, ou: ou, up: up };
    });
    return best ? { item: best.o, size: best.ou, per: best.up, mine: mine, u: u } : null;
  }
  function unitLabel(per) { return per === "kg" ? "kg" : per === "l" ? (S.lang === "ms" ? "liter" : "litre") : (S.lang === "ms" ? "biji" : "piece"); }
  // A typical weekly list, read by the same matcher as a pasted list.
  var STARTER = "ayam 1kg, telur 1 papan, beras 10kg, gula 1kg, minyak masak 5kg, bawang besar 1kg, " +
                "susu pekat, sardin, kangkung 1kg, ikan kembung 1kg";
  function addStarter(e) {
    var added = 0;
    S.cat.matchText(STARTER).forEach(function (m) {
      if (m.code != null && addItem(m.code, true, { qty: m.qty, opts: m.alt, flex: m.flex, brand: m.flex })) added++;
    });
    renderAll();
    toast(tn("addedN", added, { n: added }));
    focusEl($("listH"), !byKey(e));   // the starter button has gone with the empty list
  }

  // A line with "any brand" on accepts every brand in its options, so it is named after the brand that most shops
  // in the area shown price (reach: in Near me, shops inside the radius first), as the list reader names it when it
  // reads a list there. Older data (v1) has no national shop counts, so a list read before a place is chosen may start
  // with a brand that few shops price. Only the name changes: the brands accepted, and so every total, stay the same.
  // A line follows the place once: when a new place is shown, or when the line first appears in it (a loaded or
  // shared list). Never in a render that the shopper's own change causes (the 'Any brand' switch, a removal and its
  // Undo): the line they are working on keeps its name, its controls and keyboard focus, and the brand they had.
  // Except that a line with any brand on never reads 'not priced here' while a brand it accepts, free to take, is
  // priced here (every total counts that brand): its name follows then too, at any render, as after a tick of its
  // switch or once another line has given up that brand (undoGone). The switch keeps keyboard focus (followedKey),
  // and switching it off gives back the brand the shopper had (toggleFlex).
  // Never behind an open price-history dialog that shows the line's item and says it is in the list (a tap there
  // must not add a second line for it): that line waits, and is renamed once the dialog has closed.
  // The lines already followed are kept by identity for the place shown (S.idx), in memory only. dlg: a line is left
  // to rename when the price-history dialog closes (undoGone, followArea).
  var FOLLOW = { idx: null, seen: null, renamed: {}, dlg: false };
  function sizeKey(code) { var it = S.items.get(code); return it ? String(it.unit || "").replace(/\s+/g, "").toLowerCase() : ""; }
  // The item to name a line with any brand on after here: of the brands it accepts in the same size, the one most
  // shops price. Never the item of another line, or of a removed line that Undo can still bring back (held): each
  // line keeps its own code. null when the line's own item is as good.
  function followTo(b, held) {
    var mains = S.basket.filter(function (x) { return x !== b; }).map(function (x) { return x.code; });
    if (held) mains.push(held);
    var size = sizeKey(b.code), cands = [b.code].concat(b.alt).filter(function (c) {
      return S.items.has(c) && sizeKey(c) === size && (c === b.code || mains.indexOf(c) < 0);
    });
    var top = Math.max.apply(null, cands.map(reach));
    if (reach(b.code) >= top) return null;
    return cands.filter(function (c) { return reach(c) === top; })
      .sort(function (x, y) { return S.items.get(x).item.localeCompare(S.items.get(y).item); })[0];
  }
  // Name a line after another item it accepts (its own item becomes an option). A control of the line that has
  // keyboard focus is found again under the new code after the render (followedKey).
  function renameLine(b, to) {
    var old = b.code, swap = function (c) { return c === to ? old : c; };
    b.code = to;
    if (b.alt) b.alt = b.alt.map(swap);
    b.opts = (b.opts || []).indexOf(to) >= 0 ? b.opts.map(swap) : (b.opts || []).concat([old]);
    FOLLOW.renamed[old] = to;
  }
  // A line with any brand on whose own item is not priced here, and which can be named after a brand that is.
  function staleName(b, held) {
    return b.any === "brand" && !!b.alt && b.alt.length > 0 && reach(b.code) === 0 && followTo(b, held) != null;
  }
  // The item a render now would name a line after (followArea), or null: a line with any brand on, the first time it
  // is drawn for the place shown, or when its name is stale.
  function followNow(b, held) {
    if (b.any !== "brand" || !b.alt || !b.alt.length) return null;
    var first = FOLLOW.idx !== S.idx || !FOLLOW.seen || !FOLLOW.seen.has(b);
    return first || staleName(b, held) ? followTo(b, held) : null;
  }
  // The open price-history dialog shows this item (and says whether it is in the list).
  function dlgShows(code) { return $("itemDlg").open && S.dlgCode === code; }
  function followArea() {
    if (!S.idx) return false;
    if (FOLLOW.idx !== S.idx) { FOLLOW.idx = S.idx; FOLLOW.seen = new WeakSet(); }
    // A removed line that Undo can still bring back keeps its item: no other line takes it meanwhile.
    var changed = false, held = S.undo && S.undo.holds;
    S.basket.forEach(function (b) {
      var to = followNow(b, held);
      // The line of the item the open price history shows waits: not yet followed, it is renamed when the dialog
      // has closed (afterDlgClose). Other lines follow at once.
      if (to != null && dlgShows(b.code)) { FOLLOW.dlg = true; return; }
      FOLLOW.seen.add(b);
      if (to == null) return;
      renameLine(b, to);
      changed = true;
    });
    return changed;
  }
  // A removal's Undo has gone unused (it timed out, was closed, or another message took its place): the removed
  // line's item is free again. A line kept from taking it that now reads 'not priced here' is renamed: draw again.
  // Not behind an open price-history dialog, which may show that line's item and say it is in the list: the line is
  // renamed once the dialog has closed (afterDlgClose).
  function undoGone(u) {
    if (!u || u.holds == null || !S.idx) return;
    if ($("itemDlg").open) { FOLLOW.dlg = true; return; }
    renameStale();
  }
  // Draw again when a line would be renamed now: one whose name is stale, or one that waited for the price history to
  // close before following the place shown.
  function renameStale() {
    if (!S.idx) return;
    var held = S.undo && S.undo.holds;
    if (S.basket.some(function (b) { return followNow(b, held) != null; })) renderAll();
  }
  // A focus key (focusKey) for a control of a list line renamed in this render, with the line's new item code.
  // Only keys in the list: a price-changes row of the same item is not a control of the line.
  function followedKey(key) {
    if (!key || key.indexOf("#basketList ") !== 0) return key;
    return key.replace(/(\[data-(?:qty|rm|flex|hist)=")(\d+)"/g, function (all, a, c) {
      return FOLLOW.renamed[c] != null ? a + FOLLOW.renamed[c] + '"' : all;
    });
  }

  function renderBasket() {
    var ul = $("basketList");
    // Drop items that no longer exist in the data (after a refresh).
    S.basket = S.basket.filter(function (b) { return S.items.has(b.code); });
    if (followArea()) saveBasket();
    $("basketEmpty").hidden = S.basket.length > 0;
    $("clearBtn").hidden = !S.basket.length;
    $("shareBtn").hidden = !S.basket.length;
    ul.innerHTML = S.basket.map(function (b) {
      var it = S.items.get(b.code), name = itemTitle(b.code), med = areaMedian(b.code), kg = isPerKg(b.code);
      var u = B.parseUnit(it.unit), up = med != null && u && !(u.base === "g" && u.amt === 1000) && u.base !== "pc" ? B.unitPrice(med, u) : null;
      var flexOn = !!(b.alt && b.alt.length), step = kg ? 0.5 : 1;
      var flexBtn = b.opts && b.opts.length ? '<button type="button" class="flex-tag' + (flexOn ? " on" : "") + '" data-flex="' + b.code + '" aria-pressed="' + flexOn + '">' +
        (flexOn ? CHECK_SVG : "") + "<span>" + esc(t(b.any === "brand" ? "anyBrand" : "anyGrade", { n: b.opts.length + 1 })) + "</span></button>" : "";
      var tip = cheaperPack(b), tipHtml = "";
      if (tip) {
        tipHtml = '<div class="tip-line">' + esc(t("cheaperPack", { size: tip.item.unit, u: unitLabel(tip.per.per), a: rm(tip.per.v), b: rm(tip.mine.v) })) +
          ' <button type="button" class="link-btn" data-swap="' + b.code + '" data-to="' + tip.item.code + '">' + t("switchPack") + "</button></div>";
      }
      // The button's name is its label; screen readers hear the figures under the name as its description.
      return '<li class="bi"><div class="bi-row">' +
        '<button type="button" class="bi-name" data-hist="' + b.code + '" aria-label="' + esc(t("history") + ": " + name) + '"' +
        ' aria-describedby="bisub-' + b.code + '">' +
        "<span>" + esc(name) + "</span>" +
        '<span class="res-sub" id="bisub-' + b.code + '"><span class="unit">' + esc(it.unit) + "</span> · " + esc(lineText(b)) +
        (up ? " · " + esc(t("perUnit", { v: rm(up.v), u: unitLabel(up.per) })) : "") + "</span></button>" +
        '<div class="qty" role="group" aria-label="' + esc(t("qty")) + '">' +
        '<button type="button" data-qty="' + b.code + '" data-d="-' + step + '" aria-label="' + esc(t("lessOf", { x: name })) + '">−</button>' +
        "<output>" + fmtQty(b.qty) + (kg ? "<small>kg</small>" : "") + "</output>" +
        '<button type="button" data-qty="' + b.code + '" data-d="' + step + '" aria-label="' + esc(t("moreOf", { x: name })) + '">+</button></div>' +
        '<button type="button" class="x" data-rm="' + b.code + '" aria-label="' + esc(t("removeX", { x: name })) + '">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>' +
        (flexBtn || tipHtml ? '<div class="bi-extra">' + flexBtn + tipHtml + "</div>" : "") + "</li>";
    }).join("");
  }

  // ---------- results ----------
  var RESULT_IDS = ["summary", "emptyResult", "rankCard", "splitCard", "avgCard"];
  function hideResults() {
    RESULT_IDS.forEach(function (id) { $(id).hidden = true; });
    S.hasResult = false;
    updateBar();
    say("");
  }
  // One polite status line for screen readers: the new top shop after a change, the "nothing priced" or few-shops
  // message, or the number of search results. Set after a short pause, so a burst of changes is read once.
  var LIVE = { t: null, last: "" };
  function say(text) {
    clearTimeout(LIVE.t);
    if (!text) { LIVE.last = ""; $("resultLive").textContent = ""; return; }
    LIVE.t = setTimeout(function () {
      if (text === LIVE.last) return;
      LIVE.last = text; $("resultLive").textContent = text;
    }, 500);
  }
  function thinText() { return $("thinBadge").hidden ? "" : " " + $("thinBadge").textContent; }
  function kpi(total) { return '<div class="kpis"><div><span>' + t("total") + "</span><b>RM " + rm(total) + "</b></div></div>"; }

  // Near me: a shop whose place is unknown (listed for the home district only) is never counted inside the radius.
  function unlocated(pidx) { return !!(S.d && S.d.near) && S.d.premises[pidx][7] == null; }
  // Ranked rows; in Near me the shops with a known place come first, then the ones whose place is unknown.
  function rankRows() {
    var rows = B.rankStores(S.idx, S.d.premises.length, S.basket);
    if (!S.d.near) return rows;
    return rows.filter(function (r) { return !unlocated(r.pidx); }).concat(rows.filter(function (r) { return unlocated(r.pidx); }));
  }
  // The rows that headlines, the result bar and the plan and 3-month cards may name: in Near me, located shops
  // only. When no located shop prices anything, the home district's shops (and the headline says "in <district>").
  function claimRows(rows) {
    var known = rows.filter(function (r) { return !unlocated(r.pidx); });
    return known.length ? known : rows;
  }
  // Where the shops that claimRows gives are: "within 5 km of you" (Near me), "in <district>" (a district, or the
  // home district when no shop with a known place prices the list). Every headline that counts shops names it.
  function claimArea(crows) {
    return !crows.length || !unlocated(crows[0].pidx) ? areaPhrase() : t("inDistrict", { d: S.d.near.home || S.d.district });
  }

  function renderRanking() {
    var fk = focusKey();
    drawRanking();
    refocus(fk);       // the plan buttons and the stop value are rebuilt: keep keyboard focus on the one used
  }
  function drawRanking() {
    if (!S.d || !S.basket.length) { hideResults(); return; }
    var n = S.basket.length, rows = rankRows();
    if (!rows.length) {
      // Nothing on the list is priced here: say so plainly and offer a way out.
      hideResults();
      $("emptyResult").hidden = false;
      $("emptyMsg").textContent = t("emptyResult", { a: areaPhrase() });
      $("widerBtn").hidden = !(S.d.near && S.d.near.radius < 20);   // the radius shown, which may be wider than chosen
      $("pickDistrictBtn").hidden = !S.d.near;
      $("tryNearBtn").hidden = !!(S.d.near || !(S.meta && S.meta.geo));
      say($("emptyMsg").textContent + thinText());
      return;
    }
    $("emptyResult").hidden = true;
    $("summary").hidden = false;
    $("rankCard").hidden = false;
    S.hasResult = true;

    var crows = claimRows(rows), inRadius = !unlocated(crows[0].pidx);
    var sum = B.summarise(crows, n), h = "";
    var area = esc(claimArea(crows));
    var best = sum.best, shopHtml = best ? "<span class='shop'>" + esc(premise(best.pidx).name) + "</span>" : "";
    if (sum.kind === "save" && sum.fullCount >= 3 && (sum.save >= 0.5 || sum.max - best.total >= 0.5)) {
      // Yardstick: the typical (median) total of shops with every item; the priciest when that ties the best.
      var vsMax = sum.save < 0.5;
      h = '<p class="big">' + t("sumHead", { save: rm(vsMax ? sum.max - best.total : sum.save), shop: shopHtml }) + "</p>" +
          '<p class="muted">' + (vsMax ? t("sumSubMax", { d: area, max: rm(sum.max), k: sum.fullCount })
                                       : t("sumSub", { d: area, med: rm(sum.median), max: rm(sum.max) })) + "</p>" + kpi(best.total);
    } else if (sum.kind === "save" && sum.fullCount >= 3) {
      h = '<p class="big">' + t("sumClose", { d: area, k: sum.fullCount, x: rm(sum.max - best.total) }) + "</p>" + kpi(best.total);
    } else if (sum.kind === "save") {
      // Only 2 shops have everything: name the cheaper one, no savings headline from so little data.
      h = '<p class="big">' + t("sumFew", { k: sum.fullCount, shop: shopHtml, d: area }) + "</p>" + kpi(best.total);
    } else if (sum.kind === "only") {
      h = '<p class="big">' + t("sumOnly", { shop: shopHtml, d: area }) + "</p>" + kpi(best.total);
    } else if (sum.kind === "none") {
      h = '<p class="big">' + t("sumNone", { h: best.have, n: best.n, d: area }) + "</p>";
    }
    // Near me: the headline is about the radius, so shops with every item whose place is unknown (listed last) are
    // not in it. Say how many there are, so the headline never seems to contradict the list under it.
    var unkFull = inRadius && S.d.near ? rows.filter(function (r) { return r.have === n && unlocated(r.pidx); }).length : 0;
    if (unkFull) h += '<p class="fine also-full">' + esc(tn("nearAlsoFull", unkFull, { u: unkFull, d: S.d.near.home || S.d.district })) + "</p>";
    h += '<p class="src-line">' + esc(t("srcLine", { d: fmtDate(S.d.data_date || S.meta.data_date) })) + "</p>";
    $("summary").innerHTML = h;

    renderRankList(rows);
    renderPlan(crows);
    renderAverage(crows);

    // The result bar names a shop inside the radius only (in Near me); with none, it stays hidden.
    var top = inRadius ? crows[0] : null;
    S.hasResult = !!top;
    if (top) {
      $("rbLabel").textContent = top.have === n ? t("barLabel") : t("barLabelPartial");
      $("rbShop").textContent = premise(top.pidx).name;
      $("rbTotal").textContent = "RM " + rm(top.total);
      say(t(top.have === n ? "liveTop" : "liveTopPart", { l: $("rbLabel").textContent, shop: premise(top.pidx).name, x: rm(top.total), h: top.have, n: n }) + thinText());
    } else {
      var big = $("summary").querySelector(".big");
      say((big ? big.textContent : "") + thinText());
    }
    updateBar();
  }

  function renderRankList(rows) {
    var n = S.basket.length, LIM = 6;
    // Tags compare with the top shop that has a known place; shops whose place is unknown get no tags.
    var top = claimRows(rows)[0], topFull = top.have === n, tagged = !unlocated(top.pidx);
    var shown = S.showAll ? rows : rows.slice(0, LIM), subDone = false;
    $("rankList").innerHTML = shown.map(function (r, i) {
      var p = premise(r.pidx), full = r.have === n, unk = unlocated(r.pidx);
      var names = r.missing.slice(0, 3).map(itemTitle);
      if (r.missing.length > 3) names.push(t("andMore", { n: r.missing.length - 3 }));
      var miss = r.missing.length ? '<span class="miss">' + esc(t("missing", { x: names.join(", ") })) + "</span>" : "";
      var tag = "", sub = "";
      if (unk && !subDone) {
        // A visual heading only: it is not a shop, so screen readers skip it (each row says "location unknown").
        subDone = true;
        sub = '<li class="fine rank-sub" aria-hidden="true"><b>' + esc(t("rankUnknownHead", { d: S.d.near.home || p.district || S.d.district })) + "</b></li>";
      }
      if (unk || !tagged) tag = "";
      else if (r === top) tag = full ? '<span class="tag good">' + t("best") + "</span>" : '<span class="tag most">' + t("mostItems", { h: r.have, n: n }) + "</span>";
      else if (full && topFull) tag = '<span class="diff">' + (r.total - top.total < 0.005 ? t("tie") : t("vsBest", { x: rm(r.total - top.total) })) + "</span>";
      else if (!full) {
        // Compare on the items this shop does have: what does the top shop charge for the same items?
        // (Also when the top shop lacks some items itself, as long as it prices all of this shop's items.)
        var sh = B.sharedTotals ? B.sharedTotals(S.idx, S.basket, r.pidx, top.pidx) : null;
        tag = sh && sh.n === r.have ? '<span class="diff">' + t("sharedVs", { y: rm(sh.b) }) + "</span>"
                                    : '<span class="diff">' + tn("forN", r.have, { h: r.have }) + "</span>";
      }
      return sub + '<li class="' + (full ? "" : "partial") + '" data-p="' + p.code + '">' +
        '<span class="rk">' + (i + 1) + "</span>" +
        '<div class="rmain"><div class="rname">' + esc(p.name) + "</div>" +
        '<div class="rsub">' + distTag(r.pidx) + '<span class="tag">' + esc(p.type) + "</span>" +
        '<span class="cov"><i style="width:' + Math.round(100 * r.have / n) + '%"></i></span>' +
        "<span>" + tn("has", n, { h: r.have, n: n }) + "</span>" + freshTag(r.maxDays) + "</div>" + miss + "</div>" +
        '<div class="rtot"><b>RM ' + rm(r.total) + "</b>" + tag + "</div></li>";
    }).join("");
    var more = $("moreBtn");
    more.hidden = rows.length <= LIM;
    more.textContent = S.showAll ? t("showLess") : t("showMore", { n: rows.length });
  }

  function lineLabel(b, code) {
    return itemTitle(code) + (b.qty !== 1 ? " ×" + fmtQty(b.qty) + (isPerKg(code) ? " kg" : "") : "");
  }

  function renderPlan(rows) {
    var card = $("splitCard");
    var po = B.planOptions(S.idx, S.d.premises.length, S.basket, rows, S.stopCost, 60);
    // Only worth a card when a 2- or 3-stop plan is really better (cheaper, or covers more items).
    if (S.basket.length < 2 || po.options.length < 2) { card.hidden = true; return; }
    var k = S.planK && po.options.some(function (o) { return o.k === S.planK; }) ? S.planK : po.rec;
    var o = po.options.filter(function (x) { return x.k === k; })[0], one = po.options[0], n = S.basket.length, c = S.stopCost;
    var rec = po.options.filter(function (x) { return x.k === po.rec; })[0];
    var seg = po.options.map(function (x) {
      return '<button type="button" data-k="' + x.k + '" aria-pressed="' + (x.k === k) + '">' +
        "<span>" + esc(t(x.k === 1 ? "stopsN" : "stopsNp", { k: x.k })) + "</span><b>RM " + rm(x.total) + "</b>" +
        (x.k === po.rec ? "<i>" + t("recTag") + "</i>" : "") + "</button>";
    }).join("");
    // Worded against the recommended plan, by the same rule that picks it (Basket.planNote).
    var note = B.planNote ? B.planNote(po, k, c, n) : null, nv = note ? note.vars : {};
    if (nv.x != null) nv.x = rm(nv.x);
    nv.d = esc(claimArea(rows));   // "No single shop within 5 km of you has as many": the shops the plan is made from
    var msg = note ? t(note.key, nv) : "";
    var cols = o.shops.map(function (p) {
      var lines = S.basket.filter(function (b) { return o.assign[b.code] === p; });
      return '<div class="split-col"><h3>' + t("buyAt", { shop: esc(premise(p).name) }) + '</h3><p class="fine">' + esc(premise(p).type) +
        (distText(p) ? " · " + distHtml(p) : "") + "</p><ul>" +
        lines.map(function (b) {
          var e = B.priceAt(S.idx, b, p), age = ageOf(e.d);
          return "<li><span>" + esc(lineLabel(b, e.code)) + (age > 7 ? ' <small class="age">' + esc(t("daysShort", { d: age })) + "</small>" : "") +
            "</span><b>" + rm(B.lineTotal ? B.lineTotal(e.p, b.qty) : e.p * b.qty) + "</b></li>";
        }).join("") + "</ul></div>";
    }).join("");
    var miss = S.basket.filter(function (b) { return o.assign[b.code] == null; }).map(function (b) { return itemTitle(b.code); });
    var costs = [0, 2, 5, 10, 20].map(function (v) { return '<option value="' + v + '"' + (v === c ? " selected" : "") + ">RM " + v + "</option>"; }).join("");
    var open = S.planOpen != null ? S.planOpen : po.rec > 1;
    var head = po.rec > 1 ? t("planSumK", { k: po.rec, x: rm(rec.total), s: rec.saving != null ? rm(rec.saving) : "" })
                          : t("planSum1", { x: rm(one.total) });
    if (po.rec > 1 && rec.saving == null) head = t("planSumCover", { k: po.rec, h: rec.have, n: n });
    card.innerHTML = '<details id="planDet"' + (open ? " open" : "") + "><summary><span>" + esc(head) + "</span></summary>" +
      '<div class="seg stops" role="group" aria-label="' + esc(t("planHead")) + '">' + seg + "</div>" +
      '<p class="plan-msg">' + msg + "</p>" +
      '<div class="split-cols">' + cols + "</div>" +
      (miss.length ? '<p class="fine">' + esc(t("planMissing", { x: miss.join(", ") })) + "</p>" : "") +
      '<label class="stop-cost"><span>' + t("stopCost") + '</span><select id="stopCost">' + costs + "</select></label></details>";
    card.hidden = false;
  }

  function renderAverage(rows) {
    var card = $("avgCard"), d = S.d;
    if (!d.shop_months || !d.avg_months || !d.avg_months.length || !S.basket.length) { card.hidden = true; return; }
    var ar = B.averageRank(d.shop_months, d.premises.length, S.basket, d.avg_months.length);
    // Near me: like the headline, only shops with a known place (rows are claimRows: located when any are).
    if (rows.length && !unlocated(rows[0].pidx)) ar = ar.filter(function (r) { return !unlocated(r.pidx); });
    var n = S.basket.length, full = ar.filter(function (r) { return r.have === n; });
    // "Cheapest" only for shops with every item: no such shop over the months, no card.
    var list = full.slice(0, 3);
    if (!list.length) { card.hidden = true; return; }
    var am = d.avg_months, today = rows[0], avg1 = list[0];
    var note = today && today.have === n ? (today.pidx === avg1.pidx ? t("avgSame", { x: esc(premise(today.pidx).name) })
      : t("avgDiff", { x: esc(premise(today.pidx).name), y: esc(premise(avg1.pidx).name) })) : "";
    card.innerHTML = '<details id="avgDet"' + (S.avgOpen ? " open" : "") + "><summary><span>" +
      esc(t("avgSum", { x: premise(avg1.pidx).name })) + "</span></summary>" +
      '<p class="muted">' + esc(t("avgSub", { a: monLabel(am[0], true), b: monLabel(am[am.length - 1], true) })) + "</p>" +
      '<ol class="rank avg-list">' + list.map(function (r, i) {
        var p = premise(r.pidx);
        return '<li class="' + (r.have === n ? "" : "partial") + '"><span class="rk">' + (i + 1) + '</span><div class="rmain"><div class="rname">' + esc(p.name) + "</div>" +
          '<div class="rsub">' + distTag(r.pidx) + '<span class="tag">' + esc(p.type) + "</span><span>" +
          esc(r.cheapestMonths ? tn("avgRow", r.fullMonths, { c: r.cheapestMonths, m: r.fullMonths }) : t("avgRow0")) + "</span>" +
          (r.have < n ? "<span>" + tn("has", n, { h: r.have, n: n }) + "</span>" : "") + "</div></div>" +
          '<div class="rtot"><b>RM ' + rm(r.total) + "</b></div></li>";
      }).join("") + "</ol>" +
      (note ? '<p class="plan-msg">' + note + "</p>" : "") +
      '<p class="fine">' + t("avgNote") + "</p></details>";
    card.hidden = false;
  }

  function updateBar() {
    var bar = $("resultBar"), hide = !(S.tab === "basket" && S.basket.length && S.d && S.hasResult && !S.resultsInView);
    // The bar hides itself once the results are on screen. Keyboard focus on it then goes to the results summary it
    // points to (or the list heading when there are none), never to the page, and without scrolling the page.
    if (hide && !bar.hidden && bar.contains(document.activeElement)) {
      if (!focusEl($("summary"), true)) focusEl($("listH"), true);
    }
    bar.hidden = hide;
  }

  // ---------- paste / upload a list ----------
  // The browser puts focus back on the control that opened a dialog when it closes. When a re-render has replaced
  // that control, the same control is found again by its key (see the "close" handlers in bind()), under its line's
  // new item when a render behind the dialog renamed the line (followDlgBack, from renderAll).
  // The price history opens with keyboard focus on its title (autofocus in index.html): screen readers then read the
  // item's name, not the whole dialog as one block. Its scroll box also takes focus (tabindex -1), so focus there, as
  // after a tap on its text, stays when a place that loads meanwhile leaves the dialog nothing to scroll.
  var DLG_BACK = {};
  function openDlg(id) {
    var d = $(id);
    if (!d.open) {
      DLG_BACK[id] = focusKey();
      if (d.showModal) d.showModal(); else d.setAttribute("open", "");
    }
    placeToast();
  }
  function followDlgBack() { Object.keys(DLG_BACK).forEach(function (id) { DLG_BACK[id] = followedKey(DLG_BACK[id]); }); }
  // After a dialog closes: the toast goes back to the page, and if the control that opened the dialog is gone
  // (re-rendered or hidden), focus the same control again (by its key), or the heading of the tab shown. A line left
  // to rename while the price-history dialog was open (undoGone, followArea) is renamed first: focus on it follows it
  // (renderAll).
  function afterDlgClose(id) {
    placeToast();
    if (id === "itemDlg" && FOLLOW.dlg) { FOLLOW.dlg = false; renameStale(); }
    if (!focusLost()) return;
    var el = null;
    try { el = DLG_BACK[id] ? document.querySelector(DLG_BACK[id]) : null; } catch (e) { el = null; }
    if (!focusEl(el, true)) focusEl(tabHead(), true);
  }
  // The heading of the tab shown (the list, or the price changes): a place for keyboard focus when the control it
  // was on has gone, such as a price-changes row no longer listed for a place that loaded meanwhile.
  function tabHead() { return $(S.tab === "trends" ? "feedTitle" : "listH"); }
  // A place for keyboard focus inside a dialog when the control it was on is behind the dialog: its title when that
  // takes focus (the price history, My lists), else its close button.
  function dlgHome(dl) {
    var h = $(dl.getAttribute("aria-labelledby"));
    return h && h.getAttribute("tabindex") === "-1" ? h : dl.querySelector(".sheet-head .icon-btn");
  }
  function closeDlg(id) { var d = $(id); if (d.open) { d.close(); afterDlgClose(id); } }
  function openPaste() {
    $("pasteStep").hidden = false; $("reviewStep").hidden = true; $("ocrStatus").textContent = "";
    openDlg("pasteDlg");
  }
  function runMatch() {
    var text = $("pasteIn").value;
    S.matches = S.cat.matchText(text).map(function (m) {
      m.use = m.code != null; m.canFlex = m.flex; m.orig = m.code;
      m.origAlt = (m.alt || []).slice(); m.origFlex = m.flex;   // brought back when the suggested item is chosen again
      return m;
    });
    if (!S.matches.length) { $("ocrStatus").textContent = t("pasteEmpty"); return; }
    renderMatches();
    $("pasteStep").hidden = true; $("reviewStep").hidden = false;
    $("pasteDlg").querySelector(".sheet-inner").scrollTop = 0;
    focusEl($("reviewNote"), true);   // the button used has gone with its step: start at the summary line
  }
  function unitOf(code) {
    var it = S.items.get(code);
    return it ? (LP && LP.parseUnit ? LP.parseUnit(it.unit) : B.parseUnit(it.unit)) : null;
  }
  function renderMatches() {
    var ok = S.matches.filter(function (m) { return m.code != null; }).length;
    $("reviewNote").textContent = t("reviewNote", { ok: ok, n: S.matches.length });
    $("matchList").innerHTML = S.matches.map(function (m, i) {
      if (m.code == null) {
        return '<li class="m none"><div class="m-raw">' + esc(m.raw) + '</div><div class="m-sub">' + t("notFound") + "</div></li>";
      }
      var opts = [m.code].concat((m.options || []).filter(function (c) { return c !== m.code; })).slice(0, 12);
      var sel = '<select data-mi="' + i + '" aria-label="' + esc(m.raw) + '">' + opts.map(function (c) {
          var it = S.items.get(c), cov = coverage(c);
          return '<option value="' + c + '"' + (c === m.code ? " selected" : "") + ">" + esc(titleCase(cleanItemName(it))) + " · " + esc(it.unit) +
            (S.d && !cov ? " · " + t("notHere") : "") + "</option>";
        }).join("") + '<option value="skip"' + (m.use ? "" : " selected") + ">" + t("skipLine") + "</option></select>";
      var kg = isPerKg(m.code);
      var qty = '<label class="m-qty"><span>' + t("qtyLabel") + '</span><input type="number" inputmode="decimal" min="' + (kg ? 0.5 : 1) +
        '" max="50" step="' + (kg ? 0.5 : 1) + '" value="' + fmtQty(m.qty) + '" data-mq="' + i + '"><small>' + esc(qtyWord(m)) + "</small></label>";
      var flex = m.alt && m.alt.length ? '<label class="m-flex"><input type="checkbox" data-mf="' + i + '"' + (m.flex ? " checked" : "") + "> " +
        esc(t(m.canFlex ? "anyBrand" : "anyGrade", { n: m.alt.length + 1 })) + "</label>" : "";
      var note = m.note === "ekor" ? t("noteEkor") : m.note === "size" ? t("noteSize") : "";
      var guess = m.status === "pick";
      return '<li class="m' + (guess ? " guess" : "") + (m.use ? "" : " off") + '"><div class="m-raw">' +
        (guess ? '<b class="q">' + esc(t("checkThis")) + "</b> " : "") + esc(m.raw) + "</div>" +
        sel + '<div class="m-pack">' + packHtml(m) + "</div>" + '<div class="m-row">' + qty + flex + "</div>" +
        (note ? '<div class="m-sub">' + esc(note) + "</div>" : "") + "</li>";
    }).join("");
    var n = S.matches.filter(function (m) { return m.use; }).length;
    $("addMatchesBtn").textContent = tn("addN", n, { n: n });
    $("addMatchesBtn").disabled = !n;
  }
  // Pack size and what the amount adds up to, outside the select so it is never cut off.
  function packHtml(m) {
    var it = S.items.get(m.code), size = unitOf(m.code);
    var pack = '<span class="unit">' + esc(it.unit) + "</span>";
    if (!isPerKg(m.code) && size) pack += " · " + esc(t(m.qty === 1 ? "pack1" : "packsN", { n: fmtQty(m.qty) })) +
      (m.qty !== 1 ? " = " + esc(fmtAmt(m.qty * size.amt, size.base)) : "");
    return pack;
  }
  function qtyWord(m) { return isPerKg(m.code) ? "kg" : t(m.qty === 1 ? "packWord1" : "packWord"); }
  function addMatches() {
    var added = 0;
    S.matches.forEach(function (m) {
      if (!m.use || m.code == null) return;
      var opts = m.code !== m.orig ? [] : (m.alt || []); // the shopper picked a specific item
      if (addItem(m.code, true, { qty: m.qty, opts: opts, flex: m.flex && opts.length, brand: m.canFlex })) added++;
    });
    $("pasteDlg").close();
    $("pasteIn").value = "";
    renderAll();
    afterDlgClose("pasteDlg");   // after the re-render: the empty-list button that opened it may have gone
    toast(tn("addedN", added, { n: added }));
    if (added) $("basketList").scrollIntoView({ behavior: smooth(), block: "start" });
  }

  // Screenshot reading with Tesseract (self-hosted in vendor/tesseract, loaded only when used).
  function loadScript(src) {
    return new Promise(function (res, rej) {
      var el = document.createElement("script"); el.src = src; el.onload = res; el.onerror = rej; document.head.appendChild(el);
    });
  }
  function simdOk() {
    try {
      return WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11]));
    } catch (e) { return false; }
  }
  function readImage(file) {
    var st = $("ocrStatus");
    if (location.protocol === "file:") { st.textContent = t("ocrNeedsWeb"); return; }
    st.textContent = t("ocrLoading");
    var base = new URL("vendor/tesseract/", location.href).href, worker = null;
    (window.Tesseract ? Promise.resolve() : loadScript("vendor/tesseract/tesseract.min.js")).then(function () {
      return window.Tesseract.createWorker("eng", 1, {
        workerPath: base + "worker.min.js",
        corePath: base + (simdOk() ? "tesseract-core-simd-lstm.wasm.js" : "tesseract-core-lstm.wasm.js"),
        langPath: base, gzip: true,
        logger: function (m) { if (m.status === "recognizing text") st.textContent = t("ocrReading", { p: Math.round(m.progress * 100) }); }
      });
    }).then(function (w) { worker = w; return w.recognize(file); }).then(function (r) {
      var text = (r.data && r.data.text || "").trim();
      if (!text) { st.textContent = t("ocrNone"); return; }
      var box = $("pasteIn"); box.value = (box.value ? box.value.replace(/\s+$/, "") + "\n" : "") + text;
      st.textContent = t("ocrDone", { n: text.split(/\n+/).filter(Boolean).length });
    }).catch(function (e) { console.warn(e); st.textContent = t("ocrFail"); })
      .then(function () { if (worker) worker.terminate(); });
  }
  function readText(file) {
    var fr = new FileReader();
    fr.onload = function () {
      var text = String(fr.result || "").slice(0, 20000), box = $("pasteIn");
      box.value = (box.value ? box.value.replace(/\s+$/, "") + "\n" : "") + text;
      $("ocrStatus").textContent = t("txtDone", { n: text.split(/\n+/).filter(Boolean).length });
    };
    fr.readAsText(file);
  }

  // ---------- saved lists and share links ----------
  // Line format: main code, then alternatives, "~" amount, and an optional flag:
  // b = any brand on, g = any grade/size on, B / G = alternatives listed but switched off.
  // Old links (no flag) still work.
  function encodeList(basket) {
    return basket.map(function (b) {
      var on = !!(b.alt && b.alt.length), alts = on ? b.alt : (b.opts || []);
      var flag = alts.length ? (b.any === "brand" ? "b" : "g") : "";
      if (alts.length && !on) flag = flag.toUpperCase();
      return [b.code].concat(alts).join("-") + "~" + fmtQty(b.qty) + (flag ? "~" + flag : "");
    }).join("_");
  }
  // A shared list is never cut: every item in the link that is in this data comes back, each once (the first time
  // it is listed), as a saved list does (okBasket).
  function decodeList(str) {
    if (!str || !/^[0-9~._bBgG-]+$/.test(str)) return [];
    var out = [], seen = {};
    str.split("_").forEach(function (part) {
      var bits = part.split("~"), codes = bits[0].split("-").map(Number).filter(function (c) { return S.items.has(c); });
      if (!codes.length || seen[codes[0]]) return;
      seen[codes[0]] = true;
      var flag = bits[2] || (codes.length > 1 ? "b" : "");
      var line = { code: codes[0], qty: normQty(codes[0], bits[1]) };
      if (codes.length > 1) {
        line.opts = codes.slice(1);
        if (flag.toLowerCase() === "b") line.any = "brand";
        if (flag === "b" || flag === "g") line.alt = codes.slice(1);
      }
      out.push(line);
    });
    return out;
  }
  function shareUrl() {
    var u = location.href.split("#")[0].split("?")[0];
    return u + "?l=" + encodeList(S.basket);
  }
  function openLists() {
    refreshLists();
    openDlg("listsDlg");
  }
  // Also run while the dialog is open (an Undo from the toast can change the list there).
  function refreshLists() {
    renderLists();
    $("shareOut").value = S.basket.length ? shareUrl() : "";
    $("copyBtn").disabled = !S.basket.length;
    $("nativeShareBtn").hidden = !(navigator.share && S.basket.length);
  }
  function renderLists() {
    $("savedLists").innerHTML = S.lists.length ? S.lists.map(function (l, i) {
      return "<li><span><b>" + esc(l.name) + "</b><small>" + tn("itemsN", l.basket.length, { n: l.basket.length }) + " · " + esc(l.at) + "</small></span>" +
        '<button type="button" class="btn small" data-load="' + i + '">' + t("loadList") + "</button>" +
        '<button type="button" class="link-btn danger" data-del="' + i + '">' + t("deleteList") + "</button></li>";
    }).join("") : '<li class="empty">' + t("noSaved") + "</li>";
    $("saveListBtn").disabled = !S.basket.length;
  }
  function saveList(name) {
    if (!S.basket.length) { toast(t("emptyList")); return; }
    name = (name || "").trim() || t("listNamePh").replace(/^(e\.g\.|cth\.)\s*/, "");
    S.lists = S.lists.filter(function (l) { return l.name !== name; });
    S.lists.unshift({ name: name, at: todayISO(), basket: JSON.parse(JSON.stringify(S.basket)) });
    S.lists = S.lists.slice(0, 20); store.set("lists", S.lists);
    $("listName").value = ""; renderLists(); toast(t("savedOk", { x: name }));
  }
  function checkShared() {
    // Some apps rewrite "~" as "%7E": read the link decoded (a bad escape leaves it as it is).
    var q = location.search, h = location.hash;
    try { q = decodeURIComponent(q); } catch (e) { /* keep as is */ }
    try { h = decodeURIComponent(h); } catch (e) { /* keep as is */ }
    var m = q.match(/[?&]l=([0-9~._bBgG-]+)/) || h.match(/[#&]l=([0-9~._bBgG-]+)/);
    var lines = m ? decodeList(m[1]) : [];
    if (!lines.length) return;
    S.shared = lines;
    renderImportBar();
    $("importBar").hidden = false;
  }
  function renderImportBar() {
    if (!S.shared) return;
    var has = S.basket.length > 0;
    $("importMsg").textContent = tn("importMsg", S.shared.length, { n: S.shared.length });
    $("importYes").textContent = has ? t("importReplace") : t("importYes");
    $("importAdd").hidden = !has;
  }
  function clearSharedParam() {
    $("importBar").hidden = true; S.shared = null;
    try { history.replaceState(null, "", location.href.split("?")[0].split("#")[0]); } catch (e) { /* not allowed here */ }
  }

  // ---------- price changes ----------
  function renderFeed() {
    if (!S.meta) return;
    var national = S.scope === "national" || !S.d;
    var feed = national ? S.meta.national_feed : S.d.feed;
    var ms = S.meta.months_with_data && S.meta.months_with_data.length ? S.meta.months_with_data : S.meta.months;
    var cur = ms[ms.length - 1], prev = ms[ms.length - 2];
    document.querySelectorAll("[data-dir]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.dir === S.dir)); });
    document.querySelectorAll("[data-scope]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.scope === (national ? "national" : "district")));
      if (b.dataset.scope === "district") b.disabled = !S.d;
    });
    $("feedNote").textContent = prev ? t("feedNote", { m: monLabel(cur, true), p: monLabel(prev, true),
      s: national ? "Malaysia" : (S.d.near ? t("feedNearHome", { d: S.d.near.home || S.d.district }) : S.d.district) }) : "";
    // The % is worked out from the two typical prices shown (older data files hold another measure), and a
    // row is listed only when its prices really moved that way.
    var rows = ((feed && feed[S.dir]) || []).map(function (r) {
      return [r[0], r[3] > 0 && r[4] != null ? (r[4] / r[3] - 1) * 100 : null, r[2], r[3], r[4]];
    }).filter(function (r) { return r[1] != null && (S.dir === "falls" ? r[1] < 0 : r[1] > 0) && S.items.has(r[0]); });
    rows.sort(function (a, b) { return Math.abs(b[1]) - Math.abs(a[1]); });
    if (!rows.length) { $("feedList").innerHTML = '<li class="empty">' + t("feedEmpty") + "</li>"; return; }
    var maxAbs = Math.max.apply(null, rows.map(function (r) { return Math.abs(r[1]); }));
    $("feedList").innerHTML = rows.slice(0, 20).map(function (r, i) {
      var it = S.items.get(r[0]);
      var up = r[1] > 0, diff = r[4] - r[3];
      return '<li><button type="button" class="fi" data-hist="' + r[0] + '">' +
        '<span class="rk">' + (i + 1) + "</span>" +
        '<span class="fmain"><span class="res-name">' + esc(titleCase(cleanItemName(it))) + "</span>" +
        '<span class="res-sub"><span class="unit">' + esc(it.unit) + "</span> · RM " + rm(r[3]) + " → RM " + rm(r[4]) +
        " (" + (diff >= 0 ? "+" : "−") + "RM " + rm(Math.abs(diff)) + ") · " + tn("shopsN", r[2], { n: Number(r[2]) }) + "</span>" +
        '<span class="bar ' + (up ? "up" : "down") + '"><i style="width:' + Math.max(4, Math.round(100 * Math.abs(r[1]) / maxAbs)) + '%"></i></span></span>' +
        '<span class="pct ' + (up ? "up" : "down") + '">' + (up ? "+" : "−") + Math.abs(r[1]).toFixed(1) + "%</span></button></li>";
    }).join("");
  }

  // ---------- item dialog ----------
  function openItem(code) {
    var it = S.items.get(code); if (!it) return;
    S.dlgCode = code;
    $("dlgTitle").textContent = titleCase(cleanItemName(it));
    $("dlgSub").innerHTML = '<span class="unit">' + esc(it.unit) + "</span> · " + esc(titleCase(it.item_category || it.item_group));
    var dh = (S.d && S.d.history && S.d.history[code]) || [];
    // Near me: the same shops as the item's figures on the list (itemPrices): with a known place, else the home
    // district's, which the labels then name.
    var ip = S.idx ? itemPrices(code) : null, home = ip ? ip.home : null;
    var cheap = ip ? B.cheapestFor(S.idx, code).filter(function (c) { return ip.m.has(c.pidx); }) : [];
    // Change from the first to the last month with a price; named by its months when that is not the full year.
    var hist = dh.length ? dh : (S.meta.national_history[code] || []), hms = (dh.length && S.d.months) || S.meta.months || [];
    var fi = -1, li = -1;
    hist.forEach(function (v, i) { if (v != null) { if (fi < 0) fi = i; li = i; } });
    var chg = fi >= 0 && li > fi && hist[fi] > 0 ? (hist[li] / hist[fi] - 1) * 100 : null;
    var chgLabel = chg == null || (fi === 0 && li === hist.length - 1) || !hms[fi] || !hms[li] ? t("yearChg")
      : t("chgSpan", { a: monLabel(hms[fi], true), b: monLabel(hms[li], true) });
    $("dlgStats").innerHTML =
      "<div><span>" + esc(S.d ? (home ? t("medianIn", { d: home }) : t("medianHere")) : t("medianMy")) + "</span><b>RM " +
      rm(S.d ? areaMedian(code) : nationalLatest(code)) + "</b></div>" +
      (S.d ? "<div><span>" + esc(home ? t("lowIn", { d: home }) : t("lowHere")) + "</span><b>RM " + rm(cheap.length ? cheap[0].p : null) + "</b></div>" : "") +
      "<div><span>" + esc(chgLabel) + '</span><b class="' + (chg > 0 ? "up" : chg < 0 ? "down" : "") + '">' +
      (chg == null ? "-" : (chg > 0 ? "+" : "") + chg.toFixed(1) + "%") + "</b></div>";
    $("dlgLegend").innerHTML = (S.d ? '<span><i class="sw s1"></i>' + esc(t("lineDistrict", { d: S.d.district })) + "</span>" : "") +
                               '<span><i class="sw s2"></i>' + esc(t("lineNational")) + "</span>";
    $("cheapHead").textContent = S.d ? (home ? t("cheapestIn", { d: home }) : S.d.near ? t("cheapestNear") : t("cheapestHere")) : t("cheapestPick");
    $("dlgShops").innerHTML = !S.d ? "<li>" + t("chooseAreaShort") + "</li>" : cheap.length ? cheap.slice(0, 5).map(function (c) {
      var p = premise(c.pidx), age = ageOf(c.d);
      return "<li><span>" + esc(p.name) + "<small>" + (distText(c.pidx) ? distHtml(c.pidx) + " · " : "") + esc(p.type) + " · " +
        esc(age === 0 ? t("today") : t(age === 1 ? "daysAgo1" : "daysAgo", { n: age })) + "</small></span><b>RM " + rm(c.p) + "</b></li>";
    }).join("") : "<li>" + t("noData") + "</li>";
    updateDlgAdd();
    openDlg("itemDlg");
    drawChart();
  }
  var chartW = 0;
  function drawChart() {
    var code = S.dlgCode; if (code == null) return;
    var ms = (S.d && S.d.months) || S.meta.months;
    var none = ms.map(function () { return null; }), series = [];
    if (S.d) series.push({ name: S.d.district, label: t("lineDistrict", { d: S.d.district }), values: (S.d.history[code] || none), cls: "s1" });
    series.push({ name: "Malaysia", label: t("lineNational"), values: (S.meta.national_history[code] || none), cls: "s2" });
    chartW = $("dlgChart").clientWidth;
    window.MiniChart.lineChart($("dlgChart"), {
      labels: ms.map(function (m) { return monLabel(m); }),
      series: series,
      fmt: function (v) { return "RM " + v.toFixed(2); },
      label: t("history") + ": " + itemTitle(code),
      empty: t("noData"),
      // The monthly prices as a table for screen readers (the chart itself is a picture).
      table: { month: t("tableMonth"), labels: ms.map(function (m) { return monLabel(m, true); }), none: t("noPrice") }
    });
  }
  // aria-disabled, not disabled: a disabled button would drop keyboard focus right after it was used.
  function updateDlgAdd() {
    var b = $("dlgAdd"), inB = inBasket(S.dlgCode);
    b.textContent = inB ? t("inList") : t("addToList");
    b.setAttribute("aria-disabled", String(inB));
  }

  // ---------- render all ----------
  function renderHero() {
    var m = S.meta, pr = $("heroProof");
    if (!m) { pr.hidden = true; return; }
    $("heroSrc").textContent = t("heroSrc", { d: fmtDate(m.data_date) });
    var proof = m.proof, slug = currentSlug(), v = proof && proof.by_district && slug ? proof.by_district[slug] : null;
    if (!v && proof && proof.by_district) { slug = proof.example; v = slug ? proof.by_district[slug] : null; }
    // Only real, local numbers: shown when the latest data has them and the gap is at least RM 1.
    if (!v || v[1] - v[0] < 1) { pr.hidden = true; return; }
    pr.textContent = t("heroProof", { n: proof.items.length, a: areaName(slug), c: rm(v[0]), t: rm(v[1]), d: fmtDate(m.data_date) });
    pr.hidden = false;
  }
  function currentSlug() {
    if (!S.d || !S.meta) return null;
    var out = null;
    (S.meta.areas[S.d.state] || []).forEach(function (a) { if (a.district === S.d.district) out = a.slug; });
    return out;
  }
  function areaName(slug) {
    var out = slug;
    Object.keys(S.meta.areas).forEach(function (st) { S.meta.areas[st].forEach(function (a) { if (a.slug === slug) out = a.district; }); });
    return out;
  }
  function renderAll() {
    var fk = followedKey(focusKey());   // a line renamed since the last render (switched off: toggleFlex)
    FOLLOW.renamed = {};
    applyLang();
    renderWhere();
    renderHero();
    renderBasket(); renderRanking(); renderFeed();
    if ($("searchIn").value) renderResults();
    var m = S.meta;
    $("dataLine").textContent = t("dataLine", { d: fmtDate(m.data_date), b: fmtDate(String(m.generated_at || "").slice(0, 10)) });
    $("synthBanner").hidden = !m.synthetic;
    var age = daysSince(m.data_date);
    $("staleBanner").hidden = !(age > 14);
    if (age > 14) $("staleMsg").textContent = t("staleData", { d: fmtDate(m.data_date) });
    if ($("itemDlg").open) openItem(S.dlgCode);
    if ($("listsDlg").open) refreshLists();
    renderImportBar();
    // e.g. '+' or 'Any brand' rebuilt with the list: keep keyboard focus on the same control, also when a newly shown
    // place renamed its line (followArea)
    refocus(followedKey(fk));
    followDlgBack();
    FOLLOW.renamed = {};
  }

  // ---------- where: near me ----------
  function renderWhere() {
    var hasGeo = !!(S.meta && S.meta.geo), near = hasGeo && S.mode === "near";
    $("modeSeg").hidden = !hasGeo;
    $("nearBox").hidden = !near;
    $("geoLine").hidden = !hasGeo;
    $("pcCredit").hidden = !(hasGeo && /GeoNames/i.test(S.meta.geo.postcode_source || ""));
    document.querySelector(".where").classList.toggle("is-near", near);
    document.querySelectorAll("[data-mode]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === (near ? "near" : "district"))); });
    $("radiusSel").value = String(S.radius);
    var so = $("stateSel").options[0]; if (so && so.value === "") so.textContent = t("statePh");
    var dso = $("districtSel").options[0]; if (dso && dso.value === "") dso.textContent = t("districtPh");
    var note, msg = nearMsgText();
    if (!S.meta) note = t("loadingPrices");
    else if (near && S.locating) note = t("nearLocating");
    else if (near && S.nearLoading) note = tn("nearLoadingK", S.nearLoading.k, S.nearLoading);
    else if (near && !(S.d && S.d.near)) note = msg || t("nearAsk");
    else if (S.d && S.d.near) {
      var nr = S.d.near, located = nr.located != null ? nr.located : S.d.premises.length;
      note = (msg ? msg + " " : "") + t(located === 1 ? "nearCount1" : "nearCount", { n: located, r: nr.radius }) +
        (nr.unlocated ? " " + tn("nearUnknown", nr.unlocated, { u: nr.unlocated, d: nr.home || S.d.district }) : "") + " " + t("nearPrivate");
    } else if (S.d) {
      var np = S.d.premises.length;
      note = t(np === 1 ? "whereNote1" : "whereNote", { p: np, i: Object.keys(S.d.prices).length });
    } else note = t("chooseArea");
    // A live region: write it only when it changes, so screen readers do not repeat it after every list change.
    if ($("whereNote").textContent !== note) $("whereNote").textContent = note;
    // Few shops: in Near me count only shops with a known place; no "wider radius" advice at the widest radius.
    var nShops = S.d ? (S.d.near && S.d.near.located != null ? S.d.near.located : S.d.premises.length) : 0;
    var thin = !!S.d && nShops <= 5, key;
    $("thinBadge").hidden = !thin;
    if (thin) {
      if (S.d.near) key = S.d.near.radius >= RADII[RADII.length - 1] ? "thinNearMax" : "thinNear";
      else key = hasGeo ? "thinDistrict" : "thinDistrictNoGeo";
      $("thinBadge").textContent = tn(key, nShops, { n: nShops, r: S.d.near ? S.d.near.radius : "" });
    }
  }
  // Near me notes are kept as label keys with their values, so they follow a switch of language.
  function nearNote(key, vars) { return [key, vars || null]; }
  function nearMsgText() { return (S.nearMsg || []).map(function (m) { return t(m[0], m[1]); }).join(" "); }
  // A location request still waiting is dropped when the shopper moves on (a postcode, or a district).
  function cancelLocate() { S.locTok = (S.locTok || 0) + 1; S.locating = false; }
  function setMode(mode) {
    if (S.mode === mode && mode === "district" && S.d && !S.d.near) return;
    S.mode = mode; store.set("mode", mode); S.showAll = false; S.planK = null;
    hideError();
    if (mode === "district") {
      cancelLocate(); S.nearMsg = []; S.nearLoading = null;
      var v = $("districtSel").value;
      if (v) selectDistrict(v); else { ++S.seq; clearArea(); renderAll(); }
    } else if (S.near) loadNear();
    else { ++S.seq; clearArea(); renderAll(); }
  }
  function locate() {
    if (!navigator.geolocation) { S.nearMsg = [nearNote("nearNoFix")]; renderWhere(); return; }
    var tok = S.locTok = (S.locTok || 0) + 1;
    S.locating = true; renderWhere();
    navigator.geolocation.getCurrentPosition(function (pos) {
      if (tok !== S.locTok) return;            // the shopper typed a postcode or chose a district meanwhile
      S.locating = false;
      S.near = { lat: pos.coords.latitude, lon: pos.coords.longitude, pc: null, level: 6 };
      S.nearMsg = [];
      if (S.mode === "near") loadNear(); else renderWhere();
    }, function (err) {
      if (tok !== S.locTok) return;
      S.locating = false;
      // 1 = blocked by the shopper; 2/3 = no fix or too slow.
      S.nearMsg = err && err.code === 1 ? [nearNote("nearDenied"), nearNote("nearDeniedHow")] : [nearNote("nearNoFix")];
      renderWhere(); if (S.mode === "near") $("pcIn").focus();
    }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 });
  }
  function usePostcode(pc) {
    pc = String(pc || "").trim();
    if (!/^\d{5}$/.test(pc)) { S.nearMsg = [nearNote("nearPcBad")]; renderWhere(); return; }
    var hit = NEAR.locatePostcode(pc, S.meta.geo.postcodes);
    if (!hit) { S.nearMsg = [nearNote("nearPcUnknown")]; renderWhere(); return; }
    cancelLocate();                            // a late GPS fix must not replace the postcode's results
    var level = hit.level || (hit.exact ? 5 : 3);
    S.near = { lat: hit.lat, lon: hit.lon, pc: pc, level: level };
    S.nearMsg = []; loadNear();
  }
  var RADII = [3, 5, 10, 20];
  // Load only the district files that can have shops inside the radius; widen step by step if under 3 located shops.
  function loadNear() {
    var loc = S.near; if (!loc || S.mode !== "near") return;
    var seq = ++S.seq, want = S.radius, steps = RADII.filter(function (r) { return r >= want; });
    if (!steps.length) steps = [20];
    function tryStep(i) {
      var r = steps[i], list = NEAR.districtsWithin(S.meta.areas, loc.lat, loc.lon, r).slice(0, 30);
      if (seq === S.seq && S.mode === "near") { S.nearLoading = { k: list.length }; renderWhere(); }
      return Promise.all(list.map(function (x) { return loadData("d/" + x.slug); })).then(function (files) {
        var merged = files.length ? NEAR.mergeNear(files, loc.lat, loc.lon, r) : null;
        var located = merged ? (merged.near && merged.near.located != null ? merged.near.located : merged.premises.length) : 0;
        if (located < 3 && i + 1 < steps.length) return tryStep(i + 1);
        return { merged: merged, r: r };
      });
    }
    tryStep(0).then(function (res) {
      if (seq !== S.seq || S.mode !== "near" || S.near !== loc) return;   // a newer choice was made meanwhile
      S.nearLoading = null;
      hideError();
      var parts = [];
      if (loc.pc) parts.push(nearNote(loc.level >= 5 ? "nearPcNote" : loc.level >= 3 ? "nearPcArea" : "nearPcWide", { pc: loc.pc }));
      if (!res.merged || !res.merged.premises.length) {
        clearArea(); S.nearMsg = parts.concat([nearNote("nearNone")]); renderAll(); return;
      }
      if (res.r !== want) parts.push(nearNote("nearWider", { r0: want, r: res.r }));
      S.nearMsg = parts;
      S.d = res.merged; S.idx = B.indexDistrict(res.merged); S.showAll = false;
      S.d.near.fromPostcode = !!loc.pc;      // only whether it was a postcode; never the location itself
      renderAll();
    }).catch(function (e) {
      if (seq !== S.seq) return;
      console.error(e);
      S.nearLoading = null;
      if (S.mode !== "near") return;
      S.nearMsg = [nearNote("loadFail")];
      showError("loadFail", loadNear);
      renderWhere();
    });
  }

  // Tabs: only the selected tab is in the Tab order; the arrow keys, Home and End move between tabs (see bind()).
  function setTab(tab, focus) {
    S.tab = tab;
    document.querySelectorAll("[data-tab]").forEach(function (b) {
      var on = b.dataset.tab === tab;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    $("panel-basket").hidden = tab !== "basket";
    $("panel-trends").hidden = tab !== "trends";
    updateBar();
  }

  // ---------- events ----------
  function bind() {
    $("stateSel").addEventListener("change", function () { selectDistrict(fillDistrictOptions()); });
    $("districtSel").addEventListener("change", function (e) { selectDistrict(e.target.value); });
    document.querySelectorAll("[data-mode]").forEach(function (b) { b.addEventListener("click", function () { setMode(b.dataset.mode); }); });
    $("locBtn").addEventListener("click", locate);
    $("pcForm").addEventListener("submit", function (e) { e.preventDefault(); usePostcode($("pcIn").value); });
    $("radiusSel").addEventListener("change", function (e) {
      S.radius = Number(e.target.value); store.set("radius", S.radius); S.planK = null;
      if (S.near && S.mode === "near") loadNear();
    });
    $("errRetry").addEventListener("click", function () { var f = S.retry; hideError(); if (f) f(); });
    $("widerBtn").addEventListener("click", function (e) {
      // Wider than the radius shown (Near me may already have widened the one chosen).
      var shownR = S.d && S.d.near ? S.d.near.radius : S.radius;
      var next = RADII.filter(function (r) { return r > shownR; })[0] || 20;
      S.radius = next; store.set("radius", next); $("radiusSel").value = String(next); loadNear();
      if (byKey(e)) focusEl($("radiusSel"));   // this button goes once the wider area has shops
    });
    $("pickDistrictBtn").addEventListener("click", function () {
      setMode("district"); $("stateSel").focus();
      document.querySelector(".where").scrollIntoView({ behavior: smooth(), block: "start" });
    });
    $("tryNearBtn").addEventListener("click", function (e) {
      setMode("near");
      document.querySelector(".where").scrollIntoView({ behavior: smooth(), block: "start" });
      if (byKey(e)) focusEl($("locBtn"), true);
    });
    $("searchIn").addEventListener("input", function () {
      var n = renderResults();
      if ($("searchIn").value.trim()) say(n ? tn("resultsN", n, { n: n }) : t("resultsNone"));
    });
    $("searchIn").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { var f = $("results").querySelector("[data-add]"); if (f) f.click(); }
      if (e.key === "Escape") { e.target.value = ""; renderResults(); }
    });
    $("results").addEventListener("click", function (e) {
      var b = e.target.closest("[data-add]"); if (!b) return;
      var code = Number(b.dataset.add);
      // A second tap does not remove: removing is done with the X in the list (with Undo).
      if (!addItem(code)) toast(t("alreadyIn", { x: itemTitle(code) }));
      renderAll(); $("searchIn").focus();
    });
    $("basketList").addEventListener("click", function (e) {
      var q = e.target.closest("[data-qty]"), r = e.target.closest("[data-rm]"), h = e.target.closest("[data-hist]");
      var f = e.target.closest("[data-flex]"), sw = e.target.closest("[data-swap]");
      if (q) {
        var code = Number(q.dataset.qty), cur = lineOf(code);
        setQty(code, cur.qty + Number(q.dataset.d)); renderAll();
      } else if (r) removeWithUndo(Number(r.dataset.rm), byKey(e));
      else if (f) { toggleFlex(Number(f.dataset.flex)); renderAll(); }
      else if (sw) {
        var from = Number(sw.dataset.swap), to = Number(sw.dataset.to), line = lineOf(from);
        var a = B.parseUnit(S.items.get(from).unit), bU = B.parseUnit(S.items.get(to).unit);
        var packs = Math.max(1, Math.ceil(line.qty * a.amt / bU.amt - 0.03)), target = lineOf(to);
        if (target) { target.qty = normQty(to, target.qty + packs); removeItem(from); }
        else { line.code = to; line.qty = normQty(to, packs); delete line.opts; delete line.alt; delete line.any; delete line.own; }
        saveBasket(); renderAll();
        focusEl(document.querySelector('#basketList [data-hist="' + to + '"]'), !byKey(e));   // the Switch tip has gone: the row itself
      }
      else if (h) openItem(Number(h.dataset.hist));
    });
    $("feedList").addEventListener("click", function (e) { var h = e.target.closest("[data-hist]"); if (h) openItem(Number(h.dataset.hist)); });
    $("starterBtn").addEventListener("click", addStarter);
    ["pasteBtn", "pasteBtn2"].forEach(function (id) { $(id).addEventListener("click", openPaste); });
    $("pasteClose").addEventListener("click", function () { closeDlg("pasteDlg"); });
    $("matchBtn").addEventListener("click", runMatch);
    $("backBtn").addEventListener("click", function () { $("reviewStep").hidden = true; $("pasteStep").hidden = false; focusEl($("pasteIn")); });
    $("addMatchesBtn").addEventListener("click", addMatches);
    $("imgIn").addEventListener("change", function (e) { var f = e.target.files[0]; if (f) readImage(f); e.target.value = ""; });
    $("txtIn").addEventListener("change", function (e) { var f = e.target.files[0]; if (f) readText(f); e.target.value = ""; });
    $("matchList").addEventListener("change", function (e) {
      var el = e.target, i;
      if (el.dataset.mi != null) {
        var m = S.matches[Number(el.dataset.mi)];
        if (el.value === "skip") m.use = false;
        else {
          m.use = true; m.code = Number(el.value);
          if (m.code !== m.orig) { m.flex = false; m.alt = []; }
          else { m.alt = (m.origAlt || []).slice(); m.flex = m.origFlex; }
          // Keep the amount the shopper asked for: 10 kg switched to a 5 kg pack becomes 2 packs.
          var size = unitOf(m.code);
          if (m.want && size && size.base === m.want.base && LP && LP.packsFor) m.qty = normQty(m.code, LP.packsFor(m.want.amt, size));
          else m.qty = normQty(m.code, m.qty);
        }
        var fk = document.activeElement === el ? focusKey(el) : null;
        renderMatches(); refocus(fk);   // the line is rebuilt for the item chosen: keep focus on its select
      } else if (el.dataset.mq != null) {
        i = Number(el.dataset.mq); var v = Number(el.value), mm = S.matches[i];
        if (v > 0) {
          mm.qty = normQty(mm.code, v);
          // Remember the amount typed, so a later switch of pack keeps it (2 x 5 kg becomes 5 x 2 kg).
          var sz = unitOf(mm.code);
          if (sz && sz.amt) mm.want = { amt: mm.qty * sz.amt, base: sz.base };
        }
        // Updated in place (not rebuilt), so keyboard focus stays where the shopper is. The box always shows the
        // amount that will be added: a 0 or a blank puts it back.
        el.value = fmtQty(mm.qty);
        var li = el.closest("li");
        li.querySelector(".m-pack").innerHTML = packHtml(mm);
        el.parentNode.querySelector("small").textContent = qtyWord(mm);
      } else if (el.dataset.mf != null) {
        S.matches[Number(el.dataset.mf)].flex = el.checked;
      }
    });
    $("listsBtn").addEventListener("click", openLists);
    $("shareBtn").addEventListener("click", function () {
      // Phones: straight to the share sheet (WhatsApp...). Elsewhere: the dialog with a copyable link.
      if (navigator.share && S.basket.length) {
        try { navigator.share({ title: "Bakul Murah", url: shareUrl() }).catch(function () {}); return; } catch (err) { /* fall through */ }
      }
      openLists();
    });
    $("listsClose").addEventListener("click", function () { closeDlg("listsDlg"); });
    $("saveForm").addEventListener("submit", function (e) { e.preventDefault(); saveList($("listName").value); });
    $("savedLists").addEventListener("click", function (e) {
      var l = e.target.closest("[data-load]"), d = e.target.closest("[data-del]");
      if (l) {
        var item = S.lists[Number(l.dataset.load)], lines = item ? knownLines(okBasket(item.basket)) : [];
        if (!lines.length) { toast(t("emptyList")); return; }
        // Loading replaces the list on screen; Undo brings it back, as for Clear and Replace.
        var prev = S.basket, toLists = function () { return $("listsBtn"); };
        S.basket = lines; saveBasket(); S.planK = null;
        closeDlg("listsDlg"); renderAll();
        if (prev.length) {
          toast(t("loadedList", { x: item.name }), t("undo"), function () { S.basket = prev; saveBasket(); renderAll(); focusEl($("listsBtn")); }, toLists);
          afterUndoable(byKey(e), toLists);
        } else toast(t("loadedList", { x: item.name }));
      } else if (d) {
        var at = Number(d.dataset.del), gone = S.lists[at];
        S.lists.splice(at, 1); store.set("lists", S.lists); renderLists();
        // Focus goes to the Delete button of the list now in that place, or the dialog title when none is left.
        var back = function () {
          var els = document.querySelectorAll("#savedLists [data-del]");
          return els.length ? els[Math.min(at, els.length - 1)] : $("listsTitle");
        };
        toast(t("deletedList", { x: gone.name }), t("undo"), function () {
          var to = Math.min(at, S.lists.length);
          S.lists.splice(to, 0, gone); store.set("lists", S.lists); renderLists();
          focusEl(document.querySelector('#savedLists [data-del="' + to + '"]'));
        }, back);
        afterUndoable(byKey(e), back);
      }
    });
    $("copyBtn").addEventListener("click", function () {
      var inp = $("shareOut");
      var done = function () { toast(t("copied")); };
      try {
        navigator.clipboard.writeText(inp.value).then(done, function () { inp.select(); });
      } catch (err) { inp.select(); }
    });
    $("nativeShareBtn").addEventListener("click", function () {
      try { navigator.share({ title: "Bakul Murah", url: $("shareOut").value }).catch(function () {}); } catch (err) { /* not supported */ }
    });
    $("shareOut").addEventListener("focus", function (e) { e.target.select(); });
    // The shared-list bar goes away once used: focus moves to the list (or to Undo, for keyboard users). The bar is
    // shown on both tabs: on the price changes, focus goes to that tab's heading (the list is not shown there).
    var toList = function () { return tabHead(); };
    $("importYes").addEventListener("click", function (e) {
      // Replace (or load into an empty list); the previous list can be brought back with Undo.
      var prev = S.basket;
      S.basket = S.shared; saveBasket(); S.planK = null; clearSharedParam(); renderAll();
      if (prev.length) {
        toast(t("replacedList"), t("undo"), function () { S.basket = prev; saveBasket(); renderAll(); focusEl(tabHead()); }, toList);
        afterUndoable(byKey(e), toList);
      } else { toast(tn("addedN", S.basket.length, { n: S.basket.length })); focusEl(tabHead(), !byKey(e)); }
    });
    $("importAdd").addEventListener("click", function (e) {
      // Items already on the list keep the larger amount (opening your own link must not double it). The toast says
      // what changed: items added, amounts raised, or nothing. Undo keeps the same lines (only amounts are changed
      // here), so they are not renamed again (followArea) and keep their switches.
      var prev = S.basket.slice(), prevQty = prev.map(function (b) { return b.qty; }), added = 0, raised = 0;
      S.shared.forEach(function (l) {
        var ex = lineOf(l.code);
        if (ex) {
          var q = normQty(l.code, l.qty);
          if (q > ex.qty) { ex.qty = q; raised++; }
          return;
        }
        if (addItem(l.code, true, { qty: l.qty, opts: l.opts, flex: !!(l.alt && l.alt.length), brand: l.any === "brand" })) added++;
      });
      saveBasket(); S.planK = null; clearSharedParam(); renderAll();
      if (!added && !raised) { toast(t("importNone")); focusEl(tabHead(), !byKey(e)); return; }
      var said = [];
      if (added) said.push(tn("addedN", added, { n: added }));
      if (raised) said.push(tn("raisedN", raised, { n: raised }));
      toast(said.join(". "), t("undo"), function () {
        prev.forEach(function (b, i) { b.qty = prevQty[i]; });
        S.basket = prev; saveBasket(); renderAll(); focusEl(tabHead());
      }, toList);
      afterUndoable(byKey(e), toList);
    });
    $("importNo").addEventListener("click", function (e) { clearSharedParam(); focusEl(tabHead(), !byKey(e)); });
    $("splitCard").addEventListener("click", function (e) {
      var b = e.target.closest("[data-k]"); if (!b) return;
      S.planK = Number(b.dataset.k); S.planOpen = true; renderRanking();
    });
    $("splitCard").addEventListener("change", function (e) {
      if (e.target.id !== "stopCost") return;
      S.stopCost = okCost(e.target.value); store.set("stopCost", S.stopCost); S.planK = null; S.planOpen = true; renderRanking();
    });
    // Remember whether the plan / average cards are open across re-renders.
    $("splitCard").addEventListener("toggle", function (e) { if (e.target.id === "planDet") S.planOpen = e.target.open; }, true);
    $("avgCard").addEventListener("toggle", function (e) { if (e.target.id === "avgDet") S.avgOpen = e.target.open; }, true);
    $("clearBtn").addEventListener("click", function (e) {
      var prev = S.basket;
      S.basket = []; saveBasket(); renderAll();
      toast(t("cleared"), t("undo"), function () { S.basket = prev; saveBasket(); renderAll(); focusEl($("clearBtn")); }, toList);
      afterUndoable(byKey(e), toList);
    });
    $("toastAct").addEventListener("click", function (e) {
      var f = S.undo, dl = $("toast").closest("dialog");
      hideToast(true, true);   // the Undo itself puts focus back on the control it restores
      if (f) f();
      // That control may be behind the open dialog the toast is in (the page behind a dialog takes no focus), or on
      // the tab not shown (a line's controls, with the price changes shown): keyboard focus then stays in the dialog
      // (dlgHome), or goes to the heading of the tab shown.
      if (focusLost()) focusEl(dl && dl.open ? dlgHome(dl) : tabHead(), !byKey(e));
    });
    // Tab, Shift+Tab or Esc on Undo go back to where the shopper was (Esc also closes the toast), or to the heading of
    // the tab shown when that place is not shown (a line's controls, with the price changes shown). In a dialog, Esc
    // is left to close the dialog, and Tab moves on inside it as usual when that place is behind the dialog.
    $("toastAct").addEventListener("keydown", function (e) {
      var esc = e.key === "Escape" || e.key === "Esc", dl = $("toast").closest("dialog");
      if (!(e.key === "Tab" || (esc && !dl))) return;
      var where = S.toastBack, back = where && where();
      if (dl && (!shown(back) || !dl.contains(back))) return;
      e.preventDefault();
      // Closing an Undo unused may draw the list again (undoGone): the place to go back to is found again after it.
      if (esc) { hideToast(true); back = where && where(); }
      focusEl(shown(back) ? back : tabHead());
    });
    $("toast").addEventListener("mouseenter", function () { TOAST.hover = true; toastTimer(); });
    $("toast").addEventListener("mouseleave", function () { TOAST.hover = false; toastTimer(); });
    $("toast").addEventListener("focusin", function () { TOAST.focus = true; toastTimer(); });
    $("toast").addEventListener("focusout", function () { TOAST.focus = false; toastTimer(); });
    $("moreBtn").addEventListener("click", function () {
      if (!S.d || !S.idx) return;
      S.showAll = !S.showAll; var rows = rankRows(); if (rows.length) renderRankList(rows);
    });
    document.querySelectorAll("[data-tab]").forEach(function (b) { b.addEventListener("click", function () { setTab(b.dataset.tab); }); });
    document.querySelector(".tabs").addEventListener("keydown", function (e) {
      var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-tab]")), i = tabs.indexOf(document.activeElement), n = tabs.length;
      if (i < 0) return;
      var j = e.key === "ArrowRight" || e.key === "Right" ? (i + 1) % n : e.key === "ArrowLeft" || e.key === "Left" ? (i + n - 1) % n
            : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : -1;
      if (j < 0) return;
      e.preventDefault();
      setTab(tabs[j].dataset.tab, true);
    });
    document.querySelectorAll("[data-dir]").forEach(function (b) { b.addEventListener("click", function () { S.dir = b.dataset.dir; renderFeed(); }); });
    document.querySelectorAll("[data-scope]").forEach(function (b) { b.addEventListener("click", function () { S.scope = b.dataset.scope; renderFeed(); }); });
    document.querySelectorAll("[data-lang]").forEach(function (b) {
      b.addEventListener("click", function () { S.lang = okLang(b.dataset.lang); store.set("lang", S.lang); if (S.meta) renderAll(); else applyLang(); });
    });
    $("themeBtn").addEventListener("click", function () {
      S.theme = isDark() ? "light" : "dark"; store.set("theme", S.theme); applyTheme();
    });
    // When the page follows the phone's theme, the button's state follows it too.
    var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    if (mq && mq.addEventListener) mq.addEventListener("change", applyTheme); else if (mq && mq.addListener) mq.addListener(applyTheme);
    $("dlgClose").addEventListener("click", function () { closeDlg("itemDlg"); });
    $("itemDlg").addEventListener("click", function (e) { if (e.target === e.currentTarget) closeDlg("itemDlg"); });
    $("itemDlg").addEventListener("close", function () { S.dlgCode = null; });
    // Esc closes a dialog here (not by the browser), so focus is put back in the same moment; the close event
    // covers any other way a dialog closes.
    ["itemDlg", "pasteDlg", "listsDlg"].forEach(function (id) {
      $(id).addEventListener("keydown", function (e) {
        if ((e.key === "Escape" || e.key === "Esc") && !e.defaultPrevented && !e.isComposing) { e.preventDefault(); closeDlg(id); }
      });
      $(id).addEventListener("close", function () { afterDlgClose(id); });
    });
    $("dlgAdd").addEventListener("click", function () {
      if ($("dlgAdd").getAttribute("aria-disabled") === "true") return;
      addItem(S.dlgCode); updateDlgAdd(); renderAll();
    });
    var rt;
    window.addEventListener("resize", function () {
      clearTimeout(rt);
      rt = setTimeout(function () {
        // Only redraw when the chart width really changed (mobile fires resize on scroll and dialog open).
        if ($("dlgChart").clientWidth !== chartW) drawChart();
      }, 150);
    });
    $("resultBar").addEventListener("click", function (e) {
      // Like a skip link: the bar hides once the results are in view, so focus moves to them.
      e.preventDefault(); $("summary").scrollIntoView({ behavior: smooth(), block: "start" });
      focusEl($("summary"), true);
    });
    // Hide the bar while the results are on screen.
    if ("IntersectionObserver" in window) {
      var seen = new Set();
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) { if (en.isIntersecting) seen.add(en.target.id); else seen.delete(en.target.id); });
        S.resultsInView = seen.size > 0; updateBar();
      });
      ["summary", "splitCard", "avgCard", "rankCard"].forEach(function (id) { io.observe($(id)); });
    }
  }

  // ---------- start ----------
  function start() {
    $("whereNote").textContent = t("loadingPrices");
    return loadData("meta").then(function (meta) {
      hideError();
      try { boot(meta); } catch (e) {
        // Not a connection problem: say so plainly instead of blaming the shopper's connection.
        console.error(e);
        showError("pageFail", function () { location.reload(); });
      }
    }, function (e) {
      console.error(e);
      showError("loadFail", start);
      $("whereNote").textContent = "";
    });
  }
  function boot(meta) {
    S.meta = meta;
    meta.items.forEach(function (r) {
      var it = { code: r[0], item: r[1], unit: r[2], item_group: r[3], item_category: r[4], shops: r[5] || 0 };
      it._hay = norm(it.item + " " + it.unit + " " + it.item_category + " " + it.item_group);
      S.items.set(it.code, it);
    });
    $("srcLink").href = meta.source_url; $("licLink").href = meta.licence_url;
    if (meta.geo) $("geoLink").href = meta.geo.source_url;
    S.cat = new LP.Catalogue(Array.from(S.items.values()), reach);   // the reader ranks items as the search does
    // Older saved baskets: whole-number amounts where they should be; items no longer in the data dropped.
    S.basket = knownLines(S.basket);
    // First visit: start with "Near me" when shop locations exist; nothing loads until a place is chosen.
    // A shopper of an older version (a saved district and a list, but no saved mode) keeps their district.
    if (S.mode !== "near" && S.mode !== "district") {
      S.mode = meta.geo && !(knownSlug(S.slug) && (S.basket.length || S.lists.length)) ? "near" : "district";
    }
    if (S.mode === "near" && !meta.geo) S.mode = "district";
    if (!knownSlug(S.slug)) S.slug = null;
    fillStates();
    checkShared();
    renderAll();
    if (S.mode === "district" && S.slug) selectDistrict(S.slug);
    else if (S.mode === "near" && navigator.permissions && navigator.permissions.query) {
      // Locate straight away only if this site already has permission. The location is never stored.
      navigator.permissions.query({ name: "geolocation" }).then(function (st) { if (st.state === "granted" && !S.near) locate(); }, function () {});
    }
  }
  applyTheme(); applyLang(); bind(); start();

  window.__bakul = S; // for tests and debugging
})();
