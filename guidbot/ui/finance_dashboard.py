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
# [보안 원칙] SELECT * 사용 시 개인정보 노출은 VIEW 설계로 방어
# · 각 VIEW는 집계/통계 컬럼만 노출 (환자명·주민번호·전화번호 제외)
# · ward_room_detail: 성별/진료과만 포함 (환자명 제외) — VIEW에서 보장
# · V_KIOSK_CARD_APPROVAL: 승인번호·금액만 (카드번호 원본 제외) — VIEW에서 보장
# · LLM 컨텍스트: _render_finance_llm_chat()에서 집계값만 전달
# · 카드 매칭 결과: LLM 채팅에 미전달 (승인번호·카드번호 개인정보)
FQ: Dict[str, str] = {
    "opd_kpi": "SELECT * FROM JAIN_WM.V_OPD_KPI WHERE ROWNUM = 1",
    "opd_dept_status": "SELECT * FROM JAIN_WM.V_OPD_DEPT_STATUS ORDER BY 대기 DESC",
    "kiosk_status": "SELECT * FROM JAIN_WM.V_KIOSK_STATUS ORDER BY 키오스크ID",
    # 신규: 키오스크/창구 7일 추세 + 진료과별 수납
    "kiosk_by_dept": "SELECT * FROM JAIN_WM.V_KIOSK_BY_DEPT ORDER BY 수납건수 DESC",
    "kiosk_counter_trend": "SELECT * FROM JAIN_WM.V_KIOSK_COUNTER_TREND ORDER BY 기준일",
    # 진료과별 외래 현황 (실시간)
    "opd_dept_now": "SELECT * FROM JAIN_WM.V_OPD_DEPT_STATUS ORDER BY 합계 DESC",
    # 병동 병실현황
    "ward_room_detail": "SELECT * FROM JAIN_WM.V_WARD_ROOM_DETAIL ORDER BY 병동명, 병실번호",
    # 일일현황 (진료과×보험구분×구분)
    "daily_dept_stat": "SELECT * FROM JAIN_WM.V_DAILY_DEPT_STAT ORDER BY 진료과명, 구분",
    # 세부과 일일집계표 (V_DAY_INWEON_3)
    "day_inweon": """
        SELECT 진료과, 외래계, 입원계, 퇴원계, 재원계,
               "예방(독감)", "예방(AZ,JS,NV)", "예방(MD)", "예방(FZ)", 예방주사계
        FROM (
            SELECT 일자, 진료과, 외래계, 입원계, 퇴원계, 재원계,
                   "예방(독감)", "예방(AZ,JS,NV)", "예방(MD)", "예방(FZ)", 예방주사계
            FROM JAIN_WM.V_DAY_INWEON_3
            UNION ALL
            SELECT 일자, '총합계',SUM(외래계),SUM(입원계),SUM(퇴원계),SUM(재원계),
                   SUM("예방(독감)"),SUM("예방(AZ,JS,NV)"),SUM("예방(MD)"),SUM("예방(FZ)"),SUM(예방주사계)
            FROM JAIN_WM.V_DAY_INWEON_3
            GROUP BY 일자
        )
        WHERE 일자 = TO_CHAR(SYSDATE, 'YYYYMMDD')
        ORDER BY
            CASE WHEN 진료과 = '총합계' THEN 19 ELSE 10 END,
            CASE
                WHEN TRIM(진료과)='*내분비내과' THEN 1 WHEN TRIM(진료과)='*호흡기내과' THEN 2
                WHEN TRIM(진료과)='*소화기내과' THEN 3 WHEN TRIM(진료과)='*신장내과' THEN 4
                WHEN TRIM(진료과)='*순환기내과' THEN 5 WHEN TRIM(진료과)='인공신장실' THEN 6
                WHEN TRIM(진료과)='신경과' THEN 7   WHEN TRIM(진료과)='가정의학과' THEN 8
                WHEN TRIM(진료과)='신경외과' THEN 9  WHEN TRIM(진료과)='*유방센터' THEN 10
                WHEN TRIM(진료과)='*위장관센터' THEN 11 WHEN TRIM(진료과)='*갑상선센터' THEN 12
                WHEN TRIM(진료과)='성형외과' THEN 13 WHEN TRIM(진료과)='정형외과' THEN 14
                WHEN TRIM(진료과)='*OBGY' THEN 15  WHEN TRIM(진료과)='*난임센터' THEN 16
                WHEN TRIM(진료과)='소아청소년과' THEN 17 WHEN TRIM(진료과)='이비인후과' THEN 18
                WHEN TRIM(진료과)='피부과' THEN 19 WHEN TRIM(진료과)='응급의학과' THEN 20
                WHEN TRIM(진료과)='외부의뢰' THEN 21 WHEN TRIM(진료과)='진단검사' THEN 22
                ELSE 23
            END ASC,
            REPLACE(진료과,'*'),
            CASE WHEN SUBSTR(진료과,1,1)='*' THEN 2 ELSE 1 END
    """,

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


# ── 기간 VIEW 쿼리 (v2.1) ────────────────────────────────────────────
# FQ_HIST: 과거 날짜 조회용 쿼리 딕셔너리
#
# [설계 원칙]
#   · FQ    → 오늘(SYSDATE) 기반 실시간 VIEW 쿼리
#   · FQ_HIST → 과거 날짜용 기간 VIEW 쿼리 (날짜 플레이스홀더: {d}, {d_prev}, {d_next})
#
# [플레이스홀더]
#   {d}      : 선택 날짜 YYYYMMDD
#   {d_prev} : 선택 날짜 전일 YYYYMMDD
#   {d_next} : 선택 날짜 익일 YYYYMMDD
#
# [Oracle VIEW 준비]
#   DBeaver에서 scripts/create_views.sql 실행 후 사용 가능.
#   이력 VIEW가 없는 경우 FQ 원본 SQL로 폴백(오늘 데이터 반환 + 경고 로그).
FQ_HIST: Dict[str, str] = {
    # ── 세부과 일일집계표 ──────────────────────────────────────────────
    # V_DAY_INWEON_3 VIEW의 SYSDATE 필터를 제거해야 작동함
    # (VIEW DDL: WHERE 일자 = TO_CHAR(SYSDATE,...) 조건 삭제)
    "day_inweon": """
        SELECT 진료과, 외래계, 입원계, 퇴원계, 재원계,
               "예방(독감)", "예방(AZ,JS,NV)", "예방(MD)", "예방(FZ)", 예방주사계
        FROM (
            SELECT 일자, 진료과, 외래계, 입원계, 퇴원계, 재원계,
                   "예방(독감)", "예방(AZ,JS,NV)", "예방(MD)", "예방(FZ)", 예방주사계
            FROM JAIN_WM.V_DAY_INWEON_3
            UNION ALL
            SELECT 일자, '총합계',SUM(외래계),SUM(입원계),SUM(퇴원계),SUM(재원계),
                   SUM("예방(독감)"),SUM("예방(AZ,JS,NV)"),SUM("예방(MD)"),SUM("예방(FZ)"),SUM(예방주사계)
            FROM JAIN_WM.V_DAY_INWEON_3
            GROUP BY 일자
        )
        WHERE 일자 = '{d}'
        ORDER BY
            CASE WHEN 진료과 = '총합계' THEN 19 ELSE 10 END,
            CASE
                WHEN TRIM(진료과)='*내분비내과' THEN 1 WHEN TRIM(진료과)='*호흡기내과' THEN 2
                WHEN TRIM(진료과)='*소화기내과' THEN 3 WHEN TRIM(진료과)='*신장내과' THEN 4
                WHEN TRIM(진료과)='*순환기내과' THEN 5 WHEN TRIM(진료과)='인공신장실' THEN 6
                WHEN TRIM(진료과)='신경과' THEN 7   WHEN TRIM(진료과)='가정의학과' THEN 8
                WHEN TRIM(진료과)='신경외과' THEN 9  WHEN TRIM(진료과)='*유방센터' THEN 10
                WHEN TRIM(진료과)='*위장관센터' THEN 11 WHEN TRIM(진료과)='*갑상선센터' THEN 12
                WHEN TRIM(진료과)='성형외과' THEN 13 WHEN TRIM(진료과)='정형외과' THEN 14
                WHEN TRIM(진료과)='*OBGY' THEN 15  WHEN TRIM(진료과)='*난임센터' THEN 16
                WHEN TRIM(진료과)='소아청소년과' THEN 17 WHEN TRIM(진료과)='이비인후과' THEN 18
                WHEN TRIM(진료과)='피부과' THEN 19 WHEN TRIM(진료과)='응급의학과' THEN 20
                WHEN TRIM(진료과)='외부의뢰' THEN 21 WHEN TRIM(진료과)='진단검사' THEN 22
                ELSE 23
            END ASC,
            REPLACE(진료과,'*'),
            CASE WHEN SUBSTR(진료과,1,1)='*' THEN 2 ELSE 1 END
    """,

    # ── V_DAILY_DEPT_STAT 이력 ────────────────────────────────────────
    # V_DAILY_DEPT_STAT_HIST VIEW 필요 (기준일 컬럼 포함)
    "daily_dept_stat": (
        "SELECT * FROM JAIN_WM.V_DAILY_DEPT_STAT_HIST "
        "WHERE TO_CHAR(기준일,'YYYYMMDD') = '{d}' "
        "ORDER BY 진료과명, 구분"
    ),

    # ── 병동별 병상 이력 ──────────────────────────────────────────────
    # V_WARD_BED_HIST VIEW 필요 (기준일 컬럼 포함)
    "ward_bed_detail": (
        "SELECT * FROM JAIN_WM.V_WARD_BED_HIST "
        "WHERE TO_CHAR(기준일,'YYYYMMDD') = '{d}' "
        "ORDER BY 병동명"
    ),

    # ── 외래 추세 이력 ────────────────────────────────────────────────
    # V_OPD_DEPT_TREND는 이미 기준일 컬럼 포함 — 날짜 기준 앵커만 변경
    "opd_dept_trend": (
        "SELECT * FROM JAIN_WM.V_OPD_DEPT_TREND "
        "WHERE 기준일 BETWEEN TO_DATE('{d}','YYYYMMDD') - 6 "
        "                  AND TO_DATE('{d}','YYYYMMDD') "
        "ORDER BY 기준일, 외래환자수 DESC"
    ),

    # ── 키오스크 vs 창구 추세 이력 ────────────────────────────────────
    "kiosk_counter_trend": (
        "SELECT * FROM JAIN_WM.V_KIOSK_COUNTER_TREND "
        "WHERE 기준일 BETWEEN TO_DATE('{d}','YYYYMMDD') - 6 "
        "                  AND TO_DATE('{d}','YYYYMMDD') "
        "ORDER BY 기준일"
    ),

    # ── 대기시간 추세 이력 ────────────────────────────────────────────
    "waittime_trend": (
        "SELECT * FROM JAIN_WM.V_WAITTIME_TREND "
        "WHERE 기준일 BETWEEN TO_DATE('{d}','YYYYMMDD') - 6 "
        "                  AND TO_DATE('{d}','YYYYMMDD') "
        "ORDER BY 기준일, 진료과명"
    ),

    # ── 수납 이력 ────────────────────────────────────────────────────
    "finance_trend": (
        "SELECT * FROM JAIN_WM.V_FINANCE_TREND "
        "WHERE TO_CHAR(기준일,'YYYYMMDD') = '{d}' "
        "ORDER BY 기준일"
    ),
}

# 오늘 날짜 (YYYYMMDD) — _fq() 에서 오늘/과거 분기 판단용
import datetime as _dt_import
_TODAY_STR: str = _dt_import.date.today().strftime("%Y%m%d")


def _fq(key: str, date_str: str = "") -> List[Dict[str, Any]]:
    """
    FQ / FQ_HIST 딕셔너리에서 쿼리를 선택하여 Oracle을 조회한다 (v2.1).

    [동작 방식]
    ┌──────────────────────────────────────────────────────────────┐
    │  date_str 없음 / 오늘 날짜    →  FQ[key]  (SYSDATE VIEW)     │
    │  과거 날짜 + FQ_HIST 있음     →  FQ_HIST[key] (기간 VIEW)     │
    │  과거 날짜 + FQ_HIST 없음     →  FQ[key] 폴백 + 경고 로그     │
    └──────────────────────────────────────────────────────────────┘

    [파라미터]
    · key      : FQ / FQ_HIST 공통 키
    · date_str : YYYYMMDD 형식 날짜 (빈 문자열 = 오늘 SYSDATE)

    [플레이스홀더 치환]
    · FQ_HIST SQL 내 {d}      → date_str
    · FQ_HIST SQL 내 {d_prev} → date_str 전일
    · FQ_HIST SQL 내 {d_next} → date_str 익일
    · FQ     SQL 내 TO_CHAR(SYSDATE, ...) 패턴 → 지정 날짜로 regex 치환
      (V_DAY_INWEON_3 처럼 Python SQL에 SYSDATE가 직접 있는 경우에 작동)
    """
    import re as _re2

    try:
        from db.oracle_client import execute_query

        _is_past = bool(date_str and len(date_str) == 8 and date_str != _TODAY_STR)

        # ── 과거 날짜 요청 ─────────────────────────────────────────
        if _is_past:
            if key in FQ_HIST:
                # FQ_HIST 기간 VIEW 사용 — 플레이스홀더 치환
                _d      = date_str
                _d_prev = (_dt_import.datetime.strptime(date_str, "%Y%m%d")
                           - _dt_import.timedelta(days=1)).strftime("%Y%m%d")
                _d_next = (_dt_import.datetime.strptime(date_str, "%Y%m%d")
                           + _dt_import.timedelta(days=1)).strftime("%Y%m%d")
                _sql = FQ_HIST[key].format(d=_d, d_prev=_d_prev, d_next=_d_next)
                logger.debug(f"[Finance] 기간VIEW 조회: key={key} date={_d}")
            else:
                # FQ_HIST 미등록 키 → FQ 폴백 + 경고
                logger.warning(
                    f"[Finance] '{key}' FQ_HIST 미등록 → 오늘 데이터로 대체. "
                    f"scripts/create_views.sql 실행 후 FQ_HIST에 등록하세요."
                )
                _sql = FQ[key]

        # ── 오늘 / 날짜 없음 ───────────────────────────────────────
        else:
            _sql = FQ[key]
            # FQ SQL에 TO_CHAR(SYSDATE, ...) 패턴이 직접 포함된 경우
            # (예: day_inweon 오늘 쿼리) → date_str가 있으면 치환
            if date_str and len(date_str) == 8:
                _d      = date_str
                _d_prev = (_dt_import.datetime.strptime(date_str, "%Y%m%d")
                           - _dt_import.timedelta(days=1)).strftime("%Y%m%d")
                _d_next = (_dt_import.datetime.strptime(date_str, "%Y%m%d")
                           + _dt_import.timedelta(days=1)).strftime("%Y%m%d")
                _sql = _re2.sub(
                    r"TO_CHAR\(SYSDATE-1,\s*'YYYYMMDD'\)",
                    f"'{_d_prev}'", _sql)
                _sql = _re2.sub(
                    r"TO_CHAR\(SYSDATE,\s*'YYYYMMDD'\)",
                    f"'{_d}'", _sql)
                _sql = _re2.sub(
                    r"TO_CHAR\(SYSDATE\s*\+\s*1,\s*'YYYYMMDD'\)",
                    f"'{_d_next}'", _sql)
                _sql = _re2.sub(
                    r"(?<!')SYSDATE(?!\s*[+-]|\s*,)",
                    f"TO_DATE('{_d}','YYYYMMDD')", _sql)

        return execute_query(_sql) or []

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
    # margin은 각 차트에서 개별 지정 — 여기 포함 시 **언팩 시 중복 키 오류
    xaxis=dict(gridcolor="#F1F5F9", tickfont=dict(size=10), zeroline=False),
    yaxis=dict(gridcolor="#F1F5F9", tickfont=dict(size=10), zeroline=False),
)


