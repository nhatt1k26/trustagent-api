"""Định nghĩa Huy hiệu (badges), Đặc quyền theo cấp, và tiện ích tạo động lực.

Logic thuần (không phụ thuộc DB), nhận số liệu đã tính sẵn để đánh giá. Router
gọi các hàm này với metrics lấy từ contract_financial / snapshot.
"""

from decimal import Decimal
from typing import List, Optional


# ─── Huy hiệu (Badges) ─────────────────────────────────────────────────────────
# Mỗi badge: code, tên, mô tả, icon (tên lucide ở FE), và hàm điều kiện.
# Điều kiện nhận metrics dict: {
#   personal_fyp_month, contracts_month, contracts_total, recruits_month,
#   k2, consecutive_months, first_contract_within_30d (bool)
# }

BADGE_DEFS = [
    {
        "code": "FIRST_CONTRACT",
        "name": "Chốt đơn đầu tay",
        "desc": "Ký hợp đồng thành công đầu tiên",
        "icon": "Sparkles",
        "cond": lambda m: m.get("contracts_total", 0) >= 1,
    },
    {
        "code": "ROOKIE_STAR",
        "name": "Tân binh xuất sắc",
        "desc": "Có hợp đồng ngay trong 30 ngày đầu",
        "icon": "Rocket",
        "cond": lambda m: bool(m.get("first_contract_within_30d")),
    },
    {
        "code": "FYP_MILLIONAIRE",
        "name": "Triệu phú FYP tháng",
        "desc": "Đạt từ 100 triệu FYP quy đổi trong tháng",
        "icon": "Gem",
        "cond": lambda m: _dec(m.get("personal_fyp_month")) >= Decimal("100000000"),
    },
    {
        "code": "DEAL_HUNTER",
        "name": "Thợ săn hợp đồng",
        "desc": "Ký từ 5 hợp đồng trong tháng",
        "icon": "Target",
        "cond": lambda m: m.get("contracts_month", 0) >= 5,
    },
    {
        "code": "RECRUITER",
        "name": "Nhà tuyển dụng",
        "desc": "Tuyển được thành viên mới có sản xuất trong tháng",
        "icon": "UserPlus",
        "cond": lambda m: m.get("recruits_month", 0) >= 1,
    },
    {
        "code": "QUALITY_KEEPER",
        "name": "Giữ chất lượng",
        "desc": "Duy trì K2 từ 90% trở lên",
        "icon": "ShieldCheck",
        "cond": lambda m: _dec(m.get("k2")) >= Decimal("0.90"),
    },
    {
        "code": "CONSISTENT",
        "name": "Bền bỉ",
        "desc": "Có doanh số liên tục từ 3 tháng trở lên",
        "icon": "Flame",
        "cond": lambda m: m.get("consecutive_months", 0) >= 3,
    },
]


def _dec(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def evaluate_badges(metrics: dict) -> List[dict]:
    """Trả về danh sách badge kèm trạng thái đã đạt (earned) hay chưa."""
    result = []
    for b in BADGE_DEFS:
        earned = False
        try:
            earned = bool(b["cond"](metrics))
        except Exception:
            earned = False
        result.append({
            "code": b["code"], "name": b["name"], "desc": b["desc"],
            "icon": b["icon"], "earned": earned,
        })
    return result


# ─── Đặc quyền theo cấp bậc ────────────────────────────────────────────────────
# Mỗi đặc quyền: code, tên, mô tả, cấp tối thiểu để mở khoá (1..6).
# Cấp: AC=1, FC=2, FM=3, FD=4, SD=5, ED=6.
PRIVILEGE_DEFS = [
    {
        "code": "BASIC_CHATBOT",
        "name": "Trợ lý AI cơ bản",
        "desc": "Sử dụng chatbot tư vấn model cơ bản",
        "min_level": 1,
    },
    {
        "code": "ADVANCED_CHATBOT",
        "name": "Trợ lý AI cao cấp",
        "desc": "Sử dụng chatbot với model mạnh hơn, phản hồi chất lượng cao",
        "min_level": 3,  # FM+
    },
    {
        "code": "ZALO_OA_BROADCAST",
        "name": "Gửi thông báo Zalo OA cho đội nhóm",
        "desc": "Chủ động gửi thông báo/động viên tới đội nhóm qua Zalo OA",
        "min_level": 3,  # FM+
    },
    {
        "code": "TEAM_ANALYTICS_ADVANCED",
        "name": "Phân tích đội nhóm nâng cao",
        "desc": "Xem báo cáo hiệu suất chi tiết toàn nhánh",
        "min_level": 4,  # FD+
    },
    {
        "code": "PRIORITY_SUPPORT",
        "name": "Hỗ trợ ưu tiên",
        "desc": "Kênh hỗ trợ riêng, phản hồi ưu tiên",
        "min_level": 4,
    },
    {
        "code": "LEADERSHIP_TOOLS",
        "name": "Bộ công cụ lãnh đạo",
        "desc": "Công cụ quản trị & đào tạo đội ngũ dành cho cấp lãnh đạo",
        "min_level": 5,  # SD+
    },
]


def evaluate_privileges(level: Optional[int]) -> List[dict]:
    """Trả về danh sách đặc quyền + trạng thái mở khoá theo cấp hiện tại."""
    lv = level or 0
    result = []
    for p in PRIVILEGE_DEFS:
        result.append({
            "code": p["code"], "name": p["name"], "desc": p["desc"],
            "min_level": p["min_level"],
            "unlocked": lv >= p["min_level"],
        })
    return result


def has_privilege(level: Optional[int], code: str) -> bool:
    lv = level or 0
    for p in PRIVILEGE_DEFS:
        if p["code"] == code:
            return lv >= p["min_level"]
    return False


# ─── Kiến thức về K2 (hiển thị FE) ─────────────────────────────────────────────
K2_KNOWLEDGE = [
    {
        "q": "K2 là gì?",
        "a": "K2 (Persistency) là tỷ lệ duy trì hợp đồng - đo lường phần trăm hợp "
             "đồng/phí bảo hiểm vẫn còn hiệu lực và được đóng phí đúng hạn sau kỳ đầu.",
    },
    {
        "q": "Vì sao K2 quan trọng?",
        "a": "K2 phản ánh chất lượng tư vấn. K2 cao nghĩa là khách hàng hài lòng và "
             "duy trì hợp đồng, giúp bạn đủ điều kiện nhận đầy đủ thưởng tháng/quý/năm "
             "và thưởng quản lý. K2 dưới ngưỡng có thể mất các khoản thưởng này.",
    },
    {
        "q": "Ngưỡng K2 cần đạt?",
        "a": "Để đủ điều kiện thăng cấp và hưởng đầy đủ quyền lợi, K2 cần đạt từ 70% "
             "trở lên.",
    },
    {
        "q": "Làm sao cải thiện K2?",
        "a": "Tư vấn đúng nhu cầu, không bán quá khả năng tài chính của khách; chăm sóc "
             "sau bán (nhắc đóng phí, thăm hỏi dịp kỷ niệm hợp đồng); giải thích rõ quyền "
             "lợi để khách gắn bó lâu dài.",
    },
    {
        "q": "K2 được tính khi nào?",
        "a": "Hệ thống tự động tính K2 dựa trên trạng thái hiệu lực của các hợp đồng đã "
             "qua mốc 13 tháng. Hợp đồng mặc định duy trì hiệu lực; chỉ khi có biến cố "
             "(hủy/mất hiệu lực) mới ảnh hưởng K2.",
    },
]
