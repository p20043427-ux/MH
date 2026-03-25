"""
llm/data_explainer.py  ─  쿼리 결과 LLM 해석 (v2.1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v2.1 변경사항]

■ tuple rows 완전 지원
  기존: rows: List[Dict] 만 처리
  수정: _normalize_rows_to_dict() 헬퍼로 tuple → dict 자동 변환
  · _build_data_summary(): r[col] dict 접근 전 tuple 변환
  · analyze_query_result(): rows[0].keys() 전 tuple 변환
  · explain_data(): 진입부에서 rows 타입 정규화

■ LLM 전달 데이터 안전성 강화
  · _build_data_summary() 에서 샘플 최대 20행만 JSON 직렬화
  · PII 마스킹 완료 여부 확인 로그 추가
  · 숫자 통계 계산 시 tuple rows 지원

[v2.0 기능 유지]
  · smart_aggregate() — 원시 로우 → 집계 차트
  · CHART_NONE 반환 + 안내 메시지
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# ──────────────────────────────────────────────────────────────────────
#  차트 유형 상수
# ──────────────────────────────────────────────────────────────────────

CHART_LINE = "line"
CHART_BAR = "bar"
CHART_BAR_H = "barh"
CHART_PIE = "pie"
CHART_HIST = "hist"
CHART_NONE = "none"

# [v2.3 추가] 비차트 시각화 타입
CHART_GRID = "grid"  # 데이터 그리드 (리스트/상세 조회)
CHART_KPI = "kpi"  # KPI 카드 (단순 수치/집계 결과)

# 차트 계열 타입 (시각화 필요)
_CHART_TYPES = {CHART_LINE, CHART_BAR, CHART_BAR_H, CHART_PIE, CHART_HIST}
# 비차트 계열 타입 (테이블/카드 렌더링)
_NONCHART_TYPES = {CHART_GRID, CHART_KPI, CHART_NONE}

# ──────────────────────────────────────────────────────────────────────
#  컬럼 분류 정규식
# ──────────────────────────────────────────────────────────────────────

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AI 캐릭터(Persona) 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PERSONA_DOCTOR = "doctor"  # 의사 — 임상/진단 관점
PERSONA_NURSE = "nurse"  # 간호사 — 환자 케어/업무 흐름 관점
PERSONA_ADMIN = "admin"  # 행정 — 운영/비용/효율 관점
PERSONA_DEFAULT = "default"  # 기본 — 범용 데이터 분석가

_PERSONA_PROMPTS: Dict[str, str] = {
    PERSONA_DOCTOR: """당신은 **대학병원 전문의(내과 과장)** 입니다.
환자 임상 데이터를 분석하며, 아래 관점으로 해석합니다:
- 중증도 분포, 응급 처치 적시성, 진단 정확도에 주목
- 의학적 표현 사용 (triage, disposition, acuity 등)
- 병원 표준 프로토콜 및 임상 지침 관점의 시사점 제시
- 간결하고 전문적인 어조, 의료진 용어 사용 가능""",
    PERSONA_NURSE: """당신은 **응급실 수간호사** 입니다.
환자 케어와 업무 흐름 데이터를 분석하며, 아래 관점으로 해석합니다:
- 환자 체류 시간, 처치 대기 시간, 병동 이송 현황에 주목
- 간호 인력 배치, 병실 가용성, 업무 부하 관점의 시사점
- 환자 안전과 서비스 질 향상 중심의 제언
- 따뜻하고 실무적인 어조""",
    PERSONA_ADMIN: """당신은 **병원 경영기획팀 실장** 입니다.
운영 효율성과 성과 지표 데이터를 분석하며, 아래 관점으로 해석합니다:
- 병상 가동률, 내원 건수, 처리량(throughput) 트렌드에 주목
- 비용 효율, 수익성, 자원 최적화 관점의 시사점
- 벤치마크 비교 및 개선 방향 제시
- 명확하고 데이터 중심적인 어조, KPI 표현 활용""",
    PERSONA_DEFAULT: """당신은 **병원 데이터 분석 전문가** 입니다.
