"""UI Automation 元素识别（零第三方依赖，纯 ctypes COM）。

为什么用「EnumChildWindows + ElementFromHandle」而不是 UIA 树遍历
----------------------------------------------------------------
UIA 的 IUIAutomationElement::FindAll / ControlViewWalker 需要按引用传递
TreeScope 与条件对象，签名一旦不准就是访问违例（进程直接崩溃，无法 try 兜住）。
实测 GetElementFromPoint / walker 在纯 ctypes 下同样不稳。
因此本模块只使用**逐一实测通过**的调用：

  IUIAutomation      idx 6  ElementFromHandle(hwnd) -> element
  element            idx 21 get_CurrentControlType   -> int
                     idx 23 get_CurrentName          -> BSTR
                     idx 26 get_CurrentHasKeyboardFocus -> int(bool)
                     idx 28 get_CurrentIsEnabled     -> int(bool)
                     idx 36 get_CurrentNativeWindowHandle -> int
                     idx 38 get_CurrentIsOffscreen   -> int(bool)
                     idx 43 get_CurrentBoundingRectangle -> RECT(4×LONG)
                     idx 16 GetCurrentPattern(patternId) -> pattern 对象

元素来源用纯 Win32 的 EnumChildWindows（稳定、无 COM 风险），
再对每个 HWND 取 UIA 属性；UIA 不支持该 HWND 时退回 Win32 的
GetWindowText / GetClassName / GetWindowRect。

BSTR 读取约定（实测结论，勿改）
------------------------------
get_CurrentName 通过 out 参数返回的指针 p：
  * 文本 UTF-16 数据**起点就是 p**（不是 p+4）
  * p-4 处的 DWORD 是**字节数**（9 字的中文串得到 18）
  * 释放必须用 SysFreeString(p-4)，否则堆损坏
读长度务必用「字节数」直接 string_at(p, nbytes)，不要再乘 2。

设计原则
--------
1. 任何单点失败都不能让进程崩溃：COM 调用集中在一处、异常一律转成 None；
2. UIA 整体不可用（非 Windows / COM 被禁 / DLL 缺失）时静默降级为纯 Win32 探测，
   server.py 仍可正常工作；
3. 所有阈值、控件类型表集中在本文件顶部，便于扩展。
"""

import ctypes
import threading
import uuid
from ctypes import wintypes, POINTER, byref, c_void_p, c_int, c_long

# ---- 可调参数（集中在此，便于扩展/调优） ----
MAX_ELEMENTS = 300          # 单次枚举最多返回多少元素（避免超长列表拖垮模型上下文）
MAX_TEXT_LEN = 512          # 控件名最大保留字符数
MAX_CLASS_LEN = 64
MIN_ELEMENT_SIZE = 2        # 宽/高小于该值的元素视为不可见碎片，丢弃
BSTR_MAX_BYTES = 8192       # BSTR 字节长度上限，防御异常长度导致的越界读

# ---- 控件类型（UIA ControlTypeId）----
CONTROL_TYPES = {
    50000: "Button", 50001: "Calendar", 50002: "Checkbox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "Footer", 50036: "SemanticZoom", 50037: "AppBar",
}

# 按「可点击 / 可输入 / 可滚动 / 可拖拽」归类，供工具直接筛选
CLICKABLE_TYPES = {50000, 50002, 50003, 50005, 50011, 50013, 50019, 50031, 50009}
EDITABLE_TYPES = {50004, 50003}
SCROLLABLE_TYPES = {50008, 50014, 50015, 50023, 50030, 50033, 50021}
DRAGGABLE_TYPES = {50015, 50014, 50027, 50023, 50008}

# ---- Win32 ----
user32 = ctypes.WinDLL("user32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
oleaut = ctypes.WinDLL("oleaut32", use_last_error=True)

# CLSID_CUIAutomation / IID_IUIAutomation（Win32metadata + 实测双重确认）
_CLSID_CUIAutomation = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"
_IID_IUIAutomation = "{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}"

COINIT_APARTMENTTHREADED = 0x2
S_OK = 0


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text):
        g = cls()
        ctypes.memmove(byref(g), ctypes.create_string_buffer(uuid.UUID(text).bytes_le), 16)
        return g


