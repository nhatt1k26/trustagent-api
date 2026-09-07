"""Notification helpers - tạo thông báo cho TVV & Admin.

Các loại thông báo (type):
- CUSTOMER_ASSIGNED       : Khách hàng mới được admin assign cho TVV
- CTV_ASSIGNED            : CTV tuyến dưới mới được admin gán cho TVV (leader)
- APPOINTMENT_UPCOMING    : Sắp tới lịch hẹn khách hàng (nhắc mốc 24h / 1h)
- CONTRACT_APPROVED       : Hợp đồng của TVV được duyệt
- CONTRACT_REJECTED       : Hợp đồng của TVV bị từ chối
- REGISTRATION_APPROVED   : Hồ sơ CTV của mình được duyệt
- REGISTRATION_REJECTED   : Hồ sơ CTV của mình bị từ chối
- CONTRACT_SUBMITTED      : (Admin) TVV gửi yêu cầu chốt hợp đồng mới
- REGISTRATION_NEW        : (Admin) Có CTV đăng ký / nộp hồ sơ mới
- RANK_UPDATED            : Cấp bậc của TVV được Admin cập nhật
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AgentCredential, AgentDetail, Notification


def create_notification(
    db: Session,
    recipient_agent_id: int,
    type: str,
    title: str,
    message: str,
    link_tab: Optional[str] = None,
    ref_id: Optional[int] = None,
    commit: bool = True,
) -> Notification:
    """Tạo 1 thông báo cho 1 người nhận (theo AgentDetail.id)."""
    notif = Notification(
        recipient_agent_id=recipient_agent_id,
        type=type,
        title=title,
        message=message,
        link_tab=link_tab,
        ref_id=ref_id,
        is_read=False,
        created_datetime=datetime.utcnow(),
    )
    db.add(notif)
    if commit:
        db.commit()
    return notif


def notify_all_admins(
    db: Session,
    type: str,
    title: str,
    message: str,
    link_tab: Optional[str] = None,
    ref_id: Optional[int] = None,
) -> None:
    """Gửi thông báo tới tất cả tài khoản có role ROLE_ADMIN."""
    admin_creds = db.query(AgentCredential).filter(AgentCredential.role == "ROLE_ADMIN").all()
    for cred in admin_creds:
        detail = db.query(AgentDetail).filter_by(username=cred.username).first()
        if detail:
            create_notification(
                db,
                recipient_agent_id=detail.id,
                type=type,
                title=title,
                message=message,
                link_tab=link_tab,
                ref_id=ref_id,
                commit=False,
            )
    db.commit()
