/* ==========================================================================
   DishIt — frontend wireframe

   Renders entirely from fixtures.json, whose shapes match specs/api-contract.md.
   Porting to the live backend means replacing the four functions in the DATA
   ACCESS block with fetch() calls to /api/*. Nothing below that block changes.
   ========================================================================== */

let DB = null;
let THRESHOLD = 5;

const LOCATIONS = [
  { name: "Morningside Heights", lat: 40.8075, lng: -73.9626 },
  { name: "Upper West Side",     lat: 40.7870, lng: -73.9754 },
  { name: "Manhattan Valley",    lat: 40.7990, lng: -73.9640 }
];
let locIndex = 0;

// Manual cycling (header pill) is the demo-safe default — no permission
// prompt, works offline. Real geolocation is an opt-in upgrade a viewer
// reaches for via the hero link or the "restaurants near me" button, and
// always falls back to the manual location rather than blocking anything.
let usingGeo = false;
let geoCoords = null;

/* --- utilities ----------------------------------------------------------- */

// Inline SVG pin, not an emoji, so it renders identically across platforms
// and inherits the surrounding text color via currentColor.
const PIN = `<svg class="pin-icon" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M8 1.5c-2.49 0-4.5 2-4.5 4.46 0 3.34 4.5 8.54 4.5 8.54s4.5-5.2 4.5-8.54c0-2.46-2.01-4.46-4.5-4.46Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
  <circle cx="8" cy="6" r="1.6" stroke="currentColor" stroke-width="1.3"/>
</svg>`;

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();

function activeLocation() {
  return usingGeo && geoCoords ? { name: "Your location", ...geoCoords } : LOCATIONS[locIndex];
}

function haversineMeters(lat1, lng1, lat2, lng2) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1), dLng = toRad(lng2 - lng1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

// Straight-line distance ÷ an average walking speed (~80 m/min, ~3 mph) — no
// routing API, so it reads as "about" a time rather than a turn-by-turn ETA.
const WALK_M_PER_MIN = 80;
function distanceLabel(distance_m) {
  const mi = (distance_m / 1609).toFixed(1);
  const min = Math.max(1, Math.round(distance_m / WALK_M_PER_MIN));
  return `${mi} mi · ~${min} min walk`;
}

/** Requests real browser geolocation and recomputes restaurant distances from
 *  it on success. Always calls back — on denial, timeout, or an unsupported
 *  browser it just leaves the manual location in place, since a location
 *  upgrade should never be something the rest of the page waits on. */
function resolveGeolocation(cb) {
  if (!navigator.geolocation) { cb(false); return; }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      geoCoords = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      usingGeo = true;
      DB.restaurants.forEach((r) => { r.distance_m = haversineMeters(geoCoords.lat, geoCoords.lng, r.lat, r.lng); });
      cb(true);
    },
    () => cb(false),
    { timeout: 6000 }
  );
}

/* --- DATA ACCESS ---------------------------------------------------------
   Everything the UI knows about the data goes through these four functions.
   Each maps 1:1 onto an endpoint in specs/api-contract.md.
   ------------------------------------------------------------------------ */

/** GET /api/popular → { talked_about, controversial, top_rated } */
function getPopular() {
  const scored = DB.dishes.filter((d) => d.mention_count >= THRESHOLD);
  return {
    talked_about:  [...scored].sort((a, b) => b.mention_count - a.mention_count).slice(0, 6),
    controversial: scored.filter((d) => d.is_controversial)
                         .sort((a, b) => b.mention_count - a.mention_count).slice(0, 6),
    top_rated:     [...scored].sort((a, b) => b.sentiment.score - a.sentiment.score).slice(0, 6)
  };
}