class _RECTL(ctypes.Structure):
    """UIA 的 UiaRect：物理像素，左上 + 右下。"""
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _guid_buf(text):
    return ctypes.create_string_buffer(uuid.UUID(text).bytes_le)


# ---------------------------------------------------------------------------
# Win32 基础（不依赖 COM，始终可用）
# ---------------------------------------------------------------------------
def _bind_win32():
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowRect.argtypes = (wintypes.HWND, POINTER(wintypes.RECT))
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowEnabled.argtypes = (wintypes.HWND,)
    user32.EnumChildWindows.argtypes = (wintypes.HWND, c_void_p, wintypes.LPARAM)
    user32.GetParent.argtypes = (wintypes.HWND,)
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetClassLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SendMessageTimeoutW.restype = ctypes.c_long
    oleaut.SysStringLen.restype = ctypes.c_uint
    oleaut.SysStringLen.argtypes = (c_void_p,)
    oleaut.SysFreeString.restype = c_void_p
    oleaut.SysFreeString.argtypes = (c_void_p,)


_bind_win32()

GA_ROOT = 2


def window_text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(int(hwnd))
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(int(hwnd), buf, n + 1)
    return buf.value.strip()


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(MAX_CLASS_LEN + 1)
    user32.GetClassNameW(int(hwnd), buf, MAX_CLASS_LEN + 1)
    return buf.value


def win_rect(hwnd) -> tuple:
    r = wintypes.RECT()
    if not user32.GetWindowRect(int(hwnd), byref(r)):
        return (0, 0, 0, 0)
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


def is_window(hwnd) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(int(hwnd)))


def child_windows(hwnd, max_count: int = MAX_ELEMENTS) -> list:
    """枚举直接子窗口（稳定路径）。回调对象必须保持引用，否则被 GC 后 Win32 仍调用其地址。"""
    out = []

    def _cb(child, _lparam):
        out.append(int(child))
        return len(out) < max_count      # 返回 False 提前结束枚举

    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_cb)
    try:
        user32.EnumChildWindows(int(hwnd), ctypes.cast(proc, c_void_p), 0)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# COM 基础封装
# ---------------------------------------------------------------------------
def _vtable(obj, index):
    return ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents[index]


def _release(obj):
    """Release：索引 2（IUnknown 固定布局，绝对安全）。"""
    try:
        ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(_vtable(obj, 2))(obj)
    except Exception:
        pass


def _call(obj, index, restype, argtypes, *args):
    """按指定签名调用 vtable[index]。签名必须与真实原型一致，否则是访问违例。"""
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(_vtable(obj, index))(obj, *args)


def _hr_ok(hr) -> bool:
    return hr is not None and (hr & 0xFFFFFFFF) == S_OK


# ---- UIA 实例：**按线程缓存** ----
# COM 对象是「套间绑定(apartment-bound)」的：在 A 线程 CoCreateInstance 得到的接口指针，
# 拿到 B 线程使用会让进程直接段错误（COM 违反访问违例，try/except 拦不住，实测已复现）。
# 本插件既在 MCP 主线程调用工具、也在本地 HTTP 的 ThreadingHTTPServer 工作线程里被
# /api/state 查询能力，因此必须**每个线程各持一份**自己的 IUIAutomation。
_tls = threading.local()
_auto_error = {"text": ""}       # 失败原因（任一线程失败都记下来，供能力文案如实汇报）


def _com_init():
    """初始化当前线程的 COM 套间。返回是否可用。"""
    try:
        ole32.CoInitializeEx.restype = ctypes.c_long
        ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
        hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        return (hr & 0xFFFFFFFF) in (S_OK, 0x80010106)   # S_FALSE / RPC_E_CHANGED_MODE 也可继续
    except Exception as e:
        _auto_error["text"] = f"COM 初始化失败: {e}"
        return False


