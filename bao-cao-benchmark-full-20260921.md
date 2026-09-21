# BÁO CÁO ĐÁNH GIÁ CUBESANDBOX AGENT — FULL BENCHMARK

Kết quả chính trong báo cáo sử dụng **Full Benchmark** từ file cubesandbox-full-20260921-161141.json. Phần so sánh cuối báo cáo đối chiếu với lần Full ngay trước đó lúc 15:27 cùng ngày.

## Tóm tắt kết quả

| Chỉ số chính | Kết quả |
|---|---:|
| Load task thành công | 441/441, đạt 100% |
| Synthetic Agent | 5/5 lượt hoàn thành, kiểm tra 6/6 |
| Telemetry cgroup/OOM | Đầy đủ ở 35/35 cấu hình load test |
| Tổng thời gian profile | 216,267 giây |
| Throughput tổng hợp | 2,941 task/s |
| Peak RAM sandbox trong load test | 244,92 / 1.802,21 MiB |
| OOM kill trong load test | 0 |
| Kết luận | ĐẠT |

## 1. Cách hệ thống hoạt động

Người dùng chọn task hoặc profile benchmark trên web. Web gọi backend cục bộ; backend chuyển runner vào CubeSandbox và thực thi trong MicroVM. Runner đo tài nguyên, kiểm tra kết quả và trả dữ liệu về web.

Luồng xử lý: Người dùng → Web → Backend → CubeSandbox → Chạy task → Kiểm tra → Báo cáo.

Artifact cần giữ được lưu trong /output. Dữ liệu benchmark tạm được tạo trong /scratch và dọn sau khi đo. Token, sandboxID và domain runtime chỉ được giữ ở backend.

### API CubeSandbox được sử dụng

| API | Chức năng |
|---|---|
| POST /sandboxes | Tạo MicroVM từ template. |
| POST /process.Process/Start | Chạy runner, trả stdout, stderr và exit code. |
| GET /files?path=... | Đọc hoặc tải artifact. |
| POST /files?path=... | Ghi hoặc upload file. |
| DELETE /sandboxes/{id} | Hủy sandbox và giải phóng tài nguyên. |

Process API và File API được backend gọi qua runtime https://49983-{sandboxID}.{domain}. Trình duyệt không gọi trực tiếp CubeSandbox.

## 2. Các task trong Full Benchmark

| Task | Nội dung kiểm thử |
|---|---|
| CPU | Tính SHA-256 liên tục trong 1,5 giây. |
| Memory | Cấp phát và chạm thật 128 MiB RAM. |
| Disk | Ghi, fsync, đọc và kiểm tra 32 MiB trong /scratch. |
| Mixed | Nén và giải nén 16 MiB dữ liệu. |
| Data | Tạo 200.000 dòng CSV, làm sạch dữ liệu và tạo biểu đồ. |
| Documents | Tạo DOCX 300 đoạn và PDF 10 trang, sau đó đọc lại để kiểm tra. |
| Code | Sinh module Python và chạy 8 unit test. |
| Images | Xử lý 12 ảnh Full HD, thumbnail và contact sheet. |
| Search | Chuẩn bị một corpus dùng chung gồm 5.000 tài liệu; mỗi task tìm truy vấn riêng và trả citation theo file/dòng. |
| Workflow | Kết hợp Data, Documents và Code trong một task. |
| Synthetic Agent | Chạy Data → Documents → Code → Images → Search → memory pressure. |
| Multi-user | Chạy các workload ở mức 1, 2, 4, 6 và 8 user trong cùng sandbox. |

Search mới không tạo lại 5.000 file cho mỗi task. Corpus chỉ-đọc được chuẩn bị một lần cho mỗi load test, sau đó nhiều user cùng tìm kiếm và mỗi task chỉ tạo file kết quả riêng.

## 3. Cách tạo task và tính benchmark

### 3.1 Cách tạo 441 load task

Full Benchmark dùng 7 workload: Slide, Data, Documents, Code, Images, Search và Workflow. Mỗi workload được chạy ở 5 mức tải: 1, 2, 4, 6 và 8 user. Mỗi user thực hiện 3 task.

