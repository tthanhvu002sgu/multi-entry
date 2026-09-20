# Multi Entry

Web lập kế hoạch DCA forex dùng trên điện thoại và PC. Python tính toán qua MT5 đang chạy trên **cùng Windows VPS**. Giao diện tiếng Việt, không cần Node hoặc bước build frontend.

## Hiện trạng

- SL theo swing low (Buy) / swing high (Sell), chọn M1/M5/M15/M30/H1/H4/D1. Nhấn **Lấy SL theo swing** để bật; sau đó đổi timeframe/chiều/cặp sẽ lấy swing mới. Khi sửa entry hoặc SL, chuyển sang giữ SL cố định; nút lấy swing bật lại tính tự động.
- Swing: pivot nghiêm ngặt 2 nến trái + 2 nến phải đã đóng; bỏ nến đang chạy, tìm gần nhất theo thời gian và đúng phía entry trong tối đa 300 nến. Đỉnh/đáy bằng nhau không được coi là pivot. Không dự đoán swing hoặc đánh giá cấu trúc thị trường; chưa lọc pivot từng bị phá trong quá khứ.
- Nút ▲/▼ và phím ArrowUp/ArrowDown tại ô SL dùng **trade_tick_size** thực tế, không suy ra từ tên symbol, pip hoặc digits. Chọn 1/10/100 ticks; giao diện hiện bước bằng giá. Tick giống nhau thì bước giống nhau, không ép mỗi ticker khác nhau. Giá nhập lệch grid được đưa đến grid kế tiếp theo hướng bấm; không được vượt entry hoặc xuống giá không dương.
- Đệm swing tính bằng ticks; Buy trừ, Sell cộng, làm tròn ra ngoài theo tick size. Mốc OHLC lấy từ chart broker, chưa tự bù chênh lệch Bid/Ask cho Sell; người dùng cân nhắc spread trong mức đệm. Không thay đổi SL theo từng tick/nến mới; chỉ lấy lại khi đổi lựa chọn trong chế độ swing hoặc bấm nút.
- Tăng/giảm SL tự tính lại lot/rủi ro; đánh dấu **đã chỉnh tay**, lưu nguyên giá và nguồn swing cùng kế hoạch. Khi không tìm được swing, để trống SL và báo lỗi thay vì tự chọn mức khác không rõ nguồn.

- Tự chia 1–50 entry cách đều từ entry đầu đến SL; entry cuối cách SL một bước.
- Hai chế độ: ngân sách chịu lỗ → lot bằng nhau; lot cố định → tổng lỗ.
- Giá làm tròn theo tick size; lot theo min/step/max broker. Báo rõ khi min lot vượt ngân sách.
- Tính lại từng entry bằng `order_calc_profit()` sau khi làm tròn. P/L theo **đồng tiền tài khoản**, chỉ hiện USD khi tài khoản thực sự là USD.
- Chi phí commission hai chiều trên mỗi lot và dự phòng cố định cả kế hoạch do người dùng nhập.
- Dữ liệu MT5: tài khoản, equity, symbol, Bid/Ask, tuổi báo giá, contract size, quy tắc volume.
- Lưu tối đa 100 kế hoạch vào SQLite trên VPS, mở lại trên thiết bị khác và tính lại. Chặn tự tính nếu tiền tệ kế hoạch khác tiền tệ hiện tại. Xuất CSV kết quả.
- Đăng nhập một người dùng, cookie HttpOnly/SameSite, hạn 12 giờ, giới hạn đăng nhập sai, kiểm tra Origin cho thay đổi dữ liệu.
- Không gửi lệnh, không sửa SL, không đóng vị thế. Chỉ có thao tác chọn symbol trong Market Watch.

## Chạy nhanh trên PC — mô phỏng

Yêu cầu Python 3.11+ (khuyến nghị 3.12 64-bit).

```powershell
cd C:\Users\Vu\Documents\vscode\multi-entry
.\setup.ps1
.\start.ps1
```

Mở http://127.0.0.1:8871. `.env` mặc định `APP_MODE=demo`: giá giả lập cố định, tài khoản giả 1.000 USD, EURUSD/GBPUSD/AUDUSD/NZDUSD/USDJPY. Nến swing demo được tạo bằng sóng giả lập; không lấy từ thị trường. USDJPY demo dùng tick 0.001 và quy đổi JPY bằng giá SL giả định. Nhãn MÔ PHỎNG luôn hiển thị. Không dùng kết quả demo để giao dịch.

Nếu PowerShell chặn file script, có thể chạy các lệnh Python tương ứng trong `setup.ps1`/`start.ps1` trực tiếp, không cần đổi policy toàn máy.

## Kết nối MT5 thật trên VPS