def automation():
    """获取**当前线程**的 IUIAutomation 实例；失败返回 None（调用方据此降级）。"""
    ptr = getattr(_tls, "auto", None)
    if ptr is not None:
        return ptr
    if getattr(_tls, "tried", False):
        return None
    _tls.tried = True
    try:
        if not _com_init():
            return None
        ole32.CoCreateInstance.restype = ctypes.c_long
        ole32.CoCreateInstance.argtypes = [c_void_p, c_void_p, wintypes.DWORD,
                                          c_void_p, POINTER(c_void_p)]
        obj = c_void_p()
        hr = ole32.CoCreateInstance(_guid_buf(_CLSID_CUIAutomation), None, 1,
                                    _guid_buf(_IID_IUIAutomation), byref(obj))
        if _hr_ok(hr) and obj.value:
            _tls.auto = obj.value
        else:
            _auto_error["text"] = f"CoCreateInstance 失败 0x{hr & 0xFFFFFFFF:08X}"
    except Exception as e:
        _auto_error["text"] = f"{type(e).__name__}: {e}"
    return getattr(_tls, "auto", None)


def available() -> bool:
    return automation() is not None


def capability() -> str:
    """能力描述（供工具文案如实汇报，不夸大）。"""
    if available():
        return "UI Automation 元素级识别"
    return f"仅 Win32 窗口级探测（UIA 不可用：{_auto_error['text'] or '未知原因'}）"


# ---------------------------------------------------------------------------
# 元素属性读取（索引全部实测通过）
# ---------------------------------------------------------------------------
def _read_bstr(ptr) -> str:
    """读取 get_CurrentName 返回的 BSTR。

    实测内存布局（勿改，勿凭直觉调整）：
      * out 参数拿到的指针 p **就是 BSTR 首地址**（标准 BSTR：p-4 为字节长度前缀）
      * SysStringLen(p) 返回**字符数**（9 字中文串 = 9）
      * p-4 处的 DWORD = 字节数（9 字中文 = 18），可用于交叉校验
      * 释放必须用 SysFreeString(p)——传 p-4 会让堆块错位并访问违例
    这里按字符数读、校验可打印性；异常布局时退回按字节数读，两者都失败返回空串。
    """
    if not ptr:
        return ""
    p = int(ptr)
    try:
        # 首选：标准 BSTR 约定
        nchars = int(oleaut.SysStringLen(c_void_p(p)))
        if 0 < nchars <= BSTR_MAX_BYTES // 2:
            text = ctypes.string_at(p, nchars * 2).decode("utf-16-le", "replace")
            text = text.rstrip("\x00")
            if _printable_ratio(text) > 0.8:
                return text[:MAX_TEXT_LEN]
    except Exception:
        pass
    # 兜底：按 p-4 的字节数读（长度异常时）
    try:
        nbytes = ctypes.c_ulong.from_address(p - 4).value
        if 0 < nbytes <= BSTR_MAX_BYTES:
            text = ctypes.string_at(p, nbytes).decode("utf-16-le", "replace").rstrip("\x00")
            if _printable_ratio(text) > 0.8:
                return text[:MAX_TEXT_LEN]
    except Exception:
        pass
    return ""


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if c.isprintable()) / len(text)


def _free_bstr(p: int):
    """释放 BSTR：传 BSTR 首地址本身（不是 p-4，后者会导致堆错位）。"""
    try:
        oleaut.SysFreeString(c_void_p(int(p)))
    except Exception:
        pass


