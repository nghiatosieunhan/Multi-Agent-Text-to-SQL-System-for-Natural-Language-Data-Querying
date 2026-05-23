import sqlite3

def translate_database():
    print("🚀 Bắt đầu Việt hóa Database...")
    # Kết nối vào bản sao DB (nhớ dùng bản sao để tránh hỏng file gốc)
    conn = sqlite3.connect('Chinook_VN.sqlite')
    cursor = conn.cursor()

    # Bật Foreign Keys để SQLite tự động cập nhật các liên kết khi đổi tên bảng
    cursor.execute("PRAGMA foreign_keys=ON;")

    # 1. TỪ ĐIỂN ĐỔI TÊN BẢNG (TABLES)
    tables_map = {
        "Customer": "KhachHang",
        "Employee": "NhanVien",
        "Invoice": "HoaDon",
        "InvoiceLine": "ChiTietHoaDon",
        "Track": "BaiHat",
        "Artist": "NgheSi",
        "Genre": "TheLoai",
        "MediaType": "DinhDang",
        "Playlist": "DanhSachPhat",
        "PlaylistTrack": "ChiTietDanhSachPhat"
        # Bảng Album giữ nguyên
    }

    for old_tbl, new_tbl in tables_map.items():
        try:
            cursor.execute(f"ALTER TABLE {old_tbl} RENAME TO {new_tbl};")
            print(f"✅ Bảng: {old_tbl} -> {new_tbl}")
        except Exception as e:
            print(f"⚠️ Bỏ qua bảng {old_tbl}: {e}")

    # 2. TỪ ĐIỂN ĐỔI TÊN CỘT (COLUMNS)
    columns_map = {
        "KhachHang": {"CustomerId": "MaKhachHang", "FirstName": "Ten", "LastName": "Ho", "Country": "QuocGia", "City": "ThanhPho", "State": "TieuBang", "Address": "DiaChi"},
        "NhanVien": {"EmployeeId": "MaNhanVien", "FirstName": "Ten", "LastName": "Ho", "ReportsTo": "MaNguoiQuanLy", "Title": "ChucVu"},
        "HoaDon": {"InvoiceId": "MaHoaDon", "CustomerId": "MaKhachHang", "InvoiceDate": "NgayLapHoaDon", "Total": "TongTien"},
        "ChiTietHoaDon": {"InvoiceLineId": "MaChiTiet", "InvoiceId": "MaHoaDon", "TrackId": "MaBaiHat", "UnitPrice": "DonGia", "Quantity": "SoLuong"},
        "BaiHat": {"TrackId": "MaBaiHat", "Name": "Ten", "AlbumId": "MaAlbum", "MediaTypeId": "MaDinhDang", "GenreId": "MaTheLoai", "UnitPrice": "DonGia", "Milliseconds": "ThoiLuong_ms", "Bytes": "DungLuong_bytes"},
        "Album": {"AlbumId": "MaAlbum", "Title": "TieuDe", "ArtistId": "MaNgheSi"},
        "NgheSi": {"ArtistId": "MaNgheSi", "Name": "Ten"},
        "TheLoai": {"GenreId": "MaTheLoai", "Name": "Ten"},
        "DinhDang": {"MediaTypeId": "MaDinhDang", "Name": "Ten"},
        "DanhSachPhat": {"PlaylistId": "MaDanhSachPhat", "Name": "Ten"},
        "ChiTietDanhSachPhat": {"PlaylistId": "MaDanhSachPhat", "TrackId": "MaBaiHat"}
    }

    for tbl, cols in columns_map.items():
        for old_col, new_col in cols.items():
            try:
                cursor.execute(f"ALTER TABLE {tbl} RENAME COLUMN {old_col} TO {new_col};")
            except Exception as e:
                # Bỏ qua nếu cột đã đổi tên hoặc không tồn tại
                pass
        print(f"✅ Đã quét xong cột cho bảng: {tbl}")

    conn.commit()
    conn.close()
    print("🎉 Hoàn tất Việt hóa Cấu trúc Database!")

if __name__ == "__main__":
    translate_database()