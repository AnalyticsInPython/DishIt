/* ==========================================================================
   DishIt — frontend

   Renders from the live API in backend/app/main.py, whose shapes are specified
   in specs/api-contract.md. Every request goes through the DATA ACCESS block
   below; nothing else in this file talks to the network.

   frontend/fixtures.json is no longer loaded — it stays as the worked example of
   the contract's shapes. Rendering it client-side would mean keeping a second
   copy of the search routing here, and having exactly one implementation of that
   rule (backend/app/search.py) is the reason this file now calls the API at all.
   ========================================================================== */

let THRESHOLD = 5;

const LOCATIONS = [
  { name: "Morningside Heights", lat: 40.8075, lng: -73.9626 },
  { name: "Upper West Side",     lat: 40.7870, lng: -73.9754 },
  { name: "Manhattan Valley",    lat: 40.7990, lng: -73.9640 }
];
let locIndex = 0;
let knownRestaurantCount = null;

/* --- utilities ----------------------------------------------------------- */

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();

function activeLocation() {
  return LOCATIONS[locIndex];
}

// Straight-line distance ÷ an average walking speed (~80 m/min, ~3 mph) — no
// routing API, so it reads as "about" a time rather than a turn-by-turn ETA.
const WALK_M_PER_MIN = 80;
function distanceLabel(distance_m) {
  const mi = (distance_m / 1609).toFixed(1);
  const min = Math.max(1, Math.round(distance_m / WALK_M_PER_MIN));
  return `${mi} mi · ~${min} min walk`;
}

/* --- DATA ACCESS ---------------------------------------------------------
   Everything the UI knows about the data goes through these four functions.
   Each maps 1:1 onto an endpoint in specs/api-contract.md. They are async;
   every caller awaits them.
   ------------------------------------------------------------------------ */

/** Location is a query param on every endpoint — distance_m is computed per
 *  request from it, never stored. */
function locParams(extra = {}) {
  const { lat, lng } = activeLocation();
  return new URLSearchParams({ lat, lng, ...extra }).toString();
}

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

/** GET /api/popular → { talked_about, controversial, top_rated } */
async function getPopular() {
  return api(`/api/popular?${locParams()}`);
}

/** GET /api/restaurants → { restaurants }, nearest first, each with top_dish */
async function getRestaurants() {
  return api(`/api/restaurants?${locParams()}`);
}

/** GET /api/search?q= → { query, result_type, matched_on, primary, all, secondary }
 *
 *  Intent routing lives in the backend (backend/app/search.py), which is a
 *  line-for-line port of the scoring this function used to do here. The rule is
 *  documented in specs/api-contract.md; change it in both places or neither.
 */
async function search(q) {
  if (!norm(q)) return null;
  return api(`/api/search?${locParams({ q })}`);
}

/** GET /api/restaurants/{id} → { restaurant, dishes } */
async function getRestaurant(id) {
  return api(`/api/restaurants/${encodeURIComponent(id)}?${locParams()}`);
}

/** GET /api/dishes/{id} → { dish, quotes, also_at } */
async function getDish(id) {
  return api(`/api/dishes/${encodeURIComponent(id)}?${locParams()}`);
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
  // Every Dish carries its restaurant nested, so there is nothing to look up.
  const r = d.restaurant;
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
  // The API computes the best-scoring dish per restaurant; fixtures carry none.
  const top = r.top_dish || null;
  return `<button class="rest" data-rest="${r.id}">
    <span class="plate"><span>${esc(r.cuisine || "Restaurant")}</span></span>
    <span class="rest-body">
      <span class="rest-name">${esc(r.name)}</span>
      <span class="rest-meta">${r.neighborhood ? esc(r.neighborhood) + " · " : ""}${distanceLabel(r.distance_m)}</span>
      ${r.hours_today ? `<span class="rest-hours">${esc(r.hours_today)}</span>` : ""}
      <span class="rest-top">
        ${top ? `Top dish: <b>${esc(top.name)}</b> · ${top.score}%` : "No dishes scored yet"}
      </span>
    </span>
  </button>`;
}

function grid(dishes, opts) {
  return `<div class="grid">${dishes.map((d) => dishCard(d, opts)).join("")}</div>`;
}

function sentimentVar(label) {
  return label === "positive" ? "pos" : label === "negative" ? "neg" : "split";
}

/* --- views --------------------------------------------------------------- */

let activeTab = "controversial";

async function viewHome() {
  $("#head-search").hidden = true;
  const [pop, { restaurants }] = await Promise.all([getPopular(), getRestaurants()]);
  knownRestaurantCount = restaurants.length;
  const tabs = [
    ["controversial", "Controversial", "High volume, split opinion. The dishes people argue about."],
    ["talked_about",  "Most talked about", "Ranked by how often a dish comes up across all sources."],
    ["top_rated",     "Top rated", "Highest share of positive mentions, among dishes clearing the minimum."]
  ];
  const current = tabs.find((t) => t[0] === activeTab);

  const loc = activeLocation();
  const nearWord = esc(loc.name);

  $("#view").innerHTML = `
    <section class="hero">
      <div class="wrap">
        <h1>Stop guessing <em>what to order.</em></h1>
        <p>DishIt tells you what people are saying and what dishes are worth ordering.</p>
        <form class="search" id="hero-search">
          <input type="search" id="hero-q" placeholder="Try a dish, a restaurant, or a cuisine" aria-label="Search dishes, restaurants, or cuisines">
          <button type="submit">Search</button>
        </form>
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
    </section>

    <section class="section">
      <div class="wrap">
        <div class="section-head"><h2>Restaurants nearby</h2></div>
        <p class="section-note">${restaurants.length} restaurants near ${nearWord}, sorted by distance.</p>
        <div class="grid-r">${restaurants.map(restCard).join("")}</div>
      </div>
    </section>`;
}