/** GET /api/search?q= → { query, result_type, matched_on, primary, secondary }
 *
 *  Intent routing, which the backend must reproduce. Three buckets are scored
 *  independently — restaurant name, dish name, cuisine — and the strongest
 *  match decides which entity type leads. Cuisine is checked first because a
 *  bare cuisine term ("Italian") matches no name field at all and would
 *  otherwise fall through to a weak partial match on something unrelated.
 */
function search(q) {
  const query = norm(q);
  if (!query) return null;

  const score = (text) => {
    const t = norm(text);
    if (!t) return 0;
    if (t === query) return 100;
    if (t.startsWith(query)) return 80;
    if (t.includes(query)) return 60;
    const qt = query.split(" ");
    const tt = new Set(t.split(" "));
    const hit = qt.filter((w) => w.length > 2 && tt.has(w)).length;
    return hit ? 40 * (hit / qt.length) : 0;
  };

  const cuisineHit = Math.max(0, ...DB.restaurants.map((r) => score(r.cuisine)));
  const restHit    = Math.max(0, ...DB.restaurants.map((r) => score(r.name)));
  const dishHit    = Math.max(0, ...DB.dishes.map((d) => score(d.name)));

  const dishes = DB.dishes
    .map((d) => ({ d, s: score(d.name) })).filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || b.d.mention_count - a.d.mention_count).map((x) => x.d);

  const rests = DB.restaurants
    .map((r) => ({ r, s: Math.max(score(r.name), score(r.cuisine)) })).filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.r.distance_m - b.r.distance_m).map((x) => x.r);

  let matched_on, result_type;
  if (cuisineHit >= 80 && cuisineHit >= restHit && cuisineHit >= dishHit) {
    matched_on = "cuisine";      result_type = "restaurants";
  } else if (restHit >= dishHit && restHit > 0) {
    matched_on = "restaurant_name"; result_type = "restaurants";
  } else {
    matched_on = "dish_name";    result_type = "dishes";
  }

  if (!dishes.length && !rests.length) {
    return { query: q, result_type: "dishes", matched_on: "none", primary: [], secondary: null };
  }
  if (result_type === "dishes" && !dishes.length) { result_type = "restaurants"; matched_on = "restaurant_name"; }
  if (result_type === "restaurants" && !rests.length) { result_type = "dishes"; matched_on = "dish_name"; }

  const primary = result_type === "dishes" ? dishes : rests;
  const otherItems = result_type === "dishes" ? rests : dishes;

  return {
    query: q, result_type, matched_on, primary,
    // Full matches of both types. The routing above is only a guess about which
    // to lead with, so the UI's manual toggle needs the complete other list too.
    all: { dishes, restaurants: rests },
    secondary: otherItems.length
      ? { type: result_type === "dishes" ? "restaurants" : "dishes", items: otherItems.slice(0, 4) }
      : null
  };
}

/** GET /api/restaurants/{id} → { restaurant, dishes } */
function getRestaurant(id) {
  const restaurant = DB.restaurants.find((r) => r.id === id);
  const dishes = DB.dishes.filter((d) => d.restaurant_id === id)
    .sort((a, b) => b.mention_count - a.mention_count);
  return { restaurant, dishes };
}

/** GET /api/dishes/{id} → { dish, quotes, also_at } */
function getDish(id) {
  const dish = DB.dishes.find((d) => d.id === id);
  const also_at = DB.dishes
    .filter((d) => norm(d.name) === norm(dish.name))
    .sort((a, b) => b.sentiment.score - a.sentiment.score);
  return { dish, quotes: DB.quotes[id] || [], also_at };
}

/* --- components ---------------------------------------------------------- */

function bar(s) {
  const total = s.positive + s.negative + s.neutral || 1;
  const pct = (n) => (n / total) * 100;
  return `<div class="bar" role="img" aria-label="${s.positive} positive, ${s.negative} negative, ${s.neutral} neutral">
    ${s.positive ? `<i class="p" style="width:${pct(s.positive)}%"></i>` : ""}
    ${s.neutral  ? `<i class="x" style="width:${pct(s.neutral)}%"></i>`  : ""}
    ${s.negative ? `<i class="n" style="width:${pct(s.negative)}%"></i>` : ""}
  </div>`;
}

