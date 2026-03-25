"""
ui/nursing_dashboard.py  ─  간호 현황 대시보드 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[간호팀 특화 기능]

■ Row 1 — 병동별 KPI 카드 (4개)
    · 총 재원환자 / 고위험 합계 / 금일 낙상 건수 / 퇴원예정

■ Row 2 — 고위험 환자 현황 (3컬럼)
    · 낙상 고위험  ← V_WARD_HIGH_RISK (낙상위험)
    · 욕창 고위험  ← V_WARD_HIGH_RISK (욕창위험)
    · 당뇨 고위험  ← V_WARD_HIGH_RISK (당뇨위험)

■ Row 3 — 인수인계 AI 요약 (Gemini)
    · 병동 선택 → 자동 요약 생성
    · "오늘 우리 병동 현황 1문장으로 요약"
    · 채팅으로 추가 질문 가능

■ Row 4 — 병동별 입원 현황 테이블
    · V_WARD_BED_DETAIL 기반
    · 고위험 환자 수 컬럼 추가

■ Oracle VIEW 필요 목록
    V_WARD_HIGH_RISK   병동별 고위험 환자 (낙상/욕창/당뇨)
    V_WARD_INCIDENT    낙상 사고 금일 발생 건수
    V_WARD_BED_DETAIL  병동별 당일 현황 (기존 재사용)
    V_ADMIT_CANDIDATES 입원 예약 환자 (기존 재사용)
    V_WARD_ROOM_DETAIL 병실 상세 현황 (기존 재사용)
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

import streamlit as st

try:
    import plotly.graph_objects as go

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

import sys, os as _os

_PROJECT_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from utils.logger import get_logger as _get_logger
    from config.settings import settings as _settings

    logger = _get_logger(__name__, log_dir=_settings.log_dir)
except Exception:
    import logging as _logging

    logger = _logging.getLogger(__name__)

# ── Oracle VIEW 쿼리 ─────────────────────────────────────────────────
NURSING_QUERIES: Dict[str, str] = {
    # ─ 병동별 고위험 환자 현황 ────────────────────────────────────────
    # VIEW: V_WARD_HIGH_RISK
    # 반환: 병동명, 낙상고위험, 욕창고위험, 당뇨고위험, 합계
    # SQL 힌트 (DBeaver에서 생성):
    #   SELECT
    #     w.병동명,
    #     COUNT(CASE WHEN p.낙상위험등급 IN ('고위험','중위험') THEN 1 END) AS 낙상고위험,
    #     COUNT(CASE WHEN p.욕창위험등급 IN ('고위험','중위험') THEN 1 END) AS 욕창고위험,
    #     COUNT(CASE WHEN p.당뇨여부 = 'Y' THEN 1 END) AS 당뇨고위험,
    #     COUNT(*) AS 재원합계
    #   FROM OMTIDN02 i JOIN OMTBED01 b ON i.OMT02BEDCD = b.OMT01BEDCD
    #   JOIN JAIN_WM.병동마스터 w ON b.병동코드 = w.병동코드
    #   JOIN CDTBPTINFO p ON i.OMT02IDNO = p.PTNO
    #   WHERE i.OMT02STATUS = 'HP'
    #   GROUP BY w.병동명
    "nursing_high_risk": "SELECT * FROM JAIN_WM.V_WARD_HIGH_RISK ORDER BY 합계 DESC",
    # ─ 낙상 사고 발생 현황 (금일) ─────────────────────────────────────
    # VIEW: V_WARD_INCIDENT
    # 반환: 발생일시, 병동명, 병실번호, 사고유형, 중증도, 조치내용
    "nursing_incident": "SELECT * FROM JAIN_WM.V_WARD_INCIDENT ORDER BY 발생일시 DESC",
    # ─ 병동별 당일 현황 (기존 VIEW 재사용) ───────────────────────────
    "ward_bed_detail": "SELECT * FROM JAIN_WM.V_WARD_BED_DETAIL ORDER BY 병동명",
    # ─ 입원 예약 환자 (기존 VIEW 재사용) ────────────────────────────
    "admit_candidates": "SELECT * FROM JAIN_WM.V_ADMIT_CANDIDATES ORDER BY 진료과명, 성별",
    # ─ 병실 상세 현황 (기존 VIEW 재사용) ────────────────────────────
    "ward_room_detail": "SELECT * FROM JAIN_WM.V_WARD_ROOM_DETAIL ORDER BY 병동명, 병실번호",
}


def _nq(key: str) -> List[Dict[str, Any]]:
    """간호 대시보드 전용 쿼리 함수."""
    try:
        from db.oracle_client import execute_query

        rows = execute_query(NURSING_QUERIES[key])
        return rows if rows else []
    except Exception as e:
        logger.warning(f"[Nursing] 쿼리 실패 ({key}): {e}")
        return []


# ── CSS ──────────────────────────────────────────────────────────────
_NURSING_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/variable/pretendardvariable.css');

/* 기본 폰트 */
.main, [data-testid="stAppViewContainer"], [data-testid="stMarkdownContainer"] {
  font-family: 'Pretendard Variable', 'Malgun Gothic', sans-serif !important;
  font-size: 14px !important;
}

/* 여백 최소화 */
[data-testid="stAppViewContainer"] > .main {
  padding-top: 0.4rem !important;
  padding-left: 0.75rem !important;
  padding-right: 0.75rem !important;
}
[data-testid="stVerticalBlock"] { gap: 0.45rem !important; }
.element-container { margin-bottom: 0 !important; }

/* ── 간호 KPI 카드 ── */
.nr-kpi {
  background: #FFFFFF;
  border: 1px solid #F0F4F8;
  border-radius: 12px;
  padding: 14px 16px;
  min-height: 130px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  box-shadow: 0 3px 10px rgba(0,0,0,0.06);
}
.nr-kpi-label {
  font-size: 10px; font-weight: 700; color: #64748B;
  text-transform: uppercase; letter-spacing: .12em;
}
.nr-kpi-value {
  font-size: 36px; font-weight: 800; line-height: 1;
  font-variant-numeric: tabular-nums; letter-spacing: -0.03em;
}
.nr-kpi-sub { font-size: 11px; color: #94A3B8; margin-top: 4px; }

/* ── 고위험 환자 카드 ── */
.nr-risk-card {
  background: #FFFFFF;
  border: 1px solid #F0F4F8;
  border-radius: 12px;
  padding: 14px 16px;
  box-shadow: 0 3px 10px rgba(0,0,0,0.06);
}
.nr-risk-header {
  font-size: 12px; font-weight: 700; color: #0F172A;
  margin-bottom: 10px; padding-bottom: 8px;
  border-bottom: 1px solid #F1F5F9;
  display: flex; align-items: center; gap: 6px;
}
.nr-risk-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 0; border-bottom: 1px solid #F8FAFC; font-size: 13px;
}
.nr-risk-ward { color: #334155; font-weight: 600; }
.nr-risk-count {
  font-weight: 800; font-size: 15px;
  font-family: Consolas, monospace;
}

/* ── 상태 뱃지 ── */
.badge-fall   { background:#FEE2E2; color:#991B1B; border-radius:5px; padding:2px 8px; font-size:11px; font-weight:700; }
.badge-sore   { background:#FEF3C7; color:#92400E; border-radius:5px; padding:2px 8px; font-size:11px; font-weight:700; }
.badge-dm     { background:#EDE9FE; color:#5B21B6; border-radius:5px; padding:2px 8px; font-size:11px; font-weight:700; }
.badge-ok     { background:#DCFCE7; color:#15803D; border-radius:5px; padding:2px 8px; font-size:11px; font-weight:700; }

/* ── 일반 카드 ── */
.wd-card {
  background: #FFFFFF; border: 1px solid #F0F4F8;
  border-radius: 12px; padding: 14px 16px;
  box-shadow: 0 3px 10px rgba(0,0,0,0.06);
}
.wd-sec {
  font-size: 13px; font-weight: 700; color: #0F172A;
  margin-bottom: 10px; padding-bottom: 8px;
  border-bottom: 1px solid #F1F5F9;
  display: flex; align-items: center; gap: 7px;
}
.wd-sec-accent { width:3px; height:15px; border-radius:2px; background:#0891B2; flex-shrink:0; }
.wd-sec-sub { font-size:11px; color:#94A3B8; font-weight:400; margin-left:4px; }

/* ── 탑바 ── */
.nr-topbar-accent {
  height: 3px;
  background: linear-gradient(90deg, #0891B2 0%, #06B6D4 55%, #E2E8F0 100%);
  border-radius: 2px 2px 0 0;
}

/* ── 인수인계 버튼 ── */
button[kind="primary"] {
  background: #0891B2 !important;
  border-color: #0891B2 !important;
  font-size: 13px !important;
}
button[kind="primary"]:hover { background: #0E7490 !important; }
button[kind="secondary"] {
  font-size: 13px !important; height: 34px !important;
  border-radius: 8px !important;
}

/* ── 선택기 ── */
[data-testid="stSelectbox"] > div > div {
  height: 34px !important; border-radius: 8px !important;
  border: 1.5px solid #A5F3FC !important;
  background: #ECFEFF !important;
  font-size: 13px !important; font-weight: 600 !important; color: #0E7490 !important;
}
[data-testid="stSelectbox"] label { display: none !important; }

/* ── 인수인계 요약 박스 ── */
.handover-box {
  background: linear-gradient(135deg, #F0FDFF 0%, #ECFEFF 100%);
  border: 1.5px solid #A5F3FC;
  border-left: 4px solid #0891B2;
  border-radius: 10px;
  padding: 16px 20px;
  font-size: 14px;
  color: #0C4A6E;
  line-height: 1.75;
  white-space: pre-wrap;
}
.handover-time {
  font-size: 10px; color: #94A3B8; margin-top: 8px; text-align: right;
}
</style>
"""

