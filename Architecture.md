# Architecture.md — Quoridor: Architectural Strategy
**CSE472s Term Project · Spring 2026**

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [MVC Layer Breakdown](#2-mvc-layer-breakdown)
3. [Entity Mapping](#3-entity-mapping)
4. [AI Service Integration](#4-ai-service-integration)
5. [Data Flow & Interaction Diagrams](#5-data-flow--interaction-diagrams)
6. [Command Pattern: Undo / Redo / Reset](#6-command-pattern-undo--redo--reset)
7. [File Structure Reference](#7-file-structure-reference)
8. [Workload Distribution — 5-Member Team](#8-workload-distribution--5-member-team)
9. [Coding Conventions & Standards](#9-coding-conventions--standards)
10. [Future Extension Points](#10-future-extension-points)

---

## 1. System Overview

Quoridor is implemented as a desktop application built with **Python 3.11+** and **PySide6 (Qt 6)**. The architecture follows a strict **Model-View-Controller (MVC)** separation of concerns, ensuring that:

- The **Model** layer is entirely free of Qt imports and can be unit-tested in isolation.
- The **View** layer only reads state; it never mutates the Model directly.
- The **Controller** layer is the exclusive bridge — it processes user input, mutates the Model, and pushes the resulting state to the View.

```
┌─────────────────────────────────────────────────────────────┐
│                        QUORIDOR APP                         │
│                                                             │
│  ┌─────────────┐    signals/slots    ┌──────────────────┐   │
│  │   V I E W   │ ←────────────────   │  C O N T R O L L │   │
│  │  (PySide6)  │ ──── events ────►   │  E R  (Qt layer) │   │
│  └─────────────┘                     └──────────────────┘   │
│                                            │                │
│                                    mutates / reads          │
│                                            ▼                │
│                                  ┌──────────────────┐       │
│                                  │   M O D E L      │       │
│                                  │  (pure Python)   │       │
│                                  └──────────────────┘       │
│                                            │                │
│                                      uses for AI            │
│                                            ▼                │
│                                  ┌──────────────────┐       │
│                                  │  AI  SERVICE     │       │
│                                  │  (QRunnable)     │       │
│                                  └──────────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. MVC Layer Breakdown

### 2.1 Model Layer — `models/`

The Model layer contains **all game logic** with zero Qt dependencies.

| File | Responsibility |
|------|---------------|
| `wall.py` | `Wall` dataclass + `WallOrientation` enum. Immutable, frozen. |
| `pawn.py` | Mutable `Pawn` with position, goal-row, and `reset()`. |
| `board.py` | `Board` — owns placed wall sets, validates placements, computes passage. |
| `pathfinder.py` | `Pathfinder` — stateless BFS for path-existence and shortest-path queries. |
| `game_state.py` | `GameState` — aggregate root; owns board + pawns + history stacks. |

**Key design decisions:**

- **Immutable Wall records** (`frozen=True` dataclass) prevent accidental mutation after placement. Walls are stored in plain Python `set[tuple[int,int]]` for O(1) lookup — no class instances needed in the hot-path.

- **Board does not own Pawn objects.** The Board is a pure surface; Pawn positions are passed into wall-validation calls. This allows the AI to clone only the `Board` for lookahead without copying heavyweight Pawn state.

- **Pathfinder is stateless** — every method is `@staticmethod`. The Pathfinder takes a `Board` reference and operates on it, enabling the Board's `_is_h_wall_valid()` to tentatively place, validate, and remove a wall in the same transaction without any Pathfinder lifecycle management.

- **GameState is the aggregate root.** The Controller layer should ONLY mutate `GameState`; it should never call `Board` or `Pawn` methods directly.

### 2.2 View Layer — `views/`

The View layer is purely declarative: it renders state it has been given and emits Signals when the user interacts.

```
views/
├── styles.py                 ← Master QSS + colour token dict + font helpers
├── components/
│   ├── top_bar.py            ← Reusable navigation bar (all 6 screens)
│   ├── wall_indicator.py     ← Custom-painted wall-count bar
│   └── board_widget.py       ← Custom-painted 9×9 board canvas
└── screens/
    ├── main_menu_screen.py   ← Landing page
    ├── local_match_screen.py ← 2-player setup
    ├── ai_match_screen.py    ← AI mode setup
    ├── board_screen.py       ← Gameplay wrapper (HUDs + BoardWidget + toolbar)
    ├── victory_screen.py     ← End-game stats
    └── rules_screen.py       ← Scrollable rules reference
```

**Design rules enforced in the View layer:**

1. **No business logic.** Screens do not call model methods.
2. **Single `refresh()` method per screen.** State is pushed in a single dictionary-like call from the Controller, not through multiple setters that could leave the UI in an inconsistent intermediate state.
3. **All colour tokens are imported from `views/styles.py`**, never hardcoded. Changing a token in `COLORS` propagates everywhere automatically.
4. **Custom painting over Qt stylesheets** is used for the `BoardWidget` and `WallIndicator` because QSS cannot express the board's precise geometry rules. All other widgets use the master QSS stylesheet.

### 2.3 Controller Layer — `controllers/`

| File | Responsibility |
|------|---------------|
| `game_controller.py` | Owns `GameState`. Processes all user input events. Drives the AI via `AIWorker`. Emits `game_started`, `game_over`, `board_updated`. |
| `navigation_controller.py` | Owns `QStackedWidget` and all 6 screen instances. Wires every inter-screen signal. Manages the Rules back-stack. |

**Why two controllers?**

Separating navigation from game logic is a deliberate architectural decision. The `GameController` is concerned with *game turns* and has no knowledge of which screen is visible. The `NavigationController` is concerned with *which screen is shown* and does not know the current game score. This allows either controller to be replaced or extended without affecting the other.

---

## 3. Entity Mapping

### `Wall`
```
Wall(row, col, orientation, owner)
```
An **immutable record** describing a physical wall segment placed on the board. `(row, col)` is the top-left anchor of the two-cell span. `orientation` is either `HORIZONTAL` (blocks N-S movement) or `VERTICAL` (blocks E-W movement). `owner` records which player placed it (for future coloured-wall rendering).

> **Why frozen dataclass?** Once a wall is placed in Quoridor it can never be moved. Making the record frozen prevents any part of the codebase from accidentally mutating it and makes hashing trivial for set storage.

### `Pawn`
```
Pawn(player_index) → row, col, goal_row
```
A **mutable value object** tracking one player's position. It exposes `move_to(row, col)` and `has_reached_goal()`. The `reset()` method returns it to the canonical starting position without creating a new object, preserving any external references.

> **Why not a frozen dataclass?** Pawns move every turn. Cloning a new immutable pawn on every move would generate GC pressure; mutating in-place is cheaper and semantically correct — a pawn is a *thing that moves*, not a snapshot.

### `Board`
```
Board → h_walls: set[tuple], v_walls: set[tuple]
```
The **domain surface** — models the physical board. Exposes `can_move_between(r1,c1,r2,c2)` (O(1) wall lookup), `legal_pawn_moves(...)` (implements all Quoridor movement rules including jumps), and `is_wall_placement_valid(...)` (validates geometry, no-crossing, and path-integrity). The `clone()` method returns a shallow copy safe for AI lookahead mutation.

### `Pathfinder`
```
Pathfinder  (all static methods)
    has_path(board, start_row, start_col, goal_row) → bool
    shortest_path_length(board, ...) → int
    reachable_squares(board, from_row, from_col) → list[tuple]
```
A **stateless BFS utility**. The 9×9 grid means BFS visits at most 81 nodes — constant time in practice. `has_path` is called inside `Board.is_wall_placement_valid` for every proposed wall (after tentatively placing it), making it the hottest code path. Keeping it stateless avoids any reset/initialise overhead.

### `GameState`
```
GameState
    board: Board
    pawns: list[Pawn]
    players: list[PlayerInfo]
    current_player: int
    phase: GamePhase
    winner: Optional[int]
    _history: list[dict]     ← undo stack
    _redo_stack: list[dict]  ← redo stack
```
The **aggregate root** of the Model. All mutations (pawn moves, wall placements) route through `apply_pawn_move()` / `apply_wall_placement()`, which push command dicts onto `_history`. `undo()` pops from `_history`, inverts the command, and pushes to `_redo_stack`. Any new action clears `_redo_stack` (standard behaviour).

### `PlayerInfo`
```
PlayerInfo(name, is_ai, walls_remaining, walls_placed, turns_taken)
```
A plain dataclass holding **meta-information** about a player. This data is display-only and does not affect game logic; it is read by the View layer (HUDs, Victory screen) and by the Controller (AI scheduling check).

---

## 4. AI Service Integration

### Architecture

The AI service is implemented as a **plugin-style module** (`services/ai_engine.py`) that is entirely decoupled from Qt. It exposes a single pure function:

```python
compute_move(state: GameState, ai_idx: int, difficulty: str) → dict
```

The Controller never calls `compute_move` directly from the main thread. Instead it wraps it in `AIWorker`, a `QRunnable` that runs on Qt's global thread pool:

```
Main Thread                        Worker Thread
    │                                   │
    │── QThreadPool.start(AIWorker) ───►│
    │                                   │  compute_move(state_copy, ...)
    │                                   │  ← deep-copied GameState
    │◄── Signal: finished(move_dict) ───│
    │
    │── on_ai_move_ready(move_dict)
    │── _state.apply_*()
    │── _push_display()
```

The `GameState` is **deep-copied** before being passed to the worker, so the worker operates on a private snapshot. The main thread's `GameState` is never shared with the worker thread, eliminating all race conditions without a mutex.

### Difficulty Tiers

| Tier | Algorithm | Depth | Notes |
|------|-----------|-------|-------|
| **Novice** (easy) | Biased-random | 0-ply | 80% greedy advance, 20% random pawn move. No wall logic. |
| **Adept** (medium) | Greedy 1-ply | 1 | Evaluates all pawn moves + top 20 impactful walls using the `_evaluate()` heuristic. |
| **Architect** (hard) | Minimax + Alpha-Beta | 3 | Full tree search with α-β pruning. Considers top 12 wall candidates at the root, 8 at deeper nodes. |

### Heuristic Function

```
score = opponent_shortest_path − ai_shortest_path
```

A positive score means the AI is closer to its goal than the opponent. Both path lengths are computed via BFS. Terminal states return ±∞. The heuristic is deliberately simple — path-length differential has proven effective in Quoridor AI literature because the game is fundamentally a race.

---

## 5. Data Flow & Interaction Diagrams

### Human pawn move (local game)

```
User clicks cell (r, c) on BoardWidget
         │
         ▼
  BoardWidget.cell_clicked signal
         │
         ▼
  GameController.on_cell_clicked(r, c)
    ├─ Is pawn selected? No → select pawn, compute valid_moves
    ├─ Is (r,c) in valid_moves? Yes →
    │     GameState.apply_pawn_move(r, c)
    │         ├─ Pawn.move_to(r, c)
    │         ├─ _after_action() → push command to history, advance turn
    │         └─ Check has_reached_goal()
    │
    └─ _push_display()
         │
         ▼
  BoardScreen.refresh(pawn_positions, walls, valid_moves, ...)
         │
         ▼
  BoardWidget.refresh() → repaint
```

### AI move (after human acts)

```
GameController._after_action()
    └─ _is_ai_turn() == True
         │
         ▼
  _schedule_ai_move()
    └─ AIWorker(deep_copy(state), ai_idx, difficulty)
         │
  QThreadPool.globalInstance().start(worker)
         │
  [Worker thread]
  compute_move(state_copy, ai_idx, difficulty)
         │
  worker.signals.finished.emit(move_dict)
         │ (Qt queued connection → main thread)
         ▼
  GameController.on_ai_move_ready(move_dict)
    └─ GameState.apply_pawn_move() or apply_wall_placement()
    └─ _push_display()
```

---

## 6. Command Pattern: Undo / Redo / Reset

All reversible game actions are stored as plain `dict` objects on `GameState._history`.

### Command schema

```python
# Pawn move
{"type": "pawn_move", "player": 0, "from_row": 8, "from_col": 4,
 "to_row": 7, "to_col": 4}

# Wall placement
{"type": "wall_place", "player": 0, "row": 5, "col": 3,
 "orientation": WallOrientation.HORIZONTAL}
```

### Undo flow

```
GameState.undo()
    │
    ├─ command = _history.pop()
    ├─ _redo_stack.append(command)
    ├─ _invert_command(command)  ← reverses Pawn.move_to or Board.remove_wall
    └─ Restore current_player, decrement counters
```

### Redo flow

```
GameState.redo()
    │
    └─ command = _redo_stack.pop()
       _replay_command(command) ← re-calls apply_pawn_move / apply_wall_placement
```

### Reset

`GameState.reset()` preserves player names and AI flags but clears the board, repositions pawns, restores 10 walls per player, and empties both stacks. It is equivalent to starting a fresh game with the same configuration.

---

## 7. Validated Bug-fixes Applied to Shared Files

The following corrections were applied during the Rules screen implementation
phase and affect files shared by all team members.  Every member must pull
these changes before starting their own work.

| File | Fix |
|------|-----|
| `views/components/top_bar.py` | `Qt` was imported inside a class body (a class-level `from … import` statement), which is legal Python but makes the name unavailable to instance methods that run after `__init__`. Moved to the module-level import line. |
| `views/styles.py` | `QFont.Weight.SemiBold` does not exist in PySide6 (the correct name is `DemiBold`). Fixed in `_FONT_SCALE`. |
| `views/styles.py` | `box-sizing: border-box` in the global QSS `* {}` reset block is a CSS3 property that Qt's stylesheet engine does not support. It generated hundreds of `Unknown property box-sizing` console warnings. Removed. |
| `views/screens/rules_screen.py` | Added `reset_scroll()` public helper. `NavigationController._push_rules()` must call `self.rules.reset_scroll()` after `self.go_to(IDX_RULES)` so the scroll position resets to the top on every push. |

### Required update to `navigation_controller.py`

Member 4 (Screen & Navigation Engineer) must add the `reset_scroll()` call
inside `_push_rules()`:

```python
def _push_rules(self) -> None:
    self._prev_idx = self._stack.currentIndex()
    self.go_to(IDX_RULES)
    self.rules.reset_scroll()   # ← ADD THIS LINE
```

---

## 7. File Structure Reference

```
quoridor/
├── main.py                          ← QApplication entry point
├── requirements.txt                 ← PySide6>=6.6.0
│
├── models/
│   ├── __init__.py
│   ├── wall.py                      ← Wall, WallOrientation
│   ├── pawn.py                      ← Pawn, goal/start constants
│   ├── board.py                     ← Board (wall logic, move generation)
│   ├── pathfinder.py                ← BFS utilities (stateless)
│   └── game_state.py                ← GameState aggregate root
│
├── views/
│   ├── __init__.py
│   ├── styles.py                    ← COLORS dict, STYLESHEET QSS, font()
│   ├── components/
│   │   ├── __init__.py
│   │   ├── top_bar.py               ← Shared navigation bar
│   │   ├── wall_indicator.py        ← Custom-painted wall-count widget
│   │   └── board_widget.py          ← Custom-painted 9×9 board
│   └── screens/
│       ├── __init__.py
│       ├── main_menu_screen.py      ← Screen 1: landing page
│       ├── local_match_screen.py    ← Screen 2: local setup
│       ├── ai_match_screen.py       ← Screen 3: AI setup
│       ├── board_screen.py          ← Screen 4: gameplay
│       ├── victory_screen.py        ← Screen 5: results
│       └── rules_screen.py          ← Screen 6: rules reference
│
├── controllers/
│   ├── __init__.py
│   ├── game_controller.py           ← Turn management, AI scheduling
│   └── navigation_controller.py     ← Screen routing, signal wiring
│
└── services/
    ├── __init__.py
    └── ai_engine.py                 ← Novice/Adept/Architect AI + AIWorker
```

---

## 8. Workload Distribution — 5-Member Team

The remaining development is divided into five tightly scoped roles. Each team member owns a vertical slice of the system, has a clear deliverable, and interfaces with the others only through the already-defined Signal / Slot contracts.

---

### Member 1 — Model & Pathfinder Engineer

**Files owned:**
- `models/board.py` — complete and test the full move-generation engine
- `models/pathfinder.py` — validate BFS correctness and edge cases
- `models/game_state.py` — stabilise the command pattern; add serialisation for save/load bonus feature

**Tasks:**

1. **Complete unit tests** for `Board.legal_pawn_moves()`:
   - Normal orthogonal steps
   - Straight jump over adjacent opponent
   - Diagonal sidestep when straight jump is wall-blocked
   - Diagonal sidestep when straight jump is board-edge-blocked
2. **Verify wall-crossing detection** in `Board._is_h_wall_valid()` and `_is_v_wall_valid()`. Write regression tests for all known invalid combinations (duplicate, neighbour overlap, crossing).
3. **Implement BFS path-length caching** (optional optimisation): since `shortest_path_length` is called twice per wall-validity check, memoising on `(board_hash, start, goal)` could reduce AI latency on hard mode.
4. **Implement `GameState.to_dict / from_dict`** fully (Bonus: save/load feature). Persist to JSON at a user-chosen path.
5. **Write a comprehensive test suite** (`tests/test_model.py`) covering all rule edge cases from the project spec.

**Deliverable:** A test-verified, fully correct Model layer. All tests pass in isolation (no Qt required).

---

### Member 2 — Board Rendering & Visual Polish Engineer

**Files owned:**
- `views/components/board_widget.py` — pixel-perfect board rendering
- `views/components/wall_indicator.py` — wall bar polish
- `views/styles.py` — stylesheet refinement

**Tasks:**

1. **Pixel-calibrate the board geometry** against the Board.html prototype (Image 4). Verify that `CELL_SIZE`, `WALL_GAP`, and `PADDING` produce a board that matches the HTML's `60px` cell / `4px` gap specification at 1× scale.
2. **Implement the `_draw_wall_preview` ghost animation**: add a subtle opacity pulse (QTimer 200ms) so the preview shimmers slightly instead of being static.
3. **Player-colour differentiation for walls**: modify `_draw_placed_walls()` to use `COLORS["primary-fixed"]` for P1 walls and `COLORS["secondary"]` for P2 walls. Wall owner must be looked up from `h_walls` / `v_walls` (hint: store a parallel `h_wall_owners: dict[tuple, int]` in `BoardScreen.refresh()`).
4. **Smooth pawn animation**: instead of instantly teleporting pawns on `refresh()`, interpolate the pawn position over 200ms using a `QPropertyAnimation` or a custom `QTimer` based tweening loop.
5. **Responsive board scaling**: the board is currently fixed at its calculated pixel size. Implement `resizeEvent` to scale the board proportionally when the window is resized.
6. **Accessibility**: add `setToolTip()` calls for wall slots showing `"Place horizontal/vertical wall here"` on hover.

**Deliverable:** A visually polished, animated board widget that matches the design reference at any window size from 768px wide upward.

---

### Member 3 — AI Engineer

**Files owned:**
- `services/ai_engine.py` — complete and tune all three difficulty tiers

**Tasks:**

1. **Verify Novice correctness**: ensure the random-pool logic never produces illegal moves. Write a simulation that plays 100 Novice games and asserts no rule violations occur.
2. **Tune Adept heuristic weights**: the current heuristic is `human_dist − ai_dist`. Experiment with adding a wall-proximity penalty (penalise the AI for placing walls near the human when the human is already far away).
3. **Profile Architect at depth 3**: use Python's `cProfile` to measure move-computation time on the hardest board positions. If mean latency exceeds 2 seconds, reduce wall candidates from 12 to 8 at the root, or drop depth to 2 for mid-game positions.
4. **Add `_wall_blocking_score()`** helper: a lightweight function (O(1)) that scores a candidate wall by how many of the human's BFS edges it removes, enabling faster pruning before full BFS validation.
5. **Implement "thinking" time simulation** for Novice and Adept: add a configurable `min_think_ms` delay (default 400ms) before the worker emits `finished`, so the AI feels less instant and the UI can display the "AI Thinking…" banner meaningfully.
6. **Document all algorithms** in the project report: include pseudocode for Minimax, the heuristic formula, and a complexity analysis for each tier.

**Deliverable:** A fully documented, tested, and performant three-tier AI engine. No UI freezes on hard mode.

---

### Member 4 — Screen & Navigation Engineer

**Files owned:**
- All files under `views/screens/` (polish and complete)
- `controllers/navigation_controller.py`
- `views/components/top_bar.py`

**Tasks:**

1. **Input validation on setup screens**: `LocalMatchScreen` and `AiMatchScreen` should show an inline error state (red border on the empty `QLineEdit`) if the user clicks "Begin" without entering a name, instead of silently defaulting.
2. **Keyboard navigation**: ensure all interactive elements are reachable via Tab/Enter. The board toolbar buttons must have `setShortcut()` assignments:
   - `Ctrl+Z` → Undo
   - `Ctrl+Y` → Redo
   - `Ctrl+R` → Reset
   - `Escape` → Exit
3. **Victory screen winner name highlight**: add a styled `QLabel` in golden amber (`COLORS["tertiary-fixed"]`) to prominently display the winner's name separately from the body text.
4. **Animated screen transitions**: wrap the `QStackedWidget` with a `QSequentialAnimationGroup` that fades the outgoing screen out (opacity 1→0 over 150ms) and fades the incoming screen in (opacity 0→1 over 150ms).
5. **Rules screen scroll position**: reset the scroll area's vertical bar to 0 whenever the Rules screen is shown, so users always start at the top.
6. **Board screen wall-mode toggle**: add a `QRadioButton` pair or a toggle button to the board toolbar so the player can explicitly switch between Pawn Mode and Wall Mode instead of the current implicit detection.

**Deliverable:** Polished, keyboard-navigable, fully connected screen layer with smooth transitions.

---

### Member 5 — Documentation, QA & Bonus Features Engineer

**Files owned:**
- `README.md` (create)
- `tests/` directory (create and populate)
- Bonus feature implementation (choose one)

**Tasks:**

1. **Write `README.md`** covering all items from the project spec:
   - Game description and objective
   - Installation and running instructions (`pip install -r requirements.txt && python main.py`)
   - Controls explanation (pawn click → highlight → destination click; wall slot hover/click)
   - Screenshot gallery (capture all 6 screens)
   - Link to demo video
2. **Integration test suite** (`tests/test_integration.py`): simulate a full game using the Controller's public methods without a display (headless mode using `QApplication(["", "-platform", "offscreen"])`).
3. **Implement the selected Bonus Feature** (recommend: **Undo/Redo** is already scaffolded — complete the UI indicators and write a test suite for it; **or** implement **game state save/load** using the `GameState.to_dict()` skeleton):
   - Save: `QAction` in a `QMenu` attached to the main window, writes JSON.
   - Load: file picker dialog, restores state and refreshes the board.
4. **Record the demo video** (3–5 minutes):
   - Section 1: UI walkthrough of all 6 screens.
   - Section 2: Full human-vs-human local game to completion.
   - Section 3: Human-vs-Architect AI game showing the AI's wall strategy.
5. **Write the project report** (PDF, 6–10 pages) per the deliverable spec, covering design decisions, challenges, AI algorithm explanation, and references.

**Deliverable:** Complete README, test suite, bonus feature implementation, demo video, and PDF report.

---

## 9. Coding Conventions & Standards

All contributors must adhere to:

| Convention | Requirement |
|------------|-------------|
| **Type hints** | Every function signature must have full type annotations. Use `from __future__ import annotations` for forward references. |
| **Docstrings** | Every module, class, and public method must have a NumPy-style docstring with Parameters, Returns, and Raises sections. |
| **Naming** | `snake_case` for all Python identifiers. Qt widget object names use `PascalCase` strings (e.g., `"BtnPrimary"`). |
| **Imports** | Group: stdlib → third-party → project-local. Absolute imports only. |
| **Line length** | 95 characters maximum. |
| **No magic numbers** | All board geometry constants live in `BoardWidget` as class attributes. All colours come from `COLORS`. |
| **No Qt in models/** | `models/` must import only stdlib. Verified by a linter rule. |
| **Commits** | Conventional Commits format: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`. One logical change per commit. |

---

## 10. Future Extension Points

The architecture is designed to accommodate all four bonus features without structural changes:

| Feature | Extension Point |
|---------|----------------|
| **Save / Load** | `GameState.to_dict()` / `from_dict()` already scaffolded. Add a `QAction` in `MainWindow` and a `QFileDialog`. |
| **Undo / Redo** | Command stacks fully implemented in `GameState`. UI buttons already wired. Member 4 adds keyboard shortcuts. |
| **4-Player Mode** | Add `Pawn(2)`, `Pawn(3)` to `GameState.pawns`. Add `goal_row` entries. `Board.legal_pawn_moves` already accepts arbitrary positions. |
| **Custom Board Sizes** | `Board.BOARD_SIZE` is a class constant. `BoardWidget.CELL_SIZE / WALL_GAP / PADDING` are instance-level. Pass size to constructors. |

---

*Document version: 1.0 · May 2026 · CSE472s Spring 2026*