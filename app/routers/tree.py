"""Tree structure endpoints - referral tree & team tree with lazy loading."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_agent, get_current_username
from app.models import AgentDetail, UserRegister
from app.rank import rank_label

router = APIRouter(prefix="/api/v1/tree", tags=["tree"])


# ─── Schemas ────────────────────────────────────────────────────────────────────

class TreeNode(BaseModel):
    id: str
    name: str
    role: str
    avatarUrl: Optional[str] = None
    referCode: Optional[str] = None
    rank: Optional[str] = None        # mã cấp bậc (None = chưa xếp hạng)
    rankLabel: Optional[str] = None   # nhãn tiếng Việt
    children: Optional[List["TreeNode"]] = None
    hasChildren: bool = False  # Indicates if node has children (for lazy loading)


TreeNode.model_rebuild()


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _get_avatar_url(username: Optional[str]) -> Optional[str]:
    """Generate avatar URL for an agent."""
    if username:
        return f"/api/v1/agent/avatar/{username}"
    return None


def _get_managed_children(
    manager_agent_id: int,
    db: Session,
    current_depth: int,
    max_depth: int,
) -> List[TreeNode]:
    """
    Đệ quy lấy đội ngũ theo quyền quản lý (manage_id):
    - Lấy các UserRegister có manage_id == manager_agent_id và đã được duyệt (status = APPROVED).
    - CHỈ giữ thành viên đã có tài khoản trong AGENT_DETAIL (khớp qua email);
      bỏ qua các bản ghi đăng ký chưa tạo tài khoản (pending).
    - Với mỗi thành viên, tiếp tục lấy đội ngũ họ quản lý.
    """
    if current_depth > max_depth:
        return []

    registrations = (
        db.query(UserRegister)
        .filter(UserRegister.manage_id == manager_agent_id)
        .filter(UserRegister.status == "APPROVED")
        .order_by(UserRegister.created_datetime.asc())
        .all()
    )

    children: List[TreeNode] = []
    for reg in registrations:
        # Thành viên này phải đã có tài khoản agent (match theo email) mới đưa vào đội ngũ.
        if not reg.email:
            continue
        linked_agent: Optional[AgentDetail] = (
            db.query(AgentDetail).filter_by(email=reg.email).first()
        )
        if not linked_agent:
            continue

        child_agent_id = linked_agent.id
        child_refer_code = linked_agent.refer_code
        child_username = linked_agent.username
        child_rank = linked_agent.rank

        level_label = f"Thành viên F{current_depth}"

        # Đếm số thành viên (đã có tài khoản) mà người này đang quản lý
        grandchildren = _get_managed_children(
            child_agent_id, db, current_depth + 1, max_depth
        ) if current_depth < max_depth else None

        if grandchildren is not None:
            has_grandchildren = len(grandchildren) > 0
        else:
            # Chưa đệ quy sâu hơn (đạt max_depth) -> đếm nhanh để biết còn con hay không
            has_grandchildren = _has_managed_children(child_agent_id, db)

        children.append(TreeNode(
            id=str(reg.id),
            name=reg.fullname or "Không rõ tên",
            role=level_label,
            avatarUrl=_get_avatar_url(child_username),
            referCode=child_refer_code,
            rank=child_rank,
            rankLabel=rank_label(child_rank),
            children=grandchildren if grandchildren else [],
            hasChildren=has_grandchildren,
        ))

    return children


def _has_managed_children(manager_agent_id: int, db: Session) -> bool:
    """Kiểm tra agent có thành viên đội ngũ nào (đã có tài khoản AGENT_DETAIL) hay không."""
    registrations = (
        db.query(UserRegister)
        .filter(UserRegister.manage_id == manager_agent_id)
        .filter(UserRegister.status == "APPROVED")
        .all()
    )
    for reg in registrations:
        if reg.email and db.query(AgentDetail).filter_by(email=reg.email).first():
            return True
    return False


def _get_children_for_refer_code(
    refer_code: str,
    db: Session,
    current_depth: int,
    max_depth: int,
) -> List[TreeNode]:
    """Recursively get children (people who registered with this refer_code)."""
    if not refer_code or current_depth > max_depth:
        return []

    # Find all registrations that used this refer_code
    registrations = (
        db.query(UserRegister)
        .filter_by(refer_code=refer_code)
        .filter(UserRegister.status.in_(["APPROVED", "ACTIVATED", "PROFILE_VERIFYING"]))
        .order_by(UserRegister.created_datetime.asc())
        .all()
    )

    children: List[TreeNode] = []
    for reg in registrations:
        # Check if this person has an AgentDetail account (linked by email)
        linked_agent: Optional[AgentDetail] = None
        if reg.email:
            linked_agent = db.query(AgentDetail).filter_by(email=reg.email).first()

        child_refer_code = linked_agent.refer_code if linked_agent else None
        child_username = linked_agent.username if linked_agent else None
        child_rank = linked_agent.rank if linked_agent else None

        # Determine role label based on depth
        level_label = f"Thành viên F{current_depth}"

        # Check if this child has their own children
        has_grandchildren = False
        if child_refer_code:
            count = (
                db.query(UserRegister)
                .filter_by(refer_code=child_refer_code)
                .filter(UserRegister.status.in_(["APPROVED", "ACTIVATED", "PROFILE_VERIFYING"]))
                .count()
            )
            has_grandchildren = count > 0

        # Recurse if within depth limit
        grandchildren = None
        if current_depth < max_depth and child_refer_code:
            grandchildren = _get_children_for_refer_code(
                child_refer_code, db, current_depth + 1, max_depth
            )

        children.append(TreeNode(
            id=str(reg.id),
            name=reg.fullname or "Không rõ tên",
            role=level_label,
            avatarUrl=_get_avatar_url(child_username),
            referCode=child_refer_code,
            rank=child_rank,
            rankLabel=rank_label(child_rank),
            children=grandchildren if grandchildren else [],
            hasChildren=has_grandchildren,
        ))

    return children


# ─── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/referral", response_model=TreeNode)
def get_referral_tree(
    depth: int = Query(default=3, ge=1, le=10, description="Số cấp sâu tối đa"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """
    Lấy sơ đồ cây tuyển dụng (referral tree) của agent hiện tại.
    Mặc định lấy 3 cấp.
    """
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    refer_code = agent.refer_code or ""

    children = _get_children_for_refer_code(refer_code, db, 1, depth)

    return TreeNode(
        id=str(agent.id),
        name=agent.full_name or username,
        role="Người giới thiệu",
        avatarUrl=_get_avatar_url(username),
        referCode=refer_code,
        rank=agent.rank,
        rankLabel=rank_label(agent.rank),
        children=children,
        hasChildren=len(children) > 0,
    )


@router.get("/team", response_model=TreeNode)
def get_team_tree(
    depth: int = Query(default=3, ge=1, le=10, description="Số cấp sâu tối đa"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """
    Lấy sơ đồ cây đội ngũ (team tree) của agent hiện tại — theo quyền quản lý (manage_id).
    """
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    children = _get_managed_children(agent.id, db, 1, depth)

    return TreeNode(
        id=str(agent.id),
        name=agent.full_name or username,
        role="Trưởng nhóm",
        avatarUrl=_get_avatar_url(username),
        referCode=agent.refer_code or "",
        rank=agent.rank,
        rankLabel=rank_label(agent.rank),
        children=children,
        hasChildren=len(children) > 0,
    )


@router.get("/children/{refer_code}", response_model=List[TreeNode])
def get_children_lazy(
    refer_code: str,
    depth: int = Query(default=2, ge=1, le=5, description="Số cấp con cần load thêm"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """
    Lazy load thêm con của một node cụ thể (theo refer_code) - dùng cho cây Tuyển dụng.
    """
    # Validate the current user is authenticated (already done by dependency)
    children = _get_children_for_refer_code(refer_code, db, 1, depth)
    return children


@router.get("/team-children/{refer_code}", response_model=List[TreeNode])
def get_team_children_lazy(
    refer_code: str,
    depth: int = Query(default=2, ge=1, le=5, description="Số cấp con cần load thêm"),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """
    Lazy load thêm đội ngũ (theo manage_id) của một node - dùng cho cây Đội ngũ.
    Node được xác định qua refer_code -> tìm agent tương ứng -> lấy đội ngũ họ quản lý.
    """
    agent = db.query(AgentDetail).filter_by(refer_code=refer_code).first()
    if not agent:
        return []
    return _get_managed_children(agent.id, db, 1, depth)


# ─── Admin Endpoints ────────────────────────────────────────────────────────────

@router.get("/admin/agent/{agent_id}/referral", response_model=TreeNode)
def admin_get_agent_referral_tree(
    agent_id: int,
    depth: int = Query(default=3, ge=1, le=10),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """
    Admin: Lấy sơ đồ cây tuyển dụng của một agent bất kỳ theo ID.
    """
    # TODO(tạm thời): bỏ check role admin do role trong DB chưa thống nhất.
    agent = db.query(AgentDetail).filter_by(id=agent_id).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    refer_code = agent.refer_code or ""
    children = _get_children_for_refer_code(refer_code, db, 1, depth)

    return TreeNode(
        id=str(agent.id),
        name=agent.full_name or agent.username or f"Agent #{agent_id}",
        role="Người giới thiệu",
        avatarUrl=_get_avatar_url(agent.username),
        referCode=refer_code,
        rank=agent.rank,
        rankLabel=rank_label(agent.rank),
        children=children,
        hasChildren=len(children) > 0,
    )


@router.get("/admin/agent/{agent_id}/team", response_model=TreeNode)
def admin_get_agent_team_tree(
    agent_id: int,
    depth: int = Query(default=3, ge=1, le=10),
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """
    Admin: Lấy sơ đồ cây đội ngũ của một agent bất kỳ theo ID — theo quyền quản lý (manage_id).
    """
    # TODO(tạm thời): bỏ check role admin do role trong DB chưa thống nhất.
    agent = db.query(AgentDetail).filter_by(id=agent_id).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent không tồn tại.")

    children = _get_managed_children(agent.id, db, 1, depth)

    return TreeNode(
        id=str(agent.id),
        name=agent.full_name or agent.username or f"Agent #{agent_id}",
        role="Trưởng nhóm",
        avatarUrl=_get_avatar_url(agent.username),
        referCode=agent.refer_code or "",
        rank=agent.rank,
        rankLabel=rank_label(agent.rank),
        children=children,
        hasChildren=len(children) > 0,
    )


@router.get("/admin/all-roots", response_model=List[TreeNode])
def admin_get_all_root_agents(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
):
    """
    Admin: Lấy danh sách tất cả agent gốc (có refer_code) để chọn xem cây.
    """
    # TODO(tạm thời): bỏ check role admin do role trong DB chưa thống nhất.
    agents = (
        db.query(AgentDetail)
        .filter(AgentDetail.refer_code.isnot(None))
        .filter(AgentDetail.delete_flag == False)
        .order_by(AgentDetail.create_datetime.asc())
        .all()
    )

    result: List[TreeNode] = []
    for agent in agents:
        # Count direct children
        count = (
            db.query(UserRegister)
            .filter_by(refer_code=agent.refer_code)
            .filter(UserRegister.status.in_(["APPROVED", "ACTIVATED", "PROFILE_VERIFYING"]))
            .count()
        )
        result.append(TreeNode(
            id=str(agent.id),
            name=agent.full_name or agent.username or f"Agent #{agent.id}",
            role="Người giới thiệu",
            avatarUrl=_get_avatar_url(agent.username),
            referCode=agent.refer_code,
            rank=agent.rank,
            rankLabel=rank_label(agent.rank),
            hasChildren=count > 0,
            children=None,
        ))

    return result
