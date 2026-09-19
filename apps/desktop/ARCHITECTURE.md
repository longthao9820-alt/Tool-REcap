# Kiến trúc Recap Studio tối giản

```text
JSON một tập/cả mùa
→ tìm video nguồn
→ kiểm tra mốc cảnh và quy tắc âm thanh
→ cắt cảnh bằng FFmpeg GPU
→ Unified TTS Manager → provider đã chọn → WAV chuẩn hóa 48 kHz mono
→ đồng bộ hình theo thời lượng giọng
→ tắt âm thanh phim ở đoạn recap
→ ghép phụ đề
→ xuất từng tập vào recaps_da_render
→ thông báo Windows
```

Ứng dụng không có mô-đun phân tích AI, API, phiên âm, VoiceStudio hoặc MCP/Codex.

Các mô-đun chính:

- `batch.py`: nhập JSON 1.0/2.0, tự tìm video và tách dự án theo tập.
- `models.py`: kiểm tra hợp đồng JSON và quy tắc âm thanh.
- `voice_system.py`: catalog, stable ID, favorites/recent, cache version, health và resource manager.
- `voice_worker.py`: worker cô lập crash, giữ model trong bộ nhớ khi render batch.
- `voice_library.py`: tìm kiếm/lọc/nghe thử/cài-sửa engine.
- `tts.py`: lớp tương thích cho project cũ.
- `gpu.py`: phát hiện và thử encoder GPU.
- `media.py`: thao tác FFmpeg/FFprobe đi kèm.
- `renderer.py`: dựng video theo JSON.
- `projects.py`: hàng đợi nhiều tập.
- `notifications.py`: thông báo nổi và nhấp nháy taskbar.
- `ui.py`: giao diện một màn hình.