class Element:
    """一个界面元素的只读快照（COM 指针已释放，不持有原生资源）。"""

    __slots__ = ("hwnd", "name", "control_type", "rect", "enabled", "focused",
                 "offscreen", "parent", "class_name", "window_text", "via_uia")

    def __init__(self, hwnd, name="", control_type=0, rect=(0, 0, 0, 0),
                 enabled=True, focused=False, offscreen=False, parent=0,
                 cls="", wtext="", via_uia=False):
        self.hwnd = int(hwnd or 0)
        self.name = name
        self.control_type = int(control_type or 0)
        self.rect = tuple(rect or (0, 0, 0, 0))
        self.enabled = bool(enabled)
        self.focused = bool(focused)
        self.offscreen = bool(offscreen)
        self.parent = int(parent or 0)
        self.class_name = cls
        self.window_text = wtext
        self.via_uia = bool(via_uia)

    # -- 便捷属性 --
    @property
    def type_name(self) -> str:
        return CONTROL_TYPES.get(self.control_type, str(self.control_type or "?"))

    @property
    def label(self) -> str:
        """人类可读标识：优先 UIA 名，其次窗口标题/类名。"""
        return self.name or self.window_text or self.class_name or f"hwnd:{self.hwnd}"

    @property
    def center(self) -> tuple:
        l, t, r, b = self.rect
        return ((l + r) // 2, (t + b) // 2)

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])

    @property
    def clickable(self) -> bool:
        return self.enabled and not self.offscreen and self.control_type in CLICKABLE_TYPES

    @property
    def editable(self) -> bool:
        return self.enabled and not self.offscreen and self.control_type in EDITABLE_TYPES

    @property
    def scrollable(self) -> bool:
        return self.control_type in SCROLLABLE_TYPES

    @property
    def draggable(self) -> bool:
        return self.control_type in DRAGGABLE_TYPES

    def to_dict(self) -> dict:
        return {"hwnd": self.hwnd, "name": self.label, "type": self.type_name,
                "control_type": self.control_type, "rect": list(self.rect),
                "center": list(self.center), "enabled": self.enabled,
                "focused": self.focused, "offscreen": self.offscreen,
                "class": self.class_name, "parent": self.parent,
                "clickable": self.clickable, "editable": self.editable,
                "scrollable": self.scrollable, "draggable": self.draggable,
                "uia": self.via_uia}

    def describe(self) -> str:
        flags = []
        if self.clickable:
            flags.append("可点击")
        if self.editable:
            flags.append("可输入")
        if self.scrollable:
            flags.append("可滚动")
        if self.draggable:
            flags.append("可拖拽")
        if not self.enabled:
            flags.append("禁用")
        if self.offscreen:
            flags.append("屏外")
        if self.focused:
            flags.append("有焦点")
        l, t, r, b = self.rect
        extra = ("  [" + "、".join(flags) + "]") if flags else ""
        return (f"{self.type_name} “{self.label}” 中心({self.center[0]},{self.center[1]}) "
                f"区域 {r - l}×{b - t} hwnd={self.hwnd}{extra}")


def _element_from_hwnd(hwnd):
    """IUIAutomation::ElementFromHandle（idx 6）→ 元素指针，失败返回 None。"""
    auto = automation()
    if not auto:
        return None
    out = c_void_p()
    try:
        hr = _call(auto, 6, ctypes.c_long, [wintypes.HWND, POINTER(c_void_p)],
                   int(hwnd), byref(out))
        return out.value if _hr_ok(hr) and out.value else None
    except Exception:
        return None


def _prop_int(el, index, default=0):
    out = c_int()
    try:
        hr = _call(el, index, ctypes.c_long, [POINTER(c_int)], byref(out))
        return out.value if _hr_ok(hr) else default
    except Exception:
        return default


def _prop_name(el):
    """取元素名。返回的 BSTR 在函数内即释放（调用方拿不到裸指针，避免泄漏/重复释放）。"""
    out = c_void_p()
    try:
        hr = _call(el, 23, ctypes.c_long, [POINTER(c_void_p)], byref(out))
        if not _hr_ok(hr):
            return ""
        return _read_bstr(out.value)
    except Exception:
        return ""
    finally:
        if out.value:
            _free_bstr(out.value)


