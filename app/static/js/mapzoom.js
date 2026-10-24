/* Lagerkarte am Handy: mit zwei Fingern zoomen, mit einem verschieben (wie bei einer Landkarte).
   Ein kurzes Antippen bleibt ein Klick (öffnet den Stapel), nach dem Verschieben nicht. */
window.MapZoom = (function () {
    "use strict";

    function create(viewport, content, opts) {
        opts = opts || {};
        var maxScale = opts.max || 3;
        var state = { x: 0, y: 0, s: 1 };
        var pointers = {};
        var gesture = null;
        var moved = false;

        function minScale() {
            // ganz herausgezoomt passt die Karte in die Breite
            return Math.min(1, viewport.clientWidth / content.offsetWidth);
        }

        function clamp() {
            var w = content.offsetWidth * state.s, h = content.offsetHeight * state.s;
            var vw = viewport.clientWidth, vh = viewport.clientHeight;
            state.x = w <= vw ? (vw - w) / 2 : Math.min(0, Math.max(vw - w, state.x));
            state.y = h <= vh ? 0 : Math.min(0, Math.max(vh - h, state.y));
        }

        function apply() {
            // herausgezoomt wird der Bereich niedriger (keine leere Fläche unter der Karte)
            var maxH = Math.min(window.innerHeight * 0.62, 34 * 16);
            viewport.style.height = Math.round(Math.min(maxH, content.offsetHeight * state.s + 4)) + "px";
            clamp();
            content.style.transform = "translate(" + state.x + "px," + state.y + "px) scale(" + state.s + ")";
        }

        function zoomAt(factor, cx, cy) {
            var s = Math.min(maxScale, Math.max(minScale(), state.s * factor));
            // der Punkt unter den Fingern bleibt stehen
            state.x = cx - (cx - state.x) * (s / state.s);
            state.y = cy - (cy - state.y) * (s / state.s);
            state.s = s;
            apply();
        }

        function local(e) {
            var r = viewport.getBoundingClientRect();
            return { x: e.clientX - r.left, y: e.clientY - r.top };
        }

        function list() { return Object.keys(pointers).map(function (k) { return pointers[k]; }); }

        function startGesture() {
            var p = list();
            if (p.length === 1) {
                gesture = { kind: "pan", x: p[0].x - state.x, y: p[0].y - state.y, sx: p[0].x, sy: p[0].y };
            } else if (p.length >= 2) {
                var dx = p[1].x - p[0].x, dy = p[1].y - p[0].y;
                gesture = { kind: "pinch", dist: Math.hypot(dx, dy) || 1, s: state.s };
            }
        }

        viewport.addEventListener("pointerdown", function (e) {
            if (e.pointerType === "mouse" && e.button !== 0) return;
            pointers[e.pointerId] = local(e);
            if (list().length === 1) moved = false;
            startGesture();
        });

        viewport.addEventListener("pointermove", function (e) {
            if (!pointers[e.pointerId] || !gesture) return;
            pointers[e.pointerId] = local(e);
            var p = list();
            if (gesture.kind === "pan" && p.length === 1) {
                if (!moved && Math.hypot(p[0].x - gesture.sx, p[0].y - gesture.sy) < 8) return;
                if (!moved) viewport.setPointerCapture(e.pointerId);
                moved = true;
                state.x = p[0].x - gesture.x;
                state.y = p[0].y - gesture.y;
                apply();
            } else if (gesture.kind === "pinch" && p.length >= 2) {
                moved = true;
                var dx = p[1].x - p[0].x, dy = p[1].y - p[0].y;
                var target = gesture.s * Math.hypot(dx, dy) / gesture.dist;
                zoomAt(target / state.s, (p[0].x + p[1].x) / 2, (p[0].y + p[1].y) / 2);
            }
        });

        function end(e) {
            delete pointers[e.pointerId];
            startGesture();          // von zwei auf einen Finger: weiter verschieben ohne Sprung
        }
        viewport.addEventListener("pointerup", end);
        viewport.addEventListener("pointercancel", end);

        // nach dem Verschieben/Zoomen kein Klick auf den Stapel unter dem Finger
        viewport.addEventListener("click", function (e) {
            if (moved) {
                e.preventDefault();
                e.stopPropagation();
                moved = false;
            }
        }, true);

        viewport.addEventListener("wheel", function (e) {
            if (!e.ctrlKey) return;  // Trackpad-Pinch am Laptop
            e.preventDefault();
            var p = local(e);
            zoomAt(e.deltaY < 0 ? 1.1 : 0.9, p.x, p.y);
        }, { passive: false });

        // der Browser verschiebt den Bereich selbst, wenn ein Stapel den Fokus bekommt – in die Verschiebung übernehmen
        viewport.addEventListener("scroll", function () {
            if (!viewport.scrollLeft && !viewport.scrollTop) return;
            state.x -= viewport.scrollLeft;
            state.y -= viewport.scrollTop;
            viewport.scrollLeft = viewport.scrollTop = 0;
            apply();
        });

        window.addEventListener("resize", apply);
        apply();

        return {
            zoom: function (factor) { zoomAt(factor, viewport.clientWidth / 2, viewport.clientHeight / 2); },
            fit: function () { state.s = minScale(); state.x = 0; state.y = 0; apply(); },
            reset: function () { state.s = 1; state.x = 0; state.y = 0; apply(); },
            // Stapel sichtbar machen, auch wenn unten das Blatt aufgeht (bottomInset = dessen Höhe)
            reveal: function (el, bottomInset) {
                var v = viewport.getBoundingClientRect(), r = el.getBoundingClientRect();
                if (r.right > v.right - 8) state.x -= r.right - v.right + 16;
                if (r.left < v.left + 8) state.x += v.left - r.left + 16;
                if (r.bottom > v.bottom - 8) state.y -= r.bottom - v.bottom + 16;
                if (r.top < v.top + 8) state.y += v.top - r.top + 16;
                apply();
                r = el.getBoundingClientRect();
                var limit = window.innerHeight - (bottomInset || 0) - 12;
                var header = document.querySelector(".navbar.sticky-top, header.sticky-top");
                var top = header ? header.getBoundingClientRect().bottom + 8 : 8;
                if (r.bottom > limit) window.scrollBy({ top: r.bottom - limit, behavior: "smooth" });
                else if (r.top < top) window.scrollBy({ top: r.top - top, behavior: "smooth" });
            }
        };
    }

    return { create: create };
})();
