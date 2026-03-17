#!/usr/bin/env python
"""Populate GHRM data: software packages, CMS layouts, widgets, pages, and CMS category.

Reads software_category_slugs from the plugin's config.json so category
pages are always in sync with the configured slugs.

Usage (inside container):
    python /app/plugins/ghrm/src/bin/populate_ghrm.py

Safe to re-run — all upserts are idempotent (skip if slug already exists).
"""
import sys
import json
import os
from decimal import Decimal

sys.path.insert(0, "/app")

from src.extensions import Session  # noqa: E402
from src.models.tarif_plan import TarifPlan  # noqa: E402
from src.models.tarif_plan_category import TarifPlanCategory  # noqa: E402
from src.models.price import Price  # noqa: E402
from src.models.enums import BillingPeriod  # noqa: E402
from src.models.currency import Currency  # noqa: E402
from plugins.ghrm.src.models.ghrm_software_package import (  # noqa: E402
    GhrmSoftwarePackage,
)
from plugins.ghrm.src.models.ghrm_software_sync import GhrmSoftwareSync  # noqa: E402
from plugins.cms.src.models.cms_category import CmsCategory  # noqa: E402
from plugins.cms.src.models.cms_style import (  # noqa: E402, F401
    CmsStyle,
)  # required for FK resolution
from plugins.cms.src.models.cms_layout import CmsLayout  # noqa: E402
from plugins.cms.src.models.cms_widget import CmsWidget  # noqa: E402
from plugins.cms.src.models.cms_page import CmsPage  # noqa: E402
from plugins.cms.src.models.cms_layout_widget import CmsLayoutWidget  # noqa: E402

# ── Load plugin config to get category slugs ────────────────────────────────

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
with open(_CONFIG_PATH) as _f:
    _plugin_cfg = json.load(_f)

