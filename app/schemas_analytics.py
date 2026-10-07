"""Pydantic schemas cho module Phân tích hiệu suất (Performance Analytics).

Hỗ trợ tính perf cá nhân & đội nhóm theo khoảng thời gian tùy ý.
"""

from typing import List, Optional

from pydantic import BaseModel


class PeriodPoint(BaseModel):
    """Một điểm dữ liệu theo mốc thời gian (dùng vẽ biểu đồ)."""
    label: str          # ví dụ "2026-08" hoặc "T8/2026"
    fyp: float = 0
    contracts: int = 0


class MemberPerformance(BaseModel):
    """Hiệu suất của một thành viên trong đội nhóm."""
    agent_id: int
    name: Optional[str] = None
    refer_code: Optional[str] = None
    rank: Optional[str] = None
    rank_label: Optional[str] = None
    fyp: float = 0
    contracts: int = 0
    active: bool = False   # có phát sinh doanh số trong kỳ không


class PerformanceAnalyticsResponse(BaseModel):
    """Kết quả phân tích hiệu suất cho một agent + đội nhóm trong khoảng thời gian."""
    agent_id: int
    agent_name: Optional[str] = None
    from_date: str        # dd/mm/yyyy
    to_date: str          # dd/mm/yyyy
    granularity: str      # month | quarter

    # Tổng hợp cá nhân
    personal_fyp: float = 0
    personal_contracts: int = 0

    # Tổng hợp đội nhóm (không gồm cá nhân leader)
    team_fyp: float = 0
    team_contracts: int = 0
    team_members: int = 0
    active_team_members: int = 0

    # Tổng cộng (cá nhân + đội nhóm)
    total_fyp: float = 0
    total_contracts: int = 0

    # Chuỗi thời gian để vẽ biểu đồ
    personal_series: List[PeriodPoint] = []
    team_series: List[PeriodPoint] = []

    # Chi tiết từng thành viên đội nhóm (sắp xếp theo FYP giảm dần)
    members: List[MemberPerformance] = []


# ─── Leaderboard (Bảng xếp hạng) ───────────────────────────────────────────────

class LeaderboardEntry(BaseModel):
    """Một dòng trong bảng xếp hạng."""
    rank: int                       # thứ hạng (1, 2, 3...)
    agent_id: int
    name: Optional[str] = None
    refer_code: Optional[str] = None
    rank_label: Optional[str] = None  # cấp bậc (nhãn tiếng Việt)
    value: float = 0                # giá trị theo tiêu chí xếp hạng
    value_label: Optional[str] = None  # chuỗi hiển thị (vd "150 Tr", "12 HĐ")
    extra: Optional[dict] = None    # số liệu phụ (vd cấp cũ->mới khi thăng cấp)


class LeaderboardResponse(BaseModel):
    metric: str                     # tiêu chí: fyp / contracts / team_fyp / promotions
    metric_label: str
    from_date: str
    to_date: str
    scope: str                      # individual | team
    entries: List[LeaderboardEntry] = []


# ─── Achievements / Đặc quyền / K2 knowledge ───────────────────────────────────

class BadgeItem(BaseModel):
    code: str
    name: str
    desc: str
    icon: str
    earned: bool = False


class PrivilegeItem(BaseModel):
    code: str
    name: str
    desc: str
    min_level: int
    unlocked: bool = False


class ProgressGoal(BaseModel):
    """Thanh tiến độ mục tiêu cá nhân."""
    key: str
    label: str
    current: float = 0
    target: float = 0
    unit: Optional[str] = None       # vnd | count | percent
    hint: Optional[str] = None       # gợi ý "còn X nữa..."


class AchievementsResponse(BaseModel):
    agent_id: int
    agent_name: Optional[str] = None
    level: Optional[int] = None
    rank_label: Optional[str] = None
    consecutive_months: int = 0      # streak
    badges: List[BadgeItem] = []
    privileges: List[PrivilegeItem] = []
    goals: List[ProgressGoal] = []


class K2KnowledgeItem(BaseModel):
    q: str
    a: str