Oracle 쿼리 결과를 병원 실무자에게 쉽고 명확하게 설명합니다.""",
}

# 질문 키워드 → 자동 페르소나 매핑
_KEYWORD_PERSONA: List[tuple] = [
    # (키워드 리스트, 페르소나)
    (
        [
            "중증도",
            "triage",
            "진단",
            "처방",
            "검사",
            "치료",
            "임상",
            "환자 상태",
            "입원 지시",
        ],
        PERSONA_DOCTOR,
    ),
    (
        [
            "간호",
            "케어",
            "병동 이송",
            "처치 대기",
            "체류시간",
            "보호자",
            "상태 변화",
            "활력징후",
        ],
        PERSONA_NURSE,
    ),
    (
        ["매출", "수익", "비용", "가동률", "효율", "건수 추이", "통계", "실적", "운영"],
        PERSONA_ADMIN,
    ),
]


def detect_persona(question: str, override: str = "") -> str:
    """
    질문 내용 또는 사용자 선택에서 최적 페르소나를 결정합니다.

    Args:
        question: 사용자 질문
        override: 사용자가 명시적으로 선택한 페르소나 (없으면 자동 감지)

    Returns:
        PERSONA_* 상수 중 하나
    """
    if override and override in _PERSONA_PROMPTS:
        return override

    q = question.lower()
    for keywords, persona in _KEYWORD_PERSONA:
        if any(kw in q for kw in keywords):
            return persona

    # 응급 관련 → 의사 기본
    if any(kw in q for kw in ["응급", "내원", "emihptmi", "응급실"]):
        return PERSONA_DOCTOR

    return PERSONA_DEFAULT


_RE_DATE = re.compile(
    r"날짜|일자|일시|연월|YYYYMM|YM|MONTH|YEAR|QUARTER|분기|월$|연도|DATE|DAT$",
    re.IGNORECASE,
)
_RE_TIME = re.compile(
    # 시간 컬럼 (HHMM/HHMMSS 형식) — y축 수치로 쓰면 스파게티 차트 발생
    r"시간$|TIME$|TM$|HHMM|HHMMSS",
    re.IGNORECASE,
)
_RE_AGG_NUM = re.compile(
    r"건수|수$|환자수|개수|금액|매출|수익|비율|율$|"
    r"COUNT|SUM|AVG|MAX|MIN|TOTAL|AMOUNT|CNT|NUM|합계|평균|최대|최소",
    re.IGNORECASE,
)
_RE_CATEGORY = re.compile(
    r"병동|병실|진료과|과$|부서|구분|코드$|상태|성별|등급|구역|층$|유형|종류|"
    # 응급환자 테이블 코드 컬럼 추가
    r"INRT|INTP|INCD|EMRT|EMSY|DGKD|AREA|KTS|결과$|구분$|여부$",
    re.IGNORECASE,
)
_RE_AGE = re.compile(r"나이|연령|AGE", re.IGNORECASE)
_RE_DATE_STR = re.compile(r"^\d{4}[-/.]?\d{2}[-/.]?\d{2}$")
_RE_DATE8 = re.compile(r"^\d{8}$")  # YYYYMMDD 숫자 형식 (병원 OCS 공통)
_RE_YYYYMM = re.compile(r"^\d{4}[-/.]?\d{2}$")
_RE_TIME_VAL = re.compile(r"^\d{3,4}$")  # HMM / HHMM 형식 시간값


# ──────────────────────────────────────────────────────────────────────
#  DataAnalysisResult
# ──────────────────────────────────────────────────────────────────────


@dataclass
class DataAnalysisResult:
    """
    쿼리 결과 분석 결과.

    [v2.3 필드 구조]
    chart_type 이 CHART_GRID 인 경우 (리스트 쿼리):
      · rows/column_names → 그리드(데이터 테이블) 표시
      · chart_rows/chart_cols → 집계 요약 차트 데이터 (agg_chart_type 으로 렌더)
      · agg_chart_type/x/y → 집계 요약 차트 타입 (CHART_NONE 이면 미표시)

    chart_type 이 CHART_KPI 인 경우 (단순 집계):
      · rows/column_names → KPI 카드 표시

    chart_type 이 CHART_LINE/BAR 등인 경우 (집계 쿼리):
      · chart_rows/chart_cols → 차트 데이터
    """

    rows: List[Dict[str, Any]] = field(default_factory=list)
    column_names: List[str] = field(default_factory=list)
    row_count: int = 0
    chart_type: str = CHART_NONE  # CHART_LINE/BAR/PIE/GRID/KPI/NONE
    x_col: Optional[str] = None
    y_col: Optional[str] = None
    chart_rows: List[Dict[str, Any]] = field(default_factory=list)
    chart_cols: List[str] = field(default_factory=list)
    agg_label: str = ""
    explanation: str = ""
    sql_used: str = ""
    error: str = ""
    # [v2.3] 리스트(GRID) 데이터의 집계 요약 차트 (선택적)
    agg_chart_type: str = CHART_NONE
    agg_chart_x: Optional[str] = None
    agg_chart_y: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return self.row_count == 0

    @property
    def is_chart(self) -> bool:
        """차트 렌더링이 필요한 타입인지 여부."""
        return self.chart_type in _CHART_TYPES

    @property
    def is_grid(self) -> bool:
        """그리드(데이터 테이블) 렌더링인지 여부."""
        return self.chart_type == CHART_GRID

    @property
    def is_kpi(self) -> bool:
        """KPI 카드 렌더링인지 여부."""
        return self.chart_type == CHART_KPI

    @property
    def has_summary_chart(self) -> bool:
        """리스트 데이터에 집계 요약 차트가 있는지 여부."""
        return self.agg_chart_type not in (CHART_NONE, "", None)


# ──────────────────────────────────────────────────────────────────────
#  [v2.1 신규] rows 타입 정규화 헬퍼
# ──────────────────────────────────────────────────────────────────────


def _normalize_rows_to_dict(
    rows: List[Any],
    column_names: List[str],
) -> List[Dict[str, Any]]:
    """
    rows 를 list[dict] 로 정규화합니다.

    [왜 필요한가]
    · mask_dataframe() 반환값은 list[tuple]
    · _build_data_summary() 와 analyze_query_result() 는 r[col_name] dict 접근
    · tuple 에 str key 접근 시 TypeError → AI 해석 완전 실패

    Args:
        rows:         list[dict] 또는 list[tuple]
        column_names: tuple rows 를 dict 로 변환할 때 사용할 컬럼명

    Returns:
        list[dict] — 항상 dict 형태
    """
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    # tuple → dict
    return [dict(zip(column_names, r)) for r in rows]


# ──────────────────────────────────────────────────────────────────────
#  컬럼 분류
# ──────────────────────────────────────────────────────────────────────


def _classify_columns(
    rows: List[Dict[str, Any]],
    column_names: List[str],
) -> Dict[str, str]:
    """
    각 컬럼을 date/agg_num/category/age/text 로 분류합니다.

    분류 기준:
      1. 컬럼명 정규식 매칭 (가장 신뢰도 높음)
      2. 샘플 값 패턴 분석 (날짜 문자열 etc.)
    """
    result: Dict[str, str] = {}
    sample = rows[:30]

    for col in column_names:
        col_upper = col.upper()

        if _RE_DATE.search(col_upper):
            # 날짜 컬럼명 패턴
            result[col] = "date"
        elif _RE_TIME.search(col_upper):
            # [v2.2 추가] 시간 컬럼 → 차트 y축 사용 금지
            # PTMIAKTM(발병시간), PTMIINDT 등 HHMM 형식 컬럼을 numeric으로 오인하면 스파게티
            result[col] = "time"
        elif _RE_AGG_NUM.search(col_upper):
            # 집계 수치 컬럼명 패턴
            result[col] = "agg_num"
        elif _RE_AGE.search(col_upper):
            result[col] = "age"
        elif _RE_CATEGORY.search(col_upper):
            result[col] = "category"
        else:
            # 값 기반 분류
            vals = [str(r.get(col, "")) for r in sample if r.get(col) is not None]
            if vals:
                # [v2.2] YYYYMMDD(8자리) 숫자 형식 → date (병원 OCS 날짜 형식)
                date8_like = sum(1 for v in vals if _RE_DATE8.match(v))
                if date8_like / len(vals) >= 0.7:
                    result[col] = "date"
                    continue
                # 기존 날짜 패턴 (YYYY-MM-DD, YYYYMM 등)
                date_like = sum(
                    1 for v in vals if _RE_DATE_STR.match(v) or _RE_YYYYMM.match(v)
                )
                if date_like / len(vals) >= 0.7:
                    result[col] = "date"
                    continue
                # [v2.2] HHMM(3~4자리) 시간값 → time 타입 (numeric 오인 방지)
                time_like = sum(
                    1 for v in vals if _RE_TIME_VAL.match(v) and int(v) < 2400
                )
                if time_like / len(vals) >= 0.7:
                    result[col] = "time"
                    continue
                try:
                    float(vals[0].replace(",", ""))
                    result[col] = "numeric"
                except ValueError:
                    result[col] = "text"
            else:
                result[col] = "text"

    return result


def _is_raw_row_data(
    rows: List[Dict[str, Any]],
    column_names: List[str],
    cols: Dict[str, str],
) -> bool:
    """
    원시 로우 데이터 여부 판단.

    [v2.2 수정사항]
    · 이전: 행 수 > 10 조건 → 9행 환자 리스트가 False 반환 → 스파게티 차트 버그
    · 수정: 컬럼 수 기반 판단 추가
      - 컬럼 5개 이상 + agg_num 없음 → 리스트 데이터로 판단
      - 병원 업무 리스트는 보통 5~15개 컬럼으로 구성됨

    집계 쿼리: COUNT, SUM 등 집계 컬럼이 있으면 False (집계 결과)
    원시 로우: 집계 없고 다수 컬럼 → True (리스트 데이터)
    """
    has_agg = any(t == "agg_num" for t in cols.values())
    if has_agg:
        return False

    n_cols = len(column_names)
    n_rows = len(rows)

    # [핵심 수정] 컬럼 5개 이상이면 리스트 데이터로 판단
    # (환자 리스트, 입원 현황 등 업무 조회는 컬럼이 많음)
    if n_cols >= 5:
        return True

    # 기존 로직: 행이 많고 카테고리/날짜 mix
    if n_rows > 3:
        type_set = set(cols.values())
        return bool(type_set - {"agg_num", "date"})

    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  쿼리 의도 분류 — v2.3 핵심 로직
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_INTENT_LIST = "list"  # 레코드 리스트 → GRID
_INTENT_AGG = "aggregate"  # 집계 결과     → CHART / KPI
_INTENT_KPI = "kpi"  # 단건/단순집계  → KPI CARD

# 리스트 쿼리 키워드 (이런 질문은 차트보다 그리드가 적합)
_LIST_KEYWORDS: frozenset = frozenset(
    [
        "리스트",
        "목록",
        "현황",
        "현재",
        "오늘",
        "이번",
        "명단",
        "내역",
        "조회",
        "확인",
        "보여",
        "알려",
        "어떤",
        "어느",
        "누가",
        "환자",
        "list",
        "show",
        "detail",
        "record",
    ]
)

# KPI 쿼리 키워드 (단순 집계 결과 → 카드 표시)
_KPI_KEYWORDS: frozenset = frozenset(
    [
        "몇 명",
        "몇명",
        "총 몇",
        "총건수",
        "전체 건수",
        "합계",
        "평균",
        "최대",
        "최소",
        "건수는",
        "수는",
        "비율",
        "퍼센트",
        "%",
        "how many",
        "count",
        "total",
        "sum",
    ]
)


def classify_query_intent(
    question: str,
    rows: List[Dict[str, Any]],
    column_names: List[str],
    cols: Dict[str, str],
    sql: str = "",
) -> str:
    """
    쿼리 결과의 시각화 의도를 분류합니다.

    [판단 기준 — 우선순위 순]

    1. KPI: 1~3행 + 집계 컬럼만 있음 → 숫자 카드
       예) "응급환자 총 건수" → COUNT 1행 → KPI

    2. LIST: 컬럼 5개 이상 + 집계 없음 → 그리드
       예) "오늘 응급실 내원 환자 리스트" → 9컬럼 7행 → GRID
       질문에 리스트/현황/목록 키워드 포함 → 강제 GRID

    3. AGG: 집계 컬럼 있음 or 2~4개 컬럼 → 차트
       예) "월별 내원 건수" → 2컬럼 집계 → CHART

    Returns:
        _INTENT_LIST, _INTENT_AGG, _INTENT_KPI 중 하나
    """
    n_rows = len(rows)
    n_cols = len(column_names)
    has_agg = any(t == "agg_num" for t in cols.values())
    q_lower = question.lower()

    # ── KPI 판단: 1~3행 + 집계 컬럼 ───────────────────────────────
    if n_rows <= 3 and has_agg:
        return _INTENT_KPI

    # ── KPI 판단: 단일 숫자 결과 ──────────────────────────────────
    if n_rows == 1 and n_cols <= 4:
        agg_vals = [
            v
            for row in rows
            for k, v in row.items()
            if isinstance(v, (int, float)) and cols.get(k) in ("agg_num", "numeric")
        ]
        if agg_vals:
            return _INTENT_KPI

    # ── LIST 강제: 질문 키워드 ────────────────────────────────────
    if any(kw in q_lower for kw in _LIST_KEYWORDS) and not has_agg:
        return _INTENT_LIST

    # ── LIST 판단: 다컬럼 레코드 ──────────────────────────────────
    if n_cols >= 5 and not has_agg:
        return _INTENT_LIST

    # ── LIST 판단: 텍스트 컬럼만 있고 수치 없음 ───────────────────
    numeric_types = {"agg_num", "numeric", "age"}
    all_non_numeric = not any(t in numeric_types for t in cols.values())
    if all_non_numeric and n_cols >= 3:
        return _INTENT_LIST

    # ── AGG: 기본값 ───────────────────────────────────────────────
    return _INTENT_AGG


# ──────────────────────────────────────────────────────────────────────
#  detect_chart_type
# ──────────────────────────────────────────────────────────────────────


def detect_chart_type(
    rows: List[Dict[str, Any]],
    column_names: List[str],
    sql: str = "",
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    이미 집계된 데이터에서 차트 유형을 감지합니다.

    Returns:
        (chart_type, x_col, y_col)
    """
    if len(rows) < 2 or len(column_names) < 2:
        return CHART_NONE, None, None

    cols = _classify_columns(rows, column_names)

    # 날짜 × 수치 → 라인
    date_cols = [c for c, t in cols.items() if t == "date"]
    # [v2.2] "time" 타입은 y축 사용 금지 (HHMM 값은 숫자지만 수치 집계 무의미)
    num_cols = [c for c, t in cols.items() if t in ("agg_num", "numeric", "age")]

    if date_cols and num_cols:
        x, y = date_cols[0], num_cols[0]
        if len(set(r[x] for r in rows)) <= 50:
            return CHART_LINE, x, y

    # 카테고리 × 수치 → 바 또는 파이
    cat_cols = [c for c, t in cols.items() if t == "category"]
    if cat_cols and num_cols:
        x, y = cat_cols[0], num_cols[0]
        cardinality = len(set(str(r[x]) for r in rows))
        if cardinality <= 8:
            return CHART_PIE, x, y
        if cardinality <= 20:
            return CHART_BAR if len(rows) <= 15 else CHART_BAR_H, x, y

    # 수치 × 수치 → 바
    if len(num_cols) >= 2:
        return CHART_BAR, column_names[0], num_cols[0]

    # 텍스트 × 수치 → 바
    text_cols = [c for c, t in cols.items() if t == "text"]
    if text_cols and num_cols:
        x, y = text_cols[0], num_cols[0]
        cardinality = len(set(str(r[x]) for r in rows))
        if cardinality <= 20:
            return CHART_BAR_H if cardinality > 8 else CHART_BAR, x, y

    return CHART_NONE, None, None


