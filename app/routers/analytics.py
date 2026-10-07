"""Performance Analytics endpoints - phân tích hiệu suất cá nhân & đội nhóm.

Khác với /compensation/performance (gắn với payroll_period YYYY-MM và tiêu chí
thăng cấp), module này cho phép phân tích theo KHOẢNG THỜI GIAN TÙY Ý:
- Theo tháng, quý, năm (preset ở FE) hoặc
- Từ ngày X đến ngày Y bất kỳ.

Dữ liệu lấy trực tiếp từ contract_financial theo ngày phát hành (issued_date),
fallback ngày nộp (submit_date) nếu chưa có ngày phát hành.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_username
from app.models import AgentCredential, AgentDetail, UserRegister
from app.models_compensation import AgentAppointment, ContractFinancial
from app.rank import rank_label
from app import achievements as ach
from app import compensation as comp
from app.schemas_analytics import (
    AchievementsResponse, BadgeItem, K2KnowledgeItem, LeaderboardEntry,
    LeaderboardResponse, MemberPerformance, PeriodPoint,
    PerformanceAnalyticsResponse, PrivilegeItem, ProgressGoal,
)

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _is_admin(username: str, db: Session) -> bool:
    cred = db.query(AgentCredential).filter_by(username=username).first()
    return bool(cred and cred.role in ("ROLE_ADMIN", "ROLE_AGENT"))


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _effective_date_col():
    """Cột ngày hiệu lực để lọc: ưu tiên issued_date, fallback submit_date."""
    return func.coalesce(ContractFinancial.issued_date, ContractFinancial.submit_date)


def _agent_stats_in_range(
    db: Session, agent_id: int, start: date, end: date
) -> Tuple[Decimal, int]:
    """Tổng FYP quy đổi + số hợp đồng của 1 agent trong khoảng [start, end]."""
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
    count = int(row[1]) if row and row[1] is not None else 0
    return total, count


def _agent_series(
    db: Session, agent_id: int, start: date, end: date, granularity: str
) -> List[PeriodPoint]:
    """Chuỗi FYP theo tháng hoặc quý trong khoảng."""
    buckets = _time_buckets(start, end, granularity)
    points: List[PeriodPoint] = []
    for label, b_start, b_end in buckets:
        fyp, cnt = _agent_stats_in_range(db, agent_id, b_start, b_end)
        points.append(PeriodPoint(label=label, fyp=float(fyp), contracts=cnt))
    return points


def _time_buckets(start: date, end: date, granularity: str) -> List[Tuple[str, date, date]]:
    """Chia khoảng [start, end] thành các bucket theo tháng hoặc quý.

    Trả list (label, bucket_start, bucket_end) đã cắt theo biên start/end.
    """
    buckets: List[Tuple[str, date, date]] = []
    if granularity == "quarter":
        # bắt đầu từ quý chứa start
        q = (start.month - 1) // 3
        y = start.year
        cur = date(y, q * 3 + 1, 1)
        while cur <= end:
            q_idx = (cur.month - 1) // 3 + 1
            b_start = cur
            # cuối quý
            end_month = q_idx * 3
            b_end = _last_day(cur.year, end_month)
            label = f"Q{q_idx}/{cur.year}"
            buckets.append((label, max(b_start, start), min(b_end, end)))
            cur = _add_months(cur, 3)
    else:  # month (mặc định)
        cur = date(start.year, start.month, 1)
        while cur <= end:
            b_start = cur
            b_end = _last_day(cur.year, cur.month)
            label = f"{cur.year}-{cur.month:02d}"
            buckets.append((label, max(b_start, start), min(b_end, end)))
            cur = _add_months(cur, 1)
    return buckets


def _add_months(d: date, months: int) -> date:
    idx = d.month - 1 + months
    year = d.year + idx // 12
    month = idx % 12 + 1
    return date(year, month, 1)


def _last_day(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    nxt = date(year, month + 1, 1)
    return nxt - timedelta(days=1)


def _team_agent_ids(db: Session, leader_agent_id: int) -> List[Tuple[int, AgentDetail]]:
    """Danh sách (agent_id, AgentDetail) các thành viên đội nhóm (theo manage_id) đã có tài khoản."""
    regs = (
        db.query(UserRegister)
        .filter(UserRegister.manage_id == leader_agent_id)
        .filter(UserRegister.status.in_(["APPROVED", "ACTIVATED", "PROFILE_VERIFYING"]))
        .all()
    )
    result: List[Tuple[int, AgentDetail]] = []
    for reg in regs:
        if reg.email:
            linked = db.query(AgentDetail).filter_by(email=reg.email).first()
            if linked:
                result.append((linked.id, linked))
    return result


# ─── Endpoint ───────────────────────────────────────────────────────────────────

@router.get("/performance", response_model=PerformanceAnalyticsResponse)
def performance_analytics(
    from_date: str = Query(..., description="Ngày bắt đầu (dd/mm/yyyy hoặc yyyy-mm-dd)"),
    to_date: str = Query(..., description="Ngày kết thúc (dd/mm/yyyy hoặc yyyy-mm-dd)"),
    granularity: str = Query("month", description="month | quarter"),
    agent_id: Optional[int] = Query(None, description="Admin: xem agent bất kỳ"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Phân tích hiệu suất cá nhân + đội nhóm trong khoảng thời gian tùy ý.

    - TVV: chỉ xem của chính mình (agent_id bị bỏ qua).
    - Admin: có thể truyền agent_id để xem bất kỳ ai.
    """
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    if not start or not end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ngày không hợp lệ.")
    if end < start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ngày kết thúc phải >= ngày bắt đầu.")
    if granularity not in ("month", "quarter"):
        granularity = "month"

    is_admin = _is_admin(username, db)
    me = db.query(AgentDetail).filter_by(username=username).first()

    target_id = agent_id if (is_admin and agent_id) else (me.id if me else None)
    if not target_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không xác định được agent.")

    agent = db.query(AgentDetail).filter_by(id=target_id).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    # Cá nhân
    personal_fyp, personal_contracts = _agent_stats_in_range(db, agent.id, start, end)
    personal_series = _agent_series(db, agent.id, start, end, granularity)

    # Đội nhóm
    members_raw = _team_agent_ids(db, agent.id)
    members: List[MemberPerformance] = []
    team_fyp = Decimal("0")
    team_contracts = 0
    active_count = 0

    # Chuỗi thời gian đội nhóm: cộng dồn theo bucket
    buckets = _time_buckets(start, end, granularity)
    team_series_map = {label: PeriodPoint(label=label, fyp=0, contracts=0) for label, _, _ in buckets}

    for mid, mdetail in members_raw:
        m_fyp, m_cnt = _agent_stats_in_range(db, mid, start, end)
        team_fyp += m_fyp
        team_contracts += m_cnt
        is_active = m_fyp > 0
        if is_active:
            active_count += 1
        members.append(MemberPerformance(
            agent_id=mid,
            name=mdetail.full_name or mdetail.username,
            refer_code=mdetail.refer_code,
            rank=mdetail.rank,
            rank_label=rank_label(mdetail.rank),
            fyp=float(m_fyp),
            contracts=m_cnt,
            active=is_active,
        ))
        # cộng vào chuỗi thời gian đội nhóm
        for label, b_start, b_end in buckets:
            bf, bc = _agent_stats_in_range(db, mid, b_start, b_end)
            pt = team_series_map[label]
            pt.fyp += float(bf)
            pt.contracts += bc

    members.sort(key=lambda x: x.fyp, reverse=True)
    team_series = [team_series_map[label] for label, _, _ in buckets]

    return PerformanceAnalyticsResponse(
        agent_id=agent.id,
        agent_name=agent.full_name or agent.username,
        from_date=_fmt(start),
        to_date=_fmt(end),
        granularity=granularity,
        personal_fyp=float(personal_fyp),
        personal_contracts=personal_contracts,
        team_fyp=float(team_fyp),
        team_contracts=team_contracts,
        team_members=len(members_raw),
        active_team_members=active_count,
        total_fyp=float(personal_fyp + team_fyp),
        total_contracts=personal_contracts + team_contracts,
        personal_series=personal_series,
        team_series=team_series,
        members=members,
    )


