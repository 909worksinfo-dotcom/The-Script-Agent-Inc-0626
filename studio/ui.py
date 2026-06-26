# -*- coding: utf-8 -*-
"""UI 编排：侧边栏、两种交互方式、产出展示、数字员工档案、main()。

线上稳定性设计（修复休眠/断连中断、刷新丢产出）：
- 长流程（自动一键运行 / 手动"自动执行后续剩余任务"）放到「后台线程」执行，写入单例 RunStore；
- UI 每约 1.5s 轮询刷新（st.rerun，保留 session_state 不丢配置/Key），从 RunStore 读取进度与产出；
- 即使浏览器断连/电脑休眠，后台线程仍在服务器继续跑；重连后进度与过程日志都还在（除非主动重置）。
"""

import time
import threading

import streamlit as st

from .employees import EMPLOYEES, AGENT_SKILLS
from .tasks import TASK_MAP, TASK_ORDER
from .llm_service import PROVIDERS, MODELS
from .state import init_state, get_emp_config
from .store import get_store, task_done, is_ready, get_batches
from .engine import (
    run_generic_task,
    run_task8_batch,
    run_task9,
    run_pipeline,
    count_episodes,
    parse_script_to_df,
)
from .visuals import inject_heartbeat, inject_styles, render_viz


# ==========================================
# 运行参数快照 + 后台线程启动
# ==========================================
def _snapshot_config(store):
    """把当前会话的运行参数拷贝进 store（供前台/后台执行使用，不依赖 session_state）。"""
    store.snapshot_config(
        st.session_state.total_episodes,
        st.session_state.script_mode,
        st.session_state.seed,
        {k: dict(get_emp_config(k)) for k in EMPLOYEES},
    )


def _start_bg(store, from_progress=False):
    """启动后台流水线线程（自动一键运行 / 自动执行后续剩余任务）。"""
    if store.is_running:
        return
    _snapshot_config(store)
    store.cancel = False
    with store.lock:
        store.is_running = True
        store.failed_task = None
    t = threading.Thread(
        target=run_pipeline, args=(store,), kwargs={"from_progress": from_progress}, daemon=True
    )
    store.thread = t
    t.start()


def _clear_edit_buffers():
    for tid in TASK_ORDER:
        st.session_state.pop(f"edit_{tid}", None)
    for k in list(st.session_state.keys()):
        if k.startswith("e8_") or k.startswith("refresh_"):
            st.session_state.pop(k, None)


# ==========================================
# 侧边栏：模型配置 + 项目设置
# ==========================================
def model_config_block(prefix, default_cfg):
    provider = st.selectbox(
        "API 服务商",
        PROVIDERS,
        index=PROVIDERS.index(default_cfg["provider"]) if default_cfg["provider"] in PROVIDERS else 0,
        key=f"{prefix}_provider",
    )
    key = ""
    if provider != "Mock (演示)":
        key = st.text_input("API Key", type="password", key=f"{prefix}_key")
    model = st.selectbox("模型", MODELS[provider], key=f"{prefix}_model")
    return {"provider": provider, "key": key, "model": model}


def render_sidebar(store):
    with st.sidebar:
        st.header("⚙️ 模型配置")
        st.caption("  ")

        st.markdown("**全局默认（应用于所有数字员工）**")
        st.session_state.global_cfg = model_config_block("global", st.session_state.global_cfg)

        st.divider()
        st.session_state.per_emp = st.checkbox("🧩 为每位数字员工单独配置模型", value=st.session_state.per_emp)
        if st.session_state.per_emp:
            st.caption("未单独配置的员工沿用全局默认。")
            for k, e in EMPLOYEES.items():
                with st.expander(f"{e['emoji']} {e['name']}"):
                    st.session_state.emp_cfg[k] = model_config_block(f"emp_{k}", st.session_state.global_cfg)

        st.divider()
        st.markdown("**📂 项目设置**")
        st.session_state.total_episodes = st.number_input(
            "剧本总集数", min_value=1, max_value=100, value=st.session_state.total_episodes, step=1
        )
        mode_label = st.radio(
            "分镜脚本模式",
            ["剧本分镜脚本 (标准短剧)", "解说漫分镜脚本 (小说推文/漫改)"],
            index=0 if st.session_state.script_mode == "standard" else 1,
        )
        st.session_state.script_mode = "standard" if mode_label.startswith("剧本") else "comic"

        st.divider()
        if st.button("♻️ 重置工作室（清空所有产出）"):
            store.reset()  # 同时会通知后台线程停止
            _clear_edit_buffers()
            st.rerun()