| Thành phần tính | Kết quả |
|---|---:|
| Số task mỗi workload | (1 + 2 + 4 + 6 + 8) × 3 = 63 task |
| Tổng số task | 63 × 7 workload = 441 task |

Trong một cấu hình, mỗi user chạy 3 task của mình theo thứ tự. Các user khác nhau có thể chạy đồng thời. Runner giới hạn số process nặng để bảo vệ sandbox có giới hạn RAM không vượt quá 2 GiB:

| Workload | Giới hạn task chạy đồng thời |
|---|---:|
| Slide, Code, Search | 8 |
| Data, Documents, Images | 4 |
| Workflow | 3 |

Mỗi task có thư mục tạm riêng để tránh ghi đè dữ liệu. Khi task hoàn tất, runner đọc manifest, kiểm tra output rồi xóa thư mục tạm. Riêng Search chuẩn bị một corpus 5.000 tài liệu một lần; các user dùng chung corpus chỉ-đọc nhưng dùng truy vấn và file kết quả riêng.

Ngoài 441 load task, Full Benchmark còn chạy 5 lượt CPU, 5 lượt Memory, 5 lượt Disk, 5 lượt Mixed và 5 lượt Synthetic Agent. Các lượt lặp dùng để giảm ảnh hưởng của một kết quả bất thường và tính median.

### 3.2 Nguồn đo và công thức

| Chỉ số | Nguồn hoặc cách tính |
|---|---|
| Thời gian task | time.monotonic() trong sandbox, từ lúc process bắt đầu đến khi kết thúc. |
| Tổng thời gian profile | Đo trên trình duyệt, từ request benchmark đầu tiên đến khi load test cuối cùng hoàn tất. |
| CPU user/system | resource.getrusage() của Linux. |
| CPU % | (CPU user + CPU system) / thời gian task × 100; 100% tương đương một core. |
| Peak RSS | RAM resident cao nhất của process/task tree, lấy từ resource.getrusage() hoặc os.wait4(). |
| Disk read/write | Chênh lệch read_bytes và write_bytes trong /proc/self/io. |
| CPU nhìn thấy | os.cpu_count() trong sandbox. |
| RAM total/available | /proc/meminfo. |
| Dung lượng /scratch | shutil.disk_usage(). |
| Median | Sắp xếp 5 kết quả và lấy giá trị đứng giữa. |
| p50 | 50% task thành công có latency nhỏ hơn hoặc bằng giá trị này. |
| p95 | 95% task thành công có latency nhỏ hơn hoặc bằng giá trị này. |
| Success rate | Số task thành công / số task yêu cầu × 100. |
| Throughput từng cấu hình | Số task thành công / tổng thời gian cấu hình. |
| Throughput tổng hợp | 441 task / 149,962 giây load test = 2,941 task/s. |
| Tính đúng của output | Đếm dòng, trang, test, ảnh, kết quả tìm kiếm/citation và kiểm tra SHA-256. |

Throughput tổng hợp dùng để nhìn toàn bộ bài Full, nhưng không nên dùng một mình để so sánh các workload vì Slide và Code nhẹ hơn nhiều so với Data hoặc Workflow. Khi đánh giá capacity phải xem thêm throughput và p95 của từng workload.

## 4. Phạm vi Full Benchmark

Nguồn dữ liệu: cubesandbox-full-20260921-161141.json.

| Nội dung | Số lượng |
|---|---:|
| Benchmark CPU/RAM/Disk/Mixed | 5 lượt mỗi loại |
| Synthetic Agent | 5 lượt |
| Workload nhiều user | 7 workload |
| Mức user | 1, 2, 4, 6 và 8 |
| Cấu hình load test | 35 |
| Tổng load task | 441 |
| Tổng thời gian | 216,267 giây, khoảng 3 phút 36 giây |

## 5. Kết quả

### 5.1 Môi trường

