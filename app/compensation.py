"""Cấu hình & logic tính lương - thưởng - hoa hồng (Compensation Engine).

Toàn bộ quy tắc nghiệp vụ chi trả thù lao được đặt tập trung ở đây, tách khỏi
tầng model/router để dễ bảo trì. Giai đoạn này cấu hình là *tĩnh* (hard-coded +
seed vào DB), chưa cho admin sửa động qua UI.

Nguồn quy tắc: "BẢNG CHI TRẢ THÙ LAO - CẤP TƯ VẤN CÁ NHÂN".

Các khái niệm:
- FYP (First Year Premium): phí bảo hiểm năm đầu.
- FYP quy đổi: FYP sau khi nhân hệ số quy đổi theo sản phẩm.
- Thù lao cá nhân = FYP quy đổi * % thù lao theo cấp bậc.
- Thưởng SXN (Sản xuất nhanh): thưởng theo mốc thời gian nộp & phát hành.
- Thưởng tháng / quý / năm: theo ngưỡng FYP quy đổi lũy kế.
- Khấu trừ: Thuế TNCN 10% + Quỹ đảm bảo 3%.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

# ─── Ánh xạ cấp bậc chữ (rank code) ↔ số 1..6 dùng trong bảng lương ─────────────
# rank.py định nghĩa 6 mã: AC < FC < FM < FD < SD < ED (thấp -> cao).
RANK_TO_LEVEL = {
    "AC": 1,
    "FC": 2,
    "FM": 3,
    "FD": 4,
    "SD": 5,
    "ED": 6,
}
LEVEL_TO_RANK = {v: k for k, v in RANK_TO_LEVEL.items()}


def rank_to_level(rank_code: Optional[str]) -> Optional[int]:
    """AC..ED -> 1..6. None/không hợp lệ -> None (chưa xếp hạng)."""
    if not rank_code:
        return None
    return RANK_TO_LEVEL.get(rank_code.strip().upper())


def level_to_rank(level: Optional[int]) -> Optional[str]:
    if level is None:
        return None
    return LEVEL_TO_RANK.get(level)


# ─── % Thù lao cá nhân theo cấp bậc ─────────────────────────────────────────────
# Suy ra từ dữ liệu báo cáo T8: cấp 1-2 = 0.25, cấp 3 = 0.45, cấp 5 = 0.56...
# (cấp 4/6 nội suy theo thang tăng dần; điều chỉnh khi có bảng chính thức).
PERSONAL_COMMISSION_RATE_BY_LEVEL = {
    1: Decimal("0.25"),
    2: Decimal("0.25"),
    3: Decimal("0.45"),
    4: Decimal("0.50"),
    5: Decimal("0.56"),
    6: Decimal("0.60"),
}
# Trường hợp cấp cao hơn nhưng cùng % (vd cấp 2 có case 0.35 khi là leader trực tiếp)
# sẽ được xử lý qua hệ số override ở tầng rule DB. Mặc định lấy theo bảng trên.
DEFAULT_PERSONAL_RATE = Decimal("0.25")


def personal_commission_rate(level: Optional[int]) -> Decimal:
    if level is None:
        return DEFAULT_PERSONAL_RATE
    return PERSONAL_COMMISSION_RATE_BY_LEVEL.get(level, DEFAULT_PERSONAL_RATE)


# ─── Thưởng SXN (Sản xuất nhanh) theo mốc thời gian ────────────────────────────
# Mốc 1: hợp đồng nộp ngày 1-7 & phát hành trước ngày 14 -> thưởng 1% FYP quy đổi.
# Mốc 2: hợp đồng nộp ngày 8-15 & phát hành đến ngày 21  -> thưởng 1% FYP quy đổi.
SXN_RATE = Decimal("0.01")

SXN_WINDOWS = [
    # (submit_from, submit_to, issue_deadline_day, rate)
    {"submit_from": 1, "submit_to": 7, "issue_deadline": 14, "rate": SXN_RATE, "code": "SXN_1_7"},
    {"submit_from": 8, "submit_to": 15, "issue_deadline": 21, "rate": SXN_RATE, "code": "SXN_8_15"},
]


def sxn_bonus(fyp_converted: Decimal, submit_date: Optional[date],
              issue_date: Optional[date]) -> tuple[Decimal, Optional[str]]:
    """Tính thưởng SXN dựa trên ngày nộp và ngày phát hành.

    Trả về (số tiền thưởng, mã cửa sổ SXN áp dụng | None).
    """
    if not submit_date or not issue_date or fyp_converted <= 0:
        return Decimal("0"), None
    for w in SXN_WINDOWS:
        if w["submit_from"] <= submit_date.day <= w["submit_to"] and issue_date.day <= w["issue_deadline"]:
            return (fyp_converted * w["rate"]).quantize(Decimal("1")), w["code"]
    return Decimal("0"), None


# ─── Thưởng tuyển dụng ─────────────────────────────────────────────────────────
# Tuyển được TVM có sản xuất >= 15tr FYP quy đổi trong 30 ngày.
RECRUITMENT_BONUS_MIN_FYP = Decimal("15000000")
RECRUITMENT_BONUS_WINDOW_DAYS = 30


# ─── Thưởng tháng ──────────────────────────────────────────────────────────────
# FYP quy đổi cá nhân trong tháng >= 50tr.
MONTHLY_BONUS_THRESHOLD = Decimal("50000000")
MONTHLY_BONUS_RATE = Decimal("0.01")


def monthly_bonus(month_total_fyp: Decimal) -> Decimal:
    if month_total_fyp >= MONTHLY_BONUS_THRESHOLD:
        return (month_total_fyp * MONTHLY_BONUS_RATE).quantize(Decimal("1"))
    return Decimal("0")


# ─── Thưởng quý (bậc thang theo FYP quy đổi quý) ───────────────────────────────
QUARTERLY_BONUS_TIERS = [
    # (ngưỡng tối thiểu, tỷ lệ)
    (Decimal("100000000"), Decimal("0.03")),
    (Decimal("60000000"), Decimal("0.02")),
    (Decimal("15000000"), Decimal("0.01")),
]


def quarterly_bonus(quarter_total_fyp: Decimal) -> Decimal:
    for threshold, rate in QUARTERLY_BONUS_TIERS:
        if quarter_total_fyp >= threshold:
            return (quarter_total_fyp * rate).quantize(Decimal("1"))
    return Decimal("0")


# ─── Thưởng năm ────────────────────────────────────────────────────────────────
YEARLY_BONUS_THRESHOLD = Decimal("300000000")
YEARLY_BONUS_RATE = Decimal("0.02")


def yearly_bonus(year_total_fyp: Decimal) -> Decimal:
    if year_total_fyp >= YEARLY_BONUS_THRESHOLD:
        return (year_total_fyp * YEARLY_BONUS_RATE).quantize(Decimal("1"))
    return Decimal("0")


# ─── Overriding (thu nhập chênh lệch quản lý) ──────────────────────────────────
# Leader hưởng phần chênh lệch % hoa hồng so với tuyến dưới, tính trên FYP của
# tuyến dưới. Roll-up toàn nhánh với "compression": khi đi ngược lên cây, mỗi cấp
# chỉ hưởng phần chênh lệch so với mức % đã được hưởng bởi tầng thấp hơn.
#
# Điều kiện: chỉ leader từ cấp OVERRIDING_MIN_LEVEL trở lên mới được hưởng.
OVERRIDING_MIN_LEVEL = 3  # FM trở lên


def overriding_rate_diff(upline_level: Optional[int], downline_effective_rate: Decimal) -> Decimal:
    """Phần % chênh lệch mà upline được hưởng so với mức % đã áp ở tầng dưới.

    Trả về max(0, rate(upline) - downline_effective_rate).
    """
    if upline_level is None or upline_level < OVERRIDING_MIN_LEVEL:
        return Decimal("0")
    diff = personal_commission_rate(upline_level) - downline_effective_rate
    return diff if diff > 0 else Decimal("0")


# ─── Khấu trừ ──────────────────────────────────────────────────────────────────
PIT_RATE = Decimal("0.10")            # Thuế TNCN
GUARANTEE_FUND_RATE = Decimal("0.03")  # Quỹ đảm bảo


def deductions(gross: Decimal) -> tuple[Decimal, Decimal]:
    """Trả về (thuế TNCN, quỹ đảm bảo)."""
    pit = (gross * PIT_RATE).quantize(Decimal("1"))
    fund = (gross * GUARANTEE_FUND_RATE).quantize(Decimal("1"))
    return pit, fund


# ─── Tiêu chí BỔ NHIỆM & THĂNG CẤP (theo bảng chính sách TrustAgent) ────────────
#
# Có 2 bộ tiêu chí khác nhau:
#   1) APPOINTMENT_CRITERIA - "TIÊU CHÍ BỔ NHIỆM": dùng khi onboard/thử thách nhanh
#      (30/60/90 ngày), ngưỡng thấp, yêu cầu số lượng tuyến dưới theo từng cấp.
#   2) PROMOTION_CRITERIA - "CHỈ TIÊU & ĐIỀU KIỆN THĂNG CẤP": thăng cấp chính thức,
#      ngưỡng cao + K2 + nhánh lớn <= 60% + số tháng ghi nhận doanh số liên kề.
#
# Mỗi tiêu chí là 1 dict với các khoá (khoá nào None/0 nghĩa là không ràng buộc):
#   fyp                : FYP quy đổi tối thiểu (Decimal)
#   downline_by_level  : dict {level: số lượng tối thiểu} - đếm trong TOÀN NHÁNH
#   fm_plus            : số thành viên cấp FM(3) trở lên trong nhánh
#   members            : tổng số thành viên nhánh
#   big_branch_max_pct : trần % doanh số của 1 nhánh F1 lớn nhất (vd 0.60)
#   k2_min             : K2 (persistency) tối thiểu (vd 0.70)
#   consecutive_months : số tháng ghi nhận doanh số liên kề
#   review_days        : thời gian xét (chỉ mang tính tham chiếu cho bổ nhiệm)

# ── Bảng 1: TIÊU CHÍ BỔ NHIỆM ──
# Cột: SD, FD, FM, FC, AC, Mã ĐL | FYP | Thời gian xét
APPOINTMENT_CRITERIA = {
    1: {  # AC
        "fyp": Decimal("15000000"), "downline_by_level": {}, "fm_plus": 0,
        "agent_codes": 0, "review_days": 30,
    },
    2: {  # FC: 2 FC? -> theo ảnh: FC cần (FC:-, AC:2, Mã ĐL:1), FYP 30k
        "fyp": Decimal("30000000"),
        "downline_by_level": {1: 2},  # 2 AC
        "agent_codes": 1, "review_days": 30,
    },
    3: {  # FM: FC:2, AC:3, Mã ĐL:2, FYP 100k, 60 ngày
        "fyp": Decimal("100000000"),
        "downline_by_level": {2: 2, 1: 3},  # 2 FC + 3 AC
        "agent_codes": 2, "review_days": 60,
    },
    4: {  # FD: FM:2, FC:3, AC:4, Mã ĐL:3, FYP 200k, 60 ngày
        "fyp": Decimal("200000000"),
        "downline_by_level": {3: 2, 2: 3, 1: 4},
        "agent_codes": 3, "review_days": 60,
    },
    5: {  # SD: FD:2, FM:3, FC:4, AC:5, Mã ĐL:4, FYP 300k, 90 ngày
        "fyp": Decimal("300000000"),
        "downline_by_level": {4: 2, 3: 3, 2: 4, 1: 5},
        "agent_codes": 4, "review_days": 90,
    },
    6: {  # ED: SD:2, FD:4, FM:5, FC:6, AC:7, Mã ĐL:5, FYP 500k, 90 ngày
        "fyp": Decimal("500000000"),
        "downline_by_level": {5: 2, 4: 4, 3: 5, 2: 6, 1: 7},
        "agent_codes": 5, "review_days": 90,
    },
}

# ── Bảng 2: CHỈ TIÊU & ĐIỀU KIỆN THĂNG CẤP ──
# Cột: Số thành viên | Doanh số (FYP) | Số lượng FM+ | Nhánh lớn <=60% | K2 70% | Tháng liên kề
PROMOTION_CRITERIA = {
    2: {  # FC: 3 thành viên, 55k FYP, K2 70%, 3 tháng liên kề
        "members": 3, "fyp": Decimal("55000000"), "fm_plus": 0,
        "big_branch_max_pct": Decimal("0.60"), "k2_min": Decimal("0.70"),
        "consecutive_months": 3,
    },
    3: {  # FM: 15 thành viên, 275k, FM+ 0?, K2 70%, 6 tháng
        "members": 15, "fyp": Decimal("275000000"), "fm_plus": 0,
        "big_branch_max_pct": Decimal("0.60"), "k2_min": Decimal("0.70"),
        "consecutive_months": 6,
    },
    4: {  # FD: 45 thành viên, 1.375tr, FM+ 10, K2 70%, 12 tháng
        "members": 45, "fyp": Decimal("1375000000"), "fm_plus": 10,
        "big_branch_max_pct": Decimal("0.60"), "k2_min": Decimal("0.70"),
        "consecutive_months": 12,
    },
    5: {  # SD: 135 thành viên, 6.875tr, FM+ 20, K2 70%, 24 tháng
        "members": 135, "fyp": Decimal("6875000000"), "fm_plus": 20,
        "big_branch_max_pct": Decimal("0.60"), "k2_min": Decimal("0.70"),
        "consecutive_months": 24,
    },
    6: {  # ED: 405 thành viên, 34.375tr, FM+ 30, K2 70%, 36 tháng
        "members": 405, "fyp": Decimal("34375000000"), "fm_plus": 30,
        "big_branch_max_pct": Decimal("0.60"), "k2_min": Decimal("0.70"),
        "consecutive_months": 36,
    },
}

# K2 mặc định khi chưa có hợp đồng nào tới mốc đánh giá (coi như đạt).
DEFAULT_K2 = Decimal("1.0")

# ─── K2 (Persistency / tỷ lệ duy trì hợp đồng) ─────────────────────────────────
# Cửa sổ đo persistency: hợp đồng được xét sau K2_WINDOW_MONTHS tháng kể từ ngày
# hiệu lực. K2 = (số HĐ còn IN_FORCE) / (số HĐ đã tới mốc xét), theo FYP hoặc theo
# số lượng. Ở đây dùng theo FYP quy đổi (trọng số theo doanh số) — sát thực tế hơn.
K2_WINDOW_MONTHS = 13

# Các trạng thái vòng đời hợp đồng
LIFECYCLE_IN_FORCE = "IN_FORCE"
LIFECYCLE_LAPSED = "LAPSED"
LIFECYCLE_CANCELLED = "CANCELLED"
LIFECYCLE_SURRENDERED = "SURRENDERED"
LIFECYCLE_STATUSES = {
    LIFECYCLE_IN_FORCE, LIFECYCLE_LAPSED, LIFECYCLE_CANCELLED, LIFECYCLE_SURRENDERED,
}


def is_in_force(lifecycle_status: Optional[str]) -> bool:
    return (lifecycle_status or LIFECYCLE_IN_FORCE).upper() == LIFECYCLE_IN_FORCE


def compute_k2(evaluated_fyp: Decimal, in_force_fyp: Decimal) -> Decimal:
    """K2 = FYP còn hiệu lực / FYP đã tới mốc xét. Không có HĐ tới mốc -> mặc định đạt (1.0)."""
    if evaluated_fyp <= 0:
        return DEFAULT_K2
    k2 = (in_force_fyp / evaluated_fyp)
    # chặn trong [0, 1]
    if k2 < 0:
        return Decimal("0")
    if k2 > 1:
        return Decimal("1")
    return k2.quantize(Decimal("0.0001"))


def appointment_criteria_for(level: int) -> Optional[dict]:
    """Tiêu chí BỔ NHIỆM cho một cấp cụ thể (1..6)."""
    return APPOINTMENT_CRITERIA.get(level)


def promotion_criteria_for(current_level: Optional[int]) -> Optional[dict]:
    """Tiêu chí THĂNG CẤP để lên cấp kế tiếp từ current_level."""
    target = (current_level or 0) + 1
    return PROMOTION_CRITERIA.get(target)


def _check_downline_by_level(required: dict, downline_counts: dict) -> bool:
    """downline_counts: {level: số lượng hiện có trong nhánh}. required: {level: min}."""
    for lvl, need in (required or {}).items():
        if downline_counts.get(lvl, 0) < need:
            return False
    return True


def evaluate_appointment_criteria(level: int, metrics: dict) -> tuple[bool, list[dict]]:
    """Đánh giá tiêu chí BỔ NHIỆM cho `level` với các chỉ số đo được (metrics).

    metrics kỳ vọng các khoá:
      fyp (Decimal), downline_counts ({level:count}), agent_codes (int)
    Trả về (đạt?, danh sách chi tiết từng chỉ số để hiển thị).
    """
    req = appointment_criteria_for(level)
    details: list[dict] = []
    if not req:
        return True, details  # cấp không cấu hình -> mặc định đạt

    fyp_cur = metrics.get("fyp", Decimal("0"))
    fyp_ok = fyp_cur >= req["fyp"]
    details.append({"key": "fyp", "current": float(fyp_cur), "required": float(req["fyp"]), "met": fyp_ok})

    dl_counts = metrics.get("downline_counts", {})
    for lvl, need in req.get("downline_by_level", {}).items():
        have = dl_counts.get(lvl, 0)
        details.append({
            "key": f"downline_L{lvl}", "current": have, "required": need,
            "met": have >= need, "rank": level_to_rank(lvl),
        })
    dl_ok = _check_downline_by_level(req.get("downline_by_level", {}), dl_counts)

    codes_ok = True
    if req.get("agent_codes"):
        have = metrics.get("agent_codes", 0)
        codes_ok = have >= req["agent_codes"]
        details.append({"key": "agent_codes", "current": have, "required": req["agent_codes"], "met": codes_ok})

    return (fyp_ok and dl_ok and codes_ok), details


def evaluate_promotion_criteria(target_level: int, metrics: dict) -> tuple[bool, list[dict]]:
    """Đánh giá tiêu chí THĂNG CẤP cho `target_level`.

    metrics kỳ vọng các khoá:
      fyp, members (int), fm_plus (int), big_branch_pct (Decimal),
      k2 (Decimal), consecutive_months (int)
    """
    req = PROMOTION_CRITERIA.get(target_level)
    details: list[dict] = []
    if not req:
        return False, details

    checks = []

    fyp_cur = metrics.get("fyp", Decimal("0"))
    ok = fyp_cur >= req["fyp"]; checks.append(ok)
    details.append({"key": "fyp", "current": float(fyp_cur), "required": float(req["fyp"]), "met": ok})

    mem = metrics.get("members", 0)
    ok = mem >= req["members"]; checks.append(ok)
    details.append({"key": "members", "current": mem, "required": req["members"], "met": ok})

    fmp = metrics.get("fm_plus", 0)
    ok = fmp >= req["fm_plus"]; checks.append(ok)
    details.append({"key": "fm_plus", "current": fmp, "required": req["fm_plus"], "met": ok})

    # Nhánh lớn: doanh số nhánh F1 lớn nhất KHÔNG vượt trần %
    big_pct = metrics.get("big_branch_pct", Decimal("0"))
    ok = big_pct <= req["big_branch_max_pct"]; checks.append(ok)
    details.append({"key": "big_branch_pct", "current": float(big_pct),
                    "required": float(req["big_branch_max_pct"]), "met": ok, "is_max": True})

    k2 = metrics.get("k2", DEFAULT_K2)
    ok = k2 >= req["k2_min"]; checks.append(ok)
    details.append({"key": "k2", "current": float(k2), "required": float(req["k2_min"]), "met": ok})

    cm = metrics.get("consecutive_months", 0)
    ok = cm >= req["consecutive_months"]; checks.append(ok)
    details.append({"key": "consecutive_months", "current": cm,
                    "required": req["consecutive_months"], "met": ok})

    return all(checks), details


def evaluate_achieved_appointment_level(target_level: int, metrics: dict) -> int:
    """Xét lần lượt từ cấp thử thách xuống 1, trả cấp cao nhất đạt tiêu chí BỔ NHIỆM.

    Luôn trả >= 1 (cấp 1 mặc định đạt).
    """
    for lvl in range(target_level, 1, -1):
        ok, _ = evaluate_appointment_criteria(lvl, metrics)
        if ok:
            return lvl
    return 1


# ─── Thời hạn thử thách bổ nhiệm ───────────────────────────────────────────────
# Admin chọn 1 trong các mốc; hệ thống tự tính ngày kết thúc từ ngày bắt đầu.
CHALLENGE_DURATIONS = {
    "30_DAYS": {"label": "30 ngày", "days": 30, "months": 0},
    "2_MONTHS": {"label": "2 tháng", "days": 0, "months": 2},
    "3_MONTHS": {"label": "3 tháng", "days": 0, "months": 3},
    "6_MONTHS": {"label": "6 tháng", "days": 0, "months": 6},
    "12_MONTHS": {"label": "12 tháng", "days": 0, "months": 12},
}


def add_duration(start: date, duration_key: Optional[str]) -> Optional[date]:
    """Cộng thời hạn thử thách vào ngày bắt đầu. Trả None nếu duration không hợp lệ."""
    if not start or not duration_key:
        return None
    spec = CHALLENGE_DURATIONS.get(duration_key)
    if not spec:
        return None
    result = start
    if spec["days"]:
        result = result + timedelta(days=spec["days"])
    if spec["months"]:
        result = _add_months(result, spec["months"])
    return result


def _add_months(d: date, months: int) -> date:
    """Cộng thêm `months` tháng vào một date, xử lý tràn ngày cuối tháng."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    # Ngày cuối tháng đích
    last_day = _last_day_of_month(year, month)
    day = min(d.day, last_day)
    return date(year, month, day)


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


