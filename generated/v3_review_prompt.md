You are performing an adversarial review of a LLD document.

Your job is not to restate or summarize it. Your job is to find weaknesses, risks, contradictions, missing decisions, underspecified contracts, and ways the document could cause wasted work or bad outcomes.

Explicitly look for important things the document does not say but should. Treat omissions, missing constraints, missing requirements, missing failure handling, and missing rollout or operational details as first-class findings, not as side notes.

Review it like a skeptical senior engineer.

Mode boundary: Keep the critique at the low-level design layer. Focus on concrete contracts, schemas, APIs, state transitions, concurrency, migrations, and testability. Do not drift into code-level implementation review or line-by-line coding suggestions unless the document itself incorrectly depends on them.

Context level: doc-only
Context guidance: Review only the target document. Do not inspect local repository files, source code, or other docs. If a finding depends on implementation context, call out the missing context explicitly instead of exploring.

Focus especially on:
- schema, API, and state-machine correctness
- transactionality, concurrency, and idempotency
- edge cases, failure handling, and recovery
- backward compatibility and migration safety
- testability and operational instrumentation
- places where the low-level contract is ambiguous or contradictory

Additional instructions:
- This is a V3 frontend design for a local-first photo management app. Focus on: architectural gaps, underspecified contracts, migration risks, and anything that could cause wasted implementation work.

For each finding:
1. Give it a short title.
2. Assign severity: critical, high, medium, or low.
3. Explain why it is a problem.
4. Cite the relevant document line numbers and any local corroborating files if used.
5. Describe the likely consequence if left unresolved.
6. Recommend the decision, clarification, or design change needed.

Prioritize high-severity findings first.
Do not praise the document unless necessary for contrast.
Do not rewrite the document.
Do not give generic advice.
Be concrete, critical, and specific.
If something looks acceptable but depends on an unstated assumption, call that out explicitly.

Output markdown only, with these sections:
- Verdict
- Context Used
- Findings
- Top Risks
- Open Questions

Target document: /Users/paul/projects/photo_auto_tagging/docs/photoquery_v3_frontend_design.md