| Thông số | Giá trị |
|---|---:|
| Logical CPU nhìn thấy | 2 |
| CPU quota | Không giới hạn; CPU nhìn thấy vẫn là 2 logical CPU |
| RAM total | 1.930,21 MiB |
| RAM available đầu bài | 1.722,42 MiB |
| Cgroup memory.current đầu bài | 9,00 MiB |
| Cgroup memory.max | 1.802,21 MiB |
| /scratch free / total | 938,06 / 1.005,42 MiB |

### 5.2 Benchmark tài nguyên cơ bản

| Benchmark | Median | Khoảng đo | CPU median | Peak RSS lớn nhất | Đánh giá |
|---|---:|---:|---:|---:|---|
| CPU | 1,500 s | 1,500–1,500 s | 100% | 6,88 MiB | Ổn định ở tải một core |
| Memory 128 MiB | 1,049 s | 1,047–2,444 s | 4,4% | 134,88 MiB | Đạt; lượt đầu có warm-up |
| Disk 32 MiB | 0,063 s | 0,056–0,083 s | 88,8% | 8,88 MiB | Đạt; đã ghi đồng bộ khoảng 32 MiB mỗi lượt |
| Mixed 16 MiB | 0,089 s | 0,087–0,109 s | 99,9% | 55,00 MiB | Ổn định |

### 5.3 Synthetic Agent

| Chỉ số | Kết quả |
|---|---:|
| Lượt thành công | 5/5 |
| Integrity check | 6/6 ở cả 5 lượt |
| Artifact mỗi lượt | 5.037 file, khoảng 7,74 MiB |
| Thời gian lượt đầu | 15,660 s |
| Thời gian median | 4,616 s |
| Khoảng thời gian bốn lượt warm | 4,405–4,651 s |
| Peak RSS lớn nhất | 1.291,32 MiB |
| Peak RSS trước memory pressure | Khoảng 107 MiB |

Lượt đầu chậm hơn do cold start và lần đầu cấp phát page bộ nhớ. Peak khoảng 1.291 MiB xuất hiện ở bước memory pressure có chủ đích, không phải mức RAM thông thường của Agent.

### 5.4 Load test nhiều user

Mỗi workload chạy tổng cộng 63 task qua năm mức user. Kết quả toàn bộ là **441/441 task thành công**.

| Workload | Thành công | p95 tại 1/2/4/6/8 user (s) | Peak RSS/task | Peak RAM sandbox | Đồng thời lớn nhất | OOM tổng |
|---|---:|---|---:|---:|---:|---:|
| Slide | 63/63 | 0,024 / 0,024 / 0,048 / 0,048 / 0,024 | 9,75 MiB | 28,20 MiB | 1 | 0 |
| Data | 63/63 | 1,097 / 1,243 / 2,429 / 2,359 / 2,442 | 47,45 MiB | 191,22 MiB | 4 | 0 |
| Documents | 63/63 | 0,264 / 0,283 / 0,536 / 0,593 / 0,655 | 39,95 MiB | 164,20 MiB | 4 | 0 |
| Code | 63/63 | 0,100 / 0,099 / 0,100 / 0,106 / 0,104 | 8,88 MiB | 54,13 MiB | 2 | 0 |
| Images | 63/63 | 1,048 / 1,078 / 2,134 / 2,139 / 2,123 | 45,34 MiB | 207,54 MiB | 4 | 0 |
| Search | 63/63 | 0,120 / 0,140 / 0,137 / 0,148 / 0,148 | 12,63 MiB | 79,07 MiB | 3 | 0 |
| Workflow | 63/63 | 1,285 / 1,419 / 2,272 / 2,235 / 2,170 | 77,58 MiB | 244,92 MiB | 3 | 0 |

Tổng thời gian của 35 load test là 149,962 giây. Throughput tổng hợp đạt **2,941 task/s**. Cả 35 cấu hình đều đọc được peak RAM cgroup, giới hạn RAM và bộ đếm OOM. Peak RAM sandbox cao nhất trong ma trận load test là 244,92 MiB, bằng khoảng 13,6% giới hạn 1.802,21 MiB.

### 5.5 Kết quả Search sau khi sửa

