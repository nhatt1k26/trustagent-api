"""Notification endpoints - danh sách, đếm chưa đọc, đánh dấu đã đọc.

Khi gọi GET /notifications, hệ thống chạy lazy-scan các lịch hẹn sắp tới
của TVV để sinh thông báo nhắc lịch theo 2 mốc: còn ~24h và còn ~1h.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_username
from app.models import AgentDetail, Appointment, LeadInfo, Notification
from app.notification_service import create_notification

router = APIRouter(prefix="/api/v1/notifications", tags=["notification"])


def _appointment_datetime(appt: Appointment) -> Optional[datetime]:
    """Ghép ngày + giờ hẹn thành datetime (giờ mặc định 00:00 nếu thiếu)."""
    if not appt.appointment_date:
        return None
    base = appt.appointment_date
    hour, minute = 0, 0
    if appt.appointment_time:
        try:
            parts = appt.appointment_time.strip().split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            pass
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _scan_upcoming_appointments(db: Session, agent: AgentDetail) -> None:
    """Sinh thông báo nhắc lịch hẹn theo 2 mốc: 24h và 1h trước giờ hẹn."""
    now = datetime.now()

    appts = (
        db.query(Appointment)
        .filter(Appointment.agent_id == agent.id, Appointment.status == "upcoming")
        .all()
    )

    changed = False
    for appt in appts:
        appt_dt = _appointment_datetime(appt)
        if not appt_dt or appt_dt < now:
            continue

        delta = appt_dt - now
        lead_name = None
        if appt.lead_id:
            lead = db.query(LeadInfo).filter_by(id=appt.lead_id).first()
            lead_name = lead.fullname if lead else None
        who = lead_name or "khách hàng"
        when = appt_dt.strftime("%H:%M %d/%m/%Y")

        # Mốc 24h: trong khoảng (1h, 24h]
        if not appt.notified_24h and timedelta(hours=1) < delta <= timedelta(hours=24):
            create_notification(
                db,
                recipient_agent_id=agent.id,
                type="APPOINTMENT_UPCOMING",
                title="Sắp tới lịch hẹn (còn ~24h)",
                message=f"Bạn có lịch hẹn với {who} lúc {when}.",
                link_tab="clients-appointments",
                ref_id=appt.id,
                commit=False,
            )
            appt.notified_24h = True
            changed = True

        # Mốc 1h: trong khoảng (0, 1h]
        if not appt.notified_1h and timedelta(0) < delta <= timedelta(hours=1):
            create_notification(
                db,
                recipient_agent_id=agent.id,
                type="APPOINTMENT_UPCOMING",
                title="Sắp tới lịch hẹn (còn ~1h)",
                message=f"Lịch hẹn với {who} sẽ diễn ra lúc {when}.",
                link_tab="clients-appointments",
                ref_id=appt.id,
                commit=False,
            )
            # Nếu tạo trong vòng 1h mà chưa từng nhắc 24h thì coi như đã nhắc luôn
            appt.notified_1h = True
            appt.notified_24h = True
            changed = True

    if changed:
        db.commit()


@router.get("")
def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(30, le=100),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Danh sách thông báo của người dùng hiện tại (kèm lazy-scan lịch hẹn)."""
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        return {"total_unread": 0, "notifications": []}

    # Lazy-scan lịch hẹn để sinh nhắc nhở nếu tới mốc
    _scan_upcoming_appointments(db, agent)

    query = db.query(Notification).filter_by(recipient_agent_id=agent.id)
    if unread_only:
        query = query.filter(Notification.is_read == False)

    items = query.order_by(Notification.created_datetime.desc()).limit(limit).all()
    total_unread = (
        db.query(Notification)
        .filter_by(recipient_agent_id=agent.id, is_read=False)
        .count()
    )

    return {
        "total_unread": total_unread,
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "link_tab": n.link_tab,
                "ref_id": n.ref_id,
                "is_read": bool(n.is_read),
                "created_datetime": n.created_datetime.isoformat() if n.created_datetime else None,
            }
            for n in items
        ],
    }


@router.get("/unread-count")
def unread_count(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        return {"count": 0}
    _scan_upcoming_appointments(db, agent)
    count = (
        db.query(Notification)
        .filter_by(recipient_agent_id=agent.id, is_read=False)
        .count()
    )
    return {"count": count}


@router.put("/{notif_id}/read")
def mark_read(
    notif_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")
    notif = db.query(Notification).filter_by(id=notif_id, recipient_agent_id=agent.id).first()
    if not notif:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thông báo không tồn tại.")
    notif.is_read = True
    db.commit()
    return {"message": "ok"}


@router.put("/read-all")
def mark_all_read(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")
    db.query(Notification).filter_by(recipient_agent_id=agent.id, is_read=False).update(
        {Notification.is_read: True}
    )
    db.commit()
    return {"message": "ok"}
