"""SQLAlchemy models cho module Lương-Thưởng-Hoa hồng & Bổ nhiệm.

Tách khỏi app/models.py để hạn chế sửa file cũ. Import Base chung từ app.database.

Gồm 4 bảng mới:
- contract_financial:   dữ liệu tài chính chi tiết của hợp đồng (mở rộng Contract).
- commission_rule:      cấu hình quy tắc chi trả (read-only giai đoạn này).
- payroll_result:       kết quả tính thù lao đã lưu (per hợp đồng / per kỳ).
- agent_appointment:    tiến trình bổ nhiệm/onboard của agent do admin chỉ định.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Integer, Numeric, String, Text,
)

from app.database import Base


class ContractFinancial(Base):
    """Dữ liệu tài chính bổ sung cho một hợp đồng (1-1 với contract.id).

    Không sửa bảng contract cũ; tách phần FYP chi tiết + ngày phát hành sang đây.
    """
    __tablename__ = "contract_financial"

    id = Column(Integer, primary_key=True)
    contract_id = Column("CONTRACT_ID", Integer, nullable=False, unique=True, index=True)

    # Người thụ hưởng thù lao (TVV) & tuyến trên trực tiếp tại thời điểm chốt
    agent_id = Column("AGENT_ID", Integer, index=True)
    direct_referrer_id = Column("DIRECT_REFERRER_ID", Integer, nullable=True)  # người kết nối trực tiếp

    # Ngày nghiệp vụ
    submit_date = Column("SUBMIT_DATE", Date, nullable=True)   # ngày nộp hồ sơ / hiệu lực
    issued_date = Column("ISSUED_DATE", Date, nullable=True)   # ngày phát hành hợp đồng

    # FYP tách theo loại
    fyp_main = Column("FYP_MAIN", Numeric(18, 2), default=0)              # FYP sản phẩm chính
    fyp_supplementary = Column("FYP_SUPPLEMENTARY", Numeric(18, 2), default=0)  # FYP bổ trợ
    conversion_factor = Column("CONVERSION_FACTOR", Numeric(6, 3), default=1)   # hệ số quy đổi
    fyp_converted = Column("FYP_CONVERTED", Numeric(18, 2), default=0)    # FYP quy đổi (đã tính)

    # Cấp bậc & trạng thái bổ nhiệm tại thời điểm chốt (đóng băng để tính đúng lịch sử)
    level_at_contract = Column("LEVEL_AT_CONTRACT", Integer, nullable=True)  # 1..6
    appointment_status = Column("APPOINTMENT_STATUS", String(20), nullable=True)  # PENDING/CONFIRMED/FAILED

    # Kỳ chi trả (YYYY-MM) để gom nhóm nhanh
    payroll_period = Column("PAYROLL_PERIOD", String(7), nullable=True, index=True)

    # ── Vòng đời hợp đồng (phục vụ tính K2 tự động) ──
    # Mặc định IN_FORCE khi hợp đồng được duyệt (auto duy trì nếu không động tới).
    # Chỉ đổi khi có biến cố: LAPSED (mất hiệu lực do ngừng đóng phí), CANCELLED (hủy),
    # SURRENDERED (khách chấm dứt sớm).
    lifecycle_status = Column("LIFECYCLE_STATUS", String(20), default="IN_FORCE")
    # Ngày hợp đồng bắt đầu tính hiệu lực (dùng làm mốc đo persistency)
    effective_date = Column("EFFECTIVE_DATE", Date, nullable=True)
    # Kỳ đóng phí gần nhất đã ghi nhận (YYYY-MM) — auto cập nhật khi có đóng phí
    last_paid_period = Column("LAST_PAID_PERIOD", String(7), nullable=True)
    # Ngày & lý do khi hợp đồng rời trạng thái IN_FORCE (biến cố)
    lapsed_date = Column("LAPSED_DATE", Date, nullable=True)
    lifecycle_note = Column("LIFECYCLE_NOTE", String(500), nullable=True)

    note = Column("NOTE", Text, nullable=True)
    created_datetime = Column("CREATED_DATETIME", DateTime, default=datetime.utcnow)
    updated_datetime = Column("UPDATED_DATETIME", DateTime, default=datetime.utcnow)


class CommissionRule(Base):
    """Cấu hình quy tắc chi trả. Giai đoạn này chỉ insert seed + FE hiển thị."""
    __tablename__ = "commission_rule"

    id = Column(Integer, primary_key=True)
    category = Column("CATEGORY", String(50), nullable=False, index=True)  # PERSONAL_COMMISSION, SXN_BONUS...
    rule_key = Column("RULE_KEY", String(100), nullable=False, unique=True)
    level = Column("LEVEL", Integer, nullable=True)          # 1..6 nếu áp theo cấp
    threshold = Column("THRESHOLD", Numeric(18, 2), nullable=True)  # ngưỡng FYP nếu có
    rate = Column("RATE", Numeric(6, 4), nullable=True)      # tỷ lệ (0.25 = 25%)
    note = Column("NOTE", String(500), nullable=True)
    is_active = Column("IS_ACTIVE", Boolean, default=True)
    created_datetime = Column("CREATED_DATETIME", DateTime, default=datetime.utcnow)


class PayrollResult(Base):
    """Kết quả tính thù lao đã lưu, ở mức từng hợp đồng trong một kỳ.

    Tổng hợp per-agent/per-period được suy ra bằng aggregate query từ bảng này.
    """
    __tablename__ = "payroll_result"

    id = Column(Integer, primary_key=True)
    payroll_period = Column("PAYROLL_PERIOD", String(7), nullable=False, index=True)  # YYYY-MM
    agent_id = Column("AGENT_ID", Integer, nullable=False, index=True)
    contract_id = Column("CONTRACT_ID", Integer, nullable=True, index=True)

    customer_name = Column("CUSTOMER_NAME", String(200), nullable=True)
    level = Column("LEVEL", Integer, nullable=True)
    appointment_status = Column("APPOINTMENT_STATUS", String(20), nullable=True)

    fyp_converted = Column("FYP_CONVERTED", Numeric(18, 2), default=0)

    personal_rate = Column("PERSONAL_RATE", Numeric(6, 4), default=0)
    personal_commission = Column("PERSONAL_COMMISSION", Numeric(18, 2), default=0)

    sxn_bonus = Column("SXN_BONUS", Numeric(18, 2), default=0)
    sxn_window = Column("SXN_WINDOW", String(20), nullable=True)
    monthly_bonus = Column("MONTHLY_BONUS", Numeric(18, 2), default=0)
    quarterly_bonus = Column("QUARTERLY_BONUS", Numeric(18, 2), default=0)
    yearly_bonus = Column("YEARLY_BONUS", Numeric(18, 2), default=0)
    recruitment_bonus = Column("RECRUITMENT_BONUS", Numeric(18, 2), default=0)

    gross_total = Column("GROSS_TOTAL", Numeric(18, 2), default=0)     # tổng trước thuế & quỹ
    pit_amount = Column("PIT_AMOUNT", Numeric(18, 2), default=0)       # thuế TNCN
    fund_amount = Column("FUND_AMOUNT", Numeric(18, 2), default=0)     # quỹ đảm bảo
    net_total = Column("NET_TOTAL", Numeric(18, 2), default=0)         # thực nhận

    note = Column("NOTE", String(500), nullable=True)
    calculated_at = Column("CALCULATED_AT", DateTime, default=datetime.utcnow)


class AgentMonthlySnapshot(Base):
    """Ảnh chụp hiệu suất của 1 agent trong 1 tháng (do job sinh định kỳ).

    Dùng cho các tiêu chí cần lịch sử liên tục (số tháng liên kề, K2) và để
    tăng tốc xét thăng cấp/bổ nhiệm thay vì tính lại từ đầu mỗi lần.
    """
    __tablename__ = "agent_monthly_snapshot"

    id = Column(Integer, primary_key=True)
    agent_id = Column("AGENT_ID", Integer, nullable=False, index=True)
    period = Column("PERIOD", String(7), nullable=False, index=True)  # YYYY-MM

    personal_fyp = Column("PERSONAL_FYP", Numeric(18, 2), default=0)
    team_fyp = Column("TEAM_FYP", Numeric(18, 2), default=0)          # tổng nhánh (mọi tầng)
    personal_contracts = Column("PERSONAL_CONTRACTS", Integer, default=0)

    total_members = Column("TOTAL_MEMBERS", Integer, default=0)       # tổng thành viên nhánh
    active_members = Column("ACTIVE_MEMBERS", Integer, default=0)     # có doanh số trong tháng
    fm_plus_members = Column("FM_PLUS_MEMBERS", Integer, default=0)   # thành viên cấp FM(3)+
    # Đếm thành viên theo cấp trong nhánh, JSON: {"1": n, "2": m, ...}
    downline_by_level = Column("DOWNLINE_BY_LEVEL", Text, nullable=True)
    # Đếm theo mã đại lý (agent có refer_code/mã BH chính thức)
    agent_codes = Column("AGENT_CODES", Integer, default=0)

    # % doanh số của nhánh F1 lớn nhất so với tổng đội (0..1)
    big_branch_pct = Column("BIG_BRANCH_PCT", Numeric(6, 4), default=0)
    # K2 persistency (0..1). Mặc định 1.0 khi chưa có nguồn dữ liệu thật.
    k2 = Column("K2", Numeric(6, 4), default=1)

    rank_at_snapshot = Column("RANK_AT_SNAPSHOT", String(20), nullable=True)
    created_datetime = Column("CREATED_DATETIME", DateTime, default=datetime.utcnow)


class IncomeLine(Base):
    """Một dòng thu nhập chi tiết của agent trong 1 kỳ — để truy vết nguồn gốc.

    Mỗi khoản thu nhập (thù lao cá nhân, thưởng, overriding...) là 1 dòng, ghi rõ
    đến từ hợp đồng nào và (với overriding) từ tuyến dưới nào.
    """
    __tablename__ = "income_line"

    id = Column(Integer, primary_key=True)
    payroll_period = Column("PAYROLL_PERIOD", String(7), nullable=False, index=True)
    agent_id = Column("AGENT_ID", Integer, nullable=False, index=True)  # người NHẬN thu nhập

    # Loại khoản: PERSONAL_COMMISSION, SXN_BONUS, MONTHLY_BONUS, QUARTERLY_BONUS,
    # YEARLY_BONUS, RECRUITMENT_BONUS, OVERRIDING
    category = Column("CATEGORY", String(40), nullable=False, index=True)
    amount = Column("AMOUNT", Numeric(18, 2), default=0)
    rate = Column("RATE", Numeric(6, 4), nullable=True)  # tỷ lệ áp dụng (nếu có)
    base_fyp = Column("BASE_FYP", Numeric(18, 2), nullable=True)  # FYP làm cơ sở tính

    # Nguồn gốc để truy vết
    source_contract_id = Column("SOURCE_CONTRACT_ID", Integer, nullable=True, index=True)
    source_customer_name = Column("SOURCE_CUSTOMER_NAME", String(200), nullable=True)
    # Với OVERRIDING: agent tuyến dưới tạo ra doanh số (người bị "ăn" chênh lệch)
    source_agent_id = Column("SOURCE_AGENT_ID", Integer, nullable=True, index=True)
    source_agent_name = Column("SOURCE_AGENT_NAME", String(200), nullable=True)
    source_level = Column("SOURCE_LEVEL", Integer, nullable=True)  # cấp tuyến dưới

    description = Column("DESCRIPTION", String(500), nullable=True)
    created_datetime = Column("CREATED_DATETIME", DateTime, default=datetime.utcnow)


class AgentAppointment(Base):
    """Tiến trình bổ nhiệm/onboard: admin chỉ định cấp thử thách cho người mới.

    Sau khi phê duyệt, hệ thống theo dõi perf để agent biết cần làm gì đạt cấp.
    """
    __tablename__ = "agent_appointment"

    id = Column(Integer, primary_key=True)
    agent_id = Column("AGENT_ID", Integer, nullable=False, index=True)

    target_level = Column("TARGET_LEVEL", Integer, nullable=False)  # cấp thử thách được giao (1..6)
    status = Column("STATUS", String(20), default="PENDING")  # PENDING/CONFIRMED/FAILED

    assigned_by = Column("ASSIGNED_BY", String(100), nullable=True)   # username admin
    assigned_at = Column("ASSIGNED_AT", DateTime, default=datetime.utcnow)
    challenge_start = Column("CHALLENGE_START", Date, nullable=True)
    challenge_end = Column("CHALLENGE_END", Date, nullable=True)   # hạn hoàn tất thử thách

    confirmed_at = Column("CONFIRMED_AT", DateTime, nullable=True)
    # Cấp được job tự động assign sau khi hết hạn thử thách (xét từ target xuống 1)
    achieved_level = Column("ACHIEVED_LEVEL", Integer, nullable=True)
    # Cờ đánh dấu job đã xử lý bổ nhiệm này khi hết hạn (tránh xét lại nhiều lần)
    is_evaluated = Column("IS_EVALUATED", Boolean, default=False)
    note = Column("NOTE", String(500), nullable=True)
    created_datetime = Column("CREATED_DATETIME", DateTime, default=datetime.utcnow)
