# Meta prompt: Xây dựng tool tiếng Việt để cắt và render video recap

Bạn là kỹ sư phần mềm cấp cao chuyên xây dựng hệ thống xử lý video, FFmpeg, text-to-speech, phụ đề và đồng bộ timeline.

## Mục tiêu

Hãy thiết kế và xây dựng một tool chạy độc lập có khả năng:

1. Nhận video nguồn.
2. Nhận file `recap_project.json`.
3. Cắt các clip theo đúng mốc trong JSON.
4. Tạo giọng đọc cho từng phân đoạn.
5. Đo thời lượng giọng đọc thực tế.
6. Điều chỉnh tốc độ video để khớp với giọng.
7. Ghép các phân đoạn theo thứ tự.
8. Trộn âm thanh.
9. Tạo phụ đề.
10. Render video hoàn chỉnh.
11. Giữ nguyên tỷ lệ khung hình video nguồn.

Tool không phân tích nội dung phim và không có vòng phản hồi với AI biên kịch.

Tool phải có giao diện đồ họa hoàn chỉnh bằng tiếng Việt, dễ nhìn, dễ hiểu và đủ đơn giản để người không có kiến thức kỹ thuật cũng sử dụng được.

## Ranh giới trách nhiệm

File JSON là nguồn chỉ dẫn duy nhất và có tính quyết định.

Tool phải làm đúng JSON và không được:

- Phân tích cốt truyện.
- Nhận diện nhân vật.
- Đánh giá cảnh có phù hợp hay không.
- Tự chọn cảnh.
- Lấy thêm cảnh ngoài JSON.
- Đổi thứ tự clip.
- Viết lại kịch bản.
- Thêm hoặc xóa từ.
- Thay đổi tốc độ giọng đọc.
- Yêu cầu ChatGPT sửa kịch bản.
- Gửi kết quả ngược lại cho ChatGPT.

Tool chỉ được kiểm tra tính hợp lệ kỹ thuật của dữ liệu.

## Nền tảng đề xuất

Ưu tiên kiến trúc chạy cục bộ:

- FFmpeg và ffprobe để xử lý video.
- Một backend rõ ràng, dễ bảo trì.
- TTS được đóng gói dưới dạng adapter để có thể thay nhà cung cấp.
- Hỗ trợ ít nhất `en-US` và `de-DE`.
- Giao diện desktop là giao diện chính và bắt buộc ngay từ bản đầu tiên.
- Người dùng không cần mở terminal hoặc nhập câu lệnh.
- Giao diện dòng lệnh chỉ là tùy chọn dành cho kiểm thử và tự động hóa nội bộ.

Nếu workspace đã có công nghệ hoặc kiến trúc, hãy giữ và mở rộng kiến trúc hiện tại. Không thay toàn bộ công nghệ nếu không cần thiết.

## Ngôn ngữ của tool

Toàn bộ nội dung người dùng nhìn thấy phải sử dụng tiếng Việt, bao gồm:

- Tên màn hình.
- Tiêu đề cửa sổ.
- Nút bấm.
- Nhãn nhập liệu.
- Menu.
- Hướng dẫn.
- Chú thích.
- Trạng thái tiến trình.
- Cảnh báo.
- Thông báo lỗi.
- Thông báo hoàn thành.
- Hộp thoại xác nhận.
- Tài liệu hướng dẫn sử dụng.

Các mã kỹ thuật như `COMPLETED`, `FAILED` hoặc `DURATION_OUT_OF_RANGE` có thể giữ bằng tiếng Anh trong mã nguồn và tệp log kỹ thuật, nhưng giao diện phải hiển thị bản tiếng Việt dễ hiểu.

Ví dụ:

- `COMPLETED` → “Đã hoàn thành”.
- `FAILED` → “Xử lý thất bại”.
- `DURATION_OUT_OF_RANGE` → “Thời lượng cảnh và giọng đọc chênh lệch quá mức cho phép”.
- `VOICE_READY` → “Đã tạo xong giọng đọc”.

