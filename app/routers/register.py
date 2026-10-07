"""Agent registration - public endpoint + welcome email."""

import uuid
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.email_service import send_onboard_email
from app.models import AgentDetail, UserRegister
from app.notification_service import create_notification, notify_all_admins
from app.rank import normalize_rank
from app.schemas import AgentRegisterRequest, AgentRegisterResponse, ApiResponse

router = APIRouter(prefix="/api/v1", tags=["register"])


@router.post("/agent-register", response_model=AgentRegisterResponse, status_code=201)
def register_agent(req: AgentRegisterRequest, db: Session = Depends(get_db)):
    # Validate refer code if provided
    if req.refer_code:
        valid = db.query(AgentDetail).filter_by(refer_code=req.refer_code).first()
        if not valid:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mã giới thiệu không hợp lệ")

    # Check duplicate phone
    existing = db.query(UserRegister).filter_by(phone=req.phone, status="PENDING").first()
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Số điện thoại đã đăng ký, đang chờ duyệt")

    register_code = f"TA-{uuid.uuid4().hex[:6].upper()}"

    # Parse birthday from dd/mm/yyyy string
    parsed_birthday = None
    if req.birthday:
        try:
            parsed_birthday = datetime.strptime(req.birthday, "%d/%m/%Y").date()
        except ValueError:
            pass

    record = UserRegister(
        fullname=req.fullname,
        gender=req.gender,
        email=req.email,
        phone=req.phone,
        birthday=parsed_birthday,
        refer_code=req.refer_code,
        register_code=register_code,
        type=req.type,
        status="PENDING",
        has_insurance_job=req.has_insurance_job,
        has_insurance_code=req.has_insurance_code,
        insurance_company=req.insurance_company,
        created_datetime=datetime.utcnow(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    # #8: Thông báo cho Admin có CTV đăng ký mới
    notify_all_admins(
        db,
        type="REGISTRATION_NEW",
        title="Đăng ký CTV mới",
        message=f"{record.fullname}"
                + (f" ({record.phone})" if record.phone else "")
                + " vừa gửi đăng ký làm CTV, cần phê duyệt.",
        link_tab="new-ctv",
        ref_id=record.id,
    )

    # Send onboarding email in background thread (non-blocking)
    if req.email:
        threading.Thread(
            target=send_onboard_email,
            args=(req.email, req.fullname),
            daemon=True,
        ).start()

    return AgentRegisterResponse(
        id=record.id,
        fullname=record.fullname,
        phone=record.phone,
        email=record.email,
        status=record.status,
        register_code=register_code,
    )


@router.get("/agent-register/get-code-status/{code}")
def validate_refer_code(code: str, db: Session = Depends(get_db)):
    """Validate refer code and return the sponsor agent's name."""
    agent = db.query(AgentDetail).filter_by(refer_code=code).first()
    if agent:
        return {
            "code": "200",
            "message": "Code is valid",
            "sponsor_name": agent.full_name,
            "sponsor_code": agent.refer_code,
        }
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mã giới thiệu không hợp lệ")


# ─── Admin endpoints for managing CTV registrations ─────────────────────────────

from typing import List, Optional
from fastapi import Query
from app.deps import get_current_username
from app.models import AgentCredential


@router.get("/admin/agent-register/list")
def admin_list_registrations(
    status_filter: Optional[str] = Query(None, alias="status"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin - lấy danh sách CTV đăng ký."""
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential or credential.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")

    query = db.query(UserRegister).order_by(UserRegister.created_datetime.desc())
    if status_filter:
        query = query.filter(UserRegister.status == status_filter.upper())

    records = query.all()

    result = []
    for r in records:
        # Tài khoản agent của chính CTV này (khớp qua email) -> lấy mã giới thiệu của họ.
        own_agent = None
        if r.email:
            own_agent = db.query(AgentDetail).filter_by(email=r.email).first()

        # Người giới thiệu: r.refer_code là mã GT của người mời -> tìm agent sở hữu mã đó.
        referrer_name = None
        if r.refer_code:
            referrer = db.query(AgentDetail).filter_by(refer_code=r.refer_code).first()
            if referrer:
                referrer_name = referrer.full_name or referrer.username

        result.append({
            "id": r.id,
            "fullname": r.fullname,
            "gender": r.gender,
            "email": r.email,
            "phone": r.phone,
            "birthday": r.birthday.isoformat() if r.birthday else None,
            "refer_code": r.refer_code,          # mã người GIỚI THIỆU CTV này
            "referrer_name": referrer_name,      # họ tên người giới thiệu
            "agent_refer_code": own_agent.refer_code if own_agent else None,  # mã GT của chính CTV (sau khi có TK)
            "register_code": r.register_code,
            "type": r.type,
            "status": r.status,
            "reason": r.reason,
            "manage_id": r.manage_id,
            "has_insurance_job": r.has_insurance_job,
            "has_insurance_code": r.has_insurance_code,
            "insurance_company": r.insurance_company,
            "profile_detail": r.profile_detail,
            "created_datetime": r.created_datetime.isoformat() if r.created_datetime else None,
        })
    return result


@router.put("/admin/agent-register/{reg_id}/approve")
def admin_approve_registration(
    reg_id: int,
    leader_username: Optional[str] = Query(None),
    rank: Optional[str] = Query(None),  # cấp bậc gán khi duyệt (AC/FC/.../ED hoặc UNRANKED)
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin - phê duyệt CTV và gán leader (tuỳ chọn). Không gán = TVV nguồn."""
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential or credential.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")

    record = db.query(UserRegister).filter_by(id=reg_id).first()
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Đăng ký không tồn tại.")

    # Gán leader nếu có, còn không thì TVV nguồn (manage_id = None)
    if leader_username:
        leader = db.query(AgentDetail).filter_by(username=leader_username).first()
        if not leader:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Leader không tồn tại.")
        record.manage_id = leader.id
        leader_label = leader.full_name or leader_username
    else:
        record.manage_id = None
        leader_label = "Không gán (TVV nguồn)"

    record.status = "APPROVED"
    record.start_date = datetime.utcnow().date()

    # Gán cấp bậc nếu Admin chọn (chỉ áp dụng khi applicant đã có tài khoản agent)
    normalized_rank = normalize_rank(rank) if rank else None

    # #6: Thông báo cho chính CTV (nếu đã có tài khoản, khớp qua email)
    if record.email:
        applicant = db.query(AgentDetail).filter_by(email=record.email).first()
        if applicant:
            if rank is not None:
                applicant.rank = normalized_rank
            create_notification(
                db,
                recipient_agent_id=applicant.id,
                type="REGISTRATION_APPROVED",
                title="Hồ sơ đã được phê duyệt",
                message="Hồ sơ CTV của bạn đã được Admin phê duyệt. Chào mừng bạn gia nhập TrustAgent!",
                link_tab="collaborators",
                ref_id=record.id,
                commit=False,
            )

    # #2: Thông báo cho Leader được gán quản lý CTV này
    if leader_username:
        leader = db.query(AgentDetail).filter_by(username=leader_username).first()
        if leader:
            create_notification(
                db,
                recipient_agent_id=leader.id,
                type="CTV_ASSIGNED",
                title="CTV tuyến dưới mới",
                message=f"CTV {record.fullname} vừa được gán vào đội nhóm do bạn quản lý.",
                link_tab="team-tree",
                ref_id=record.id,
                commit=False,
            )

    db.commit()

    return {"message": f"Đã phê duyệt CTV {record.fullname}. Leader: {leader_label}."}


@router.put("/admin/agent-register/{reg_id}/reject")
def admin_reject_registration(
    reg_id: int,
    reason: str = Query(""),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin - từ chối đăng ký CTV."""
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential or credential.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")

    record = db.query(UserRegister).filter_by(id=reg_id).first()
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Đăng ký không tồn tại.")

    record.status = "REJECTED"
    record.reason = reason

    # #6: Thông báo cho chính CTV (nếu đã có tài khoản)
    if record.email:
        applicant = db.query(AgentDetail).filter_by(email=record.email).first()
        if applicant:
            create_notification(
                db,
                recipient_agent_id=applicant.id,
                type="REGISTRATION_REJECTED",
                title="Hồ sơ bị từ chối",
                message="Hồ sơ CTV của bạn chưa được duyệt."
                        + (f" Lý do: {reason}" if reason else " Vui lòng liên hệ Admin để biết thêm."),
                link_tab="collaborators",
                ref_id=record.id,
                commit=False,
            )

    db.commit()

    return {"message": f"Đã từ chối đăng ký của {record.fullname}."}


@router.get("/admin/agent-register/leaders")
def admin_get_leaders(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Lấy danh sách agents có thể làm leader."""
    # TODO(tạm thời): bỏ check role do role trong DB chưa thống nhất.
    credential = db.query(AgentCredential).filter_by(username=username).first()
    agents = db.query(AgentDetail).filter(AgentDetail.delete_flag == False).all()
    return [
        {"id": a.id, "username": a.username, "full_name": a.full_name, "phone": a.phone}
        for a in agents
    ]


# ─── Onboarding endpoint (TVV submits full profile after creating account) ──────

import json
from pydantic import BaseModel


class OnboardingRequest(BaseModel):
    full_name: str
    gender: Optional[str] = None
    birthday: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    id_number: Optional[str] = None
    issue_date: Optional[str] = None
    issue_place: Optional[str] = None
    temporary_address: Optional[str] = None
    address: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    tax_id: Optional[str] = None
    has_insurance_code: Optional[bool] = False
    insurance_company: Optional[str] = None
    # Ảnh CCCD dạng base64 data URL (vd: "data:image/jpeg;base64,....")
    id_front: Optional[str] = None
    id_back: Optional[str] = None


# Giới hạn dung lượng mỗi ảnh CCCD (base64) ~ 3MB để tránh phình DB.
MAX_ID_IMAGE_LEN = 3 * 1024 * 1024


def _validate_id_image(value: Optional[str], label: str) -> Optional[str]:
    """Kiểm tra ảnh base64 hợp lệ (data URL ảnh) và không quá lớn."""
    if not value:
        return None
    if not value.startswith("data:image/"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{label} không hợp lệ. Vui lòng chọn file ảnh.",
        )
    if len(value) > MAX_ID_IMAGE_LEN:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"{label} quá lớn. Vui lòng chọn ảnh nhỏ hơn.",
        )
    return value


@router.post("/agent-register/onboarding")
def submit_onboarding(
    req: OnboardingRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """TVV submits profile detail after creating account. Updates agent_register record."""
    # Find the agent's email
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    # Validate ảnh CCCD (base64) trước khi lưu
    id_front = _validate_id_image(req.id_front, "Ảnh mặt trước CCCD")
    id_back = _validate_id_image(req.id_back, "Ảnh mặt sau CCCD")

    # Find linked agent_register record by email
    register_record = db.query(UserRegister).filter_by(email=agent.email).first()
    if not register_record:
        # No prior form submission - create one
        register_record = UserRegister(
            fullname=req.full_name,
            gender=req.gender,
            email=agent.email,
            phone=req.phone or agent.phone,
            status="ONBOARDING",
            type="TVV",
            created_datetime=datetime.utcnow(),
        )
        db.add(register_record)

    # Store the full profile detail as JSON in profile_detail column
    profile_data = {
        "fullName": req.full_name,
        "gender": req.gender,
        "birthday": req.birthday,
        "phone": req.phone,
        "email": req.email,
        "idNumber": req.id_number,
        "issueDate": req.issue_date,
        "issuePlace": req.issue_place,
        "temporaryAddress": req.temporary_address,
        "address": req.address,
        "bankName": req.bank_name,
        "accountNumber": req.account_number,
        "taxId": req.tax_id,
        "hasInsuranceCode": req.has_insurance_code,
        "insuranceCompany": req.insurance_company,
        # Key khớp với AdminNewCTV.tsx (idFrontUrl / idBackUrl)
        "idFrontUrl": id_front,
        "idBackUrl": id_back,
    }
    register_record.profile_detail = json.dumps(profile_data, ensure_ascii=False)
    register_record.status = "PROFILE_VERIFYING"
    register_record.fullname = req.full_name

    # Update insurance fields on the register record
    if req.has_insurance_code is not None:
        register_record.has_insurance_code = req.has_insurance_code
    if req.insurance_company:
        register_record.insurance_company = req.insurance_company

    # Also update AgentDetail with basic info
    if req.full_name:
        agent.full_name = req.full_name
    if req.phone:
        agent.phone = req.phone
    if req.gender:
        agent.gender = req.gender
    if req.birthday:
        try:
            agent.birthday = datetime.strptime(req.birthday, "%Y-%m-%d").date()
        except ValueError:
            pass
    if req.address:
        agent.address = req.address

    db.commit()

    return {"message": "Hồ sơ đã được gửi thành công. Vui lòng chờ Admin phê duyệt."}


@router.get("/agent-register/my-status")
def get_my_registration_status(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Check if current user has a pending registration form (used to show onboarding banner)."""
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    register_record = db.query(UserRegister).filter_by(email=agent.email).first()
    if not register_record:
        return {"has_registration": False, "status": None}

    return {
        "has_registration": True,
        "status": register_record.status,
        "register_code": register_record.register_code,
        "fullname": register_record.fullname,
    }


# ─── Admin: nhập liệu / bổ sung hồ sơ giúp CTV (khi hồ sơ còn PENDING) ──────────

@router.put("/admin/agent-register/{reg_id}/profile")
def admin_fill_profile(
    reg_id: int,
    req: OnboardingRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin bổ sung / nhập liệu hồ sơ giúp CTV.

    Dùng cho các hồ sơ đang ở trạng thái PENDING (chờ bổ sung hồ sơ): Admin nhập
    thông tin chi tiết thay cho CTV rồi chuyển sang PROFILE_VERIFYING (chờ duyệt).
    """
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential or credential.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")

    record = db.query(UserRegister).filter_by(id=reg_id).first()
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Đăng ký không tồn tại.")

    # Validate ảnh CCCD (base64) nếu Admin có tải lên
    id_front = _validate_id_image(req.id_front, "Ảnh mặt trước CCCD")
    id_back = _validate_id_image(req.id_back, "Ảnh mặt sau CCCD")

    # Giữ lại ảnh cũ nếu Admin không cung cấp ảnh mới
    existing: dict = {}
    if record.profile_detail:
        try:
            existing = json.loads(record.profile_detail)
        except (ValueError, TypeError):
            existing = {}

    profile_data = {
        "fullName": req.full_name,
        "gender": req.gender,
        "birthday": req.birthday,
        "phone": req.phone,
        "email": req.email,
        "idNumber": req.id_number,
        "issueDate": req.issue_date,
        "issuePlace": req.issue_place,
        "temporaryAddress": req.temporary_address,
        "address": req.address,
        "bankName": req.bank_name,
        "accountNumber": req.account_number,
        "taxId": req.tax_id,
        "hasInsuranceCode": req.has_insurance_code,
        "insuranceCompany": req.insurance_company,
        "idFrontUrl": id_front if id_front is not None else existing.get("idFrontUrl"),
        "idBackUrl": id_back if id_back is not None else existing.get("idBackUrl"),
    }
    record.profile_detail = json.dumps(profile_data, ensure_ascii=False)

    # Cập nhật các cột cơ bản trên bản ghi đăng ký
    if req.full_name:
        record.fullname = req.full_name
    if req.gender:
        record.gender = req.gender
    if req.phone:
        record.phone = req.phone
    if req.email:
        record.email = req.email
    if req.birthday:
        try:
            record.birthday = datetime.strptime(req.birthday, "%Y-%m-%d").date()
        except ValueError:
            pass
    if req.has_insurance_code is not None:
        record.has_insurance_code = req.has_insurance_code
    if req.insurance_company:
        record.insurance_company = req.insurance_company

    # Bổ sung xong -> chuyển sang chờ duyệt
    record.status = "PROFILE_VERIFYING"

    # Đồng bộ thông tin cơ bản sang AgentDetail nếu CTV đã có tài khoản
    if record.email:
        linked_agent = db.query(AgentDetail).filter_by(email=record.email).first()
        if linked_agent:
            if req.full_name:
                linked_agent.full_name = req.full_name
            if req.phone:
                linked_agent.phone = req.phone
            if req.gender:
                linked_agent.gender = req.gender
            if req.birthday:
                try:
                    linked_agent.birthday = datetime.strptime(req.birthday, "%Y-%m-%d").date()
                except ValueError:
                    pass
            if req.address:
                linked_agent.address = req.address

    db.commit()

    return {"message": f"Đã bổ sung hồ sơ cho {record.fullname}. Hồ sơ chuyển sang trạng thái chờ duyệt."}
