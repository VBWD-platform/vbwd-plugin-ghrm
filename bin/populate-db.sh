#!/bin/bash
# Populate GHRM Data
# ==================
# Creates CMS layouts, widgets, template pages, and category pages required
# by the GitHub Repo Manager (GHRM) plugin.
# Runs Alembic migrations first, then populate_ghrm.py inside the API container.
#
# Behaviour: idempotent — safe to re-run at any time. Existing records are
# skipped; new records are inserted.
#
# Usage:
#   ./plugins/ghrm/bin/populate_ghrm.sh
#
# Requirements:
#   - docker compose running with api service
#   - PostgreSQL database running
#   - GHRM Alembic migration applied
#
# This script creates:
#   - 2 CMS layouts  : ghrm-software-catalogue, ghrm-software-detail
#   - 4 CMS widgets  : ghrm-search-bar, ghrm-category-index,
#                      ghrm-package-list, ghrm-package-detail
#   - 2 template pages (is_published=false) for layout + style resolution
#   - 1 catalogue index page (/category)
#   - 1 page per configured software_category_slug

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR"/../../.. && pwd)"

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  GHRM Plugin — Data Population        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

cd "$PROJECT_ROOT/vbwd-backend" 2>/dev/null || cd "$PROJECT_ROOT" 2>/dev/null

if ! docker compose ps 2>/dev/null | grep -q "api.*Up"; then
    echo -e "${RED}✗ Error: api service is not running${NC}"
    echo ""
    echo "Please start the services first:"
    echo "  cd $PROJECT_ROOT/vbwd-backend"
    echo "  make up"
    exit 1
fi

echo -e "${YELLOW}Step 1/2 — Running Alembic migrations...${NC}"
echo ""

docker compose exec -T api python -m alembic upgrade heads

if [ $? -ne 0 ]; then
    echo ""
    echo -e "${RED}✗ Alembic migrations failed — aborting population${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Step 2/2 — Populating GHRM data (upsert)...${NC}"
echo ""

docker compose exec -T api python /app/plugins/ghrm/src/bin/populate_ghrm.py

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║    GHRM Data Population Complete      ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${GREEN}✓ Layouts  : ghrm-software-catalogue, ghrm-software-detail${NC}"
    echo -e "${GREEN}✓ Widgets  : ghrm-search-bar, ghrm-category-index, ghrm-package-list, ghrm-package-detail${NC}"
    echo -e "${GREEN}✓ Templates: ghrm-software-catalogue, ghrm-software-detail (unpublished)${NC}"
    echo -e "${GREEN}✓ Pages    : /category + one per configured category slug${NC}"
    echo ""
    echo "  Catalogue: http://localhost:8080/category"
    echo "  Admin:     http://localhost:8081/admin/settings/backend-plugins/ghrm"
    echo ""
    echo "  To apply custom styles, assign a CMS Style to a template page:"
    echo "  http://localhost:8081/admin/cms/pages"
    echo ""
    exit 0
else
    echo ""
    echo -e "${RED}✗ Failed to populate GHRM data${NC}"
    exit 1
fi
