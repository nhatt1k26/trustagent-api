"""Lead CRUD + statistics - KH của TVV."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_agent, get_current_username
from app.models import AgentDetail, LeadInfo
from app.schemas import (
    ApiResponse,
    LeadCreateRequest,
    LeadResponse,
    LeadStatResponse,
)

router = APIRouter(prefix="/api/v1/lead", tags=["lead"])


@router.get("/getList", response_model=List[LeadResponse])
def get_leads(
    stage: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        return []

    query = db.query(LeadInfo).filter_by(agent_id=agent.id)

    if stage and stage != "all":
        query = query.filter(LeadInfo.stage == stage.upper())

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (LeadInfo.fullname.ilike(search_term)) |
            (LeadInfo.phone.ilike(search_term)) |
            (LeadInfo.email.ilike(search_term))
        )

    return query.order_by(LeadInfo.created_datetime.desc()).all()


@router.get("/get-lead-stat", response_model=LeadStatResponse)
def get_lead_stat(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        return LeadStatResponse()

    leads = db.query(LeadInfo).filter_by(agent_id=agent.id).all()
    stat = LeadStatResponse(total=len(leads))
    for lead in leads:
        stage = (lead.stage or "").upper()
        if stage == "NEW":
            stat.new += 1
        elif stage == "CONTACTED":
            stat.contacted += 1
        elif stage == "NEGOTIATING":
            stat.negotiating += 1
        elif stage == "CLOSED":
            stat.closed += 1
        elif stage == "LOST":
            stat.lost += 1
    return stat


@router.post("/create", response_model=LeadResponse, status_code=201)
def create_lead(
    req: LeadCreateRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")

    lead = LeadInfo(
        fullname=req.fullname,
        phone=req.phone,
        email=req.email,
        gender=req.gender,
        birthday=req.birthday,
        source=req.source,
        lead_group=req.lead_group,
        stage="NEW",
        address=req.address,
        province=req.province,
        district=req.district,
        occupation=req.occupation,
        income_range=req.income_range,
        marital_status=req.marital_status,
        customer_type=req.customer_type,
        priority=req.priority,
        notes=req.notes,
        agent_id=agent.id,
        created_datetime=datetime.utcnow(),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.put("/{lead_id}", response_model=LeadResponse)
def update_lead(
    lead_id: int,
    updates: dict = Body(...),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")

    lead = db.query(LeadInfo).filter_by(id=lead_id, agent_id=agent.id).first()
    if not lead:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")

    allowed_fields = [
        "fullname", "phone", "email", "gender", "source",
        "lead_group", "stage", "address", "province", "district",
        "occupation", "income_range", "marital_status", "customer_type",
        "priority", "notes"
    ]

    for key, value in updates.items():
        if key in allowed_fields:
            if key == "stage" and value:
                value = value.upper()
            setattr(lead, key, value)

    db.commit()
    db.refresh(lead)
    return lead


@router.get("/{lead_id}", response_model=LeadResponse)
def get_lead_by_id(
    lead_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")

    lead = db.query(LeadInfo).filter_by(id=lead_id, agent_id=agent.id).first()
    if not lead:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    return lead


@router.delete("/{lead_id}")
def delete_lead(
    lead_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")

    lead = db.query(LeadInfo).filter_by(id=lead_id, agent_id=agent.id).first()
    if not lead:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    db.delete(lead)
    db.commit()
    return ApiResponse(message="Lead deleted")
