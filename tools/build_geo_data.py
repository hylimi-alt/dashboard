#!/usr/bin/env python3
"""윤스맵 '구역 분석'용 데이터 만들기 (세대수·동수 / 초중학교 학생수)

사용법 (연 1~2회, 새 파일을 받았을 때):
  python3 tools/build_geo_data.py <원본폴더>
  <원본폴더> 안에 아래 3개 파일이 있어야 한다 (이름 그대로).
    apartment/공동주택_Kapt_*_필수항목.csv      (K-apt 단지: 단지코드·단지명·세대수·동수·주소)
    schools/초중학교_위치_운영중_*.csv          (학교 위치: 학교명·학교급·위도·경도)
    school_students/초중학교_학년별학생수_*.csv (학교알리미 학년별 학생수)
결과: map/data/apts.json, map/data/schools.json, map/data/geo_meta.json
출처 표시: 국토교통부·한국부동산원 K-apt / 한국교육시설안전원·공공데이터포털 학교위치 / 교육부·학교알리미 학생수
"""
import csv, glob, json, os, sys, collections

src = sys.argv[1] if len(sys.argv) > 1 else '.'
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'map', 'data')
os.makedirs(out, exist_ok=True)

def pick(pattern):
    f = sorted(glob.glob(os.path.join(src, pattern)))
    if not f: sys.exit('파일을 찾지 못했습니다: ' + pattern)
    return f[-1]
def rd(p): return list(csv.DictReader(open(p, encoding='utf-8-sig')))

# 1) 단지
fa = pick('apartment/공동주택_Kapt_*_필수항목.csv')
apts = []
for r in rd(fa):
    try: hh = int(float(r['세대수']))
    except: continue
    try: dong = int(float(r['동수']))
    except: dong = 0
    addr = (r['도로명주소'] or '').strip() or (r['법정동주소'] or '').strip()
    apts.append([r['단지코드'], r['단지명'], hh, dong, addr, r['시도'], r['시군구'], r['단지분류']])
asof_apt = rd(fa)[0].get('자료추출기준일', '')

# 2) 학교 + 학생수 (학교명+학교급 → 동명은 시군구로 좁힘, 그래도 2곳 이상이면 학생수 미연결)
fs = pick('schools/초중학교_위치_운영중_*.csv')
ft = pick('school_students/초중학교_학년별학생수_*.csv')
sch = rd(fs); stu = rd(ft)
idx = collections.defaultdict(list)
for r in sch: idx[(r['학교명'].replace(' ', ''), r['학교급구분'])].append(r)
def gu_ok(addr, region):
    toks = region.split()[1:]
    return all(t[:2] in addr for t in toks) if toks else True
cnt = {}; amb = []; none = []
for r in stu:
    c = idx.get((r['학교명'].replace(' ', ''), r['학교급']), [])
    if len(c) > 1: c = [x for x in c if gu_ok(x['소재지도로명주소'], r['지역'])]
    if len(c) == 1:
        try: cnt[c[0]['학교ID']] = int(r['학생수(계)'])
        except: pass
    elif len(c) > 1: amb.append(r['학교명'] + '|' + r['지역'])
    else: none.append(r['학교명'] + '|' + r['지역'])
schools = []
for r in sch:
    lvl = 'e' if r['학교급구분'] == '초등학교' else 'm' if r['학교급구분'] == '중학교' else None
    if not lvl: continue
    try: lat = round(float(r['위도']), 6); lng = round(float(r['경도']), 6)
    except: continue
    schools.append([r['학교명'], lvl, lat, lng, cnt.get(r['학교ID'])])
asof_sch = sch[0].get('데이터기준일자', '')
asof_stu = stu[0].get('학생수자료기준일', '')

json.dump(apts, open(os.path.join(out, 'apts.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
json.dump(schools, open(os.path.join(out, 'schools.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
json.dump({'apt': asof_apt, 'school': asof_sch, 'students': asof_stu,
           'counts': {'apts': len(apts), 'schools': len(schools), 'with_students': sum(1 for s in schools if s[4] is not None)},
           'unmatched_students': amb + none},
          open(os.path.join(out, 'geo_meta.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('단지', len(apts), '| 학교', len(schools), '| 학생수 연결', sum(1 for s in schools if s[4] is not None),
      '| 학생수 미연결(이름 중복·없음)', len(amb) + len(none))
