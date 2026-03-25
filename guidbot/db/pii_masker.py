"""
db/pii_masker.py ─ 개인정보(PII) 마스킹 엔진 (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[마스킹이 필요한 이유]

  병원 DB 에는 다음과 같은 개인정보가 포함되어 있습니다:
    - 주민등록번호 (PT_NO, JUMIN_NO 등)
    - 이름          (PT_NM, PAT_NAME 등)
    - 전화번호      (TEL_NO, HP_NO 등)
    - 주소          (ADDR, ADDRESS 등)
    - 이메일        (EMAIL 등)

  마스킹 없이 이 데이터를 LLM 에 전달하면:
    1. 개인정보가 Google 서버로 전송됨 (개인정보보호법 위반 위험)
    2. AI 답변에 개인정보가 그대로 포함될 수 있음
    3. 로그 파일에 개인정보가 기록될 수 있음

[이중 마스킹 전략]

  Layer 1 — 화면 표시 마스킹:
    데이터 테이블을 화면에 렌더링할 때 개인정보 컬럼 값을 마스킹.
    직원이 화면에서 볼 때도 최소한의 정보만 표시.

  Layer 2 — LLM 전달 전 마스킹:
    AI 분석을 위해 LLM 에 데이터를 전달하기 전,
    개인정보 컬럼을 완전히 제거하거나 마스킹한 요약 통계만 전달.

[마스킹 방법별 적용 기준]

  주민등록번호 (JUMIN_NO, RRN_NO 등):
    값:  900101-1234567
    표시: 900101-*******  (뒷자리 완전 마스킹)

  이름 (PT_NM, PAT_NAME 등):
    값:  홍길동
    표시: 홍**  (성만 표시)

  전화번호 (TEL_NO, HP_NO 등):
    값:  010-1234-5678
    표시: 010-****-5678

  주소 (ADDR, ADDRESS 등):
    값:  서울시 강남구 테헤란로 123
    표시: 서울시 강남구 ***

  이메일:
    값:  hong@hospital.kr
    표시: h***@hospital.kr

  기타 숫자 ID (PT_NO 등):
    값:  PT20240001
    표시: PT****  (앞 2자만 표시)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from utils.logger import get_logger
from config.settings import settings

logger = get_logger(__name__, log_dir=settings.log_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PII 컬럼명 패턴 정의
#  컬럼명 키워드 기반으로 자동 감지 (대소문자 무관)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 주민등록번호 계열 컬럼명 키워드
_RRN_COLUMN_KEYWORDS: frozenset = frozenset(
    [
        "jumin",
        "rrn",
        "rrno",
        "주민",
        "주민번호",
        "주민등록",
        "ssn",
        "birth_no",
    ]
)

# 이름 계열 컬럼명 키워드
_NAME_COLUMN_KEYWORDS: frozenset = frozenset(
    [
        # 영문 일반
        "nm",
        "_name",
        "patient_name",
        "person_name",
        "emp_nm",
        # 한국어
        "성명",
        "이름",
        "환자명",
        "환자이름",
        "성함",
        # 병원 OCS 공통 패턴
        "pt_nm",
        "pat_nm",
        "ptname",
        "ptnm",
        # EMIHPTMI 응급환자 테이블 컬럼명 (PTMI 접두사)
        "ptmiptnm",  # 환자명
        "ptminame",  # 환자명 (변형)
        # OMTIDN02 입원환자
        "omt02name",
        "omt02aname",
    ]
)

# 전화번호 계열 컬럼명 키워드
_PHONE_COLUMN_KEYWORDS: frozenset = frozenset(
    [
        "tel",
        "phone",
        "hp",
        "mobile",
        "전화",
        "핸드폰",
        "휴대폰",
        "contact",
    ]
)

# 주소 계열 컬럼명 키워드
_ADDR_COLUMN_KEYWORDS: frozenset = frozenset(
    [
        "addr",
        "address",
        "주소",
        "거주지",
        "address1",
        "home_addr",
    ]
)

# 이메일 계열 컬럼명 키워드
_EMAIL_COLUMN_KEYWORDS: frozenset = frozenset(
    [
        "email",
        "mail",
        "e_mail",
        "이메일",
    ]
)

# 환자번호/직원번호/주민번호 계열 컬럼명 키워드
_ID_COLUMN_KEYWORDS: frozenset = frozenset(
    [
        # 환자번호
        "pt_no",
        "pat_no",
        "patient_no",
        "patient_id",
        "ptno",
        "ptid",
        # 직원/차트
        "emp_no",
        "staff_no",
        "chart_no",
        # EMIHPTMI 응급환자: 주민번호/환자번호
        "ptmiptno",  # 환자번호
        "ptmippsid",  # 주민번호
        "ptmipsid",  # 주민번호 (단축)
        "ptmirrn",  # 주민번호 (변형)
        # OMTIDN02 입원환자
        "omt02ptno",
        "omt02idnoa",
    ]
)

# 주민등록번호 계열 컬럼명 키워드 (완전 마스킹 대상)
_RRN_COLUMN_KEYWORDS: frozenset = frozenset(
    [
        "rrn",
        "jumin",
        "주민",
        "idno",
        "id_no",
        "ssn",
        "ptmirrn",
        "ptmipsid",
        "ptmippsid",
        "omt02idnoa",
        "reg_no",
        "regno",
    ]
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  값 마스킹 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _mask_rrn(value: str) -> str:
    """
    주민등록번호 마스킹.

    앞 6자리(생년월일)는 표시, 뒤 7자리는 완전 마스킹.
    형식: YYMMDD-NNNNNNN → YYMMDD-*******
    """
    v = str(value).strip()
    # 주민번호 패턴 (하이픈 유무 모두 처리)
    pattern = re.compile(r"(\d{6})-?(\d{7})")
    if pattern.search(v):
        return pattern.sub(r"\1-*******", v)
    # 패턴 불일치 시 전체 마스킹
    return "*" * len(v)


def _mask_name(value: str) -> str:
    """
    이름 마스킹.

    한글 2자: 홍* (성만 표시)
    한글 3자: 홍** (성만 표시)
    영문:     H*** (첫 글자만 표시)
    """
    v = str(value).strip()
    if not v:
        return v

    # 한글 이름
    if re.match(r"^[가-힣]+$", v):
        if len(v) <= 1:
            return "*"
        return v[0] + "*" * (len(v) - 1)

    # 영문 이름
    if re.match(r"^[a-zA-Z ]+$", v):
        parts = v.split()
        masked = [p[0] + "***" if p else "" for p in parts]
        return " ".join(masked)

    # 혼합/기타
    return v[0] + "*" * (len(v) - 1)


def _mask_phone(value: str) -> str:
    """
    전화번호 마스킹.

    010-1234-5678 → 010-****-5678
    0212345678   → 02****5678
    """
    v = str(value).strip()
    # 하이픈 포함 형식
    pattern_dash = re.compile(r"(\d{2,3})-(\d{3,4})-(\d{4})")
    if pattern_dash.search(v):
        return pattern_dash.sub(r"\1-****-\3", v)
    # 하이픈 없는 11자리
    pattern_raw = re.compile(r"(\d{3})(\d{4})(\d{4})")
    if pattern_raw.search(v):
        return pattern_raw.sub(r"\1****\3", v)
    return "*" * len(v)


def _mask_addr(value: str) -> str:
    """
    주소 마스킹.

    시/도 + 구/군 까지만 표시하고 이후는 마스킹.
    서울시 강남구 테헤란로 123 → 서울시 강남구 ***
    """
    v = str(value).strip()
    if not v:
        return v
    # 공백으로 분리, 앞 2 토큰만 표시
    parts = v.split()
    if len(parts) <= 2:
        return v[0] + "***"
    return " ".join(parts[:2]) + " ***"


def _mask_email(value: str) -> str:
    """
    이메일 마스킹.

    hong@hospital.kr → h***@hospital.kr
    """
    v = str(value).strip()
    at_idx = v.find("@")
    if at_idx > 0:
        local = v[:at_idx]
        domain = v[at_idx:]
        return local[0] + "***" + domain
    return "***"


def _mask_id(value: str) -> str:
    """
    환자번호/직원번호 마스킹.

    PT20240001 → PT****
    앞 2자만 표시하고 나머지 마스킹.
    """
    v = str(value).strip()
    if len(v) <= 2:
        return "*" * len(v)
    # 앞 2자 + 마스킹 (뒤 4자리는 표시하여 확인 가능하게)
    if len(v) >= 6:
        return v[:2] + "****" + v[-2:]
    return v[:2] + "*" * (len(v) - 2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  컬럼명 → PII 유형 감지
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# PII 유형 상수
PII_RRN = "rrn"  # 주민등록번호
PII_NAME = "name"  # 이름
PII_PHONE = "phone"  # 전화번호
PII_ADDR = "addr"  # 주소
PII_EMAIL = "email"  # 이메일
PII_ID = "id"  # 환자번호/직원번호


def detect_pii_type(column_name: str) -> Optional[str]:
    """
    컬럼명에서 PII 유형을 자동 감지합니다.

    컬럼명 키워드 기반으로 PII 여부와 종류를 판단합니다.
    RAG_ACCESS_CONFIG 에 명시적으로 등록된 컬럼은 이 함수 없이도 마스킹되지만,
    이 함수로 등록되지 않은 컬럼도 자동 감지하여 이중 보호합니다.

    Args:
        column_name: Oracle 컬럼명 (대소문자 무관)

    Returns:
        PII 유형 상수 (PII_RRN, PII_NAME 등) | None (PII 아님)

    Example::
        detect_pii_type("PT_NM")    # → "name"
        detect_pii_type("HP_NO")    # → "phone"
        detect_pii_type("WARD_CD")  # → None (비 PII)
    """
    col = column_name.lower()

    # 정확한 컬럼명 매칭 우선 (오탐 방지)
    exact_rrn = {"jumin_no", "rrn_no", "jumin", "ssn"}
    exact_name = {"pt_nm", "pat_nm", "emp_nm", "user_nm"}
    exact_phone = {"tel_no", "hp_no", "phone_no", "mobile_no"}
    exact_id = {"pt_no", "pat_no", "emp_no", "chart_no", "patient_id"}

    if col in exact_rrn:
        return PII_RRN
    if col in exact_name:
        return PII_NAME
    if col in exact_phone:
        return PII_PHONE

    # 주민번호 키워드 체크 (이름보다 먼저 — 더 민감)
    for kw in _RRN_COLUMN_KEYWORDS:
        if kw in col:
            return PII_RRN
    if col in exact_id:
        return PII_ID

    # 키워드 포함 여부 검사
    for kw in _RRN_COLUMN_KEYWORDS:
        if kw in col:
            return PII_RRN

    for kw in _NAME_COLUMN_KEYWORDS:
        if kw in col:
            return PII_NAME

    for kw in _PHONE_COLUMN_KEYWORDS:
        if kw in col:
            return PII_PHONE

    for kw in _ADDR_COLUMN_KEYWORDS:
        if kw in col:
            return PII_ADDR

    for kw in _EMAIL_COLUMN_KEYWORDS:
        if kw in col:
            return PII_EMAIL

    return None


def mask_value(value: Any, pii_type: str) -> str:
    """
    PII 유형에 맞는 마스킹 적용.

    Args:
        value:    마스킹할 원본 값
        pii_type: detect_pii_type() 반환값

    Returns:
        마스킹된 문자열
    """
    if value is None or str(value).strip() in ("", "None", "NULL"):
        return ""

    v = str(value)
    try:
        if pii_type == PII_RRN:
            return _mask_rrn(v)
        if pii_type == PII_NAME:
            return _mask_name(v)
        if pii_type == PII_PHONE:
            return _mask_phone(v)
        if pii_type == PII_ADDR:
            return _mask_addr(v)
        if pii_type == PII_EMAIL:
            return _mask_email(v)
        if pii_type == PII_ID:
            return _mask_id(v)
    except Exception as exc:
        logger.warning(f"마스킹 실패 ({pii_type}): {exc}")
        return "***"

    return v


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RAG_ACCESS_CONFIG 연동 — 동적 PII 컬럼 보강

_dynamic_pii_cache: Dict[str, Set[str]] = {}  # {table_name: {col_upper, ...}}
_dynamic_pii_loaded_at: float = 0.0
_DYNAMIC_PII_TTL: float = 300.0  # 5분


def get_dynamic_pii_columns(table_name: str) -> Set[str]:
    """
    [v1.1 신규] RAG_ACCESS_CONFIG MASK_COLUMNS 를 기반으로
    동적 PII 컬럼 집합을 반환합니다. 5분 TTL 캐시.

    기본 키워드 방식의 한계:
      - PTMINAME, PTMIPSID 같은 테이블 특화 컬럼명은 패턴 매칭 불가
      - RAG_ACCESS_CONFIG 에 직접 등록된 MASK_COLUMNS 가 더 정확함

    Args:
        table_name: 테이블명 (대소문자 무관)

    Returns:
        대문자 정규화된 PII 컬럼명 집합
    """
    import time as _time

    global _dynamic_pii_loaded_at, _dynamic_pii_cache

    if (_time.time() - _dynamic_pii_loaded_at) > _DYNAMIC_PII_TTL:
        try:
            from db.oracle_access_config import get_access_config_manager

            all_pii = get_access_config_manager().get_all_pii_columns()
            _dynamic_pii_cache = {
                k.upper(): {c.upper() for c in v} for k, v in all_pii.items()
            }
            _dynamic_pii_loaded_at = _time.time()
        except Exception:
            pass  # 연결 실패 시 기존 캐시 유지

    return _dynamic_pii_cache.get(table_name.upper(), set())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DataFrame 마스킹
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class MaskingResult:
    """
    마스킹 적용 결과.

    Attributes:
        rows:            마스킹 완료된 행 데이터
        columns:         컬럼명 목록
        masked_columns:  마스킹이 적용된 컬럼명 목록
        pii_map:         {컬럼명: PII유형} 매핑
        has_pii:         PII 컬럼 존재 여부
    """

    rows: List[tuple]
    columns: List[str]
    masked_columns: List[str] = field(default_factory=list)
    pii_map: Dict[str, str] = field(default_factory=dict)
    has_pii: bool = False


def mask_dataframe(
    rows: List[tuple],
    columns: List[str],
    extra_mask_cols: Optional[Set[str]] = None,
    auto_detect: bool = True,
) -> MaskingResult:
    """
    쿼리 결과 데이터에 PII 마스킹을 적용합니다.

    [적용 순서]
    1. RAG_ACCESS_CONFIG 에 등록된 컬럼 (extra_mask_cols)
    2. 컬럼명 자동 감지 (auto_detect=True 시)

    Args:
        rows:            Oracle 쿼리 결과 행 목록
        columns:         컬럼명 목록 (rows 와 순서 일치)
        extra_mask_cols: 추가로 강제 마스킹할 컬럼명 집합 (RAG_ACCESS_CONFIG)
        auto_detect:     컬럼명 자동 PII 감지 여부 (기본 True)

    Returns:
        MaskingResult (마스킹된 rows + 메타 정보)

    Example::

        result = mask_dataframe(rows, columns, extra_mask_cols={"PT_NO", "PT_NM"})
        if result.has_pii:
            st.caption(f"⚠️ {result.masked_columns} 컬럼 개인정보 마스킹됨")
        # result.rows 를 DataFrame 으로 변환하여 표시
    """
    if not rows or not columns:
        return MaskingResult(rows=rows, columns=columns)

    extra = {c.lower() for c in (extra_mask_cols or set())}

    # 컬럼별 PII 유형 결정
    pii_map: Dict[str, Optional[str]] = {}
    for col in columns:
        col_lower = col.lower()

        # 1순위: extra_mask_cols 에 명시된 컬럼
        if col_lower in extra:
            pii_type = detect_pii_type(col) or PII_ID
            pii_map[col] = pii_type
            continue

        # 2순위: 컬럼명 자동 감지
        if auto_detect:
            pii_type = detect_pii_type(col)
            pii_map[col] = pii_type  # None 이면 마스킹 안 함
        else:
            pii_map[col] = None

    # PII 컬럼 목록
    masked_cols = [col for col, ptype in pii_map.items() if ptype is not None]
    final_pii_map = {col: ptype for col, ptype in pii_map.items() if ptype}

    if masked_cols:
        logger.info(
            f"PII 마스킹 적용: {masked_cols} ({len(rows)}행 × {len(masked_cols)}컬럼)"
        )

    # 행 데이터 마스킹
    masked_rows: List[tuple] = []
    col_indices = {col: idx for idx, col in enumerate(columns)}

    for row in rows:
        row_list = list(row)
        for col, ptype in final_pii_map.items():
            idx = col_indices.get(col)
            if idx is not None:
                row_list[idx] = mask_value(row_list[idx], ptype)
        masked_rows.append(tuple(row_list))

    return MaskingResult(
        rows=masked_rows,
        columns=columns,
        masked_columns=masked_cols,
        pii_map=final_pii_map,
        has_pii=bool(masked_cols),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LLM 전달용 데이터 정제
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def sanitize_for_llm(
    rows: List[tuple],
    columns: List[str],
    extra_mask_cols: Optional[Set[str]] = None,
    max_rows: int = 50,
) -> Tuple[List[tuple], List[str], List[str]]:
    """
    LLM 에 전달하기 전 개인정보를 제거/마스킹합니다.

    [전략]
    - PII 컬럼은 완전히 제거 (마스킹보다 강력한 보호)
    - 행 수를 max_rows 로 제한 (토큰 절약)
    - 통계 목적이므로 개인 식별 불필요

    Args:
        rows:            원본 쿼리 결과
        columns:         컬럼명 목록
        extra_mask_cols: RAG_ACCESS_CONFIG 에서 가져온 마스킹 컬럼
        max_rows:        LLM 에 전달할 최대 행 수 (기본 50)

    Returns:
        (정제된 rows, 정제된 columns, 제거된 컬럼 목록)

    Example::

        clean_rows, clean_cols, removed = sanitize_for_llm(
            rows, columns, extra_mask_cols={"PT_NO", "PT_NM"}
        )
        context = f"데이터 ({len(clean_rows)}행):\\n"
        context += ", ".join(clean_cols) + "\\n"
        for row in clean_rows[:max_rows]:
            context += str(row) + "\\n"
    """
    extra = {c.lower() for c in (extra_mask_cols or set())}

    # 제거할 컬럼 인덱스 결정
    remove_indices: Set[int] = set()
    removed_cols: List[str] = []

    for idx, col in enumerate(columns):
        col_lower = col.lower()
        is_explicit = col_lower in extra
        is_auto_pii = detect_pii_type(col) is not None
        if is_explicit or is_auto_pii:
            remove_indices.add(idx)
            removed_cols.append(col)

    if removed_cols:
        logger.info(f"LLM 전달 전 PII 컬럼 제거: {removed_cols}")

    # 컬럼 필터링
    clean_columns = [
        col for idx, col in enumerate(columns) if idx not in remove_indices
    ]

    # 행 데이터 필터링 + 행 수 제한
    clean_rows = [
        tuple(val for idx, val in enumerate(row) if idx not in remove_indices)
        for row in rows[:max_rows]
    ]

    return clean_rows, clean_columns, removed_cols


def build_llm_safe_context(
    rows: List[tuple],
    columns: List[str],
    table_name: str = "",
    extra_mask_cols: Optional[Set[str]] = None,
    max_rows: int = 50,
) -> str:
    """
    LLM 에 전달할 안전한 데이터 컨텍스트 문자열 생성.

    개인정보 컬럼을 제거하고 통계 요약 + 샘플 데이터를 포함합니다.

    Args:
        rows:            쿼리 결과 행
        columns:         컬럼명 목록
        table_name:      테이블명 (컨텍스트 제목용)
        extra_mask_cols: 추가 마스킹 컬럼
        max_rows:        샘플 행 수 (기본 50)

    Returns:
        LLM 프롬프트에 삽입할 안전한 데이터 컨텍스트 문자열
    """
    clean_rows, clean_cols, removed = sanitize_for_llm(
        rows, columns, extra_mask_cols, max_rows
    )

    lines = []
    if table_name:
        lines.append(f"[데이터: {table_name}]")
    lines.append(f"전체 행 수: {len(rows)}행 (샘플 {len(clean_rows)}행 표시)")

    if removed:
        lines.append(f"※ 개인정보 보호로 제외된 컬럼: {', '.join(removed)}")

    lines.append(f"컬럼: {', '.join(clean_cols)}")
    lines.append("─" * 40)

    for row in clean_rows:
        lines.append("  " + " | ".join(str(v) if v is not None else "" for v in row))

    return "\n".join(lines)
