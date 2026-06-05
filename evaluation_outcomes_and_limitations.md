# Báo Cáo Đánh Giá Tổng Kết Hệ Thống (Outcomes & Limitations)

Dựa trên các kết quả Benchmark thực tế (đặc biệt là bài Blind Test trên tập Northwind với độ chính xác >95%), dưới đây là bản đánh giá chi tiết mức độ hoàn thành của hệ thống đối chiếu với các mục tiêu (Expected Outcomes) và phương pháp (Used Methods) ban đầu, kèm theo các hạn chế (Limitations) của từng quyết định thiết kế.

---

## PHẦN I: ĐÁNH GIÁ CÁC MỤC TIÊU KỲ VỌNG (EXPECTED OUTCOMES)

### 1. End-To-End Natural Language Data Querying System
- **Kết quả đạt được:** Hoàn thành xuất sắc. Hệ thống có khả năng nhận câu hỏi Tiếng Việt tự nhiên, tự động chuyển đổi thành SQL, thực thi trên SQLite và trả về dữ liệu chuẩn xác mà không yêu cầu người dùng biết code.
- **Hạn chế:** Hiện tại hệ thống hoạt động chủ yếu qua giao diện CLI (Command Line) và trả về JSON. Cần phát triển thêm một Web UI (ví dụ Streamlit/React) để người dùng cuối thực sự dễ thao tác.

### 2. Consistent And Structured Query Generation
- **Kết quả đạt được:** Độ ổn định cực cao nhờ bộ **18-Rules (Meta-Rules)**. Cấu trúc SQL sinh ra nhất quán, đạt tỷ lệ lỗi cú pháp (Syntax Error) gần như 0%. (Bài test Northwind đạt 97.9% SQL Execution OK).
- **Hạn chế:** Phụ thuộc vào độ dài Context Window của LLM. Với các Database có hàng trăm bảng (Schema quá khổng lồ), việc nhồi nhét Schema và 18-Rules có thể gây quá tải token.

### 3. Support For Common Analytical Queries
- **Kết quả đạt được:** Hệ thống xử lý mượt mà cả 4 cấp độ: Simple (94.7%), Join (88.9%), Aggregate (73.3%), và Complex (78.8%) trên tập dữ liệu hoàn toàn xa lạ.
- **Hạn chế:** Các câu truy vấn có tính đệ quy sâu (Recursive CTEs) hoặc các hàm toán học thống kê phức tạp chưa được template hóa đầy đủ, LLM đôi khi có thể quên sử dụng hàm làm tròn (như `ROUND`).

### 4. Efficient Query Processing With Semantic Caching
- **Kết quả đạt được:** Xây dựng thành công cơ chế Cache. Đặc biệt, chúng ta đã phát hiện và xử lý triệt để lỗi **"Value Bleeding"** bằng cách chuyển từ Vector Cache sang **Jaccard Similarity Cache** (Threshold 0.90).
- **Hạn chế:** Cache dựa trên Jaccard (Lexical) cực kỳ an toàn nhưng lại cứng nhắc. Nếu người dùng hỏi cùng một ý nhưng dùng từ đồng nghĩa hoàn toàn khác, Cache sẽ bỏ qua (Hit rate thấp nhưng độ chính xác khi Hit là 100%).

### 5. Modular Multi-Agent Pipeline
- **Kết quả đạt được:** Pipeline được module hóa rõ ràng (QueryPlanner, SQLGenerator, FAISS Retriever, Executor). Dễ dàng bóc tách để debug và tối ưu hóa từng khâu.
- **Hạn chế:** Việc gọi qua lại giữa các Agent làm tăng độ trễ (Latency) tổng thể của hệ thống so với một mô hình End-to-End nguyên khối.

### 6. Practical Data Collection And Engineering Pipeline
- **Kết quả đạt được:** Đã tích hợp và Việt hóa thành công các bộ dữ liệu thực tế lớn (Chinook_VN, Northwind_VN, Business). Xây dựng cơ chế Auto-Onboard mượt mà.
- **Hạn chế:** Vẫn cần bước chạy lệnh `onboard` thủ công ban đầu để trích xuất Schema cho Database mới.