function dishCard(d, opts = {}) {
  const thin = d.mention_count < THRESHOLD;
  const cls = thin ? "s-thin" : "s-" + d.sentiment.label;
  const r = DB.byRest[d.restaurant_id];
  return `<button class="dish ${cls}" data-dish="${d.id}">
    <span class="dish-score">
      <span class="pct">${thin ? "—" : d.sentiment.score + "%"}</span>
      <span class="lab">${thin ? "no score" : d.sentiment.label}</span>
    </span>
    <span>
      <span class="dish-name">
        ${esc(d.name)}
        ${d.is_controversial ? '<span class="badge badge-split">Controversial</span>' : ""}
        ${thin ? '<span class="badge badge-thin">Thin data</span>' : ""}
        ${d.on_current_menu === false ? '<span class="badge badge-off">Off menu</span>' : ""}
      </span>
      ${opts.hideWhere ? "" : `<span class="dish-where">${esc(r.name)} · ${esc(r.cuisine)}</span>`}
      ${thin ? "" : bar(d.sentiment)}
      <span class="meta">
        <span><b>${d.mention_count}</b> mentions</span>
        <span>${d.source_mix.critic} critic · ${d.source_mix.public} public</span>
      </span>
      ${thin ? `<span class="thin-note">Only ${d.mention_count} mention${d.mention_count === 1 ? "" : "s"} — below the ${THRESHOLD}-mention minimum, so we don't score it yet.</span>` : ""}
    </span>
  </button>`;
}

function restCard(r) {
  const dishes = DB.dishes.filter((d) => d.restaurant_id === r.id && d.mention_count >= THRESHOLD)
    .sort((a, b) => b.sentiment.score - a.sentiment.score);
  const top = dishes[0];
  return `<button class="rest" data-rest="${r.id}">
    <span class="plate"><span>${esc(r.cuisine)}</span></span>
    <span class="rest-body">
      <span class="rest-name">${esc(r.name)}</span>
      <span class="rest-meta">${esc(r.neighborhood)} · ${distanceLabel(r.distance_m)}</span>
      ${r.hours_today ? `<span class="rest-hours">${esc(r.hours_today)}</span>` : ""}
      <span class="rest-top">
        ${top ? `Top dish: <b>${esc(top.name)}</b> · ${top.sentiment.score}%` : "No dishes scored yet"}
      </span>
    </span>
  </button>`;
}

function grid(dishes, opts) {
  return `<div class="grid">${dishes.map((d) => dishCard(d, opts)).join("")}</div>`;
}

/* --- views --------------------------------------------------------------- */

let activeTab = "controversial";

function viewHome() {
  $("#head-search").hidden = true;
  const pop = getPopular();
  const tabs = [
    ["controversial", "Controversial", "High volume, split opinion. The dishes people argue about."],
    ["talked_about",  "Most talked about", "Ranked by how often a dish comes up across all sources."],
    ["top_rated",     "Top rated", "Highest share of positive mentions, among dishes clearing the minimum."]
  ];
  const current = tabs.find((t) => t[0] === activeTab);

  const loc = activeLocation();
  const nearWord = usingGeo ? "you" : esc(loc.name);

  $("#view").innerHTML = `
    <section class="hero">
      <div class="wrap">
        <h1>Stop guessing <em>what to order.</em></h1>
        <p>DishIt tells you what people are saying and what dishes are worth ordering.</p>
        <form class="search" id="hero-search">
          <input type="search" id="hero-q" placeholder="Try a dish, a restaurant, or a cuisine" aria-label="Search dishes, restaurants, or cuisines">
          <button type="submit">Search</button>
        </form>
        <p class="hero-loc">
          ${usingGeo
            ? `<span>Distances from <strong>your location</strong></span><button class="loc-cta" type="button" id="hero-loc-btn">${PIN} Refresh location</button>`
            : `<button class="loc-cta" type="button" id="hero-loc-btn">${PIN} Use my location</button>`}
        </p>
        <div class="hero-tries">
          <span>Try</span>
          <button class="chip" data-q="cacio e pepe">cacio e pepe</button>
          <button class="chip" data-q="Sable &amp; Rye">Sable &amp; Rye</button>
          <button class="chip" data-q="Korean">Korean</button>
          <button class="chip" data-q="ramen">ramen</button>
        </div>
      </div>
    </section>

    <section class="section">
      <div class="wrap">
        <div class="section-head">
          <h2>Popular near ${nearWord}</h2>
          <div class="tabs" role="tablist">
            ${tabs.map((t) => `<button class="tab" role="tab" data-tab="${t[0]}" aria-selected="${t[0] === activeTab}">${t[1]}</button>`).join("")}
          </div>
        </div>
        <p class="section-note">${current[2]}</p>
        ${pop[activeTab].length
          ? grid(pop[activeTab])
          : `<div class="empty"><h3>Nothing here yet</h3><p>No dishes in this neighborhood have enough mentions to qualify for this list.</p></div>`}
      </div>
    </section>`;
}

function viewResults(q) {
  $("#head-search").hidden = false;
  $("#head-q").value = q;
  const res = search(q);

  if (!res || !res.primary.length) {
    $("#view").innerHTML = `
      <div class="wrap">
        <div class="results-head">
          <button class="back" id="back-btn">← Home</button>
          <h2>No matches for “${esc(q)}”</h2>
        </div>
        <div class="empty">
          <h3>Nothing in range matched that</h3>
          <p>We only cover ${DB.restaurants.length} restaurants near ${usingGeo ? "you" : esc(activeLocation().name)} right now, so plenty of real dishes aren't in here yet.</p>
          <div class="hero-tries">
            <span>Try</span>
            <button class="chip" data-q="cacio e pepe">cacio e pepe</button>
            <button class="chip" data-q="tacos">tacos</button>
            <button class="chip" data-q="Italian">Italian</button>
          </div>
        </div>
      </div>`;
    return;
  }

  const label = { cuisine: "a cuisine", restaurant_name: "a restaurant name", dish_name: "a dish name" }[res.matched_on];
  const showing = window.__forceType || res.result_type;
  const items = showing === "dishes" ? res.all.dishes : res.all.restaurants;

  $("#view").innerHTML = `
    <div class="wrap">
      <div class="results-head">
        <button class="back" id="back-btn">← Home</button>
        <h2>${esc(q)}</h2>
        <p class="routed">Matched <code>${res.matched_on}</code> — reading this as ${label}, so ${res.result_type} lead.</p>
        <div class="toggle" role="group" aria-label="Result type">
          <button data-force="dishes" aria-pressed="${showing === "dishes"}">Dishes</button>
          <button data-force="restaurants" aria-pressed="${showing === "restaurants"}">Restaurants</button>
        </div>
      </div>

      ${items.length
        ? (showing === "dishes"
            ? grid(items)
            : `<div class="grid-r">${items.map(restCard).join("")}</div>`)
        : `<div class="empty"><h3>No ${showing} matched</h3><p>Switch back above to see what did.</p></div>`}

      ${res.secondary && showing === res.result_type ? `
        <div class="secondary">
          <div class="section-head"><h2>Also found</h2></div>
          <p class="section-note">${res.secondary.items.length} ${res.secondary.type} matched too.</p>
          ${res.secondary.type === "dishes"
            ? grid(res.secondary.items)
            : `<div class="grid-r">${res.secondary.items.map(restCard).join("")}</div>`}
        </div>` : ""}
    </div>`;
}

function viewRestaurant(id) {
  $("#head-search").hidden = false;
  const { restaurant: r, dishes } = getRestaurant(id);
  const scored = dishes.filter((d) => d.mention_count >= THRESHOLD);

  $("#view").innerHTML = `
    <div class="wrap">
      <div class="detail-head">
        <button class="back" id="back-btn">← Back</button>
        <h2>${esc(r.name)}</h2>
        <p class="detail-sub">${esc(r.cuisine)} · ${esc(r.neighborhood)} · ${esc(r.cross_street)} · ${distanceLabel(r.distance_m)}${r.hours_today ? ` · ${esc(r.hours_today)}` : ""}</p>
      </div>
      ${scored.length
        ? `<p class="section-note">${scored.length} dishes have enough mentions to score, ranked by how often they come up.</p>${grid(dishes, { hideWhere: true })}`
        : `<div class="empty"><h3>Not enough written about this one yet</h3><p>We found mentions of ${dishes.length} dish${dishes.length === 1 ? "" : "es"} here, but none clear the ${THRESHOLD}-mention minimum, so there's nothing we'd stand behind ranking.</p></div>`}
    </div>`;
}

/* --- dish modal ---------------------------------------------------------- */

function openDish(id) {
  const { dish: d, quotes, also_at } = getDish(id);
  const r = DB.byRest[d.restaurant_id];
  const thin = d.mention_count < THRESHOLD;
  const elsewhere = also_at.filter((x) => x.id !== d.id);

  $("#modal-root").innerHTML = `
    <div class="scrim" id="scrim">
      <div class="modal" role="dialog" aria-modal="true" aria-label="${esc(d.name)}">
        <div class="modal-head">
          <div>
            <h3>${esc(d.name)}</h3>
            <p class="modal-where">${esc(r.name)} · ${esc(r.cuisine)} · ${esc(r.neighborhood)}</p>
          </div>
          <button class="modal-x" id="modal-x" aria-label="Close">✕</button>
        </div>
        <div class="modal-body">

          <div>
            ${thin ? `<div class="readout"><span class="pct" style="color:var(--muted)">—</span><span class="of">not enough mentions to score</span></div>`
                   : `<div class="readout">
                        <span class="pct" style="color:var(--${d.sentiment.label === "positive" ? "pos" : d.sentiment.label === "negative" ? "neg" : "split"})">${d.sentiment.score}%</span>
                        <span class="of">positive across ${d.mention_count} mentions</span>
                      </div>`}
            ${thin ? "" : bar(d.sentiment)}
            <div class="split-row">
              <span class="a">${d.sentiment.positive} positive</span>
              <span>${d.sentiment.neutral} neutral</span>
              <span class="b">${d.sentiment.negative} negative</span>
            </div>
            <span class="meta">
              <span>${d.source_mix.critic} critic · ${d.source_mix.public} public</span>
              <span>${d.on_current_menu === null ? "menu not checked" : d.on_current_menu ? "on current menu" : "not on current menu"}</span>
            </span>
            ${d.is_controversial ? `<div class="thin-note">Flagged controversial: high mention volume with genuinely split sentiment, not a weak consensus.</div>` : ""}
          </div>

          ${quotes.length ? `
          <div>
            <div class="block-title">What people said</div>
            ${quotes.map((q) => {
              const tag = q.source_url ? "a" : "div";
              const href = q.source_url ? `href="${esc(q.source_url)}" target="_blank" rel="noopener"` : "";
              return `
              <${tag} class="quote q-${q.sentiment}" ${href}>
                <blockquote><p>${esc(q.text)}</p></blockquote>
                <cite>${esc(q.source_label)} ${q.source_url ? '<span class="evidence-link">View evidence →</span>' : ""}</cite>
              </${tag}>`;
            }).join("")}
          </div>` : ""}

          ${elsewhere.length ? `
          <div>
            <div class="block-title">This dish elsewhere</div>
            <div class="alsoat">
              ${also_at.map((x) => {
                const xr = DB.byRest[x.restaurant_id];
                const isThis = x.id === d.id;
                const col = x.sentiment.label === "positive" ? "pos" : x.sentiment.label === "negative" ? "neg" : "split";
                return `<button class="alsoat-row ${isThis ? "is-this" : ""}" data-dish="${x.id}">
                  <span class="alsoat-pct" style="color:var(--${col})">${x.sentiment.score}%</span>
                  <span class="alsoat-name">${esc(xr.name)}${isThis ? " — you're here" : ""}<small>${esc(xr.neighborhood)} · ${distanceLabel(xr.distance_m)}</small></span>
                  <span class="alsoat-n">${x.mention_count} mentions</span>
                </button>`;
              }).join("")}
            </div>
          </div>` : ""}

        </div>
      </div>
    </div>`;

  $("#modal-x").focus();
}

function closeDish() { $("#modal-root").innerHTML = ""; }

/* --- routing + events ---------------------------------------------------- */

function go(view, arg) {
  window.__forceType = null;
  closeDish();
  $("#loc-name").textContent = activeLocation().name;
  if (view === "results") viewResults(arg);
  else if (view === "restaurant") viewRestaurant(arg);
  else viewHome();
  window.scrollTo(0, 0);
}

document.addEventListener("submit", (e) => {
  if (e.target.id === "hero-search" || e.target.id === "head-search") {
    e.preventDefault();
    const q = (e.target.id === "hero-search" ? $("#hero-q") : $("#head-q")).value.trim();
    if (q) go("results", q);
  }
});

document.addEventListener("click", (e) => {
  const t = e.target;

  const chip = t.closest("[data-q]");
  if (chip) { go("results", chip.dataset.q); return; }

  const dish = t.closest("[data-dish]");
  if (dish) { openDish(dish.dataset.dish); return; }

  const rest = t.closest("[data-rest]");
  if (rest) { go("restaurant", rest.dataset.rest); return; }

  const tab = t.closest("[data-tab]");
  if (tab) { activeTab = tab.dataset.tab; viewHome(); return; }

  const force = t.closest("[data-force]");
  if (force) {
    window.__forceType = force.dataset.force;
    viewResults($("#head-q").value.trim());
    return;
  }

  if (t.closest("#modal-x") || t.id === "scrim") { closeDish(); return; }
  if (t.closest("#back-btn") || t.closest("#home-link")) { e.preventDefault(); go("home"); return; }

  if (t.closest("#loc-btn")) {
    usingGeo = false;
    locIndex = (locIndex + 1) % LOCATIONS.length;
    go("home");
    return;
  }

  const heroLoc = t.closest("#hero-loc-btn");
  if (heroLoc) {
    heroLoc.textContent = "Locating…";
    resolveGeolocation(() => go("home"));
    return;
  }

  if (t.closest("#theme-btn")) {
    const root = document.documentElement;
    const dark = root.dataset.theme === "dark";
    root.dataset.theme = dark ? "light" : "dark";
    $("#theme-btn").textContent = dark ? "Dark" : "Light";
    return;
  }
});

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDish(); });

/* --- boot ---------------------------------------------------------------- */

function boot(data) {
  DB = data;
  THRESHOLD = data._threshold || 5;
  DB.byRest = Object.fromEntries(DB.restaurants.map((r) => [r.id, r]));
  go("home");
}

if (window.__FIXTURES__) {
  boot(window.__FIXTURES__);
} else {
  fetch("fixtures.json").then((r) => r.json()).then(boot).catch(() => {
    $("#view").innerHTML = `<div class="wrap"><div class="empty"><h3>Could not load fixtures.json</h3>
      <p>Serve this directory over HTTP rather than opening the file directly — <code>python3 -m http.server</code> from <code>frontend/</code> is enough.</p></div></div>`;
  });
}
