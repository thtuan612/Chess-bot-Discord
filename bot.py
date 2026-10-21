"""
bot.py - Bot Discord chơi cờ vua.

Lệnh:
  /chess vs doi_thu           - tạo phòng (thread) đấu 1v1 với người khác
  /chess bot mau elo          - tạo phòng đấu với Stockfish
  /resign                     - đầu hàng ván hiện tại

Đi quân bằng Discord UI (menu chọn ô), không dùng /move.
Mỗi ván chỉ giữ 1 tin nhắn ảnh bàn cờ — cập nhật (edit) thay vì gửi thêm ảnh mới.
"""
import io
import logging
import os
from typing import Optional

import chess
import chess.engine
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from game import ChessGame

load_dotenv()
TOKEN = os.environ["DISCORD_TOKEN"]
STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", "stockfish")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("chess-bot")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

games: dict[int, ChessGame] = {}  # thread_id -> ChessGame
active_player: dict[int, int] = {}  # user_id -> thread_id


def user_busy(user_id: int) -> bool:
    return user_id in active_player


def get_game(channel: discord.abc.Messageable) -> Optional[ChessGame]:
    if isinstance(channel, discord.Thread):
        return games.get(channel.id)
    return None


def square_option_label(board: chess.Board, square: chess.Square) -> str:
    piece = board.piece_at(square)
    name = chess.square_name(square)
    if piece:
        return f"{piece.unicode_symbol()} {name}"
    return name


def build_board_embed(game: ChessGame, caption: str) -> discord.Embed:
    embed = discord.Embed(title=caption, color=0x2B2D31)
    embed.set_image(url="attachment://board.png")
    if game.finished:
        embed.set_footer(text="Ván đấu đã kết thúc")
    else:
        turn_name = "Trắng" if game.board.turn == chess.WHITE else "Đen"
        embed.set_footer(text=f"Lượt đi: {turn_name} — chọn ô xuất phát rồi ô đích")
    return embed


def board_file(game: ChessGame) -> discord.File:
    return discord.File(io.BytesIO(game.render_png()), filename="board.png")


async def cleanup_game(thread: discord.Thread, game: ChessGame):
    game.finished = True
    for uid in (game.white_id, game.black_id):
        if uid:
            active_player.pop(uid, None)
    if game.engine:
        await game.engine.quit()
        game.engine = None
    await thread.edit(archived=True, locked=True)


async def check_game_end(thread: discord.Thread, game: ChessGame) -> bool:
    if game.board.is_game_over(claim_draw=True):
        game.finished = True
        await update_board(thread, game, game.result_text())
        await thread.send(f"**{game.result_text()}**")
        await cleanup_game(thread, game)
        return True
    return False


# ---------------------------------------------------------------------------
# Discord UI: chọn nước đi
# ---------------------------------------------------------------------------


