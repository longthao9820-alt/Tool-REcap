# Recap Studio

Ứng dụng Windows có thể phân tích phim trực tiếp qua API OpenAI-compatible hoặc
dựng video recap hàng loạt từ JSON có sẵn.

Mở trực tiếp: `RecapStudio.exe`.

Tài liệu sử dụng: `apps/desktop/README.md`.

Các nguyên tắc chính:

- Một JSON có thể chứa một tập hoặc cả mùa.
- Tool tự tìm video và tạo `recaps_da_render` cạnh thư mục phim.
- Render H.264 bằng GPU khi có encoder phù hợp.
- Giọng đọc chỉ dùng 12 preset VoiceStudio tiếng Anh (6 en-US, 6 en-GB) và có thể nghe thử ngay trong tool.
- Nút **Phân tích bằng AI API…** tự tạo contact sheet, đọc phụ đề cục bộ, gọi API
  trực tiếp và kiểm tra JSON bằng importer thật trước khi render.
- Prompt Recap được lưu riêng, không dùng chung với Prompt Highlight.
- Tên publication trong JSON được dùng nguyên văn làm tên MP4 và hai file SRT.
- Mỗi output chỉ xuất `<title>.mp4`, `<title>.narration.srt` và `<title>.original.srt`.
- Video nguồn và JSON của người dùng không bao giờ bị sửa.

Bản tối ưu TV Show: loại các runtime/model TTS cũ khỏi Toolrecap, tách riêng
Faster-Whisper để nhận dạng lời thoại, và dùng prompt Original Commentary làm mặc
định. Project dùng ngôn ngữ hoặc giọng đã loại phải chọn lại VoiceStudio tiếng Anh.
Bản EXE trong `release/` dùng chung `runtime/` ở thư mục gốc.