# ──────────────────────────────────────────────────────────────────────
#  smart_aggregate (v2.0 기능 유지)
# ──────────────────────────────────────────────────────────────────────


def smart_aggregate(
    rows: List[Dict[str, Any]],
    column_names: List[str],
    sql: str = "",
) -> Tuple[str, Optional[str], Optional[str], List[Dict], List[str], str]:
    """
    원시 로우 데이터를 자동 집계하여 차트용 데이터를 반환합니다.

    집계 전략:
      1. 날짜 컬럼 있으면 → 월별 건수 집계 → 라인 차트
      2. 저카디널리티 카테고리 컬럼 → 건수 집계 → 바/파이
      3. 수치 컬럼 → 10구간 분포 → 히스토그램
      4. 집계 불가 → CHART_NONE

    Returns:
        (chart_type, x_col, y_col, chart_rows, chart_cols, agg_label)
    """
    cols = _classify_columns(rows, column_names)

    # 1) 날짜 컬럼 월별 집계
    date_col = next((c for c, t in cols.items() if t == "date"), None)
    if date_col:
        monthly: Dict[str, int] = {}
        for r in rows:
            val = str(r.get(date_col, "") or "")
            # [v2.2] YYYYMMDD(8자리) → YYYY-MM 변환 (병원 OCS 형식 지원)
            if re.match(r"\d{8}$", val):
                ym = val[:4] + "-" + val[4:6]  # 20260311 → 2026-03
            else:
                ym = val[:7]  # YYYY-MM
            if re.match(r"\d{4}-\d{2}", ym):
                monthly[ym] = monthly.get(ym, 0) + 1
        if len(monthly) >= 2:
            # [v2.2] sorted() 로 날짜 오름차순 정렬 보장
            chart_rows = [{"월": k, "건수": v} for k, v in sorted(monthly.items())]
            return (
                CHART_LINE,
                "월",
                "건수",
                chart_rows,
                ["월", "건수"],
                f"월별 건수 (총 {len(rows):,}건)",
            )
        elif len(monthly) == 1:
            # 날짜가 1가지만 (오늘 데이터) → 날짜 집계 의미 없음
            # → 카테고리 집계로 이동
            pass

    # 2) 카테고리 컬럼 건수 집계
    # [v2.2] 의미있는 카테고리 컬럼 우선 선택
    # 응급 테이블: KTS(중증도) > EMRT(진료결과) > EMSY(증상) > INRT(경로) 순
    _CAT_PRIORITY = [
        "KTS",
        "EMRT",
        "EMSY",
        "INRT",
        "INMN",
        "AREA",
        "DEPT",
        "CODE",
        "TYPE",
    ]

    def _cat_priority_key(col_name: str) -> int:
        for i, kw in enumerate(_CAT_PRIORITY):
            if kw in col_name.upper():
                return i
        return 99

    cat_candidates = [(c, t) for c, t in cols.items() if t == "category"]
    cat_candidates.sort(key=lambda x: _cat_priority_key(x[0]))
    cat_col = cat_candidates[0][0] if cat_candidates else None

    if cat_col:
        counter: Counter = Counter(str(r.get(cat_col, "(없음)")) for r in rows)
        if 1 < len(counter) <= 30:
            top = counter.most_common(20)
            chart_rows = [{"분류": k, "건수": v} for k, v in top]
            cardinality = len(counter)
            ctype = (
                CHART_BAR_H
                if cardinality > 8
                else (CHART_PIE if cardinality <= 6 else CHART_BAR)
            )
            return (
                ctype,
                "분류",
                "건수",
                chart_rows,
                ["분류", "건수"],
                f"{cat_col}별 건수",
            )

    # 3) 수치 컬럼 분포
    num_col = next((c for c, t in cols.items() if t in ("age", "numeric")), None)
    if num_col:
        vals = [
            float(r[num_col]) for r in rows if isinstance(r.get(num_col), (int, float))
        ]
        if len(vals) >= 10:
            mn, mx = min(vals), max(vals)
            step = (mx - mn) / 10 or 1
            bins: Dict[str, int] = {}
            for v in vals:
                b = int((v - mn) / step)
                label = f"{mn + b * step:.0f}~{mn + (b + 1) * step:.0f}"
                bins[label] = bins.get(label, 0) + 1
            chart_rows = [{"구간": k, "인원": v} for k, v in bins.items()]
            return (
                CHART_HIST,
                "구간",
                "인원",
                chart_rows,
                ["구간", "인원"],
                f"{num_col} 분포",
            )

    return CHART_NONE, None, None, [], [], ""


