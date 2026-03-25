"""
ui 패키지 ─ Streamlit 기반 사용자 인터페이스

[패키지 구성]
  components : 재사용 가능한 UI 컴포넌트 (소스 카드, 배너, 헤더 등)
  sidebar    : 사이드바 렌더링 (DB 상태, 사용 통계, 관리자 패널)
  theme      : 전역 CSS 테마 (병원 내부 시스템 스타일)

[사용 원칙]
  - UI 컴포넌트는 비즈니스 로직을 포함하지 않음
  - 상태는 st.session_state 로만 관리
  - 모든 컴포넌트는 독립적으로 테스트 가능하도록 설계
"""

from ui.sidebar import render_sidebar, DBHealth

__all__ = ["render_sidebar", "DBHealth"]
