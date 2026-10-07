"""Pydantic 请求/响应模型"""
from pydantic import BaseModel, Field
from typing import Optional, List

#: 新用户注册赠送的默认积分（管理后台建号可覆盖该值）
DEFAULT_INITIAL_POINTS = 500


# ========== 认证相关 ==========
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    password_confirm: str
    security_question: str = Field(..., max_length=128)
    security_answer: str = Field(..., max_length=255)
    avatar: Optional[str] = None  # base64 编码图片
    state: Optional[str] = None   # 登录握手 state（防 CSRF / 重放）


class LoginRequest(BaseModel):
    username: str
    password: str
    state: Optional[str] = None   # 登录握手 state（防 CSRF / 重放）


class LoginResultRequest(BaseModel):
    """登录页回传登录结果（服务端中转回跳，供桌面客户端轮询取回）。"""
    state: str
    token: str
    user: Optional[dict] = None


class SecurityQuestionRequest(BaseModel):
    """忘记密码第一步：按用户名查密保问题。"""
    username: str = Field(..., min_length=1, max_length=32)


class ResetPasswordRequest(BaseModel):
    """忘记密码第二步：校验密保答案并重置密码。"""
    username: str = Field(..., min_length=1, max_length=32)
    security_answer: str = Field(..., min_length=1, max_length=255)
    new_password: str = Field(..., min_length=6, max_length=64)
    password_confirm: str


# ========== 积分相关 ==========
class PointsConsumeRequest(BaseModel):
    amount: int = Field(..., gt=0)
    model: str = "agens"
    task_id: Optional[str] = None


# ========== 商城相关 ==========
class CreateOrderRequest(BaseModel):
    product_id: str
    pay_method: str  # alipay / wechat


# ========== 管理员相关 ==========
class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminAdjustPointsRequest(BaseModel):
    amount: int  # 正增负减
    reason: str = "admin_adjust"
    detail: Optional[str] = None


class AdminBatchUsersRequest(BaseModel):
    """批量操作用户：action ∈ {disable, enable, delete}"""
    action: str
    ids: List[int] = []


class AdminBatchOrdersRequest(BaseModel):
    """批量删除订单：ids 为订单号（orders.id 为 VARCHAR）。"""
    ids: List[str] = []


class AdminSetMembershipRequest(BaseModel):
    """设置用户会员类型（可选同时设置到期日）。

    - membership_type ∈ {free, pro, max}
    - membership_expire: 'YYYY-MM-DD' 或完整 ISO；留空表示不改（free 时清空）
    """
    membership_type: str
    membership_expire: Optional[str] = None


class AdminBatchPointsRequest(BaseModel):
    """批量调整积分：mode ∈ {delta, set}。

    - delta：在现有余额基础上增减（amount 正增负减）
    - set：直接把余额设为 amount（不得为负）
    """
    ids: List[int] = []
    mode: str = "delta"
    amount: int = 0
    reason: Optional[str] = None
    detail: Optional[str] = None


class AdminBatchMembershipRequest(BaseModel):
    """批量设置会员类型（可选到期日）。

    - membership_type ∈ {free, pro, max}
    - membership_expire: 'YYYY-MM-DD' 或完整 ISO；留空→free 清空、付费则保留原值
    """
    ids: List[int] = []
    membership_type: str
    membership_expire: Optional[str] = None


class AdminCreateUserRequest(BaseModel):
    """管理后台新增账号。

    与用户自助注册的关键差异：不受「同一 IP 只能注册一个」限制，
    密保可省略（由管理员代建账号时允许留空，用户日后仍可自行设置）。

    - username: 3~32 字符
    - password: 6~64 字符
    - points: 初始积分，>= 0；不传用DEFAULT_INITIAL_POINTS
    - membership_type ∈ {free, pro, max}，默认 free
    - membership_expire: 'YYYY-MM-DD' 或完整 ISO；留空则付费档视为「永久」
    - avatar: base64 数据URL 或纯base64；留空用用户名首字占位
    """
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    points: Optional[int] = Field(None, ge=0)
    membership_type: str = "free"
    membership_expire: Optional[str] = None
    avatar: Optional[str] = None
    security_question: Optional[str] = None
    security_answer: Optional[str] = None
    register_ip: Optional[str] = None


class AdminAnnouncementRequest(BaseModel):
    """设置系统公告：content 为空字符串表示清除公告。"""
    content: str = ""
    enabled: bool = True


# ========== 通用响应 ==========
class ApiResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[dict] = None
