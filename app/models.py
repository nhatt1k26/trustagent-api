"""SQLAlchemy models matching existing DB tables in clientcrm."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, Enum, Integer,
    LargeBinary, Numeric, String, Text,
)

from app.database import Base


# ─── Auth ───────────────────────────────────────────────────────────────────────

class AgentCredential(Base):
    __tablename__ = "AGENT_CREDENTIAL"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)


# ─── Agent ──────────────────────────────────────────────────────────────────────

class AgentDetail(Base):
    __tablename__ = "AGENT_DETAIL"

    id = Column(Integer, primary_key=True)
    username = Column("username", String(100), unique=True)
    full_name = Column("FULL_NAME", String(200))
    email = Column("email", String(150))
    phone = Column("phone", String(20))
    gender = Column("GENDER", String(10))
    birthday = Column("BIRTHDAY", Date)
    address = Column("ADDRESS", String(500))
    avatar = Column("avatar", LargeBinary)
    avatar_mimetype = Column("avatar_mimetype", String(50))
    refer_code = Column("refer_code", String(50), unique=True)
    # Cấp bậc nhân viên. NULL = chưa xếp hạng (UNRANKED).
    # 6 cấp: AC, FC, FM, FD, SD, ED. Xem app/rank.py để biết nhãn tiếng Việt.
    rank = Column("rank", String(20), nullable=True)
    create_datetime = Column("create_datetime", DateTime)
    delete_flag = Column("delete_flag", Boolean, default=False)


# ─── Agent Register ─────────────────────────────────────────────────────────────

class UserRegister(Base):
    __tablename__ = "agent_register"

    id = Column(Integer, primary_key=True)
    fullname = Column("FULLNAME", String(200))
    gender = Column("GENDER", String(10))
    email = Column("EMAIL", String(150))
    phone = Column("PHONE", String(20))
    birthday = Column("BIRTHDAY", Date)
    refer_code = Column("REFER_CODE", String(50))
    register_code = Column("register_code", String(100))
    type = Column("TYPE", String(50))
    role = Column("role", String(50))
    status = Column("STATUS", String(50))
    reason = Column("REASON", String(500))
    profile_detail = Column("PROFILE_DETAIL", Text)
    start_date = Column("START_DATE", Date)
    manage_id = Column("manage_id", Integer)
    has_insurance_job = Column("has_insurance_job", Boolean)
    has_insurance_code = Column("has_insurance_code", Boolean)
    insurance_company = Column("insurance_company", String(200))
    created_datetime = Column("CREATED_DATETIME", DateTime)


# ─── Lead ───────────────────────────────────────────────────────────────────────

class LeadInfo(Base):
    __tablename__ = "LEAD_INFO"

    id = Column(Integer, primary_key=True)
    fullname = Column("FULLNAME", String(200))
    gender = Column("GENDER", String(10))
    agent_id = Column("AGENT_ID", Integer)
    refer_code = Column("REFER_CODE", String(50))
    source = Column("SOURCE", String(100))
    lead_group = Column("LEAD_GROUP", String(50))
    stage = Column("STAGE", String(50))
    birthday = Column("BIRTHDAY", Date)
    national_id = Column("NATIONAL_ID", String(30))
    tax_code = Column("TAX_CODE", String(30))
    phone = Column("PHONE", String(20))
    email = Column("EMAIL", String(150))
    address = Column("ADDRESS", String(500))
    province = Column("PROVINCE", String(100))
    district = Column("DISTRICT", String(100))
    occupation = Column("OCCUPATION", String(200))
    income_range = Column("INCOME_RANGE", String(50))
    marital_status = Column("MARITAL_STATUS", String(50))
    customer_type = Column("CUSTOMER_TYPE", String(50))
    company_name = Column("COMPANY_NAME", String(200))
    representative_name = Column("REPRESENTATIVE_NAME", String(200))
    priority = Column("PRIORITY", String(20))
    notes = Column("NOTES", Text)
    created_datetime = Column("CREATED_DATETIME", DateTime)


# ─── Appointment ────────────────────────────────────────────────────────────────

class Appointment(Base):
    __tablename__ = "appointment"

    id = Column(Integer, primary_key=True)
    agent_id = Column("AGENT_ID", Integer)
    lead_id = Column("LEAD_ID", Integer)
    has_referral = Column("HAS_REFERRAL", Boolean)
    appointment_date = Column("APPOINTMENT_DATE", DateTime)
    appointment_time = Column("APPOINTMENT_TIME", String(10))
    appointment_location = Column("APPOINTMENT_LOCATION", String(500))
    appointment_goal = Column("APPOINTMENT_GOAL", String(500))
    meeting_type = Column("MEETING_TYPE", String(50))
    product = Column("PRODUCT", String(200))
    link = Column("LINK", String(500))
    notes = Column("NOTES", Text)
    status = Column("STATUS", String(20), default="upcoming")
    is_notified = Column("IS_NOTIFIED", Boolean)
    notified_24h = Column("NOTIFIED_24H", Boolean, default=False)  # đã nhắc mốc 24h
    notified_1h = Column("NOTIFIED_1H", Boolean, default=False)    # đã nhắc mốc 1h
    created_datetime = Column("CREATED_DATETIME", DateTime)


# ─── Customer Consultation (Đăng ký tư vấn của Khách hàng) ──────────────────────

class CustomerConsultation(Base):
    __tablename__ = "customer_consultation"

    id = Column(Integer, primary_key=True)
    fullname = Column("fullname", String(200), nullable=False)
    phone = Column("phone", String(20), nullable=False)
    email = Column("email", String(150))
    gender = Column("gender", String(10))
    birthday = Column("birthday", Date)
    address = Column("address", String(500))
    product_interest = Column("product_interest", String(200))
    notes = Column("notes", Text)
    status = Column("status", String(50), default="new")  # new, assigned, contacted, closed
    assigned_agent_id = Column("assigned_agent_id", Integer)
    assigned_agent_username = Column("assigned_agent_username", String(100))
    assigned_at = Column("assigned_at", DateTime)
    created_datetime = Column("created_datetime", DateTime, default=datetime.utcnow)


# ─── Contract (Hợp đồng bảo hiểm) ───────────────────────────────────────────────

class Contract(Base):
    __tablename__ = "contract"

    id = Column(Integer, primary_key=True)
    agent_id = Column("AGENT_ID", Integer)  # TVV tạo yêu cầu chốt HĐ
    lead_id = Column("LEAD_ID", Integer, nullable=True)  # Khách hàng (LEAD_INFO)
    customer_name = Column("CUSTOMER_NAME", String(200))  # Denormalized để hiển thị
    customer_phone = Column("CUSTOMER_PHONE", String(20))

    main_package = Column("MAIN_PACKAGE", String(300))  # Gói bảo hiểm chính
    supplementary_packages = Column("SUPPLEMENTARY_PACKAGES", Text)  # JSON array gói bổ trợ
    premium = Column("PREMIUM", Numeric(18, 2))  # Phí bảo hiểm / doanh số (FYP)
    insurance_term = Column("INSURANCE_TERM", String(50))  # Thời hạn bảo hiểm, vd "20 năm"
    start_date = Column("START_DATE", Date)
    end_date = Column("END_DATE", Date)

    document_urls = Column("DOCUMENT_URLS", Text)  # JSON array tên file hồ sơ đã tải lên
    notes = Column("NOTES", Text)

    status = Column("STATUS", String(50), default="SUBMITTED")  # SUBMITTED, APPROVED, REJECTED
    admin_notes = Column("ADMIN_NOTES", String(500))  # Lý do từ chối / ghi chú đối soát

    created_datetime = Column("CREATED_DATETIME", DateTime, default=datetime.utcnow)
    submitted_at = Column("SUBMITTED_AT", DateTime)
    reviewed_at = Column("REVIEWED_AT", DateTime)


class ContractDocument(Base):
    __tablename__ = "contract_document"

    id = Column(Integer, primary_key=True)
    contract_id = Column("CONTRACT_ID", Integer, nullable=False)
    filename = Column("FILENAME", String(255))
    mimetype = Column("MIMETYPE", String(100))
    content = Column("CONTENT", LargeBinary)
    created_datetime = Column("CREATED_DATETIME", DateTime, default=datetime.utcnow)


# ─── Notification (Thông báo) ───────────────────────────────────────────────────

class Notification(Base):
    __tablename__ = "notification"

    id = Column(Integer, primary_key=True)
    recipient_agent_id = Column("RECIPIENT_AGENT_ID", Integer, nullable=False)
    type = Column("TYPE", String(50))  # xem app/notification_service.py
    title = Column("TITLE", String(255))
    message = Column("MESSAGE", String(1000))
    link_tab = Column("LINK_TAB", String(50))  # tab FE điều hướng tới khi click
    ref_id = Column("REF_ID", Integer)  # id đối tượng liên quan (contract/lead/appointment...)
    is_read = Column("IS_READ", Boolean, default=False)
    created_datetime = Column("CREATED_DATETIME", DateTime, default=datetime.utcnow)


# Cờ nhắc lịch hẹn theo 2 mốc (24h / 1h). Thêm ở đây để tránh sửa cấu trúc Appointment ở trên.
# (SQLAlchemy sẽ tự thêm cột khi create_all nếu bảng chưa có; với bảng cũ cần migration bổ sung.)
