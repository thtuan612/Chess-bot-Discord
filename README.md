# Chess Bot Discord

Bot cờ vua 1v1 cho Discord. Mỗi ván chạy trong **thread riêng**; người khác vào xem được, không đi quân được.

Đi quân bằng **Discord UI** (menu trên ảnh bàn cờ). Cùng một tin nhắn ảnh được **edit** sau mỗi nước; chỉ đẩy xuống cuối khi chat làm trôi. Sau khi kết thúc, phòng công khai bị khoá và bản xem lại nằm trong **lịch sử riêng** (chỉ 2 người chơi + quản trị).

## Cài đặt

1. Cài thư viện Python (khuyến nghị Python 3.10+):
   ```bash
   pip install -r requirements.txt
   ```

2. `cairosvg` cần Cairo trên hệ thống:
   - Ubuntu/Debian: `sudo apt install libcairo2`

3. Tạo bot tại https://discord.com/developers/applications → Bot → copy **Token**.

4. Copy `.env.example` thành `.env`:
   ```
   DISCORD_TOKEN=token_bot_cua_ban
   HISTORY_CHANNEL_ID=id_kenh_lich_su
   ```
   Tạo một kênh văn bản (ví dụ `#cờ-lịch-sử`), bật quyền bot **Create Private Threads**, copy ID kênh vào `HISTORY_CHANNEL_ID`.

5. Mời bot với quyền: Send Messages, Create Public/Private Threads, Send Messages in Threads, Manage Threads, Manage Messages (xoá tin bàn cờ khi bị chat đẩy), Attach Files, Use Slash Commands.

6. Chạy:
   ```bash
   python bot.py
   ```

7. Trong kênh chính, quản trị chạy `/chess hub` một lần để gửi panel.

## Lệnh

| Lệnh | Mô tả |
|---|---|
| `/chess vs doi_thu:@ai_do` | Tạo phòng (thread) đấu 1v1 |
| `/chess hub` | Gửi panel kênh chính (quản trị) |
| `/leaderboard` | Bảng xếp hạng — phân trang Discord UI |
| `/resign` | Đầu hàng (cũng có nút trên bàn cờ) |

### Đi quân

1. Chọn **ô xuất phát** → **ô đích**
2. Phong cấp tốt nếu cần (Hậu / Xe / Tượng / Mã)
3. **Chọn lại** / **Đầu hàng**

## Tính năng chính

- **Bảng xếp hạng công khai**: `/leaderboard` hoặc nút trên hub — ⏮ ◀ ▶ ⏭ (10 người/trang), điểm = thắng 1 + hòa 0.5.
- **Bàn cờ liền mạch**: mỗi nước **edit** cùng một tin nhắn ảnh; chỉ xoá + gửi lại ở cuối khi chat làm trôi (debounce, không ghim / không đổi tên phòng).
- **Tạo phòng riêng tư**: `/chess vs` chỉ báo xác nhận ephemeral cho người gọi.
- **Lịch sử**: khi ván xong, thread công khai bị khoá (không đổi tên); bot tạo **private thread** trong kênh lịch sử, mời đúng 2 người chơi, gửi bàn cuối + chuỗi nước đi, rồi khoá (chỉ xem).
- **Hiệu suất**: cache PNG theo FEN, khoá theo thread, debounce đẩy bàn cờ.

## Đã gỡ

- Chế độ `/chess bot` (đấu Stockfish) — đã bỏ do lỗi ổn định.
