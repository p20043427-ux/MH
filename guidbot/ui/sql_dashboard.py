"""
ui/sql_dashboard.py ─ 전산팀 SQL 대시보드 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[목적]
"데이터 분석" 모드는 자연어 → SQL 자동 생성이 목적.
SQL 대시보드는 전산팀이 직접 SQL을 작성·실행하는 도구.

[기능]
1. SQL 에디터 (코드 스타일 textarea)
2. 스키마 브라우저 (허용 테이블/컬럼 탐색)
3. 실행 → Oracle 직접 실행
4. 결과: 데이터 테이블 + 차트 + AI 요약
5. 쿼리 히스토리 (최근 20개, session_state 저장)
6. 즐겨찾기 (이름 붙여 저장)

[보안]
- 관리자 인증(role == "admin") 필수
- SELECT 전용 (SqlValidator 재활용)
- 실행 로그 기록 (누가 언제 어떤 쿼리)
- PII 컬럼 자동 마스킹 (data_dashboard._apply_masking 재활용)
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from config.settings import settings
from ui.theme import UITheme as T
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  세션 키 상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_SS_HISTORY = "sqld_history"  # List[dict] 쿼리 히스토리
_SS_FAVORITES = "sqld_favorites"  # List[dict] 즐겨찾기
_SS_LAST_RESULT = "sqld_last_result"  # 마지막 실행 결과
_SS_EDITOR_SQL = "sqld_editor_sql"  # 에디터 현재 SQL
_MAX_HISTORY = 20


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  초기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _init_state() -> None:
    for key, default in [
        (_SS_HISTORY, []),
        (_SS_FAVORITES, []),
        (_SS_LAST_RESULT, None),
        (_SS_EDITOR_SQL, _DEFAULT_SQL),
    ]:
        st.session_state.setdefault(key, default)


_DEFAULT_SQL = """-- 전산팀 SQL 대시보드
-- 직접 SELECT 쿼리를 입력하고 실행하세요.
SELECT
    PTMIINDT        AS 내원일자,
    PTMIKTS1        AS 최초중증도,
    PTMIEMRT        AS 응급진료결과,
    COUNT(*)        AS 건수
