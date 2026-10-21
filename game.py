"""
game.py - Trạng thái một ván cờ: bàn cờ và render ảnh PNG.
"""
from dataclasses import dataclass, field
from typing import Optional

import chess
import chess.svg
import cairosvg


@dataclass
class ChessGame:
    thread_id: int
    white_id: int
    black_id: int
    board: chess.Board = field(default_factory=chess.Board)
    finished: bool = False
    board_message_id: Optional[int] = None
    white_name: str = "Trắng"
    black_name: str = "Đen"
    _png_cache_fen: Optional[str] = field(default=None, repr=False)
    _png_cache_bytes: Optional[bytes] = field(default=None, repr=False)

    def player_color(self, user_id: int) -> Optional[chess.Color]:
        if user_id == self.white_id:
            return chess.WHITE
        if user_id == self.black_id:
            return chess.BLACK
        return None

    def is_player(self, user_id: int) -> bool:
        return user_id in (self.white_id, self.black_id)

    def legal_from_squares(self) -> list[chess.Square]:
        return sorted({m.from_square for m in self.board.legal_moves})

    def legal_moves_from(self, from_sq: chess.Square) -> list[chess.Move]:
        return [m for m in self.board.legal_moves if m.from_square == from_sq]

    def render_png(self) -> bytes:
        fen = self.board.fen()
        if self._png_cache_fen == fen and self._png_cache_bytes is not None:
            return self._png_cache_bytes

        last_move = self.board.peek() if self.board.move_stack else None
        check_square = self.board.king(self.board.turn) if self.board.is_check() else None
        svg_data = chess.svg.board(
            board=self.board,
            lastmove=last_move,
            check=check_square,
            size=400,
        )
        png = cairosvg.svg2png(bytestring=svg_data.encode("utf-8"))
        self._png_cache_fen = fen
        self._png_cache_bytes = png
        return png

    def result_text(self) -> str:
        if self.board.is_checkmate():
            winner = "Đen" if self.board.turn == chess.WHITE else "Trắng"
            return f"Chiếu hết! {winner} thắng."
        if self.board.is_stalemate():
            return "Hòa do hết nước đi hợp lệ (stalemate)."
        if self.board.is_insufficient_material():
            return "Hòa do không đủ quân để chiếu hết."
        if self.board.can_claim_fifty_moves():
            return "Hòa theo luật 50 nước."
        if self.board.can_claim_threefold_repetition():
            return "Hòa do lặp lại thế cờ 3 lần."
        return "Ván đấu kết thúc."

    def outcome(self) -> str:
        """'1-0' | '0-1' | '1/2-1/2' | '*' """
        if not self.board.is_game_over(claim_draw=True):
            return "*"
        result = self.board.result(claim_draw=True)
        return result

    def pgn_moves(self) -> str:
        board = self.board.copy()
        moves = list(board.move_stack)
        board.reset()
        parts: list[str] = []
        for i, move in enumerate(moves):
            if i % 2 == 0:
                parts.append(f"{i // 2 + 1}.")
            parts.append(board.san(move))
            board.push(move)
        return " ".join(parts) if parts else "(chưa có nước đi)"
