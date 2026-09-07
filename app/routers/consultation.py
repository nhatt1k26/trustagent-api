"""Customer Consultation endpoints - KH đăng ký tư vấn bảo hiểm."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_username
from app.models import AgentCredential, AgentDetail, CustomerConsultation, LeadInfo
from app.notification_service import create_notification
from app.schemas import (
    AssignCustomerConsultationRequest,
    CustomerConsultationCreateRequest,
    CustomerConsultationResponse,
)

router = APIRouter(prefix="/api/v1/customer-consultation", tags=["customer-consultation"])


@router.post("/submit", response_model=CustomerConsultationResponse, status_code=201)
def submit_customer_consultation(req: CustomerConsultationCreateRequest, db: Session = Depends(get_db)):
    """Public - KH gửi yêu cầu tư vấn bảo hiểm (không cần đăng nhập)."""
    birthday_date = None
    if req.birthday:
        try:
            birthday_date = datetime.strptime(req.birthday, "%d/%m/%Y").date()
        except ValueError:
            pass

    record = CustomerConsultation(
        fullname=req.fullname,
        phone=req.phone,
        email=req.email,
        gender=req.gender,
        birthday=birthday_date,
        address=req.address,
        product_interest=req.product_interest,
        notes=req.notes,
        status="new",
        created_datetime=datetime.utcnow(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/list", response_model=List[CustomerConsultationResponse])
def list_customer_consultations(
    status_filter: Optional[str] = Query(None, alias="status"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin/TVV - lấy danh sách KH đăng ký tư vấn."""
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential or credential.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")

    query = db.query(CustomerConsultation).order_by(CustomerConsultation.created_datetime.desc())

    if status_filter:
        query = query.filter(CustomerConsultation.status == status_filter)

    return query.all()


@router.put("/{id}/assign", response_model=CustomerConsultationResponse)
def assign_customer_consultation(
    id: int,
    req: AssignCustomerConsultationRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin - phân công TVV phụ trách KH. Tự động tạo Lead cho TVV."""
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential or credential.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")

    consultation = db.query(CustomerConsultation).filter_by(id=id).first()
    if not consultation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Yêu cầu tư vấn không tồn tại.")

    agent = db.query(AgentDetail).filter_by(username=req.agent_username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "TVV không tồn tại.")

    consultation.assigned_agent_id = agent.id
    consultation.assigned_agent_username = req.agent_username
    consultation.assigned_at = datetime.utcnow()
    consultation.status = "assigned"

    # Tự động tạo Lead trong LEAD_INFO cho TVV
    existing_lead = db.query(LeadInfo).filter_by(
        phone=consultation.phone, agent_id=agent.id
    ).first()

    if not existing_lead:
        new_lead = LeadInfo(
            fullname=consultation.fullname,
            phone=consultation.phone,
            email=consultation.email,
            gender=consultation.gender,
            birthday=consultation.birthday,
            address=consultation.address,
            source="Đăng ký tư vấn (Website)",
            stage="NEW",
            notes=consultation.notes,
            agent_id=agent.id,
            created_datetime=datetime.utcnow(),
        )
        db.add(new_lead)

    # Thông báo cho TVV được assign khách hàng
    create_notification(
        db,
        recipient_agent_id=agent.id,
        type="CUSTOMER_ASSIGNED",
        title="Khách hàng mới được phân công",
        message=f"Bạn vừa được giao phụ trách khách hàng {consultation.fullname}"
                + (f" ({consultation.phone})" if consultation.phone else "") + ".",
        link_tab="clients-appointments",
        ref_id=consultation.id,
        commit=False,
    )

    db.commit()
    db.refresh(consultation)
    return consultation


@router.put("/{id}/status")
def update_customer_consultation_status(
    id: int,
    new_status: str = Query(...),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Cập nhật trạng thái yêu cầu tư vấn KH."""
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential or credential.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")

    consultation = db.query(CustomerConsultation).filter_by(id=id).first()
    if not consultation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Yêu cầu tư vấn không tồn tại.")

    if new_status not in ("new", "assigned", "contacted", "closed"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Trạng thái không hợp lệ.")

    consultation.status = new_status
    db.commit()
    return {"message": "Cập nhật trạng thái thành công."}


@router.get("/agents", response_model=list)
def get_available_agents(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Lấy danh sách TVV có thể assign cho KH."""
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential or credential.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")

    agents = db.query(AgentDetail).filter(AgentDetail.delete_flag == False).all()
    return [
        {
            "id": a.id,
            "username": a.username,
            "full_name": a.full_name,
            "phone": a.phone,
        }
        for a in agents
    ]