1. Copy dự án sang Windows VPS thông thường. MetaQuotes Virtual Hosting thuê trong MT5 không phải môi trường hỗ trợ cài Python/web server này.
2. Mở MT5 và đăng nhập tài khoản broker. Chạy Python cùng người dùng/phiên Windows với terminal.
3. Chạy `setup.ps1`, sửa `.env`:

```dotenv
APP_MODE=mt5
HOST=127.0.0.1
PORT=8871
APP_PASSWORD=thay-bang-mat-khau-rieng-dai-it-nhat-12-ky-tu
MT5_PATH=C:\Program Files\Your Broker MT5\terminal64.exe
MT5_LOGIN=12345678
MT5_SERVER=YourBroker-Server
MAX_TICK_AGE_SECONDS=120
COOKIE_SECURE=false
```

4. Điền đúng path, login và server thực tế. Ứng dụng kết nối tài khoản đã đăng nhập, không lưu mật khẩu giao dịch và không chủ động chuyển tài khoản. Từ chối kết quả nếu MT5 đang dùng account/server khác cấu hình.
5. Chạy `start.ps1`. Đăng nhập web, chọn symbol, dùng nút **Dùng giá hiện tại**, nhập SL, số entry và ngân sách.

Ứng dụng chỉ hỗ trợ `SYMBOL_CALC_MODE_FOREX` (0) và `FOREX_NO_LEVERAGE` (5). Vàng, chỉ số, futures và các CFD dùng cách tính khác không thuộc phiên bản này. Không tự suy đoán thông số từ tên symbol.

## Truy cập từ điện thoại / PC khác

**Khuyến nghị:** giữ Python bind `127.0.0.1`, đặt reverse proxy HTTPS hoặc tunnel HTTPS có xác thực phía trước trên VPS. Proxy phải giữ nguyên Host và không cache `/api/`. Đặt `COOKIE_SECURE=true` khi truy cập bằng HTTPS, vẫn giữ APP_PASSWORD. Proxy đi vào `127.0.0.1:8871`.

Hoặc dùng mạng VPN riêng giữa các thiết bị rồi bind `HOST` vào IP VPN của VPS và đặt APP_PASSWORD. Không mở trực tiếp port Python ra Internet qua HTTP. `run.py` tắt tin cậy forwarded headers; mọi truy cập sau proxy chia sẻ giới hạn đăng nhập của IP proxy.

Đây là web responsive: điện thoại và PC mở cùng URL. Chưa có offline PWA; cần kết nối VPS để có số liệu mới. Dùng một worker (`run.py` đã cấu hình), tránh nhiều tiến trình điều khiển cùng terminal và phân mảnh phiên đăng nhập.

## Tự khởi động trên VPS

Sau khi xác nhận chạy thủ công, cấu hình Windows Task Scheduler:

- Trigger: At log on của đúng tài khoản Windows chạy MT5.
- Program: đường dẫn tuyệt đối đến `.venv\Scripts\pythonw.exe`.
- Arguments: đường dẫn tuyệt đối đến `run.py`, bọc dấu nháy nếu có khoảng trắng.
- Start in: thư mục dự án.
- Chọn chạy khi người dùng đã đăng nhập; restart on failure; không mở phiên bản thứ hai nếu task đã chạy.
- Cấu hình MT5 tự khởi động trong cùng phiên và giữ đăng nhập broker.

Phải thử ngắt Remote Desktop, mở lại web; sau đó thử reboot + đăng nhập Windows. Đăng xuất Windows có thể kết thúc MT5/Python. Ứng dụng thử initialize lại khi có request và báo lỗi nếu terminal/broker chưa sẵn sàng. Không tự cấu hình auto-login Windows hoặc firewall trong dự án.

## Quy tắc tính và giới hạn

Với N entry: `entry[i] = entry1 + i × (SL - entry1) / N`, i từ 0 đến N−1. Làm tròn mỗi giá theo tick size; từ chối nếu trùng entry/SL. Buy phải SL thấp hơn entry đầu; Sell ngược lại.

Lỗ theo giá được tính từng entry bởi broker adapter. Bộ giải lot dựa trên tính tuyến tính theo volume của forex. Tính tại min lot, chia ngược ngân sách sau dự phòng/commission, làm tròn xuống, giới hạn max volume và tính lại từng entry. P/L từ API làm tròn 10 chữ số thập phân đơn vị tài khoản để tránh sai số float phá vỡ biên lot; mức này nhỏ hơn rất nhiều độ chính xác tiền hiển thị. Không làm tròn lot lên để dùng hết ngân sách.

Khi không đủ ngân sách cho min lot, bảng hiển thị **kịch bản min lot để tham khảo**, gắn nhãn KHÔNG KHẢ THI; không coi đây là khuyến nghị đặt lệnh. Lot cố định không hợp lệ sẽ báo lỗi, không tự chỉnh.

