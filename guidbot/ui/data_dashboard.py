"""
ui/data_dashboard.py  ─  데이터 분석 대시보드 UI (v5.2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v5.2 버그 수정]

■ AI 요약 사라짐 수정 (핵심)
  원인 1: if _analysis: 조건으로 AI 해석 블록 감쌈
          → analyze_query_result 실패 시 AI 해석 통째로 건너뜀
  원인 2: masked_rows 가 list[tuple] 인데 explain_data 는 list[dict] 기대
          → _build_data_summary 내부 r[col] 접근 시 TypeError
          → except 에서 잡혀 경고만 출력 → AI 해석 빈 결과
  수정: _analysis 여부와 무관하게 AI 해석 항상 실행
        masked_rows → dict 변환 후 explain_data 전달

■ LLM 개인정보 차단 검증 강화
  _llm_safe_rows(): tuple → dict 변환 + PII 패턴 잔존 여부 로그
  Gemini 서버에 전달되기 직전 마지막 검증 수행

[v5.1 기능 유지]
  · 마스킹 이중 보호 (화면 + LLM)
  · 버튼 클릭 즉시 실행 (st.stop/rerun 제거)
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

try:
    import plotly.express as px

    _PLOTLY_AVAILABLE = True
except ImportError:
    _PLOTLY_AVAILABLE = False

from config.settings import settings
from ui.theme import UITheme as T
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# ──────────────────────────────────────────────────────────────────────
#  빠른 질문 예시
# ──────────────────────────────────────────────────────────────────────

_EXAMPLE_QUESTIONS: List[Dict[str, str]] = [
    {"icon": "📅", "text": "지난 1년간 월별 건강검진 수 추세 보여줘"},
    {"icon": "💰", "text": "부서별 매출 TOP 10"},
    {"icon": "🔬", "text": "최근 3년 위내시경 검사 건수 그래프"},
    {"icon": "📊", "text": "검진센터 요일별 방문자 수"},
    {"icon": "🏥", "text": "올해 월별 외래 환자 수 추이"},
    {"icon": "📈", "text": "연도별 건강검진 수검률 변화"},
]

# ──────────────────────────────────────────────────────────────────────
#  렌더 key 카운터
# ──────────────────────────────────────────────────────────────────────

_DA_RENDER_SEQ: List[int] = [0]


def _da_reset_seq() -> None:
    _DA_RENDER_SEQ[0] = 0


def _da_next_seq() -> int:
    _DA_RENDER_SEQ[0] += 1
    return _DA_RENDER_SEQ[0]


# ──────────────────────────────────────────────────────────────────────
#  PII 마스킹 헬퍼
# ──────────────────────────────────────────────────────────────────────


def _extract_table_name(sql: str) -> str:
    """SQL FROM 절에서 테이블명(대문자) 추출."""
    match = re.search(
        r"\bFROM\s+(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)",
        sql,
        re.IGNORECASE,
    )
    return match.group(1).upper() if match else ""


def _apply_masking(
    rows: List[Any],
    col_names: List[str],
    table_name: str,
) -> tuple[List[tuple], List[str], bool, List[str]]:
    """
    PII 마스킹 적용.

    마스킹 레이어:
      1. RAG_ACCESS_CONFIG.MASK_COLUMNS (set[str] — 절대 str 전달 금지)
      2. pii_masker.auto_detect (키워드 자동 감지)

    Returns: (masked_rows, masked_col_names, has_pii, masked_col_list)
    """
    # tuple 정규화
    if rows and isinstance(rows[0], dict):
        col_names = col_names or list(rows[0].keys())
        rows = [tuple(r.values()) for r in rows]

    if not col_names:
        logger.warning(f"컬럼명 불명 → 마스킹 생략 (table={table_name})")
        return rows, col_names, False, []

    # 1) RAG_ACCESS_CONFIG
    extra_mask: set[str] = set()
    try:
        from db.oracle_access_config import get_access_config_manager

        cfg = get_access_config_manager().get_config(table_name)
        if cfg and cfg.mask_columns:
            extra_mask = cfg.mask_columns  # 이미 소문자 set[str]
    except Exception as exc:
        logger.warning(f"RAG_ACCESS_CONFIG 조회 실패 → 자동 감지만: {exc}")

    # 2) pii_masker
    try:
        from db.pii_masker import mask_dataframe

        result = mask_dataframe(
            rows=rows,
            columns=col_names,
            extra_mask_cols=extra_mask,  # set[str] — str 전달 시 문자 분해 버그
            auto_detect=True,
        )
        return result.rows, result.columns, result.has_pii, result.masked_columns
    except Exception as exc:
        logger.error(f"PII 마스킹 실패 → 원본 반환: {exc}", exc_info=True)
        return rows, col_names, False, []


def _llm_safe_rows(
    masked_rows: List[tuple],
    masked_col_names: List[str],
    table_name: str = "",
) -> List[Dict[str, Any]]:
    """
    [v5.3] LLM 전달 직전 PII 컬럼 완전 제거.

    [전략 변경: v5.2 → v5.3]
    ─────────────────────────────────────────
    v5.2: 화면 마스킹값(홍**, 900101-*******) 을 LLM 에 그대로 전달
          문제: 마스킹된 값도 개인정보 / 키워드 누락 시 원본 전달

    v5.3: PII 컬럼을 LLM 컨텍스트에서 완전 제거
          화면 ─ 마스킹 표시 (홍**, 010-****-5678) ← 직원이 봄
          LLM  ─ PII 컬럼 제외 + 통계 요약만 전달
                 예) "환자명: 9명 포함 (개인정보 제외)"
    ─────────────────────────────────────────
    개인정보보호법 제24조, 의료법 제21조 준수:
    · 개인정보를 외부 AI 서버(Google Gemini)로 전송하지 않음
    · 통계/집계 정보만 AI 분석에 활용
    """
    if not masked_rows or not masked_col_names:
        return []

    # tuple → dict
    dict_rows: List[Dict[str, Any]] = [
        dict(zip(masked_col_names, r)) for r in masked_rows
    ]

    # PII 컬럼 식별 (두 소스 통합)
    pii_cols_upper: set = set()

    # 소스 1: RAG_ACCESS_CONFIG.MASK_COLUMNS (가장 정확)
    if table_name:
        try:
            from db.oracle_access_config import get_access_config_manager

            cfg = get_access_config_manager().get_config(table_name)
            if cfg and cfg.mask_columns:
                pii_cols_upper.update(c.upper() for c in cfg.mask_columns)
        except Exception:
            pass

    # 소스 2: pii_masker 키워드 자동 감지
    try:
        from db.pii_masker import detect_pii_type

        for col in masked_col_names:
            if detect_pii_type(col) is not None:
                pii_cols_upper.add(col.upper())
    except Exception:
        pass

    # PII 컬럼 제거 + LLM 전달용 dict 구성
    safe_cols = [c for c in masked_col_names if c.upper() not in pii_cols_upper]
    pii_cols_found = [c for c in masked_col_names if c.upper() in pii_cols_upper]

    safe_rows: List[Dict[str, Any]] = [{c: r[c] for c in safe_cols} for r in dict_rows]

    if pii_cols_found:
        logger.info(
            f"[LLM PII 제거] {pii_cols_found} 컬럼 제외 "
            f"({len(masked_col_names)}개 → {len(safe_cols)}개 컬럼, "
            f"{len(safe_rows)}행 LLM 전달)"
        )
    else:
        logger.debug(f"LLM 전달 안전 확인: {len(safe_rows)}행, {safe_cols}")

    return safe_rows, pii_cols_found  # (safe_rows, removed_pii_cols)


# ──────────────────────────────────────────────────────────────────────
#  CSS
# ──────────────────────────────────────────────────────────────────────


def _inject_dashboard_css() -> None:
    st.markdown(
        f"""
        <style>
        .da-section-card {{
            background: {T.CARD_BG};
            border: 1px solid {T.BORDER};
            border-radius: 10px;
            padding: 1rem 1.2rem;
            margin-bottom: 1rem;
        }}
        .da-section-header {{
            display: flex;
            align-items: center;
            gap: 0.4rem;
            font-size: 14px;
            font-weight: 700;
            color: {T.PRIMARY};
            margin-bottom: 0.6rem;
            padding-bottom: 0.4rem;
            border-bottom: 2px solid {T.PRIMARY}33;
        }}
        .da-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 0.5rem;
        }}
        .da-badge-blue   {{ background: {T.PRIMARY}22; color: {T.PRIMARY}; }}
        .da-badge-green  {{ background: #22c55e22; color: #16a34a; }}
        .da-badge-orange {{ background: #f9731622; color: #ea580c; }}
        .da-badge-red    {{ background: #ef444422; color: #dc2626; }}
        .da-ai-block {{
            background: linear-gradient(135deg, {T.PRIMARY}08, {T.PRIMARY}15);
            border-left: 3px solid {T.PRIMARY};
            border-radius: 0 8px 8px 0;
            padding: 0.8rem 1rem;
            font-size: 14px;
            line-height: 1.7;
            color: {T.TEXT};
        }}
        .da-pii-notice {{
            background: #F0FDF4;
            border: 1px solid #86EFAC;
            border-radius: 6px;
            padding: 0.4rem 0.8rem;
            font-size: 11px;
            color: #166534;
            margin-bottom: 0.5rem;
        }}
        .da-error-box {{
            background: #fef2f2;
            border: 1px solid #fca5a5;
            border-radius: 8px;
            padding: 0.8rem 1rem;
            color: #dc2626;
            font-size: 13px;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────
#  섹션 헤더
# ──────────────────────────────────────────────────────────────────────


def _section_header(
    icon: str, title: str, badge: str = "", badge_color: str = "blue"
) -> None:
    badge_html = (
        f'<span class="da-badge da-badge-{badge_color}">{badge}</span>' if badge else ""
    )
    st.markdown(
        f'<div class="da-section-header">'
        f"<span>{icon}</span><span>{title}</span>{badge_html}</div>",
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────
#  홈 화면
# ──────────────────────────────────────────────────────────────────────


def _render_home_screen() -> None:
    st.markdown(
        f"""
        <div style="text-align:center; padding: 1.5rem 0 1rem;">
            <div style="font-size:36px; margin-bottom:0.5rem;">📊</div>
            <h3 style="font-size:18px; font-weight:700; color:{T.TEXT}; margin:0 0 0.3rem;">
                데이터 분석 모드
            </h3>
            <p style="font-size:13px; color:{T.TEXT_MUTED}; margin:0;">
                병원 Oracle DB 에 자연어로 질문하세요.<br>
                SQL 자동 생성 → 데이터 조회 → 차트 → AI 해석
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p style="font-size:12px; color:{T.TEXT_MUTED}; font-weight:600; margin:0.5rem 0 0.4rem;">예시 질문</p>',
        unsafe_allow_html=True,
    )
    for row_start in range(0, len(_EXAMPLE_QUESTIONS), 2):
        cols = st.columns(2)
        for ci, col in enumerate(cols):
            qi = row_start + ci
            if qi >= len(_EXAMPLE_QUESTIONS):
                break
            q = _EXAMPLE_QUESTIONS[qi]
            with col:
                if st.button(
                    f"{q['icon']}  {q['text']}",
                    key=f"da_ex_{qi}",
                    use_container_width=True,
                ):
                    st.session_state["da_prefill"] = q["text"]
                    st.rerun()


# ──────────────────────────────────────────────────────────────────────
#  데이터 테이블
# ──────────────────────────────────────────────────────────────────────


def _render_data_table(
    rows: List[Any],
    column_names: List[str],
    query_time_ms: float,
    masked_columns: Optional[List[str]] = None,
) -> None:
    row_count = len(rows)
    _section_header(
        "📋",
        "데이터 테이블",
        badge=f"{row_count:,}행",
        badge_color="green" if row_count > 0 else "orange",
    )

    if masked_columns:
        st.markdown(
            f'<div class="da-pii-notice">'
            f"🔒 개인정보 보호: <strong>{', '.join(masked_columns)}</strong> 마스킹됨"
            f"</div>",
            unsafe_allow_html=True,
        )

    if not rows:
        st.info("조회된 데이터가 없습니다.")
        return

    df = pd.DataFrame(rows, columns=column_names)
    st.dataframe(df, use_container_width=True, height=min(400, 60 + row_count * 35))

    csv_data = df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        label="⬇️  CSV 다운로드",
        data=csv_data,
        file_name="hospital_data.csv",
        mime="text/csv",
        key=f"da_csv_{_da_next_seq()}",
    )
    st.markdown(
        f'<p style="font-size:11px; color:{T.TEXT_MUTED}; margin-top:0.2rem;">'
        f"쿼리 실행 시간: {query_time_ms:.0f}ms</p>",
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────
#  KPI 카드 렌더러  [v5.3 신규]
# ──────────────────────────────────────────────────────────────────────


def _render_kpi_cards(
    rows: List[Any],
    column_names: List[str],
    agg_label: str = "",
) -> None:
    """
    단순 집계 결과(1~3행)를 KPI 카드 형태로 표시합니다.

    [사용 예]
    · "응급환자 총 건수" → COUNT(*) = 247 → 큰 숫자 카드
    · "오늘 입원/외래/응급 환자 수" → 3개 카드 나란히
    · "평균 체류 시간, 최대 체류 시간" → 2개 카드

    [설계 원칙]
    · 1행: 컬럼 수만큼 카드 (최대 4개 1행)
    · 2~3행: 각 행을 카드 그룹으로 표시
    """
    from llm.data_explainer import CHART_KPI

    _section_header("📊", "핵심 지표", badge=agg_label or "KPI", badge_color="green")

    if not rows:
        st.info("표시할 데이터가 없습니다.")
        return

    # dict 정규화
    if isinstance(rows[0], tuple):
        dict_rows = [dict(zip(column_names, r)) for r in rows]
    else:
        dict_rows = rows

    # 1행인 경우: 컬럼별 카드
    if len(dict_rows) == 1:
        row = dict_rows[0]
        n = len(column_names)
        cols_per_row = min(n, 4)
        st_cols = st.columns(cols_per_row)

        for i, col_name in enumerate(column_names[:4]):
            val = row.get(col_name)
            display_val = _format_kpi_value(val)
            label = _prettify_col_name(col_name)

            with st_cols[i % cols_per_row]:
                st.markdown(
                    f"""
                    <div style="
                        background: linear-gradient(135deg, {T.PRIMARY}14, {T.PRIMARY}08);
                        border: 1px solid {T.PRIMARY}28;
                        border-radius: 10px;
                        padding: 1.1rem 1rem 0.8rem;
                        text-align: center;
                    ">
                        <div style="font-size:0.72rem; color:{T.TEXT_MUTED};
                                    font-weight:600; text-transform:uppercase;
                                    letter-spacing:0.06em; margin-bottom:0.35rem;">
                            {label}
                        </div>
                        <div style="font-size:1.85rem; font-weight:800;
                                    color:{T.PRIMARY}; line-height:1.1;">
                            {display_val}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        return

    # 2~3행: 행별로 테이블 형태 + 수치 강조
    for row_dict in dict_rows[:3]:
        cols_ui = st.columns(min(len(column_names), 4))
        for i, col_name in enumerate(column_names[:4]):
            val = row_dict.get(col_name)
            display_val = _format_kpi_value(val)
            label = _prettify_col_name(col_name)
            with cols_ui[i % 4]:
                st.metric(label=label, value=display_val)


def _format_kpi_value(val) -> str:
    """KPI 수치 표시 형식 변환."""
    if val is None:
        return "-"
    if isinstance(val, float):
        if val == int(val):
            return f"{int(val):,}"
        return f"{val:,.1f}"
    if isinstance(val, int):
        return f"{val:,}"
    return str(val)


def _prettify_col_name(col: str) -> str:
    """컬럼명을 가독성 좋게 변환 (대문자 → 공백 구분)."""
    # AS 별칭이 한국어인 경우 그대로 사용
    if any("가" <= c <= "힣" for c in col):
        return col
    # SNAKE_CASE → 공백
    return col.replace("_", " ").title()


# ──────────────────────────────────────────────────────────────────────
#  그리드 + 집계 요약 차트 렌더러  [v5.3 신규]
# ──────────────────────────────────────────────────────────────────────


def _render_grid_with_summary(
    rows: List[Any],
    column_names: List[str],
    query_time_ms: float,
    masked_columns: Optional[List[str]],
    # 집계 요약 차트 (선택적)
    agg_chart_type: str = "none",
    agg_chart_x: Optional[str] = None,
    agg_chart_y: Optional[str] = None,
    agg_chart_rows: List[Any] = None,
    agg_chart_cols: List[str] = None,
    agg_label: str = "",
) -> None:
    """
    리스트 쿼리 결과를 그리드로 표시하고,
    집계 요약 차트를 오른쪽에 나란히 표시합니다.

    [레이아웃]
    ┌─────────────────────┬──────────────────┐
    │  데이터 그리드 (3/5) │  집계 요약 차트  │
    │  (환자 목록 테이블)  │  (2/5, 있을 때)  │
    └─────────────────────┴──────────────────┘

    Args:
        rows/column_names:  그리드 원본 데이터
        agg_chart_*:        집계 요약 차트 (smart_aggregate 결과)
    """
    from llm.data_explainer import CHART_NONE, _CHART_TYPES

    has_summary = (
        agg_chart_type not in (CHART_NONE, "", None)
        and agg_chart_rows
        and agg_chart_x
        and agg_chart_y
    )

    if has_summary:
        # 그리드 3 / 차트 2 비율 배치
        col_grid, col_chart = st.columns([3, 2])
    else:
        col_grid = st.container()
        col_chart = None

    with col_grid:
        _section_header("📋", "조회 결과", badge=agg_label or f"총 {len(rows)}건")
        _render_data_table(
            rows, column_names, query_time_ms, masked_columns=masked_columns
        )

    if has_summary and col_chart:
        with col_chart:
            _section_header("📊", "요약", badge=agg_label, badge_color="green")
            # [v5.6] 직접 차트만 렌더링 (셀렉터/헤더 없이)
            # _render_chart는 내부에 차트유형/X축/Y축 셀렉터를 포함하므로
            # 우측 요약 패널에서는 차트 figure만 직접 표시
            _s_rows = agg_chart_rows or []
            _s_cols = agg_chart_cols or []
            if _s_rows and _s_cols and agg_chart_x and agg_chart_y:
                _s_df = pd.DataFrame(_s_rows, columns=_s_cols)
                _s_colors = [T.PRIMARY, "#22c55e", "#f59e0b", "#ef4444"]
                _s_fig = _draw_chart_figure(
                    _s_df, agg_chart_type, agg_chart_x, agg_chart_y, _s_colors
                )
                if _s_fig:
                    st.plotly_chart(
                        _s_fig, use_container_width=True, key="plotly_grid_summary"
                    )
                else:
                    try:
                        st.bar_chart(
                            _s_df.set_index(agg_chart_x)[[agg_chart_y]], height=300
                        )
                    except Exception:
                        st.dataframe(_s_df, use_container_width=True)


# ──────────────────────────────────────────────────────────────────────
#  차트
# ──────────────────────────────────────────────────────────────────────

# ── 차트 타입 레이블 ─────────────────────────────────────────────
_CHART_TYPE_OPTIONS: List[tuple] = [
    # (chart_type_id, 표시명, 설명)
    ("line", "라인", "추세·시계열"),
    ("bar", "바", "범주 비교"),
    ("barh", "가로바", "랭킹 정렬"),
    ("pie", "파이", "비율·구성"),
    ("hist", "분포", "도수 분포"),
]


def _draw_chart_figure(
    df,
    chart_type: str,
    x_col: str,
    y_col: str,
    colors: List[str],
):
    """
    DataFrame + 차트 타입 → Plotly Figure 반환.
    재사용 가능한 순수 렌더링 함수 (상태 없음).
    """
    from llm.data_explainer import (
        CHART_LINE,
        CHART_BAR,
        CHART_BAR_H,
        CHART_PIE,
        CHART_HIST,
    )

    fig = None
    if not _PLOTLY_AVAILABLE:
        return None

    try:
        if chart_type == CHART_LINE:
            fig = px.line(
                df,
                x=x_col,
                y=y_col,
                markers=True,
                color_discrete_sequence=colors,
                template="plotly_white",
            )
            fig.update_traces(line=dict(width=2.5), marker=dict(size=7))
        elif chart_type == CHART_BAR:
            fig = px.bar(
                df,
                x=x_col,
                y=y_col,
                color_discrete_sequence=colors,
                template="plotly_white",
            )
            fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")
        elif chart_type == CHART_BAR_H:
            df_s = df.sort_values(y_col, ascending=True)
            fig = px.bar(
                df_s,
                x=y_col,
                y=x_col,
                orientation="h",
                color_discrete_sequence=colors,
                template="plotly_white",
            )
            fig.update_traces(texttemplate="%{x:,.0f}", textposition="outside")
        elif chart_type == CHART_PIE:
            fig = px.pie(
                df, names=x_col, values=y_col, color_discrete_sequence=colors, hole=0.35
            )
            fig.update_traces(textinfo="percent+label", textposition="inside")
        elif chart_type == CHART_HIST:
            fig = px.bar(
                df,
                x=x_col,
                y=y_col,
                color_discrete_sequence=[T.PRIMARY],
                template="plotly_white",
                text=y_col,
            )
            fig.update_traces(texttemplate="%{text:,}명", textposition="outside")

        if fig:
            fig.update_layout(
                margin=dict(l=10, r=10, t=30, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="Noto Sans KR, Arial", size=12),
                xaxis=dict(gridcolor="#e5e7eb"),
                yaxis=dict(gridcolor="#e5e7eb"),
                height=380,
            )
    except Exception as exc:
        logger.warning(f"차트 생성 실패: {exc}")
        return None

    return fig


def _render_chart(
    rows: List[Any],
    column_names: List[str],
    chart_type: str,
    x_col: Optional[str],
    y_col: Optional[str],
    agg_label: str = "",
    chart_key: str = "",  # [v5.4] 차트 타입 셀렉터 키 구분용
) -> None:
    """
    시각화 섹션 렌더링.

    [v5.4 — 차트 타입 재정의 기능]
    · 자동 감지된 차트 타입을 사용자가 직접 변경할 수 있습니다.
    · 라인/바/가로바/파이/분포 5가지 선택지 제공
    · session_state 에 선택값 저장 → 즉시 재렌더링
    · chart_key: 동일 페이지에 여러 차트가 있을 때 키 충돌 방지
    """
    from llm.data_explainer import (
        CHART_LINE,
        CHART_BAR,
        CHART_BAR_H,
        CHART_PIE,
        CHART_HIST,
        CHART_NONE,
    )

    # ── 헤더 ────────────────────────────────────────────────────
    _section_header("📈", "시각화", badge=agg_label or chart_type, badge_color="blue")

    # ── session_state 키 정의 ───────────────────────────────────
    _ck = chart_key or "main"
    _ss_type = f"da_chart_type_override_{_ck}"
    _ss_xcol = f"da_chart_x_{_ck}"
    _ss_ycol = f"da_chart_y_{_ck}"

    _current_type = st.session_state.get(_ss_type, chart_type)

    # ── 데이터 없음 처리 ────────────────────────────────────────
    if not rows or not column_names:
        st.info("시각화할 데이터가 없습니다.")
        return
    if active_type_check := (st.session_state.get(_ss_type, chart_type)):
        if active_type_check == CHART_NONE and not x_col and not y_col:
            st.info("📊 집계 결과가 있을 때 차트가 표시됩니다.")
            return

    # ── 컨트롤 행: 차트 유형 / X축 / Y축 ───────────────────────
    _col_type, _col_x, _col_y = st.columns([2, 2, 2])

    with _col_type:
        _selected_label = st.selectbox(
            "차트 유형",
            options=[lbl for _, lbl, _ in _CHART_TYPE_OPTIONS],
            index=next(
                (
                    i
                    for i, (t, _, _) in enumerate(_CHART_TYPE_OPTIONS)
                    if t == _current_type
                ),
                0,
            ),
            key=f"da_chart_sel_{_ck}",
            label_visibility="visible",
        )
    _override_type = next(
        (t for t, lbl, _ in _CHART_TYPE_OPTIONS if lbl == _selected_label),
        _current_type,
    )
    st.session_state[_ss_type] = _override_type
    active_type = _override_type

    # X축 셀렉터 — 초기값: AI 자동 감지값, 이후 사용자 선택
    _x_default_idx = column_names.index(x_col) if x_col and x_col in column_names else 0
    with _col_x:
        _x_override = st.selectbox(
            "X축 (가로)",
            options=column_names,
            index=st.session_state.get(f"{_ss_xcol}_idx", _x_default_idx),
            key=f"da_chart_xsel_{_ck}",
            label_visibility="visible",
        )
    st.session_state[f"{_ss_xcol}_idx"] = column_names.index(_x_override)

    # Y축 셀렉터 — 초기값: AI 자동 감지값, 이후 사용자 선택
    _y_candidates = [c for c in column_names if c != _x_override]
    _y_init = (
        y_col
        if y_col and y_col in _y_candidates
        else (_y_candidates[0] if _y_candidates else column_names[-1])
    )
    _y_default_idx = _y_candidates.index(_y_init) if _y_init in _y_candidates else 0
    with _col_y:
        _y_override = st.selectbox(
            "Y축 (세로)",
            options=_y_candidates or column_names,
            index=st.session_state.get(f"{_ss_ycol}_idx", _y_default_idx),
            key=f"da_chart_ysel_{_ck}",
            label_visibility="visible",
        )
    st.session_state[f"{_ss_ycol}_idx"] = (
        _y_candidates.index(_y_override) if _y_override in _y_candidates else 0
    )

    # 최종 활성 컬럼 (초기값 = AI 자동 감지, 수정 후 = 사용자 선택)
    active_x = _x_override or x_col
    active_y = _y_override or y_col

    if active_type == CHART_NONE or not active_x or not active_y:
        st.info("📊 집계 결과가 있을 때 차트가 표시됩니다.")
        return

    df = pd.DataFrame(rows, columns=column_names)
    colors = [T.PRIMARY, "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]

    fig = _draw_chart_figure(df, active_type, active_x, active_y, colors)

    if fig:
        st.plotly_chart(fig, use_container_width=True, key=f"plotly_{_ck}")
    else:
        try:
            df_chart = df.set_index(active_x)[[active_y]]
            if active_type == CHART_LINE:
                st.line_chart(df_chart, use_container_width=True, height=350)
            else:
                st.bar_chart(df_chart, use_container_width=True, height=350)
        except Exception as exc:
            st.warning(f"차트 렌더링 실패: {exc}")


# ──────────────────────────────────────────────────────────────────────
#  자유 시각화 빌더 v5.6 — 원본 데이터 기반 자유 집계
# ──────────────────────────────────────────────────────────────────────


def _render_custom_chart_builder(
    raw_rows: List[Any],
    raw_col_names: List[str],
    chart_key: str = "custom",
) -> None:
    """
    원본 데이터로 자유 집계 + 시각화 빌더.

    [기존 smart_aggregate와의 차이]
    · 기존: AI가 자동으로 날짜/카테고리 감지 → 고정 집계 (건수/월만 보임)
    · 신규: X축/집계방식/색상구분을 직접 선택 → 병동별 진료과 비율 등 가능

    [지원 집계]
    · 건수 (COUNT): X값별 행 수
    · 비율 (%):     전체 대비 비율
    · 수치 평균:    X값별 수치 컬럼 평균

    [색상 구분 = 그룹핑]
    · (없음): 단순 집계
    · 컬럼 선택: X×그룹 교차 집계 → 스택/그룹 바차트
    """
    try:
        import plotly.express as px

        HAS_PLOTLY = True
    except ImportError:
        HAS_PLOTLY = False

    if not raw_rows or not raw_col_names:
        st.info("데이터가 없습니다.")
        return

    df_raw = pd.DataFrame(raw_rows, columns=raw_col_names)
    _ck = chart_key

    # 컬럼 분류: 카테고리 / 수치 + PII 컬럼 감지
    _cat_cols, _num_cols = [], []

    # PII 컬럼 식별 (X축 선택지에서 제외 → LLM에 마스킹값 key 전달 방지)
    _pii_upper: set = set()
    try:
        from db.pii_masker import detect_pii_type

        for col in raw_col_names:
            if detect_pii_type(col) is not None:
                _pii_upper.add(col.upper())
    except Exception:
        pass

    for col in raw_col_names:
        try:
            _s = df_raw[col].dropna().head(50)
            _nr = _s.apply(
                lambda v: (
                    isinstance(v, (int, float))
                    or (
                        isinstance(v, str)
                        and v.replace(".", "", 1).lstrip("-").isdigit()
                    )
                )
            ).mean()
            if _nr > 0.8 and df_raw[col].nunique() > 20:
                _num_cols.append(col)
            elif col.upper() not in _pii_upper:
                # PII 컬럼은 카테고리 선택지에서 제외
                _cat_cols.append(col)
        except Exception:
            if col.upper() not in _pii_upper:
                _cat_cols.append(col)

    if not _cat_cols:
        st.info("카테고리 컬럼이 없어 자유 시각화를 사용할 수 없습니다.")
        return

    # 컨트롤 행
    _c1, _c2, _c3, _c4 = st.columns([2, 2, 2, 2])
    with _c1:
        _x_col = st.selectbox("📊 X축 (그룹 기준)", _cat_cols, key=f"cv_x_{_ck}")
    with _c2:
        _agg_opts = ["건수", "비율(%)"] + (
            [f"평균({c})" for c in _num_cols[:3]] if _num_cols else []
        )
        _agg = st.selectbox("📐 집계 방식", _agg_opts, key=f"cv_agg_{_ck}")
    with _c3:
        _color_label = st.selectbox(
            "🎨 색상 구분",
            ["(없음)"] + [c for c in _cat_cols if c != _x_col],
            key=f"cv_color_{_ck}",
        )
        _color_col = None if _color_label == "(없음)" else _color_label
    with _c4:
        _chart_sel = st.selectbox(
            "📈 차트 유형", ["바", "가로바", "파이", "라인"], key=f"cv_type_{_ck}"
        )

    # 집계 실행
    try:
        _grp = [_x_col] + ([_color_col] if _color_col else [])

        if _agg == "건수":
            _df_agg = df_raw.groupby(_grp).size().reset_index(name="건수")
            _yv = "건수"
        elif _agg == "비율(%)":
            _df_agg = df_raw.groupby(_grp).size().reset_index(name="건수")
            _df_agg["비율(%)"] = (_df_agg["건수"] / _df_agg["건수"].sum() * 100).round(
                1
            )
            _yv = "비율(%)"
        else:
            _nc = _agg.replace("평균(", "").replace(")", "")
            _df_agg = df_raw.groupby(_grp)[_nc].mean().round(1).reset_index()
            _df_agg.rename(columns={_nc: f"평균_{_nc}"}, inplace=True)
            _yv = f"평균_{_nc}"

        _df_agg = _df_agg.sort_values(_yv, ascending=False)

        # 제목 배지
        _lbl = f"총 {len(df_raw):,}건 | {_x_col}별 {_agg}" + (
            f" × {_color_col}" if _color_col else ""
        )
        st.markdown(
            f'<span style="font-size:12px;color:#6B7280;">{_lbl}</span>',
            unsafe_allow_html=True,
        )

        if HAS_PLOTLY:
            _pal = px.colors.qualitative.Set2
            _kw = dict(template="plotly_white", color_discrete_sequence=_pal)
            if _chart_sel == "바":
                fig = (
                    px.bar(
                        _df_agg,
                        x=_x_col,
                        y=_yv,
                        color=_color_col,
                        barmode="group",
                        text=_yv,
                        **_kw,
                    )
                    if _color_col
                    else px.bar(
                        _df_agg,
                        x=_x_col,
                        y=_yv,
                        text=_yv,
                        color_discrete_sequence=[T.PRIMARY],
                        template="plotly_white",
                    )
                )
            elif _chart_sel == "가로바":
                _ds = _df_agg.sort_values(_yv, ascending=True)
                fig = (
                    px.bar(
                        _ds,
                        x=_yv,
                        y=_x_col,
                        color=_color_col,
                        orientation="h",
                        barmode="group",
                        **_kw,
                    )
                    if _color_col
                    else px.bar(
                        _ds,
                        x=_yv,
                        y=_x_col,
                        orientation="h",
                        text=_yv,
                        color_discrete_sequence=[T.PRIMARY],
                        template="plotly_white",
                    )
                )
            elif _chart_sel == "파이":
                fig = px.pie(_df_agg, names=_x_col, values=_yv, **_kw)
            else:
                _ds = _df_agg.sort_values(_x_col)
                fig = (
                    px.line(_ds, x=_x_col, y=_yv, color=_color_col, markers=True, **_kw)
                    if _color_col
                    else px.line(
                        _ds,
                        x=_x_col,
                        y=_yv,
                        markers=True,
                        color_discrete_sequence=[T.PRIMARY],
                        template="plotly_white",
                    )
                )

            fig.update_layout(
                margin=dict(l=20, r=20, t=30, b=30),
                height=400,
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig, use_container_width=True, key=f"plotly_cv_{_ck}")
        else:
            st.bar_chart(_df_agg.set_index(_x_col)[[_yv]], use_container_width=True)

        # ── CSV 다운로드 + AI 요약 ─────────────────────────────────
        _dl_col, _ai_col = st.columns([2, 3])
        with _dl_col:
            _csv = _df_agg.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                "⬇ 집계 CSV",
                data=_csv,
                file_name=f"agg_{_x_col}_{_agg}.csv",
                mime="text/csv",
                key=f"cv_dl_{_ck}",
            )

        # ── AI 요약 섹션 ──────────────────────────────────────────
        st.markdown("---")
        _section_header("🤖", "AI 요약 분석", badge="집계 결과 기반")

        # 포커스 입력 + 요청 버튼
        _focus_col, _btn_col = st.columns([5, 1])
        with _focus_col:
            _cv_focus = st.text_input(
                "분석 포커스 (선택)",
                placeholder="예) 특이한 점 찾아줘 / 상위 항목 원인 분석 / 비율 해석",
                key=f"cv_ai_focus_{_ck}",
                label_visibility="visible",
            )
        with _btn_col:
            st.markdown('<div style="margin-top:1.65rem;">', unsafe_allow_html=True)
            _cv_ai_btn = st.button(
                "🔄 AI 분석",
                key=f"cv_ai_btn_{_ck}",
                use_container_width=True,
                help="현재 집계 결과를 AI가 해석합니다",
            )
            st.markdown("</div>", unsafe_allow_html=True)

        # AI 캐시 키: X축+집계방식+색상+포커스 조합
        _cv_ai_cache_key = f"cv_ai_{_ck}_{_x_col}_{_agg}_{_color_col}_{_cv_focus}"

        # 재요청 버튼 → 캐시 삭제
        if _cv_ai_btn and _cv_ai_cache_key in st.session_state:
            del st.session_state[_cv_ai_cache_key]

        # 캐시 히트 → 즉시 표시
        if _cv_ai_cache_key in st.session_state:
            st.markdown(
                f'<div class="da-ai-block">{st.session_state[_cv_ai_cache_key]}</div>',
                unsafe_allow_html=True,
            )
        elif _cv_ai_btn or f"cv_ai_auto_{_ck}" not in st.session_state:
            # 첫 렌더링 또는 버튼 클릭 → AI 호출
            st.session_state[f"cv_ai_auto_{_ck}"] = True  # 최초 자동실행 방지

            # 집계 결과를 LLM 컨텍스트로 변환
            # [PII 안전] 집계 결과(_df_agg)는 X축 groupby key + 건수/비율만 포함
            # X축 선택지에서 PII 컬럼이 이미 제외됐으므로 집계 key는 비PII 값
            # 추가 안전장치: 집계 결과 컬럼 중 PII 컬럼이 있으면 제거
            _safe_agg_cols = [c for c in _df_agg.columns if c.upper() not in _pii_upper]
            _cv_summary_rows = (
                _df_agg[_safe_agg_cols].head(30).to_dict(orient="records")
            )
            _cv_summary_cols = _safe_agg_cols

            # 집계 메타 정보를 질문에 포함
            _cv_color_sfx = f" (색상 구분: {_color_col})" if _color_col else ""
            _cv_focus_sfx = (
                f" [분석 포커스] {_cv_focus.strip()}"
                if _cv_focus and _cv_focus.strip()
                else ""
            )
            _cv_question = f"{_x_col}별 {_agg} 집계 분석{_cv_color_sfx}{_cv_focus_sfx}"

            _cv_ph = st.empty()
            _cv_full = ""
            try:
                from llm.data_explainer import explain_data

                for _chunk in explain_data(
                    question=_cv_question,
                    rows=_cv_summary_rows,
                    column_names=_cv_summary_cols,
                    sql="",
                    chart_type=_chart_sel,
                    agg_label=_lbl,
                    pii_removed_cols=[],
                ):
                    _cv_full += _chunk
                    _cv_ph.markdown(
                        f'<div class="da-ai-block">{_cv_full}▌</div>',
                        unsafe_allow_html=True,
                    )
            except Exception as _cv_exc:
                _cv_ph.warning(f"AI 요약 실패: {_cv_exc}")

            _cv_ph.empty()
            if _cv_full:
                st.markdown(
                    f'<div class="da-ai-block">{_cv_full}</div>',
                    unsafe_allow_html=True,
                )
                st.session_state[_cv_ai_cache_key] = _cv_full

    except Exception as exc:
        st.warning(f"집계 실패: {exc}")


# ──────────────────────────────────────────────────────────────────────
#  AI 해석
# ──────────────────────────────────────────────────────────────────────


def _render_ai_explanation(
    question: str,
    rows: List[Dict[str, Any]],  # ← PII 컬럼 완전 제거된 list[dict]
    column_names: List[str],
    sql: str,
    chart_type: str,
    agg_label: str = "",
    pii_removed_cols: List[str] = None,
) -> str:
    """
    LLM 스트리밍 AI 해석 + RAG 재질의 버튼.

    [v5.4 신규 — RAG 재질의 버튼]
    AI 해석 내용을 바탕으로 규정집/문서를 바로 검색할 수 있습니다.
    · 빠른 검색: 핵심 키워드만 → 즉시 답변
    · 표준 검색: 상세 내용 → 균형 검색
    · 심층 검색: 관련 규정 전체 → 정밀 분석

    재질의 흐름:
      버튼 클릭 → prefill_prompt 설정 + search_mode 변경
               → main.py chat_input prefill 로 자동 입력
               → RAG 파이프라인 실행

    Returns:
        완성된 AI 해석 텍스트 (캐시 저장용)
    """
    _section_header("🤖", "AI 해석")

    # ── 분석 포커스 입력 ──────────────────────────────────────────
    # 사용자가 AI 해석 방향을 직접 지정할 수 있습니다.
    # 초기값: 비어있음 → 원본 질문 기반 자동 해석 (기존 동작 유지)
    # 입력 예: "병동별 분포에 집중", "이상값 찾아줘", "추세 설명해줘"
    _focus_key = f"da_ai_focus_{hash(sql or question) % 9999}"
    _regen_key = f"da_ai_regen_{hash(sql or question) % 9999}"

    _col_focus, _col_btn = st.columns([5, 1])
    with _col_focus:
        _focus_input = st.text_input(
            "분석 포커스 (선택)",
            placeholder="예) 병동별 분포 분석 / 이상값 찾아줘 / 추세 위주로 설명",
            key=_focus_key,
            label_visibility="visible",
        )
    with _col_btn:
        st.markdown('<div style="margin-top:1.65rem;">', unsafe_allow_html=True)
        _regen_clicked = st.button(
            "🔄 재생성",
            key=_regen_key,
            use_container_width=True,
            help="입력한 포커스로 AI 해석을 다시 생성합니다",
        )
        st.markdown("</div>", unsafe_allow_html=True)

    # ── 스트리밍 플레이스홀더 ──────────────────────────────────────
    _stream_placeholder = st.empty()
    _final_placeholder = st.empty()
    full_text = ""

    # 캐시 키: 포커스+질문 조합 (포커스 변경 시 자동 재생성)
    _cache_key = (
        f"da_ai_cache_{hash((question + (_focus_input or '')) + (sql or '')) % 99999}"
    )

    # 재생성 버튼 클릭 시 캐시 무효화
    if _regen_clicked and _cache_key in st.session_state:
        del st.session_state[_cache_key]

    # 캐시 히트: 이전 해석 즉시 표시
    if _cache_key in st.session_state:
        _cached_text = st.session_state[_cache_key]
        _final_placeholder.markdown(
            f'<div class="da-ai-block">{_cached_text}</div>',
            unsafe_allow_html=True,
        )
        return _cached_text

    # 포커스가 있으면 질문에 추가
    _effective_question = question
    if _focus_input and _focus_input.strip():
        _effective_question = f"{question}\n\n[분석 포커스] {_focus_input.strip()}"

    try:
        from llm.data_explainer import explain_data

        for chunk in explain_data(
            question=_effective_question,
            rows=rows,
            column_names=column_names,
            sql=sql,
            chart_type=chart_type,
            agg_label=agg_label,
            pii_removed_cols=pii_removed_cols or [],
        ):
            full_text += chunk
            _stream_placeholder.markdown(
                f'<div class="da-ai-block">{full_text}▌</div>',
                unsafe_allow_html=True,
            )
    except Exception as exc:
        logger.error(f"AI 해석 오류: {exc}", exc_info=True)
        _stream_placeholder.warning(f"AI 해석 생성 오류: {exc}")

    _stream_placeholder.empty()
    if full_text:
        _final_placeholder.markdown(
            f'<div class="da-ai-block">{full_text}</div>',
            unsafe_allow_html=True,
        )
        # 캐시 저장 (같은 질문+포커스 재방문 시 즉시 표시)
        st.session_state[_cache_key] = full_text

    return full_text


def _render_rag_followup_buttons(ai_text: str, original_question: str) -> None:
    """
    AI 해석 결과를 바탕으로 RAG 검색 재질의 버튼을 표시합니다.

    [설계 원칙]
    · AI가 분석한 내용에서 검색 쿼리를 자동 생성
    · 사용자가 단계(빠른/표준/심층)를 선택해 추가 조사
    · 클릭 시 main.py chat_input으로 prefill → RAG 파이프라인 자동 실행
    · search_mode session_state 업데이트 → 선택한 검색 깊이 적용

    [검색 쿼리 생성 전략]
    1순위: AI 해석에서 핵심 키워드 자동 추출
    2순위: 원본 질문 그대로 사용
    """
    # AI 해석에서 핵심 검색어 추출 (첫 문장의 핵심 명사구)
    import re as _re

    _query = _extract_search_query(ai_text, original_question)

    # 구분선 + 섹션 라벨 (순수 Streamlit 컴포넌트로 div 간섭 제거)
    st.markdown(
        '<hr style="margin:0.8rem 0 0.5rem;border:none;border-top:1px solid rgba(0,0,0,0.07);">',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<span style="font-size:11px;color:#6B7280;font-weight:600;">'
        "이 내용으로 규정/지침 검색</span>",
        unsafe_allow_html=True,
    )

    _RAG_MODES = [
        ("fast", "빠른 검색", "#3B82F6"),
        ("standard", "표준 검색", "#8B5CF6"),
        ("deep", "심층 검색", "#0EA5E9"),
    ]

    _cols = st.columns(len(_RAG_MODES))
    for i, (mode_id, mode_label, color) in enumerate(_RAG_MODES):
        with _cols[i]:
            _btn_key = f"da_rag_btn_{mode_id}_{hash(_query) % 9999}"
            if st.button(
                f"{mode_label}",
                key=_btn_key,
                use_container_width=True,
                help=f"AI 해석 내용을 [{mode_label}] 모드로 규정집에서 검색합니다.",
            ):
                # search_mode 변경 → main.py sidebar에서 반영됨
                st.session_state["search_mode"] = mode_id
                # prefill_prompt: main.py chat_input이 자동으로 이 텍스트를 입력
                st.session_state["prefill_prompt"] = _query
                # 데이터 분석 모드 → 일반 RAG 모드로 전환
                # (search_mode가 fast/standard/deep이면 main.py RAG 파이프라인 실행)
                logger.info(f"RAG 재질의: mode={mode_id}, query='{_query[:60]}'")
                st.rerun()

    # ── 자유 추가 질문 입력창 ─────────────────────────────────────
    # 사용자가 AI 해석을 보고 궁금한 내용을 직접 입력 → RAG 검색 연결
    st.markdown(
        '<hr style="margin:0.8rem 0 0.4rem;border:none;border-top:1px dashed rgba(0,0,0,0.06);">',
        unsafe_allow_html=True,
    )

    _custom_key = f"da_custom_q_{hash(_query) % 9999}"
    _custom_q = st.text_input(
        "추가로 궁금한 내용을 입력하세요",
        placeholder="예) 응급실 과부하 기준은 어떻게 되나요? / 중증도 3등급 처치 지침이 있나요?",
        key=_custom_key,
        label_visibility="visible",
    )

    if _custom_q and _custom_q.strip():
        _custom_q = _custom_q.strip()
        _c1, _c2, _c3, _c4 = st.columns([2, 1, 1, 1])
        with _c1:
            st.markdown(
                '<div style="font-size:11px;color:#374151;padding-top:0.5rem;">'
                + "🔍 <em>"
                + _custom_q[:50]
                + "</em></div>",
                unsafe_allow_html=True,
            )
        for _col, (mode_id, mode_label, _) in zip([_c2, _c3, _c4], _RAG_MODES):
            with _col:
                if st.button(
                    mode_label,
                    key=f"da_custom_btn_{mode_id}_{hash(_custom_q) % 9999}",
                    use_container_width=True,
                ):
                    st.session_state["search_mode"] = mode_id
                    st.session_state["prefill_prompt"] = _custom_q
                    logger.info(
                        f"사용자 추가 질문 → RAG: mode={mode_id}, q='{_custom_q[:60]}'"
                    )
                    st.rerun()

    # ── 자동 추출 쿼리 확인/수정 ───────────────────────────────────
    with st.expander("검색어 확인 · 수정", expanded=False):
        _edited = st.text_input(
            "검색어",
            value=_query,
            key=f"da_rag_query_edit_{hash(_query) % 9999}",
            label_visibility="collapsed",
        )
        if _edited != _query:
            _col_a, _col_b, _col_c = st.columns(3)
            for _col, (mode_id, mode_label, _) in zip(
                [_col_a, _col_b, _col_c], _RAG_MODES
            ):
                with _col:
                    if st.button(
                        f"{mode_label} (수정된 쿼리)",
                        key=f"da_rag_edit_btn_{mode_id}_{hash(_edited) % 9999}",
                        use_container_width=True,
                    ):
                        st.session_state["search_mode"] = mode_id
                        st.session_state["prefill_prompt"] = _edited
                        st.rerun()


def _extract_search_query(ai_text: str, fallback: str) -> str:
    """
    AI 해석 텍스트에서 RAG 검색에 적합한 쿼리를 추출합니다.

    [추출 전략]
    1. 첫 번째 핵심 문장 (✅ ** 요약 이후) 에서 의미 있는 구절 추출
    2. 길면 앞 60자로 축약
    3. 불필요한 마크다운 기호 제거
    4. 추출 실패 → fallback(원본 질문) 사용
    """
    import re as _re

    # 마크다운 제거
    _clean = _re.sub(r"[*#`>_~\[\]()]", "", ai_text)
    # 이모지 제거
    _clean = _re.sub(r"[\U00010000-\U0010ffff]|[\u2600-\u27BF]", "", _clean)

    # 핵심 문장 추출: "핵심 요약:" 이후 또는 첫 문장
    _match = _re.search(r"핵심\s*요약[:\s]+(.{10,80})", _clean)
    if _match:
        return _match.group(1).strip()[:80]

    # 첫 의미 있는 문장 (20자 이상)
    for sent in _clean.split("."):
        sent = sent.strip()
        if len(sent) >= 20:
            return sent[:80]

    return fallback[:80]


# ──────────────────────────────────────────────────────────────────────
#  SQL 생성 (Phase 1)
# ──────────────────────────────────────────────────────────────────────


def _generate_sql_only(question: str) -> None:
    cache = st.session_state.get("da_cache", {})
    if question in cache:
        logger.info(f"캐시 HIT: '{question[:40]}'")
        _render_cached_result(cache[question], question)
        return

    with st.spinner("🔄 SQL 생성 중..."):
        t0 = time.time()
        try:
            from llm.sql_generator import generate_sql

            sql_result = generate_sql(question)
        except Exception as exc:
            st.error(f"⚠️ SQL 생성 오류: {exc}")
            return
        gen_time_ms = (time.time() - t0) * 1000

    if not sql_result.is_valid:
        st.error(f"⚠️ SQL 생성 실패: {sql_result.error}")
        if sql_result.raw_llm:
            with st.expander("LLM 원본 (디버그)"):
                st.text(sql_result.raw_llm[:800])
        return

    st.session_state["da_sql_ready"] = {
        "question": question,
        "sql": sql_result.sql,
        "original_sql": sql_result.sql,
        "gen_time_ms": gen_time_ms,
    }
    st.markdown(
        f'<div style="font-size:12px;color:#6B7280;padding:4px 0;">'
        f"✅ SQL 생성 완료 ({gen_time_ms:.0f}ms) — 아래에서 확인 후 실행하세요.</div>",
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────
#  캐시 결과 렌더링
# ──────────────────────────────────────────────────────────────────────


def _render_cached_result(cached: dict, question: str) -> None:
    """
    캐시된 조회 결과를 렌더링합니다.

    [v5.6 수정]
    · chart_key를 question hash 기반으로 고유화
      → 여러 캐시 결과가 동시에 렌더링될 때 key 충돌 방지
    · 자유 시각화 탭을 card 밖에서 렌더링 (탭 사라짐 방지)
    · _render_chart 대신 _draw_chart_figure 직접 호출 (셀렉터 중복 방지)
    """
    # question hash로 고유 suffix 생성 (충돌 방지 핵심)
    _hk = str(abs(hash(question)) % 99999)

    st.info("이전 조회 결과입니다. 최신 데이터: '🔄 초기화' 후 재질문.")

    st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
    _section_header("📝", "생성된 SQL", badge=f"{cached.get('gen_time_ms', 0):.0f}ms")
    st.code(cached["sql"], language="sql")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
    _render_data_table(
        cached["rows"],
        cached["column_names"],
        cached.get("query_time_ms", 0),
        masked_columns=cached.get("masked_columns"),
    )
    st.markdown("</div>", unsafe_allow_html=True)

    from llm.data_explainer import CHART_GRID, CHART_KPI

    _ct = cached.get("chart_type", "none")

    # ── KPI 카드 ──────────────────────────────────────────────────
    if _ct == CHART_KPI:
        st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
        _render_kpi_cards(
            cached["rows"],
            cached["column_names"],
            agg_label=cached.get("agg_label", ""),
        )
        st.markdown("</div>", unsafe_allow_html=True)

    # ── 자유 시각화 탭 — da-section-card 밖에서 렌더링 ────────────
    # _render_chart 내부 selectbox key가 충돌하지 않도록
    # _draw_chart_figure 직접 호출 + question hash key 사용
    _c_rows = cached["rows"]
    _c_cols = cached["column_names"]

    _section_header(
        "📊", "자유 시각화", badge="원본 데이터 기반 집계", badge_color="purple"
    )
    _ctab_auto, _ctab_custom = st.tabs(["🤖 AI 자동 집계", "🎛️ 사용자 정의"])

    with _ctab_auto:
        # AI 자동 집계 차트 — _draw_chart_figure 직접 호출
        _drew = False
        if _ct == CHART_GRID:
            _agg_rows_c = cached.get("agg_chart_rows") or []
            _agg_cols_c = cached.get("agg_chart_cols") or []
            _agg_ct_c = cached.get("agg_chart_type", "none")
            _agg_x_c = cached.get("agg_chart_x")
            _agg_y_c = cached.get("agg_chart_y")
            if _agg_rows_c and _agg_x_c and _agg_y_c and _agg_ct_c not in ("none", ""):
                _cv_df = pd.DataFrame(_agg_rows_c, columns=_agg_cols_c)
                _fig = _draw_chart_figure(
                    _cv_df,
                    _agg_ct_c,
                    _agg_x_c,
                    _agg_y_c,
                    [T.PRIMARY, "#22c55e", "#f59e0b", "#ef4444"],
                )
                if _fig:
                    st.plotly_chart(
                        _fig, use_container_width=True, key=f"plotly_cached_grid_{_hk}"
                    )
                    _drew = True
        elif _ct not in ("none", ""):
            _cr = cached.get("chart_rows") or cached["rows"]
            _cc = cached.get("chart_cols") or cached["column_names"]
            _cx = cached.get("x_col")
            _cy = cached.get("y_col")
            if _cr and _cx and _cy:
                _cv_df = pd.DataFrame(_cr, columns=_cc)
                _fig = _draw_chart_figure(
                    _cv_df, _ct, _cx, _cy, [T.PRIMARY, "#22c55e", "#f59e0b", "#ef4444"]
                )
                if _fig:
                    st.plotly_chart(
                        _fig, use_container_width=True, key=f"plotly_cached_main_{_hk}"
                    )
                    _drew = True
        if not _drew:
            st.info("AI 자동 집계 결과가 없습니다. '사용자 정의' 탭을 사용하세요.")

    with _ctab_custom:
        _render_custom_chart_builder(
            raw_rows=_c_rows,
            raw_col_names=_c_cols,
            chart_key=f"cached_custom_{_hk}",
        )

    # ── AI 해석 ────────────────────────────────────────────────────
    if cached.get("ai_text"):
        st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
        _section_header("🤖", "AI 해석")
        st.markdown(
            f'<div class="da-ai-block">{cached["ai_text"]}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)
        _question = cached.get("question", "")
        if not _question:
            import re as _re

            _fm = _re.search(r"FROM\s+([\w.]+)", cached.get("sql", ""), _re.IGNORECASE)
            _question = _fm.group(1) if _fm else "데이터 분석"
        _render_rag_followup_buttons(cached["ai_text"], _question)


# ──────────────────────────────────────────────────────────────────────
#  Oracle 즉시 실행 — v5.2 마스킹 파이프라인 + AI 해석 복구
# ──────────────────────────────────────────────────────────────────────

# Oracle 실행 결과 캐시 (동일 SQL 5분 내 재실행 방지)
_ORACLE_EXEC_CACHE: dict = {}
_ORACLE_EXEC_CACHE_TTL = 300  # 5분


def _get_oracle_cached(sql: str):
    """Oracle 실행 결과 캐시 조회. (rows, col_names, query_ms) 반환."""
    import hashlib, time as _t

    _k = hashlib.md5(sql.strip().encode()).hexdigest()[:16]
    entry = _ORACLE_EXEC_CACHE.get(_k)
    if entry and (_t.time() - entry["ts"]) < _ORACLE_EXEC_CACHE_TTL:
        return entry["data"], entry["ts"]
    return None, None


def _set_oracle_cached(sql: str, data: tuple) -> None:
    """Oracle 실행 결과 캐시 저장."""
    import hashlib, time as _t

    _k = hashlib.md5(sql.strip().encode()).hexdigest()[:16]
    if len(_ORACLE_EXEC_CACHE) > 30:
        _oldest = min(_ORACLE_EXEC_CACHE, key=lambda k: _ORACLE_EXEC_CACHE[k]["ts"])
        del _ORACLE_EXEC_CACHE[_oldest]
    _ORACLE_EXEC_CACHE[_k] = {"data": data, "ts": _t.time()}


def _execute_oracle_now(
    question: str,
    sql: str,
    gen_time_ms: float,
) -> Optional[dict]:
    """
    Oracle 실행 → PII 마스킹 → 화면 표시 → LLM AI 해석.
    [v5.7] Oracle 실행 결과 5분 캐싱 — 동일 SQL 재실행 방지.

    [v5.2 마스킹 파이프라인]
    ─────────────────────────────────────────────────────────────────
    Oracle rows (원본 dict)
        ↓ _apply_masking()
    masked_rows (list[tuple])  ← Layer 1: _render_data_table (화면)
        ↓ _llm_safe_rows()
    safe_dict_rows (list[dict]) ← Layer 2: explain_data (LLM 전달)
    ─────────────────────────────────────────────────────────────────

    [v5.2 AI 요약 복구]
    · if _analysis: 조건 완전 제거
    · analyze_query_result 실패 여부와 무관하게 항상 AI 해석 실행
    · masked_rows(tuple) → safe_dict_rows(dict) 변환 후 전달
    """
    # ── SQL 표시 ────────────────────────────────────────────────────
    st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
    _section_header("✅", "실행 SQL", badge=f"{gen_time_ms:.0f}ms", badge_color="green")
    st.code(sql, language="sql")
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Oracle 실행 (캐시 우선) ──────────────────────────────────────
    _cached_exec, _cached_ts = _get_oracle_cached(sql)
    if _cached_exec is not None:
        rows, _col_names_cached, query_ms = _cached_exec
        _cache_age = int(time.time() - _cached_ts)
        st.caption(f"⚡ 캐시 결과 ({_cache_age}초 전 조회 · 5분 유지)")
    else:
        with st.spinner("⏳ Oracle DB 조회 중..."):
            _t0 = time.time()
            rows = None
            _err = ""
            try:
                from db.oracle_client import execute_query as _eq

                rows = _eq(
                    sql=sql, max_rows=int(getattr(settings, "oracle_max_rows", 5000))
                )
            except Exception as _exc:
                _err = str(_exc)
                logger.error(f"Oracle 실행 오류: {_exc}", exc_info=True)
            query_ms = (time.time() - _t0) * 1000

    if rows is None:
        st.error(f"⚠️ Oracle 오류: {_err or '쿼리 실패'}")
        st.markdown(
            '<div style="background:#FEF9C3;border-left:3px solid #EAB308;'
            'padding:8px 12px;border-radius:4px;font-size:12px;margin-top:4px;">'
            "💡 ORA-00933: ROWNUM 문법 | ORA-00942: 테이블 없음 | ORA-01017: 자격증명</div>",
            unsafe_allow_html=True,
        )
        return None

    if not rows:
        st.info("ℹ️ 조회 결과 없음.")
        return {
            "sql": sql,
            "rows": [],
            "column_names": [],
            "masked_columns": [],
            "chart_type": "none",
            "x_col": None,
            "y_col": None,
            "gen_time_ms": gen_time_ms,
            "query_time_ms": query_ms,
        }

    # 실행 성공 → 캐시 저장 (5분 유지)
    if _cached_exec is None:
        _col_names_for_cache = list(rows[0].keys()) if isinstance(rows[0], dict) else []
        _set_oracle_cached(sql, (rows, _col_names_for_cache, query_ms))

    # ── 결과 분석 (차트 유형 감지) ──────────────────────────────────
    # analyze_query_result 는 원본 rows(dict) 기반으로 차트 유형 감지
    # 마스킹 전에 호출해야 집계 컬럼 패턴 감지가 정확함
    _analysis = None
    try:
        from llm.data_explainer import analyze_query_result as _aqr

        _analysis = _aqr(question, rows, sql)
    except Exception as _exc:
        logger.warning(f"analyze_query_result 실패 (무시하고 계속): {_exc}")

    _col_names: List[str] = (
        _analysis.column_names
        if _analysis and _analysis.column_names
        else (list(rows[0].keys()) if rows and isinstance(rows[0], dict) else [])
    )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  PII 마스킹 — 이 줄 아래로 원본 rows 사용 금지
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    _tbl_name = _extract_table_name(sql)

    masked_rows, masked_col_names, has_pii, masked_col_list = _apply_masking(
        rows=rows,
        col_names=_col_names,
        table_name=_tbl_name,
    )

    if has_pii:
        logger.info(
            f"PII 마스킹 완료: table={_tbl_name} | "
            f"컬럼={masked_col_list} | {len(masked_rows)}행"
        )
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # ── 분석 결과 변수 추출 ────────────────────────────────────────
    _chart_type = _analysis.chart_type if _analysis else "none"
    _x_col = _analysis.x_col if _analysis else None
    _y_col = _analysis.y_col if _analysis else None
    _agg_label = _analysis.agg_label if _analysis else ""
    _agg_chart_type = _analysis.agg_chart_type if _analysis else "none"
    _agg_chart_x = _analysis.agg_chart_x if _analysis else None
    _agg_chart_y = _analysis.agg_chart_y if _analysis else None

    _chart_rows_masked: List[Any] = []
    _chart_cols_masked: List[str] = []

    # 집계 요약 차트 — 마스킹 불필요 (smart_aggregate 결과: 분류명+건수만 포함)
    # 직접 dict 형태로 사용 (tuple 변환 없이)
    _agg_chart_rows_masked: List[Any] = []
    _agg_chart_cols_masked: List[str] = []
    if _analysis and _analysis.has_summary_chart:
        _agg_raw = _analysis.chart_rows or []
        _agg_c = _analysis.chart_cols or []
        if _agg_raw:
            # 이미 집계된 데이터 (분류명, 건수) — 개인정보 없음, 마스킹 생략
            # dict 형태 보장
            if isinstance(_agg_raw[0], dict):
                _agg_chart_rows_masked = _agg_raw
                _agg_chart_cols_masked = _agg_c
            else:
                _agg_chart_rows_masked = [dict(zip(_agg_c, r)) for r in _agg_raw]
                _agg_chart_cols_masked = _agg_c

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Layer 1 — 시각화 타입별 3-way 분기
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    from llm.data_explainer import CHART_GRID, CHART_KPI, _CHART_TYPES

    if _analysis and _analysis.is_kpi:
        # ── KPI 카드: 단순 집계 결과 (COUNT, SUM 등 1~3행) ──────────
        st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
        _render_kpi_cards(masked_rows, masked_col_names, agg_label=_agg_label)
        st.markdown("</div>", unsafe_allow_html=True)

    elif _analysis and _analysis.is_grid:
        # ── 그리드: 리스트/목록 쿼리 → 테이블 + 탭 시각화 ─────────
        st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
        _render_grid_with_summary(
            masked_rows,
            masked_col_names,
            query_ms,
            masked_columns=masked_col_list if has_pii else None,
            agg_chart_type=_agg_chart_type,
            agg_chart_x=_agg_chart_x,
            agg_chart_y=_agg_chart_y,
            agg_chart_rows=_agg_chart_rows_masked,
            agg_chart_cols=_agg_chart_cols_masked,
            agg_label=_agg_label,
        )
        st.markdown("</div>", unsafe_allow_html=True)

        # ── [v5.6] 자유 시각화 탭 — da-section-card 밖에서 렌더링 ──
        # st.tabs()는 HTML div 안에서 렌더링되면 탭 UI가 숨겨질 수 있음
        # → card div 완전히 닫힌 후 탭 렌더링
        _section_header(
            "📊", "자유 시각화", badge="원본 데이터 기반 집계", badge_color="purple"
        )

        _viz_tab_auto, _viz_tab_custom = st.tabs(["🤖 AI 자동 집계", "🎛️ 사용자 정의"])

        with _viz_tab_auto:
            if _analysis and _analysis.has_summary_chart and _agg_chart_rows_masked:
                # 차트만 직접 렌더링 (헤더/셀렉터 중복 방지)
                _cv_df = pd.DataFrame(
                    _agg_chart_rows_masked, columns=_agg_chart_cols_masked
                )
                _colors_auto = [T.PRIMARY, "#22c55e", "#f59e0b", "#ef4444"]
                from llm.data_explainer import (
                    CHART_LINE,
                    CHART_BAR,
                    CHART_BAR_H,
                    CHART_PIE,
                    CHART_HIST,
                )

                _fig_auto = _draw_chart_figure(
                    _cv_df, _agg_chart_type, _agg_chart_x, _agg_chart_y, _colors_auto
                )
                if _fig_auto:
                    st.plotly_chart(
                        _fig_auto, use_container_width=True, key="plotly_auto_tab"
                    )
                else:
                    st.bar_chart(
                        _cv_df.set_index(_agg_chart_x)[[_agg_chart_y]]
                        if _agg_chart_x and _agg_chart_y
                        else _cv_df
                    )
            else:
                st.info("AI 자동 집계 결과가 없습니다. '사용자 정의' 탭을 사용하세요.")

        with _viz_tab_custom:
            _render_custom_chart_builder(
                raw_rows=masked_rows,
                raw_col_names=masked_col_names,
                chart_key="grid_custom",
            )

    else:
        # ── 기존 흐름: 데이터 테이블 + 탭 시각화 ──────────────────
        st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
        _render_data_table(
            masked_rows,
            masked_col_names,
            query_ms,
            masked_columns=masked_col_list if has_pii else None,
        )
        st.markdown("</div>", unsafe_allow_html=True)

        # [v5.6] 자유 시각화 탭 — da-section-card 밖에서 렌더링
        _section_header(
            "📊", "자유 시각화", badge="원본 데이터 기반 집계", badge_color="purple"
        )

        _viz_tab_auto2, _viz_tab_custom2 = st.tabs(["🤖 AI 자동 집계", "🎛️ 사용자 정의"])

        with _viz_tab_auto2:
            if (
                _analysis
                and not _analysis.is_empty
                and _chart_type not in ("none", CHART_GRID, CHART_KPI)
            ):
                _chart_rows_raw = _analysis.chart_rows if _analysis.chart_rows else rows
                _chart_cols_raw = (
                    _analysis.chart_cols if _analysis.chart_cols else _col_names
                )
                _cr_masked, _cc_masked, _, _ = _apply_masking(
                    rows=_chart_rows_raw,
                    col_names=_chart_cols_raw,
                    table_name=_tbl_name,
                )
                if _cr_masked:
                    _cv_df2 = pd.DataFrame(_cr_masked, columns=_cc_masked)
                    _colors_auto2 = [T.PRIMARY, "#22c55e", "#f59e0b", "#ef4444"]
                    _fig_auto2 = _draw_chart_figure(
                        _cv_df2, _chart_type, _x_col, _y_col, _colors_auto2
                    )
                    if _fig_auto2:
                        st.plotly_chart(
                            _fig_auto2, use_container_width=True, key="plotly_auto_tab2"
                        )
                    else:
                        st.bar_chart(
                            _cv_df2.set_index(_x_col)[[_y_col]]
                            if _x_col and _y_col
                            else _cv_df2
                        )
                else:
                    st.info("AI 자동 집계 결과가 없습니다.")
            else:
                st.info("AI 자동 집계 결과가 없습니다. '사용자 정의' 탭을 사용하세요.")

        with _viz_tab_custom2:
            _render_custom_chart_builder(
                raw_rows=masked_rows,
                raw_col_names=masked_col_names,
                chart_key="else_custom",
            )

    # ── Layer 2: AI 해석 ────────────────────────────────────────────
    # [v5.2 핵심 수정]
    # 1. _analysis None 여부와 무관하게 항상 실행 (if _analysis: 제거)
    # 2. masked_rows(tuple) → safe_dict_rows(dict) 변환 후 전달
    #    → explain_data 내부 r[col] dict 접근 TypeError 원천 차단
    # 3. _llm_safe_rows() — PII 컬럼 완전 제거 (v5.3)
    #    화면: masked_rows (마스킹 표시) / LLM: safe_dict_rows (PII 컬럼 없음)
    _llm_result = _llm_safe_rows(masked_rows, masked_col_names, table_name=_tbl_name)
    safe_dict_rows, _removed_pii_cols = (
        _llm_result if isinstance(_llm_result, tuple) else (_llm_result, [])
    )

    # safe_col_names: PII 컬럼이 제거된 컬럼명 목록 (LLM 에게 전달)
    safe_col_names = [
        c
        for c in masked_col_names
        if c.upper() not in {p.upper() for p in _removed_pii_cols}
    ]

    # [v5.5] AI 해석 card와 버튼/입력창 영역을 분리
    # da-section-card div 안에 Streamlit 위젯(button, text_input)이 들어가면
    # 해당 div의 CSS 범위에 묶여 렌더링이 되지 않는 문제 수정.
    # AI 텍스트 자체만 card로 감싸고, 버튼/입력창은 card 밖에서 렌더링.
    st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
    _ai_text = _render_ai_explanation(
        question=question,
        rows=safe_dict_rows,
        column_names=safe_col_names,
        sql=sql,
        chart_type=_chart_type,
        agg_label=_agg_label,
        pii_removed_cols=_removed_pii_cols,
    )
    st.markdown("</div>", unsafe_allow_html=True)

    # [v5.5] RAG 재질의 버튼 + 자유 입력창 — da-section-card 밖에서 렌더링
    # card div 닫힌 후 → Streamlit 위젯이 정상 렌더링됨
    if _ai_text:
        _render_rag_followup_buttons(_ai_text, question)

    logger.info(
        f"Oracle 실행 완료: {len(rows)}행 | {query_ms:.0f}ms | "
        f"PII={has_pii}({masked_col_list}) | AI해석={'OK' if _ai_text else 'EMPTY'} "
        f"| '{question[:40]}'"
    )

    return {
        "sql": sql,
        "rows": masked_rows,  # list[tuple] — 마스킹됨
        "column_names": masked_col_names,
        "masked_columns": masked_col_list,
        "chart_type": _chart_type,
        "x_col": _x_col,
        "y_col": _y_col,
        "chart_rows": _chart_rows_masked,
        "chart_cols": _chart_cols_masked,
        "agg_label": _agg_label,
        # [v5.4] 집계 요약 차트 (GRID 타입일 때 옆에 표시)
        "agg_chart_type": _agg_chart_type,
        "agg_chart_x": _agg_chart_x,
        "agg_chart_y": _agg_chart_y,
        "agg_chart_rows": _agg_chart_rows_masked,
        "agg_chart_cols": _agg_chart_cols_masked,
        "gen_time_ms": gen_time_ms,
        "query_time_ms": query_ms,
        "ai_text": _ai_text,
    }


# ──────────────────────────────────────────────────────────────────────
#  스키마 편집기
# ──────────────────────────────────────────────────────────────────────


def _render_schema_editor() -> None:
    st.markdown(
        "<div style='font-size:13px;color:#374151;margin-bottom:6px;'>"
        "테이블별 컬럼 정보를 직접 입력합니다.</div>",
        unsafe_allow_html=True,
    )
    _cur = st.session_state.get("da_manual_schema", "")
    _ed = st.text_area(
        label="명세서",
        value=_cur,
        height=260,
        key="da_schema_editor",
        label_visibility="collapsed",
        placeholder="### 테이블명\n| 컬럼명 | 타입 | 설명 |\n|--------|------|------|\n| ... |",
    )
    c1, c2, _ = st.columns([2, 2, 6])
    with c1:
        if st.button("💾 저장", key="btn_schema_save", type="primary"):
            st.session_state["da_manual_schema"] = _ed
            st.session_state["da_cache"] = {}
            st.success("✅ 저장됨")
    with c2:
        if st.button("🗑 초기화", key="btn_schema_clear"):
            st.session_state["da_manual_schema"] = ""
            st.session_state["da_cache"] = {}
            st.rerun()
    if st.session_state.get("da_manual_schema"):
        st.caption(f"✅ 수동 명세 {len(st.session_state['da_manual_schema'])}자 저장됨")


# ──────────────────────────────────────────────────────────────────────
#  탭 렌더링 진입점
# ──────────────────────────────────────────────────────────────────────


def render_data_analysis_tab() -> None:
    """
    데이터 분석 탭 진입점 (v5.2)

    세션 상태:
    da_messages  : 대화 히스토리
    da_cache     : {질문: result_dict}  — rows 는 항상 마스킹된 tuple
    da_sql_ready : SQL 생성 대기 상태
    da_prefill   : 예시 카드 클릭 질문
    """
    _da_reset_seq()
    _inject_dashboard_css()

    if not getattr(settings, "oracle_enabled", False):
        st.markdown(
            '<div class="da-error-box">⚠️ Oracle 연결 비활성화. '
            ".env 에 ORACLE_ENABLED=true 설정 필요.</div>",
            unsafe_allow_html=True,
        )
        return

    with st.expander("📋 테이블 명세서 편집 (SQL 생성 정확도 향상)", expanded=False):
        _render_schema_editor()

    for key, default in [("da_messages", []), ("da_cache", {})]:
        if key not in st.session_state:
            st.session_state[key] = default

    _, col_r = st.columns([6, 1])
    with col_r:
        if st.button("🔄 초기화", key="da_refresh"):
            for k in ["da_cache", "da_messages", "da_sql_ready"]:
                st.session_state.pop(k, None)
            st.session_state["da_cache"] = {}
            st.session_state["da_messages"] = []
            st.rerun()

    # 히스토리
    for _msg in st.session_state["da_messages"]:
        with st.chat_message(_msg["role"]):
            if _msg["role"] == "user":
                st.markdown(_msg["content"])
            else:
                _hq = _msg.get("question", "")
                if _hq and _hq in st.session_state.get("da_cache", {}):
                    _render_cached_result(st.session_state["da_cache"][_hq], _hq)
                else:
                    st.markdown(f'*이전 분석 (질문: "{_hq[:40]}")*')

    if not st.session_state["da_messages"] and not st.session_state.get("da_sql_ready"):
        _render_home_screen()

    _prefill = st.session_state.pop("da_prefill", None)
    _prompt = _prefill or st.chat_input(
        "데이터에 대해 질문하세요...", key="da_chat_input"
    )

    if _prompt:
        st.session_state.pop("da_sql_ready", None)
        _msgs = st.session_state["da_messages"]
        if not _msgs or _msgs[-1].get("content") != _prompt:
            _msgs.append({"role": "user", "content": _prompt})
        with st.chat_message("user"):
            st.markdown(_prompt)
        with st.chat_message("assistant"):
            _generate_sql_only(_prompt)

    # ═══════════════════════════════════════════════════════════════
    #  SQL 편집기 + 실행 버튼 (chat_message 밖 — 슬라이더 소멸 방지)
    # ═══════════════════════════════════════════════════════════════
    _ready = st.session_state.get("da_sql_ready")
    if _ready:
        _q = _ready["question"]
        _sql = _ready["sql"]
        _gt = _ready["gen_time_ms"]
        _orig = _ready["original_sql"]

        st.markdown('<div class="da-section-card">', unsafe_allow_html=True)
        _section_header(
            "✏️", "SQL 확인 및 수정", badge=f"{_gt:.0f}ms", badge_color="blue"
        )
        st.markdown(
            '<div style="font-size:12px;color:#6B7280;margin-bottom:6px;">'
            "⚠️ AI 생성 SQL — 실행 전 반드시 확인하세요.</div>",
            unsafe_allow_html=True,
        )

        _ek = f"da_sql_editor_{abs(hash(_q))}"
        st.text_area(
            label="SQL",
            value=_sql,
            height=max(180, _sql.count("\n") * 22 + 80),
            key=_ek,
            label_visibility="collapsed",
        )

        _def_limit = int(getattr(settings, "oracle_max_rows", 5000))
        _row_limit = st.select_slider(
            "조회 행 수 제한",
            options=[100, 500, 1000, 2000, 5000, 10000],
            value=min(_def_limit, 5000),
            key="da_row_limit_slider",
            format_func=lambda x: f"{x:,}행",
        )

        c_run, c_rst, _ = st.columns([2, 2, 6])
        with c_run:
            _btn_run = st.button("▶ 실행", key="btn_run_sql", type="primary")
        with c_rst:
            _btn_rst = st.button("↩ 원래대로", key="btn_reset_sql")

        st.markdown("</div>", unsafe_allow_html=True)

        if _btn_run:
            _cur_sql = (st.session_state.get(_ek, _orig) or _orig).strip()
            # FETCH FIRST → ROWNUM 변환 (Oracle 11g 호환)
            _cur_sql = re.sub(
                r"\n?\s*FETCH\s+FIRST\s+\d+\s+ROWS\s+ONLY\s*$",
                "",
                _cur_sql,
                flags=re.IGNORECASE,
            ).rstrip()
            if not re.search(r"\bROWNUM\b", _cur_sql, re.IGNORECASE):
                _cur_sql = (
                    f"SELECT * FROM (\n{_cur_sql}\n) WHERE ROWNUM <= {_row_limit}"
                )

            result = _execute_oracle_now(_q, _cur_sql, _gt)
            if result is not None:
                st.session_state["da_cache"][_q] = result
                st.session_state.setdefault("da_messages", []).append(
                    {"role": "assistant", "content": "[분석결과]", "question": _q}
                )
                st.session_state.pop("da_sql_ready", None)
                st.rerun()

        if _btn_rst:
            st.session_state.pop(_ek, None)
            st.session_state["da_sql_ready"]["sql"] = _orig
            st.rerun()

        st.markdown(
            '<div style="font-size:12px;color:#9CA3AF;text-align:center;padding:8px 0 4px;">'
            "✏️ SQL 수정 가능 │ 🔢 행 수 조절 │ ▶ 실행으로 조회 시작</div>",
            unsafe_allow_html=True,
        )