/* Schlagkarte: alle Schlag-Umrisse als SVG, auf Wunsch mit Luftbild darunter.
 *
 * Daten (aus fields.map_data):
 *   [{ id, name, number, ha, crop, color, rings: [[[länge, breite], …], …], point: [länge, breite] | null }, …]
 *   Schläge ohne Umriss, aber mit Lage aus der Schlagdokumentation, erscheinen als Punkt.
 * Optionen: { url: "/schlag/0" (0 wird durch die ID ersetzt), labels: true, interactive: true,
 *             background: [{ name, color, rings, line: false }] – Grundkarte unter den Einträgen (nicht klickbar),
 *             minSpan: 800 – kleinster Ausschnitt (100 ≈ 70–110 m),
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
        var background = opts.background || [];
        if (!items.length && !background.length) return null;

        // Flächentreue genügt hier: Länge mit cos(Breite) stauchen (Equirektangular um die Kartenmitte)
        var lats = [], lons = [];
        items.concat(background).forEach(function (it) {
            it.rings.forEach(function (r) { r.forEach(function (p) { lons.push(p[0]); lats.push(p[1]); }); });
            if (it.point) { lons.push(it.point[0]); lats.push(it.point[1]); }
        });
        var lat0 = (Math.min.apply(null, lats) + Math.max.apply(null, lats)) / 2;
        var lon0 = (Math.min.apply(null, lons) + Math.max.apply(null, lons)) / 2;
        var k = Math.cos(lat0 * Math.PI / 180), scale = 100000;
        // Nullpunkt in der Kartenmitte: kleine Zahlen, sonst rundet der Browser beim starken Zoomen sichtbar
        function project(p) { return [(p[0] - lon0) * k * scale, -(p[1] - lat0) * scale]; }

        var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        // `fitItems`: Ausschnitt nur nach den Einträgen, die Grundkarte liegt drumherum (z. B. ein einzelner Baum)
        var fit = opts.fitItems && items.length ? items : null;
        items.concat(background).forEach(function (it) {
            it.projected = it.rings.map(function (r) { return r.map(project); });
            if (it.point) it.projected.push([project(it.point)]);
            if (fit && fit.indexOf(it) < 0) return;
            it.projected.forEach(function (r) { r.forEach(function (p) {
                minX = Math.min(minX, p[0]); maxX = Math.max(maxX, p[0]);
                minY = Math.min(minY, p[1]); maxY = Math.max(maxY, p[1]);
            }); });
        });
        var pad = Math.max(maxX - minX, maxY - minY) * 0.04 || 10;
        // nur ein Punkt (oder sehr kleine Fläche): Mindest-Ausschnitt (Standard etwa 600 m, `minSpan` in Karteneinheiten)
        var minSpan = opts.minSpan || 800;
        var grow = Math.max(0, (minSpan - (maxX - minX + 2 * pad)) / 2, (minSpan - (maxY - minY + 2 * pad)) / 2);
        pad += grow;
        var full = { x: minX - pad, y: minY - pad, w: maxX - minX + 2 * pad, h: maxY - minY + 2 * pad };
        var view = { x: full.x, y: full.y, w: full.w, h: full.h };

        var svg = el("svg", { "class": "field-map-svg", preserveAspectRatio: "xMidYMid meet" });
        var photo = el("g", { "class": "field-aerial" });
        var shapes = el("g"), labels = el("g", { "class": "field-labels" }), points = [];
        var base = el("g", { "class": "field-background" });
        svg.appendChild(photo);
        svg.appendChild(base);
        svg.appendChild(shapes);
        background.forEach(function (it) {
            var d = it.projected.map(function (r) {
                return "M" + r.map(function (p) { return p[0].toFixed(1) + " " + p[1].toFixed(1); }).join("L") + (it.line ? "" : "Z");
            }).join("");
            var path = el("path", { d: d, "class": "field-bg" + (it.line ? " is-line" : ""),
                                    fill: it.line ? "none" : it.color, stroke: it.color });
            var title = el("title");
            title.textContent = it.name;
            path.appendChild(title);
            base.appendChild(path);
        });
        svg.appendChild(labels);

        items.forEach(function (it) {
            var path;
            if (!it.rings.length) {
                var c = it.projected[0][0];
                path = el("circle", { cx: c[0].toFixed(1), cy: c[1].toFixed(1), r: 5, fill: it.color,
                                      "class": "field-shape field-point", "data-id": it.id });
                points.push(path);
            } else {
                var d = it.projected.map(function (r) {
                    return "M" + r.map(function (p) { return p[0].toFixed(1) + " " + p[1].toFixed(1); }).join("L") + "Z";
                }).join("");
                path = el("path", { d: d, fill: it.color, "class": "field-shape", "data-id": it.id });
            }
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
                if (!it.rings.length) text.setAttribute("dy", "-1.2em");   // über den Punkt
                labels.appendChild(text);
            }
        });
        // eigener Standort (blauer Punkt mit Genauigkeitskreis), erst nach `locate(true)` sichtbar
        var me = el("g", { "class": "field-me", style: "display: none" });
        var meRange = el("circle", { "class": "field-me-range" }), meDot = el("circle", { "class": "field-me-dot" });
        // Ziel (`opts.target` = [Länge, Breite], z. B. ein Baum): gestrichelte Linie vom Standort dorthin
        var target = opts.target ? project(opts.target) : null;
        var meLine = el("line", { "class": "field-me-line" });
        if (target) me.appendChild(meLine);
        me.appendChild(meRange);
        me.appendChild(meDot);
        svg.appendChild(me);
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
            var lon1 = lon0 + b.x / (k * scale), lon2 = lon0 + (b.x + b.w) / (k * scale);
            var lat2 = lat0 - b.y / scale, lat1 = lat0 - (b.y + b.h) / scale;
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
            points.forEach(function (c) { c.setAttribute("r", (7 * px).toFixed(2)); });
            meDot.setAttribute("r", (8 * px).toFixed(2));
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
        // --- Standort ----------------------------------------------------------------
        var watchId = null, firstFix = true;
        function locate(on, report) {
            report = report || function () {};
            if (!on) {
                if (watchId !== null) navigator.geolocation.clearWatch(watchId);
                watchId = null;
                me.style.display = "none";
                report("off");
                return false;
            }
            if (!navigator.geolocation || !window.isSecureContext) { report("error"); return false; }
            if (watchId !== null) return true;
            firstFix = true;
            report("wait");
            watchId = navigator.geolocation.watchPosition(function (pos) {
                var p = project([pos.coords.longitude, pos.coords.latitude]);
                meDot.setAttribute("cx", p[0].toFixed(1)); meDot.setAttribute("cy", p[1].toFixed(1));
                meRange.setAttribute("cx", p[0].toFixed(1)); meRange.setAttribute("cy", p[1].toFixed(1));
                // 1 Karteneinheit = 1/100000 Grad Breite ≈ 1,11 m
                meRange.setAttribute("r", Math.max(0, (pos.coords.accuracy || 0) / 1.113).toFixed(1));
                me.style.display = "";
                // Abstand zur Karte (in Metern), 0 = innerhalb
                var dx = Math.max(full.x - p[0], 0, p[0] - (full.x + full.w));
                var dy = Math.max(full.y - p[1], 0, p[1] - (full.y + full.h));
                var away = Math.hypot(dx, dy) * 1.113;
                var guide = null;
                if (target) {
                    meLine.setAttribute("x1", p[0].toFixed(1)); meLine.setAttribute("y1", p[1].toFixed(1));
                    meLine.setAttribute("x2", target[0].toFixed(1)); meLine.setAttribute("y2", target[1].toFixed(1));
                    var tx = target[0] - p[0], ty = target[1] - p[1];
                    // Richtung in Grad ab Norden im Uhrzeigersinn (y zeigt auf der Karte nach Süden)
                    guide = { distance: Math.hypot(tx, ty) * 1.113, bearing: (Math.atan2(tx, -ty) * 180 / Math.PI + 360) % 360 };
                }
                if (firstFix) {
                    firstFix = false;
                    if (target && guide.distance < 3000) {
                        // Standort und Ziel zusammen zeigen
                        var x1 = Math.min(p[0], target[0]), x2 = Math.max(p[0], target[0]);
                        var y1 = Math.min(p[1], target[1]), y2 = Math.max(p[1], target[1]);
                        var pad = Math.max(x2 - x1, y2 - y1) * 0.2 + 15;
                        var bw = Math.max(x2 - x1 + 2 * pad, 50), bh = Math.max(y2 - y1 + 2 * pad, 50);
                        var cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
                        view = { x: cx - bw / 2, y: cy - bh / 2, w: bw, h: bh };
                        apply();
                    } else if (!target && away < 1000) {
                        var w = Math.min(view.w, Math.max(full.w / 4, 250)), f = w / view.w;
                        view = { x: p[0] - w / 2, y: p[1] - view.h * f / 2, w: w, h: view.h * f };
                        apply();
                    }
                }
                report(away < 1000 || target ? "ok" : "far", { accuracy: pos.coords.accuracy, away: away, guide: guide });
            }, function (err) {
                report(err && err.code === 1 ? "denied" : "error");
                if (err && err.code === 1) locate(false);
            }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 30000 });
            return true;
        }

        window.addEventListener("resize", apply);
        apply();
        if (aerial && aerial.on) setAerial(true);
        return {
            zoomIn: function () { zoom(1 / 1.5); },
            zoomOut: function () { zoom(1.5); },
            reset: function () { view = { x: full.x, y: full.y, w: full.w, h: full.h }; apply(); },
            aerial: function (on) { setAerial(on === undefined ? !aerialOn : on); return aerialOn; },
            locate: locate,
            locating: function () { return watchId !== null; },
        };
    }

    // Knopf „Mein Standort“: an/aus, Meldungen unten auf der Karte (Texte kommen übersetzt aus der Vorlage)
    function locateButton(map, btn, texts) {
        if (!map || !btn) return;
        if (!navigator.geolocation || !window.isSecureContext) { btn.disabled = true; btn.title = texts.none; return; }
        var box = btn.closest(".field-map"), note = document.createElement("div");
        note.className = "field-map-note";
        note.style.display = "none";
        box.appendChild(note);
        function say(text) { note.textContent = text || ""; note.style.display = text ? "" : "none"; }

        // Hinführen (nur mit Ziel): Kasten oben links mit Pfeil, Entfernung und Himmelsrichtung. Mit Kompass zeigt der
        // Pfeil in Laufrichtung (wie das Handy gehalten wird), sonst gilt „Norden oben“ wie auf der Karte.
        var guideBox = null, guideArrow, guideText, guideHint, compassBtn, heading = null, lastGuide = null, compassOn = false;
        if (texts.target) {
            guideBox = document.createElement("div");
            guideBox.className = "field-map-guide";
            guideBox.style.display = "none";
            guideBox.innerHTML = '<i class="bi bi-arrow-up-circle-fill guide-arrow" aria-hidden="true"></i>' +
                '<div class="guide-body"><div class="guide-name"></div><div class="guide-text"></div><div class="guide-hint"></div></div>' +
                '<button type="button" class="btn btn-sm btn-light guide-compass"><i class="bi bi-compass me-1"></i></button>';
            guideBox.querySelector(".guide-name").textContent = texts.target;
            guideArrow = guideBox.querySelector(".guide-arrow");
            guideText = guideBox.querySelector(".guide-text");
            guideHint = guideBox.querySelector(".guide-hint");
            compassBtn = guideBox.querySelector(".guide-compass");
            compassBtn.appendChild(document.createTextNode(texts.compass));
            compassBtn.addEventListener("click", function () { startCompass(); });
            box.appendChild(guideBox);
        }
        function direction(deg) { return texts.dirs[Math.round(deg / 45) % 8]; }
        function renderGuide() {
            if (!guideBox || !lastGuide) return;
            guideBox.style.display = "";
            var d = lastGuide.distance;
            if (d < 6) {
                guideText.textContent = texts.here;
                guideArrow.className = "bi bi-check-circle-fill guide-arrow is-here";
                guideArrow.style.transform = "";
            } else {
                var shown = d < 100 ? Math.round(d) + " m" : d < 1000 ? Math.round(d / 5) * 5 + " m" :
                    (d / 1000).toLocaleString(document.documentElement.lang || "de", { maximumFractionDigits: 1 }) + " km";
                guideText.textContent = shown + " · " + direction(lastGuide.bearing);
                guideArrow.className = "bi bi-arrow-up-circle-fill guide-arrow";
                guideArrow.style.transform = "rotate(" + Math.round(lastGuide.bearing - (heading || 0)) + "deg)";
            }
            guideHint.textContent = heading === null ? texts.north_up : texts.ahead;
            compassBtn.style.display = heading === null && !compassOn ? "" : "none";
        }
        function onOrientation(e) {
            var h = typeof e.webkitCompassHeading === "number" ? e.webkitCompassHeading :
                (e.absolute && e.alpha !== null ? (360 - e.alpha) % 360 : null);
            if (h === null) return;
            var turn = (screen.orientation && screen.orientation.angle) || window.orientation || 0;
            heading = (h + turn + 360) % 360;
            renderGuide();
        }
        function startCompass() {
            if (compassOn || !texts.target) return;
            var listen = function () {
                compassOn = true;
                window.addEventListener("deviceorientationabsolute", onOrientation);
                window.addEventListener("deviceorientation", onOrientation);
                renderGuide();
            };
            if (window.DeviceOrientationEvent && typeof DeviceOrientationEvent.requestPermission === "function") {
                DeviceOrientationEvent.requestPermission().then(function (r) { if (r === "granted") listen(); })
                    .catch(function () { renderGuide(); });
            } else if (window.DeviceOrientationEvent) {
                listen();
            }
        }

        btn.addEventListener("click", function () {
            var on = !map.locating();
            if (on) startCompass();
            else if (guideBox) { guideBox.style.display = "none"; lastGuide = null; }
            map.locate(on, function (state, info) {
                btn.classList.toggle("active", state !== "off" && state !== "denied" && state !== "error");
                btn.setAttribute("aria-pressed", btn.classList.contains("active") ? "true" : "false");
                if (state === "wait") say(texts.wait);
                else if (state === "ok") {
                    say(info.accuracy > 30 ? texts.rough.replace("{m}", Math.round(info.accuracy)) : "");
                    if (info.guide) { lastGuide = info.guide; renderGuide(); }
                }
                else if (state === "far") say(texts.far.replace("{km}", (info.away / 1000).toLocaleString(document.documentElement.lang || "de", { maximumFractionDigits: 1 })));
                else if (state === "denied") say(texts.denied);
                else if (state === "error") say(texts.none);
                else say("");
            });
        });
    }

    return { create: create, locateButton: locateButton };
})();
