"""
bot.py - Bot Discord chơi cờ vua.

Lệnh:
  /chess vs doi_thu           - tạo phòng (thread) đấu 1v1 với người khác
  /chess bot mau elo          - tạo phòng đấu với Stockfish
  /move nuoc_di                - đi quân (vd: e4, Nf3, hoặc e2e4)
  /board                       - gửi lại ảnh bàn cờ hiện tại
  /resign                      - đầu hàng ván hiện tại

Mỗi ván chạy trong 1 public thread riêng: ai cũng xem được nội dung thread,
nhưng chỉ 2 người chơi đã đăng ký cho ván đó mới dùng được /move - người
khác gõ /move sẽ bị từ chối (ephemeral).
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

games: dict[int, ChessGame] = {}        # thread_id -> ChessGame
active_player: dict[int, int] = {}      # user_id -> thread_id (chặn chơi nhiều ván cùng lúc)


def user_busy(user_id: int) -> bool:
    return user_id in active_player


def get_game(channel: discord.abc.Messageable) -> Optional[ChessGame]:
    if isinstance(channel, discord.Thread):
        return games.get(channel.id)
    return None


async def send_board(thread: discord.Thread, game: ChessGame, caption: str = "Bàn cờ"):
    png = game.render_png()
    file = discord.File(io.BytesIO(png), filename="board.png")
    embed = discord.Embed(title=caption, color=0x2B2D31)
    embed.set_image(url="attachment://board.png")
    turn_name = "Trắng" if game.board.turn == chess.WHITE else "Đen"
    embed.set_footer(text=f"Lượt đi: {turn_name}")
    await thread.send(embed=embed, file=file)


async def cleanup_game(thread: discord.Thread, game: ChessGame):
    game.finished = True
    for uid in (game.white_id, game.black_id):
        if uid:
            active_player.pop(uid, None)
    if game.engine:
        await game.engine.quit()
    await thread.edit(archived=True, locked=True)


async def check_game_end(thread: discord.Thread, game: ChessGame) -> bool:
    # claim_draw=True: tự kết thúc ván khi đạt luật 50 nước / lặp thế cờ 3 lần,
    # vì không có cơ chế cho người chơi "claim draw" riêng trong các lệnh hiện tại.
    if game.board.is_game_over(claim_draw=True):
        await thread.send(f"**{game.result_text()}**")
        await cleanup_game(thread, game)
        return True
    return False


async def play_bot_move(thread: discord.Thread, game: ChessGame):
    move = await game.get_bot_move()
    game.board.push(move)
    await send_board(thread, game, f"Máy đi: {move.uci()}")
    await check_game_end(thread, game)


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
        f"Dùng `/move` để đi quân. Người khác có thể vào xem nhưng không đi được."
    )
    await send_board(thread, game, "Khai cuộc")


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
        f"Dùng `/move` để đi quân. Người khác có thể vào xem nhưng không đi được."
    )
    await send_board(thread, game, "Khai cuộc")

    if game.is_bot_turn():
        await play_bot_move(thread, game)


@bot.tree.command(name="move", description="Đi một nước cờ trong phòng hiện tại")
@app_commands.describe(nuoc_di="Nước đi, vd: e4, Nf3, hoặc e2e4")
async def move_cmd(interaction: discord.Interaction, nuoc_di: str):
    game = get_game(interaction.channel)
    if not game or game.finished:
        await interaction.response.send_message(
            "Không có ván cờ nào đang diễn ra trong phòng này.", ephemeral=True
        )
        return

    color = game.player_color(interaction.user.id)
    if color is None:
        await interaction.response.send_message(
            "Bạn không phải người chơi trong ván này — chỉ có thể xem.", ephemeral=True
        )
        return
    if color != game.board.turn:
        await interaction.response.send_message("Chưa đến lượt bạn.", ephemeral=True)
        return

    parsed = game.parse_move(nuoc_di)
    if parsed is None:
        await interaction.response.send_message(
            f"Nước đi `{nuoc_di}` không hợp lệ.", ephemeral=True
        )
        return

    thread: discord.Thread = interaction.channel
    game.board.push(parsed)
    await interaction.response.send_message(f"Đã đi: `{nuoc_di}`")
    await send_board(thread, game)
    ended = await check_game_end(thread, game)

    if not ended and game.is_bot_turn():
        await play_bot_move(thread, game)


@bot.tree.command(name="board", description="Gửi lại bàn cờ hiện tại")
async def board_cmd(interaction: discord.Interaction):
    game = get_game(interaction.channel)
    if not game:
        await interaction.response.send_message("Không có ván cờ nào ở đây.", ephemeral=True)
        return
    await interaction.response.send_message("Bàn cờ hiện tại:")
    await send_board(interaction.channel, game)


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
    await interaction.response.send_message(
        f"{interaction.user.mention} đã đầu hàng. {winner} thắng!"
    )
    await cleanup_game(interaction.channel, game)


bot.tree.add_command(chess_group)


@bot.event
async def on_ready():
    await bot.tree.sync()
    log.info(f"Đã đăng nhập: {bot.user}")


if __name__ == "__main__":
    bot.run(TOKEN)
  
