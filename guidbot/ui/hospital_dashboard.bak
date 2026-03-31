"""
ui/hospital_dashboard.py  ─  병원 현황판 대시보드 v3.0 Production
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v3.0 상용 품질 개선 — 2026-03-27]

  ① Circuit Breaker 패턴
     · DB 3회 연속 실패 시 30초 차단 → 연쇄 대기 방지
     · 30초 후 자동 Half-Open → 재시도

  ② Thundering Herd 방지 (Staggered TTL)
     · 세션별 jitter(0~TTL×10%) 추가 → 만료 시점 분산
     · 20명 동시 접속 시 캐시 만료 폭발 방지

  ③ Thread-safe 쿼리 카운터
     · _QUERY_FAIL_COUNT Lock 보호 → Race Condition 제거

  ④ Oracle 연결 체크 개선
     · 성공: 5분 캐시 / 실패: 1분 후 자동 재체크
     · 이전: 세션 동안 False 고정 문제 해결

  ⑤ 안전한 타입 변환
     · _safe_int / _safe_float 헬퍼 — None/빈값/오류 방어
     · 38개 int(r.get(...) or 0) 패턴 → _safe_int() 교체

  ⑥ 성별 필터 완전 수정
     · _norm_sex() 모듈 레벨 이동 (이전: 루프 내 재정의)
     · V_WARD_ROOM_DETAIL 단일 소스로 통일
     · 폴백 버그 완전 제거

  ⑦ LLM 스트리밍 최적화
     · str 연결 O(n²) → list+join O(n)
     · 시스템 프롬프트 4000자 제한

  ⑧ 새로고침 개선
     · jitter 리셋 + Oracle 만료 처리
     · 30초 쿨다운 유지

[v2.5 → v3.0 마이그레이션 노트]
  - _QUERY_FAIL_LOCK 신규 전역 변수
  - _QUERY_CIRCUIT_OFF 신규 전역 변수
  - oracle_ok_expire 신규 session_state 키
  - _ttl_jitter_{key} 신규 session_state 키 (자동 생성/삭제)
  - _safe_int / _safe_float 신규 헬퍼 함수
  - _norm_sex 위치: 루프 내 → 모듈 레벨
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v2.2 차트 선택기 통합]
  ① 진료과별 재원 구성   : 도넛(기본) / 가로막대 / 트리맵
  ② 주간 추이 7일        : 테이블(기본) / 라인 / 영역 / 막대
  ③ 병동별 당일 현황     : 테이블(기본) / 가로막대 / 히트맵
  ④ 최근 7일 주상병 분포 : 파이(기본) / 가로막대 / 트리맵
  ⑤ 금일 vs 전일 주상병  : 중첩막대(기본) / 그룹막대 / 수평막대
  - pill 버튼 UI: 각 섹션 헤더 우측에 소형 선택 버튼 표시
  - session_state 저장: 병동 전환·새로고침 후에도 선택 유지

[v2.1 성능 개선]
  ① Oracle ping 중복 제거  : render마다 test_connection() 2회 → 세션당 1회
  ② 쿼리 캐싱 도입        : _query_cached (ttl=120s) — 버튼 클릭 재조회 방지
  ③ ward_room_detail 조건부: 패널 열릴 때만 대용량 쿼리 실행
  ④ KPI 이중 계산 제거    : bed_detail 합계를 필터 적용 후 1회만 계산
  ⑤ op_stat 필터 불일치   : _ward_surg 집계를 op_stat_f(필터 후)로 수정
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import streamlit as st

# [차트 엔진 체크] Plotly 및 Pandas 가용성 확인
try:
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

import os as _os
import sys

_PROJECT_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 병동 대시보드 사용자 로그 + 모니터링
try:
    from utils.dashboard_monitor import get_dash_monitor as _get_dash_monitor
    _DASH_MON = _get_dash_monitor()
except Exception:
    class _NullMon:
        def log_action(self, *a, **k): pass
        def log_llm_query(self, *a, **k): pass
        def log_query_fail(self, *a, **k): pass
        def log_query_time(self, *a, **k): pass
    _DASH_MON = _NullMon()

try:
    from utils.logger import get_logger as _get_logger
    from config.settings import settings as _settings
    logger = _get_logger(__name__, log_dir=_settings.log_dir)
except Exception:
    import logging as _logging
    logger = _logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [v2.2] 차트 선택기 — 외부 모듈 없이 이 파일에 직접 내장
#
# 외부 import에 의존하면 경로 문제로 선택기가 통째로 숨겨지는 문제 발생.
# chart_selector / chart_renderers 로직을 인라인으로 구현하여
# 어떤 실행 환경에서도 항상 선택기가 표시되도록 보장합니다.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 섹션별 차트 옵션 정의
# 구조: {section_key: {"options": [(value, label), ...], "default": str}}
_CHART_OPTIONS: Dict[str, Dict] = {
    # ── 진료과별 재원 구성 ─────────────────────────────────────────
    "dept_stay": {
        "options": [
            ("donut",   "도넛"),      # 기본: 중앙 총계 강조
            ("bar_h",   "막대"),      # 수치 비교
            ("treemap", "트리맵"),    # 면적 비율
        ],
        "default": "donut",
    },
    # ── 주간 추이 7일 ──────────────────────────────────────────────
    "weekly_trend": {
        "options": [
            ("table", "테이블"),      # 기본: 기존 HTML 표
            ("line",  "라인"),        # 추세선
            ("area",  "영역"),        # 볼륨 강조
            ("bar",   "막대"),        # 일별 비교
        ],
        "default": "table",
    },
    # ── 병동별 당일 현황 ───────────────────────────────────────────
    "ward_detail": {
        "options": [
            ("table",   "테이블"),    # 기본: 기존 풍부한 HTML 표
            ("bar_h",   "막대"),      # 가동률 비교
            ("heatmap", "히트맵"),   # 병동×지표 매트릭스
        ],
        "default": "table",
    },
    # ── 최근 7일 주상병 분포 ───────────────────────────────────────
    "dx_7day": {
        "options": [
            ("pie",     "파이"),      # 기본: 비율 파악
            ("bar_h",   "막대"),      # 상병명 가독성
            ("treemap", "트리맵"),    # 빈도 강조
        ],
        "default": "pie",
    },
    # ── 금일 vs 전일 주상병 비교 ───────────────────────────────────
    "dx_compare": {
        "options": [
            ("overlay", "중첩막대"),  # 기본: 투명도 중첩
            ("grouped", "그룹막대"),  # 나란히 비교
            ("bar_h",   "수평막대"),  # 상병명 공간 여유
        ],
        "default": "overlay",
    },
}

# session_state 키 접두사 (전역 병동 선택기 키와 충돌 방지)
_CT_PREFIX = "ct__"


def _get_chart_type(section_key: str) -> str:
    """
    특정 섹션의 현재 선택된 차트 타입을 session_state에서 읽어 반환합니다.
    저장된 값이 없으면 해당 섹션의 기본값을 반환합니다.
    """
    cfg = _CHART_OPTIONS.get(section_key, {})
    return st.session_state.get(_CT_PREFIX + section_key, cfg.get("default", ""))


def _chart_selector(section_key: str, title: str, subtitle: str = "") -> str:
    """
    섹션 헤더 한 줄에 제목(좌) + pill 선택기(우)를 렌더링합니다.
    반환값: 현재 선택된 chart_type 문자열

    [개선 사항 v2.3]
    - 라디오 circle 완전 제거: label > div (BaseWeb indicator) CSS 숨김 추가
    - label_visibility="hidden" 사용 → "collapsed"보다 공간 제거에 더 안정적
    - st.columns 비율 조정 → 선택기가 충분한 공간 확보
    """
    cfg = _CHART_OPTIONS.get(section_key)
    if not cfg:
        return ""

    options   = cfg["options"]
    state_key = _CT_PREFIX + section_key
    current   = st.session_state.get(state_key, cfg["default"])

    labels = [lbl for _, lbl in options]
    values = [val for val, _ in options]
    try:
        idx = values.index(current)
    except ValueError:
        idx = 0

    # ── 섹션 헤더: 제목(좌) + pill(우) 완전 한 줄 ─────────────────────
    # 핵심: st.columns 로 두 영역 분리 → pill이 제목 옆에 나란히 배치
    # 옵션 수에 따라 pill 컬럼 너비 동적 조정
    _n_opts = len(labels)  # 옵션 개수
    # 3개=35%, 4개=45%, 2개=25% 공간 할당
    _pill_w  = min(45, max(25, _n_opts * 11))
    _title_w = 100 - _pill_w
    _col_t, _col_p = st.columns([_title_w, _pill_w], gap="small")

    with _col_t:
        _sub = (
            f'<span style="font-size:10px;color:#94A3B8;margin-left:5px;">'
            f'{subtitle}</span>'
        ) if subtitle else ""
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:5px;min-height:28px;">'  
            f'<span style="width:3px;height:13px;background:linear-gradient(180deg,#1E40AF,#3B82F6);'
            f'border-radius:2px;display:inline-block;flex-shrink:0;"></span>'
            f'<span style="font-size:11.5px;font-weight:700;color:#0F172A;white-space:nowrap;">'
            f'{title}{_sub}</span></div>',
            unsafe_allow_html=True,
        )

    with _col_p:
        kw: dict = dict(
            label="​",
            options=labels,
            index=idx,
            horizontal=True,
            key=f"radio_ct_{section_key}",
        )
        try:
            kw["label_visibility"] = "hidden"
            selected_label = st.radio(**kw)
        except TypeError:
            kw.pop("label_visibility", None)
            selected_label = st.radio(**kw)

    # 섹션 전체 하단 구분선
    st.markdown(
        '<div style="height:1px;background:#F1F5F9;margin:2px 0 6px;"></div>',
        unsafe_allow_html=True,
    )
    selected_value = values[labels.index(selected_label)]
    st.session_state[state_key] = selected_value
    return st.session_state.get(state_key, cfg["default"])

# ── 색상 체계 v4.0 ──────────────────────────────────────────────────
C = {
    "bg": "#F8FAFC",
    "card": "#FFFFFF",
    "surface": "#F1F5F9",
    "surface_alt": "#E2E8F0",
    "border": "#CBD5E1",
    "border_light": "#E2E8F0",
    "divider": "#F1F5F9",
    "t1": "#0F172A",
    "t2": "#334155",
    "t3": "#64748B",
    "t4": "#94A3B8",
    "t5": "#CBD5E1",
    "semantic_up": "#EF4444",
    "semantic_dn": "#3B82F6",
    "ok": "#059669",
    "ok_bg": "#D1FAE5",
    "ok_bd": "#6EE7B7",
    "ok_text": "#047857",
    "warn": "#F59E0B",
    "warn_bg": "#FFFBEB",
    "warn_bd": "#FCD34D",
    "warn_text": "#92400E",
    "danger": "#DC2626",
    "err_bg": "#FEE2E2",
    "err_bd": "#FCA5A5",
    "danger_text": "#991B1B",
    "chart1": "#1E40AF",
    "chart2": "#2563EB",
    "chart3": "#3B82F6",
    "chart4": "#059669",
    "chart5": "#0D9488",
    "chart6": "#F59E0B",
    "chart7": "#EF4444",
    "chart8": "#8B5CF6",
    "primary": "#1E40AF",
    "primary_light": "#DBEAFE",
    "primary_text": "#1D4ED8",
    "accent": "#7C3AED",
    "blue": "#3B82F6",
    "green": "#059669",
    "amber": "#F59E0B",
    "coral": "#DC2626",
    "sky": "#0EA5E9",
    "indigo": "#4F46E5",
    "purple": "#8B5CF6",
    "navy": "#1E40AF",
    "navy2": "#1E3A8A",
    "navy3": "#1E3A8A",
    "blue_bg": "#DBEAFE",
    "sky_bg": "#E0F2FE",
    "amber_bg": "#FFFBEB",
    "purple_bg": "#F3E8FF",
    "green_bg": "#D1FAE5",
    "coral_bg": "#FEE2E2",
    "amber_bd": "#FCD34D",
    "purple_bd": "#E9D5FF",
    "t_heading": "#0F172A",
}

# ── Oracle VIEW 쿼리 딕셔너리 ────────────────────────────────────────
QUERIES: Dict[str, str] = {
    "ward_dept_stay":      "SELECT * FROM JAIN_WM.V_WARD_DEPT_STAY ORDER BY 재원수 DESC",
    "ward_bed_detail":     "SELECT * FROM JAIN_WM.V_WARD_BED_DETAIL ORDER BY 병동명",
    "ward_op_stat":        "SELECT * FROM JAIN_WM.V_WARD_OP_STAT ORDER BY 수술건수 DESC",
    "ward_kpi_trend":      "SELECT * FROM JAIN_WM.V_WARD_KPI_TREND ORDER BY 기준일",
    "ward_yesterday":      "SELECT * FROM JAIN_WM.V_WARD_YESTERDAY ORDER BY 병동명",
    "ward_dx_today":       "SELECT * FROM JAIN_WM.V_WARD_DX_TODAY ORDER BY 기준일 DESC, 환자수 DESC",
    "ward_dx_trend":       "SELECT * FROM JAIN_WM.V_WARD_DX_TREND ORDER BY 기준일, 환자수 DESC",
    "admit_candidates":    "SELECT * FROM JAIN_WM.V_ADMIT_CANDIDATES ORDER BY 진료과명, 성별",
    "ward_room_detail":    "SELECT * FROM JAIN_WM.V_WARD_ROOM_DETAIL ORDER BY 병동명, 병실번호",
    "finance_kpi":         "SELECT * FROM JAIN_WM.V_FINANCE_TODAY WHERE ROWNUM = 1",
    "finance_overdue":     "SELECT * FROM JAIN_WM.V_OVERDUE_STAT",
    "finance_by_insurance":"SELECT * FROM JAIN_WM.V_FINANCE_BY_INS",
    "opd_kpi":             "SELECT * FROM JAIN_WM.V_OPD_KPI WHERE ROWNUM = 1",
    "opd_by_dept":         "SELECT * FROM JAIN_WM.V_OPD_BY_DEPT ORDER BY 환자수 DESC",
    "opd_hourly":          "SELECT * FROM JAIN_WM.V_OPD_HOURLY_STAT ORDER BY 시간대",
    "opd_noshow":          "SELECT * FROM JAIN_WM.V_NOSHOW_STAT WHERE ROWNUM = 1",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 쿼리 실행 + 2분 TTL 캐시
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 쿼리 실패 카운터 — Thread-safe ─────────────────────────────────
# Streamlit은 다중 스레드 → 동시 접속 시 Race Condition 방지를 위해 Lock 사용
_QUERY_FAIL_COUNT: Dict[str, int] = {}  # 연속 실패 카운터 (Circuit Breaker용)
_QUERY_FAIL_LOCK   = threading.Lock()
_QUERY_CIRCUIT_OFF: Dict[str, float] = {}  # Circuit Breaker: 키 → 복구 시각
_CIRCUIT_TIMEOUT   = 30.0  # 30초 후 자동 복구 시도


def _query(key: str) -> List[Dict[str, Any]]:
    """
    Oracle 쿼리 실행 (Thread-safe + Circuit Breaker)

    [Circuit Breaker 패턴]
    · 3회 연속 실패 시 30초 동안 DB 조회 차단 → 빈 리스트 즉시 반환
    · 30초 후 자동으로 Half-Open 상태 → 다시 시도
    · 성공 시 카운터 초기화
    → 효과: DB 장애 시 20명 사용자에게 연쇄 대기 없이 빈 카드만 표시

    [Thread Safety]
    · _QUERY_FAIL_COUNT 는 Lock 으로 보호
    · Streamlit @cache_data 는 자체 Lock 보유 (이중 보호)
    """
    # ── Circuit Breaker: 차단 중인지 확인 ──────────────────────
    _now = time.time()
    _off_until = _QUERY_CIRCUIT_OFF.get(key, 0)
    if _now < _off_until:
        # 차단 중 — 남은 시간 로그 (1분에 1회만)
        _rem = int(_off_until - _now)
        if _rem % 10 == 0:
            logger.debug(f"[Circuit OPEN] {key} — {_rem}초 후 재시도")
        return []

    _t0 = time.perf_counter()
    try:
        from db.oracle_client import execute_query
        rows = execute_query(QUERIES[key])
        _elapsed_ms = int((time.perf_counter() - _t0) * 1000)

        # ── 성공: 카운터 초기화 ──────────────────────────────
        with _QUERY_FAIL_LOCK:
            _QUERY_FAIL_COUNT.pop(key, None)
            _QUERY_CIRCUIT_OFF.pop(key, None)

        if _elapsed_ms > 500:
            logger.warning(f"[SLOW] {key}: {_elapsed_ms}ms")
        return rows if rows else []

    except Exception as e:
        _elapsed_ms = int((time.perf_counter() - _t0) * 1000)
        with _QUERY_FAIL_LOCK:
            _QUERY_FAIL_COUNT[key] = _QUERY_FAIL_COUNT.get(key, 0) + 1
            fail_cnt = _QUERY_FAIL_COUNT[key]
            if fail_cnt >= 3:
                # Circuit Breaker OPEN: 30초 차단
                _QUERY_CIRCUIT_OFF[key] = time.time() + _CIRCUIT_TIMEOUT
                logger.error(
                    f"[Circuit OPEN] {key}: {fail_cnt}회 실패 → {_CIRCUIT_TIMEOUT:.0f}초 차단. "
                    f"{type(e).__name__}: {e}"
                )
            else:
                logger.warning(f"[Query FAIL] {key} ({fail_cnt}/3): {type(e).__name__}: {e}")
        # 모니터링 로그
        _DASH_MON.log_query_fail(key, error=f"{type(e).__name__}: {e}")
        return []




def _norm_sex(val: str) -> str:
    """
    DB 성별 값 정규화 — 한글/영문 모두 처리
    DB 값: '여'/'남' (한글) 또는 'F'/'M' (영문)
    반환: 'F' (여성) / 'M' (남성) / '' (미상)
    """
    v = str(val).strip()
    if v in ("F", "f", "여"): return "F"
    if v in ("M", "m", "남"): return "M"
    return ""



def _safe_int(val: Any, default: int = 0) -> int:
    """None/빈문자열/오류 안전한 int 변환."""
    try:
        return int(val or default)
    except (ValueError, TypeError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    """None/빈문자열/오류 안전한 float 변환."""
    try:
        return float(val or default)
    except (ValueError, TypeError):
        return default


# ── 성별 정규화 매핑 (모듈 레벨 상수 — dict lookup O(1)) ──────────────
# DB 성별 값: '여'/'남' 한글 또는 'F'/'M' 영문 혼재 가능
# dict lookup이 if/elif 분기보다 평균 2배 빠름
_SEX_NORM: dict[str, str] = {
    "여": "F", "F": "F", "f": "F",
    "남": "M", "M": "M", "m": "M",
}

def _norm_sex(val: str) -> str:
    """성별 값을 'F'(여) 또는 'M'(남) 코드로 정규화. 미인식 값은 빈 문자열."""
    return _SEX_NORM.get(str(val).strip(), "")


# 쿼리별 TTL 정책
# 변동 빠름(KPI/입퇴원): 30초 — 실시간성 중요
# 변동 중간(병실/예약):   60초 — 1분 이내 반영
# 변동 느림(추이/주상병): 300초 — 5분 집계 데이터
_TTL_MAP: Dict[str, int] = {
    "ward_bed_detail":      30,   # KPI 핵심 — 가동률/재원수
    "ward_op_stat":         30,   # 수술 현황
    "ward_yesterday":       30,   # 전일 비교
    "ward_dept_stay":       60,   # 진료과별 재원
    "admit_candidates":     60,   # 익일 예약
    "ward_room_detail":     60,   # 병실 상태
    "ward_kpi_trend":      300,   # 7일 추이
    "ward_dx_today":       300,   # 주상병
    "ward_dx_trend":       300,   # 주상병 추이
    "finance_kpi":         120,
    "finance_overdue":     120,
    "finance_by_insurance":120,
    "opd_kpi":             120,
    "opd_by_dept":         120,
    "opd_hourly":          120,
    "opd_noshow":          120,
}
_DEFAULT_TTL = 120

@st.cache_data(show_spinner=False)
def _query_cached_ttl(key: str, _cache_ts: int) -> List[Dict[str, Any]]:
    """키별 TTL 캐시. _cache_ts = int(time.time() / ttl) 로 버킷 분할."""
    return _query(key)


def _qc(key: str) -> List[Dict[str, Any]]:
    """
    키별 TTL 캐시 래퍼 — Thundering Herd 방지 포함

    [Thundering Herd 문제]
    bucket = int(time.time() / ttl) 방식은 TTL 경계에서
    모든 세션이 동시에 캐시 만료 → 20명이 동시에 DB 조회

    [해결: Staggered TTL]
    세션별 고유 jitter(0 ~ ttl*0.1)를 현재 시각에 빼서
    버킷 경계를 세션마다 다르게 만듦 → 만료 시점 분산
    jitter는 session_state에 저장 → 세션 내 일관성 유지
    """
    ttl = _TTL_MAP.get(key, _DEFAULT_TTL)
    # 세션별 jitter: 0 ~ ttl*10% 범위, 세션당 1회 생성하여 고정
    _jitter_key = f"_ttl_jitter_{key}"
    if _jitter_key not in st.session_state:
        import random as _r
        st.session_state[_jitter_key] = _r.uniform(0, ttl * 0.10)
    jitter = st.session_state[_jitter_key]
    # jitter 만큼 시각을 뒤로 밀어 버킷 경계 분산
    bucket = int((time.time() - jitter) / ttl)
    return _query_cached_ttl(key, bucket)


# ── UI 공용 컴포넌트 ─────────────────────────────────────────────────

def _kpi_card(
    label: str,
    value: str,
    unit: str,
    sub: str,
    color: str,
    col_obj=None,
    delta: str = "",
    bar_pct: float = 0,
) -> None:
    tgt = col_obj if col_obj else st
    if "▲" in delta:
        _dc_cls = "kpi-delta-up"
    elif "▼" in delta:
        _dc_cls = "kpi-delta-dn"
    else:
        _dc_cls = "kpi-delta-nt"
    _delta_html = (f'<span class="{_dc_cls}">{delta}</span>') if delta else ""
    _bar_html = (
        f'<div class="kpi-bar-bg">'
        f'<div class="kpi-bar-fill" style="width:{min(100, bar_pct):.1f}%;background:{color};"></div>'
        f"</div>"
        if bar_pct > 0
        else '<div style="height:3px;margin:4px 0 3px;"></div>'
    )
    tgt.markdown(
        f'<div class="kpi-card">'
        f'<div class="kpi-label">{label}</div>'
        f'<div style="display:flex;align-items:baseline;gap:3px;margin-bottom:2px;">'
        f'<span class="kpi-value" style="color:{color};">{value}</span>'
        f'<span class="kpi-unit">{unit}</span>'
        f"</div>"
        f"{_bar_html}"
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<span style="font-size:13px;color:#64748B;font-weight:500;">{sub}</span>'
        f'<span style="font-size:15px;font-weight:800;letter-spacing:-0.02em;line-height:1;">{_delta_html}</span>'
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _section_title(title: str, badge: str = "") -> None:
    badge_html = (
        f'<span style="font-size:10px;background:{C["sky_bg"]};color:{C["sky"]};'
        f"border:1px solid rgba(56,189,248,0.3);border-radius:3px;"
        f'padding:1px 7px;font-weight:600;margin-left:8px;">{badge}</span>'
        if badge
        else ""
    )
    st.markdown(
        f'<div style="font-size:11px;font-weight:700;color:{C["t2"]};'
        f'text-transform:uppercase;letter-spacing:.06em;margin:18px 0 8px;">'
        f"{title}{badge_html}</div>",
        unsafe_allow_html=True,
    )


# ── CSS ─────────────────────────────────────────────────────────────
_WARD_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/variable/pretendardvariable.css');
*, *::before, *::after { box-sizing: border-box; }
.main,
[data-testid="stAppViewContainer"],
[data-testid="stMarkdownContainer"],
[data-testid="stText"] {
  font-family: 'Pretendard Variable', 'Pretendard', 'Malgun Gothic', -apple-system, sans-serif !important;
  font-size: 14px !important;
  color: #333333;
}
[data-testid="stAppViewContainer"] > .main {
  padding-top: 0.4rem !important;
  padding-left: 0.75rem !important;
  padding-right: 0.75rem !important;
}
/* ── KPI 카드 — 주간 추이 카드와 동일 높이 ─────────────────── */
.kpi-card {
  background: #FFFFFF;
  border: 1px solid #E8EDF2;
  border-radius: 12px;
  padding: 14px 16px;
  height: 100%;
  min-height: 110px;
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  box-shadow: 0 1px 4px rgba(15,23,42,0.06);
  transition: box-shadow 100ms ease;
}
.kpi-card:hover { box-shadow: 0 3px 12px rgba(15,23,42,0.09); }

/* ── KPI 타이포그래피 ───────────────────────────────────────── */
.kpi-label { font-size:10.5px; font-weight:700; color:#64748B; text-transform:uppercase; letter-spacing:.12em; margin-bottom:4px; }
.kpi-value { font-size:30px; font-weight:800; color:#0F172A; font-variant-numeric:tabular-nums; line-height:1; letter-spacing:-0.03em; }
.kpi-unit  { font-size:13px; color:#64748B; font-weight:500; margin-left:3px; }
.kpi-sub   { font-size:11.5px; color:#94A3B8; }
.kpi-delta-up { font-size:13px; font-weight:700; color:#16A34A; }
.kpi-delta-dn { font-size:13px; font-weight:700; color:#DC2626; }
.kpi-delta-nt { font-size:12px; font-weight:600; color:#94A3B8; }
.kpi-bar-bg   { height:3px; background:#F1F5F9; border-radius:2px; overflow:hidden; margin:5px 0; }
.kpi-bar-fill { height:100%; border-radius:2px; transition:width 400ms ease; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   KPI 스타일 전파 — 테이블/섹션에서 중요 수치에 적용
   .kpi-num: 굵고 큰 숫자 (테이블 내 핵심 수치)
   .kpi-badge: 상태 배지 (위험/주의/정상)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.kpi-num {
  font-size: 15px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
  line-height: 1;
}
.kpi-badge-danger { background:#FEE2E2; color:#991B1B; border:1px solid #FCA5A5; border-radius:4px; padding:1px 6px; font-size:10px; font-weight:700; }
.kpi-badge-warn   { background:#FEF3C7; color:#92400E; border:1px solid #FCD34D; border-radius:4px; padding:1px 6px; font-size:10px; font-weight:700; }
.kpi-badge-ok     { background:#DCFCE7; color:#15803D; border:1px solid #86EFAC; border-radius:4px; padding:1px 6px; font-size:10px; font-weight:700; }
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   그리드 시스템 v3.0 — 병동 대시보드 전용
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   원칙:
   1. 모든 카드는 .wd-card 클래스 통일
   2. Row 높이는 클래스로 고정 (.wd-row-kpi / .wd-row-chart / .wd-row-free)
   3. 카드 내부 padding 14px 16px 통일
   4. 카드 간 gap = Streamlit gap="small" (약 10px)
   5. 섹션 헤더 스타일 .wd-sec 통일
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ── 기본 카드 ──────────────────────────────────────────────── */
.wd-card {
  background: #FFFFFF;
  border: 1px solid #E8EDF2;
  border-radius: 12px;
  padding: 14px 16px;          /* 모든 카드 동일 내부 여백 */
  box-shadow: 0 1px 4px rgba(15,23,42,0.06);
  height: 100%;                /* Row 내 높이 통일을 위해 100% 사용 */
  transition: box-shadow 100ms ease;
  overflow: hidden;
}
.wd-card:hover {
  box-shadow: 0 3px 12px rgba(15,23,42,0.09);
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Row별 높이 정렬 — KPI ↔ 주간 추이 동일 높이 핵심
   원인: stHorizontalBlock 기본 align-items=flex-start
         → 각 컬럼이 독립적으로 content 높이만큼만 커짐
   해결: .wd-row-kpi 내 stHorizontalBlock = stretch
         → 두 컬럼이 더 높은 쪽에 맞춰 동일 높이
         → .wd-card height:100% 가 실제로 작동
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* 1. KPI Row의 외부 수평 블록: 자식 컬럼들을 같은 높이로 늘림 */
.wd-row-kpi [data-testid="stHorizontalBlock"] {
  align-items: stretch !important;
}

/* 2. KPI / 주간 추이 컬럼 자체를 flex column으로 */
.wd-row-kpi [data-testid="stColumn"] {
  display: flex !important;
  flex-direction: column !important;
}
.wd-row-kpi [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {
  flex: 1 !important;
  display: flex !important;
  flex-direction: column !important;
}

/* 3. 주간 추이 카드: 컬럼 전체 높이 채움 */
.wd-row-kpi [data-testid="stColumn"]:last-child .wd-card {
  height: 100% !important;
  flex: 1 !important;
  display: flex !important;
  flex-direction: column !important;
}

/* 4. KPI 컬럼 내부: 두 서브 행이 균등 분배 */
.wd-row-kpi [data-testid="stColumn"]:first-child > [data-testid="stVerticalBlock"] {
  justify-content: space-between !important;
}

/* 5. KPI 서브 컬럼 (3×2)의 내부 카드도 꽉 채움 */
.wd-row-kpi [data-testid="stColumn"]:first-child [data-testid="stHorizontalBlock"] {
  align-items: stretch !important;
}
.wd-row-kpi [data-testid="stColumn"]:first-child [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {
  display: flex !important;
  flex-direction: column !important;
}
.wd-row-kpi .kpi-card {
  flex: 1 !important;
  height: auto !important;
}

/* 6. 차트 Row */
.wd-row-chart [data-testid="stHorizontalBlock"] {
  align-items: stretch !important;
}
.wd-row-chart [data-testid="stColumn"] {
  display: flex !important;
  flex-direction: column !important;
}
.wd-row-chart .wd-card { min-height: 260px; flex: 1 !important; }
.wd-row-free  .wd-card { height: auto; min-height: 0; }

/* ── 섹션 헤더 ──────────────────────────────────────────────── */
/* _chart_selector 헤더와 동일 스타일로 통일 */
.wd-sec {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  font-weight: 700;
  color: #1E293B;
  padding-bottom: 6px;
  margin-bottom: 8px;
  border-bottom: 1px solid #F1F5F9;
  line-height: 1.3;
}
.wd-sec-accent {
  width: 3px;
  height: 14px;
  border-radius: 2px;
  background: linear-gradient(180deg, #1E40AF, #60A5FA);
  flex-shrink: 0;
}
.wd-sec-sub {
  font-size: 10.5px;
  color: #94A3B8;
  font-weight: 400;
  margin-left: 4px;
}

/* ── Row 간격 통일 ──────────────────────────────────────────── */
/* Streamlit stVerticalBlock 기본 gap과 맞춤 */
[data-testid="stVerticalBlock"] { gap: 0.5rem !important; }

/* ── 모든 element-container 하단 여백 제거 ─────────────────── */
.element-container { margin-bottom: 0 !important; }

/* ── Plotly 차트 여백 통일 ──────────────────────────────────── */
/* 카드 내 Plotly iframe 상하 여백 제거 */
.stPlotlyChart { margin: 0 !important; padding: 0 !important; }
iframe.stPlotlyChart { border: none !important; }
/* ── 데이터 테이블 통일 스타일 ─────────────────────── */
.wd-tbl { width:100%; border-collapse:collapse; font-size:12.5px; table-layout:fixed; }
.wd-th {
  padding:7px 10px; font-size:10.5px; font-weight:700;
  text-transform:uppercase; letter-spacing:.07em;
  color:#64748B; background:#F8FAFC;
  border-bottom:1.5px solid #E2E8F0; white-space:nowrap;
}
.wd-td { padding:8px 10px; border-bottom:1px solid #F8FAFC; color:#334155; vertical-align:middle; font-size:12.5px; }
.wd-td-num { font-variant-numeric:tabular-nums; font-family:'Consolas','SF Mono',monospace; font-size:12.5px; }
.badge-ok   { background:#DCFCE7; color:#15803D; border:1px solid #86EFAC; border-radius:5px; padding:2px 9px; font-size:11px; font-weight:700; }
.badge-warn { background:#FEF3C7; color:#92400E; border:1px solid #FCD34D; border-radius:5px; padding:2px 9px; font-size:11px; font-weight:700; }
.badge-err  { background:#FEE2E2; color:#991B1B; border:1px solid #FCA5A5; border-radius:5px; padding:2px 9px; font-size:11px; font-weight:700; }
/* ── 대시보드 버튼 — 세련된 pill 스타일 ────────────────── */
button[kind="secondary"], [data-testid="stBaseButton-secondary"] {
  font-size:11.5px !important;
  font-weight:600 !important;
  padding:0 14px !important;
  height:32px !important;
  line-height:32px !important;
  border-radius:20px !important;
  border:1.5px solid #E2E8F0 !important;
  background:linear-gradient(180deg,#FFFFFF 0%,#F8FAFC 100%) !important;
  color:#475569 !important;
  box-shadow:0 1px 3px rgba(15,23,42,0.06),0 1px 2px rgba(15,23,42,0.04) !important;
  transition:all 120ms ease !important;
  white-space:nowrap !important;
  overflow:hidden !important;
  text-overflow:ellipsis !important;
  width:100% !important;
  letter-spacing:0.01em !important;
}
button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover {
  background:linear-gradient(180deg,#F1F5F9 0%,#E2E8F0 100%) !important;
  border-color:#94A3B8 !important;
  color:#1E293B !important;
  box-shadow:0 3px 8px rgba(15,23,42,0.10) !important;
  transform:translateY(-1px) !important;
}
button[kind="secondary"]:active, [data-testid="stBaseButton-secondary"]:active {
  transform:translateY(0) !important;
  box-shadow:0 1px 2px rgba(15,23,42,0.06) !important;
}
button[kind="primary"], [data-testid="stBaseButton-primary"] {
  font-size:11.5px !important;
  font-weight:700 !important;
  padding:0 14px !important;
  height:32px !important;
  border-radius:20px !important;
  white-space:nowrap !important;
  overflow:hidden !important;
  text-overflow:ellipsis !important;
  width:100% !important;
  letter-spacing:0.01em !important;
  box-shadow:0 2px 6px rgba(30,64,175,0.20) !important;
  transition:all 120ms ease !important;
}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
  transform:translateY(-1px) !important;
  box-shadow:0 4px 12px rgba(30,64,175,0.28) !important;
}
[data-testid="stSelectbox"] > div > div {
  height:34px !important; border-radius:8px !important; border:1.5px solid #BFDBFE !important;
  background:#EFF6FF !important; font-size:12px !important; font-weight:600 !important;
  color:#1E40AF !important; white-space:nowrap !important; overflow:hidden !important;
  text-overflow:ellipsis !important;
}
[data-testid="stSelectbox"] label { display:none !important; }
[data-testid="stMarkdownContainer"]:empty { display:none !important; }
[data-testid="stMarkdownContainer"] > div:empty { display:none !important; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   [v2.3] 차트 선택기 pill 버튼 — 완전 재설계
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/* ── 라디오 위젯 래퍼 초기화 ── */
div[data-testid="stRadio"] {
  padding: 0 !important;
  margin: 0 !important;
  min-height: 0 !important;
  line-height: 0 !important;
}

/* ── 위젯 라벨(제목) 완전 제거 ──
   stWidgetLabel: Streamlit ≥ 1.10
   첫 번째 p 태그: 구버전 fallback */
div[data-testid="stRadio"] > div[data-testid="stWidgetLabel"],
div[data-testid="stRadio"] > label:not([data-baseweb]),
div[data-testid="stRadio"] > p {
  display: none !important;
  height: 0 !important;
  max-height: 0 !important;
  min-height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
  opacity: 0 !important;
  pointer-events: none !important;
}

/* ── 옵션 컨테이너 — 가로 Flex ── */
div[data-testid="stRadio"] > div {
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  gap: 2px !important;
  align-items: center !important;
  justify-content: flex-end !important;
  padding: 0 !important;
  margin: 0 !important;
  min-height: 0 !important;
  line-height: normal !important;
}

/* ── 각 옵션 라벨 — pill 모양 ──
   label[data-baseweb="radio"]: BaseWeb radio label 정확 타겟팅 */
div[data-testid="stRadio"] label {
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 2px 8px !important;
  border-radius: 14px !important;
  border: 1px solid #E2E8F0 !important;
  background: #FFFFFF !important;
  color: #64748B !important;
  font-size: 10.5px !important;
  font-weight: 500 !important;
  cursor: pointer !important;
  white-space: nowrap !important;
  margin: 0 !important;
  transition: background 0.1s, color 0.1s, border-color 0.1s !important;
  gap: 0 !important;
  line-height: 1.5 !important;
}
div[data-testid="stRadio"] label:hover {
  background: #F1F5F9 !important;
  border-color: #93C5FD !important;
  color: #1E40AF !important;
}
div[data-testid="stRadio"] label:has(input:checked) {
  background: #1E40AF !important;
  border-color: #1E40AF !important;
  color: #FFFFFF !important;
  font-weight: 600 !important;
}

/* ── radio 원형 circle 제거 ──
   Streamlit/BaseWeb radio 실제 DOM 구조 (2024~):
     label[data-baseweb="radio"]
       div[class*="radioMarkOuter"]  ← circle 외곽
         div[class*="radioMarkInner"] ← circle 내부 점
       div (text wrapper)
         span (실제 텍스트)

   전략: input 숨김 + class 기반 circle div 숨김
   → input + div (인접형제) 방식은 Streamlit 버전에 따라 텍스트 wrapper도 잡힘
   → class 패턴 또는 aria-hidden 속성으로 circle만 정확히 타겟팅 */

/* input 완전 숨김 */
div[data-testid="stRadio"] input[type="radio"] {
  position: absolute !important;
  opacity: 0 !important;
  width: 0 !important;
  height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  pointer-events: none !important;
}

/* BaseWeb radioMarkOuter / radioMarkInner — circle 컨테이너 숨김
   class 이름이 해시되어 있으므로 부분 일치(^=, *=) 사용 */
div[data-testid="stRadio"] label > div:first-child {
  display: none !important;
  width: 0 !important;
  height: 0 !important;
  min-width: 0 !important;
  min-height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
  flex-shrink: 0 !important;
}

/* 텍스트 wrapper(두 번째 div)는 반드시 표시 */
div[data-testid="stRadio"] label > div:last-child,
div[data-testid="stRadio"] label > div:nth-child(2) {
  display: inline !important;
  width: auto !important;
  height: auto !important;
  overflow: visible !important;
}
</style>
"""

