/* Einstellungen → Benachrichtigungen: Push auf diesem Gerät ein-/ausschalten. */
(function () {
    "use strict";
    var card = document.getElementById("pushDevice");
    if (!card) return;
    var state = card.querySelector("[data-state]");
    var btnOn = card.querySelector("[data-push-on]");
    var btnOff = card.querySelector("[data-push-off]");
    var csrf = card.dataset.csrf;

    function show(key) {
        card.querySelectorAll("[data-msg]").forEach(function (el) { el.hidden = el.dataset.msg !== key; });
        btnOn.hidden = !(key === "off" || key === "error");
        btnOff.hidden = key !== "on";
        state.dataset.state = key;
    }

    function post(url, body) {
        return fetch(url, {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
            body: JSON.stringify(body || {})
        }).then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
    }

    function keyBytes(b64) {
        var pad = "=".repeat((4 - b64.length % 4) % 4);
        var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
        var out = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
        return out;
    }

    var ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
    var standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
        show(ios && !standalone ? "ios" : "unsupported");
        return;
    }
    if (!window.isSecureContext) { show("insecure"); return; }
    if (Notification.permission === "denied") { show("denied"); return; }

    var registration = navigator.serviceWorker.register("/sw.js");
    registration.then(function (reg) { return reg.pushManager.getSubscription(); })
        .then(function (sub) {
            if (sub && Notification.permission === "granted") {
                // Abo beim Server auffrischen (z. B. nach Neuinstallation oder Benutzerwechsel)
                return post(card.dataset.subscribe, sub.toJSON()).then(function () { show("on"); });
            }
            show("off");
        })
        .catch(function () { show("error"); });

    btnOn.addEventListener("click", function () {
        btnOn.disabled = true;
        Notification.requestPermission().then(function (perm) {
            if (perm !== "granted") { show(perm === "denied" ? "denied" : "off"); throw null; }
            return Promise.all([registration, fetch(card.dataset.key, { credentials: "same-origin" }).then(function (r) { return r.json(); })]);
        }).then(function (res) {
            return res[0].pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(res[1].key) });
        }).then(function (sub) {
            return post(card.dataset.subscribe, sub.toJSON());
        }).then(function () {
            window.location.reload();
        }).catch(function (err) {
            if (err) show("error");
        }).then(function () { btnOn.disabled = false; });
    });

    btnOff.addEventListener("click", function () {
        btnOff.disabled = true;
        registration.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
            if (!sub) return null;
            var endpoint = sub.endpoint;
            return sub.unsubscribe().then(function () { return post(card.dataset.unsubscribe, { endpoint: endpoint }); });
        }).then(function () { window.location.reload(); }).catch(function () { show("error"); });
    });
})();