# ──────────────────────────────────────────────────────────────────────
#  데이터 요약 (LLM 컨텍스트 생성)
# ──────────────────────────────────────────────────────────────────────


def _build_data_summary(
    rows: List[Any],
    column_names: List[str],
    max_sample_rows: int = 20,
) -> str:
    """
    LLM 에 전달할 데이터 요약 텍스트를 생성합니다.

    [v2.1 수정]
    · rows 가 list[tuple] 이면 list[dict] 로 자동 변환
      → r[col_name] dict 접근 TypeError 원천 차단
    · 마스킹 확인 로그 추가

    Args:
        rows:            쿼리 결과 (list[dict] 또는 list[tuple])
        column_names:    컬럼명 목록
        max_sample_rows: LLM 에 전달할 최대 샘플 행 수 (기본 20)

    Returns:
        헤더 + 샘플 JSON + 수치 통계 텍스트
    """
    # [v2.1] tuple → dict 변환 (핵심 수정)
    dict_rows = _normalize_rows_to_dict(rows, column_names)

    total = len(dict_rows)
    sample = dict_rows[:max_sample_rows]

    header = f"총 {total}행, {len(column_names)}개 컬럼\n"
    header += f"컬럼: {', '.join(column_names)}\n\n"
    sample_text = f"샘플 데이터 (최대 {max_sample_rows}행):\n"

    # [v2.1] PII 마스킹 확인 로그 (DEBUG)
    # 마스킹된 값 패턴 예시: "홍**", "010-****-5678", "9**408-*******"
    _sample_json = json.dumps(sample, ensure_ascii=False, default=str, indent=2)
    logger.debug(
        f"[LLM 전달 샘플 미리보기] 컬럼={column_names}, "
        f"첫 행={json.dumps(sample[0] if sample else {}, ensure_ascii=False, default=str)}"
    )
    sample_text += _sample_json

    # 수치 통계 요약
    stats_lines: List[str] = []
    for col in column_names:
        numeric_vals = [
            float(r[col]) for r in dict_rows if isinstance(r.get(col), (int, float))
        ]
        if numeric_vals:
            stats_lines.append(
                f"  {col}: 합계={sum(numeric_vals):,.1f}, "
                f"평균={sum(numeric_vals) / len(numeric_vals):,.1f}, "
                f"최소={min(numeric_vals):,.1f}, 최대={max(numeric_vals):,.1f}"
            )

    stats_text = (
        ("\n\n수치 통계 요약:\n" + "\n".join(stats_lines)) if stats_lines else ""
    )
    return header + sample_text + stats_text


