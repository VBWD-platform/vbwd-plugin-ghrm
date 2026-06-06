# GHRM — GitHub Integration

How the **GitHub Repo Manager (ghrm)** plugin gives subscription-paying users
access to private GitHub repositories: what it does, how it's wired, how to
configure it, and how to operate it. For the step-by-step "go live with a real
GitHub App" runbook see [`../README.md`](../README.md) → *Going live*.

> **Core stays agnostic.** Everything here lives in the `ghrm` plugin. GHRM
> reaches the subscription plugin only through a **declared dependency**
> (`PluginMetadata.dependencies = ["subscription"]`) — never through core. Core
> (`vbwd/`) contains no GitHub or GHRM code.

---

## 1. What it does

A user buys a subscription whose tariff plan is bound to a **software package**
(`GhrmSoftwarePackage`, one per plan). When the user connects their GitHub
account, GHRM adds them as a **collaborator** on exactly that package's
repository — at a configurable permission level (default **read / clone-only**)
— so they can `git clone` the private repo. When the subscription lapses, access
is removed after a grace period. Every failure is visible (never a silent
"connected").

The flow, end to end:

1. User subscribes to a plan → the plan's package defines `github_owner/repo`.
2. User opens the package's **GitHub Access** tab and clicks **Connect** → GitHub
   OAuth (user-to-server) → GHRM stores the user's real GitHub login.
3. GHRM resolves the user's active plans (via the subscription dependency) and,
   for each owned package, adds the user as a collaborator on that repo.
4. Outside collaborators get a **pending invitation** (GitHub `201`) → status
   `INVITED`; org members/owners are added immediately (`204`) → `ACTIVE`.
5. User accepts the GitHub invitation → next `/access` load verifies it →
   `ACTIVE`, and the tab shows fine-grained-PAT + `git clone` instructions.
6. Subscription cancelled / payment failed → `GRACE` (with an expiry); the
   scheduler removes access when grace expires → `REVOKED`.

---

## 2. Architecture

A **single GitHub App** does both jobs:

