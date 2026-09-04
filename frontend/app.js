/* ==========================================================================
   DishIt — frontend

   Renders from the live backend (/api/*, see specs/api-contract.md and
   backend/app/main.py). frontend/fixtures.json is kept in the repo as
   sample/reference data matching the same shapes, but is no longer loaded
   at runtime — everything below fetches from the real API.
   ========================================================================== */

// Nav-history state for the restaurant view's "back" button: which query (if
// any) it should return to instead of always going Home.
let currentView = "home";
let lastQuery = null;
let restReturnsToResults = false;

// Scope is Morningside Heights only — no location switching.
const HOME_LOCATION = { name: "Morningside Heights", lat: 40.8075, lng: -73.9626 };

// No live endpoint reports the mention threshold, so this mirrors
// backend/app/main.py's THRESHOLD constant — kept in sync deliberately, same
// as the search-scoring logic used to be before that moved server-side.
const THRESHOLD = 5;

// Shared icon-only search-button markup (hero form; the head form's copy lives
// directly in index.html since it's static).
const SEARCH_BUTTON = `<button type="submit" aria-label="Search">
  <svg class="search-icon" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.6"/>
    <path d="M17 17l-4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
  </svg>
</button>`;

/* --- utilities ----------------------------------------------------------- */

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Joins already-escaped fragments with " · ", dropping any that are falsy —
// several real-data fields (neighborhood, cuisine, cross_street) can be null,
// and esc(null) would otherwise render the literal string "null".
function joinMeta(...parts) {
  return parts.filter(Boolean).join(" · ");
}

function activeLocation() {
  return HOME_LOCATION;
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
   Thin fetch wrappers, one per endpoint in specs/api-contract.md. All the
   actual routing/scoring/aggregation logic lives server-side now.
   ------------------------------------------------------------------------ */

async function apiGet(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} → ${response.status}`);
  return response.json();
}

const locationQuery = () => `lat=${HOME_LOCATION.lat}&lng=${HOME_LOCATION.lng}`;

/** GET /api/popular → { talked_about, controversial, top_rated } */
async function getPopular() {
  return apiGet(`/api/popular?${locationQuery()}`);
}

/** GET /api/search?q= → { query, result_type, matched_on, primary, secondary } */
async function search(q) {
  const query = q.trim();
  if (!query) return null;
  return apiGet(`/api/search?q=${encodeURIComponent(query)}&${locationQuery()}`);
}

/** GET /api/restaurants/{id} → { restaurant, dishes } */
async function getRestaurant(id) {
  return apiGet(`/api/restaurants/${id}?${locationQuery()}`);
}

/** GET /api/dishes/{id} → { dish, quotes, also_at } */
async function getDish(id) {
  return apiGet(`/api/dishes/${id}?${locationQuery()}`);
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

// Labels sized proportionally to their own segment (flex-basis matches the
// same percentages bar() uses), so position still tracks the bar. Rather than
// centering every label (which can bleed past the row's edge for a long label
// on a thin edge segment), the leftmost label grows rightward from the left
// edge and the rightmost grows leftward from the right edge — only a middle
// label (when all three segments are present) is actually centered. That
// makes edge overflow structurally impossible regardless of label length.
function splitRow(s) {
  const total = s.positive + s.negative + s.neutral || 1;
  const segs = [
    { cls: "a", pct: (s.positive / total) * 100, label: `${s.positive} positive` },
    { cls: "x", pct: (s.neutral / total) * 100, label: `${s.neutral} neutral` },
    { cls: "b", pct: (s.negative / total) * 100, label: `${s.negative} negative` }
  ];
  const present = segs.filter((seg) => seg.pct > 0);
  const spans = present
    .map((seg, i) => {
      const edge = present.length === 1 ? "mid"
        : i === 0 ? "edge-l"
        : i === present.length - 1 ? "edge-r" : "mid";
      return `<span class="split-label ${seg.cls} ${edge}" style="flex-basis:${seg.pct}%">${seg.label}</span>`;
    })
    .join("");
  return `<div class="split-row">${spans}</div>`;
}

// Edge-anchoring (above) only guarantees the outer two labels can't bleed past
// the row's own left/right edges — it does nothing to stop a thin *middle*
// segment's centered text from overlapping into a neighbor, which still
// happens whenever neutral is a sliver. The only real fix is measuring actual
// rendered widths after layout and nudging apart anything that still
// overlaps, so call this once right after inserting a `.split-row` into the
// DOM (see openDish()).
function fixSplitLabelOverlap(scopeEl) {
  const row = scopeEl.querySelector(".split-row");
  if (!row) return;
  const labels = [...row.querySelectorAll(".split-label")];
  if (labels.length < 2) return;

  const GAP = 14;
  const rowLeft = row.getBoundingClientRect().left;
  const edges = labels.map((el) => {
    const r = el.getBoundingClientRect();
    return { el, left: r.left - rowLeft, right: r.right - rowLeft, shift: 0 };
  });

  for (let i = 1; i < edges.length; i++) {
    const minLeft = edges[i - 1].right + edges[i - 1].shift + GAP;
    if (edges[i].left + edges[i].shift < minLeft) {
      edges[i].shift = minLeft - edges[i].left;
    }
  }
  for (let i = edges.length - 2; i >= 0; i--) {
    const maxRight = edges[i + 1].left + edges[i + 1].shift - GAP;
    if (edges[i].right + edges[i].shift > maxRight) {
      edges[i].shift = maxRight - edges[i].right;
    }
  }
  edges.forEach(({ el, shift }) => {
    if (Math.abs(shift) > 0.5) el.style.transform = `translateX(${shift}px)`;
  });
}

function dishCard(d, opts = {}) {
  const thin = d.mention_count < THRESHOLD;
  const cls = thin ? "s-thin" : "s-" + d.sentiment.label;
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
      ${opts.hideWhere ? "" : `<span class="dish-where">${joinMeta(esc(r.name), r.cuisine && esc(r.cuisine))}</span>`}
      ${bar(d.sentiment)}
      <span class="meta">
        <span><b>${d.mention_count}</b> mentions</span>
        <span>${d.source_mix.critic} critic · ${d.source_mix.public} public</span>
      </span>
      ${thin ? `<span class="thin-note">Not enough mentions to score yet.</span>` : ""}
    </span>
  </button>`;
}