# _PLOTLY_BASE: margin 제거 — 각 차트 함수에서 명시적으로 지정 (중복 키 오류 방지)
_PLOTLY_BASE = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#333333", size=12),
)
# _PLOTLY_DARK, _PLOTLY_LIGHT 제거됨 (미사용 — _PLOTLY_BASE 직접 사용)


def _layout(fig: "go.Figure", **kwargs) -> "go.Figure":
    """
    Plotly Figure에 공통 레이아웃을 적용하는 헬퍼.

    _PLOTLY_BASE를 기본으로 kwargs를 병합합니다.
    kwargs가 _PLOTLY_BASE와 동일한 키를 가질 경우 kwargs가 우선합니다.
    → margin 등 키가 두 번 전달되어 TypeError 발생하는 문제를 방지합니다.

    사용 예:
        _layout(fig, height=230, margin=dict(l=0, r=0, t=8, b=8))
    """
    merged = {**_PLOTLY_BASE, **kwargs}   # kwargs가 _PLOTLY_BASE를 덮어씀
    fig.update_layout(**merged)
    return fig

# 차트 공통 색상 팔레트 (9색, 색약 고려)
_PALETTE = [
    "#1E40AF", "#2563EB", "#3B82F6",
    "#0D9488", "#059669", "#F59E0B",
    "#EF4444", "#7C3AED", "#78716C",
]

