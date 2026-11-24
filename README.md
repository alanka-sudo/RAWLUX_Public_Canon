# RAWLUX — CR3 sang JPG, ưu tiên màu Canon

Website FastAPI công khai được, có Dockerfile cho Render và hỗ trợ chuyển nhiều file tuần tự.

## Hai chế độ

1. **Ưu tiên màu Canon:** thử trích JPEG gốc được máy Canon nhúng trong CR3 bằng ExifTool. Nếu đúng kích thước ảnh RAW, trả nguyên byte JPEG đó (giữ màu từ máy). Nếu preview nhỏ hơn ảnh RAW, xử lý RAW full size rồi hiệu chỉnh gần đúng theo phân bố màu/độ sáng của preview. Nếu không có preview, xử lý RAW thông thường. **Không đảm bảo khớp 100% Canon DPP hoặc Windows Photos.**
2. **RAW đầy đủ:** giải mã sensor bằng rawpy, cân bằng trắng của máy, sRGB; chất lượng JPG 75–100 và tăng sáng EV tuỳ chọn.

Lưu ý: với ảnh JPEG nhúng đúng kích thước, thiết lập EV/chất lượng sẽ không chỉnh sửa JPEG để giữ nguyên màu Canon và dữ liệu JPEG nguyên gốc. Muốn tăng sáng, chọn chế độ RAW đầy đủ.

## Chạy trên máy Windows (cần cài ExifTool nếu muốn chế độ Canon)

```powershell
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Mở http://127.0.0.1:8000. Không có `exiftool` thì trang vẫn chạy nhưng không thể trích JPEG nhúng.

## Đăng website công khai lên Render

1. Đăng ký/đăng nhập https://github.com và tạo repository **mới**, upload **nội dung bên trong** thư mục này (Dockerfile, app.py, requirements.txt, render.yaml, static/...). Không đưa CR3 riêng tư vào GitHub.
2. Truy cập https://dashboard.render.com, chọn **New → Web Service**, kết nối GitHub repository.
3. Chọn **Docker** runtime, branch `main`, và cấu hình instance phù hợp. Dockerfile đã cấu hình port theo biến `$PORT`.
4. Nhấn **Deploy Web Service**. Sau khi deploy thành công, Render cấp một địa chỉ `https://...onrender.com` để chia sẻ công khai. HTTPS do Render quản lý.
5. Với ảnh CR3 4000×6000, nên dùng instance có đủ RAM (khuyến nghị 2 GB trở lên; với nhiều người dùng cần nhiều hơn). Gói miễn phí có thể thiếu RAM, giới hạn băng thông/tải file hoặc sleep. Nếu lỗi 413 / timeout / out-of-memory, tăng giới hạn hosting và tài nguyên máy chủ.
6. Nếu muốn `tenmiencuaban.com`, mua tên miền rồi thêm trong Settings → Custom Domains của Render và cấu hình DNS theo hướng dẫn của Render.

**Không giới hạn số lượng ảnh ở phía giao diện**, nhưng dung lượng từng ảnh mặc định bị giới hạn `MAX_UPLOAD_MB=250` và phụ thuộc giới hạn upload, RAM, lưu lượng, chi phí của hosting. Tệp xử lý được xóa sau khi người dùng tải xong; dịch vụ hosting vẫn có thể lưu log truy cập. Không có hệ thống tài khoản hoặc lưu trữ ảnh lâu dài.

## Kiểm tra

`GET /api/health` trả trạng thái, `POST /api/convert` nhận `file`, `mode` (`canon` / `raw`), `quality`, `ev`. Header phản hồi `X-Render-Mode` nói rõ `canon-embedded`, `canon-preview-matched` hay `raw-developed`.

**Giới hạn xác thực:** chưa thử trực tiếp với CR3 của bạn. Việc có JPEG nhúng đầy đủ phụ thuộc mẫu Canon và cài đặt máy ảnh.
