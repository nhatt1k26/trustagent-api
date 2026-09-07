"""Contract (Hợp đồng bảo hiểm) endpoints.

TVV: gửi yêu cầu chốt hợp đồng + xem danh sách hợp đồng của mình.
Admin: phê duyệt / từ chối hợp đồng sau khi đối soát với Bảo Việt.
"""

import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_username
from app.models import AgentCredential, AgentDetail, Contract, ContractDocument, LeadInfo
from app.notification_service import create_notification, notify_all_admins
from app.schemas import ContractResponse

router = APIRouter(prefix="/api/v1/contract", tags=["contract"])

ALLOWED_MIMETYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB / file


def _require_admin(username: str, db: Session) -> AgentCredential:
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential or credential.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")
    return credential


def _parse_date(value: Optional[str]):
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _serialize(contract: Contract, db: Session, include_agent: bool = False) -> dict:
    try:
        riders = json.loads(contract.supplementary_packages) if contract.supplementary_packages else []
    except (ValueError, TypeError):
        riders = []

    docs = db.query(ContractDocument).filter_by(contract_id=contract.id).all()

    data = {
        "id": contract.id,
        "agent_id": contract.agent_id,
        "lead_id": contract.lead_id,
        "customer_name": contract.customer_name,
        "customer_phone": contract.customer_phone,
        "main_package": contract.main_package,
        "supplementary_packages": riders,
        "premium": float(contract.premium) if contract.premium is not None else None,
        "insurance_term": contract.insurance_term,
        "start_date": contract.start_date.strftime("%d/%m/%Y") if contract.start_date else None,
        "end_date": contract.end_date.strftime("%d/%m/%Y") if contract.end_date else None,
        "documents": [{"id": d.id, "filename": d.filename, "mimetype": d.mimetype} for d in docs],
        "notes": contract.notes,
        "status": contract.status,
        "admin_notes": contract.admin_notes,
        "created_datetime": contract.created_datetime,
        "submitted_at": contract.submitted_at,
        "reviewed_at": contract.reviewed_at,
    }

    if include_agent:
        agent = db.query(AgentDetail).filter_by(id=contract.agent_id).first()
        data["agent_name"] = agent.full_name if agent else None
        data["agent_code"] = agent.refer_code if agent else None

    return data


# ─── TVV endpoints ──────────────────────────────────────────────────────────────

