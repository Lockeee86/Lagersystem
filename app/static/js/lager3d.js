/* 3D-Ansicht der Lagerkarte: isometrische Zeichnung aller Stapel als SVG.
 *
 * Daten (aus views.warehouse_map):
 *   { rows, slots, levels, rowLabels: ["A", …], stackUrl: ".../stapel/0/0?partial=1",
 *     stacks: [[row, slot, [[farbe, sortenId], …von unten nach oben], alt (1/0)], …] }
 */
window.Lager3D = (function () {
    "use strict";

    const SVG = "http://www.w3.org/2000/svg";
    const BLANK = "#8a8476";  // leere Kiste

    function shade(hex, f) {
        const n = parseInt(hex.slice(1), 16);
        return "rgb(" + [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
            return Math.min(255, Math.round(v * f));
        }).join(",") + ")";
    }

    function el(name, attrs) {
        const node = document.createElementNS(SVG, name);
        Object.keys(attrs).forEach((k) => { node.setAttribute(k, attrs[k]); });
        return node;
    }

    function create(container, data, opts) {
        let rot = 0, zoom = 1, filter = "", selected = null;
        const byPos = {};
        const oldPos = {};
        data.stacks.forEach((st) => { byPos[st[0] + ":" + st[1]] = st[2]; if (st[3]) oldPos[st[0] + ":" + st[1]] = 1; });

        try { rot = parseInt(localStorage.getItem("lager3dRot"), 10) || 0; } catch (e) {}

        // Reihe r / Platz s (1-basiert) → Bodenkoordinaten je nach Blickrichtung
        function place(r, s) {
            // „Erste Reihe unten“ (Verwaltung): Reihe A vorne, sonst hinten – wie in Karte und Liste
            const R = data.rows, S = data.slots, x = s - 1, y = data.bottomUp ? R - r : r - 1;
            if (rot === 1) return [R - 1 - y, x];
            if (rot === 2) return [S - 1 - x, R - 1 - y];
            if (rot === 3) return [y, S - 1 - x];
            return [x, y];
        }

        function render() {
            const W = rot % 2 ? data.rows : data.slots;
            const H = rot % 2 ? data.slots : data.rows;
            const m = 24;
            const fit = (container.clientWidth - 2 * m) / (W + H);
            const a = Math.max(5, Math.min(30, fit)) * zoom;   // halbe Kachelbreite
            const hh = a * 1.15;                               // Kistenhöhe
            const ox = H * a + m, oy = data.levels * hh + m;
            const w = Math.round((W + H) * a + 2 * m);
            const h = Math.round((W + H) * a / 2 + data.levels * hh + 2 * m);

            function iso(px, py, z) {
                return ((px - py) * a + ox).toFixed(1) + "," + ((px + py) * a / 2 - z * hh + oy).toFixed(1);
            }

            const svg = el("svg", { width: w, height: h, viewBox: "0 0 " + w + " " + h, role: "img", "aria-label": container.dataset.aria || "" });
            const cells = [];
            for (let r = 1; r <= data.rows; r++) {
                for (let s = 1; s <= data.slots; s++) {
                    const p = place(r, s);
                    cells.push({ r, s, gx: p[0], gy: p[1] });
                }
            }
            // Von hinten nach vorne zeichnen, damit vordere Stapel hintere verdecken
            cells.sort((c, d) => { return (c.gx + c.gy) - (d.gx + d.gy) || c.gx - d.gx; });

            cells.forEach((c) => {
                const sel = selected === c.r + ":" + c.s;
                svg.appendChild(el("polygon", {
                    points: [iso(c.gx, c.gy, 0), iso(c.gx + 1, c.gy, 0), iso(c.gx + 1, c.gy + 1, 0), iso(c.gx, c.gy + 1, 0)].join(" "),
                    "class": "iso-floor" + (sel ? " is-selected" : ""), "data-r": c.r, "data-s": c.s
                }));
            });

            const inset = 0.07;
            cells.forEach((c) => {
                const boxes = byPos[c.r + ":" + c.s];
                if (!boxes) return;
                const sel = selected === c.r + ":" + c.s;
                const full = boxes.length >= data.levels;
                const g = el("g", { "data-r": c.r, "data-s": c.s, "class": "iso-stack" + (sel ? " is-selected" : "") });
                boxes.forEach((b, l) => {
                    const color = b[0] || BLANK;
                    const dim = filter && (filter === "free" ? full : filter === "old" ? !oldPos[c.r + ":" + c.s]
                                                                   : "v" + b[1] !== filter);
                    const x0 = c.gx + inset, x1 = c.gx + 1 - inset, y0 = c.gy + inset, y1 = c.gy + 1 - inset;
                    const z0 = l + 0.05, z1 = l + 0.95;
                    const attrs = dim ? { opacity: "0.15" } : {};
                    const fill = b[2] == null ? 100 : b[2];
                    const prism = function (top, extra) {
                        [
                            [[iso(x0, y1, top), iso(x1, y1, top), iso(x1, y1, z0), iso(x0, y1, z0)], shade(color, 0.72)],
                            [[iso(x1, y0, top), iso(x1, y1, top), iso(x1, y1, z0), iso(x1, y0, z0)], shade(color, 0.52)],
                            [[iso(x0, y0, top), iso(x1, y0, top), iso(x1, y1, top), iso(x0, y1, top)], color]
                        ].forEach((face) => {
                            const poly = el("polygon", Object.assign({ points: face[0].join(" "), fill: face[1] }, attrs, extra || {}));
                            g.appendChild(poly);
                        });
                    };
                    if (fill > 0 && fill < 100) {
                        // angebrochen: durchsichtige Kiste mit gestricheltem Rand, Ware nur so hoch wie der Füllstand
                        prism(z0 + (z1 - z0) * fill / 100);
                        prism(z1, { "fill-opacity": dim ? "0" : "0.16", stroke: shade(color, 1.15), "stroke-width": "1.2",
                                    "stroke-dasharray": "3 2", "class": "iso-part" });
                    } else {
                        prism(z1);
                    }
                });
                svg.appendChild(g);
            });

            // Reihenbuchstaben zuletzt, damit kein Stapel sie verdeckt
            for (let i = 0; i < data.rows; i++) {
                const lp = place(i + 1, 0);
                const pt = iso(lp[0] + 0.5, lp[1] + 0.5, 0).split(",");
                const t = el("text", { x: pt[0], y: pt[1], "class": "iso-label", "text-anchor": "middle", "dominant-baseline": "middle" });
                t.textContent = data.rowLabels[i];
                svg.appendChild(t);
            }

            container.replaceChildren(svg);
            if (opts.label) opts.label.textContent = opts.views[rot];
        }

        container.addEventListener("click", (e) => {
            const hit = e.target.closest("[data-r]");
            if (!hit) return;
            selected = hit.dataset.r + ":" + hit.dataset.s;
            render();
            opts.onStack(data.stackUrl.replace("/stapel/0/0", "/stapel/" + hit.dataset.r + "/" + hit.dataset.s));
        });

        let resizeTimer = null;
        window.addEventListener("resize", () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(render, 150);
        });

        render();

        return {
            rotate () {
                rot = (rot + 1) % 4;
                try { localStorage.setItem("lager3dRot", String(rot)); } catch (e) {}
                render();
            },
            zoom (factor) {
                zoom = Math.max(0.6, Math.min(3, zoom * factor));
                render();
            },
            setFilter (f) {
                filter = f;
                render();
            }
        };
    }

    return { create };
})();
