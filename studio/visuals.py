# -*- coding: utf-8 -*-
"""心跳保活、全局样式，以及「办公室场景（含数字员工卡通形象）+ 任务流水线」可视化。"""

import os
import base64
import functools

import streamlit as st
import streamlit.components.v1 as components

from .tasks import TASK_MAP, TASK_ORDER
from .employees import EMPLOYEES
from .store import task_done, is_ready

# 办公室全景图位于项目根目录（studio 包的上一级）
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_PKG_DIR)
OFFICE_IMG = os.path.join(_PROJECT_ROOT, "studio_office.png")
OFFICE_W, OFFICE_H = 772, 356  # 全景图原始像素尺寸（用于锁定宽高比与定位）

# 各数字员工在全景图上的工位坐标（left%, top%）——对准画面中的办公桌/座椅
EMP_POS = {
    "researcher": (39, 55),   # 左上 L 形办公桌
    "creative": (62, 55),     # 右上 L 形办公桌
    "writer": (11, 84),       # 左下 工位（左）
    "assistant": (25, 85),    # 左下 工位（中）
    "reviewer": (41, 84),     # 中下 办公桌
}

# 场景中工位名牌使用的精简称谓（全称仍展示于流水线与员工档案，避免名牌互相遮挡）
EMP_SHORT = {
    "researcher": "研究员",
    "creative": "创意天才",
    "writer": "编剧",
    "reviewer": "审核员",
    "assistant": "文档助理",
}

# 数字员工卡通形象配色 (上衣, 头发)
EMP_SPRITE = {
    "researcher": ("#2bb3c0", "#3a2a22"),
    "creative": ("#f2b600", "#5a3a1a"),
    "writer": ("#7c5cff", "#241f2e"),
    "reviewer": ("#e5654a", "#2a1a14"),
    "assistant": ("#16a34a", "#3a2a16"),
}