// No "top dish" preview here (unlike the old fixture-mode version) — a bare
// Restaurant object from /api/search or the derived "nearby" list carries no
// per-restaurant dish data, and there's no live endpoint that returns one
// without an extra fetch per card.
function restCard(r) {
  const meta = joinMeta(
    r.neighborhood && esc(r.neighborhood),
    r.distance_m != null ? distanceLabel(r.distance_m) : null
  );
  return `<button class="rest" data-rest="${r.id}">
    <span class="plate">${r.cuisine ? `<span>${esc(r.cuisine)}</span>` : ""}</span>
    <span class="rest-body">
      <span class="rest-name">${esc(r.name)}</span>
      ${meta ? `<span class="rest-meta">${meta}</span>` : ""}
      ${r.hours_today ? `<span class="rest-hours">${esc(r.hours_today)}</span>` : ""}
    </span>
  </button>`;
}

function grid(dishes, opts) {
  return `<div class="grid">${dishes.map((d) => dishCard(d, opts)).join("")}</div>`;
}

// Hand-drawn tangle-of-lines ball, same inline-SVG-icon approach used
// elsewhere in this file — no external image request.
const TUMBLEWEED_SVG = `<svg class="tumbleweed-icon" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <g stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
    <circle cx="32" cy="32" r="26"/>
    <path d="M12 20c8-10 20-14 30-8 9 5 12 16 8 25-4 9-15 14-25 11"/>
    <path d="M18 12c6 6 8 16 4 26-4 9-13 15-22 14"/>
    <path d="M40 10c2 10-2 20-10 27-7 6-17 8-25 4"/>
    <path d="M46 22c6 4 9 13 6 21-3 8-11 13-20 12"/>
    <path d="M10 34c10 2 20-2 26-10 5-7 6-16 2-24"/>
  </g>
</svg>`;

function tumbleweedEmpty(bodyHtml) {
  return `<div class="empty">
    <div class="tumbleweed">${TUMBLEWEED_SVG}</div>
    <h3>Nothing but tumbleweeds here…</h3>
    ${bodyHtml}
  </div>`;
}

function sentimentVar(label) {
  return label === "positive" ? "pos" : label === "negative" ? "neg" : "split";
}

