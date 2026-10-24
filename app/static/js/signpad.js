// Unterschrift am Handy: Zeichenfeld im Fenster #signModal (Formular #signForm, Feld #signCanvas).
// Auslöser: <button data-bs-toggle="modal" data-bs-target="#signModal" data-sign-action="…" data-sign-title="…"
//            data-sign-name="…" data-sign-lock="1">  – lock: Name nicht änderbar (z. B. bei Unterweisungen).
(function () {
    var modal = document.getElementById("signModal");
    if (!modal) return;
    var canvas = document.getElementById("signCanvas"), ctx = canvas.getContext("2d"),
        form = document.getElementById("signForm"), save = document.getElementById("signSave"),
        nameInput = document.getElementById("signName"), pad = canvas.parentNode, drawn = false, last = null;

    function clear() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        drawn = false; save.disabled = true; pad.classList.remove("signed");
    }
    function resize() {
        var ratio = window.devicePixelRatio || 1, rect = pad.getBoundingClientRect();
        canvas.width = Math.round(rect.width * ratio);
        canvas.height = Math.round(rect.height * ratio);
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.lineWidth = 2.5; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#000";
        clear();
    }
    function point(e) {
        var rect = canvas.getBoundingClientRect();
        return {x: e.clientX - rect.left, y: e.clientY - rect.top};
    }
    canvas.addEventListener("pointerdown", function (e) {
        e.preventDefault();
        canvas.setPointerCapture(e.pointerId);
        last = point(e);
        ctx.beginPath(); ctx.arc(last.x, last.y, 1.2, 0, 2 * Math.PI); ctx.fillStyle = "#000"; ctx.fill();
    });
    canvas.addEventListener("pointermove", function (e) {
        if (!last) return;
        var p = point(e);
        ctx.beginPath(); ctx.moveTo(last.x, last.y); ctx.lineTo(p.x, p.y); ctx.stroke();
        last = p;
        if (!drawn) { drawn = true; save.disabled = false; pad.classList.add("signed"); }
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(function (name) {
        canvas.addEventListener(name, function () { last = null; });
    });
    document.getElementById("signClear").addEventListener("click", clear);

    modal.addEventListener("show.bs.modal", function (e) {
        var btn = e.relatedTarget;
        form.action = btn.dataset.signAction;
        document.getElementById("signTitle").textContent = btn.dataset.signTitle;
        nameInput.value = btn.dataset.signName || "";
        nameInput.readOnly = btn.dataset.signLock === "1";
    });
    modal.addEventListener("shown.bs.modal", resize);
    window.addEventListener("resize", function () { if (modal.classList.contains("show") && !drawn) resize(); });
    form.addEventListener("submit", function (e) {
        if (!drawn) { e.preventDefault(); return; }
        document.getElementById("signData").value = canvas.toDataURL("image/png");
        save.disabled = true;
    });
})();
