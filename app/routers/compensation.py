"""Compensation module endpoints - Lương / Thưởng / Hoa hồng / Bổ nhiệm.

Nhóm chức năng:
1. /config        - Xem cấu hình quy tắc chi trả (read-only) + seed.
2. /financial     - Nhập/xem dữ liệu tài chính chi tiết của hợp đồng.
3. /payroll       - Tính & lưu kết quả thù lao theo kỳ; xem lại kết quả.
4. /performance   - Perf cá nhân/đội ngũ + chỉ số cần để thăng cấp.
5. /appointment   - Bổ nhiệm/onboard: admin giao cấp thử thách, cập nhật trạng thái.
"""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import compensation as comp
from app.database import get_db
from app.deps import get_current_username
from app.models import AgentCredential, AgentDetail, Contract, UserRegister
from app.models_compensation import (
    AgentAppointment, CommissionRule, ContractFinancial, IncomeLine, PayrollResult,
)
from app.notification_service import create_notification
from app.rank import rank_label
from app.schemas_compensation import (
    AppointmentResponse, CalculatePayrollRequest, ChallengeDurationOption,
    CommissionConfigResponse, ContractFinancialRequest, ContractFinancialResponse,
    CreateAppointmentRequest, IncomeBreakdownResponse, IncomeLineItem,
    PayrollResultItem, PayrollSummaryResponse, PerformanceResponse,
    PromotionCriteriaResponse, ScanDueResponse, UpdateAppointmentStatusRequest,
    UpdateLifecycleRequest, ContractFinancialListItem,
)