# ── 색상 팔레트 ──────────────────────────────────────────────────────
NC = {
    "teal": "#0891B2",
    "teal_dark": "#0E7490",
    "teal_light": "#ECFEFF",
    "fall": "#EF4444",
    "fall_bg": "#FEE2E2",
    "sore": "#F59E0B",
    "sore_bg": "#FEF3C7",
    "dm": "#8B5CF6",
    "dm_bg": "#EDE9FE",
    "ok": "#059669",
    "ok_bg": "#DCFCE7",
    "text1": "#0F172A",
    "text2": "#334155",
    "text3": "#64748B",
    "text4": "#94A3B8",
}


# ════════════════════════════════════════════════════════════════════
# 메인 렌더 함수
# ════════════════════════════════════════════════════════════════════


def render_nursing_dashboard() -> None:
    """간호 현황 대시보드 메인 렌더러."""

    st.markdown(_NURSING_CSS, unsafe_allow_html=True)

    # ── Oracle 연결 확인 ──────────────────────────────────────────
    oracle_ok = False
    try:
        from db.oracle_client import test_connection

        oracle_ok, _ = test_connection()
    except Exception as e:
        logger.debug(f"[Nursing] 오류 무시: {e}")

    # ── 데이터 조회 ────────────────────────────────────────────────
    high_risk = _nq("nursing_high_risk")
    incident = _nq("nursing_incident")
    bed_detail = _nq("ward_bed_detail")
    admit_cands = _nq("admit_candidates")
    room_detail = _nq("ward_room_detail")

    # ── 병동 목록 세션 동기화 ─────────────────────────────────────
    _all_wards = ["전체"] + sorted(
        {
            r.get("병동명", "")
            for r in bed_detail
            if r.get("병동명", "") not in ("", "전체")
        }
    )
    st.session_state["ward_name_list"] = _all_wards

    # ── 탑바 ─────────────────────────────────────────────────────
    st.markdown('<div class="nr-topbar-accent"></div>', unsafe_allow_html=True)

    _c_title, _c_ward, _c_btns, _c_info = st.columns(
        [3, 2, 2, 3], vertical_alignment="center"
    )

    with _c_title:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:8px;padding:6px 0;">'
            f'<div style="width:3px;height:22px;background:{NC["teal"]};border-radius:2px;"></div>'
            "<div>"
            '<div style="font-size:9px;font-weight:700;color:#94A3B8;'
            'text-transform:uppercase;letter-spacing:.15em;">좋은문화병원</div>'
            f'<div style="font-size:17px;font-weight:800;color:{NC["text1"]};'
            'letter-spacing:-0.03em;">💊 간호 현황</div>'
            "</div></div>",
            unsafe_allow_html=True,
        )

    with _c_ward:
        _cur_ward = st.session_state.get("nr_ward_selected", "전체")
        _sel = st.selectbox(
            "병동",
            options=_all_wards,
            index=_all_wards.index(_cur_ward) if _cur_ward in _all_wards else 0,
            key="nr_ward_selector",
            label_visibility="collapsed",
            help="선택 병동 기준으로 전체 데이터 필터링",
        )
        if _sel != _cur_ward:
            st.session_state["nr_ward_selected"] = _sel
            st.session_state.pop("nr_handover_done", None)
            st.rerun()

    with _c_btns:
        _b1, _b2 = st.columns(2, gap="small")
        with _b1:
            if st.button(
                "🔄 새로고침",
                key="nr_refresh",
                use_container_width=True,
                type="secondary",
            ):
                st.cache_data.clear()
                st.rerun()
        with _b2:
            if st.button(
                "📋 인수인계",
                key="nr_handover_btn",
                use_container_width=True,
                type="primary",
                help="AI가 현재 병동 현황을 인수인계 형식으로 요약합니다",
            ):
                st.session_state["nr_show_handover"] = True
                st.session_state.pop("nr_handover_done", None)
                st.rerun()

    with _c_info:
        _o_c = "#16A34A" if oracle_ok else "#F59E0B"
        _o_l = "Oracle 연결 정상" if oracle_ok else "Oracle 미연결"
        st.markdown(
            f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;padding:8px 0;">'
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'<span style="width:8px;height:8px;border-radius:50%;background:{_o_c};display:inline-block;"></span>'
            f'<span style="font-size:12px;font-weight:700;color:{_o_c};">{_o_l}</span>'
            f'<span style="font-size:11px;color:#CBD5E1;">|</span>'
            f'<span style="font-size:11px;color:#64748B;font-family:Consolas,monospace;">'
            f"갱신 {time.strftime('%Y-%m-%d %H:%M')}</span>"
            f"</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div style="height:1px;background:#F1F5F9;margin:0 0 8px;"></div>',
        unsafe_allow_html=True,
    )

    # ── 병동 필터 적용 ────────────────────────────────────────────
    _g_ward = st.session_state.get("nr_ward_selected", "전체")

    def _fw(data: List[Dict], ward: str, col: str = "병동명") -> List[Dict]:
        if ward == "전체":
            return data
        return [r for r in data if r.get(col, "") == ward]

    bed_f = _fw(bed_detail, _g_ward)
    high_risk_f = _fw(high_risk, _g_ward)
    incident_f = _fw(incident, _g_ward)

    # ── Oracle 미연결 배너 ─────────────────────────────────────────
    if not oracle_ok:
        st.markdown(
            '<div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;'
            'padding:8px 14px;margin-bottom:8px;display:flex;align-items:center;gap:8px;">'
            '<span style="font-size:16px;">⚠️</span>'
            "<div>"
            '<b style="font-size:13px;color:#92400E;">Oracle 미연결 — 실제 데이터 없음</b>'
            '<div style="font-size:12px;color:#B45309;margin-top:2px;">'
            "Oracle 연결 후 새로고침하세요. VIEW 목록: V_WARD_HIGH_RISK, V_WARD_INCIDENT, V_WARD_BED_DETAIL"
            "</div></div></div>",
            unsafe_allow_html=True,
        )

    # ════════════════════════════════════════════════════════════════
    # [Row 1] KPI 카드 4개
    # ════════════════════════════════════════════════════════════════
    _total_stay = sum(int(r.get("재원수", 0) or 0) for r in bed_f)
    _total_beds = sum(int(r.get("총병상", 0) or 0) for r in bed_f)
    _occ_rate = round(_total_stay / max(_total_beds, 1) * 100, 1)

    # 고위험 집계
    _fall_total = sum(int(r.get("낙상고위험", 0) or 0) for r in high_risk_f)
    _sore_total = sum(int(r.get("욕창고위험", 0) or 0) for r in high_risk_f)
    _dm_total = sum(int(r.get("당뇨고위험", 0) or 0) for r in high_risk_f)
    _risk_total = _fall_total + _sore_total + _dm_total

    # 금일 낙상 사고
    _incident_cnt = len(incident_f)

    # 퇴원예정
    _dc_planned = sum(int(r.get("익일퇴원예고", 0) or 0) for r in bed_f)

    _occ_color = (
        "#EF4444" if _occ_rate >= 90 else "#F59E0B" if _occ_rate >= 80 else "#059669"
    )
    _risk_color = (
        "#EF4444" if _risk_total > 10 else "#F59E0B" if _risk_total > 5 else "#059669"
    )
    _inc_color = "#EF4444" if _incident_cnt > 0 else "#059669"

    kc1, kc2, kc3, kc4 = st.columns(4, gap="small")

    def _nr_kpi(col, label, value, unit, sub, color, icon=""):
        col.markdown(
            f'<div class="nr-kpi">'
            f'<div class="nr-kpi-label">{icon} {label}</div>'
            f'<div class="nr-kpi-value" style="color:{color};">'
            f'{value}<span style="font-size:16px;color:#64748B;font-weight:500;margin-left:3px;">{unit}</span>'
            f"</div>"
            f'<div class="nr-kpi-sub">{sub}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )

    _nr_kpi(
        kc1,
        "재원 환자",
        str(_total_stay),
        "명",
        f"총병상 {_total_beds}개 · 가동률 {_occ_rate}%",
        _occ_color,
        "🏥",
    )
    _nr_kpi(
        kc2,
        "고위험 합계",
        str(_risk_total),
        "명",
        f"낙상 {_fall_total} · 욕창 {_sore_total} · 당뇨 {_dm_total}",
        _risk_color,
        "🚨",
    )
    _nr_kpi(
        kc3,
        "금일 낙상 사고",
        str(_incident_cnt),
        "건",
        "금일 발생 낙상 사고 보고 기준",
        _inc_color,
        "⚠️",
    )
    _nr_kpi(
        kc4,
        "퇴원 예정",
        str(_dc_planned),
        "명",
        "퇴원예고(DC) 처리 대상",
        NC["teal"],
        "📤",
    )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════
    # [인수인계 AI 요약 패널] — 인수인계 버튼 클릭 시 표시
    # ════════════════════════════════════════════════════════════════
    if st.session_state.get("nr_show_handover", False):
        _render_handover_panel(
            ward=_g_ward,
            bed_f=bed_f,
            high_risk_f=high_risk_f,
            incident_f=incident_f,
            admit_cands=admit_cands,
            total_stay=_total_stay,
            fall_total=_fall_total,
            sore_total=_sore_total,
            dm_total=_dm_total,
            incident_cnt=_incident_cnt,
            dc_planned=_dc_planned,
        )
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════
    # [Row 2] 고위험 환자 현황 (낙상 | 욕창 | 당뇨)
    # ════════════════════════════════════════════════════════════════
    rc1, rc2, rc3 = st.columns(3, gap="small")

    _risk_cols = [
        (
            rc1,
            "🚨 낙상 고위험",
            "낙상고위험",
            NC["fall"],
            NC["fall_bg"],
            "badge-fall",
            "낙상",
        ),
        (
            rc2,
            "🩹 욕창 고위험",
            "욕창고위험",
            NC["sore"],
            NC["sore_bg"],
            "badge-sore",
            "욕창",
        ),
        (
            rc3,
            "💉 당뇨 고위험",
            "당뇨고위험",
            NC["dm"],
            NC["dm_bg"],
            "badge-dm",
            "당뇨",
        ),
    ]

    for _col, _title, _key, _clr, _bg, _badge_cls, _label in _risk_cols:
        with _col:
            _sorted = sorted(high_risk_f, key=lambda r: -int(r.get(_key, 0) or 0))
            _total_k = sum(int(r.get(_key, 0) or 0) for r in _sorted)

            # 차트 데이터
            _wards = [
                r.get("병동명", "") for r in _sorted[:8] if int(r.get(_key, 0) or 0) > 0
            ]
            _values = [
                int(r.get(_key, 0) or 0)
                for r in _sorted[:8]
                if int(r.get(_key, 0) or 0) > 0
            ]

            st.markdown(
                f'<div class="nr-risk-card">'
                f'<div class="nr-risk-header">'
                f'<span style="width:3px;height:15px;background:{_clr};border-radius:2px;"></span>'
                f"{_title}"
                f'<span style="background:{_bg};color:{_clr};border-radius:5px;'
                f'padding:2px 8px;font-size:11px;font-weight:800;margin-left:auto;">'
                f"합계 {_total_k}명</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            if _wards and HAS_PLOTLY:
                _fig = go.Figure(
                    go.Bar(
                        x=_values,
                        y=_wards,
                        orientation="h",
                        marker_color=_clr,
                        marker=dict(line=dict(width=0)),
                        text=_values,
                        textposition="outside",
                        textfont=dict(size=11, color=_clr),
                        hovertemplate=f"%{{y}}: %{{x}}명<extra></extra>",
                    )
                )
                _fig.update_layout(
                    height=max(140, len(_wards) * 32),
                    margin=dict(l=0, r=30, t=4, b=4),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#333333", size=11),
                    xaxis=dict(
                        gridcolor="#F1F5F9", tickfont=dict(size=10), zeroline=False
                    ),
                    yaxis=dict(tickfont=dict(size=11), gridcolor="rgba(0,0,0,0)"),
                    bargap=0.35,
                )
                st.plotly_chart(
                    _fig, use_container_width=True, key=f"nr_risk_bar_{_key}"
                )
            elif not _wards:
                st.markdown(
                    f'<div style="padding:24px;text-align:center;color:{NC["text4"]};">'
                    f'<div style="font-size:22px;margin-bottom:6px;">✅</div>'
                    f'<div style="font-size:12px;font-weight:600;">해당 없음</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

            # 병동별 수치 테이블
            _tbl = '<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:4px;">'
            for _r in _sorted[:6]:
                _v = int(_r.get(_key, 0) or 0)
                if _v == 0:
                    continue
                _pct = round(
                    _v / max(int(_r.get("재원합계", _total_stay) or 1), 1) * 100
                )
                _wn = _r.get("병동명", "")
                _tbl += (
                    f'<tr style="border-bottom:1px solid #F8FAFC;">'
                    f'<td style="padding:4px 6px;font-weight:600;color:{NC["text2"]};">{_wn}</td>'
                    f'<td style="padding:4px 6px;text-align:right;font-weight:800;'
                    f'color:{_clr};font-family:Consolas,monospace;">{_v}명</td>'
                    f'<td style="padding:4px 6px;">'
                    f'<div style="display:flex;align-items:center;gap:4px;">'
                    f'<div style="flex:1;height:5px;background:#F1F5F9;border-radius:3px;">'
                    f'<div style="width:{min(_pct, 100)}%;height:100%;background:{_clr};border-radius:3px;"></div>'
                    f"</div>"
                    f'<span style="font-size:10px;color:{NC["text4"]};">{_pct}%</span>'
                    f"</div></td></tr>"
                )
            if not _sorted or _total_k == 0:
                _tbl += (
                    f'<tr><td colspan="3" style="padding:8px;text-align:center;'
                    f'color:{NC["text4"]};font-size:11px;">'
                    f"Oracle 연결 후 데이터 표시</td></tr>"
                )
            _tbl += "</table>"
            st.markdown(_tbl + "</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════
    # [Row 3] 낙상 사고 현황 | 입원 예약 현황
    # ════════════════════════════════════════════════════════════════
    ic1, ic2 = st.columns([1, 1], gap="small")

    # ── 좌: 금일 낙상 사고 현황 ──────────────────────────────────
    with ic1:
        st.markdown(
            '<div class="wd-card">'
            '<div class="wd-sec">'
            '<span class="wd-sec-accent" style="background:#EF4444;"></span>'
            "⚠️ 금일 낙상 사고 현황"
            f'<span class="wd-sec-sub">{time.strftime("%Y-%m-%d")} 기준</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        if incident_f:
            _th = (
                "padding:8px 10px;font-size:10.5px;font-weight:700;"
                "text-transform:uppercase;letter-spacing:.06em;"
                "color:#64748B;border-bottom:1.5px solid #E2E8F0;background:#F8FAFC;"
            )
            _tbl = (
                '<table style="width:100%;border-collapse:collapse;font-size:12.5px;">'
                "<thead><tr>"
                f'<th style="{_th}text-align:left;">발생 시각</th>'
                f'<th style="{_th}text-align:left;">병동/병실</th>'
                f'<th style="{_th}text-align:center;">사고 유형</th>'
                f'<th style="{_th}text-align:center;">중증도</th>'
                "</tr></thead><tbody>"
            )
            for _inc in incident_f[:10]:
                _dt = str(_inc.get("발생일시", ""))[:16]
                _ward = _inc.get("병동명", "")
                _room = _inc.get("병실번호", "")
                _type = _inc.get("사고유형", "낙상")
                _sev = _inc.get("중증도", "경증")
                _sev_c = (
                    "#EF4444"
                    if _sev in ("중증", "심각")
                    else "#F59E0B"
                    if _sev == "중등도"
                    else "#059669"
                )
                _sev_bg = (
                    "#FEE2E2"
                    if _sev in ("중증", "심각")
                    else "#FEF3C7"
                    if _sev == "중등도"
                    else "#DCFCE7"
                )
                _tbl += (
                    f'<tr style="border-bottom:1px solid #F8FAFC;">'
                    f'<td style="padding:7px 10px;color:#334155;font-family:Consolas,monospace;">{_dt}</td>'
                    f'<td style="padding:7px 10px;font-weight:600;color:#0F172A;">{_ward} {_room}</td>'
                    f'<td style="padding:7px 10px;text-align:center;">'
                    f'<span style="background:#FEE2E2;color:#991B1B;border-radius:5px;'
                    f'padding:2px 8px;font-size:11px;font-weight:700;">{_type}</span></td>'
                    f'<td style="padding:7px 10px;text-align:center;">'
                    f'<span style="background:{_sev_bg};color:{_sev_c};border-radius:5px;'
                    f'padding:2px 8px;font-size:11px;font-weight:700;">{_sev}</span></td>'
                    f"</tr>"
                )
            _tbl += "</tbody></table>"
            st.markdown(_tbl + "</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="padding:28px;text-align:center;color:{NC["text4"]};">'
                f'<div style="font-size:28px;margin-bottom:8px;">✅</div>'
                f'<div style="font-size:13px;font-weight:700;color:#059669;">금일 낙상 사고 없음</div>'
                f'<div style="font-size:11px;margin-top:4px;">{"Oracle 연결 후 표시" if not oracle_ok else "현재 보고된 낙상 사고가 없습니다"}</div>'
                f"</div></div>",
                unsafe_allow_html=True,
            )

    # ── 우: 금일 입원 예약 현황 ───────────────────────────────────
    with ic2:
        st.markdown(
            '<div class="wd-card">'
            '<div class="wd-sec">'
            '<span class="wd-sec-accent"></span>'
            "📋 금일 입원 예약 현황"
            f'<span class="wd-sec-sub">{len(admit_cands)}명 예약</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        if admit_cands:
            _th2 = (
                "padding:8px 10px;font-size:10.5px;font-weight:700;"
                "text-transform:uppercase;letter-spacing:.06em;"
                "color:#64748B;border-bottom:1.5px solid #E2E8F0;background:#F8FAFC;"
            )
            _tbl2 = (
                '<table style="width:100%;border-collapse:collapse;font-size:12.5px;">'
                "<thead><tr>"
                f'<th style="{_th2}text-align:left;">진료과</th>'
                f'<th style="{_th2}text-align:center;">성별</th>'
                f'<th style="{_th2}text-align:center;">나이</th>'
                f'<th style="{_th2}text-align:center;">수속 상태</th>'
                "</tr></thead><tbody>"
            )
            for i, _ac in enumerate(admit_cands[:12]):
                _dept = _ac.get("진료과명", "")
                _sex = _ac.get("성별", "")
                _age = _ac.get("나이", "")
                _stat = _ac.get("수속상태", "")
                _bg2 = "#F8FAFC" if i % 2 == 0 else "#FFFFFF"
                _sc = "#1D4ED8" if _sex in ("M", "남") else "#BE185D"
                _ss = _sex if _sex else "─"
                _stat_html = (
                    '<span style="background:#DCFCE7;color:#15803D;border-radius:5px;'
                    'padding:2px 7px;font-size:10px;font-weight:700;">✅완료</span>'
                    if _stat == "AD"
                    else '<span style="background:#FEF3C7;color:#92400E;border-radius:5px;'
                    'padding:2px 7px;font-size:10px;font-weight:700;">⏳대기</span>'
                )
                _tbl2 += (
                    f'<tr style="background:{_bg2};border-bottom:1px solid #F8FAFC;">'
                    f'<td style="padding:7px 10px;font-weight:600;color:#0F172A;">{_dept}</td>'
                    f'<td style="padding:7px 10px;text-align:center;font-weight:700;color:{_sc};">{_ss}</td>'
                    f'<td style="padding:7px 10px;text-align:center;color:#334155;font-family:Consolas,monospace;">{_age}세</td>'
                    f'<td style="padding:7px 10px;text-align:center;">{_stat_html}</td>'
                    f"</tr>"
                )
            if len(admit_cands) > 12:
                _tbl2 += (
                    f'<tr><td colspan="4" style="padding:6px;text-align:center;'
                    f'font-size:11px;color:{NC["text4"]};">외 {len(admit_cands) - 12}명</td></tr>'
                )
            _tbl2 += "</tbody></table>"
            st.markdown(_tbl2 + "</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="padding:28px;text-align:center;color:{NC["text4"]};">'
                f'<div style="font-size:24px;margin-bottom:8px;">📋</div>'
                f'<div style="font-size:13px;font-weight:600;">예약 환자 데이터 없음</div>'
                f'<div style="font-size:11px;margin-top:4px;">{"Oracle 연결 후 표시" if not oracle_ok else "금일 입원 예약이 없습니다"}</div>'
                f"</div></div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════
    # [Row 4] 병동별 당일 현황 테이블 (고위험 컬럼 추가)
    # ════════════════════════════════════════════════════════════════
    # 고위험 사전 매핑
    _risk_map = {r.get("병동명", ""): r for r in high_risk}

    _tH = (
        "padding:8px 12px;font-size:11px;font-weight:700;"
        "text-transform:uppercase;letter-spacing:.07em;"
        "color:#64748B;border-bottom:1.5px solid #E2E8F0;"
        "background:#F8FAFC;white-space:nowrap;"
    )
    _th_ward = (
        f'<th style="{_tH}text-align:left;">병동</th>'
        f'<th style="{_tH}text-align:right;">재원</th>'
        f'<th style="{_tH}text-align:right;">총병상</th>'
        f'<th style="{_tH}text-align:right;">가동률</th>'
        f'<th style="{_tH}text-align:right;">입원</th>'
        f'<th style="{_tH}text-align:right;">퇴원</th>'
        f'<th style="{_tH}text-align:right;color:#EF4444;">낙상↑</th>'
        f'<th style="{_tH}text-align:right;color:#F59E0B;">욕창↑</th>'
        f'<th style="{_tH}text-align:right;color:#8B5CF6;">당뇨↑</th>'
        f'<th style="{_tH}text-align:right;color:#059669;">익일가용</th>'
    )
    _rows_ward = ""
    if bed_f:
        for i, r in enumerate(bed_f):
            _wn = r.get("병동명", "")
            _stay = int(r.get("재원수", 0) or 0)
            _tot = int(r.get("총병상", 0) or 0)
            _rate = float(r.get("가동률", 0) or 0)
            _adm = int(r.get("금일입원", 0) or 0)
            _disc = int(r.get("금일퇴원", 0) or 0)
            _ndc = int(r.get("익일퇴원예고", 0) or 0)
            _navail = max(0, (_tot - _stay) + _ndc)

            _rsk = _risk_map.get(_wn, {})
            _fall = int(_rsk.get("낙상고위험", 0) or 0)
            _sore = int(_rsk.get("욕창고위험", 0) or 0)
            _dm = int(_rsk.get("당뇨고위험", 0) or 0)

            _rate_c = (
                "#EF4444" if _rate >= 90 else "#F59E0B" if _rate >= 80 else "#059669"
            )
            _fall_c = (
                f"color:#EF4444;font-weight:800;" if _fall > 0 else f"color:#CBD5E1;"
            )
            _sore_c = (
                f"color:#F59E0B;font-weight:800;" if _sore > 0 else f"color:#CBD5E1;"
            )
            _dm_c = f"color:#8B5CF6;font-weight:800;" if _dm > 0 else f"color:#CBD5E1;"
            _bg = "#F8FAFC" if i % 2 == 0 else "#FFFFFF"
            _td = f"padding:8px 12px;background:{_bg};border-bottom:1px solid #F8FAFC;"

            _rows_ward += (
                f"<tr>"
                f'<td style="{_td}font-weight:700;color:#0F172A;">{_wn}</td>'
                f'<td style="{_td}text-align:right;font-weight:700;color:#0F172A;font-family:Consolas,monospace;">{_stay}</td>'
                f'<td style="{_td}text-align:right;color:#64748B;font-family:Consolas,monospace;">{_tot}</td>'
                f'<td style="{_td}text-align:right;font-weight:700;color:{_rate_c};font-family:Consolas,monospace;">{_rate:.1f}%</td>'
                f'<td style="{_td}text-align:right;color:#1D4ED8;font-family:Consolas,monospace;">{_adm}</td>'
                f'<td style="{_td}text-align:right;color:#475569;font-family:Consolas,monospace;">{_disc}</td>'
                f'<td style="{_td}text-align:right;{_fall_c}font-family:Consolas,monospace;">{_fall if _fall > 0 else "─"}</td>'
                f'<td style="{_td}text-align:right;{_sore_c}font-family:Consolas,monospace;">{_sore if _sore > 0 else "─"}</td>'
                f'<td style="{_td}text-align:right;{_dm_c}font-family:Consolas,monospace;">{_dm if _dm > 0 else "─"}</td>'
                f'<td style="{_td}text-align:right;font-weight:700;color:{"#059669" if _navail > 0 else "#94A3B8"};font-family:Consolas,monospace;">{_navail}</td>'
                f"</tr>"
            )
    else:
        _rows_ward = (
            '<tr><td colspan="10" style="padding:40px;text-align:center;color:#94A3B8;">'
            '<div style="font-size:13px;font-weight:600;">병동 현황 데이터 없음</div>'
            '<div style="font-size:11px;margin-top:4px;">Oracle 연결 후 표시됩니다</div>'
            "</td></tr>"
        )

    st.markdown(
        f'<div class="wd-card">'
        f'<div class="wd-sec"><span class="wd-sec-accent"></span>'
        f"병동별 당일 현황 + 고위험 현황"
        f'<span class="wd-sec-sub">낙상↑ / 욕창↑ / 당뇨↑ 컬럼 포함</span>'
        f"</div>"
        f'<div style="overflow-x:auto;">'
        f'<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        f"<thead><tr>{_th_ward}</tr></thead>"
        f"<tbody>{_rows_ward}</tbody>"
        f"</table></div></div>",
        unsafe_allow_html=True,
    )


# ════════════════════════════════════════════════════════════════════
# 인수인계 AI 요약 패널
# ════════════════════════════════════════════════════════════════════


def _render_handover_panel(
    ward: str,
    bed_f: List[Dict],
    high_risk_f: List[Dict],
    incident_f: List[Dict],
    admit_cands: List[Dict],
    total_stay: int,
    fall_total: int,
    sore_total: int,
    dm_total: int,
    incident_cnt: int,
    dc_planned: int,
) -> None:
    """
    인수인계 AI 요약 패널.

    Gemini에 현재 병동 현황을 컨텍스트로 주입하고
    인수인계 형식의 요약을 생성합니다.

    [채팅 모드]
    요약 생성 후 추가 질문도 가능합니다.
    예: "낙상 고위험 환자 중 70대 이상은?"
        "퇴원예정 환자 주의사항은?"
    """
    st.markdown(
        '<div class="wd-card" style="margin-bottom:0;">'
        '<div class="wd-sec">'
        '<span class="wd-sec-accent"></span>'
        f"📋 인수인계 AI 요약 — {ward}"
        '<span class="wd-sec-sub">Gemini 기반 자동 생성 · 추가 질문 가능</span>'
        "</div>",
        unsafe_allow_html=True,
    )

    # ── 컨텍스트 구성 (PII 없음 — 집계 통계만) ───────────────────
    _ctx = {
        "기준시각": time.strftime("%Y-%m-%d %H:%M"),
        "선택병동": ward,
        "재원현황": {
            "재원환자": total_stay,
            "낙상고위험": fall_total,
            "욕창고위험": sore_total,
            "당뇨고위험": dm_total,
            "금일낙상사고": incident_cnt,
            "퇴원예정": dc_planned,
        },
        "병동별현황": [
            {
                "병동": r.get("병동명"),
                "재원": r.get("재원수"),
                "가동률": r.get("가동률"),
                "입원": r.get("금일입원"),
                "퇴원": r.get("금일퇴원"),
            }
            for r in bed_f[:8]
        ],
        "고위험병동TOP3": [
            {
                "병동": r.get("병동명"),
                "낙상": r.get("낙상고위험"),
                "욕창": r.get("욕창고위험"),
                "당뇨": r.get("당뇨고위험"),
            }
            for r in sorted(high_risk_f, key=lambda x: -int(x.get("합계", 0) or 0))[:3]
        ],
        "낙상사고목록": [
            {
                "시각": str(r.get("발생일시", ""))[:16],
                "병동": r.get("병동명", ""),
                "중증도": r.get("중증도", ""),
            }
            for r in incident_f[:5]
        ],
        "입원예약현황": {
            "총예약": len(admit_cands),
            "수속완료": sum(1 for r in admit_cands if r.get("수속상태") == "AD"),
        },
    }

    _system = (
        "당신은 병원 간호팀 인수인계를 지원하는 AI 어시스턴트입니다.\n"
        "아래 병동 운영 통계를 바탕으로 인수인계에 필요한 핵심 정보를 간결하게 요약하세요.\n\n"
        "[작성 원칙]\n"
        "1. 교대 근무 간호사가 1분 안에 파악할 수 있도록 간결하게 작성\n"
        "2. 위험 수치(낙상·욕창 고위험, 사고 발생)는 맨 앞에 배치하고 강조\n"
        "3. 개인 환자 정보(이름·병록번호·주민번호)는 절대 언급하지 말 것\n"
        "4. 숫자는 명확히 표기 (예: 재원 32명, 낙상고위험 7명)\n"
        "5. 현재 시각과 병동명을 첫 줄에 표기\n"
        "6. 마크다운 없이 평문으로 작성\n\n"
        f"## 현재 병동 운영 데이터 (집계 통계)\n"
        f"```json\n{json.dumps(_ctx, ensure_ascii=False, indent=2)}\n```"
    )

    # ── 히스토리 초기화 ───────────────────────────────────────────
    _hist_key = f"nr_handover_hist_{ward}"
    if _hist_key not in st.session_state:
        st.session_state[_hist_key] = []

    _close_col, _ = st.columns([1, 7])
    with _close_col:
        if st.button(
            "✕ 닫기",
            key="nr_handover_close",
            type="secondary",
            use_container_width=True,
        ):
            st.session_state["nr_show_handover"] = False
            st.session_state.pop(_hist_key, None)
            st.rerun()

    # ── 자동 요약 생성 (최초 1회) ────────────────────────────────
    if not st.session_state.get("nr_handover_done"):
        with st.spinner("🤖 인수인계 요약 생성 중..."):
            _summary = ""
            try:
                from core.llm import get_llm_client

                _llm = get_llm_client()
                _req = str(uuid.uuid4())[:8]
                for _tok in _llm.generate_stream(
                    f"{ward} 병동 인수인계 요약을 작성해 주세요.",
                    _system,
                    request_id=_req,
                ):
                    _summary += _tok
            except Exception as _e:
                _summary = f"⚠️ LLM 연결 실패: {_e}\n\n직접 작성이 필요합니다."
                logger.warning(f"[Nursing Handover LLM] {_e}")

        _now = time.strftime("%Y-%m-%d %H:%M")
        st.markdown(
            f'<div class="handover-box">{_summary}</div>'
            f'<div class="handover-time">🤖 AI 생성 · {_now} · 반드시 실제 현황과 대조 후 사용하세요</div>',
            unsafe_allow_html=True,
        )
        st.session_state["nr_handover_done"] = True
        st.session_state["nr_handover_summary"] = _summary
        st.session_state[_hist_key] = [{"role": "assistant", "content": _summary}]
    else:
        # 이전 요약 표시
        _prev = st.session_state.get("nr_handover_summary", "")
        if _prev:
            _now = time.strftime("%Y-%m-%d %H:%M")
            st.markdown(
                f'<div class="handover-box">{_prev}</div>'
                f'<div class="handover-time">🤖 AI 생성 · {_now} · 반드시 실제 현황과 대조 후 사용하세요</div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:12px;font-weight:700;color:#64748B;margin-bottom:4px;">추가 질문</div>',
        unsafe_allow_html=True,
    )

    # ── 이전 채팅 표시 ────────────────────────────────────────────
    _hist = st.session_state.get(_hist_key, [])
    for _m in _hist[1:]:  # 첫번째(자동요약)는 위에서 이미 표시
        with st.chat_message(_m["role"]):
            st.markdown(_m["content"])

    # ── 추가 질문 입력 ────────────────────────────────────────────
    _q = st.chat_input(
        "인수인계 관련 추가 질문 (예: 낙상 고위험 환자 주의사항은?)",
        key="nr_handover_chat",
    )
    if _q:
        _hist.append({"role": "user", "content": _q})
        with st.chat_message("user"):
            st.markdown(_q)

        with st.chat_message("assistant"):
            _ph = st.empty()
            _full = ""
            try:
                from core.llm import get_llm_client

                _llm = get_llm_client()
                for _tok in _llm.generate_stream(
                    _q, _system, request_id=str(uuid.uuid4())[:8]
                ):
                    _full += _tok
                    _ph.markdown(_full + "▌")
            except Exception as _e:
                _full = f"⚠️ 오류: {_e}"
            _ph.markdown(_full)

        _hist.append({"role": "assistant", "content": _full})
        st.session_state[_hist_key] = _hist

    st.markdown("</div>", unsafe_allow_html=True)
