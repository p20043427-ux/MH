"""
ui/finance_dashboard.py  ─  원무 현황 대시보드 v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[3탭 구조]
  탭1 실시간 현황  — KPI / 진료과 대기·진료·완료 / 키오스크 / 퇴원 파이프라인
  탭2 수납·미수금  — 보험유형별 파이 / 30일 수납 추세 / 진료과별 수납 / 미수금 연령별
  탭3 통계·분석   — 외래 추세 라인 / 평균 대기시간 추세 / 재원일수 분포

[사용 Oracle VIEW]
  기존: V_OPD_KPI / V_OPD_DEPT_STATUS / V_KIOSK_STATUS
        V_DISCHARGE_PIPELINE / V_OPD_DEPT_TREND / V_WARD_BED_DETAIL
  신규: V_FINANCE_TODAY / V_FINANCE_TREND / V_FINANCE_BY_DEPT
        V_OVERDUE_STAT / V_WAITTIME_TREND / V_LOS_DIST
"""

from __future__ import annotations
import time
from typing import Any, Dict, List, Optional
import streamlit as st

try:
    import plotly.graph_objects as go

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

import sys, os as _os

_PR = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), ".."))
if _PR not in sys.path:
    sys.path.insert(0, _PR)

try:
    from utils.logger import get_logger as _gl
    from config.settings import settings as _s

    logger = _gl(__name__, log_dir=_s.log_dir)
except Exception:
    import logging as _l

    logger = _l.getLogger(__name__)

# ── Oracle 쿼리 ─────────────────────────────────────────────────────
FQ: Dict[str, str] = {
    "opd_kpi": "SELECT * FROM JAIN_WM.V_OPD_KPI WHERE ROWNUM = 1",
    "opd_dept_status": "SELECT * FROM JAIN_WM.V_OPD_DEPT_STATUS ORDER BY 대기 DESC",
    "kiosk_status": "SELECT * FROM JAIN_WM.V_KIOSK_STATUS ORDER BY 키오스크ID",
    "discharge_pipeline": "SELECT * FROM JAIN_WM.V_DISCHARGE_PIPELINE ORDER BY 단계, 병동명",
    "opd_dept_trend": "SELECT * FROM JAIN_WM.V_OPD_DEPT_TREND ORDER BY 기준일, 외래환자수 DESC",
    "ward_bed_detail": "SELECT * FROM JAIN_WM.V_WARD_BED_DETAIL ORDER BY 병동명",
    "finance_today": "SELECT * FROM JAIN_WM.V_FINANCE_TODAY ORDER BY 금액 DESC",
    "finance_trend": "SELECT * FROM JAIN_WM.V_FINANCE_TREND ORDER BY 기준일",
    "finance_by_dept": "SELECT * FROM JAIN_WM.V_FINANCE_BY_DEPT ORDER BY 수납금액 DESC",
    "overdue_stat": "SELECT * FROM JAIN_WM.V_OVERDUE_STAT ORDER BY 연령구분",
    "waittime_trend": "SELECT * FROM JAIN_WM.V_WAITTIME_TREND ORDER BY 기준일, 진료과명",
    "los_dist": "SELECT * FROM JAIN_WM.V_LOS_DIST ORDER BY 구간순서",
}


def _fq(key: str) -> List[Dict[str, Any]]:
    try:
        from db.oracle_client import execute_query

        return execute_query(FQ[key]) or []
    except Exception as e:
        logger.warning(f"[Finance] {key}: {e}")
        return []


# ── 팔레트 ──────────────────────────────────────────────────────────
C = {
    "blue": "#1E40AF",
    "blue_l": "#EFF6FF",
    "indigo": "#4F46E5",
    "indigo_l": "#EEF2FF",
    "violet": "#7C3AED",
    "violet_l": "#F5F3FF",
    "teal": "#0891B2",
    "teal_l": "#ECFEFF",
    "green": "#059669",
    "green_l": "#DCFCE7",
    "yellow": "#D97706",
    "yellow_l": "#FEF3C7",
    "orange": "#EA580C",
    "orange_l": "#FFF7ED",
    "red": "#DC2626",
    "red_l": "#FEE2E2",
    "t1": "#0F172A",
    "t2": "#334155",
    "t3": "#64748B",
    "t4": "#94A3B8",
}

# ── CSS ─────────────────────────────────────────────────────────────
_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/variable/pretendardvariable.css');
.main,[data-testid="stAppViewContainer"],[data-testid="stMarkdownContainer"]{
  font-family:'Pretendard Variable','Malgun Gothic',sans-serif!important;font-size:14px!important;}