class FromSquareSelect(discord.ui.Select):
    def __init__(self, game: ChessGame):
        options = [
            discord.SelectOption(
                label=square_option_label(game.board, sq),
                value=str(sq),
            )
            for sq in game.legal_from_squares()[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Không còn nước đi", value="-1")]
        super().__init__(
            placeholder="1. Chọn ô xuất phát…",
            options=options,
            custom_id="chess:from",
            disabled=game.finished or not game.legal_from_squares(),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        game = get_game(interaction.channel)
        if not await _ensure_player_turn(interaction, game):
            return

        from_sq = int(self.values[0])
        if from_sq < 0:
            await interaction.response.send_message("Không còn nước đi.", ephemeral=True)
            return

        view = ChessMoveView(game, from_sq=from_sq)
        await interaction.response.edit_message(view=view)


class ToSquareSelect(discord.ui.Select):
    def __init__(self, game: ChessGame, from_sq: chess.Square):
        moves = game.legal_moves_from(from_sq)
        # Gộp theo ô đích; nếu có phong cấp sẽ chọn sau
        seen: set[int] = set()
        options: list[discord.SelectOption] = []
        for move in moves:
            if move.to_square in seen:
                continue
            seen.add(move.to_square)
            piece = game.board.piece_at(from_sq)
            capture = game.board.piece_at(move.to_square)
            label = chess.square_name(move.to_square)
            if capture:
                label = f"x {capture.unicode_symbol()} {label}"
            elif piece and piece.piece_type == chess.KING and abs(
                chess.square_file(from_sq) - chess.square_file(move.to_square)
            ) > 1:
                label = f"Nhập thành → {label}"
            options.append(discord.SelectOption(label=label[:100], value=str(move.to_square)))

        super().__init__(
            placeholder="2. Chọn ô đích…",
            options=options[:25] or [discord.SelectOption(label="—", value="-1")],
            custom_id="chess:to",
            disabled=not options,
            row=1,
        )
        self.from_sq = from_sq

    async def callback(self, interaction: discord.Interaction):
        game = get_game(interaction.channel)
        if not await _ensure_player_turn(interaction, game):
            return

        to_sq = int(self.values[0])
        candidates = [
            m
            for m in game.legal_moves_from(self.from_sq)
            if m.to_square == to_sq
        ]
        if not candidates:
            await interaction.response.send_message("Nước đi không hợp lệ.", ephemeral=True)
            return

        # Phong cấp: nhiều nước cùng đích (Q/R/B/N)
        if len(candidates) > 1 or candidates[0].promotion:
            view = PromotionView(game, self.from_sq, to_sq)
            await interaction.response.edit_message(view=view)
            return

        await interaction.response.defer()
        await apply_move(interaction.channel, game, candidates[0])


class CancelSelectButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Chọn lại",
            style=discord.ButtonStyle.secondary,
            custom_id="chess:cancel",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        game = get_game(interaction.channel)
        if not game or game.finished:
            await interaction.response.send_message("Ván đã kết thúc.", ephemeral=True)
            return
        color = game.player_color(interaction.user.id)
        if color is None:
            await interaction.response.send_message(
                "Bạn không phải người chơi trong ván này.", ephemeral=True
            )
            return
        await interaction.response.edit_message(view=ChessMoveView(game))


class ResignButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Đầu hàng",
            style=discord.ButtonStyle.danger,
            custom_id="chess:resign",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        game = get_game(interaction.channel)
        if not game or game.finished:
            await interaction.response.send_message(
                "Không có ván cờ nào đang diễn ra.", ephemeral=True
            )
            return
        color = game.player_color(interaction.user.id)
        if color is None:
            await interaction.response.send_message(
                "Bạn không phải người chơi trong ván này.", ephemeral=True
            )
            return

        winner = "Đen" if color == chess.WHITE else "Trắng"
        game.finished = True
        await interaction.response.send_message(
            f"{interaction.user.mention} đã đầu hàng. {winner} thắng!"
        )
        await update_board(interaction.channel, game, f"Đầu hàng — {winner} thắng")
        await cleanup_game(interaction.channel, game)


class PromoteButton(discord.ui.Button):
    def __init__(self, from_sq: int, to_sq: int, piece_type: chess.PieceType, label: str):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"chess:promo:{piece_type}",
            row=0,
        )
        self.from_sq = from_sq
        self.to_sq = to_sq
        self.piece_type = piece_type

    async def callback(self, interaction: discord.Interaction):
        game = get_game(interaction.channel)
        if not await _ensure_player_turn(interaction, game):
            return

        move = chess.Move(self.from_sq, self.to_sq, promotion=self.piece_type)
        if move not in game.board.legal_moves:
            await interaction.response.send_message("Nước phong cấp không hợp lệ.", ephemeral=True)
            return

        await interaction.response.defer()
        await apply_move(interaction.channel, game, move)


class PromotionView(discord.ui.View):
    def __init__(self, game: ChessGame, from_sq: chess.Square, to_sq: chess.Square):
        super().__init__(timeout=None)
        self.thread_id = game.thread_id
        for piece_type, label in (
            (chess.QUEEN, "Hậu (Q)"),
            (chess.ROOK, "Xe (R)"),
            (chess.BISHOP, "Tượng (B)"),
            (chess.KNIGHT, "Mã (N)"),
        ):
            move = chess.Move(from_sq, to_sq, promotion=piece_type)
            if move in game.board.legal_moves:
                self.add_item(PromoteButton(from_sq, to_sq, piece_type, label))
        self.add_item(CancelSelectButton())
        self.add_item(ResignButton())


class ChessMoveView(discord.ui.View):
    """Menu đi quân: chọn ô xuất phát → ô đích (→ phong cấp nếu cần)."""

    def __init__(self, game: ChessGame, from_sq: Optional[chess.Square] = None):
        super().__init__(timeout=None)
        self.thread_id = game.thread_id
        self.add_item(FromSquareSelect(game))
        if from_sq is not None and not game.finished:
            self.add_item(ToSquareSelect(game, from_sq))
            self.add_item(CancelSelectButton())
        self.add_item(ResignButton())
        if game.finished:
            for item in self.children:
                item.disabled = True


async def _ensure_player_turn(
    interaction: discord.Interaction, game: Optional[ChessGame]
) -> bool:
    if not game or game.finished:
        await interaction.response.send_message(
            "Không có ván cờ nào đang diễn ra trong phòng này.", ephemeral=True
        )
        return False
    color = game.player_color(interaction.user.id)
    if color is None:
        await interaction.response.send_message(
            "Bạn không phải người chơi trong ván này — chỉ có thể xem.", ephemeral=True
        )
        return False
    if color != game.board.turn:
        await interaction.response.send_message("Chưa đến lượt bạn.", ephemeral=True)
        return False
    return True


async def update_board(
    thread: discord.Thread,
    game: ChessGame,
    caption: str = "Bàn cờ",
):
    """Chỉ giữ 1 ảnh bàn cờ: edit tin nhắn cũ, không gửi thêm ảnh mới."""
    embed = build_board_embed(game, caption)
    file = board_file(game)
    view = ChessMoveView(game)

    if game.board_message_id:
        try:
            msg = await thread.fetch_message(game.board_message_id)
            await msg.edit(embed=embed, attachments=[file], view=view)
            return
        except (discord.NotFound, discord.HTTPException):
            game.board_message_id = None

    msg = await thread.send(embed=embed, file=file, view=view)
    game.board_message_id = msg.id


async def apply_move(thread: discord.Thread, game: ChessGame, move: chess.Move):
    san = game.board.san(move)
    game.board.push(move)
    await update_board(thread, game, f"Đã đi: {san}")
    ended = await check_game_end(thread, game)
    if not ended and game.is_bot_turn():
        await play_bot_move(thread, game)


async def play_bot_move(thread: discord.Thread, game: ChessGame):
    move = await game.get_bot_move()
    san = game.board.san(move)
    game.board.push(move)
    await update_board(thread, game, f"Máy đi: {san}")
    await check_game_end(thread, game)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

chess_group = app_commands.Group(name="chess", description="Chơi cờ vua trên server")


@chess_group.command(name="vs", description="Thách đấu 1v1 với người khác")
@app_commands.describe(doi_thu="Người bạn muốn thách đấu")
async def chess_vs(interaction: discord.Interaction, doi_thu: discord.Member):
    if doi_thu.bot or doi_thu.id == interaction.user.id:
        await interaction.response.send_message(
            "Không thể thách đấu bot hoặc chính mình.", ephemeral=True
        )
        return
    if user_busy(interaction.user.id) or user_busy(doi_thu.id):
        await interaction.response.send_message(
            "Một trong hai người đang có ván đấu chưa kết thúc.", ephemeral=True
        )
        return

    await interaction.response.defer()
    thread = await interaction.channel.create_thread(
        name=f"co-vua-{interaction.user.display_name}-vs-{doi_thu.display_name}"[:100],
        type=discord.ChannelType.public_thread,
    )

    game = ChessGame(thread_id=thread.id, white_id=interaction.user.id, black_id=doi_thu.id)
    games[thread.id] = game
    active_player[interaction.user.id] = thread.id
    active_player[doi_thu.id] = thread.id

    await interaction.followup.send(f"Đã tạo phòng: {thread.mention}")
    await thread.send(
        f"{interaction.user.mention} (Trắng) đấu với {doi_thu.mention} (Đen).\n"
        f"Dùng menu trên ảnh bàn cờ để đi quân. Người khác có thể vào xem nhưng không đi được."
    )
    await update_board(thread, game, "Khai cuộc")


@chess_group.command(name="bot", description="Đấu với máy (Stockfish)")
@app_commands.describe(
    mau="Bạn muốn cầm quân Trắng hay Đen (mặc định Trắng)",
    elo="Độ mạnh của máy, 800-2800 (để trống = mặc định)",
)
@app_commands.choices(
    mau=[
        app_commands.Choice(name="Trắng", value="white"),
        app_commands.Choice(name="Đen", value="black"),
    ]
)
async def chess_bot(
    interaction: discord.Interaction,
    mau: Optional[app_commands.Choice[str]] = None,
    elo: Optional[int] = None,
):
    if user_busy(interaction.user.id):
        await interaction.response.send_message(
            "Bạn đang có ván đấu chưa kết thúc.", ephemeral=True
        )
        return

    await interaction.response.defer()
    player_white = (mau.value if mau else "white") == "white"

    thread = await interaction.channel.create_thread(
        name=f"co-vua-{interaction.user.display_name}-vs-stockfish"[:100],
        type=discord.ChannelType.public_thread,
    )

    try:
        _transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
    except (FileNotFoundError, OSError):
        await interaction.followup.send(
            "Không tìm thấy Stockfish trên máy chủ. Kiểm tra lại biến môi trường "
            "`STOCKFISH_PATH` hoặc cài Stockfish rồi thử lại."
        )
        await thread.delete()
        return

    if elo:
        elo = max(800, min(2800, elo))
        await engine.configure({"UCI_LimitStrength": True, "UCI_Elo": elo})

    game = ChessGame(
        thread_id=thread.id,
        white_id=interaction.user.id if player_white else None,
        black_id=None if player_white else interaction.user.id,
        vs_bot=True,
        engine=engine,
    )
    games[thread.id] = game
    active_player[interaction.user.id] = thread.id

    await interaction.followup.send(f"Đã tạo phòng: {thread.mention}")
    mau_text = "Trắng" if player_white else "Đen"
    await thread.send(
        f"{interaction.user.mention} đấu với Stockfish (bạn cầm {mau_text}).\n"
        f"Dùng menu trên ảnh bàn cờ để đi quân. Người khác có thể vào xem nhưng không đi được."
    )
    await update_board(thread, game, "Khai cuộc")

    if game.is_bot_turn():
        await play_bot_move(thread, game)


@bot.tree.command(name="resign", description="Đầu hàng ván cờ hiện tại")
async def resign_cmd(interaction: discord.Interaction):
    game = get_game(interaction.channel)
    if not game or game.finished:
        await interaction.response.send_message(
            "Không có ván cờ nào đang diễn ra.", ephemeral=True
        )
        return
    color = game.player_color(interaction.user.id)
    if color is None:
        await interaction.response.send_message(
            "Bạn không phải người chơi trong ván này.", ephemeral=True
        )
        return

    winner = "Đen" if color == chess.WHITE else "Trắng"
    game.finished = True
    await interaction.response.send_message(
        f"{interaction.user.mention} đã đầu hàng. {winner} thắng!"
    )
    await update_board(interaction.channel, game, f"Đầu hàng — {winner} thắng")
    await cleanup_game(interaction.channel, game)


bot.tree.add_command(chess_group)


@bot.event
async def on_ready():
    # Gỡ /move và /board cũ khỏi Discord nếu từng sync trước đó
    bot.tree.remove_command("move")
    bot.tree.remove_command("board")
    await bot.tree.sync()
    log.info(f"Đã đăng nhập: {bot.user}")


if __name__ == "__main__":
    bot.run(TOKEN)
