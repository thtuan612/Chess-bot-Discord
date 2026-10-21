"""
bot.py - Bot Discord chơi cờ vua 1v1.

Lệnh:
  /chess vs doi_thu   - tạo phòng (thread) đấu 1v1
  /chess hub          - gửi panel kênh chính (thách đấu / BXH / hướng dẫn)
  /leaderboard        - bảng xếp hạng (phân trang Discord UI)
  /resign             - đầu hàng ván hiện tại

Đi quân bằng Discord UI. Cùng một tin nhắn bàn cờ được edit sau mỗi nước;
chỉ đẩy xuống cuối khi chat làm nó trôi. Phòng tạo xong chỉ báo riêng cho bạn.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Optional

import chess
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from game import ChessGame
from stats import StatsStore

load_dotenv()
TOKEN = os.environ["DISCORD_TOKEN"]
HISTORY_CHANNEL_ID = int(os.environ["HISTORY_CHANNEL_ID"]) if os.environ.get("HISTORY_CHANNEL_ID") else None
PAGE_SIZE = 10
BOARD_DEBOUNCE_SEC = 1.2

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("chess-bot")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

games: dict[int, ChessGame] = {}
active_player: dict[int, int] = {}
stats = StatsStore()

_board_locks: dict[int, asyncio.Lock] = {}
_board_refresh_tasks: dict[int, asyncio.Task] = {}


def _lock_for(thread_id: int) -> asyncio.Lock:
    lock = _board_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _board_locks[thread_id] = lock
    return lock


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
    embed = discord.Embed(
        title=caption,
        description=(
            f"⚪ **{game.white_name}** vs ⚫ **{game.black_name}**\n"
            f"Nước đã đi: `{len(game.board.move_stack)}`"
        ),
        color=0x5865F2 if not game.finished else 0x2B2D31,
    )
    embed.set_image(url="attachment://board.png")
    if game.finished:
        embed.set_footer(text="Ván đấu đã kết thúc — chỉ xem")
    else:
        turn_name = "Trắng" if game.board.turn == chess.WHITE else "Đen"
        turn_player = game.white_name if game.board.turn == chess.WHITE else game.black_name
        embed.set_footer(text=f"Lượt: {turn_name} ({turn_player}) — chọn ô xuất phát rồi ô đích")
    return embed


def board_file(game: ChessGame) -> discord.File:
    return discord.File(io.BytesIO(game.render_png()), filename="board.png")


def hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="♟️ Cờ vua",
        description=(
            "Thách đấu 1v1 trong thread riêng. Người khác vào xem được, "
            "chỉ hai người chơi mới đi quân.\n\n"
            "**Cách chơi**\n"
            "• Bấm **Thách đấu** hoặc dùng `/chess vs`\n"
            "• Trên ảnh bàn cờ: chọn ô xuất phát → ô đích\n"
            "• Bàn cờ cập nhật tại chỗ; chat sẽ đẩy nó xuống cuối phòng\n"
            "• Kết thúc → phòng khoá, bản lưu trong lịch sử (chỉ 2 người chơi + quản trị)\n\n"
            "Xem bảng xếp hạng công khai bằng nút bên dưới hoặc `/leaderboard`."
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="Một người chỉ chơi 1 ván tại một thời điểm")
    return embed


# ---------------------------------------------------------------------------
# Kết thúc ván → khoá + lịch sử
# ---------------------------------------------------------------------------


async def archive_to_history(
    public_thread: discord.Thread,
    game: ChessGame,
    result_line: str,
) -> None:
    """Khoá thread công khai; tạo thread riêng trong kênh lịch sử (chỉ 2 người chơi + admin xem)."""
    note = (
        f"**{result_line}**\n"
        f"Phòng đã khoá. Bản xem lại (chỉ 2 người chơi + quản trị) "
    )

    history_thread: Optional[discord.Thread] = None
    if HISTORY_CHANNEL_ID:
        history_ch = public_thread.guild.get_channel(HISTORY_CHANNEL_ID)
        if history_ch is None:
            try:
                history_ch = await public_thread.guild.fetch_channel(HISTORY_CHANNEL_ID)
            except discord.HTTPException:
                history_ch = None

        if isinstance(history_ch, discord.TextChannel):
            try:
                history_thread = await history_ch.create_thread(
                    name=f"Lịch sử · {game.white_name} vs {game.black_name}"[:100],
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                )
                for uid in (game.white_id, game.black_id):
                    try:
                        await history_thread.add_user(discord.Object(id=uid))
                    except discord.HTTPException:
                        pass

                final_embed = discord.Embed(
                    title="📜 Lịch sử ván đấu",
                    description=(
                        f"{result_line}\n\n"
                        f"⚪ <@{game.white_id}> vs ⚫ <@{game.black_id}>\n"
                        f"**Nước đi:**\n```{game.pgn_moves()[:1800]}```"
                    ),
                    color=0x57F287,
                )
                final_embed.set_image(url="attachment://board.png")
                final_embed.set_footer(text="Chỉ xem — không chỉnh sửa")
                await history_thread.send(
                    content="Bản lưu ván đấu (chỉ người chơi và quản trị viên).",
                    embed=final_embed,
                    file=board_file(game),
                )
                await history_thread.edit(archived=True, locked=True)
            except discord.HTTPException as exc:
                log.warning("Không tạo được thread lịch sử: %s", exc)
                history_thread = None

    if history_thread:
        note += f"→ {history_thread.mention}"
    else:
        note += "→ chưa cấu hình `HISTORY_CHANNEL_ID` (hoặc bot thiếu quyền Create Private Threads)."

    try:
        await public_thread.send(note)
    except discord.HTTPException:
        pass

    # Chỉ khoá/lưu trữ — không đổi tên (tránh spam thông báo)
    try:
        await public_thread.edit(archived=True, locked=True)
    except discord.HTTPException:
        pass


async def cleanup_game(
    thread: discord.Thread,
    game: ChessGame,
    result_line: str,
    *,
    result_code: Optional[str] = None,
    resign_winner: Optional[int] = None,
):
    game.finished = True
    for uid in (game.white_id, game.black_id):
        active_player.pop(uid, None)

    task = _board_refresh_tasks.pop(thread.id, None)
    if task and not task.done():
        task.cancel()

    if resign_winner is not None:
        loser = game.black_id if resign_winner == game.white_id else game.white_id
        w_name = game.white_name if resign_winner == game.white_id else game.black_name
        l_name = game.black_name if resign_winner == game.white_id else game.white_name
        await stats.record_resign(resign_winner, loser, w_name, l_name)
    elif result_code:
        await stats.record_result(
            game.white_id,
            game.black_id,
            result_code,
            game.white_name,
            game.black_name,
        )

    # Edit tại chỗ (giữ cùng tin nhắn/ảnh), gỡ nút đi quân
    await update_board(thread, game, result_line, force_bottom=False, disable_view=True)
    await archive_to_history(thread, game, result_line)

    games.pop(thread.id, None)
    _board_locks.pop(thread.id, None)


async def check_game_end(thread: discord.Thread, game: ChessGame) -> bool:
    if game.board.is_game_over(claim_draw=True):
        game.finished = True
        text = game.result_text()
        await cleanup_game(thread, game, text, result_code=game.outcome())
        return True
    return False


# ---------------------------------------------------------------------------
# Discord UI: chọn nước đi
# ---------------------------------------------------------------------------


class FromSquareSelect(discord.ui.Select):
    def __init__(self, game: ChessGame):
        options = [
            discord.SelectOption(label=square_option_label(game.board, sq), value=str(sq))
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
        await interaction.response.edit_message(view=ChessMoveView(game, from_sq=from_sq))


class ToSquareSelect(discord.ui.Select):
    def __init__(self, game: ChessGame, from_sq: chess.Square):
        moves = game.legal_moves_from(from_sq)
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
        candidates = [m for m in game.legal_moves_from(self.from_sq) if m.to_square == to_sq]
        if not candidates:
            await interaction.response.send_message("Nước đi không hợp lệ.", ephemeral=True)
            return
        if len(candidates) > 1 or candidates[0].promotion:
            await interaction.response.edit_message(view=PromotionView(game, self.from_sq, to_sq))
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
        if game.player_color(interaction.user.id) is None:
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

        winner_id = game.black_id if color == chess.WHITE else game.white_id
        winner = "Đen" if color == chess.WHITE else "Trắng"
        line = f"Đầu hàng — {winner} thắng"
        await interaction.response.send_message(
            f"{interaction.user.mention} đã đầu hàng. {winner} thắng!"
        )
        await cleanup_game(
            interaction.channel, game, line, resign_winner=winner_id
        )


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
    def __init__(
        self,
        game: ChessGame,
        from_sq: Optional[chess.Square] = None,
        *,
        disabled: bool = False,
    ):
        super().__init__(timeout=None)
        if disabled or game.finished:
            return
        self.add_item(FromSquareSelect(game))
        if from_sq is not None:
            self.add_item(ToSquareSelect(game, from_sq))
            self.add_item(CancelSelectButton())
        self.add_item(ResignButton())


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


# ---------------------------------------------------------------------------
# Bàn cờ: edit tại chỗ; chỉ đẩy xuống cuối khi bị chat làm trôi
# ---------------------------------------------------------------------------


async def _is_last_message(thread: discord.Thread, message_id: int) -> bool:
    try:
        async for msg in thread.history(limit=1):
            return msg.id == message_id
    except discord.HTTPException:
        pass
    return False


async def _delete_quiet(thread: discord.Thread, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        msg = await thread.fetch_message(message_id)
        await msg.delete()
    except (discord.NotFound, discord.HTTPException):
        pass


async def update_board(
    thread: discord.Thread,
    game: ChessGame,
    caption: str = "Bàn cờ",
    *,
    force_bottom: bool = False,
    disable_view: bool = False,
):
    """Ưu tiên edit cùng một tin nhắn ảnh. Chỉ gửi lại ở cuối khi thật sự bị trôi."""
    async with _lock_for(thread.id):
        embed = build_board_embed(game, caption)
        file = board_file(game)
        view = None if (disable_view or game.finished) else ChessMoveView(game)

        relocate = False
        if force_bottom and game.board_message_id:
            if not await _is_last_message(thread, game.board_message_id):
                relocate = True
                await _delete_quiet(thread, game.board_message_id)
                game.board_message_id = None

        if game.board_message_id and not relocate:
            try:
                msg = await thread.fetch_message(game.board_message_id)
                await msg.edit(embed=embed, attachments=[file], view=view)
                return
            except (discord.NotFound, discord.HTTPException):
                game.board_message_id = None

        msg = await thread.send(embed=embed, file=file, view=view)
        game.board_message_id = msg.id


def schedule_board_bottom(thread: discord.Thread, game: ChessGame) -> None:
    """Debounce: chat dày đặc chỉ đẩy bàn cờ 1 lần nếu nó không còn ở cuối."""
    old = _board_refresh_tasks.get(thread.id)
    if old and not old.done():
        old.cancel()

    async def _run():
        try:
            await asyncio.sleep(BOARD_DEBOUNCE_SEC)
            if game.finished or games.get(thread.id) is not game:
                return
            if game.board_message_id and await _is_last_message(thread, game.board_message_id):
                return
            turn = "Trắng" if game.board.turn == chess.WHITE else "Đen"
            await update_board(thread, game, f"Lượt {turn}", force_bottom=True)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Lỗi đẩy bàn cờ xuống cuối")

    _board_refresh_tasks[thread.id] = asyncio.create_task(_run())


async def apply_move(thread: discord.Thread, game: ChessGame, move: chess.Move):
    san = game.board.san(move)
    game.board.push(move)
    # Edit cùng tin nhắn → ảnh bàn cờ liền mạch, không spam tin mới
    await update_board(thread, game, f"Đã đi: {san}", force_bottom=False)
    await check_game_end(thread, game)


# ---------------------------------------------------------------------------
# Bảng xếp hạng (phân trang — danh sách dài)
# ---------------------------------------------------------------------------


def leaderboard_embed(page: int) -> tuple[discord.Embed, int]:
    ranked = stats.ranked()
    total = len(ranked)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    chunk = ranked[start : start + PAGE_SIZE]

    lines: list[str] = []
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    for i, p in enumerate(chunk):
        rank = start + i
        medal = medals.get(rank, f"`#{rank + 1}`")
        name = p.display_name or f"<@{p.user_id}>"
        lines.append(
            f"{medal} **{name}** — {p.points:.1f} điểm · "
            f"{p.wins}W / {p.draws}D / {p.losses}L · {p.games} ván"
        )

    embed = discord.Embed(
        title="🏆 Bảng xếp hạng cờ vua",
        description="\n".join(lines) if lines else "_Chưa có dữ liệu. Hãy chơi vài ván!_",
        color=0xFEE75C,
    )
    embed.set_footer(text=f"Trang {page + 1}/{pages} · {total} người chơi · 1 thắng = 1đ, hòa = 0.5đ")
    return embed, pages


class LeaderboardView(discord.ui.View):
    def __init__(self, page: int = 0):
        super().__init__(timeout=300)
        self.page = page
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        ranked = stats.ranked()
        pages = max(1, (len(ranked) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "lb:first":
                    child.disabled = self.page <= 0
                elif child.custom_id == "lb:prev":
                    child.disabled = self.page <= 0
                elif child.custom_id == "lb:next":
                    child.disabled = self.page >= pages - 1
                elif child.custom_id == "lb:last":
                    child.disabled = self.page >= pages - 1

    async def _goto(self, interaction: discord.Interaction, page: int) -> None:
        self.page = page
        embed, _ = leaderboard_embed(self.page)
        self._sync_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary, custom_id="lb:first", row=0)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto(interaction, 0)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary, custom_id="lb:prev", row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto(interaction, self.page - 1)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary, custom_id="lb:next", row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto(interaction, self.page + 1)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary, custom_id="lb:last", row=0)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        ranked = stats.ranked()
        pages = max(1, (len(ranked) + PAGE_SIZE - 1) // PAGE_SIZE)
        await self._goto(interaction, pages - 1)

    @discord.ui.button(label="Làm mới", style=discord.ButtonStyle.success, custom_id="lb:refresh", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto(interaction, self.page)


# ---------------------------------------------------------------------------
# Hub kênh chính
# ---------------------------------------------------------------------------


class HubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Thách đấu",
        style=discord.ButtonStyle.primary,
        custom_id="hub:challenge",
        emoji="⚔️",
        row=0,
    )
    async def challenge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Dùng lệnh `/chess vs @đối_thủ` để tạo phòng đấu.\n"
            "(Nút này gợi ý — chọn đối thủ bằng slash command là nhanh nhất.)",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Bảng xếp hạng",
        style=discord.ButtonStyle.secondary,
        custom_id="hub:lb",
        emoji="🏆",
        row=0,
    )
    async def show_lb(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed, _ = leaderboard_embed(0)
        await interaction.response.send_message(embed=embed, view=LeaderboardView(0))

    @discord.ui.button(
        label="Hướng dẫn",
        style=discord.ButtonStyle.secondary,
        custom_id="hub:help",
        emoji="📖",
        row=0,
    )
    async def help_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "**Cách chơi**\n"
            "1. `/chess vs @ai_đó` → tạo thread (chỉ bạn thấy xác nhận)\n"
            "2. Trên ảnh bàn cờ: chọn ô xuất phát → ô đích (phong cấp nếu cần)\n"
            "3. Mỗi nước edit cùng một ảnh; chat làm trôi thì bot đẩy bàn cờ xuống cuối\n"
            "4. Kết thúc / đầu hàng → phòng khoá, bản lưu vào lịch sử riêng\n\n"
            "`/leaderboard` — xếp hạng công khai (phân trang)\n"
            "`/chess hub` — gửi lại panel kênh chính (quản trị)",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Tạo trận
# ---------------------------------------------------------------------------


async def start_vs_match(interaction: discord.Interaction, doi_thu: discord.Member):
    if doi_thu.bot or doi_thu.id == interaction.user.id:
        send = (
            interaction.followup.send
            if interaction.response.is_done()
            else interaction.response.send_message
        )
        await send("Không thể thách đấu bot hoặc chính mình.", ephemeral=True)
        return
    if user_busy(interaction.user.id) or user_busy(doi_thu.id):
        send = (
            interaction.followup.send
            if interaction.response.is_done()
            else interaction.response.send_message
        )
        await send("Một trong hai người đang có ván đấu chưa kết thúc.", ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    parent = interaction.channel
    if isinstance(parent, discord.Thread):
        parent = parent.parent
    if not isinstance(parent, (discord.TextChannel, discord.ForumChannel)):
        await interaction.followup.send(
            "Chỉ tạo phòng đấu trong kênh văn bản.", ephemeral=True
        )
        return

    thread = await parent.create_thread(
        name=f"♟️ {interaction.user.display_name} vs {doi_thu.display_name}"[:100],
        type=discord.ChannelType.public_thread,
        auto_archive_duration=1440,
    )

    game = ChessGame(
        thread_id=thread.id,
        white_id=interaction.user.id,
        black_id=doi_thu.id,
        white_name=interaction.user.display_name,
        black_name=doi_thu.display_name,
    )
    games[thread.id] = game
    active_player[interaction.user.id] = thread.id
    active_player[doi_thu.id] = thread.id

    # Chỉ người gọi lệnh thấy xác nhận — tránh spam kênh chính
    await interaction.followup.send(f"Đã tạo phòng: {thread.mention}", ephemeral=True)
    await thread.send(
        f"{interaction.user.mention} (Trắng) đấu với {doi_thu.mention} (Đen).\n"
        f"Dùng menu trên ảnh bàn cờ để đi quân."
    )
    await update_board(thread, game, "Khai cuộc", force_bottom=False)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

chess_group = app_commands.Group(name="chess", description="Chơi cờ vua trên server")


@chess_group.command(name="vs", description="Thách đấu 1v1 với người khác")
@app_commands.describe(doi_thu="Người bạn muốn thách đấu")
async def chess_vs(interaction: discord.Interaction, doi_thu: discord.Member):
    await start_vs_match(interaction, doi_thu)


@chess_group.command(name="hub", description="Gửi panel kênh chính (thách đấu / BXH / hướng dẫn)")
@app_commands.default_permissions(manage_guild=True)
async def chess_hub(interaction: discord.Interaction):
    await interaction.response.send_message(embed=hub_embed(), view=HubView())


@bot.tree.command(name="leaderboard", description="Bảng xếp hạng cờ vua (phân trang)")
async def leaderboard_cmd(interaction: discord.Interaction):
    embed, _ = leaderboard_embed(0)
    await interaction.response.send_message(embed=embed, view=LeaderboardView(0))


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

    winner_id = game.black_id if color == chess.WHITE else game.white_id
    winner = "Đen" if color == chess.WHITE else "Trắng"
    line = f"Đầu hàng — {winner} thắng"
    await interaction.response.send_message(
        f"{interaction.user.mention} đã đầu hàng. {winner} thắng!"
    )
    await cleanup_game(interaction.channel, game, line, resign_winner=winner_id)


bot.tree.add_command(chess_group)


@bot.event
async def on_ready():
    bot.add_view(HubView())
    # Gỡ lệnh cũ
    for name in ("move", "board"):
        bot.tree.remove_command(name)
    # Gỡ subgroup /chess bot nếu còn
    chess_cmd = bot.tree.get_command("chess")
    if isinstance(chess_cmd, app_commands.Group):
        chess_cmd.remove_command("bot")
    await bot.tree.sync()
    log.info("Đã đăng nhập: %s | lịch sử=%s", bot.user, HISTORY_CHANNEL_ID or "chưa cấu hình")


@bot.event
async def on_message(message: discord.Message):
    # Chỉ đẩy bàn cờ xuống cuối khi có chat làm nó trôi (không ghim / không đổi tên)
    if message.guild is None:
        return
    if not isinstance(message.channel, discord.Thread):
        return
    game = games.get(message.channel.id)
    if not game or game.finished:
        return
    if message.id == game.board_message_id:
        return
    if message.type != discord.MessageType.default and message.type != discord.MessageType.reply:
        return
    # Tin của bot (bàn cờ / thông báo) — không tự đẩy lại
    if message.author.bot:
        return

    schedule_board_bottom(message.channel, game)


if __name__ == "__main__":
    bot.run(TOKEN)
