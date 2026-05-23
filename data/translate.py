import json
import re

# 1. Khai báo bộ từ điển (Dictionary)
# Lưu ý: Xếp các từ dài lên trước để tránh regex bị replace đè (ví dụ InvoiceLine phải đứng trước Invoice)
MAPPING_DICT = {
    # Bảng
    "InvoiceLine": "ChiTietHoaDon",
    "PlaylistTrack": "ChiTietDanhSachPhat",
    "Customer": "KhachHang",
    "Employee": "NhanVien",
    "Invoice": "HoaDon",
    "Track": "BaiHat",
    "Artist": "NgheSi",
    "Genre": "TheLoai",
    "MediaType": "DinhDang",
    "Playlist": "DanhSachPhat",
    "Title": "TieuDe",
    "Name": "Ten",
    
    # Cột
    "CustomerId": "MaKhachHang",
    "InvoiceId": "MaHoaDon",
    "TrackId": "MaBaiHat",
    "AlbumId": "MaAlbum",
    "ArtistId": "MaNgheSi",
    "GenreId": "MaTheLoai",
    "MediaTypeId": "MaDinhDang",
    "PlaylistId": "MaDanhSachPhat",
    "EmployeeId": "MaNhanVien",
    "SupportRepId": "MaNhanVienHoTro",
    "ReportsTo": "MaNguoiQuanLy",
    
    "FirstName": "Ten",
    "LastName": "Ho",
    "UnitPrice": "DonGia",
    "Quantity": "SoLuong",
    "Total": "TongTien",
    "InvoiceDate": "NgayLapHoaDon",
    "Milliseconds": "ThoiLuong_ms",
    "Bytes": "DungLuong_bytes",
    "Country": "QuocGia",
    "City": "ThanhPho",
    "State": "TieuBang",
    "Address": "DiaChi",
    "Email": "Email"
}

def translate_sql(sql: str) -> str:
    """Dùng Regex để thay thế chính xác các từ (chỉ thay thế nguyên từ - whole word)"""
    translated_sql = sql
    for en_word, vn_word in MAPPING_DICT.items():
        # r'\b' đảm bảo chỉ replace đúng từ đó, không replace một phần của từ khác
        pattern = r'\b' + re.escape(en_word) + r'\b'
        translated_sql = re.sub(pattern, vn_word, translated_sql)
    return translated_sql

def main():
    print("Đang đọc file data.json...")
    with open("data.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    # Cập nhật metadata
    data["metadata"]["dataset"] = "Chinook VN (Vietnamese Business DB)"
    
    print("Đang tiến hành Việt hóa 300 câu SQL và cấu trúc...")
    for q in data["questions"]:
        # 1. Dịch câu SQL mẫu
        if "gold_sql" in q:
            q["gold_sql"] = translate_sql(q["gold_sql"])
        
        # 2. Dịch mảng tables (nếu có)
        if "tables" in q:
            q["tables"] = [MAPPING_DICT.get(t, t) for t in q["tables"]]
            
        # 3. Dịch mảng sql_keywords (nếu có)
        if "sql_keywords" in q:
            q["sql_keywords"] = [MAPPING_DICT.get(kw, kw) for kw in q["sql_keywords"]]

    # Lưu ra file mới
    output_file = "data_vn.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"🎉 Hoàn tất! Đã lưu file mới tại: {output_file}")

if __name__ == "__main__":
    main()