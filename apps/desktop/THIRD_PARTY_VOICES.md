# Third-party voice runtime

Recap Studio chỉ cung cấp các preset tiếng Anh của VoiceStudio/OmniVoice.

## VoiceStudio / OmniVoice

- Engine: `voicestudio`.
- Preset: 12 giọng tiếng Anh, gồm 6 `en-US` và 6 `en-GB`.
- Source: <https://github.com/debpalash/VoiceStudio>.
- Model: `k2-fsa/OmniVoice@c5fdb5c`.
- Khai báo hiện tại: Apache-2.0 model; AGPL-3.0 runtime.
- `commercial_use: false`, `personal_use_only: true`, `attribution_required: true`.
- Runtime và model nằm ngoài Toolrecap theo các đường dẫn trong
  `runtime/speech/engines/voicestudio/provider.json`.

Người phát hành phải tự kiểm tra quyền sử dụng giọng và footage cho mục đích cụ
thể. Originality của Facebook không thay thế giấy phép hoặc chính sách sở hữu trí
tuệ.

## Speech-to-text

Faster-Whisper large-v3-turbo chỉ dùng để nhận dạng lời thoại nguồn. Thành phần
này nằm trong `runtime/speech/stt-runtime` và không phải engine tạo giọng đọc.

## Quy tắc đóng gói

- Không đưa runtime/model của engine TTS đã loại vào bản phát hành.
- Catalog production chỉ được chứa `voicestudio.en.*`.
- Preview production chỉ được chứa 12 WAV VoiceStudio trong `en-US` và `en-GB`.
- Báo cáo render phải ghi engine, voice ID, model version và license.
