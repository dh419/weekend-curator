# Weekend Curator

주말 가족 나들이 코스와 실내 놀이·식단 정보를 정적 HTML 대시보드로 생성해 GitHub Pages로 게시하는 프로젝트입니다.

- **Pages**: https://dh419.github.io/weekend-curator/

## 동작 흐름
1. **데이터 수집 및 보강**: GitHub Actions(`generate_dashboard.yml`)가 매주 금요일(`16:00~17:30 KST`) 크론 또는 수동 실행 시 Google Drive에서 주차별 JSON을 가져와 Kakao·Gemini API로 이동 정보와 리뷰 요약을 보강합니다.
2. **HTML 생성 및 배포**: `scripts/sync_from_gdrive.py`가 `docs/YYYY-MM/`에 대시보드를 생성하고, `scripts/update_index.py`가 `hub-data.json`, `all-spots.html`, 검색 차단(`robots.txt`, `noindex`)을 갱신해 Pages로 배포합니다.

## 구성
- `.github/workflows/generate_dashboard.yml`: 주간 대시보드 생성 및 배포 워크플로
- `.github/workflows/test_grounding.yml`: 보강 로직 검증 워크플로
- `scripts/sync_from_gdrive.py`: Drive JSON 수집 및 대시보드 생성
- `scripts/enrich_with_kakao_gemini.py`: Kakao·Gemini 기반 장소/블로그 정보 보강
- `scripts/generate_dashboard.py`, `scripts/weekend_defaults.py`: 대시보드 렌더링 및 기본 데이터
- `scripts/update_index.py`: `hub-data.json`, `all-spots.json`/`all-spots.html` 및 검색 차단 자동 보정
- `templates/master_dashboard.html`: 대시보드 템플릿
- `docs/YYYY-MM/`: 주차별 대시보드 HTML
- `index.html`, `all-spots.html`, `hub-data.json`, `all-spots.json`, `robots.txt`: 허브 및 누적 장소 아카이브
