"""Appointment CRUD - Quản lý lịch hẹn tư vấn."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_agent
from app.models import AgentDetail, Appointment, LeadInfo
from app.schemas import ApiResponse, AppointmentRequest, AppointmentResponse

router = APIRouter(prefix="/api/v1/appointment", tags=["appointment"])


def _build_response(appt: Appointment, db: Session) -> dict:
    """Build response dict with lead info resolved."""
    lead_name = None
    lead_phone = None
    if appt.lead_id:
        lead = db.query(LeadInfo).filter_by(id=appt.lead_id).first()
        if lead:
            lead_name = lead.fullname
            lead_phone = lead.phone

    # Format date for FE (dd/mm/yyyy)
    date_str = None
    if appt.appointment_date:
        date_str = appt.appointment_date.strftime("%d/%m/%Y")

    return {
        "id": appt.id,
        "lead_id": appt.lead_id,
        "lead_name": lead_name,
        "lead_phone": lead_phone,
        "appointment_date": date_str,
        "appointment_time": appt.appointment_time,
        "appointment_location": appt.appointment_location,
        "appointment_goal": appt.appointment_goal,
        "meeting_type": appt.meeting_type,
        "product": appt.product,
        "notes": appt.notes,
        "status": appt.status or "upcoming",
        "created_datetime": appt.created_datetime,
    }


@router.get("/agent-appointments", response_model=List[AppointmentResponse])
def get_appointments(
    appointment_status: Optional[str] = Query(None, alias="status"),
    agent: AgentDetail = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    query = db.query(Appointment).filter_by(agent_id=agent.id)

    if appointment_status and appointment_status != "all":
        query = query.filter(Appointment.status == appointment_status)

    appointments = query.order_by(Appointment.appointment_date.desc()).all()
    return [_build_response(a, db) for a in appointments]


@router.post("/save", response_model=AppointmentResponse)
def create_or_update_appointment(
    req: AppointmentRequest,
    agent: AgentDetail = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    # Parse date from dd/mm/yyyy string
    parsed_date = None
    if req.appointment_date:
        try:
            parsed_date = datetime.strptime(req.appointment_date, "%d/%m/%Y")
        except ValueError:
            try:
                parsed_date = datetime.strptime(req.appointment_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Invalid date format. Use dd/mm/yyyy"
                )

    if req.id:
        appt = db.query(Appointment).filter_by(id=req.id, agent_id=agent.id).first()
        if not appt:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")

        if req.lead_id is not None:
            appt.lead_id = req.lead_id
        if parsed_date:
            appt.appointment_date = parsed_date
        if req.appointment_time is not None:
            appt.appointment_time = req.appointment_time
        if req.appointment_location is not None:
            appt.appointment_location = req.appointment_location
        if req.appointment_goal is not None:
            appt.appointment_goal = req.appointment_goal
        if req.meeting_type is not None:
            appt.meeting_type = req.meeting_type
        if req.product is not None:
            appt.product = req.product
        if req.link is not None:
            appt.link = req.link
        if req.notes is not None:
            appt.notes = req.notes
        if req.status is not None:
            appt.status = req.status
    else:
        appt = Appointment(
            agent_id=agent.id,
            lead_id=req.lead_id,
            appointment_date=parsed_date,
            appointment_time=req.appointment_time,
            appointment_location=req.appointment_location,
            appointment_goal=req.appointment_goal,
            meeting_type=req.meeting_type,
            product=req.product,
            link=req.link,
            notes=req.notes,
            status=req.status or "upcoming",
            is_notified=False,
            created_datetime=datetime.utcnow(),
        )
        db.add(appt)

    db.commit()
    db.refresh(appt)
    return _build_response(appt, db)


@router.patch("/{appointment_id}/status", response_model=AppointmentResponse)
def update_appointment_status(
    appointment_id: int,
    new_status: str = Query(..., alias="status"),
    agent: AgentDetail = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Quick status update (upcoming/completed/cancelled)."""
    valid_statuses = ["upcoming", "completed", "cancelled"]
    if new_status not in valid_statuses:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid status. Must be one of: {valid_statuses}"
        )

    appt = db.query(Appointment).filter_by(id=appointment_id, agent_id=agent.id).first()
    if not appt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")

    appt.status = new_status
    db.commit()
    db.refresh(appt)
    return _build_response(appt, db)


@router.delete("/{appointment_id}")
def delete_appointment(
    appointment_id: int,
    agent: AgentDetail = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    appt = db.query(Appointment).filter_by(id=appointment_id, agent_id=agent.id).first()
    if not appt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    db.delete(appt)
    db.commit()
    return ApiResponse(message="Deleted")
