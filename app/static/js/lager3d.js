/* 3D-Ansicht der Lagerkarte: isometrische Zeichnung aller Stapel als SVG.
 *
 * Daten (aus views.warehouse_map):
 *   { rows, slots, levels, rowLabels: ["A", …], stackUrl: ".../stapel/0/0?partial=1",
 *     stacks: [[row, slot, [[farbe, sortenId], …von unten nach oben], alt (1/0)], …] }
 */
window.Lager3D = (function () {
    "use strict";

    var SVG = "http://www.w3.org/2000/svg";
    var BLANK = "#8a8476";  // leere Kiste

    function shade(hex, f) {
        var n = parseInt(hex.slice(1), 16);
        return "rgb(" + [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (v) {
            return Math.round(v * f);
        }).join(",") + ")";
    }

    function el(name, attrs) {
        var node = document.createElementNS(SVG, name);
        Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
        return node;
    }

    function create(container, data, opts) {
        var rot = 0, zoom = 1, filter = "", selected = null;
        var byPos = {};
        var oldPos = {};
        data.stacks.forEach(function (st) { byPos[st[0] + ":" + st[1]] = st[2]; if (st[3]) oldPos[st[0] + ":" + st[1]] = 1; });

        try { rot = parseInt(localStorage.getItem("lager3dRot"), 10) || 0; } catch (e) {}

        // Reihe r / Platz s (1-basiert) → Bodenkoordinaten je nach Blickrichtung
        function place(r, s) {
            // „Erste Reihe unten“ (Verwaltung): Reihe A vorne, sonst hinten – wie in Karte und Liste
            var R = data.rows, S = data.slots, x = s - 1, y = data.bottomUp ? R - r : r - 1;
            if (rot === 1) return [R - 1 - y, x];
            if (rot === 2) return [S - 1 - x, R - 1 - y];
            if (rot === 3) return [y, S - 1 - x];
            return [x, y];
        }

        function render() {
            var W = rot % 2 ? data.rows : data.slots;
            var H = rot % 2 ? data.slots : data.rows;
            var m = 24;
            var fit = (container.clientWidth - 2 * m) / (W + H);
            var a = Math.max(5, Math.min(30, fit)) * zoom;   // halbe Kachelbreite
            var hh = a * 1.15;                               // Kistenhöhe
            var ox = H * a + m, oy = data.levels * hh + m;
            var w = Math.round((W + H) * a + 2 * m);
            var h = Math.round((W + H) * a / 2 + data.levels * hh + 2 * m);

            function iso(px, py, z) {
                return ((px - py) * a + ox).toFixed(1) + "," + ((px + py) * a / 2 - z * hh + oy).toFixed(1);
            }

            var svg = el("svg", { width: w, height: h, viewBox: "0 0 " + w + " " + h, role: "img", "aria-label": container.dataset.aria || "" });
            var cells = [];
            for (var r = 1; r <= data.rows; r++) {
                for (var s = 1; s <= data.slots; s++) {
                    var p = place(r, s);
                    cells.push({ r: r, s: s, gx: p[0], gy: p[1] });
                }
            }
            // Von hinten nach vorne zeichnen, damit vordere Stapel hintere verdecken
            cells.sort(function (c, d) { return (c.gx + c.gy) - (d.gx + d.gy) || c.gx - d.gx; });

            cells.forEach(function (c) {
                var sel = selected === c.r + ":" + c.s;
                svg.appendChild(el("polygon", {
                    points: [iso(c.gx, c.gy, 0), iso(c.gx + 1, c.gy, 0), iso(c.gx + 1, c.gy + 1, 0), iso(c.gx, c.gy + 1, 0)].join(" "),
                    "class": "iso-floor" + (sel ? " is-selected" : ""), "data-r": c.r, "data-s": c.s
                }));
            });

            var inset = 0.07;
            cells.forEach(function (c) {
                var boxes = byPos[c.r + ":" + c.s];
                if (!boxes) return;
                var sel = selected === c.r + ":" + c.s;
                var full = boxes.length >= data.levels;
                var g = el("g", { "data-r": c.r, "data-s": c.s, "class": "iso-stack" + (sel ? " is-selected" : "") });
                boxes.forEach(function (b, l) {
                    var color = b[0] || BLANK;
                    var dim = filter && (filter === "free" ? full : filter === "old" ? !oldPos[c.r + ":" + c.s]
                                                                   : "v" + b[1] !== filter);
                    var x0 = c.gx + inset, x1 = c.gx + 1 - inset, y0 = c.gy + inset, y1 = c.gy + 1 - inset;
                    var z0 = l + 0.05, z1 = l + 0.95;
                    var attrs = dim ? { opacity: "0.15" } : {};
                    [
                        [[iso(x0, y1, z1), iso(x1, y1, z1), iso(x1, y1, z0), iso(x0, y1, z0)], shade(color, 0.72)],
                        [[iso(x1, y0, z1), iso(x1, y1, z1), iso(x1, y1, z0), iso(x1, y0, z0)], shade(color, 0.52)],
                        [[iso(x0, y0, z1), iso(x1, y0, z1), iso(x1, y1, z1), iso(x0, y1, z1)], color]
                    ].forEach(function (face) {
                        var poly = el("polygon", Object.assign({ points: face[0].join(" "), fill: face[1] }, attrs));
                        g.appendChild(poly);
                    });
                });
                svg.appendChild(g);
            });

            // Reihenbuchstaben zuletzt, damit kein Stapel sie verdeckt
            for (var i = 0; i < data.rows; i++) {
                var lp = place(i + 1, 0);
                var pt = iso(lp[0] + 0.5, lp[1] + 0.5, 0).split(",");
                var t = el("text", { x: pt[0], y: pt[1], "class": "iso-label", "text-anchor": "middle", "dominant-baseline": "middle" });
                t.textContent = data.rowLabels[i];
                svg.appendChild(t);
            }

            container.replaceChildren(svg);
            if (opts.label) opts.label.textContent = opts.views[rot];
        }

        container.addEventListener("click", function (e) {
            var hit = e.target.closest("[data-r]");
            if (!hit) return;
            selected = hit.dataset.r + ":" + hit.dataset.s;
            render();
            opts.onStack(data.stackUrl.replace("/stapel/0/0", "/stapel/" + hit.dataset.r + "/" + hit.dataset.s));
        });

        var resizeTimer = null;
        window.addEventListener("resize", function () {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(render, 150);
        });

        render();

        return {
            rotate: function () {
                rot = (rot + 1) % 4;
                try { localStorage.setItem("lager3dRot", String(rot)); } catch (e) {}
                render();
            },
            zoom: function (factor) {
                zoom = Math.max(0.6, Math.min(3, zoom * factor));
                render();
            },
            setFilter: function (f) {
                filter = f;
                render();
            }
        };
    }

    return { create: create };
})();