`order_calc_profit()` trả tiền tài khoản theo điều kiện hiện tại, không bảo đảm tỷ giá lúc chạm SL. Commission/swap không được suy ra tự động. Entry và SL hiểu là giá thực thi dự kiến, không cộng thêm spread tự động. Người dùng tự thêm dự phòng, kết quả không phải trần lỗ được bảo đảm.

Bản đầu là **kế hoạch tất cả entry khớp**, không kiểm tra khả năng khớp, khoảng cách pending order hợp lệ, margin đủ, stop-out, tổng volume limit của tài khoản, hedging/netting hoặc vị thế đang mở. Leverage không được dùng để nhân P/L. Giá hiện tại chỉ cập nhật khi tải context, dùng giá hiện tại, hoặc tính lại; không tự di chuyển entry/SL người dùng.

Giá symbol quá 120 giây bị từ chối (bao gồm cuối tuần). Có thể sửa ngưỡng nhưng phải hiểu đây là chấp nhận dữ liệu cũ hơn. Chưa xác minh tuổi của từng tỷ giá trung gian nội bộ MT5 dùng quy đổi. Trước khi sử dụng thực tế, so sánh với broker/MT5, đặc biệt tài khoản cent và cặp chéo; cần người có kinh nghiệm vận hành MT5 xác nhận thông số và sai số chấp nhận.

## Kiểm tra

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Kiểm tra bao gồm ví dụ EURUSD, Buy/Sell, ngân sách tại biên, min lot, phí, giá trùng, lot sai bước, xác thực, lưu/xoá, đổi tài khoản, và dữ liệu broker giả lập. Demo/reference EURUSD: entry 1.1000, SL 1.0900, N=4 → 1.1000/1.0975/1.0950/1.0925. Lot 0.01 → $25; ngân sách $60 → 0.02 mỗi entry, tổng $50 chưa phí.

Trước khi vận hành thật: đối chiếu P/L từng entry với `order_calc_profit`/MT5 đúng broker, sai số tiền tối đa 0.01 đơn vị tài khoản khi cùng thời điểm/tỷ giá; lot đúng grid và tổng rủi ro không vượt ngân sách ngoài sai số số thực 1e-8. Kiểm thử mất mạng, đổi account, báo giá cũ, reboot và truy cập hai thiết bị. Không thể xác nhận kết nối VPS thật nếu chưa chạy trên VPS của bạn.

## Cấu trúc

- `app/engine.py`: chia entry và giải lot.
- `app/broker.py`: MT5 và demo adapter; tuần tự hoá truy cập terminal.
- `app/main.py`: API, auth, lưu SQLite.
- `static/`: giao diện responsive không phụ thuộc CDN.
- `tests/`: kiểm tra tính toán và API.
- `data/plans.sqlite3`: kế hoạch, cần sao lưu; không commit `.env`/`data`.

## Nguồn kỹ thuật

- https://www.mql5.com/en/docs/python_metatrader5
- https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcprofit_py
- https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfo_py
- https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py

## Nhật ký

- 2026-09-20: thêm SL swing 2–2 theo timeframe, nến đóng lấy bằng `copy_rates_from_pos(..., 1, 300)`, đệm ticks, nút ▲/▼ theo tick size và multiplier 1/10/100. Chỉnh tay giữ SL, tự tính lại rủi ro, lưu nguồn swing; bảo vệ phản hồi cũ khi đổi input trong lúc request chạy. Thêm USDJPY demo để kiểm tra bước giá khác nhau. 37 tests qua; Edge kiểm tra auto Buy/Sell, đổi symbol/timeframe, override, bước EURUSD/JPY, lưu/mở, và không tràn ngang 360/390/768/1440px. Chưa kiểm tra dữ liệu swing trên VPS MT5 thật.

- 2026-09-20: tạo phiên bản đầu, UI tiếng Việt, tính lot/SL, adapter Python–MT5, demo, đăng nhập, SQLite, CSV, hướng dẫn Windows VPS và bộ kiểm tra. MT5 thật và công bố URL từ VPS chờ cấu hình môi trường của người dùng.
- 2026-09-20: xác nhận 24 kiểm tra tự động đều qua trong `.venv` Python 3.12/Windows. Kiểm tra trình duyệt Edge: Buy/Sell, ngân sách tại biên, min lot không khả thi, lot sai bước, làm cũ kết quả khi sửa input, lưu/mở giữa hai phiên, xoá và tải CSV. Không có lỗi JavaScript; không tràn ngang ở 360/390/768/1440px. Lưu phiên bản thư viện đã kiểm tra trong `requirements-lock.txt`; setup dùng file này làm constraints. Có 2 cảnh báo deprecation từ thư viện kiểm thử, không ảnh hưởng kết quả.