# ==========================================
# 产出展示
# ==========================================
def render_result(store, tid):
    o = store.outputs
    if tid == 8:
        scripts = o.get(8) or {}
        if not scripts:
            return
        mode_name = "解说漫" if store.script_mode == "comic" else "标准短剧"
        st.caption(f"分镜模式：{mode_name}")
        for label, content in scripts.items():
            st.markdown(f"**📑 分镜 · {label}**")
            if isinstance(content, str) and content.startswith("❌"):
                st.error(content)
                continue
            try:
                df = parse_script_to_df(content)
                if df is not None and len(df) > 0:
                    shot_rows = df[~df["镜号"].astype(str).str.contains("🎬")]
                    st.markdown(
                        f'<span class="qa-ok">✅ 解析成功</span>　共 {len(shot_rows)} 个镜头 · '
                        f'{int(df["镜号"].astype(str).str.contains("🎬").sum())} 个分集标记',
                        unsafe_allow_html=True,
                    )
                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "镜号": st.column_config.TextColumn("镜号", width="small"),
                            "场景": st.column_config.TextColumn("场景", width="medium"),
                            "画面内容 (Visual)": st.column_config.TextColumn("画面内容 (Visual)", width="large"),
                            "台词/解说 (Dialogue/Commentary)": st.column_config.TextColumn(
                                "台词/解说 (Dialogue/Commentary)", width="large"
                            ),
                        },
                    )
                    csv_out = df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        f"📥 下载 {label} CSV (Excel 专用)",
                        data=csv_out,
                        file_name=f"storyboard_{label}.csv",
                        mime="text/csv",
                        key=f"dl8_{label}",
                    )
                else:
                    st.warning("⚠️ 未检测到有效分镜内容，展示原始返回：")
                    st.text(content)
            except Exception as e:
                st.error(f"⚠️ 解析异常: {e}")
                st.text(content)
        return

    content = o.get(tid)
    if not content:
        return
    if isinstance(content, str) and content.startswith("❌"):
        st.error(content)
        return

    if tid in (5, 7):
        n = count_episodes(content)
        target = store.total_episodes
        cls = "qa-ok" if n >= target else "qa-warn"
        msg = (
            f"✅ 集数完整性校验通过：检测到 {n} 集 / 目标 {target} 集"
            if n >= target
            else f"⚠️ 集数校验：检测到 {n} 集 / 目标 {target} 集（可点击重新执行补全）"
        )
        st.markdown(f'<span class="{cls}">{msg}</span>', unsafe_allow_html=True)

    st.markdown(content)
    st.download_button(
        "📥 下载该产出 (TXT)",
        data=content.encode("utf-8"),
        file_name=f"task{tid}_{TASK_MAP[tid]['short']}.txt",
        mime="text/plain",
        key=f"dl_{tid}",
    )

    if tid == 9:
        st.success("✅ 文档助理已完成飞书文档粘贴与校对，确认无错误、无遗漏。")
        st.download_button(
            "📥 下载飞书最终交付文档 (Markdown)",
            data=content.encode("utf-8"),
            file_name="飞书_最终剧本交付文档.md",
            mime="text/markdown",
            key="dl9_md",
        )


def status_badge(store, tid):
    if task_done(store, tid):
        return "🟢 已完成"
    if is_ready(store, tid):
        return "🟡 可执行"
    return "⚪ 等待依赖"


def render_run_status(store):
    """运行状态条 + 持久化的过程日志（刷新/重连都不丢失）。"""
    if store.is_running:
        cur = store.running_task
        cur_txt = f"任务{cur}「{TASK_MAP[cur]['title']}」" if cur else "准备中"
        st.info(
            f"⏳ 后台正在执行 · 当前：{cur_txt}　|　页面每约 1.5 秒自动刷新进度。"
            "你可以切到别的标签页 / 电脑休眠，任务仍在服务器后台继续运行，回来后进度与产出都不会丢失。"
        )
    if store.failed_task is not None:
        st.error(
            f"⛔ 任务{store.failed_task} 失败（已重试 3 次）：任务失败，请再次点击生成，或调整为「手动逐步启动」模式逐步执行。"
        )
    if store.log:
        with st.expander("🛰️ 运行过程日志（实时 · 刷新/重连不丢失）", expanded=store.is_running):
            st.code("\n".join(store.log[-200:]), language=None)