# 공통 축 스타일
# tickfont는 포함하지 않음 → dict(**_AX, tickfont=...) 호출 시 중복 키 오류 방지
_AX = dict(gridcolor="#F1F5F9", linecolor="#E2E8F0", zeroline=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [v2.2] 섹션별 대안 차트 렌더러 (인라인)
#
# - "table" 타입은 각 섹션의 기존 HTML 코드 블록이 그대로 처리
# - 나머지 타입만 아래 함수가 처리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_dept_chart(data: List[Dict], chart_type: str) -> None:
    """
    진료과별 재원 구성 대안 차트 렌더러.
    chart_type: "donut" | "bar_h" | "treemap"
    data: ward_dept_stay 쿼리 결과
    """
    from collections import defaultdict as _ddc2
    if not data or not HAS_PLOTLY:
        st.caption("데이터 없음")
        return

    # 진료과별 합산 집계
    agg: Dict[str, int] = _ddc2(int)
    for r in data:
        agg[r.get("진료과명", "기타")] += _safe_int(r.get("재원수"))
    sorted_items = sorted(agg.items(), key=lambda x: -x[1])
    top8 = list(sorted_items[:8])
    etc  = sum(v for _, v in sorted_items[8:])
    if etc > 0:
        top8.append(("기타", etc))
    labels = [n for n, _ in top8]
    values = [v for _, v in top8]
    total  = max(sum(values), 1)
    colors = _PALETTE[:len(labels)]

    if chart_type == "donut":
        fig = go.Figure(go.Pie(
            labels=labels, values=values, hole=0.52,
            marker=dict(colors=colors, line=dict(color="#FFFFFF", width=2)),
            textinfo="percent", textfont=dict(size=10, color="#FFFFFF"),
            direction="clockwise", sort=True,
            hovertemplate="<b>%{label}</b><br>%{value}명 (%{percent})<extra></extra>",
        ))
        fig.update_layout(
            height=200,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#333333", size=12),
            margin=dict(l=0, r=0, t=8, b=8),
            showlegend=False,
            annotations=[dict(text=f"<b>{total}</b><br>명", x=0.5, y=0.5,
                              showarrow=False, font=dict(size=14, color="#0F172A"))],
        )
        st.plotly_chart(fig, use_container_width=True, key="dept_donut")
        # 범례 테이블
        rows = ""
        for i, (nm, val) in enumerate(zip(labels, values)):
            pct = val / total * 100
            bg  = "#FFFFFF" if i % 2 == 0 else "#F8FAFC"
            clr = colors[i % len(colors)]
            rows += (
                f'<tr style="background:{bg};">'
                f'<td style="padding:3px 6px;"><span style="display:inline-block;'
                f'width:8px;height:8px;border-radius:2px;background:{clr};"></span></td>'
                f'<td style="padding:3px 6px;color:#0F172A;font-size:11.5px;font-weight:500;">{nm}</td>'
                f'<td style="padding:3px 6px;text-align:right;color:#1E40AF;'
                f'font-family:Consolas,monospace;font-weight:700;font-size:11.5px;">{val}</td>'
                f'<td style="padding:3px 6px;text-align:right;color:#64748B;'
                f'font-family:Consolas,monospace;font-size:11px;">{pct:.0f}%</td></tr>'
            )
        st.markdown(
            f'<table style="width:100%;border-collapse:collapse;margin-top:6px;border-top:1px solid #F1F5F9;">'
            f'<thead><tr style="background:#F8FAFC;">'
            f'<th style="padding:4px 6px;width:20px;"></th>'
            f'<th style="padding:4px 6px;color:#64748B;font-size:10px;text-align:left;">진료과</th>'
            f'<th style="padding:4px 6px;color:#64748B;font-size:10px;text-align:right;">재원수</th>'
            f'<th style="padding:4px 6px;color:#64748B;font-size:10px;text-align:right;">비율</th>'
            f"</tr></thead><tbody>{rows}</tbody></table>",
            unsafe_allow_html=True,
        )

    elif chart_type == "bar_h":
        fig = go.Figure(go.Bar(
            x=values, y=labels, orientation="h",
            marker=dict(color=colors, line=dict(color="rgba(0,0,0,0)")),
            text=[f"{v}명 ({v/total*100:.0f}%)" for v in values],
            textposition="outside", textfont=dict(size=10, color="#475569"),
            hovertemplate="<b>%{y}</b><br>%{x}명<extra></extra>",
        ))
        fig.update_layout(
            height=max(200, len(labels) * 30),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#333333", size=12),
            margin=dict(l=0, r=70, t=8, b=8),
            yaxis=dict(**_AX, autorange="reversed"),
            xaxis=dict(**_AX, title=dict(text="재원 환자 수 (명)", font=dict(size=10))),
        )
        st.plotly_chart(fig, use_container_width=True, key="dept_bar_h")

    elif chart_type == "treemap":
        fig = go.Figure(go.Treemap(
            labels=labels, values=values, parents=[""] * len(labels),
            marker=dict(colors=colors, line=dict(width=2, color="#FFFFFF")),
            texttemplate="<b>%{label}</b><br>%{value}명<br>%{percentRoot:.0%}",
            textfont=dict(size=10),
            hovertemplate="<b>%{label}</b><br>%{value}명 (%{percentRoot:.1%})<extra></extra>",
        ))
        fig.update_layout(
            height=270,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#333333", size=12),
            margin=dict(l=0, r=0, t=8, b=8),
        )
        st.plotly_chart(fig, use_container_width=True, key="dept_treemap")


def _render_trend_chart(data: List[Dict], chart_type: str, occupied: int, occ_rate: float) -> None:
    """
    주간 추이 7일 대안 차트 렌더러.
    chart_type: "table"(기존 표) | "line" | "area" | "bar"
    """
    if not data:
        st.caption("추이 데이터 없음")
        return

    dates  = [str(r.get("기준일", ""))           for r in data]
    occs   = [_safe_float(r.get("가동률"))     for r in data]
    admins = [_safe_int(r.get("금일입원"))      for r in data]
    discs  = [_safe_int(r.get("금일퇴원"))      for r in data]
    key_sfx = chart_type

    if chart_type == "table":
        # ── 기존 HTML 표 그대로 유지 ────────────────────────────────
        _tH2 = "padding:7px 10px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#64748B;border-bottom:1.5px solid #E2E8F0;background:#F8FAFC;"
        rows = ""
        for ti, row in enumerate(data):
            dt   = str(row.get("기준일", ""))
            occ  = float(row.get("가동률", 0) or 0)
            adm  = int(row.get("금일입원", 0) or 0)
            disc = int(row.get("금일퇴원", 0) or 0)
            tbg  = "#F8FAFC" if ti % 2 == 0 else "#FFFFFF"
            if occ >= 90:   oc, lbl = "#EF4444", '<span style="font-size:9px;background:#FEE2E2;color:#991B1B;border-radius:3px;padding:1px 5px;margin-left:3px;font-weight:700;">위험</span>'
            elif occ >= 80: oc, lbl = "#F59E0B", '<span style="font-size:9px;background:#FFFBEB;color:#92400E;border-radius:3px;padding:1px 5px;margin-left:3px;font-weight:700;">주의</span>'
            else:            oc, lbl = "#059669", ""
            td = f"padding:7px 10px;background:{tbg};border-bottom:1px solid #F8FAFC;font-size:13px;"
            rows += (
                f"<tr>"
                f'<td style="{td}font-weight:600;color:#334155;white-space:nowrap;">{dt}</td>'
                f'<td style="{td}text-align:right;">'  
                f'<span style="font-size:16px;font-weight:800;color:{oc};font-family:Consolas,monospace;letter-spacing:-0.02em;">{occ:.1f}%</span>{lbl}</td>'  
                f'<td style="{td}text-align:right;">'  
                f'<span style="font-size:16px;font-weight:800;color:{C["primary_text"]};font-family:Consolas,monospace;">{adm}</span></td>'  
                f'<td style="{td}text-align:right;">'  
                f'<span style="font-size:15px;font-weight:700;color:#64748B;font-family:Consolas,monospace;">{disc}</span></td>'  
                f"</tr>"
            )
        st.markdown(
            f'<table style="width:100%;border-collapse:collapse;"><thead><tr>'
            f'<th style="{_tH2}text-align:left;">날짜</th>'
            f'<th style="{_tH2}text-align:right;">가동률</th>'
            f'<th style="{_tH2}text-align:right;color:{C["primary_text"]};">입원</th>'
            f'<th style="{_tH2}text-align:right;color:#475569;">퇴원</th>'
            f"</tr></thead><tbody>{rows}</tbody></table>",
            unsafe_allow_html=True,
        )
        return

    if not HAS_PLOTLY:
        st.caption("plotly 미설치 — pip install plotly")
        return

    fig = go.Figure()

    if chart_type == "line":
        fig.add_trace(go.Scatter(x=dates, y=occs, name="가동률(%)", mode="lines+markers",
            line=dict(color="#1E40AF", width=2.5, shape="spline"),
            fill="tozeroy", fillcolor="rgba(30,64,175,0.06)",
            marker=dict(size=6, color="#1E40AF", line=dict(width=2, color="#fff")),
            yaxis="y", hovertemplate="%{x}<br>가동률 %{y:.1f}%<extra></extra>"))
        fig.add_trace(go.Scatter(x=dates, y=admins, name="금일입원", mode="lines+markers",
            line=dict(color="#059669", width=1.5, dash="dot", shape="spline"),
            marker=dict(size=4, color="#059669"), yaxis="y2",
            hovertemplate="%{x}<br>입원 %{y}명<extra></extra>"))
        fig.add_trace(go.Scatter(x=dates, y=discs, name="금일퇴원", mode="lines+markers",
            line=dict(color="#F59E0B", width=1.5, dash="dot", shape="spline"),
            marker=dict(size=4, color="#F59E0B"), yaxis="y2",
            hovertemplate="%{x}<br>퇴원 %{y}명<extra></extra>"))
        fig.update_layout(
            height=230, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12), showlegend=True,
            margin=dict(l=0, r=0, t=8, b=40),
            legend=dict(orientation="h", y=-0.22, x=0, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
            yaxis=dict(**_AX, title=dict(text="가동률(%)", font=dict(size=10)), ticksuffix="%", range=[0, 110]),
            yaxis2=dict(**_AX, overlaying="y", side="right", title=dict(text="인원(명)", font=dict(size=10))),
            xaxis=dict(**_AX),
        )

    elif chart_type == "area":
        fig.add_trace(go.Scatter(x=dates, y=admins, name="금일입원", mode="lines",
            fill="tozeroy", fillcolor="rgba(5,150,105,0.15)",
            line=dict(color="#059669", width=1.5),
            hovertemplate="%{x}<br>입원 %{y}명<extra></extra>"))
        fig.add_trace(go.Scatter(x=dates, y=discs, name="금일퇴원", mode="lines",
            fill="tozeroy", fillcolor="rgba(245,158,11,0.15)",
            line=dict(color="#F59E0B", width=1.5),
            hovertemplate="%{x}<br>퇴원 %{y}명<extra></extra>"))
        fig.add_trace(go.Scatter(x=dates, y=occs, name="가동률(%)", mode="lines+markers",
            line=dict(color="#1E40AF", width=2), marker=dict(size=5, color="#1E40AF"),
            yaxis="y2", hovertemplate="%{x}<br>가동률 %{y:.1f}%<extra></extra>"))
        fig.update_layout(
            height=230, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12), showlegend=True,
            margin=dict(l=0, r=0, t=8, b=40),
            legend=dict(orientation="h", y=-0.22, x=0, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
            yaxis=dict(**_AX, title=dict(text="인원(명)", font=dict(size=10))),
            yaxis2=dict(**_AX, overlaying="y", side="right", ticksuffix="%", range=[0, 110],
                        title=dict(text="가동률(%)", font=dict(size=10))),
            xaxis=dict(**_AX),
        )

    elif chart_type == "bar":
        fig.add_trace(go.Bar(x=dates, y=admins, name="금일입원", marker_color="#059669",
                             hovertemplate="%{x}<br>입원 %{y}명<extra></extra>"))
        fig.add_trace(go.Bar(x=dates, y=discs, name="금일퇴원", marker_color="#F59E0B",
                             hovertemplate="%{x}<br>퇴원 %{y}명<extra></extra>"))
        fig.add_trace(go.Scatter(x=dates, y=occs, name="가동률(%)", mode="lines+markers",
            line=dict(color="#1E40AF", width=2), marker=dict(size=5),
            yaxis="y2", hovertemplate="%{x}<br>가동률 %{y:.1f}%<extra></extra>"))
        fig.update_layout(
            height=230, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12),
            margin=dict(l=0, r=0, t=8, b=40),
            barmode="group", showlegend=True,
            legend=dict(orientation="h", y=-0.22, x=0, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
            yaxis=dict(**_AX, title=dict(text="인원(명)", font=dict(size=10))),
            yaxis2=dict(**_AX, overlaying="y", side="right", ticksuffix="%", range=[0, 110],
                        title=dict(text="가동률(%)", font=dict(size=10))),
            xaxis=dict(**_AX),
        )

    st.plotly_chart(fig, use_container_width=True, key=f"trend_{key_sfx}")


def _render_ward_alt_chart(data: List[Dict], chart_type: str, ward_surg: Dict) -> None:
    """
    병동별 당일 현황 대안 차트 렌더러.
    chart_type: "bar_h" | "heatmap"  (table은 기존 HTML 처리)
    """
    if not data or not HAS_PLOTLY:
        st.caption("데이터 없음")
        return

    wards    = [str(r.get("병동명", ""))           for r in data]
    stays    = [_safe_int(r.get("재원수"))       for r in data]
    admins   = [_safe_int(r.get("금일입원"))     for r in data]
    discs    = [_safe_int(r.get("금일퇴원"))     for r in data]
    beds     = [_safe_int(r.get("총병상"))       for r in data]
    occs     = [_safe_float(r.get("가동률"))     for r in data]

    if chart_type == "bar_h":
        bar_colors = ["#EF4444" if r >= 90 else "#F59E0B" if r >= 80 else "#059669" for r in occs]
        fig = go.Figure(go.Bar(
            x=occs, y=wards, orientation="h",
            marker=dict(color=bar_colors, line=dict(color="rgba(0,0,0,0)")),
            text=[f"{r:.1f}%  ({s}명/{b}병상)" for r, s, b in zip(occs, stays, beds)],
            textposition="outside", textfont=dict(size=10, color="#475569"),
            hovertemplate="<b>%{y}</b><br>가동률 %{x:.1f}%<extra></extra>",
        ))
        for thr, lbl, clr in [(90, "위험 90%", "#EF4444"), (80, "주의 80%", "#F59E0B")]:
            fig.add_vline(x=thr, line=dict(color=clr, dash="dash", width=1.2),
                          annotation_text=lbl, annotation_font=dict(size=9, color=clr),
                          annotation_position="top")
        fig.update_layout(
            height=max(200, len(wards) * 36),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12),
            margin=dict(l=0, r=90, t=8, b=8),
            yaxis=dict(**_AX, autorange="reversed"),
            xaxis=dict(**_AX, range=[0, 115], ticksuffix="%",
                       title=dict(text="병상 가동률 (%)", font=dict(size=10))),
        )
        st.plotly_chart(fig, use_container_width=True, key="ward_bar_h")

    elif chart_type == "heatmap":
        indicators = ["재원수", "금일입원", "금일퇴원", "가동률(%)"]
        z_raw = [stays, admins, discs, occs]
        z_matrix = [list(row) for row in zip(*z_raw)]  # [병동 × 지표]
        fig = go.Figure(go.Heatmap(
            z=z_matrix, x=indicators, y=wards,
            colorscale=[[0.0, "#DBEAFE"], [0.5, "#93C5FD"], [1.0, "#1E40AF"]],
            text=[[f"{v:.1f}" if isinstance(v, float) else str(v) for v in row] for row in z_matrix],
            texttemplate="%{text}",
            textfont=dict(size=10, color="#FFFFFF"),
            hovertemplate="<b>%{y}</b><br>%{x}: %{text}<extra></extra>",
            showscale=True,
            colorbar=dict(len=0.8, thickness=12, tickfont=dict(size=9)),
        ))
        fig.update_layout(
            height=max(220, len(wards) * 34),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12),
            margin=dict(l=0, r=60, t=30, b=8),
            xaxis=dict(side="top", tickfont=dict(size=10)),
            yaxis=dict(**_AX, autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True, key="ward_heatmap")