# ════════════════════════════════════════════════════════════════════
# 탭 1 — 실시간 현황
# ════════════════════════════════════════════════════════════════════
def _tab_realtime(opd_kpi, dept_status, kiosk_status, discharge_pipe, bed_detail,
                  kiosk_by_dept=None, kiosk_counter_trend=None, ward_room_detail=None,
                  opd_dept_trend=None, daily_dept_stat=None):
    opd_dept_trend   = opd_dept_trend   or []
    daily_dept_stat  = daily_dept_stat  or []
    kiosk_by_dept        = kiosk_by_dept or []
    kiosk_counter_trend  = kiosk_counter_trend or []
    ward_room_detail     = ward_room_detail or []

    # ── KPI 계산 — daily_dept_stat 우선, fallback bed_detail ──────────
    # daily_dept_stat 컬럼: 진료과명 / 구분(외래·입원·퇴원·재원) / 보험구분 / 건수
    def _ds_sum(cat):
        return sum(int(r.get("건수", 0) or 0)
                   for r in daily_dept_stat if r.get("구분") == cat)

    _opd_total = _ds_sum("외래") or (
        sum(int(r.get("대기",0) or 0)+int(r.get("진료중",0) or 0)+int(r.get("완료",0) or 0)
            for r in dept_status))
    _adm  = _ds_sum("입원") or sum(int(r.get("금일입원",0) or 0) for r in bed_detail)
    _disc = _ds_sum("퇴원") or sum(int(r.get("금일퇴원",0) or 0) for r in bed_detail)
    _stay = _ds_sum("재원") or sum(int(r.get("재원수",   0) or 0) for r in bed_detail)
    _opd_wait = sum(int(r.get("대기",0) or 0) for r in dept_status)
    _opd_proc = sum(int(r.get("진료중",0) or 0) for r in dept_status)
    _opd_done = sum(int(r.get("완료",0) or 0) for r in dept_status)

    # ── KPI 카드 4개 ────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4, gap="small")
    k1.markdown(
        f'<div class="fn-kpi" style="border-top:3px solid {C["blue"]};">'
        f'<div class="fn-kpi-icon">👥</div>'
        f'<div class="fn-kpi-label">금일 외래</div>'
        f'<div class="fn-kpi-value" style="color:{C["blue"]};">{_opd_total:,}'
        f'<span class="fn-kpi-unit">명</span></div>'
        f'<div style="display:flex;gap:4px;margin-top:6px;flex-wrap:wrap;">'
        f'<span style="background:{C["yellow"]}22;color:{C["yellow"]};border-radius:4px;'
        f'padding:2px 6px;font-size:10px;font-weight:700;">대기 {_opd_wait}</span>'
        f'<span style="background:{C["blue"]}22;color:{C["blue"]};border-radius:4px;'
        f'padding:2px 6px;font-size:10px;font-weight:700;">보류 {_opd_proc}</span>'
        f'<span style="background:{C["green"]}22;color:{C["green"]};border-radius:4px;'
        f'padding:2px 6px;font-size:10px;font-weight:700;">완료 {_opd_done}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )
    _kpi_card(k2, "🏥", "금일 입원", f"{_adm:,}", "명", "입원 처리 완료", C["indigo"])
    _kpi_card(k3, "📤", "금일 퇴원", f"{_disc:,}", "명", "퇴원 처리 완료", C["t2"])
    _kpi_card(k4, "🛏️", "현재 재원", f"{_stay:,}", "명",
              f"총병상 {sum(int(r.get('총병상',0) or 0) for r in bed_detail)}개 기준", C["violet"])
    _gap()

    # ── 병동 현황 요약 — 탑바 스타일과 동일한 인라인 행 ─────────────
    if bed_detail:
        _total_bed = sum(int(r.get("총병상", 0) or 0) for r in bed_detail)
        _occ_rate  = round(_stay / max(_total_bed, 1) * 100, 1)
        _ndc_pre   = sum(int(r.get("익일퇴원예약", 0) or 0) for r in bed_detail)
        _rest      = max(0, _total_bed - _stay)
        _oc        = "#DC2626" if _occ_rate >= 90 else "#F59E0B" if _occ_rate >= 80 else "#059669"
        # 병동 현황 요약 — KPI 카드 스타일 (재원 제거, KPI 카드와 중복)
        _ward_kpi_items = [
            ("가동률",      f"{_occ_rate:.1f}", _oc,       "%",      "🏥"),
            ("총병상",      str(_total_bed),    "#64748B", "개",     "🛏️"),
            ("잔여병상",    str(_rest),         "#059669", "개",     "✅"),
            ("익일퇴원예정",str(_ndc_pre),      "#7C3AED", "명",     "📤"),
        ]
        _wk_cols = st.columns(len(_ward_kpi_items), gap="small")
        for _wki, (_wl, _wv, _wc, _wu, _ico) in enumerate(_ward_kpi_items):
            _wk_cols[_wki].markdown(
                f'<div class="fn-kpi" style="border-top:3px solid {_wc};min-height:80px;">'
                f'<div class="fn-kpi-icon">{_ico}</div>'
                f'<div class="fn-kpi-label">{_wl}</div>'
                f'<div class="fn-kpi-value" style="color:{_wc};">{_wv}'
                f'<span class="fn-kpi-unit">{_wu}</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # 병실현황 패널 (탑바 버튼 클릭 시 펼침)
    if st.session_state.get("fn_show_room", False) and ward_room_detail:
        st.markdown(
            '<div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:10px;'
            'padding:12px 14px;margin-bottom:8px;">',
            unsafe_allow_html=True,
        )
        _TH_R = ("padding:6px 8px;font-size:10px;font-weight:700;text-transform:uppercase;"
                 "color:#64748B;border-bottom:1.5px solid #E2E8F0;background:#F8FAFC;")
        _rh = (
            '<div style="overflow-x:auto;max-height:280px;overflow-y:auto;">'
            '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>'
            f'<th style="{_TH_R}text-align:left;">병동</th>'
            f'<th style="{_TH_R}text-align:center;">병실</th>'
            f'<th style="{_TH_R}text-align:center;">베드</th>'
            f'<th style="{_TH_R}text-align:center;">상태</th>'
            f'<th style="{_TH_R}text-align:center;">성별</th>'
            f'<th style="{_TH_R}text-align:left;">진료과</th>'
            f'<th style="{_TH_R}text-align:right;">병실료</th>'
            '</tr></thead><tbody>'
        )
        _STATUS_CLR = {"재원":("#1D4ED8","#DBEAFE"),"퇴원예정":("#7C3AED","#EDE9FE"),
                       "빈병상":("#16A34A","#DCFCE7"),"LOCK":("#DC2626","#FEE2E2")}
        for _ri, _r in enumerate(ward_room_detail[:80]):
            _bno = str(_r.get("병실번호","")).zfill(6)
            _sc, _sbg = _STATUS_CLR.get(_r.get("상태","빈병상"), ("#64748B","#F1F5F9"))
            _fee = int(_r.get("병실료", 0) or 0)
            _bg_r = "#F8FAFC" if _ri % 2 == 0 else "#FFFFFF"
            _td_r = f"padding:5px 8px;background:{_bg_r};border-bottom:1px solid #F8FAFC;"
            _rh += (
                f"<tr>"
                f'<td style="{_td_r}font-weight:600;">{_r.get("병동명","")}</td>'
                f'<td style="{_td_r}text-align:center;font-family:Consolas,monospace;">{_bno[2:4]}</td>'
                f'<td style="{_td_r}text-align:center;font-family:Consolas,monospace;color:#7C3AED;">{_bno[4:6]}</td>'
                f'<td style="{_td_r}text-align:center;">'
                f'<span style="background:{_sbg};color:{_sc};border-radius:4px;padding:1px 6px;'
                f'font-size:10px;font-weight:700;">{_r.get("상태","")}</span></td>'
                f'<td style="{_td_r}text-align:center;font-weight:600;">{_r.get("성별","─")}</td>'
                f'<td style="{_td_r}">{_r.get("진료과","─")}</td>'
                f'<td style="{_td_r}text-align:right;font-family:Consolas,monospace;">'
                f'{"─" if not _fee else f"{_fee:,}원"}</td>'
                f"</tr>"
            )
        st.markdown(_rh + "</tbody></table></div></div>", unsafe_allow_html=True)
    elif st.session_state.get("fn_show_room", False):
        st.info("V_WARD_ROOM_DETAIL 데이터 없음 — Oracle 연결 확인")

    _gap()

    # ── 📋 일일현황 테이블 (전체 너비) ─────────────────────────────
    st.markdown(
        '<div class="wd-card" style="border-top:3px solid ' + C["blue"] + ';">',
        unsafe_allow_html=True,
    )
    _sec_hd("📋 일일현황", "진료과별 외래·입원·퇴원·재원 (보험구분)", C["blue"])
    _INS_CODES = ["공단", "보호", "산재", "자보", "기타"]
    _CATS = ["외래", "입원", "퇴원", "재원"]
    _CAT_COLORS = {"외래": C["blue"], "입원": C["indigo"], "퇴원": C["t2"], "재원": C["violet"]}
    if daily_dept_stat:
        from collections import defaultdict as _ddd2
        _agg: dict = _ddd2(lambda: _ddd2(lambda: _ddd2(int)))
        for _r in daily_dept_stat:
            _dept = _r.get("진료과명", "")
            _cat  = _r.get("구분", "")
            _ins  = _r.get("보험구분", "기타")
            _cnt  = int(_r.get("건수", 0) or 0)
            if _dept and _cat:
                _agg[_dept][_cat][_ins] += _cnt
        _depts = sorted(_agg.keys())
        _TH_D = ("padding:4px 6px;font-size:11px;font-weight:700;"
                 "letter-spacing:.02em;color:#64748B;border-bottom:1.5px solid #E2E8F0;"
                 "background:#F8FAFC;white-space:nowrap;text-align:right;")
        _TH_L = _TH_D.replace("text-align:right;", "text-align:left;")
        _colspan = len(_INS_CODES) + 1
        _gh = "".join(
            f'<th colspan="{_colspan}" style="padding:4px;font-size:11.5px;font-weight:800;'
            f'color:{_CAT_COLORS[c]};border-bottom:1px solid #E2E8F0;background:#F8FAFC;'
            f'text-align:center;border-left:2px solid {_CAT_COLORS[c]}33;">{c}</th>'
            for c in _CATS
        )
        _sh2 = "".join(
            (f'<th style="{_TH_D}border-left:2px solid {_CAT_COLORS[c]}33;">{ins}</th>'
             if i == 0 else f'<th style="{_TH_D}">{ins}</th>') +
            (f'<th style="{_TH_D}color:{_CAT_COLORS[c]};font-weight:800;">계</th>'
             if i == len(_INS_CODES) - 1 else "")
            for c in _CATS for i, ins in enumerate(_INS_CODES)
        )
        _rows_d = ""
        _tot: dict = _ddd2(lambda: _ddd2(int))
        for _di, _dept in enumerate(_depts):
            _rbg = "#F8FAFC" if _di % 2 == 0 else "#FFFFFF"
            _td_s = f"padding:4px 5px;background:{_rbg};border-bottom:1px solid #F1F5F9;font-size:12px;font-family:Consolas,monospace;text-align:right;"
            _rows_d += (f'<tr><td style="{_td_s.replace("text-align:right;","text-align:left;")}'
                        f'font-weight:700;font-family:inherit;color:#0F172A;white-space:nowrap;">{_dept}</td>')
            for _ci, _cat in enumerate(_CATS):
                _subtot = 0
                for _ii, _ins in enumerate(_INS_CODES):
                    _v = _agg[_dept][_cat][_ins]
                    _subtot += _v
                    _tot[_cat][_ins] += _v
                    _bl2 = f"border-left:2px solid {_CAT_COLORS[_cat]}33;" if _ii == 0 else ""
                    _rows_d += f'<td style="{_td_s}{_bl2}">{"─" if _v == 0 else _v}</td>'
                _tot[_cat]["계"] += _subtot
                _rows_d += (f'<td style="{_td_s}font-weight:700;color:{_CAT_COLORS[_cat]};">'
                            f'{"─" if _subtot == 0 else _subtot}</td>')
            _rows_d += "</tr>"
        _sth_d = "padding:4px 5px;background:#EFF6FF;border-top:2px solid #BFDBFE;font-size:12px;font-family:Consolas,monospace;text-align:right;font-weight:700;"
        _rows_d += f'<tr><td style="{_sth_d.replace("text-align:right;","text-align:left;")}font-family:inherit;color:#1E40AF;">합계</td>'
        for _cat in _CATS:
            for _ii, _ins in enumerate(_INS_CODES):
                _v = _tot[_cat][_ins]
                _bl2 = f"border-left:2px solid {_CAT_COLORS[_cat]}33;" if _ii == 0 else ""
                _rows_d += f'<td style="{_sth_d}{_bl2}color:{_CAT_COLORS[_cat]};">{"─" if _v==0 else _v}</td>'
            _v2 = _tot[_cat]["계"]
            _rows_d += (f'<td style="{_sth_d}color:{_CAT_COLORS[_cat]};font-size:12.5px;">'
                        f'{"─" if _v2==0 else _v2}</td>')
        _rows_d += "</tr>"
        st.markdown(
            f'<div style="overflow-x:auto;">'
            f'<table style="width:100%;border-collapse:collapse;font-size:11px;">'
            f'<thead>'
            f'<tr><th style="{_TH_L}min-width:40px;">진료과</th>{_gh}</tr>'
            f'<tr><th style="{_TH_L}"></th>{_sh2}</tr>'
            f'</thead><tbody>{_rows_d}</tbody></table></div>',
            unsafe_allow_html=True,
        )
    # ── 외래·입원·재원 진료과별 파이차트 3분할 ─────────────────
    _PALETTE_P = [
        "#1E40AF","#4F46E5","#0891B2","#059669","#D97706",
        "#DC2626","#7C3AED","#DB2777","#65A30D","#9333EA",
        "#0284C7","#16A34A","#EA580C","#0F766E","#BE185D",
    ]
    _PIE_DEFS = [
        ("외래", "🥧 외래 진료과별 구성", C["blue"],   "daily_pie_opd"),
        ("입원", "🥧 입원 진료과별 구성", C["indigo"], "daily_pie_adm"),
        ("재원", "🥧 재원 진료과별 구성", C["violet"], "daily_pie_stay"),
    ]
    if daily_dept_stat and HAS_PLOTLY:
        from collections import defaultdict as _ddd_pie
        # 구분별 진료과 집계
        _pie_all: dict = _ddd_pie(lambda: _ddd_pie(int))
        for _r in daily_dept_stat:
            _cat_p  = _r.get("구분", "")
            _dept_p = _r.get("진료과명", "")
            if _cat_p and _dept_p:
                _pie_all[_cat_p][_dept_p] += int(_r.get("건수", 0) or 0)

        _pie_cols = st.columns(3, gap="small")
        for _pci, (_pcat, _ptitle, _pclr, _pkey) in enumerate(_PIE_DEFS):
            _pd = _pie_all.get(_pcat, {})
            with _pie_cols[_pci]:
                st.markdown(
                    f'<div style="background:#FFFFFF;border:1px solid #F0F4F8;'
                    f'border-top:3px solid {_pclr};border-radius:10px;padding:10px 12px;">',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div style="font-size:12px;font-weight:700;color:{_pclr};'
                    f'margin-bottom:4px;">{_ptitle}</div>',
                    unsafe_allow_html=True,
                )
                if _pd:
                    # 상위 10개만, 나머지 기타 합산
                    _sorted_d = sorted(_pd.items(), key=lambda x: -x[1])
                    _top10    = _sorted_d[:10]
                    _etc_sum  = sum(v for _, v in _sorted_d[10:])
                    _pl = [k for k, _ in _top10]
                    _pv = [v for _, v in _top10]
                    if _etc_sum > 0:
                        _pl.append("기타")
                        _pv.append(_etc_sum)
                    _ptotal = sum(_pv)
                    _figP = go.Figure(go.Pie(
                        labels=_pl, values=_pv,
                        marker=dict(
                            colors=_PALETTE_P[:len(_pl)],
                            line=dict(color="#fff", width=1.5),
                        ),
                        hole=0.48,
                        textinfo="label+percent",
                        textfont=dict(size=10),
                        insidetextorientation="radial",
                        hovertemplate="<b>%{label}</b><br>%{value:,}명 (%{percent})<extra></extra>",
                        sort=True,
                    ))
                    _figP.update_layout(
                        **_PLOTLY_LAYOUT,
                        height=300,
                        margin=dict(l=0, r=0, t=4, b=4),
                        showlegend=False,
                        annotations=[dict(
                            text=f"<b>{_ptotal:,}</b><br><span style='font-size:10px'>명</span>",
                            x=0.5, y=0.5,
                            font=dict(size=13, color=_pclr),
                            showarrow=False,
                        )],
                    )
                    st.plotly_chart(_figP, use_container_width=True, key=_pkey)
                else:
                    st.markdown(
                        f'<div style="height:200px;display:flex;align-items:center;'
                        f'justify-content:center;color:#94A3B8;font-size:12px;">데이터 없음</div>',
                        unsafe_allow_html=True,
                    )
                st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="padding:20px;text-align:center;color:#94A3B8;font-size:12px;">'
            'V_DAILY_DEPT_STAT 데이터 없음<br>'
            '<span style="font-size:10px;">필요 컬럼: 진료과명 / 구분 / 보험구분 / 건수</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)
    _gap()

    # ── 키오스크 진료과별 수납 건수 + 7일 추세 ──────────────────────


    ck, cd = st.columns([1, 1], gap="small")

    with ck:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["violet"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("🖥️ 키오스크 진료과별 수납 건수", "금일 기준", C["violet"])
        if kiosk_by_dept:
            _k_total = sum(int(r.get("수납건수", 0) or 0) for r in kiosk_by_dept)
            st.markdown(
                f'<div style="font-size:10px;color:#64748B;margin-bottom:8px;">'
                f'총 <b style="color:{C["violet"]};font-size:13px;">{_k_total:,}</b>건</div>',
                unsafe_allow_html=True,
            )
            _max_k = max((int(r.get("수납건수",0) or 0) for r in kiosk_by_dept), default=1)
            _TH_K = "padding:6px 8px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#64748B;border-bottom:1.5px solid #E2E8F0;background:#F8FAFC;"
            _k_amt_total = sum(int(r.get("수납금액", 0) or 0) for r in kiosk_by_dept)
            _tk = (
                '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>'
                f'<th style="{_TH_K}text-align:left;">진료과</th>'
                f'<th style="{_TH_K}text-align:right;color:{C["violet"]};">건수</th>'
                f'<th style="{_TH_K}text-align:right;color:{C["indigo"]};">금액(만)</th>'
                f'<th style="{_TH_K}">비율</th>'
                '</tr></thead><tbody>'
            )
            for _ki, _r in enumerate(kiosk_by_dept[:15]):
                _kd  = _r.get("진료과명", "")
                _kc  = int(_r.get("수납건수", 0) or 0)
                _ka  = int(_r.get("수납금액", 0) or 0)
                _kp  = round(_kc / max(_k_total, 1) * 100)
                _kbg = "#F8FAFC" if _ki % 2 == 0 else "#FFFFFF"
                _td_k = f"padding:5px 8px;background:{_kbg};border-bottom:1px solid #F8FAFC;"
                _tk += (
                    f"<tr><td style='{_td_k}font-weight:600;'>{_kd}</td>"
                    f"<td style='{_td_k}text-align:right;color:{C['violet']};font-family:Consolas,monospace;font-weight:700;'>{_kc:,}</td>"
                    f"<td style='{_td_k}text-align:right;color:{C['indigo']};font-family:Consolas,monospace;'>{_ka//10000 if _ka else '─'}만</td>"
                    f"<td style='{_td_k}'>"
                    f'<div style="display:flex;align-items:center;gap:4px;">'
                    f'<div style="flex:1;height:6px;background:#F1F5F9;border-radius:3px;">'
                    f'<div style="width:{_kp}%;height:100%;background:{C["violet"]};border-radius:3px;"></div></div>'
                    f'<span style="font-size:10px;color:#64748B;">{_kp}%</span>'
                    f"</div></td></tr>"
                )
            st.markdown(_tk + "</tbody></table>", unsafe_allow_html=True)
        elif kiosk_status:
            _krec = sum(int(r.get("접수건수", 0) or 0) for r in kiosk_status)
            st.markdown(
                f'<div style="padding:16px;text-align:center;color:#64748B;">'
                f'<div style="font-size:24px;font-weight:800;color:{C["violet"]};">{_krec:,}</div>'
                f'<div style="font-size:11px;">총 접수 건수</div>'
                f'<div style="font-size:10px;color:#94A3B8;margin-top:4px;">V_KIOSK_BY_DEPT 생성 후 진료과별 상세 조회 가능</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            _plotly_empty()
        st.markdown("</div>", unsafe_allow_html=True)

    with cd:
        st.markdown(
            '<div class="wd-card" style="border-top:3px solid ' + C["teal"] + ';">',
            unsafe_allow_html=True,
        )
        _sec_hd("📈 7일간 키오스크 vs 창구 수납 건수", "일별 추이", C["teal"])
        if kiosk_counter_trend and HAS_PLOTLY:
            def _fmt_date(d):
                s = str(d).replace('-','')[:8]
                return f"{s[4:6]}/{s[6:8]}" if len(s) >= 8 else str(d)[:10]
            _dates_k = [_fmt_date(r.get("기준일","")) for r in kiosk_counter_trend]
            _k_vals  = [int(r.get("키오스크건수", 0) or 0) for r in kiosk_counter_trend]
            _c_vals  = [int(r.get("창구건수",    0) or 0) for r in kiosk_counter_trend]
            _figKC = go.Figure()
            _figKC.add_trace(go.Bar(
                x=_dates_k, y=_k_vals, name="키오스크",
                marker_color=C["violet"], marker=dict(line=dict(width=0)),
                text=_k_vals, textposition="outside",
                textfont=dict(size=11, color=C["violet"]),
                hovertemplate="%{x}<br>키오스크: %{y}건<extra></extra>",
            ))
            _figKC.add_trace(go.Bar(
                x=_dates_k, y=_c_vals, name="창구",
                marker_color=C["teal"], marker=dict(line=dict(width=0)),
                text=_c_vals, textposition="outside",
                textfont=dict(size=11, color=C["teal"]),
                hovertemplate="%{x}<br>창구: %{y}건<extra></extra>",
            ))
            _kc_max = max(max(_k_vals, default=0), max(_c_vals, default=0))
            _figKC.update_layout(
                **_PLOTLY_LAYOUT,
                barmode="group",
                height=280,
                margin=dict(l=0, r=0, t=30, b=8),
                legend=dict(orientation="h", y=1.12, x=0.5, xanchor="center",
                            font=dict(size=12), bgcolor="rgba(0,0,0,0)"),
                bargap=0.25, bargroupgap=0.05,
            )
            _figKC.update_xaxes(tickfont=dict(size=12, color="#334155"))
            _figKC.update_yaxes(
                title_text="수납 건수",
                title_font=dict(size=10, color=C["t3"]),
                range=[0, _kc_max * 1.22],
            )
            st.plotly_chart(_figKC, use_container_width=True, key="kiosk_counter_trend")
        else:
            st.markdown(
                '<div style="padding:20px;text-align:center;color:#94A3B8;font-size:12px;">'
                'V_KIOSK_COUNTER_TREND 생성 후 조회 가능<br>'
                '<span style="font-size:11px;">필요 컬럼: 기준일 / 키오스크건수 / 창구건수</span>'
                '</div>',
                unsafe_allow_html=True,
            )
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
            )
            _fig2.update_xaxes(tickangle=-30, nticks=15)
            _fig2.update_yaxes(tickformat=",",
                title_text="수납금액(만원)", title_font=dict(size=10, color=C["t3"]))
            _fig2.update_layout(yaxis2=dict(
                overlaying="y", side="right", showgrid=False,
                tickfont=dict(size=10, color=C["indigo"]),
                title=dict(text="건수", font=dict(size=10, color=C["indigo"]))))
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
                bargap=0.3,
            )
            _fig3.update_xaxes(ticksuffix="만")
            _fig3.update_yaxes(tickfont=dict(size=11), autorange="reversed")
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
def _tab_analytics(opd_dept_trend, waittime_trend, los_dist, daily_dept_stat=None):
    daily_dept_stat = daily_dept_stat or []

    # ── 7일 외래 인원 추이 ─────────────────────────────────────────
    _gap()
    st.markdown(
        '<div class="wd-card" style="border-top:3px solid ' + C["blue"] + ';">',
        unsafe_allow_html=True,
    )
    _sec_hd("📈 7일간 추이", "외래·입원·퇴원·재원", C["blue"])
    if daily_dept_stat and HAS_PLOTLY:
        # daily_dept_stat에서 일자별 구분별 합산
        from collections import defaultdict as _ddc_tr
        _trend_day: dict = _ddc_tr(lambda: _ddc_tr(int))
        for _r in daily_dept_stat:
            _rd = str(_r.get("기준일", "")).replace("-","")
            _d_fmt = f"{_rd[4:6]}/{_rd[6:8]}" if len(_rd)>=8 else str(_r.get("기준일",""))[:10]
            _cat2 = _r.get("구분", "")
            if _cat2 in ("외래","입원","퇴원","재원"):
                _trend_day[_d_fmt][_cat2] += int(_r.get("건수", 0) or 0)
        _tr_dates = sorted(_trend_day.keys())
        if _tr_dates:
            _tr_colors = {"외래": C["blue"], "입원": C["indigo"], "퇴원": C["t2"], "재원": C["violet"]}
            _figOPD = go.Figure()
            for _tc in ("외래","입원","퇴원","재원"):
                _ty = [_trend_day[d][_tc] for d in _tr_dates]
                _figOPD.add_trace(go.Scatter(
                    x=_tr_dates, y=_ty, name=_tc,
                    mode="lines+markers",
                    line=dict(color=_tr_colors[_tc], width=2.5, shape="spline"),
                    marker=dict(size=6, color=_tr_colors[_tc], line=dict(color="#fff",width=1.5)),
                    hovertemplate=f"%{{x}}<br>{_tc}: %{{y}}명<extra></extra>",
                ))
            _figOPD.update_layout(
                **_PLOTLY_LAYOUT, height=240,
                margin=dict(l=0, r=0, t=28, b=8),
                legend=dict(orientation="h", y=1.12, x=0.5, xanchor="center",
                            font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
                hovermode="x unified",
            )
            _figOPD.update_xaxes(tickfont=dict(size=12, color="#334155"))
            _figOPD.update_yaxes(title_text="인원 (명)", title_font=dict(size=10, color=C["t3"]))
            st.plotly_chart(_figOPD, use_container_width=True, key="opd_7day_trend")
        else:
            st.markdown('<div style="padding:20px;text-align:center;color:#94A3B8;">V_DAILY_DEPT_STAT 기준일 컬럼 필요</div>', unsafe_allow_html=True)
    elif opd_dept_trend and HAS_PLOTLY:
        # fallback: opd_dept_trend 합산
        from collections import defaultdict as _ddc_opd
        _opd_day: dict = _ddc_opd(int)
        for _r in opd_dept_trend:
            _d = str(_r.get("기준일","")).replace("-","")
            _d_fmt = f"{_d[4:6]}/{_d[6:8]}" if len(_d)>=8 else str(_r.get("기준일",""))[:10]
            _opd_day[_d_fmt] += int(_r.get("외래환자수",0) or 0)
        _opd_dates = sorted(_opd_day.keys())
        _opd_vals  = [_opd_day[d] for d in _opd_dates]
        _figOPD2 = go.Figure(go.Scatter(
            x=_opd_dates, y=_opd_vals, name="외래",
            mode="lines+markers", line=dict(color=C["blue"], width=2.5, shape="spline"),
            marker=dict(size=6, color=C["blue"], line=dict(color="#fff",width=1.5)),
        ))
        _figOPD2.update_layout(**_PLOTLY_LAYOUT, height=240, margin=dict(l=0,r=0,t=8,b=8))
        st.plotly_chart(_figOPD2, use_container_width=True, key="opd_7day_trend")
    else:
        st.markdown('<div style="padding:24px;text-align:center;color:#94A3B8;font-size:12px;">데이터 없음</div>', unsafe_allow_html=True)

    # ── 진료과별 7일간 인원 히트맵 (한눈에 파악) ─────────────────
    _gap()
    st.markdown('<div class="wd-card" style="border-top:3px solid ' + C["indigo"] + ';">', unsafe_allow_html=True)
    _sec_hd("🗓️ 진료과별 7일 외래 인원 히트맵", "색이 진할수록 인원 많음", C["indigo"])
    if opd_dept_trend and HAS_PLOTLY:
        from collections import defaultdict as _ddc_dp
        _dp_map: dict = _ddc_dp(lambda: _ddc_dp(int))
        for _r in opd_dept_trend:
            _d = str(_r.get("기준일","")).replace("-","")
            _d_fmt = f"{_d[4:6]}/{_d[6:8]}" if len(_d)>=8 else str(_r.get("기준일",""))[:10]
            _dp = _r.get("진료과명","")
            if _dp: _dp_map[_dp][_d_fmt] += int(_r.get("외래환자수",0) or 0)
        _dp_dates = sorted({d for dv in _dp_map.values() for d in dv})
        # 합계 기준 상위 15개 진료과
        _top_depts = sorted(_dp_map, key=lambda d:-sum(_dp_map[d].values()))[:15]
        if _top_depts and _dp_dates:
            _z = [[_dp_map[dept].get(d,0) for d in _dp_dates] for dept in _top_depts]
            # 각 진료과 합계 (우측 레이블)
            _totals = [sum(_dp_map[d].values()) for d in _top_depts]
            _y_labels = [f"{d} ({t}명)" for d, t in zip(_top_depts, _totals)]
            _figDP = go.Figure(go.Heatmap(
                z=_z, x=_dp_dates, y=_y_labels,
                colorscale=[[0.0,"#EFF6FF"],[0.5,"#60A5FA"],[1.0,"#1E40AF"]],
                text=[[str(v) if v>0 else "" for v in row] for row in _z],
                texttemplate="%{text}",
                textfont=dict(size=11),
                hovertemplate="<b>%{y}</b><br>%{x}: %{z}명<extra></extra>",
                showscale=True,
                colorbar=dict(len=0.8, thickness=12, tickfont=dict(size=9)),
            ))
            _figDP.update_layout(
                **_PLOTLY_LAYOUT,
                height=max(280, len(_top_depts)*32),
                margin=dict(l=0, r=60, t=8, b=8),
            )
            _figDP.update_xaxes(tickfont=dict(size=11, color="#334155"), side="top")
            _figDP.update_yaxes(tickfont=dict(size=11, color="#0F172A"), autorange="reversed")
            st.plotly_chart(_figDP, use_container_width=True, key="dept_7day_heatmap")
        else:
            _plotly_empty()
    else:
        _plotly_empty()
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
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
                legend=dict(orientation="h", y=-0.18, x=0.5, xanchor="center",
                            font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
                hovermode="x unified",
            )
            _figT.update_yaxes(title_text="외래 환자 수(명)",
                title_font=dict(size=10, color=C["t3"]))
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
                bargap=0.25,
            )
            _figL.update_xaxes(tickfont=dict(size=11), gridcolor="rgba(0,0,0,0)")
            _figL.update_yaxes(title_text="환자 수(명)",
                title_font=dict(size=10, color=C["t3"]))
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
# 원무 AI 채팅 분석
# ════════════════════════════════════════════════════════════════════
def _render_finance_llm_chat(bed_detail=None, dept_status=None, kiosk_by_dept=None,
                              daily_dept_stat=None, kiosk_counter_trend=None,
                              discharge_pipe=None, kiosk_status=None):
    """원무 현황 AI 채팅 — 대시보드 전체 데이터를 컨텍스트로 전달."""
    import json as _json, uuid as _uuid
    bed_detail          = bed_detail          or []
    dept_status         = dept_status         or []
    kiosk_by_dept       = kiosk_by_dept       or []
    daily_dept_stat     = daily_dept_stat     or []
    kiosk_counter_trend = kiosk_counter_trend or []
    discharge_pipe      = discharge_pipe      or []

    # ── 집계 ──────────────────────────────────────────────────────
    _stay  = sum(int(r.get("재원수",   0) or 0) for r in bed_detail)
    _adm   = sum(int(r.get("금일입원", 0) or 0) for r in bed_detail)
    _disc  = sum(int(r.get("금일퇴원", 0) or 0) for r in bed_detail)
    _total_bed = sum(int(r.get("총병상", 0) or 0) for r in bed_detail)
    _rest  = max(0, _total_bed - _stay)
    _occ   = round(_stay / max(_total_bed,1)*100, 1)
    _ndc   = sum(int(r.get("익일퇴원예약", 0) or 0) for r in bed_detail)
    _wait  = sum(int(r.get("대기",   0) or 0) for r in dept_status)
    _proc  = sum(int(r.get("진료중", 0) or 0) for r in dept_status)
    _done  = sum(int(r.get("완료",   0) or 0) for r in dept_status)
    _k_tot = sum(int(r.get("수납건수", 0) or 0) for r in kiosk_by_dept)
    _k_amt = sum(int(r.get("수납금액", 0) or 0) for r in kiosk_by_dept)

    # 일일현황 보험구분 합산
    from collections import defaultdict as _ddd_c
    _ds_cat: dict = _ddd_c(lambda: _ddd_c(int))
    for _r in daily_dept_stat:
        _ds_cat[_r.get("구분","")][_r.get("보험구분","기타")] += int(_r.get("건수",0) or 0)

    # 퇴원 파이프라인
    _pipe: dict = {}
    for _r in discharge_pipe:
        _s = _r.get("단계",""); _n = int(_r.get("환자수",0) or 0)
        if _s: _pipe[_s] = _pipe.get(_s,0) + _n

    # 키오스크 7일 추세 요약
    _kc_summary = [{"날짜": str(r.get("기준일",""))[:10],
                    "키오스크": r.get("키오스크건수"), "창구": r.get("창구건수")}
                   for r in kiosk_counter_trend[-7:]]

    _ctx = {
        "기준시각": time.strftime("%Y-%m-%d %H:%M"),
        "병동_현황": {
            "금일입원": _adm, "금일퇴원": _disc, "재원수": _stay,
            "총병상": _total_bed, "잔여병상": _rest,
            "가동률": f"{_occ}%", "익일퇴원예정": _ndc,
            "병동별": [{"병동": r.get("병동명"), "재원": r.get("재원수"),
                         "가동률": r.get("가동률")} for r in bed_detail[:12]],
        },
        "외래_현황": {
            "대기": _wait, "진료중": _proc, "완료": _done, "합계": _wait+_proc+_done,
            "진료과별": [{"진료과": r.get("진료과명"),
                           "대기": r.get("대기"), "완료": r.get("완료"),
                           "평균대기(분)": r.get("평균대기시간")}
                          for r in sorted(dept_status,
                              key=lambda x:-int(x.get("대기",0) or 0))[:12]],
        },
        "일일현황_보험구분": {
            cat: dict(v) for cat, v in _ds_cat.items()
        },
        "키오스크_수납": {
            "총건수": _k_tot, "총금액(만원)": _k_amt//10000,
            "진료과별": [{"진료과": r.get("진료과명"),
                           "건수": r.get("수납건수"),
                           "금액(만원)": int(r.get("수납금액",0) or 0)//10000}
                          for r in kiosk_by_dept[:10]],
            "7일_추세": _kc_summary,
        },
        "퇴원_파이프라인": _pipe,
    }
    _sys_prompt = (
        "당신은 병원 원무팀 업무 지원 AI입니다.\n"
        "반드시 아래 [현재 대시보드 데이터]만 근거로 답변하세요. 데이터에 없는 내용은 추측하지 마세요.\n"
        "핵심 수치는 **굵게**, 위험/주의는 🔴, 정상은 🟢, 권장 조치는 ✅ 로 표시하세요.\n"
        "개인 환자 정보(환자명, 주민번호, 카드번호, 승인번호 등)는 절대 언급하지 마세요.\n"
        "카드 매칭 관련 데이터(승인번호, 카드번호)는 이 컨텍스트에 포함되어 있지 않습니다.\n\n"
        f"## [현재 대시보드 데이터] — {time.strftime('%Y-%m-%d %H:%M')} 기준\n"
        f"```json\n{_json.dumps(_ctx, ensure_ascii=False, indent=2)[:6000]}\n```"
    )

    if "fn_chat_history" not in st.session_state:
        st.session_state["fn_chat_history"] = []
    _history = st.session_state["fn_chat_history"]

    # 채팅 카드
    st.markdown(
        f'<div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:12px;'
        f'padding:14px 16px;margin-top:4px;">',
        unsafe_allow_html=True,
    )
    # 헤더
    st.markdown(
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">'
        '<span style="font-size:16px;">🤖</span>'
        '<span style="font-size:13px;font-weight:700;color:#0F172A;">AI 원무 분석 채팅</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    # 빠른 질문
    _quick = [
        ("외래 혼잡도", "현재 외래 진료과별 대기 현황을 분석하고 혼잡한 진료과 조치 방안을 알려주세요."),
        ("키오스크 현황", f"오늘 키오스크 수납 {_k_tot}건 현황을 분석하고 창구 부담 현황을 알려주세요."),
        ("입퇴원 분석", f"금일 입원 {_adm}명·퇴원 {_disc}명·재원 {_stay}명 현황을 분석해주세요."),
        ("운영 요약", "오늘 원무 전체 운영 현황을 3줄로 요약해주세요."),
    ]
    _qcols = st.columns(len(_quick), gap="small")
    for _qi, (_ql, _qv) in enumerate(_quick):
        with _qcols[_qi]:
            if st.button(_ql, key=f"fn_qs_{_qi}", use_container_width=True, type="secondary"):
                st.session_state["fn_chat_prefill"] = _qv
                st.rerun()
    # 대화 이력
    for _msg in _history:
        with st.chat_message(_msg["role"]):
            st.markdown(_msg["content"])

    _prefill = st.session_state.pop("fn_chat_prefill", None)
    _user_in = st.chat_input(
        "원무 현황에 대해 질문하세요  예) 대기 많은 진료과는? / 키오스크 수납 현황은?",
        key="fn_chat_input",
    ) or _prefill

    if _user_in:
        with st.chat_message("user"):
            st.markdown(_user_in)
        _history.append({"role": "user", "content": _user_in})

        with st.chat_message("assistant"):
            _t0  = time.time()
            _ph  = st.empty()
            _toks: list = []
            _full = ""
            try:
                from core.llm import get_llm_client
                _llm = get_llm_client()
                _safe_p = _sys_prompt[:4000] + "...(생략)" if len(_sys_prompt) > 4000 else _sys_prompt
                for _tok in _llm.generate_stream(_user_in, _safe_p, request_id=_uuid.uuid4().hex[:8]):
                    _toks.append(_tok)
                    if len(_toks) % 4 == 0:
                        _ph.markdown("".join(_toks) + "▌")
                _full = "".join(_toks)
            except Exception as _e:
                _full = (
                    f"**LLM 연결 실패**\n\n`{_e}`\n\n"
                    f"현재 데이터: 재원 **{_stay}명** / 외래대기 **{_wait}명** / 키오스크수납 **{_k_tot}건**"
                )
            _ph.markdown(_full)

        _history.append({"role": "assistant", "content": _full})
        st.session_state["fn_chat_history"] = _history
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════
# 세부과 일일집계표 (V_DAY_INWEON_3)
# ════════════════════════════════════════════════════════════════════
def _render_day_inweon(day_inweon: list) -> None:
    """세부과 일일집계표 — V_DAY_INWEON_3 기반."""
    # ── 컬럼 정의 ──────────────────────────────────────────────────
    _COLS = [
        ("진료과",         "left",  "#0F172A", False),
        ("외래계",         "right", C["blue"],  True),
        ("입원계",         "right", C["indigo"], True),
        ("퇴원계",         "right", C["t2"],    True),
        ("재원계",         "right", C["violet"], True),
        ("예방(독감)",     "right", C["teal"],   True),
        ("예방(AZ,JS,NV)", "right", C["teal"],   True),
        ("예방(MD)",       "right", C["teal"],   True),
        ("예방(FZ)",       "right", C["teal"],   True),
        ("예방주사계",     "right", C["green"],  True),
    ]
    _TH = ("padding:6px 8px;font-size:11px;font-weight:700;"
           "color:#64748B;border-bottom:2px solid #E2E8F0;"
           "background:#F8FAFC;white-space:nowrap;")
    # 예방접종 그룹 헤더
    _group_html = (
        f'<th style="{_TH}text-align:left;" rowspan="2">진료과</th>'
        f'<th style="{_TH}text-align:right;color:{C["blue"]};" rowspan="2">외래계</th>'
        f'<th style="{_TH}text-align:right;color:{C["indigo"]};" rowspan="2">입원계</th>'
        f'<th style="{_TH}text-align:right;color:{C["t2"]};" rowspan="2">퇴원계</th>'
        f'<th style="{_TH}text-align:right;color:{C["violet"]};" rowspan="2">재원계</th>'
        f'<th colspan="5" style="{_TH}text-align:center;color:{C["teal"]};">'
        f'예방접종</th>'
    )
    _sub_html = (
        f'<th style="{_TH}text-align:right;color:{C["teal"]};">독감</th>'
        f'<th style="{_TH}text-align:right;color:{C["teal"]};">AZ·JS·NV</th>'
        f'<th style="{_TH}text-align:right;color:{C["teal"]};">MD</th>'
        f'<th style="{_TH}text-align:right;color:{C["teal"]};">FZ</th>'
        f'<th style="{_TH}text-align:right;color:{C["green"]};font-weight:800;">소계</th>'
    )
    _rows_html = ""
    for _i, _r in enumerate(day_inweon):
        _dept = str(_r.get("진료과", ""))
        _dept_t    = _dept.strip()          # trim — 공백 제거
        _is_total  = (_dept_t == "총합계")
        _is_group  = _dept_t.startswith("*")  # trim 후 * 검사
        _dept_disp = _dept_t.lstrip("*").strip() if _is_group else _dept_t

        if _is_total:
            _bg = "#EFF6FF"
            _td = f"padding:6px 8px;background:{_bg};border-top:2px solid #BFDBFE;font-weight:800;font-size:12.5px;font-family:Consolas,monospace;text-align:right;"
            _dept_td = _td.replace("text-align:right;", "text-align:left;") + f"color:#1E40AF;font-family:inherit;"
        elif _is_group:
            _bg = "#F0F4FF"
            _td = f"padding:5px 8px;background:{_bg};border-bottom:1px solid #E2E8F0;font-size:12px;font-family:Consolas,monospace;text-align:right;font-weight:700;"
            _dept_td = _td.replace("text-align:right;", "text-align:left;") + f"color:{C['blue']};font-family:inherit;padding-left:8px;"
        else:
            _bg = "#F8FAFC" if _i % 2 == 0 else "#FFFFFF"
            _td = f"padding:5px 8px;background:{_bg};border-bottom:1px solid #F1F5F9;font-size:12px;font-family:Consolas,monospace;text-align:right;"
            _dept_td = _td.replace("text-align:right;", "text-align:left;") + "color:#334155;font-family:inherit;padding-left:18px;"

        def _fmt(key):
            v = _r.get(key)
            if v is None: return "─"
            try:
                n = int(str(v).replace(",",""))
                if n == 0: return "─"
                return f"{n:,}"
            except: return str(v) if str(v).strip() else "─"

        _rows_html += (
            f"<tr>"
            f'<td style="{_dept_td}">{_dept_disp}</td>'
            f'<td style="{_td}color:{C["blue"]};">{_fmt("외래계")}</td>'
            f'<td style="{_td}color:{C["indigo"]};">{_fmt("입원계")}</td>'
            f'<td style="{_td}color:{C["t2"]};">{_fmt("퇴원계")}</td>'
            f'<td style="{_td}color:{C["violet"]};">{_fmt("재원계")}</td>'
            f'<td style="{_td}color:{C["teal"]};">{_fmt("예방(독감)")}</td>'
            f'<td style="{_td}color:{C["teal"]};">{_fmt("예방(AZ,JS,NV)")}</td>'
            f'<td style="{_td}color:{C["teal"]};">{_fmt("예방(MD)")}</td>'
            f'<td style="{_td}color:{C["teal"]};">{_fmt("예방(FZ)")}</td>'
            f'<td style="{_td}color:{C["green"]};font-weight:{"800" if _is_total or _is_group else "600"};">'
            f'{_fmt("예방주사계")}</td>'
            f"</tr>"
        )

    import time as _t
    _today = _t.strftime("%Y.%m.%d")
    st.markdown(
        f'<div class="wd-card" style="border-top:3px solid {C["blue"]};">'
        f'<div class="wd-sec">'
        f'<span class="wd-sec-bar" style="background:{C["blue"]};"></span>'
        f'세부과 일일집계표'
        f'<span class="wd-sec-sub"> {_today} 기준</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if day_inweon:
        st.markdown(
            f'<div style="overflow-x:auto;">'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            f'<thead>'
            f'<tr>{_group_html}</tr>'
            f'<tr>{_sub_html}</tr>'
            f'</thead>'
            f'<tbody>{_rows_html}</tbody>'
            f'</table></div>',
            unsafe_allow_html=True,
        )
        # 범례
        st.markdown(
            f'<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:8px;'
            f'padding-top:6px;border-top:1px solid #F1F5F9;">'
            f'<span style="font-size:10px;color:#64748B;">📌 * 표시: 하위 진료과 합산 그룹</span>'
            f'<span style="font-size:10px;color:{C["blue"]};">■ 외래</span>'
            f'<span style="font-size:10px;color:{C["indigo"]};">■ 입원</span>'
            f'<span style="font-size:10px;color:{C["violet"]};">■ 재원</span>'
            f'<span style="font-size:10px;color:{C["teal"]};">■ 예방접종</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="padding:28px;text-align:center;color:#94A3B8;font-size:13px;">'
            'V_DAY_INWEON_3 데이터 없음<br>'
            '<span style="font-size:11px;">전일(SYSDATE-1) 데이터를 조회합니다.</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════
# 카드 매칭 탭 (v2.2 신규)
# ════════════════════════════════════════════════════════════════════
def _tab_card_match() -> None:
    """
    카드사 승인내역 ↔ 병원 결제 내역 매칭 탭 (v2.3)

    [두 가지 매칭 방향]
    ① 정방향: 카드사 xlsx 업로드 → 병원 DB 매칭
               카드사 데이터 기준 → 병원 누락건 탐지
    ② 역방향: 병원 DB 조회 → 카드사 xlsx 매칭
               병원 데이터 기준 → 카드사 미확인건 탐지

    [v2.3 수정]
    · DB 조회 성공/실패 vs 데이터 없음 구분 처리
    · V_KIOSK_CARD_APPROVAL 없을 때 실제 수납 테이블 폴백 조회
    · 날짜 선택 앞에 "입금일자" 레이블 표시
    · 카드 탭은 개인정보 포함 → LLM 채팅에서 제외
    """
    import io
    import datetime as _dt_cm

    # ── 헤더 ────────────────────────────────────────────────────────
    st.markdown(
        f'<div class="wd-card" style="border-top:3px solid {C["indigo"]};padding:16px;">',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="wd-sec">'
        f'<span class="wd-sec-bar" style="background:{C["indigo"]};"></span>'
        f'💳 카드사 승인내역 ↔ 병원 결제 매칭'
        f'<span class="wd-sec-sub"> 정방향(카드사→병원) / 역방향(병원→카드사) 이중 검증</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 공통 컨트롤 행: 입금일자 + 매칭방향 ────────────────────────
    col_lbl, col_dt, col_dir, col_btn = st.columns([1, 2, 3, 1], gap="small")

    with col_lbl:
        # "입금일자" 레이블
        st.markdown(
            f'<div style="font-size:12px;font-weight:700;color:{C["t2"]};'
            f'padding:8px 0 0 4px;">📅 입금일자</div>',
            unsafe_allow_html=True,
        )

    with col_dt:
        _cm_date = st.date_input(
            "입금일자",
            value=st.session_state.get("cm_date", _dt_cm.date.today()),
            key="cm_date",
            label_visibility="collapsed",
            format="YYYY-MM-DD",
            max_value=_dt_cm.date.today(),
            help="병원 DB 조회 기준 날짜 (카드사 파일 승인일시와 일치시키세요, ±2일 허용)",
        )
        _cm_date_str = _cm_date.strftime("%Y%m%d")

    with col_dir:
        _direction = st.radio(
            "매칭 방향",
            options=["① 정방향 — 카드사 xlsx → 병원 DB 매칭",
                     "② 역방향 — 병원 DB → 카드사 xlsx 매칭"],
            key="cm_direction",
            label_visibility="collapsed",
            horizontal=True,
        )
        _is_forward = "정방향" in _direction

    with col_btn:
        _do_match = st.button(
            "🔍 매칭 실행",
            key="btn_card_match",
            type="primary",
            use_container_width=True,
        )

    # ── 파일 업로드 ─────────────────────────────────────────────────
    col_up, col_info = st.columns([4, 6], gap="small")
    with col_up:
        uploaded = st.file_uploader(
            "카드사 승인내역 xlsx",
            type=["xlsx", "xls"],
            key="card_match_file",
            help="카드사 사이트 → 승인내역 다운로드 → xlsx 업로드\n필수 컬럼: 승인일시, 승인번호, 승인금액",
        )
    with col_info:
        st.markdown(
            f'<div style="background:#F0F4FF;border:1px solid #BFDBFE;border-radius:8px;'
            f'padding:8px 14px;margin-top:4px;font-size:11.5px;color:{C["t2"]};">'
            f'<b>① 정방향</b>: 카드사 파일 기준 → 병원 DB 누락건 탐지 (카드사 있음 / 병원 없음 → 🔴)<br>'
            f'<b>② 역방향</b>: 병원 DB 기준 → 카드사 파일 미확인건 탐지 (병원 있음 / 카드사 없음 → 🟠)<br>'
            f'<span style="color:{C["t3"]};font-size:10.5px;">'
            f'🔒 이 탭의 승인번호·카드번호는 개인정보 — AI 채팅에서 제외됨</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 병원 DB 조회 — V_KIOSK_CARD_APPROVAL VIEW 전용 ─────────────
    # [보안 원칙] RAG_READONLY 계정은 지정된 VIEW에만 SELECT 권한 부여
    #             원본 테이블(WMACT07 등) 직접 접근 절대 금지
    #             VIEW 조회 실패 시 → 폴백 없이 오류 안내 표시
    # 날짜 ±2일 범위 (카드사 정산 지연·주말 고려)
    _d_from = (_dt_cm.datetime.strptime(_cm_date_str, "%Y%m%d")
               - _dt_cm.timedelta(days=2)).strftime("%Y%m%d")
    _d_to   = (_dt_cm.datetime.strptime(_cm_date_str, "%Y%m%d")
               + _dt_cm.timedelta(days=2)).strftime("%Y%m%d")

    _hosp_rows: List[Dict[str, Any]] = []
    _db_ok    = False
    _db_err   = ""
    _db_used_sql = ""

    try:
        from db.oracle_client import execute_query

        # VIEW 경유 조회 — RAG_READONLY SELECT 권한 있는 VIEW만 사용
        # 승인일시(W07ACTDAT)가 CHAR(8) YYYYMMDD 문자열
        # → TO_CHAR() 사용 금지 (ORA-01481), 문자열 직접 비교
        _sql_view = f"""
            SELECT
                승인일시                                      AS 거래일자,
                REGEXP_REPLACE(승인번호, '[^0-9]', '')        AS 승인번호,
                승인금액,
                NVL(카드사명, '')                             AS 카드사명,
                NVL(단말기ID, '')                             AS 단말기ID,
                NVL(설치위치, '')                             AS 설치위치
            FROM JAIN_WM.V_KIOSK_CARD_APPROVAL
            WHERE 승인일시 BETWEEN '{_d_from}' AND '{_d_to}'
            ORDER BY 승인일시
        """
        _rows_view = execute_query(_sql_view)
        if _rows_view is not None:
            _hosp_rows   = _rows_view
            _db_ok       = True
            _db_used_sql = "V_KIOSK_CARD_APPROVAL"
    except Exception as _e1:
        _db_ok  = False
        _db_err = str(_e1)
        logger.error(f"[CardMatch] V_KIOSK_CARD_APPROVAL 조회 실패: {_e1}")
        # ⚠️ 원본 테이블 직접 접근 폴백 없음
        # RAG_READONLY 계정 보안 정책상 WMACT07 등 원본 테이블 접근 금지
        # 해결 방법: DBeaver 관리자 계정으로 V_KIOSK_CARD_APPROVAL VIEW 생성 후
        #           RAG_READONLY에 GRANT SELECT ON JAIN_WM.V_KIOSK_CARD_APPROVAL TO RAG_READONLY

    # ── DB 상태 배지 ─────────────────────────────────────────────
    if _db_ok:
        _db_badge = (
            f'<span style="background:{C["green"]}1A;color:{C["green"]};'
            f'border-radius:5px;padding:2px 8px;font-size:11px;font-weight:700;">'
            f'✅ DB 연결 ({_db_used_sql} / {len(_hosp_rows):,}건)</span>'
        )
    else:
        # VIEW 조회 실패 → 관리자 조치 안내 표시 (원본 테이블 폴백 없음)
        _db_badge = (
            f'<span style="background:{C["red"]}1A;color:{C["red"]};'
            f'border-radius:5px;padding:2px 8px;font-size:11px;font-weight:700;">'
            f'❌ VIEW 조회 실패</span>'
            f'<span style="font-size:10.5px;color:{C["t3"]};margin-left:6px;">'
            f'DBeaver(관리자 계정)에서 V_KIOSK_CARD_APPROVAL VIEW 생성 후 '
            f'GRANT SELECT ON JAIN_WM.V_KIOSK_CARD_APPROVAL TO RAG_READONLY 실행 필요</span>'
        )

    st.markdown(
        f'<div style="margin:6px 0 8px;display:flex;align-items:center;gap:8px;">'
        f'<span style="font-size:11px;color:{C["t3"]};">병원 DB 현황:</span>'
        f'{_db_badge}'
        f'<span style="font-size:10.5px;color:{C["t3"]};">'
        f'입금일자 {_cm_date_str} ±2일 / 총 {len(_hosp_rows):,}건</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 카드사 파일 파싱 ────────────────────────────────────────────
    _df_card = None
    if uploaded:
        try:
            import pandas as pd

            _bytes = uploaded.read()
            _df_raw = pd.read_excel(io.BytesIO(_bytes), dtype=str)
            _df_raw.columns = [str(c).strip() for c in _df_raw.columns]

            _missing = {"승인일시", "승인번호", "승인금액"} - set(_df_raw.columns)
            if _missing:
                st.error(f"❌ 필수 컬럼 없음: {', '.join(_missing)} | 현재: {', '.join(_df_raw.columns[:8])}")
            else:
                # 정상승인만
                if "거래결과" in _df_raw.columns:
                    _df_card = _df_raw[_df_raw["거래결과"].str.contains("정상", na=False)].copy()
                else:
                    _df_card = _df_raw.copy()

                _df_card["_apv_no"] = (
                    _df_card["승인번호"].astype(str).str.strip()
                    .str.replace(r"\D", "", regex=True)
                )
                _df_card["_apv_amt"] = pd.to_numeric(
                    _df_card["승인금액"].astype(str)
                    .str.replace(r"[,￦₩\s]", "", regex=True)
                    .str.replace(r"[^\d\-]", "", regex=True),
                    errors="coerce"
                ).fillna(0).astype(int)
                _df_card["_apv_date"] = pd.to_datetime(
                    _df_card["승인일시"].astype(str).str[:10]
                    .str.replace(r"[/\-]", "", regex=True),
                    format="%Y%m%d", errors="coerce"
                ).dt.strftime("%Y%m%d")
                _df_card = _df_card[_df_card["_apv_amt"] > 0].reset_index(drop=True)

                # ── 카드번호 마스킹 (화면 표시 전 처리) ────────────────
                # 카드번호: "4111-36**-****-1271" → "4111-****-****-1271"
                # 앞 4자리·뒤 4자리만 남기고 중간 전체 마스킹
                if "카드번호" in _df_card.columns:
                    _df_card["카드번호_표시"] = _df_card["카드번호"].astype(str).apply(
                        lambda v: (
                            v[:4] + "-****-****-" + v[-4:]
                            if len(v.replace("-","").replace("*","")) >= 8
                            else "****-****-****-****"
                        )
                    )

                # 미리보기 — 카드번호 원본 대신 마스킹본 표시
                with st.expander(f"📄 카드사 파일 — {len(_df_card):,}건 (정상승인)", expanded=False):
                    _prev_cols = [c for c in [
                        "승인일시", "승인번호", "승인금액", "카드사",
                        "카드번호_표시",  # 원본 카드번호 대신 마스킹본 사용
                        "거래결과", "단말기ID", "설치위치",
                    ] if c in _df_card.columns]
                    _disp_df = _df_card[_prev_cols].rename(
                        columns={"카드번호_표시": "카드번호(마스킹)"}
                    )
                    st.dataframe(_disp_df.head(50), use_container_width=True, height=200)

        except Exception as _pe:
            st.error(f"❌ 파일 파싱 오류: {_pe}")
            _df_card = None

    # ── 역방향: 병원 DB 먼저 표시 ──────────────────────────────────
    if not _is_forward and _hosp_rows:
        import pandas as pd
        with st.expander(f"🏥 병원 DB 조회 결과 — {len(_hosp_rows):,}건", expanded=True):
            _h_df = pd.DataFrame(_hosp_rows)
            st.dataframe(_h_df, use_container_width=True, height=260)

    # ── 매칭 실행 조건 체크 ─────────────────────────────────────────
    if not _do_match and "card_match_result" not in st.session_state:
        if not uploaded:
            st.markdown(
                f'<div style="padding:30px;text-align:center;color:{C["t4"]};font-size:13px;">'
                f'{"① 카드사 xlsx를 업로드하고 [매칭 실행] 버튼을 클릭하세요." if _is_forward else "② 카드사 xlsx를 업로드하고 [매칭 실행] 버튼을 클릭하세요."}'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)
        return

    # ── 매칭 실행 ───────────────────────────────────────────────────
    if _do_match:
        import pandas as pd

        if not _db_ok and not _hosp_rows:
            st.error("❌ 병원 DB 연결 실패 — Oracle 연결 상태를 확인하세요.")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        if _df_card is None:
            st.warning("⚠️ 카드사 파일을 먼저 업로드하세요.")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        # 병원 dict: 승인번호 → 행 데이터
        _hosp_dict: Dict[str, Dict] = {}
        for _hr in _hosp_rows:
            _hno = str(_hr.get("승인번호", "") or "").strip().replace(" ", "")
            if _hno:
                _hosp_dict[_hno] = _hr

        # 카드사 dict: 승인번호 → 행 데이터
        _card_dict: Dict[str, Dict] = {}
        for _, _crow in _df_card.iterrows():
            _cno = str(_crow.get("_apv_no", "")).strip()
            if _cno:
                _card_dict[_cno] = _crow.to_dict()

        _results = []
        _card_matched = set()

        if _is_forward:
            # ── 정방향: 카드사 파일 기준 ─────────────────────────
            for _, _crow in _df_card.iterrows():
                _cno  = str(_crow["_apv_no"]).strip()
                _camt = int(_crow["_apv_amt"])
                _hrow = _hosp_dict.get(_cno)

                if _hrow:
                    _hamt   = int(_hrow.get("승인금액", 0) or 0)
                    _status = "정상" if _camt == _hamt else "금액불일치"
                    _card_matched.add(_cno)
                else:
                    _status = "누락"
                    _hrow   = {}

                _results.append({
                    "상태":       _status,
                    "거래일자":   str(_crow.get("_apv_date", ""))[:8],
                    "승인번호":   _cno,
                    "카드사금액": _camt,
                    "병원금액":   int(_hrow.get("승인금액", 0) or 0),
                    "차이":       _camt - int(_hrow.get("승인금액", 0) or 0),
                    "카드사":     str(_crow.get("카드사", "")) if "카드사" in _df_card.columns else "",
                    "단말기ID":   str(_crow.get("단말기ID", "") or _hrow.get("단말기ID", "")),
                    "설치위치":   str(_crow.get("설치위치", "") or _hrow.get("설치위치", "")),
                    "출처":       "카드사→병원",
                })
            # 병원에만 있는 건 추가 (역도 감지)
            for _hno, _hr in _hosp_dict.items():
                if _hno not in _card_matched:
                    _results.append({
                        "상태":       "병원만",
                        "거래일자":   str(_hr.get("거래일자", ""))[:8],
                        "승인번호":   _hno,
                        "카드사금액": 0,
                        "병원금액":   int(_hr.get("승인금액", 0) or 0),
                        "차이":       -int(_hr.get("승인금액", 0) or 0),
                        "카드사":     str(_hr.get("카드사명", "")),
                        "단말기ID":   str(_hr.get("단말기ID", "")),
                        "설치위치":   str(_hr.get("설치위치", "")),
                        "출처":       "병원만",
                    })
        else:
            # ── 역방향: 병원 DB 기준 ──────────────────────────────
            for _hno, _hr in _hosp_dict.items():
                _hamt = int(_hr.get("승인금액", 0) or 0)
                _crow = _card_dict.get(_hno)

                if _crow:
                    _camt   = int(_crow.get("_apv_amt", 0) or 0)
                    _status = "정상" if _hamt == _camt else "금액불일치"
                    _card_matched.add(_hno)
                else:
                    _status = "병원만"
                    _crow   = {}
                    _camt   = 0

                _results.append({
                    "상태":       _status,
                    "거래일자":   str(_hr.get("거래일자", ""))[:8],
                    "승인번호":   _hno,
                    "병원금액":   _hamt,
                    "카드사금액": _camt,
                    "차이":       _hamt - _camt,
                    "카드사":     str(_hr.get("카드사명", "")),
                    "단말기ID":   str(_hr.get("단말기ID", "")),
                    "설치위치":   str(_hr.get("설치위치", "")),
                    "출처":       "병원→카드사",
                })
            # 카드사에만 있는 건 추가
            for _cno, _crow in _card_dict.items():
                if _cno not in _card_matched:
                    _camt = int(_crow.get("_apv_amt", 0) or 0)
                    _results.append({
                        "상태":       "누락",
                        "거래일자":   str(_crow.get("_apv_date", ""))[:8],
                        "승인번호":   _cno,
                        "병원금액":   0,
                        "카드사금액": _camt,
                        "차이":       _camt,
                        "카드사":     str(_crow.get("카드사", "")) if "카드사" in _df_card.columns else "",
                        "단말기ID":   str(_crow.get("단말기ID", "")),
                        "설치위치":   str(_crow.get("설치위치", "")),
                        "출처":       "카드사만",
                    })

        st.session_state["card_match_result"]  = _results
        st.session_state["card_match_dir"]     = "정방향" if _is_forward else "역방향"
        st.session_state["card_match_date"]    = _cm_date_str

    _results = st.session_state.get("card_match_result", [])
    _match_dir = st.session_state.get("card_match_dir", "정방향")
    if not _results:
        st.markdown("</div>", unsafe_allow_html=True)
        return

    # ── KPI 요약 ────────────────────────────────────────────────────
    _cnt_ok   = sum(1 for r in _results if r["상태"] == "정상")
    _cnt_miss = sum(1 for r in _results if r["상태"] == "누락")
    _cnt_amt  = sum(1 for r in _results if r["상태"] == "금액불일치")
    _cnt_hosp = sum(1 for r in _results if r["상태"] == "병원만")
    _total    = len(_results)
    _match_rate = round(_cnt_ok / max(_cnt_ok + _cnt_miss + _cnt_amt, 1) * 100, 1)

    _gap()
    _label_miss = "누락 (카드사○/병원×)" if _match_dir == "정방향" else "누락 (카드사만)"
    _label_hosp = "병원만 (병원○/카드사×)" if _match_dir == "정방향" else "병원만 (카드사 미확인)"

    kc1, kc2, kc3, kc4, kc5 = st.columns(5, gap="small")
    def _cm_kpi(col, icon, label, val, color, sub=""):
        col.markdown(
            f'<div class="fn-kpi" style="border-top:3px solid {color};min-height:90px;">'
            f'<div class="fn-kpi-icon">{icon}</div>'
            f'<div class="fn-kpi-label" style="font-size:9px;">{label}</div>'
            f'<div class="fn-kpi-value" style="color:{color};font-size:26px;">{val}</div>'
            f'<div class="fn-kpi-sub">{sub}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    _cm_kpi(kc1, "✅", "정상 매칭",    f"{_cnt_ok:,}건",   C["green"],  f"매칭률 {_match_rate}%")
    _cm_kpi(kc2, "🔴", _label_miss,   f"{_cnt_miss:,}건", C["red"],    "즉시 확인 필요")
    _cm_kpi(kc3, "🟡", "금액 불일치", f"{_cnt_amt:,}건",  C["yellow"], "승인번호 일치/금액 다름")
    _cm_kpi(kc4, "🟠", _label_hosp,  f"{_cnt_hosp:,}건", C["orange"], "카드사 재확인")
    _cm_kpi(kc5, "📋", f"전체({_match_dir})", f"{_total:,}건", C["t2"], f"{_cm_date_str} 기준")
    _gap()

    # ── 필터 + 다운로드 ─────────────────────────────────────────────
    _flt_c, _dl_c = st.columns([7, 3], gap="small")
    with _flt_c:
        _flt_status = st.multiselect(
            "상태 필터",
            options=["정상", "누락", "금액불일치", "병원만"],
            default=["누락", "금액불일치", "병원만"],
            key="cm_flt_status",
            label_visibility="collapsed",
        )
    with _dl_c:
        import pandas as _pd_dl
        _dl_csv = _pd_dl.DataFrame(_results).to_csv(
            index=False, encoding="utf-8-sig"
        ).encode("utf-8-sig")
        st.download_button(
            "⬇️ 전체 결과 CSV",
            data=_dl_csv,
            file_name=f"카드매칭_{_match_dir}_{_cm_date_str}.csv",
            mime="text/csv",
            key="btn_cm_dl",
            use_container_width=True,
        )

    # ── 결과 테이블 ─────────────────────────────────────────────────
    _filtered = [r for r in _results
                 if r["상태"] in (_flt_status or ["정상","누락","금액불일치","병원만"])]

    _STATUS_STYLE = {
        "정상":      ("#DCFCE7","#15803D","#059669","✅ 정상"),
        "누락":      ("#FEE2E2","#991B1B","#DC2626","🔴 누락"),
        "금액불일치": ("#FEF3C7","#92400E","#D97706","🟡 금액차이"),
        "병원만":    ("#FFF7ED","#9A3412","#EA580C","🟠 병원만"),
    }
    _TH = ("padding:7px 10px;font-size:10.5px;font-weight:700;"
           "color:#64748B;border-bottom:2px solid #E2E8F0;"
           "background:#F8FAFC;white-space:nowrap;")
    _tbl = (
        '<div style="overflow-x:auto;max-height:500px;overflow-y:auto;">'
        '<table style="width:100%;border-collapse:collapse;font-size:12.5px;">'
        '<thead style="position:sticky;top:0;z-index:2;"><tr>'
        f'<th style="{_TH}text-align:center;width:90px;">상태</th>'
        f'<th style="{_TH}text-align:center;width:80px;">거래일자</th>'
        f'<th style="{_TH}text-align:left;width:95px;">승인번호</th>'
        f'<th style="{_TH}text-align:right;width:90px;">카드사금액</th>'
        f'<th style="{_TH}text-align:right;width:90px;">병원금액</th>'
        f'<th style="{_TH}text-align:right;width:80px;">차이</th>'
        f'<th style="{_TH}text-align:left;width:70px;">카드사</th>'
        f'<th style="{_TH}text-align:left;width:90px;">단말기</th>'
        f'<th style="{_TH}text-align:left;">설치위치</th>'
        '</tr></thead><tbody>'
    )
    _fmta = lambda v: f"{v:,}" if v else "─"
    _fmtd = lambda v: (
        f'<span style="color:#DC2626;font-weight:700;">▲{v:,}</span>' if v > 0
        else f'<span style="color:#2563EB;font-weight:700;">▼{abs(v):,}</span>' if v < 0
        else "─"
    )
    for _i, _r in enumerate(_filtered[:500]):
        _st = _r["상태"]
        _bg, _tx, _ac, _badge = _STATUS_STYLE.get(_st, ("#F8FAFC","#334155","#334155",_st))
        _rbg = _bg if _st != "정상" else ("#F8FAFC" if _i%2==0 else "#FFFFFF")
        _td  = f"padding:6px 10px;background:{_rbg};border-bottom:1px solid #F0F4F8;"
        _tbl += (
            f"<tr>"
            f'<td style="{_td}text-align:center;">'
            f'<span style="background:{_bg};color:{_ac};border-radius:5px;'
            f'padding:2px 7px;font-size:10.5px;font-weight:700;">{_badge}</span></td>'
            f'<td style="{_td}text-align:center;font-family:Consolas,monospace;'
            f'color:{C["t3"]};font-size:11.5px;">{_r["거래일자"]}</td>'
            f'<td style="{_td}font-family:Consolas,monospace;font-weight:600;'
            f'color:{C["t1"]};font-size:11.5px;">{_r["승인번호"]}</td>'
            f'<td style="{_td}text-align:right;font-family:Consolas,monospace;'
            f'color:{C["blue"]};">{_fmta(_r["카드사금액"])}</td>'
            f'<td style="{_td}text-align:right;font-family:Consolas,monospace;'
            f'color:{C["indigo"]};">{_fmta(_r["병원금액"])}</td>'
            f'<td style="{_td}text-align:right;">{_fmtd(_r["차이"])}</td>'
            f'<td style="{_td}color:{C["t2"]};">{_r.get("카드사","") or "─"}</td>'
            f'<td style="{_td}color:{C["t3"]};font-size:11.5px;">{_r.get("단말기ID","") or "─"}</td>'
            f'<td style="{_td}color:{C["t2"]};">{_r.get("설치위치","") or "─"}</td>'
            f"</tr>"
        )
    if len(_filtered) > 500:
        _tbl += (
            f'<tr><td colspan="9" style="padding:8px;text-align:center;'
            f'color:{C["t3"]};font-size:11px;">... 이하 {len(_filtered)-500:,}건 생략 — CSV 다운로드 이용</td></tr>'
        )
    if not _filtered:
        _tbl += (
            f'<tr><td colspan="9" style="padding:30px;text-align:center;'
            f'color:{C["t4"]};">필터 조건에 맞는 데이터 없음</td></tr>'
        )
    st.markdown(_tbl + "</tbody></table></div>", unsafe_allow_html=True)

    # ── 범례 ────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;'
        f'padding-top:6px;border-top:1px solid #F1F5F9;font-size:10.5px;">'
        f'<span style="color:{C["green"]};font-weight:700;">✅ 정상: 승인번호+금액 일치</span>'
        f'<span style="color:{C["red"]};font-weight:700;">🔴 누락: 한쪽에만 존재 → 즉시 확인</span>'
        f'<span style="color:{C["yellow"]};font-weight:700;">🟡 금액불일치: 오류 가능성</span>'
        f'<span style="color:{C["orange"]};font-weight:700;">🟠 병원만: 카드사 재확인 필요</span>'
        f'<span style="color:{C["t3"]};">🔒 승인번호·카드번호 → AI 채팅 미전송</span>'
        f'</div>',
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

    # ── 날짜 계산 — widget key(fn_sel_date)에서 직접 읽음 ──────
    # Streamlit widget key는 rerun 시 즉시 최신값 반영
    # fn_query_date 중간 변수 제거 → 동기화 지연 문제 해결
    import datetime as _dt_main
    _is_custom = st.session_state.get("fn_use_custom_date", False)
    if _is_custom:
        _sel_d = st.session_state.get("fn_sel_date", _dt_main.date.today())
        if isinstance(_sel_d, _dt_main.date):
            _qdate = _sel_d.strftime("%Y%m%d")
        else:
            _qdate = str(_sel_d).replace("-", "")[:8]
    else:
        _qdate = ""

    # 실시간 데이터: 날짜 지정 시에도 현재 시점 유지
    opd_kpi      = (_fq("opd_kpi") or [{}])[0]
    dept_status  = _fq("opd_dept_status")
    kiosk_status = _fq("kiosk_status")
    # 날짜 적용 데이터
    kiosk_by_dept       = _fq("kiosk_by_dept",       _qdate if _is_custom else "")
    kiosk_counter_trend = _fq("kiosk_counter_trend", _qdate if _is_custom else "")
    discharge_pipe      = _fq("discharge_pipeline",  _qdate if _is_custom else "")
    opd_dept_trend      = _fq("opd_dept_trend",      _qdate if _is_custom else "")
    bed_detail          = _fq("ward_bed_detail",      _qdate if _is_custom else "")
    ward_room_detail    = _fq("ward_room_detail")
    daily_dept_stat     = _fq("daily_dept_stat",     _qdate if _is_custom else "")
    day_inweon          = _fq("day_inweon",           _qdate if _is_custom else "")
    finance_today = _fq("finance_today")
    finance_trend = _fq("finance_trend")
    finance_by_dept = _fq("finance_by_dept")
    overdue_stat = _fq("overdue_stat")
    waittime_trend = _fq("waittime_trend")
    los_dist = _fq("los_dist")

    # 탑바
    st.markdown('<div class="fn-topbar"></div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([3, 4, 5], vertical_alignment="center")
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
        b1, b2, b3 = st.columns(3, gap="small")
        with b1:
            if st.button("🔄 새로고침", key="fn_refresh",
                         use_container_width=True, type="secondary"):
                st.cache_data.clear()
                st.rerun()
        with b2:
            _auto = st.session_state.get("fn_auto", False)
            if st.button("⏸ 자동갱신" if _auto else "▶ 자동갱신",
                         key="fn_auto_toggle", use_container_width=True, type="secondary"):
                st.session_state["fn_auto"] = not _auto
                st.rerun()
        with b3:
            st.markdown(
                '<a href="http://192.1.1.231:8501" target="_blank" style="'
                'display:block;text-align:center;background:#EFF6FF;color:#1E40AF;'
                'border:1.5px solid #BFDBFE;border-radius:20px;padding:5px 0;'
                'font-size:11.5px;font-weight:600;text-decoration:none;">🔗 병동 대시보드</a>',
                unsafe_allow_html=True,
            )
    with c3:
        # ── 날짜 선택 영역 ─────────────────────────────────────
        # datetime은 상단에서 _dt_main으로 이미 임포트
        _dc1, _dc2, _dc3 = st.columns([2, 3, 2], gap="small")
        with _dc1:
            _oc = "#16A34A" if oracle_ok else "#F59E0B"
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:5px;padding:6px 0;">'
                f'<span style="width:8px;height:8px;border-radius:50%;'
                f'background:{_oc};flex-shrink:0;display:inline-block;"></span>'
                f'<span style="font-size:11px;font-weight:700;color:{_oc};'
                f'white-space:nowrap;">{"Oracle 연결" if oracle_ok else "Oracle 미연결"}</span>'
                f"</div>",
                unsafe_allow_html=True,
            )
        with _dc2:
            # 날짜 고정 여부
            _use_custom = st.toggle(
                "📅 날짜 지정",
                value=st.session_state.get("fn_use_custom_date", False),
                key="fn_use_custom_date",
            )
        with _dc3:
            if st.session_state.get("fn_use_custom_date", False):
                st.date_input(
                    "",
                    value=st.session_state.get("fn_sel_date", _dt_main.date.today()),
                    key="fn_sel_date",      # widget key → rerun 시 즉시 반영
                    label_visibility="collapsed",
                    format="YYYY-MM-DD",
                    max_value=_dt_main.date.today(),  # 미래 날짜 방지
                )
                # 선택 날짜 배너 표시
                _shown = st.session_state.get("fn_sel_date", _dt_main.date.today())
                st.markdown(
                    f'<div style="font-size:10px;color:{C["indigo"]};font-weight:700;'
                    f'text-align:center;">📅 {_shown} 기준</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="font-size:11px;color:{C["t3"]};font-family:Consolas,monospace;'
                    f'padding:6px 0;text-align:right;">'
                    f'{time.strftime("%Y-%m-%d %H:%M")}</div>',
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

    # 3탭 (실시간 현황 / 주간추이분석 / 카드 매칭)
    t1, t3, t_card = st.tabs(["🏥 실시간 현황", "📈 주간추이분석", "💳 카드 매칭"])
    with t1:
        _tab_realtime(opd_kpi, dept_status, kiosk_status, discharge_pipe, bed_detail,
                      kiosk_by_dept=kiosk_by_dept,
                      kiosk_counter_trend=kiosk_counter_trend,
                      ward_room_detail=ward_room_detail,
                      opd_dept_trend=opd_dept_trend,
                      daily_dept_stat=daily_dept_stat)
        # ── [v2.1] 세부과 일일집계표 — 실시간 현황 탭에만 표시 ────────
        # 이전: 탭 밖 공통 영역 → 통계·분석 탭에서도 보여 중복 발생
        # 수정: with t1: 블록 안으로 이동 → 실시간 현황 탭에서만 렌더링
        _gap()
        _render_day_inweon(day_inweon)
    with t3:
        _tab_analytics(opd_dept_trend, waittime_trend, los_dist,
                         daily_dept_stat=daily_dept_stat)
    with t_card:
        # ── [v2.2] 카드사 승인내역 ↔ 병원 결제 매칭 탭 ────────────
        # 카드사 xlsx 업로드 → 승인번호+금액으로 Oracle V_KIOSK_CARD_APPROVAL 매칭
        _tab_card_match()

    # ── AI 채팅 분석 (카드 매칭 탭 활성 시 제외) ────────────────────
    # 카드 매칭 탭: 승인번호·카드번호 등 개인정보 포함 → LLM 미전송
    _active_tab = st.session_state.get("cm_direction", "")
    _is_card_tab = st.session_state.get("card_match_result") is not None and \
                   "card_match" in str(st.session_state.get("card_match_file", ""))
    # Streamlit은 활성 탭을 직접 감지할 수 없으므로,
    # 카드 매칭 결과가 session_state에 있고 카드 탭이 마지막으로 사용된 경우 제외
    # 단순하게: 항상 AI 채팅 표시하되 카드 매칭 탭 데이터는 컨텍스트에 포함 안 함
    _gap()
    _render_finance_llm_chat(
        bed_detail=bed_detail,
        dept_status=dept_status,
        kiosk_by_dept=kiosk_by_dept,
        daily_dept_stat=daily_dept_stat,
        kiosk_counter_trend=kiosk_counter_trend,
        discharge_pipe=discharge_pipe,
        # 카드 매칭 데이터는 의도적으로 전달하지 않음 (개인정보 보호)
    )

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