@router.get("/getList", response_model=List[ContractResponse])
def get_my_contracts(
    status_filter: Optional[str] = Query(None, alias="status"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """TVV - danh sách hợp đồng do mình gửi."""
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        return []

    query = db.query(Contract).filter_by(agent_id=agent.id)
    if status_filter and status_filter != "all":
        query = query.filter(Contract.status == status_filter.upper())

    contracts = query.order_by(Contract.created_datetime.desc()).all()
    return [_serialize(c, db) for c in contracts]


@router.get("/get-stats")
def get_my_contract_stats(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """TVV - thống kê nhanh trạng thái hợp đồng."""
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        return {"total": 0, "submitted": 0, "approved": 0, "rejected": 0}

    contracts = db.query(Contract).filter_by(agent_id=agent.id).all()
    return {
        "total": len(contracts),
        "submitted": sum(1 for c in contracts if (c.status or "").upper() == "SUBMITTED"),
        "approved": sum(1 for c in contracts if (c.status or "").upper() == "APPROVED"),
        "rejected": sum(1 for c in contracts if (c.status or "").upper() == "REJECTED"),
    }


@router.post("/create", response_model=ContractResponse, status_code=201)
async def create_contract(
    lead_id: Optional[int] = Form(None),
    customer_name: str = Form(...),
    customer_phone: Optional[str] = Form(None),
    main_package: str = Form(...),
    supplementary_packages: Optional[str] = Form(None),  # JSON array string
    premium: Optional[float] = Form(None),
    insurance_term: Optional[str] = Form(None),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    files: List[UploadFile] = File(default=[]),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """TVV - tạo yêu cầu chốt hợp đồng (gửi ngay để admin duyệt)."""
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    # Chuẩn hoá gói bổ trợ
    riders: List[str] = []
    if supplementary_packages:
        try:
            parsed = json.loads(supplementary_packages)
            if isinstance(parsed, list):
                riders = [str(x) for x in parsed]
        except (ValueError, TypeError):
            riders = [supplementary_packages]

    now = datetime.utcnow()
    contract = Contract(
        agent_id=agent.id,
        lead_id=lead_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        main_package=main_package,
        supplementary_packages=json.dumps(riders, ensure_ascii=False),
        premium=premium,
        insurance_term=insurance_term,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
        notes=notes,
        status="SUBMITTED",
        created_datetime=now,
        submitted_at=now,
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)

    # Lưu file hồ sơ
    for f in files:
        if not f.filename:
            continue
        if f.content_type and f.content_type not in ALLOWED_MIMETYPES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Định dạng file không hỗ trợ: {f.filename}",
            )
        content = await f.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"File quá lớn (tối đa 10MB): {f.filename}")
        db.add(ContractDocument(
            contract_id=contract.id,
            filename=f.filename,
            mimetype=f.content_type,
            content=content,
            created_datetime=now,
        ))
    db.commit()

    # #7: Thông báo cho Admin có yêu cầu chốt hợp đồng mới cần đối soát
    notify_all_admins(
        db,
        type="CONTRACT_SUBMITTED",
        title="Yêu cầu chốt hợp đồng mới",
        message=f"TVV {agent.full_name or agent.username} vừa gửi hợp đồng cho khách hàng "
                f"{contract.customer_name} ({contract.main_package}) cần đối soát & phê duyệt.",
        link_tab="contracts",
        ref_id=contract.id,
    )

    return _serialize(contract, db)


@router.get("/document/{doc_id}")
def download_document(
    doc_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Tải/xem file hồ sơ hợp đồng. TVV chỉ xem file của mình, admin xem tất cả."""
    doc = db.query(ContractDocument).filter_by(id=doc_id).first()
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tài liệu.")

    contract = db.query(Contract).filter_by(id=doc.contract_id).first()
    credential = db.query(AgentCredential).filter_by(username=username).first()
    is_admin = credential and credential.role in ("ROLE_ADMIN", "ROLE_AGENT")

    if not is_admin:
        agent = db.query(AgentDetail).filter_by(username=username).first()
        if not agent or not contract or contract.agent_id != agent.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập tài liệu này.")

    return Response(
        content=doc.content,
        media_type=doc.mimetype or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{doc.filename or "document"}"'},
    )


# ─── Admin endpoints ────────────────────────────────────────────────────────────

@router.get("/admin/list")
def admin_list_contracts(
    status_filter: Optional[str] = Query(None, alias="status"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin - danh sách hợp đồng cần đối soát & phê duyệt."""
    _require_admin(username, db)

    query = db.query(Contract)
    if status_filter and status_filter != "all":
        query = query.filter(Contract.status == status_filter.upper())

    contracts = query.order_by(Contract.submitted_at.desc().nullslast(), Contract.created_datetime.desc()).all()
    return [_serialize(c, db, include_agent=True) for c in contracts]


@router.get("/admin/stats")
def admin_contract_stats(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin - thống kê tổng."""
    _require_admin(username, db)
    contracts = db.query(Contract).all()
    return {
        "total": len(contracts),
        "submitted": sum(1 for c in contracts if (c.status or "").upper() == "SUBMITTED"),
        "approved": sum(1 for c in contracts if (c.status or "").upper() == "APPROVED"),
        "rejected": sum(1 for c in contracts if (c.status or "").upper() == "REJECTED"),
    }


@router.put("/admin/{contract_id}/approve")
def admin_approve_contract(
    contract_id: int,
    notes: Optional[str] = Query(None),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin - phê duyệt hợp đồng sau khi đối soát với Bảo Việt."""
    _require_admin(username, db)

    contract = db.query(Contract).filter_by(id=contract_id).first()
    if not contract:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hợp đồng không tồn tại.")
    if (contract.status or "").upper() not in ("SUBMITTED",):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Chỉ phê duyệt được hợp đồng đang chờ duyệt.")

    contract.status = "APPROVED"
    contract.reviewed_at = datetime.utcnow()
    if notes:
        contract.admin_notes = notes

    # #4: Thông báo cho TVV hợp đồng đã được duyệt
    if contract.agent_id:
        create_notification(
            db,
            recipient_agent_id=contract.agent_id,
            type="CONTRACT_APPROVED",
            title="Hợp đồng đã được phê duyệt",
            message=f"Hợp đồng cho khách hàng {contract.customer_name} ({contract.main_package}) "
                    f"đã được Admin phê duyệt.",
            link_tab="clients-appointments",
            ref_id=contract.id,
            commit=False,
        )

    db.commit()

    return {"message": f"Đã phê duyệt hợp đồng #{contract_id} cho khách hàng {contract.customer_name}."}


@router.put("/admin/{contract_id}/reject")
def admin_reject_contract(
    contract_id: int,
    reason: str = Query(""),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin - từ chối hợp đồng."""
    _require_admin(username, db)

    contract = db.query(Contract).filter_by(id=contract_id).first()
    if not contract:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hợp đồng không tồn tại.")

    contract.status = "REJECTED"
    contract.admin_notes = reason
    contract.reviewed_at = datetime.utcnow()

    # #5: Thông báo cho TVV hợp đồng bị từ chối
    if contract.agent_id:
        create_notification(
            db,
            recipient_agent_id=contract.agent_id,
            type="CONTRACT_REJECTED",
            title="Hợp đồng bị từ chối",
            message=f"Hợp đồng cho khách hàng {contract.customer_name} chưa được duyệt."
                    + (f" Lý do: {reason}" if reason else ""),
            link_tab="clients-appointments",
            ref_id=contract.id,
            commit=False,
        )

    db.commit()

    return {"message": f"Đã từ chối hợp đồng #{contract_id}."}