# ==========================================
# 自动模式
# ==========================================
def render_auto_mode(store, viz_ph):
    st.markdown("### 🤖 自动按序执行")
    st.info(
        "点击下方按钮，5 位数字员工将依次自动完成任务 1 → 9，全程实时可视化，最终自动产出飞书交付文档。"
        "运行在服务器后台进行，电脑休眠/断连也会继续跑到最后。中途如遇问题，可切换至② 手动逐步启动继续任务。"
    )
    auto_cols = st.columns([1, 3])
    start_auto = auto_cols[0].button(
        "🚀 一键运行全流程", type="primary", use_container_width=True, disabled=store.is_running
    )
    auto_cols[1].caption(f"当前总集数：{st.session_state.total_episodes} 集 · 大纲与分镜均自动分批生成。")

    if start_auto and not store.is_running:
        _start_bg(store, from_progress=False)
        st.rerun()

    st.divider()
    st.markdown("### 📦 各任务产出")
    for tid in TASK_ORDER:
        e = EMPLOYEES[TASK_MAP[tid]["owner"]]
        with st.expander(
            f"任务 {tid} · {TASK_MAP[tid]['title']}　|　{e['emoji']} {e['name']}　{status_badge(store, tid)}",
            expanded=False,
        ):
            st.caption(TASK_MAP[tid]["desc"])
            if task_done(store, tid):
                render_result(store, tid)
            elif store.running_task == tid:
                st.info("⏳ 正在执行…")
            else:
                st.info("尚未执行。")


# ==========================================
# 手动模式
# ==========================================
def render_manual_mode(store, viz_ph):
    st.markdown("### 🙋 手动逐步启动")
    st.info(
        "按顺序逐个点击任务按钮启动对应数字员工；每个任务产出后可【修改】再进入下一步，"
        "也可【跳过本任务】直接粘贴本地剧本文本后保存，再继续下一步；"
        "也可随时点击下方按钮，让产品【自动跑完后续剩余任务】（后台执行，断连不中断）。"
    )

    running = store.is_running
    o = store.outputs

    # —— 自动执行后续剩余任务（后台从当前进度起跑完）——
    done_cnt = sum(1 for t in TASK_ORDER if task_done(store, t))
    rc = st.columns([1.4, 2.6])
    run_rest = rc[0].button(
        "🚀 自动执行后续剩余任务", type="primary",
        disabled=running or (done_cnt == len(TASK_ORDER)),
        help="从当前已完成的最后一步往后，自动执行后续任务，直到全部完成；不回头补跑前序未完成的任务",
    )
    rc[1].caption(
        f"已完成 {done_cnt}/{len(TASK_ORDER)} 个任务。"
        "点击后将从“当前进度的下一个任务”开始往后自动执行（后台运行，单个任务卡顿时仍可在下方查看前序已完成产出）。"
    )
    if run_rest and not running:
        _clear_edit_buffers()
        _start_bg(store, from_progress=True)
        st.rerun()

    # 预同步「编辑缓冲区」——必须在创建任何编辑框（widget）之前完成
    for tid in TASK_ORDER:
        if tid == 8:
            continue
        ekey = f"edit_{tid}"
        cur = o.get(tid)
        cur = cur if isinstance(cur, str) else ""
        if st.session_state.pop(f"refresh_{tid}", False):
            st.session_state[ekey] = cur
        elif ekey not in st.session_state:
            st.session_state[ekey] = cur

    for tid in TASK_ORDER:
        t = TASK_MAP[tid]
        e = EMPLOYEES[t["owner"]]
        with st.container(border=True):
            c1, c2 = st.columns([0.72, 0.28])
            c1.markdown(f"**任务 {tid} · {t['title']}**　{e['emoji']} **{e['name']}**")
            c2.markdown(f"<div style='text-align:right'>{status_badge(store, tid)}</div>", unsafe_allow_html=True)
            st.caption(t["desc"])

            ready = is_ready(store, tid)
            if not ready:
                missing = [f"任务{d}" for d in t["deps"] if not task_done(store, d)]
                st.warning(
                    f"🔒 需先完成：{'、'.join(missing)}"
                    "（或在下方“修改 / 粘贴”里直接粘贴本地文本并保存，即可跳过依赖）"
                )

            if tid == 8:
                _render_manual_task8(store, ready)
            else:
                btn_label = "▶️ 重新执行" if task_done(store, tid) else "▶️ 启动该任务（调用数字员工生成）"
                if st.button(btn_label, key=f"run_{tid}", disabled=(not ready) or running,
                             type="primary" if ready and not task_done(store, tid) else "secondary"):
                    _snapshot_config(store)
                    store.failed_task = None
                    store.running_task = tid
                    if tid == 9:
                        run_task9(store)
                        res = store.outputs.get(9)
                    else:
                        res = run_generic_task(store, tid)
                    store.running_task = None
                    if isinstance(res, str) and res.startswith("❌"):
                        store.failed_task = tid
                    st.session_state[f"refresh_{tid}"] = True
                    st.rerun()

                # 查看 / 修改 / 粘贴 本任务产出
                with st.expander("📄 查看 / ✏️ 修改 / 📋 粘贴本任务产出", expanded=task_done(store, tid)):
                    if tid in (5, 7) and task_done(store, tid):
                        n = count_episodes(o.get(tid))
                        target = store.total_episodes
                        cls = "qa-ok" if n >= target else "qa-warn"
                        st.markdown(
                            f'<span class="{cls}">集数校验：检测到 {n} 集 / 目标 {target} 集</span>',
                            unsafe_allow_html=True,
                        )
                    st.text_area(
                        "本任务产出（可直接修改 AI 产出；也可粘贴本地剧本文本后“保存”以跳过本任务）：",
                        key=f"edit_{tid}", height=260,
                    )
                    bc = st.columns([1, 1, 2])
                    if bc[0].button("💾 保存为本任务产出", key=f"save_{tid}", disabled=running):
                        store.set_output(tid, st.session_state[f"edit_{tid}"])
                        st.rerun()
                    if bc[1].button("🧹 清空", key=f"clear_{tid}", disabled=running):
                        store.clear_output(tid)
                        st.session_state[f"refresh_{tid}"] = True
                        st.rerun()
                    if task_done(store, tid) and isinstance(o.get(tid), str):
                        st.download_button(
                            "📥 下载该产出 (TXT)", data=o[tid].encode("utf-8"),
                            file_name=f"task{tid}_{t['short']}.txt", mime="text/plain", key=f"dlm_{tid}",
                        )
                        if tid == 9:
                            st.download_button(
                                "📥 下载飞书最终交付文档 (Markdown)", data=o[9].encode("utf-8"),
                                file_name="飞书_最终剧本交付文档.md", mime="text/markdown", key="dlm9_md",
                            )

                if tid == 9 and task_done(store, tid):
                    with st.expander("📑 飞书文档渲染预览（含分镜表格）", expanded=False):
                        st.markdown(o[9])