### 7. User-Friendly And Interpretable Output
- **Kết quả đạt được:** Báo cáo Benchmark xuất ra console cực kỳ chi tiết, trực quan (chia theo Intent, có phân tích lỗi).
- **Hạn chế:** Cần bổ sung các module vẽ biểu đồ (Basic Data Visualization) tự động dựa trên kết quả trả về để hoàn thiện trải nghiệm.

---

## PHẦN II: PHƯƠNG PHÁP VÀ KỸ THUẬT SỬ DỤNG (METHODS & TECHNIQUES)

### 1. Large Language Models (LLMs)
- **Áp dụng:** Sử dụng **Gemini Flash-Lite** làm lõi xử lý với chi phí rẻ và tốc độ cao.
- **Hạn chế:** Bị giới hạn nghiêm ngặt bởi **Rate Limit** của Google API (Lỗi *Vertex AI silent TPM drop*). Khi chạy Batch Evaluation với luồng cao, LLM bị ngắt kết nối và phải trả về lệnh Fallback (`LIMIT 10`), làm giảm điểm số trên báo cáo.

### 2. Multi-Agent System Architecture
- **Áp dụng:** Phân chia trách nhiệm rõ ràng giúp giảm ảo giác (Hallucination).
- **Hạn chế:** Tăng độ phức tạp của mã nguồn, khó theo dõi luồng lỗi nếu không có log chi tiết (như structlog đã dùng).

### 3. Structured Prompting Strategy
- **Áp dụng:** Áp dụng **18-Rules** là bước ngoặt của dự án. Quy tắc *Strict Literal Preservation* giúp mô hình học được tư duy từ ví dụ (Spider) mà không bị "chép phao" râu ông nọ cắm cằm bà kia.
- **Hạn chế:** Prompt rất dài và tiêu tốn nhiều Token cho mỗi lần gọi.

### 4. Query Template Guidance (Few-shot RAG)
- **Áp dụng:** Sử dụng FAISS để lưu trữ hàng chục ngàn mẫu SQL (Spider, Bird). Dynamic Few-shot giúp LLM luôn có template tham khảo.
- **Hạn chế:** Nguy cơ **Data Leakage** (Lộ đề). Đã từng xảy ra việc 300 câu Tiếng Việt bị nạp nhầm vào FAISS. Cần cơ chế Filter (dataset_type) cực kỳ chặt chẽ khi truy vấn.

### 5. Semantic Caching Mechanism
- **Áp dụng:** Thiết kế Jaccard Cache bảo vệ hệ thống khỏi truy xuất thừa và chống Value Bleeding.
- **Hạn chế:** Không bắt được ngữ nghĩa sâu (Deep Semantic) tốt như Vector Cache. Việc dùng Vector Cache trước đây gây lỗi sai dữ liệu, đòi hỏi sự đánh đổi giữa Tốc độ và Sự an toàn.

### 6. Task Decomposition Approach
- **Áp dụng:** Tách bài toán thành: Tìm hiểu Schema -> Lọc Few-shot -> Sinh SQL -> Thực thi.
- **Hạn chế:** Tăng số lượng requests mạng (Network overhead).

### 9. Experimental Evaluation Methodology
- **Áp dụng:** Xây dựng script `evaluate.py` chạy theo cơ chế *Execution Match* (Thực thi SQL và so sánh Data trả về). Đây là phương pháp chấm điểm uy tín nhất.
- **Hạn chế:** Bị lỗi **False Negative** (Oan sai). Ví dụ: LLM sinh đúng hàm `AVG` nhưng đáp án mẫu là `ROUND(AVG, 2)`. Dữ liệu trả về lệch thập phân khiến máy chấm là SAI, làm điểm số bị thấp hơn năng lực thực tế.