# ─── Leaderboard (Bảng xếp hạng) ───────────────────────────────────────────────

_METRIC_LABELS = {
    "fyp": "FYP cá nhân",
    "contracts": "Số hợp đồng",
    "customers": "Số khách hàng",
    "team_fyp": "FYP đội nhóm",
    "promotions": "Thăng cấp trong kỳ",
    "k2": "K2 (duy trì hợp đồng)",
    "rising_star": "Ngôi sao đang lên",
}


def _fmt_short(v: float) -> str:
    if v >= 1_000_000_000:
        return f"{v/1_000_000_000:.2f} tỷ"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f} Tr"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def _all_active_agents(db: Session) -> List[AgentDetail]:
    return (
        db.query(AgentDetail)
        .filter(AgentDetail.delete_flag == False)  # noqa: E712
        .all()
    )


def _leaderboard_individual(db: Session, metric: str, start: date, end: date,
                            limit: int) -> List[LeaderboardEntry]:
    """Xếp hạng cá nhân theo fyp / contracts / customers."""
    eff = _effective_date_col()

    if metric == "customers":
        # Số khách hàng khác nhau (distinct lead_id, fallback customer_name) — đơn giản
        # hoá bằng distinct source theo contract. Ở đây đếm số HĐ distinct theo khách.
        value_col = func.count(func.distinct(ContractFinancial.contract_id))
    elif metric == "contracts":
        value_col = func.count(ContractFinancial.id)
    else:  # fyp
        value_col = func.coalesce(func.sum(ContractFinancial.fyp_converted), 0)

    rows = (
        db.query(ContractFinancial.agent_id, value_col.label("v"))
        .filter(eff != None)  # noqa: E711
        .filter(eff >= start)
        .filter(eff <= end)
        .filter(ContractFinancial.agent_id != None)  # noqa: E711
        .group_by(ContractFinancial.agent_id)
        .order_by(value_col.desc())
        .limit(limit)
        .all()
    )

    entries: List[LeaderboardEntry] = []
    for i, (agent_id, v) in enumerate(rows, start=1):
        agent = db.query(AgentDetail).filter_by(id=agent_id).first()
        val = float(v or 0)
        entries.append(LeaderboardEntry(
            rank=i, agent_id=agent_id,
            name=(agent.full_name or agent.username) if agent else f"#{agent_id}",
            refer_code=agent.refer_code if agent else None,
            rank_label=rank_label(agent.rank) if agent else None,
            value=val,
            value_label=(_fmt_short(val) if metric == "fyp" else f"{int(val)}"),
        ))
    return entries


