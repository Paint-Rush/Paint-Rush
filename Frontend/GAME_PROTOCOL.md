# Frontend integration contract

Baseline: Paint-Rush/Paint-Rush main `a519e2e` (2026-11-04 KST). Backend files remain unchanged.

## Account and loadout

- Login/register: `POST /api/v1/auth/login`, `POST /api/v1/auth/register` with `{ username, password }`.
- Lobby: authenticated `GET /users/me/profile` and `/users/me/level`, using the teammate account service.
- Loadout: `GET /characters`, `/cards`, `/users/me/deck`, `/users/me/profile`.
- Before a new match: `PUT /users/me/deck` with six `cards`, then `PATCH /users/me/profile` with `preferred_character_id`. Both responses must match the selection before opening the socket. Failure does not silently fall back to a different deck or preview mode.
- Server character IDs come from `/characters`. The four legacy character options are used only for unauthenticated preview.
- The frontend accepts only the six supplied cards listed below. `/cards` must register the same IDs, types and valid costs before live matching; an incompatible catalog is reported explicitly. Old IDs are not silently mapped to these abilities.

## Supplied cards

The photo's 2×2 graph-paper squares represent **one game tile**. All coordinates below use game tiles.

| ID | Card | Reference ability |
| --- | --- | --- |
| `jumper` | JUMPER | Jump within the centered 5×5 square (±2 per axis), painting the projected grid route. Intervening walls do not block the jump. |
| `dash` | DASH | Move exactly four tiles horizontally or vertically, painting the route; blocked routes are rejected. |
| `sliding` | SLIDING | Move one to five tiles diagonally, painting the route; blocked routes are rejected. |
| `criss_cross` | CRISS CROSS | Paint the centered nine-tile X shown in Figma. |
| `swastika` | SWASTIKA | Paint the centered seventeen-tile pattern shown in Figma. |
| `the_line` | THE LINE | Paint the four tiles ahead in the chosen cardinal direction, excluding the origin. |

The six-card deck contains one of each by default. Preview hand size is three and the other three form the draw queue. Every use replaces the used slot with the next queued card and appends the used card to that queue.

All attacks leave the player in place. Movement paints the traveled grid route; glass and blocked tiles are never painted. Patterns are clipped to map bounds. Hovering a card shows valid targets with white-backed red dashed/hatch marks, distinguishable from solid ownership. Hovering a target shows the pending route/attack without spending cost. Clicking that target executes; centered attacks also execute by clicking the card. Touch users can tap a targeted card first. Escape cancels selection. Invalid targets never consume cost or rotate cards.

User-specified costs: JUMPER **4**, DASH **3**, SLIDING **4**, CRISS CROSS **5**, SWASTIKA **6**, THE LINE **3**. Offline preview deducts these costs and rejects insufficient cost before rotating the hand. The live `/cards` catalog must match these values as well as IDs and types.

## Socket lifecycle

1. Open same-origin `/ws/game`, send `{ type: 'auth', token }`.
2. On `auth_success`, request `room_state` to recover an existing room.
3. Only if the server reports `not_in_room`, send `{ type: 'queue_join', character_id }`. The server loads the saved deck.
4. Render authoritative READY/PLAYING/DEATHMATCH/FINISHED snapshots and timer updates. The client never declares a live winner from a local timer.
5. Send `queue_cancel` to cancel waiting. After confirmed results, `room_leave` requests return to lobby; wait for the server acknowledgement.

## Cards and map

- Centered attacks: `{ type: 'card_use', hand_index }`.
- Movement and THE LINE targeting: `{ type: 'card_use', hand_index, target_x, target_y }`, using the clicked tile. Invalid destinations are not submitted.
- No local cost deduction, rotation, teleport, territory ownership, damage, or kill is applied before an authoritative server snapshot.
- `player_moved`, `tiles_updated`, hand/action notifications trigger a complete state refresh. Other players' private hand/cost/deck data are not fabricated.
- Validate room ID, participants, distinct colors/corner spawns, map dimensions, coordinate bounds, hand length and cost limits. Count owners in the tile matrix and compare to every player's tile_count. On disagreement, retain the last valid state and block input until a valid snapshot arrives.
- Coverage UI denominator is the full map area (20 × 20 = 400). Unpainted tiles retain their share of the bar.
- The temporary default preview map is flat and has no blocked cells; new map design is deferred. Imported and server maps retain validation and are not silently rewritten.
- The approved camera layout starts with all 400 tiles visible. Wheel zoom is anchored to the pointer (1–3×); right-button drag pans. Space returns the camera to the player while preserving zoom. Bounds prevent unnecessary empty space at map corners. Player movement does not pull the free camera away from its chosen position.
- Only while zoomed, a bottom-right minimap displays actual tile ownership, living player positions and the viewport rectangle. Clicking it moves the camera without submitting a card or modifying player data. Its reset icon restores overview. The top camera-mode buttons and slider are removed.
- The compact hand retains the original attack-card artwork. An active card grows to 110%, rises 6px and shows a selected badge. Selection persists from card hover to map targeting, then clears on use, Escape or input lock. Reduced-motion settings disable the transition.
- Death and connection loss block card input. Respawn position/alive state comes from the server. Reconnect probes the existing room rather than joining twice.

## Existing limits

- Rechecked GitHub main `edbeb645` on 2026-11-04: `Backend/app/cards.py` still uses four legacy card IDs and one-step movement, which do not match the six requested cards. Backend registration and ability handlers need the same six-card contract before real multiplayer works. No backend files were changed and no legacy-card compatibility aliases were added.
- Offline preview executes movement, paints the supplied attack patterns, updates ownership/coverage and rotates the hand. Live games still use validated server snapshots exclusively. No NPC decisions, attack damage values or kill rules were invented.
- The server currently does not include opponent names, levels or complete decks in room_state. Private values remain undisclosed. Self profile and saved deck come from authenticated account endpoints.
- New result-screen design is explicitly excluded until its storyboard is supplied. The previous provisional result/leave flow is retained.
- No NPC algorithm, backend, database or deployment service is started/modified by this task. Verification uses browser HTTP/WebSocket interception and local HTML, not a live database or real multiplayer session.
