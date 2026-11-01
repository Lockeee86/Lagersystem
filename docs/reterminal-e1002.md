# Stall-Display auf dem reTerminal E1002 (Farb-E-Paper)

Der reTerminal E1002 (7,3", 800×480, E Ink Spectra 6, ESP32-S3) hat keinen Browser. Die App liefert die Stall-Anzeige
deshalb zusätzlich als fertiges Bild in genau den sechs Farben des Panels:

```
https://<dein-server>/huehner/anzeige/<schlüssel>/bild.png
```

Den Link (mit Schlüssel) gibt es unter **Hühner → Anzeige im Stall**. Wer den Link kennt, sieht das Bild;
„Neuer Link“ macht den alten ungültig.

## ESPHome

Grundlage ist die ESPHome-Vorlage von Seeed für den reTerminal E1002 (Pins, PSRAM, Display-Modell
`Seeed-reTerminal-E1002`). Daran nur Folgendes ergänzen bzw. anpassen:

```yaml
http_request:
  timeout: 20s
  # verify_ssl: false      # nur bei selbst signiertem Zertifikat

online_image:
  - id: stall
    url: "https://<dein-server>/huehner/anzeige/<schlüssel>/bild.png"
    format: PNG
    type: RGB565
    buffer_size: 65536
    update_interval: 10min     # Farb-E-Paper braucht pro Bild einige Sekunden und flackert dabei
    on_download_finished:
      - component.update: epaper_display

display:
  - platform: epaper_spi
    id: epaper_display
    model: Seeed-reTerminal-E1002
    # … Pins und übrige Angaben aus der Seeed-Vorlage …
    update_interval: never     # gezeichnet wird nur, wenn ein neues Bild da ist
    lambda: |-
      it.image(0, 0, id(stall));
```

Hinweise:

- Das Bild zeigt oben rechts „Stand hh:mm“ – E-Paper behält das letzte Bild, auch ohne Strom oder WLAN.
- Alle 10–15 Minuten reicht; öfter kostet Akku und das Panel flackert bei jedem Wechsel.
- Für Akkubetrieb kann zwischen zwei Bildern Deep Sleep genutzt werden (siehe Seeed-Wiki, ESPHome für reTerminal E).
- Der Server muss vom WLAN im Stall aus erreichbar sein (gleiches Netz oder über das Internet mit HTTPS).
