"""Pydantic schemas cho module Lương-Thưởng-Hoa hồng & Bổ nhiệm."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


# ─── Cấu hình quy tắc (read-only) ──────────────────────────────────────────────

class CommissionRuleItem(BaseModel):
    id: int
    category: str
    rule_key: str
    level: Optional[int] = None
    threshold: Optional[float] = None
    rate: Optional[float] = None
    note: Optional[str] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class CommissionConfigResponse(BaseModel):
    """Toàn bộ cấu hình gom nhóm theo category để FE render bảng."""
    categories: dict  # category -> list[CommissionRuleItem]
    rank_levels: dict  # {"AC": 1, ...}


# ─── Dữ liệu tài chính hợp đồng ────────────────────────────────────────────────

class ContractFinancialRequest(BaseModel):
    contract_id: int
    direct_referrer_id: Optional[int] = None
    submit_date: Optional[str] = None   # dd/mm/yyyy
    issued_date: Optional[str] = None   # dd/mm/yyyy
    fyp_main: Optional[float] = 0
    fyp_supplementary: Optional[float] = 0
    conversion_factor: Optional[float] = 1
    level_at_contract: Optional[int] = None
    appointment_status: Optional[str] = None
    note: Optional[str] = None


class ContractFinancialResponse(BaseModel):
    id: int
    contract_id: int
    agent_id: Optional[int] = None
    direct_referrer_id: Optional[int] = None
    submit_date: Optional[str] = None
    issued_date: Optional[str] = None
    fyp_main: Optional[float] = None
    fyp_supplementary: Optional[float] = None
    conversion_factor: Optional[float] = None
    fyp_converted: Optional[float] = None
    level_at_contract: Optional[int] = None
    appointment_status: Optional[str] = None
    payroll_period: Optional[str] = None
    lifecycle_status: Optional[str] = None
    effective_date: Optional[str] = None
    last_paid_period: Optional[str] = None
    lapsed_date: Optional[str] = None
    lifecycle_note: Optional[str] = None
    note: Optional[str] = None


class UpdateLifecycleRequest(BaseModel):
    # LAPSED / CANCELLED / SURRENDERED / IN_FORCE (phục hồi)
    lifecycle_status: str
    lapsed_date: Optional[str] = None  # dd/mm/yyyy - ngày xảy ra biến cố
    note: Optional[str] = None


class ContractFinancialListItem(BaseModel):
    """Dòng danh sách hợp đồng (đã có dữ liệu tài chính) cho admin quản lý vòng đời."""
    contract_id: int
    agent_id: Optional[int] = None
    agent_name: Optional[str] = None
    customer_name: Optional[str] = None
    fyp_converted: Optional[float] = None
    issued_date: Optional[str] = None
    effective_date: Optional[str] = None
    lifecycle_status: Optional[str] = None
    lapsed_date: Optional[str] = None
    lifecycle_note: Optional[str] = None
    payroll_period: Optional[str] = None


# ─── Kết quả tính thù lao ──────────────────────────────────────────────────────

class PayrollResultItem(BaseModel):
    id: Optional[int] = None
    payroll_period: str
    agent_id: int
    contract_id: Optional[int] = None
    customer_name: Optional[str] = None
    level: Optional[int] = None
    appointment_status: Optional[str] = None
    fyp_converted: float = 0
    personal_rate: float = 0
    personal_commission: float = 0
    sxn_bonus: float = 0
    sxn_window: Optional[str] = None
    monthly_bonus: float = 0
    quarterly_bonus: float = 0
    yearly_bonus: float = 0
    recruitment_bonus: float = 0
    gross_total: float = 0
    pit_amount: float = 0
    fund_amount: float = 0
    net_total: float = 0
    note: Optional[str] = None

    class Config:
        from_attributes = True


class IncomeLineItem(BaseModel):
    """Một dòng thu nhập chi tiết, có truy vết nguồn gốc."""
    id: int
    payroll_period: str
    agent_id: int
    category: str
    amount: float = 0
    rate: Optional[float] = None
    base_fyp: Optional[float] = None
    source_contract_id: Optional[int] = None
    source_customer_name: Optional[str] = None
    source_agent_id: Optional[int] = None
    source_agent_name: Optional[str] = None
    source_level: Optional[int] = None
    description: Optional[str] = None

    class Config:
        from_attributes = True


class IncomeBreakdownResponse(BaseModel):
    """Bảng thu nhập chi tiết của 1 agent trong 1 kỳ, gom theo loại khoản."""
    payroll_period: str
    agent_id: int
    agent_name: Optional[str] = None
    total_amount: float = 0
    by_category: dict = {}          # category -> tổng tiền
    lines: List[IncomeLineItem] = []


class PayrollSummaryResponse(BaseModel):
    """Tổng hợp thù lao 1 agent trong 1 kỳ + danh sách dòng chi tiết."""
    payroll_period: str
    agent_id: int
    agent_name: Optional[str] = None
    total_fyp_converted: float = 0
    total_personal_commission: float = 0
    total_bonus: float = 0
    total_gross: float = 0
    total_pit: float = 0
    total_fund: float = 0
    total_net: float = 0
    lines: List[PayrollResultItem] = []


class CalculatePayrollRequest(BaseModel):
    payroll_period: str  # YYYY-MM
    agent_id: Optional[int] = None  # None = tính toàn bộ agent trong kỳ


# ─── Perf & thăng cấp ──────────────────────────────────────────────────────────

class CriterionDetail(BaseModel):
    """Chi tiết 1 tiêu chí: giá trị hiện tại / yêu cầu / đạt hay chưa."""
    key: str                    # fyp, members, fm_plus, big_branch_pct, k2, consecutive_months...
    label: Optional[str] = None
    current: float = 0
    required: float = 0
    met: bool = False
    is_max: bool = False        # True nếu là ràng buộc "không vượt quá" (vd nhánh lớn <= 60%)


class PromotionCriteriaResponse(BaseModel):
    current_level: Optional[int] = None
    current_rank: Optional[str] = None
    target_level: Optional[int] = None
    target_rank: Optional[str] = None
    eligible: bool = False
    message: Optional[str] = None
    # Danh sách chi tiết từng tiêu chí (đầy đủ theo bảng chính sách)
    criteria: List[CriterionDetail] = []

    # Giữ lại vài field cũ để tương thích ngược (FE cũ có thể vẫn đọc)
    personal_fyp_current: float = 0
    personal_fyp_required: float = 0
    personal_fyp_met: bool = False
    team_fyp_current: float = 0
    team_fyp_required: float = 0
    team_fyp_met: bool = False
    active_members_current: int = 0
    active_members_required: int = 0
    active_members_met: bool = False


class PerformanceResponse(BaseModel):
    agent_id: int
    agent_name: Optional[str] = None
    period: str  # YYYY-MM hoặc YYYY
    personal_fyp: float = 0
    personal_contracts: int = 0
    team_fyp: float = 0
    team_members: int = 0
    active_team_members: int = 0
    appointment_status: Optional[str] = None
    promotion: Optional[PromotionCriteriaResponse] = None


# ─── Bổ nhiệm / Onboard ────────────────────────────────────────────────────────

class CreateAppointmentRequest(BaseModel):
    agent_id: int
    target_level: int
    challenge_start: Optional[str] = None   # dd/mm/yyyy - mặc định hôm nay nếu bỏ trống
    # Thời hạn thử thách: 30_DAYS / 2_MONTHS / 3_MONTHS / 6_MONTHS / 12_MONTHS
    challenge_duration: Optional[str] = None
    # Cho phép nhập ngày kết thúc trực tiếp (ưu tiên challenge_duration nếu có)
    challenge_end: Optional[str] = None
    note: Optional[str] = None


class UpdateAppointmentStatusRequest(BaseModel):
    status: str  # CONFIRMED / FAILED / PENDING
    note: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: int
    agent_id: int
    agent_name: Optional[str] = None
    target_level: int
    target_rank: Optional[str] = None
    status: str
    assigned_by: Optional[str] = None
    assigned_at: Optional[datetime] = None
    challenge_start: Optional[str] = None
    challenge_end: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    achieved_level: Optional[int] = None   # cấp được job assign sau khi hết hạn
    achieved_rank: Optional[str] = None
    note: Optional[str] = None

    class Config:
        from_attributes = True


class ChallengeDurationOption(BaseModel):
    key: str
    label: str


class ScanDueResponse(BaseModel):
    message: str
    evaluated: int          # số bổ nhiệm hết hạn đã xét
    assigned: int           # số agent được assign lại cấp
    details: list = []