# ──────────────────────────────────────────────────────────────────────
#  LLM 해석 스트리밍
# ──────────────────────────────────────────────────────────────────────


def explain_data(
    question: str,
    rows: List[Any],  # list[dict] (PII 컬럼 제거 완료)
    column_names: List[str],
    sql: str,
    chart_type: str = CHART_NONE,
    agg_label: str = "",
    pii_removed_cols: List[str] = None,  # [v2.3] 제거된 PII 컬럼명
    persona: str = "",  # [v2.3] PERSONA_* 또는 "" (자동감지)
) -> Generator[str, None, None]:
    """
    쿼리 결과를 LLM 으로 해석하여 자연어 설명을 스트리밍 반환합니다.

    [v2.1 수정]
    · rows 타입 체크 후 _normalize_rows_to_dict() 로 dict 변환
    · _build_data_summary() 에서 tuple 접근 TypeError 사전 차단

    [LLM 개인정보 보호]
    · 이 함수에 전달되는 rows 는 반드시 PII 마스킹 완료된 데이터여야 함
    · data_dashboard._llm_safe_rows() 에서 잔존 PII 패턴 로그 확인
    · 이 함수는 rows 내용을 신뢰하고 그대로 사용

    Args:
        question:     사용자 질문
        rows:         마스킹 완료된 쿼리 결과
        column_names: 컬럼명 목록
        sql:          실행된 SQL
        chart_type:   시각화 유형 (CHART_* 상수)
        agg_label:    집계 설명 (예: "병동별 환자 수")

    Yields:
        AI 해석 텍스트 청크
    """
    if not rows:
        yield "조회된 데이터가 없습니다. 조건을 변경하거나 다른 질문을 시도해 보세요."
        return

    # [v2.1] rows 타입 정규화 (tuple → dict)
    dict_rows = _normalize_rows_to_dict(rows, column_names)

    data_summary = _build_data_summary(dict_rows, column_names)
    chart_desc = {
        CHART_LINE: "시계열 추세 (라인 차트)",
        CHART_BAR: "범주별 비교 (바 차트)",
        CHART_BAR_H: "랭킹 (수평 바 차트)",
        CHART_PIE: "비율/구성 (파이 차트)",
        CHART_HIST: "분포 (히스토그램)",
        CHART_GRID: "데이터 그리드 (리스트 목록)",
        CHART_KPI: "KPI 수치 카드",
        CHART_NONE: "표 형식",
    }.get(chart_type, "표 형식")

    agg_note = f"\n[집계 방식: {agg_label}]" if agg_label else ""

    # 출처 테이블명 추출 (SQL FROM 절)
    _from_match = re.search(r"\bFROM\s+([\w.]+)", sql, re.IGNORECASE)
    _source_table = _from_match.group(1).split(".")[-1][:30] if _from_match else "쿼리"

    # 페르소나 결정 (자동 감지 또는 사용자 지정)
    _persona_id = detect_persona(question, override=persona)
    _persona_def = _PERSONA_PROMPTS.get(_persona_id, _PERSONA_PROMPTS[PERSONA_DEFAULT])

    # PII 제거 컬럼 안내 (LLM 이 "왜 일부 컬럼이 없는지" 알 수 있게)
    _pii_note = ""
    if pii_removed_cols:
        _pii_note = (
            f"\n\n> **개인정보 보호**: {', '.join(pii_removed_cols)} 컬럼은 "
            f"개인정보보호법에 따라 AI 분석 대상에서 제외되었습니다. "
            f"해당 컬럼은 화면에는 마스킹 처리되어 표시됩니다."
        )

    prompt = f"""{_persona_def}

## 분석 요청
{question}

## 실행된 SQL
```sql
{sql}
```

## 데이터 결과{agg_note}{_pii_note}
{data_summary}

## 시각화 방식
{chart_desc}

## 해석 지침 (반드시 준수)
1. **핵심 요약** — 첫 1~2문장: 가장 중요한 인사이트를 먼저
2. **주요 수치** — 정확한 숫자와 비율 포함 (예: "9명 중 5명(55.6%)")
3. **패턴/트렌드** — 집중, 증감, 이상값 등
4. **{_persona_id.replace("doctor", "임상").replace("nurse", "케어").replace("admin", "운영").replace("default", "실무")} 시사점** — 실제 업무에 도움이 되는 제언
5. 이모지 활용으로 가독성 향상 (✅ ⚠️ 📊 등)
6. 출처: [{_source_table} 테이블 쿼리 결과]
7. 전체 5~8문장 이내, 간결하고 명확하게

**금지**: 환자 개인정보(이름, 주민번호 등) 절대 포함 금지
**금지**: SQL 기술적 설명 (FROM, WHERE 등) 나열 금지"""

    try:
        from core.llm import get_llm_client

        llm = get_llm_client()
        for chunk in llm.generate_stream(
            query="위 데이터를 분석하여 인사이트를 제공해주세요.",
            context=prompt,
        ):
            yield chunk if isinstance(chunk, str) else str(chunk)
    except Exception as exc:
        logger.error(f"데이터 해석 LLM 오류: {exc}", exc_info=True)
        yield f"데이터 해석 중 오류: {exc}"