def _render_dx7_chart(data: List[Dict], chart_type: str) -> None:
    """
    최근 7일 주상병 분포 대안 차트 렌더러.
    chart_type: "pie" | "bar_h" | "treemap"
    """
    from collections import defaultdict as _ddx7
    if not data:
        st.info("주상병 데이터 없음")
        return

    agg: Dict[str, int] = _ddx7(int)
    for r in data:
        agg[r.get("주상병명", "기타")] += _safe_int(r.get("환자수"))
    sorted_items = sorted(agg.items(), key=lambda x: -x[1])
    top8  = list(sorted_items[:8])
    etc   = sum(v for _, v in sorted_items[8:])
    if etc > 0:
        top8.append(("기타", etc))
    labels = [n for n, _ in top8]
    values = [v for _, v in top8]
    total  = max(sum(values), 1)
    colors = _PALETTE[:len(labels)]

    if chart_type == "pie":
        if not HAS_PLOTLY:
            st.info("주상병 데이터 없음")
            return
        fig = go.Figure(go.Pie(
            labels=labels, values=values, hole=0.52,
            marker=dict(colors=colors, line=dict(color="#FFFFFF", width=2)),
            textinfo="percent", textfont=dict(size=10, color="#FFFFFF"),
            direction="clockwise", sort=True,
            hovertemplate="<b>%{label}</b><br>%{value}명 (%{percent})<extra></extra>",
        ))
        fig.update_layout(
            height=220, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12), margin=dict(l=0, r=0, t=8, b=8), showlegend=False,
            annotations=[dict(text=f"<b>{total}</b><br>명", x=0.5, y=0.5,
                              showarrow=False, font=dict(size=14, color="#0F172A"))],
        )
        st.plotly_chart(fig, use_container_width=True, key="dx7_pie")
        # 범례 테이블
        rows = ""
        for i, (nm, cnt) in enumerate(top8):
            pct = cnt / total * 100
            bg  = "#FFFFFF" if i % 2 == 0 else "#F8FAFC"
            clr = colors[i % len(colors)]
            rows += (
                f'<tr style="background:{bg};">'
                f'<td style="padding:4px 6px;text-align:center;">'
                f'<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:{clr};"></span></td>'
                f'<td style="padding:4px 6px;color:#0F172A;font-weight:500;">{nm}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:#1E40AF;font-family:Consolas,monospace;font-weight:700;">{cnt}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:#64748B;font-family:Consolas,monospace;">{pct:.0f}%</td></tr>'
            )
        st.markdown(
            '<table style="width:100%;border-collapse:collapse;font-size:11.5px;margin-top:8px;border-top:1px solid #F1F5F9;">'
            '<tr style="background:#F8FAFC;">'
            '<th style="padding:5px 6px;color:#64748B;font-size:10px;width:24px;">#</th>'
            '<th style="padding:5px 6px;color:#64748B;font-size:10px;text-align:left;">주상병명</th>'
            '<th style="padding:5px 6px;color:#64748B;font-size:10px;text-align:right;width:40px;">건수</th>'
            '<th style="padding:5px 6px;color:#64748B;font-size:10px;text-align:right;width:40px;">비율</th></tr>'
            f'{rows}</table>',
            unsafe_allow_html=True,
        )

    elif chart_type == "bar_h":
        if not HAS_PLOTLY:
            st.info("주상병 데이터 없음")
            return
        fig = go.Figure(go.Bar(
            x=values, y=labels, orientation="h",
            marker=dict(color=colors, line=dict(color="rgba(0,0,0,0)")),
            text=[f"{v}건" for v in values],
            textposition="outside", textfont=dict(size=10, color="#475569"),
            hovertemplate="<b>%{y}</b><br>%{x}건<extra></extra>",
        ))
        fig.update_layout(
            height=max(200, len(labels) * 30),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12),
            margin=dict(l=0, r=60, t=8, b=8),
            yaxis=dict(**_AX, autorange="reversed"),
            xaxis=dict(**_AX, title=dict(text="입원 건수", font=dict(size=10))),
        )
        st.plotly_chart(fig, use_container_width=True, key="dx7_bar_h")

    elif chart_type == "treemap":
        if not HAS_PLOTLY:
            st.info("주상병 데이터 없음")
            return
        fig = go.Figure(go.Treemap(
            labels=labels, values=values, parents=[""] * len(labels),
            marker=dict(colors=colors, line=dict(width=2, color="#FFFFFF")),
            texttemplate="<b>%{label}</b><br>%{value}건<br>%{percentRoot:.0%}",
            textfont=dict(size=10),
            hovertemplate="<b>%{label}</b><br>%{value}건 (%{percentRoot:.1%})<extra></extra>",
        ))
        fig.update_layout(height=270, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12), margin=dict(l=0, r=0, t=8, b=8))
        st.plotly_chart(fig, use_container_width=True, key="dx7_treemap")