| Job | Mechanism | Config |
|---|---|---|
| Manage collaborators / invitations on repos | App **installation token** (JWT signed with the App's private key → `/app/installations/<id>/access_tokens`) | `github_app_id`, `github_installation_id`, `github_app_private_key_path` (PEM) |
| Identify the connecting user (their login) | App **user OAuth** (user-to-server token → `GET /user`) | `github_oauth_client_id`, `github_oauth_client_secret`, `github_oauth_redirect_uri` |

Key classes (in `src/services/`):

- `IGithubAppClient` — the port. Two Liskov-paired implementations:
  - `GithubAppClient` (real, `github_app_client_real.py`) — talks to
    `api.github.com`.
  - `MockGithubAppClient` (`github_app_client.py`) — in-memory, for dev/CI.
  - Selected by `_make_github_client` based on the `GHRM_USE_MOCK_GITHUB` env
    flag (see §6).
- `GithubAccessService` (`github_access_service.py`) — the orchestrator:
  entitlement resolution, the membership lifecycle, grant/revoke, error
  surfacing. Depends only on ports (`IGithubAppClient`, the membership/identity
  repos, and the ghrm-owned `ISubscriptionEntitlements` port).
- `ISubscriptionEntitlements` (`src/services/ports.py`) — the GHRM-owned port
  for "which plans is this user actively entitled to?". Implemented by an
  adapter in `__init__.py` that wraps the subscription plugin's read model
  (`active_plan_ids`). **This is the only place the subscription plugin is
  imported.**

### Data model

- `GhrmUserGithubAccess` — identity/OAuth only: `github_username`,
  `github_user_id`, `oauth_token` (encrypted), `oauth_scope`, derived
  `connected`.
- `GhrmRepoMembership` — per **(user, package)** collaborator state:
  `status`, `invitation_id`, `invited_at`, `grace_expires_at`, `last_error`.
  Unique on `(user_id, package_id)`, so a user can be `ACTIVE` on one repo and
  `INVITED`/`ERROR` on another.
- `GhrmSoftwarePackage` — the per-plan package: `tariff_plan_id`,
  `github_owner`, `github_repo`, **`collaborator_permission`** (see §5), etc.

---

## 3. Membership lifecycle (per user × package)

```
(no membership)
   │ entitlement present AND github connected → add_collaborator
   ▼
PUT /collaborators → 201 (invitation)        → INVITED  (store invitation_id)
                   → 204 (already member)     → ACTIVE
INVITED ──[acceptance verified: GET collaborators/<u> = 204]──▶ ACTIVE
ACTIVE/INVITED ──[subscription cancelled | payment_failed]──▶ GRACE (grace_expires_at)
GRACE ──[scheduler: grace expired]──▶ remove_collaborator / cancel invite ──▶ REVOKED
ACTIVE/INVITED/REVOKED ──[subscription renewed]──▶ re-add ──▶ INVITED/ACTIVE
any GitHub API failure ──▶ ERROR (last_error stored, surfaced in the tab; retried next event/connect)
disconnect ──▶ remove all memberships + delete identity
```

Statuses are serialized **lowercase** in the API (`invited`, `active`, `grace`,
`revoked`, `error`); the frontend compares case-insensitively.

Failures are never swallowed: `_ensure_collaborator` catches only
`GithubAppClientError`, sets the membership to `ERROR` with `last_error`, and
logs a WARN. Anything else propagates.

---

## 4. Entitlement scoping (only the repo you paid for)

On connect, GHRM calls `ISubscriptionEntitlements.active_plan_ids(user_id)` and,
for each active plan, finds the bound package (`find_by_tariff_plan_id`) and
grants access to **only that repo**. A user is never added to a repo whose plan
they don't actively hold. Ongoing changes are driven by subscription events
(activated / cancelled / payment_failed / renewed); connect-time resolution
only fixes the buy-then-connect ordering. There is **no periodic reconcile
job**.

---

## 5. Per-package permission level + security guardrail

Each package carries `collaborator_permission` — the GitHub permission granted
to its collaborators. Stored as the raw GitHub string; allowed values:

| Label | Value |
|---|---|
| Read (clone only) | `pull` |
| Triage | `triage` |
| Write | `push` |
| Maintain | `maintain` |
| Admin | `admin` |

Default is **`pull`** (least privilege — read is enough to clone).

### The `allow_extensive_github_permissions` guardrail (default OFF)

To prevent an admin from granting write-or-above **by mistake**, anything beyond
`pull` is gated behind the plugin flag `allow_extensive_github_permissions`
(default `false`). Enforced at **three layers** — the backend is authoritative;
the UI is convenience:

1. **Validation** (package create/update): with the flag off, any value other
   than `pull` is rejected with `400`.
2. **Grant clamp** (`_ensure_collaborator`): with the flag off, the granted
   permission is forced to `pull` regardless of the package's stored value —
   so a value saved while the flag was on is neutralized once it's turned off.
3. **UI** (fe-admin): with the flag off, the package form offers only Read; the
   write+ options are disabled with a hint. fe-admin reads the flag from
   `GET /api/v1/ghrm/config`.

To allow Write+: enable `allow_extensive_github_permissions` in the GHRM plugin
settings, then set the package's level on its admin form. The new level applies
to **future grants only** (existing collaborators are not re-synced on change).

---

## 6. Mock vs. real client

`_make_github_client` returns the **mock** when `GHRM_USE_MOCK_GITHUB == "true"`
(the default for dev/CI — offline-green), and logs a clear WARN so a live mock
is never mistaken for the real thing:

```
[GHRM] using MOCK GitHub client — no real API calls (GHRM_USE_MOCK_GITHUB=true).
```

Any other value selects the **real** client. The classic tell that you're still
on the mock: the connected user shows as **`@testuser`** (the mock's hard-coded
identity). Env changes require a container **recreate** (`docker compose up -d
--force-recreate api`), not just a restart — Docker bakes env at create time.

---

## 7. Configuration

Config lives in `config.json` (template / defaults) and is editable per
environment via the admin plugin-settings UI (driven by `admin-config.json`);
the runtime values are persisted as the plugin's config.

| Key | Meaning |
|---|---|
| `github_app_id` | GitHub App ID |
| `github_installation_id` | Installation ID (from installing the App on the org) |
| `github_app_private_key_path` | Container path to the App PEM (e.g. `/app/plugins/ghrm/auth/github-app.pem`) |
| `github_oauth_client_id` | The App's Client ID (user OAuth) |
| `github_oauth_client_secret` | A generated client secret (treat as a secret) |
| `github_oauth_redirect_uri` | Must equal the App's registered **Callback URL** (e.g. `http://localhost:8080/ghrm/auth/github/callback`) |
| `allow_extensive_github_permissions` | Gate for Write+ levels (default `false`) — see §5 |
| `software_category_slugs` | Catalogue categories |
| `software_catalogue_cms_page_slug` / `software_detail_cms_page_slug` | CMS page slugs for the catalogue/detail pages |
| `grace_period_fallback_days` | Grace days used when an event doesn't specify trailing days |

Env (compose), not plugin config:

| Env var | Meaning |
|---|---|
| `GHRM_USE_MOCK_GITHUB` | `true` (default) → mock client; anything else → real client |

### Required GitHub App setup

- **Repository permissions:** **Administration → Read & write** (manage
  collaborators/invitations) + **Contents → Read-only** (so a user's
  fine-grained PAT can clone).
- **User authorization Callback URL** = `github_oauth_redirect_uri` exactly.
  *(Missing/mismatched callback URL → the authorize page returns 404.)*
- **Visibility = public ("Any account").** A **private** App's authorize page
  only works for the owning org's members — outside users (real customers) get a
  **404**. Public does not expose your repos; repo access is still governed by
  the App installation + permissions.
- **Install** the App on the org that owns the package repos, with access to
  those repos.
- The PEM is a mounted secret / git-ignored (`*.pem`) — never committed.

---

## 8. HTTP endpoints

**User** (auth required):

| Method / path | Purpose |
|---|---|
| `GET /api/v1/ghrm/auth/github` | Build + start the OAuth authorize redirect |
| `GET /api/v1/ghrm/auth/github/callback` | OAuth callback: exchange code, store identity, grant entitled access |
| `GET /api/v1/ghrm/access` | Per-package membership state (`{connected, github_username, memberships[]}`); lazily verifies `INVITED → ACTIVE` |
| `GET /api/v1/ghrm/packages/<slug>/install` | Per-state install guidance: `ACTIVE` → fine-grained-PAT steps + `git clone` command; `INVITED` → "accept your invitation"; otherwise 403 |
| `GET /api/v1/ghrm/config` | Public config the frontend needs (layout slugs + `allow_extensive_github_permissions`) |

**Admin** (gated by the declared permission taxonomy):

| Area | Permission |
|---|---|
| Package CRUD / rotate-key / sync / preview | `ghrm.packages.view` (reads) / `ghrm.packages.manage` (writes) |
| Access log, `access/sync/<user_id>` | `ghrm.access.view` / `ghrm.access.manage` |
| Widgets | `ghrm.packages.view` / `ghrm.packages.manage` |
| Plugin settings | `ghrm.configure` |

> No server-minted deploy tokens. Users clone with their **own fine-grained
> PAT** (`Contents: read` on the repo). The tab never renders a server token.

---

## 9. Scheduler

A background tick runs `revoke_expired_grace_access()`: for each grace-expired
membership it removes the collaborator (or cancels a still-pending invitation)
and sets `REVOKED`. The scheduler is guarded so it does not start under
`TESTING`.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Connected as **`@testuser`** | Mock client is live. Set `GHRM_USE_MOCK_GITHUB=false` and **recreate** the api container. Check logs for the `[GHRM] using MOCK GitHub client` WARN. |
| Authorize page **404** for an outside user | App is **private** → make it **public** ("Any account"). |
| Authorize page **404** for everyone | App has **no/mismatched Callback URL** → set it to `github_oauth_redirect_uri`. |
| Membership shows **ERROR** | Real GitHub call failed. Common: missing **Administration: write**, App not installed on that repo, or wrong `github_owner/repo`. The reason is in `last_error` (admin access-log). |
| Collaborator added with **Write** but should be read | The package's `collaborator_permission` is `push`+ and `allow_extensive_github_permissions` is on. Set the package to Read, and/or turn the flag off (the grant then clamps to `pull`). Existing collaborators are not re-synced (future-grants-only) — change the role manually on GitHub or re-trigger via reconnect. |
| Post-connect lands on a 404 page | The OAuth callback redirects to `/dashboard` after success; ensure that route exists in the consuming frontend. |

---

## 11. Testing

- **Mock is the CI default** (offline-green). Unit tests use `MagicMock` repos +
  `MockGithubAppClient` + a stubbed `ISubscriptionEntitlements` (no subscription
  import in unit tests). Contract tests exercise the real client over httpx
  `MockTransport`.
- **Gated live test:** `tests/integration/test_github_live.py` runs only when
  `GHRM_LIVE_TEST=1` plus real App creds + a throwaway repo are present; it
  invites → verifies → removes against real GitHub and cleans up after itself.
  Skipped in CI.
- Guard: `bin/pre-commit-check.sh --plugin ghrm --full`.