Ngôn ngữ giao diện tiếng Việt không làm thay đổi ngôn ngữ recap. Giọng recap vẫn là tiếng Anh Mỹ hoặc tiếng Đức theo `recap_project.json`.

## Yêu cầu giao diện và trải nghiệm sử dụng

Thiết kế giao diện cho người dùng phổ thông, không giả định người dùng biết JSON, FFmpeg, codec, API hoặc dòng lệnh.

### Nguyên tắc chung

- Giao diện sạch, rõ ràng, có thứ tự thị giác tốt.
- Nút chính phải nổi bật và dùng động từ dễ hiểu.
- Không hiển thị quá nhiều tùy chọn kỹ thuật cùng lúc.
- Các thiết lập nâng cao phải được thu gọn trong mục “Cài đặt nâng cao”.
- Sử dụng giá trị mặc định an toàn để người dùng có thể render mà không cần chỉnh codec hoặc tham số FFmpeg.
- Không dùng thuật ngữ kỹ thuật khi có thể diễn đạt bằng tiếng Việt thông thường.
- Nếu bắt buộc dùng thuật ngữ, thêm giải thích ngắn ngay bên cạnh.
- Không yêu cầu người dùng tự sửa JSON trong ứng dụng.
- Không hiển thị khóa API sau khi đã lưu.
- Không làm người dùng mất dữ liệu khi đóng nhầm cửa sổ hoặc khi render bị gián đoạn.

### Quy trình bốn bước trên giao diện

#### Bước 1 — Chọn dữ liệu

Hiển thị rõ hai vùng:

1. “Chọn video nguồn”.
2. “Chọn file chỉ dẫn recap”.

Hỗ trợ:

- Nút chọn tệp.
- Kéo và thả tệp.
- Hiển thị tên và đường dẫn tệp đã chọn.
- Nút thay đổi hoặc xóa lựa chọn.

#### Bước 2 — Kiểm tra dự án

Sau khi chọn đủ tệp, tự động kiểm tra và hiển thị bản tóm tắt:

- Tên video.
- Thời lượng.
- Độ phân giải.
- Tỷ lệ khung hình.
- Loại nội dung.
- Ngôn ngữ recap.
- Giọng đọc sẽ sử dụng.
- Số video recap sẽ được tạo.
- Số phân đoạn cần xử lý.
- Thư mục lưu kết quả.

Nếu có lỗi, giải thích bằng tiếng Việt và chỉ rõ người dùng cần làm gì. Không chỉ hiển thị mã lỗi kỹ thuật.

#### Bước 3 — Thiết lập và xác nhận

Hiển thị các lựa chọn đơn giản:

- Thư mục lưu video.
- Có tạo phụ đề SRT hay không.
- Có gắn phụ đề trực tiếp vào video hay không.
- Chất lượng đầu ra: “Tiêu chuẩn”, “Chất lượng cao” hoặc “Giữ gần chất lượng gốc”.
- Danh sách các video sẽ render.

Các thông số codec, bitrate, CRF, preset và tham số FFmpeg phải nằm trong “Cài đặt nâng cao”, không xuất hiện trên màn hình chính.

Nút hành động chính phải là “Bắt đầu tạo video”.

#### Bước 4 — Xử lý và kết quả

Trong khi chạy, hiển thị:

- Thanh tiến trình tổng.
- Tên bước đang thực hiện.
- Video hoặc phân đoạn đang xử lý.
- Số lượng đã hoàn thành trên tổng số.
- Thời gian đã chạy.
- Nút “Dừng xử lý” có xác nhận.

Không hiển thị liên tục các câu lệnh FFmpeg trên màn hình chính. Nhật ký kỹ thuật được đặt trong mục riêng dành cho người cần kiểm tra lỗi.

Khi hoàn thành, hiển thị:

- Danh sách video đã tạo.
- Trạng thái từng video.
- Đường dẫn tệp.
- Nút “Mở video”.
- Nút “Mở thư mục kết quả”.
- Nút “Tạo dự án mới”.

### Xử lý lỗi thân thiện

Mỗi lỗi trên giao diện phải trả lời ba câu hỏi:

1. Chuyện gì đã xảy ra?
2. Lỗi xảy ra ở tệp hoặc phân đoạn nào?
3. Người dùng có thể làm gì tiếp theo?

Ví dụ:

> Không thể tạo video “Highlight 02” vì thời lượng cảnh ngắn hơn giọng đọc quá nhiều. Các video đã hoàn thành vẫn được giữ lại. Bạn có thể kiểm tra file chỉ dẫn hoặc tiếp tục mở thư mục kết quả.

Không hiển thị stack trace, mã lệnh hoặc lỗi thô trên màn hình chính. Những thông tin đó chỉ xuất hiện trong nhật ký kỹ thuật.

### Khả năng sử dụng

- Có cỡ chữ dễ đọc và độ tương phản rõ.
- Có trạng thái hover, focus, disabled và loading cho nút bấm.
- Không dùng màu sắc làm cách duy nhất để biểu thị lỗi hoặc thành công.
- Các nút nguy hiểm phải có xác nhận.
- Có thể thao tác bằng bàn phím ở những chức năng chính.
- Giao diện hoạt động tốt trên màn hình laptop thông dụng.
- Không để nội dung bị cắt, tràn hoặc che khuất khi thay đổi kích thước cửa sổ.
- Ghi nhớ thư mục nguồn và thư mục đầu ra gần nhất nếu người dùng cho phép.

### Thiết lập lần đầu

Nếu tool cần FFmpeg, TTS hoặc khóa API, cung cấp trình hướng dẫn thiết lập bằng tiếng Việt:

- Kiểm tra thành phần đã sẵn sàng hay chưa.
- Giải thích ngắn gọn thành phần đó dùng để làm gì.
- Cho phép chọn hoặc nhập thông tin cần thiết.
- Kiểm tra kết nối hoặc cấu hình.
- Hiển thị kết quả thành công hoặc hướng dẫn sửa lỗi.

Người dùng không phải tự chỉnh tệp cấu hình bằng tay nếu có thể thực hiện an toàn trên giao diện.

## Hợp đồng đầu vào

Tool phải đọc các trường:

- `schema_version`
- `project_id`
- `source_video`
- `classification`
- `render_policy`
- `voice_profile`
- `outputs`
- `segments`
- `source_clips`

Mỗi output trong `outputs` tạo thành một video riêng.

Ví dụ:

- `FULL_RECAP` tạo một video recap toàn tập.
- `HIGHLIGHT` tạo một video highlight.

Tool phải kiểm tra:

- JSON hợp lệ.
- Schema được hỗ trợ.
- Video nguồn tồn tại.
- Mốc bắt đầu nhỏ hơn mốc kết thúc.
- Mốc clip không vượt quá video.
- `segment_id` và `clip_id` không bị trùng.
- Ngôn ngữ có hồ sơ TTS tương ứng.
- Phân đoạn narration có `narration_text`.
- Phân đoạn original dialogue không yêu cầu TTS.

## Quy trình bắt buộc

### Giai đoạn 1 — Đọc và kiểm tra dự án

- Đọc JSON.
- Kiểm tra cấu trúc.
- Đọc thông số video bằng ffprobe.
- Ghi nhận chiều rộng, chiều cao, FPS, thời lượng và tỷ lệ khung hình.
- Khóa tỷ lệ nguồn cho toàn bộ đầu ra.

### Giai đoạn 2 — Cắt clip

- Cắt đúng các mốc `start_ms` và `end_ms`.
- Đặt tên theo `render_id`, `segment_id` và `clip_id`.
- Giữ nguyên tỷ lệ khung hình.
- Không crop.
- Không kéo méo.
- Không tự mở rộng mốc.
- Không dùng clip ngoài JSON.

### Giai đoạn 3 — Tạo giọng

Đối với `segment_type: narration`:

- Tạo một tệp giọng cho từng phân đoạn.
- Dùng đúng `voice_profile`.
- Dùng đúng ngôn ngữ.
- Giữ tốc độ giọng ở `1.00x`.
- Lưu audio vào bộ nhớ đệm để không tạo lại khi render lại.
- Đo chính xác thời lượng audio sau khi tạo.

Đối với `segment_type: original_dialogue`:

