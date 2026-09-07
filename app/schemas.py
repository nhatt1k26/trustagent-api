"""Pydantic schemas for request/response validation."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    code: str = "200"
    message: str = "Success"


class AgentRegisterRequest(BaseModel):
    fullname: str
    gender: Optional[str] = None
    email: Optional[str] = None
    phone: str
    birthday: Optional[str] = None  # dd/mm/yyyy string from FE
    refer_code: Optional[str] = None
    type: Optional[str] = "CTV"
    has_insurance_job: Optional[bool] = False
    has_insurance_code: Optional[bool] = False
    insurance_company: Optional[str] = None


class AgentRegisterResponse(BaseModel):
    id: int
    fullname: str
    phone: str
    email: Optional[str] = None
    status: str
    register_code: Optional[str] = None

    class Config:
        from_attributes = True


class AgentDetailResponse(BaseModel):
    id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    create_datetime: Optional[datetime] = None

    class Config:
        from_attributes = True


class LeadCreateRequest(BaseModel):
    fullname: str
    phone: Optional[str] = None
    email: Optional[str] = None
    gender: Optional[str] = None
    birthday: Optional[date] = None
    source: Optional[str] = None
    lead_group: Optional[str] = None
    address: Optional[str] = None
    province: Optional[str] = None
    district: Optional[str] = None
    occupation: Optional[str] = None
    income_range: Optional[str] = None
    marital_status: Optional[str] = None
    customer_type: Optional[str] = "individual"
    priority: Optional[str] = "MEDIUM"
    notes: Optional[str] = None


class LeadResponse(BaseModel):
    id: int
    fullname: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    gender: Optional[str] = None
    birthday: Optional[date] = None
    address: Optional[str] = None
    stage: Optional[str] = None
    lead_group: Optional[str] = None
    source: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[str] = None
    created_datetime: Optional[datetime] = None

    class Config:
        from_attributes = True


class LeadStatResponse(BaseModel):
    total: int = 0
    new: int = 0
    contacted: int = 0
    negotiating: int = 0
    closed: int = 0
    lost: int = 0


class AppointmentRequest(BaseModel):
    id: Optional[int] = None
    lead_id: Optional[int] = None
    appointment_date: Optional[str] = None  # dd/mm/yyyy
    appointment_time: Optional[str] = None  # HH:mm
    appointment_location: Optional[str] = None
    appointment_goal: Optional[str] = None
    meeting_type: Optional[str] = None
    product: Optional[str] = None
    link: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = "upcoming"


class AppointmentResponse(BaseModel):
    id: int
    lead_id: Optional[int] = None
    lead_name: Optional[str] = None
    lead_phone: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    appointment_location: Optional[str] = None
    appointment_goal: Optional[str] = None
    meeting_type: Optional[str] = None
    product: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    created_datetime: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserProfileResponse(BaseModel):
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    birthday: Optional[str] = None
    address: Optional[str] = None
    role: str
    created_at: Optional[str] = None
    rank: Optional[str] = None        # mã cấp bậc (None = chưa xếp hạng)
    rank_label: Optional[str] = None  # nhãn tiếng Việt


# ─── Agent Rank (cấp bậc nhân viên) ─────────────────────────────────────────────

class UpdateAgentRankRequest(BaseModel):
    rank: Optional[str] = None  # AC/FC/FM/FD/SD/ED hoặc "UNRANKED"/null


class AdminAgentRankItem(BaseModel):
    id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    refer_code: Optional[str] = None
    rank: Optional[str] = None
    rank_label: Optional[str] = None
    manage_id: Optional[int] = None       # id leader đang quản lý (AgentDetail.id)
    leader_name: Optional[str] = None      # tên leader để hiển thị


class UpdateAgentLeaderRequest(BaseModel):
    # id của leader (AgentDetail.id). null = gỡ leader (TVV nguồn).
    leader_id: Optional[int] = None


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    birthday: Optional[str] = None  # dd/mm/yyyy
    address: Optional[str] = None


# ─── Customer Consultation (KH đăng ký tư vấn) ─────────────────────────────────

class CustomerConsultationCreateRequest(BaseModel):
    fullname: str
    phone: str
    email: Optional[str] = None
    gender: Optional[str] = None
    birthday: Optional[str] = None  # dd/mm/yyyy
    address: Optional[str] = None
    product_interest: Optional[str] = None
    notes: Optional[str] = None


class CustomerConsultationResponse(BaseModel):
    id: int
    fullname: str
    phone: str
    email: Optional[str] = None
    gender: Optional[str] = None
    birthday: Optional[date] = None
    address: Optional[str] = None
    product_interest: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = "new"
    assigned_agent_id: Optional[int] = None
    assigned_agent_username: Optional[str] = None
    assigned_at: Optional[datetime] = None
    created_datetime: Optional[datetime] = None

    class Config:
        from_attributes = True


class AssignCustomerConsultationRequest(BaseModel):
    agent_username: str


# ─── Contract (Hợp đồng) ────────────────────────────────────────────────────────

class ContractDocumentInfo(BaseModel):
    id: int
    filename: Optional[str] = None
    mimetype: Optional[str] = None

    class Config:
        from_attributes = True


class ContractResponse(BaseModel):
    id: int
    agent_id: Optional[int] = None
    lead_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    main_package: Optional[str] = None
    supplementary_packages: Optional[List[str]] = None
    premium: Optional[float] = None
    insurance_term: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    documents: Optional[List[ContractDocumentInfo]] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    admin_notes: Optional[str] = None
    created_datetime: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    agent_name: Optional[str] = None
    agent_code: Optional[str] = None