| Chỉ số | Kết quả |
|---|---:|
| Corpus | 5.000 tài liệu, khoảng 4,60 MiB |
| Thời gian setup median | 1,354 s |
| Search 8 user × 3 task | 24/24 thành công |
| p50/p95 ở 8 user | 0,124 / 0,148 s |
| Peak RSS/task | 12,63 MiB |
| Peak RAM sandbox | 79,07 MiB |
| OOM kill | 0 |
| Lỗi hết /scratch | 0 |

So với Full Benchmark lúc 15:27 cùng ngày:

- Tỷ lệ thành công giữ nguyên ở **441/441**.
- Tổng thời gian profile giảm từ 224,547 xuống 216,267 giây, nhanh hơn khoảng 3,7%.
- Tổng thời gian load test giảm từ 155,138 xuống 149,962 giây, nhanh hơn khoảng 3,3%.
- Throughput tổng hợp tăng từ 2,843 lên 2,941 task/s, tăng khoảng 3,5%.
- Telemetry cgroup đã chuyển từ thiếu dữ liệu sang đầy đủ ở cả 35 cấu hình load test.

## 6. Nhận xét về CubeSandbox

### Điểm tốt

- Tất cả 441 load task và 5 Synthetic Agent run đều hoàn thành.
- CubeSandbox chạy được đồng thời Data, Documents, Code, Images, Search và Workflow.
- Corpus Search dùng chung giải quyết được giới hạn storage/inode của thiết kế cũ.
- Artifact, citation, checksum, số trang, số ảnh và unit test đều được kiểm tra thật.
- Workload thông thường có peak RSS khoảng 9–78 MiB/task; chỉ bài stress RAM đạt khoảng 1.291 MiB.
- Peak RAM toàn sandbox trong ma trận load test cao nhất là 244,92 MiB trên giới hạn 1.802,21 MiB.
- Cả 35 cấu hình load test đều ghi nhận OOM kill bằng 0.
- /scratch được dọn sau từng task và không còn lỗi đầy dung lượng.

### Điểm cần lưu ý

- CPU quota ở trạng thái không giới hạn; giới hạn thực tế về số CPU tiến trình nhìn thấy là 2 logical CPU.
- Peak RAM sandbox được lấy mẫu mỗi 50 ms, vì vậy có thể bỏ lỡ spike ngắn hơn chu kỳ lấy mẫu.
- Bộ đếm OOM đầy đủ cho 35 cấu hình load test; riêng 5 lượt Synthetic Agent chỉ có peak RSS tiến trình và trạng thái hoàn thành, chưa có OOM delta riêng trong JSON này.
- Slide, Code và Search có thời gian task ngắn; với stagger 0,2 giây, concurrency thực tế chỉ đạt 1–3 dù cấu hình tới 8 user.
- Multi-user đang dùng chung một sandbox, chưa kiểm thử cách ly giữa tenant.
- Benchmark đo phần tool execution, chưa bao gồm suy luận LLM, token, API model hoặc truy cập Internet.

## 7. Kết luận và khuyến nghị

CubeSandbox **đạt yêu cầu về chức năng, độ ổn định và telemetry trong Full Benchmark**: 441/441 load task thành công, 5/5 Synthetic Agent hoàn thành, không có timeout, không còn lỗi storage của Search và không ghi nhận OOM trong 35 cấu hình load test.

Khuyến nghị vận hành ban đầu:

- Workflow: 2–3 task đồng thời mỗi sandbox.
- Data, Documents và Images: tối đa 4 task đồng thời.
- Search: corpus dùng chung; có thể phục vụ 8 user trong bài hiện tại, nhưng nên chạy thêm với stagger bằng 0 để xác nhận concurrency 8 thực sự.
- Tách thời gian setup corpus khỏi latency truy vấn khi đặt SLO.
- Bổ sung OOM delta riêng cho Synthetic Agent và cân nhắc giảm chu kỳ lấy mẫu RAM nếu cần bắt các spike rất ngắn.
- Chạy thêm bài nhiều sandbox hoặc nhiều tenant để đánh giá cách ly và năng lực toàn cụm.

Đánh giá tổng thể: **ĐẠT** trong phạm vi benchmark đã thực hiện.
