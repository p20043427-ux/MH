"""
db/oracle_access_config.py  ─  RAG 화이트리스트 + 테이블/컬럼 설명 관리 (v1.1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v1.1 변경사항 — TABLE_DESC + COLUMN_DESCS 지원]

■ RAG_ACCESS_CONFIG 테이블 DDL 확장

  ALTER TABLE JAIN_WM.RAG_ACCESS_CONFIG ADD (
      TABLE_DESC   VARCHAR2(1000) DEFAULT NULL,
      COLUMN_DESCS CLOB           DEFAULT NULL   -- JSON 형식
  );

■ 테이블 설명 (TABLE_DESC)
  예: '입원 중인 환자의 병실 배치 현황. 병동/병실/침상 단위로 관리.'

■ 컬럼 설명 (COLUMN_DESCS) — JSON 문자열
  예:
  {
    "OMT02BLD":    "병동코드 (01=내과, 02=외과, 08=정형외과)",
    "OMT02BEDNO":  "병실번호",
    "OMT02NAME":   "환자명 (PII - 마스킹)",
    "OMT02USEFLAG":"사용여부 (Y=사용중, N=퇴원)"
  }

■ 활용
  · sql_generator._build_table_schema() 에서 get_schema_context() 호출
  · LLM 프롬프트에 정확한 컬럼 의미 전달 → SQL 생성 정확도 향상
  · schema_oracle_loader.py 가 벡터DB 구축에 사용

[v1.0 기능 유지]
  · 화이트리스트 관리 (IS_ACTIVE)
  · 마스킹 컬럼 목록 (MASK_COLUMNS)
  · TTL 5분 캐시
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# ──────────────────────────────────────────────────────────────────────
#  SELECT SQL (v1.1 — TABLE_DESC, COLUMN_DESCS 컬럼 추가)
# ──────────────────────────────────────────────────────────────────────

_SQL_LOAD_CONFIG = """
SELECT
    TABLE_NAME,
    SCHEMA_NAME,
    IS_ACTIVE,
    MASK_COLUMNS,
    ALIAS,
    DESCRIPTION,
    TABLE_DESC,
    COLUMN_DESCS
FROM JAIN_WM.RAG_ACCESS_CONFIG
WHERE IS_ACTIVE = 1
ORDER BY TABLE_NAME
"""

# v1.0 폴백 SQL (TABLE_DESC, COLUMN_DESCS 컬럼이 없는 경우)
_SQL_LOAD_CONFIG_V10 = """
SELECT
    TABLE_NAME,
    SCHEMA_NAME,
    IS_ACTIVE,
    MASK_COLUMNS,
    ALIAS,
    DESCRIPTION
FROM JAIN_WM.RAG_ACCESS_CONFIG
WHERE IS_ACTIVE = 1
ORDER BY TABLE_NAME
"""

# ──────────────────────────────────────────────────────────────────────
#  DDL 가이드 (주석으로 제공)
# ──────────────────────────────────────────────────────────────────────
"""
[신규 컬럼 추가 DDL — DBeaver 에서 JAIN_WM 계정으로 실행]

ALTER TABLE JAIN_WM.RAG_ACCESS_CONFIG ADD (
    TABLE_DESC   VARCHAR2(1000) DEFAULT NULL,
    COLUMN_DESCS CLOB           DEFAULT NULL
);

[데이터 등록 예시]

UPDATE JAIN_WM.RAG_ACCESS_CONFIG
SET
    TABLE_DESC   = '입원 환자 병실 배치 현황. 병동/병실/침상 단위 관리. 퇴원 시 USEFLAG=N',
    COLUMN_DESCS = '{
        "OMT02BLD":    "병동코드 (01=내과, 02=외과, 03=산부인과, 08=정형외과, 09=신경외과)",
        "OMT02BEDNO":  "병실번호 (예: 0801=8병동 1호실)",
        "OMT02BEDSEQ": "침상번호 (1~6)",
        "OMT02NAME":   "환자명 (PII - 마스킹 대상)",
        "OMT02IDNOA":  "주민등록번호 (PII - 마스킹 대상)",
        "OMT02USEFLAG":"사용여부 (Y=사용중/입원중, N=퇴원/공실)",
        "OMT02DEPT":   "진료과코드",
        "OMT02INDATE": "입원일자 (YYYYMMDD)",
        "OMT02PTNO":   "환자번호 (병원 내부 ID)"
    }',
    MASK_COLUMNS = 'OMT02NAME,OMT02IDNOA,OMT02AIDNOA,환자명,주민번호,환자번호'