const LOADING_HTML = `<div class="wrap"><p class="section-note">Loading…</p></div>`;
function backendErrorHtml() {
  return `<div class="wrap"><div class="empty"><h3>Couldn't reach the backend</h3><p>Confirm the API server is running and reload.</p></div></div>`;
}

/* --- views --------------------------------------------------------------- */

let activeTab = "controversial";

async function viewHome() {
  $("#head-search").hidden = true;
  $("#view").innerHTML = LOADING_HTML;

  let pop;
  try {
    pop = await getPopular();
  } catch {
    $("#view").innerHTML = backendErrorHtml();
    return;
  }

  const tabs = [
    ["controversial", "Controversial", ""],
    ["talked_about",  "Most talked about", "Ranked by how often a dish comes up across all sources."],
    ["top_rated",     "Top rated", "Highest share of positive mentions, among dishes clearing the minimum."]
  ];
  const current = tabs.find((t) => t[0] === activeTab);

  const loc = activeLocation();
  const nearWord = esc(loc.name);

  // No live endpoint lists "all restaurants nearby" independent of dish data,
  // so this derives an approximate nearby list from the restaurants embedded
  // in the three popular-dish lists (deduped by id) rather than a real
  // restaurant-listing endpoint.
  const seen = new Map();
  for (const list of [pop.talked_about, pop.controversial, pop.top_rated]) {
    for (const d of list) seen.set(d.restaurant.id, d.restaurant);
  }
  const nearbyRestaurants = [...seen.values()]
    .sort((a, b) => (a.distance_m ?? Infinity) - (b.distance_m ?? Infinity));

  $("#view").innerHTML = `
    <section class="hero">
      <div class="wrap">
        <h1>Stop guessing <em>what to order.</em></h1>
        <p>DishIt tells you what people are saying and what dishes are worth ordering.</p>
        <form class="search" id="hero-search">
          <input type="search" id="hero-q" placeholder="Try a dish, a restaurant, or a cuisine" aria-label="Search dishes, restaurants, or cuisines">
          ${SEARCH_BUTTON}
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
        ${current[2] ? `<p class="section-note">${current[2]}</p>` : ""}
        ${pop[activeTab].length
          ? grid(pop[activeTab])
          : `<div class="empty"><h3>Nothing here yet</h3><p>No dishes in this neighborhood have enough mentions to qualify for this list.</p></div>`}
      </div>
    </section>

    <section class="section">
      <div class="wrap">
        <div class="section-head"><h2>Restaurants nearby</h2></div>
        ${nearbyRestaurants.length
          ? `<p class="section-note">${nearbyRestaurants.length} restaurants near ${nearWord}, sorted by distance.</p>
             <div class="grid-r">${nearbyRestaurants.map(restCard).join("")}</div>`
          : `<div class="empty"><h3>Nothing here yet</h3><p>No restaurants near ${nearWord} have a dish with enough mentions yet.</p></div>`}
      </div>
    </section>`;
}

async function viewResults(q) {
  $("#head-search").hidden = false;
  $("#head-q").value = q;
  lastQuery = q;
  $("#view").innerHTML = LOADING_HTML;

  let res;
  try {
    res = await search(q);
  } catch {
    $("#view").innerHTML = backendErrorHtml();
    return;
  }

  if (!res || !res.primary.length) {
    $("#view").innerHTML = `
      <div class="wrap">
        <div class="results-head">
          <button class="back" id="back-btn">← Home</button>
          <h2>No matches for “${esc(q)}”</h2>
        </div>
        ${tumbleweedEmpty(`
          <p>We only cover restaurants near ${esc(activeLocation().name)} right now, so plenty of real dishes aren't in here yet.</p>
          <div class="hero-tries">
            <span>Try</span>
            <button class="chip" data-q="cacio e pepe">cacio e pepe</button>
            <button class="chip" data-q="tacos">tacos</button>
            <button class="chip" data-q="Italian">Italian</button>
          </div>
        `)}
      </div>`;
    return;
  }

  const showing = window.__forceType || res.result_type;
  // The backend has no "every match of the other type" field, only a
  // 4-item-capped secondary list — the manual toggle works within that.
  const items = showing === res.result_type ? res.primary : (res.secondary?.items ?? []);

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
        : tumbleweedEmpty("")}

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
  $("#view").innerHTML = LOADING_HTML;

  let restaurant, dishes;
  try {
    ({ restaurant, dishes } = await getRestaurant(id));
  } catch {
    $("#view").innerHTML = backendErrorHtml();
    return;
  }

  const scored = dishes.filter((d) => d.mention_count >= THRESHOLD);
  const r = restaurant;
  const sub = joinMeta(
    r.cuisine && esc(r.cuisine),
    r.neighborhood && esc(r.neighborhood),
    r.cross_street && esc(r.cross_street),
    r.distance_m != null ? distanceLabel(r.distance_m) : null,
    r.hours_today && esc(r.hours_today)
  );

  $("#view").innerHTML = `
    <div class="wrap">
      <div class="detail-head">
        <button class="back" id="rest-back-btn">← Back</button>
        <h2>${esc(r.name)}</h2>
        <p class="detail-sub">${sub}</p>
      </div>
      ${scored.length
        ? `<p class="section-note">Most popular</p>${grid(dishes, { hideWhere: true })}`
        : `<div class="empty"><h3>Not enough written about this one yet</h3><p>We found mentions of ${dishes.length} dish${dishes.length === 1 ? "" : "es"} here, but none clear the ${THRESHOLD}-mention minimum, so there's nothing we'd stand behind ranking.</p></div>`}
    </div>`;
}

