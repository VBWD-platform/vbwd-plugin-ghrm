# GHRM Catalogue, Category & Detail Pages — How They Work

The GHRM software catalogue is **not** a bespoke set of Vue pages. It is a thin
layer of route registrations on top of the CMS: every catalogue URL renders a
CMS **post** (`cms_post`) through the shared `CmsPage.vue`, and the GHRM-specific
content is injected by two CMS **widgets** (`GhrmCatalogueContent`,
`GhrmPackageDetail`). This doc explains the full chain so you can reason about
404s, add categories, or change the URL scheme without guessing.

See also the CMS-side companion: `plugins/cms/docs/developer/page-slug-resolution.md`.

---

## The three page kinds

| URL (example) | Vue route name | CMS post slug fetched | GHRM widget in the layout |
|---|---|---|---|
| `/software` | `ghrm-category-index` | `software` (= `catalogue_page_slug`) | `GhrmCatalogueContent` |
| `/software/mobile` | `ghrm-package-list` | `software/mobile` (= `catalogue_page_slug` + `/` + `category_slug`) | `GhrmCatalogueContent` |
| `/software/mobile/vbwd-android` | `ghrm-package-detail` | `ghrm-software-detail` (= `detail_page_slug`, **static**) | `GhrmPackageDetail` |

The important asymmetry:

- **Index and category** pages embed the URL segment into the CMS slug. One CMS
  post exists **per category** (`software`, `software/mobile`, `software/backend`…).
- **Detail** pages do **not**. All packages share **one** CMS post
  (`detail_page_slug`). The route matches `/…/:category_slug/:package_slug`, but
  the fetched slug is the fixed `detail_page_slug`. The `package_slug` is read
  from the route params by the `GhrmPackageDetail` widget, which then calls
  `GET /api/v1/ghrm/packages/<package_slug>` to load the actual software.

---

## Config is the single source of truth for the URL scheme

The frontend does **not** hardcode `/software` or the detail slug. It fetches
them at install time:

```
GET /api/v1/ghrm/config
→ { "catalogue_page_slug": "software", "detail_page_slug": "ghrm-software-detail", ... }
```

Served by `get_public_config()` in `src/routes.py`. The values come from
`_cfg()` → `plugin._config` (see the two-config-files gotcha below), falling
back to the plugin's bundled `config.json` if the plugin isn't loaded.

Relevant config keys (`plugins/ghrm/config.json` defaults):

| Key | Default | Purpose |
|---|---|---|
| `software_catalogue_cms_page_slug` | `ghrm-software-catalogue` | CMS post slug for the catalogue index; also the **URL base** for all catalogue routes |
| `software_detail_cms_page_slug` | `ghrm-software-detail` | CMS post slug fetched for **every** package detail page |
| `software_category_slugs` | `["backend","fe-user","fe-admin"]` | One category listing page is seeded per slug |
| `software_catalogue_cms_layout_slug` | `ghrm-software-catalogue` | CMS layout the catalogue/category pages use |
| `software_detail_cms_layout_slug` | `ghrm-software-detail` | CMS layout the detail page uses |

---

## Frontend route registration

`vbwd-fe-user/plugins/ghrm/index.ts`, in `install(sdk)`:

```ts
const base = catalogueBase();          // '/' + catalogue_page_slug, e.g. '/software'

sdk.addRoute({ path: base,                              // /software
  name: 'ghrm-category-index',
  component: CmsPage, props: { slug: cataloguePageSlug } });

sdk.addRoute({ path: `${base}/:category_slug`,          // /software/mobile
  name: 'ghrm-package-list',
  component: CmsPage,
  props: r => ({ slug: `${cataloguePageSlug}/${r.params.category_slug}` }) });

sdk.addRoute({ path: `${base}/:category_slug/:package_slug`,   // /software/mobile/vbwd-android
  name: 'ghrm-package-detail',
  component: CmsPage, props: { slug: detailPageSlug } });      // ← STATIC slug
```

`base` is held in `src/catalogueBase.ts` so route registration and widget links
agree. It defaults to `/category` until the config fetch resolves; `install()`
never throws (a failed fetch keeps the documented defaults) so the app always
boots.

---

## What the seed creates

`src/bin/populate_ghrm.py` (`seed_catalog`) creates, idempotently:

- **Layouts** — `ghrm-software-catalogue`, `ghrm-software-detail` (each with a
  `header` / `breadcrumbs` / GHRM-widget / `footer` area).
- **Widgets** — `ghrm-categories` → `GhrmCatalogueContent`,
  `ghrm-software-detail` → `GhrmPackageDetail` (both `vue-component`), assigned
  into the matching layout area.
- **Template pages** — `catalogue_page_slug` (status **draft**) and
  `detail_page_slug` (status **published**). These carry the layout/style used
  when GHRM renders.
- **Content pages** (all **published**) —
  - `software` (dark style) — an alternate root entry point,
  - `category` (light style) — the default catalogue root,
  - `category/<cat_slug>` for each entry in `software_category_slugs`.

> The **detail** post is published by the seed; the **catalogue** template is
> seeded as a draft (the `software` / `category` content pages are the live
> entry points). If you point `catalogue_page_slug` at the draft template
> without publishing it, the index 404s.

---

## ⚠️ The two-config-files gotcha (this caused a real prod 404)

There are **two** config files and they are read by **different** code paths:

| File | Read by | Used for |
|---|---|---|
| `plugins/ghrm/config.json` | `populate_ghrm.py` (`_CONFIG_PATH`) | which slugs the **seed** creates |
| `plugins/config.json` (aggregated, key `"ghrm"`) | `PluginManager` → `plugin._config` → `_cfg()` | which slug the **runtime** `/ghrm/config` reports, i.e. what the frontend **fetches** |

If you override `software_detail_cms_page_slug` in **`plugins/config.json`**
(e.g. to `package`) but the **bundled** `plugins/ghrm/config.json` still says
`ghrm-software-detail`, then:

- the seed creates a CMS post `ghrm-software-detail`,
- the frontend fetches `GET /cms/posts/package` → **404** → CmsPage renders its
  in-app "Page not found".

`/software` and `/software/mobile` keep working (their content pages were
seeded), so only the detail level breaks — which is exactly the failure
signature.

**Rule:** the runtime slug (`plugins/config.json`) and the bundled slug
(`plugins/ghrm/config.json`) must agree, **and** you must re-run
`populate_ghrm.py` after changing either so a matching published CMS post
exists. Note also that `plugins/config.json` is the boot source — the
`var/plugins/backend-plugins-config.json` mirror is only read by the admin
frontend-plugins route, not by `_cfg()`. Changing config requires an **API
restart** to reload `plugin._config`.

---

## Debugging a catalogue 404

```bash
# 1. What slug does the frontend actually fetch for detail pages?
curl -s http://localhost:8080/api/v1/ghrm/config
#    → note catalogue_page_slug and detail_page_slug

# 2. Does a published CMS post exist at that slug?
curl -s -o /dev/null -w "%{http_code}\n" \
  http://localhost:8080/api/v1/cms/posts/<detail_page_slug>
#    404 here → the post is missing/unpublished  → seed/config mismatch (above)

# 3. Does the package itself exist?
curl -s -o /dev/null -w "%{http_code}\n" \
  http://localhost:8080/api/v1/ghrm/packages/<package_slug>
```

A router-level miss (blank/NotFound) vs. the **branded CMS 404** tells you where
the failure is: the branded 404 means the route matched and `CmsPage` mounted,
so the problem is the CMS post fetch (step 2), not the Vue route.
