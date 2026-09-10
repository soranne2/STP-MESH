# STP-MESH
# STEP Mesher

STEP(`.stp`) 파트를 읽어 파트 유형별로 메시를 만들고 Abaqus `.inp`로 내보내는 파이썬 프로그램.
GUI는 PySide6, 형상/메시 엔진은 gmsh(OpenCASCADE)를 쓴다.

```bash
pip install -r requirements.txt
python app.py          # GUI
python cli.py *.stp -o out --size 5 --hole-nodes 8 --washer 3
```

## 파트 유형별 처리

| 유형 | 방식 | 결과 요소 |
|---|---|---|
| 프레스 (얇은 판재) | 마주보는 평행 평면쌍을 찾아 t/2 오프셋 → mid-surface → quad-dominant | S4R / S3 |
| 사출 (복잡 형상) | 곡률 기반 크기 제어 + 홀 국부 세밀화 → tetra | C3D4 / C3D10 |
| 압출 (프로파일) | 단면 캡을 복사해 quad로 메시 → 압출 방향 sweep | C3D8R / C3D20R |

`자동 판정`을 고르면 형상 지표로 알아서 나눈다.

- 압출: 평행하고 면적이 같은 캡 한 쌍이 있고 `V ≈ 단면적 × 길이`이며, 길이가 단면 대각보다 길면 압출.
- 프레스: 등가두께 `2V/A`가 설정값(기본 6) 이하이고 전체 크기 대비 충분히 얇으면 박판.
- 나머지는 사출로 본다.

## 홀과 washer

공통 파라미터는 요소 크기 5, 홀 원주 노드 8, washer 폭 3이고 전부 GUI에서 바꿀 수 있다.

평면 위의 완전 원형 엣지를 홀로 잡은 뒤,

1. 반지름 `r+3` 디스크를 면에 imprint해서 링을 만들고
2. 4방향 반경선으로 링을 사분면으로 쪼갠 다음
3. 각 조각을 transfinite + recombine 처리한다.

결과적으로 홀 주위에 정렬된 quad 링이 생기고 원주에는 정확히 8개 절점이 놓인다.
분할이 실패하면 Distance/Threshold 크기 필드로 자동 대체하므로 메시 생성 자체는 멈추지 않는다
(로그에 몇 개가 대체됐는지 남는다).

압출 파트는 단면(2D) 상태에서 washer를 만든 뒤 sweep하므로 링이 길이방향으로 그대로 따라 올라간다.
tetra는 정렬 링이 성립하지 않아 원주 노드 고정 + washer 폭 안쪽 크기 유지로만 처리한다.

## 두께방향 layer (압출)

단면 벽두께를 `2 × 단면적 / 단면 둘레`로 추정하고, 이 값을 층 수로 나눈 값을 단면 요소 크기로 쓴다.
벽 2.5mm에 2층이면 단면 크기가 1.25mm가 된다. 벽두께를 직접 넣고 싶으면 `벽 두께 지정`에 값을 주면 된다.
길이방향 분할은 `길이방향 크기`로 따로 잡는다(0이면 공통 요소 크기).

## 출력

gmsh의 Abaqus writer는 shell을 평면응력(CPS3/CPS4)으로 쓰기 때문에, 저장 후 후처리로

- `CPS4 → S4R`, `CPS3 → S3`, `C3D8 → C3D8R` 등 실사용 타입으로 치환하고
- 두께별 `*SHELL SECTION` / `*SOLID SECTION`을 붙인다.

두께가 다른 mid-surface 패치는 `SHELL_T1p50` 같은 ELSET으로 자동 분리된다.
`MATERIAL=` 이름만 실제 재료로 바꿔서 쓰면 된다.

품질은 `minSICN`으로 min/avg와 임계값 미만 요소 수를 리포트한다.

## 알아둘 한계

가장 약한 고리는 mid-surface다. 여기 구현한 것은 **평행 평면쌍의 t/2 오프셋**까지이고,
상용 전처리기가 하는 벤딩 R 구간의 중립면 생성과 자동 연장·봉합은 들어있지 않다.

- 평평하거나 계단진 프레스물: 잘 나온다.
- 벤딩이 많은 딥드로잉 커버: 패치 사이에 틈이 남는다. 로그에 free edge 수를 찍어주니 그 값이 크면 신뢰하지 말 것.
- 면쌍을 아예 못 찾으면 최대 면을 등가두께 절반만큼 오프셋하는 방식으로 대체한다(근사).

실무에서는 ANSA MIDDLE로 mid-surface만 뽑아 STEP으로 내보낸 뒤, 이 프로그램의 메시·홀·washer·export 부분만
쓰는 편이 훨씬 안정적이다. 그 경우 파트 유형을 `프레스`로 두면 면쌍 탐색에 실패하고 곧바로 단독 면 메시로 넘어간다.

그 외:

- 어셈블리 STEP은 solid마다 별도 파일(`이름_p01.inp`)로 나온다.
- 압출 판정은 캡 한 쌍이 정확히 평면일 때만 된다. 끝단에 모따기나 가공 형상이 있으면 실패하므로 tetra로 돌리거나 `압출 판정 허용`을 키운다.
- 단위가 m인 STEP은 `단위 배율`을 1000으로.

## 구조

```
app.py              GUI 진입점
cli.py              배치 진입점
mesher/config.py    파라미터 + 프리셋 JSON
mesher/geometry.py  STEP 로드, 분류, 홀 검출, washer 생성
mesher/shell.py     mid-surface + shell
mesher/tetra.py     tetra
mesher/hexa.py      단면 quad + sweep hexa
mesher/exporter.py  inp 후처리, SECTION 작성
mesher/pipeline.py  분류 → 메시 → 품질 → 저장
gui/theme.py        블랙 + 토스블루 스타일
gui/main_window.py  메인 윈도우
```