def _leaderboard_team(db: Session, start: date, end: date, limit: int) -> List[LeaderboardEntry]:
    """Xếp hạng đội nhóm theo tổng FYP toàn nhánh của leader."""
    from app.routers.snapshot import _collect_branch  # tái dùng roll-up cây

    leaders = _all_active_agents(db)
    scored: List[Tuple[int, AgentDetail, Decimal, int]] = []
    for leader in leaders:
        branch = _collect_branch(db, leader.id)
        if not branch:
            continue  # chỉ xếp hạng người có đội nhóm
        total = _agent_stats_in_range(db, leader.id, start, end)[0]
        contracts = _agent_stats_in_range(db, leader.id, start, end)[1]
        for m in branch:
            f, c = _agent_stats_in_range(db, m.id, start, end)
            total += f
            contracts += c
        scored.append((leader.id, leader, total, contracts))

    scored.sort(key=lambda x: x[2], reverse=True)
    entries: List[LeaderboardEntry] = []
    for i, (aid, leader, total, contracts) in enumerate(scored[:limit], start=1):
        entries.append(LeaderboardEntry(
            rank=i, agent_id=aid,
            name=leader.full_name or leader.username,
            refer_code=leader.refer_code,
            rank_label=rank_label(leader.rank),
            value=float(total),
            value_label=_fmt_short(float(total)),
            extra={"contracts": contracts},
        ))
    return entries


