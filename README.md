# AI Recap Video — nền tảng backend (Milestone 0)

Phần này là nền móng của backend: nhập một tập phim có sẵn trên máy, đọc thông tin
media bằng FFprobe, và lưu một "điểm hoàn thành" (checkpoint) có thể chạy lại được.
Chưa có giao diện; đây là lớp lõi để các phần sau dựng lên.

## Yêu cầu

- Python 3.11 trở lên (đang kiểm thử trên 3.12).
- `ffprobe` (thuộc bộ FFmpeg) có trong PATH — chỉ cần cho kiểm thử chạy thật với FFprobe.
- Không cần thư viện ngoài; chỉ cần `pytest` để chạy kiểm thử.

## Chạy kiểm thử

Mở PowerShell tại thư mục dự án và chạy một lệnh duy nhất:

```powershell
python -m pytest
```

Cấu hình trong `pyproject.toml` đã tự thêm `backend` vào đường dẫn Python, nên không
cần cài đặt gì thêm ngoài `pytest`:

```powershell
python -m pip install pytest
```

## Nội dung đã có

- `backend/recap_core/domain/` — mô hình nghiệp vụ thuần: định danh, hash SHA-256,
  metadata media đã chuẩn hóa, `Project`/`Episode`, máy trạng thái `Job`.
  Lớp này không phụ thuộc FastAPI, SQLite, subprocess hay FFmpeg.
- `backend/recap_core/ports/` — các cổng trừu tượng (`MediaProber`, `CommandRunner`,
  repository) để hạ tầng có thể thay thế được.
- `backend/recap_core/infrastructure/` — SQLite (WAL + khóa ngoại), migration đánh số,
  adapter FFprobe, bố cục thư mục dự án với ghi tệp nguyên tử (`.partial` rồi mới đổi tên).
- `backend/recap_core/application/import_episode.py` — luồng nhập tập phim.
- `migrations/0001_foundation.sql` — lược đồ cơ sở dữ liệu đầu tiên.

## Luồng nhập tập phim

1. Kiểm tra tệp nguồn tồn tại; nếu không có thì báo lỗi rõ ràng.
2. Tính SHA-256 của tệp nguồn (chỉ đọc, không bao giờ ghi đè tệp gốc).
3. Sinh khóa idempotent theo `(giai đoạn, phạm vi, hash nguồn, phiên bản probe)`.
   Nhập lại cùng một tệp sẽ không tạo bản ghi trùng.
4. Tạo đầy đủ thư mục dự án (`source/`, `metadata/`, `renders/`, …).
5. Gọi FFprobe và chuẩn hóa kết quả; dữ liệu sai, thiếu, hoặc thời lượng không hợp lệ
   đều báo lỗi có kiểu, không tạo trạng thái "hoàn thành" giả.
6. Ghi `metadata/<hash>.media.json` theo kiểu nguyên tử.
7. Ghi `Episode`, artifact và job `SUCCEEDED` trong **một giao dịch duy nhất**. Nếu tiến
   trình chết trước bước này, lần chạy sau sẽ nhận ra job dang dở, đánh dấu `INTERRUPTED`
   và chạy tiếp cho tới khi hoàn tất.

## Dữ liệu lưu ở đâu

- Cơ sở dữ liệu SQLite: do người gọi chỉ định (ví dụ `<thư mục dự án>/db/recap.sqlite3`).
- Tệp sinh ra: nằm trong thư mục dự án; mọi đường dẫn ra ngoài thư mục gốc đều bị từ chối.
- Tệp phim gốc: giữ nguyên tại chỗ, không bao giờ bị sửa.

## Lỗi thường gặp

- `ProbeUnavailableError` — không tìm thấy hoặc không chạy được `ffprobe`. Cài FFmpeg và
  bảo đảm `ffprobe` nằm trong PATH.
- `ProbeFailedError` — FFprobe chạy nhưng kết quả không dùng được (tệp hỏng, JSON sai,
  thời lượng không hợp lệ).
- `SourceIdentityMismatchError` — tệp nguồn đã bị thay đổi so với lần nhập trước.
- `UnsafeArtifactPathError` — đích ghi tệp nằm ngoài thư mục dự án.
