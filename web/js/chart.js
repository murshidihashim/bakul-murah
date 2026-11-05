/* Small inline-SVG line chart. No library. */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";

  function el(name, attrs, parent) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  function niceTicks(lo, hi, count) {
    if (lo === hi) { lo = lo * 0.9; hi = hi * 1.1 || 1; }
    var span = hi - lo, step = Math.pow(10, Math.floor(Math.log10(span / count)));
    var err = (count * step) / span;
    if (err <= 0.15) step *= 10; else if (err <= 0.35) step *= 5; else if (err <= 0.75) step *= 2;
    var t0 = Math.floor(lo / step) * step, t1 = Math.ceil(hi / step) * step, out = [];
    for (var t = t0; t <= t1 + step / 2; t += step) out.push(Math.round(t * 100) / 100);
    return out;
  }

  /**
   * opts: {labels:[...], series:[{name, values:[num|null], cls}], fmt:fn(v), label: aria text}
   */
  function lineChart(host, opts) {
    host.innerHTML = "";
    var W = Math.max(280, host.clientWidth || 320), H = W < 420 ? 200 : 230;
    var m = { t: 12, r: 14, b: 26, l: 46 };
    var all = [];
    opts.series.forEach(function (s) { s.values.forEach(function (v) { if (v != null) all.push(v); }); });
    if (!all.length) { host.textContent = opts.empty || "No data"; return; }
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    var pad = (hi - lo) * 0.15 || hi * 0.05;
    var ticks = niceTicks(Math.max(0, lo - pad), hi + pad, 4);
    var y0 = ticks[0], y1 = ticks[ticks.length - 1];
    var n = opts.labels.length;
    var x = function (i) { return m.l + (n === 1 ? (W - m.l - m.r) / 2 : i * (W - m.l - m.r) / (n - 1)); };
    var y = function (v) { return m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b); };

    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, role: "img",
                          "aria-label": opts.label || "Price chart", class: "lc" }, host);
    var g = el("g", { class: "grid" }, svg);
    ticks.forEach(function (t) {
      el("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t) }, g);
      var tx = el("text", { x: m.l - 8, y: y(t) + 4, "text-anchor": "end", class: "tick" }, g);
      tx.textContent = opts.fmt ? opts.fmt(t) : t;
    });
    var every = W < 420 ? Math.ceil(n / 4) : Math.ceil(n / 7);
    opts.labels.forEach(function (lab, i) {
      if ((n - 1 - i) % every) return;
      var tx = el("text", { x: x(i), y: H - 6, "text-anchor": "middle", class: "tick" }, svg);
      tx.textContent = lab;
    });

    opts.series.forEach(function (s) {
      var d = "", pen = false;
      s.values.forEach(function (v, i) {
        if (v == null) { pen = false; return; }
        d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1);
        pen = true;
      });
      el("path", { d: d, class: "ln " + (s.cls || "") }, svg);
      s.values.forEach(function (v, i) {
        if (v == null) return;
        var last = i === s.values.length - 1;
        el("circle", { cx: x(i), cy: y(v), r: last ? 4 : 2.5, class: "pt " + (s.cls || "") + (last ? " last" : "") }, svg);
      });
    });

    // Hover / tap readout.
    var guide = el("line", { y1: m.t, y2: H - m.b, class: "guide", visibility: "hidden" }, svg);
    var tip = document.createElement("div");
    tip.className = "tip"; tip.hidden = true; host.appendChild(tip);
    var hit = el("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" }, svg);
    var lastTouch = 0;
    function escH(t) { return String(t).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
    function show(evt) {
      if (evt.touches) lastTouch = Date.now();
      var r = svg.getBoundingClientRect();
      var px = ((evt.touches ? evt.touches[0].clientX : evt.clientX) - r.left) * (W / r.width);
      var i = Math.round((px - m.l) / ((W - m.l - m.r) / Math.max(1, n - 1)));
      i = Math.max(0, Math.min(n - 1, i));
      guide.setAttribute("x1", x(i)); guide.setAttribute("x2", x(i)); guide.setAttribute("visibility", "visible");
      var html = "<b>" + opts.labels[i] + "</b>";
      opts.series.forEach(function (s) {
        var v = s.values[i];
        html += '<span class="tip-row"><i class="sw ' + (s.cls || "") + '"></i>' + escH(s.name) + ": " +
                (v == null ? "-" : (opts.fmt ? opts.fmt(v) : v)) + "</span>";
      });
      tip.innerHTML = html; tip.hidden = false;
      var left = (x(i) / W) * r.width;
      tip.style.left = Math.min(Math.max(left, 70), r.width - 70) + "px";
    }
    function hide() {
      // Phones send fake mouse events after a tap; keep the readout a tap just opened.
      if (Date.now() - lastTouch < 1000) return;
      guide.setAttribute("visibility", "hidden"); tip.hidden = true;
    }
    hit.addEventListener("mousemove", show);
    hit.addEventListener("touchstart", show, { passive: true });
    hit.addEventListener("touchmove", show, { passive: true });
    hit.addEventListener("mouseleave", hide);
  }

  window.MiniChart = { lineChart: lineChart, niceTicks: niceTicks };
})();
