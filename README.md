# Chess Bot Discord

Bot cờ vua cho Discord: tạo phòng đấu 1v1 hoặc đấu với máy (Stockfish).
Mỗi ván chạy trong một **thread riêng**, tối đa 2 người chơi — người khác vào
thread chỉ xem được, không đi quân được.

## Cài đặt

1. Cài thư viện Python (khuyến nghị Python 3.10+):
   ```bash
   pip install -r requirements.txt
   ```

2. Cài Stockfish (engine cờ vua) trên máy chủ:
   - Ubuntu/Debian: `sudo apt install stockfish` (thường nằm ở `/usr/games/stockfish`)
   - Hoặc tải binary tại https://stockfishchess.org/download/ rồi dùng đường dẫn đó.

3. `cairosvg` (dùng để vẽ ảnh bàn cờ) cần thư viện Cairo của hệ thống:
   - Ubuntu/Debian: `sudo apt install libcairo2`

4. Tạo bot tại https://discord.com/developers/applications → New Application →
   Bot → copy **Token**.

5. Copy `.env.example` thành `.env` và điền:
   ```
   DISCORD_TOKEN=token_bot_của_bạn
   STOCKFISH_PATH=/usr/games/stockfish
   ```

6. Mời bot vào server với các quyền: Send Messages, Create Public Threads,
   Send Messages in Threads, Manage Threads, Attach Files, Use Slash Commands.

7. Chạy bot:
   ```bash
   python bot.py
   ```
   Slash command cần vài phút để đồng bộ lần đầu (hoặc ngay lập tức nếu test
   trong 1 server cụ thể).

## Các lệnh

| Lệnh | Mô tả |
|---|---|
| `/chess vs doi_thu:@ai_do` | Tạo phòng (thread) đấu 1v1 với người khác |
| `/chess bot mau:Trắng/Đen elo:800-2800` | Tạo phòng đấu với Stockfish |
| `/move nuoc_di:e4` | Đi quân — hỗ trợ cả SAN (`e4`, `Nf3`) và UCI (`e2e4`) |
| `/board` | Gửi lại ảnh bàn cờ hiện tại |
| `/resign` | Đầu hàng ván đang đấu |

Mỗi ván tạo một **public thread** riêng dưới kênh gọi lệnh — ai trong kênh đó
cũng xem được thread, nhưng `/move` chỉ hoạt động với 2 người chơi đã đăng ký
cho ván đó; người khác gõ `/move` sẽ nhận phản hồi riêng (ephemeral) báo không
có quyền đi.

## Giới hạn hiện tại / có thể mở rộng sau

- Chưa có cầu hòa (draw offer) — chỉ có đầu hàng (`/resign`).
- Một người chỉ chơi được 1 ván tại 1 thời điểm (chặn mở ván thứ 2 khi ván cũ
  chưa kết thúc).
- Mỗi ván đấu máy mở 1 tiến trình Stockfish riêng, tự tắt khi ván kết thúc.
- Thời gian suy nghĩ của máy cố định 1 giây/nước (`think_time` trong
  `game.py` → `get_bot_move`), có thể chỉnh để máy mạnh/nhanh hơn.
