# Weekend Curator (주말 나들이 큐레이션)

동탄반석로 71 출발 기준, 두 아이(2020년생 딸 · 2024년생 아들)와 함께하기 좋은 경기권 주말 야외·체험 나들이 코스를 추천하고 단일 HTML 대시보드로 생성해 GitHub Pages로 게시하는 프로젝트입니다.

- **GitHub Pages**: [https://dh419.github.io/weekend-curator/](https://dh419.github.io/weekend-curator/)

## 주요 기능
- **맞춤형 코스 큐레이션**: 이동 시간, 유모차·놀이터 안전성, 아이 친화적 비매운(면/식사) 맛집 정보 포함
- **독립 실행형 HTML**: 외부 CSS/JS 의존성 없는 단일 파일 대시보드
- **단일 HTML 셸**: `localStorage` 기반 무깜빡임 테마 복원 적용

## 무인 수집 파이프라인 (Gemini App → Google Drive → GitHub Actions)
1. **JSON 생성 및 업로드**: Gemini 앱 예약 작업(매주 금요일)이 Google Drive `dashboard_incoming/weekend-curator` (`19jx-SYkJqEz02VG4iLLSzPupxhluBLfN`) 폴더에 `YYYY-MM-DD-weekend.json` 저장
2. **트리거 감지**: Google Apps Script(`10분 주기`)가 신규/수정 파일을 감지해 `generate_dashboard.yml` (`workflow_dispatch`) 실행
3. **HTML 빌드 & 인덱스 갱신**: `scripts/sync_from_gdrive.py`가 JSON을 수집해 `docs/YYYY-MM/YYYY-MM-DD.html`을 생성하고, `scripts/update_index.py`가 `hub-data.json`을 갱신한 뒤 GitHub Pages로 자동 배포

## 디렉터리 구조
- `scripts/sync_from_gdrive.py`: Google Drive 전용 하위 폴더에서 최신 `*-weekend.json` 수집 및 HTML 생성
- `scripts/update_index.py`: `hub-data.json` 사이드바 인덱스 자동 갱신
- `templates/master_dashboard.html`: 대시보드 마스터 HTML 템플릿
- `docs/YYYY-MM/`: 주차별 나들이 큐레이션 HTML (`GitHub Pages` 배포 대상)

## 주말 나들이 큐레이션 스킬 체이닝 (Multi-Skill Pipeline)
주말 나들이 대시보드 생성 시 아래 전역 스킬을 유기적으로 복합 발동합니다:
1. **`kids-activity-curator`**: 동탄 출발 기준 6선 코스·이동시간·유모차/놀이터·비매운 맛집 큐레이션 및 `hub-data.json` 동기화
2. **`html-report-shell`** + **`svg-micro-animations`**: 무깜빡임 테마 복원, 카드 호버 리프트 및 `prefers-reduced-motion` 가드 적용
3. **`baoyu-infographic`**: 필요 시 주말 6선 코스를 가족 공유용 한 장 요약 인포그래픽으로 생성
4. **`webapp-testing` / `agent-browser`**: 생성된 HTML·허브(`index.html`, `hub-data.json`)의 모바일 레이아웃·제목/링크 사후 검증
