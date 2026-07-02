# GHRM catalogue import — VBWD-platform public repos

Data-exchange import that populates the GHRM software catalogue from **every
public repo under `github.com/VBWD-platform`** (97 repos, snapshot 2026-07-02).

Each repo becomes one **single-repo** `GhrmSoftwarePackage`, linked to its own
free yearly subscription plan (GHRM requires every package to point at a
`TarifPlan`, so the plans are generated too).

## Files (import IN THIS ORDER)

The importer resolves links by **slug**, so dependencies must exist first:

| Order | File | Entity | Notes |
|-------|------|--------|-------|
| 1 | `1_subscription_categories.json` | `subscription_categories` | 5 categories: backend, fe-user, fe-admin, mobile, payments |
| 2 | `2_subscription_plans.json` | `subscription_plans` | 97 free (`price 0`, `YEARLY`) plans, slug `ghrm-<repo>`, linked to categories |
| 3 | `3_ghrm_packages.json` | `ghrm_packages` | 97 packages, natural key = repo name, linked to plans by `tariff_plan_slug` |

Importing packages **before** their plans yields one "no tariff plan with slug…"
error per orphaned row (the batch does not crash) — just re-run step 3 after
step 2.

## How to import

Admin UI → **Settings → Import / Export**, upload each file to its matching
entity (upsert mode). All rows upsert by slug, so re-importing is idempotent.

## What the rows contain

- `collaborator_permission: "pull"` — least-privilege read access (these are
  public repos; the catalogue is a showcase, not a private-repo gate).
- `github_owner: "VBWD-platform"`, `github_repo: <repo>`,
  `github_protected_branch: "main"`.
- `package_kind: "single"` — one repo per package (no bundles).
- Descriptions come from each repo's GitHub description; icon is the generic
  GitHub mark. Secrets (`sync_api_key`, `github_installation_id`) are never in
  the file — the model mints a fresh `sync_api_key` on create.

## Caveats

- **~97 free plans appear in the subscription catalogue.** This is inherent to
  GHRM (a package must own a plan). The `ghrm-` slug prefix keeps them distinct.
- **Only `backend`, `fe-user`, `fe-admin` have catalogue pages by default.**
  The plugin's `config.json` `software_category_slugs` is
  `["backend","fe-user","fe-admin"]`. To surface the `mobile` and `payments`
  categories as their own pages, add them there and re-run `populate_ghrm.py`
  (or add the CMS category pages manually).

## Regenerating

```bash
gh api "orgs/VBWD-platform/repos?per_page=100&type=public" --paginate \
  --jq '[.[] | select(.private==false) | {name, description:(.description//""), default_branch}]' \
  > repos.json
REPOS_JSON=repos.json OUT_DIR=. python3 gen_ghrm_import.py
```