WHERE TABLE_NAME = 'OMTIDN02';
COMMIT;
"""


# ──────────────────────────────────────────────────────────────────────
#  데이터 클래스
# ──────────────────────────────────────────────────────────────────────


@dataclass
class TableAccessConfig:
    """
    테이블별 접근 설정 + 스키마 정보.

    Attributes:
        table_name:    테이블명 (대문자)
        schema_name:   스키마명 (대문자, 기본 JAIN_WM)
        is_active:     활성화 여부
        mask_columns:  마스킹 컬럼 집합 (소문자 정규화)
        alias:         테이블 별칭 (예: "병실현황")
        description:   관리자 메모
        table_desc:    [v1.1] 테이블 한국어 설명 (SQL 생성용)
        column_descs:  [v1.1] 컬럼별 설명 dict (SQL 생성용)
    """

    table_name: str
    schema_name: str = "JAIN_WM"
    is_active: bool = True
    mask_columns: Set[str] = field(default_factory=set)
    alias: str = ""
    description: str = ""
    table_desc: str = ""  # v1.1
    column_descs: Dict[str, str] = field(default_factory=dict)  # v1.1

    @property
    def full_name(self) -> str:
        """JAIN_WM.OMTIDN02 형태"""
        return f"{self.schema_name}.{self.table_name}"

    def schema_context_for_llm(self) -> str:
        """
        SQL 생성 LLM 에 전달할 스키마 컨텍스트 텍스트를 생성합니다.

        [v1.3 수정사항]
        · 스키마명 명시: ### JAIN_OCS.EMIHPTMI (응급환자) 형식
          → LLM 이 JAIN_WM 으로 추측하는 문제 해결
        · FROM 절 힌트 추가: "SQL 에서 반드시 FROM JAIN_OCS.EMIHPTMI 사용"
          → 스키마 오류 (ORA-00942) 근본 차단
        · 컬럼 타입 정보 포함: COLUMN_DESCS 에 (타입) 표시
          → TRUNC(NUMBER) 같은 타입 오류 (ORA-00932) 방지

        형식:
          ### JAIN_OCS.EMIHPTMI (응급환자 진료내역)
          FROM 절: FROM JAIN_OCS.EMIHPTMI
          설명: 응급실 내원 환자의 진료 내역...

          | 컬럼명       | 설명                          |
          |--------------|-------------------------------|
          | PTMIINDT     | 내원일자 (NUMBER YYYYMMDD 형식)|
        """
        full_name = self.full_name  # JAIN_OCS.EMIHPTMI
        title = self.alias or self.table_name
        lines = [
            f"### {full_name} ({title})",
            f"**FROM 절**: FROM {full_name}",  # ← LLM 에게 스키마 포함 FROM 절 명시
        ]

        if self.table_desc:
            lines.append(f"**설명**: {self.table_desc}")

        # 마스킹 컬럼 대문자 집합 (SQL 생성 시 SELECT 금지 안내용)
        pii_upper = (
            {c.upper() for c in self.mask_columns} if self.mask_columns else set()
        )

        if self.column_descs:
            lines.append(
                "\n> **SELECT 규칙**: 각 컬럼 선택 시 설명 텍스트를 AS 한국어별칭으로 반드시 부여"
                " (예: `PTMIAKDT AS 내원일자`)"
            )
            lines.append("\n| 컬럼명 | 설명 (→ AS 별칭으로 사용) | 비고 |")
            lines.append("|--------|--------------------------|------|")
            for col, desc in self.column_descs.items():
                # 설명에서 핵심 단어 추출하여 alias 힌트 제공
                _alias_hint = (
                    desc.split("(")[0].strip() if "(" in desc else desc.strip()
                )
                _alias_hint = (
                    _alias_hint.split(" ")[0] if " " in _alias_hint else _alias_hint
                )
                if col.upper() in pii_upper or col.lower() in self.mask_columns:
                    # [v1.5] SELECT 금지 → 마스킹 처리 안내로 변경
                    # 화면에는 마스킹(***) 처리되어 표시됨 — LLM 전달 시 자동 제거
                    lines.append(
                        f"| {col} | {desc} | 🔒 화면 마스킹 표시 (AS {_alias_hint}) |"
                    )
                else:
                    lines.append(f"| {col} | {desc} | AS {_alias_hint} |")
        elif self.mask_columns:
            lines.append(f"\n마스킹 컬럼: {', '.join(sorted(self.mask_columns))}")

        # [v1.5] PII 컬럼 안내 — 마스킹 처리로 변경
        # 화면에는 마스킹 표시, LLM AI 분석 시 자동 제거됨
        if pii_upper:
            lines.append(
                f"\n🔒 **마스킹 처리 컬럼** (SELECT 가능, 화면 표시 시 자동 마스킹): "
                f"{', '.join(sorted(pii_upper))}"
            )

        return "\n".join(lines)

    def get_pii_column_names(self) -> List[str]:
        """
        [v1.4 신규] 이 테이블의 PII 컬럼명 목록을 대문자로 반환합니다.

        SQL 생성 레이어에서 SELECT 절 PII 컬럼 자동 제거에 사용.
        """
        return sorted(
            {c.upper() for c in self.mask_columns} if self.mask_columns else set()
        )


# ──────────────────────────────────────────────────────────────────────
#  AccessConfigManager
# ──────────────────────────────────────────────────────────────────────


class AccessConfigManager:
    """
    RAG_ACCESS_CONFIG 테이블에서 화이트리스트 + 스키마 정보를 읽어
    캐싱합니다.

    · TTL 5분 캐시 (DB 변경 즉시 반영 위해 짧게 설정)
    · DB 장애 시 .env 폴백 자동 지원
    · v1.1: TABLE_DESC, COLUMN_DESCS 컬럼 자동 감지
      (신규 컬럼 없는 경우 v1.0 SQL 폴백)
    """

    _CACHE_TTL = 300  # 5분

    def __init__(self) -> None:
        self._configs: Dict[str, TableAccessConfig] = {}
        self._loaded_at: float = 0.0
        self._use_v11_columns: Optional[bool] = None  # None = 미확인

    def _is_stale(self) -> bool:
        return (time.time() - self._loaded_at) > self._CACHE_TTL

    def _load_from_db(self) -> None:
        """
        Oracle DB 에서 설정을 로드합니다.

        v1.1 컬럼(TABLE_DESC, COLUMN_DESCS) 존재 여부를 자동 감지하여
        없으면 v1.0 SQL 폴백.
        """
        try:
            from db.oracle_client import execute_query

            # v1.1 컬럼 존재 여부 자동 감지
            if self._use_v11_columns is None:
                self._use_v11_columns = self._check_v11_columns(execute_query)

            sql = _SQL_LOAD_CONFIG if self._use_v11_columns else _SQL_LOAD_CONFIG_V10
            rows = execute_query(sql=sql, max_rows=200)

            if not rows:
                logger.warning("RAG_ACCESS_CONFIG 에 활성화된 테이블이 없습니다.")
                self._loaded_at = time.time()
                return

            new_configs: Dict[str, TableAccessConfig] = {}
            for row in rows:
                if isinstance(row, dict):
                    cfg = self._row_to_config(row)
                elif isinstance(row, (tuple, list)):
                    # 컬럼 순서 기반 매핑
                    keys = (
                        [
                            "TABLE_NAME",
                            "SCHEMA_NAME",
                            "IS_ACTIVE",
                            "MASK_COLUMNS",
                            "ALIAS",
                            "DESCRIPTION",
                            "TABLE_DESC",
                            "COLUMN_DESCS",
                        ]
                        if self._use_v11_columns
                        else [
                            "TABLE_NAME",
                            "SCHEMA_NAME",
                            "IS_ACTIVE",
                            "MASK_COLUMNS",
                            "ALIAS",
                            "DESCRIPTION",
                        ]
                    )
                    cfg = self._row_to_config(dict(zip(keys, row)))
                else:
                    continue
                new_configs[cfg.table_name] = cfg

            self._configs = new_configs
            self._loaded_at = time.time()
            logger.info(
                f"RAG_ACCESS_CONFIG 로드: {len(new_configs)}개 테이블 "
                f"(v1.1={'예' if self._use_v11_columns else '아니오 - v1.0 폴백'})"
            )

        except Exception as exc:
            logger.error(f"RAG_ACCESS_CONFIG DB 로드 실패: {exc}", exc_info=True)
            self._load_from_env_fallback()

    def _check_v11_columns(self, execute_query) -> bool:
        """TABLE_DESC, COLUMN_DESCS 컬럼 존재 여부 확인."""
        try:
            execute_query(
                sql="SELECT TABLE_DESC FROM JAIN_WM.RAG_ACCESS_CONFIG WHERE ROWNUM <= 1",
                max_rows=1,
            )
            return True
        except Exception:
            logger.info("RAG_ACCESS_CONFIG: TABLE_DESC 컬럼 없음 → v1.0 폴백")
            return False

    def _row_to_config(self, row: dict) -> TableAccessConfig:
        """DB 행 → TableAccessConfig 변환."""
        tbl = str(row.get("TABLE_NAME", "")).upper().strip()
        sch = str(row.get("SCHEMA_NAME", "JAIN_WM") or "JAIN_WM").upper().strip()
        active = bool(row.get("IS_ACTIVE", 1))

        # MASK_COLUMNS: "OMT02NAME,환자명,주민번호" → {"omt02name", "환자명", "주민번호"}
        mask_raw = str(row.get("MASK_COLUMNS") or "")
        mask_cols: Set[str] = {
            c.strip().lower() for c in mask_raw.split(",") if c.strip()
        }

        # [v1.1] TABLE_DESC
        table_desc = str(row.get("TABLE_DESC") or "").strip()

        # [v1.1] COLUMN_DESCS: JSON 또는 탭 구분 텍스트 → dict
        #
        # [v1.3 수정] 탭 구분 형식 자동 감지 및 파싱 추가
        # DBeaver 에서 컬럼 설명을 엑셀처럼 붙여넣으면 탭 구분 형식으로 저장됨:
        #   컬럼명\t설명
        #   PTMIINDT\t내원일자 (NUMBER YYYYMMDD)
        # 이 형식을 JSON 파싱 실패 시 자동 폴백으로 처리.
        col_descs_raw = str(row.get("COLUMN_DESCS") or "").strip()
        col_descs: Dict[str, str] = {}
        if col_descs_raw:
            # 1차 시도: JSON 파싱
            try:
                col_descs = json.loads(col_descs_raw)
                logger.debug(
                    f"COLUMN_DESCS JSON 파싱 성공 (table={tbl}): {len(col_descs)}개"
                )
            except json.JSONDecodeError:
                # 2차 시도: 탭 구분 텍스트
                # [v1.4 수정] 4컬럼 형식 지원
                # 형식 A (2컬럼): 컬럼명\t설명
                # 형식 B (4컬럼): 컬럼명\t타입\t크기\t설명  ← DBeaver 붙여넣기
                # 형식 C (3컬럼): 컬럼명\t타입\t설명
                try:
                    parsed: Dict[str, str] = {}
                    for line in col_descs_raw.strip().splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        parts = [p.strip() for p in line.split("\t")]
                        if not parts or not parts[0]:
                            continue

                        col_key = parts[0].upper()
                        # 헤더 행 건너뜀
                        if col_key in (
                            "컬럼명",
                            "COLUMN_NAME",
                            "COL",
                            "컬럼",
                            "COLUMN",
                            "NAME",
                        ):
                            continue

                        if len(parts) >= 4:
                            # 형식 B: 컬럼명 / 타입 / 크기 / 설명
                            col_type = parts[1]
                            col_size = parts[2]
                            col_desc = parts[3]
                            # 설명 없으면 타입 정보 사용
                            if col_desc:
                                col_val = f"{col_desc} ({col_type}({col_size}))"
                            else:
                                col_val = f"{col_type}({col_size})"
                        elif len(parts) == 3:
                            # 형식 C: 컬럼명 / 타입 / 설명
                            col_type = parts[1]
                            col_desc = parts[2]
                            col_val = (
                                f"{col_desc} ({col_type})" if col_desc else col_type
                            )
                        elif len(parts) == 2:
                            # 형식 A: 컬럼명 / 설명
                            col_val = parts[1]
                        else:
                            continue

                        if col_key and col_val:
                            parsed[col_key] = col_val

                    if parsed:
                        col_descs = parsed
                        logger.info(
                            f"COLUMN_DESCS 탭 구분 파싱 성공 (table={tbl}): "
                            f"{len(parsed)}개 컬럼"
                        )
                    else:
                        logger.warning(
                            f"COLUMN_DESCS 파싱 실패 (JSON도 탭도 아님, table={tbl}): "
                            f"원본 앞 80자: {col_descs_raw[:80]}"
                        )
                except Exception as exc2:
                    logger.warning(f"COLUMN_DESCS 탭 파싱 오류 (table={tbl}): {exc2}")

        return TableAccessConfig(
            table_name=tbl,
            schema_name=sch,
            is_active=active,
            mask_columns=mask_cols,
            alias=str(row.get("ALIAS") or "").strip(),
            description=str(row.get("DESCRIPTION") or "").strip(),
            table_desc=table_desc,
            column_descs=col_descs,
        )

    def _load_from_env_fallback(self) -> None:
        """
        DB 연결 실패 시 .env 의 ORACLE_WHITELIST_TABLES 로 폴백.

        [v1.2 멀티 스키마 지원]
        ORACLE_WHITELIST_TABLES=OMTIDN02,JAIN_OCS.EXMRQST01 처럼
        스키마.테이블명 형식 지원. 스키마 미지정 시 ORACLE_SCHEMA 기본값 사용.

        v1.1 정보(TABLE_DESC, COLUMN_DESCS)는 폴백 시 제공되지 않음.
        """
        raw: List[str] = getattr(settings, "oracle_whitelist_tables", []) or []
        default_schema = str(getattr(settings, "oracle_schema", "JAIN_WM")).upper()
        fallback: Dict[str, TableAccessConfig] = {}
        for raw_entry in raw:
            raw_entry = raw_entry.strip().upper()
            if not raw_entry:
                continue
            # JAIN_OCS.EXMRQST01 형식 → 스키마.테이블 분리
            if "." in raw_entry:
                parts = raw_entry.split(".", 1)
                sch = parts[0].strip()
                tbl = parts[1].strip()
            else:
                sch = default_schema
                tbl = raw_entry
            if not tbl:
                continue
            fallback[tbl] = TableAccessConfig(
                table_name=tbl,
                schema_name=sch,
            )
        self._configs = fallback
        self._loaded_at = time.time()
        logger.warning(
            f"RAG_ACCESS_CONFIG 폴백: .env 화이트리스트 {len(fallback)}개 "
            f"(TABLE_DESC/COLUMN_DESCS 없음)"
        )

    def _ensure_loaded(self) -> None:
        if not self._configs or self._is_stale():
            self._load_from_db()

    def get_config(self, table_name: str) -> Optional[TableAccessConfig]:
        """
        특정 테이블의 설정을 반환합니다.

        Args:
            table_name: 대문자 테이블명 (예: OMTIDN02)

        Returns:
            TableAccessConfig 또는 None (미등록 테이블)
        """
        self._ensure_loaded()
        return self._configs.get(table_name.upper())

    def get_all_active(self) -> List[TableAccessConfig]:
        """활성화된 모든 테이블 설정 반환."""
        self._ensure_loaded()
        return [c for c in self._configs.values() if c.is_active]

    def get_whitelist(self) -> List[str]:
        """활성화된 테이블명 목록 반환."""
        return [c.table_name for c in self.get_all_active()]

    def get_schema_context_for_sql_gen(
        self,
        table_names: Optional[List[str]] = None,
    ) -> str:
        """
        [v1.1 신규] SQL 생성 LLM 에 전달할 스키마 컨텍스트를 생성합니다.

        table_names 지정 시 해당 테이블만, None 이면 전체 활성 테이블.

        Returns:
            마크다운 형식의 테이블 스키마 설명 텍스트
        """
        self._ensure_loaded()
        targets = (
            [
                self._configs[t.upper()]
                for t in table_names
                if t.upper() in self._configs
            ]
            if table_names
            else self.get_all_active()
        )
        if not targets:
            return "(RAG_ACCESS_CONFIG 에 등록된 테이블 없음)"

        sections = [cfg.schema_context_for_llm() for cfg in targets]
        header = (
            f"## 허용 테이블 목록 ({len(sections)}개)\n"
            "아래 테이블만 SQL 에서 사용할 수 있습니다.\n\n"
        )
        _full = header + "\n\n---\n\n".join(sections)
        # [v1.5] 8000자 초과 시 잘라냄 → LLM 토큰 절감 → SQL 생성 빠름
        if len(_full) > 8000:
            _full = _full[:7900] + "\n...(스키마 일부 생략)"
        return _full

    def get_all_pii_columns(self) -> Dict[str, List[str]]:
        """
        [v1.4 신규] 전체 테이블의 PII 컬럼 목록을 반환합니다.

        SQL 생성 레이어에서 SELECT 절 PII 컬럼 자동 제거에 사용.

        Returns:
            {테이블명: [PII컬럼명, ...]} 형태의 dict
        """
        self._ensure_loaded()
        result: Dict[str, List[str]] = {}
        for tbl, cfg in self._configs.items():
            pii_cols = cfg.get_pii_column_names()
            if pii_cols:
                result[tbl] = pii_cols
        return result

    def invalidate_cache(self) -> None:
        """캐시를 즉시 만료시킵니다 (TTL 우회)."""
        self._loaded_at = 0.0
        logger.info("RAG_ACCESS_CONFIG 캐시 초기화")


# ──────────────────────────────────────────────────────────────────────
#  싱글톤 접근자
# ──────────────────────────────────────────────────────────────────────

_manager_instance: Optional[AccessConfigManager] = None


def get_access_config_manager() -> AccessConfigManager:
    """AccessConfigManager 싱글톤을 반환합니다."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = AccessConfigManager()
    return _manager_instance
