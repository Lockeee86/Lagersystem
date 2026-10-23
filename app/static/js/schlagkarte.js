/* Schlagkarte: alle Schlag-Umrisse als SVG, auf Wunsch mit Luftbild darunter.
 *
 * Daten (aus fields.map_data):
 *   [{ id, name, number, ha, crop, color, rings: [[[länge, breite], …], …] }, …]
 * Optionen: { url: "/schlag/0" (0 wird durch die ID ersetzt), labels: true, interactive: true,
 *             aerial: { url: "/luftbild", credit: "© …", on: true } }
 * Das Luftbild kommt über den eigenen Server (CSP erlaubt nur 'self') für den gerade sichtbaren Ausschnitt in
 * Länge/Breite (EPSG:4326) – das ist dieselbe lineare Abbildung wie die Umrisse, das Bild passt also als Rechteck.
 */
window.Schlagkarte = (function () {
    "use strict";

    var SVG = "http://www.w3.org/2000/svg";

    function el(name, attrs) {
        var node = document.createElementNS(SVG, name);
        Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
        return node;
    }

    function create(container, items, opts) {
        opts = opts || {};
        if (!items.length) return null;

        // Flächentreue genügt hier: Länge mit cos(Breite) stauchen (Equirektangular um die Kartenmitte)
        var lats = [], lons = [];
        items.forEach(function (it) { it.rings.forEach(function (r) { r.forEach(function (p) { lons.push(p[0]); lats.push(p[1]); }); }); });
        var lat0 = (Math.min.apply(null, lats) + Math.max.apply(null, lats)) / 2;
        var k = Math.cos(lat0 * Math.PI / 180), scale = 100000;
        function project(p) { return [p[0] * k * scale, -p[1] * scale]; }

        var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        items.forEach(function (it) {
            it.projected = it.rings.map(function (r) { return r.map(project); });
            it.projected.forEach(function (r) { r.forEach(function (p) {
                minX = Math.min(minX, p[0]); maxX = Math.max(maxX, p[0]);
                minY = Math.min(minY, p[1]); maxY = Math.max(maxY, p[1]);
            }); });
        });
        var pad = Math.max(maxX - minX, maxY - minY) * 0.04 || 10;
        var full = { x: minX - pad, y: minY - pad, w: maxX - minX + 2 * pad, h: maxY - minY + 2 * pad };
        var view = { x: full.x, y: full.y, w: full.w, h: full.h };

        var svg = el("svg", { "class": "field-map-svg", preserveAspectRatio: "xMidYMid meet" });
        var photo = el("g", { "class": "field-aerial" });
        var shapes = el("g"), labels = el("g", { "class": "field-labels" });
        svg.appendChild(photo);
        svg.appendChild(shapes);
        svg.appendChild(labels);

        items.forEach(function (it) {
            var d = it.projected.map(function (r) {
                return "M" + r.map(function (p) { return p[0].toFixed(1) + " " + p[1].toFixed(1); }).join("L") + "Z";
            }).join("");
            var path = el("path", { d: d, fill: it.color, "class": "field-shape", "data-id": it.id });
            var title = el("title");
            title.textContent = it.name + (it.number ? " (" + it.number + ")" : "") +
                (it.ha ? " · " + String(it.ha.toFixed(2)).replace(".", ",") + " ha" : "") + (it.crop ? " · " + it.crop : "");
            path.appendChild(title);
            shapes.appendChild(path);
            if (opts.labels !== false) {
                // Beschriftung am Schwerpunkt der Eckpunkte des größten Rings
                var ring = it.projected.reduce(function (a, b) { return b.length > a.length ? b : a; });
                var cx = 0, cy = 0;
                ring.forEach(function (p) { cx += p[0]; cy += p[1]; });
                var text = el("text", { x: cx / ring.length, y: cy / ring.length, "class": "field-label" });
                text.textContent = it.number || it.name;
                labels.appendChild(text);
            }
        });
        container.appendChild(svg);

        // --- Luftbild -------------------------------------------------------------
        var aerial = opts.aerial && opts.aerial.url ? opts.aerial : null;
        var aerialOn = false, aerialTimer = null, credit = null;
        if (aerial) {
            credit = document.createElement("div");
            credit.className = "field-map-credit";
            container.appendChild(credit);
        }

        function visibleBox() {
            var r = svg.getBoundingClientRect();
            if (!r.width || !r.height) return null;
            var s = Math.max(view.w / r.width, view.h / r.height);
            var w = r.width * s, h = r.height * s;
            return { x: view.x - (w - view.w) / 2, y: view.y - (h - view.h) / 2, w: w, h: h, pw: r.width, ph: r.height };
        }

        function loadAerial() {
            if (!aerialOn) return;
            var b = visibleBox();
            if (!b) return;
            var lon1 = b.x / (k * scale), lon2 = (b.x + b.w) / (k * scale);
            var lat2 = -b.y / scale, lat1 = -(b.y + b.h) / scale;
            if (lon2 - lon1 > 2 || lat2 - lat1 > 2) return;
            var dpr = Math.min(window.devicePixelRatio || 1, 2);
            var pw = b.pw * dpr, ph = b.ph * dpr, f = Math.min(1, 2048 / Math.max(pw, ph));
            var src = aerial.url + "?bbox=" + [lon1, lat1, lon2, lat2].map(function (v) { return v.toFixed(6); }).join(",") +
                "&w=" + Math.round(pw * f) + "&h=" + Math.round(ph * f);
            var img = el("image", { x: b.x, y: b.y, width: b.w, height: b.h, preserveAspectRatio: "none" });
            img.addEventListener("load", function () {
                // ältere Bilder erst entfernen, wenn das neue da ist (kein Flackern beim Verschieben)
                while (photo.firstChild && photo.firstChild !== img) photo.removeChild(photo.firstChild);
                credit.textContent = aerial.credit || "";
            });
            img.addEventListener("error", function () {
                photo.removeChild(img);
                credit.textContent = aerial.failed || "";
            });
            img.setAttribute("href", src);
            photo.appendChild(img);
        }

        function setAerial(on) {
            if (!aerial) return;
            aerialOn = on;
            svg.classList.toggle("with-aerial", on);
            photo.style.display = on ? "" : "none";
            credit.style.display = on ? "" : "none";
            if (on) loadAerial();
        }

        function apply() {
            svg.setAttribute("viewBox", [view.x, view.y, view.w, view.h].join(" "));
            if (aerialOn) {
                clearTimeout(aerialTimer);
                aerialTimer = setTimeout(loadAerial, 350);
            }
            // Schrift- und Linienstärke in Bildschirmpixeln konstant halten
            var px = view.w / (svg.clientWidth || 600);
            svg.style.setProperty("--px", px);
            // Nummern erst ab etwa 3-facher Vergrößerung, sonst verdecken sie die kleinen Schläge
            var zoomed = view.w < full.w / 3;
            labels.style.display = opts.labels !== false && (zoomed || items.length < 4) ? "" : "none";
        }

        function zoom(factor, cx, cy) {
            var nw = Math.min(full.w * 1.5, Math.max(full.w / 60, view.w * factor));
            var f = nw / view.w;
            cx = cx === undefined ? view.x + view.w / 2 : cx;
            cy = cy === undefined ? view.y + view.h / 2 : cy;
            view.x = cx - (cx - view.x) * f; view.y = cy - (cy - view.y) * f;
            view.w = nw; view.h = view.h * f;
            apply();
        }

        function toMap(evt) {
            var r = svg.getBoundingClientRect();
            var s = Math.max(view.w / r.width, view.h / r.height);
            var ox = (r.width * s - view.w) / 2, oy = (r.height * s - view.h) / 2;
            return [view.x - ox + (evt.clientX - r.left) * s, view.y - oy + (evt.clientY - r.top) * s, s];
        }

        if (opts.interactive !== false) {
            var pointers = {}, moved = false, last = null, pinch = null;
            svg.addEventListener("wheel", function (e) {
                e.preventDefault();
                var m = toMap(e);
                zoom(e.deltaY > 0 ? 1.2 : 1 / 1.2, m[0], m[1]);
            }, { passive: false });
            svg.addEventListener("pointerdown", function (e) {
                pointers[e.pointerId] = e;
                moved = false;
                last = e;
                var ids = Object.keys(pointers);
                if (ids.length === 2) {
                    var a = pointers[ids[0]], b = pointers[ids[1]];
                    pinch = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
                }
                svg.setPointerCapture(e.pointerId);
            });
            svg.addEventListener("pointermove", function (e) {
                if (!pointers[e.pointerId]) return;
                pointers[e.pointerId] = e;
                var ids = Object.keys(pointers);
                if (ids.length === 2 && pinch) {
                    var a = pointers[ids[0]], b = pointers[ids[1]];
                    var dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
                    var mid = toMap({ clientX: (a.clientX + b.clientX) / 2, clientY: (a.clientY + b.clientY) / 2 });
                    zoom(pinch / dist, mid[0], mid[1]);
                    pinch = dist;
                    moved = true;
                    return;
                }
                var s = toMap(e)[2];
                var dx = e.clientX - last.clientX, dy = e.clientY - last.clientY;
                if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
                view.x -= dx * s; view.y -= dy * s;
                last = e;
                apply();
            });
            function up(e) {
                delete pointers[e.pointerId];
                if (Object.keys(pointers).length < 2) pinch = null;
            }
            svg.addEventListener("pointerup", function (e) {
                up(e);
                if (moved || !opts.url) return;
                var target = document.elementFromPoint(e.clientX, e.clientY);
                if (target && target.dataset && target.dataset.id) {
                    window.location = opts.url.replace(/\/0(\b|$)/, "/" + target.dataset.id);
                }
            });
            svg.addEventListener("pointercancel", up);
        }
        window.addEventListener("resize", apply);
        apply();
        if (aerial && aerial.on) setAerial(true);
        return {
            zoomIn: function () { zoom(1 / 1.5); },
            zoomOut: function () { zoom(1.5); },
            reset: function () { view = { x: full.x, y: full.y, w: full.w, h: full.h }; apply(); },
            aerial: function (on) { setAerial(on === undefined ? !aerialOn : on); return aerialOn; },
        };
    }

    return { create: create };
})();
