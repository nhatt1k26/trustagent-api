"""Job sinh snapshot hiệu suất theo tháng + xét thăng cấp dùng snapshot.

Đây là phần "tính theo chu kỳ & lưu DB": mỗi tháng chạy 1 lần để chụp lại các chỉ
số của từng agent (FYP cá nhân/đội, số thành viên theo cấp, FM+, nhánh lớn %, K2).
Snapshot phục vụ tiêu chí cần lịch sử liên tục (số tháng liên kề) và tăng tốc xét
thăng cấp.

Cách chạy: cron gọi POST /snapshot/run?period=YYYY-MM hằng tháng, hoặc admin bấm.
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import compensation as comp
from app.database import get_db
from app.deps import get_current_username
from app.models import AgentCredential, AgentDetail, UserRegister
from app.models_compensation import AgentMonthlySnapshot, ContractFinancial


def _agent_k2(db: Session, agent_id: int, period: str) -> Decimal:
    """K2 của agent tính đến `period`: xét các HĐ đã tới mốc K2_WINDOW_MONTHS.

    Mốc xét = ngày hiệu lực + K2_WINDOW_MONTHS tháng <= cuối kỳ `period`.
    K2 = FYP quy đổi các HĐ còn IN_FORCE / FYP quy đổi các HĐ đã tới mốc.
    """
    _, period_end = _period_bounds(period)
    fins = (
        db.query(ContractFinancial)
        .filter(ContractFinancial.agent_id == agent_id)
        .all()
    )
    evaluated = Decimal("0")
    in_force = Decimal("0")
    for f in fins:
        eff = f.effective_date or f.issued_date or f.submit_date
        if not eff:
            continue
        # mốc xét persistency = ngày hiệu lực + K2_WINDOW_MONTHS tháng
        maturity = comp._add_months(eff, comp.K2_WINDOW_MONTHS)
        if maturity > period_end:
            continue  # chưa tới mốc xét
        fyp = Decimal(str(f.fyp_converted or 0))
        evaluated += fyp
        if comp.is_in_force(f.lifecycle_status):
            in_force += fyp
    return comp.compute_k2(evaluated, in_force)

router = APIRouter(prefix="/api/v1/snapshot", tags=["snapshot"])


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _require_admin(username: str, db: Session) -> AgentCredential:
    cred = db.query(AgentCredential).filter_by(username=username).first()
    if not cred or cred.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")
    return cred


def _period_bounds(period: str) -> Tuple[date, date]:
    """YYYY-MM -> (ngày đầu tháng, ngày cuối tháng)."""
    y, m = period.split("-")
    y, m = int(y), int(m)
    start = date(y, m, 1)
    if m == 12:
        end = date(y, 12, 31)
    else:
        end = date(y, m + 1, 1) - timedelta(days=1)
    return start, end


def _effective_date_col():
    return func.coalesce(ContractFinancial.issued_date, ContractFinancial.submit_date)


def _agent_fyp_in_range(db: Session, agent_id: int, start: date, end: date) -> Tuple[Decimal, int]:
    eff = _effective_date_col()
    row = (
        db.query(
            func.coalesce(func.sum(ContractFinancial.fyp_converted), 0),
            func.count(ContractFinancial.id),
        )
        .filter(ContractFinancial.agent_id == agent_id)
        .filter(eff != None)  # noqa: E711
        .filter(eff >= start)
        .filter(eff <= end)
        .first()
    )
    total = Decimal(str(row[0])) if row and row[0] is not None else Decimal("0")
    cnt = int(row[1]) if row and row[1] is not None else 0
    return total, cnt


def _direct_members(db: Session, leader_id: int) -> List[AgentDetail]:
    """Thành viên F1 (tuyến trực tiếp theo manage_id) đã có tài khoản agent."""
    regs = (
        db.query(UserRegister)
        .filter(UserRegister.manage_id == leader_id)
        .filter(UserRegister.status.in_(["APPROVED", "ACTIVATED", "PROFILE_VERIFYING"]))
        .all()
    )
    result: List[AgentDetail] = []
    for reg in regs:
        if reg.email:
            linked = db.query(AgentDetail).filter_by(email=reg.email).first()
            if linked:
                result.append(linked)
    return result


def _collect_branch(db: Session, leader_id: int, max_depth: int = 8) -> List[AgentDetail]:
    """Thu thập TOÀN NHÁNH (mọi tầng) của leader theo manage_id, chống lặp vô hạn."""
    result: List[AgentDetail] = []
    visited = {leader_id}
    frontier = [leader_id]
    depth = 0
    while frontier and depth < max_depth:
        next_frontier: List[int] = []
        for lid in frontier:
            for m in _direct_members(db, lid):
                if m.id in visited:
                    continue
                visited.add(m.id)
                result.append(m)
                next_frontier.append(m.id)
        frontier = next_frontier
        depth += 1
    return result


def build_snapshot_metrics(db: Session, agent: AgentDetail, start: date, end: date) -> dict:
    """Tính toàn bộ chỉ số của 1 agent trong 1 tháng (dùng cho snapshot & xét cấp)."""
    personal_fyp, personal_contracts = _agent_fyp_in_range(db, agent.id, start, end)

    # Toàn nhánh
    branch = _collect_branch(db, agent.id)
    downline_by_level: Dict[int, int] = {}
    fm_plus = 0
    agent_codes = 0
    team_fyp = Decimal("0")
    active_members = 0

    for m in branch:
        lvl = comp.rank_to_level(m.rank) or 1
        downline_by_level[lvl] = downline_by_level.get(lvl, 0) + 1
        if lvl >= comp.OVERRIDING_MIN_LEVEL:  # FM trở lên
            fm_plus += 1
        if m.refer_code:
            agent_codes += 1
        m_fyp, _ = _agent_fyp_in_range(db, m.id, start, end)
        team_fyp += m_fyp
        if m_fyp > 0:
            active_members += 1

    # Nhánh lớn %: doanh số của nhánh F1 lớn nhất / tổng doanh số toàn nhánh
    big_branch_pct = Decimal("0")
    directs = _direct_members(db, agent.id)
    if directs and team_fyp > 0:
        branch_totals: List[Decimal] = []
        for d in directs:
            sub = _collect_branch(db, d.id)
            total = _agent_fyp_in_range(db, d.id, start, end)[0]
            for s in sub:
                total += _agent_fyp_in_range(db, s.id, start, end)[0]
            branch_totals.append(total)
        max_branch = max(branch_totals) if branch_totals else Decimal("0")
        if team_fyp > 0:
            big_branch_pct = (max_branch / team_fyp).quantize(Decimal("0.0001"))

    # K2 tính thật từ vòng đời hợp đồng (persistency 13 tháng)
    period = end.strftime("%Y-%m")
    k2 = _agent_k2(db, agent.id, period)

    return {
        "personal_fyp": personal_fyp,
        "personal_contracts": personal_contracts,
        "team_fyp": team_fyp,
        "total_members": len(branch),
        "active_members": active_members,
        "fm_plus": fm_plus,
        "downline_by_level": downline_by_level,
        "agent_codes": agent_codes,
        "big_branch_pct": big_branch_pct,
        "k2": k2,
    }


# ─── Endpoint ───────────────────────────────────────────────────────────────────

@router.post("/run")
def run_snapshot(
    period: str = Query(..., description="Kỳ YYYY-MM"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Sinh/cập nhật snapshot hiệu suất tháng cho toàn bộ agent (upsert theo agent+period)."""
    _require_admin(username, db)
    start, end = _period_bounds(period)

    agents = db.query(AgentDetail).filter(AgentDetail.delete_flag == False).all()  # noqa: E712
    count = 0
    for agent in agents:
        metrics = build_snapshot_metrics(db, agent, start, end)
        snap = (
            db.query(AgentMonthlySnapshot)
            .filter_by(agent_id=agent.id, period=period)
            .first()
        )
        if not snap:
            snap = AgentMonthlySnapshot(agent_id=agent.id, period=period)
            db.add(snap)
        snap.personal_fyp = metrics["personal_fyp"]
        snap.team_fyp = metrics["team_fyp"]
        snap.personal_contracts = metrics["personal_contracts"]
        snap.total_members = metrics["total_members"]
        snap.active_members = metrics["active_members"]
        snap.fm_plus_members = metrics["fm_plus"]
        snap.downline_by_level = json.dumps({str(k): v for k, v in metrics["downline_by_level"].items()})
        snap.agent_codes = metrics["agent_codes"]
        snap.big_branch_pct = metrics["big_branch_pct"]
        snap.k2 = metrics["k2"]
        snap.rank_at_snapshot = agent.rank
        count += 1

    db.commit()
    return {"message": f"Đã sinh snapshot kỳ {period} cho {count} agent.", "count": count}


def count_consecutive_months(db: Session, agent_id: int, up_to_period: str,
                             min_fyp: Decimal = Decimal("1")) -> int:
    """Đếm số tháng liên kề (tính đến up_to_period) agent có doanh số >= min_fyp.

    Dựa trên snapshot đã lưu; nếu thiếu snapshot thì dừng đếm.
    """
    y, m = up_to_period.split("-")
    y, m = int(y), int(m)
    streak = 0
    for _ in range(60):  # tối đa 60 tháng
        p = f"{y}-{m:02d}"
        snap = db.query(AgentMonthlySnapshot).filter_by(agent_id=agent_id, period=p).first()
        if not snap or Decimal(str(snap.personal_fyp or 0)) < min_fyp:
            break
        streak += 1
        m -= 1
        if m < 1:
            m = 12
            y -= 1
    return streak
