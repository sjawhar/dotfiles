# OpenChamber Mobile Recents Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, settings-gated mobile landing screen to OpenChamber that shows the cross-project recent/live sessions list (instead of a blank new-session draft) when no session is open — upstreamable as the PR that closes openchamber/openchamber#2565.

**Architecture:** OpenChamber v1.17.2 already ships the exact widget needed — `MobileSessionSwitcher` renders a flat, cross-project, busy-first session list via `useSwitcherItems` (global store, no directory scoping). We extract its row/list rendering into a reusable component, mount it as a full-screen landing surface in `MobileApp`'s `<main>` when a new persisted display setting is `'recents'` and no session is open, and add a settings toggle. No new data plumbing; the fan-in proxy already feeds real cross-process busy status into the lifecycle sort.

**Tech Stack:** React 18 + TypeScript (strict), zustand + persist, bun (install/test), vite (web build). Repo: `/home/ubuntu/openchamber-spike/openchamber-src` (clone of `openchamber/openchamber`).

## Global Constraints

- **Repo & branch:** work in `/home/ubuntu/openchamber-spike/openchamber-src`. Branch `mobile-recents-landing` off `origin/main` (NOT the v1.17.2 tag — PR #2569 touched `MobileHeader.tsx`/`MobileSessionSwitcher.tsx` after the tag).
- **Version control:** this is a plain-git third-party clone (not jj, not knives-managed). **ONE commit total** at the end (Sami's one-commit-per-PR rule overrides per-step commits below — where a task says "Commit", instead just verify and move on; the single commit happens in Task 7).
- **Commit/PR style:** conventional commits, e.g. `feat(mobile): optional recent-sessions landing screen`. Match repo code style: single quotes, 2-space indent, `React.FC<Props>` components, named exports.
- **Default behavior must not change:** default `mobileLandingMode` is `'last-session'` (current behavior). `'recents'` is opt-in via Settings → Sessions.
- **TypeScript iron rules:** no `any`, no `as` narrowing, no `!`, no `@ts-ignore`. `readonly` where the repo pattern allows.
- **Verification commands** (run from repo root unless stated): `bun run type-check:ui`, `bun run type-check:web`, `bun run build:web`, `cd packages/ui && bun test <file>`.
- **The deployed spike stack** lives in tmux session `openchamber-stack` (windows: `serve`, `fanin`, `openchamber`). OpenCode backend serve on :5096, fan-in proxy on :5199, OpenChamber on :3210, HTTPS via `tailscale serve` (`/` → 3210).
- **Task 8 (upstream PR) is GATED on Sami validating the UX on his phone.** Do not push or open a PR before he says go.

---

### Task 1: Branch setup + baseline

**Files:** none created — repo state only.

**Interfaces:**
- Produces: branch `mobile-recents-landing` on top of `origin/main`, dependencies installed, baseline type-check/test green (or pre-existing failures recorded).

- [ ] **Step 1: Fetch and branch from main**

```bash
cd /home/ubuntu/openchamber-spike/openchamber-src
git fetch origin main
git checkout -b mobile-recents-landing origin/main
git log --oneline -3   # note the SHA you branched from
```

Expected: branch created; HEAD is newer than tag v1.17.2.

- [ ] **Step 2: Confirm commit identity**

```bash
git config user.name; git config user.email
```

Expected: Sami's identity from ~/.gitconfig (`sami@trajectorylabs.net`). If empty, stop and ask — do not invent an identity.

- [ ] **Step 3: Install dependencies**

```bash
bun install
```

Expected: completes without error (repo uses bun workspaces).

- [ ] **Step 4: Baseline checks**

```bash
bun run type-check:ui
cd packages/ui && bun test 2>&1 | tail -5
```

Expected: type-check passes on clean main. Record any pre-existing test failures verbatim — they are not ours to fix, only not to worsen.

---

### Task 2: `mobileLandingMode` in the display store (TDD)

**Files:**
- Modify: `packages/ui/src/stores/useSessionDisplayStore.ts`
- Test (modify — file ALREADY EXISTS with migration tests through v4): `packages/ui/src/stores/useSessionDisplayStore.test.ts` — APPEND a new describe block; do not replace existing tests

**Interfaces:**
- Produces: `mobileLandingMode: 'last-session' | 'recents'` field + `setMobileLandingMode(mode)` setter on `useSessionDisplayStore`; exported type `MobileLandingMode`. Persisted store version bumps 4 → 5 with migration defaulting to `'last-session'`.

- [ ] **Step 1: Write the failing test**

```typescript
// packages/ui/src/stores/useSessionDisplayStore.test.ts
import { describe, expect, test } from 'bun:test';

import { migrateSessionDisplayState } from './useSessionDisplayStore';

describe('migrateSessionDisplayState', () => {
  test('v4 state gains mobileLandingMode last-session default', () => {
    const migrated = migrateSessionDisplayState({ showRecentSection: true }, 4);
    expect(migrated.mobileLandingMode).toBe('last-session');
  });

  test('v5 state keeps a persisted recents preference', () => {
    const migrated = migrateSessionDisplayState({ mobileLandingMode: 'recents' }, 5);
    expect(migrated.mobileLandingMode).toBe('recents');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ui && bun test src/stores/useSessionDisplayStore.test.ts`
Expected: FAIL — `migrated.mobileLandingMode` is `undefined` (field does not exist yet).

- [ ] **Step 3: Implement the store change**

In `packages/ui/src/stores/useSessionDisplayStore.ts`:

Add after the `SessionGroupingMode` type (line ~8):

```typescript
// Mobile landing surface when no session is open: restore-last/new-draft
// (current behavior) or the cross-project recent-sessions list (#2565).
type MobileLandingMode = 'last-session' | 'recents';
```

Add to the `SessionDisplayStore` type:

```typescript
  mobileLandingMode: MobileLandingMode;
  setMobileLandingMode: (mode: MobileLandingMode) => void;
```

Add to `migrateSessionDisplayState` (after the `version < 4` block):

```typescript
  if (version < 5) {
    state.mobileLandingMode = 'last-session';
  }
```

Add to the store creator defaults (next to `projectSortOrder: 'manual',`):

```typescript
      mobileLandingMode: 'last-session',
      setMobileLandingMode: (mode) => set({ mobileLandingMode: mode }),
```

Bump `version: 4` → `version: 5` and extend the migration comment: `// v4→v5 adds mobileLandingMode (default last-session).`

Extend the type export line:

```typescript
export type { MobileLandingMode, ProjectSortOrder, SessionGroupingMode };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ui && bun test src/stores/useSessionDisplayStore.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Type-check**

Run: `bun run type-check:ui`
Expected: PASS.

---

### Task 3: Extract the reusable recents list from `MobileSessionSwitcher`

**Files:**
- Create: `packages/ui/src/apps/MobileRecentSessionsList.tsx`
- Modify: `packages/ui/src/apps/MobileSessionSwitcher.tsx`

**Interfaces:**
- Consumes: `useSwitcherItems(enabled, { maxParents })` from `@/components/session/sidebar/hooks/useSwitcherItems` (items expose `item.node.session: Session`, `item.secondaryMeta?.projectLabel/branchLabel`, `item.projectId`).
- Produces: `MobileRecentSessionsList: React.FC<{ enabled: boolean; limit: number; currentSessionId: string | null; emptyLabel: string; onSelectSession: (session: Session, projectId: string | null) => void }>` — renders the rows only (no container/scroll chrome). Task 4 and the popover both consume it.

- [ ] **Step 1: Create the new file with SwitcherRow moved verbatim**

Move `getSessionTitle` and `SwitcherRow` (lines 17–66 of `MobileSessionSwitcher.tsx`) into the new file **unchanged**, plus the list mapping:

```tsx
// packages/ui/src/apps/MobileRecentSessionsList.tsx
import React from 'react';
import type { Session } from '@opencode-ai/sdk/v2';

import { Icon } from '@/components/icon/Icon';
import { formatSessionCompactDateLabel } from '@/components/session/sidebar/utils';
import { useSwitcherItems } from '@/components/session/sidebar/hooks/useSwitcherItems';
import { useI18n } from '@/lib/i18n';
import { cn } from '@/lib/utils';
import { useSessionUnseenCount } from '@/sync/notification-store';
import { useGlobalSessionStatus } from '@/sync/sync-context';

const getSessionTitle = (session: Session, fallback: string): string =>
  session.title?.trim() || fallback;

/** One row: live status (busy spinner / attention dot), title,
    "project · branch", compact time. Moved verbatim from MobileSessionSwitcher. */
const SwitcherRow: React.FC<{
  session: Session;
  meta: string;
  active: boolean;
  onSelect: () => void;
}> = ({ session, meta, active, onSelect }) => {
  // ... body copied EXACTLY from MobileSessionSwitcher.tsx lines 28-66 ...
};

/** Flat, cross-project, lifecycle-sorted (busy-first) recent sessions rows.
    Shared by the header popover and the recents landing surface (#2565). */
export const MobileRecentSessionsList: React.FC<{
  enabled: boolean;
  limit: number;
  currentSessionId: string | null;
  emptyLabel: string;
  onSelectSession: (session: Session, projectId: string | null) => void;
}> = ({ enabled, limit, currentSessionId, emptyLabel, onSelectSession }) => {
  const items = useSwitcherItems(enabled, { maxParents: limit });

  if (items.length === 0) {
    return (
      <p className="px-3 py-6 text-center typography-small text-muted-foreground">
        {emptyLabel}
      </p>
    );
  }

  return (
    <>
      {items.map((item) => {
        const session = item.node.session;
        const meta = [item.secondaryMeta?.projectLabel, item.secondaryMeta?.branchLabel]
          .filter(Boolean)
          .join(' · ');
        return (
          <SwitcherRow
            key={session.id}
            session={session}
            meta={meta}
            active={session.id === currentSessionId}
            onSelect={() => onSelectSession(session, item.projectId ?? null)}
          />
        );
      })}
    </>
  );
};
```

(When copying `SwitcherRow`, take the exact body from the current file — do not retype it.)

- [ ] **Step 2: Rewire `MobileSessionSwitcher` to consume it**

In `MobileSessionSwitcher.tsx`: delete the moved `getSessionTitle`/`SwitcherRow` and their now-unused imports (`Icon`, `formatSessionCompactDateLabel`, `useSwitcherItems`, `useSessionUnseenCount`, `useGlobalSessionStatus`); keep the rest. Replace the `items` hook call and the `items.length === 0 ? ... : items.map(...)` block inside the scroll `<div>` with:

```tsx
          <MobileRecentSessionsList
            enabled={open || shouldRender}
            limit={RECENT_SESSIONS_LIMIT}
            currentSessionId={currentSessionId}
            emptyLabel={t('sessions.switcher.empty')}
            onSelectSession={(session, projectId) => {
              if (projectId) setActiveProjectIdOnly(projectId);
              handleSelect(session);
            }}
          />
```

Add the import: `import { MobileRecentSessionsList } from './MobileRecentSessionsList';`

- [ ] **Step 3: Verify**

Run: `bun run type-check:ui`
Expected: PASS — and no unused-import lint debt: `cd packages/ui && bun run lint 2>&1 | tail -3` (expect no NEW errors vs. baseline).

---

### Task 4: The landing surface component

**Files:**
- Create: `packages/ui/src/apps/MobileLandingSessions.tsx`

**Interfaces:**
- Consumes: `MobileRecentSessionsList` (Task 3), `useSessionUIStore` (`currentSessionId`, `setCurrentSession`), `useProjectsStore.setActiveProjectIdOnly`, `refreshGlobalSessions`/`resolveGlobalSessionDirectory` from `@/stores/useGlobalSessionsStore`, i18n keys added in Task 6.
- Produces: `MobileLandingSessions: React.FC<{ onStartNewSession: () => void }>` — full-height landing surface. Task 5 mounts it.

- [ ] **Step 1: Create the component**

```tsx
// packages/ui/src/apps/MobileLandingSessions.tsx
import React from 'react';
import type { Session } from '@opencode-ai/sdk/v2';

import { Icon } from '@/components/icon/Icon';
import { useI18n } from '@/lib/i18n';
import { refreshGlobalSessions, resolveGlobalSessionDirectory } from '@/stores/useGlobalSessionsStore';
import { useProjectsStore } from '@/stores/useProjectsStore';
import { useSessionUIStore } from '@/sync/session-ui-store';

import { MobileRecentSessionsList } from './MobileRecentSessionsList';

const LANDING_SESSIONS_LIMIT = 30;

/** Opt-in landing surface (#2565): when no session is open, show the
    cross-project recents list instead of the auto-drafted new session. */
export const MobileLandingSessions: React.FC<{
  onStartNewSession: () => void;
}> = ({ onStartNewSession }) => {
  const { t } = useI18n();
  const currentSessionId = useSessionUIStore((state) => state.currentSessionId);
  const setCurrentSession = useSessionUIStore((state) => state.setCurrentSession);
  const setActiveProjectIdOnly = useProjectsStore((state) => state.setActiveProjectIdOnly);

  React.useEffect(() => {
    // Fresh authoritative snapshot on mount, same as the switcher popover.
    void refreshGlobalSessions();
  }, []);

  const handleSelect = React.useCallback(
    (session: Session, projectId: string | null) => {
      if (projectId) setActiveProjectIdOnly(projectId);
      void setCurrentSession(session.id, resolveGlobalSessionDirectory(session));
    },
    [setActiveProjectIdOnly, setCurrentSession],
  );

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-background">
      <div className="flex items-center justify-between px-4 pb-1 pt-3">
        <h2 className="typography-micro uppercase tracking-wide text-muted-foreground">
          {t('mobile.landing.recents.title')}
        </h2>
        <button
          type="button"
          className="flex items-center gap-1.5 rounded-lg px-2 py-1 typography-ui-label text-primary active:bg-interactive-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          onClick={onStartNewSession}
          style={{ touchAction: 'manipulation' }}
        >
          <RiAddLine className="size-3.5" aria-hidden />
          {t('sessions.switcher.draftTitle')}
        </button>
      </div>
      <div className="oc-hide-scrollbar min-h-0 flex-1 space-y-0.5 overflow-y-auto overscroll-contain px-2 pb-[calc(var(--oc-safe-area-bottom,0px)+12px)]">
        <MobileRecentSessionsList
          enabled
          limit={LANDING_SESSIONS_LIMIT}
          currentSessionId={currentSessionId}
          emptyLabel={t('sessions.switcher.empty')}
          onSelectSession={handleSelect}
        />
      </div>
    </div>
  );
};
```

Note: the sprite has NO `add-line` icon. Match the existing mobile new-chat button: `MobileSessionsSheet.tsx` (~L1402) uses `RiAddLine` — copy its exact import and usage (and drop the `Icon` import if unused). Do NOT use `<Icon name="add-line">`.

- [ ] **Step 2: Verify**

Run: `bun run type-check:ui`
Expected: PASS (i18n keys will fail until Task 6 — if so, do Task 6 Step 1 first, then re-run).

---

### Task 5: Mount the landing in `MobileApp`

**Files:**
- Modify: `packages/ui/src/apps/MobileApp.tsx` (the `<main>` block at ~line 501 and component-body state near the other `React.useState` hooks)

**Interfaces:**
- Consumes: `MobileLandingSessions` (Task 4), `useSessionDisplayStore.mobileLandingMode` (Task 2), `useSessionUIStore` (already imported in this file: `currentSessionId`, `newSessionDraft`, `openNewSessionDraft`).
- Produces: landing renders when `mobileLandingMode === 'recents' && !isIPad && !currentSessionId && !draftOpen && !landingDismissed && !initialSessionRoutePending`; "New session" calls `openNewSessionDraft()` (a REAL user draft — clears the persisted last-session pointer, unlike ChatContainer's `{ automatic: true }` auto-draft) then dismisses; opening any session clears the dismissal.

- [ ] **Step 1: Add state + selector in the MobileApp component body**

Near the other hooks (search for `const [sessionsSheetOpen`), add:

```tsx
  const mobileLandingMode = useSessionDisplayStore((state) => state.mobileLandingMode);
  const landingCurrentSessionId = useSessionUIStore((state) => state.currentSessionId);
  const landingDraftOpen = useSessionUIStore((state) => Boolean(state.newSessionDraft?.open));
  const openNewSessionDraft = useSessionUIStore((state) => state.openNewSessionDraft);
  // One-shot dismissal: "New session" opens a real user draft; opening a
  // session re-arms the landing for the next empty state.
  const [landingDismissed, setLandingDismissed] = React.useState(false);
  // A ?session=ID deep link applies asynchronously (useRouter effect) — hold
  // the landing back so it never flashes before setCurrentSession runs.
  const [initialSessionRoutePending, setInitialSessionRoutePending] = React.useState(
    () => new URLSearchParams(window.location.search).has('session'),
  );
  React.useEffect(() => {
    if (landingCurrentSessionId) {
      setLandingDismissed(false);
      setInitialSessionRoutePending(false);
    }
  }, [landingCurrentSessionId]);
  const showRecentsLanding =
    mobileLandingMode === 'recents'
    && !isIPad
    && !landingCurrentSessionId
    && !landingDraftOpen
    && !landingDismissed
    && !initialSessionRoutePending;
```

(`isIPad` is the existing tablet-layout boolean in `MobileApp.tsx` — the iPad split view already shows a persistent `MobileSessionsSheet` sidebar, so the landing would duplicate it. Verify the exact variable name and reuse it. If `newSessionDraft`'s open flag has a different shape, check `session-ui-store.ts` ~L690-724 and match — `openNewSessionDraft()` for non-automatic opens clears the persisted last-session pointer, which is exactly the user-intent semantic we want.)

Add imports:

```tsx
import { useSessionDisplayStore } from '@/stores/useSessionDisplayStore';
import { MobileLandingSessions } from './MobileLandingSessions';
```

(`useSessionUIStore` is already imported — reuse it. If a subscription to `currentSessionId` already exists in the component, reuse that variable instead of adding `landingCurrentSessionId` — search for `useSessionUIStore((state) => state.currentSessionId)` first.)

- [ ] **Step 2: Conditional render in `<main>`**

Replace (at ~line 501):

```tsx
          <main ref={chatMainRef} className="relative min-h-0 flex-1 overflow-hidden" data-page-scroll-lock="true">
            <div className="h-full w-full">
              <ErrorBoundary>
                <ChatView />
              </ErrorBoundary>
            </div>
          </main>
```

with:

```tsx
          <main ref={chatMainRef} className="relative min-h-0 flex-1 overflow-hidden" data-page-scroll-lock="true">
            <div className="h-full w-full">
              <ErrorBoundary>
                {showRecentsLanding ? (
                {showRecentsLanding ? (
                  <MobileLandingSessions
                    onStartNewSession={() => {
                      openNewSessionDraft();
                      setLandingDismissed(true);
                    }}
                  />
                ) : (
                  <ChatView />
                )}
              </ErrorBoundary>
            </div>
          </main>
```

- [ ] **Step 3: Verify**

Run: `bun run type-check:ui`
Expected: PASS.

---

### Task 6: Settings toggle + i18n strings

**Files:**
- Create: `packages/ui/src/components/sections/openchamber/MobileLandingSettings.tsx`
- Modify: `packages/ui/src/components/sections/openchamber/OpenChamberPage.tsx` (`SessionsSectionContent`, line ~215)
- Modify: `packages/ui/src/lib/i18n/messages/en.ts` (and `en.settings.ts` if settings strings live there — check where `'settings.openchamber.sessionRetention.*'` keys are defined and co-locate)

**Interfaces:**
- Consumes: `useSessionDisplayStore.mobileLandingMode`/`setMobileLandingMode` (Task 2), `SettingsSection`/`SettingsCheckboxRow` from `@/components/sections/shared/SettingsSection` (props: `checked`, `onChange`, `label`, `description` — verified against `SettingsCheckboxRowProps`).
- Produces: a "Mobile landing" section on Settings → Sessions (all surfaces; only affects mobile), 4 new i18n keys.

- [ ] **Step 1: Add i18n keys (namespace-correct placement)**

The dictionaries are SPLIT: top-level keys live in `messages/en.ts`, settings keys in `messages/en.settings.ts` (see `'settings.openchamber.sessionRetention.*'` there ~L925). Place accordingly:

In `packages/ui/src/lib/i18n/messages/en.ts` (near the other `mobile.*` keys ~L41):

```typescript
  'mobile.landing.recents.title': 'Recent sessions',
```

In `packages/ui/src/lib/i18n/messages/en.settings.ts` (near the other `settings.openchamber.*` keys):

```typescript
  'settings.openchamber.mobileLanding.title': 'Mobile landing',
  'settings.openchamber.mobileLanding.recents.label': 'Open to recent sessions',
  'settings.openchamber.mobileLanding.recents.description':
    'On phones, land on the cross-project recent sessions list when no session is open, instead of a new session draft.',
```

Then run `bun run type-check:ui`. **If** other locale dictionaries fail the `Record<I18nKey, string>` shape, add the same English strings to every failing locale file (`es`, `fr`, `ja`, `ko`, `pl`, `pt-BR`, `uk`, `zh-CN`, `zh-TW` — top-level keys in `<locale>.ts`, settings keys in `<locale>.settings.ts`) — English placeholder is the accepted first-pass convention; translators follow up upstream.

Also register the new toggle in settings search: `packages/ui/src/lib/settings/search.ts` (~L389) has per-section entries — add an entry for the Mobile landing toggle following the exact pattern of the neighboring sessions-section entries (same shape: page slug `'sessions'`, the new i18n label/description keys).

- [ ] **Step 2: Create the settings component**

```tsx
// packages/ui/src/components/sections/openchamber/MobileLandingSettings.tsx
import React from 'react';

import { SettingsCheckboxRow, SettingsSection } from '@/components/sections/shared/SettingsSection';
import { useI18n } from '@/lib/i18n';
import { useSessionDisplayStore } from '@/stores/useSessionDisplayStore';

export const MobileLandingSettings: React.FC = () => {
  const { t } = useI18n();
  const mobileLandingMode = useSessionDisplayStore((state) => state.mobileLandingMode);
  const setMobileLandingMode = useSessionDisplayStore((state) => state.setMobileLandingMode);

  return (
    <SettingsSection title={t('settings.openchamber.mobileLanding.title')}>
      <SettingsCheckboxRow
        label={t('settings.openchamber.mobileLanding.recents.label')}
        description={t('settings.openchamber.mobileLanding.recents.description')}
        checked={mobileLandingMode === 'recents'}
        onChange={(checked) => setMobileLandingMode(checked ? 'recents' : 'last-session')}
      />
    </SettingsSection>
  );
};
```

Before writing, read `packages/ui/src/components/sections/shared/SettingsSection.tsx` lines 350–400 and match `SettingsSection`'s exact props (it may want `title` + children or a different wrapper); also confirm `onChange` signature (`(checked: boolean) => void` vs event). Mirror `SessionRetentionSettings.tsx`'s usage exactly.

- [ ] **Step 3: Mount it in the Sessions section**

In `OpenChamberPage.tsx` (~line 215):

```tsx
const SessionsSectionContent: React.FC = () => {
    return (
        <>
            <DefaultsSettings />
            <MobileLandingSettings />
            <SessionRetentionSettings />
        </>
    );
};
```

Add the import next to the other section imports: `import { MobileLandingSettings } from './MobileLandingSettings';`

- [ ] **Step 4: Verify**

Run: `bun run type-check:ui && bun run type-check:web`
Expected: PASS both.

---

### Task 7: Build, deploy locally, single commit

**Files:** none new — build output + tmux + git.

**Interfaces:**
- Consumes: everything above.
- Produces: patched OpenChamber serving on :3210 from the clone; one commit on `mobile-recents-landing`.

- [ ] **Step 1: Full verification sweep**

```bash
cd /home/ubuntu/openchamber-spike/openchamber-src
bun run type-check:ui && bun run type-check:web
cd packages/ui && bun test 2>&1 | tail -5 && cd ../..
bun run build:web
```

Expected: type-checks pass, tests no worse than Task 1 baseline, `packages/web/dist/` freshly built.

- [ ] **Step 2: Swap the running stack to the patched build**

```bash
tmux respawn-window -k -t openchamber-stack:openchamber \
  "cd /home/ubuntu/openchamber-spike/openchamber-src && OPENCODE_HOST=http://127.0.0.1:5199 OPENCODE_SKIP_START=true bun packages/web/server/index.js --port 3210"
sleep 8
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3210/
```

Expected: `200`. If the server rejects those env/flags, check `packages/web/server/lib/opencode/cli-options.js` for the exact flag names and adjust — the npm CLI accepted `--ui-password`; the raw server may want only env vars.

- [ ] **Step 3: Single commit (source only — dist/ is gitignored)**

```bash
git add packages/ui/src packages/web 2>/dev/null; git status --short
git commit -m "feat(mobile): optional recent-sessions landing screen

When no session is open, an opt-in setting (Settings -> Sessions ->
Mobile landing) replaces the auto-drafted new session with the
cross-project recent sessions list, reusing the session switcher's
rows. Default behavior is unchanged.

Closes #2565"
```

Expected: exactly the files from Tasks 2–6 staged; nothing else (no dist, no lockfile churn unless bun install changed it — if `bun.lock` changed, include it only if `bun install` on clean main also changes it; otherwise restore it).

---

### Task 8: QA in a real browser (mobile surface)

**Files:** none — Playwright against http://127.0.0.1:3210.

**Interfaces:**
- Consumes: running patched stack (Task 7), Playwright skill.

- [ ] **Step 1: Force the mobile surface and enable the toggle**

Navigate to `http://127.0.0.1:3210/?surface=mobile` with a phone-sized viewport (`browser_resize` 390×844). Unlock with the locally configured OpenChamber UI password. Open Settings → Sessions → toggle "Open to recent sessions" ON. (Confirm the `?surface=mobile` param works — `packages/ui/src/lib/runtimeSurface.ts`; otherwise rely on the small viewport + coarse-pointer emulation.)

- [ ] **Step 2: Landing behavior**

Reload `/?surface=mobile` with NO `session` param. Expected: the recents landing renders (list of real sessions, busy spinners on active ones) instead of a new-session draft. Screenshot for the PR.

- [ ] **Step 3: Tap-through**

Tap the top session row. Expected: session opens, content streams live (fan-in). Navigate back to no-session state (or reload) — landing shows again.

- [ ] **Step 4: New-session escape hatch**

On the landing, tap "New session". Expected: the draft composer appears (landing dismissed). Open a session, then return to empty state: landing shows again (dismissal reset).

- [ ] **Step 5: Toggle OFF regression check**

Settings → toggle OFF → reload. Expected: original behavior (draft composer), proving default-off users are unaffected.

- [ ] **Step 6: Hand off to Sami**

Report QA evidence + screenshots. **STOP — Sami validates on his phone before Task 9.**

---

### Task 9 (GATED — only after Sami approves the UX): Upstream PR

**Files:** none — GitHub workflow.

- [ ] **Step 1: Load the `pr-preflight` skill and follow its judgment gate** (this is a third-party upstream contribution).

- [ ] **Step 2: Rebase on latest origin/main** (`git fetch origin && git rebase origin/main`), re-run Task 7 Step 1 sweep.

- [ ] **Step 3: Fork + push**

```bash
gh repo fork openchamber/openchamber --clone=false
git remote add fork "$(gh repo view sjawhar/openchamber --json url -q .url)" 2>/dev/null || true
git push fork mobile-recents-landing
```

- [ ] **Step 4: Open the PR** — use the `sami-voice` skill for the description. Title: `feat(mobile): optional recent-sessions landing screen`. Body: what/why (2 short paragraphs), the settings gate (default unchanged), the Task 8 screenshots, `Closes #2565`. Return the PR URL.

---

## Self-Review

- **Spec coverage:** settings-gated landing (Tasks 2, 4, 5, 6), reuse of existing switcher machinery (Task 3), local deployment for Sami's validation (Task 7, 8), upstream PR gated on validation (Task 9). Desktop-sidebar sessions-first changes are deliberately OUT of scope (not approved; separate plan if wanted).
- **Placeholder scan:** two intentional verify-then-adapt notes remain (Icon name in Task 4; `SettingsSection`/`SettingsCheckboxRow` exact props in Task 6) — both include the exact file+line to read and the expected shape, which beats guessing props into a plan the repo will contradict.
- **Type consistency:** `MobileLandingMode = 'last-session' | 'recents'` (Task 2) matches Task 5's comparison and Task 6's setter; `MobileRecentSessionsList` props identical between Task 3 (definition) and Tasks 3.2/4 (consumers); `onSelectSession(session, projectId ?? null)` matches the `(session: Session, projectId: string | null)` signature.
