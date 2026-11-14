/* Gemeinsames JavaScript für alle Seiten */
window.App = (function () {
    "use strict";

    // --- Hell/Dunkel ---------------------------------------------------------
    function setTheme(theme) {
        document.documentElement.setAttribute("data-bs-theme", theme);
        const icon = document.querySelector("#themeToggle i");
        if (icon) icon.className = theme === "dark" ? "bi bi-sun" : "bi bi-moon-stars";
    }
    const toggle = document.getElementById("themeToggle");
    if (toggle) {
        setTheme(document.documentElement.getAttribute("data-bs-theme") || "light");
        // Umschalten merkt sich der Server (fest hell bzw. dunkel); „Nach Tageszeit“ wieder unter Mein Konto
        toggle.addEventListener("click", () => {
            const theme = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
            setTheme(theme);
            const body = new FormData();
            body.append("csrf_token", toggle.dataset.csrf);
            body.append("theme_mode", theme);
            fetch(toggle.dataset.url, { method: "POST", body, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } });
        });
    }

    // --- Statt Inline-Handlern (die CSP lässt keine zu): data-autosubmit, data-select-all ---
    document.addEventListener("change", (e) => {
        const el = e.target.closest("[data-autosubmit]");
        if (el && el.form) { el.form.submit(); }
    });
    document.addEventListener("click", (e) => {
        const el = e.target.closest("[data-select-all]");
        if (el && el.select) { el.select(); }
    });

    // --- Sicherheitsabfrage: <button data-confirm="…"> -------------------------
    document.addEventListener("click", (e) => {
        const el = e.target.closest("[data-confirm]");
        if (el && !window.confirm(el.dataset.confirm)) {
            e.preventDefault();
            e.stopPropagation();
        }
    }, true);

    // --- Doppeltes Absenden verhindern ----------------------------------------
    document.addEventListener("submit", (e) => {
        const form = e.target;
        if (form.method.toLowerCase() !== "post" || form.target === "_blank") return;
        if (form.dataset.submitted) { e.preventDefault(); return; }
        form.dataset.submitted = "1";
        setTimeout(() => { delete form.dataset.submitted; }, 4000);
    });

    // --- "Heute"-Button für Datumsfelder ----------------------------------------
    document.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-today]");
        if (!btn) return;
        const input = document.querySelector(btn.dataset.today);
        const d = new Date();
        input.value = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    });

    // --- Farbvorschläge ----------------------------------------------------------
    document.addEventListener("click", (e) => {
        const btn = e.target.closest(".color-preset");
        if (!btn) return;
        const input = document.querySelector(btn.closest(".color-presets").dataset.target);
        input.value = btn.dataset.color;
    });

    // --- Anlegen/Bearbeiten-Modals: Button mit data-edit='{json}' ---------------
    document.addEventListener("show.bs.modal", (e) => {
        const modal = e.target;
        if (!modal.classList.contains("edit-modal")) return;
        const trigger = e.relatedTarget;
        let data = {};
        try { data = JSON.parse((trigger && trigger.dataset.edit) || "{}"); } catch (err) {}
        const form = modal.querySelector("form");
        form.reset();
        form.elements.id.value = data.id || "";
        const isEdit = !!data.id;
        modal.querySelector(".modal-title").textContent = isEdit ? modal.dataset.titleEdit : modal.dataset.titleNew;
        modal.querySelectorAll(".edit-only").forEach((el) => { el.classList.toggle("d-none", !isEdit); });
        modal.querySelectorAll("[data-required-new]").forEach((el) => { el.required = !isEdit; });
        Object.keys(data).forEach((key) => {
            const field = form.elements[key];
            if (!field || key === "id") return;
            if (field.type === "checkbox") field.checked = !!data[key];
            else field.value = data[key];
        });
    });
    document.addEventListener("shown.bs.modal", (e) => {
        const first = e.target.querySelector("input:not([type=hidden]):not([type=color]), select, textarea");
        if (first && window.matchMedia("(pointer: fine)").matches) first.focus();
    });

    // --- Automatisch neu laden, wenn jemand anderes etwas gebucht hat ------------
    // `topics`: worauf die Seite hört – "movement" (Kistenbewegungen) und die Themen aus live.py (tasks, feeding …)
    function autoRefresh(canReload, interval, topics) {
        let last = null;
        topics = topics || ["movement"];
        canReload = canReload || function () { return true; };
        const busy = () => {
            const el = document.activeElement;
            return (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && el.type !== "checkbox" && el.type !== "radio") ||
                !!document.querySelector("form[data-dirty], .modal.show, .offcanvas.show, .is-dragging, .swiping");
        };
        function check() {
            if (document.hidden) return;
            fetch("/api/version", { credentials: "same-origin", headers: { "Accept": "application/json" } })
                .then((r) => { return r.ok ? r.json() : null; })
                .then((data) => {
                    if (!data) return;
                    const now = topics.map((t) => (t === "movement" ? data.movement : (data.live || {})[t])).join("/");
                    if (last !== null && now !== last) {
                        if (!canReload() || busy()) { return; }             // später noch einmal versuchen, `last` bleibt
                        window.location.reload();
                        return;
                    }
                    last = now;
                })
                .catch(() => {});
        }
        check();
        setInterval(check, interval || 20000);
        document.addEventListener("visibilitychange", check);
    }

    function initForms(root) {
        const input = root.querySelector("input[name=code]");
        if (input && window.matchMedia("(pointer: fine)").matches) input.focus();
    }

    // Formulare mit Eingaben nicht durch Auto-Reload verlieren
    // „Alle markieren“: <input type="checkbox" data-check-all="name">
    document.addEventListener("change", (e) => {
        const name = e.target.dataset && e.target.dataset.checkAll;
        if (!name) return;
        document.querySelectorAll('input[type=checkbox][name="' + name + '"]').forEach((box) => {
            box.checked = e.target.checked;
        });
    });

    // Mehrfachauswahl Schläge (Aufgaben): Suchfeld filtert, Knopf zeigt die Auswahl
    document.addEventListener("input", (e) => {
        if (!e.target.matches("[data-plot-filter]")) return;
        const q = e.target.value.trim().toLowerCase();
        e.target.closest("[data-plot-pick]").querySelectorAll("[data-plot-name]").forEach((row) => {
            row.classList.toggle("d-none", q !== "" && row.dataset.plotName.indexOf(q) < 0);
        });
    });
    document.addEventListener("change", (e) => {
        const pick = e.target.closest && e.target.closest("[data-plot-pick]");
        if (!pick || e.target.type !== "checkbox") return;
        const names = Array.prototype.map.call(pick.querySelectorAll("input[type=checkbox]:checked"),
                                             (box) => { return box.dataset.label; });
        const summary = pick.querySelector("[data-plot-summary]"), count = pick.querySelector("[data-plot-count]");
        summary.textContent = names.length ? names.join(", ") : summary.dataset.none;
        count.textContent = names.length;
        count.classList.toggle("d-none", names.length < 2);
    });

    // Datei gewählt → Formular gleich abschicken (Fotoleiste)
    document.addEventListener("change", (e) => {
        if (e.target.matches("input[type=file][data-autosubmit]") && e.target.files.length) {
            const form = e.target.form;
            form.classList.add("is-busy");
            form.submit();
        }
    });

    document.addEventListener("input", (e) => {
        const form = e.target.form;
        if (form) form.dataset.dirty = "1";
    });

    // Handy: beim Tippen in Feldern die untere Leiste ausblenden (sonst springt sie über die Tastatur)
    const typingFields = "input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]):not([type=file]):not([type=range]), textarea, select";
    document.addEventListener("focusin", (e) => {
        if (e.target.matches && e.target.matches(typingFields)) document.body.classList.add("typing");
    });
    document.addEventListener("focusout", () => {
        setTimeout(() => {
            const el = document.activeElement;
            if (!(el && el.matches && el.matches(typingFields))) document.body.classList.remove("typing");
        }, 50);
    });

    // iOS: Nach dem Schließen der Tastatur (und beim Ein-/Ausblenden der Adressleiste) bleibt die fest stehende
    // Leiste manchmal mitten auf dem Bildschirm hängen und wandert beim Scrollen. Darum an den sichtbaren
    // Bildschirmrand (visualViewport) heften.
    const tabbar = document.querySelector(".tabbar");
    const vv = window.visualViewport;
    if (tabbar && vv) {
        let pending = false;
        const pin = function () {
            pending = false;
            // wo die Leiste ohne Korrektur stünde vs. wo der sichtbare Bereich endet (beides in Seitenkoordinaten)
            const current = parseFloat(tabbar.style.getPropertyValue("--vv-shift")) || 0;
            const base = tabbar.getBoundingClientRect().bottom - current;
            let shift = Math.round(vv.offsetTop + vv.height - base);
            // Nachfedern am Seitenanfang/-ende (iOS „Gummiband“): dort nichts verschieben, sonst springt die Leiste
            const maxY = document.documentElement.scrollHeight - window.innerHeight;
            const bouncing = window.scrollY < 0 || window.scrollY > maxY + 1;
            if (bouncing) { pending = false; return; }
            if (document.body.classList.contains("typing") || Math.abs(shift) > window.innerHeight / 2) shift = 0;
            tabbar.style.setProperty("--vv-shift", shift + "px");
        };
        const schedule = function () {
            if (!pending) { pending = true; requestAnimationFrame(pin); }
        };
        vv.addEventListener("resize", schedule);
        vv.addEventListener("scroll", schedule);
        window.addEventListener("scroll", schedule, { passive: true });
        window.addEventListener("orientationchange", schedule);
        document.addEventListener("focusout", () => { setTimeout(schedule, 300); });
        schedule();
    }

    // #neu (oder eine andere id) in der Adresse: einklappbare Karte aufklappen und hinscrollen
    const openFold = function () {
        const id = decodeURIComponent((location.hash || "").slice(1));
        const card = id && document.getElementById(id);
        if (!card || !card.classList.contains("fold-card")) return;
        const body = card.querySelector(":scope > .collapse"), head = card.querySelector(":scope > .fold-head");
        body.classList.add("show");
        head.classList.remove("collapsed");
        head.setAttribute("aria-expanded", "true");
        card.scrollIntoView({ block: "start" });
    };
    openFold();
    window.addEventListener("hashchange", openFold);

    // Wischen am Handy: [data-swipe] nach rechts = form.swipe-done abschicken, nach links = form.swipe-away
    // (dessen Knopf mit data-confirm fragt vorher nach). Vertikales Scrollen bleibt unberührt.
    document.querySelectorAll("[data-swipe]").forEach((row) => {
        const done = row.querySelector("form.swipe-done"), away = row.querySelector("form.swipe-away");
        if (!done && !away) { return; }
        if (done) { row.classList.add("can-done"); }
        if (away) { row.classList.add("can-away"); }
        let x0, y0, dx = 0, mode = null;
        const reset = function () {
            row.classList.remove("swiping", "swipe-right", "swipe-left", "swipe-armed");
            row.style.removeProperty("--dx");
            mode = null; dx = 0;
        };
        row.addEventListener("touchstart", (e) => {
            if (e.touches.length !== 1 || e.target.closest("select, input, textarea")) { return; }
            x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; mode = "start";
        }, { passive: true });
        row.addEventListener("touchmove", (e) => {
            if (!mode) { return; }
            let mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
            if (mode === "start") {
                if (Math.abs(my) > 10 && Math.abs(my) > Math.abs(mx)) { mode = null; return; }
                if (Math.abs(mx) < 12) { return; }
                mode = "swipe"; row.classList.add("swiping");
            }
            if ((mx > 0 && !done) || (mx < 0 && !away)) { mx = mx / 6; }          // ohne Aktion nur leicht nachgeben
            dx = mx;
            row.style.setProperty("--dx", dx + "px");
            row.classList.toggle("swipe-right", dx > 0);
            row.classList.toggle("swipe-left", dx < 0);
            row.classList.toggle("swipe-armed", Math.abs(dx) > Math.min(110, row.offsetWidth * .35));
        }, { passive: true });
        row.addEventListener("touchend", () => {
            if (mode !== "swipe") { mode = null; return; }
            const armed = row.classList.contains("swipe-armed"), form = dx > 0 ? done : away, dir = dx;
            reset();
            if (armed && form) {
                if (navigator.vibrate) { navigator.vibrate(12); }
                const btn = form.querySelector("button");
                if (form === done) {                    // erledigt: kurz hinausgleiten lassen, dann abschicken
                    row.style.setProperty("--dx", (dir > 0 ? 1 : -1) * row.offsetWidth + "px");
                    row.classList.add("swipe-right", "swipe-armed", "swipe-out");
                    setTimeout(() => { if (btn) { btn.click(); } else { form.submit(); } }, 180);
                } else if (btn) { btn.click(); } else { form.submit(); }
            }
        });
        row.addEventListener("touchcancel", reset);
    });

    // Wochenleiste: seitlich wischen = Woche davor/danach
    document.querySelectorAll(".week-strip").forEach((strip) => {
        let x0 = null, y0 = 0;
        strip.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
        strip.addEventListener("touchend", (e) => {
            if (x0 === null) { return; }
            const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
            x0 = null;
            if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(dy)) {
                strip.classList.add(dx < 0 ? "leave-left" : "leave-right");
                window.location.href = dx < 0 ? strip.dataset.next : strip.dataset.prev;
            }
        });
    });

    // Ladeanzeige: dünner Balken oben, sobald eine Seite länger als einen Augenblick braucht
    let progress = document.querySelector(".nav-progress"), progressTimer;
    const startProgress = function () {
        if (!progress) { return; }
        clearTimeout(progressTimer);
        progressTimer = setTimeout(() => { progress.classList.add("run"); }, 150);
    };
    document.addEventListener("click", (e) => {
        const a = e.target.closest("a[href]");
        if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey ||
            e.target.closest("svg[data-points]")) { return; }
        const href = a.getAttribute("href");
        if (a.target || a.hasAttribute("download") || a.dataset.bsToggle || href.charAt(0) === "#" ||
            a.origin !== window.location.origin || (a.pathname === window.location.pathname && a.hash)) { return; }
        startProgress();
    });
    document.addEventListener("submit", (e) => {
        if (!e.defaultPrevented && !(e.target.getAttribute("target"))) { startProgress(); }
    });
    window.addEventListener("pageshow", () => {
        clearTimeout(progressTimer);
        if (progress) { progress.classList.remove("run"); }
    });

    // Befehlssuche: Strg+K (Mac: ⌘K) öffnet eine Liste aller Seiten und Aktionen, Tippen filtert
    const cmdData = document.getElementById("cmdData"), cmdModal = document.getElementById("cmdk");
    if (cmdData && cmdModal) {
        let cmd = JSON.parse(cmdData.textContent), input = cmdModal.querySelector(".cmdk-input"),
            list = cmdModal.querySelector(".cmdk-list"), sel = 0, shown = [];
        const norm = function (s) {
            return (s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/ß/g, "ss");
        };
        const esc = function (s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; };
        const render = function () {
            const q = norm(input.value.trim()), words = q.split(/\s+/).filter(Boolean);
            shown = (cmd.items || []).filter((it) => {
                const hay = norm(it.label + " " + it.group);
                return words.every((w) => { return hay.indexOf(w) >= 0; });
            }).sort((a, b) => {
                return (norm(b.label).indexOf(q) === 0) - (norm(a.label).indexOf(q) === 0);
            }).slice(0, 40);
            if (q && cmd.search) {
                shown.push({ label: cmd.searchLabel.replace("%Q%", input.value.trim()), icon: "bi-search", group: "",
                             url: cmd.search + "?q=" + encodeURIComponent(input.value.trim()) });
                if (/^[a-z]{1,2}-?[a-z]?\d/i.test(input.value.trim())) { shown.unshift(shown.pop()); }   // Code zuerst suchen
            }
            sel = Math.min(sel, Math.max(shown.length - 1, 0));
            let html = "", group = null;
            shown.forEach((it, i) => {
                if (it.group !== group && it.group) { html += '<div class="cmdk-group">' + esc(it.group) + "</div>"; }
                group = it.group;
                html += '<a class="cmdk-item' + (i === sel ? " active" : "") + '" role="option" href="' + esc(it.url) +
                        '" data-i="' + i + '"><i class="bi ' + esc(it.icon) + '"></i><span>' + esc(it.label) + "</span></a>";
            });
            list.innerHTML = html || '<div class="cmdk-empty">' + esc(cmd.empty) + "</div>";
        };
        const move = function (d) {
            if (!shown.length) { return; }
            sel = (sel + d + shown.length) % shown.length;
            render();
            const act = list.querySelector(".cmdk-item.active");
            if (act) { act.scrollIntoView({ block: "nearest" }); }
        };
        input.addEventListener("input", () => { sel = 0; render(); });
        input.addEventListener("keydown", (e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
            else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
            else if (e.key === "Enter" && shown[sel]) { e.preventDefault(); startProgress(); window.location.href = shown[sel].url; }
        });
        list.addEventListener("mousemove", (e) => {
            const it = e.target.closest(".cmdk-item");
            if (it && +it.dataset.i !== sel) { sel = +it.dataset.i; list.querySelectorAll(".cmdk-item").forEach((el) => {
                el.classList.toggle("active", +el.dataset.i === sel); }); }
        });
        cmdModal.addEventListener("shown.bs.modal", () => { input.focus(); input.select(); });
        cmd.items = null;
        const openCmd = function () {
            input.value = ""; sel = 0;
            bootstrap.Modal.getOrCreateInstance(cmdModal).show();
            if (cmd.items) { render(); return; }
            cmd.items = [];
            render();
            fetch(cmd.source, { credentials: "same-origin" }).then((r) => { return r.json(); })
                .then((items) => { cmd.items = items; render(); })
                .catch(() => { cmd.items = null; });
        };
        document.addEventListener("keydown", (e) => {
            if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); openCmd(); }
        });
        document.querySelectorAll("[data-cmdk], .app-search-kbd").forEach((el) => {
            el.addEventListener("click", (e) => { e.preventDefault(); openCmd(); });
        });
    }

    // Dezente Bewegung: Kennzahlen zählen hoch (ohne „weniger Bewegung“ in den Systemeinstellungen)
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        document.querySelectorAll(".kpi-value").forEach((el) => {
            const node = el.firstChild;
            if (!node || node.nodeType !== 3) { return; }
            const text = node.nodeValue.trim(), m = /^([\d.]+)(,(\d+))?$/.exec(text);
            if (!m) { return; }
            const target = parseFloat(m[1].replace(/\./g, "") + "." + (m[3] || "0")), digits = m[3] ? m[3].length : 0;
            if (!target) { return; }
            let t0 = null;
            const fmt = function (v) { return v.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits }); };
            const step = function (t) {
                t0 = t0 || t;
                const p = Math.min((t - t0) / 700, 1);
                node.nodeValue = fmt(target * (1 - Math.pow(1 - p, 3)));
                if (p < 1) { requestAnimationFrame(step); } else { node.nodeValue = text; }
            };
            requestAnimationFrame(step);
        });
    }

    // Ziehen zum Aktualisieren (Handy, nur auf Seiten mit <main data-ptr>): ganz oben nach unten ziehen lädt neu
    const ptrMain = document.querySelector("main[data-ptr]");
    if (ptrMain && window.matchMedia("(pointer: coarse)").matches) {
        const ptr = document.createElement("div");
        ptr.className = "ptr";
        ptr.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
        ptr.setAttribute("aria-hidden", "true");
        document.body.appendChild(ptr);
        let y0 = null, x0 = 0, pull = 0;
        const LIMIT = 90;
        const set = (v) => {
            pull = v;
            ptr.style.setProperty("--pull", `${Math.min(v, LIMIT + 30)}px`);
            ptr.style.setProperty("--turn", `${Math.min(v / LIMIT, 1) * 270}deg`);
            ptr.classList.toggle("armed", v >= LIMIT);
            ptr.classList.toggle("show", v > 8);
        };
        document.addEventListener("touchstart", (e) => {
            y0 = null;
            if (window.scrollY > 0 || e.touches.length !== 1 || document.querySelector(".modal.show, .offcanvas.show") ||
                e.target.closest("[data-swipe], input, textarea, select, .week-strip, .kpi-row")) { return; }
            y0 = e.touches[0].clientY; x0 = e.touches[0].clientX;
        }, { passive: true });
        document.addEventListener("touchmove", (e) => {
            if (y0 === null) { return; }
            const dy = e.touches[0].clientY - y0, dx = e.touches[0].clientX - x0;
            if (dy < 0 || window.scrollY > 0 || Math.abs(dx) > Math.abs(dy)) { y0 = null; set(0); return; }
            set(dy * .55);
        }, { passive: true });
        const end = () => {
            if (y0 === null) { return; }
            y0 = null;
            if (pull >= LIMIT) {
                ptr.classList.add("loading");
                if (navigator.vibrate) { navigator.vibrate(10); }
                window.location.reload();
            } else { set(0); }
        };
        document.addEventListener("touchend", end);
        document.addEventListener("touchcancel", () => { y0 = null; set(0); });
    }

    // Diagramme anfassbar: Elemente mit <title> (SVG) oder data-tip zeigen ihren Wert beim Antippen bzw. Überfahren
    const tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.setAttribute("role", "status");
    document.body.appendChild(tip);
    let tipFor = null;
    const tipText = (el) => {
        if (el.dataset && el.dataset.tip) { return el.dataset.tip; }
        const t = el.querySelector && el.querySelector(":scope > title");
        return t ? t.textContent : "";
    };
    const tipTarget = (node) => {
        for (let el = node; el && el !== document.body; el = el.parentNode) {
            if (el.nodeType === 1 && ((el.dataset && el.dataset.tip) || (el.namespaceURI === "http://www.w3.org/2000/svg" &&
                el.querySelector(":scope > title")))) { return el; }
        }
        return null;
    };
    const showTip = (el, x, y) => {
        const text = tipText(el);
        if (!text) { return; }
        tipFor = el;
        tip.textContent = text;
        tip.classList.add("show");
        const w = tip.offsetWidth, h = tip.offsetHeight;
        tip.style.left = `${Math.max(8, Math.min(x - w / 2, window.innerWidth - w - 8))}px`;
        tip.style.top = `${Math.max(8, y - h - 14)}px`;
    };
    const hideTip = () => { tipFor = null; tip.classList.remove("show"); };
    document.addEventListener("pointerover", (e) => {
        if (e.pointerType === "touch") { return; }
        const el = tipTarget(e.target);
        if (el) { showTip(el, e.clientX, e.clientY); } else if (tipFor) { hideTip(); }
    });
    document.addEventListener("pointermove", (e) => {
        if (e.pointerType !== "touch" && tipFor) { showTip(tipFor, e.clientX, e.clientY); }
    });
    document.addEventListener("click", (e) => {
        if (e.target.closest && e.target.closest("svg[data-points]")) { e.preventDefault(); return; }   // Wert zeigen statt Link folgen
        const el = tipTarget(e.target);
        if (el && el !== tipFor) { showTip(el, e.clientX, e.clientY); } else { hideTip(); }
    });
    window.addEventListener("scroll", hideTip, { passive: true });
    // Linien-Diagramme: <svg data-points='[[x 0..1, "Text"], …]'> zeigt den nächsten Messpunkt mit einer Hilfslinie
    document.querySelectorAll("svg[data-points]").forEach((svg) => {
        let points;
        try { points = JSON.parse(svg.dataset.points); } catch (err) { return; }
        if (!points.length) { return; }
        const box = svg.viewBox.baseVal;
        const cursor = document.createElementNS("http://www.w3.org/2000/svg", "line");
        cursor.setAttribute("class", "chart-cursor");
        cursor.setAttribute("y1", box.y); cursor.setAttribute("y2", box.y + box.height);
        cursor.style.display = "none";
        svg.appendChild(cursor);
        svg.classList.add("has-points");
        const at = (e) => {
            const rect = svg.getBoundingClientRect();
            const f = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            let best = points[0];
            for (const p of points) { if (Math.abs(p[0] - f) < Math.abs(best[0] - f)) { best = p; } }
            const x = box.x + best[0] * box.width;
            cursor.setAttribute("x1", x); cursor.setAttribute("x2", x);
            cursor.style.display = "";
            svg.dataset.tip = best[1];
            showTip(svg, rect.left + best[0] * rect.width, rect.top);
        };
        const off = () => { cursor.style.display = "none"; delete svg.dataset.tip; hideTip(); };
        svg.addEventListener("pointermove", at);
        svg.addEventListener("pointerdown", at);
        svg.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") { off(); } });
    });

    // Tabellen am PC: Klick auf die Spaltenüberschrift sortiert (nur <table class="table-sort">, ohne Blättern)
    document.querySelectorAll("table.table-sort").forEach((table) => {
        const body = table.tBodies[0];
        if (!body) { return; }
        const value = (row, i) => {
            const cell = row.cells[i];
            if (!cell) { return ""; }
            return cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent.trim();
        };
        const number = (s) => {
            const m = /^-?[\d.]+(,\d+)?/.exec(s.replace(/\s/g, ""));
            return m ? parseFloat(m[0].replace(/\./g, "").replace(",", ".")) : NaN;
        };
        const date = (s) => {
            const m = /^(\d{2})\.(\d{2})\.(\d{4})/.exec(s);
            return m ? +(m[3] + m[2] + m[1]) : NaN;
        };
        table.querySelectorAll("thead th").forEach((th, i) => {
            if (th.dataset.nosort !== undefined || !th.textContent.trim()) { return; }
            th.classList.add("sortable");
            th.setAttribute("role", "button");
            th.tabIndex = 0;
            const sort = () => {
                const dir = th.classList.contains("sort-asc") ? -1 : 1;
                table.querySelectorAll("thead th").forEach((o) => o.classList.remove("sort-asc", "sort-desc"));
                th.classList.add(dir === 1 ? "sort-asc" : "sort-desc");
                const rows = Array.from(body.rows);
                const key = (r) => {
                    const s = value(r, i), d = date(s), n = number(s);
                    return !isNaN(d) ? d : !isNaN(n) && th.dataset.text === undefined ? n : s.toLowerCase();
                };
                rows.sort((a, b) => {
                    const x = key(a), y = key(b);
                    if (typeof x === "number" && typeof y === "number") { return (x - y) * dir; }
                    return String(x).localeCompare(String(y), document.documentElement.lang || "de", { numeric: true }) * dir;
                });
                rows.forEach((r) => body.appendChild(r));
            };
            th.addEventListener("click", sort);
            th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
        });
    });

    // Live-Aktualisierung: <main data-live="tasks,feeding"> lädt neu, sobald jemand anderes etwas geändert hat
    const liveMain = document.querySelector("main[data-live]");
    if (liveMain) { autoRefresh(null, 8000, liveMain.dataset.live.split(",")); }

    // Geteilte Ansicht (PC/Tablet quer): Klick auf einen Eintrag der Liste öffnet ihn rechts daneben (iframe, die Seite
    // erkennt das am Header Sec-Fetch-Dest und lässt Kopfzeile und Leisten weg)
    const splitMain = document.querySelector("main[data-split]");
    if (splitMain) {
        const pattern = new RegExp(splitMain.dataset.split);
        const wide = window.matchMedia("(min-width: 1200px)");
        let pane = null, frame = null, full = null, active = null;
        const close = () => {
            if (!pane) { return; }
            pane.remove(); pane = frame = full = null;
            splitMain.classList.remove("split-open");
            if (active) { active.classList.remove("split-active"); active = null; }
        };
        const open = (link) => {
            if (!pane) {
                pane = document.createElement("aside");
                pane.className = "split-pane";
                pane.innerHTML = '<div class="split-bar"><a class="btn btn-sm btn-outline-secondary split-full"><i class="bi bi-box-arrow-up-right me-1"></i></a>' +
                    '<button type="button" class="btn btn-sm btn-outline-secondary split-close"><i class="bi bi-x-lg"></i></button></div><iframe></iframe>';
                full = pane.querySelector(".split-full"); frame = pane.querySelector("iframe");
                full.appendChild(document.createTextNode(splitMain.dataset.splitOpen));
                pane.querySelector(".split-close").setAttribute("aria-label", splitMain.dataset.splitClose);
                pane.querySelector(".split-close").addEventListener("click", close);
                frame.addEventListener("load", () => {            // nach Aktionen im Fenster: „Ganze Seite“ folgt mit
                    try { full.href = frame.contentWindow.location.pathname + frame.contentWindow.location.search; } catch (err) { /* fremd */ }
                });
                splitMain.appendChild(pane);
                splitMain.classList.add("split-open");
            }
            if (active) { active.classList.remove("split-active"); }
            active = link; link.classList.add("split-active");
            full.href = link.getAttribute("href");
            frame.title = link.textContent.trim().slice(0, 80);
            frame.src = link.href;
        };
        splitMain.addEventListener("click", (e) => {
            const link = e.target.closest("a[href]");
            if (!link || !wide.matches || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey ||
                link.target || link.closest(".split-pane") || link.hash || !pattern.test(link.pathname)) { return; }
            e.preventDefault();
            e.stopImmediatePropagation();
            open(link);
        }, true);
        document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pane && !document.querySelector(".modal.show")) { close(); } });
        wide.addEventListener("change", () => { if (!wide.matches) { close(); } });
    }
    // Im Fenster der geteilten Ansicht: Links führen auf die ganze Seite (Formulare bleiben im Fenster)
    if (document.body.classList.contains("is-embed") && window.top !== window) {
        document.addEventListener("click", (e) => {
            const link = e.target.closest("a[href]");
            if (!link || e.defaultPrevented || link.target || link.dataset.bsToggle || link.hasAttribute("download")) { return; }
            const href = link.getAttribute("href");
            if (href.charAt(0) === "#" || (link.pathname === window.location.pathname && link.hash)) { return; }
            e.preventDefault();
            window.top.location.href = link.href;
        });
    }

    // Reiter auf Detailseiten (macros.page_tabs): zeigt je Reiter nur die Elemente mit passendem data-tab
    const tabNav = document.querySelector("[data-page-tabs]");
    if (tabNav) {
        const parts = Array.from(document.querySelectorAll("[data-tab]"));
        const buttons = Array.from(tabNav.querySelectorAll("[data-tab-key]")).filter((b) => {
            const used = parts.some((el) => el.dataset.tab === b.dataset.tabKey);
            if (!used) { b.remove(); }
            return used;
        });
        if (buttons.length < 2) {
            tabNav.remove();
        } else {
            const memory = `reiter:${window.location.pathname}`;
            const keys = buttons.map((b) => b.dataset.tabKey);
            const show = (key) => {
                parts.forEach((el) => { el.hidden = el.dataset.tab !== key; });
                buttons.forEach((b) => {
                    const on = b.dataset.tabKey === key;
                    b.classList.toggle("active", on);
                    b.setAttribute("aria-selected", on ? "true" : "false");
                });
                try { sessionStorage.setItem(memory, key); } catch (err) { /* privat */ }
            };
            const fromHash = () => {                 // #logbuch → Reiter, in dem das Ziel liegt; #teile → Reiter selbst
                const id = decodeURIComponent(window.location.hash.slice(1));
                if (!id) { return null; }
                if (keys.includes(id)) { return { key: id, target: null }; }
                const target = document.getElementById(id);
                const part = target && target.closest("[data-tab]");
                return part ? { key: part.dataset.tab, target } : null;
            };
            const go = () => {
                const hit = fromHash();
                if (!hit) { return false; }
                show(hit.key);
                if (hit.target) { hit.target.scrollIntoView({ block: "start" }); }
                return true;
            };
            let saved = null;
            try { saved = sessionStorage.getItem(memory); } catch (err) { /* privat */ }
            if (!go()) { show(keys.includes(saved) ? saved : keys[0]); }
            window.addEventListener("hashchange", go);
            tabNav.addEventListener("click", (e) => {
                const b = e.target.closest("[data-tab-key]");
                if (b) { show(b.dataset.tabKey); }
            });
            // Fehlermeldung im Formular eines verborgenen Reiters: Reiter mit dem ungültigen Feld zeigen
            document.addEventListener("invalid", (e) => {
                const part = e.target.closest && e.target.closest("[data-tab]");
                if (part && part.hidden) { show(part.dataset.tab); }
            }, true);
        }
    }

    // Sprungziel in einem eingeklappten Bereich (z. B. Mein Konto → Erweitert): aufklappen und hinscrollen
    const openHashTarget = () => {
        if (window.location.hash.length < 2) { return; }
        let target = null;
        try { target = document.querySelector(window.location.hash); } catch (err) { return; }
        const box = target && target.closest(".collapse:not(.show):not(.filter-more)");
        if (!box || box.closest(".fold-card")) { return; }
        bootstrap.Collapse.getOrCreateInstance(box, { toggle: false }).show();
        box.addEventListener("shown.bs.collapse", () => target.scrollIntoView({ block: "start" }), { once: true });
    };
    openHashTarget();
    window.addEventListener("hashchange", openHashTarget);

    // Seitenleiste am PC ein-/ausklappen (je Gerät gemerkt)
    const sideToggle = document.getElementById("sideToggle");
    if (sideToggle) {
        sideToggle.addEventListener("click", () => {
            const narrow = document.documentElement.classList.toggle("side-collapsed");
            try { localStorage.setItem("seitenleiste", narrow ? "schmal" : "breit"); } catch (err) { /* privat */ }
        });
    }

    // Fütterungsmengen: „Wie vorgegeben“ füllt leere Felder mit der Vorgabe (data-target-kg)
    document.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-fill-targets]");
        if (!btn) { return; }
        btn.closest("form").querySelectorAll("input[data-target-kg]").forEach((input) => {
            if (input.dataset.targetKg && !input.value) { input.value = input.dataset.targetKg; }
        });
    });

    // Erste Schritte: einmal zeigen, beim Schließen am Benutzer merken
    const welcome = document.getElementById("welcome");
    if (welcome) {
        welcome.addEventListener("hidden.bs.modal", () => {
            const body = new URLSearchParams({ csrf_token: welcome.dataset.csrf });
            fetch(welcome.dataset.done, { method: "POST", body, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } });
        });
        bootstrap.Modal.getOrCreateInstance(welcome).show();
    }

    // Hinweise unten (Erfolg/Info): nach 5 s ausblenden, Antippen/Wischen schließt sofort, Maus darüber hält an
    document.querySelectorAll(".app-toast").forEach((el, i) => {
        let timer, close = function () {
            el.classList.add("hide");
            setTimeout(() => { el.remove(); }, 250);
        };
        const start = function () { timer = setTimeout(close, 5000 + i * 600); el.classList.add("running"); };
        el.querySelector(".app-toast-close").addEventListener("click", close);
        el.addEventListener("mouseenter", () => { clearTimeout(timer); el.classList.remove("running"); });
        el.addEventListener("mouseleave", start);
        let x0 = null;
        el.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; }, { passive: true });
        el.addEventListener("touchend", (e) => {
            if (x0 !== null && Math.abs(e.changedTouches[0].clientX - x0) > 60) { close(); }
            x0 = null;
        });
        requestAnimationFrame(() => { el.classList.add("show"); start(); });
    });

    // Liste per Ziehen am Griff umsortieren (Maus und Finger); die Reihenfolge im DOM ist das Ergebnis
    function sortable(list, itemSelector, handleSelector) {
        if (!list) { return; }
        let item = null;
        list.addEventListener("pointerdown", (e) => {
            const handle = e.target.closest(handleSelector);
            if (!handle || (e.pointerType === "mouse" && e.button !== 0)) { return; }
            item = handle.closest(itemSelector);
            if (!item) { return; }
            e.preventDefault();
            handle.setPointerCapture(e.pointerId);
            item.classList.add("is-dragging");
            list.classList.add("is-sorting");
        });
        list.addEventListener("pointermove", (e) => {
            if (!item) { return; }
            const others = Array.from(list.querySelectorAll(itemSelector)).filter((el) => el !== item);
            let before = null;
            for (const el of others) {
                const r = el.getBoundingClientRect();
                if (e.clientY < r.top + r.height / 2) { before = el; break; }
            }
            if (before) { if (item.nextElementSibling !== before) { list.insertBefore(item, before); } }
            else if (list.lastElementChild !== item) { list.appendChild(item); }
            const edge = 70;                                   // am Rand mitscrollen
            if (e.clientY < edge) { window.scrollBy(0, -12); } else if (e.clientY > window.innerHeight - edge) { window.scrollBy(0, 12); }
        });
        const drop = () => {
            if (!item) { return; }
            item.classList.remove("is-dragging");
            list.classList.remove("is-sorting");
            item = null;
        };
        list.addEventListener("pointerup", drop);
        list.addEventListener("pointercancel", drop);
    }

    return { autoRefresh, initForms, sortable };
})();
