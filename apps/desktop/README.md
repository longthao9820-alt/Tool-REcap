# Recap Studio — trình render JSON

Recap Studio có thể dựng JSON có sẵn hoặc phân tích phim trực tiếp qua API
OpenAI-compatible. Ứng dụng tự tạo contact sheet có timecode và đọc phụ đề bằng
FFmpeg cục bộ, gửi evidence chữ/ảnh tới endpoint đã cấu hình, rồi kiểm tra JSON bằng
importer thật trước khi nạp. Prompt Recap được lưu riêng với Prompt Highlight.
Nếu video không có phụ đề nhưng có âm thanh, tool tự trích audio và dùng
Faster-Whisper multilingual cục bộ để tạo transcript có timecode; transcript và
visual evidence được gửi cùng nhau để API hiểu nội dung.

## Cách dùng

1. Bấm **Phân tích bằng AI API…**, chọn video/thư mục, endpoint/model và Prompt
   TV Show Original Commentary; hoặc chuẩn bị JSON schema 2.2 có cả
   `FULL_RECAP` và `MAIN_STORY` nếu muốn chọn hai chế độ trong tool.
2. Mở `C:\Users\Long\Desktop\Toolrecap\RecapStudio.exe`.
3. Bấm **Chọn JSON…**. Tool tự tìm các video theo `source_file`.
4. Nếu còn thiếu tập, bấm **Chọn lại…** và chọn thư mục phim một lần.
5. Chọn các tập, **Recap cả tập** hoặc **Nội dung chính**, ngôn ngữ và giọng đọc;
   bấm **Nghe thử**.
6. Bấm **Bắt đầu render**. Tool chỉ render các output thuộc kiểu recap đã chọn.

JSON 1.0–2.1 và JSON 2.2 chỉ chứa một chế độ vẫn được hỗ trợ. Khi JSON không có
output của một chế độ, dropdown chỉ hiển thị chế độ thực sự có trong file.

Kết quả mặc định nằm tại `<thư mục phim>\recaps_da_render\<tập>`. Mỗi output
chỉ có ba tệp publication: `<title>.mp4`, `<title>.narration.srt` và
`<title>.original.srt`. Báo cáo và file trung gian được lưu trong vùng dữ liệu nội bộ.

## Thành phần đi kèm

- FFmpeg/FFprobe: `runtime/ffmpeg/bin`.
- Faster-Whisper large-v3-turbo multilingual: nhận dạng lời thoại cục bộ khi không có phụ đề,
  tự phát hiện ngôn ngữ nguồn và ưu tiên GPU (tự chuyển CPU nếu GPU không dùng được).
- VoiceStudio/OmniVoice: chỉ còn 12 preset tiếng Anh, gồm 6 en-US và 6 en-GB.
- VoiceStudio dùng runtime/model ngoài đã cấu hình trong `runtime/speech/engines/voicestudio/provider.json`.
- Faster-Whisper dùng runtime độc lập tại `runtime/speech/stt-runtime`; không còn phụ thuộc engine F5-TTS.
- Các engine/model TTS cũ không còn nằm trong Toolrecap.
- Thư viện giọng có tìm kiếm, lọc, yêu thích, gần đây và trạng thái engine.
- Preview WAV được cache và phát trực tiếp trong tool.
- JSON Schema: `schemas/recap-project-batch-v2.json`.
- JSON mẫu: `docs/recap-json-mua-mau.json`.

## Quy tắc render

- Giữ nguyên tỷ lệ và độ phân giải video nguồn.
- Giọng luôn ở tốc độ 1.00x; tool điều chỉnh tốc độ hình trong 0.75x–1.15x khi cần.
- Nếu cảnh dài hơn giọng, tool tăng tốc hình tối đa 1.15x rồi cắt đều theo tỷ lệ trên từng clip;
  cảnh ngắn đến mức phải giảm dưới 0.75x vẫn được dừng để tránh đóng băng hoặc lặp hình.
- Phân đoạn có lời recap tắt hoàn toàn âm thanh phim.
- Phân đoạn giữ âm thanh phim không được có lời recap.
- Subtitle narration được chia thành các phrase 3–7 từ để nhập vào CapCut; không
  còn đặt cả đoạn narration dài vào một cue.
- Thoại ở các segment giữ audio nguồn được xuất riêng vào `.original.srt` theo
  timeline video cuối.
- Trước khi render TV Show, tool chạy originality audit nội bộ: mục đích biên tập,
  `editorial_value`, tỷ trọng commentary, độ dài original dialogue và clip nguồn dài.
- Audit là guardrail biên tập, không phải chứng nhận hoặc bảo đảm của Facebook.
- Ưu tiên H.264 GPU: NVIDIA NVENC, AMD AMF hoặc Intel Quick Sync.
- Không tự chuyển CPU khi người dùng vẫn chọn **Dùng GPU**.
- Model nặng được giữ trong worker khi render batch và giải phóng khi chuyển sang
  engine nặng khác.

Khi hàng đợi hoàn tất, ứng dụng hiển thị thông báo **Tiến trình đã hoàn tất**, phát
âm báo và nhấp nháy biểu tượng trên taskbar.

## Dữ liệu và lỗi thường gặp

- Model và mẫu nghe thử nằm trong `runtime/speech`, cạnh thư mục `release`.
- Không chuyển riêng file EXE sang thư mục khác; cần giữ thư mục `runtime` đi kèm.
- Nếu báo giọng cũ đã bị loại: chọn tập, mở **Thư viện giọng**, chọn giọng mới rồi bấm **Dùng giọng này**.
- Bản tối ưu chỉ nhận narration `en-US` hoặc `en-GB`.
- Nếu file nghe thử hỏng hoặc im lặng, tool tự tạo lại; nếu vẫn lỗi, báo lỗi rõ thay vì phát file rỗng.
- Kiểm tra WAV và nhận dạng chữ chỉ đo tính hợp lệ/độ rõ, không bảo đảm giọng hợp sở thích mỗi người.
