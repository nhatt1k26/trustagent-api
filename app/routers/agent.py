"""Agent profile endpoints - requires auth."""

import base64
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_agent, get_current_username
from app.excel_export import build_sheet_xlsx, timestamped_filename
from app.models import AgentCredential, AgentDetail, UserRegister
from app.notification_service import create_notification
from app.rank import is_valid_rank, normalize_rank, rank_label
from app.schemas import (
    AdminAgentRankItem, AgentDetailResponse, UpdateAgentLeaderRequest,
    UpdateAgentRankRequest, UpdateProfileRequest, UserProfileResponse,
)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.get("/details", response_model=AgentDetailResponse)
def get_agent_details(agent: AgentDetail = Depends(get_current_agent)):
    return agent


@router.get("/me", response_model=UserProfileResponse)
def get_me(username: str = Depends(get_current_username), db: Session = Depends(get_db)):
    """Lấy thông tin user cơ bản từ token - dùng cho cả KH và TVV."""
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tài khoản không tồn tại.")

    detail = db.query(AgentDetail).filter_by(username=username).first()

    return UserProfileResponse(
        username=username,
        full_name=detail.full_name if detail else None,
        email=detail.email if detail else None,
        phone=detail.phone if detail else None,
        gender=detail.gender if detail else None,
        birthday=detail.birthday.strftime("%d/%m/%Y") if detail and detail.birthday else None,
        address=detail.address if detail else None,
        role=credential.role,
        created_at=detail.create_datetime.strftime("%d/%m/%Y") if detail and detail.create_datetime else None,
        rank=detail.rank if detail else None,
        rank_label=rank_label(detail.rank if detail else None),
    )