async function viewResults(q) {
  $("#head-search").hidden = false;
  $("#head-q").value = q;
  const res = await search(q);

  if (!res || !res.primary.length) {
    $("#view").innerHTML = `
      <div class="wrap">
        <div class="results-head">
          <button class="back" id="back-btn">← Home</button>
          <h2>No matches for “${esc(q)}”</h2>
        </div>
        <div class="empty">
          <h3>Nothing in range matched that</h3>
          <p>We only cover ${knownRestaurantCount ?? "a handful of"} restaurants near ${esc(activeLocation().name)} right now, so plenty of real dishes aren't in here yet.</p>
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

  const showing = window.__forceType || res.result_type;
  const items = showing === "dishes" ? res.all.dishes : res.all.restaurants;

  $("#view").innerHTML = `
    <div class="wrap">
      <div class="results-head">
        <button class="back" id="back-btn">← Home</button>
        <h2>${esc(q)}</h2>
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

async function viewRestaurant(id) {
  $("#head-search").hidden = false;
  const { restaurant: r, dishes } = await getRestaurant(id);
  const scored = dishes.filter((d) => d.mention_count >= THRESHOLD);

  $("#view").innerHTML = `
    <div class="wrap">
      <div class="detail-head">
        <button class="back" id="back-btn">← Back</button>
        <h2>${esc(r.name)}</h2>
        <p class="detail-sub">${esc(r.cuisine)}${r.neighborhood ? ` · ${esc(r.neighborhood)}` : ""} · ${esc(r.cross_street)} · ${distanceLabel(r.distance_m)}${r.hours_today ? ` · ${esc(r.hours_today)}` : ""}</p>
      </div>
      ${scored.length
        ? `<p class="section-note">${scored.length} dishes have enough mentions to score, ranked by how often they come up.</p>${grid(dishes, { hideWhere: true })}`
        : `<div class="empty"><h3>Not enough written about this one yet</h3><p>We found mentions of ${dishes.length} dish${dishes.length === 1 ? "" : "es"} here, but none clear the ${THRESHOLD}-mention minimum, so there's nothing we'd stand behind ranking.</p></div>`}
    </div>`;
}

/* --- dish modal ---------------------------------------------------------- */

async function openDish(id) {
  const { dish: d, quotes, also_at } = await getDish(id);
  const r = d.restaurant;
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
                        <span class="pct" style="color:var(--${sentimentVar(d.sentiment.label)})">${d.sentiment.score}%</span>
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
                const xr = x.restaurant;
                const isThis = x.id === d.id;
                return `<button class="alsoat-row ${isThis ? "is-this" : ""}" data-dish="${x.id}">
                  <span class="alsoat-pct" style="color:var(--${sentimentVar(x.sentiment.label)})">${x.sentiment.score}%</span>
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

// Views are async now, so every navigation is too. Failures surface in the view
// rather than as a silent unhandled rejection with a blank page.
async function go(view, arg) {
  window.__forceType = null;
  closeDish();
  $("#loc-name").textContent = activeLocation().name;
  try {
    if (view === "results") await viewResults(arg);
    else if (view === "restaurant") await viewRestaurant(arg);
    else await viewHome();
  } catch (err) {
    showError(err);
  }
  window.scrollTo(0, 0);
}

function showError(err) {
  $("#view").innerHTML = `<div class="wrap"><div class="empty">
    <h3>Couldn't reach the API</h3>
    <p>${esc(err.message || String(err))}</p>
    <p class="section-note">Start it with <code>uv run uvicorn app.main:app --app-dir backend</code>,
    which serves this page and the API together.</p>
  </div></div>`;
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
  if (dish) { openDish(dish.dataset.dish).catch(showError); return; }

  const rest = t.closest("[data-rest]");
  if (rest) { go("restaurant", rest.dataset.rest); return; }

  const tab = t.closest("[data-tab]");
  if (tab) { activeTab = tab.dataset.tab; viewHome().catch(showError); return; }

  const force = t.closest("[data-force]");
  if (force) {
    window.__forceType = force.dataset.force;
    viewResults($("#head-q").value.trim()).catch(showError);
    return;
  }

  if (t.closest("#modal-x") || t.id === "scrim") { closeDish(); return; }
  if (t.closest("#back-btn") || t.closest("#home-link")) { e.preventDefault(); go("home"); return; }

  if (t.closest("#loc-btn")) {
    locIndex = (locIndex + 1) % LOCATIONS.length;
    go("home");
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

go("home");
