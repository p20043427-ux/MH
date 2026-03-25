"""
config/settings.py ─ 중앙 설정 관리 모듈 (v3.0)

[v3.0 변경사항]
- backup_dir cached_property 추가 (vector_store.py 에서 사용)
- monitoring_enabled 플래그 추가 (메트릭 수집 ON/OFF)
- 전 필드 설명 주석 전면 보강
- _BASE_DIR 단일 지점 수정으로 전체 경로 자동 반영 설계

[디렉토리 구조]
D:\\mh\\guidbot\\                   ← _BASE_DIR (프로젝트 루트)
├── .env                         ← API 키·비밀번호 (Git 제외!)
├── data_cache\\                  ← HuggingFace 임베딩 모델 캐시
├── data_rag_working\\            ← G드라이브 동기화 PDF 작업본
├── vector_store\\                ← FAISS 벡터 DB (index.faiss, index.pkl)
├── vector_store_backup\\         ← DB 자동 백업 (최근 5개 보관)
└── logs\\                        ← 모듈별 일별 로그 파일

[보안 규칙]
- google_api_key, admin_password: .env 파일 또는 환경변수로만 설정
- DB 접속 계정: SELECT 전용 rag_readonly 계정 사용 강력 권장
- SecretStr 필드: repr/로그 출력 시 자동으로 '**********' 마스킹

[설정 우선순위]
환경변수(OS) > .env 파일 > 필드 기본값
"""

from __future__ import annotations

import hmac
import os
from functools import cached_property
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ══════════════════════════════════════════════════════════════════════
#  ★ 프로젝트 기준 경로 ─ 이 한 줄만 수정하면 모든 하위 경로 자동 반영 ★
#    예: Path(r"E:\hospital\guidbot") 로 변경하면 전체 경로 변경됨
# ══════════════════════════════════════════════════════════════════════
_BASE_DIR = Path(r"D:\mh\guidbot")