def _leaderboard_promotions(db: Session, start: date, end: date,
                            limit: int) -> List[LeaderboardEntry]:
    """Danh sách agent thăng cấp thành công trong khoảng (theo agent_appointment)."""
    appts = (
        db.query(AgentAppointment)
        .filter(AgentAppointment.status == "CONFIRMED")
        .filter(AgentAppointment.confirmed_at != None)  # noqa: E711
        .all()
    )
    result = []
    for a in appts:
        if not a.confirmed_at:
            continue
        d = a.confirmed_at.date()
        if d < start or d > end:
            continue
        agent = db.query(AgentDetail).filter_by(id=a.agent_id).first()
        result.append((a, agent))

    # sắp theo cấp đạt được giảm dần, rồi thời gian
    result.sort(key=lambda x: (x[0].achieved_level or 0, x[0].confirmed_at), reverse=True)
    entries: List[LeaderboardEntry] = []
    for i, (a, agent) in enumerate(result[:limit], start=1):
        from app import compensation as comp
        achieved = a.achieved_level or a.target_level
        entries.append(LeaderboardEntry(
            rank=i, agent_id=a.agent_id,
            name=(agent.full_name or agent.username) if agent else f"#{a.agent_id}",
            refer_code=agent.refer_code if agent else None,
            rank_label=comp.level_to_rank(achieved),
            value=float(achieved),
            value_label=comp.level_to_rank(achieved) or str(achieved),
            extra={"confirmed_at": a.confirmed_at.strftime("%d/%m/%Y")},
        ))
    return entries


def _leaderboard_k2(db: Session, end: date, limit: int) -> List[LeaderboardEntry]:
    """Xếp hạng K2 theo snapshot tháng chứa `end`. Chỉ xếp agent đã có HĐ tới mốc."""
    from app.models_compensation import AgentMonthlySnapshot
    period = end.strftime("%Y-%m")
    snaps = (
        db.query(AgentMonthlySnapshot)
        .filter(AgentMonthlySnapshot.period == period)
        .all()
    )
    # Chỉ hiển thị agent có phát sinh doanh số (tránh toàn bộ K2=100% mặc định)
    scored = [s for s in snaps if (s.personal_fyp or 0) > 0]
    scored.sort(key=lambda s: (float(s.k2 or 0), float(s.personal_fyp or 0)), reverse=True)
    entries: List[LeaderboardEntry] = []
    for i, s in enumerate(scored[:limit], start=1):
        agent = db.query(AgentDetail).filter_by(id=s.agent_id).first()
        k2v = float(s.k2 or 0)
        entries.append(LeaderboardEntry(
            rank=i, agent_id=s.agent_id,
            name=(agent.full_name or agent.username) if agent else f"#{s.agent_id}",
            refer_code=agent.refer_code if agent else None,
            rank_label=rank_label(agent.rank) if agent else None,
            value=k2v, value_label=f"{k2v*100:.0f}%",
        ))
    return entries