router = APIRouter(prefix="/api/v1/compensation", tags=["compensation"])


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _require_admin(username: str, db: Session) -> AgentCredential:
    cred = db.query(AgentCredential).filter_by(username=username).first()
    if not cred or cred.role not in ("ROLE_ADMIN", "ROLE_AGENT"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Không có quyền truy cập.")
    return cred


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _fmt_date(d: Optional[date]) -> Optional[str]:
    return d.strftime("%d/%m/%Y") if d else None


def _to_dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _agent_by_id(db: Session, agent_id: int) -> Optional[AgentDetail]:
    return db.query(AgentDetail).filter_by(id=agent_id).first()


# ─── 1. Cấu hình quy tắc chi trả ───────────────────────────────────────────────

@router.post("/config/seed")
def seed_rules(username: str = Depends(get_current_username), db: Session = Depends(get_db)):
    """Seed bộ quy tắc mặc định vào bảng commission_rule (idempotent)."""
    _require_admin(username, db)
    inserted = 0
    for r in comp.default_rule_seed():
        exists = db.query(CommissionRule).filter_by(rule_key=r["rule_key"]).first()
        if exists:
            continue
        db.add(CommissionRule(
            category=r["category"],
            rule_key=r["rule_key"],
            level=r["level"],
            threshold=r["threshold"],
            rate=r["rate"],
            note=r["note"],
            is_active=True,
        ))
        inserted += 1
    db.commit()
    return {"message": f"Đã seed {inserted} quy tắc mới.", "inserted": inserted}


@router.get("/config", response_model=CommissionConfigResponse)
def get_config(username: str = Depends(get_current_username), db: Session = Depends(get_db)):
    """Xem toàn bộ cấu hình quy tắc, gom nhóm theo category (read-only)."""
    rules = db.query(CommissionRule).filter_by(is_active=True).order_by(
        CommissionRule.category.asc(), CommissionRule.id.asc()
    ).all()

    categories: dict = defaultdict(list)
    for r in rules:
        categories[r.category].append({
            "id": r.id,
            "category": r.category,
            "rule_key": r.rule_key,
            "level": r.level,
            "threshold": float(r.threshold) if r.threshold is not None else None,
            "rate": float(r.rate) if r.rate is not None else None,
            "note": r.note,
            "is_active": r.is_active,
        })

    return CommissionConfigResponse(categories=dict(categories), rank_levels=comp.RANK_TO_LEVEL)


# ─── 2. Dữ liệu tài chính hợp đồng ─────────────────────────────────────────────

def _resolve_level(db: Session, agent_id: Optional[int], override: Optional[int]) -> Optional[int]:
    if override is not None:
        return override
    agent = _agent_by_id(db, agent_id) if agent_id else None
    return comp.rank_to_level(agent.rank) if agent else None


def _appointment_status_for(db: Session, agent_id: Optional[int]) -> Optional[str]:
    if not agent_id:
        return None
    appt = (
        db.query(AgentAppointment)
        .filter_by(agent_id=agent_id)
        .order_by(AgentAppointment.assigned_at.desc())
        .first()
    )
    return appt.status if appt else None


def _serialize_financial(f: ContractFinancial) -> dict:
    return {
        "id": f.id,
        "contract_id": f.contract_id,
        "agent_id": f.agent_id,
        "direct_referrer_id": f.direct_referrer_id,
        "submit_date": _fmt_date(f.submit_date),
        "issued_date": _fmt_date(f.issued_date),
        "fyp_main": float(f.fyp_main or 0),
        "fyp_supplementary": float(f.fyp_supplementary or 0),
        "conversion_factor": float(f.conversion_factor or 1),
        "fyp_converted": float(f.fyp_converted or 0),
        "level_at_contract": f.level_at_contract,
        "appointment_status": f.appointment_status,
        "payroll_period": f.payroll_period,
        "lifecycle_status": f.lifecycle_status,
        "effective_date": _fmt_date(f.effective_date),
        "last_paid_period": f.last_paid_period,
        "lapsed_date": _fmt_date(f.lapsed_date),
        "lifecycle_note": f.lifecycle_note,
        "note": f.note,
    }


@router.post("/financial", response_model=ContractFinancialResponse)
def upsert_financial(
    body: ContractFinancialRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Nhập/cập nhật dữ liệu tài chính cho một hợp đồng (dùng để tính thù lao)."""
    _require_admin(username, db)

    contract = db.query(Contract).filter_by(id=body.contract_id).first()
    if not contract:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hợp đồng không tồn tại.")

    fin = db.query(ContractFinancial).filter_by(contract_id=body.contract_id).first()
    is_new = fin is None
    if is_new:
        fin = ContractFinancial(contract_id=body.contract_id)

    agent_id = contract.agent_id
    fin.agent_id = agent_id
    fin.direct_referrer_id = body.direct_referrer_id
    fin.submit_date = _parse_date(body.submit_date)
    fin.issued_date = _parse_date(body.issued_date)
    fin.fyp_main = _to_dec(body.fyp_main)
    fin.fyp_supplementary = _to_dec(body.fyp_supplementary)
    fin.conversion_factor = _to_dec(body.conversion_factor) or Decimal("1")

    # FYP quy đổi = (chính + bổ trợ) * hệ số quy đổi
    fin.fyp_converted = ((fin.fyp_main + fin.fyp_supplementary) * fin.conversion_factor).quantize(Decimal("1"))

    fin.level_at_contract = _resolve_level(db, agent_id, body.level_at_contract)
    fin.appointment_status = body.appointment_status or _appointment_status_for(db, agent_id)

    # Kỳ chi trả suy từ ngày phát hành (fallback ngày nộp)
    ref_date = fin.issued_date or fin.submit_date
    fin.payroll_period = ref_date.strftime("%Y-%m") if ref_date else None

    # Vòng đời: mặc định IN_FORCE + ngày hiệu lực (auto duy trì). Không ghi đè nếu
    # hợp đồng đã bị đánh dấu biến cố (LAPSED/CANCELLED/SURRENDERED).
    if is_new or not fin.lifecycle_status:
        fin.lifecycle_status = comp.LIFECYCLE_IN_FORCE
    if not fin.effective_date:
        fin.effective_date = fin.issued_date or fin.submit_date

    fin.note = body.note
    fin.updated_datetime = datetime.utcnow()

    if is_new:
        db.add(fin)
    db.commit()
    db.refresh(fin)
    return _serialize_financial(fin)


@router.get("/financial", response_model=List[ContractFinancialListItem])
def list_financials(
    lifecycle: Optional[str] = Query(None, description="Lọc theo trạng thái vòng đời"),
    search: Optional[str] = Query(None, description="Tìm theo tên KH"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin: danh sách hợp đồng đã có dữ liệu tài chính, để quản lý vòng đời (K2)."""
    _require_admin(username, db)

    q = db.query(ContractFinancial)
    if lifecycle:
        q = q.filter(ContractFinancial.lifecycle_status == lifecycle.upper())
    fins = q.order_by(ContractFinancial.updated_datetime.desc().nullslast(),
                      ContractFinancial.id.desc()).all()

    items: List[ContractFinancialListItem] = []
    for f in fins:
        customer = _contract_customer(db, f.contract_id)
        if search and search.strip():
            if search.strip().lower() not in (customer or "").lower():
                continue
        agent = _agent_by_id(db, f.agent_id) if f.agent_id else None
        items.append(ContractFinancialListItem(
            contract_id=f.contract_id,
            agent_id=f.agent_id,
            agent_name=agent.full_name if agent else None,
            customer_name=customer,
            fyp_converted=float(f.fyp_converted or 0),
            issued_date=_fmt_date(f.issued_date),
            effective_date=_fmt_date(f.effective_date),
            lifecycle_status=f.lifecycle_status or comp.LIFECYCLE_IN_FORCE,
            lapsed_date=_fmt_date(f.lapsed_date),
            lifecycle_note=f.lifecycle_note,
            payroll_period=f.payroll_period,
        ))
    return items


@router.get("/financial/{contract_id}", response_model=ContractFinancialResponse)
def get_financial(
    contract_id: int,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    fin = db.query(ContractFinancial).filter_by(contract_id=contract_id).first()
    if not fin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chưa có dữ liệu tài chính cho hợp đồng này.")
    return _serialize_financial(fin)


@router.put("/financial/{contract_id}/lifecycle", response_model=ContractFinancialResponse)
def update_contract_lifecycle(
    contract_id: int,
    body: UpdateLifecycleRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin đánh dấu biến cố vòng đời hợp đồng (chỉ dùng khi có thay đổi).

    Auto duy trì: hợp đồng mặc định IN_FORCE, không cần thao tác gì để tiếp tục.
    Chỉ gọi endpoint này khi hợp đồng LAPSED/CANCELLED/SURRENDERED (hoặc phục hồi
    IN_FORCE). Trạng thái này ảnh hưởng K2 khi job snapshot chạy.
    """
    _require_admin(username, db)

    new_status = (body.lifecycle_status or "").upper()
    if new_status not in comp.LIFECYCLE_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Trạng thái không hợp lệ. Cho phép: {', '.join(sorted(comp.LIFECYCLE_STATUSES))}.",
        )

    fin = db.query(ContractFinancial).filter_by(contract_id=contract_id).first()
    if not fin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chưa có dữ liệu tài chính cho hợp đồng này.")

    fin.lifecycle_status = new_status
    if new_status == comp.LIFECYCLE_IN_FORCE:
        # phục hồi -> xoá dấu biến cố
        fin.lapsed_date = None
    else:
        fin.lapsed_date = _parse_date(body.lapsed_date) or date.today()
    fin.lifecycle_note = body.note
    fin.updated_datetime = datetime.utcnow()
    db.commit()
    db.refresh(fin)
    return _serialize_financial(fin)


# ─── 3. Tính & lưu payroll ─────────────────────────────────────────────────────

def _period_bounds(period: str) -> tuple[str, str]:
    """YYYY-MM -> (quý key 'YYYY-Q', năm 'YYYY')."""
    year, month = period.split("-")
    quarter = (int(month) - 1) // 3 + 1
    return f"{year}-Q{quarter}", year


def _agent_period_fyp(db: Session, agent_id: int, prefix: str) -> Decimal:
    """Tổng FYP quy đổi của agent với payroll_period bắt đầu bằng prefix (YYYY-MM / YYYY)."""
    rows = (
        db.query(ContractFinancial)
        .filter(ContractFinancial.agent_id == agent_id)
        .filter(ContractFinancial.payroll_period.like(f"{prefix}%"))
        .all()
    )
    return sum((_to_dec(r.fyp_converted) for r in rows), Decimal("0"))


def _agent_quarter_fyp(db: Session, agent_id: int, period: str) -> Decimal:
    year, month = period.split("-")
    quarter = (int(month) - 1) // 3 + 1
    months = range((quarter - 1) * 3 + 1, quarter * 3 + 1)
    total = Decimal("0")
    for m in months:
        total += _agent_period_fyp(db, agent_id, f"{year}-{m:02d}")
    return total


def _calc_one(db: Session, fin: ContractFinancial, period: str) -> PayrollResult:
    """Tính thù lao cho 1 hợp đồng trong kỳ."""
    fyp = _to_dec(fin.fyp_converted)
    level = fin.level_at_contract

    rate = comp.personal_commission_rate(level)
    personal = (fyp * rate).quantize(Decimal("1"))

    sxn_amount, sxn_win = comp.sxn_bonus(fyp, fin.submit_date, fin.issued_date)

    # Thưởng tháng/quý/năm dựa trên lũy kế của agent (tính 1 lần trên tổng, gán vào dòng đầu
    # sẽ gây trùng => ở đây tính theo tổng kỳ và chỉ cộng ở bước tổng hợp). Để đơn giản &
    # minh bạch từng dòng, ta để 0 ở dòng chi tiết và cộng khoản lũy kế ở summary.
    monthly_b = Decimal("0")
    quarterly_b = Decimal("0")
    yearly_b = Decimal("0")
    recruitment_b = Decimal("0")

    gross = personal + sxn_amount + monthly_b + quarterly_b + yearly_b + recruitment_b
    pit, fund = comp.deductions(gross)
    net = gross - pit - fund

    return PayrollResult(
        payroll_period=period,
        agent_id=fin.agent_id,
        contract_id=fin.contract_id,
        customer_name=_contract_customer(db, fin.contract_id),
        level=level,
        appointment_status=fin.appointment_status,
        fyp_converted=fyp,
        personal_rate=rate,
        personal_commission=personal,
        sxn_bonus=sxn_amount,
        sxn_window=sxn_win,
        monthly_bonus=monthly_b,
        quarterly_bonus=quarterly_b,
        yearly_bonus=yearly_b,
        recruitment_bonus=recruitment_b,
        gross_total=gross,
        pit_amount=pit,
        fund_amount=fund,
        net_total=net,
        calculated_at=datetime.utcnow(),
    )


def _contract_customer(db: Session, contract_id: Optional[int]) -> Optional[str]:
    if not contract_id:
        return None
    c = db.query(Contract).filter_by(id=contract_id).first()
    return c.customer_name if c else None


@router.post("/payroll/calculate")
def calculate_payroll(
    body: CalculatePayrollRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Tính & lưu kết quả thù lao cho một kỳ (YYYY-MM). Ghi đè kết quả cũ của kỳ đó."""
    _require_admin(username, db)
    period = body.payroll_period

    q = db.query(ContractFinancial).filter(ContractFinancial.payroll_period == period)
    if body.agent_id:
        q = q.filter(ContractFinancial.agent_id == body.agent_id)
    fins = q.all()

    if not fins:
        return {"message": "Không có hợp đồng nào trong kỳ.", "count": 0}

    # Xoá kết quả cũ của kỳ (và agent nếu chỉ định) để tính lại sạch
    del_q = db.query(PayrollResult).filter(PayrollResult.payroll_period == period)
    if body.agent_id:
        del_q = del_q.filter(PayrollResult.agent_id == body.agent_id)
    del_q.delete(synchronize_session=False)

    results: List[PayrollResult] = []
    for fin in fins:
        if not fin.agent_id:
            continue
        results.append(_calc_one(db, fin, period))

    db.add_all(results)

    # Sinh income_line chi tiết (truy vết nguồn gốc) — xoá cũ trước
    il_del = db.query(IncomeLine).filter(IncomeLine.payroll_period == period)
    if body.agent_id:
        il_del = il_del.filter(IncomeLine.agent_id == body.agent_id)
    il_del.delete(synchronize_session=False)

    lines = _build_income_lines(db, period, fins)
    if lines:
        db.add_all(lines)

    db.commit()

    return {
        "message": f"Đã tính {len(results)} dòng thù lao & {len(lines)} dòng thu nhập chi tiết cho kỳ {period}.",
        "count": len(results),
        "income_lines": len(lines),
    }


def _upline_chain(db: Session, agent_id: int, max_up: int = 8) -> List[AgentDetail]:
    """Chuỗi tuyến trên trực tiếp của agent (theo manage_id), từ gần đến xa."""
    chain: List[AgentDetail] = []
    seen = {agent_id}
    current = agent_id
    for _ in range(max_up):
        agent = _agent_by_id(db, current)
        if not agent or not agent.email:
            break
        reg = db.query(UserRegister).filter_by(email=agent.email).first()
        if not reg or not reg.manage_id or reg.manage_id in seen:
            break
        leader = _agent_by_id(db, reg.manage_id)
        if not leader:
            break
        chain.append(leader)
        seen.add(leader.id)
        current = leader.id
    return chain


def _build_income_lines(db: Session, period: str, fins: List[ContractFinancial]) -> List[IncomeLine]:
    """Sinh các dòng thu nhập chi tiết từ danh sách hợp đồng trong kỳ.

    Gồm: thù lao cá nhân + SXN (theo từng HĐ) và OVERRIDING (chênh lệch quản lý,
    roll-up lên các tuyến trên với compression).
    """
    lines: List[IncomeLine] = []

    for fin in fins:
        if not fin.agent_id:
            continue
        fyp = _to_dec(fin.fyp_converted)
        if fyp <= 0:
            continue
        level = fin.level_at_contract
        own_rate = comp.personal_commission_rate(level)
        customer = _contract_customer(db, fin.contract_id)
        owner = _agent_by_id(db, fin.agent_id)
        owner_name = owner.full_name if owner else None

        # 1) Thù lao cá nhân của người tạo hợp đồng
        personal = (fyp * own_rate).quantize(Decimal("1"))
        lines.append(IncomeLine(
            payroll_period=period, agent_id=fin.agent_id, category="PERSONAL_COMMISSION",
            amount=personal, rate=own_rate, base_fyp=fyp,
            source_contract_id=fin.contract_id, source_customer_name=customer,
            description=f"Thù lao cá nhân {float(own_rate)*100:.0f}% trên FYP quy đổi HĐ {customer or ''}",
        ))

        # 2) Thưởng SXN của người tạo hợp đồng
        sxn_amount, sxn_win = comp.sxn_bonus(fyp, fin.submit_date, fin.issued_date)
        if sxn_amount > 0:
            lines.append(IncomeLine(
                payroll_period=period, agent_id=fin.agent_id, category="SXN_BONUS",
                amount=sxn_amount, rate=comp.SXN_RATE, base_fyp=fyp,
                source_contract_id=fin.contract_id, source_customer_name=customer,
                description=f"Thưởng SXN ({sxn_win}) HĐ {customer or ''}",
            ))

        # 3) OVERRIDING: roll-up lên các tuyến trên với compression
        prev_rate = own_rate  # mức % đã được hưởng bởi tầng thấp hơn
        for leader in _upline_chain(db, fin.agent_id):
            up_level = comp.rank_to_level(leader.rank)
            diff = comp.overriding_rate_diff(up_level, prev_rate)
            if diff > 0:
                ov_amount = (fyp * diff).quantize(Decimal("1"))
                if ov_amount > 0:
                    lines.append(IncomeLine(
                        payroll_period=period, agent_id=leader.id, category="OVERRIDING",
                        amount=ov_amount, rate=diff, base_fyp=fyp,
                        source_contract_id=fin.contract_id, source_customer_name=customer,
                        source_agent_id=fin.agent_id, source_agent_name=owner_name,
                        source_level=level,
                        description=f"Chênh lệch quản lý {float(diff)*100:.1f}% trên HĐ của "
                                    f"{owner_name or ''} (KH {customer or ''})",
                    ))
                # nâng mức đã hưởng lên rate của leader này (compression)
                prev_rate = comp.personal_commission_rate(up_level)
            # nếu diff = 0 (leader cấp thấp/không đủ) thì bỏ qua, giữ prev_rate

    return lines


def _summary_from_results(db: Session, agent_id: int, period: str,
                          rows: List[PayrollResult]) -> PayrollSummaryResponse:
    agent = _agent_by_id(db, agent_id)

    total_fyp = sum((_to_dec(r.fyp_converted) for r in rows), Decimal("0"))
    total_personal = sum((_to_dec(r.personal_commission) for r in rows), Decimal("0"))
    total_sxn = sum((_to_dec(r.sxn_bonus) for r in rows), Decimal("0"))

    # Khoản lũy kế tính 1 lần trên tổng kỳ
    monthly_b = comp.monthly_bonus(total_fyp)
    quarter_fyp = _agent_quarter_fyp(db, agent_id, period)
    quarterly_b = comp.quarterly_bonus(quarter_fyp)
    year = period.split("-")[0]
    year_fyp = _agent_period_fyp(db, agent_id, year)
    yearly_b = comp.yearly_bonus(year_fyp)

    total_bonus = total_sxn + monthly_b + quarterly_b + yearly_b
    total_gross = total_personal + total_bonus
    total_pit, total_fund = comp.deductions(total_gross)
    total_net = total_gross - total_pit - total_fund

    lines = [PayrollResultItem(
        id=r.id, payroll_period=r.payroll_period, agent_id=r.agent_id, contract_id=r.contract_id,
        customer_name=r.customer_name, level=r.level, appointment_status=r.appointment_status,
        fyp_converted=float(r.fyp_converted or 0), personal_rate=float(r.personal_rate or 0),
        personal_commission=float(r.personal_commission or 0), sxn_bonus=float(r.sxn_bonus or 0),
        sxn_window=r.sxn_window, monthly_bonus=float(r.monthly_bonus or 0),
        quarterly_bonus=float(r.quarterly_bonus or 0), yearly_bonus=float(r.yearly_bonus or 0),
        recruitment_bonus=float(r.recruitment_bonus or 0), gross_total=float(r.gross_total or 0),
        pit_amount=float(r.pit_amount or 0), fund_amount=float(r.fund_amount or 0),
        net_total=float(r.net_total or 0), note=r.note,
    ) for r in rows]

    return PayrollSummaryResponse(
        payroll_period=period,
        agent_id=agent_id,
        agent_name=agent.full_name if agent else None,
        total_fyp_converted=float(total_fyp),
        total_personal_commission=float(total_personal),
        total_bonus=float(total_bonus),
        total_gross=float(total_gross),
        total_pit=float(total_pit),
        total_fund=float(total_fund),
        total_net=float(total_net),
        lines=lines,
    )


@router.get("/payroll", response_model=PayrollSummaryResponse)
def get_payroll(
    period: str = Query(..., description="Kỳ YYYY-MM"),
    agent_id: Optional[int] = Query(None),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Xem kết quả thù lao đã lưu. TVV chỉ xem của mình; admin xem theo agent_id."""
    agent = db.query(AgentDetail).filter_by(username=username).first()
    cred = db.query(AgentCredential).filter_by(username=username).first()
    is_admin = cred and cred.role in ("ROLE_ADMIN", "ROLE_AGENT")

    target_id = agent_id if (is_admin and agent_id) else (agent.id if agent else None)
    if not target_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không xác định được agent.")

    rows = (
        db.query(PayrollResult)
        .filter_by(payroll_period=period, agent_id=target_id)
        .order_by(PayrollResult.id.asc())
        .all()
    )
    return _summary_from_results(db, target_id, period, rows)


@router.get("/income/breakdown", response_model=IncomeBreakdownResponse)
def get_income_breakdown(
    period: str = Query(..., description="Kỳ YYYY-MM"),
    agent_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None, description="Lọc theo loại khoản"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Chi tiết từng khoản thu nhập của agent trong kỳ, có truy vết nguồn gốc.

    Mỗi dòng cho biết: loại khoản, số tiền, đến từ hợp đồng nào, (overriding) từ
    tuyến dưới nào. TVV chỉ xem của mình; admin xem theo agent_id.
    """
    me = db.query(AgentDetail).filter_by(username=username).first()
    cred = db.query(AgentCredential).filter_by(username=username).first()
    is_admin = cred and cred.role in ("ROLE_ADMIN", "ROLE_AGENT")

    target_id = agent_id if (is_admin and agent_id) else (me.id if me else None)
    if not target_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không xác định được agent.")

    q = db.query(IncomeLine).filter_by(payroll_period=period, agent_id=target_id)
    if category:
        q = q.filter(IncomeLine.category == category.upper())
    rows = q.order_by(IncomeLine.category.asc(), IncomeLine.id.asc()).all()

    by_category: dict = {}
    total = Decimal("0")
    for r in rows:
        amt = _to_dec(r.amount)
        total += amt
        by_category[r.category] = float(_to_dec(by_category.get(r.category, 0)) + amt)

    agent = _agent_by_id(db, target_id)
    items = [IncomeLineItem(
        id=r.id, payroll_period=r.payroll_period, agent_id=r.agent_id, category=r.category,
        amount=float(r.amount or 0), rate=float(r.rate) if r.rate is not None else None,
        base_fyp=float(r.base_fyp) if r.base_fyp is not None else None,
        source_contract_id=r.source_contract_id, source_customer_name=r.source_customer_name,
        source_agent_id=r.source_agent_id, source_agent_name=r.source_agent_name,
        source_level=r.source_level, description=r.description,
    ) for r in rows]

    return IncomeBreakdownResponse(
        payroll_period=period,
        agent_id=target_id,
        agent_name=agent.full_name if agent else None,
        total_amount=float(total),
        by_category=by_category,
        lines=items,
    )


# ─── 4. Performance & thăng cấp ────────────────────────────────────────────────

def _team_agent_ids(db: Session, leader_agent_id: int) -> List[int]:
    """Lấy id các agent trong đội ngũ (theo manage_id) đã link tài khoản."""
    regs = (
        db.query(UserRegister)
        .filter(UserRegister.manage_id == leader_agent_id)
        .filter(UserRegister.status.in_(["APPROVED", "ACTIVATED", "PROFILE_VERIFYING"]))
        .all()
    )
    ids: List[int] = []
    for reg in regs:
        if reg.email:
            linked = db.query(AgentDetail).filter_by(email=reg.email).first()
            if linked:
                ids.append(linked.id)
    return ids


_CRITERION_LABELS = {
    "fyp": "Doanh số FYP (đội)",
    "members": "Số thành viên nhánh",
    "fm_plus": "Số thành viên FM trở lên",
    "big_branch_pct": "Tỷ trọng nhánh lớn nhất",
    "k2": "Chỉ số K2 (duy trì)",
    "consecutive_months": "Số tháng ghi nhận doanh số liên kề",
}


def _build_promotion(db: Session, agent: AgentDetail, period: str) -> PromotionCriteriaResponse:
    """Xét tiêu chí THĂNG CẤP đầy đủ (theo bảng chính sách) dùng snapshot + cây đội ngũ."""
    from app.schemas_compensation import CriterionDetail
    from app.routers.snapshot import build_snapshot_metrics, count_consecutive_months

    current_level = comp.rank_to_level(agent.rank)
    target_level = (current_level or 0) + 1
    criteria = comp.PROMOTION_CRITERIA.get(target_level)

    # Chỉ số hiện tại: dùng snapshot builder cho tháng của `period`
    from app.routers.snapshot import _period_bounds
    try:
        start, end = _period_bounds(period[:7])
    except Exception:
        today = date.today()
        start, end = today.replace(day=1), today
    m = build_snapshot_metrics(db, agent, start, end)
    consec = count_consecutive_months(db, agent.id, period[:7])

    if not criteria:
        return PromotionCriteriaResponse(
            current_level=current_level, current_rank=agent.rank,
            target_level=None, eligible=False,
            personal_fyp_current=float(m["personal_fyp"]),
            team_fyp_current=float(m["team_fyp"]),
            active_members_current=m["active_members"],
            message="Đã đạt cấp cao nhất hoặc chưa có tiêu chí cho cấp kế tiếp.",
        )

    metrics = {
        "fyp": m["team_fyp"],  # bảng thăng cấp tính theo doanh số đội
        "members": m["total_members"],
        "fm_plus": m["fm_plus"],
        "big_branch_pct": m["big_branch_pct"],
        "k2": m["k2"],
        "consecutive_months": consec,
    }
    eligible, details = comp.evaluate_promotion_criteria(target_level, metrics)

    criteria_list = [CriterionDetail(
        key=d["key"], label=_CRITERION_LABELS.get(d["key"], d["key"]),
        current=float(d["current"]), required=float(d["required"]),
        met=d["met"], is_max=d.get("is_max", False),
    ) for d in details]

    return PromotionCriteriaResponse(
        current_level=current_level, current_rank=agent.rank,
        target_level=target_level, target_rank=comp.level_to_rank(target_level),
        eligible=eligible,
        message="Đủ điều kiện xét thăng cấp." if eligible else "Chưa đủ điều kiện thăng cấp.",
        criteria=criteria_list,
        # tương thích ngược
        personal_fyp_current=float(m["personal_fyp"]),
        team_fyp_current=float(m["team_fyp"]),
        team_fyp_required=float(criteria["fyp"]),
        active_members_current=m["active_members"],
    )


@router.get("/performance", response_model=PerformanceResponse)
def get_performance(
    period: str = Query(..., description="Kỳ YYYY-MM hoặc năm YYYY"),
    agent_id: Optional[int] = Query(None),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Perf cá nhân + đội ngũ và chỉ số cần để thăng cấp tiếp theo."""
    me = db.query(AgentDetail).filter_by(username=username).first()
    cred = db.query(AgentCredential).filter_by(username=username).first()
    is_admin = cred and cred.role in ("ROLE_ADMIN", "ROLE_AGENT")

    target_id = agent_id if (is_admin and agent_id) else (me.id if me else None)
    if not target_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không xác định được agent.")
    agent = _agent_by_id(db, target_id)
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    personal_fyp = _agent_period_fyp(db, agent.id, period)
    personal_contracts = (
        db.query(ContractFinancial)
        .filter(ContractFinancial.agent_id == agent.id)
        .filter(ContractFinancial.payroll_period.like(f"{period}%"))
        .count()
    )
    team_ids = _team_agent_ids(db, agent.id)
    team_fyp = sum((_agent_period_fyp(db, tid, period) for tid in team_ids), Decimal("0"))
    active_members = sum(1 for tid in team_ids if _agent_period_fyp(db, tid, period) > 0)

    return PerformanceResponse(
        agent_id=agent.id,
        agent_name=agent.full_name,
        period=period,
        personal_fyp=float(personal_fyp),
        personal_contracts=personal_contracts,
        team_fyp=float(team_fyp),
        team_members=len(team_ids),
        active_team_members=active_members,
        appointment_status=_appointment_status_for(db, agent.id),
        promotion=_build_promotion(db, agent, period),
    )


# ─── 5. Bổ nhiệm / Onboard ─────────────────────────────────────────────────────

def _serialize_appointment(db: Session, appt: AgentAppointment) -> dict:
    agent = _agent_by_id(db, appt.agent_id)
    return {
        "id": appt.id,
        "agent_id": appt.agent_id,
        "agent_name": agent.full_name if agent else None,
        "target_level": appt.target_level,
        "target_rank": comp.level_to_rank(appt.target_level),
        "status": appt.status,
        "assigned_by": appt.assigned_by,
        "assigned_at": appt.assigned_at,
        "challenge_start": _fmt_date(appt.challenge_start),
        "challenge_end": _fmt_date(appt.challenge_end),
        "confirmed_at": appt.confirmed_at,
        "achieved_level": appt.achieved_level,
        "achieved_rank": comp.level_to_rank(appt.achieved_level) if appt.achieved_level else None,
        "note": appt.note,
    }


@router.get("/appointment/durations", response_model=List[ChallengeDurationOption])
def list_challenge_durations(username: str = Depends(get_current_username), db: Session = Depends(get_db)):
    """Danh sách các mốc thời hạn thử thách cho FE hiển thị dropdown."""
    return [
        ChallengeDurationOption(key=k, label=v["label"])
        for k, v in comp.CHALLENGE_DURATIONS.items()
    ]


@router.post("/appointment", response_model=AppointmentResponse, status_code=201)
def create_appointment(
    body: CreateAppointmentRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin giao cấp bậc thử thách (bổ nhiệm) cho một agent.

    Ngày kết thúc thử thách được xác định theo thứ tự ưu tiên:
    1. challenge_duration (30_DAYS / 2_MONTHS / ...) tính từ challenge_start.
    2. challenge_end nhập trực tiếp.
    challenge_start mặc định là hôm nay nếu bỏ trống.
    """
    _require_admin(username, db)

    agent = _agent_by_id(db, body.agent_id)
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")
    if body.target_level not in comp.LEVEL_TO_RANK:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cấp thử thách không hợp lệ (1..6).")

    start = _parse_date(body.challenge_start) or date.today()

    if body.challenge_duration:
        if body.challenge_duration not in comp.CHALLENGE_DURATIONS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Thời hạn thử thách không hợp lệ.")
        end = comp.add_duration(start, body.challenge_duration)
    else:
        end = _parse_date(body.challenge_end)

    if not end:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cần cung cấp thời hạn thử thách (challenge_duration) hoặc ngày kết thúc (challenge_end).",
        )
    if end <= start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ngày kết thúc phải sau ngày bắt đầu.")

    appt = AgentAppointment(
        agent_id=body.agent_id,
        target_level=body.target_level,
        status=comp.APPOINTMENT_STATUS_PENDING,
        assigned_by=username,
        assigned_at=datetime.utcnow(),
        challenge_start=start,
        challenge_end=end,
        is_evaluated=False,
        note=body.note,
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)

    # Thông báo cho agent về đợt thử thách
    create_notification(
        db,
        recipient_agent_id=agent.id,
        type="APPOINTMENT_ASSIGNED",
        title="Bạn được giao cấp thử thách",
        message=f"Bạn đang thử thách cấp {comp.level_to_rank(body.target_level)} "
                f"đến hết ngày {_fmt_date(end)}. Hãy hoàn thành các tiêu chí để được bổ nhiệm.",
        link_tab="compensation",
        ref_id=appt.id,
    )

    return _serialize_appointment(db, appt)


# ─── Job quét bổ nhiệm hết hạn ─────────────────────────────────────────────────

def _evaluate_due_appointment(db: Session, appt: AgentAppointment) -> Optional[int]:
    """Xét 1 bổ nhiệm đã hết hạn: tính perf tích lũy và assign cấp phù hợp.

    Xét lần lượt từ cấp thử thách xuống 1, thỏa cấp nào assign cấp đó.
    Trả về cấp được assign (hoặc None nếu agent không còn tồn tại).
    """
    agent = _agent_by_id(db, appt.agent_id)
    if not agent:
        appt.is_evaluated = True
        appt.status = comp.APPOINTMENT_STATUS_FAILED
        return None

    # Chu kỳ đánh giá: từ ngày bắt đầu thử thách đến ngày kết thúc.
    # Dùng bộ chỉ số đầy đủ (FYP + số lượng tuyến dưới theo cấp + mã ĐL) để xét
    # tiêu chí BỔ NHIỆM theo bảng chính sách.
    from app.routers.snapshot import build_snapshot_metrics

    start = appt.challenge_start or date.today().replace(day=1)
    end = appt.challenge_end or date.today()
    m = build_snapshot_metrics(db, agent, start, end)
    metrics = {
        "fyp": m["personal_fyp"],
        "downline_counts": m["downline_by_level"],
        "agent_codes": m["agent_codes"],
    }

    achieved = comp.evaluate_achieved_appointment_level(appt.target_level, metrics)

    appt.achieved_level = achieved
    appt.is_evaluated = True
    appt.confirmed_at = datetime.utcnow()
    appt.status = (
        comp.APPOINTMENT_STATUS_CONFIRMED if achieved >= appt.target_level
        else comp.APPOINTMENT_STATUS_CONFIRMED  # vẫn CONFIRMED nhưng ở cấp thấp hơn
    )

    # Assign rank thực tế cho agent theo cấp đạt được
    agent.rank = comp.level_to_rank(achieved)

    # Thông báo kết quả
    if achieved >= appt.target_level:
        msg = f"Chúc mừng! Bạn đã hoàn thành thử thách và được bổ nhiệm cấp {comp.level_to_rank(achieved)}."
    else:
        msg = (f"Kết thúc thử thách cấp {comp.level_to_rank(appt.target_level)}. "
               f"Theo kết quả đạt được, bạn được xếp ở cấp {comp.level_to_rank(achieved)}.")
    create_notification(
        db,
        recipient_agent_id=agent.id,
        type="APPOINTMENT_RESULT",
        title="Kết quả bổ nhiệm",
        message=msg,
        link_tab="compensation",
        ref_id=appt.id,
        commit=False,
    )
    return achieved


def _perf_over_range(db: Session, agent: AgentDetail, start: Optional[date],
                     end: Optional[date]) -> tuple[Decimal, Decimal, int]:
    """Tổng hợp FYP cá nhân, FYP đội ngũ, số thành viên hoạt động trong khoảng [start, end]."""
    if not start or not end:
        # fallback: dùng tháng hiện tại
        now = date.today()
        prefix = now.strftime("%Y-%m")
        personal = _agent_period_fyp(db, agent.id, prefix)
        team_ids = _team_agent_ids(db, agent.id)
        team = sum((_agent_period_fyp(db, tid, prefix) for tid in team_ids), Decimal("0"))
        active = sum(1 for tid in team_ids if _agent_period_fyp(db, tid, prefix) > 0)
        return personal, team, active

    # Danh sách các kỳ YYYY-MM giao với khoảng
    periods = _months_between(start, end)
    personal = Decimal("0")
    team_total = Decimal("0")
    team_ids = _team_agent_ids(db, agent.id)
    active_set = set()
    for prefix in periods:
        personal += _agent_period_fyp(db, agent.id, prefix)
        for tid in team_ids:
            tfyp = _agent_period_fyp(db, tid, prefix)
            team_total += tfyp
            if tfyp > 0:
                active_set.add(tid)
    return personal, team_total, len(active_set)


def _months_between(start: date, end: date) -> List[str]:
    """Trả list 'YYYY-MM' từ start đến end (bao gồm 2 đầu)."""
    result: List[str] = []
    y, m = start.year, start.month
    while (y < end.year) or (y == end.year and m <= end.month):
        result.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return result


def _lazy_scan_due(db: Session) -> dict:
    """Quét & xử lý các bổ nhiệm đã hết hạn. Trả về thống kê. Tự commit."""
    today = date.today()
    due = (
        db.query(AgentAppointment)
        .filter(AgentAppointment.status == comp.APPOINTMENT_STATUS_PENDING)
        .filter(AgentAppointment.is_evaluated == False)  # noqa: E712
        .filter(AgentAppointment.challenge_end != None)   # noqa: E711
        .filter(AgentAppointment.challenge_end <= today)
        .all()
    )

    evaluated = 0
    assigned = 0
    details = []
    for appt in due:
        achieved = _evaluate_due_appointment(db, appt)
        evaluated += 1
        if achieved:
            assigned += 1
            details.append({
                "appointment_id": appt.id,
                "agent_id": appt.agent_id,
                "target_level": appt.target_level,
                "achieved_level": achieved,
                "achieved_rank": comp.level_to_rank(achieved),
            })

    if evaluated:
        db.commit()
    return {"evaluated": evaluated, "assigned": assigned, "details": details}


@router.post("/appointment/scan-due", response_model=ScanDueResponse)
def scan_due_appointments(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Job quét: xử lý các bổ nhiệm đã hết hạn thử thách (PENDING, chưa evaluated).

    Với mỗi bổ nhiệm hết hạn: xét tiêu chí từ cấp thử thách xuống 1 và assign cấp
    phù hợp. Có thể gọi bởi cron ngoài hoặc admin bấm thủ công.
    """
    _require_admin(username, db)
    stats = _lazy_scan_due(db)
    return ScanDueResponse(
        message=f"Đã xét {stats['evaluated']} bổ nhiệm hết hạn, "
                f"assign lại cấp cho {stats['assigned']} agent.",
        evaluated=stats["evaluated"],
        assigned=stats["assigned"],
        details=stats["details"],
    )


@router.put("/appointment/{appointment_id}/status", response_model=AppointmentResponse)
def update_appointment_status(
    appointment_id: int,
    body: UpdateAppointmentStatusRequest,
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Admin xác nhận hoàn tất / thất bại bổ nhiệm. CONFIRMED -> set rank agent theo cấp thử thách."""
    _require_admin(username, db)

    appt = db.query(AgentAppointment).filter_by(id=appointment_id).first()
    if not appt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bổ nhiệm không tồn tại.")

    new_status = (body.status or "").upper()
    if new_status not in (comp.APPOINTMENT_STATUS_PENDING, comp.APPOINTMENT_STATUS_CONFIRMED,
                          comp.APPOINTMENT_STATUS_FAILED):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Trạng thái không hợp lệ.")

    appt.status = new_status
    if body.note:
        appt.note = body.note

    if new_status == comp.APPOINTMENT_STATUS_CONFIRMED:
        appt.confirmed_at = datetime.utcnow()
        agent = _agent_by_id(db, appt.agent_id)
        if agent:
            agent.rank = comp.level_to_rank(appt.target_level)

    db.commit()
    db.refresh(appt)
    return _serialize_appointment(db, appt)


@router.get("/appointment", response_model=List[AppointmentResponse])
def list_appointments(
    agent_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """Danh sách bổ nhiệm. TVV chỉ thấy của mình; admin thấy tất cả / lọc theo agent."""
    me = db.query(AgentDetail).filter_by(username=username).first()
    cred = db.query(AgentCredential).filter_by(username=username).first()
    is_admin = cred and cred.role in ("ROLE_ADMIN", "ROLE_AGENT")

    # Lazy-scan: khi admin xem danh sách, tự xử lý các bổ nhiệm đã hết hạn.
    if is_admin:
        _lazy_scan_due(db)

    q = db.query(AgentAppointment)
    if is_admin:
        if agent_id:
            q = q.filter(AgentAppointment.agent_id == agent_id)
    else:
        if not me:
            return []
        q = q.filter(AgentAppointment.agent_id == me.id)

    if status_filter:
        q = q.filter(AgentAppointment.status == status_filter.upper())

    appts = q.order_by(AgentAppointment.assigned_at.desc()).all()
    return [_serialize_appointment(db, a) for a in appts]