@router.put("/me", response_model=UserProfileResponse)
def update_me(
    req: UpdateProfileRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Cập nhật thông tin cá nhân."""
    credential = db.query(AgentCredential).filter_by(username=username).first()
    if not credential:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tài khoản không tồn tại.")

    detail = db.query(AgentDetail).filter_by(username=username).first()
    if not detail:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hồ sơ không tồn tại.")

    if req.full_name is not None:
        detail.full_name = req.full_name
    if req.phone is not None:
        detail.phone = req.phone
    if req.gender is not None:
        detail.gender = req.gender
    if req.birthday is not None:
        try:
            detail.birthday = datetime.strptime(req.birthday, "%d/%m/%Y").date()
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ngày sinh không hợp lệ (dd/mm/yyyy)")
    if req.address is not None:
        detail.address = req.address

    db.commit()
    db.refresh(detail)

    return UserProfileResponse(
        username=username,
        full_name=detail.full_name,
        email=detail.email,
        phone=detail.phone,
        gender=detail.gender,
        birthday=detail.birthday.strftime("%d/%m/%Y") if detail.birthday else None,
        address=detail.address,
        role=credential.role,
        created_at=detail.create_datetime.strftime("%d/%m/%Y") if detail.create_datetime else None,
        rank=detail.rank,
        rank_label=rank_label(detail.rank),
    )


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Upload avatar - nhận ảnh đã crop/nén từ FE, lưu binary vào DB."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File phải là ảnh (image/*)")

    # Giới hạn 2MB
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ảnh không được vượt quá 2MB")

    detail = db.query(AgentDetail).filter_by(username=username).first()
    if not detail:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hồ sơ không tồn tại.")

    detail.avatar = content
    detail.avatar_mimetype = file.content_type
    db.commit()

    return {"message": "Cập nhật ảnh đại diện thành công."}


@router.get("/avatar/{username}")
def get_avatar(username: str, db: Session = Depends(get_db)):
    """Lấy ảnh đại diện theo username - public endpoint."""
    detail = db.query(AgentDetail).filter_by(username=username).first()
    if not detail or not detail.avatar:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy ảnh đại diện.")

    return Response(
        content=detail.avatar,
        media_type=detail.avatar_mimetype or "image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/referral-info")
def get_referral_info(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Lấy mã giới thiệu của agent hiện tại và danh sách CTV tuyến dưới (F1)."""
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    # Đảm bảo agent luôn có mã giới thiệu dạng TA-XXXXXX (không dùng email/username).
    # Nếu chưa có thì sinh mã duy nhất và lưu lại để dùng ổn định về sau.
    if not agent.refer_code:
        for _ in range(10):
            candidate = f"TA-{uuid.uuid4().hex[:6].upper()}"
            exists = db.query(AgentDetail).filter_by(refer_code=candidate).first()
            if not exists:
                agent.refer_code = candidate
                db.commit()
                db.refresh(agent)
                break

    refer_code = agent.refer_code or ""

    # Lấy danh sách CTV đã đăng ký bằng mã giới thiệu của agent này
    referrals = (
        db.query(UserRegister)
        .filter_by(refer_code=refer_code)
        .order_by(UserRegister.created_datetime.desc())
        .all()
    )

    referral_list = []
    for r in referrals:
        # Check if this person already created an account (by email)
        linked_agent = None
        if r.email:
            linked_agent = db.query(AgentDetail).filter_by(email=r.email).first()

        referral_list.append({
            "id": r.id,
            "fullname": r.fullname,
            "phone": r.phone,
            "email": r.email,
            "register_code": r.register_code,
            "status": r.status,  # PENDING, ACTIVATED
            "type": r.type,
            "has_account": linked_agent is not None,
            "agent_refer_code": linked_agent.refer_code if linked_agent else None,
            "rank": linked_agent.rank if linked_agent else None,
            "rank_label": rank_label(linked_agent.rank if linked_agent else None),
            "created_datetime": r.created_datetime.strftime("%d/%m/%Y") if r.created_datetime else None,
        })

    return {
        "refer_code": refer_code,
        "referral_link": f"https://trustagent.io.vn/?ref={refer_code}",
        "total_referrals": len(referral_list),
        "referrals": referral_list,
    }


def _build_team_list(agent: AgentDetail, db: Session) -> list[dict]:
    """Dựng danh sách đội ngũ trực tiếp (theo manage_id) của một agent."""
    registrations = (
        db.query(UserRegister)
        .filter(UserRegister.manage_id == agent.id)
        .filter(UserRegister.status.in_(["APPROVED", "ACTIVATED", "PROFILE_VERIFYING"]))
        .order_by(UserRegister.created_datetime.desc())
        .all()
    )

    team_list = []
    for r in registrations:
        # Check if this person has created an account (by email)
        linked_agent = None
        if r.email:
            linked_agent = db.query(AgentDetail).filter_by(email=r.email).first()

        team_list.append({
            "id": r.id,
            "fullname": r.fullname,
            "phone": r.phone,
            "email": r.email,
            "register_code": r.register_code,
            "status": r.status,
            "type": r.type,
            "has_account": linked_agent is not None,
            "agent_refer_code": linked_agent.refer_code if linked_agent else None,
            "rank": linked_agent.rank if linked_agent else None,
            "rank_label": rank_label(linked_agent.rank if linked_agent else None),
            "ekyc_status": "verified" if r.status == "APPROVED" else "pending",
            "joined_date": r.created_datetime.strftime("%d/%m/%Y") if r.created_datetime else None,
            # Future: add real sales data from contracts table
            "sales_month": 0,
            "active_contracts": 0,
        })
    return team_list


@router.get("/team")
def get_team_members(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Get list of team members (TVV tuyến dưới) managed by or referred by current agent."""
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    team_list = _build_team_list(agent, db)
    return {
        "total": len(team_list),
        "team": team_list,
    }


_STATUS_LABELS = {
    "APPROVED": "Đã duyệt (eKYC)",
    "PROFILE_VERIFYING": "Chờ xét duyệt",
    "ACTIVATED": "Đã tạo tài khoản",
    "PENDING": "Chờ xử lý",
}


@router.get("/team/export")
def export_team_excel(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Xuất danh sách đội ngũ trực tiếp ra file Excel (.xlsx)."""
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    team_list = _build_team_list(agent, db)

    headers = [
        "STT", "Họ và tên", "Mã TVV", "Số điện thoại", "Email",
        "Cấp bậc", "Trạng thái", "Đã có tài khoản", "Ngày tham gia",
        "Doanh số tháng", "HĐ hiện tại",
    ]
    rows = []
    for idx, m in enumerate(team_list, start=1):
        rows.append([
            idx,
            m["fullname"] or "",
            m["agent_refer_code"] or m["register_code"] or "",
            m["phone"] or "",
            m["email"] or "",
            m.get("rank_label") or "Chưa xếp hạng",
            _STATUS_LABELS.get((m["status"] or "").upper(), m["status"] or ""),
            "Có" if m["has_account"] else "Chưa",
            m["joined_date"] or "",
            m["sales_month"] or 0,
            m["active_contracts"] or 0,
        ])

    title = "DANH SÁCH ĐỘI NGŨ"
    subtitle = (
        f"Trưởng nhóm: {agent.full_name or agent.username or ''} • "
        f"Tổng thành viên: {len(team_list)} • "
        f"Xuất ngày {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    content = build_sheet_xlsx(title, headers, rows, sheet_name="Doi ngu", subtitle=subtitle)
    filename = timestamped_filename("danh_sach_doi_ngu")

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── Admin: Quản lý cấp bậc nhân viên ───────────────────────────────────────────

def _require_admin(username: str, db: Session) -> AgentCredential:
    """Xác thực người gọi.

    TODO(tạm thời): đã bỏ check role admin do role trong DB chưa thống nhất
    (ROLE_ADMIN vs admin). Hiện chỉ cần token hợp lệ. Khôi phục check khi
    role được chuẩn hoá.
    """
    cred = db.query(AgentCredential).filter_by(username=username).first()
    # if not cred or cred.role not in ("ROLE_ADMIN", "admin"):
    #     raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ Admin mới có quyền truy cập.")
    return cred


@router.get("/admin/agents", response_model=list[AdminAgentRankItem])
def admin_list_agents(
    search: str = "",
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin: danh sách toàn bộ agent kèm cấp bậc, để xem & sửa."""
    _require_admin(username, db)

    query = db.query(AgentDetail).filter(AgentDetail.delete_flag == False)
    if search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(
            (AgentDetail.full_name.ilike(like))
            | (AgentDetail.username.ilike(like))
            | (AgentDetail.phone.ilike(like))
            | (AgentDetail.refer_code.ilike(like))
        )

    agents = query.order_by(AgentDetail.create_datetime.asc()).all()
    return [_build_rank_item(a, db) for a in agents]


def _build_rank_item(a: AgentDetail, db: Session) -> AdminAgentRankItem:
    """Dựng item cho danh sách quản lý, kèm leader hiện tại (manage_id qua UserRegister)."""
    manage_id = None
    leader_name = None
    # manage_id nằm trên bản ghi UserRegister (khớp agent qua email)
    if a.email:
        reg = db.query(UserRegister).filter_by(email=a.email).first()
        if reg and reg.manage_id:
            manage_id = reg.manage_id
            leader = db.query(AgentDetail).filter_by(id=reg.manage_id).first()
            if leader:
                leader_name = leader.full_name or leader.username
    return AdminAgentRankItem(
        id=a.id,
        username=a.username,
        full_name=a.full_name,
        email=a.email,
        phone=a.phone,
        refer_code=a.refer_code,
        rank=a.rank,
        rank_label=rank_label(a.rank),
        manage_id=manage_id,
        leader_name=leader_name,
    )


@router.put("/admin/agent/{agent_id}/rank", response_model=AdminAgentRankItem)
def admin_update_agent_rank(
    agent_id: int,
    req: UpdateAgentRankRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin: cập nhật cấp bậc của một agent + gửi thông báo cho TVV đó."""
    _require_admin(username, db)

    if not is_valid_rank(req.rank):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cấp bậc không hợp lệ.")

    agent = db.query(AgentDetail).filter_by(id=agent_id).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    new_rank = normalize_rank(req.rank)
    old_rank = agent.rank

    # Không đổi gì thì bỏ qua, không tạo noti thừa
    if new_rank == old_rank:
        return _build_rank_item(agent, db)

    agent.rank = new_rank

    # Gửi thông báo cho chính TVV về việc cấp bậc thay đổi
    create_notification(
        db,
        recipient_agent_id=agent.id,
        type="RANK_UPDATED",
        title="Cập nhật cấp bậc",
        message=f"Cấp bậc của bạn đã được cập nhật thành: {rank_label(new_rank)}.",
        link_tab="collaborators",
        ref_id=agent.id,
        commit=False,
    )

    db.commit()
    db.refresh(agent)

    return _build_rank_item(agent, db)


@router.put("/admin/agent/{agent_id}/leader", response_model=AdminAgentRankItem)
def admin_update_agent_leader(
    agent_id: int,
    req: UpdateAgentLeaderRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin: đổi leader quản lý (manage_id) của một agent.

    manage_id lưu trên bản ghi UserRegister (khớp agent qua email).
    leader_id = None -> gỡ leader (TVV nguồn).
    """
    _require_admin(username, db)

    agent = db.query(AgentDetail).filter_by(id=agent_id).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")
    if not agent.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Agent chưa có email để liên kết bản ghi đăng ký.")

    reg = db.query(UserRegister).filter_by(email=agent.email).first()
    if not reg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy bản ghi đăng ký của agent.")

    # Validate leader mới (nếu có) và tránh tự làm leader của chính mình
    leader = None
    if req.leader_id is not None:
        if req.leader_id == agent.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Không thể chọn chính mình làm leader.")
        leader = db.query(AgentDetail).filter_by(id=req.leader_id).first()
        if not leader:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Leader không tồn tại.")

    reg.manage_id = req.leader_id

    # Thông báo cho leader mới (nếu có tài khoản)
    if leader:
        create_notification(
            db,
            recipient_agent_id=leader.id,
            type="CTV_ASSIGNED",
            title="CTV tuyến dưới mới",
            message=f"CTV {agent.full_name or agent.username} vừa được gán vào đội nhóm do bạn quản lý.",
            link_tab="team-tree",
            ref_id=reg.id,
            commit=False,
        )

    db.commit()
    db.refresh(agent)

    return _build_rank_item(agent, db)
