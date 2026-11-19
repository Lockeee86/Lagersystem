// Hof-Widget für Scriptable (iPhone) – erzeugt vom Lagersystem unter Mein Konto → Widget fürs iPhone.
// Nicht weitergeben: die Adresse enthält deinen persönlichen Schlüssel.
//
// Größen: klein, mittel, groß – und auf dem Sperrbildschirm rund, rechteckig, eine Zeile.
// Für das kleine Widget und den Sperrbildschirm unter „Parameter“ eintragen:
//   huehner | biogas | aufgaben | wichtig
// leer = klein: Hühner, sonst Biogas, sonst Aufgaben; Sperrbildschirm: Überblick

const DATA_URL = "__WIDGET_URL__";
const REFRESH_MINUTES = 15;

const C = {
  bg: Color.dynamic(new Color("#f4f5f0"), new Color("#1d221e")),
  tile: Color.dynamic(new Color("#e6eae2"), new Color("#262c27")),
  text: Color.dynamic(new Color("#1b1f1b"), new Color("#f4f5f0")),
  sub: Color.dynamic(new Color("#5a6359"), new Color("#b9c0b6")),
  accent: Color.dynamic(new Color("#3f7a2e"), new Color("#a8d08d")),
  ok: new Color("#3fae5a"),
  warn: new Color("#e0a020"),
  bad: new Color("#e5534b"),
  idle: new Color("#3e5ba8"),
  unknown: new Color("#7a837a"),
  badBg: Color.dynamic(new Color("#fbe3e1"), new Color("#3a1f1d")),
  badPill: Color.dynamic(new Color("#f7d2ce"), new Color("#4a2522")),
  warnPill: Color.dynamic(new Color("#f6e7c2"), new Color("#3b3320")),
  badText: Color.dynamic(new Color("#a5281f"), new Color("#ffb1ab")),
  warnText: Color.dynamic(new Color("#7d5800"), new Color("#f2cf70")),
  track: Color.dynamic(new Color("#cfd6cb"), new Color("#3a423b")),
};

// --- Daten (mit Zwischenspeicher, falls das Netz gerade fehlt) ---------------------------

async function load() {
  const fm = FileManager.local();
  const cache = fm.joinPath(fm.cacheDirectory(), "hof-widget.json");
  try {
    const req = new Request(DATA_URL);
    req.timeoutInterval = 20;
    const data = await req.loadJSON();
    if (!data || !(data.v >= 1)) throw new Error("Unerwartete Antwort");
    fm.writeString(cache, JSON.stringify(data));
    return { data, offline: false };
  } catch (e) {
    if (fm.fileExists(cache)) return { data: JSON.parse(fm.readString(cache)), offline: true };
    return { data: null, offline: true, error: String(e) };
  }
}

// --- Bausteine ------------------------------------------------------------------------------

function txt(stack, value, size, opts = {}) {
  const t = stack.addText(String(value));
  t.font = opts.heavy ? Font.heavySystemFont(size) : opts.bold ? Font.boldSystemFont(size)
    : opts.semibold ? Font.semiboldSystemFont(size) : Font.systemFont(size);
  t.textColor = opts.color || C.text;
  t.lineLimit = opts.lines || 1;
  t.minimumScaleFactor = opts.scale || 0.6;
  return t;
}

// Huhn selbst gezeichnet – SF Symbols kennt kein Huhn (nur einen Vogel)
const HEN_POLY = [[[14, 64], [6, 24], [20, 34], [25, 20], [36, 44]], [[56, 50], [63, 26], [81, 26], [82, 56]],
  [[83, 19], [95, 25], [83, 30]]];
const HEN_ELL = [[16, 40, 64, 44], [60, 12, 24, 24], [59, 6, 10, 10], [66, 2, 10, 10], [73, 6, 10, 10],
  [77, 31, 8, 11], [39, 80, 6, 13], [53, 80, 6, 13], [33, 90, 14, 5], [47, 90, 14, 5]];

function henImage(color) {
  const ctx = new DrawContext();
  ctx.size = new Size(100, 100);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  ctx.setFillColor(color);
  // jede Form einzeln füllen – in einem Pfad könnten Überlappungen Löcher ergeben
  const fill = (add) => {
    const path = new Path();
    add(path);
    ctx.addPath(path);
    ctx.fillPath();
  };
  for (const poly of HEN_POLY) fill((p) => { p.addLines(poly.map(([x, y]) => new Point(x, y))); p.closeSubpath(); });
  for (const [x, y, w, h] of HEN_ELL) fill((p) => p.addEllipse(new Rect(x, y, w, h)));
  return ctx.getImage();
}