Document contents with line numbers:

   1 | # PhotoQuery V3 Frontend Design
   2 | 
   3 | Date: 2026-03-22
   4 | 
   5 | ## Purpose
   6 | 
   7 | This document defines the architecture for the V3 frontend surface of photoquery. V3 is not a framework migration or a rewrite. It is a controlled restructure of the frontend around a simpler surface model, shared state architecture, and clean API boundary — built on the backend domain split happening in Phase 1/2.
   8 | 
   9 | ### What V3 Is
  10 | 
  11 | - A new SvelteKit route tree (`/v3/`) coexisting with V2 during development
  12 | - 5 surfaces instead of 7, with clearer domain ownership
  13 | - Shared domain stores replacing per-surface state fragmentation
  14 | - Thin route components that orchestrate stores and render views
  15 | - A clean API boundary designed to be packaging-agnostic (Tauri-ready if ever needed)
  16 | 
  17 | ### What V3 Is Not
  18 | 
  19 | - Not a stack change (stays SvelteKit + Tailwind)
  20 | - Not a backend rewrite (reuses FastAPI API layer)
  21 | - Not a copy of V2 routes (fresh route files, shared lib modules)
  22 | - Not Tauri or desktop packaging (but the API boundary should not prevent it later)
  23 | 
  24 | ### Dependencies
  25 | 
  26 | V3 implementation should begin after or alongside:
  27 | - **CENTRAL-OPS-120**: Semantic contract tightening (Phase 1)
  28 | - **CENTRAL-OPS-121/122/123**: services.py domain split (Phase 2)
  29 | 
  30 | V3 design (this doc) and V3 scaffolding can proceed now. V3 feature implementation should wait until at least the first Phase 2 task lands, so routes build on decomposed backend modules rather than the monolith.
  31 | 
  32 | ## Surface Model
  33 | 
  34 | V2 has 7 surfaces with significant overlap. V3 consolidates to 5.
  35 | 
  36 | ### V2 → V3 Surface Map
  37 | 
  38 | | V3 Surface | Replaces | Core Purpose |
  39 | |---|---|---|
  40 | | **Library** | Gallery + Search | Browse, filter, search — the "find photos" surface |
  41 | | **Workspace** | Workspace + Review | Act on photos — rate, tag, decide, with mode toolbars |
  42 | | **Discover** | Discover | Explore AI clusters, promote to workspace |
  43 | | **Metrics** | Metrics | Training health, evaluation snapshots |
  44 | | **Settings** | Settings | Admin, config, index roots, slug management |
  45 | 
  46 | ### Why These 5
  47 | 
  48 | **Library absorbs Search**: In V2, Search is a standalone surface that does text/image query and shows results. Gallery does filter-based browsing with grid/scroll/single views. These are the same user intent ("find photos") with different input methods. V3 unifies them: Library has a search bar, filter panel, and result grid. Text search, image search, and slug/quality filters all feed the same result set.
  49 | 
  50 | **Workspace absorbs Review**: In V2, Review is a thin facade that dispatches to Workspace with a mode parameter. It adds no unique UI — just a mode selector that then delegates. V3 drops the indirection: Workspace has a mode picker (concept, quality, keeper, queue) built into its toolbar. No separate route needed.
  51 | 
  52 | **Discover stays separate**: Discover's clustering/exploration UX is genuinely distinct from Library's item-level browsing. Promoting a cluster to Workspace is a natural cross-surface action, not a reason to merge surfaces.
  53 | 
  54 | **Metrics and Settings stay separate**: These are admin/operational surfaces with distinct concerns. No benefit to merging.
  55 | 
  56 | ### Removed Surfaces
  57 | 
  58 | **Review** (`/v2/review`): Absorbed into Workspace mode picker. The compatibility banner and mode dispatch code go away entirely.
  59 | 
  60 | **Search** (`/v2/search`): Absorbed into Library as a search input mode alongside filters.
  61 | 
  62 | ## Route Structure
  63 | 
  64 | ```text
  65 | photoquery/ui/v3/
  66 |   src/
  67 |     routes/
  68 |       +layout.svelte           ← app shell: nav, toast container, ws listener
  69 |       library/
  70 |         +page.svelte           ← thin orchestration: loads LibraryView
  71 |       workspace/
  72 |         +page.svelte           ← thin orchestration: loads WorkspaceView
  73 |       discover/
  74 |         +page.svelte           ← thin orchestration: loads DiscoverView
  75 |       metrics/
  76 |         +page.svelte
  77 |       settings/
  78 |         +page.svelte
  79 |     lib/
  80 |       stores/                  ← shared domain stores (see State Architecture)
  81 |         items.ts
  82 |         slugs.ts
  83 |         preferences.ts
  84 |         mutations.ts
  85 |       api/                     ← API client layer (see API Contract)
  86 |         client.ts
  87 |         library.ts
  88 |         workspace.ts
  89 |         discover.ts
  90 |         system.ts
  91 |       surfaces/                ← surface-specific view state + components
  92 |         library/
  93 |           LibraryView.svelte
  94 |           LibraryFilters.svelte
  95 |           SearchInput.svelte
  96 |           state.ts             ← surface view state (filter draft, scroll pos)
  97 |         workspace/
  98 |           WorkspaceView.svelte
  99 |           ModeToolbar.svelte
 100 |           ItemFocus.svelte
 101 |           state.ts
 102 |         discover/
 103 |           DiscoverView.svelte
 104 |           ClusterGrid.svelte
 105 |           state.ts
 106 |       components/              ← shared UI primitives
 107 |         ImageGrid.svelte
 108 |         ImageCard.svelte
 109 |         Lightbox.svelte
 110 |         KeyboardHandler.svelte
 111 |         RatingInput.svelte
 112 | ```
 113 | 
 114 | ### Route Component Contract
 115 | 
 116 | Each `+page.svelte` is a thin orchestration shell — no state management, no API calls, no rendering logic. Pattern:
 117 | 
 118 | ```svelte
 119 | <script>
 120 |   import { LibraryView } from '$lib/surfaces/library/LibraryView.svelte';
 121 |   import { onMount } from 'svelte';
 122 |   import { slugStore } from '$lib/stores/slugs';
 123 | 
 124 |   onMount(() => { slugStore.ensureLoaded(); });
 125 | </script>
 126 | 
 127 | <LibraryView />
 128 | ```
 129 | 
 130 | All logic lives in `$lib/surfaces/` and `$lib/stores/`. Routes exist only for SvelteKit routing and layout nesting.
 131 | 
 132 | ## State Architecture
 133 | 
 134 | V2's core problem: each surface maintains its own state (gallery/state.ts, discover/state.ts, review/state.ts) with no shared layer. Favoriting a photo in Gallery doesn't update Workspace. Handoffs between surfaces use URL query params to reconstruct state.
 135 | 
 136 | ### Shared Domain Stores
 137 | 
 138 | V3 introduces three shared Svelte stores that hold domain data across surfaces:
 139 | 
 140 | **`items.ts`** — Cached item data keyed by image_id
 141 | ```typescript
 142 | interface ItemStore {
 143 |   /** Get item by ID, fetching if not cached */
 144 |   get(id: string): Readable<Item | null>;
 145 |   /** Bulk load items (from search results, gallery pages, etc.) */
 146 |   load(items: Item[]): void;
 147 |   /** Invalidate specific items after mutation */
 148 |   invalidate(ids: string[]): void;
 149 |   /** Invalidate all (after bulk operations) */
 150 |   invalidateAll(): void;
 151 | }
 152 | ```
 153 | 
 154 | Items store is the single source of truth for item data. All surfaces read from it. Mutations go through `mutations.ts` which updates the store and fires API calls.
 155 | 
 156 | **`slugs.ts`** — Concept/quality/keeper slug registry
 157 | ```typescript
 158 | interface SlugStore {
 159 |   /** All available slugs, fetched once on app init */
 160 |   all: Readable<Slug[]>;
 161 |   /** Slugs grouped by kind */
 162 |   byKind: Readable<Record<SlugKind, Slug[]>>;
 163 |   /** Force refresh (after training, slug creation) */
 164 |   refresh(): Promise<void>;
 165 | }
 166 | ```
 167 | 
 168 | V2 fetches slugs independently in Gallery, Review, Workspace, and Search. V3 fetches once and shares.
 169 | 
 170 | **`preferences.ts`** — User view preferences (persisted to localStorage)
 171 | ```typescript
 172 | interface PreferencesStore {
 173 |   gridSize: Writable<'sm' | 'md' | 'lg'>;
 174 |   viewMode: Writable<'grid' | 'scroll' | 'single'>;
 175 |   showMetadata: Writable<boolean>;
 176 |   recentSearches: Writable<string[]>;
 177 | }
 178 | ```
 179 | 
 180 | ### Mutation Store
 181 | 
 182 | **`mutations.ts`** — Centralized mutation dispatch
 183 | ```typescript
 184 | interface MutationStore {
 185 |   favorite(imageId: string, value: boolean): Promise<void>;
 186 |   rate(imageId: string, slug: string, label: string): Promise<void>;
 187 |   qualityRate(imageId: string, score: number): Promise<void>;
 188 |   keeperDecide(imageId: string, decision: string): Promise<void>;
 189 | }
 190 | ```
 191 | 
 192 | Every mutation:
 193 | 1. Calls the API endpoint
 194 | 2. Optimistically updates the items store
 195 | 3. Emits a mutation event for any surface-specific side effects
 196 | 
 197 | This solves V2's biggest UX gap: mutating in one surface doesn't propagate to others.
 198 | 
 199 | ### Surface View State
 200 | 
 201 | Each surface has a `state.ts` module for surface-specific view concerns:
 202 | - Library: filter draft, active search query, current page/scroll position
 203 | - Workspace: active mode (concept/quality/keeper/queue), working set source, focus index
 204 | - Discover: active snapshot, cluster filters (cohesion/quality/coverage/type)
 205 | 
 206 | Surface state is **derived from** shared stores where possible. For example, Library's displayed items derive from the items store filtered by the current search/filter state.
 207 | 
 208 | Surface state is **not shared** across surfaces. When the user navigates from Library to Workspace with a selection, the handoff uses a lightweight action (e.g., `workspace.loadFromSelection(imageIds)`) rather than URL query param encoding.
 209 | 
 210 | ## API Contract
 211 | 
 212 | ### Reuse Strategy
 213 | 
 214 | V3 reuses the existing FastAPI API layer (`/api/*`). The 15 route modules are functional and will be better organized after the backend domain split. No new API framework or endpoint restructure needed.
 215 | 
 216 | ### Changes from V2
 217 | 
 218 | Three targeted improvements to the API contract, implemented incrementally:
 219 | 
 220 | #### 1. Unified Item Response Shape
 221 | 
 222 | V2 pain: Workspace's `resolveWorkspaceItems()` has 7 branches hitting different endpoints with different response shapes. `normalize.ts` exists solely to paper over this.
 223 | 
 224 | V3 target: All endpoints that return items should return a consistent shape:
 225 | 
 226 | ```typescript
 227 | interface ItemResponse {
 228 |   image_id: string;
 229 |   path: string;
 230 |   filename: string;
 231 |   width: number;
 232 |   height: number;
 233 |   format: string;
 234 |   // Domain fields (populated if available, null otherwise)
 235 |   quality_score: number | null;
 236 |   keeper_status: string | null;
 237 |   favorite: boolean;
 238 |   concepts: ConceptLabel[];
 239 |   // Source metadata (how this item entered the result set)
 240 |   source_score?: number;    // relevance/similarity score
 241 |   source_reason?: string;   // e.g., "text_search", "concept_match", "cluster_member"
 242 | }
 243 | ```
 244 | 
 245 | This doesn't require changing every backend endpoint at once. The API client layer (`$lib/api/*.ts`) can normalize responses client-side initially, then backend endpoints can adopt the unified shape as part of the domain split work.
 246 | 
 247 | #### 2. Mutation Event Channel
 248 | 
 249 | New: `GET /api/events` (SSE stream) that emits item mutation events:
 250 | 
 251 | ```json
 252 | {"type": "item_updated", "image_id": "abc123", "fields": ["favorite", "quality_score"]}
 253 | {"type": "slugs_changed"}
 254 | {"type": "index_progress", "scanned": 1200, "total": 4800}
 255 | ```
 256 | 
 257 | This replaces V2's pattern of "mutate then manually re-fetch in each surface." The mutations store subscribes to this channel and updates shared stores automatically.
 258 | 
 259 | If SSE adds complexity, an alternative is a simple polling endpoint (`GET /api/changes?since=<ts>`) that returns recent mutations. Less elegant but simpler to implement.
 260 | 
 261 | #### 3. Pagination Contract
 262 | 
 263 | V2 has inconsistent pagination: some endpoints use `limit`/`offset`, some return all results, some have custom cursor-like semantics. V3 API clients should normalize to a consistent pattern:
 264 | 
 265 | ```typescript
 266 | interface PagedResponse<T> {
 267 |   items: T[];
 268 |   total: number;
 269 |   offset: number;
 270 |   limit: number;
 271 |   has_more: boolean;
 272 | }
 273 | ```
 274 | 
 275 | Like the item shape, this can be normalized client-side initially and adopted backend-side incrementally.
 276 | 
 277 | ### API Boundary Principle
 278 | 
 279 | All API communication goes through `$lib/api/client.ts`. No `fetch()` calls in components or stores. This creates a clean seam: if the app is ever wrapped in Tauri, the API client layer is the only thing that changes (from HTTP fetch to Tauri IPC commands). Everything above the API client is packaging-agnostic.
 280 | 
 281 | ## Component Architecture
 282 | 
 283 | ### Shared Primitives
 284 | 
 285 | These components are used across multiple surfaces:
 286 | 
 287 | **ImageGrid** — Responsive photo grid with virtual scrolling for large result sets. Accepts items from any source (Library search results, Workspace candidates, Discover cluster members). Grid size controlled by preferences store.
 288 | 
 289 | **ImageCard** — Single image tile with optional overlays (favorite badge, quality score, concept labels). Click/keyboard interaction emits events; parent surface handles the action.
 290 | 
 291 | **Lightbox** — Full-screen single-image view with keyboard navigation (arrow keys, rating keys). Shared across Library (browse), Workspace (rate/decide), and Discover (inspect cluster members).
 292 | 
 293 | **KeyboardHandler** — Centralized keyboard shortcut manager. Registers surface-specific bindings on mount, cleans up on unmount. Prevents shortcut conflicts between surfaces and avoids browser shortcut collisions.
 294 | 
 295 | **RatingInput** — Star/score/label input widget shared between quality rating, concept review, and keeper decision surfaces.
 296 | 
 297 | ### Surface Views
 298 | 
 299 | Each surface has a primary View component that composes shared primitives:
 300 | 
 301 | - **LibraryView**: SearchInput + LibraryFilters + ImageGrid + Lightbox
 302 | - **WorkspaceView**: ModeToolbar + ItemFocus + ImageGrid + RatingInput
 303 | - **DiscoverView**: ClusterGrid + ImageGrid + Lightbox (for cluster inspection)
 304 | 
 305 | View components own layout and surface-specific behavior. They read from shared stores and surface state, dispatch through the mutations store, and render shared primitives.
 306 | 
 307 | ## Migration Plan
 308 | 
 309 | ### Phase A: Scaffold (can start now)
 310 | 
 311 | 1. Create `photoquery/ui/v3/` SvelteKit project
 312 | 2. Set up shared stores (`items.ts`, `slugs.ts`, `preferences.ts`, `mutations.ts`)
 313 | 3. Set up API client layer (`$lib/api/`)
 314 | 4. Build shared primitives (ImageGrid, ImageCard, Lightbox, KeyboardHandler)
 315 | 5. Mount at `/v3` alongside `/v2`
 316 | 
 317 | No backend changes required. V3 scaffold can use existing API endpoints.
 318 | 
 319 | ### Phase B: Surface Implementation (after Phase 2 backend split starts)
 320 | 
 321 | Build surfaces in this order:
 322 | 1. **Library** — most standalone, validates the shared store + API client architecture
 323 | 2. **Settings** — low-risk, validates system API integration
 324 | 3. **Workspace** — most complex, benefits from Library proving the store pattern
 325 | 4. **Discover** — builds on Workspace's mutation patterns
 326 | 5. **Metrics** — mostly read-only, lowest priority
 327 | 
 328 | ### Phase C: V2 Freeze + Cutover
 329 | 
 330 | 1. Freeze V2 to bugfix-only (no new features)
 331 | 2. Feature parity validation: confirm all V2 workflows work in V3
 332 | 3. Redirect `/v2` to `/v3` (keep V2 accessible at `/v2-legacy` for rollback)
 333 | 4. After stabilization period: remove V2 code
 334 | 
 335 | ### Phase D: Legacy UI Retirement
 336 | 
 337 | 1. Confirm V3 covers all workflows that V1/Gradio served
 338 | 2. Remove Gradio UI from `photoquery ui` launch path
 339 | 3. Remove `photoquery/ui/app.py` (or archive in a branch)
 340 | 
 341 | ## Keyboard Interaction Model
 342 | 
 343 | Photo management is keyboard-intensive. V3 should support:
 344 | 
 345 | - Arrow keys: navigate grid / next-prev in lightbox
 346 | - Number keys (1-5): quality rating
 347 | - Y/N: keeper accept/reject
 348 | - Space: toggle favorite
 349 | - Enter: open lightbox / confirm action
 350 | - Escape: close lightbox / clear selection
 351 | - / : focus search input
 352 | 
 353 | KeyboardHandler registers bindings per-surface and prevents conflicts. Browser shortcut collisions (Cmd+W, etc.) are unavoidable in a web context — document the limitation and design around it (no critical actions on browser-colliding shortcuts).
 354 | 
 355 | ## Open Questions
 356 | 
 357 | ### 1. Image delivery path
 358 | V2 uses `GET /api/image?path=...` to serve images. Should V3 continue this, or introduce thumbnail/preview tiers (e.g., `GET /api/image?path=...&size=thumb`) for faster grid loading? The backend already has crop support; this may be a matter of wiring it to a query parameter.
 359 | 
 360 | ### 2. Offline / background indexing feedback
 361 | When `photoquery index` runs in the background while the UI is open, how should V3 reflect progress? The mutation event channel (SSE) could carry `index_progress` events, but this needs backend support. Alternatively, poll `/api/status` on an interval.
 362 | 
 363 | ### 3. Working set persistence
 364 | V2 has a working-set concept (saved item selections). Should V3 working sets be:
 365 | - Ephemeral (session-only, like a clipboard)
 366 | - Persisted (saved to DB, nameable, shareable across sessions)
 367 | - Both (default ephemeral, explicit save action)
 368 | 
 369 | V2 has `/api/workspace/working-sets` CRUD — the plumbing exists for persistence. The UX question is whether users want persistent collections or just transient selections.
 370 | 
 371 | ### 4. V2 feature parity checklist
 372 | Before V2 freeze, a parity checklist is needed. The V2 parity audit (`docs/photoquery_ui_v2_parity_audit_20260310.md`) documents what V2 covers vs legacy. V3 needs a similar audit vs V2 before cutover.
 373 | 
 374 | ## Design Principles
 375 | 
 376 | 1. **Routes are routing.** No state, no API calls, no rendering logic in `+page.svelte` files.
 377 | 2. **Stores are truth.** All domain data lives in shared stores. Surfaces read, mutations write.
 378 | 3. **API client is the seam.** All HTTP goes through `$lib/api/client.ts`. No fetch in components.
 379 | 4. **Keyboard-first.** Every action reachable by keyboard. Mouse is optional.
 380 | 5. **No V2 copy-paste.** Fresh implementation informed by V2 learnings, not forked V2 files.

