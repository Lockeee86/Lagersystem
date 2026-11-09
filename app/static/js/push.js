/* Einstellungen → Benachrichtigungen: Push auf diesem Gerät ein-/ausschalten. */
(function () {
    "use strict";
    const card = document.getElementById("pushDevice");
    if (!card) return;
    const state = card.querySelector("[data-state]");
    const btnOn = card.querySelector("[data-push-on]");
    const btnOff = card.querySelector("[data-push-off]");
    const csrf = card.dataset.csrf;

    function show(key) {
        card.querySelectorAll("[data-msg]").forEach((el) => { el.hidden = el.dataset.msg !== key; });
        btnOn.hidden = !(key === "off" || key === "error");
        btnOff.hidden = key !== "on";
        state.dataset.state = key;
    }

    function post(url, body) {
        return fetch(url, {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
            body: JSON.stringify(body || {})
        }).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
    }

    function keyBytes(b64) {
        const pad = "=".repeat((4 - b64.length % 4) % 4);
        const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
        const out = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
        return out;
    }

    const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
    const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
        show(ios && !standalone ? "ios" : "unsupported");
        return;
    }
    if (!window.isSecureContext) { show("insecure"); return; }
    if (Notification.permission === "denied") { show("denied"); return; }

    const registration = navigator.serviceWorker.register("/sw.js");
    registration.then((reg) => { return reg.pushManager.getSubscription(); })
        .then((sub) => {
            if (sub && Notification.permission === "granted") {
                // Abo beim Server auffrischen (z. B. nach Neuinstallation oder Benutzerwechsel)
                return post(card.dataset.subscribe, sub.toJSON()).then(() => { show("on"); });
            }
            show("off");
        })
        .catch(() => { show("error"); });

    btnOn.addEventListener("click", () => {
        btnOn.disabled = true;
        Notification.requestPermission().then((perm) => {
            if (perm !== "granted") { show(perm === "denied" ? "denied" : "off"); throw null; }
            return Promise.all([registration, fetch(card.dataset.key, { credentials: "same-origin" }).then((r) => { return r.json(); })]);
        }).then((res) => {
            return res[0].pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(res[1].key) });
        }).then((sub) => {
            return post(card.dataset.subscribe, sub.toJSON());
        }).then(() => {
            window.location.reload();
        }).catch((err) => {
            if (err) show("error");
        }).then(() => { btnOn.disabled = false; });
    });

    btnOff.addEventListener("click", () => {
        btnOff.disabled = true;
        registration.then((reg) => { return reg.pushManager.getSubscription(); }).then((sub) => {
            if (!sub) return null;
            const endpoint = sub.endpoint;
            return sub.unsubscribe().then(() => { return post(card.dataset.unsubscribe, { endpoint }); });
        }).then(() => { window.location.reload(); }).catch(() => { show("error"); });
    });
})();