- Không tạo giọng.
- Giữ video và âm thanh gốc ở tốc độ `1.00x`.

### Giai đoạn 4 — Đồng bộ video với giọng

Tính:

```text
required_video_speed =
tổng_thời_lượng_clip / thời_lượng_giọng_thực_tế
```

Quy tắc:

- Giọng đọc luôn giữ tốc độ `1.00x`.
- Chỉ điều chỉnh tốc độ hình ảnh.
- Nếu hình dài hơn giọng, có thể cắt phần thừa và tăng tốc video.
- Nếu hình ngắn hơn giọng, có thể giảm tốc video.
- Tốc độ thông thường nằm trong `video_speed_min` và `video_speed_max`.
- Không vượt `video_speed_absolute_min` và `video_speed_absolute_max`.
- Không lặp clip.
- Không đóng băng khung hình.
- Không lấy cảnh khác.
- Không thay đổi lời đọc.

Nếu tốc độ cần thiết vượt giới hạn tuyệt đối:

- Dừng output đang render.
- Trả lỗi cục bộ `DURATION_OUT_OF_RANGE`.
- Ghi chi tiết vào nhật ký kỹ thuật.
- Không gọi hoặc gửi yêu cầu cho ChatGPT.

### Giai đoạn 5 — Xử lý âm thanh

Với `original_audio: mute`:

- Tắt âm thanh nguồn.

Với `original_audio: duck`:

- Hạ âm thanh nguồn khi giọng recap phát.
- Giọng recap phải luôn nghe rõ.

Với `original_audio: preserve`:

- Giữ âm thanh nguồn.
- Chỉ sử dụng cho phân đoạn được JSON chỉ định.

Nếu video bị thay đổi tốc độ:

- Không để âm thanh gốc bị méo hoặc sai cao độ.
- Ưu tiên tắt hoặc hạ âm thanh nguồn trong đoạn narration.
- Phân đoạn hội thoại gốc luôn giữ tốc độ `1.00x`.

### Giai đoạn 6 — Phụ đề

- Tạo phụ đề từ `narration_text`.
- Dùng thời lượng audio thực tế.
- Hỗ trợ Unicode đầy đủ cho tiếng Đức.
- Xuất SRT.
- Cho phép cấu hình gắn phụ đề trực tiếp vào video.
- Không tạo phụ đề narration cho phân đoạn `original_dialogue` nếu JSON không yêu cầu.

### Giai đoạn 7 — Render

- Ghép clip theo `order`.
- Ghép phân đoạn theo `order`.
- Render riêng từng output.
- Giữ nguyên tỷ lệ khung hình nguồn.
- Không chuyển ngang thành dọc.
- Không thêm nền để đổi tỷ lệ.
- Không crop.
- Không kéo méo.
- Xuất video nháp và video hoàn chỉnh.
- Tên đầu ra phải chứa `project_id` và `render_id`.

## Cấu trúc thư mục

```text
project/
  source/
  instructions/
    recap_project.json
  clips/
  voices/
  subtitles/
  previews/
  final/
  logs/
```

## Trạng thái kỹ thuật

Hỗ trợ các trạng thái:

- `NEW`
- `VALIDATED`
- `CLIPS_READY`
- `VOICE_READY`
- `READY_TO_RENDER`
- `RENDERING`
- `COMPLETED`
- `FAILED`
- `DURATION_OUT_OF_RANGE`

Không sử dụng `NEEDS_SCRIPT_REVISION`.

Giao diện phải ánh xạ các trạng thái kỹ thuật sang thông báo tiếng Việt; không hiển thị mã trạng thái đơn độc cho người dùng phổ thông.

## Yêu cầu bảo mật và độ ổn định

- Không ghi khóa API trực tiếp trong mã nguồn.
- Đọc khóa và cấu hình nhạy cảm từ biến môi trường hoặc kho bí mật.
- Không ghi nội dung khóa vào log.
- Xử lý đường dẫn an toàn.
- Không ghi đè video nguồn.
- Có thể tiếp tục từ bước gần nhất sau khi bị gián đoạn.
- Không tạo lại clip hoặc audio đã hoàn thành nếu dữ liệu đầu vào không đổi.
- Một output lỗi không được làm mất các output đã hoàn thành.