# ──────────────────────────────────────────────────────────────────────
#  통합 분석 파이프라인
# ──────────────────────────────────────────────────────────────────────


def analyze_query_result(
    question: str,
    rows: List[Any],  # list[dict] 또는 list[tuple] 모두 허용
    sql: str,
) -> DataAnalysisResult:
    """
    [v2.1] 쿼리 결과 분석 → DataAnalysisResult 반환.

    [v2.1 수정]
    · rows 가 list[tuple] 이면 column_names 없이는 처리 불가
      → rows[0].keys() 전에 isinstance 체크
      → tuple 형태이고 column_names 추출 불가 시 빈 결과 반환 (안전 폴백)
    """
    if not rows:
        return DataAnalysisResult(sql_used=sql)

    # [v2.1] 타입 체크
    if isinstance(rows[0], dict):
        column_names = list(rows[0].keys())
        dict_rows = rows
    elif isinstance(rows[0], (tuple, list)):
        # tuple rows: column_names 알 수 없음 → 차트 감지 불가
        logger.warning(
            "analyze_query_result: tuple rows 수신 — "
            "column_names 없어서 차트 감지 생략. "
            "data_dashboard._execute_oracle_now 에서 col_names 전달 확인 필요."
        )
        return DataAnalysisResult(rows=rows, row_count=len(rows), sql_used=sql)
    else:
        return DataAnalysisResult(sql_used=sql)

    cols = _classify_columns(dict_rows, column_names)

    chart_rows: List[Dict] = []
    chart_cols: List[str] = []
    agg_label: str = ""
    chart_type: str = CHART_NONE
    x_col: Optional[str] = None
    y_col: Optional[str] = None

    # ── [v2.3] 쿼리 의도 3-way 분류 ──────────────────────────────
    # KPI / LIST(GRID) / AGG(차트) 로 분기하여
    # 불필요한 차트 생성을 완전히 차단합니다.
    intent = classify_query_intent(question, dict_rows, column_names, cols, sql)

    if intent == _INTENT_KPI:
        # 단순 수치 결과 → KPI 카드
        chart_type = CHART_KPI
        chart_rows = dict_rows
        chart_cols = column_names
        agg_label = "KPI"

    elif intent == _INTENT_LIST:
        # 레코드 리스트 → 그리드 (차트 없음)
        # CHART_GRID: 차트 렌더링 생략, 데이터 테이블만 표시
        chart_type = CHART_GRID
        chart_rows = dict_rows
        chart_cols = column_names
        agg_label = f"총 {len(dict_rows)}건"

        # 리스트 데이터에서 의미있는 집계 차트를 보조로 생성
        # (카테고리 컬럼이 있으면 파이/바 차트를 smart_aggregate 로 추가)
        _agg_type, _agg_x, _agg_y, _agg_rows, _agg_cols, _agg_label = smart_aggregate(
            dict_rows, column_names, sql
        )
        if _agg_type not in (CHART_NONE, None) and _agg_rows:
            # 리스트 데이터의 집계 요약 차트는 별도 필드로 전달
            # DataAnalysisResult 에 agg_chart_* 필드 추가
            return DataAnalysisResult(
                rows=dict_rows,
                column_names=column_names,
                row_count=len(dict_rows),
                chart_type=CHART_GRID,  # 기본: 그리드
                x_col=None,
                y_col=None,
                chart_rows=_agg_rows,  # 집계 데이터 (차트용)
                chart_cols=_agg_cols,
                agg_label=_agg_label,
                agg_chart_type=_agg_type,  # 집계 차트 타입
                agg_chart_x=_agg_x,
                agg_chart_y=_agg_y,
                sql_used=sql,
            )

    else:  # _INTENT_AGG
        is_raw = _is_raw_row_data(dict_rows, column_names, cols)
        if is_raw:
            chart_type, x_col, y_col, chart_rows, chart_cols, agg_label = (
                smart_aggregate(dict_rows, column_names, sql)
            )
        else:
            chart_type, x_col, y_col = detect_chart_type(dict_rows, column_names, sql)
            chart_rows = dict_rows
            chart_cols = column_names

    return DataAnalysisResult(
        rows=dict_rows,
        column_names=column_names,
        row_count=len(dict_rows),
        chart_type=chart_type,
        x_col=x_col,
        y_col=y_col,
        chart_rows=chart_rows,
        chart_cols=chart_cols,
        agg_label=agg_label,
        sql_used=sql,
    )