function iconFor(name, color) {
  if (name === "huhn") return henImage(color);
  return (SFSymbol.named(name) || SFSymbol.named("circle.fill")).image;
}

function symbol(name) {
  return { image: iconFor(name, W) };
}

function row(stack, spacing = 6) {
  const r = stack.addStack();
  r.layoutHorizontally();
  r.centerAlignContent();
  r.spacing = spacing;
  return r;
}

function column(stack, spacing = 2) {
  const c = stack.addStack();
  c.layoutVertically();
  c.spacing = spacing;
  return c;
}

function header(stack, icon, label, color) {
  const r = row(stack, 5);
  const img = r.addImage(iconFor(icon, color || C.accent));
  img.imageSize = new Size(14, 14);
  img.tintColor = color || C.accent;
  txt(r, label, 13, { bold: true, color: color || C.accent });
  return r;
}

function dot(stack, color, size = 8) {
  const d = stack.addStack();
  d.size = new Size(size, size);
  d.cornerRadius = size / 2;
  d.backgroundColor = color;
  return d;
}

function num(value) {
  return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

function dec(value) {
  return String(value).replace(".", ",");
}

function priceBar(stack, b, width, height) {
  if (!b.bands || !b.bands.length) return;
  const dc = new DrawContext();
  dc.size = new Size(width, height);
  dc.opaque = false;
  dc.respectScreenScale = true;
  const colors = { good: C.ok, mid: C.warn, bad: C.bad };
  let x = 0;
  for (const band of b.bands) {
    const w = width * band.width / 100;
    dc.setFillColor(colors[band.level] || C.unknown);
    dc.fillRect(new Rect(x, 0, w + 0.5, height));
    x += w;
  }
  if (b.now_pct !== null && b.now_pct !== undefined) {
    dc.setFillColor(Color.white());
    dc.fillRect(new Rect(width * b.now_pct / 100 - 1, 0, 2, height));
  }
  const img = stack.addImage(dc.getImage());
  img.imageSize = new Size(width, height);
  img.cornerRadius = height / 2;
}

// Fortschrittsbalken (Projekte): gefüllter Teil in `color`, Rest grau
function progressBar(stack, pct, width, height, color) {
  const dc = new DrawContext();
  dc.size = new Size(width, height);
  dc.opaque = false;
  dc.respectScreenScale = true;
  dc.setFillColor(C.track);
  dc.fillRect(new Rect(0, 0, width, height));
  dc.setFillColor(color || C.accent);
  dc.fillRect(new Rect(0, 0, width * Math.max(0, Math.min(100, pct || 0)) / 100, height));
  const img = stack.addImage(dc.getImage());
  img.imageSize = new Size(width, height);
  img.cornerRadius = height / 2;
}

function projectColor(p) {
  return p.health === "bad" ? C.bad : p.health === "warn" ? C.warn : C.accent;
}

function doorLine(stack, ch, t, size = 13) {
  if (!ch.door) return;
  const bad = ch.door_alert || ch.door === "door_half";
  const r = row(stack, 6);
  dot(r, bad ? C.bad : ch.door === "door_closed" ? C.ok : C.warn);
  let label = ch.door_alert ? t.door_alert : t[ch.door];
  if (ch.door_since && !ch.door_alert) label += " " + t.since + " " + ch.door_since;
  txt(r, label, size, { semibold: bad, color: bad ? C.badText : C.text });
}

function motorPills(stack, motors) {
  const r = row(stack, 4);
  const colors = { laeuft: C.ok, steht: C.idle, stoerung: C.bad, unbekannt: C.unknown };
  motors.slice(0, 6).forEach((m, i) => {
    const p = r.addStack();
    p.size = new Size(0, 18);
    p.setPadding(0, 7, 0, 7);
    p.cornerRadius = 5;
    p.backgroundColor = colors[m.state] || C.unknown;
    p.centerAlignContent();
    txt(p, String(i + 1), 11, { bold: true, color: Color.white() });
  });
}

function footer(stack, d, offline) {
  stack.addSpacer();
  txt(stack, offline ? d.t.offline + " " + d.stand : d.t.stand + " " + d.stand, 10, { color: C.sub });
}

// --- Kleines Widget ------------------------------------------------------------------------

function smallChickens(w, d) {
  const ch = d.chickens;
  w.url = ch.url;
  header(w, "huhn", d.t.chickens);
  w.addSpacer();
  txt(w, ch.eggs, 44, { heavy: true });
  txt(w, d.t.eggs_today, 13, { color: C.sub });
  w.addSpacer();
  doorLine(w, ch, d.t);
  if (ch.temp !== null && ch.temp !== undefined) txt(w, d.t.stall + " " + dec(ch.temp) + " °C", 12, { color: C.sub });
}

function smallBiogas(w, d) {
  const b = d.biogas;
  w.url = b.url;
  header(w, "bolt.fill", d.t.biogas);
  w.addSpacer();
  const r = row(w, 3);
  r.bottomAlignContent();
  txt(r, num(b.kw), 34, { heavy: true });
  txt(r, "kW", 14, { semibold: true, color: C.sub });
  txt(w, b.running_text, 12, { color: C.sub });
  w.addSpacer();
  if (b.price !== null && b.price !== undefined) {
    txt(w, d.t.price + " " + b.price + " €/MWh", 12, { color: C.sub });
    w.addSpacer(4);
    priceBar(w, b, 130, 7);
  }
}

function smallTasks(w, d) {
  const tk = d.tasks;
  w.url = tk.url;
  header(w, "checklist", d.t.tasks);
  w.addSpacer();
  txt(w, tk.open, 44, { heavy: true });
  txt(w, d.t.today_mine, 13, { color: C.sub });
  w.addSpacer();
  if (tk.items.length) txt(w, tk.items[0], 13, { lines: 2 });
}

function smallImportant(w, d, offline) {
  const items = d.important;
  if (items.length) {
    w.backgroundColor = C.badBg;
    header(w, "exclamationmark.circle.fill", d.t.important, C.badText);
    w.url = items[0].url || d.url;
    w.addSpacer();
    items.slice(0, 3).forEach((it) => {
      txt(w, it.text, 13, { semibold: it.level === "danger", lines: 2,
        color: it.level === "danger" ? C.badText : C.warnText });
      w.addSpacer(5);
    });
  } else {
    header(w, "checkmark.circle.fill", d.t.important);
    w.url = d.url;
    w.addSpacer();
    txt(w, d.t.all_good, 16, { bold: true, lines: 2 });
  }
  footer(w, d, offline);
}

function smallProjects(w, d) {
  const list = d.projects || [];
  header(w, "flag.fill", d.t.projects);
  if (!list.length) {
    w.url = d.url;
    w.addSpacer();
    txt(w, d.t.no_projects, 15, { bold: true, lines: 3 });
    w.addSpacer();
    return;
  }
  const p = list[0];
  w.url = p.url;
  w.addSpacer(8);
  txt(w, p.name, 16, { bold: true, lines: 2, scale: 0.7 });
  w.addSpacer();
  const r = row(w, 6);
  progressBar(r, p.pct, 90, 7, projectColor(p));
  txt(r, p.done + "/" + p.total, 12, { semibold: true, color: C.sub });
  w.addSpacer(6);
  if (p.next) {
    txt(w, d.t.next_step, 11, { color: C.sub });
    txt(w, p.next + (p.next_due ? " · " + p.next_due : ""), 12, { lines: 2, semibold: p.next_late,
      color: p.next_late ? C.badText : C.text });
  } else if (p.reason) {
    txt(w, p.reason, 12, { lines: 2, color: C.badText });
  }
  if (list.length > 1) txt(w, "+ " + (list.length - 1) + " " + d.t.more, 10, { color: C.sub });
}

function small(w, d, offline) {
  const param = (args.widgetParameter || "").trim().toLowerCase();
  let kind = ["huehner", "biogas", "aufgaben", "wichtig", "projekte"].includes(param) ? param
    : d.chickens ? "huehner" : d.biogas ? "biogas" : "aufgaben";
  if (kind === "huehner" && !d.chickens) kind = "aufgaben";
  if (kind === "biogas" && !d.biogas) kind = "aufgaben";
  if (kind === "projekte" && !d.projects) kind = "aufgaben";
  if (kind === "huehner") smallChickens(w, d);
  else if (kind === "biogas") smallBiogas(w, d);
  else if (kind === "projekte") smallProjects(w, d);
  else if (kind === "wichtig") return smallImportant(w, d, offline);
  else smallTasks(w, d);
  if (offline) txt(w, d.t.offline + " " + d.stand, 9, { color: C.sub });
}

// --- Mittleres Widget ----------------------------------------------------------------------

function medium(w, d, offline) {
  const body = row(w, 14);
  body.topAlignContent();
  const left = column(body, 3);
  if (d.biogas) {
    const b = d.biogas;
    left.url = b.url;
    header(left, "bolt.fill", d.t.biogas);
    left.addSpacer();
    const r = row(left, 3);
    r.bottomAlignContent();
    txt(r, num(b.kw), 30, { heavy: true });
    txt(r, "kW", 13, { semibold: true, color: C.sub });
    txt(left, b.running_text, 12, { color: C.sub });
    left.addSpacer();
    if (b.price !== null && b.price !== undefined) {
      txt(left, d.t.price + " " + b.price + " €/MWh", 11, { color: C.sub });
      left.addSpacer(3);
      priceBar(left, b, 150, 6);
    }
  } else if (d.chickens) {
    left.url = d.chickens.url;
    header(left, "huhn", d.t.chickens);
    left.addSpacer();
    txt(left, d.chickens.eggs, 36, { heavy: true });
    txt(left, d.t.eggs_today, 12, { color: C.sub });
    left.addSpacer();
    doorLine(left, d.chickens, d.t, 12);
  } else {
    left.url = d.tasks.url;
    header(left, "checklist", d.t.my_tasks);
    left.addSpacer();
    txt(left, d.tasks.open, 36, { heavy: true });
    txt(left, d.t.today_mine, 12, { color: C.sub });
  }
  body.addSpacer();
  const right = column(body, 6);
  right.size = new Size(120, 0);
  if (d.biogas && d.chickens) {
    const c = column(right, 1);
    c.url = d.chickens.url;
    txt(c, d.t.chickens, 11, { color: C.sub });
    txt(c, d.chickens.eggs + " " + d.t.eggs, 17, { bold: true });
    doorLine(c, d.chickens, d.t, 11);
  }
  if (d.biogas || d.chickens) {
    const c = column(right, 1);
    c.url = d.tasks.url;
    txt(c, d.t.my_tasks, 11, { color: C.sub });
    txt(c, d.tasks.open + " " + d.t.open, 17, { bold: true });
  }
  right.addSpacer();
  if (d.important.length) {
    const pill = row(right, 4);
    pill.url = d.important[0].url || d.url;
    pill.setPadding(3, 8, 3, 8);
    pill.cornerRadius = 9;
    pill.backgroundColor = C.badPill;
    const img = pill.addImage(symbol("exclamationmark.circle.fill").image);
    img.imageSize = new Size(11, 11);
    img.tintColor = C.badText;
    txt(pill, d.important_text, 11, { semibold: true, color: C.badText });
  } else {
    txt(right, (offline ? d.t.offline + " " : d.t.stand + " ") + d.stand, 10, { color: C.sub });
  }
}

// --- Großes Widget -------------------------------------------------------------------------

function tile(parent) {
  const t = column(parent, 4);
  t.backgroundColor = C.tile;
  t.cornerRadius = 14;
  t.setPadding(9, 10, 9, 10);
  return t;
}

// Überschrift der Kachel über die ganze Breite – sonst ist eine Kachel nur so breit wie ihr Inhalt
// und beide rutschen nach links. Mit je einem Abstandhalter teilen sich die Kacheln die Breite.
function stretch(t, label) {
  const r = row(t, 0);
  txt(r, label, 11, { bold: true, color: C.accent });
  r.addSpacer();
}

function large(w, d, offline) {
  const top = row(w, 6);
  txt(top, d.farm, 15, { bold: true });
  top.addSpacer();
  txt(top, (offline ? d.t.offline + " " : d.t.stand + " ") + d.stand, 11, { color: C.sub });
  w.addSpacer(8);

  const maxHints = d.biogas && d.chickens ? 2 : 3;
  d.important.slice(0, maxHints).forEach((it) => {
    const r = row(w, 6);
    r.url = it.url || d.url;
    r.setPadding(5, 9, 5, 9);
    r.cornerRadius = 10;
    r.backgroundColor = it.level === "danger" ? C.badPill : C.warnPill;
    const img = r.addImage(symbol(it.level === "danger" ? "exclamationmark.circle.fill" : "clock.fill").image);
    img.imageSize = new Size(12, 12);
    img.tintColor = it.level === "danger" ? C.badText : C.warnText;
    txt(r, it.text, 12, { semibold: true, color: it.level === "danger" ? C.badText : C.warnText });
    r.addSpacer();
    w.addSpacer(4);
  });
  if (d.important.length) w.addSpacer(4);

  if (d.biogas || d.chickens) {
    const tiles = row(w, 8);
    tiles.topAlignContent();
    if (d.biogas) {
      const b = d.biogas;
      const t = tile(tiles);
      t.url = b.url;
      stretch(t, d.t.biogas);
      const r = row(t, 3);
      r.bottomAlignContent();
      txt(r, num(b.kw), 22, { heavy: true });
      txt(r, "kW", 11, { color: C.sub });
      motorPills(t, b.motors);
      if (b.price !== null && b.price !== undefined) {
        txt(t, b.price + " €/MWh" + (b.best ? " · " + b.best : ""), 10, { color: C.sub });
      }
      if (!d.chickens) t.addSpacer();
    }
    if (d.chickens) {
      const ch = d.chickens;
      const t = tile(tiles);
      t.url = ch.url;
      stretch(t, d.t.chickens);
      const r = row(t, 3);
      r.bottomAlignContent();
      txt(r, ch.eggs, 22, { heavy: true });
      txt(r, d.t.eggs, 11, { color: C.sub });
      doorLine(t, ch, d.t, 11);
      const extra = [];
      if (ch.temp !== null && ch.temp !== undefined) extra.push(d.t.stall + " " + dec(ch.temp) + " °C");
      extra.push(d.t.stock + " " + ch.stock);
      txt(t, extra.join(" · "), 10, { color: C.sub });
    }
    w.addSpacer(8);
  }

  if (d.feeding) {
    const f = row(w, 6);
    f.url = d.feeding.url;
    f.setPadding(6, 10, 6, 10);
    f.cornerRadius = 10;
    f.backgroundColor = C.tile;
    const img = f.addImage(symbol("leaf.fill").image);
    img.imageSize = new Size(12, 12);
    img.tintColor = C.accent;
    txt(f, d.feeding.text, 12, { semibold: true });
    f.addSpacer();
    w.addSpacer(8);
  }

  // Projekte: je eine Zeile mit Balken (ohne Hinweise zwei, sonst eine)
  const projects = (d.projects || []).slice(0, d.important.length ? 1 : 2);
  if (projects.length) {
    projects.forEach((p) => {
      const r = row(w, 6);
      r.url = p.url;
      const img = r.addImage(symbol("flag.fill").image);
      img.imageSize = new Size(11, 11);
      img.tintColor = projectColor(p);
      txt(r, p.name, 12, { semibold: true, scale: 0.8 });
      r.addSpacer();
      progressBar(r, p.pct, 60, 5, projectColor(p));
      txt(r, p.done + "/" + p.total, 11, { color: C.sub });
      w.addSpacer(4);
    });
    w.addSpacer(4);
  }

  const tk = d.tasks;
  const head = row(w, 6);
  head.url = tk.url;
  txt(head, d.t.my_tasks, 12, { bold: true });
  head.addSpacer();
  txt(head, tk.open + " " + d.t.open, 11, { color: C.sub });
  w.addSpacer(4);
  // ohne Hinweise ist unten Platz für mehr Aufgaben
  const maxTasks = Math.max(1, 4 - Math.min(d.important.length, 2) - projects.length);
  const shown = tk.items.slice(0, maxTasks);
  shown.forEach((title) => {
    const r = row(w, 8);
    r.url = tk.url;
    const box = r.addStack();
    box.size = new Size(13, 13);
    box.cornerRadius = 4;
    box.borderWidth = 1.5;
    box.borderColor = C.sub;
    txt(r, title, 12);
    w.addSpacer(3);
  });
  if (tk.open > shown.length) txt(w, "+ " + (tk.open - shown.length) + " " + d.t.more, 11, { color: C.sub });
  w.addSpacer();
}

// --- Sperrbildschirm (rund, rechteckig, eine Zeile) ------------------------------------------
// iOS färbt diese Widgets selbst ein – deshalb nur Weiß und Symbole, keine Ampelfarben.

const W = Color.white();

function lockKind(d) {
  const p = (args.widgetParameter || "").trim().toLowerCase();
  if (p === "huehner" && d.chickens) return p;
  if (p === "biogas" && d.biogas) return p;
  if (p === "projekte" && d.projects) return p;
  if (p === "aufgaben" || p === "wichtig") return p;
  return "status";
}

function doorText(ch, t) {
  if (!ch || !ch.door) return null;
  return ch.door_alert ? t.door_alert : t[ch.door];
}

function doorIcon(ch) {
  if (!ch || !ch.door) return "huhn";
  return ch.door === "door_closed" ? "lock.fill" : "lock.open.fill";
}

function lockLine(stack, icon, text, opts = {}) {
  const r = row(stack, 4);
  const img = r.addImage(symbol(icon).image);
  img.imageSize = new Size(opts.size || 12, opts.size || 12);
  img.tintColor = W;
  txt(r, text, opts.size || 13, { semibold: opts.bold, color: W, scale: 0.7 });
  return r;
}

function lockInline(w, d) {
  const kind = lockKind(d);
  const ch = d.chickens, b = d.biogas;
  const parts = {
    huehner: ch ? [doorText(ch, d.t), ch.eggs + " " + d.t.eggs].filter(Boolean).join(" · ") : null,
    biogas: b ? num(b.kw) + " kW · " + b.running + "/" + b.total : null,
    aufgaben: d.tasks.open + " " + d.t.tasks,
    wichtig: d.important.length ? d.important_text : d.t.all_good,
    projekte: d.projects ? (d.projects.length ? d.projects[0].name + " " + d.projects[0].done + "/" + d.projects[0].total
      : d.t.no_projects) : null,
  };
  let text = parts[kind];
  if (kind === "status") {
    // kurz halten – iOS kürzt die Zeile über der Uhr nach etwa 25 Zeichen
    text = d.important.length ? parts.wichtig + " · " + parts.aufgaben
      : [parts.aufgaben, ch ? doorText(ch, d.t) : null].filter(Boolean).join(" · ");
  }
  txt(w, text || parts.aufgaben, 13, { color: W });
  w.url = kind === "huehner" ? ch.url : kind === "biogas" ? b.url : kind === "aufgaben" ? d.tasks.url
    : kind === "projekte" ? ((d.projects[0] && d.projects[0].url) || d.url)
    : (d.important[0] && d.important[0].url) || d.url;
}

function lockCircular(w, d) {
  w.addAccessoryWidgetBackground = true;
  let kind = lockKind(d);
  if (kind === "status") kind = d.chickens ? "huehner" : d.biogas ? "biogas" : "aufgaben";
  let icon, value, url;
  if (kind === "huehner") {
    icon = doorIcon(d.chickens); value = String(d.chickens.eggs); url = d.chickens.url;
  } else if (kind === "biogas") {
    icon = "bolt.fill"; value = d.biogas.running + "/" + d.biogas.total; url = d.biogas.url;
  } else if (kind === "projekte") {
    const p = d.projects[0];
    icon = "flag.fill"; value = p ? p.done + "/" + p.total : "0"; url = p ? p.url : d.url;
  } else if (kind === "wichtig") {
    icon = d.important.length ? "exclamationmark.circle.fill" : "checkmark.circle";
    value = String(d.important.length); url = (d.important[0] && d.important[0].url) || d.url;
  } else {
    icon = "checklist"; value = String(d.tasks.open); url = d.tasks.url;
  }
  w.url = url;
  w.addSpacer();
  const top = row(w, 0);
  top.addSpacer();
  const img = top.addImage(symbol(icon).image);
  img.imageSize = new Size(16, 16);
  img.tintColor = W;
  top.addSpacer();
  const mid = row(w, 0);
  mid.addSpacer();
  txt(mid, value, 20, { heavy: true, color: W, scale: 0.5 });
  mid.addSpacer();
  w.addSpacer();
}

function lockRectangular(w, d, offline) {
  const kind = lockKind(d);
  const ch = d.chickens, b = d.biogas, tk = d.tasks;
  if (kind === "huehner") {
    w.url = ch.url;
    lockLine(w, "huhn", d.t.chickens, { bold: true });
    lockLine(w, "oval.portrait.fill", ch.eggs + " " + d.t.eggs_today);
    lockLine(w, doorIcon(ch), [doorText(ch, d.t), ch.temp !== null && ch.temp !== undefined ? dec(ch.temp) + " °C" : null]
      .filter(Boolean).join(" · ") || d.t.no_data);
  } else if (kind === "biogas") {
    w.url = b.url;
    lockLine(w, "bolt.fill", d.t.biogas + " · " + num(b.kw) + " kW", { bold: true });
    lockLine(w, "gearshape.2", b.running_text);
    if (b.price !== null && b.price !== undefined) lockLine(w, "eurosign.circle", b.price + " €/MWh" + (b.best ? " · " + b.best : ""));
  } else if (kind === "projekte") {
    const p = d.projects[0];
    w.url = p ? p.url : d.url;
    if (!p) {
      lockLine(w, "flag", d.t.projects, { bold: true });
      lockLine(w, "checkmark.circle", d.t.no_projects);
    } else {
      lockLine(w, "flag.fill", p.name, { bold: true });
      lockLine(w, "chart.bar.fill", p.done + "/" + p.total + " " + d.t.steps + (p.reason ? " · " + p.reason : ""));
      if (p.next) lockLine(w, "arrow.right.circle", p.next + (p.next_due ? " · " + p.next_due : ""), { size: 12 });
    }
  } else if (kind === "aufgaben") {
    w.url = tk.url;
    lockLine(w, "checklist", d.t.my_tasks + ": " + tk.open, { bold: true });
    tk.items.slice(0, 2).forEach((title) => lockLine(w, "square", title, { size: 12 }));
  } else if (kind === "wichtig") {
    w.url = (d.important[0] && d.important[0].url) || d.url;
    if (!d.important.length) {
      lockLine(w, "checkmark.circle", d.t.important, { bold: true });
      lockLine(w, "hand.thumbsup", d.t.all_good);
    } else {
      lockLine(w, "exclamationmark.circle.fill", d.t.important + ": " + d.important.length, { bold: true });
      d.important.slice(0, 2).forEach((it) => lockLine(w, it.level === "danger" ? "exclamationmark.triangle" : "clock", it.text, { size: 12 }));
    }
  } else {
    // Überblick: Hühner, Biogas, Aufgaben + Hinweise – je eine Zeile
    w.url = (d.important[0] && d.important[0].url) || d.url;
    if (ch) lockLine(w, doorIcon(ch), [doorText(ch, d.t), ch.eggs + " " + d.t.eggs].filter(Boolean).join(" · "));
    if (b) lockLine(w, "bolt.fill", num(b.kw) + " kW · " + b.running + "/" + b.total);
    lockLine(w, d.important.length ? "exclamationmark.circle.fill" : "checklist",
      tk.open + " " + d.t.tasks + (d.important.length ? " · " + d.important_text : ""), { bold: d.important.length > 0 });
  }
  if (offline) txt(w, d.t.offline + " " + d.stand, 9, { color: W });
}

// --- Start -----------------------------------------------------------------------------------

const { data, offline, error } = await load();
const w = new ListWidget();
w.backgroundColor = C.bg;
w.setPadding(14, 14, 14, 14);
w.refreshAfterDate = new Date(Date.now() + REFRESH_MINUTES * 60 * 1000);
const family = config.widgetFamily || "medium";
const lockScreen = family.startsWith("accessory");
if (lockScreen) {
  w.backgroundColor = Color.clear();
  w.setPadding(0, 0, 0, 0);
}

if (!data && lockScreen) {
  txt(w, "Hof: keine Verbindung", 12, { color: W });
} else if (!data) {
  header(w, "exclamationmark.triangle.fill", "Hof-Widget", C.badText);
  w.addSpacer();
  txt(w, "Keine Verbindung zur App.", 13, { lines: 3 });
  txt(w, error || "", 10, { color: C.sub, lines: 3 });
} else {
  w.url = data.url;
  if (family === "accessoryInline") lockInline(w, data);
  else if (family === "accessoryCircular") lockCircular(w, data);
  else if (family === "accessoryRectangular") lockRectangular(w, data, offline);
  else if (family === "small") small(w, data, offline);
  else if (family === "large" || family === "extraLarge") large(w, data, offline);
  else medium(w, data, offline);
}

if (config.runsInWidget) {
  Script.setWidget(w);
} else if (family === "accessoryCircular") {
  await w.presentAccessoryCircular();
} else if (family === "accessoryRectangular") {
  await w.presentAccessoryRectangular();
} else if (family === "accessoryInline") {
  await w.presentAccessoryInline();
} else if (family === "small") {
  await w.presentSmall();
} else if (family === "large") {
  await w.presentLarge();
} else {
  await w.presentMedium();
}
Script.complete();