## Kiểm thử bắt buộc

Viết kiểm thử cho:

1. TV show tiếng Anh 16:9.
2. German Soap tiếng Đức 16:9.
3. Video nguồn 4:3.
4. Hình dài hơn giọng.
5. Hình ngắn hơn giọng.
6. Tốc độ yêu cầu nằm trong giới hạn.
7. Tốc độ yêu cầu vượt giới hạn.
8. Nhiều clip trong một phân đoạn.
9. Phân đoạn giữ hội thoại gốc.
10. JSON sai cấu trúc.
11. Mốc thời gian vượt video.
12. Video nguồn không tồn tại.
13. Render lại mà không tạo lại TTS.
14. Kiểm tra tỷ lệ đầu ra giống tỷ lệ nguồn.
15. Toàn bộ nhãn, nút, cảnh báo và lỗi chính hiển thị bằng tiếng Việt.
16. Người dùng mới có thể hoàn thành quy trình bằng giao diện mà không dùng terminal.
17. Giao diện hoạt động đúng khi kéo thả video và JSON.
18. Giao diện hiển thị tiến trình và kết quả của từng output.
19. Lỗi kỹ thuật được chuyển thành hướng dẫn tiếng Việt dễ hiểu.
20. Giao diện không vỡ bố cục trên màn hình laptop thông dụng.

## Tiêu chí nghiệm thu

Tool đạt yêu cầu khi:

- Đọc đúng JSON theo schema.
- Cắt đúng mốc thời gian.
- Tạo đúng giọng Anh hoặc Đức.
- Không bao giờ thay đổi tốc độ giọng.
- Chỉ thay đổi tốc độ video trong giới hạn.
- Không tự chọn hoặc thay cảnh.
- Không có giao tiếp ngược với ChatGPT.
- Giữ nguyên tỷ lệ video nguồn.
- Phụ đề khớp audio.
- Có thể render recap toàn tập và highlight.
- Lỗi đầu vào được xử lý cục bộ, rõ ràng.
- Toàn bộ giao diện và tài liệu dành cho người dùng sử dụng tiếng Việt.
- Người chưa biết kỹ thuật có thể chọn video, chọn JSON và render mà không cần hướng dẫn trực tiếp.
- Các thiết lập nâng cao không làm rối quy trình chính.
- Mọi lỗi quan trọng đều có giải thích và hướng xử lý bằng tiếng Việt.
- Các kiểm thử quan trọng đều vượt qua.

## Cách thực hiện

Trước tiên:

1. Kiểm tra workspace hiện tại.
2. Chọn kiến trúc nhỏ nhất đáp ứng yêu cầu.
3. Xác định các file và module cần tạo.
4. Trình bày kế hoạch triển khai ngắn.
5. Sau đó thực hiện đầy đủ phần code.
6. Chạy kiểm thử và một bản render thử.
7. Chạy ứng dụng và kiểm tra trực quan toàn bộ giao diện, trạng thái, chữ tiếng Việt, khoảng cách, nội dung bị cắt và khả năng thao tác.
8. Báo cáo những phần đã hoàn thành, kiểm thử đã chạy và giới hạn còn lại.

Không tự mở rộng sang đăng Facebook, phân tích phim, tạo thumbnail hoặc quản lý nội dung.

## Thông tin dự án

Workspace: `[ĐƯỜNG DẪN WORKSPACE]`

Hệ điều hành mục tiêu: `[WINDOWS/LINUX/MACOS]`

Ngôn ngữ lập trình ưu tiên: `[NGÔN NGỮ HOẶC ĐỂ TRỐNG]`

TTS provider: `[NHÀ CUNG CẤP HOẶC ĐỂ AI ĐỀ XUẤT]`

Định dạng video đầu ra: `[MP4 HOẶC ĐỊNH DẠNG KHÁC]`

Kiểu giao diện: `Ứng dụng desktop tiếng Việt; CLI chỉ là tùy chọn nội bộ`