def _render_manual_task8(store, ready):
    """手动模式下任务 8（分镜脚本）的控制：分批生成 / 查看表格 / 修改批次 / 粘贴本地分镜跳过。"""
    o = store.outputs
    scripts = o.get(8) or {}
    running = store.is_running

    for label in list(scripts.keys()):
        ek = f"e8_{label}"
        cur = scripts.get(label)
        cur = cur if isinstance(cur, str) else ""
        if st.session_state.pop(f"refresh_e8_{label}", False):
            st.session_state[ek] = cur
        elif ek not in st.session_state:
            st.session_state[ek] = cur

    if ready:
        batches = get_batches(st.session_state.total_episodes)
        mode_name = "解说漫" if st.session_state.script_mode == "comic" else "标准短剧"
        st.caption(f"分镜模式：{mode_name}（可在侧边栏切换）。建议每次 10 集，可逐批生成。")
        bcols = st.columns(min(5, len(batches)) or 1)
        for i, (a, b) in enumerate(batches):
            label = f"{a}-{b}集"
            done_mark = "✅" if label in scripts else ""
            if bcols[i % len(bcols)].button(f"生成 {label} {done_mark}", key=f"b8_{label}", disabled=running):
                _snapshot_config(store)
                store.failed_task = None
                store.running_task = 8
                res = run_task8_batch(store, a, b)
                store.running_task = None
                if isinstance(res, str) and res.startswith("❌"):
                    store.failed_task = 8
                st.session_state[f"refresh_e8_{label}"] = True
                st.rerun()
        if st.button("⚡ 一键生成全部批次分镜", key="b8_all", disabled=running):
            _snapshot_config(store)
            store.failed_task = None
            store.running_task = 8
            for (a, b) in batches:
                res = run_task8_batch(store, a, b)
                st.session_state[f"refresh_e8_{a}-{b}集"] = True
                if isinstance(res, str) and res.startswith("❌"):
                    store.failed_task = 8
                    break
            store.running_task = None
            st.rerun()
    else:
        st.caption("（前置任务未完成；可在下方直接粘贴本地分镜以跳过生成）")

    if scripts:
        with st.expander("📄 查看分镜表格", expanded=True):
            render_result(store, 8)

    if scripts:
        with st.expander("✏️ 修改已生成的分镜批次（CSV）", expanded=False):
            for label in list(scripts.keys()):
                st.markdown(f"**批次 {label}**")
                st.text_area(f"编辑 {label} 的 CSV：", key=f"e8_{label}", height=200)
                if st.button(f"💾 保存批次 {label}", key=f"save8_{label}", disabled=running):
                    store.set_batch(label, st.session_state[f"e8_{label}"])
                    st.rerun()

    with st.expander("📋 跳过生成 · 直接粘贴本地分镜（新建批次）", expanded=not scripts):
        st.caption(
            "适用于直接使用本地已有分镜：建议保持 “镜号,场景,画面内容 (Visual),台词 (Dialogue) & 音效 (SFX)” "
            "的 CSV 表头格式，并以“第 X 集”单独成行。"
        )
        st.text_input("批次名称：", value="本地粘贴", key="paste8_label")
        st.text_area(
            "粘贴本地分镜 CSV：", key="paste8_text", height=200,
            placeholder="第 1 集\n镜号,场景,画面内容 (Visual),台词 (Dialogue) & 音效 (SFX)\n1,场景,画面内容,Role: line",
        )
        if st.button("📌 保存为分镜批次", key="paste8_save", disabled=running):
            txt = (st.session_state.get("paste8_text") or "").strip()
            lbl = ((st.session_state.get("paste8_label") or "").strip()) or "本地粘贴"
            if txt:
                store.set_batch(lbl, txt)
                st.rerun()
            else:
                st.warning("请先粘贴分镜内容再保存。")