/* --- dish modal ---------------------------------------------------------- */

const MODAL_LOADING_HTML = `<div class="scrim" id="scrim"><div class="modal"><div class="modal-body"><p class="section-note">Loading…</p></div></div></div>`;

async function openDish(id) {
  $("#modal-root").innerHTML = MODAL_LOADING_HTML;

  let d, quotes, also_at;
  try {
    ({ dish: d, quotes, also_at } = await getDish(id));
  } catch {
    $("#modal-root").innerHTML = "";
    return;
  }

  const r = d.restaurant;
  const thin = d.mention_count < THRESHOLD;
  const elsewhere = also_at.filter((x) => x.id !== d.id);

  $("#modal-root").innerHTML = `
    <div class="scrim" id="scrim">
      <div class="modal" role="dialog" aria-modal="true" aria-label="${esc(d.name)}">
        <div class="modal-head">
          <div>
            <h3>${esc(d.name)}</h3>
            <p class="modal-where">${joinMeta(esc(r.name), r.cuisine && esc(r.cuisine), r.neighborhood && esc(r.neighborhood))}</p>
          </div>
          <button class="modal-x" id="modal-x" aria-label="Close">✕</button>
        </div>
        <div class="modal-body">

          <div>
            ${thin ? `<div class="readout"><span class="pct" style="color:var(--muted)">—</span><span class="of">not enough mentions to score yet</span></div>`
                   : `<div class="readout">
                        <span class="pct" style="color:var(--${sentimentVar(d.sentiment.label)})">${d.sentiment.score}%</span>
                        <span class="of">positive across ${d.mention_count} mentions</span>
                      </div>`}
            ${bar(d.sentiment)}
            ${splitRow(d.sentiment)}
            <span class="meta">
              <span>${d.source_mix.critic} critic · ${d.source_mix.public} public</span>
              <span>${d.on_current_menu === null ? "menu not checked" : d.on_current_menu ? "on current menu" : "not on current menu"}</span>
            </span>
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
                <cite>${esc(q.source_label)} ${q.source_url ? '<span class="original-review-link">Original review →</span>' : ""}</cite>
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
                  <span class="alsoat-name">${esc(xr.name)}${isThis ? " — you're here" : ""}<small>${joinMeta(xr.neighborhood && esc(xr.neighborhood), xr.distance_m != null ? distanceLabel(xr.distance_m) : null)}</small></span>
                  <span class="alsoat-n">${x.mention_count} mentions</span>
                </button>`;
              }).join("")}
            </div>
          </div>` : ""}

        </div>
      </div>
    </div>`;

  fixSplitLabelOverlap($("#modal-root"));
  $("#modal-x").focus();
}

function closeDish() { $("#modal-root").innerHTML = ""; }

/* --- routing + events ---------------------------------------------------- */

function go(view, arg) {
  window.__forceType = null;
  currentView = view;
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
  if (rest) {
    restReturnsToResults = currentView === "results";
    go("restaurant", rest.dataset.rest);
    return;
  }

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

  if (t.closest("#rest-back-btn")) {
    if (restReturnsToResults && lastQuery) go("results", lastQuery);
    else go("home");
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
