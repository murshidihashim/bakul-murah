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

  // A radius saved by an older version may be text or a value no longer offered.
  function okRadius(r) { r = Number(r); return [3, 5, 10, 20].indexOf(r) >= 0 ? r : 5; }

  var S = {
    lang: store.get("lang", (navigator.language || "en").toLowerCase().indexOf("ms") === 0 ? "ms" : "en"),
    theme: store.get("theme", null),
    slug: store.get("slug", null),
    basket: store.get("basket", []),
    tab: "basket", dir: "rises", scope: "district", showAll: false,
    stopCost: store.get("stopCost", 5), planK: null, planOpen: null, avgOpen: false, lists: store.get("lists", []),
    // "Near me": the mode and radius are remembered; the location itself stays in memory only.
    // mode is null on a first visit and is set once the data has loaded ("near" when shop locations exist).
    mode: store.get("mode", null), radius: okRadius(store.get("radius", 5)), near: null, nearMsg: "",
    meta: null, items: new Map(), d: null, idx: null, dlgCode: null, cat: null, matches: [], shared: null,
    seq: 0, retry: null, undo: null, resultsInView: false
  };

  // ---------- helpers ----------
  function t(key, vars) {
    var s = (I18N[S.lang] && I18N[S.lang][key]) || I18N.en[key] || key;
    if (vars) Object.keys(vars).forEach(function (k) { s = s.split("{" + k + "}").join(vars[k]); });
    return s;
  }
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
  function distKind(p) {
    var x = premise(p);
    if (x.km == null || x.prec === "district") return "unknown";
    return x.prec === "postcode" || x.prec === "town" ? "approx" : "exact";
  }
  function distText(p) {
    if (!S.d || !S.d.near) return "";
    var x = premise(p), k = distKind(p);
    if (k === "unknown") return t("locUnknown", { d: x.district || S.d.district });
    if (k === "approx") return t("kmApprox", { k: Math.max(1, Math.round(x.km)) });
    return t("kmAway", { k: x.km.toFixed(1) });
  }
  function distTag(p) {
    var s = distText(p); if (!s) return "";
    var k = distKind(p);
    return '<span class="dist' + (k === "exact" ? "" : " " + k) + '">' + esc(s) + "</span>";
  }
  function freshTag(maxDays) {
    if (maxDays <= 7) return '<span class="fresh">' + esc(t("freshWeek")) + "</span>";
    return '<span class="fresh' + (maxDays > 14 ? " old" : "") + '">' + esc(t("freshDays", { d: maxDays })) + "</span>";
  }
  function toast(msg, actLabel, actFn) {
    var el = $("toast"), btn = $("toastAct");
    $("toastMsg").textContent = msg;
    btn.hidden = !actFn; btn.textContent = actLabel || "";
    S.undo = actFn || null;
    el.classList.add("on");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove("on"); S.undo = null; }, actFn ? 6000 : 1800);
  }
  function showError(msg, retryFn) {
    S.retry = retryFn || null;
    $("errMsg").textContent = msg;
    $("errRetry").hidden = !retryFn;
    $("errBox").hidden = false;
  }
  function hideError() { $("errBox").hidden = true; S.retry = null; }

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
    S.slug = slug; store.set("slug", slug); S.showAll = false; S.planK = null;
    if (S.mode === "near") { if (S.near) loadNear(); return Promise.resolve(); }
    var seq = ++S.seq;
    $("whereNote").textContent = t("loadingPrices");
    return loadData("d/" + slug).then(function (d) {
      if (seq !== S.seq) return;            // a newer choice was made meanwhile
      hideError();
      S.d = d; S.idx = B.indexDistrict(d);
      renderAll();
    }).catch(function (e) {
      if (seq !== S.seq) return;
      console.error(e);
      showError(t("loadFail"), function () { selectDistrict(slug); });
      renderWhere();
    });
  }

  // ---------- search ----------
  function norm(s) { return s.toLowerCase().normalize("NFKD").replace(/[^\w\s.%]/g, " "); }
  // Shops pricing an item: in the chosen area, or in Malaysia before an area is chosen.
  function coverage(code) {
    if (S.idx) { var m = S.idx.get(code); return m ? m.size : 0; }
    var it = S.items.get(code); return it ? it.shops || 0 : 0;
  }
  function areaMedian(code) {
    var m = S.idx && S.idx.get(code); if (!m) return null;
    var xs = []; m.forEach(function (v) { xs.push(v.p); }); return B.median(xs);
  }
  function nationalLatest(code) {
    var h = S.meta && S.meta.national_history && S.meta.national_history[code], v = null;
    if (h) h.forEach(function (x) { if (x != null) v = x; });
    return v;
  }
  // Typical price shown next to an item, with the place it applies to.
  function typicalText(code) {
    if (!S.d) { var n = nationalLatest(code); return n == null ? "" : t("typicalMy", { p: rm(n) }); }
    var med = areaMedian(code);
    return med == null ? t("notHere") : t("lineInfo", { p: rm(med), n: coverage(code) });
  }
  function search(q) {
    var toks = norm(q).split(/\s+/).filter(Boolean);
    if (!toks.length) return [];
    var out = [];
    S.items.forEach(function (it) {
      var hay = it._hay, ok = true;
      for (var i = 0; i < toks.length; i++) if (hay.indexOf(toks[i]) < 0) { ok = false; break; }
      if (!ok) return;
      var score = coverage(it.code) * 2 + (hay.indexOf(toks[0]) === 0 ? 50 : 0);
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
      if (S.d) side = cov ? "RM " + rm(areaMedian(it.code)) + "<small>" + t("shopsHave", { n: cov }) + "</small>" : "<small>" + t("notHere") + "</small>";
      else side = nationalLatest(it.code) != null ? "RM " + rm(nationalLatest(it.code)) + "<small>" + t("shopsMy", { n: cov }) + "</small>" : "";
      return '<li><button type="button" role="option" class="res' + (S.d && !cov ? " dim" : "") + '" data-add="' + it.code + '"' + (inB ? ' aria-selected="true"' : "") + ">" +
        '<span class="res-main"><span class="res-name">' + esc(titleCase(cleanItemName(it))) + "</span>" +
        '<span class="res-sub"><span class="unit">' + esc(it.unit) + "</span> · " + esc(titleCase(it.item_category || it.item_group)) + "</span></span>" +
        '<span class="res-side">' + side + "</span>" +
        '<span class="plus" aria-hidden="true">' + (inB ? CHECK_SVG : "+") + "</span></button></li>";
    }).join("");
    ul.hidden = !res.length;
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
  function removeWithUndo(code) {
    var at = -1, line = null;
    S.basket.forEach(function (b, i) { if (b.code === code) { at = i; line = b; } });
    if (!line) return;
    removeItem(code); renderAll();
    toast(t("removed", { x: itemTitle(code) }), t("undo"), function () {
      if (inBasket(code)) return;
      S.basket.splice(Math.min(at, S.basket.length), 0, line); saveBasket(); renderAll();
    });
  }
  function setQty(code, q) {
    S.basket.forEach(function (b) { if (b.code === code) b.qty = normQty(code, q); });
    saveBasket();
  }
  function toggleFlex(code) {
    var b = lineOf(code); if (!b || !b.opts) return;
    if (b.alt && b.alt.length) delete b.alt; else b.alt = b.opts.slice();
    saveBasket();
  }

  // Cheaper pack of the same product in this area (e.g. 5 kg vs 2 kg of the same oil), by typical price per kg/l.
  function cheaperPack(b) {
    if (!S.idx || (b.alt && b.alt.length)) return null;
    var it = S.items.get(b.code), u = B.parseUnit(it.unit), med = areaMedian(b.code);
    if (!u || med == null || u.base === "pc") return null;
    var mine = B.unitPrice(med, u), best = null;
    S.items.forEach(function (o) {
      if (o.code === b.code || o.item !== it.item || coverage(o.code) < 2) return;
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
  function addStarter() {
    var added = 0;
    S.cat.matchText(STARTER).forEach(function (m) {
      if (m.code != null && addItem(m.code, true, { qty: m.qty, opts: m.alt, flex: m.flex, brand: m.flex })) added++;
    });
    renderAll();
    toast(t("addedN", { n: added }));
  }

  function renderBasket() {
    var ul = $("basketList");
    // Drop items that no longer exist in the data (after a refresh).
    S.basket = S.basket.filter(function (b) { return S.items.has(b.code); });
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
      return '<li class="bi"><div class="bi-row">' +
        '<button type="button" class="bi-name" data-hist="' + b.code + '" aria-label="' + esc(t("history") + ": " + name) + '">' +
        "<span>" + esc(name) + "</span>" +
        '<span class="res-sub"><span class="unit">' + esc(it.unit) + "</span> · " + esc(typicalText(b.code)) +
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
  }
  function kpi(total) { return '<div class="kpis"><div><span>' + t("total") + "</span><b>RM " + rm(total) + "</b></div></div>"; }

  function renderRanking() {
    if (!S.d || !S.basket.length) { hideResults(); return; }
    var n = S.basket.length, rows = B.rankStores(S.idx, S.d.premises.length, S.basket);
    if (!rows.length) {
      // Nothing on the list is priced here: say so plainly and offer a way out.
      hideResults();
      $("emptyResult").hidden = false;
      $("emptyMsg").textContent = t("emptyResult", { a: areaPhrase() });
      $("widerBtn").hidden = !(S.d.near && S.radius < 20);
      $("pickDistrictBtn").hidden = !S.d.near;
      $("tryNearBtn").hidden = !!(S.d.near || !(S.meta && S.meta.geo));
      return;
    }
    $("emptyResult").hidden = true;
    $("summary").hidden = false;
    $("rankCard").hidden = false;
    S.hasResult = true;

    var sum = B.summarise(rows, n), area = esc(areaPhrase()), h = "";
    var best = sum.best, shopHtml = best ? "<span class='shop'>" + esc(premise(best.pidx).name) + "</span>" : "";
    if (sum.kind === "save" && sum.fullCount >= 3 && (sum.save >= 0.5 || sum.max - best.total >= 0.5)) {
      // Yardstick: the middle-priced shop; the priciest when the middle one ties the best.
      var vsMax = sum.save < 0.5;
      h = '<p class="big">' + t("sumHead", { save: rm(vsMax ? sum.max - best.total : sum.save), shop: shopHtml }) + "</p>" +
          '<p class="muted">' + (vsMax ? t("sumSubMax", { d: area, max: rm(sum.max), k: sum.fullCount })
                                       : t("sumSub", { d: area, med: rm(sum.median), max: rm(sum.max) })) + "</p>" + kpi(best.total);
    } else if (sum.kind === "save" && sum.fullCount >= 3) {
      h = '<p class="big">' + t("sumClose", { d: area, k: sum.fullCount, x: rm(sum.max - best.total) }) + "</p>" + kpi(best.total);
    } else if (sum.kind === "save") {
      // Only 2 shops have everything: name the cheaper one, no savings headline from so little data.
      h = '<p class="big">' + t("sumFew", { k: sum.fullCount, shop: shopHtml }) + "</p>" + kpi(best.total);
    } else if (sum.kind === "only") {
      h = '<p class="big">' + t("sumOnly", { shop: shopHtml }) + "</p>" + kpi(best.total);
    } else if (sum.kind === "none") {
      h = '<p class="big">' + t("sumNone", { h: best.have, n: best.n }) + "</p>";
    }
    h += '<p class="src-line">' + esc(t("srcLine", { d: fmtDate(S.d.data_date || S.meta.data_date) })) + "</p>";
    $("summary").innerHTML = h;

    renderRankList(rows);
    renderPlan(rows);
    renderAverage(rows);

    var topFull = rows[0].have === n;
    $("rbLabel").textContent = topFull ? t("barLabel") : t("barLabelPartial");
    $("rbShop").textContent = premise(rows[0].pidx).name;
    $("rbTotal").textContent = "RM " + rm(rows[0].total);
    updateBar();
  }

  function renderRankList(rows) {
    var n = S.basket.length, top = rows[0], topFull = top.have === n, LIM = 6;
    var shown = S.showAll ? rows : rows.slice(0, LIM);
    $("rankList").innerHTML = shown.map(function (r, i) {
      var p = premise(r.pidx), full = r.have === n;
      var names = r.missing.slice(0, 3).map(itemTitle);
      if (r.missing.length > 3) names.push(t("andMore", { n: r.missing.length - 3 }));
      var miss = r.missing.length ? '<span class="miss">' + esc(t("missing", { x: names.join(", ") })) + "</span>" : "";
      var tag = "";
      if (i === 0) tag = full ? '<span class="tag good">' + t("best") + "</span>" : '<span class="tag most">' + t("mostItems", { h: r.have, n: n }) + "</span>";
      else if (full && topFull) tag = '<span class="diff">' + (r.total - top.total < 0.005 ? t("tie") : t("vsBest", { x: rm(r.total - top.total) })) + "</span>";
      else if (!full) {
        // Compare on the items this shop does have: what does the top shop charge for the same items?
        var sh = topFull && B.sharedTotals ? B.sharedTotals(S.idx, S.basket, r.pidx, top.pidx) : null;
        tag = sh && sh.n === r.have ? '<span class="diff">' + t("sharedVs", { y: rm(sh.b) }) + "</span>"
                                    : '<span class="diff">' + t("forN", { h: r.have }) + "</span>";
      }
      return '<li class="' + (full ? "" : "partial") + '" data-p="' + p.code + '">' +
        '<span class="rk">' + (i + 1) + "</span>" +
        '<div class="rmain"><div class="rname">' + esc(p.name) + "</div>" +
        '<div class="rsub">' + distTag(r.pidx) + '<span class="tag">' + esc(p.type) + "</span>" +
        '<span class="cov"><i style="width:' + Math.round(100 * r.have / n) + '%"></i></span>' +
        "<span>" + t("has", { h: r.have, n: n }) + "</span>" + freshTag(r.maxDays) + "</div>" + miss + "</div>" +
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
    var msg;
    if (k === 1) {
      var two = po.options.filter(function (x) { return x.k > 1 && x.have === one.have && x.saving > 0; })[0];
      msg = two && po.rec === 1 ? t("planOneLow", { x: rm(two.saving), c: c }) : t("planOne");
    } else if (o.have > one.have) msg = t("planCover", { h: o.have, n: n });
    else if (k === po.rec) msg = t("planSaveRec", { x: rm(o.saving), c: c });
    else msg = t("planSaveNot", { x: rm(o.saving), c: c });
    var cols = o.shops.map(function (p) {
      var lines = S.basket.filter(function (b) { return o.assign[b.code] === p; });
      return '<div class="split-col"><h3>' + t("buyAt", { shop: esc(premise(p).name) }) + '</h3><p class="fine">' + esc(premise(p).type) +
        (distText(p) ? " · " + esc(distText(p)) : "") + "</p><ul>" +
        lines.map(function (b) {
          var e = B.priceAt(S.idx, b, p);
          return "<li><span>" + esc(lineLabel(b, e.code)) + (e.d > 7 ? ' <small class="age">' + esc(t("daysShort", { d: e.d })) + "</small>" : "") +
            "</span><b>" + rm(e.p * b.qty) + "</b></li>";
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
    var n = S.basket.length, full = ar.filter(function (r) { return r.have === n; });
    var list = (full.length ? full : ar).slice(0, 3);
    if (!list.length) { card.hidden = true; return; }
    var am = d.avg_months, today = rows[0], avg1 = list[0];
    var note = today && today.have === avg1.have ? (today.pidx === avg1.pidx ? t("avgSame", { x: esc(premise(today.pidx).name) })
      : t("avgDiff", { x: esc(premise(today.pidx).name), y: esc(premise(avg1.pidx).name) })) : "";
    card.innerHTML = '<details id="avgDet"' + (S.avgOpen ? " open" : "") + "><summary><span>" +
      esc(t("avgSum", { x: premise(avg1.pidx).name })) + "</span></summary>" +
      '<p class="muted">' + esc(t("avgSub", { a: monLabel(am[0], true), b: monLabel(am[am.length - 1], true) })) + "</p>" +
      '<ol class="rank avg-list">' + list.map(function (r, i) {
        var p = premise(r.pidx);
        return '<li class="' + (r.have === n ? "" : "partial") + '"><span class="rk">' + (i + 1) + '</span><div class="rmain"><div class="rname">' + esc(p.name) + "</div>" +
          '<div class="rsub">' + distTag(r.pidx) + '<span class="tag">' + esc(p.type) + "</span><span>" +
          esc(r.cheapestMonths ? t("avgRow", { c: r.cheapestMonths, m: r.fullMonths }) : t("avgRow0")) + "</span>" +
          (r.have < n ? "<span>" + t("has", { h: r.have, n: n }) + "</span>" : "") + "</div></div>" +
          '<div class="rtot"><b>RM ' + rm(r.total) + "</b></div></li>";
      }).join("") + "</ol>" +
      (note ? '<p class="plan-msg">' + note + "</p>" : "") +
      '<p class="fine">' + t("avgNote") + "</p></details>";
    card.hidden = false;
  }

  function updateBar() {
    $("resultBar").hidden = !(S.tab === "basket" && S.basket.length && S.d && S.hasResult && !S.resultsInView);
  }

  // ---------- paste / upload a list ----------
  function openDlg(id) { var d = $(id); if (!d.open) { if (d.showModal) d.showModal(); else d.setAttribute("open", ""); } }
  function openPaste() {
    $("pasteStep").hidden = false; $("reviewStep").hidden = true; $("ocrStatus").textContent = "";
    openDlg("pasteDlg");
  }
  function runMatch() {
    var text = $("pasteIn").value;
    S.matches = S.cat.matchText(text).map(function (m) { m.use = m.code != null; m.canFlex = m.flex; m.orig = m.code; return m; });
    if (!S.matches.length) { $("ocrStatus").textContent = t("pasteEmpty"); return; }
    renderMatches();
    $("pasteStep").hidden = true; $("reviewStep").hidden = false;
    $("pasteDlg").querySelector(".sheet-inner").scrollTop = 0;
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
      var it = S.items.get(m.code), kg = isPerKg(m.code), size = unitOf(m.code);
      // Pack size and what the amount adds up to, outside the select so it is never cut off.
      var pack = '<span class="unit">' + esc(it.unit) + "</span>";
      if (!kg && size) pack += " · " + esc(t(m.qty === 1 ? "pack1" : "packsN", { n: fmtQty(m.qty) })) +
        (m.qty !== 1 ? " = " + esc(fmtAmt(m.qty * size.amt, size.base)) : "");
      var qty = '<label class="m-qty"><span>' + t("qtyLabel") + '</span><input type="number" inputmode="decimal" min="' + (kg ? 0.5 : 1) +
        '" max="50" step="' + (kg ? 0.5 : 1) + '" value="' + fmtQty(m.qty) + '" data-mq="' + i + '"><small>' + (kg ? "kg" : esc(t(m.qty === 1 ? "packWord1" : "packWord"))) + "</small></label>";
      var flex = m.alt && m.alt.length ? '<label class="m-flex"><input type="checkbox" data-mf="' + i + '"' + (m.flex ? " checked" : "") + "> " +
        esc(t(m.canFlex ? "anyBrand" : "anyGrade", { n: m.alt.length + 1 })) + "</label>" : "";
      var note = m.note === "ekor" ? t("noteEkor") : m.note === "size" ? t("noteSize") : "";
      var guess = m.status === "pick";
      return '<li class="m' + (guess ? " guess" : "") + (m.use ? "" : " off") + '"><div class="m-raw">' +
        (guess ? '<b class="q">' + esc(t("checkThis")) + "</b> " : "") + esc(m.raw) + "</div>" +
        sel + '<div class="m-pack">' + pack + "</div>" + '<div class="m-row">' + qty + flex + "</div>" +
        (note ? '<div class="m-sub">' + esc(note) + "</div>" : "") + "</li>";
    }).join("");
    var n = S.matches.filter(function (m) { return m.use; }).length;
    $("addMatchesBtn").textContent = t("addN", { n: n });
    $("addMatchesBtn").disabled = !n;
  }
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
    toast(t("addedN", { n: added }));
    if (added) $("basketList").scrollIntoView({ behavior: "smooth", block: "start" });
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
  function decodeList(str) {
    if (!str || !/^[0-9~._bBgG-]+$/.test(str)) return [];
    var out = [];
    str.split("_").forEach(function (part) {
      var bits = part.split("~"), codes = bits[0].split("-").map(Number).filter(function (c) { return S.items.has(c); });
      if (!codes.length || out.some(function (b) { return b.code === codes[0]; })) return;
      var flag = bits[2] || (codes.length > 1 ? "b" : "");
      var line = { code: codes[0], qty: normQty(codes[0], bits[1]) };
      if (codes.length > 1) {
        line.opts = codes.slice(1);
        if (flag.toLowerCase() === "b") line.any = "brand";
        if (flag === "b" || flag === "g") line.alt = codes.slice(1);
      }
      out.push(line);
    });
    return out.slice(0, 80);
  }
  function shareUrl() {
    var u = location.href.split("#")[0].split("?")[0];
    return u + "?l=" + encodeList(S.basket);
  }
  function openLists() {
    renderLists();
    $("shareOut").value = S.basket.length ? shareUrl() : "";
    $("copyBtn").disabled = !S.basket.length;
    $("nativeShareBtn").hidden = !(navigator.share && S.basket.length);
    openDlg("listsDlg");
  }
  function renderLists() {
    $("savedLists").innerHTML = S.lists.length ? S.lists.map(function (l, i) {
      return "<li><span><b>" + esc(l.name) + "</b><small>" + t("itemsN", { n: l.basket.length }) + " · " + esc(l.at) + "</small></span>" +
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
    var m = location.search.match(/[?&]l=([0-9~._bBgG-]+)/) || location.hash.match(/[#&]l=([0-9~._bBgG-]+)/);
    var lines = m ? decodeList(m[1]) : [];
    if (!lines.length) return;
    S.shared = lines;
    renderImportBar();
    $("importBar").hidden = false;
  }
  function renderImportBar() {
    if (!S.shared) return;
    var has = S.basket.length > 0;
    $("importMsg").textContent = t("importMsg", { n: S.shared.length });
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
      s: national ? "Malaysia" : (S.d.near ? t("nearYouShort") : S.d.district) }) : "";
    var rows = feed ? feed[S.dir] : [];
    if (!rows || !rows.length) { $("feedList").innerHTML = '<li class="empty">' + t("feedEmpty") + "</li>"; return; }
    var maxAbs = Math.max.apply(null, rows.map(function (r) { return Math.abs(r[1]); }));
    $("feedList").innerHTML = rows.slice(0, 20).map(function (r, i) {
      var it = S.items.get(r[0]); if (!it) return "";
      var up = r[1] > 0, diff = r[4] - r[3];
      return '<li><button type="button" class="fi" data-hist="' + r[0] + '">' +
        '<span class="rk">' + (i + 1) + "</span>" +
        '<span class="fmain"><span class="res-name">' + esc(titleCase(cleanItemName(it))) + "</span>" +
        '<span class="res-sub"><span class="unit">' + esc(it.unit) + "</span> · RM " + rm(r[3]) + " → RM " + rm(r[4]) +
        " (" + (diff >= 0 ? "+" : "−") + "RM " + rm(Math.abs(diff)) + ") · " + t("shopsN", { n: r[2] }) + "</span>" +
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
    var cheap = S.idx ? B.cheapestFor(S.idx, code) : [];
    var first = null, last = null;
    (dh.length ? dh : (S.meta.national_history[code] || [])).forEach(function (v) { if (v != null) { if (first == null) first = v; last = v; } });
    var chg = first && last ? ((last / first - 1) * 100) : null;
    $("dlgStats").innerHTML =
      "<div><span>" + (S.d ? t("medianHere") : t("medianMy")) + "</span><b>RM " + rm(S.d ? areaMedian(code) : nationalLatest(code)) + "</b></div>" +
      (S.d ? "<div><span>" + t("lowHere") + "</span><b>RM " + rm(cheap.length ? cheap[0].p : null) + "</b></div>" : "") +
      "<div><span>" + t("yearChg") + '</span><b class="' + (chg > 0 ? "up" : chg < 0 ? "down" : "") + '">' +
      (chg == null ? "-" : (chg > 0 ? "+" : "") + chg.toFixed(1) + "%") + "</b></div>";
    $("dlgLegend").innerHTML = (S.d ? '<span><i class="sw s1"></i>' + esc(t("lineDistrict", { d: S.d.district })) + "</span>" : "") +
                               '<span><i class="sw s2"></i>' + esc(t("lineNational")) + "</span>";
    $("cheapHead").textContent = S.d ? (S.d.near ? t("cheapestNear") : t("cheapestHere")) : t("cheapestPick");
    $("dlgShops").innerHTML = !S.d ? "<li>" + t("chooseAreaShort") + "</li>" : cheap.length ? cheap.slice(0, 5).map(function (c) {
      var p = premise(c.pidx);
      return "<li><span>" + esc(p.name) + "<small>" + (distText(c.pidx) ? esc(distText(c.pidx)) + " · " : "") + esc(p.type) + " · " +
        (c.d === 0 ? t("today") : t("daysAgo", { n: c.d })) + "</small></span><b>RM " + rm(c.p) + "</b></li>";
    }).join("") : "<li>" + t("noData") + "</li>";
    updateDlgAdd();
    var dlg = $("itemDlg");
    if (!dlg.open) { if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", ""); }
    drawChart();
  }
  var chartW = 0;
  function drawChart() {
    var code = S.dlgCode; if (code == null) return;
    var ms = (S.d && S.d.months) || S.meta.months;
    var none = ms.map(function () { return null; }), series = [];
    if (S.d) series.push({ name: S.d.district, values: (S.d.history[code] || none), cls: "s1" });
    series.push({ name: "Malaysia", values: (S.meta.national_history[code] || none), cls: "s2" });
    chartW = $("dlgChart").clientWidth;
    window.MiniChart.lineChart($("dlgChart"), {
      labels: ms.map(function (m) { return monLabel(m); }),
      series: series,
      fmt: function (v) { return "RM " + v.toFixed(2); },
      label: t("history") + ": " + itemTitle(code),
      empty: t("noData")
    });
  }
  function updateDlgAdd() {
    var b = $("dlgAdd"), inB = inBasket(S.dlgCode);
    b.textContent = inB ? t("inList") : t("addToList");
    b.disabled = inB;
  }

  // ---------- render all ----------
  function renderHero() {
    var m = S.meta, pr = $("heroProof");
    if (!m) { pr.hidden = true; return; }
    $("heroSrc").textContent = t("heroSrc", { d: fmtDate(m.data_date) });
    var proof = m.proof, slug = currentSlug(), v = proof && proof.by_district && slug ? proof.by_district[slug] : null;
    if (!v && proof && proof.by_district) { slug = proof.example; v = slug ? proof.by_district[slug] : null; }
    // Only real, local numbers: shown when this week's data has them and the gap is at least RM 1.
    if (!v || v[1] - v[0] < 1) { pr.hidden = true; return; }
    pr.textContent = t("heroProof", { n: proof.items.length, a: areaName(slug), c: rm(v[0]), t: rm(v[1]) });
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
    renderImportBar();
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
    var note;
    if (!S.meta) note = t("loadingPrices");
    else if (near && S.locating) note = t("nearLocating");
    else if (near && !(S.d && S.d.near)) note = S.nearMsg || t("nearAsk");
    else if (S.d && S.d.near) {
      var nr = S.d.near, located = nr.located != null ? nr.located : S.d.premises.length;
      note = (S.nearMsg ? S.nearMsg + " " : "") + t(located === 1 ? "nearCount1" : "nearCount", { n: located, r: nr.radius }) +
        (nr.unlocated ? " " + t("nearUnknown", { u: nr.unlocated, d: nr.home || S.d.district }) : "") + " " + t("nearPrivate");
    } else if (S.d) {
      var np = S.d.premises.length;
      note = t(np === 1 ? "whereNote1" : "whereNote", { p: np, i: Object.keys(S.d.prices).length });
    } else note = t("chooseArea");
    $("whereNote").textContent = note;
    var thin = S.d && S.d.premises.length <= 5;
    $("thinBadge").hidden = !thin;
    if (thin) $("thinBadge").textContent = t(S.d.near ? "thinNear" : "thinDistrict", { n: S.d.premises.length });
  }
  function setMode(mode) {
    if (S.mode === mode && mode === "district" && S.d && !S.d.near) return;
    S.mode = mode; store.set("mode", mode); S.showAll = false; S.planK = null;
    hideError();
    if (mode === "district") {
      S.nearMsg = "";
      var v = $("districtSel").value;
      if (v) selectDistrict(v); else { ++S.seq; clearArea(); renderAll(); }
    } else if (S.near) loadNear();
    else { ++S.seq; clearArea(); renderAll(); }
  }
  function locate() {
    if (!navigator.geolocation) { S.nearMsg = t("nearNoFix"); renderWhere(); return; }
    S.locating = true; renderWhere();
    navigator.geolocation.getCurrentPosition(function (pos) {
      S.locating = false;
      S.near = { lat: pos.coords.latitude, lon: pos.coords.longitude, pc: null, level: 6 };
      S.nearMsg = ""; loadNear();
    }, function (err) {
      S.locating = false;
      // 1 = blocked by the shopper; 2/3 = no fix or too slow.
      S.nearMsg = err && err.code === 1 ? t("nearDenied") + " " + t("nearDeniedHow") : t("nearNoFix");
      renderWhere(); $("pcIn").focus();
    }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 });
  }
  function usePostcode(pc) {
    pc = String(pc || "").trim();
    if (!/^\d{5}$/.test(pc)) { S.nearMsg = t("nearPcBad"); renderWhere(); return; }
    var hit = NEAR.locatePostcode(pc, S.meta.geo.postcodes);
    if (!hit) { S.nearMsg = t("nearPcUnknown"); renderWhere(); return; }
    var level = hit.level || (hit.exact ? 5 : 3);
    S.near = { lat: hit.lat, lon: hit.lon, pc: pc, level: level };
    S.nearMsg = ""; loadNear();
  }
  var RADII = [3, 5, 10, 20];
  // Load only the district files that can have shops inside the radius; widen step by step if under 3 located shops.
  function loadNear() {
    var loc = S.near; if (!loc) return;
    var seq = ++S.seq, want = S.radius, steps = RADII.filter(function (r) { return r >= want; });
    if (!steps.length) steps = [20];
    function tryStep(i) {
      var r = steps[i], list = NEAR.districtsWithin(S.meta.areas, loc.lat, loc.lon, r).slice(0, 30);
      if (seq === S.seq) $("whereNote").textContent = t("nearLoadingK", { k: list.length });
      return Promise.all(list.map(function (x) { return loadData("d/" + x.slug); })).then(function (files) {
        var merged = files.length ? NEAR.mergeNear(files, loc.lat, loc.lon, r) : null;
        var located = merged ? (merged.near && merged.near.located != null ? merged.near.located : merged.premises.length) : 0;
        if (located < 3 && i + 1 < steps.length) return tryStep(i + 1);
        return { merged: merged, r: r };
      });
    }
    tryStep(0).then(function (res) {
      if (seq !== S.seq || S.mode !== "near" || S.near !== loc) return;   // a newer choice was made meanwhile
      hideError();
      var parts = [];
      if (loc.pc) parts.push(t(loc.level >= 5 ? "nearPcNote" : loc.level >= 3 ? "nearPcArea" : "nearPcWide", { pc: loc.pc }));
      if (!res.merged || !res.merged.premises.length) {
        clearArea(); S.nearMsg = parts.concat([t("nearNone")]).join(" "); renderAll(); return;
      }
      if (res.r !== want) parts.push(t("nearWider", { r0: want, r: res.r }));
      S.nearMsg = parts.join(" ");
      S.d = res.merged; S.idx = B.indexDistrict(res.merged); S.showAll = false;
      renderAll();
    }).catch(function (e) {
      if (seq !== S.seq) return;
      console.error(e);
      S.nearMsg = t("loadFail");
      showError(t("loadFail"), loadNear);
      renderWhere();
    });
  }

  function setTab(tab) {
    S.tab = tab;
    document.querySelectorAll("[data-tab]").forEach(function (b) { b.setAttribute("aria-selected", String(b.dataset.tab === tab)); });
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
    $("widerBtn").addEventListener("click", function () {
      var next = RADII.filter(function (r) { return r > S.radius; })[0] || 20;
      S.radius = next; store.set("radius", next); $("radiusSel").value = String(next); loadNear();
    });
    $("pickDistrictBtn").addEventListener("click", function () {
      setMode("district"); $("stateSel").focus();
      document.querySelector(".where").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("tryNearBtn").addEventListener("click", function () {
      setMode("near");
      document.querySelector(".where").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("searchIn").addEventListener("input", renderResults);
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
      } else if (r) removeWithUndo(Number(r.dataset.rm));
      else if (f) { toggleFlex(Number(f.dataset.flex)); renderAll(); }
      else if (sw) {
        var from = Number(sw.dataset.swap), to = Number(sw.dataset.to), line = lineOf(from);
        var a = B.parseUnit(S.items.get(from).unit), bU = B.parseUnit(S.items.get(to).unit);
        var packs = Math.max(1, Math.ceil(line.qty * a.amt / bU.amt - 0.03)), target = lineOf(to);
        if (target) { target.qty = normQty(to, target.qty + packs); removeItem(from); }
        else { line.code = to; line.qty = normQty(to, packs); delete line.opts; delete line.alt; delete line.any; }
        saveBasket(); renderAll();
      }
      else if (h) openItem(Number(h.dataset.hist));
    });
    $("feedList").addEventListener("click", function (e) { var h = e.target.closest("[data-hist]"); if (h) openItem(Number(h.dataset.hist)); });
    $("starterBtn").addEventListener("click", addStarter);
    ["pasteBtn", "pasteBtn2"].forEach(function (id) { $(id).addEventListener("click", openPaste); });
    $("pasteClose").addEventListener("click", function () { $("pasteDlg").close(); });
    $("matchBtn").addEventListener("click", runMatch);
    $("backBtn").addEventListener("click", function () { $("reviewStep").hidden = true; $("pasteStep").hidden = false; });
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
          // Keep the amount the shopper asked for: 10 kg switched to a 5 kg pack becomes 2 packs.
          var size = unitOf(m.code);
          if (m.want && size && size.base === m.want.base && LP && LP.packsFor) m.qty = normQty(m.code, LP.packsFor(m.want.amt, size));
          else m.qty = normQty(m.code, m.qty);
        }
        renderMatches();
      } else if (el.dataset.mq != null) {
        i = Number(el.dataset.mq); var v = Number(el.value);
        if (v > 0) { S.matches[i].qty = normQty(S.matches[i].code, v); renderMatches(); }
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
    $("listsClose").addEventListener("click", function () { $("listsDlg").close(); });
    $("saveForm").addEventListener("submit", function (e) { e.preventDefault(); saveList($("listName").value); });
    $("savedLists").addEventListener("click", function (e) {
      var l = e.target.closest("[data-load]"), d = e.target.closest("[data-del]");
      if (l) {
        var item = S.lists[Number(l.dataset.load)];
        S.basket = JSON.parse(JSON.stringify(item.basket)); saveBasket(); S.planK = null;
        $("listsDlg").close(); renderAll(); toast(t("loadedList", { x: item.name }));
      } else if (d) {
        var at = Number(d.dataset.del), gone = S.lists[at];
        S.lists.splice(at, 1); store.set("lists", S.lists); renderLists();
        toast(t("deletedList", { x: gone.name }), t("undo"), function () {
          S.lists.splice(Math.min(at, S.lists.length), 0, gone); store.set("lists", S.lists); renderLists();
        });
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
    $("importYes").addEventListener("click", function () {
      // Replace (or load into an empty list); the previous list can be brought back with Undo.
      var prev = S.basket;
      S.basket = S.shared; saveBasket(); S.planK = null; clearSharedParam(); renderAll();
      if (prev.length) toast(t("replacedList"), t("undo"), function () { S.basket = prev; saveBasket(); renderAll(); });
      else toast(t("addedN", { n: S.basket.length }));
    });
    $("importAdd").addEventListener("click", function () {
      var added = 0;
      S.shared.forEach(function (l) {
        if (addItem(l.code, true, { qty: l.qty, opts: l.opts, flex: !!(l.alt && l.alt.length), brand: l.any === "brand" })) added++;
      });
      S.planK = null; clearSharedParam(); renderAll(); toast(t("addedN", { n: added }));
    });
    $("importNo").addEventListener("click", clearSharedParam);
    $("splitCard").addEventListener("click", function (e) {
      var b = e.target.closest("[data-k]"); if (!b) return;
      S.planK = Number(b.dataset.k); S.planOpen = true; renderRanking();
    });
    $("splitCard").addEventListener("change", function (e) {
      if (e.target.id !== "stopCost") return;
      S.stopCost = Number(e.target.value); store.set("stopCost", S.stopCost); S.planK = null; S.planOpen = true; renderRanking();
    });
    // Remember whether the plan / average cards are open across re-renders.
    $("splitCard").addEventListener("toggle", function (e) { if (e.target.id === "planDet") S.planOpen = e.target.open; }, true);
    $("avgCard").addEventListener("toggle", function (e) { if (e.target.id === "avgDet") S.avgOpen = e.target.open; }, true);
    $("clearBtn").addEventListener("click", function () {
      var prev = S.basket;
      S.basket = []; saveBasket(); renderAll();
      toast(t("cleared"), t("undo"), function () { S.basket = prev; saveBasket(); renderAll(); });
    });
    $("toastAct").addEventListener("click", function () {
      var f = S.undo; S.undo = null; $("toast").classList.remove("on"); if (f) f();
    });
    $("moreBtn").addEventListener("click", function () { S.showAll = !S.showAll; renderRankList(B.rankStores(S.idx, S.d.premises.length, S.basket)); });
    document.querySelectorAll("[data-tab]").forEach(function (b) { b.addEventListener("click", function () { setTab(b.dataset.tab); }); });
    document.querySelectorAll("[data-dir]").forEach(function (b) { b.addEventListener("click", function () { S.dir = b.dataset.dir; renderFeed(); }); });
    document.querySelectorAll("[data-scope]").forEach(function (b) { b.addEventListener("click", function () { S.scope = b.dataset.scope; renderFeed(); }); });
    document.querySelectorAll("[data-lang]").forEach(function (b) {
      b.addEventListener("click", function () { S.lang = b.dataset.lang; store.set("lang", S.lang); if (S.meta) renderAll(); else applyLang(); });
    });
    $("themeBtn").addEventListener("click", function () {
      S.theme = isDark() ? "light" : "dark"; store.set("theme", S.theme); applyTheme();
    });
    $("dlgClose").addEventListener("click", function () { $("itemDlg").close(); });
    $("itemDlg").addEventListener("click", function (e) { if (e.target === e.currentTarget) e.currentTarget.close(); });
    $("itemDlg").addEventListener("close", function () { S.dlgCode = null; });
    $("dlgAdd").addEventListener("click", function () { addItem(S.dlgCode); updateDlgAdd(); renderAll(); });
    var rt;
    window.addEventListener("resize", function () {
      clearTimeout(rt);
      rt = setTimeout(function () {
        // Only redraw when the chart width really changed (mobile fires resize on scroll and dialog open).
        if ($("dlgChart").clientWidth !== chartW) drawChart();
      }, 150);
    });
    $("resultBar").addEventListener("click", function (e) {
      e.preventDefault(); $("summary").scrollIntoView({ behavior: "smooth", block: "start" });
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
      S.meta = meta;
      meta.items.forEach(function (r) {
        var it = { code: r[0], item: r[1], unit: r[2], item_group: r[3], item_category: r[4], shops: r[5] || 0 };
        it._hay = norm(it.item + " " + it.unit + " " + it.item_category + " " + it.item_group);
        S.items.set(it.code, it);
      });
      $("srcLink").href = meta.source_url; $("licLink").href = meta.licence_url;
      if (meta.geo) $("geoLink").href = meta.geo.source_url;
      S.cat = new LP.Catalogue(Array.from(S.items.values()), coverage);
      // Older saved baskets: whole-number amounts where they should be.
      S.basket.forEach(function (b) { b.qty = normQty(b.code, b.qty); });
      // First visit: start with "Near me" when shop locations exist; nothing loads until a place is chosen.
      if (S.mode !== "near" && S.mode !== "district") S.mode = meta.geo ? "near" : "district";
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
    }).catch(function (e) {
      console.error(e);
      showError(t("loadFail"), start);
      $("whereNote").textContent = "";
    });
  }
  applyTheme(); applyLang(); bind(); start();

  window.__bakul = S; // for tests and debugging
})();