@functools.lru_cache(maxsize=1)
def _office_bg_uri():
    """读取全景图并转为 data URI（仅编码一次，作为 CSS 背景注入）。"""
    try:
        with open(OFFICE_IMG, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


def _sprite_svg(shirt, hair):
    """生成一个简约像素风卡通小人（front-facing chibi）。"""
    return (
        '<svg class="emp-sprite" viewBox="0 0 24 30" shape-rendering="crispEdges" '
        'xmlns="http://www.w3.org/2000/svg">'
        f'<rect x="6" y="2" width="12" height="6" fill="{hair}"/>'
        f'<rect x="5" y="4" width="2" height="7" fill="{hair}"/>'
        f'<rect x="17" y="4" width="2" height="7" fill="{hair}"/>'
        '<rect x="7" y="6" width="10" height="8" fill="#f5c9a6"/>'
        f'<rect x="7" y="6" width="10" height="2" fill="{hair}"/>'
        '<rect x="9" y="9" width="2" height="2" fill="#26303a"/>'
        '<rect x="13" y="9" width="2" height="2" fill="#26303a"/>'
        '<rect x="10" y="12" width="4" height="1" fill="#d99a78"/>'
        f'<rect x="6" y="13" width="12" height="2" fill="{shirt}"/>'
        f'<rect x="5" y="14" width="14" height="10" fill="{shirt}"/>'
        f'<rect x="3" y="15" width="2" height="7" fill="{shirt}"/>'
        f'<rect x="19" y="15" width="2" height="7" fill="{shirt}"/>'
        '<rect x="3" y="22" width="2" height="2" fill="#f5c9a6"/>'
        '<rect x="19" y="22" width="2" height="2" fill="#f5c9a6"/>'
        '<rect x="10" y="14" width="4" height="3" fill="#ffffff" opacity="0.85"/>'
        "</svg>"
    )


# 心跳保活：用 Web Worker（后台标签页节流更弱）每 15s 轻量 ping 健康检查，
# 维持与服务器的连接更久。
# 注意：刻意【移除】了原先的 offline/online 事件派发——它会强制 Streamlit 重连，
# 在线上表现为"网页自动刷新、正在执行的过程输出消失"，是该问题的元凶之一。
# 真正的"断连不中断、产出不丢失"由后台线程 + 单例 RunStore 保证（见 store.py / engine.py）。
_HEARTBEAT_HTML = """
    <script>
    try {
        const workerCode = `setInterval(() => { postMessage('ping'); }, 15000);`;
        const blob = new Blob([workerCode], { type: 'application/javascript' });
        const worker = new Worker(URL.createObjectURL(blob));
        worker.onmessage = function(e) {
            fetch('/_stcore/health').catch(() => {});
        };
    } catch (err) { /* keep-alive best-effort */ }
    </script>
    """

APP_CSS = f"""
<style>
    .stTextArea textarea {{
        font-size: 14px;
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        opacity: 1 !important;
    }}
    .studio-title {{ font-size: 30px; font-weight: 800; color: #1c1e26; margin: 2px 0 2px; }}
    .studio-sub {{ color: #5b606b; font-size: 14px; margin-bottom: 14px; }}

    /* ===== 办公室场景：全景图为底 + 数字员工卡通形象定位在工位 ===== */
    .office-scene {{ position:relative; width:100%; max-width:1180px; margin:4px 0 2px;
        aspect-ratio:{OFFICE_W}/{OFFICE_H}; background-size:cover; background-position:center;
        background-color:#3b3f52; border-radius:14px; overflow:hidden; border:1px solid #e7e4db;
        box-shadow:0 6px 18px rgba(40,33,20,.06); image-rendering:pixelated; }}
    .emp-pin {{ position:absolute; transform:translate(-50%,-50%);
        display:flex; flex-direction:column; align-items:center; gap:2px; }}
    .emp-pin .emp-sprite {{ width:36px; height:auto;
        filter:drop-shadow(0 3px 2px rgba(0,0,0,.45)); }}
    .emp-pin .tag {{ font-size:10px; font-weight:700; color:#1c1e26; line-height:1.25;
        background:rgba(255,255,255,.9); border-radius:7px; padding:1px 6px; white-space:nowrap;
        box-shadow:0 1px 3px rgba(0,0,0,.25); }}
    .emp-pin .halo {{ display:none; position:absolute; width:60px; height:60px; border-radius:50%;
        left:50%; top:22px; transform:translate(-50%,-50%); pointer-events:none;
        background:radial-gradient(circle, rgba(81,71,255,.6), rgba(81,71,255,0) 70%); }}
    .emp-pin.idle {{ opacity:.92; }}
    .emp-pin.done .tag {{ background:#dff5e7; color:#0f7a37; }}
    .emp-pin.working {{ z-index:6; }}
    .emp-pin.working .tag {{ background:#5147ff; color:#fff; box-shadow:0 2px 8px rgba(81,71,255,.5); }}
    .emp-pin.working .emp-sprite {{ animation: empbob .6s ease-in-out infinite; }}
    .emp-pin.working .halo {{ display:block; animation: emphalo 1.1s ease-in-out infinite; }}
    @keyframes empbob {{ 0%,100%{{transform:translateY(0)}} 50%{{transform:translateY(-6px)}} }}
    @keyframes emphalo {{ 0%,100%{{opacity:.4; transform:translate(-50%,-50%) scale(.82)}}
        50%{{opacity:.95; transform:translate(-50%,-50%) scale(1.2)}} }}
    .office-cap {{ color:#9a9aa3; font-size:11px; margin:3px 0 8px; }}

    /* ===== 任务流水线 ===== */
    .pipeline {{ display:flex; align-items:stretch; gap:4px; flex-wrap:wrap; margin:10px 0 2px; }}
    .pnode {{ border:2px solid #b8b3a6; border-radius:12px; padding:9px 11px; min-width:118px;
        display:flex; flex-direction:column; gap:3px; }}
    .pn-id {{ font-size:11px; font-weight:800; font-family:ui-monospace,Menlo,monospace; }}
    .pnode b {{ font-size:12.5px; color:#1c1e26; }}
    .pn-owner {{ font-size:10.5px; color:#5b606b; }}
    .parrow {{ align-self:center; color:#cbc6b8; font-weight:800; padding:0 1px; }}
    .qa-ok {{ color:#16a34a; font-weight:700; }}
    .qa-warn {{ color:#b8860b; font-weight:700; }}
    .legend {{ color:#9a9aa3; font-size:11px; margin-top:4px; }}
</style>
"""


def inject_heartbeat():
    components.html(_HEARTBEAT_HTML, width=0, height=0)


def inject_styles():
    st.markdown(APP_CSS, unsafe_allow_html=True)
    # 将全景图作为 .office-scene 背景注入（仅注入一次/次运行，避免每次刷新都重传大图）
    uri = _office_bg_uri()
    if uri:
        st.markdown(f"<style>.office-scene{{background-image:url('{uri}');}}</style>", unsafe_allow_html=True)


def emp_state_key(store, k):
    """返回该员工当前状态：working / done / idle。"""
    rt = store.running_task
    if rt and TASK_MAP[rt]["owner"] == k:
        return "working"
    owned = [t for t, v in TASK_MAP.items() if v["owner"] == k]
    if owned and all(task_done(store, t) for t in owned):
        return "done"
    return "idle"


def office_scene_html(store):
    """全景图为底，把 5 位数字员工的卡通形象定位到各自工位；工作中者跳动+高亮。"""
    state_cn = {"working": "工作中", "done": "已完成", "idle": "待命"}
    pins = []
    for k, e in EMPLOYEES.items():
        state = emp_state_key(store, k)
        left, top = EMP_POS[k]
        shirt, hair = EMP_SPRITE[k]
        owned = [t for t, v in TASK_MAP.items() if v["owner"] == k]
        done_n = sum(1 for t in owned if task_done(store, t))
        title = f"{e['name']} · {state_cn[state]} · 任务 {done_n}/{len(owned)}"
        pins.append(
            f'<div class="emp-pin {state}" style="left:{left}%;top:{top}%" title="{title}">'
            f'<div class="halo"></div>'
            f"{_sprite_svg(shirt, hair)}"
            f'<div class="tag">{e["emoji"]} {EMP_SHORT[k]}</div>'
            f"</div>"
        )
    return '<div class="office-scene">' + "".join(pins) + "</div>"


def node_status(store, tid):
    if store.running_task == tid:
        return "run"
    if task_done(store, tid):
        return "done"
    if is_ready(store, tid):
        return "ready"
    return "idle"


def pipeline_html(store):
    palette = {
        "done": ("#16a34a", "#eaf7ef"),
        "run": ("#5147ff", "#eeecff"),
        "ready": ("#d8a500", "#fdf6e3"),
        "idle": ("#b8b3a6", "#f6f4ee"),
    }
    nodes = []
    for tid in TASK_ORDER:
        stt = node_status(store, tid)
        color, bg = palette[stt]
        e = EMPLOYEES[TASK_MAP[tid]["owner"]]
        nodes.append(
            f'<div class="pnode" style="border-color:{color};background:{bg}">'
            f'<span class="pn-id" style="color:{color}">任务 {tid}</span>'
            f'<b>{TASK_MAP[tid]["short"]}</b>'
            f'<span class="pn-owner">{e["emoji"]} {e["name"]}</span>'
            f"</div>"
        )
    joined = '<span class="parrow">➜</span>'.join(nodes)
    legend = (
        '<div class="legend">图例：'
        '<span style="color:#16a34a">●已完成</span>　'
        '<span style="color:#5147ff">●工作中</span>　'
        '<span style="color:#d8a500">●可执行</span>　'
        '<span style="color:#b8b3a6">●等待依赖</span></div>'
    )
    return '<div class="pipeline">' + joined + "</div>" + legend


def render_viz(ph, store):
    with ph.container():
        st.markdown("##### 🏢 数字员工办公室 · 实时工作状态")
        st.markdown(office_scene_html(store), unsafe_allow_html=True)
        st.markdown(
            '<div class="office-cap">🟣 形象跳动+高亮光圈＝该员工正在工作　·　'
            "🟢 名牌变绿＝已完成　·　⚪ 常态＝待命（鼠标悬停查看详情）</div>",
            unsafe_allow_html=True,
        )
        st.markdown("##### 🔗 协作流水线 · 9 大任务")
        st.markdown(pipeline_html(store), unsafe_allow_html=True)
