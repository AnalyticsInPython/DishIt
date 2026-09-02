# DishIt — clickable wireframe

**Live:** https://claude.ai/code/artifact/f1e91eaa-10f7-4db3-9bb6-e6ddb901c7bb

One self-contained file: `dishit-wireframe.html`. No build, no dependencies, no
backend. Open it locally or share the link. Republish by re-running the Artifact
step on the same file path — the URL stays put.

## What it is

A flow-and-hierarchy prototype for deciding **whether a restaurant is worth
going to, based on its individual dishes**. Deliberately grayscale: hue is
reserved for interactive affordances, so sentiment is encoded in value and
texture only. That way a design review argues about the flow and the
information model rather than the palette.

## Everything in here is invented

All 28 restaurants are fictional. Neighborhoods and cuisines are real Manhattan;
the businesses are not. Attaching made-up sentiment scores to real restaurants
would be fabricating reviews about real companies, so we don't.

## The sentiment model

Every badge a user sees is *derived* from three numbers per dish — `mentions`,
and a positive/negative/mixed split. Nothing is hand-labelled, so these
thresholds are the one place to argue about tuning. They live in `verdictFor()`
and `popularityFor()` near the top of the `<script>` block.

```
mentions < 15                      ->  "Not enough data"   (no verdict, no bar)
positive >= .75                    ->  LOVED
negative >= .50                    ->  DISLIKED
positive >= .35 && negative >= .25 ->  DIVISIVE
mixed >= .40                       ->  DIVISIVE
otherwise                          ->  MIXED

mentions 200+  Very popular    60-199  Popular
        15-59  Some buzz         < 15  Rarely discussed
```

The 15-mention floor matters: below it the app shows **no verdict at all**
rather than a confident-looking one on thin evidence.

`restaurantVerdict()` rolls a restaurant's dishes up into the one-line thesis
("Consistently strong", "Hit or miss — order carefully", …). It is dish-derived
on purpose — there is no star rating anywhere in this product.

## Editing the data

`RAW` is a flat array near the top of the `<script>`:

```js
["bocca-nera","Bocca Nera","SoHo",["Italian"],4,[
  ["Spicy Rigatoni alla Vodka",412,91,3,6],   // name, mentions, pos%, neg%, mixed%
  ...
]]
```

Percentages are normalized at load, so they don't have to sum to exactly 100.
Restaurant coordinates are derived from the neighborhood centroid plus a
deterministic jitter off the id, and walk times from there at 3 mph.

The dataset intentionally covers every case the UI has to handle: a
famous-and-beloved dish, a famous-but-divisive one, a hidden gem, a genuinely
disliked dish, and several below the confidence floor. Keep those archetypes if
you swap the data, or parts of the UI will never be exercised.

## Prototype chrome

The bar above the device frame is scaffolding, not product: **Phone 390 /
Desktop 1280** (drives real `@container` breakpoints, not a fake preview),
**Light / Dark / System**, and **Reset session**.

## Session behavior

No accounts. `localStorage` holds recent searches, recently viewed restaurants,
location, active filters, and sort — all reads and writes wrapped in try/catch,
so the app renders correctly in a private window or with site data blocked.
Reset session clears it.

## Deliberately out of scope

Review quotes and themed pros/cons, map view, neighborhood browse page,
shortlist/compare tray, dietary filters, photos, hours, reservations, and any
real data. The data shape leaves room for all of them.