class AppSettings(BaseSettings):
    """
    애플리케이션 전체 설정 클래스.

    pydantic-settings 의 BaseSettings 를 상속하여 환경변수·.env 파일을
    타입 안전하게 로드하고 검증합니다.
    앱 기동 시 단 한 번 인스턴스화되며, 이후 전역 settings 변수로 공유됩니다.

    [검증 흐름]
    1. .env 파일 / 환경변수에서 값 로드
    2. field_validator 로 개별 필드 범위·조건 검증
    3. model_validator 로 필드 간 관계 검증 + 디렉토리 자동 생성
    4. ValidationError 발생 시 앱 즉시 종료 (Fail-Fast 원칙)
    """

    model_config = SettingsConfigDict(
        # .env 파일 탐색 순서: 현재 작업 디렉토리 > _BASE_DIR
        # pydantic-settings 는 리스트로 전달 시 순서대로 탐색하여 모두 적용
        env_file=[".env", str(_BASE_DIR / ".env")],
        env_file_encoding="utf-8",
        case_sensitive=False,  # 환경변수 대소문자 구분 없음 (GOOGLE_API_KEY = google_api_key)
        extra="ignore",  # .env 에 미정의 변수가 있어도 오류 없이 무시
    )

    # ──────────────────────────────────────────────────────────────────
    #  Google Gemini AI API 설정
    # ──────────────────────────────────────────────────────────────────

    google_api_key: SecretStr = Field(
        ...,  # ... = 필수 필드 (없으면 ValidationError 로 기동 불가)
        description=(
            "Google Generative AI API 키 (기본 키, 필수). "
            ".env 파일의 GOOGLE_API_KEY=AIza... 형태로 설정하세요. "
            "소스코드 직접 입력 절대 금지!"
        ),
    )

    # ── API 키 풀 (할당량 초과 시 자동 교체, 최대 4개 추가) ──────────
    # 사용법 (.env):
    #   GOOGLE_API_KEY_2=AIza...
    #   GOOGLE_API_KEY_3=AIza...
    #   GOOGLE_API_KEY_4=AIza...
    #   GOOGLE_API_KEY_5=AIza...
    # 설정하지 않은 키는 None 으로 처리되어 자동으로 풀에서 제외됩니다.
    google_api_key_2: Optional[SecretStr] = Field(
        default=None,
        description="Google API 키 풀 2번 (할당량 초과 시 자동 전환)",
    )
    google_api_key_3: Optional[SecretStr] = Field(
        default=None,
        description="Google API 키 풀 3번",
    )
    google_api_key_4: Optional[SecretStr] = Field(
        default=None,
        description="Google API 키 풀 4번",
    )
    google_api_key_5: Optional[SecretStr] = Field(
        default=None,
        description="Google API 키 풀 5번",
    )

    chat_model: str = Field(
        default="models/gemini-2.5-flash",
        description=(
            "Gemini 대화 모델명. "
            "models/gemini-2.5-flash: 기본값(thinking OFF 시 3~5초), "
            "models/gemini-2.0-flash: 2~4초, "
            "models/gemini-2.0-flash-lite: 1~2초(빠름)"
        ),
    )

    # ── LLM 속도 최적화 파라미터 (v3.1 신규) ─────────────────────────
    # gemini-2.5-flash 는 기본적으로 '추론(thinking)' 모드로 동작합니다.
    # 병원 규정 Q&A 처럼 단순 검색·요약에는 불필요한 21초 지연만 발생.
    # thinking_budget=0 으로 비활성화하면 3~5초로 단축됩니다.

    llm_thinking_disabled: bool = Field(
        default=True,
        description=(
            "gemini-2.5-flash 의 thinking(추론) 단계 비활성화 여부. "
            "True(기본값): thinking_budget=0 → 21초에서 3~5초로 단축. "
            "False: 모델이 자율 추론 → 정확도 소폭 향상, 속도 대폭 저하."
        ),
    )

    llm_max_output_tokens: int = Field(
        default=1024,
        ge=256,
        le=8192,
        description=(
            "LLM 최대 출력 토큰 수. "
            "제한 없으면 LLM 이 장황하게 생성하여 속도 저하. "
            "규정 안내에는 1024토큰으로 충분."
        ),
    )

    llm_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description=("LLM 생성 온도. 낮을수록 일관된 규정 답변. 규정 Q&A 권장값: 0.1"),
    )

    # ──────────────────────────────────────────────────────────────────
    #  임베딩 모델 설정 (로컬 HuggingFace, 무료·무제한)
    # ──────────────────────────────────────────────────────────────────

    embedding_model: str = Field(
        default="jhgan/ko-sroberta-multitask",
        description=(
            "로컬 임베딩 모델명 (HuggingFace Hub ID). "
            "jhgan/ko-sroberta-multitask: 한국어 의미 검색 최적화, 768차원, 약 350MB. "
            "외부 API 없이 로컬에서 무제한 무료 실행 가능."
        ),
    )

    # ──────────────────────────────────────────────────────────────────
    #  RAG 파이프라인 파라미터 (검색 품질 직결)
    # ──────────────────────────────────────────────────────────────────

    chunk_size: int = Field(
        default=1000,
        ge=100,
        le=4000,
        description=(
            "청크 최대 문자 수. v3.0 에서 800 → 1000 으로 상향. "
            "조항 경계 인식 청킹으로 제N조 하나가 대부분 1개 청크에 담김. "
            "너무 크면 검색 정확도 저하, 너무 작으면 맥락 손실."
        ),
    )

    chunk_overlap: int = Field(
        default=200,
        ge=0,
        le=500,
        description=(
            "인접 청크 간 오버랩 문자 수. v3.0 에서 100 → 200 으로 상향. "
            "긴 조항이 여러 청크로 분리될 때 경계 맥락 손실을 최소화함."
        ),
    )

    retrieve_top_k: int = Field(
        default=20,
        ge=1,
        le=30,
        description=(
            "FAISS 1차 유사도 검색 후보 수. v5.1 에서 5 → 20 으로 상향.\n"
            "[왜 20인가]\n"
            "  취업규칙처럼 연속된 조항이 여러 페이지에 걸쳐 있을 때,\n"
            "  '육아휴직' 페이지가 Vector 유사도 6~15위권에 있어도 후보에 포함되어야 함.\n"
            "  top_k=5 이면 p.19(출산전후휴가)만 들어오고 육아휴직 페이지는 누락됨.\n"
            "  CPU 환경에서 Cross-Encoder 20개 처리 추가 시간: 약 +0.3~0.5초 (허용 범위)."
        ),
    )

    rerank_top_n: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Cross-Encoder 리랭킹 후 LLM 에 전달할 최종 문서 수.\n"
            "[왜 3인가]\n"
            "  출처 카드가 5개면 UI 가 과도하게 길어져 가독성 저하.\n"
            "  ContextBuilder 가 중복·노이즈 제거를 하므로 3개로도 핵심 내용 커버 가능.\n"
            "  단일 조항 여러 청크: 3개 안에 핵심 조항 포함되도록 Cross-Encoder 정확도에 의존.\n"
            "  .env 에서 RERANK_TOP_N=5 로 언제든 되돌릴 수 있습니다."
        ),
    )

    batch_size: int = Field(
        default=100,
        ge=10,
        le=1000,
        description=(
            "임베딩 배치 처리 크기. OOM 방지를 위해 이 크기씩 나눠 처리. "
            "GPU 사용 시 200~500 으로 늘려도 됨."
        ),
    )

    min_text_length: int = Field(
        default=30,
        ge=5,
        description=(
            "유효 페이지 최소 문자 수. v3.0 에서 20 → 30 으로 상향. "
            "이 길이 미만이면 노이즈 페이지로 판단하여 색인 제외."
        ),
    )

    # ──────────────────────────────────────────────────────────────────
    #  v4.0 신규: Query Rewriting 설정
    # ──────────────────────────────────────────────────────────────────

    query_rewriting_enabled: bool = Field(
        default=True,
        description=(
            "Query Rewriting 활성화 여부. "
            "True: 짧거나 모호한 질문을 LLM 이 자동 확장 → 검색 정확도 향상. "
            "False: 원본 쿼리 그대로 사용 (응답 속도 우선 시)."
        ),
    )

    query_multi_enabled: bool = Field(
        default=True,
        description=(
            "Multi-Query 확장 활성화. "
            "True: 질문 1개 → 3개 다양한 표현으로 확장하여 검색. "
            "False: 원본 쿼리만 사용."
        ),
    )

    query_hyde_enabled: bool = Field(
        default=False,
        description=(
            "HyDE(Hypothetical Document Embedding) 활성화. "
            "True: 가상 규정 단락을 생성하여 벡터 검색 정확도 향상 (짧은 쿼리에 효과적). "
            "False: 기본값 OFF (LLM 추가 호출로 0.5~1초 부가 지연 있음)."
        ),
    )

    # ──────────────────────────────────────────────────────────────────
    #  v4.0 신규: Hybrid Retrieval 설정
    # ──────────────────────────────────────────────────────────────────

    hybrid_retrieval_enabled: bool = Field(
        default=True,
        description=(
            "Hybrid Retrieval(Vector+BM25) 활성화 여부. "
            "True: Vector 검색 + BM25 키워드 검색을 RRF 로 융합 → 정확도 향상. "
            "False: Vector 검색만 사용 (rank-bm25 미설치 시 자동 비활성화됨)."
        ),
    )

    hybrid_vector_weight: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "Hybrid RRF 에서 Vector 검색 가중치 (0~1). "
            "기본값 0.7: 의미 검색 우선. "
            "키워드 검색 강화 시 0.5~0.6 으로 조정."
        ),
    )

    hybrid_bm25_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description=(
            "Hybrid RRF 에서 BM25 키워드 검색 가중치 (0~1). "
            "기본값 0.3. hybrid_vector_weight + hybrid_bm25_weight = 1.0 권장."
        ),
    )

    # ──────────────────────────────────────────────────────────────────
    #  v4.0 신규: Context Builder 설정 (토큰 최소화)
    # ──────────────────────────────────────────────────────────────────

    context_max_tokens: int = Field(
        default=2500,
        ge=500,
        le=8000,
        description=(
            "LLM 에 전달할 컨텍스트 최대 토큰 수 (1자 ≈ 1토큰 보수 추정). "
            "기본값 2,500: 규정 안내에 충분. "
            "더 상세한 답변 필요 시 3,500~5,000 으로 증가 (응답 시간 소폭 증가)."
        ),
    )

    context_max_chunk_chars: int = Field(
        default=800,
        ge=200,
        le=3000,
        description=(
            "개별 청크 최대 문자 수. 초과 시 문장 경계에서 트리밍. "
            "기본값 800: chunk_size=1000 보다 약간 작게 설정하여 노이즈 제거 효과."
        ),
    )

    context_score_threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description=(
            "컨텍스트 포함 최소 Cross-Encoder 점수 (sigmoid 변환 후). v5.1 에서 0.3 → 0.05 로 하향.\n"
            "[왜 0.05인가]\n"
            "  병원 규정집은 법령 문체 특성상 질문과 조항 사이 표면 유사도가 낮음.\n"
            "  예: '육아휴직' 질문 → '제43조(육아휴직)① 근로자는...' 조항\n"
            "      Cross-Encoder 가 0.1~0.2 점을 줘도 실제로는 정답 문서임.\n"
            "  threshold=0.3 이면 이 조항이 잘려나가 LLM 이 '찾을 수 없음' 으로 답변.\n"
            "  0.05 로 낮추면 모든 후보가 통과 → ContextBuilder 의 중복 제거/토큰 제한이\n"
            "  품질 게이팅 역할을 대신함. 최소 1개 보장 로직은 그대로 유지."
        ),
    )

    context_dedup_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "중복 청크 제거 Jaccard 유사도 임계값. "
            "이 값 이상이면 중복으로 판단하여 하위 순위 청크 제외. "
            "0.7 = 70% 이상 내용이 겹치면 중복으로 처리."
        ),
    )

    # ──────────────────────────────────────────────────────────────────
    #  경로 설정 (_BASE_DIR 하위 경로가 기본값)
    # ──────────────────────────────────────────────────────────────────

    local_cache_path: Path = Field(
        default=_BASE_DIR / "data_cache",
        description="HuggingFace 임베딩 모델 캐시. 최초 실행 시 ~350MB 다운로드.",
    )

    rag_source_path: Path = Field(
        default=Path(r"G:\공유 드라이브\좋은문화병원_DATA\규정집"),
        description="원본 규정집 PDF 소스 경로 (G드라이브). 미연결 시 동기화만 건너뜀.",
    )

    rag_db_path: Path = Field(
        default=_BASE_DIR / "vector_store",
        description="FAISS 벡터 DB 저장 경로. index.faiss, index.pkl 파일 생성됨.",
    )

    local_work_dir: Path = Field(
        default=_BASE_DIR / "data_rag_working",
        description="G드라이브 동기화 및 관리자 업로드 PDF 작업 경로.",
    )

    db_docs_dir: Path = Field(
        default=_BASE_DIR / "docs" / "db_manuals",
        description=(
            "DB 명세서·ERD·시스템 매뉴얼 등 DB 관련 PDF 를 넣어두는 경로. "
            "이 폴더의 PDF 는 build_db.py 실행 시 규정집과 함께 벡터DB 에 자동 포함됨. "
            "metadata.category = db_manual 로 태깅되어 출처 구분 가능. "
            "폴더가 없거나 비어있으면 자동으로 건너뜀."
        ),
    )

    log_dir: Path = Field(
        default=_BASE_DIR / "logs",
        description="로그 파일 저장 경로. 모듈별 별도 파일, 일별 롤오버, 30일 보관.",
    )

    # ──────────────────────────────────────────────────────────────────
    #  DB 연결 설정 (선택 사항 ─ DB 스키마 RAG 연동용)
    # ──────────────────────────────────────────────────────────────────

    db_enabled: bool = Field(
        default=False,
        description=(
            "병원 DB 스키마 자동 추출 및 RAG 지식화 기능 활성화 여부. "
            "True 설정 시 db_host, db_user, db_password 도 필수."
        ),
    )

    db_type: Literal["mysql", "mssql", "postgresql"] = Field(
        default="mysql",
        description="DB 종류. mysql(MariaDB 포함) / mssql(SQL Server) / postgresql.",
    )

    db_host: str = Field(default="localhost", description="DB 서버 호스트 주소.")
    db_port: int = Field(default=3306, ge=1, le=65535, description="DB 포트번호.")
    db_name: str = Field(default="hospital_db", description="데이터베이스 이름.")

    db_user: str = Field(
        default="",
        description="DB 사용자명. SELECT 전용 rag_readonly 계정 사용 강력 권장.",
    )

    db_password: SecretStr = Field(
        default=SecretStr(""),
        description="DB 패스워드. .env 의 DB_PASSWORD 환경변수로 설정.",
    )

    # ──────────────────────────────────────────────────────────────────
    #  Oracle 데이터 분석 모드 전용 설정 (v4.0 신규)
    #
    #  [기존 db_* 설정과의 차이]
    #  · db_*       → 스키마 추출용 MySQL/MSSQL/PostgreSQL (RAG 지식화)
    #  · oracle_*   → 자연어 질의용 Oracle DB (데이터 분석 모드)
    #  두 설정은 독립적으로 동작합니다.
    #
    #  [보안 체크리스트]
    #  ☑ oracle_password: .env 에만 저장, SecretStr 자동 마스킹
    #  ☑ oracle_user: SELECT 전용 계정 (rag_readonly 권장)
    #  ☑ oracle_whitelist_tables: 허용 테이블 외 접근 차단
    #  ☑ oracle_max_rows: 대용량 데이터 유출/OOM 방지
    # ──────────────────────────────────────────────────────────────────

    oracle_enabled: bool = Field(
        default=False,
        description=(
            "Oracle 데이터 분석 모드 활성화 여부. "
            "True 설정 시 oracle_host, oracle_user, oracle_password 필수."
        ),
    )

    oracle_host: str = Field(
        default="localhost",
        description="Oracle DB 서버 IP 또는 호스트명. 예) 192.168.1.10",
    )

    oracle_port: int = Field(
        default=1521,
        ge=1,
        le=65535,
        description="Oracle 리스너 포트. 기본값 1521 (Oracle 표준).",
    )

    oracle_service_name: str = Field(
        default="ORCL",
        description="Oracle 서비스명. 예) ORCL, HOSPITAL, XE",
    )

    oracle_dsn: str = Field(
        default="",
        description=(
            "Oracle DSN 직접 지정. 비어있으면 host:port/service_name 으로 자동 구성. "
            "TNS Alias 또는 Easy Connect 문자열 직접 입력 시 사용."
        ),
    )

    oracle_user: str = Field(
        default="",
        description="Oracle DB 사용자명. SELECT 전용 계정 사용 강력 권장.",
    )

    oracle_password: SecretStr = Field(
        default=SecretStr(""),
        description="Oracle DB 패스워드. .env 의 ORACLE_PASSWORD 환경변수로 설정.",
    )

    oracle_schema: str = Field(
        default="",
        description=(
            "Oracle 스키마(소유자) 이름. 비어있으면 oracle_user 와 동일하게 처리. "
            "DBA 계정으로 타 스키마에 접근 시 해당 스키마명 입력."
        ),
    )

    oracle_pool_min: int = Field(
        default=2,
        ge=1,
        le=20,
        description="커넥션 풀 최소 유지 수. 첫 요청 지연 없이 즉시 응답하기 위해 2 이상 권장.",
    )

    oracle_pool_max: int = Field(
        default=10,
        ge=1,
        le=100,
        description="커넥션 풀 최대 수. 동시 사용자 수에 따라 조정.",
    )

    oracle_max_rows: int = Field(
        default=5000,
        ge=100,
        le=100000,
        description=(
            "쿼리 결과 최대 행 수. 대용량 테이블 전체 로드 방지. "
            "차트 시각화는 1000행 이하 권장."
        ),
    )

    # ──────────────────────────────────────────────────────────────────
    #  Oracle Thick Mode (10g / 11g 구버전 지원)
    #
    #  [왜 필요한가?]
    #  python-oracledb 기본(Thin Mode)은 Oracle 12.1 이상만 지원합니다.
    #  Oracle 10g/11g 환경에서는 DPY-3010 오류 발생 → Thick Mode 필수.
    #
    #  [Thick Mode 활성화 절차]
    #  1. Oracle Instant Client 11.2 (64bit) 다운로드:
    #     https://www.oracle.com/database/technologies/instant-client/winx64-64-downloads.html
    #     → basiclite 패키지 선택 (용량 작음)
    #  2. 압축 해제 예시: C:\oracle\instantclient_11_2
    #  3. .env 에 아래 두 줄 추가:
    #     ORACLE_THICK_MODE=true
    #     ORACLE_CLIENT_LIB_DIR=C:\oracle\instantclient_11_2
    # ──────────────────────────────────────────────────────────────────
    oracle_thick_mode: bool = Field(
        default=False,
        description=(
            "Thick Mode 활성화 — Oracle 10g/11g 구버전 연결 시 필수. "
            "True 로 설정하면 oracledb.init_oracle_client() 를 호출합니다. "
            ".env: ORACLE_THICK_MODE=true"
        ),
    )
    oracle_client_lib_dir: str = Field(
        default="",
        description=(
            "Oracle Instant Client 설치 경로 (Thick Mode 시 필수). "
            r".env: ORACLE_CLIENT_LIB_DIR=C:\oracle\instantclient_11_2"
        ),
    )
    #
    #  [버그 이력]
    #  v4.0: list[str] → pydantic-settings가 json.loads("") 시도 → JSONDecodeError
    #  v4.1: str       → validator가 list 반환 → pydantic이 str 필드에 list 거부 → ValidationError
    #  v4.2: Any       → pydantic-settings: Any는 complex 아님 → json.loads 건너뜀
    #                    validator 반환 list → pydantic: Any는 모든 타입 허용 → ✅
    #
    #  [왜 Any 가 해결책인가?]
    #  pydantic-settings 의 field_is_complex() 는 list/dict/set 등만 complex로 판단합니다.
    #  Any 는 complex 가 아니므로 json.loads() 를 시도하지 않습니다.
    #  validator(mode="before") 가 raw string → list 변환 후,
    #  pydantic 은 Any 필드에 list 를 그대로 수락합니다.
    #
    #  [.env 허용 형식 — 모두 자동 처리]
    #  ① 빈 값:        ORACLE_WHITELIST_TABLES=             → []          ← 오류 없음
    #  ② 쉼표 문자열:  ORACLE_WHITELIST_TABLES=OMTIDN02     → ['OMTIDN02'] ← 실제 사용 케이스
    #  ③ 다중 테이블:  ORACLE_WHITELIST_TABLES=A,B,C        → ['A','B','C'] ← 권장 형식
    #  ④ JSON 배열:    ORACLE_WHITELIST_TABLES=["A","B"]    → ['A','B']   ← 레거시 호환
    # ──────────────────────────────────────────────────────────────────
    oracle_whitelist_tables: Any = Field(
        default_factory=list,
        description=(
            "데이터 분석 모드 허용 테이블. 쉼표 구분 문자열로 입력. "
            ".env: ORACLE_WHITELIST_TABLES=CHECKUP_MASTER,REVENUE_DAILY"
        ),
    )

    # ──────────────────────────────────────────────────────────────────
    #  oracle_table_descriptions (v4.2 최종 수정)
    #
    #  [버그 이력] oracle_whitelist_tables 와 동일한 이유로 Any 사용.
    #  dict 타입은 pydantic-settings 가 json.loads 시도 → 빈 값이면 crash.
    #
    #  [사용 방법]
    #  .env 에서 직접 정의하기 어려운 멀티라인 딕셔너리는
    #  settings.py 내부에서 서브클래스 property 로 정의하는 것을 권장합니다.
    #
    #  예시 — settings.py 하단에 추가:
    #
    #      class HospitalSettings(AppSettings):
    #          @property
    #          def oracle_table_descriptions_map(self) -> dict:
    #              return {
    #                  "CHECKUP_MASTER": """건강검진 마스터
    #    - VISIT_DATE: 방문일자 (DATE)
    #    - PATIENT_NO: 환자번호 (VARCHAR2)""",
    #              }
    # ──────────────────────────────────────────────────────────────────
    oracle_table_descriptions: Any = Field(
        default_factory=dict,
        description=(
            "테이블 스키마 설명 JSON. 빈 값이면 {} 로 처리. "
            "복잡한 스키마는 .env 대신 settings.py 서브클래스에서 직접 정의 권장."
        ),
    )

    # ──────────────────────────────────────────────────────────────────
    #  보안 설정
    # ──────────────────────────────────────────────────────────────────

    admin_password: SecretStr = Field(
        default=SecretStr("moonhwa"),
        description=(
            "관리자 패널 패스워드. 운영 환경에서는 반드시 .env 의 "
            "ADMIN_PASSWORD 환경변수로 설정. 최소 12자, 특수문자 포함 권장."
        ),
    )

    # ──────────────────────────────────────────────────────────────────
    #  Streamlit UI / 서버 설정
    # ──────────────────────────────────────────────────────────────────

    app_title: str = Field(
        default="좋은문화병원 가이드봇",
        description="브라우저 탭 제목.",
    )

    server_ip: str = Field(
        default="0.0.0.0",
        description="Streamlit 바인딩 IP. 0.0.0.0=LAN 허용, 127.0.0.1=로컬 전용.",
    )

    # ──────────────────────────────────────────────────────────────────
    #  모니터링 설정 (v3.0 신규)
    # ──────────────────────────────────────────────────────────────────

    monitoring_enabled: bool = Field(
        default=True,
        description=(
            "성능 메트릭 수집 활성화. True: 질문 수/응답 시간/오류율 등 수집 후 "
            "사이드바 표시. False: 메트릭 비활성화 (미세 성능 최적화 필요 시)."
        ),
    )

    # ══════════════════════════════════════════════════════════════════
    #  입력값 검증 (field_validator)
    # ══════════════════════════════════════════════════════════════════

    @field_validator("oracle_whitelist_tables", mode="before")
    @classmethod
    def _parse_oracle_whitelist(cls, v) -> list:
        """
        oracle_whitelist_tables 파서 — 세 가지 .env 형식 모두 허용.

        [처리 흐름]
          빈 문자열 / None → []
          "A,B,C"          → ["A", "B", "C"]    ← .env 권장 형식
          '["A","B"]'      → ["A", "B"]          ← JSON 배열 형식
          list             → 그대로 반환 (기본값 경로)

        [왜 mode="before" 인가?]
          pydantic 이 str → list 변환을 시도하기 전에 이 함수가 먼저 실행됩니다.
          "before" 없이는 str 을 list 로 직접 캐스팅하려다 ValidationError 발생.
        """
        import json as _json

        if isinstance(v, list):
            return v  # 이미 리스트 (기본값 경로)
        if not v or not str(v).strip():
            return []  # 빈 값 → 빈 리스트
        s = str(v).strip()
        if s.startswith("["):  # JSON 배열 형식
            return _json.loads(s)
        # 쉼표 구분 문자열 → 각 항목 공백 제거 후 대문자 정규화
        return [t.strip().upper() for t in s.split(",") if t.strip()]

    @field_validator("oracle_table_descriptions", mode="before")
    @classmethod
    def _parse_oracle_table_desc(cls, v) -> dict:
        """
        oracle_table_descriptions 파서 — 빈 값과 JSON 문자열 모두 허용.

        [처리 흐름]
          빈 문자열 / None → {}
          '{...}'          → dict (JSON 파싱)
          dict             → 그대로 반환

        [운영 권장 방식]
        .env 에서 멀티라인 JSON 을 관리하면 오류가 나기 쉽습니다.
        대신 settings.py 에 서브클래스를 만들어 직접 정의하세요:

            class HospitalSettings(AppSettings):
                @property
                def oracle_table_descriptions_resolved(self) -> dict:
                    return {
                        "CHECKUP_MASTER": \"""건강검진 마스터 테이블
          - VISIT_DATE: 방문일자 (DATE)\""",
                    }
        """
        import json as _json

        if isinstance(v, dict):
            return v
        if not v or not str(v).strip():
            return {}
        s = str(v).strip()
        if s.startswith("{"):
            return _json.loads(s)
        return {}  # 알 수 없는 형식 → 빈 dict

    @field_validator("chunk_overlap")
    @classmethod
    def _overlap_lt_chunk_size(cls, v: int, info) -> int:
        """
        chunk_overlap 은 반드시 chunk_size 보다 작아야 합니다.

        오버랩 >= 청크 크기이면 모든 청크가 사실상 동일한 내용을 담게 되어
        중복 색인·검색 노이즈가 심각하게 발생합니다.
        """
        chunk_size = info.data.get("chunk_size", 1000)
        if v >= chunk_size:
            raise ValueError(
                f"chunk_overlap({v})은 chunk_size({chunk_size})보다 작아야 합니다."
            )
        return v

    @field_validator("rerank_top_n")
    @classmethod
    def _rerank_lte_retrieve(cls, v: int, info) -> int:
        """
        rerank_top_n 은 retrieve_top_k 이하여야 합니다.

        Cross-Encoder 가 선택할 최종 수(top_n)는 FAISS 1차 후보 수(top_k) 이하여야
        논리적으로 의미가 있습니다.
        """
        top_k = info.data.get("retrieve_top_k", 10)
        if v > top_k:
            raise ValueError(
                f"rerank_top_n({v})은 retrieve_top_k({top_k}) 이하여야 합니다."
            )
        return v

    # ══════════════════════════════════════════════════════════════════
    #  모델 전체 검증 + 디렉토리 자동 생성
    # ══════════════════════════════════════════════════════════════════

    @model_validator(mode="after")
    def _create_directories(self) -> "AppSettings":
        """
        앱 기동 시 필수 디렉토리를 자동으로 생성합니다.

        모든 field_validator 완료 후 최종 실행됩니다.
        exist_ok=True 로 이미 있으면 무시, parents=True 로 중간 경로까지 생성.
        """
        for path in (
            self.local_cache_path,
            self.rag_db_path,
            self.local_work_dir,
            self.db_docs_dir,  # DB 명세서 PDF 폴더 자동 생성
            self.log_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return self

    # ══════════════════════════════════════════════════════════════════
    #  편의 프로퍼티 (cached_property = 최초 1회 계산 후 캐시)
    # ══════════════════════════════════════════════════════════════════

    @cached_property
    def faiss_index_path(self) -> Path:
        """FAISS 인덱스 파일 전체 경로 (존재 여부 확인에 사용)."""
        return self.rag_db_path / "index.faiss"

    @cached_property
    def backup_dir(self) -> Path:
        """
        벡터 DB 자동 백업 저장 경로.

        vector_store.py 의 _backup_existing() 에서 빌드마다 호출됩니다.
        타임스탬프 이름으로 백업하고 최근 5개만 보관합니다.
        경로: D:\\mh\\guidbot\\vector_store_backup
        """
        return _BASE_DIR / "vector_store_backup"

    @cached_property
    def db_url(self) -> str:
        """
        SQLAlchemy 연결 URL (db_type 에 따라 드라이버 자동 선택).

        [보안 주의] 패스워드가 포함된 URL 이므로 로그 출력 금지!
        connector.py 의 _get_masked_url() 로 마스킹 처리하여 로그에 기록합니다.
        """
        pwd = self.db_password.get_secret_value()
        u, h, p, n = self.db_user, self.db_host, self.db_port, self.db_name
        return {
            "mysql": (f"mysql+pymysql://{u}:{pwd}@{h}:{p}/{n}?charset=utf8mb4"),
            "mssql": (
                f"mssql+pyodbc://{u}:{pwd}@{h}:{p}/{n}"
                "?driver=ODBC+Driver+17+for+SQL+Server"
            ),
            "postgresql": (f"postgresql+psycopg2://{u}:{pwd}@{h}:{p}/{n}"),
        }[self.db_type]

    # ══════════════════════════════════════════════════════════════════
    #  보안 메서드
    # ══════════════════════════════════════════════════════════════════

    def get_google_api_key(self) -> str:
        """
        기본 Google API 키 반환 (하위 호환성 유지).

        [주의] 일반 비즈니스 로직에서는 직접 호출하지 마세요.
        키 풀 전체를 사용하려면 get_api_key_pool() 을 사용하세요.
        """
        return self.google_api_key.get_secret_value()

    def get_api_key_pool(self) -> list[str]:
        """
        사용 가능한 API 키 전체 목록을 반환합니다.

        [동작 방식]
        - GOOGLE_API_KEY (필수) + GOOGLE_API_KEY_2~5 (선택) 를 순서대로 수집
        - None 이거나 빈 문자열인 키는 자동으로 제외
        - 중복 키도 자동으로 제외 (같은 키를 여러 번 등록한 실수 방지)

        Returns:
            유효한 API 키 문자열 목록 (최소 1개 보장, 최대 5개)

        Example::
            pool = settings.get_api_key_pool()
            # ['AIzaXXX', 'AIzaYYY', 'AIzaZZZ']
        """
        keys: list[str] = []
        seen: set[str] = set()

        candidates = [
            self.google_api_key,
            self.google_api_key_2,
            self.google_api_key_3,
            self.google_api_key_4,
            self.google_api_key_5,
        ]
        for secret in candidates:
            if secret is None:
                continue
            val = secret.get_secret_value().strip()
            if val and val not in seen:
                keys.append(val)
                seen.add(val)

        return keys

    def check_admin(self, candidate: str) -> bool:
        """
        관리자 패스워드 검증 (타이밍 공격 방어).

        hmac.compare_digest() 사용 이유:
        일반 == 비교는 앞 글자부터 순서대로 비교하여 응답 시간 차이로
        패스워드 추측이 가능합니다 (타이밍 공격).
        compare_digest 는 항상 전체를 비교하여 실행 시간이 일정합니다.

        Args:
            candidate: 입력된 패스워드 문자열

        Returns:
            True: 일치 | False: 불일치
        """
        return hmac.compare_digest(
            self.admin_password.get_secret_value().encode("utf-8"),
            candidate.encode("utf-8"),
        )


# ══════════════════════════════════════════════════════════════════════
#  전역 싱글톤 인스턴스
#  앱 기동 시 단 1회 생성 → 모든 모듈에서 from config.settings import settings 로 공유
# ══════════════════════════════════════════════════════════════════════

# Pydantic v2: Optional[SecretStr] 같은 forward reference 를 클래스 정의 후에
# 명시적으로 해소해야 합니다. model_rebuild() 가 이를 처리합니다.
AppSettings.model_rebuild()

settings = AppSettings()  # type: ignore[call-arg]

# ── 외부 라이브러리용 환경변수 주입 ──────────────────────────────────
# setdefault: 이미 환경변수가 설정된 경우 덮어쓰지 않음 (사용자 커스터마이징 존중)

# Google Generative AI SDK 가 자동으로 읽는 환경변수
os.environ.setdefault("GOOGLE_API_KEY", settings.get_google_api_key())

# sentence-transformers 가 모델 다운로드 시 저장할 경로
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(settings.local_cache_path))