# ─── Bổ nhiệm (appointment / onboard) ──────────────────────────────────────────
# Trạng thái bổ nhiệm của một agent trong một chu kỳ thử thách.
APPOINTMENT_STATUS_PENDING = "PENDING"       # đang thử thách, chưa hoàn tất bổ nhiệm
APPOINTMENT_STATUS_CONFIRMED = "CONFIRMED"    # đã hoàn tất bổ nhiệm
APPOINTMENT_STATUS_FAILED = "FAILED"          # không đạt

# Khi CHƯA hoàn tất bổ nhiệm: một số khoản thưởng đội ngũ/override có thể bị giữ lại.
# Ở đây ta vẫn tính thù lao cá nhân bình thường nhưng đánh cờ để tầng đội ngũ xử lý.
def is_appointment_effective(appointment_status: Optional[str]) -> bool:
    """Agent đã hoàn tất bổ nhiệm để được hưởng đầy đủ quyền lợi đội ngũ chưa?"""
    return (appointment_status or "").upper() == APPOINTMENT_STATUS_CONFIRMED


# ─── Bộ quy tắc để seed vào DB (bảng commission_rule) ──────────────────────────
def default_rule_seed() -> list[dict]:
    """Danh sách quy tắc phẳng để insert vào bảng commission_rule.

    Mỗi rule: {category, rule_key, level, threshold, rate, note}
    Dùng cho FE hiển thị cấu hình (read-only giai đoạn này).
    """
    rules: list[dict] = []

    for level, rate in PERSONAL_COMMISSION_RATE_BY_LEVEL.items():
        rules.append({
            "category": "PERSONAL_COMMISSION",
            "rule_key": f"personal_rate_level_{level}",
            "level": level,
            "threshold": None,
            "rate": rate,
            "note": f"% thù lao cá nhân cho cấp {level}",
        })

    for w in SXN_WINDOWS:
        rules.append({
            "category": "SXN_BONUS",
            "rule_key": w["code"],
            "level": None,
            "threshold": None,
            "rate": w["rate"],
            "note": f"Thưởng SXN: nộp ngày {w['submit_from']}-{w['submit_to']}, "
                    f"phát hành đến ngày {w['issue_deadline']}",
        })

    rules.append({
        "category": "MONTHLY_BONUS", "rule_key": "monthly_bonus",
        "level": None, "threshold": MONTHLY_BONUS_THRESHOLD, "rate": MONTHLY_BONUS_RATE,
        "note": "Thưởng tháng khi FYP quy đổi >= 50tr",
    })

    for threshold, rate in QUARTERLY_BONUS_TIERS:
        rules.append({
            "category": "QUARTERLY_BONUS", "rule_key": f"quarterly_{int(threshold)}",
            "level": None, "threshold": threshold, "rate": rate,
            "note": f"Thưởng quý bậc >= {int(threshold):,}",
        })

    rules.append({
        "category": "YEARLY_BONUS", "rule_key": "yearly_bonus",
        "level": None, "threshold": YEARLY_BONUS_THRESHOLD, "rate": YEARLY_BONUS_RATE,
        "note": "Thưởng năm khi FYP quy đổi >= 300tr/năm",
    })

    rules.append({
        "category": "DEDUCTION", "rule_key": "pit",
        "level": None, "threshold": None, "rate": PIT_RATE, "note": "Thuế TNCN 10%",
    })
    rules.append({
        "category": "DEDUCTION", "rule_key": "guarantee_fund",
        "level": None, "threshold": None, "rate": GUARANTEE_FUND_RATE, "note": "Quỹ đảm bảo 3%",
    })

    rules.append({
        "category": "RECRUITMENT_BONUS", "rule_key": "recruitment_15tr_30d",
        "level": None, "threshold": RECRUITMENT_BONUS_MIN_FYP, "rate": None,
        "note": "Tuyển TVM có SX >= 15tr FYP quy đổi trong 30 ngày",
    })

    return rules