def _prop_rect(el):
    r = _RECTL(-1, -1, -1, -1)
    try:
        hr = _call(el, 43, ctypes.c_long, [POINTER(_RECTL)], byref(r))
        if _hr_ok(hr) and r.right > r.left and r.bottom > r.top:
            return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        pass
    return None


def describe_hwnd(hwnd, depth: int = 0) -> Element:
    """取单个 HWND 的元素快照：UIA 优先，失败退回 Win32 属性。"""
    hwnd = int(hwnd or 0)
    if not hwnd or not is_window(hwnd):
        return Element(0)
    cls = class_name(hwnd)
    wtext = window_text(hwnd)
    rect = win_rect(hwnd)
    enabled = bool(user32.IsWindowEnabled(hwnd))
    offscreen = not bool(user32.IsWindowVisible(hwnd))
    parent = int(user32.GetParent(hwnd) or 0)
    el = _element_from_hwnd(hwnd)
    if not el:
        return Element(hwnd, wtext, 0, rect, enabled, False, offscreen, parent, cls, wtext, False)
    try:
        name = _prop_name(el)
        ctype = _prop_int(el, 21, 0)
        urect = _prop_rect(el) or rect
        uenabled = bool(_prop_int(el, 28, 1))
        uoff = bool(_prop_int(el, 38, 0))
        ufocus = bool(_prop_int(el, 26, 0))
        return Element(hwnd, name or wtext, ctype, urect, uenabled, ufocus, uoff,
                       parent, cls, wtext, True)
    finally:
        _release(el)


def list_elements(hwnd, include_offscreen: bool = False, max_count: int = MAX_ELEMENTS) -> list:
    """枚举窗口下的界面元素（含子窗口树），供 AI 识别可点击/可滚动/可拖拽目标。"""
    hwnd = int(hwnd or 0)
    if not hwnd:
        return []
    out = []
    seen = set()

    def _walk(h, level):
        if len(out) >= max_count or level > 3:
            return
        for ch in child_windows(h, max_count):
            if ch in seen or not is_window(ch):
                continue
            seen.add(ch)
            el = describe_hwnd(ch)
            if el.width >= MIN_ELEMENT_SIZE and el.height >= MIN_ELEMENT_SIZE:
                if include_offscreen or not el.offscreen:
                    out.append(el)
            if len(out) >= max_count:
                return
            _walk(ch, level + 1)

    root = describe_hwnd(hwnd)
    if root.width >= MIN_ELEMENT_SIZE and root.height >= MIN_ELEMENT_SIZE:
        if include_offscreen or not root.offscreen:
            out.append(root)
    _walk(hwnd, 0)
    return out[:max_count]


def find_elements(hwnd, name: str = "", type_name: str = "", only: str = "",
                  include_offscreen: bool = False, max_count: int = MAX_ELEMENTS) -> list:
    """按名称/类型/能力筛选元素。only ∈ clickable/editable/scrollable/draggable。"""
    items = list_elements(hwnd, include_offscreen=include_offscreen, max_count=max_count * 2)
    key = str(name or "").strip().lower()
    want_type = str(type_name or "").strip().lower()
    cap = str(only or "").strip().lower()
    out = []
    for el in items:
        if key and key not in el.label.lower() and key not in el.class_name.lower():
            continue
        if want_type and want_type not in el.type_name.lower():
            continue
        if cap == "clickable" and not el.clickable:
            continue
        if cap == "editable" and not el.editable:
            continue
        if cap == "scrollable" and not el.scrollable:
            continue
        if cap == "draggable" and not el.draggable:
            continue
        out.append(el)
        if len(out) >= max_count:
            break
    return out


def summarize(elements: list, limit: int = 40) -> str:
    """把元素列表渲染成给模型看的紧凑文本。"""
    if not elements:
        return "（未识别到界面元素）"
    lines = []
    for i, el in enumerate(elements[:limit], 1):
        lines.append(f"{i}. {el.describe()}")
    if len(elements) > limit:
        lines.append(f"… 另有 {len(elements) - limit} 个元素未显示")
    return "\n".join(lines)