CATEGORY_SLUGS: list[str] = _plugin_cfg.get(
    "software_category_slugs", ["backend", "fe-user", "fe-admin"]
)
CATALOGUE_LAYOUT_SLUG: str = "ghrm-software-catalogue"
DETAIL_LAYOUT_SLUG: str = "ghrm-software-detail"
CATALOGUE_PAGE_SLUG: str = _plugin_cfg.get(
    "software_catalogue_cms_page_slug", "ghrm-software-catalogue"
)
DETAIL_PAGE_SLUG: str = _plugin_cfg.get(
    "software_detail_cms_page_slug", "ghrm-software-detail"
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def slug_to_label(slug: str) -> str:
    """Convert a slug like 'fe-user' → 'Fe User'."""
    return slug.replace("-", " ").title()


def get_or_create(session, model, slug: str, **kwargs):
    """Return existing row by slug, or create and flush a new one."""
    obj = session.query(model).filter_by(slug=slug).first()
    if obj:
        return obj, False
    obj = model(slug=slug, **kwargs)
    session.add(obj)
    session.flush()
    return obj, True


# ── Main ─────────────────────────────────────────────────────────────────────

session = Session()

# ── Software package definitions ──────────────────────────────────────────────
#
# Each entry creates (if not present) a TarifPlan, assigns it to categories,
# and creates a GhrmSoftwarePackage linked to that plan.
#
# For existing plugin plans created by install_demo_data.py, the plan is
# looked up by slug and only the GhrmSoftwarePackage is created.
#
# field: plan_slug, plan_name, categories, pkg_slug, pkg_name, description,
#        github_owner, github_repo, author_name, sort_order
#
SOFTWARE_PACKAGES = [
    # ── VBWD-platform plugins ─────────────────────────────────────────────────
    {
        "plan_slug": "plugin-stripe",
        "plan_name": "Stripe",
        "categories": ["backend", "fe-admin", "fe-user", "payments"],
        "pkg_slug": "vbwd-plugin-stripe",
        "pkg_name": "Stripe",
        "description": "Accept credit card payments via Stripe. Includes webhook handling, "
        "subscription sync, and a pre-built checkout UI widget.",
        "github_owner": "VBWD-platform",
        "github_repo": "vbwd-plugin-stripe",
        "author_name": "VBWD-platform",
        "sort_order": 10,
        "icon_url": "https://clipartcraft.com/images/stripe-logo-icon-8.png",
    },
    {
        "plan_slug": "plugin-paypal",
        "plan_name": "Paypal",
        "categories": ["backend", "fe-admin", "fe-user", "payments"],
        "pkg_slug": "vbwd-plugin-paypal",
        "pkg_name": "PayPal",
        "description": "PayPal Checkout and Subscriptions integration. Supports one-time "
        "payments and recurring billing with automatic invoice generation.",
        "github_owner": "VBWD-platform",
        "github_repo": "vbwd-plugin-paypal",
        "author_name": "VBWD-platform",
        "sort_order": 11,
        "icon_url": "https://www.paypalobjects.com/webstatic/icon/pp258.png",
    },
    {
        "plan_slug": "plugin-theme-switcher",
        "plan_name": "Theme-Switcher",
        "categories": ["fe-user"],
        "pkg_slug": "vbwd-fe-user-plugin-theme-switcher",
        "pkg_name": "Theme-Switcher",
        "description": "Light / dark / system theme toggle for the vbwd user frontend. "
        "Persists user preference in localStorage, zero-flash on reload.",
        "github_owner": "VBWD-platform",
        "github_repo": "vbwd-fe-user-plugin-theme-switcher",
        "author_name": "VBWD-platform",
        "sort_order": 12,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/4489/4489592.png",
    },
    {
        "plan_slug": "plugin-llm-chat",
        "plan_name": "LLM Chat",
        "categories": ["fe-user"],
        "pkg_slug": "vbwd-fe-user-plugin-chat",
        "pkg_name": "LLM Chat",
        "description": "Embeddable AI chat widget backed by any OpenAI-compatible endpoint. "
        "Supports streaming, conversation history, and custom system prompts.",
        "github_owner": "VBWD-platform",
        "github_repo": "vbwd-fe-user-plugin-chat",
        "author_name": "VBWD-platform",
        "sort_order": 13,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/4712/4712104.png",
    },
    {
        "plan_slug": "plugin-ai-tarot",
        "plan_name": "AI Tarot",
        "categories": ["fe-user", "backend", "fe-admin"],
        "pkg_slug": "vbwd-plugin-taro",
        "pkg_name": "AI Tarot",
        "description": "Full-stack Tarot reading plugin with all 78 Rider-Waite cards, "
        "AI-generated interpretations, and a beautiful animated flip UI.",
        "github_owner": "VBWD-platform",
        "github_repo": "vbwd-plugin-taro",
        "author_name": "VBWD-platform",
        "sort_order": 14,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/1913/1913744.png",
    },
    {
        "plan_slug": "plugin-import-export",
        "plan_name": "Import-Export",
        "categories": ["backend"],
        "pkg_slug": "vbwd-plugin-import-export",
        "pkg_name": "Import-Export",
        "description": "Bulk data import and export for users, subscriptions, and invoices. "
        "Supports CSV, JSON, and XLSX with column mapping and dry-run preview.",
        "github_owner": "VBWD-platform",
        "github_repo": "vbwd-plugin-import-export",
        "author_name": "VBWD-platform",
        "sort_order": 15,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/8227/8227945.png",
    },
    {
        "plan_slug": "plugin-analytics",
        "plan_name": "Analytics",
        "categories": ["backend", "fe-admin"],
        "pkg_slug": "vbwd-fe-admin-plugin-analytics-widget",
        "pkg_name": "Analytics",
        "description": "Advanced analytics dashboard with MRR, ARR, churn, ARPU, "
        "and cohort retention charts. Exports to CSV for further analysis.",
        "github_owner": "VBWD-platform",
        "github_repo": "vbwd-fe-admin-plugin-analytics-widget",
        "author_name": "VBWD-platform",
        "sort_order": 16,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/1546/1546912.png",
    },
    # ── GHRM (new plan + package) ─────────────────────────────────────────────
    {
        "plan_slug": "plugin-ghrm",
        "plan_name": "GHRM",
        "categories": ["backend", "fe-admin", "fe-user"],
        "pkg_slug": "vbwd-plugin-ghrm",
        "pkg_name": "GitHub Repo Manager",
        "description": "Subscription-gated software distribution via GitHub Releases. "
        "Buyers get a deploy token; GitHub Actions pushes releases automatically.",
        "github_owner": "VBWD-platform",
        "github_repo": "vbwd-plugin-ghrm",
        "author_name": "VBWD-platform",
        "sort_order": 17,
        "icon_url": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
    },
    # ── dantweb open-source packages (10) ────────────────────────────────────
    {
        "plan_slug": "pkg-lensforge",
        "plan_name": "LensForge",
        "categories": ["backend"],
        "pkg_slug": "lensforge",
        "pkg_name": "LensForge",
        "description": "Pluggable Python AI vision microservice SDK. Build modular "
        "computer-vision pipelines with interchangeable detector and classifier nodes.",
        "github_owner": "dantweb",
        "github_repo": "lensforge",
        "author_name": "dantweb",
        "sort_order": 20,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/685/685655.png",
    },
    {
        "plan_slug": "pkg-loopai-core",
        "plan_name": "LoopAI Core",
        "categories": ["backend"],
        "pkg_slug": "loopai-core",
        "pkg_name": "LoopAI Core",
        "description": "Python orchestration core for AI agent workflows. "
        "Define multi-step agent loops with tool calling, retry logic, and state machines.",
        "github_owner": "dantweb",
        "github_repo": "loopai-core",
        "author_name": "dantweb",
        "sort_order": 21,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/4481/4481503.png",
    },
    {
        "plan_slug": "pkg-loopai-view",
        "plan_name": "LoopAI View",
        "categories": ["fe-user", "fe-admin"],
        "pkg_slug": "loopai-view",
        "pkg_name": "LoopAI View",
        "description": "Vue.js UI module for LoopAI — ready-made agent dashboards, "
        "chat threads, tool-call inspector, and real-time progress indicators.",
        "github_owner": "dantweb",
        "github_repo": "loopai-view",
        "author_name": "dantweb",
        "sort_order": 22,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/1087/1087815.png",
    },
    {
        "plan_slug": "pkg-agent-post",
        "plan_name": "Agent Post",
        "categories": ["backend"],
        "pkg_slug": "agent-post",
        "pkg_name": "Agent Post",
        "description": "AI agent for automated content scheduling and social-media publishing. "
        "Supports Twitter/X, LinkedIn, Telegram, and custom webhooks.",
        "github_owner": "dantweb",
        "github_repo": "agent_post",
        "author_name": "dantweb",
        "sort_order": 23,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/1384/1384060.png",
    },
    {
        "plan_slug": "pkg-oxid-shop-watch",
        "plan_name": "OXID Shop Watch",
        "categories": ["backend"],
        "pkg_slug": "oxid-shop-watch",
        "pkg_name": "OXID Shop Watch",
        "description": "E2E testing API for OXID eShop 7.4+. Trigger full smoke-test "
        "suites via HTTP, get structured pass/fail results, and receive Slack alerts.",
        "github_owner": "dantweb",
        "github_repo": "oxid-shop-watch",
        "author_name": "dantweb",
        "sort_order": 24,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/2037/2037082.png",
    },
    {
        "plan_slug": "pkg-ecwatch-core",
        "plan_name": "ECWatch Core",
        "categories": ["backend", "fe-admin"],
        "pkg_slug": "ecwatch-core",
        "pkg_name": "ECWatch Core",
        "description": "E-commerce monitoring engine — tracks price changes, stock levels, "
        "and uptime across multiple shops. Fires webhooks on detected changes.",
        "github_owner": "dantweb",
        "github_repo": "ecwatch-core",
        "author_name": "dantweb",
        "sort_order": 25,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/3281/3281289.png",
    },
    {
        "plan_slug": "pkg-ecwatch-web",
        "plan_name": "ECWatch Web",
        "categories": ["fe-user", "fe-admin"],
        "pkg_slug": "ecwatch-web",
        "pkg_name": "ECWatch Web",
        "description": "Real-time web dashboard for ECWatch Core. "
        "Visualise price history charts, stock alerts, and uptime timelines for any shop.",
        "github_owner": "dantweb",
        "github_repo": "ecwatch-web",
        "author_name": "dantweb",
        "sort_order": 26,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/1828/1828765.png",
    },
    {
        "plan_slug": "pkg-oxid-devchat",
        "plan_name": "OXID DevChat",
        "categories": ["backend"],
        "pkg_slug": "oxid-devchat",
        "pkg_name": "OXID DevChat",
        "description": "AI-powered developer assistant for OXID eShop. Ask questions about "
        "module APIs, get code snippets, and debug Symfony container issues in plain English.",
        "github_owner": "dantweb",
        "github_repo": "oxid-devchat",
        "author_name": "dantweb",
        "sort_order": 27,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/4944/4944377.png",
    },
    {
        "plan_slug": "pkg-wp-post2md",
        "plan_name": "WP Post2MD",
        "categories": ["backend"],
        "pkg_slug": "wp-post2md",
        "pkg_name": "WP Post2MD",
        "description": "WordPress plugin that exports posts and pages to Markdown files "
        "and packages them into a timestamped ZIP archive for download or migration.",
        "github_owner": "dantweb",
        "github_repo": "wp-dantweb-post2md",
        "author_name": "dantweb",
        "sort_order": 28,
        "icon_url": "https://s.w.org/style/images/about/WordPress-logotype-wmark.png",
    },
    {
        "plan_slug": "pkg-quantum-llm",
        "plan_name": "Quantum LLM",
        "categories": ["backend", "fe-admin"],
        "pkg_slug": "quantum-llm",
        "pkg_name": "Quantum LLM",
        "description": "Research toolkit exploring quantum-mechanical effects in large language models. "
        "Includes TDD implementation plans, visualisations, and paper summaries.",
        "github_owner": "dantweb",
        "github_repo": "quantum-llm",
        "author_name": "dantweb",
        "sort_order": 29,
        "icon_url": "https://cdn-icons-png.flaticon.com/512/1976/1976916.png",
    },
]


def _get_or_create_plan(session, slug, name):
    """Return existing TarifPlan by slug or create a free yearly one."""
    plan = session.query(TarifPlan).filter_by(slug=slug).first()
    if plan:
        return plan, False
    eur = session.query(Currency).filter_by(code="EUR").first()
    if not eur:
        eur = Currency(
            code="EUR",
            name="Euro",
            symbol="€",
            exchange_rate=Decimal("1.0"),
            is_default=True,
            is_active=True,
        )
        session.add(eur)
        session.flush()
    price_obj = Price(
        price_float=0.0,
        price_decimal=Decimal("0.00"),
        currency_id=eur.id,
        net_amount=Decimal("0.00"),
        gross_amount=Decimal("0.00"),
        taxes={},
    )
    session.add(price_obj)
    session.flush()
    plan = TarifPlan(
        name=name,
        slug=slug,
        description="",
        price_float=0.0,
        price_id=price_obj.id,
        price=Decimal("0.00"),
        currency="EUR",
        billing_period=BillingPeriod.YEARLY,
        trial_days=0,
        features={},
        is_active=True,
        sort_order=0,
    )
    session.add(plan)
    session.flush()
    return plan, True


try:
    # ── Software Packages ────────────────────────────────────────────────────

    print("\n=== Software Packages ===")

    for entry in SOFTWARE_PACKAGES:
        plan, plan_created = _get_or_create_plan(
            session, entry["plan_slug"], entry["plan_name"]
        )
        if plan_created:
            print(f"  Created plan : {entry['plan_slug']}")
        else:
            print(f"  Exists  plan : {entry['plan_slug']}")

        # Attach plan to categories (create category if missing)
        for cat_slug in entry["categories"]:
            cat = session.query(TarifPlanCategory).filter_by(slug=cat_slug).first()
            if not cat:
                cat = TarifPlanCategory(
                    name=cat_slug.replace("-", " ").title(),
                    slug=cat_slug,
                    description="",
                    sort_order=99,
                    is_single=False,
                )
                session.add(cat)
                session.flush()
                print(f"  Created category: {cat_slug}")
            if plan not in cat.tarif_plans:
                cat.tarif_plans.append(plan)

        session.flush()

        # Create GhrmSoftwarePackage if it doesn't exist yet
        existing_pkg = (
            session.query(GhrmSoftwarePackage).filter_by(slug=entry["pkg_slug"]).first()
        )
        if existing_pkg:
            # Update description and icon_url if missing or changed
            if not existing_pkg.description and entry.get("description"):
                existing_pkg.description = entry["description"]
            if entry.get("icon_url") and existing_pkg.icon_url != entry["icon_url"]:
                existing_pkg.icon_url = entry["icon_url"]
            session.flush()
            print(f"  Exists  pkg  : {entry['pkg_slug']}")
        else:
            pkg = GhrmSoftwarePackage(
                tariff_plan_id=plan.id,
                name=entry["pkg_name"],
                slug=entry["pkg_slug"],
                description=entry.get("description"),
                author_name=entry.get("author_name"),
                icon_url=entry.get("icon_url"),
                github_owner=entry["github_owner"],
                github_repo=entry["github_repo"],
                sort_order=entry.get("sort_order", 0),
                is_active=True,
            )
            session.add(pkg)
            session.flush()
            print(f"  Created pkg  : {entry['pkg_slug']}")

    session.flush()

    # ── Seed GhrmSoftwareSync records with demo readme ───────────────────────

    print("\n=== Software Sync Demo Content ===")

    for entry in SOFTWARE_PACKAGES:
        pkg = (
            session.query(GhrmSoftwarePackage).filter_by(slug=entry["pkg_slug"]).first()
        )
        if not pkg:
            continue
        sync = (
            session.query(GhrmSoftwareSync)
            .filter_by(software_package_id=pkg.id)
            .first()
        )
        if not sync:
            sync = GhrmSoftwareSync(software_package_id=pkg.id)
            session.add(sync)
            session.flush()
            print(f"  Created sync : {entry['pkg_slug']}")
        else:
            print(f"  Exists  sync : {entry['pkg_slug']}")

        # Only write demo readme if no cached or override readme exists yet
        if not sync.cached_readme and not sync.override_readme:
            name = entry["pkg_name"]
            desc = entry.get("description", "")
            owner = entry["github_owner"]
            repo = entry["github_repo"]
            sync.override_readme = (
                f"# {name}\n\n"
                f"{desc}\n\n"
                f"## Installation\n\n"
                f"```bash\n"
                f"# Using npm\n"
                f"npm install @{owner}/{repo}\n\n"
                f"# Using pip\n"
                f"pip install {repo}\n"
                f"```\n\n"
                f"## Quick Start\n\n"
                f"```python\n"
                f"from {repo.replace('-', '_')} import main\n\n"
                f"main()\n"
                f"```\n\n"
                f"## Documentation\n\n"
                f"Full documentation and API reference are available on "
                f"[GitHub](https://github.com/{owner}/{repo}).\n\n"
                f"## License\n\n"
                f"MIT © {owner}\n"
            )
            session.flush()

    session.flush()

    # ── CMS Category ────────────────────────────────────────────────────────

    print("\n=== CMS Category ===")
    cms_cat, created = get_or_create(
        session,
        CmsCategory,
        slug="ghrm",
        name="Software Catalogue",
        sort_order=50,
    )
    print(f"  {'Created' if created else 'Exists'}: cms_category ghrm")

    # ── Layouts ─────────────────────────────────────────────────────────────

    print("\n=== CMS Layouts ===")

    # Catalogue layout — used by category index + package list pages
    layout_catalogue, created = get_or_create(
        session,
        CmsLayout,
        slug=CATALOGUE_LAYOUT_SLUG,
        name="GHRM Software Catalogue",
        areas=[
            {"name": "header", "type": "header", "label": "Header"},
            {"name": "breadcrumbs", "type": "vue", "label": ""},
            {"name": "ghrm-categories", "type": "vue", "label": "Categories"},
            {"name": "footer", "type": "footer", "label": "Footer"},
        ],
        sort_order=10,
        is_active=True,
    )
    if not created:
        layout_catalogue.areas = [
            {"name": "header", "type": "header", "label": "Header"},
            {"name": "breadcrumbs", "type": "vue", "label": ""},
            {"name": "ghrm-categories", "type": "vue", "label": "Categories"},
            {"name": "footer", "type": "footer", "label": "Footer"},
        ]
        session.flush()
    print(f"  {'Created' if created else 'Exists'}: {CATALOGUE_LAYOUT_SLUG}")

    # Detail layout — used by individual package pages
    layout_detail, created = get_or_create(
        session,
        CmsLayout,
        slug=DETAIL_LAYOUT_SLUG,
        name="GHRM Software Detail",
        areas=[
            {"name": "header", "type": "header", "label": "Header"},
            {"name": "breadcrumbs", "type": "vue", "label": ""},
            {"name": "ghrm-software-detail", "type": "vue", "label": "Software Detail"},
            {"name": "footer", "type": "footer", "label": "Footer"},
        ],
        sort_order=11,
        is_active=True,
    )
    if not created:
        layout_detail.areas = [
            {"name": "header", "type": "header", "label": "Header"},
            {"name": "breadcrumbs", "type": "vue", "label": ""},
            {"name": "ghrm-software-detail", "type": "vue", "label": "Software Detail"},
            {"name": "footer", "type": "footer", "label": "Footer"},
        ]
        session.flush()
    print(f"  {'Created' if created else 'Exists'}: {DETAIL_LAYOUT_SLUG}")

    # ── Widgets ─────────────────────────────────────────────────────────────

    print("\n=== CMS Widgets ===")

    WIDGETS = [
        {
            "slug": "ghrm-categories",
            "name": "GHRM Categories",
            "widget_type": "vue-component",
            "content_json": {
                "component": "GhrmCatalogueContent",
                "items_per_page": 12,
            },
        },
        {
            "slug": "ghrm-software-detail",
            "name": "GHRM Software Detail",
            "widget_type": "vue-component",
            "content_json": {
                "component": "GhrmPackageDetail",
                "items_per_page": 12,
            },
        },
    ]
    # breadcrumbs widget is created by populate_cms.py — look it up here
    breadcrumbs_widget = session.query(CmsWidget).filter_by(slug="breadcrumbs").first()

    widget_map: dict = {}
    for w in WIDGETS:
        widget, created = get_or_create(
            session,
            CmsWidget,
            slug=w["slug"],
            name=w["name"],
            widget_type=w["widget_type"],
            content_json=w["content_json"],
            is_active=True,
        )
        widget_map[w["slug"]] = widget
        print(f"  {'Created' if created else 'Exists'}: {w['slug']}")

    # ── Layout → Widget assignments ─────────────────────────────────────────

    print("\n=== Layout Widget Assignments ===")

    def assign_widget(layout, widget, area_name: str, sort_order: int = 0):
        exists = (
            session.query(CmsLayoutWidget)
            .filter_by(layout_id=layout.id, widget_id=widget.id, area_name=area_name)
            .first()
        )
        if not exists:
            session.add(
                CmsLayoutWidget(
                    layout_id=layout.id,
                    widget_id=widget.id,
                    area_name=area_name,
                    sort_order=sort_order,
                )
            )
            session.flush()
            return True
        return False

    # Look up shared header/footer nav widgets created by populate_cms.py
    header_nav = session.query(CmsWidget).filter_by(slug="header-nav").first()
    footer_nav = session.query(CmsWidget).filter_by(slug="footer-nav").first()

    # Catalogue layout
    if header_nav:
        added = assign_widget(layout_catalogue, header_nav, "header", 0)
        print(
            f"  {'Assigned' if added else 'Exists'}: {CATALOGUE_LAYOUT_SLUG} / header → header-nav"
        )
    else:
        print("  ! header-nav not found — run populate_cms first")

    if breadcrumbs_widget:
        added = assign_widget(layout_catalogue, breadcrumbs_widget, "breadcrumbs", 3)
        print(
            f"  {'Assigned' if added else 'Exists'}: {CATALOGUE_LAYOUT_SLUG} / breadcrumbs → breadcrumbs"
        )
    else:
        print("  ! breadcrumbs widget not found — run populate_cms first")

    added = assign_widget(
        layout_catalogue, widget_map["ghrm-categories"], "ghrm-categories", 0
    )
    print(
        f"  {'Assigned' if added else 'Exists'}: {CATALOGUE_LAYOUT_SLUG} / ghrm-categories → ghrm-categories"
    )

    if footer_nav:
        added = assign_widget(layout_catalogue, footer_nav, "footer", 0)
        print(
            f"  {'Assigned' if added else 'Exists'}: {CATALOGUE_LAYOUT_SLUG} / footer → footer-nav"
        )
    else:
        print("  ! footer-nav not found — run populate_cms first")

    # Detail layout
    if header_nav:
        added = assign_widget(layout_detail, header_nav, "header", 0)
        print(
            f"  {'Assigned' if added else 'Exists'}: {DETAIL_LAYOUT_SLUG} / header → header-nav"
        )

    if breadcrumbs_widget:
        added = assign_widget(layout_detail, breadcrumbs_widget, "breadcrumbs", 3)
        print(
            f"  {'Assigned' if added else 'Exists'}: {DETAIL_LAYOUT_SLUG} / breadcrumbs → breadcrumbs"
        )

    added = assign_widget(
        layout_detail, widget_map["ghrm-software-detail"], "ghrm-software-detail", 0
    )
    print(
        f"  {'Assigned' if added else 'Exists'}: {DETAIL_LAYOUT_SLUG} / ghrm-software-detail → ghrm-software-detail"
    )

    if footer_nav:
        added = assign_widget(layout_detail, footer_nav, "footer", 0)
        print(
            f"  {'Assigned' if added else 'Exists'}: {DETAIL_LAYOUT_SLUG} / footer → footer-nav"
        )

    # ── CMS Pages ───────────────────────────────────────────────────────────

    print("\n=== CMS Pages ===")

    # ── Template pages (used by GhrmLayoutWrapper to resolve layout + style) ──

    tmpl_catalogue, created = get_or_create(
        session,
        CmsPage,
        slug=CATALOGUE_PAGE_SLUG,
        name="GHRM Catalogue Template",
        language="en",
        content_json={"type": "doc", "content": []},
        is_published=False,
        sort_order=0,
        category_id=cms_cat.id,
        layout_id=layout_catalogue.id,
        meta_title="Software Catalogue",
        robots="noindex",
    )
    print(f"  {'Created' if created else 'Exists'}: template /{CATALOGUE_PAGE_SLUG}")

    tmpl_detail, created = get_or_create(
        session,
        CmsPage,
        slug=DETAIL_PAGE_SLUG,
        name="GHRM Detail Template",
        language="en",
        content_json={"type": "doc", "content": []},
        is_published=True,
        sort_order=1,
        category_id=cms_cat.id,
        layout_id=layout_detail.id,
        meta_title="Software Detail",
        robots="noindex",
    )
    print(f"  {'Created' if created else 'Exists'}: template /{DETAIL_PAGE_SLUG}")

    # Look up dark-midnight style (created by populate_cms.py)
    from plugins.cms.src.models.cms_style import CmsStyle as _CmsStyle

    style_dark = session.query(_CmsStyle).filter_by(slug="dark-midnight").first()
    style_light = session.query(_CmsStyle).filter_by(slug="light-clean").first()

    # /software — alternate root entry point with dark theme
    page_software, created = get_or_create(
        session,
        CmsPage,
        slug="software",
        name="Software",
        language="en",
        content_json={"type": "doc", "content": []},
        is_published=True,
        sort_order=0,
        category_id=cms_cat.id,
        layout_id=layout_catalogue.id,
        style_id=style_dark.id if style_dark else None,
        meta_title="Software Catalogue",
        meta_description="Browse available software packages",
        robots="index,follow",
    )
    print(f"  {'Created' if created else 'Exists'}: /software")

    # Category index (root /category page)
    page_index, created = get_or_create(
        session,
        CmsPage,
        slug="category",
        name="Software Catalogue",
        language="en",
        content_json={"type": "doc", "content": []},
        is_published=True,
        sort_order=0,
        category_id=cms_cat.id,
        layout_id=layout_catalogue.id,
        style_id=style_light.id if style_light else None,
        meta_title="Software Catalogue",
        meta_description="Browse available software packages",
        robots="index,follow",
    )
    print(f"  {'Created' if created else 'Exists'}: /category")

    # One page per configured category slug
    for i, cat_slug in enumerate(CATEGORY_SLUGS):
        label = slug_to_label(cat_slug)
        page_slug = f"category/{cat_slug}"
        page, created = get_or_create(
            session,
            CmsPage,
            slug=page_slug,
            name=f"{label} Packages",
            language="en",
            content_json={"type": "doc", "content": []},
            is_published=True,
            sort_order=i + 1,
            category_id=cms_cat.id,
            layout_id=layout_catalogue.id,
            style_id=style_light.id if style_light else None,
            meta_title=f"{label} Packages",
            meta_description=f"Browse {label} software packages",
            robots="index,follow",
        )
        print(f"  {'Created' if created else 'Exists'}: /{page_slug}")

    session.commit()

    print("\n=== Done ===")
    print("  CMS category    : ghrm")
    print(f"  Layouts         : {CATALOGUE_LAYOUT_SLUG}, {DETAIL_LAYOUT_SLUG}")
    print(
        f"  Widgets         : {', '.join(w['slug'] for w in WIDGETS)} (vue-component)"
    )
    print(f"  Template pages  : {CATALOGUE_PAGE_SLUG}, {DETAIL_PAGE_SLUG}")
    print(f"  Content pages   : category + {len(CATEGORY_SLUGS)} category pages")
    print(
        "\n  Assign a CMS Style to a template page in the admin to override catalogue styles."
    )

except Exception:
    session.rollback()
    import traceback

    traceback.print_exc()
    sys.exit(1)
finally:
    session.close()