def _leaderboard_rising_star(db: Session, start: date, end: date,
                             limit: int) -> List[LeaderboardEntry]:
    """Ngôi sao đang lên: % tăng trưởng FYP so với kỳ trước cùng độ dài.

    Ưu tiên người tăng trưởng mạnh (kể cả xuất phát nhỏ), miễn kỳ này có FYP > 0.
    """
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)

    agents = _all_active_agents(db)
    scored = []
    for a in agents:
        cur_fyp = _agent_stats_in_range(db, a.id, start, end)[0]
        if cur_fyp <= 0:
            continue
        prev_fyp = _agent_stats_in_range(db, a.id, prev_start, prev_end)[0]
        if prev_fyp <= 0:
            growth = Decimal("1")  # từ 0 -> có doanh số: coi như +100% (tân binh bùng nổ)
        else:
            growth = (cur_fyp - prev_fyp) / prev_fyp
        if growth <= 0:
            continue  # chỉ vinh danh người đang tăng
        scored.append((a, cur_fyp, prev_fyp, growth))

    scored.sort(key=lambda x: x[3], reverse=True)
    entries: List[LeaderboardEntry] = []
    for i, (a, cur, prev, growth) in enumerate(scored[:limit], start=1):
        entries.append(LeaderboardEntry(
            rank=i, agent_id=a.id,
            name=a.full_name or a.username, refer_code=a.refer_code,
            rank_label=rank_label(a.rank),
            value=float(growth),
            value_label=f"+{float(growth)*100:.0f}%",
            extra={"current_fyp": _fmt_short(float(cur)), "prev_fyp": _fmt_short(float(prev))},
        ))
    return entries


