/* Service-Worker: zeigt Push-Nachrichten an und öffnet beim Antippen die passende Seite. */
"use strict";

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener("push", function (e) {
    var data = {};
    try { data = e.data ? e.data.json() : {}; } catch (err) { data = { title: e.data && e.data.text() }; }
    e.waitUntil(self.registration.showNotification(data.title || "Hof", {
        body: data.body || "",
        tag: data.tag || undefined,
        renotify: !!data.tag,
        icon: "/logo/icon-192.png",
        badge: "/logo/icon-192.png",
        data: { url: data.url || "/" }
    }));
});

self.addEventListener("notificationclick", function (e) {
    e.notification.close();
    var url = new URL((e.notification.data && e.notification.data.url) || "/", self.location.origin).href;
    e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
        for (var i = 0; i < list.length; i++) {
            if (new URL(list[i].url).origin === self.location.origin && "focus" in list[i]) {
                return list[i].focus().then(function (c) { return c.navigate ? c.navigate(url) : c; });
            }
        }
        return self.clients.openWindow(url);
    }));
});
