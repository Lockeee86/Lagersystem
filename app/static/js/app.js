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
    function autoRefresh(canReload, interval) {
        let last = null;
        canReload = canReload || function () { return true; };
        function check() {
            if (document.hidden) return;
            fetch("/api/version", { credentials: "same-origin", headers: { "Accept": "application/json" } })
                .then((r) => { return r.ok ? r.json() : null; })
                .then((data) => {
                    if (!data) return;
                    if (last !== null && data.movement !== last && canReload() && !document.querySelector("form[data-dirty]")) {
                        window.location.reload();
                    }
                    last = data.movement;
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
        if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
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

    return { autoRefresh, initForms };
})();