FROM JAIN_OCS.EMIHPTMI
WHERE PTMIINDT = TO_CHAR(SYSDATE, 'YYYYMMDD')
GROUP BY PTMIINDT, PTMIKTS1, PTMIEMRT
ORDER BY 건수 DESC
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CSS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_CSS = f"""
<style>
/* SQL 에디터 textarea — 다크 코드 스타일 */
[data-testid="stTextArea"] textarea {{
    font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace !important;
    font-size: 13px !important;
    line-height: 1.6 !important;
    background: #0d1117 !important;
    color: #c9d1d9 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
}}
[data-testid="stTextArea"] textarea:focus {{
    border-color: {T.PRIMARY}88 !important;
    box-shadow: 0 0 0 2px {T.PRIMARY}22 !important;
}}

/* 실행 버튼 */
.sqld-run-btn > div[data-testid="stButton"] > button {{
    background: linear-gradient(135deg, {T.PRIMARY}, #1d4ed8) !important;
    color: white !important;
    font-weight: 700 !important;
    font-size: 14px !important;
    border: none !important;
    border-radius: 8px !important;
    height: 44px !important;
    letter-spacing: 0.02em !important;
}}
.sqld-run-btn > div[data-testid="stButton"] > button:hover {{
    opacity: 0.92 !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px {T.PRIMARY}44 !important;
}}

/* 히스토리 항목 */
.sqld-hist-item {{
    background: rgba(0,0,0,0.04);
    border: 1px solid rgba(0,0,0,0.08);
    border-radius: 6px;
    padding: 0.5rem 0.7rem;
    margin-bottom: 0.35rem;
    cursor: pointer;
    transition: all 140ms;
}}
.sqld-hist-item:hover {{
    background: {T.PRIMARY}10;
    border-color: {T.PRIMARY}30;
}}
.sqld-hist-time {{
    font-size: 10px;
    color: #9CA3AF;
    margin-bottom: 0.2rem;
}}
.sqld-hist-sql {{
    font-size: 11px;
    font-family: 'Courier New', monospace;
    color: #374151;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}

/* 결과 헤더 */
.sqld-result-header {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.6rem 0;
    border-bottom: 2px solid {T.PRIMARY}28;
    margin-bottom: 0.8rem;
}}
.sqld-result-title {{
    font-size: 15px;
    font-weight: 700;
    color: #111827;
}}
.sqld-badge {{
    background: {T.PRIMARY}18;
    color: {T.PRIMARY};
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 12px;
    border: 1px solid {T.PRIMARY}30;
}}

/* 스키마 브라우저 */
.sqld-schema-table {{
    background: #f9fafb;
    border: 1px solid #E5E7EB;
    border-radius: 8px;
    padding: 0.6rem 0.8rem;
    margin-bottom: 0.4rem;
}}
.sqld-schema-name {{
    font-size: 12px;
    font-weight: 700;
    color: {T.PRIMARY};
    font-family: monospace;
}}
.sqld-schema-desc {{
    font-size: 11px;
    color: #6B7280;
    margin-top: 2px;
}}
</style>
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SQL 실행 + 후처리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _execute_sql(
    sql: str,
    admin_user: str = "admin",
) -> Tuple[bool, Optional[List], Optional[List[str]], str, float]:
    """
    SQL 실행 → (success, rows, col_names, error_msg, elapsed_ms)

    보안 레이어:
    1. SqlValidator — SELECT 전용, 허용 테이블 확인
    2. execute_query — 연결 풀에서 실행
    3. 감사 로그 — 누가 언제 어떤 쿼리

    Returns:
        (성공, 행 목록, 컬럼명, 오류메시지, 실행시간ms)
    """
    from llm.sql_generator import SqlValidator
    from db.oracle_client import execute_query

    sql = sql.strip()
    if not sql:
        return False, None, None, "SQL이 비어 있습니다.", 0.0

    # ── 보안 검증 ──────────────────────────────────────
    validator = SqlValidator()
    is_valid, validated_sql, error = validator.validate(sql)
    if not is_valid:
        return False, None, None, f"보안 검증 실패: {error}", 0.0

    # ── 실행 + 감사 로그 ───────────────────────────────
    logger.info(
        f"[SQL DASHBOARD] user={admin_user} | "
        f"sql={validated_sql[:120].replace(chr(10), ' ')}"
    )

    # ── 쿼리 결과 캐시 확인 (3분 TTL) ──────────────────
    import hashlib as _sqhash

    _sqk = _sqhash.md5(validated_sql.strip().encode()).hexdigest()[:14]
    _sq_cache = st.session_state.get("sqld_result_cache", {})
    _sq_entry = _sq_cache.get(_sqk)
    if _sq_entry and (time.time() - _sq_entry["ts"]) < 180:
        _age = int(time.time() - _sq_entry["ts"])
        logger.info(f"SQL 대시보드 캐시 히트: {_sqk} ({_age}초 전)")
        return (
            True,
            _sq_entry["rows"],
            _sq_entry["cols"],
            f"(캐시 {_age}초 전)",
            _sq_entry["ms"],
        )

    t0 = time.time()
    try:
        # execute_query는 List[Dict] 반환 → 컬럼명은 첫 행의 keys로 추출
        result = execute_query(
            validated_sql,
            user_context=admin_user,
        )
        if result is None:
            return False, None, None, "쿼리 실행 실패 (결과 없음)", 0.0
        rows = [tuple(r.values()) for r in result]
        col_names = list(result[0].keys()) if result else []
        elapsed = (time.time() - t0) * 1000
        # 결과 캐시 저장
        _sq_cache[_sqk] = {
            "rows": rows,
            "cols": col_names,
            "ms": elapsed,
            "ts": time.time(),
        }
        if len(_sq_cache) > 20:
            _oldest = min(_sq_cache, key=lambda k: _sq_cache[k]["ts"])
            del _sq_cache[_oldest]
        st.session_state["sqld_result_cache"] = _sq_cache
        return True, rows, col_names, "", elapsed
    except Exception as exc:
        elapsed = (time.time() - t0) * 1000
        return False, None, None, str(exc), elapsed


def _add_history(sql: str, elapsed_ms: float, row_count: int) -> None:
    """쿼리를 히스토리에 추가 (최대 20개 유지)."""
    hist = st.session_state.get(_SS_HISTORY, [])
    entry = {
        "sql": sql.strip(),
        "time": datetime.now().strftime("%H:%M:%S"),
        "elapsed": f"{elapsed_ms:.0f}ms",
        "rows": row_count,
        "preview": sql.strip().replace("\n", " ")[:60],
    }
    hist.insert(0, entry)
    st.session_state[_SS_HISTORY] = hist[:_MAX_HISTORY]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  결과 렌더러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _render_result(
    rows: List[Any],
    col_names: List[str],
    elapsed_ms: float,
    sql: str,
    admin_user: str,
) -> None:
    """
    쿼리 결과 렌더링: 데이터 테이블 + 차트 + AI 요약.

    data_dashboard의 _apply_masking, _render_chart,
    _render_ai_explanation 함수를 그대로 재활용합니다.
    """
    from ui.data_dashboard import _extract_table_name as _extract_table_name_from_sql
    from ui.data_dashboard import (
        _apply_masking,
        _render_data_table,
        _render_chart,
        _render_kpi_cards,
        _render_ai_explanation,
    )
    from llm.data_explainer import (
        analyze_query_result,
        CHART_GRID,
        CHART_KPI,
        _CHART_TYPES,
    )

    if not rows:
        st.info("결과가 없습니다. (0행)")
        return

    # ── 결과 헤더 ───────────────────────────────────────
    st.markdown(
        f'<div class="sqld-result-header">'
        f'<span class="sqld-result-title">쿼리 결과</span>'
        f'<span class="sqld-badge">{len(rows):,}행</span>'
        f'<span class="sqld-badge">{elapsed_ms:.0f}ms</span>'
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── PII 마스킹 ─────────────────────────────────────
    _tbl = _extract_table_name_from_sql(sql)
    masked_rows, masked_cols, has_pii, pii_list = _apply_masking(
        rows=rows,
        col_names=col_names,
        table_name=_tbl,
    )
    if has_pii:
        st.warning(f"⚠️ PII 마스킹 적용: {pii_list}", icon="🔒")

    # ── 분석 (차트 타입 결정) ──────────────────────────
    dict_rows = [
        dict(zip(masked_cols, r)) if isinstance(r, tuple) else r for r in masked_rows
    ]
    _analysis = None
    try:
        _analysis = analyze_query_result(
            question="SQL 대시보드 직접 실행 결과",
            rows=dict_rows,
            sql=sql,
        )
    except Exception as _e:
        logger.debug(f"analyze_query_result 실패: {_e}")

    _chart_type = _analysis.chart_type if _analysis else "none"
    _x_col = _analysis.x_col if _analysis else None
    _y_col = _analysis.y_col if _analysis else None
    _agg_label = _analysis.agg_label if _analysis else ""

    # ── 탭: 테이블 / 차트 / AI 해석 ──────────────────
    tab_table, tab_chart, tab_ai = st.tabs(["📋 데이터", "📈 시각화", "🤖 AI 분석"])

    with tab_table:
        _render_data_table(
            masked_rows,
            masked_cols,
            elapsed_ms,
            masked_columns=pii_list if has_pii else None,
        )

    with tab_chart:
        # ── [v1.3] AI 자동 집계 + 사용자 정의 탭 ──────────────────
        from ui.data_dashboard import _render_custom_chart_builder, _draw_chart_figure

        _sqld_hash = str(abs(hash(sql)) % 99999)

        _vtab_auto, _vtab_custom = st.tabs(["🤖 AI 자동 집계", "🎛️ 사용자 정의"])

        with _vtab_auto:
            # KPI / GRID / CHART 직접 렌더 (셀렉터 없이 _draw_chart_figure 사용)
            if _analysis and _analysis.is_kpi:
                _render_kpi_cards(masked_rows, masked_cols, agg_label=_agg_label)
            elif _analysis and _analysis.is_grid:
                _agg_ct = _analysis.agg_chart_type
                _agg_rows = _analysis.chart_rows or []
                _agg_cols = _analysis.chart_cols or []
                _agg_x = _analysis.agg_chart_x
                _agg_y = _analysis.agg_chart_y
                if (
                    _agg_ct not in ("none", "", None)
                    and _agg_rows
                    and _agg_x
                    and _agg_y
                ):
                    _agg_dict = (
                        _agg_rows
                        if isinstance(_agg_rows[0], dict)
                        else [dict(zip(_agg_cols, r)) for r in _agg_rows]
                    )
                    import pandas as _pd_s

                    _s_df = _pd_s.DataFrame(_agg_dict, columns=_agg_cols)
                    from ui.theme import UITheme as _T

                    _s_fig = _draw_chart_figure(
                        _s_df,
                        _agg_ct,
                        _agg_x,
                        _agg_y,
                        [_T.PRIMARY, "#22c55e", "#f59e0b", "#ef4444"],
                    )
                    if _s_fig:
                        st.plotly_chart(
                            _s_fig,
                            use_container_width=True,
                            key=f"plotly_sqld_summary_{_sqld_hash}",
                        )
                    else:
                        st.info(
                            "리스트 데이터입니다. 집계 쿼리 작성 시 차트가 표시됩니다."
                        )
                else:
                    st.info(
                        "리스트 데이터입니다. '사용자 정의' 탭에서 원하는 축을 선택하세요."
                    )
            elif _chart_type not in ("none", CHART_GRID, CHART_KPI):
                _cr = (
                    _analysis.chart_rows
                    if (_analysis and _analysis.chart_rows)
                    else dict_rows
                )
                _cc = (
                    _analysis.chart_cols
                    if (_analysis and _analysis.chart_cols)
                    else masked_cols
                )
                _cx = _x_col
                _cy = _y_col
                if _cr and _cx and _cy:
                    import pandas as _pd_m

                    _m_df = _pd_m.DataFrame(_cr, columns=_cc)
                    from ui.theme import UITheme as _T2

                    _m_fig = _draw_chart_figure(
                        _m_df,
                        _chart_type,
                        _cx,
                        _cy,
                        [_T2.PRIMARY, "#22c55e", "#f59e0b", "#ef4444"],
                    )
                    if _m_fig:
                        st.plotly_chart(
                            _m_fig,
                            use_container_width=True,
                            key=f"plotly_sqld_main_{_sqld_hash}",
                        )
                    else:
                        st.info("사용자 정의 탭에서 차트를 설정해 주세요.")
                else:
                    st.info("집계/그룹 쿼리 작성 시 차트가 자동 생성됩니다.")
            else:
                st.info(
                    "집계/그룹 쿼리 작성 시 자동 생성됩니다. '사용자 정의' 탭도 사용 가능합니다."
                )

        with _vtab_custom:
            # 원본 데이터로 자유 집계 + AI 요약 포함
            _render_custom_chart_builder(
                raw_rows=dict_rows,
                raw_col_names=masked_cols,
                chart_key=f"sqld_custom_{_sqld_hash}",
            )

    with tab_ai:
        # LLM 안전 데이터 준비 (PII 컬럼 제거)
        try:
            from ui.data_dashboard import _llm_safe_rows

            _llm_res = _llm_safe_rows(masked_rows, masked_cols, table_name=_tbl)
            _safe_rows, _pii_removed = (
                _llm_res if isinstance(_llm_res, tuple) else (_llm_res, [])
            )
            _safe_cols = [
                c
                for c in masked_cols
                if c.upper() not in {p.upper() for p in _pii_removed}
            ]
        except Exception:
            _safe_rows = dict_rows
            _safe_cols = masked_cols
            _pii_removed = []

        _render_ai_explanation(
            question=f"다음 SQL 실행 결과를 분석해줘:\n{sql[:300]}",
            rows=_safe_rows,
            column_names=_safe_cols,
            sql=sql,
            chart_type=_chart_type,
            agg_label=_agg_label,
            pii_removed_cols=_pii_removed,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  스키마 브라우저
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _render_schema_browser() -> None:
    """허용 테이블 목록 + 컬럼 탐색 패널."""
    try:
        from db.oracle_access_config import get_access_config_manager

        mgr = get_access_config_manager()
        cfgs = mgr.get_all_active()
    except Exception:
        st.caption("스키마 정보를 불러올 수 없습니다.")
        return

    if not cfgs:
        st.caption("등록된 테이블이 없습니다.")
        return

    for cfg in cfgs:
        with st.expander(
            f"**{cfg.full_name}**  {cfg.alias or ''}",
            expanded=False,
        ):
            if cfg.table_desc:
                st.caption(cfg.table_desc)

            # 컬럼 클릭 → 에디터에 삽입 버튼
            if cfg.column_descs:
                for col_name, col_desc in list(cfg.column_descs.items())[:20]:
                    _pii = col_name.upper() in {c.upper() for c in cfg.mask_columns}
                    _badge = " 🔒" if _pii else ""
                    _col_a, _col_b = st.columns([3, 1])
                    with _col_a:
                        st.markdown(
                            f'<span style="font-family:monospace;font-size:12px;'
                            f'color:#2563EB;">{col_name}</span>'
                            f'<span style="font-size:11px;color:#6B7280;'
                            f' margin-left:6px;">{col_desc[:40]}{_badge}</span>',
                            unsafe_allow_html=True,
                        )
                    with _col_b:
                        if st.button(
                            "삽입",
                            key=f"sqld_ins_{cfg.table_name}_{col_name}",
                            use_container_width=True,
                        ):
                            _cur = st.session_state.get(_SS_EDITOR_SQL, "")
                            _new_sql = _cur.rstrip() + f"\n    {col_name},"
                            st.session_state[_SS_EDITOR_SQL] = _new_sql
                            # textarea의 value도 동기화 (key로 직접 접근)
                            st.session_state["sqld_textarea"] = _new_sql
                            st.rerun()

                # 전체 컬럼 수 표시
                if len(cfg.column_descs) > 20:
                    st.caption(f"+ {len(cfg.column_descs) - 20}개 컬럼 더 있음")

            # FROM 절 복사 버튼
            if st.button(
                f"FROM {cfg.full_name} 삽입",
                key=f"sqld_from_{cfg.table_name}",
                use_container_width=True,
            ):
                _cur = st.session_state.get(_SS_EDITOR_SQL, "")
                if "FROM" not in _cur.upper():
                    _new_sql = _cur.rstrip() + f"\nFROM {cfg.full_name}"
                else:
                    _new_sql = _cur.rstrip() + f"\n-- FROM {cfg.full_name}"
                st.session_state[_SS_EDITOR_SQL] = _new_sql
                st.session_state["sqld_textarea"] = _new_sql
                st.rerun()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  히스토리 + 즐겨찾기 패널
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _render_history_panel() -> None:
    """쿼리 히스토리 패널."""
    hist = st.session_state.get(_SS_HISTORY, [])
    if not hist:
        st.caption("실행된 쿼리가 없습니다.")
        return

    for i, entry in enumerate(hist):
        _c1, _c2 = st.columns([4, 1])
        with _c1:
            st.markdown(
                f'<div class="sqld-hist-item">'
                f'<div class="sqld-hist-time">'
                f"{entry['time']}  ·  {entry['elapsed']}  ·  {entry['rows']}행"
                f"</div>"
                f'<div class="sqld-hist-sql">{entry["preview"]}</div>'
                f"</div>",
                unsafe_allow_html=True,
            )
        with _c2:
            if st.button(
                "불러오기", key=f"sqld_hist_load_{i}", use_container_width=True
            ):
                st.session_state[_SS_EDITOR_SQL] = entry["sql"]
                st.session_state["sqld_textarea"] = entry["sql"]
                st.rerun()


def _render_favorites_panel() -> None:
    """즐겨찾기 패널."""
    favs = st.session_state.get(_SS_FAVORITES, [])

    # 현재 SQL 저장
    with st.form("sqld_fav_form"):
        _name = st.text_input(
            "이름",
            placeholder="쿼리 이름 (예: 응급환자 일별 집계)",
            label_visibility="collapsed",
        )
        if st.form_submit_button("현재 SQL 즐겨찾기 저장", use_container_width=True):
            _sql = st.session_state.get(_SS_EDITOR_SQL, "").strip()
            if _sql and _name:
                favs.append({"name": _name, "sql": _sql})
                st.session_state[_SS_FAVORITES] = favs
                st.success(f"저장: {_name}")
                st.rerun()
            else:
                st.warning("이름과 SQL을 모두 입력해주세요.")

    if favs:
        st.divider()
        for i, fav in enumerate(favs):
            _fa, _fb, _fc = st.columns([4, 1, 1])
            with _fa:
                st.markdown(
                    f'<div style="font-size:12px;font-weight:600;'
                    f'color:#374151;">⭐ {fav["name"]}</div>'
                    f'<div style="font-size:10px;color:#9CA3AF;font-family:monospace;">'
                    f"{fav['sql'][:50]}...</div>",
                    unsafe_allow_html=True,
                )
            with _fb:
                if st.button(
                    "불러오기", key=f"sqld_fav_load_{i}", use_container_width=True
                ):
                    st.session_state[_SS_EDITOR_SQL] = fav["sql"]
                    st.session_state["sqld_textarea"] = fav["sql"]
                    st.rerun()
            with _fc:
                if st.button("삭제", key=f"sqld_fav_del_{i}", use_container_width=True):
                    favs.pop(i)
                    st.session_state[_SS_FAVORITES] = favs
                    st.rerun()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  메인 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_sql_dashboard(admin_user: str = "admin") -> None:
    """
    SQL 대시보드 전체 렌더링.

    [레이아웃]
    ┌────────────────────────────────┬──────────────────┐
    │  왼쪽 (에디터 + 결과)  (3/4)   │  오른쪽 패널 (1/4) │
    │  ┌──────────────────────────┐  │  스키마 브라우저   │
    │  │  SQL 에디터              │  │  히스토리          │
    │  └──────────────────────────┘  │  즐겨찾기          │
    │  [실행]  [초기화]  [즐겨찾기]   │                   │
    │  ─────────────────────────────  │                   │
    │  [📋 데이터] [📈 시각화] [🤖AI] │                   │
    └────────────────────────────────┴──────────────────┘

    Args:
        admin_user: 현재 관리자 식별자 (감사 로그용)
    """
    # 권한 재확인 (이중 방어)
    if st.session_state.get("role") != "admin":
        st.error("🔒 관리자 인증이 필요합니다.")
        return

    _init_state()
    st.markdown(_CSS, unsafe_allow_html=True)

    # ── 페이지 제목 ─────────────────────────────────────
    _hcol1, _hcol2 = st.columns([5, 1])
    with _hcol1:
        st.markdown(
            f'<h2 style="margin:0;font-size:1.4rem;font-weight:800;'
            f'color:#111827;">🗄️ SQL 대시보드</h2>'
            f'<p style="margin:0.15rem 0 0;font-size:12px;color:#6B7280;">'
            f"전산팀 직접 쿼리 실행 도구 · SELECT 전용 · 실행 로그 기록</p>",
            unsafe_allow_html=True,
        )
    with _hcol2:
        if st.button(
            "← 메인",
            key="sqld_back_main",
        ):
            st.session_state["active_page"] = "main"
            st.rerun()
        # 데이터 분석 모드로 전환 버튼
        if st.button(
            "데이터 분석",
            key="sqld_goto_da",
        ):
            st.session_state["active_page"] = "main"
            st.session_state["search_mode"] = "data_analysis"
            st.rerun()

    st.markdown('<div style="height:0.6rem;"></div>', unsafe_allow_html=True)

    # ── 2단 레이아웃: 에디터(3) / 패널(1) ──────────────
    col_editor, col_panel = st.columns([3, 1], gap="medium")

    with col_panel:
        _tab_schema, _tab_hist, _tab_fav = st.tabs(["스키마", "히스토리", "즐겨찾기"])
        with _tab_schema:
            _render_schema_browser()
        with _tab_hist:
            _render_history_panel()
        with _tab_fav:
            _render_favorites_panel()

    with col_editor:
        # ── SQL 에디터 ─────────────────────────────────
        st.markdown(
            '<div style="font-size:12px;font-weight:600;color:#374151;'
            'margin-bottom:0.3rem;">SQL 에디터</div>',
            unsafe_allow_html=True,
        )

        sql_input = st.text_area(
            label="SQL",
            value=st.session_state.get(_SS_EDITOR_SQL, _DEFAULT_SQL),
            height=260,
            key="sqld_textarea",
            label_visibility="collapsed",
            placeholder="SELECT ... FROM ... WHERE ...",
        )
        # 에디터 값 동기화
        st.session_state[_SS_EDITOR_SQL] = sql_input

        # ── 액션 버튼 행 ───────────────────────────────
        _b1, _b2, _b3, _b4 = st.columns([3, 1.5, 1.5, 1.5])

        with _b1:
            st.markdown('<div class="sqld-run-btn">', unsafe_allow_html=True)
            _run = st.button(
                "▶  실행  (Ctrl+Enter)",
                key="sqld_run",
                type="primary",
                use_container_width=True,
            )
            st.markdown("</div>", unsafe_allow_html=True)

        with _b2:
            if st.button("초기화", key="sqld_clear", use_container_width=True):
                st.session_state[_SS_EDITOR_SQL] = _DEFAULT_SQL
                st.rerun()

        with _b3:
            if st.button(
                "결과 지우기", key="sqld_clear_result", use_container_width=True
            ):
                st.session_state[_SS_LAST_RESULT] = None
                st.rerun()

        with _b4:
            # SQL 복사 (클립보드 — JS)
            st.download_button(
                "SQL 다운로드",
                data=sql_input,
                file_name=f"query_{datetime.now():%Y%m%d_%H%M%S}.sql",
                mime="text/plain",
                key="sqld_download",
                use_container_width=True,
            )

        # ── 실행 처리 ──────────────────────────────────
        if _run:
            if not sql_input.strip():
                st.warning("SQL을 입력해주세요.")
            else:
                with st.spinner("실행 중..."):
                    ok, rows, col_names, err, elapsed_ms = _execute_sql(
                        sql_input,
                        admin_user=admin_user,
                    )

                if ok:
                    _add_history(sql_input, elapsed_ms, len(rows))
                    st.session_state[_SS_LAST_RESULT] = {
                        "rows": rows,
                        "col_names": col_names,
                        "elapsed": elapsed_ms,
                        "sql": sql_input,
                    }
                    st.rerun()
                else:
                    st.error(f"❌ 실행 오류: {err}")

        # ── 결과 렌더링 ────────────────────────────────
        _last = st.session_state.get(_SS_LAST_RESULT)
        if _last:
            st.divider()
            _render_result(
                rows=_last["rows"],
                col_names=_last["col_names"],
                elapsed_ms=_last["elapsed"],
                sql=_last["sql"],
                admin_user=admin_user,
            )
