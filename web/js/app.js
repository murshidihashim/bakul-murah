/* Bakul Murah app. Plain JS, no build step. */
(function () {
  "use strict";
  var B = window.Basket, I18N = window.I18N;
  var $ = function (id) { return document.getElementById(id); };

  // ---------- storage (per-device conveniences only) ----------
  var store = {
    get: function (k, d) { try { var v = localStorage.getItem("bakul." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem("bakul." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  };

  var S = {
    lang: store.get("lang", (navigator.language || "en").toLowerCase().indexOf("ms") === 0 ? "ms" : "en"),
    theme: store.get("theme", null),
    slug: store.get("slug", null),
    basket: store.get("basket", []),
    tab: "basket", dir: "rises", scope: "district", showAll: false,
    stopCost: store.get("stopCost", 5), planK: null, lists: store.get("lists", []),
    // "Near me": the mode and radius are remembered; the location itself is kept in memory only.
    mode: store.get("mode", "district"), radius: store.get("radius", 5), near: null, nearMsg: "",
    meta: null, items: new Map(), d: null, idx: null, dlgCode: null, cat: null, matches: [], shared: null
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
  function fmtQty(q) { return String(Math.round(q * 100) / 100); }
  function isPerKg(code) { var it = S.items.get(code); return !!it && /^1\s*kg$/i.test(it.unit.trim()); }
  function lineOf(code) { return S.basket.filter(function (b) { return b.code === code; })[0]; }
  function saveBasket() { store.set("basket", S.basket); }
  function itemName(code) { var it = S.items.get(code); return it ? it.item : "#" + code; }
  function titleCase(s) {
    // Data is in capitals. Soften for reading, keep short words like "A" and units intact.
    return s.toLowerCase().replace(/(^|[\s(\/-])([a-z])/g, function (_, a, b) { return a + b.toUpperCase(); });
  }
  function premise(p) {
    var r = S.d.premises[p];
    return { code: r[0], name: r[1], type: r[2], addr: r[3], prec: r[6], km: r[7], district: r[8] };
  }
  function areaPhrase() { return S.d.near ? t("nearYou", { r: S.d.near.radius }) : t("inDistrict", { d: S.d.district }); }
  // Distance label for a shop in "near me" mode ("2.3 km", or "around Petaling" when only the district is known).
  function distText(p) {
    if (!S.d.near) return "";
    var x = premise(p);
    return x.prec === "district" ? t("approxIn", { d: x.district }) : t("kmAway", { k: x.km.toFixed(1) });
  }
  function distTag(p) { var s = distText(p); return s ? '<span class="dist">' + esc(s) + "</span>" : ""; }
  function toast(msg) {
    var el = $("toast"); el.textContent = msg; el.classList.add("on");
    clearTimeout(toast._t); toast._t = setTimeout(function () { el.classList.remove("on"); }, 1800);
  }

  function loadData(key) {
    return new Promise(function (resolve, reject) {
      window.PCDATA = window.PCDATA || {};
      if (window.PCDATA[key]) return resolve(window.PCDATA[key]);
      var s = document.createElement("script");
      s.src = "data/" + key + ".js";
      s.onload = function () { window.PCDATA[key] ? resolve(window.PCDATA[key]) : reject(new Error(key)); };
      s.onerror = function () { reject(new Error("load " + key)); };
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
    document.querySelectorAll("[data-lang]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.lang === S.lang)); });
    $("dlgClose").setAttribute("aria-label", t("close"));
  }

  // ---------- location ----------
  function fillStates() {
    var sel = $("stateSel"), areas = S.meta.areas;
    sel.innerHTML = Object.keys(areas).map(function (st) { return "<option>" + esc(st) + "</option>"; }).join("");
    var found = null;
    Object.keys(areas).forEach(function (st) { areas[st].forEach(function (a) { if (a.slug === S.slug) found = st; }); });
    sel.value = found || Object.keys(areas)[0];
    return fillDistricts();
  }
  function fillDistricts() {
    var list = S.meta.areas[$("stateSel").value] || [];
    var sel = $("districtSel");
    sel.innerHTML = list.map(function (a) { return '<option value="' + esc(a.slug) + '">' + esc(a.district) + "</option>"; }).join("");
    if (list.some(function (a) { return a.slug === S.slug; })) sel.value = S.slug;
    else if (list.length) {
      // Default to the district with most shops in this state.
      sel.value = list.slice().sort(function (a, b) { return b.premises - a.premises; })[0].slug;
    }
    return selectDistrict(sel.value);
  }
  function selectDistrict(slug) {
    S.slug = slug; store.set("slug", slug); S.showAll = false;
    if (S.mode === "near" && S.near && S.meta.geo) { loadNear(); return Promise.resolve(); }
    return loadData("d/" + slug).then(function (d) {
      S.d = d; S.idx = B.indexDistrict(d);
      renderAll();
    }).catch(function () { toast(t("loadFail")); });
  }

  // ---------- search ----------
  function norm(s) { return s.toLowerCase().normalize("NFKD").replace(/[^\w\s.%]/g, " "); }
  function coverage(code) { var m = S.idx && S.idx.get(code); return m ? m.size : 0; }
  function districtMedian(code) {
    var m = S.idx && S.idx.get(code); if (!m) return null;
    var xs = []; m.forEach(function (v) { xs.push(v.p); }); return B.median(xs);
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
  function renderResults() {
    var q = $("searchIn").value, ul = $("results");
    var res = search(q);
    ul.innerHTML = res.map(function (it) {
      var cov = coverage(it.code), med = districtMedian(it.code), inB = inBasket(it.code);
      return '<li><button type="button" role="option" class="res' + (cov ? "" : " dim") + '" data-add="' + it.code + '"' + (inB ? ' aria-selected="true"' : "") + ">" +
        '<span class="res-main"><span class="res-name">' + esc(titleCase(it.item)) + "</span>" +
        '<span class="res-sub"><span class="unit">' + esc(it.unit) + "</span> · " + esc(titleCase(it.item_category || it.item_group)) + "</span></span>" +
        '<span class="res-side">' + (cov ? "RM " + rm(med) + "<small>" + t("shopsHave", { n: cov }) + "</small>" : "<small>" + t("notHere") + "</small>") + "</span>" +
        '<span class="plus" aria-hidden="true">' + (inB ? '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>' : "+") + "</span></button></li>";
    }).join("");
    ul.hidden = !res.length;
  }

  // ---------- basket ----------
  function inBasket(code) { return S.basket.some(function (b) { return b.code === code; }); }
  /** Add an item. o: {qty, opts: [alternative codes], flex: bool}. Adds to the amount if already listed. */
  function addItem(code, quiet, o) {
    o = o || {};
    var ex = lineOf(code);
    if (ex) {
      if (!o.qty) return false;
      ex.qty = Math.min(50, ex.qty + o.qty); saveBasket(); return true;
    }
    var line = { code: code, qty: o.qty || 1 };
    if (o.opts && o.opts.length) {
      line.opts = o.opts.slice(0, 30);
      if (o.brand) line.any = "brand";
      if (o.flex) line.alt = line.opts.slice();
    }
    S.basket.push(line); saveBasket();
    if (!quiet) toast(t("added", { x: titleCase(itemName(code)) }));
    return true;
  }
  function removeItem(code) {
    S.basket = S.basket.filter(function (b) { return b.code !== code; }); store.set("basket", S.basket);
  }
  function setQty(code, q) {
    var min = isPerKg(code) ? 0.5 : 1;
    S.basket.forEach(function (b) { if (b.code === code) b.qty = Math.max(min, Math.min(50, Math.round(q * 2) / 2)); });
    saveBasket();
  }
  function toggleFlex(code) {
    var b = lineOf(code); if (!b || !b.opts) return;
    if (b.alt && b.alt.length) delete b.alt; else b.alt = b.opts.slice();
    saveBasket();
  }

  // Cheaper pack of the same product in this district (e.g. 5 kg vs 2 kg of the same oil), by median price per kg/l.
  function cheaperPack(b) {
    if (b.alt && b.alt.length) return null;
    var it = S.items.get(b.code), u = B.parseUnit(it.unit), med = districtMedian(b.code);
    if (!u || med == null || u.base === "pc") return null;
    var mine = B.unitPrice(med, u), best = null;
    S.items.forEach(function (o) {
      if (o.code === b.code || o.item !== it.item || coverage(o.code) < 2) return;
      var ou = B.parseUnit(o.unit); if (!ou || ou.base !== u.base) return;
      var up = B.unitPrice(districtMedian(o.code), ou);
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
    ul.innerHTML = S.basket.map(function (b) {
      var it = S.items.get(b.code), med = districtMedian(b.code), kg = isPerKg(b.code);
      var u = B.parseUnit(it.unit), up = med != null && u && !(u.base === "g" && u.amt === 1000) && u.base !== "pc" ? B.unitPrice(med, u) : null;
      var flexOn = b.alt && b.alt.length, step = kg ? 0.5 : 1;
      var flexBtn = b.opts && b.opts.length ? '<button type="button" class="flex-tag' + (flexOn ? " on" : "") + '" data-flex="' + b.code + '" aria-pressed="' + (flexOn ? "true" : "false") + '">' +
        esc(t(b.any === "brand" ? "anyBrand" : "anyGrade", { n: b.opts.length + 1 })) + "</button>" : "";
      var tip = cheaperPack(b), tipHtml = "";
      if (tip) {
        tipHtml = '<div class="tip-line">' + esc(t("cheaperPack", { size: tip.item.unit, u: unitLabel(tip.per.per), a: rm(tip.per.v), b: rm(tip.mine.v) })) +
          ' <button type="button" class="link-btn" data-swap="' + b.code + '" data-to="' + tip.item.code + '">' + t("switchPack") + "</button></div>";
      }
      return '<li class="bi"><div class="bi-row">' +
        '<button type="button" class="bi-name" data-hist="' + b.code + '" aria-label="' + esc(t("history") + ": " + titleCase(it.item)) + '">' +
        '<span>' + esc(titleCase(it.item)) + "</span>" +
        '<span class="res-sub"><span class="unit">' + esc(it.unit) + "</span> · " + (med == null ? t("notHere") : "RM " + rm(med)) +
        (up ? " · " + esc(t("perUnit", { v: rm(up.v), u: unitLabel(up.per) })) : "") + "</span></button>" +
        '<div class="qty" role="group" aria-label="' + esc(t("qty")) + '">' +
        '<button type="button" data-qty="' + b.code + '" data-d="-' + step + '" aria-label="-">−</button>' +
        "<output>" + fmtQty(b.qty) + (kg ? '<small>kg</small>' : "") + "</output>" +
        '<button type="button" data-qty="' + b.code + '" data-d="' + step + '" aria-label="+">+</button></div>' +
        '<button type="button" class="x" data-rm="' + b.code + '" aria-label="' + esc(t("remove")) + '">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>' +
        (flexBtn || tipHtml ? '<div class="bi-extra">' + flexBtn + tipHtml + "</div>" : "") + "</li>";
    }).join("");
  }

  function renderRanking() {
    var has = S.basket.length > 0 && S.d;
    $("summary").hidden = $("rankCard").hidden = !has;
    $("splitCard").hidden = true;
    $("avgCard").hidden = true;
    if (!has) return;
    var rows = B.rankStores(S.idx, S.d.premises.length, S.basket);
    var sum = B.summarise(rows, S.basket.length);
    var dname = esc(areaPhrase());

    var h = "";
    var shopHtml = sum.best ? "<span class='shop'>" + esc(premise(sum.best.pidx).name) + "</span>" : "";
    if (sum.kind === "save" && (sum.save >= 0.5 || sum.max - sum.best.total >= 0.5)) {
      // Prefer the median shop as the yardstick; fall back to the priciest when the median ties the best.
      var vsMax = sum.save < 0.5;
      h = '<p class="big">' + t("sumHead", { save: rm(vsMax ? sum.max - sum.best.total : sum.save), shop: shopHtml }) + "</p>" +
          '<p class="muted">' + (vsMax ? t("sumSubMax", { d: dname, max: rm(sum.max), k: sum.fullCount })
                                       : t("sumSub", { d: dname, med: rm(sum.median), max: rm(sum.max) })) + "</p>" +
          '<div class="kpis"><div><span>' + t("total") + "</span><b>RM " + rm(sum.best.total) + "</b></div></div>";
    } else if (sum.kind === "save") {
      h = '<p class="big">' + t("sumClose", { d: dname, k: sum.fullCount, x: rm(sum.max - sum.best.total) }) + "</p>" +
          '<div class="kpis"><div><span>' + t("total") + "</span><b>RM " + rm(sum.best.total) + "</b></div></div>";
    } else if (sum.kind === "only") {
      h = '<p class="big">' + t("sumOnly", { shop: "<span class='shop'>" + esc(premise(sum.best.pidx).name) + "</span>" }) + "</p>" +
          '<div class="kpis"><div><span>' + t("total") + "</span><b>RM " + rm(sum.best.total) + "</b></div></div>";
    } else if (sum.kind === "none") {
      h = '<p class="big">' + t("sumNone", { h: sum.best.have, n: sum.best.n }) + "</p>";
    }
    $("summary").innerHTML = h;

    renderPlan(rows);
    renderAverage(rows);

    // Ranked list.
    var n = S.basket.length, top = rows[0] ? rows[0].total : 0, LIM = 8;
    var shown = S.showAll ? rows : rows.slice(0, LIM);
    $("rankList").innerHTML = shown.map(function (r, i) {
      var p = premise(r.pidx), full = r.have === n;
      var names = r.missing.slice(0, 3).map(function (c) { return titleCase(itemName(c)); });
      if (r.missing.length > 3) names.push(t("andMore", { n: r.missing.length - 3 }));
      var miss = r.missing.length ? '<span class="miss">' + esc(t("missing", { x: names.join(", ") })) + "</span>" : "";
      var diff = i === 0 ? '<span class="tag good">' + t("best") + "</span>"
               : full && rows[0].have === n ? '<span class="diff">' + (r.total - top < 0.005 ? t("tie") : t("vsBest", { x: rm(r.total - top) })) + "</span>"
               : !full ? '<span class="diff">' + t("forN", { h: r.have }) + "</span>" : "";
      return '<li class="' + (full ? "" : "partial") + '">' +
        '<span class="rk">' + (i + 1) + "</span>" +
        '<div class="rmain"><div class="rname">' + esc(p.name) + "</div>" +
        '<div class="rsub">' + distTag(r.pidx) + '<span class="tag">' + esc(p.type) + "</span>" +
        '<span class="cov"><i style="width:' + Math.round(100 * r.have / n) + '%"></i></span>' +
        "<span>" + t("has", { h: r.have, n: n }) + "</span>" +
        (r.maxDays > 14 ? '<span class="old">' + t("old") + "</span>" : "") + "</div>" + miss + "</div>" +
        '<div class="rtot"><b>RM ' + rm(r.total) + "</b>" + diff + "</div></li>";
    }).join("");
    var more = $("moreBtn");
    more.hidden = rows.length <= LIM;
    more.textContent = S.showAll ? t("showLess") : t("showMore", { n: rows.length });

    // Mobile shortcut bar: best shop for the list.
    $("rbLabel").textContent = t("barLabel");
    $("rbShop").textContent = rows.length ? premise(rows[0].pidx).name : "";
    $("rbTotal").textContent = rows.length ? "RM " + rm(rows[0].total) : "";
    updateBar();
  }

  function lineLabel(b, code) {
    var name = titleCase(itemName(code));
    return name + (b.qty !== 1 ? " ×" + fmtQty(b.qty) + (isPerKg(code) ? " kg" : "") : "");
  }

  function renderPlan(rows) {
    var card = $("splitCard");
    var po = B.planOptions(S.idx, S.d.premises.length, S.basket, rows, S.stopCost, 60);
    if (!po.options.length || S.basket.length < 2) { card.hidden = true; return; }
    var k = S.planK && po.options.some(function (o) { return o.k === S.planK; }) ? S.planK : po.rec;
    var o = po.options.filter(function (x) { return x.k === k; })[0], one = po.options[0], n = S.basket.length;
    var seg = po.options.map(function (x) {
      return '<button type="button" data-k="' + x.k + '" aria-pressed="' + (x.k === k) + '">' +
        "<span>" + esc(t(x.k === 1 ? "stopsN" : "stopsNp", { k: x.k })) + "</span><b>RM " + rm(x.total) + "</b>" +
        (x.k === po.rec ? '<i>' + t("recTag") + "</i>" : "") + "</button>";
    }).join("");
    var c = S.stopCost, msg;
    if (k === 1) {
      var two = po.options.filter(function (x) { return x.k === 2; })[0];
      msg = two && two.have === one.have && two.saving > 0 && po.rec === 1 ? t("planOneLow", { x: rm(two.saving), c: c }) : t("planOne");
    } else if (o.have > one.have) msg = t("planCover", { h: o.have, n: n });
    else if (k === po.rec) msg = t("planSaveRec", { x: rm(o.saving), c: c });
    else msg = o.saving > 0 ? t("planSaveNot", { x: rm(o.saving), c: c }) : t("planSave", { x: rm(Math.max(0, o.saving || 0)) });
    var cols = o.shops.map(function (p) {
      var lines = S.basket.filter(function (b) { return o.assign[b.code] === p; });
      return '<div class="split-col"><h3>' + t("buyAt", { shop: esc(premise(p).name) }) + '</h3><p class="fine">' + esc(premise(p).type) +
        (distText(p) ? " · " + esc(distText(p)) : "") + "</p><ul>" +
        lines.map(function (b) {
          var e = B.priceAt(S.idx, b, p);
          return "<li><span>" + esc(lineLabel(b, e.code)) + "</span><b>" + rm(e.p * b.qty) + "</b></li>";
        }).join("") + "</ul></div>";
    }).join("");
    var miss = S.basket.filter(function (b) { return o.assign[b.code] == null; }).map(function (b) { return titleCase(itemName(b.code)); });
    var costs = [0, 2, 5, 10, 20].map(function (v) { return '<option value="' + v + '"' + (v === c ? " selected" : "") + ">RM " + v + "</option>"; }).join("");
    card.innerHTML = '<div class="card-head"><h2>' + t("planHead") + '</h2><span class="pill">RM ' + rm(o.total) + "</span></div>" +
      '<div class="seg stops" role="group" aria-label="' + esc(t("planHead")) + '">' + seg + "</div>" +
      '<p class="plan-msg">' + msg + "</p>" +
      '<div class="split-cols">' + cols + "</div>" +
      (miss.length ? '<p class="fine">' + esc(t("planMissing", { x: miss.join(", ") })) + "</p>" : "") +
      '<label class="stop-cost"><span>' + t("stopCost") + '</span><select id="stopCost">' + costs + "</select></label>";
    card.hidden = false;
    card.classList.remove("quiet");
  }

  function renderAverage(rows) {
    var card = $("avgCard"), d = S.d;
    if (!d.shop_months || !d.avg_months || !S.basket.length) { card.hidden = true; return; }
    var ar = B.averageRank(d.shop_months, d.premises.length, S.basket, d.avg_months.length);
    var n = S.basket.length, full = ar.filter(function (r) { return r.have === n; });
    var list = (full.length ? full : ar).slice(0, 3);
    if (!list.length) { card.hidden = true; return; }
    var am = d.avg_months, today = rows[0], avg1 = list[0];
    var note = today && today.have === avg1.have ? (today.pidx === avg1.pidx ? t("avgSame", { x: esc(premise(today.pidx).name) })
      : t("avgDiff", { x: esc(premise(today.pidx).name), y: esc(premise(avg1.pidx).name) })) : "";
    card.innerHTML = '<div class="card-head"><h2>' + t("avgHead") + "</h2></div>" +
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
      '<p class="fine">' + t("avgNote") + "</p>";
    card.hidden = false;
  }

  function updateBar() {
    $("resultBar").hidden = !(S.tab === "basket" && S.basket.length && S.d && !S.resultsInView);
  }

  // ---------- paste / upload a list ----------
  function openDlg(id) { var d = $(id); if (!d.open) { if (d.showModal) d.showModal(); else d.setAttribute("open", ""); } }
  function openPaste() {
    $("pasteStep").hidden = false; $("reviewStep").hidden = true; $("ocrStatus").textContent = "";
    openDlg("pasteDlg");
  }
  function runMatch() {
    var text = $("pasteIn").value;
    S.matches = S.cat.matchText(text).map(function (m) { m.use = m.code != null; m.canFlex = m.flex; return m; });
    if (!S.matches.length) { $("ocrStatus").textContent = t("pasteEmpty"); return; }
    renderMatches();
    $("pasteStep").hidden = true; $("reviewStep").hidden = false;
    $("pasteDlg").querySelector(".sheet-inner").scrollTop = 0;
  }
  function renderMatches() {
    var ok = S.matches.filter(function (m) { return m.code != null; }).length;
    $("reviewNote").textContent = t("reviewNote", { ok: ok, n: S.matches.length });
    $("matchList").innerHTML = S.matches.map(function (m, i) {
      if (m.code == null) {
        return '<li class="m none"><div class="m-raw">' + esc(m.raw) + '</div><div class="m-sub">' + t("notFound") + "</div></li>";
      }
      var opts = [m.code].concat(m.options.filter(function (c) { return c !== m.code; })).slice(0, 12);
      var sel = '<select data-mi="' + i + '" aria-label="' + esc(m.raw) + '">' + opts.map(function (c) {
          var it = S.items.get(c), cov = coverage(c);
          return '<option value="' + c + '"' + (c === m.code ? " selected" : "") + ">" + esc(titleCase(it.item)) + " · " + esc(it.unit) +
            (cov ? "" : " · " + t("notHere")) + "</option>";
        }).join("") + '<option value="skip"' + (m.use ? "" : " selected") + ">" + t("skipLine") + "</option></select>";
      var kg = isPerKg(m.code);
      var qty = '<label class="m-qty"><span>' + t("qtyLabel") + '</span><input type="number" inputmode="decimal" min="' + (kg ? 0.5 : 1) +
        '" max="50" step="' + (kg ? 0.5 : 1) + '" value="' + fmtQty(m.qty) + '" data-mq="' + i + '"><small>' + (kg ? "kg" : esc(t("packs"))) + "</small></label>";
      var flex = m.alt && m.alt.length ? '<label class="m-flex"><input type="checkbox" data-mf="' + i + '"' + (m.flex ? " checked" : "") + "> " +
        esc(t(m.canFlex ? "anyBrand" : "anyGrade", { n: m.alt.length + 1 })) + "</label>" : "";
      var note = m.note === "ekor" ? t("noteEkor") : m.note === "size" ? t("noteSize") : "";
      return '<li class="m' + (m.status === "pick" ? " guess" : "") + (m.use ? "" : " off") + '"><div class="m-raw">' + (m.status === "pick" ? '<b class="q">?</b> ' : "") + esc(m.raw) + "</div>" +
        sel + '<div class="m-row">' + qty + flex + "</div>" + (note ? '<div class="m-sub">' + esc(note) + "</div>" : "") + "</li>";
    }).join("");
    var n = S.matches.filter(function (m) { return m.use; }).length;
    $("addMatchesBtn").textContent = t("addN", { n: n });
    $("addMatchesBtn").disabled = !n;
  }
  function addMatches() {
    var added = 0;
    S.matches.forEach(function (m) {
      if (!m.use || m.code == null) return;
      var opts = m.alt;
      if (m.code !== m.orig && m.orig != null) opts = []; // shopper picked a specific item
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
  function encodeList(basket) {
    return basket.map(function (b) {
      var codes = [b.code].concat(b.alt && b.alt.length ? b.alt : []);
      return codes.join("-") + "~" + fmtQty(b.qty);
    }).join("_");
  }
  function decodeList(str) {
    if (!str || !/^[0-9~._-]+$/.test(str)) return [];
    var out = [];
    str.split("_").forEach(function (part) {
      var bits = part.split("~"), codes = bits[0].split("-").map(Number).filter(function (c) { return S.items.has(c); });
      var q = Number(bits[1]) || 1;
      if (!codes.length || out.some(function (b) { return b.code === codes[0]; })) return;
      var line = { code: codes[0], qty: Math.max(0.5, Math.min(50, q)) };
      if (codes.length > 1) { line.opts = codes.slice(1); line.alt = codes.slice(1); line.any = "brand"; }
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
      return '<li><span><b>' + esc(l.name) + "</b><small>" + t("itemsN", { n: l.basket.length }) + " · " + esc(l.at) + "</small></span>" +
        '<button type="button" class="btn small" data-load="' + i + '">' + t("loadList") + "</button>" +
        '<button type="button" class="link-btn danger" data-del="' + i + '">' + t("deleteList") + "</button></li>";
    }).join("") : '<li class="empty">' + t("noSaved") + "</li>";
    $("saveListBtn").disabled = !S.basket.length;
  }
  function saveList(name) {
    if (!S.basket.length) { toast(t("emptyList")); return; }
    name = (name || "").trim() || t("listNamePh").replace(/^(e\.g\.|cth\.)\s*/, "");
    var at = new Date().toISOString().slice(0, 10);
    S.lists = S.lists.filter(function (l) { return l.name !== name; });
    S.lists.unshift({ name: name, at: at, basket: JSON.parse(JSON.stringify(S.basket)) });
    S.lists = S.lists.slice(0, 20); store.set("lists", S.lists);
    $("listName").value = ""; renderLists(); toast(t("savedOk", { x: name }));
  }
  function checkShared() {
    var m = location.search.match(/[?&]l=([0-9~._-]+)/) || location.hash.match(/[#&]l=([0-9~._-]+)/);
    var lines = m ? decodeList(m[1]) : [];
    if (!lines.length) return;
    S.shared = lines;
    $("importMsg").textContent = t("importMsg", { n: lines.length });
    $("importBar").hidden = false;
  }
  function clearSharedParam() {
    $("importBar").hidden = true; S.shared = null;
    try { history.replaceState(null, "", location.href.split("?")[0].split("#")[0]); } catch (e) { /* not allowed here */ }
  }

  // ---------- trends feed ----------
  function renderFeed() {
    if (!S.meta || !S.d) return;
    var feed = S.scope === "national" ? S.meta.national_feed : S.d.feed;
    var ms = S.meta.months, cur = ms[ms.length - 1], prev = ms[ms.length - 2];
    document.querySelectorAll("[data-dir]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.dir === S.dir)); });
    document.querySelectorAll("[data-scope]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.scope === S.scope)); });
    $("feedNote").textContent = prev ? t("feedNote", { m: monLabel(cur, true), p: monLabel(prev, true),
      s: S.scope === "national" ? "Malaysia" : S.d.district }) : "";
    var rows = feed ? feed[S.dir] : [];
    if (!rows || !rows.length) { $("feedList").innerHTML = '<li class="empty">' + t("feedEmpty") + "</li>"; return; }
    var maxAbs = Math.max.apply(null, rows.map(function (r) { return Math.abs(r[1]); }));
    $("feedList").innerHTML = rows.slice(0, 20).map(function (r, i) {
      var it = S.items.get(r[0]); if (!it) return "";
      var up = r[1] > 0;
      return '<li><button type="button" class="fi" data-hist="' + r[0] + '">' +
        '<span class="rk">' + (i + 1) + "</span>" +
        '<span class="fmain"><span class="res-name">' + esc(titleCase(it.item)) + "</span>" +
        '<span class="res-sub"><span class="unit">' + esc(it.unit) + "</span> · RM " + rm(r[3]) + " → RM " + rm(r[4]) + " · " + t("shopsN", { n: r[2] }) + "</span>" +
        '<span class="bar ' + (up ? "up" : "down") + '"><i style="width:' + Math.max(4, Math.round(100 * Math.abs(r[1]) / maxAbs)) + '%"></i></span></span>' +
        '<span class="pct ' + (up ? "up" : "down") + '">' + (up ? "+" : "−") + Math.abs(r[1]).toFixed(1) + "%</span></button></li>";
    }).join("");
  }

  // ---------- item dialog ----------
  function openItem(code) {
    var it = S.items.get(code); if (!it) return;
    S.dlgCode = code;
    $("dlgTitle").textContent = titleCase(it.item);
    $("dlgSub").innerHTML = '<span class="unit">' + esc(it.unit) + "</span> · " + esc(titleCase(it.item_category || it.item_group));
    var dh = (S.d.history && S.d.history[code]) || [];
    var nh = (S.meta.national_history && S.meta.national_history[code]) || [];
    var cheap = B.cheapestFor(S.idx, code);
    var first = null, last = null;
    dh.forEach(function (v) { if (v != null) { if (first == null) first = v; last = v; } });
    var chg = first && last ? ((last / first - 1) * 100) : null;
    $("dlgStats").innerHTML =
      "<div><span>" + t("medianHere") + "</span><b>RM " + rm(districtMedian(code)) + "</b></div>" +
      "<div><span>" + t("lowHere") + "</span><b>RM " + rm(cheap.length ? cheap[0].p : null) + "</b></div>" +
      "<div><span>" + t("yearChg") + '</span><b class="' + (chg > 0 ? "up" : chg < 0 ? "down" : "") + '">' +
      (chg == null ? "-" : (chg > 0 ? "+" : "") + chg.toFixed(1) + "%") + "</b></div>";
    $("dlgLegend").innerHTML = '<span><i class="sw s1"></i>' + esc(t("lineDistrict", { d: S.d.district })) + "</span>" +
                               '<span><i class="sw s2"></i>' + esc(t("lineNational")) + "</span>";
    $("dlgShops").innerHTML = cheap.length ? cheap.slice(0, 5).map(function (c) {
      var p = premise(c.pidx);
      return "<li><span>" + esc(p.name) + "<small>" + (distText(c.pidx) ? esc(distText(c.pidx)) + " · " : "") + esc(p.type) + " · " + (c.d === 0 ? t("today") : t("daysAgo", { n: c.d })) + "</small></span><b>RM " + rm(c.p) + "</b></li>";
    }).join("") : "<li>" + t("noData") + "</li>";
    updateDlgAdd();
    var dlg = $("itemDlg");
    if (!dlg.open) { if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", ""); }
    drawChart();
  }
  var chartW = 0;
  function drawChart() {
    var code = S.dlgCode; if (code == null) return;
    var ms = S.d.months || S.meta.months;
    chartW = $("dlgChart").clientWidth;
    window.MiniChart.lineChart($("dlgChart"), {
      labels: ms.map(function (m) { return monLabel(m); }),
      series: [
        { name: S.d.district, values: (S.d.history[code] || ms.map(function () { return null; })), cls: "s1" },
        { name: "Malaysia", values: (S.meta.national_history[code] || ms.map(function () { return null; })), cls: "s2" }
      ],
      fmt: function (v) { return "RM " + v.toFixed(2); },
      label: t("history") + ": " + titleCase(itemName(code)),
      empty: t("noData")
    });
  }
  function updateDlgAdd() {
    var b = $("dlgAdd"), inB = inBasket(S.dlgCode);
    b.textContent = inB ? t("inList") : t("addToList");
    b.disabled = inB;
  }

  // ---------- render all ----------
  function renderAll() {
    applyLang();
    renderWhere();
    renderBasket(); renderRanking(); renderFeed();
    if ($("searchIn").value) renderResults();
    var m = S.meta;
    $("dataLine").textContent = t("dataLine", { d: m.data_date, b: m.generated_at.slice(0, 10) });
    $("synthBanner").hidden = !m.synthetic;
    if ($("itemDlg").open) openItem(S.dlgCode);
    if (S.shared) $("importMsg").textContent = t("importMsg", { n: S.shared.length });
  }

  // ---------- near me ----------
  function renderWhere() {
    var hasGeo = !!(S.meta && S.meta.geo), near = hasGeo && S.mode === "near";
    $("modeSeg").hidden = !hasGeo;
    $("nearBox").hidden = !near;
    $("geoLine").hidden = !hasGeo;
    document.querySelector(".where").classList.toggle("is-near", near);
    document.querySelectorAll("[data-mode]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === (near ? "near" : "district"))); });
    $("radiusSel").value = String(S.radius);
    if (near && !(S.d && S.d.near)) { $("whereNote").textContent = S.nearMsg || t("nearAsk"); return; }
    if (S.d && S.d.near) {
      var nd = S.d.near.districts;
      $("whereNote").textContent = (S.nearMsg ? S.nearMsg + " " : "") +
        (nd.length === 1 ? t("nearNoteOne", { n: S.d.premises.length, r: S.d.near.radius, d: nd[0] })
                         : t("nearNote", { n: S.d.premises.length, r: S.d.near.radius, k: nd.length }));
      return;
    }
    if (S.d) {
      var np = S.d.premises.length;
      $("whereNote").textContent = t(np === 1 ? "whereNote1" : "whereNote", { p: np, i: Object.keys(S.d.prices).length }) +
        (np < 4 ? " " + t("fewShops") : "");
    }
  }
  function setMode(mode) {
    S.mode = mode; store.set("mode", mode); S.showAll = false; S.planK = null;
    if (mode === "district") { S.nearMsg = ""; selectDistrict($("districtSel").value); }
    else if (S.near) loadNear();
    else renderAll();
  }
  function locate() {
    if (!navigator.geolocation) { S.nearMsg = t("nearDenied"); renderWhere(); return; }
    S.nearMsg = t("nearLocating"); renderWhere();
    navigator.geolocation.getCurrentPosition(function (pos) {
      S.near = { lat: pos.coords.latitude, lon: pos.coords.longitude, pc: null };
      S.nearMsg = ""; loadNear();
    }, function () { S.nearMsg = t("nearDenied"); renderWhere(); $("pcIn").focus(); },
    { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 });
  }
  function usePostcode(pc) {
    pc = String(pc || "").trim();
    if (!/^\d{5}$/.test(pc)) { S.nearMsg = t("nearPcBad"); renderWhere(); return; }
    var hit = window.Near.locatePostcode(pc, S.meta.geo.postcodes);
    if (!hit) { S.nearMsg = t("nearPcUnknown"); renderWhere(); return; }
    S.near = { lat: hit.lat, lon: hit.lon, pc: pc };
    S.nearMsg = ""; loadNear();
  }
  var RADII = [3, 5, 10, 20];
  // Load only the district files that can have shops inside the radius; widen step by step if under 3 shops.
  function loadNear() {
    var loc = S.near; if (!loc) return;
    $("whereNote").textContent = t("nearLoading");
    var want = S.radius, steps = RADII.filter(function (r) { return r >= want; });
    if (!steps.length) steps = [20];
    function tryStep(i) {
      var r = steps[i], slugs = window.Near.districtsWithin(S.meta.areas, loc.lat, loc.lon, r).slice(0, 30).map(function (x) { return x.slug; });
      return Promise.all(slugs.map(function (sl) { return loadData("d/" + sl); })).then(function (files) {
        var merged = files.length ? window.Near.mergeNear(files, loc.lat, loc.lon, r) : null;
        if ((!merged || merged.premises.length < 3) && i + 1 < steps.length) return tryStep(i + 1);
        return { merged: merged, r: r };
      });
    }
    tryStep(0).then(function (res) {
      if (S.mode !== "near" || S.near !== loc) return;   // the user moved on meanwhile
      if (!res.merged || !res.merged.premises.length) { S.nearMsg = t("nearNone"); renderWhere(); return; }
      var parts = [];
      if (loc.pc) parts.push(t("nearPcNote", { pc: loc.pc }));
      if (res.r !== want) parts.push(t("nearWider", { r0: want, r: res.r }));
      S.nearMsg = parts.join(" ");
      S.d = res.merged; S.idx = B.indexDistrict(res.merged); S.showAll = false;
      renderAll();
    }).catch(function () { toast(t("loadFail")); });
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
    $("stateSel").addEventListener("change", fillDistricts);
    document.querySelectorAll("[data-mode]").forEach(function (b) { b.addEventListener("click", function () { setMode(b.dataset.mode); }); });
    $("locBtn").addEventListener("click", locate);
    $("pcForm").addEventListener("submit", function (e) { e.preventDefault(); usePostcode($("pcIn").value); });
    $("radiusSel").addEventListener("change", function (e) {
      S.radius = Number(e.target.value); store.set("radius", S.radius); S.planK = null;
      if (S.near && S.mode === "near") loadNear();
    });
    $("districtSel").addEventListener("change", function (e) { selectDistrict(e.target.value); });
    $("searchIn").addEventListener("input", renderResults);
    $("searchIn").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { var f = $("results").querySelector("[data-add]"); if (f) f.click(); }
      if (e.key === "Escape") { e.target.value = ""; renderResults(); }
    });
    $("results").addEventListener("click", function (e) {
      var b = e.target.closest("[data-add]"); if (!b) return;
      var code = Number(b.dataset.add);
      if (!addItem(code)) { removeItem(code); toast(t("removed", { x: titleCase(itemName(code)) })); }
      renderAll(); $("searchIn").focus();
    });
    $("basketList").addEventListener("click", function (e) {
      var q = e.target.closest("[data-qty]"), r = e.target.closest("[data-rm]"), h = e.target.closest("[data-hist]");
      var f = e.target.closest("[data-flex]"), sw = e.target.closest("[data-swap]");
      if (q) {
        var code = Number(q.dataset.qty), cur = lineOf(code);
        setQty(code, cur.qty + Number(q.dataset.d)); renderAll();
      } else if (r) { removeItem(Number(r.dataset.rm)); renderAll(); }
      else if (f) { toggleFlex(Number(f.dataset.flex)); renderAll(); }
      else if (sw) {
        var from = Number(sw.dataset.swap), to = Number(sw.dataset.to), line = lineOf(from);
        var a = B.parseUnit(S.items.get(from).unit), bU = B.parseUnit(S.items.get(to).unit);
        if (lineOf(to)) { removeItem(from); }
        else { line.code = to; line.qty = Math.max(1, Math.ceil(line.qty * a.amt / bU.amt - 0.03)); delete line.opts; delete line.alt; saveBasket(); }
        renderAll();
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
          if (m.orig == null) m.orig = m.code;
          m.use = true; m.code = Number(el.value);
          if (m.code !== m.orig) { m.flex = false; m.alt = []; }
        }
        renderMatches();
      } else if (el.dataset.mq != null) {
        i = Number(el.dataset.mq); var v = Number(el.value);
        if (v > 0) S.matches[i].qty = Math.min(50, v);
      } else if (el.dataset.mf != null) {
        S.matches[Number(el.dataset.mf)].flex = el.checked;
      }
    });
    $("listsBtn").addEventListener("click", openLists);
    $("shareBtn").addEventListener("click", openLists);
    $("listsClose").addEventListener("click", function () { $("listsDlg").close(); });
    $("saveForm").addEventListener("submit", function (e) { e.preventDefault(); saveList($("listName").value); });
    $("savedLists").addEventListener("click", function (e) {
      var l = e.target.closest("[data-load]"), d = e.target.closest("[data-del]");
      if (l) {
        var item = S.lists[Number(l.dataset.load)];
        S.basket = JSON.parse(JSON.stringify(item.basket)); saveBasket(); S.planK = null;
        $("listsDlg").close(); renderAll(); toast(t("loadedList", { x: item.name }));
      } else if (d) {
        S.lists.splice(Number(d.dataset.del), 1); store.set("lists", S.lists); renderLists();
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
      S.basket = S.shared; saveBasket(); S.planK = null; clearSharedParam(); renderAll(); toast(t("addedN", { n: S.basket.length }));
    });
    $("importNo").addEventListener("click", clearSharedParam);
    $("splitCard").addEventListener("click", function (e) {
      var b = e.target.closest("[data-k]"); if (!b) return;
      S.planK = Number(b.dataset.k); renderRanking();
    });
    $("splitCard").addEventListener("change", function (e) {
      if (e.target.id !== "stopCost") return;
      S.stopCost = Number(e.target.value); store.set("stopCost", S.stopCost); S.planK = null; renderRanking();
    });
    $("clearBtn").addEventListener("click", function () { S.basket = []; store.set("basket", []); renderAll(); });
    $("moreBtn").addEventListener("click", function () { S.showAll = !S.showAll; renderRanking(); });
    document.querySelectorAll("[data-tab]").forEach(function (b) { b.addEventListener("click", function () { setTab(b.dataset.tab); }); });
    document.querySelectorAll("[data-dir]").forEach(function (b) { b.addEventListener("click", function () { S.dir = b.dataset.dir; renderFeed(); }); });
    document.querySelectorAll("[data-scope]").forEach(function (b) { b.addEventListener("click", function () { S.scope = b.dataset.scope; renderFeed(); }); });
    document.querySelectorAll("[data-lang]").forEach(function (b) {
      b.addEventListener("click", function () { S.lang = b.dataset.lang; store.set("lang", S.lang); renderAll(); });
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
  applyTheme(); applyLang(); bind();
  loadData("meta").then(function (meta) {
    S.meta = meta;
    meta.items.forEach(function (r) {
      var it = { code: r[0], item: r[1], unit: r[2], item_group: r[3], item_category: r[4] };
      it._hay = norm(it.item + " " + it.unit + " " + it.item_category + " " + it.item_group);
      S.items.set(it.code, it);
    });
    $("srcLink").href = meta.source_url; $("licLink").href = meta.licence_url;
    S.cat = new window.ListParse.Catalogue(Array.from(S.items.values()), coverage);
    // Older saved baskets: make sure every line has a whole-number amount where it should.
    S.basket.forEach(function (b) { if (!isPerKg(b.code)) b.qty = Math.max(1, Math.round(b.qty)); });
    checkShared();
    if (meta.geo) $("geoLink").href = meta.geo.source_url;
    return fillStates().then(function () {
      // Near-me mode: locate straight away only if this site already has permission. Never stored.
      if (S.mode === "near" && meta.geo && navigator.permissions && navigator.permissions.query) {
        navigator.permissions.query({ name: "geolocation" }).then(function (st) { if (st.state === "granted") locate(); }, function () {});
      }
    });
  }).catch(function (e) {
    $("main").insertAdjacentHTML("afterbegin", '<p class="card error">' + esc(t("loadFail")) + "</p>");
    console.error(e);
  });

  window.__bakul = S; // for tests and debugging
})();
