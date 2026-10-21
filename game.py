"""
game.py - Trạng thái một ván cờ: bàn cờ, render ảnh PNG, và tích hợp Stockfish
cho chế độ đấu với máy.
"""
from dataclasses import dataclass, field
from typing import Optional

import chess
import chess.engine
import chess.svg
import cairosvg


@dataclass
class ChessGame:
    thread_id: int
    white_id: Optional[int]          # Discord user id cầm quân Trắng, None = máy đi
    black_id: Optional[int]          # Discord user id cầm quân Đen, None = máy đi
    board: chess.Board = field(default_factory=chess.Board)
    vs_bot: bool = False
    engine: Optional[chess.engine.UciProtocol] = None
    finished: bool = False

    def player_color(self, user_id: int) -> Optional[chess.Color]:
        """Trả về màu quân của user_id trong ván này, hoặc None nếu chỉ là người xem."""
        if user_id == self.white_id:
            return chess.WHITE
        if user_id == self.black_id:
            return chess.BLACK
        return None

    def is_bot_turn(self) -> bool:
        if not self.vs_bot:
            return False
        turn_id = self.white_id if self.board.turn == chess.WHITE else self.black_id
        return turn_id is None

    def render_png(self) -> bytes:
        last_move = self.board.peek() if self.board.move_stack else None
        check_square = self.board.king(self.board.turn) if self.board.is_check() else None
        svg_data = chess.svg.board(
            board=self.board,
            lastmove=last_move,
            check=check_square,
            size=480,
        )
        return cairosvg.svg2png(bytestring=svg_data.encode("utf-8"))

    def parse_move(self, text: str) -> Optional[chess.Move]:
        """Chấp nhận cả ký hiệu SAN (e4, Nf3) lẫn UCI (e2e4)."""
        text = text.strip()
        try:
            return self.board.parse_san(text)
        except ValueError:
            pass
        try:
            move = chess.Move.from_uci(text.lower())
        except ValueError:
            return None
        return move if move in self.board.legal_moves else None

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

    async def get_bot_move(self, think_time: float = 1.0) -> chess.Move:
        result = await self.engine.play(self.board, chess.engine.Limit(time=think_time))
        return result.move
      
