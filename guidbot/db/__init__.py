"""
db 패키지 ─ 병원 DB 연결 및 스키마 추출

[패키지 구성]
  connector        : SQLAlchemy 연결 풀 관리 (SELECT 전용 계정 권장)
  schema_extractor : information_schema 자동 쿼리 → RAG Document 변환

[보안 원칙]
  - SELECT 전용 rag_readonly 계정 사용 강력 권장
  - DB 패스워드는 반드시 .env 의 DB_PASSWORD 로 설정
  - 연결 URL 을 로그에 직접 출력하지 않음 (_get_masked_url() 사용)

[사용 예시]
  from db import get_db_connector

  connector = get_db_connector()
  if connector:
      with connector.get_session() as session:
          rows = session.execute(text("SELECT ...")).mappings().all()
"""

from db.connector import DatabaseConnector, get_db_connector

__all__ = ["DatabaseConnector", "get_db_connector"]
