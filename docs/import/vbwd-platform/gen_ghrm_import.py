#!/usr/bin/env python3
"""Generate GHRM data-exchange import files from the public VBWD-platform repos.

Produces three VBWD-standard envelopes, to be imported in this order:
    1. subscription_categories.json  (plan categories the plans link to)
    2. subscription_plans.json       (one free yearly plan per package)
    3. ghrm_packages.json            (one single-repo package per repo)

Source: github.com/VBWD-platform public repos (fetched via `gh api`).
"""
import json
import os

SRC = os.environ.get("REPOS_JSON")
OUT_DIR = os.environ["OUT_DIR"]

with open(SRC) as f:
    repos = json.load(f)

OWNER = "VBWD-platform"

# ── Category classification ────────────────────────────────────────────────
# One primary surface category per repo, plus a "payments" tag for gateways.
CATEGORIES = {
    "backend": ("Backend", "Server-side Flask/Python plugins and the core API.", 10),
    "fe-user": ("User Frontend", "Vue user-facing app plugins and shared UI.", 20),
    "fe-admin": ("Admin Frontend", "Vue admin backoffice plugins.", 30),
    "mobile": ("Mobile", "Android (Kotlin/Compose) and iOS (Swift) SDK + plugins.", 40),
    "payments": ("Payments", "Payment-gateway integrations.", 50),
}

PAYMENT_FRAGMENTS = (
    "stripe",
    "paypal",
    "yookassa",
    "c2p2",
    "truemoney",
    "mercado-pago",
    "toss-payments",
    "conekta",
    "promptpay",
    "token-payment",
    "invoice",
)

# Icons per surface (generic, no fabricated per-product art).
GITHUB_MARK = (
    "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png"
)


def primary_category(name: str) -> str:
    if name.startswith("vbwd-fe-user"):
        return "fe-user"
    if name.startswith("vbwd-fe-admin"):
        return "fe-admin"
    if name == "vbwd-fe-core":
        return "fe-user"
    if name.startswith(("vbwd-android", "vbwd-ios")):
        return "mobile"
    # vbwd-plugin-*, vbwd-backend, and anything else server-side
    return "backend"


def categories_for(name: str) -> list:
    cats = [primary_category(name)]
    if any(frag in name for frag in PAYMENT_FRAGMENTS):
        cats.append("payments")
    return cats


def human_name(name: str) -> str:
    """Derive a readable catalogue label + surface suffix from the repo name."""
    surface = ""
    core = name
    if name.startswith("vbwd-fe-user-plugin-"):
        core = name[len("vbwd-fe-user-plugin-") :]
        surface = " — User UI"
    elif name.startswith("vbwd-fe-admin-plugin-"):
        core = name[len("vbwd-fe-admin-plugin-") :]
        surface = " — Admin UI"
    elif name.startswith("vbwd-fe-user"):
        core = name[len("vbwd-fe-user") :].lstrip("-") or "core"
        surface = " — User UI"
    elif name.startswith("vbwd-fe-admin"):
        core = name[len("vbwd-fe-admin") :].lstrip("-") or "core"
        surface = " — Admin UI"
    elif name.startswith("vbwd-fe-core"):
        core = "core"
        surface = " — Shared UI"
    elif name.startswith("vbwd-android-plugin-"):
        core = name[len("vbwd-android-plugin-") :]
        surface = " — Android"
    elif name.startswith("vbwd-android"):
        core = name[len("vbwd-android") :].lstrip("-") or "core"
        surface = " — Android"
    elif name.startswith("vbwd-ios-plugin-"):
        core = name[len("vbwd-ios-plugin-") :]
        surface = " — iOS"
    elif name.startswith("vbwd-ios"):
        core = name[len("vbwd-ios") :].lstrip("-") or "core"
        surface = " — iOS"
    elif name.startswith("vbwd-plugin-"):
        core = name[len("vbwd-plugin-") :]
    elif name.startswith("vbwd-backend"):
        core = "backend"
    elif name.startswith("vbwd-"):
        core = name[len("vbwd-") :]
    label = core.replace("-", " ").replace("_", " ").title()
    return f"{label}{surface}"


# ── Build rows ──────────────────────────────────────────────────────────────
repos = sorted(repos, key=lambda r: r["name"])
used_categories = set()
plan_rows = []
package_rows = []

for i, repo in enumerate(repos):
    name = repo["name"]
    desc = (repo.get("description") or "").strip()
    label = human_name(name)
    if not desc:
        desc = f"{label} — public VBWD-platform repository ({OWNER}/{name})."
    cats = categories_for(name)
    used_categories.update(cats)
    plan_slug = f"ghrm-{name}"
    sort_order = 100 + i

    plan_rows.append(
        {
            "slug": plan_slug,
            "name": label,
            "description": desc,
            "price": 0.0,
            "billing_period": "YEARLY",
            "features": {},
            "trial_days": 0,
            "is_active": True,
            "sort_order": sort_order,
            "category_slugs": cats,
        }
    )

    package_rows.append(
        {
            "slug": name,
            "name": label,
            "author_name": OWNER,
            "icon_url": GITHUB_MARK,
            "github_owner": OWNER,
            "github_repo": name,
            "description": desc,
            "github_protected_branch": repo.get("default_branch", "main"),
            "tech_specs": {},
            "related_slugs": [],
            "is_active": True,
            "sort_order": sort_order,
            "collaborator_permission": "pull",
            "package_kind": "single",
            "bundle_repos": [],
            "tariff_plan_slug": plan_slug,
        }
    )

category_rows = []
for slug in sorted(used_categories, key=lambda s: CATEGORIES[s][2]):
    cname, cdesc, csort = CATEGORIES[slug]
    category_rows.append(
        {
            "slug": slug,
            "name": cname,
            "description": cdesc,
            "is_single": False,
            "sort_order": csort,
        }
    )


def envelope(entity_key, rows):
    return {
        "vbwd_export": entity_key,
        "version": 1,
        "instance": "vbwd.cc",
        "format": "json",
        entity_key: rows,
    }


os.makedirs(OUT_DIR, exist_ok=True)
files = {
    "1_subscription_categories.json": ("subscription_categories", category_rows),
    "2_subscription_plans.json": ("subscription_plans", plan_rows),
    "3_ghrm_packages.json": ("ghrm_packages", package_rows),
}
for fname, (key, rows) in files.items():
    with open(os.path.join(OUT_DIR, fname), "w") as f:
        json.dump(envelope(key, rows), f, indent=2, ensure_ascii=False)
        f.write("\n")

print(f"categories: {len(category_rows)} -> {sorted(used_categories)}")
print(f"plans:      {len(plan_rows)}")
print(f"packages:   {len(package_rows)}")
print(f"written to: {OUT_DIR}")
