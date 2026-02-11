# Polymarket Alpha Tracker — Implementation Plan

## Goal
Build a single-page React (JSX) dashboard that monitors Polymarket wallets and surfaces convergence alerts. Dark Bloomberg-terminal aesthetic, browser-only, no backend.

## Architecture
- Single `index.html` file with inline React JSX
- Tailwind CSS via CDN
- React + ReactDOM via CDN (with Babel standalone for JSX)
- All state in useReducer (no localStorage)
- Direct fetch() to Polymarket data API (with CORS proxy fallback)

## Tech Stack
- React 18 (CDN)
- Tailwind CSS 3 (CDN)
- Lucide React (CDN)
- Google Fonts: JetBrains Mono + DM Sans
- No build step

---

## Tasks

### Phase 1: Scaffolding

#### Task 1: Create HTML shell with CDN imports
**File:** `index.html`
- Create HTML5 boilerplate
- Import React 18, ReactDOM, Babel standalone from CDN
- Import Tailwind CSS from CDN
- Import Google Fonts (JetBrains Mono, DM Sans)
- Import Lucide React from CDN
- Create root div and script tag with type="text/babel"
- Add Tailwind config for custom colors (dark navy, electric green, amber, red)
- Verify: Open in browser, see blank dark page

#### Task 2: Create App component with three-panel layout
**File:** `index.html` (inside script tag)
- Create App component with CSS Grid layout: left sidebar (300px collapsible), center (flex-1), right (350px)
- Add panel headers with placeholder text
- Mobile responsive: stack panels vertically below 1024px breakpoint
- Dark background (#0a0e17), border separators between panels
- Verify: Three panels visible in browser

### Phase 2: State Management

#### Task 3: Implement useReducer with full state shape
- Define initial state matching the spec's state shape (wallets, settings, activityFeed, convergenceAlerts, polling)
- Define action types: ADD_WALLET, REMOVE_WALLET, BULK_IMPORT, UPDATE_SETTINGS, ADD_TRADES, SET_CONVERGENCE_ALERTS, SET_POLLING_STATUS, SET_LEADERBOARD, UPDATE_WALLET_STATUS
- Implement reducer function handling all actions
- Wire up to App component

#### Task 4: Create utility functions
- `formatAddress(addr)` — truncate to 0x1234...abcd
- `timeAgo(timestamp)` — relative time display
- `formatDollars(amount)` — format as $X,XXX.XX
- `timeWindowToSeconds(window)` — convert "1h"/"6h"/"24h"/"7d" to seconds
- `sleep(ms)` — promise-based delay
- `validateAddress(addr)` — validate 0x + 40 hex chars

### Phase 3: Left Panel — Watchlist Manager

#### Task 5: Build Add Wallet form
- Address input with 0x validation
- Nickname input
- Tag multi-select with preset tags: Politics, Sports, Macro, Crypto, Leaderboard
- Custom tag creation (type + enter)
- Add button dispatching ADD_WALLET action
- Duplicate detection (by address)
- Success feedback

#### Task 6: Build Wallet List with filtering/sorting
- Tag filter pills at top (clickable to toggle)
- Search input filtering by nickname
- Wallet cards: nickname, truncated address + copy, tag pills, active dot, remove button
- Sort options: recently active, alphabetical, date added
- Remove with confirmation dialog
- Handle 200+ wallets (virtualized rendering not needed for MVP but efficient rendering)

#### Task 7: Build Bulk Import
- "Bulk Import" button opens modal
- Textarea for pasting `address, nickname, tag1|tag2` format
- Parse and validate each line
- Import button dispatches BULK_IMPORT
- Show count of successes/failures
- Close modal after import

#### Task 8: Build Leaderboard Discovery
- "Browse Leaderboard" collapsible section
- Tabs for 1D / 7D / 30D / All-time
- Fetch from /rankings endpoint
- Display: rank, name/address, profit, volume
- "Add to Watchlist" button on each row (pre-fills add form)
- Loading skeleton while fetching

### Phase 4: Center Panel — Activity Feed

#### Task 9: Build Activity Feed controls
- Dollar threshold slider ($1 - $10,000, default $50)
- Tag filter dropdown
- Side filter: All / Buys / Sells
- Time window selector: 1h / 6h / 24h / 7d
- Dispatch UPDATE_SETTINGS for each change

#### Task 10: Build Trade Cards
- Design trade card component matching spec
- Wallet nickname with colored tag dot
- Market title
- Direction (Buy Yes / Buy No / Sell Yes / Sell No) color-coded
- Amount, price/odds, shares
- Relative timestamp with hover tooltip
- Link to polymarket.com market page
- Empty state message

#### Task 11: Build refresh controls and countdown
- "Last updated: X ago" timestamp
- Countdown timer to next poll
- "Refresh Now" button
- Progress bar during polling cycle
- Auto-refresh every 60 seconds

### Phase 5: Right Panel — Convergence Alerts

#### Task 12: Build Convergence Alert cards
- Alert card with amber highlight
- Market title with link
- Direction label (e.g., "3 wallets buying YES")
- List of converging wallet nicknames
- Total amount, trade count
- Convergence strength indicator (moderate/strong)
- Time span of convergence
- "View Market" button
- Empty state with helpful guidance
- Info tooltip explaining convergence

### Phase 6: API & Polling

#### Task 13: Implement API layer
- Base URL constant with CORS proxy fallback
- `fetchTrades(address, limit)` function
- `fetchLeaderboard(window, limit)` function
- Error handling: network errors, 429 rate limits with exponential backoff
- Response parsing and normalization

#### Task 14: Implement polling loop
- Batch processing (5 wallets per batch, 1s delay between)
- Progress tracking (0-100%)
- Compare timestamps to detect new trades
- Merge new trades into activity feed
- Update wallet lastTradeAt
- 60-second interval with cleanup on unmount

#### Task 15: Implement convergence detection
- Group trades by conditionId + direction
- Count unique wallets per group
- Generate alerts for groups with 2+ wallets
- Score by wallet count then total amount
- Sort and dispatch to state

### Phase 7: Polish

#### Task 16: Add loading states and animations
- Skeleton loaders for initial data fetches
- Slide-down + fade animation for new trades
- Toast notifications for wallet add/remove
- Error banner for API failures
- Rate limit warning display

#### Task 17: Responsive design and final polish
- Mobile layout (single column below 1024px)
- Collapsible sidebar on mobile
- Touch-friendly tap targets
- Scrollable panels with proper overflow
- Final visual pass: spacing, typography, colors

---

## Execution Notes
- This is a single-file app — all code goes in `index.html`
- Since there's no test framework, verification is manual (open in browser)
- Tasks should be executed sequentially (each builds on the previous)
- The file will be large but well-organized with clear component boundaries