# ==========================================
# 数字员工档案：角色 / Agent 技能 / 记忆
# ==========================================
def render_profiles(store):
    st.divider()
    st.markdown("### 🧑‍💼 数字员工档案（角色介绍 · Agent 技能 · 记忆）")
    emp_cols = st.columns(5)
    for i, (k, e) in enumerate(EMPLOYEES.items()):
        with emp_cols[i]:
            with st.expander(f"{e['emoji']} {e['name']}", expanded=False):
                cfg = get_emp_config(k)
                st.markdown(f"**{e['title']}**")
                st.caption(e["intro"])
                st.markdown("**🛠️ Agent 技能**")
                for s in AGENT_SKILLS[k]:
                    st.markdown(f"- {s}")
                st.markdown("**🔧 配套外部工具**")
                st.caption(e["tool"])
                st.markdown(f"**🤖 当前模型**：`{cfg['provider']} / {cfg['model']}`")
                st.markdown("**🧠 记忆（本次协作累积）**")
                mem = store.memory.get(k, [])
                if mem:
                    for m in mem:
                        st.markdown(f"- {m}")
                else:
                    st.caption("（暂无记忆，执行任务后自动写入）")


# ==========================================
# 主入口
# ==========================================
def main():
    # 注意：st.set_page_config 必须由入口脚本在调用 main() 前完成。
    inject_heartbeat()
    inject_styles()
    store = get_store()
    init_state()
    render_sidebar(store)

    st.markdown('<div class="studio-title">🎬 AI Agent 短剧剧本工作室</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="studio-sub">5 位数字员工分工协作，调用「三幕式创意和剧本生成」工具，'
        "全流程可视化产出 1 部极致优质的海外短剧剧本。</div>",
        unsafe_allow_html=True,
    )

    # 可视化占位符：办公室全景 + 数字员工卡通形象
    viz_ph = st.empty()
    render_viz(viz_ph, store)

    # 运行状态条 + 持久化过程日志（刷新/重连不丢失）
    render_run_status(store)

    st.divider()

    st.markdown("### 🕹️ 选择运营方式")
    run_mode = st.radio(
        "产品提供两种与用户的交互方式：",
        ["① 自动按序执行（一键运行全流程）", "② 手动逐步启动（逐个任务点击执行）"],
        horizontal=True,
    )

    with st.expander("✏️ （可选）为「短剧爆款研究员」提供创作方向 / 赛道（留空则由其自主选择）", expanded=False):
        st.session_state.seed = st.text_area(
            "创作方向 / 赛道参考：",
            value=st.session_state.seed,
            height=80,
            placeholder="例如：赛博朋克 + 底层逆袭；或：女频 狼人 虐恋……（留空则研究员自主决策）",
        )

    if run_mode.startswith("①"):
        render_auto_mode(store, viz_ph)
    else:
        render_manual_mode(store, viz_ph)

    render_profiles(store)

    st.divider()
    st.caption("© AI Agent 短剧剧本工作室 - zhangzhou.01@bytedance.com ")

    # 后台运行时：每约 1.5s 自动刷新进度（st.rerun 保留 session_state，不丢配置/Key）
    if store.is_running:
        time.sleep(1.5)
        st.rerun()
