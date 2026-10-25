/* Gemeinsames JavaScript für alle Seiten */
window.App = (function () {
    "use strict";

    // --- Hell/Dunkel ---------------------------------------------------------
    function setTheme(theme) {
        document.documentElement.setAttribute("data-bs-theme", theme);
        try { localStorage.setItem("theme", theme); } catch (e) {}
        var icon = document.querySelector("#themeToggle i");
        if (icon) icon.className = theme === "dark" ? "bi bi-sun" : "bi bi-moon-stars";
    }
    var toggle = document.getElementById("themeToggle");
    if (toggle) {
        setTheme(document.documentElement.getAttribute("data-bs-theme") || "light");
        toggle.addEventListener("click", function () {
            setTheme(document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark");
        });
    }

    // --- Sicherheitsabfrage: <button data-confirm="…"> -------------------------
    document.addEventListener("click", function (e) {
        var el = e.target.closest("[data-confirm]");
        if (el && !window.confirm(el.dataset.confirm)) {
            e.preventDefault();
            e.stopPropagation();
        }
    }, true);

    // --- Doppeltes Absenden verhindern ----------------------------------------
    document.addEventListener("submit", function (e) {
        var form = e.target;
        if (form.method.toLowerCase() !== "post" || form.target === "_blank") return;
        if (form.dataset.submitted) { e.preventDefault(); return; }
        form.dataset.submitted = "1";
        setTimeout(function () { delete form.dataset.submitted; }, 4000);
    });

    // --- "Heute"-Button für Datumsfelder ----------------------------------------
    document.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-today]");
        if (!btn) return;
        var input = document.querySelector(btn.dataset.today);
        var d = new Date();
        input.value = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    });

    // --- Farbvorschläge ----------------------------------------------------------
    document.addEventListener("click", function (e) {
        var btn = e.target.closest(".color-preset");
        if (!btn) return;
        var input = document.querySelector(btn.closest(".color-presets").dataset.target);
        input.value = btn.dataset.color;
    });

    // --- Anlegen/Bearbeiten-Modals: Button mit data-edit='{json}' ---------------
    document.addEventListener("show.bs.modal", function (e) {
        var modal = e.target;
        if (!modal.classList.contains("edit-modal")) return;
        var trigger = e.relatedTarget;
        var data = {};
        try { data = JSON.parse((trigger && trigger.dataset.edit) || "{}"); } catch (err) {}
        var form = modal.querySelector("form");
        form.reset();
        form.elements.id.value = data.id || "";
        var isEdit = !!data.id;
        modal.querySelector(".modal-title").textContent = isEdit ? modal.dataset.titleEdit : modal.dataset.titleNew;
        modal.querySelectorAll(".edit-only").forEach(function (el) { el.classList.toggle("d-none", !isEdit); });
        modal.querySelectorAll("[data-required-new]").forEach(function (el) { el.required = !isEdit; });
        Object.keys(data).forEach(function (key) {
            var field = form.elements[key];
            if (!field || key === "id") return;
            if (field.type === "checkbox") field.checked = !!data[key];
            else field.value = data[key];
        });
    });
    document.addEventListener("shown.bs.modal", function (e) {
        var first = e.target.querySelector("input:not([type=hidden]):not([type=color]), select, textarea");
        if (first && window.matchMedia("(pointer: fine)").matches) first.focus();
    });

    // --- Automatisch neu laden, wenn jemand anderes etwas gebucht hat ------------
    function autoRefresh(canReload, interval) {
        var last = null;
        canReload = canReload || function () { return true; };
        function check() {
            if (document.hidden) return;
            fetch("/api/version", { credentials: "same-origin", headers: { "Accept": "application/json" } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data) return;
                    if (last !== null && data.movement !== last && canReload() && !document.querySelector("form[data-dirty]")) {
                        window.location.reload();
                    }
                    last = data.movement;
                })
                .catch(function () {});
        }
        check();
        setInterval(check, interval || 20000);
        document.addEventListener("visibilitychange", check);
    }

    function initForms(root) {
        var input = root.querySelector("input[name=code]");
        if (input && window.matchMedia("(pointer: fine)").matches) input.focus();
    }

    // Formulare mit Eingaben nicht durch Auto-Reload verlieren
    // „Alle markieren“: <input type="checkbox" data-check-all="name">
    document.addEventListener("change", function (e) {
        var name = e.target.dataset && e.target.dataset.checkAll;
        if (!name) return;
        document.querySelectorAll('input[type=checkbox][name="' + name + '"]').forEach(function (box) {
            box.checked = e.target.checked;
        });
    });

    // Mehrfachauswahl Schläge (Aufgaben): Suchfeld filtert, Knopf zeigt die Auswahl
    document.addEventListener("input", function (e) {
        if (!e.target.matches("[data-plot-filter]")) return;
        var q = e.target.value.trim().toLowerCase();
        e.target.closest("[data-plot-pick]").querySelectorAll("[data-plot-name]").forEach(function (row) {
            row.classList.toggle("d-none", q !== "" && row.dataset.plotName.indexOf(q) < 0);
        });
    });
    document.addEventListener("change", function (e) {
        var pick = e.target.closest && e.target.closest("[data-plot-pick]");
        if (!pick || e.target.name !== "plot_ids") return;
        var names = Array.prototype.map.call(pick.querySelectorAll("input[name=plot_ids]:checked"),
                                             function (box) { return box.dataset.label; });
        var summary = pick.querySelector("[data-plot-summary]"), count = pick.querySelector("[data-plot-count]");
        summary.textContent = names.length ? names.join(", ") : summary.dataset.none;
        count.textContent = names.length;
        count.classList.toggle("d-none", names.length < 2);
    });

    // Datei gewählt → Formular gleich abschicken (Fotoleiste)
    document.addEventListener("change", function (e) {
        if (e.target.matches("input[type=file][data-autosubmit]") && e.target.files.length) {
            var form = e.target.form;
            form.classList.add("is-busy");
            form.submit();
        }
    });

    document.addEventListener("input", function (e) {
        var form = e.target.form;
        if (form) form.dataset.dirty = "1";
    });

    // Flash-Meldungen nach einiger Zeit ausblenden
    setTimeout(function () {
        document.querySelectorAll(".flash-stack .alert-success, .flash-stack .alert-info").forEach(function (el) {
            bootstrap.Alert.getOrCreateInstance(el).close();
        });
    }, 6000);

    return { autoRefresh: autoRefresh, initForms: initForms };
})();
