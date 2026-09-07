"""Cấp bậc nhân viên (agent rank).

6 cấp chính thức + 1 trạng thái chưa xếp hạng (biểu diễn bằng NULL trong DB / "UNRANKED").
Mã cấp bậc đi kèm nhãn tiếng Việt để hiển thị.
"""

from typing import Optional

# Danh sách 6 cấp hợp lệ (từ thấp đến cao) + nhãn tiếng Việt
RANK_LABELS = {
    "AC": "Chuyên viên khách hàng",
    "FC": "Chuyên viên tư vấn tài chính",
    "FM": "Trưởng phòng Tư vấn tài chính",
    "FD": "Giám đốc kinh doanh",
    "SD": "Giám đốc kinh doanh cấp cao",
    "ED": "Giám đốc điều hành hệ thống đối tác",
}

UNRANKED = "UNRANKED"
UNRANKED_LABEL = "Chưa xếp hạng"

# Các giá trị được phép nhận từ client khi cập nhật cấp bậc
VALID_RANKS = set(RANK_LABELS.keys()) | {UNRANKED}


def normalize_rank(value: Optional[str]) -> Optional[str]:
    """Chuẩn hoá giá trị rank gửi lên. Trả về mã hợp lệ hoặc None (chưa xếp hạng)."""
    if not value:
        return None
    code = value.strip().upper()
    if code == UNRANKED:
        return None
    if code in RANK_LABELS:
        return code
    return None


def is_valid_rank(value: Optional[str]) -> bool:
    """Kiểm tra giá trị rank gửi lên có hợp lệ không (chấp nhận UNRANKED/None)."""
    if not value:
        return True
    return value.strip().upper() in VALID_RANKS


def rank_label(code: Optional[str]) -> str:
    """Trả về nhãn tiếng Việt của cấp bậc; NULL/UNRANKED -> 'Chưa xếp hạng'."""
    if not code:
        return UNRANKED_LABEL
    return RANK_LABELS.get(code.strip().upper(), UNRANKED_LABEL)
