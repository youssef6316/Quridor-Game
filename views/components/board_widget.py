"""
views/components/board_widget.py
=================================
Custom-painted 9×9 Quoridor board widget.

Responsibilities
----------------
• Draws the grid of cells, gap strips (wall slots), and board border.
• Renders placed horizontal and vertical walls.
• Renders both player pawns as filled circles.
• Highlights valid pawn move targets on hover / after pawn selection.
• Shows a translucent wall preview while the user hovers over a wall slot.
• Translates mouse events into semantic game signals consumed by the
  GameController.

Coordinate helpers
------------------
Given cell (row, col) on the 9×9 grid:
  x  = PADDING + col  * (CELL_SIZE + WALL_GAP)
  y  = PADDING + row  * (CELL_SIZE + WALL_GAP)

A horizontal wall at (r, c) (between rows r and r+1, cols c and c+1):
  x  = PADDING + c * (CELL_SIZE + WALL_GAP)
  y  = PADDING + r * (CELL_SIZE + WALL_GAP) + CELL_SIZE
  w  = 2 * CELL_SIZE + WALL_GAP
  h  = WALL_GAP

A vertical wall at (r, c) (between cols c and c+1, rows r and r+1):
  x  = PADDING + c * (CELL_SIZE + WALL_GAP) + CELL_SIZE
  y  = PADDING + r * (CELL_SIZE + WALL_GAP)
  w  = WALL_GAP
  h  = 2 * CELL_SIZE + WALL_GAP

Signals emitted
---------------
cell_clicked(row, col)       — user clicked on an empty cell
wall_hover(r, c, orientation) — user is hovering over a wall slot
wall_clicked(r, c, orientation) — user clicked on a wall slot
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from PySide6.QtCore import Qt, QPoint, QRectF, Signal, QTimer
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QBrush, QRadialGradient,
    QMouseEvent, QEnterEvent,
)
from PySide6.QtWidgets import QWidget, QSizePolicy

from models.wall import WallOrientation
from views.styles import COLORS


# ──────────────────────────────────────────────────────────────────────────────
# Internal element types returned by hit-testing
# ──────────────────────────────────────────────────────────────────────────────

class _ElemKind(Enum):
    CELL          = auto()
    H_WALL_SLOT   = auto()
    V_WALL_SLOT   = auto()
    INTERSECTION  = auto()
    OUTSIDE       = auto()


@dataclass
class _BoardElem:
    kind   : _ElemKind
    row    : int = -1
    col    : int = -1
    wall_r : int = -1   # wall-grid row (0-7)
    wall_c : int = -1   # wall-grid col (0-7)
    orientation: Optional[WallOrientation] = None


# ──────────────────────────────────────────────────────────────────────────────
# BoardWidget
# ──────────────────────────────────────────────────────────────────────────────

class BoardWidget(QWidget):
    """
    Pixel-perfect Quoridor board rendered via QPainter.

    Signals
    -------
    cell_clicked(row, col)
        Emitted when the user left-clicks a board cell.
    wall_clicked(r, c, orientation)
        Emitted when the user left-clicks a valid wall slot.
    """

    # ── Qt Signals ──────────────────────────────────────────────────────────
    cell_clicked  : Signal = Signal(int, int)
    wall_clicked  : Signal = Signal(int, int, WallOrientation)

    # ── Visual constants ─────────────────────────────────────────────────────
    CELL_SIZE  : int = 52    # pixel width/height of one grid cell
    WALL_GAP   : int = 7     # pixel width of wall-slot strip
    PADDING    : int = 14    # board outer padding
    PAWN_RATIO : float = 0.50   # pawn radius as fraction of CELL_SIZE/2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # ── State exposed to the controller ─────────────────────────────────
        # These are set externally (by GameController) before each repaint.
        self.pawn_positions   : list[tuple[int, int]] = [(8, 4), (0, 4)]
        self.h_walls          : set[tuple[int, int]]  = set()
        self.v_walls          : set[tuple[int, int]]  = set()
        self.valid_moves      : list[tuple[int, int]] = []
        self.selected_pawn    : Optional[int]          = None   # 0 or 1

        # ── Hover / preview state ────────────────────────────────────────────
        self._hover_elem      : Optional[_BoardElem] = None
        self._wall_preview    : Optional[_BoardElem] = None   # filled when hovering slot

        # ── Widget setup ─────────────────────────────────────────────────────
        board_px = (
            2 * self.PADDING +
            9 * self.CELL_SIZE +
            8 * self.WALL_GAP
        )
        self.setFixedSize(board_px, board_px)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)  # receive mouseMoveEvent without pressing

    # ------------------------------------------------------------------
    # Public update API (called by GameController / BoardScreen)
    # ------------------------------------------------------------------

    def refresh(
        self,
        pawn_positions : list[tuple[int, int]],
        h_walls        : set[tuple[int, int]],
        v_walls        : set[tuple[int, int]],
        valid_moves    : list[tuple[int, int]],
        selected_pawn  : Optional[int],
    ) -> None:
        """
        Update all display state at once and trigger a repaint.

        Parameters
        ----------
        pawn_positions : list[tuple[int, int]]
            [(p0_row, p0_col), (p1_row, p1_col)].
        h_walls : set[tuple[int, int]]
            All placed horizontal walls as (r, c).
        v_walls : set[tuple[int, int]]
            All placed vertical walls as (r, c).
        valid_moves : list[tuple[int, int]]
            Squares highlighted as valid move targets for the active player.
        selected_pawn : int | None
            Index of the pawn currently selected (shows move highlights).
        """
        self.pawn_positions = pawn_positions
        self.h_walls        = h_walls
        self.v_walls        = v_walls
        self.valid_moves    = valid_moves
        self.selected_pawn  = selected_pawn
        self.update()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _cell_rect(self, row: int, col: int) -> QRectF:
        """Return the pixel rectangle for grid cell (row, col)."""
        unit = self.CELL_SIZE + self.WALL_GAP
        x = self.PADDING + col * unit
        y = self.PADDING + row * unit
        return QRectF(x, y, self.CELL_SIZE, self.CELL_SIZE)

    def _h_wall_rect(self, r: int, c: int) -> QRectF:
        """Return the pixel rect for the horizontal wall slot at (r, c)."""
        unit = self.CELL_SIZE + self.WALL_GAP
        x = self.PADDING + c * unit
        y = self.PADDING + r * unit + self.CELL_SIZE
        w = 2 * self.CELL_SIZE + self.WALL_GAP
        return QRectF(x, y, w, self.WALL_GAP)

    def _v_wall_rect(self, r: int, c: int) -> QRectF:
        """Return the pixel rect for the vertical wall slot at (r, c)."""
        unit = self.CELL_SIZE + self.WALL_GAP
        x = self.PADDING + c * unit + self.CELL_SIZE
        y = self.PADDING + r * unit
        h = 2 * self.CELL_SIZE + self.WALL_GAP
        return QRectF(x, y, self.WALL_GAP, h)

    def _pawn_center(self, row: int, col: int) -> tuple[float, float]:
        """Return the pixel centre-point for a pawn at (row, col)."""
        r = self._cell_rect(row, col)
        return (r.x() + r.width() / 2, r.y() + r.height() / 2)

    # ------------------------------------------------------------------
    # Hit testing
    # ------------------------------------------------------------------

    def _hit_test(self, pos: QPoint) -> _BoardElem:
        """
        Determine which board element (cell, wall slot, etc.) is under the
        given pixel position.

        Parameters
        ----------
        pos : QPoint
            Mouse position in widget-local coordinates.

        Returns
        -------
        _BoardElem
            Describes what is at that position.
        """
        unit = self.CELL_SIZE + self.WALL_GAP
        px = pos.x() - self.PADDING
        py = pos.y() - self.PADDING

        if px < 0 or py < 0:
            return _BoardElem(_ElemKind.OUTSIDE)

        col_unit  = int(px // unit)
        row_unit  = int(py // unit)
        col_frac  = px % unit   # offset within the column unit
        row_frac  = py % unit   # offset within the row unit

        in_cell_col = col_frac < self.CELL_SIZE
        in_cell_row = row_frac < self.CELL_SIZE

        # Guard maximum bounds
        col_unit = min(col_unit, 8)
        row_unit = min(row_unit, 8)

        if in_cell_row and in_cell_col:
            # Plain cell
            if 0 <= row_unit <= 8 and 0 <= col_unit <= 8:
                return _BoardElem(_ElemKind.CELL, row=row_unit, col=col_unit)

        elif not in_cell_row and in_cell_col:
            # Horizontal wall gap — between rows row_unit and row_unit+1
            # at cell column col_unit
            wr = row_unit   # wall r ∈ [0,7]
            wc = col_unit   # start column for the wall
            if 0 <= wr <= 7 and 0 <= wc <= 7:
                return _BoardElem(
                    _ElemKind.H_WALL_SLOT,
                    wall_r=wr, wall_c=wc,
                    orientation=WallOrientation.HORIZONTAL,
                )

        elif in_cell_row and not in_cell_col:
            # Vertical wall gap — between cols col_unit and col_unit+1
            # at cell row row_unit
            wr = row_unit
            wc = col_unit
            if 0 <= wr <= 7 and 0 <= wc <= 7:
                return _BoardElem(
                    _ElemKind.V_WALL_SLOT,
                    wall_r=wr, wall_c=wc,
                    orientation=WallOrientation.VERTICAL,
                )

        return _BoardElem(_ElemKind.OUTSIDE)

    # ------------------------------------------------------------------
    # Mouse event handlers
    # ------------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Update hover / wall-preview state on mouse movement."""
        elem = self._hit_test(event.position().toPoint())
        self._hover_elem = elem

        if elem.kind in (_ElemKind.H_WALL_SLOT, _ElemKind.V_WALL_SLOT):
            self._wall_preview = elem
        else:
            self._wall_preview = None

        self.update()

    def leaveEvent(self, event) -> None:
        """Clear hover state when the mouse leaves the widget."""
        self._hover_elem   = None
        self._wall_preview = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """
        Dispatch left-click events to the appropriate signal:
          • Cell   → cell_clicked(row, col)
          • Wall slot → wall_clicked(r, c, orientation)
        """
        if event.button() != Qt.MouseButton.LeftButton:
            return

        elem = self._hit_test(event.position().toPoint())

        if elem.kind == _ElemKind.CELL:
            self.cell_clicked.emit(elem.row, elem.col)

        elif elem.kind in (_ElemKind.H_WALL_SLOT, _ElemKind.V_WALL_SLOT):
            self.wall_clicked.emit(
                elem.wall_r, elem.wall_c, elem.orientation
            )

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        """Full board repaint — called by Qt whenever update() is triggered."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._draw_background(painter)
        self._draw_cells(painter)
        self._draw_valid_move_highlights(painter)
        self._draw_placed_walls(painter)
        self._draw_wall_preview(painter)
        self._draw_pawns(painter)

        painter.end()

    # ── Painting sub-routines ────────────────────────────────────────────────

    def _draw_background(self, p: QPainter) -> None:
        """Draw board outer background with subtle rounded border."""
        board_px = self.width()
        bg = QRectF(0, 0, board_px, board_px)
        path = QPainterPath()
        path.addRoundedRect(bg, 8, 8)
        p.fillPath(path, QColor(COLORS["surface-container-high"]))

        pen = QPen(QColor(COLORS["outline-variant"]))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawPath(path)

    def _draw_cells(self, p: QPainter) -> None:
        """Draw all 81 grid cells."""
        cell_color = QColor(COLORS["surface-container"])
        for row in range(9):
            for col in range(9):
                rect = self._cell_rect(row, col)
                path = QPainterPath()
                path.addRoundedRect(rect, 3, 3)
                p.fillPath(path, cell_color)

    def _draw_valid_move_highlights(self, p: QPainter) -> None:
        """Render translucent highlight circles for valid move targets."""
        if not self.valid_moves:
            return

        highlight = QColor(COLORS["primary-fixed-dim"])
        highlight.setAlpha(60)

        ring = QColor(COLORS["primary-fixed-dim"])
        ring.setAlpha(180)
        pen = QPen(ring)
        pen.setWidth(2)

        for (row, col) in self.valid_moves:
            rect = self._cell_rect(row, col)
            cx = rect.x() + rect.width() / 2
            cy = rect.y() + rect.height() / 2
            r  = rect.width() * 0.3

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(highlight)
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

    def _draw_placed_walls(self, p: QPainter) -> None:
        """Render all placed walls (horizontal and vertical)."""
        p.setPen(Qt.PenStyle.NoPen)

        # Colour table indexed by owner (0=P1, 1=P2)
        # Both currently use the same wall colour; owner can be stored in Wall
        wall_color = QColor(COLORS["primary-fixed"])
        wall_color.setAlpha(230)
        p.setBrush(wall_color)

        for (r, c) in self.h_walls:
            rect = self._h_wall_rect(r, c)
            path = QPainterPath()
            path.addRoundedRect(rect, 2, 2)
            p.fillPath(path, wall_color)

        for (r, c) in self.v_walls:
            rect = self._v_wall_rect(r, c)
            path = QPainterPath()
            path.addRoundedRect(rect, 2, 2)
            p.fillPath(path, wall_color)

    def _draw_wall_preview(self, p: QPainter) -> None:
        """Render a translucent preview of the wall the user may place."""
        if not self._wall_preview:
            return

        elem = self._wall_preview
        preview_color = QColor(COLORS["primary-fixed"])
        preview_color.setAlpha(100)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(preview_color)

        if elem.orientation is WallOrientation.HORIZONTAL:
            rect = self._h_wall_rect(elem.wall_r, elem.wall_c)
        else:
            rect = self._v_wall_rect(elem.wall_r, elem.wall_c)

        path = QPainterPath()
        path.addRoundedRect(rect, 2, 2)
        p.fillPath(path, preview_color)

    def _draw_pawns(self, p: QPainter) -> None:
        """Render both pawns as styled circles with radial gradients."""
        pawn_configs = [
            # (fill_hex, border_hex)
            (COLORS["primary-fixed"],  COLORS["primary"]),         # Player 1
            (COLORS["secondary"],      COLORS["secondary-fixed"]),  # Player 2
        ]

        for idx, (row, col) in enumerate(self.pawn_positions):
            cx, cy = self._pawn_center(row, col)
            half_cell = self.CELL_SIZE / 2
            r = half_cell * self.PAWN_RATIO

            fill_hex, border_hex = pawn_configs[idx]

            # Radial gradient for a 3-D sphere look
            grad = QRadialGradient(cx - r * 0.25, cy - r * 0.25, r * 1.3)
            grad.setColorAt(0.0, QColor(fill_hex).lighter(160))
            grad.setColorAt(0.7, QColor(fill_hex))
            grad.setColorAt(1.0, QColor(fill_hex).darker(140))

            # Drop shadow
            shadow_c = QColor(0, 0, 0, 60)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(shadow_c)
            p.drawEllipse(QRectF(cx - r + 2, cy - r + 3, 2 * r, 2 * r))

            # Pawn fill
            p.setBrush(QBrush(grad))
            pen = QPen(QColor(border_hex))
            pen.setWidth(2)
            p.setPen(pen)
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

            # Active player indicator: bright ring if pawn is selected
            if self.selected_pawn == idx:
                ring_color = QColor(COLORS["primary"])
                ring_color.setAlpha(200)
                ring_pen = QPen(ring_color)
                ring_pen.setWidth(3)
                p.setPen(ring_pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(cx - r - 4, cy - r - 4, 2 * (r + 4), 2 * (r + 4)))