def _render_dx_compare_chart(data: List[Dict], chart_type: str) -> None:
    """
    금일 vs 전일 주상병 비교 대안 차트 렌더러.
    chart_type: "overlay" | "grouped" | "bar_h"
    """
    from collections import defaultdict as _ddcmp
    if not data:
        st.info("주상병 분포 데이터 없음")
        return

    t_map: Dict[str, int] = _ddcmp(int)
    y_map: Dict[str, int] = _ddcmp(int)
    for r in data:
        nm  = r.get("주상병명", "기타") or "기타"
        cnt = _safe_int(r.get("환자수"))
        day = str(r.get("기준일", ""))
        (t_map if "오늘" in day else y_map)[nm] += cnt

    all_dx  = set(list(t_map.keys()) + list(y_map.keys()))
    sorted_dx = sorted(all_dx, key=lambda d: -(t_map.get(d, 0) + y_map.get(d, 0)))
    top_dx    = sorted_dx[:8]
    t_vals    = [t_map.get(d, 0) for d in top_dx]
    y_vals    = [y_map.get(d, 0) for d in top_dx]
    x_max     = max(max(t_vals, default=1), max(y_vals, default=1)) * 1.2
    _COL_T    = "#1D4ED8"
    _COL_Y    = "#0EA5E9"

    # 증감 주석
    def _anns_h(names, t_vs, y_vs, x_mx):
        anns = []
        for i, (nm, tv, yv) in enumerate(zip(names, t_vs, y_vs)):
            d = tv - yv
            clr = C["danger"] if d > 0 else C["ok"] if d < 0 else "#64748B"
            txt = f"▲{d:+d}" if d > 0 else f"▼{d}" if d < 0 else "─"
            anns.append(dict(x=x_mx - 0.2, y=nm, text=f"<b>{txt}</b>",
                             showarrow=False, font=dict(size=11, color=clr),
                             xref="x", yref="y", xanchor="right"))
        return anns

    if not HAS_PLOTLY:
        st.info("주상병 분포 데이터 없음")
        return

    if chart_type == "overlay":
        ranked = list(reversed(top_dx))
        rank_labels = [f"{len(top_dx) - i}위" for i in range(len(ranked))]
        tv_r  = [t_map.get(d, 0) for d in ranked]
        yv_r  = [y_map.get(d, 0) for d in ranked]
        diffs = [t - y for t, y in zip(tv_r, yv_r)]
        anns  = []
        for i, (df, tv, yv) in enumerate(zip(diffs, tv_r, yv_r)):
            clr = C["danger"] if df > 0 else C["ok"] if df < 0 else "#64748B"
            txt = f"▲{df:+d}" if df > 0 else f"▼{df}" if df < 0 else "─"
            anns.append(dict(x=x_max - 0.2, y=rank_labels[i], text=f"<b>{txt}</b>",
                             showarrow=False, font=dict(size=12, color=clr),
                             xref="x", yref="y", xanchor="right"))
        fig = go.Figure()
        fig.add_trace(go.Bar(name="전일", y=rank_labels, x=yv_r, orientation="h",
            marker_color=_COL_Y, marker=dict(opacity=0.6, line=dict(width=0)),
            text=yv_r, textposition="inside", textfont=dict(size=11, color="#FFFFFF"),
            hovertemplate="전일: %{x}명<extra></extra>"))
        fig.add_trace(go.Bar(name="금일", y=rank_labels, x=tv_r, orientation="h",
            marker_color=_COL_T, marker=dict(line=dict(width=0)),
            text=tv_r, textposition="inside", textfont=dict(size=12, color="#FFFFFF"),
            hovertemplate="금일: %{x}명<extra></extra>"))
        fig.update_layout(
            barmode="overlay", height=270,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12),
            margin=dict(l=0, r=50, t=8, b=46),
            legend=dict(orientation="h", y=-0.16, x=0, font=dict(size=12, color="#1E293B"),
                        bgcolor="rgba(0,0,0,0)", traceorder="reversed"),
            showlegend=True, annotations=anns,
            xaxis=dict(range=[0, x_max], gridcolor="#F1F5F9",
                       tickfont=dict(size=10.5, color="#64748B"), zeroline=False,
                       title=dict(text="입원 환자 수 (명)", font=dict(size=11, color="#64748B"))),
            yaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(size=12, color="#0F172A"), zeroline=False),
            bargap=0.3,
        )
        st.plotly_chart(fig, use_container_width=True, key="dx_overlay")
        # 하단 랭킹 테이블
        rows = ""
        for ri, (nm, tc, yc) in enumerate(zip(top_dx, t_vals, y_vals), 1):
            d  = tc - yc
            dc = C["danger"] if d > 0 else C["ok"] if d < 0 else "#94A3B8"
            dt = f"▲{d:+d}" if d > 0 else f"▼{d}" if d < 0 else "─"
            bg = "#FFFFFF" if ri % 2 == 0 else "#F8FAFC"
            rows += (
                f'<tr style="background:{bg};">'
                f'<td style="padding:4px 6px;font-weight:700;color:#1E40AF;">{ri}위</td>'
                f'<td style="padding:4px 5px;color:#0F172A;font-weight:500;">{nm}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:{_COL_T};font-family:Consolas,monospace;font-weight:700;">{tc}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:{_COL_Y};font-family:Consolas,monospace;">{yc}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:{dc};font-weight:700;">{dt}</td></tr>'
            )
        st.markdown(
            f'<table style="width:100%;border-collapse:collapse;font-size:11.5px;margin-top:8px;border-top:1px solid #F1F5F9;">'
            f'<tr style="background:#F8FAFC;">'
            f'<th style="padding:5px 6px;color:#64748B;font-size:10px;width:30px;">#</th>'
            f'<th style="padding:5px 6px;color:#64748B;font-size:10px;text-align:left;">주상병명</th>'
            f'<th style="padding:5px 6px;color:{_COL_T};font-size:10px;text-align:right;width:34px;">금일</th>'
            f'<th style="padding:5px 6px;color:{_COL_Y};font-size:10px;text-align:right;width:34px;">전일</th>'
            f'<th style="padding:5px 6px;color:#64748B;font-size:10px;text-align:right;width:38px;">증감</th></tr>'
            f'{rows}</table>',
            unsafe_allow_html=True,
        )

    elif chart_type == "grouped":
        fig = go.Figure()
        fig.add_trace(go.Bar(x=top_dx, y=y_vals, name="전일",
            marker=dict(color=_COL_Y, line=dict(color="rgba(0,0,0,0)")),
            hovertemplate="전일: %{y}명<extra></extra>"))
        fig.add_trace(go.Bar(x=top_dx, y=t_vals, name="금일",
            marker=dict(color=_COL_T, line=dict(color="rgba(0,0,0,0)")),
            hovertemplate="금일: %{y}명<extra></extra>"))
        fig.update_layout(
            height=260, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12),
            barmode="group", showlegend=True,
            legend=dict(orientation="h", y=-0.2, x=0, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(**_AX, tickangle=-30, tickfont=dict(size=9)),
            yaxis=dict(**_AX, title=dict(text="입원 환자 수 (명)", font=dict(size=10))),
            margin=dict(l=0, r=0, t=8, b=60),
        )
        st.plotly_chart(fig, use_container_width=True, key="dx_grouped")

    elif chart_type == "bar_h":
        deltas      = [t - y for t, y in zip(t_vals, y_vals)]
        delta_colors = ["#EF4444" if d > 0 else "#3B82F6" if d < 0 else "#94A3B8" for d in deltas]
        anns = [
            dict(x=max(tv, yv) + 0.5, y=dx,
                 text=f"{'▲' if d > 0 else '▼' if d < 0 else '─'}{abs(d)}",
                 showarrow=False, font=dict(size=9, color=c), xanchor="left")
            for dx, tv, yv, d, c in zip(top_dx, t_vals, y_vals, deltas, delta_colors)
        ]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=t_vals, y=top_dx, orientation="h", name="금일",
            marker=dict(color=_COL_T, opacity=0.9, line=dict(color="rgba(0,0,0,0)")),
            hovertemplate="금일: %{x}명<extra></extra>"))
        fig.add_trace(go.Bar(x=y_vals, y=top_dx, orientation="h", name="전일",
            marker=dict(color=_COL_Y, opacity=0.6, line=dict(color="rgba(0,0,0,0)")),
            hovertemplate="전일: %{x}명<extra></extra>"))
        fig.update_layout(
            height=max(200, len(top_dx) * 36),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#333333", size=12),
            barmode="overlay", showlegend=True,
            legend=dict(orientation="h", y=-0.15, x=0, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
            yaxis=dict(**_AX, autorange="reversed"),
            xaxis=dict(**_AX, range=[0, x_max],
                       title=dict(text="입원 환자 수 (명)", font=dict(size=10))),
            annotations=anns,
            margin=dict(l=0, r=50, t=8, b=40),
        )
        st.plotly_chart(fig, use_container_width=True, key="dx_bar_h")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 병동 대시보드 렌더러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _render_ward() -> None:
    """병동 대시보드 v5.2 — 차트 선택기 통합"""
    st.markdown(_WARD_CSS, unsafe_allow_html=True)

    _oracle_alive = st.session_state.get("oracle_ok", False)

    if not _oracle_alive:
        st.markdown(
            '<div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;'
            'padding:8px 14px;margin-bottom:8px;display:flex;align-items:center;gap:8px;">'
            '<span style="font-size:18px;">⚠️</span>'
            "<div>"
            '<b style="font-size:13px;color:#92400E;">Oracle 미연결 — 데모 데이터 없음</b>'
            '<div style="font-size:12px;color:#B45309;margin-top:2px;">'
            "VIEW 조회 불가 상태입니다. Oracle DB 연결 후 새로고침하세요."
            "</div></div></div>",
            unsafe_allow_html=True,
        )

    # ── 데이터 조회 (2분 TTL 캐시) ───────────────────────────────────
    dept_stay    = _qc("ward_dept_stay")
    bed_detail   = _qc("ward_bed_detail")
    op_stat      = _qc("ward_op_stat")
    trend        = _qc("ward_kpi_trend")
    dx_today     = _qc("ward_dx_today")
    dx_trend     = _qc("ward_dx_trend")
    yesterday    = _qc("ward_yesterday")
    admit_cands  = _qc("admit_candidates")
    # ── V_WARD_ROOM_DETAIL: 병실현황 + 병상 수배 모두 이 VIEW 사용 ─────
    # - 성별/나이/진료과 등 환자 정보 포함
    # - 병실현황 패널, 병상 수배 성별 필터 모두 이 데이터로 처리
    # - 조건부 로드: 패널 열림 또는 병상 수배 검색 시
    _show_room_panel = st.session_state.get("show_room_panel", False)
    _need_room_detail = _show_room_panel or st.session_state.get("asgn_result_ready", False)
    ward_room_detail = _qc("ward_room_detail") if _need_room_detail else []
    bed_room_stat: List[Dict] = ward_room_detail  # 동일 데이터 — 기존 참조 호환용 alias

    _adm_total = len(admit_cands)
    _adm_done  = sum(1 for r in admit_cands if r.get("수속상태", "") == "AD")

    _all_wards = ["전체"] + sorted({
        r.get("병동명", "") for r in bed_detail
        if r.get("병동명", "") and r.get("병동명", "") != "전체"
    })
    st.session_state["ward_name_list"] = _all_wards
    _g_ward = st.session_state.get("ward_selected", "전체")

    # ── 필터 헬퍼 ────────────────────────────────────────────────────
    def _trend_dedup(data):
        seen = {}
        if not data:
            return []
        for r in data:
            dt = r.get("기준일", "")
            if dt not in seen:
                seen[dt] = r
            elif r.get("병동명", "") == "전체":
                seen[dt] = r
        return [seen[k] for k in sorted(seen)]

    def _filter_by_ward(data: List[Dict], ward: str, ward_col: str = "병동명") -> List[Dict]:
        if ward == "전체":
            return data
        return [r for r in data if r.get(ward_col, "") == ward]

    def _filter_dx_ward(data: List[Dict], ward: str) -> List[Dict]:
        if ward == "전체":
            total_rows = [r for r in data if r.get("병동명", "") == "전체"]
            if total_rows:
                return total_rows
            from collections import defaultdict
            agg: dict = defaultdict(lambda: defaultdict(int))
            for r in data:
                k = (r.get("기준일", ""), r.get("주상병코드", ""), r.get("주상병명", ""))
                agg[k]["환자수"] += _safe_int(r.get("환자수"))
            return [
                {"기준일": k[0], "병동명": "전체", "주상병코드": k[1], "주상병명": k[2], "환자수": v["환자수"]}
                for k, v in agg.items()
            ]
        return [r for r in data if r.get("병동명", "") == ward]

    # ── 필터 적용 ─────────────────────────────────────────────────────
    if _g_ward != "전체":
        bed_detail_f = _filter_by_ward(bed_detail, _g_ward)
        op_stat_f    = _filter_by_ward(op_stat, _g_ward)
        dept_stay_f  = dept_stay  # V_WARD_DEPT_STAY는 병동명 컬럼 없음 → 전체 유지
        trend_f      = _trend_dedup(trend)
    else:
        bed_detail_f = bed_detail
        dept_stay_f  = dept_stay
        op_stat_f    = op_stat
        trend_f      = _trend_dedup(trend)

    dx_today_f = _filter_dx_ward(dx_today, _g_ward)
    dx_trend_f = _filter_dx_ward(dx_trend, _g_ward)

    # ── KPI 계산 (필터 후 1회) ────────────────────────────────────────
    total_bed  = sum(_safe_int(r.get("총병상"))   for r in bed_detail_f)
    admit_cnt  = sum(_safe_int(r.get("금일입원"))  for r in bed_detail_f)
    occupied   = sum(_safe_int(r.get("재원수"))    for r in bed_detail_f)
    disc_cnt   = sum(_safe_int(r.get("금일퇴원"))  for r in bed_detail_f)
    occ_rate   = round(occupied / max(total_bed, 1) * 100, 1)

    # ── 수술 집계 (필터 후 op_stat_f) ────────────────────────────────
    _ward_surg: dict = {}
    for _sr in op_stat_f:
        _sw = _sr.get("병동명", "")
        _ward_surg[_sw] = _ward_surg.get(_sw, 0) + int(_sr.get("수술건수", 0) or 0)

    def _ds(cur: int, prev: int, unit: str = "명") -> str:
        d = cur - prev
        return f"▲ +{d}{unit}" if d > 0 else f"▼ {d}{unit}" if d < 0 else "─"

    # ── 전일 데이터 ───────────────────────────────────────────────────
    _yest_f = _filter_by_ward(yesterday, _g_ward) if _g_ward != "전체" else yesterday
    _pa = sum(_safe_int(r.get("금일입원"))  for r in _yest_f)
    _pd = sum(_safe_int(r.get("금일퇴원"))  for r in _yest_f)
    _ps = sum(_safe_int(r.get("재원수"))    for r in _yest_f)
    _po = round(_ps / max(total_bed, 1) * 100, 1)
    if not _yest_f:
        _pa, _pd, _ps, _po = admit_cnt, disc_cnt, occupied, occ_rate

    # ── 익일 예약 ─────────────────────────────────────────────────────
    _first_bed  = bed_detail[0] if bed_detail else {}
    _next_op    = int(_first_bed.get("익일수술예약", 0) or 0)
    _next_adm   = int(_first_bed.get("익일입원예약", 0) or 0)
    _next_disc  = int(_first_bed.get("익일퇴원예약", 0) or 0)

    _total_rest    = sum(max(0, _safe_int(r.get("총병상")) - _safe_int(r.get("재원수"))) for r in bed_detail_f)
    _total_ndc_pre = sum(_safe_int(r.get("익일퇴원예약")) for r in bed_detail_f)  # 익일가용 계산 기준

    # ── 가동률 색상 ───────────────────────────────────────────────────
    if occ_rate >= 90:
        _oc = "#DC2626"
    elif occ_rate >= 80:
        _oc = "#F59E0B"
    else:
        _oc = "#059669"
    _do = f"▲ +{occ_rate - _po:.1f}%" if occ_rate > _po else f"▼ {occ_rate - _po:.1f}%"

    _kpi_for_llm = {
        "가동률": occ_rate, "재원수": occupied, "총병상": total_bed,
        "금일입원": admit_cnt, "금일퇴원": disc_cnt, "선택병동": _g_ward,
    }

    # ════════════════════════════════════════════════════════════
    # 병실 현황 패널 (show_room_panel=True 일 때만)
    # ════════════════════════════════════════════════════════════
    if _show_room_panel:
        _rp_ward = st.session_state.get("ward_selected", "전체")
        _rp_data = (
            [r for r in ward_room_detail if r.get("병동명", "") == _rp_ward]
            if _rp_ward != "전체" else ward_room_detail
        )
        _STATUS_CLR = {
            "재원":    ("#1D4ED8", "#DBEAFE"),
            "퇴원예정":("#7C3AED", "#EDE9FE"),
            "빈병상":  ("#16A34A", "#DCFCE7"),
            "LOCK":    ("#DC2626", "#FEE2E2"),
        }
        _rp_stay  = sum(1 for r in _rp_data if r.get("상태") == "재원")
        _rp_dc    = sum(1 for r in _rp_data if r.get("상태") == "퇴원예정")
        _rp_avail = sum(1 for r in _rp_data if r.get("상태") == "빈병상")
        _rp_lock  = sum(1 for r in _rp_data if r.get("상태") == "LOCK")

        st.markdown('<div class="wd-card" style="margin-bottom:8px;padding:14px 16px;">', unsafe_allow_html=True)
        _hdr_l, _hdr_r = st.columns([4, 6], gap="small", vertical_alignment="center")
        with _hdr_l:
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:8px;padding:4px 0;">'
                f'<span style="width:3px;height:18px;background:#1E40AF;border-radius:2px;"></span>'
                f'<span style="font-size:14px;font-weight:800;color:#0F172A;">🏥 병실 현황 — {_rp_ward}</span>'
                f"</div>",
                unsafe_allow_html=True,
            )
        with _hdr_r:
            _rp_total   = len(_rp_data)
            _status_opts = [
                f"전체 ({_rp_total})", f"재원 ({_rp_stay})", f"퇴원예정 ({_rp_dc})",
                f"빈병상 ({_rp_avail})", f"LOCK ({_rp_lock})",
            ]
            _status_sel = st.radio(
                "상태 필터", _status_opts, horizontal=True,
                key="rp_status_filter", label_visibility="collapsed",
            )
            _status_key = _status_sel.split(" (")[0].strip()
        st.markdown('<div style="height:1px;background:#E2E8F0;margin:8px 0 10px;"></div>', unsafe_allow_html=True)

        _rp_data_f = (
            _rp_data if _status_key == "전체"
            else [r for r in _rp_data if r.get("상태", "") == _status_key]
        )
        _col_tbl, _col_assign = st.columns([7, 3], gap="small")

        with _col_tbl:
            if not _rp_data_f:
                st.markdown(
                    '<div style="padding:32px;text-align:center;color:#94A3B8;">'
                    '<div style="font-size:24px;margin-bottom:8px;">🏥</div>'
                    '<div style="font-size:13px;font-weight:600;">병실 데이터 없음</div>'
                    '<div style="font-size:11px;margin-top:4px;">V_WARD_ROOM_DETAIL VIEW 확인</div></div>',
                    unsafe_allow_html=True,
                )
            else:
                def _parse_room(no):
                    s = str(no).zfill(6)
                    return s[:2], s[2:4], s[4:6]

                from collections import OrderedDict
                _room_groups = OrderedDict()
                for r in _rp_data_f:
                    _bno = r.get("병실번호", "")
                    _wd, _rm, _bd = _parse_room(_bno)
                    _grp_key = r.get("병동명", "") + "_" + _wd + _rm
                    if _grp_key not in _room_groups:
                        _room_groups[_grp_key] = []
                    _room_groups[_grp_key].append((_bd, r))

                _TH = (
                    "padding:7px 10px;font-size:10.5px;font-weight:700;"
                    "text-transform:uppercase;letter-spacing:.06em;"
                    "color:#64748B;border-bottom:1.5px solid #E2E8F0;background:#F8FAFC;white-space:nowrap;"
                )
                _html = (
                    '<div style="overflow-x:auto;">'
                    '<table style="width:100%;border-collapse:collapse;"><thead><tr>'
                    f'<th style="{_TH}text-align:left;min-width:70px;">병동</th>'
                    f'<th style="{_TH}text-align:center;min-width:50px;">병실</th>'
                    f'<th style="{_TH}text-align:center;min-width:40px;">베드</th>'
                    f'<th style="{_TH}text-align:center;">인실</th>'
                    f'<th style="{_TH}text-align:center;">등급</th>'
                    f'<th style="{_TH}text-align:right;">병실료</th>'
                    f'<th style="{_TH}text-align:center;">나이</th>'
                    f'<th style="{_TH}text-align:center;">성별</th>'
                    f'<th style="{_TH}text-align:left;">진료과</th>'
                    f'<th style="{_TH}text-align:center;">상태</th>'
                    f'<th style="{_TH}text-align:left;">LOCK</th>'
                    f'<th style="{_TH}text-align:left;min-width:120px;">📝 병실메모</th>'
                    "</tr></thead><tbody>"
                )
                for _gi, (_grp_key, _beds) in enumerate(_room_groups.items()):
                    if _gi > 0:
                        _html += '<tr><td colspan="12" style="padding:0;border-top:2px solid #E2E8F0;"></td></tr>'
                    for _bi, (_bed_cd, _r) in enumerate(_beds):
                        _bg     = "#F0F7FF" if _gi % 2 == 0 else "#F8FAFC"
                        _status = _r.get("상태", "빈병상")
                        _sc, _sbg = _STATUS_CLR.get(_status, ("#64748B", "#F1F5F9"))
                        _lock_cm  = _r.get("LOCK코멘트", "") or ""
                        _grade    = _r.get("병실등급", "") or "─"
                        _dc_dt_v  = _r.get("퇴원예정일", "") or ""
                        if _dc_dt_v and len(str(_dc_dt_v)) >= 8:
                            _dc_str  = str(_dc_dt_v)
                            _dc_disp = f"{_dc_str[4:6]}/{_dc_str[6:8]}"
                        elif _dc_dt_v:
                            _dc_disp = str(_dc_dt_v)[:10]
                        else:
                            _dc_disp = ""
                        _room_memo = (_r.get("병실메모", "") or "").strip()
                        _fee_raw   = _r.get("병실료", 0) or 0
                        _fee_str   = f"{int(_fee_raw):,}원" if _fee_raw else "─"
                        _age_v     = _r.get("나이")
                        _sex_v     = _r.get("성별")
                        _dept_v    = _r.get("진료과")
                        _age_s     = f"{int(_age_v)}세" if _age_v else "─"
                        _sex_s     = _sex_v or "─"
                        _dept_s    = _dept_v or "─"
                        _sex_c     = "#1D4ED8" if _sex_s == "남" else "#BE185D" if _sex_s == "여" else "#94A3B8"
                        _wd_td     = _r.get("병동명", "") if _bi == 0 else ""
                        _rm_td     = _beds[0][1].get("병실번호", "")[2:4] if _bi == 0 else ""
                        _wd_fw     = "font-weight:700;color:#0F172A;" if _bi == 0 else "color:#CBD5E1;"
                        _rm_fw     = "font-weight:600;color:#334155;" if _bi == 0 else "color:#CBD5E1;"
                        _dc_date_html = (
                            f'<div style="font-size:10px;color:#7C3AED;font-weight:600;margin-top:3px;font-family:Consolas,monospace;">📅 {_dc_disp}</div>'
                            if (_status == "퇴원예정" and _dc_disp) else ""
                        )
                        _memo_c  = "#334155" if _room_memo else "#CBD5E1"
                        _memo_bg = "#FFF7ED" if _room_memo else "transparent"
                        _lock_disp = ("🔒 " + _lock_cm) if _lock_cm else "─"
                        _cells = [
                            f'<tr style="background:{_bg};">',
                            f'<td style="padding:7px 10px;font-size:13px;{_wd_fw}">{_wd_td}</td>',
                            f'<td style="padding:7px 10px;text-align:center;font-size:13px;font-family:Consolas,monospace;{_rm_fw}">{_rm_td}</td>',
                            f'<td style="padding:7px 10px;text-align:center;font-size:12px;color:#7C3AED;font-family:Consolas,monospace;font-weight:700;">{_bed_cd}</td>',
                            f'<td style="padding:7px 10px;text-align:center;font-size:12px;color:#475569;">{(_r.get("인실구분", "") if _bi == 0 else "")}</td>',
                            f'<td style="padding:7px 10px;text-align:center;font-size:12px;color:#64748B;">{(_grade if _bi == 0 else "")}</td>',
                            f'<td style="padding:7px 10px;text-align:right;font-size:12px;color:#0F172A;font-family:Consolas,monospace;">{(_fee_str if _bi == 0 else "")}</td>',
                            f'<td style="padding:7px 10px;text-align:center;font-size:12px;color:#334155;font-family:Consolas,monospace;">{_age_s}</td>',
                            f'<td style="padding:7px 10px;text-align:center;font-size:12px;font-weight:700;color:{_sex_c};">{_sex_s}</td>',
                            f'<td style="padding:7px 10px;font-size:12px;color:#475569;">{_dept_s}</td>',
                            (f'<td style="padding:6px 10px;text-align:center;vertical-align:middle;">'
                             f'<span style="background:{_sbg};color:{_sc};border-radius:5px;padding:2px 8px;font-size:11px;font-weight:700;">{_status}</span>'
                             f"{_dc_date_html}</td>"),
                            f'<td style="padding:7px 10px;font-size:11px;color:#F59E0B;">{_lock_disp}</td>',
                            (f'<td style="padding:7px 10px;font-size:12px;background:{_memo_bg};color:{_memo_c};'
                             f'max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                             f"{'📝 ' + _room_memo if _room_memo else '─'}</td>"),
                            "</tr>",
                        ]
                        _html += "".join(_cells)
                _html += "</tbody></table></div>"
                st.markdown(_html, unsafe_allow_html=True)

        with _col_assign:
            st.markdown(
                '<div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:14px;">'
                '<div style="font-size:12px;font-weight:700;color:#1E40AF;'
                'text-transform:uppercase;letter-spacing:.07em;margin-bottom:10px;">🔍 병상 수배</div>',
                unsafe_allow_html=True,
            )
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 병상 수배 조건 설정 (v2.5)
            # 기본: 직접 입력 / 예약자 불러오기는 expander로 보조
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

            # ── 병동 선택 ─────────────────────────────────────
            _asgn_wards = ["전체"] + sorted({r.get("병동명", "") for r in ward_room_detail if r.get("병동명", "")})
            _asgn_ward_sel = st.selectbox(
                "병동",
                _asgn_wards,
                index=_asgn_wards.index(_rp_ward) if _rp_ward in _asgn_wards else 0,
                key="asgn_ward_sel",
            )

            # ── 인실 선택 ─────────────────────────────────────
            _asgn_room_sel = st.selectbox(
                "인실",
                ["전체", "1인실", "2인실", "3인실", "4인실"],
                key="asgn_room_sel",
            )

            # ── 성별 ──────────────────────────────────────────
            _asgn_sex_sel = st.radio(
                "성별", ["전체", "남", "여"],
                horizontal=True, key="asgn_sex_sel",
            )

            # ── 진료과 (영문코드 드롭다운) ─────────────────────
            _all_dept_codes = sorted({
                (r.get("진료과", "") or "").strip()
                for r in ward_room_detail
                if r.get("진료과", "") and r.get("상태") in ("재원", "퇴원예정")
            })
            _dept_opts_base = ["전체"] + [d for d in _all_dept_codes if d]
            _is_1insl = (_asgn_room_sel == "1인실")
            if _is_1insl:
                st.markdown(
                    '<div style="font-size:10.5px;color:#059669;background:#DCFCE7;'
                    'border-radius:5px;padding:4px 8px;margin-bottom:4px;">'
                    '✅ 1인실은 진료과 무관 배정 가능</div>',
                    unsafe_allow_html=True,
                )
                _asgn_dept_sel = "전체"
            else:
                _asgn_dept_sel = st.selectbox(
                    "진료과",
                    _dept_opts_base,
                    key="asgn_dept_sel",
                    help="재원 환자 기준 영문코드 (예: IM=내과, GS=외과, OS=정형외과)",
                )

            # ── 예약 환자에서 불러오기 (선택사항) ─────────────
            # expander로 접어두어 필수값처럼 보이지 않게 함
            _pt_badge_html = ""
            if admit_cands:
                with st.expander(f"📋 예약 환자 불러오기 ({len(admit_cands)}명)", expanded=False):
                    _pt_opts = ["— 선택 안 함 —"] + [
                        (
                            f"{r.get('진료과명','?')}"
                            f" | {'남' if r.get('성별','M')=='M' else '여'}"
                            f" | {r.get('나이','?')}세"
                        )
                        for r in admit_cands
                    ]
                    _pt_sel = st.selectbox(
                        "환자",
                        _pt_opts,
                        key="asgn_pt_sel",
                        label_visibility="collapsed",
                    )
                    if _pt_sel != "— 선택 안 함 —":
                        _pt_idx   = _pt_opts.index(_pt_sel) - 1
                        _pt_r     = admit_cands[_pt_idx]
                        _raw_dept = (_pt_r.get("진료과코드", "") or _pt_r.get("진료과명", "")).strip().upper()
                        _asgn_sex_sel  = "남" if _pt_r.get("성별", "M") == "M" else "여"
                        _asgn_dept_sel = _raw_dept if _raw_dept else "전체"
                        _age_v    = int(_pt_r.get("나이", 0) or 0)
                        _sx_icon  = "🔵" if _asgn_sex_sel == "남" else "🔴"
                        _pt_badge_html = (
                            f'<div style="margin:4px 0;padding:6px 10px;background:#EFF6FF;'
                            f'border:1px solid #BFDBFE;border-radius:6px;font-size:11.5px;'
                            f'line-height:1.7;color:#1E40AF;">'
                            f'📋 <b>{_pt_r.get("진료과명", _raw_dept)}</b>'
                            f' {_sx_icon} {_asgn_sex_sel}성 {_age_v}세 → 조건 자동 적용'
                            f'</div>'
                        )
                        st.markdown(_pt_badge_html, unsafe_allow_html=True)

            # ── 검색 버튼 ─────────────────────────────────
            if st.button("🔍 가용 병상 검색", key="asgn_search_btn", use_container_width=True, type="primary"):
                st.session_state.update({
                    "asgn_result_ready": True,
                    "asgn_dept_saved":   _asgn_dept_sel,
                    "asgn_sex_saved":    _asgn_sex_sel,
                    "asgn_room_saved":   _asgn_room_sel,
                    "asgn_ward_saved":   _asgn_ward_sel,
                })
            st.markdown("</div>", unsafe_allow_html=True)

            if st.session_state.get("asgn_result_ready"):
                # 검색 버튼 클릭 시 저장된 값 사용 (rerun 후에도 유지)
                _sw  = st.session_state.get("asgn_ward_saved", "전체")
                _sri = st.session_state.get("asgn_room_saved", "전체")
                _sdp = ("전체" if st.session_state.get("asgn_room_saved") == "1인실"
                        else st.session_state.get("asgn_dept_saved", "전체"))
                _ssx = st.session_state.get("asgn_sex_saved", "전체")

                # ── 1단계: 빈병상 + 병동/인실 기본 필터 ────────────────
                _candidates_raw = [
                    r for r in ward_room_detail
                    if r.get("상태") == "빈병상"
                    and (_sw == "전체" or r.get("병동명", "") == _sw)
                    and (_sri == "전체" or r.get("인실구분", "") == _sri)
                ]

                # ── 2단계: 성별 필터 ─────────────────────────────────────
                # 빈병상 자체에는 환자 없음 → 같은 병실 재원 환자 성별로 판단
                # 1인실은 성별 무관 (독립 병실), 2인실 이상만 적용
                # 판단: 병실번호 앞 4자리(=병실) 기준으로 반대 성별 재원 시 제외
                # 같은 병실에 아무도 없으면 → 성별 무관 허용
                if _ssx != "전체" and _sri != "1인실":
                    # _norm_sex() 모듈 레벨 함수 사용 (_SEX_NORM dict lookup O(1))
                    _opp_code = "F" if _ssx == "남" else "M"
                    _my_code  = "M" if _ssx == "남" else "F"   # 내 성별 코드

                    # 성별 필터 소스: V_WARD_ROOM_DETAIL (성별 컬럼 포함)
                    # ward_room_detail은 _need_room_detail=True 일 때 전체 병동 로드됨
                    # 검색 대상 병동(_sw)의 재원 환자만 추출
                    if _sw != "전체":
                        _sex_data_src = [
                            r for r in ward_room_detail
                            if r.get("병동명", "") == _sw
                        ]
                    else:
                        _sex_data_src = ward_room_detail

                    # 단일 순회로 blocked_rooms + same_sex_rooms 동시 수집 (O(n) → O(n/2))
                    _blocked_rooms: set[str] = set()
                    _same_sex_rooms: set[str] = set()
                    for _r in _sex_data_src:
                        if _r.get("상태") not in ("재원", "퇴원예정"):
                            continue
                        _rkey = str(_r.get("병실번호", "")).zfill(6)[:4]
                        _rsx  = _norm_sex(_r.get("성별", ""))
                        if _rsx == _opp_code:
                            _blocked_rooms.add(_rkey)
                        elif _rsx == _my_code:
                            _same_sex_rooms.add(_rkey)

                    _candidates_sex = [
                        r for r in _candidates_raw
                        if str(r.get("병실번호", "")).zfill(6)[:4] not in _blocked_rooms
                    ]
                    _candidates_sex = sorted(
                        _candidates_sex,
                        key=lambda r: (0 if str(r.get("병실번호","")).zfill(6)[:4] in _same_sex_rooms else 1,
                                       r.get("병실번호","")),
                    )
                    # 성별 필터 결과 확정 — 폴백 제거
                    # 기존: _candidates_sex가 [] (falsy)이면 _candidates_raw로 폴백
                    # → 모든 후보가 차단됐을 때 필터가 완전히 무시되는 버그
                    # 수정: 결과가 0개면 그대로 0개 유지 → '조건 맞는 병상 없음' 표시
                    _candidates_raw = _candidates_sex
                else:
                    pass  # 전체 또는 1인실: 성별 필터 없음

                # ── 3단계: 진료과 매칭 정렬 ──────────────────────────────
                # 1인실은 진료과 무관이므로 _sdp=="전체"로 바이패스됨
                if _sdp and _sdp != "전체":
                    _dept_rooms = {
                        str(r.get("병실번호", "")).zfill(6)[:4]
                        for r in ward_room_detail
                        if (r.get("진료과", "") or "").strip().upper() == _sdp.upper()
                        and r.get("상태") in ("재원", "퇴원예정")
                    }
                    _candidates = sorted(
                        _candidates_raw,
                        key=lambda r: (
                            0 if str(r.get("병실번호", "")).zfill(6)[:4] in _dept_rooms else 1,
                            r.get("병실번호", ""),
                        )
                    )
                else:
                    _candidates = sorted(_candidates_raw, key=lambda r: r.get("병실번호", ""))
                st.markdown(
                    f'<div style="margin-top:8px;padding:10px;background:#FFFFFF;'
                    f'border:1px solid #E2E8F0;border-radius:8px;">'
                    f'<div style="font-size:11px;font-weight:700;color:#64748B;margin-bottom:6px;">가용 병상 {len(_candidates)}개</div>',
                    unsafe_allow_html=True,
                )
                if _candidates:
                    # list → join 방식: str += O(n²) 방지
                    _res_parts: list = []
                    for _cr in _candidates[:15]:
                        _cbno  = str(_cr.get("병실번호", "")).zfill(6)
                        _croom = _cbno[2:4]
                        _cbed  = _cbno[4:6]
                        _cward = _cr.get("병동명", "")
                        _cinsl = _cr.get("인실구분", "")
                        _cfee  = _cr.get("병실료", 0) or 0
                        _cfee_s = f"{int(_cfee):,}원" if _cfee else "─"
                        _res_parts.append(
                            f'<div style="display:flex;align-items:center;justify-content:space-between;'
                            f'padding:6px 8px;border-bottom:1px solid #F1F5F9;">'
                            f'<div><span style="font-size:13px;font-weight:700;color:#1E40AF;">{_cward}</span>'
                            f'<span style="font-size:12px;color:#64748B;margin-left:6px;">병실 {_croom} · 베드 {_cbed}</span>'
                            f'</div>'
                            f'<div style="display:flex;align-items:center;gap:6px;">'
                            f'<span style="font-size:11px;color:#475569;">{_cinsl}</span>'
                            f'<span style="font-size:11px;color:#94A3B8;">{_cfee_s}</span>'
                            f'<span style="background:#DCFCE7;color:#16A34A;border-radius:4px;padding:1px 7px;font-size:10px;font-weight:700;">빈병상</span>'
                            f"</div></div>"
                        )
                    st.markdown("".join(_res_parts) + "</div>", unsafe_allow_html=True)
                else:
                    st.markdown(
                        '<div style="padding:16px;text-align:center;color:#94A3B8;font-size:12px;">'
                        "조건에 맞는 빈 병상이 없습니다</div></div>",
                        unsafe_allow_html=True,
                    )

        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    # ── Row 1: KPI 2행×3열 | 주간 추이 ─────────────────────────
    st.markdown('<div class="wd-row-kpi">', unsafe_allow_html=True)
    # ════════════════════════════════════════════════════════════
    if occ_rate >= 90:
        _oc_color = "#EF4444"
    elif occ_rate >= 80:
        _oc_color = "#F59E0B"
    else:
        _oc_color = "#16A34A"

    _col_kpi, _col_trend = st.columns([9, 5], gap="small")

    with _col_kpi:
        _r1c1, _r1c2, _r1c3 = st.columns(3, gap="small")
        with _r1c1:
            _kpi_card("병상 가동률", f"{occ_rate:.1f}", "%", f"재원 {occupied} / {total_bed}병상", _oc_color, delta=_do, bar_pct=occ_rate)
        with _r1c2:
            _kpi_card("금일 퇴원", str(disc_cnt), "명", f"전일 {_pd}명", "#475569", delta=_ds(disc_cnt, _pd))
        with _r1c3:
            _kpi_card("금일 입원", str(admit_cnt), "명", f"전일 {_pa}명", C["primary_text"], delta=_ds(admit_cnt, _pa))

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        _r2c1, _r2c2, _r2c3 = st.columns(3, gap="small")
        with _r2c1:
            _kpi_card("재원 환자", str(occupied), "명", "전일 대비", "#0F172A", delta=_ds(occupied, _ps))
        with _r2c2:
            _today_op_total = sum(_ward_surg.values())
            _kpi_card("금일 수술", str(_today_op_total), "건", f"익일 예약 {_next_op}건", "#7C3AED")
        with _r2c3:
            st.markdown(
                f'<div class="kpi-card">'
                f'<div class="kpi-label">익일 예약</div>'
                f'<div style="display:flex;align-items:baseline;justify-content:space-between;margin:6px 0 3px;">'
                f'<span style="font-size:13px;color:#64748B;font-weight:500;">입원</span>'
                f'<div style="display:flex;align-items:baseline;gap:2px;">'
                f'<span style="font-size:28px;font-weight:800;color:{C["primary_text"]};font-variant-numeric:tabular-nums;line-height:1;">{_next_adm}</span>'
                f'<span style="font-size:13px;color:#64748B;">명</span></div></div>'
                f'<div style="height:1px;background:#F1F5F9;margin:2px 0;"></div>'
                f'<div style="display:flex;align-items:baseline;justify-content:space-between;margin-top:3px;">'
                f'<span style="font-size:13px;color:#64748B;font-weight:500;">퇴원</span>'
                f'<div style="display:flex;align-items:baseline;gap:2px;">'
                f'<span style="font-size:28px;font-weight:800;color:#475569;font-variant-numeric:tabular-nums;line-height:1;">{_next_disc}</span>'
                f'<span style="font-size:13px;color:#64748B;">명</span></div></div>'
                f'<div style="font-size:11px;color:#94A3B8;margin-top:4px;">'
                f"금일예약 {_adm_total}명 (완료 {_adm_done} / 대기 {_adm_total - _adm_done})</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # ── KPI 하단: 빠른질문 버튼 + AI 채팅 ──────────────────────
        # KPI 카드를 보고 판단한 뒤 바로 클릭 → 주간추이와 높이 자동 정렬
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        # 빠른질문 버튼 5개 — 얇은 구분선 + 라벨
        st.markdown(
            '<div style="border-top:1px solid #E2E8F0;padding-top:8px;margin-bottom:6px;">'
            '<span style="font-size:10px;font-weight:700;color:#94A3B8;'
            'text-transform:uppercase;letter-spacing:.08em;">빠른 분석</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        _quick_qs2 = [
            ("익일 가용",     "익일 입원 예약 대비 가용 병상 현황을 분석하고, 부족 위험이 있는 병동을 알려주세요."),
            ("입퇴원 분석",   "금일 입원과 퇴원 현황을 분석하고 전일 대비 변화를 설명해주세요."),
            ("입원 상병 추세","최근 7일 주요 입원 상병 추세를 분석하고 특이사항을 알려주세요."),
            ("재원환자 분석", "병동별 재원 환자 현황을 분석하고 장기 재원 위험 병동을 알려주세요."),
            ("운영 요약",     "오늘 병동 전체 운영 현황을 3줄로 요약해주세요."),
        ]
        _qb_cols = st.columns(len(_quick_qs2), gap="small")
        for _qi2, (_ql2, _qv2) in enumerate(_quick_qs2):
            with _qb_cols[_qi2]:
                if st.button(
                    _ql2, key=f"qs_kpi_{_qi2}",
                    use_container_width=True, type="secondary",
                    help=_qv2[:40] + "...",
                ):
                    _DASH_MON.log_action("quick_btn", label=_ql2)
                    st.session_state["ward_chat_quick_input"] = _qv2
                    st.rerun()

        # AI 채팅 — KPI 컬럼 내 하단에 배치 (높이 자동 확장)
        st.markdown(
            '<div style="margin-top:8px;border:1px solid #E2E8F0;border-radius:10px;'
            'padding:12px 14px;background:#FAFBFC;flex:1;">',
            unsafe_allow_html=True,
        )
        _render_ward_llm_chat(
            kpi=_kpi_for_llm, bed_occ=[],
            bed_detail=bed_detail_f, op_stat=op_stat_f,
        )
        st.markdown('</div>', unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    # [v2.2] 주간 추이 7일 — 차트 선택기 통합
    # 기본: 테이블 (기존 HTML 표) / 라인 / 영역 / 막대 선택 가능
    # ════════════════════════════════════════════════════════════
    with _col_trend:
        _oc_c_trend = "#EF4444" if occ_rate >= 90 else "#F59E0B" if occ_rate >= 80 else "#16A34A"

        st.markdown('<div class="wd-card" style="padding:14px 16px;">', unsafe_allow_html=True)

        # ── [v2.2] 섹션 헤더 + pill 선택기 ────────────────────────────
        chart_type_trend = _chart_selector("weekly_trend", "주간 추이 7일", _g_ward)

        if trend_f:
            # 전일 기준 7일치만 표시 — 금일(오늘)은 KPI 카드로 확인
            # 금일 기준일 제외: today 문자열과 일치하는 행 제거 후 마지막 7개
            _today_str = time.strftime('%Y-%m-%d')
            _trend_no_today = [
                r for r in trend_f
                if str(r.get('기준일', ''))[:10] != _today_str
            ]
            # 날짜 오름차순 정렬 후 마지막 7개 (전일 포함 7일)
            _trend_7 = sorted(_trend_no_today, key=lambda r: str(r.get('기준일', '')))[-7:]
            _render_trend_chart(_trend_7, chart_type_trend, occupied, occ_rate)
        else:
            st.markdown(
                '<div style="display:flex;align-items:center;justify-content:center;'
                'min-height:160px;color:#94A3B8;flex-direction:column;gap:8px;">'
                '<div style="font-size:28px;">📊</div>'
                '<div style="font-size:13px;font-weight:600;">추이 데이터 없음</div>'
                f'<div style="font-size:11px;color:#64748B;">'
                + ("Oracle 미연결" if not st.session_state.get("oracle_ok", False) else "V_WARD_KPI_TREND 확인")
                + "</div></div>",
                unsafe_allow_html=True,
            )

        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    # Row 2: 익일 예약 수용률 계산 + 패널
    # ════════════════════════════════════════════════════════════
    _total_avail  = _total_rest + _total_ndc_pre
    _cap_sum_c2   = "#16A34A" if _total_avail > 5 else "#F59E0B" if _total_avail > 0 else "#EF4444"
    _adm_cap_pct  = round(_next_adm / max(_total_avail, 1) * 100)
    _adm_cap_color = "#EF4444" if _adm_cap_pct >= 90 else "#F59E0B" if _adm_cap_pct >= 70 else "#16A34A"


    # ════════════════════════════════════════════════════════════
    st.markdown('</div>', unsafe_allow_html=True)  # /wd-row-kpi
    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

    # ── Row 3: 병동별 당일 현황 + 진료과 재원 ───────────────────
    st.markdown('<div class="wd-row-chart">', unsafe_allow_html=True)
    # [v2.2] 두 섹션 모두 차트 선택기 통합
    # ════════════════════════════════════════════════════════════
    col_L, col_R = st.columns([4, 2], gap="small")

    # ── [v2.2] 병동별 당일 현황 ─────────────────────────────────────
    with col_L:
        st.markdown('<div class="wd-card">', unsafe_allow_html=True)

        # ── [v2.2] 섹션 헤더 + pill 선택기 ─────────────────────────────
        chart_type_ward = _chart_selector("ward_detail", "병동별 당일 현황")

        if chart_type_ward == "table":
            # ── 기존 풍부한 HTML 테이블 (퇴원예정, 수술, 잔여병상, 익일가용 포함) ──
            # 헤더 공통 스타일 — white-space 제거하여 줄바꿈 허용
            _tH = (
                "padding:7px 6px;font-size:10px;font-weight:700;"
                "text-transform:uppercase;letter-spacing:.04em;"
                "color:#64748B;border-bottom:1.5px solid #E2E8F0;"
                "background:#F8FAFC;word-break:keep-all;line-height:1.3;"
            )
            # 컬럼별 너비 고정 (table-layout:fixed 와 함께 사용)
            _W = {
                "병동":     "width:72px;",
                "총병상":   "width:52px;",
                "입원":     "width:44px;",
                "재원":     "width:44px;",
                "퇴원":     "width:44px;",
                "금일예정": "width:58px;",   # 금일퇴원예정
                "익일예정": "width:58px;",   # 익일퇴원예정
                "수술":     "width:44px;",
                "가동률":   "width:56px;",
                "잔여":     "width:50px;",
                "익일가용": "width:56px;",
            }
            _th = (
                f'<th style="{_tH}{_W["병동"]}text-align:left;">병동</th>'
                f'<th style="{_tH}{_W["총병상"]}text-align:right;">총병상</th>'
                f'<th style="{_tH}{_W["입원"]}text-align:right;">입원</th>'
                f'<th style="{_tH}{_W["재원"]}text-align:right;">재원</th>'
                f'<th style="{_tH}{_W["퇴원"]}text-align:right;">퇴원</th>'
                f'<th style="{_tH}{_W["금일예정"]}text-align:right;color:#0284C7;">금일<br>퇴원예정</th>'
                f'<th style="{_tH}{_W["익일예정"]}text-align:right;color:#7C3AED;">익일<br>퇴원예정</th>'
                f'<th style="{_tH}{_W["수술"]}text-align:right;color:#8B5CF6;">수술</th>'
                f'<th style="{_tH}{_W["가동률"]}text-align:right;">가동률</th>'
                f'<th style="{_tH}{_W["잔여"]}text-align:right;">잔여<br>병상</th>'
                f'<th style="{_tH}{_W["익일가용"]}text-align:right;color:#059669;">익일<br>가용</th>'
            )
            rows_html = ""
            if bed_detail_f:
                for i, r in enumerate(bed_detail_f):
                    bg    = "#F8FAFC" if i % 2 == 0 else "#FFFFFF"
                    rate  = _safe_float(r.get("가동률"))
                    adm   = _safe_int(r.get("금일입원"))
                    stay  = _safe_int(r.get("재원수"))
                    disc  = _safe_int(r.get("금일퇴원"))
                    tot   = _safe_int(r.get("총병상"))
                    rest  = max(0, tot - stay)
                    t_disc  = _safe_int(r.get("금일퇴원예고"))  # 금일퇴원예정
                    n_disc  = _safe_int(r.get("익일퇴원예약"))  # 익일퇴원예정
                    n_avail = max(0, rest + n_disc)
                    r_cls = "#DC2626" if rate >= 90 else "#F59E0B" if rate >= 80 else "#059669"
                    _td = f"padding:7px 6px;background:{bg};border-bottom:1px solid #F1F5F9;vertical-align:middle;"
                    # 공통 숫자 셀 스타일
                    _num = f"{_td}text-align:right;font-family:Consolas,monospace;font-size:13px;"
                    _surg_cnt = _ward_surg.get(r.get("병동명", ""), 0)
                    rows_html += (
                        f"<tr>"
                        # 병동명 — 진하게, 좌측정렬
                        f'<td style="{_td}color:#0F172A;font-weight:700;font-size:13px;">{r.get("병동명", "")}</td>'
                        # 총병상 — 회색 (변하지 않는 기준값)
                        f'<td style="{_num}color:#94A3B8;font-weight:500;">{tot}</td>'
                        # 금일입원 — 파랑 강조
                        f'<td style="{_num}color:#1D4ED8;font-weight:700;">{adm}</td>'
                        # 재원수 — 진하게 (핵심 수치)
                        f'<td style="{_num}color:#0F172A;font-weight:700;">{stay}</td>'
                        # 금일퇴원 — 중간 회색
                        f'<td style="{_num}color:#475569;font-weight:500;">{disc}</td>'
                        # 금일퇴원예정 — 청록 (오늘 확보 가능)
                        f'<td style="{_num}color:#0284C7;font-weight:{"700" if t_disc > 0 else "400"};">{t_disc if t_disc > 0 else "─"}</td>'
                        # 익일퇴원예정 — 보라 (내일 기준)
                        f'<td style="{_num}color:#7C3AED;font-weight:{"700" if n_disc > 0 else "400"};">{n_disc if n_disc > 0 else "─"}</td>'
                        # 수술 — 연보라 (있을 때만 강조)
                        f'<td style="{_num}color:{"#7C3AED" if _surg_cnt > 0 else "#CBD5E1"};font-weight:{"700" if _surg_cnt > 0 else "400"};">{_surg_cnt if _surg_cnt > 0 else "─"}</td>'
                        # 가동률 — 색상 분기 (위험/주의/정상)
                        f'<td style="{_num}color:{r_cls};font-weight:700;">{rate:.1f}%</td>'
                        # 잔여병상 — 색상 분기 (부족할수록 붉게)
                        f'<td style="{_num}color:{"#DC2626" if rate >= 95 else "#F59E0B" if rate >= 85 else "#16A34A"};font-weight:700;">{rest}</td>'
                        # 익일가용 — 녹색 (여유있을 때) / 회색 (없을 때)
                        f'<td style="{_num}color:{"#059669" if n_avail > 0 else "#94A3B8"};font-weight:{"700" if n_avail > 0 else "400"};">{n_avail}</td></tr>'
                    )
                _tb    = sum(_safe_int(r.get("총병상")) for r in bed_detail_f)
                _ta    = sum(_safe_int(r.get("금일입원")) for r in bed_detail_f)
                _ts    = sum(_safe_int(r.get("재원수")) for r in bed_detail_f)
                _td2   = sum(_safe_int(r.get("금일퇴원")) for r in bed_detail_f)
                _ttdc  = sum(_safe_int(r.get("금일퇴원예고")) for r in bed_detail_f)  # 금일퇴원예정 합계
                _tndc  = sum(_safe_int(r.get("익일퇴원예약")) for r in bed_detail_f)  # 익일퇴원예정 합계
                _tr    = round(_ts / max(_tb, 1) * 100, 1)
                _sth   = "padding:7px 6px;background:#EFF6FF;border-top:2px solid #BFDBFE;vertical-align:middle;font-weight:700;font-size:13px;font-family:Consolas,monospace;"
                rows_html += (
                    f"<tr>"
                    f'<td style="{_sth}color:#1E40AF;">합계</td>'
                    f'<td style="{_sth}text-align:right;color:#1E40AF;font-family:Consolas,monospace;">{_tb}</td>'
                    f'<td style="{_sth}text-align:right;color:{C["primary_text"]};font-family:Consolas,monospace;">{_ta}</td>'
                    f'<td style="{_sth}text-align:right;color:#0F172A;font-family:Consolas,monospace;">{_ts}</td>'
                    f'<td style="{_sth}text-align:right;color:#64748B;font-family:Consolas,monospace;">{_td2}</td>'
                    f'<td style="{_sth}text-align:right;color:#0891B2;font-family:Consolas,monospace;">{_ttdc if _ttdc > 0 else "─"}</td>'
                    f'<td style="{_sth}text-align:right;color:#7C3AED;font-family:Consolas,monospace;">{_tndc if _tndc > 0 else "─"}</td>'
                    f'<td style="{_sth}text-align:right;color:#8B5CF6;font-family:Consolas,monospace;">{sum(_ward_surg.values()) or "─"}</td>'
                    f'<td style="{_sth}text-align:right;color:#1E40AF;font-family:Consolas,monospace;">{_tr:.1f}%</td>'
                    f'<td style="{_sth}text-align:right;font-family:Consolas,monospace;color:#1E40AF;">{max(0, _tb - _ts)}</td>'
                    f'<td style="{_sth}text-align:right;font-weight:700;color:#059669;font-family:Consolas,monospace;">{max(0, (_tb - _ts) + _tndc)}</td></tr>'
                )
                body = (
                    f'<div style="overflow-x:auto;">'
                    f'<table style="width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed;">'
                    f"<thead><tr>{_th}</tr></thead><tbody>{rows_html}</tbody></table></div>"
                    f'<div style="display:flex;align-items:center;justify-content:space-between;'
                    f'padding:5px 6px 0;border-top:1px solid #F1F5F9;margin-top:4px;flex-wrap:wrap;gap:4px;">'
                    f'<div style="display:flex;align-items:center;gap:8px;">'
                    f'<span style="font-size:10px;color:#94A3B8;font-weight:600;">가동률 기준</span>'
                    f'<span style="font-size:10px;color:#059669;font-weight:700;">■ 정상 &lt;80%</span>'
                    f'<span style="font-size:10px;color:#F59E0B;font-weight:700;">■ 주의 80~90%</span>'
                    f'<span style="font-size:10px;color:#DC2626;font-weight:700;">■ 위험 ≥90%</span>'
                    f"</div>"
                    f'<div style="display:flex;align-items:center;gap:0;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:5px;padding:2px 0;">'
                    f'<span style="font-size:9.5px;font-weight:700;color:#64748B;padding:0 8px;border-right:1px solid #E2E8F0;">📋 익일 예약</span>'
                    f'<span style="display:inline-flex;align-items:center;gap:3px;padding:0 8px;border-right:1px solid #E2E8F0;">'
                    f'<span style="font-size:9.5px;color:#64748B;">입원</span>'
                    f'<b style="font-size:11px;color:{C["primary_text"]};font-family:Consolas,monospace;">{_next_adm}명</b></span>'
                    f'<span style="display:inline-flex;align-items:center;gap:3px;padding:0 8px;border-right:1px solid #E2E8F0;">'
                    f'<span style="font-size:9.5px;color:#64748B;">가용</span>'
                    f'<b style="font-size:11px;color:{_cap_sum_c2};font-family:Consolas,monospace;">{_total_avail}개</b></span>'
                    f'<span style="display:inline-flex;align-items:center;gap:3px;padding:0 8px;'
                    f"background:{'#FEF2F2' if _adm_cap_pct >= 90 else '#FFFBEB' if _adm_cap_pct >= 70 else '#F0FDF4'};"
                    f'border-radius:0 4px 4px 0;">'
                    f'<span style="font-size:9.5px;color:#64748B;">수용률</span>'
                    f'<b style="font-size:12px;color:{_adm_cap_color};font-family:Consolas,monospace;font-weight:800;">{_adm_cap_pct}%</b>'
                    f"</span></div></div>"
                )
            else:
                body = (
                    '<div style="padding:40px 20px;text-align:center;color:#94A3B8;">'
                    '<div style="font-size:24px;margin-bottom:8px;">🏥</div>'
                    '<div style="font-size:13px;font-weight:600;color:#64748B;">병동 현황 데이터 없음</div></div>'
                )
            st.markdown(body, unsafe_allow_html=True)

        else:
            # bar_h 또는 heatmap: 인라인 렌더러 사용
            _render_ward_alt_chart(bed_detail_f, chart_type_ward, _ward_surg)

        st.markdown("</div>", unsafe_allow_html=True)

    # ── [v2.2] 진료과별 재원 구성 ────────────────────────────────────
    with col_R:
        st.markdown('<div class="wd-card" style="padding:14px 16px;">', unsafe_allow_html=True)

        _gw_p2 = st.session_state.get("ward_selected", "전체")

        # ── [v2.2] 섹션 헤더 + pill 선택기 ─────────────────────────────
        chart_type_dept = _chart_selector(
            "dept_stay", "진료과별 재원 구성",
            _gw_p2 if _gw_p2 != "전체" else "",
        )

        # 선택된 타입으로 렌더링 (donut/bar_h/treemap 모두 _render_dept_chart 처리)
        _render_dept_chart(dept_stay_f, chart_type_dept)

        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    st.markdown('</div>', unsafe_allow_html=True)  # /wd-row-chart
    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    # ── Row 4: 주상병 분석 ──────────────────────────────────────
    st.markdown('<div class="wd-row-chart">', unsafe_allow_html=True)
    # [v2.2] 최근 7일 / 금일 vs 전일 — 차트 선택기 통합
    # ════════════════════════════════════════════════════════════
    from collections import defaultdict as _dd

    col_pie, col_bar = st.columns([1, 1], gap="small")

    # ── [v2.2] 최근 7일 입원 주상병 분포 ─────────────────────────────
    with col_pie:
        st.markdown('<div class="wd-card" style="padding:14px 16px;">', unsafe_allow_html=True)

        chart_type_dx7 = _chart_selector("dx_7day", "최근 7일 입원 주상병 분포")
        _render_dx7_chart(dx_trend, chart_type_dx7)

        st.markdown("</div>", unsafe_allow_html=True)

    # ── [v2.2] 금일 vs 전일 입원 주상병 분포 ─────────────────────────
    with col_bar:
        st.markdown('<div class="wd-card" style="padding:14px 16px;">', unsafe_allow_html=True)

        chart_type_compare = _chart_selector("dx_compare", "금일 vs 전일 입원 주상병 분포")
        _render_dx_compare_chart(dx_today, chart_type_compare)

        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    st.markdown('</div>', unsafe_allow_html=True)  # /wd-row-chart
    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    # ── Row 5: AI 분석 채팅 ─────────────────────────────────────
    # ════════════════════════════════════════════════════════════
    # ════════════════════════════════════════════════════════════
    # 익일 입원 예약 상세 (하단 고정 표시)
    # ════════════════════════════════════════════════════════════
    st.markdown(
        f'<div class="wd-card" style="margin-bottom:8px;">'
        f'<div class="wd-sec"><span class="wd-sec-accent"></span>'
        f'익일 입원 예약 상세<span class="wd-sec-sub">{_next_adm}명 · 진료과/성별/연령 분포</span></div>',
        unsafe_allow_html=True,
    )
    if admit_cands and HAS_PLOTLY:
        from collections import defaultdict as _ddc
        _dept_m: dict = _ddc(int)
        _dept_f: dict = _ddc(int)
        _age_bins = {"10대이하": 0, "20대": 0, "30대": 0, "40대": 0, "50대": 0, "60대": 0, "70대이상": 0}
        for _ac in admit_cands:
            _dn  = _ac.get("진료과명", "기타")
            _sx  = _ac.get("성별", "M")
            _age = int(_ac.get("나이", 0) or 0)
            if _sx == "M":
                _dept_m[_dn] += 1
            else:
                _dept_f[_dn] += 1
            _ab = "70대이상" if _age >= 70 else f"{(_age // 10) * 10}대" if _age >= 20 else "10대이하"
            if _ab in _age_bins:
                _age_bins[_ab] += 1
        _all_depts = sorted(set(list(_dept_m) + list(_dept_f)))
        _m_vals = [_dept_m.get(d, 0) for d in _all_depts]
        _f_vals = [_dept_f.get(d, 0) for d in _all_depts]
        # plotly.graph_objects 는 최상단 go 사용
        _fig_adm = go.Figure()
        _fig_adm.add_trace(go.Bar(name="남성", x=_all_depts, y=_m_vals, marker_color="#3B82F6", text=_m_vals, textposition="outside", textfont=dict(size=11, color="#1E40AF")))
        _fig_adm.add_trace(go.Bar(name="여성", x=_all_depts, y=_f_vals, marker_color="#F472B6", text=_f_vals, textposition="outside", textfont=dict(size=11, color="#9D174D")))
        _fig_adm.update_layout(
            barmode="group", height=210, margin=dict(l=0, r=0, t=16, b=8),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#333333", size=11),
            legend=dict(orientation="h", y=1.12, x=0.5, xanchor="center", font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(tickfont=dict(size=11), gridcolor="rgba(0,0,0,0)"),
            yaxis=dict(gridcolor="rgba(226,232,240,0.5)", tickfont=dict(size=10), zeroline=False),
            bargap=0.25, bargroupgap=0.05,
        )
        _col_bar_adm, _col_age_adm = st.columns([3, 2], gap="small")
        with _col_bar_adm:
            st.plotly_chart(_fig_adm, use_container_width=True, key="ward_adm_bar")
        with _col_age_adm:
            _age_html = (
                '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
                '<tr style="background:#F8FAFC;">'
                '<th style="padding:7px 10px;color:#64748B;font-size:11px;text-align:left;">연령대</th>'
                '<th style="padding:7px 10px;color:#64748B;font-size:11px;text-align:right;">인원</th>'
                '<th style="padding:7px 10px;color:#64748B;font-size:11px;">비율</th></tr>'
            )
            _total_a = max(sum(_age_bins.values()), 1)
            for _ab, _ac2 in _age_bins.items():
                _pct = _ac2 / _total_a * 100
                _age_html += (
                    f'<tr style="border-bottom:1px solid #F8FAFC;">'
                    f'<td style="padding:6px 10px;font-weight:500;color:#0F172A;">{_ab}</td>'
                    f'<td style="padding:6px 10px;text-align:right;font-weight:700;color:#1E40AF;font-family:Consolas,monospace;">{_ac2}</td>'
                    f'<td style="padding:6px 10px;">'
                    f'<div style="display:flex;align-items:center;gap:4px;">'
                    f'<div style="flex:1;height:6px;background:#F1F5F9;border-radius:3px;">'
                    f'<div style="width:{int(_pct)}%;height:100%;background:#3B82F6;border-radius:3px;"></div>'
                    f'</div><span style="font-size:11px;color:#64748B;">{_pct:.0f}%</span>'
                    f"</div></td></tr>"
                )
            _age_html += "</table>"
            st.markdown(
                f'<div style="padding-top:8px;">'
                f'<div style="font-size:11px;font-weight:700;color:#64748B;margin-bottom:6px;text-transform:uppercase;letter-spacing:.07em;">연령대 분포</div>'
                f"{_age_html}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            '<div style="padding:32px;text-align:center;color:#94A3B8;">'
            '<div style="font-size:28px;margin-bottom:8px;">📋</div>'
            '<div style="font-size:13px;font-weight:600;color:#64748B;">예약 환자 데이터 없음</div></div>',
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)





# ── LLM 채팅 ────────────────────────────────────────────────────────

def _render_ward_llm_chat(
    kpi: Dict,
    bed_occ: List[Dict],
    bed_detail: List[Dict],
    op_stat: List[Dict],
) -> None:
    """
    병동 현황 AI 분석 채팅 v2.3

    [개선 사항]
    - 빠른 질문 버튼: 자주 쓰는 분석 질문을 원클릭으로 입력
    - 컨텍스트 요약 배지: 현재 KPI를 채팅 상단에 표시
    - 스트리밍 응답 유지
    - PII 마스킹 유지
    """
    import re as _re

    # ── 시스템 프롬프트 구성 ─────────────────────────────────────────
    _occ   = kpi.get("가동률", 0) or 0
    _stay  = kpi.get("재원수", 0) or 0
    _beds  = kpi.get("총병상", 0) or 0
    _adm   = kpi.get("금일입원", 0) or 0
    _disc  = kpi.get("금일퇴원", 0) or 0
    _ward  = kpi.get("선택병동", "전체")

    _ctx_data = {
        "기준시각": time.strftime("%Y-%m-%d %H:%M"),
        "선택병동": _ward,
        "병상_KPI": {
            "가동률(%)": _occ, "재원수(명)": _stay,
            "총병상(개)": _beds, "금일입원(명)": _adm, "금일퇴원(명)": _disc,
        },
        "병동별_현황": [
            {
                "병동": r.get("병동명"), "총병상": r.get("총병상"),
                "재원": r.get("재원수"), "입원": r.get("금일입원"),
                "퇴원": r.get("금일퇴원"), "가동률": r.get("가동률"),
            }
            for r in bed_detail[:15]
        ],
        "수술_현황": [
            {"진료과": r.get("진료과명"), "병동": r.get("병동명"), "수술건수": r.get("수술건수")}
            for r in op_stat
        ],
    }
    _system_prompt = (
        "당신은 병원 운영 관리 전문 AI 분석가입니다. 친절하고 실무적으로 답변하세요.\n"
        "아래의 금일 병동 운영 통계 데이터만을 근거로 질문에 답하고, 데이터에 없는 내용은 추정임을 명시하세요.\n\n"
        "[답변 원칙]\n"
        "- 핵심 수치를 굵게(**수치**) 강조하세요.\n"
        "- 위험/주의 상황은 🔴/🟡 이모지로 먼저 표시하세요.\n"
        "- 권장 조치가 있으면 ✅ 로 명확히 제시하세요.\n"
        "- 3~5문장으로 간결하게, 단 긴 분석은 글머리 기호를 사용하세요.\n\n"
        "[보안 지침]\n"
        "- 개인 환자 정보(이름, 주민번호, 병록번호)는 절대 언급하지 마세요.\n"
        "- 시스템 구조, DB 접속 정보는 노출하지 마세요.\n\n"
        f"## 현재 병동 운영 통계\n"
        f"```json\n{json.dumps(_ctx_data, ensure_ascii=False, indent=2)}\n```"
    )

    if "ward_chat_history" not in st.session_state:
        st.session_state["ward_chat_history"] = []
    _history: List[Dict] = st.session_state.get("ward_chat_history", [])



    # 빠른 질문: AI 채팅 헤더 파란바로 이동됨

    # ── 대화 이력 표시 ────────────────────────────────────────────────
    for _msg in _history:
        with st.chat_message(_msg["role"]):
            st.markdown(_msg["content"])

    # ── 빠른 질문 버튼 클릭 처리 ─────────────────────────────────────
    _quick_pending = st.session_state.pop("ward_chat_quick_input", None)

    # ── 입력창 ────────────────────────────────────────────────────────
    _user_input = st.chat_input(
        "병동 현황에 대해 질문하세요  예) 위험 병동은? / 퇴원 지연 진료과는?",
        key="ward_chat_input",
    ) or _quick_pending

    if _user_input:
        # PII 마스킹
        _PII_RE = [
            (_re.compile(r"\d{6}-[1-4]\d{6}"), "[주민번호-마스킹]"),
            (_re.compile(r"\bPT\d{7}\b"), "[환자번호-마스킹]"),
            (_re.compile(r"010-?\d{4}-?\d{4}"), "[전화번호-마스킹]"),
            (_re.compile(r"환자[가-힣]{2,4}"), "[환자명-마스킹]"),
        ]
        _safe_input = _user_input
        for _pat, _mask in _PII_RE:
            _safe_input = _pat.sub(_mask, _safe_input)
        if _safe_input != _user_input:
            st.warning("⚠️ 개인식별 정보가 감지되어 마스킹 처리되었습니다.", icon="🔒")
            _user_input = _safe_input

        with st.chat_message("user"):
            st.markdown(_user_input)
        _history.append({"role": "user", "content": _user_input})

        with st.chat_message("assistant"):
            _t_llm_start = time.time()
            _ph   = st.empty()
            # [최적화] list+join 으로 O(n²) → O(n) 스트리밍
            _toks: list = []
            _tok_cnt = 0
            _last_render = time.time()
            # 시스템 프롬프트 크기 제한 (4000자) → LLM 비용·속도 최적화
            _safe_prompt = (_system_prompt[:4000] + "...(생략)"
                            if len(_system_prompt) > 4000 else _system_prompt)
            _full = ""
            try:
                from core.llm import get_llm_client
                _llm    = get_llm_client()
                _req_id = str(uuid.uuid4())[:8]
                for _tok in _llm.generate_stream(_user_input, _safe_prompt, request_id=_req_id):
                    _toks.append(_tok)
                    _tok_cnt += 1
                    # 4토큰마다 또는 0.1초마다 렌더 (time.time 호출 최소화)
                    if _tok_cnt % 4 == 0 or (time.time() - _last_render) > 0.1:
                        _ph.markdown("".join(_toks) + "▌")
                        _last_render = time.time()
                _full = "".join(_toks)
            except Exception as _e:
                _full = (
                    f"**LLM 연결 실패**\n\n"
                    f"오류: `{_e}`\n\n"
                    f"현재 데이터 요약:\n"
                    f"- 가동률: **{_occ:.1f}%** ({'🔴 위험' if _occ>=90 else '🟡 주의' if _occ>=80 else '🟢 정상'})\n"
                    f"- 재원: **{_stay}명** / {_beds}병상\n"
                    f"- 금일 입원: **{_adm}명** / 퇴원: **{_disc}명**"
                )
                logger.warning(f"[Ward Chat LLM] {_e}")
            _ph.markdown(_full)

        _history.append({"role": "assistant", "content": _full})
        st.session_state["ward_chat_history"] = _history
        _llm_elapsed = int((time.time() - _t_llm_start) * 1000) if "_t_llm_start" in dir() else 0
        _DASH_MON.log_llm_query(
            question=_user_input,
            elapsed_ms=_llm_elapsed,
            success=("LLM 연결 실패" not in _full),
        )
        st.rerun()


# ── 원무 대시보드 ────────────────────────────────────────────────────

def _render_finance() -> None:
    kpi      = (_qc("finance_kpi") or [{}])[0]
    overdue  = _qc("finance_overdue")
    by_ins   = _qc("finance_by_insurance")
    outpat   = int(kpi.get("외래수납", 0) or 0)
    inpat    = int(kpi.get("입원수납", 0) or 0)
    total_s  = int(kpi.get("총수납", 0) or 0)
    total_od = sum(_safe_int(r.get("미수금액")) for r in overdue)
    c1, c2, c3, c4 = st.columns(4)
    _kpi_card("외래 수납", f"{outpat / 1_000_000:.1f}", "백만", "목표 65M 대비 달성률", C["blue"], c1)
    _kpi_card("입원 수납", f"{inpat / 1_000_000:.1f}", "백만", "전일 대비 변동", C["green"], c2)
    _kpi_card("미수금 잔액", f"{total_od / 1_000_000:.1f}", "백만", "30일+ 집중 관리 필요", C["coral"], c3)
    _kpi_card("총 수납", f"{total_s / 1_000_000:.1f}", "백만", "외래+입원 합계", C["sky"], c4)
    col_ins, col_od = st.columns([1, 2])
    with col_ins:
        _section_title("보험 유형별 수납")
        if by_ins and HAS_PLOTLY:
            INS_LABEL = {"C1": "건강보험", "MD": "의료급여", "CA": "자동차보험", "WC": "산재보험", "GN": "일반"}
            labels = [INS_LABEL.get(r["급종코드"], r["급종코드"]) for r in by_ins]
            values = [_safe_int(r.get("수납금액")) for r in by_ins]
            colors = [C["blue"], C["green"], C["amber"], C["coral"], "#666"]
            fig = go.Figure(go.Pie(
                labels=labels, values=values, hole=0.65,
                marker=dict(colors=colors[:len(labels)], line=dict(width=0)),
                textinfo="label+percent", textfont=dict(size=10, color="rgba(255,255,255,0.8)"),
            ))
            fig.update_layout(height=200, margin=dict(l=0, r=0, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", showlegend=False)
            st.plotly_chart(fig, use_container_width=True, key="finance_pie")
    with col_od:
        _section_title("미수금 현황")
        for r in overdue:
            amt  = _safe_int(r.get("미수금액"))
            days = _safe_int(r.get("최장경과일"))
            st_text = "위험" if days >= 30 else ("주의" if days >= 14 else "정상")
            sc  = C["coral"] if st_text == "위험" else C["amber"] if st_text == "주의" else C["green"]
            sbg = C["coral_bg"] if st_text == "위험" else C["amber_bg"] if st_text == "주의" else C["green_bg"]
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'padding:6px 0;border-bottom:1px solid {C["border"]};font-size:12px;">'
                f'<span style="color:{C["t2"]};min-width:70px;">{r.get("진료과", "")}</span>'
                f'<span style="color:{C["t1"]};font-family:Consolas,monospace;">{amt:,}원</span>'
                f'<span style="color:#64748B;font-family:Consolas,monospace;">{days}일</span>'
                f'<span style="background:{sbg};color:{sc};padding:2px 8px;border-radius:3px;font-weight:600;font-size:11px;">{st_text}</span></div>',
                unsafe_allow_html=True,
            )


# ── 외래 대시보드 ────────────────────────────────────────────────────

def _render_opd() -> None:
    kpi     = (_qc("opd_kpi") or [{}])[0]
    by_dept = _qc("opd_by_dept")
    hourly  = _qc("opd_hourly")
    noshow  = (_qc("opd_noshow") or [{}])[0]
    total    = int(kpi.get("총내원", 0) or 0)
    new_rate = float(kpi.get("초진율", 0) or 0)
    ns_rate  = float(noshow.get("노쇼율", 0) or 0)
    c1, c2, c3, c4 = st.columns(4)
    _kpi_card("금일 외래", str(total), "명", "전일 대비 변동", C["blue"], c1)
    _kpi_card("예약 이행률", f"{100 - ns_rate:.1f}", "%", f"No-show {ns_rate}% (목표 ≤10%)", C["coral"] if ns_rate > 10 else C["green"], c2)
    _kpi_card("초진 비율", f"{new_rate}", "%", f"재진 {100 - new_rate:.1f}%", C["green"], c3)
    _kpi_card("평균 대기", "22", "분", "목표 20분 기준", C["amber"], c4)
    col_h, col_top = st.columns([6, 4])
    with col_h:
        _section_title("시간대별 내원 패턴")
        if hourly and HAS_PLOTLY:
            labels = [r["시간대"] for r in hourly if _safe_int(r.get("내원수")) > 0]
            values = [_safe_int(r.get("내원수")) for r in hourly if _safe_int(r.get("내원수")) > 0]
            colors = ["rgba(255,123,123,0.8)" if v >= 200 else "rgba(91,156,246,0.8)" if v >= 150 else "rgba(91,156,246,0.4)" for v in values]
            fig = go.Figure(go.Bar(x=labels, y=values, marker_color=colors, marker=dict(line=dict(width=0))))
            fig.update_layout(height=200, margin=dict(l=0, r=0, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color=C["t2"], size=10), xaxis=dict(gridcolor="#F1F5F9", tickfont=dict(size=10)), yaxis=dict(gridcolor="#F1F5F9", tickfont=dict(size=10)), showlegend=False)
            st.plotly_chart(fig, use_container_width=True, key="opd_hourly_chart")
    with col_top:
        _section_title("진료과별 환자수 TOP 5")
        top5_colors = [C["blue"], C["green"], C["amber"], C["coral"], C["sky"]]
        max_cnt = max((_safe_int(r.get("환자수")) for r in by_dept[:5]), default=1)
        for i, row in enumerate(by_dept[:5]):
            cnt = int(row.get("환자수", 0) or 0)
            col = top5_colors[i % len(top5_colors)]
            pct = cnt / max_cnt * 100
            st.markdown(
                f'<div style="margin-bottom:10px;">'
                f'<div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;">'
                f'<span style="color:{col};font-weight:600;margin-right:6px;">{i + 1}</span>'
                f'<span style="color:{C["t2"]};flex:1;">{row.get("진료과", "")}</span>'
                f'<span style="color:{C["t1"]};font-family:Consolas,monospace;">{cnt}명</span></div>'
                f'<div style="width:100%;height:4px;background:rgba(255,255,255,0.07);border-radius:2px;">'
                f'<div style="width:{pct:.0f}%;height:100%;background:{col};border-radius:2px;"></div>'
                f"</div></div>",
                unsafe_allow_html=True,
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 렌더러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_hospital_dashboard(tab: str = "ward") -> None:
    """
    병원 현황판 메인 렌더러 v4.2

    [v4.2 신규]
    - 사이드바 차트 초기화 버튼 추가 (HAS_CHART_MODULES=True 일 때)

    [v4.1 개선]
    - Oracle ping: 세션당 1회 (이전: 렌더마다 2회)
    - 새로고침: oracle_ok 삭제 + st.cache_data.clear()
    """
    # ── Oracle 연결 체크 — 5분 캐시 + 실패 시 1분 재체크 ──────────────
    # 이전: 세션당 1회 → 연결 복구 후에도 False 고정
    # 개선: 성공 시 5분, 실패 시 1분 후 자동 재체크
    _oracle_check_key  = "oracle_ok"
    _oracle_expire_key = "oracle_ok_expire"
    _now_ts = time.time()
    _oracle_expired = _now_ts > st.session_state.get(_oracle_expire_key, 0)

    oracle_ok = False
    if _oracle_check_key not in st.session_state or _oracle_expired:
        try:
            from db.oracle_client import test_connection
            oracle_ok, _ = test_connection()
            # 성공: 5분 후 재체크 / 실패: 60초 후 재체크
            _expire = _now_ts + (300 if oracle_ok else 60)
        except Exception:
            oracle_ok = False
            _expire = _now_ts + 60
        st.session_state[_oracle_check_key]  = oracle_ok
        st.session_state[_oracle_expire_key] = _expire
    else:
        oracle_ok = st.session_state[_oracle_check_key]

    _ts       = time.strftime("%Y-%m-%d %H:%M")
    _tab_names = {"ward": "병동 대시보드", "finance": "원무 대시보드", "opd": "외래 대시보드"}
    _tab_name  = _tab_names.get(tab, "병동 대시보드")
    _ss_key    = f"dash_last_refresh_{tab}"
    if _ss_key not in st.session_state:
        st.session_state[_ss_key] = _ts

    # ── 병동 목록 선제 로드 ──────────────────────────────────────────
    if tab == "ward" and "ward_name_list" not in st.session_state:
        try:
            _pre_bed   = _qc("ward_bed_detail")
            _pre_wards = ["전체"] + sorted({r.get("병동명", "") for r in _pre_bed if r.get("병동명", "") and r.get("병동명", "") != "전체"})
            st.session_state["ward_name_list"] = _pre_wards
        except Exception:
            st.session_state["ward_name_list"] = ["전체"]

    _o_color = "#16A34A" if oracle_ok else "#F59E0B"
    _o_label = "Oracle 연결 정상" if oracle_ok else "데모 데이터"

    # ── 탑바: ① 순수 HTML 그라데이션 배너 (제목+상태) ─────────────
    # 인터랙티브 요소(selectbox/button)는 배너 밖 별도 행으로 분리
    # → div 래퍼 탈출 문제 완전 해결
    _glow_c = "rgba(74,222,128,0.6)" if oracle_ok else "rgba(251,191,36,0.6)"
    _dot_c  = "#4ADE80" if oracle_ok else "#FBBF24"
    st.markdown(
        f'<div style="'
        f'background:linear-gradient(135deg,#0F2A6B 0%,#1E3A8A 30%,#1E40AF 60%,#2563EB 100%);'
        f'border-radius:14px;padding:12px 22px;margin-bottom:6px;'
        f'box-shadow:0 4px 20px rgba(30,64,175,0.28);'
        f'display:flex;align-items:center;justify-content:space-between;">'
        # 좌: 병원명 + 대시보드명
        f'<div style="display:flex;align-items:center;gap:12px;">'
        f'<div style="width:3px;height:28px;background:rgba(255,255,255,0.7);border-radius:2px;"></div>'
        f'<div>'
        f'<div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.60);'
        f'text-transform:uppercase;letter-spacing:.18em;margin-bottom:3px;">좋은문화병원</div>'
        f'<div style="font-size:20px;font-weight:800;color:#FFFFFF;letter-spacing:-0.03em;'
        f'line-height:1;">{_tab_name}</div>'
        f'</div></div>'
        # 우: Oracle 상태
        f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;">'
        f'<div style="display:flex;align-items:center;gap:6px;">'
        f'<span style="width:8px;height:8px;border-radius:50%;background:{_dot_c};'
        f'display:inline-block;box-shadow:0 0 7px {_glow_c};"></span>'
        f'<span style="font-size:12px;font-weight:700;color:rgba(255,255,255,0.95);">{_o_label}</span>'
        f'<span style="color:rgba(255,255,255,0.30);font-size:11px;">|</span>'
        f'<span style="font-size:11px;color:rgba(255,255,255,0.70);font-family:Consolas,monospace;">'
        f'갱신 {st.session_state[_ss_key]}</span>'
        f'</div>'
        f'<div style="font-size:10px;color:rgba(255,255,255,0.45);">'
        f'AI 병동 분석 — 하단에서 질문하세요</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 탑바: ② 인터랙티브 버튼 행 (배너 바로 아래) ──────────────────
    # 배너와 버튼 행 사이 여백 + 구분선
    st.markdown(
        '<div style="height:6px;border-bottom:1px solid #E2E8F0;margin-bottom:8px;"></div>',
        unsafe_allow_html=True,
    )
    _c_ward_sel, _c_btns_bar = st.columns([3, 7], vertical_alignment="center", gap="small")
    with _c_ward_sel:
        if tab == "ward":
            _ward_name_list = st.session_state.get("ward_name_list", ["전체"])
            _cur_ward       = st.session_state.get("ward_selected", "전체")
            _sel = st.selectbox(
                "병동", options=_ward_name_list,
                index=_ward_name_list.index(_cur_ward) if _cur_ward in _ward_name_list else 0,
                key="global_ward_selector", label_visibility="collapsed",
                help="선택한 병동의 데이터만 모든 차트에 반영됩니다",
            )
            if _sel != st.session_state.get("ward_selected"):
                _DASH_MON.log_action("ward_filter", label=_sel)
                st.session_state["ward_selected"] = _sel
                st.rerun()
    with _c_btns_bar:
        _bx1, _bx2, _bx3 = st.columns([2, 2, 6], gap="small")
        with _bx1:
            _rm_panel_open = st.session_state.get("show_room_panel", False)
            if st.button("병실현황" if not _rm_panel_open else "▲ 닫기",
                         key="btn_room_panel", type="secondary", use_container_width=True):
                st.session_state["show_room_panel"] = not _rm_panel_open
                st.rerun()
        with _bx2:
            if st.button("🔄 새로고침", key=f"dash_refresh_{tab}",
                         type="secondary", use_container_width=True):
                _DASH_MON.log_action("refresh", label=tab)
                st.session_state.pop("oracle_ok", None)
                st.session_state.pop("oracle_ok_expire", None)
                for _k in list(st.session_state.keys()):
                    if _k.startswith("_ttl_jitter_"):
                        del st.session_state[_k]
                st.cache_data.clear()
                st.session_state[_ss_key] = time.strftime("%Y-%m-%d %H:%M")
                st.rerun()
        # _bx3은 빈 공간 (우측 정렬 효과)

    st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)

    if tab == "ward":
        _render_ward()
    elif tab == "finance":
        _render_finance()
    else:
        _render_opd()