@router.get("/leaderboard", response_model=LeaderboardResponse)
def leaderboard(
    metric: str = Query("fyp", description="fyp|contracts|customers|team_fyp|promotions|k2|rising_star"),
    from_date: str = Query(..., description="Ngày bắt đầu"),
    to_date: str = Query(..., description="Ngày kết thúc"),
    limit: int = Query(10, ge=1, le=100),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Bảng xếp hạng cá nhân/đội nhóm theo nhiều tiêu chí.

    Ai đăng nhập cũng xem được (khích lệ thi đua). Dữ liệu toàn hệ thống.
    """
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    if not start or not end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ngày không hợp lệ.")
    if end < start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ngày kết thúc phải >= ngày bắt đầu.")

    metric = metric.lower()
    scope = "team" if metric == "team_fyp" else "individual"

    if metric == "team_fyp":
        entries = _leaderboard_team(db, start, end, limit)
    elif metric == "promotions":
        entries = _leaderboard_promotions(db, start, end, limit)
    elif metric == "k2":
        entries = _leaderboard_k2(db, end, limit)
    elif metric == "rising_star":
        entries = _leaderboard_rising_star(db, start, end, limit)
    elif metric in ("fyp", "contracts", "customers"):
        entries = _leaderboard_individual(db, metric, start, end, limit)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tiêu chí không hợp lệ.")

    return LeaderboardResponse(
        metric=metric,
        metric_label=_METRIC_LABELS.get(metric, metric),
        from_date=_fmt(start),
        to_date=_fmt(end),
        scope=scope,
        entries=entries,
    )


# ─── Achievements (Badges + Streak + Progress) & Đặc quyền ─────────────────────

def _month_bounds_today() -> Tuple[date, date]:
    t = date.today()
    start = t.replace(day=1)
    return start, t


@router.get("/achievements", response_model=AchievementsResponse)
def get_achievements(
    agent_id: Optional[int] = Query(None),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Huy hiệu, chuỗi (streak), thanh tiến độ mục tiêu và đặc quyền theo cấp.

    TVV xem của mình; admin có thể truyền agent_id.
    """
    is_admin = _is_admin(username, db)
    me = db.query(AgentDetail).filter_by(username=username).first()
    target_id = agent_id if (is_admin and agent_id) else (me.id if me else None)
    if not target_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không xác định được agent.")
    agent = db.query(AgentDetail).filter_by(id=target_id).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    level = comp.rank_to_level(agent.rank)
    m_start, today = _month_bounds_today()
    period = today.strftime("%Y-%m")

    # Số liệu tháng hiện tại
    fyp_month, contracts_month = _agent_stats_in_range(db, agent.id, m_start, today)
    contracts_total = (
        db.query(func.count(ContractFinancial.id))
        .filter(ContractFinancial.agent_id == agent.id)
        .scalar()
    ) or 0

    # HĐ đầu tiên trong 30 ngày kể từ ngày tham gia
    first_within_30d = False
    if agent.create_datetime:
        join_date = agent.create_datetime.date()
        eff = _effective_date_col()
        first_row = (
            db.query(func.min(eff))
            .filter(ContractFinancial.agent_id == agent.id)
            .filter(eff != None)  # noqa: E711
            .scalar()
        )
        if first_row:
            first_within_30d = (first_row - join_date).days <= 30

    # K2 + streak từ snapshot
    from app.routers.snapshot import _agent_k2, count_consecutive_months
    from app.models_compensation import AgentMonthlySnapshot
    k2 = _agent_k2(db, agent.id, period)
    consecutive = count_consecutive_months(db, agent.id, period)

    # Recruits có sản xuất trong tháng (thành viên F1 trực tiếp có FYP > 0)
    from app.routers.snapshot import _direct_members
    recruits_month = 0
    for mem in _direct_members(db, agent.id):
        f, _ = _agent_stats_in_range(db, mem.id, m_start, today)
        if f > 0:
            recruits_month += 1

    metrics = {
        "personal_fyp_month": fyp_month,
        "contracts_month": contracts_month,
        "contracts_total": contracts_total,
        "recruits_month": recruits_month,
        "k2": k2,
        "consecutive_months": consecutive,
        "first_contract_within_30d": first_within_30d,
    }

    badges = [BadgeItem(**b) for b in ach.evaluate_badges(metrics)]
    privileges = [PrivilegeItem(**p) for p in ach.evaluate_privileges(level)]

    # Thanh tiến độ: mục tiêu triệu phú tháng + mục tiêu lên cấp kế tiếp
    goals: List[ProgressGoal] = []
    goals.append(ProgressGoal(
        key="millionaire_month", label="Triệu phú FYP tháng",
        current=float(fyp_month), target=100_000_000, unit="vnd",
        hint=_goal_hint(Decimal("100000000"), fyp_month),
    ))
    nxt = comp.PROMOTION_CRITERIA.get((level or 0) + 1)
    if nxt:
        # dùng FYP đội cho mục tiêu thăng cấp (giống bảng thăng cấp)
        from app.routers.snapshot import build_snapshot_metrics
        sm = build_snapshot_metrics(db, agent, m_start, today)
        goals.append(ProgressGoal(
            key="next_rank_fyp", label=f"Doanh số đội lên {comp.level_to_rank((level or 0)+1)}",
            current=float(sm["team_fyp"]), target=float(nxt["fyp"]), unit="vnd",
            hint=_goal_hint(nxt["fyp"], Decimal(str(sm["team_fyp"]))),
        ))

    return AchievementsResponse(
        agent_id=agent.id, agent_name=agent.full_name or agent.username,
        level=level, rank_label=rank_label(agent.rank),
        consecutive_months=consecutive,
        badges=badges, privileges=privileges, goals=goals,
    )


def _goal_hint(target: Decimal, current: Decimal) -> str:
    remain = target - current
    if remain <= 0:
        return "Đã hoàn thành mục tiêu!"
    return f"Còn {_fmt_short(float(remain))} nữa để đạt mục tiêu."


@router.get("/k2-knowledge", response_model=List[K2KnowledgeItem])
def get_k2_knowledge(username: str = Depends(get_current_username)):
    """Kiến thức về K2 (FAQ) để hiển thị cho TVV."""
    return [K2KnowledgeItem(**k) for k in ach.K2_KNOWLEDGE]