[data-testid="stAppViewContainer"]>.main{padding-top:.3rem!important;padding-left:.75rem!important;padding-right:.75rem!important;}
[data-testid="stVerticalBlock"]{gap:.4rem!important;}
.element-container{margin-bottom:0!important;}
[data-testid="stMarkdownContainer"]:empty{display:none!important;}
.fn-topbar{height:3px;background:linear-gradient(90deg,#1E40AF 0%,#7C3AED 50%,#E2E8F0 100%);border-radius:2px 2px 0 0;}
.fn-kpi{background:#fff;border:1px solid #F0F4F8;border-radius:12px;padding:13px 15px;min-height:118px;
  display:flex;flex-direction:column;justify-content:space-between;box-shadow:0 3px 10px rgba(0,0,0,.06);}
.fn-kpi:hover{box-shadow:0 6px 18px rgba(0,0,0,.10);}
.fn-kpi-icon{font-size:18px;margin-bottom:3px;}
.fn-kpi-label{font-size:10px;font-weight:700;color:#64748B;text-transform:uppercase;letter-spacing:.12em;}
.fn-kpi-value{font-size:30px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums;letter-spacing:-.03em;}
.fn-kpi-unit{font-size:13px;color:#64748B;font-weight:500;margin-left:2px;}
.fn-kpi-sub{font-size:11px;color:#94A3B8;margin-top:3px;}
.goal-bar-wrap{height:5px;background:#F1F5F9;border-radius:3px;margin-top:5px;overflow:hidden;}
.goal-bar-fill{height:100%;border-radius:3px;}
.wd-card{background:#fff;border:1px solid #F0F4F8;border-radius:12px;padding:14px 16px;box-shadow:0 3px 10px rgba(0,0,0,.06);}
.wd-sec{font-size:13px;font-weight:700;color:#0F172A;margin-bottom:10px;padding-bottom:8px;
  border-bottom:1px solid #F1F5F9;display:flex;align-items:center;gap:7px;}
.wd-sec-bar{width:3px;height:15px;border-radius:2px;flex-shrink:0;}
.wd-sec-sub{font-size:11px;color:#94A3B8;font-weight:400;margin-left:3px;}
.badge{border-radius:5px;padding:2px 8px;font-size:11px;font-weight:700;display:inline-block;}
.badge-blue{background:#DBEAFE;color:#1E40AF;}.badge-green{background:#DCFCE7;color:#15803D;}
.badge-yellow{background:#FEF3C7;color:#92400E;}.badge-red{background:#FEE2E2;color:#991B1B;}
.badge-purple{background:#EDE9FE;color:#5B21B6;}.badge-gray{background:#F1F5F9;color:#475569;}
.dc-pipeline{display:flex;border:1px solid #F0F4F8;border-radius:10px;overflow:hidden;background:#F8FAFC;margin-bottom:12px;}
.dc-step{flex:1;padding:14px 8px;text-align:center;border-right:1px solid #E2E8F0;}
.dc-step:last-child{border-right:none;}
.dc-step-code{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;margin-bottom:4px;}
.dc-step-num{font-size:30px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums;}
.dc-step-desc{font-size:10px;color:#64748B;margin-top:3px;}
.overdue-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #F8FAFC;}
.overdue-label{font-size:12px;font-weight:700;width:80px;flex-shrink:0;}
.overdue-bar-wrap{flex:1;height:8px;background:#F1F5F9;border-radius:4px;overflow:hidden;}
.overdue-bar{height:100%;border-radius:4px;}
.overdue-val{font-size:12px;font-weight:700;font-family:Consolas,monospace;width:65px;text-align:right;flex-shrink:0;}
.kiosk-card{background:#fff;border:1.5px solid #E2E8F0;border-radius:10px;padding:11px 13px;box-shadow:0 2px 6px rgba(0,0,0,.04);}
[data-testid="stTabs"]>div:first-child{border-bottom:1.5px solid #E2E8F0!important;gap:0!important;}
[data-testid="stTabs"] button{font-size:13px!important;font-weight:600!important;padding:6px 16px!important;border-radius:0!important;color:#64748B!important;}
[data-testid="stTabs"] button[aria-selected="true"]{color:#1E40AF!important;border-bottom:2.5px solid #1E40AF!important;background:transparent!important;}
[data-testid="stSelectbox"]>div>div,[data-testid="stMultiSelect"]>div>div{
  border-radius:8px!important;border:1.5px solid #BFDBFE!important;
  background:#EFF6FF!important;font-size:13px!important;font-weight:600!important;color:#1E40AF!important;}
button[kind="secondary"]{font-size:13px!important;height:34px!important;border-radius:8px!important;}
.wait-danger{color:#DC2626;font-weight:800;} .wait-warn{color:#F59E0B;font-weight:700;} .wait-ok{color:#059669;font-weight:600;}
</style>
"""


# ── 헬퍼 ────────────────────────────────────────────────────────────
def _kpi_card(
    col, icon, label, val, unit, sub, color, goal_pct: Optional[float] = None
):
    _bar = ""
    if goal_pct is not None:
        _p = min(max(int(goal_pct), 0), 100)
        _bc = C["green"] if _p >= 100 else C["yellow"] if _p >= 70 else C["red"]
        _bar = (
            f'<div class="goal-bar-wrap"><div class="goal-bar-fill" style="width:{_p}%;background:{_bc};"></div></div>'
            f'<div style="font-size:10px;color:{_bc};font-weight:700;margin-top:2px;">목표 {_p}%</div>'
        )
    col.markdown(
        f'<div class="fn-kpi" style="border-top:3px solid {color};">'
        f'<div class="fn-kpi-icon">{icon}</div>'
        f'<div class="fn-kpi-label">{label}</div>'
        f'<div class="fn-kpi-value" style="color:{color};">{val}'
        f'<span class="fn-kpi-unit">{unit}</span></div>'
        f'<div class="fn-kpi-sub">{sub}</div>{_bar}</div>',
        unsafe_allow_html=True,
    )


def _sec_hd(title, sub="", color=None):
    color = color or C["blue"]
    st.markdown(
        f'<div class="wd-sec"><span class="wd-sec-bar" style="background:{color};"></span>'
        f"{title}{'<span class=wd-sec-sub>' + sub + '</span>' if sub else ''}</div>",
        unsafe_allow_html=True,
    )


def _fmt_won(n: int) -> str:
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}억"
    if n >= 10_000:
        return f"{n // 10_000:,}만"
    return f"{n:,}"


def _gap(px=8):
    st.markdown(f'<div style="height:{px}px"></div>', unsafe_allow_html=True)


def _plotly_empty():
    st.markdown(
        '<div style="padding:32px;text-align:center;color:#94A3B8;font-size:13px;">데이터 없음</div>',
        unsafe_allow_html=True,
    )


_PALETTE = [
    "#1E40AF",
    "#059669",
    "#D97706",
    "#DC2626",
    "#7C3AED",
    "#0891B2",
    "#DB2777",
    "#0284C7",
    "#65A30D",
    "#9333EA",
]
_PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#333333", size=11),
    margin=dict(l=0, r=8, t=8, b=8),
    xaxis=dict(gridcolor="#F1F5F9", tickfont=dict(size=10), zeroline=False),
    yaxis=dict(gridcolor="#F1F5F9", tickfont=dict(size=10), zeroline=False),
)


# ════════════════════════════════════════════════════════════════════
# 탭 1 — 실시간 현황
# ════════════════════════════════════════════════════════════════════
def _tab_realtime(opd_kpi, dept_status, kiosk_status, discharge_pipe, bed_detail):
    # KPI 5개
    _opd = int(opd_kpi.get("총내원", 0) or 0)
    _adm = sum(int(r.get("금일입원", 0) or 0) for r in bed_detail)
    _disc = sum(int(r.get("금일퇴원", 0) or 0) for r in bed_detail)
    _wait = sum(int(r.get("대기", 0) or 0) for r in dept_status)
    _done = sum(int(r.get("완료", 0) or 0) for r in dept_status)
    _wc = C["red"] if _wait >= 30 else C["yellow"] if _wait >= 15 else C["green"]

    k1, k2, k3, k4, k5 = st.columns(5, gap="small")
    _kpi_card(k1, "👥", "금일 외래", f"{_opd:,}", "명", "금일 내원 합계", C["blue"])
    _kpi_card(k2, "🏥", "금일 입원", f"{_adm:,}", "명", "입원 처리 완료", C["indigo"])
    _kpi_card(k3, "📤", "금일 퇴원", f"{_disc:,}", "명", "퇴원 처리 완료", C["t2"])
    _kpi_card(k4, "⏳", "현재 대기", f"{_wait:,}", "명", "전체 진료과 합산", _wc)
    _kpi_card(k5, "✅", "수납 완료", f"{_done:,}", "명", "진료·수납 완료", C["green"])
    _gap()

    # 진료과 대기 테이블 + 바차트
    ct, cc = st.columns([5, 4], gap="small")
    with ct:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["blue"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("📊 진료과별 대기·진료·완료", "실시간", C["blue"])
        _TH = "padding:8px 10px;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#64748B;border-bottom:1.5px solid #E2E8F0;background:#F8FAFC;white-space:nowrap;"
        _t = (
            '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:13px;">'
            f"<thead><tr>"
            f'<th style="{_TH}text-align:left;">진료과</th>'
            f'<th style="{_TH}text-align:right;color:{C["yellow"]};">대기</th>'
            f'<th style="{_TH}text-align:right;color:{C["blue"]};">진료중</th>'
            f'<th style="{_TH}text-align:right;color:{C["green"]};">완료</th>'
            f'<th style="{_TH}text-align:right;">합계</th>'
            f'<th style="{_TH}text-align:right;">평균대기</th>'
            f'<th style="{_TH}text-align:center;">상태</th>'
            f"</tr></thead><tbody>"
        )
        if dept_status:
            for i, r in enumerate(dept_status):
                _d = r.get("진료과명", "")
                _w = int(r.get("대기", 0) or 0)
                _p = int(r.get("진료중", 0) or 0)
                _e = int(r.get("완료", 0) or 0)
                _t2 = int(r.get("합계", _w + _p + _e) or 0)
                _a = float(r.get("평균대기시간", 0) or 0)
                _bg = "#F8FAFC" if i % 2 == 0 else "#fff"
                _ac = (
                    "wait-danger"
                    if _a >= 30
                    else "wait-warn"
                    if _a >= 15
                    else "wait-ok"
                )
                _b = (
                    '<span class="badge badge-red">혼잡</span>'
                    if _w >= 10
                    else '<span class="badge badge-yellow">보통</span>'
                    if _w >= 5
                    else '<span class="badge badge-green">여유</span>'
                    if _w > 0
                    else '<span class="badge badge-gray">대기없음</span>'
                )
                _td = f"padding:8px 10px;background:{_bg};border-bottom:1px solid #F8FAFC;"
                _t += (
                    f"<tr><td style='{_td}font-weight:700;color:{C['t1']};'>{_d}</td>"
                    f"<td style='{_td}text-align:right;font-weight:800;color:{C['yellow']};font-family:Consolas,monospace;font-size:15px;'>{_w}</td>"
                    f"<td style='{_td}text-align:right;font-weight:700;color:{C['blue']};font-family:Consolas,monospace;'>{_p}</td>"
                    f"<td style='{_td}text-align:right;color:{C['green']};font-family:Consolas,monospace;'>{_e}</td>"
                    f"<td style='{_td}text-align:right;color:{C['t3']};font-family:Consolas,monospace;'>{_t2}</td>"
                    f"<td style='{_td}text-align:right;' class='{_ac}'>{_a:.0f}분</td>"
                    f"<td style='{_td}text-align:center;'>{_b}</td></tr>"
                )
            _sw = sum(int(r.get("대기", 0) or 0) for r in dept_status)
            _sp = sum(int(r.get("진료중", 0) or 0) for r in dept_status)
            _se = sum(int(r.get("완료", 0) or 0) for r in dept_status)
            _sh = f"padding:8px 10px;background:#EFF6FF;border-top:2px solid #BFDBFE;font-weight:700;"
            _t += (
                f"<tr><td style='{_sh}color:{C['blue']};'>합계</td>"
                f"<td style='{_sh}text-align:right;color:{C['yellow']};font-family:Consolas,monospace;font-size:15px;'>{_sw}</td>"
                f"<td style='{_sh}text-align:right;color:{C['blue']};font-family:Consolas,monospace;'>{_sp}</td>"
                f"<td style='{_sh}text-align:right;color:{C['green']};font-family:Consolas,monospace;'>{_se}</td>"
                f"<td style='{_sh}text-align:right;font-family:Consolas,monospace;color:{C['blue']};'>{_sw + _sp + _se}</td>"
                f"<td style='{_sh}'>─</td><td style='{_sh}text-align:center;'>─</td></tr>"
            )
        else:
            _t += '<tr><td colspan="7" style="padding:30px;text-align:center;color:#94A3B8;">V_OPD_DEPT_STATUS 확인</td></tr>'
        st.markdown(_t + "</tbody></table></div></div>", unsafe_allow_html=True)

    with cc:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["blue"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("진료과 대기 시각화")
        if dept_status and HAS_PLOTLY:
            _top = sorted(dept_status, key=lambda x: -int(x.get("대기", 0) or 0))[:10]
            _ds = [r.get("진료과명", "") for r in reversed(_top)]
            _wv = [int(r.get("대기", 0) or 0) for r in reversed(_top)]
            _pv = [int(r.get("진료중", 0) or 0) for r in reversed(_top)]
            _ev = [int(r.get("완료", 0) or 0) for r in reversed(_top)]
            _fig = go.Figure()
            for _vals, _name, _clr in [
                (_ev, "완료", C["green"]),
                (_pv, "진료중", C["blue"]),
                (_wv, "대기", C["yellow"]),
            ]:
                _fig.add_trace(
                    go.Bar(
                        name=_name,
                        y=_ds,
                        x=_vals,
                        orientation="h",
                        marker_color=_clr,
                        marker=dict(line=dict(width=0)),
                        hovertemplate=f"%{{y}}: {_name} %{{x}}명<extra></extra>",
                    )
                )
            _l = dict(
                orientation="h",
                y=1.04,
                x=0.5,
                xanchor="center",
                font=dict(size=11),
                bgcolor="rgba(0,0,0,0)",
                traceorder="reversed",
            )
            _fig.update_layout(
                **_PLOTLY_LAYOUT,
                barmode="stack",
                height=max(200, len(_ds) * 30),
                margin=dict(l=0, r=30, t=4, b=4),
                legend=_l,
                bargap=0.3,
            )
            st.plotly_chart(_fig, use_container_width=True, key="rt_dept_bar")
        else:
            _plotly_empty()
        st.markdown("</div>", unsafe_allow_html=True)

    _gap()

    # 키오스크 + 퇴원 파이프라인
    ck, cd = st.columns([1, 1], gap="small")

    with ck:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["violet"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("🖥️ 키오스크 운영 현황", time.strftime("%H:%M") + " 기준", C["violet"])
        if kiosk_status:
            _kon = sum(
                1
                for r in kiosk_status
                if r.get("가동상태", "") in ("정상", "ON", "운영중")
            )
            _kerr = sum(
                1
                for r in kiosk_status
                if r.get("가동상태", "") in ("오류", "ERROR", "OFF")
            )
            _krec = sum(int(r.get("접수건수", 0) or 0) for r in kiosk_status)
            st.markdown(
                f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;">'
                f'<span class="badge badge-green">✅ 정상 {_kon}대</span>'
                f'<span class="badge badge-red">❌ 오류 {_kerr}대</span>'
                f'<span class="badge badge-blue">접수 {_krec:,}건</span></div>',
                unsafe_allow_html=True,
            )
            for _chunk in [
                kiosk_status[i : i + 3] for i in range(0, len(kiosk_status), 3)
            ]:
                _cols = st.columns(len(_chunk), gap="small")
                for _col, _r in zip(_cols, _chunk):
                    _id = _r.get("키오스크ID", "")
                    _loc = _r.get("위치", "")
                    _rec = int(_r.get("접수건수", 0) or 0)
                    _err = int(_r.get("오류건수", 0) or 0)
                    _st = _r.get("가동상태", "")
                    _on = _st in ("정상", "ON", "운영중")
                    _er = _st in ("오류", "ERROR", "OFF")
                    _bc = "#86EFAC" if _on else "#FCA5A5" if _er else "#E2E8F0"
                    _bg = "#F0FDF4" if _on else "#FEF2F2" if _er else "#F8FAFC"
                    _sl = "🟢 정상" if _on else "🔴 오류" if _er else "🟡 점검"
                    _sc = "#15803D" if _on else "#DC2626" if _er else "#F59E0B"
                    _col.markdown(
                        f'<div class="kiosk-card" style="border-color:{_bc};background:{_bg};">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                        f'<b style="font-size:12px;">{_id}</b>'
                        f'<span style="font-size:11px;font-weight:700;color:{_sc};">{_sl}</span></div>'
                        f'<div style="font-size:11px;color:{C["t3"]};">{_loc}</div>'
                        f'<div style="display:flex;justify-content:space-around;margin-top:6px;">'
                        f'<div style="text-align:center;">'
                        f'<div style="font-size:19px;font-weight:800;color:{C["blue"]};font-family:Consolas,monospace;">{_rec}</div>'
                        f'<div style="font-size:9px;color:{C["t4"]};">접수</div></div>'
                        f'<div style="text-align:center;">'
                        f'<div style="font-size:19px;font-weight:800;color:{"#DC2626" if _err > 0 else C["t4"]};font-family:Consolas,monospace;">{_err}</div>'
                        f'<div style="font-size:9px;color:{C["t4"]};">오류</div></div>'
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.markdown(
                '<div style="padding:28px;text-align:center;color:#94A3B8;font-size:13px;">V_KIOSK_STATUS 확인</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with cd:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["green"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd(
            "🚶 퇴원 처리 파이프라인", time.strftime("%Y-%m-%d") + " 기준", C["green"]
        )
        _PC = [
            ("퇴원지시", "DC", C["violet"], C["violet_l"], "퇴원 오더 완료"),
            ("계산대기", "PC", C["yellow"], C["yellow_l"], "원무 계산 대기"),
            ("계산완료", "PD", C["blue"], C["blue_l"], "수납 처리 완료"),
            ("퇴원완료", "DD", C["green"], C["green_l"], "최종 퇴원"),
        ]
        _pcnt: Dict[str, int] = {}
        for r in discharge_pipe:
            _s = r.get("단계", "")
            _n = int(r.get("환자수", 0) or 0)
            if _s:
                _pcnt[_s] = _pcnt.get(_s, 0) + _n

        st.markdown('<div class="dc-pipeline">', unsafe_allow_html=True)
        for _lbl, _code, _clr, _bg, _desc in _PC:
            _n = _pcnt.get(_lbl, 0)
            st.markdown(
                f'<div class="dc-step" style="background:{_bg};">'
                f'<div class="dc-step-code" style="color:{_clr};">{_lbl}</div>'
                f'<div class="dc-step-num" style="color:{_clr};">{_n}</div>'
                f'<div class="dc-step-desc">{_desc}</div></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

        if discharge_pipe:
            _wmap: Dict[str, Dict[str, int]] = {}
            for r in discharge_pipe:
                _w2 = r.get("병동명", "기타")
                _s2 = r.get("단계", "")
                _n2 = int(r.get("환자수", 0) or 0)
                if _w2 and _s2:
                    _wmap.setdefault(_w2, {})[_s2] = (
                        _wmap.get(_w2, {}).get(_s2, 0) + _n2
                    )
            _TH2 = "padding:7px 10px;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#64748B;border-bottom:1.5px solid #E2E8F0;background:#F8FAFC;"
            _t2 = (
                '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:12.5px;">'
                f"<thead><tr>"
                f'<th style="{_TH2}text-align:left;">병동</th>'
                f'<th style="{_TH2}text-align:right;color:{C["violet"]};">지시</th>'
                f'<th style="{_TH2}text-align:right;color:{C["yellow"]};">계산대기</th>'
                f'<th style="{_TH2}text-align:right;color:{C["blue"]};">계산완료</th>'
                f'<th style="{_TH2}text-align:right;color:{C["green"]};">퇴원완료</th>'
                f"</tr></thead><tbody>"
            )
            for i, (_wn, _sv) in enumerate(sorted(_wmap.items())):
                _bg2 = "#F8FAFC" if i % 2 == 0 else "#fff"
                _td2 = f"padding:7px 10px;border-bottom:1px solid #F8FAFC;background:{_bg2};"
                _t2 += (
                    f"<tr><td style='{_td2}font-weight:700;'>{_wn}</td>"
                    f"<td style='{_td2}text-align:right;color:{C['violet']};font-family:Consolas,monospace;'>{_sv.get('퇴원지시', 0) or '─'}</td>"
                    f"<td style='{_td2}text-align:right;color:{C['yellow']};font-family:Consolas,monospace;'>{_sv.get('계산대기', 0) or '─'}</td>"
                    f"<td style='{_td2}text-align:right;color:{C['blue']};font-family:Consolas,monospace;'>{_sv.get('계산완료', 0) or '─'}</td>"
                    f"<td style='{_td2}text-align:right;font-weight:700;color:{C['green']};font-family:Consolas,monospace;'>{_sv.get('퇴원완료', 0) or '─'}</td></tr>"
                )
            st.markdown(_t2 + "</tbody></table></div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════
# 탭 2 — 수납·미수금
# ════════════════════════════════════════════════════════════════════
def _tab_revenue(finance_today, finance_trend, finance_by_dept, overdue_stat):
    _tot_amt = sum(int(r.get("금액", 0) or 0) for r in finance_today)
    _tot_cnt = sum(int(r.get("건수", 0) or 0) for r in finance_today)
    _tot_gol = sum(int(r.get("목표금액", 0) or 0) for r in finance_today)
    _gol_pct = round(_tot_amt / _tot_gol * 100, 1) if _tot_gol > 0 else 0.0
    _ov_amt = sum(int(r.get("금액", 0) or 0) for r in overdue_stat)

    k1, k2, k3, k4 = st.columns(4, gap="small")
    _kpi_card(
        k1,
        "💰",
        "금일 수납 합계",
        _fmt_won(_tot_amt),
        "",
        f"건수 {_tot_cnt:,}건",
        C["blue"],
        goal_pct=_gol_pct,
    )
    _kpi_card(
        k2,
        "🎯",
        "목표 달성률",
        f"{_gol_pct:.1f}",
        "%",
        f"목표 {_fmt_won(_tot_gol)}",
        C["green"] if _gol_pct >= 100 else C["yellow"] if _gol_pct >= 70 else C["red"],
    )
    _kpi_card(
        k3, "🔴", "미수금 합계", _fmt_won(_ov_amt), "", "30일 이상 기준", C["red"]
    )
    _kpi_card(
        k4, "📋", "금일 건수", f"{_tot_cnt:,}", "건", "보험유형 합산", C["indigo"]
    )
    _gap()

    # 보험유형 파이 + 수납 추세
    cp, ct = st.columns([2, 3], gap="small")

    with cp:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["indigo"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("🥧 보험유형별 수납", "금일 기준", C["indigo"])
        if finance_today and HAS_PLOTLY:
            _labels = [r.get("보험유형", "기타") for r in finance_today]
            _values = [int(r.get("금액", 0) or 0) for r in finance_today]
            _counts = [int(r.get("건수", 0) or 0) for r in finance_today]
            _pcolors = [
                C["blue"],
                C["green"],
                C["yellow"],
                C["violet"],
                C["teal"],
                C["orange"],
                C["red"],
            ]
            _fig = go.Figure(
                go.Pie(
                    labels=_labels,
                    values=_values,
                    customdata=_counts,
                    hovertemplate="<b>%{label}</b><br>금액:%{value:,}원<br>건수:%{customdata}건<br>%{percent}<extra></extra>",
                    marker=dict(
                        colors=_pcolors[: len(_labels)],
                        line=dict(color="#fff", width=2),
                    ),
                    hole=0.52,
                    textinfo="label+percent",
                    textfont=dict(size=11),
                    insidetextorientation="radial",
                )
            )
            _fig.update_layout(
                height=260,
                margin=dict(l=0, r=0, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#333", size=11),
                legend=dict(
                    orientation="v",
                    x=1.02,
                    y=0.5,
                    font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)",
                ),
                annotations=[
                    dict(
                        text=f"<b>{_fmt_won(_tot_amt)}</b>",
                        x=0.5,
                        y=0.5,
                        font=dict(size=13, color=C["t1"]),
                        showarrow=False,
                    )
                ],
            )
            st.plotly_chart(_fig, use_container_width=True, key="rev_pie")
            # 수치 테이블
            _TH3 = "padding:7px 10px;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#64748B;border-bottom:1.5px solid #E2E8F0;background:#F8FAFC;"
            _t3 = (
                '<table style="width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px;">'
                f"<thead><tr>"
                f'<th style="{_TH3}text-align:left;">보험유형</th>'
                f'<th style="{_TH3}text-align:right;">건수</th>'
                f'<th style="{_TH3}text-align:right;">금액</th>'
                f'<th style="{_TH3}text-align:right;">달성률</th>'
                f"</tr></thead><tbody>"
            )
            for i, r in enumerate(finance_today):
                _typ = r.get("보험유형", "")
                _cnt = int(r.get("건수", 0) or 0)
                _amt = int(r.get("금액", 0) or 0)
                _gol = int(r.get("목표금액", 0) or 0)
                _pct = round(_amt / _gol * 100, 1) if _gol > 0 else 0.0
                _pc = (
                    C["green"]
                    if _pct >= 100
                    else C["yellow"]
                    if _pct >= 70
                    else C["red"]
                )
                _bg3 = "#F8FAFC" if i % 2 == 0 else "#fff"
                _td3 = f"padding:7px 10px;background:{_bg3};border-bottom:1px solid #F8FAFC;"
                _t3 += (
                    f"<tr><td style='{_td3}font-weight:700;'>{_typ}</td>"
                    f"<td style='{_td3}text-align:right;color:{C['t3']};font-family:Consolas,monospace;'>{_cnt:,}</td>"
                    f"<td style='{_td3}text-align:right;font-weight:700;font-family:Consolas,monospace;'>{_fmt_won(_amt)}</td>"
                    f"<td style='{_td3}text-align:right;font-weight:700;color:{_pc};'>{_pct:.0f}%</td></tr>"
                )
            st.markdown(_t3 + "</tbody></table>", unsafe_allow_html=True)
        else:
            st.markdown(
                '<div style="padding:30px;text-align:center;color:#94A3B8;">V_FINANCE_TODAY 확인</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with ct:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["blue"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("📈 최근 30일 수납 추세", "일별 수납 금액", C["blue"])
        if finance_trend and HAS_PLOTLY:
            _dates = [str(r.get("기준일", ""))[:10] for r in finance_trend]
            _amts = [int(r.get("수납금액", 0) or 0) // 10000 for r in finance_trend]
            _cnts = [int(r.get("수납건수", 0) or 0) for r in finance_trend]
            _fig2 = go.Figure()
            _fig2.add_trace(
                go.Bar(
                    x=_dates,
                    y=_amts,
                    name="수납금액(만원)",
                    marker_color=C["blue_l"],
                    marker=dict(line=dict(color=C["blue"], width=0.5)),
                    hovertemplate="%{x}<br>%{y:,}만원<extra></extra>",
                    yaxis="y",
                )
            )
            _fig2.add_trace(
                go.Scatter(
                    x=_dates,
                    y=_amts,
                    name="추세",
                    mode="lines+markers",
                    line=dict(color=C["blue"], width=2.5),
                    marker=dict(
                        size=5, color=C["blue"], line=dict(color="#fff", width=1.5)
                    ),
                    hoverinfo="skip",
                    yaxis="y",
                )
            )
            _fig2.add_trace(
                go.Bar(
                    x=_dates,
                    y=_cnts,
                    name="수납건수",
                    marker_color=C["indigo_l"],
                    marker=dict(line=dict(color=C["indigo"], width=0.5)),
                    hovertemplate="%{x}<br>%{y:,}건<extra></extra>",
                    yaxis="y2",
                    visible="legendonly",
                )
            )
            _lg2 = dict(
                orientation="h",
                y=1.06,
                x=0.5,
                xanchor="center",
                font=dict(size=11),
                bgcolor="rgba(0,0,0,0)",
            )
            _fig2.update_layout(
                **_PLOTLY_LAYOUT,
                height=250,
                margin=dict(l=0, r=40, t=8, b=8),
                legend=_lg2,
                hovermode="x unified",
                bargap=0.25,
                xaxis=dict(
                    gridcolor="#F1F5F9",
                    tickfont=dict(size=10),
                    tickangle=-30,
                    nticks=15,
                ),
                yaxis=dict(
                    gridcolor="#F1F5F9",
                    tickfont=dict(size=10),
                    tickformat=",",
                    title=dict(
                        text="수납금액(만원)", font=dict(size=10, color=C["t3"])
                    ),
                ),
                yaxis2=dict(
                    overlaying="y",
                    side="right",
                    showgrid=False,
                    tickfont=dict(size=10, color=C["indigo"]),
                    title=dict(text="건수", font=dict(size=10, color=C["indigo"])),
                ),
            )
            st.plotly_chart(_fig2, use_container_width=True, key="rev_trend")
            _l7 = finance_trend[-7:]
            _p7 = finance_trend[-14:-7] if len(finance_trend) >= 14 else []
            _l7a = sum(int(r.get("수납금액", 0) or 0) for r in _l7)
            _p7a = sum(int(r.get("수납금액", 0) or 0) for r in _p7)
            _l7c = sum(int(r.get("수납건수", 0) or 0) for r in _l7)
            _df = _l7a - _p7a
            _dc = C["green"] if _df >= 0 else C["red"]
            _ds = f"{'▲' if _df >= 0 else '▼'} {_fmt_won(abs(_df))}"
            st.markdown(
                f'<div style="display:flex;gap:8px;margin-top:6px;flex-wrap:wrap;">'
                f'<span class="badge badge-blue">최근 7일 {_fmt_won(_l7a)}</span>'
                f'<span class="badge badge-gray">{_l7c:,}건</span>'
                f'<span style="background:{_dc}1A;color:{_dc};border-radius:5px;padding:2px 8px;font-size:11px;font-weight:700;">전주 대비 {_ds}</span>'
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            _plotly_empty()
        st.markdown("</div>", unsafe_allow_html=True)

    _gap()

    # 진료과별 수납 + 미수금
    cd2, co = st.columns([3, 2], gap="small")

    with cd2:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["teal"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("🏆 진료과별 수납 현황 (당월)", "금액 순위", C["teal"])
        if finance_by_dept and HAS_PLOTLY:
            _depts2 = [r.get("진료과명", "") for r in finance_by_dept[:12]]
            _amts2 = [
                int(r.get("수납금액", 0) or 0) // 10000 for r in finance_by_dept[:12]
            ]
            _ptc = [int(r.get("환자수", 0) or 0) for r in finance_by_dept[:12]]
            _maxA = max(_amts2) if _amts2 else 1
            _gcol = [f"rgba(30,64,175,{0.3 + 0.7 * (_a / _maxA):.2f})" for _a in _amts2]
            _fig3 = go.Figure(
                go.Bar(
                    x=_amts2,
                    y=_depts2,
                    orientation="h",
                    marker=dict(color=_gcol, line=dict(color=C["blue"], width=0.5)),
                    customdata=_ptc,
                    text=[f"{_a:,}만" for _a in _amts2],
                    textposition="outside",
                    textfont=dict(size=11, color=C["blue"]),
                    hovertemplate="<b>%{y}</b><br>%{x:,}만원<br>환자수:%{customdata}명<extra></extra>",
                )
            )
            _fig3.update_layout(
                **_PLOTLY_LAYOUT,
                height=max(240, len(_depts2) * 28),
                margin=dict(l=0, r=60, t=4, b=4),
                showlegend=False,
                xaxis=dict(
                    gridcolor="#F1F5F9",
                    tickfont=dict(size=10),
                    zeroline=False,
                    ticksuffix="만",
                ),
                yaxis=dict(tickfont=dict(size=11), autorange="reversed"),
                bargap=0.3,
            )
            st.plotly_chart(_fig3, use_container_width=True, key="rev_dept_bar")
        else:
            _plotly_empty()
        st.markdown("</div>", unsafe_allow_html=True)

    with co:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["red"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("🔴 미수금 현황", "연령별 분류", C["red"])
        if overdue_stat:
            _totO = sum(int(r.get("금액", 0) or 0) for r in overdue_stat)
            st.markdown(
                f'<div style="background:{C["red_l"]};border:1px solid #FECDD3;border-radius:8px;padding:10px 14px;margin-bottom:10px;">'
                f'<div style="font-size:10px;font-weight:700;color:#991B1B;text-transform:uppercase;letter-spacing:.1em;">미수금 총액</div>'
                f'<div style="font-size:28px;font-weight:800;color:{C["red"]};">{_fmt_won(_totO)}</div></div>',
                unsafe_allow_html=True,
            )
            _OC = {
                "30일미만": (C["green"], C["green_l"]),
                "30~60일": (C["yellow"], C["yellow_l"]),
                "60~90일": (C["orange"], C["orange_l"]),
                "90일이상": (C["red"], C["red_l"]),
            }
            _maxO = max((int(r.get("금액", 0) or 0) for r in overdue_stat), default=1)
            for r in overdue_stat:
                _age = r.get("연령구분", "")
                _amt = int(r.get("금액", 0) or 0)
                _cnt = int(r.get("건수", 0) or 0)
                _pctO = round(_amt / _maxO * 100) if _maxO > 0 else 0
                _oc, _obg = _OC.get(_age, (C["t3"], "#F8FAFC"))
                st.markdown(
                    f'<div class="overdue-row"><span class="overdue-label" style="color:{_oc};">{_age}</span>'
                    f'<div class="overdue-bar-wrap"><div class="overdue-bar" style="width:{_pctO}%;background:{_oc};"></div></div>'
                    f'<span class="overdue-val" style="color:{_oc};">{_fmt_won(_amt)}</span>'
                    f'<span style="font-size:10px;color:{C["t4"]};width:30px;text-align:right;">{_cnt}건</span></div>',
                    unsafe_allow_html=True,
                )
            if HAS_PLOTLY:
                _olabels = [r.get("연령구분", "") for r in overdue_stat]
                _ovalues = [int(r.get("금액", 0) or 0) for r in overdue_stat]
                _oclr = [_OC.get(l, (C["t3"], ""))[0] for l in _olabels]
                _figO = go.Figure(
                    go.Pie(
                        labels=_olabels,
                        values=_ovalues,
                        marker=dict(colors=_oclr, line=dict(color="#fff", width=2)),
                        hole=0.55,
                        textinfo="percent",
                        textfont=dict(size=11),
                        hovertemplate="<b>%{label}</b><br>%{value:,}원<br>%{percent}<extra></extra>",
                    )
                )
                _figO.update_layout(
                    height=180,
                    margin=dict(l=0, r=0, t=8, b=0),
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#333", size=11),
                    legend=dict(
                        orientation="h",
                        y=-0.1,
                        x=0.5,
                        xanchor="center",
                        font=dict(size=10),
                        bgcolor="rgba(0,0,0,0)",
                    ),
                    annotations=[
                        dict(
                            text="<b>미수금</b>",
                            x=0.5,
                            y=0.5,
                            font=dict(size=11, color=C["red"]),
                            showarrow=False,
                        )
                    ],
                )
                st.plotly_chart(_figO, use_container_width=True, key="rev_overdue_pie")
        else:
            st.markdown(
                '<div style="padding:30px;text-align:center;color:#94A3B8;">V_OVERDUE_STAT 확인</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════
# 탭 3 — 통계·분석
# ════════════════════════════════════════════════════════════════════
def _tab_analytics(opd_dept_trend, waittime_trend, los_dist):
    # 외래 추세 라인
    st.markdown(
        '<div class="wd-card" style="border-top:3px solid ' + C["blue"] + ';">',
        unsafe_allow_html=True,
    )
    _sec_hd("📈 진료과별 외래 인원 추세 (7일)", "진료과 다중 선택", C["blue"])
    _cS, _cC = st.columns([2, 8], gap="small")
    with _cS:
        _all = [
            d
            for d in sorted(
                {r.get("진료과명", "") for r in opd_dept_trend if r.get("진료과명", "")}
            )
            if d
        ]
        _def = _all[:6] if len(_all) >= 6 else _all
        _sel = st.multiselect(
            "진료과",
            options=_all,
            default=st.session_state.get("fn_sel_depts", _def),
            key="fn_an_depts_sel",
            label_visibility="collapsed",
        )
        if _sel:
            st.session_state["fn_sel_depts"] = _sel[:10]
        for _d in (_sel or [])[:10]:
            st.markdown(
                f'<div style="background:{C["blue_l"]};color:{C["blue"]};border-radius:5px;padding:2px 8px;font-size:11px;font-weight:600;margin-top:3px;">{_d}</div>',
                unsafe_allow_html=True,
            )
    with _cC:
        _sd = _sel[:10] if _sel else _def
        if _sd and opd_dept_trend and HAS_PLOTLY:
            _dates = [
                str(r.get("기준일", ""))[:10]
                for r in sorted(opd_dept_trend, key=lambda x: str(x.get("기준일", "")))
            ]
            _dates = sorted(set(_dates))
            _figT = go.Figure()
            for i, _dept in enumerate(_sd):
                _dm = {
                    str(r.get("기준일", ""))[:10]: int(r.get("외래환자수", 0) or 0)
                    for r in opd_dept_trend
                    if r.get("진료과명", "") == _dept
                }
                _y = [_dm.get(d, 0) for d in _dates]
                _clr = _PALETTE[i % len(_PALETTE)]
                _figT.add_trace(
                    go.Scatter(
                        x=_dates,
                        y=_y,
                        name=_dept,
                        mode="lines+markers",
                        line=dict(color=_clr, width=2.5),
                        marker=dict(
                            size=6, color=_clr, line=dict(color="#fff", width=1.5)
                        ),
                        hovertemplate=f"<b>{_dept}</b><br>%{{x}}: %{{y}}명<extra></extra>",
                    )
                )
            _figT.update_layout(
                **_PLOTLY_LAYOUT,
                height=280,
                margin=dict(l=0, r=0, t=8, b=8),
                legend=dict(
                    orientation="h",
                    y=-0.18,
                    x=0.5,
                    xanchor="center",
                    font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)",
                ),
                hovermode="x unified",
                yaxis=dict(
                    gridcolor="#F1F5F9",
                    tickfont=dict(size=10),
                    title=dict(
                        text="외래 환자 수(명)", font=dict(size=10, color=C["t3"])
                    ),
                ),
            )
            st.plotly_chart(_figT, use_container_width=True, key="an_opd_trend")
        else:
            _plotly_empty()
    st.markdown("</div>", unsafe_allow_html=True)
    _gap()

    # 대기시간 추세 + 재원일수 분포
    cW, cL = st.columns([3, 2], gap="small")

    with cW:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["teal"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("⏱️ 진료과별 평균 대기시간 추세 (7일)", "단위: 분", C["teal"])
        if waittime_trend and HAS_PLOTLY:
            _wds = sorted(
                {r.get("진료과명", "") for r in waittime_trend if r.get("진료과명", "")}
            )
            _wdt = sorted({str(r.get("기준일", ""))[:10] for r in waittime_trend})
            _figW = go.Figure()
            for i, _wd in enumerate(_wds[:8]):
                _wm = {
                    str(r.get("기준일", ""))[:10]: float(r.get("평균대기시간", 0) or 0)
                    for r in waittime_trend
                    if r.get("진료과명", "") == _wd
                }
                _wy = [_wm.get(d, 0) for d in _wdt]
                _clr = _PALETTE[i % len(_PALETTE)]
                _figW.add_trace(
                    go.Scatter(
                        x=_wdt,
                        y=_wy,
                        name=_wd,
                        mode="lines+markers",
                        line=dict(color=_clr, width=2),
                        marker=dict(size=5, color=_clr),
                        hovertemplate=f"<b>{_wd}</b><br>%{{x}}: %{{y:.1f}}분<extra></extra>",
                    )
                )
            _figW.add_hline(
                y=30,
                line_dash="dot",
                line_color="#EF4444",
                opacity=0.6,
                annotation_text="혼잡 30분",
                annotation_position="bottom right",
                annotation_font=dict(size=10, color="#EF4444"),
            )
            _figW.add_hline(
                y=15,
                line_dash="dot",
                line_color="#F59E0B",
                opacity=0.5,
                annotation_text="주의 15분",
                annotation_position="bottom right",
                annotation_font=dict(size=10, color="#F59E0B"),
            )
            _figW.update_layout(
                **_PLOTLY_LAYOUT,
                height=250,
                margin=dict(l=0, r=0, t=8, b=8),
                legend=dict(
                    orientation="h",
                    y=-0.22,
                    x=0.5,
                    xanchor="center",
                    font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)",
                ),
                hovermode="x unified",
                yaxis=dict(
                    gridcolor="#F1F5F9",
                    tickfont=dict(size=10),
                    title=dict(text="대기시간(분)", font=dict(size=10, color=C["t3"])),
                ),
            )
            st.plotly_chart(_figW, use_container_width=True, key="an_wait_trend")
        else:
            st.markdown(
                '<div style="padding:30px;text-align:center;color:#94A3B8;">V_WAITTIME_TREND 확인</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with cL:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["violet"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("🛏️ 입원 재원일수 분포", "현재 재원 환자 기준", C["violet"])
        if los_dist and HAS_PLOTLY:
            _bins = [r.get("재원일수구간", "") for r in los_dist]
            _pats = [int(r.get("환자수", 0) or 0) for r in los_dist]
            _totP = sum(_pats)
            _lcol = [C["green"], C["teal"], C["blue"], C["yellow"], C["red"]]
            _figL = go.Figure(
                go.Bar(
                    x=_bins,
                    y=_pats,
                    marker=dict(
                        color=_lcol[: len(_bins)],
                        line=dict(color="#fff", width=1.5),
                        cornerradius=4,
                    ),
                    text=[
                        f"{p}명\n({round(p / _totP * 100) if _totP else 0}%)"
                        for p in _pats
                    ],
                    textposition="outside",
                    textfont=dict(size=11),
                    hovertemplate="%{x}: %{y}명<extra></extra>",
                )
            )
            if any(b in _bins for b in ("15~30일", "30일초과")):
                _figL.add_annotation(
                    x=len(_bins) - 1,
                    y=max(_pats) * 0.7,
                    text="⚠️ DRG 임계",
                    font=dict(size=10, color="#EF4444"),
                    showarrow=False,
                )
            _figL.update_layout(
                **_PLOTLY_LAYOUT,
                height=240,
                margin=dict(l=0, r=0, t=8, b=8),
                showlegend=False,
                xaxis=dict(tickfont=dict(size=11), gridcolor="rgba(0,0,0,0)"),
                yaxis=dict(
                    gridcolor="#F1F5F9",
                    tickfont=dict(size=10),
                    title=dict(text="환자 수(명)", font=dict(size=10, color=C["t3"])),
                ),
                bargap=0.25,
            )
            st.plotly_chart(_figL, use_container_width=True, key="an_los_dist")
            _long = sum(
                int(r.get("환자수", 0) or 0)
                for r in los_dist
                if r.get("재원일수구간", "") in ("15~30일", "30일초과")
            )
            if _long > 0:
                st.markdown(
                    f'<div style="background:{C["red_l"]};border:1px solid #FECDD3;border-radius:8px;padding:8px 12px;margin-top:4px;">'
                    f'<span style="font-size:12px;font-weight:700;color:{C["red"]};">⚠️ DRG 임계(15일+) {_long}명 — 퇴원 검토 필요</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div style="padding:30px;text-align:center;color:#94A3B8;">V_LOS_DIST 확인</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════
# 메인 진입점
# ════════════════════════════════════════════════════════════════════
def render_finance_dashboard() -> None:
    """원무 현황 대시보드 v2.0."""
    st.markdown(_CSS, unsafe_allow_html=True)

    oracle_ok = False
    try:
        from db.oracle_client import test_connection

        oracle_ok, _ = test_connection()
    except Exception:
        pass

    opd_kpi = (_fq("opd_kpi") or [{}])[0]
    dept_status = _fq("opd_dept_status")
    kiosk_status = _fq("kiosk_status")
    discharge_pipe = _fq("discharge_pipeline")
    opd_dept_trend = _fq("opd_dept_trend")
    bed_detail = _fq("ward_bed_detail")
    finance_today = _fq("finance_today")
    finance_trend = _fq("finance_trend")
    finance_by_dept = _fq("finance_by_dept")
    overdue_stat = _fq("overdue_stat")
    waittime_trend = _fq("waittime_trend")
    los_dist = _fq("los_dist")

    # 탑바
    st.markdown('<div class="fn-topbar"></div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([4, 2, 4], vertical_alignment="center")
    with c1:
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;">'
            f'<div style="width:3px;height:22px;background:{C["blue"]};border-radius:2px;"></div>'
            f'<div><div style="font-size:9px;font-weight:700;color:{C["t4"]};text-transform:uppercase;letter-spacing:.15em;">좋은문화병원</div>'
            f'<div style="font-size:17px;font-weight:800;color:{C["t1"]};letter-spacing:-.03em;">💼 원무 현황</div>'
            f"</div></div>",
            unsafe_allow_html=True,
        )
    with c2:
        b1, b2 = st.columns(2, gap="small")
        with b1:
            if st.button(
                "🔄 새로고침",
                key="fn_refresh",
                use_container_width=True,
                type="secondary",
            ):
                st.cache_data.clear()
                st.rerun()
        with b2:
            _auto = st.session_state.get("fn_auto", False)
            if st.button(
                "⏸ 자동갱신" if _auto else "▶ 자동갱신",
                key="fn_auto_toggle",
                use_container_width=True,
                type="secondary",
            ):
                st.session_state["fn_auto"] = not _auto
                st.rerun()
    with c3:
        _oc = "#16A34A" if oracle_ok else "#F59E0B"
        st.markdown(
            f'<div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;padding:8px 0;">'
            f'<span style="width:8px;height:8px;border-radius:50%;background:{_oc};display:inline-block;"></span>'
            f'<span style="font-size:12px;font-weight:700;color:{_oc};">{"Oracle 연결 정상" if oracle_ok else "Oracle 미연결"}</span>'
            f'<span style="font-size:11px;color:#CBD5E1;">|</span>'
            f'<span style="font-size:11px;color:{C["t3"]};font-family:Consolas,monospace;">갱신 {time.strftime("%Y-%m-%d %H:%M")}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        '<div style="height:1px;background:#F1F5F9;margin:0 0 6px;"></div>',
        unsafe_allow_html=True,
    )

    if not oracle_ok:
        _ms = [
            "V_OPD_DEPT_STATUS",
            "V_KIOSK_STATUS",
            "V_DISCHARGE_PIPELINE",
            "V_FINANCE_TODAY",
            "V_FINANCE_TREND",
            "V_FINANCE_BY_DEPT",
            "V_OVERDUE_STAT",
            "V_WAITTIME_TREND",
            "V_LOS_DIST",
        ]
        st.markdown(
            f'<div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:8px 14px;margin-bottom:8px;">'
            f'<b style="font-size:13px;color:#92400E;">⚠️ Oracle 미연결 — 아래 VIEW 생성 필요</b>'
            f'<div style="font-size:11px;color:#B45309;margin-top:3px;">{" / ".join(_ms)}</div></div>',
            unsafe_allow_html=True,
        )

    # 3탭
    t1, t2, t3 = st.tabs(["🏥 실시간 현황", "💰 수납·미수금", "📊 통계·분석"])
    with t1:
        _tab_realtime(opd_kpi, dept_status, kiosk_status, discharge_pipe, bed_detail)
    with t2:
        _tab_revenue(finance_today, finance_trend, finance_by_dept, overdue_stat)
    with t3:
        _tab_analytics(opd_dept_trend, waittime_trend, los_dist)

    # ── 자동갱신: st_autorefresh 없을 때 meta HTTP-refresh 사용 ──
    # time.sleep(300) 은 Streamlit 메인 스레드를 300초 블로킹 → 금지
    if st.session_state.get("fn_auto", False):
        try:
            from streamlit_autorefresh import st_autorefresh

            st_autorefresh(interval=300_000, key="fn_autorefresh")  # 5분
        except ImportError:
            st.markdown(
                '<meta http-equiv="refresh" content="300">',
                unsafe_allow_html=True,
            )
