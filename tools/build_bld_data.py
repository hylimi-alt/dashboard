#!/usr/bin/env python3
"""동(건물) 단위 세대수 데이터 생성기  (구역 분석 '혼합 방식'의 원천 데이터)

입력(공공데이터, 모두 무료)
  --hub  건축물대장_표제부_공동주택_YYYYMM기준.csv     (건축HUB 표제부: 동별 세대수)
  --gis  AL_D010_XX_*_multifamily.zip 들이 든 폴더     (브이월드 GIS건물통합정보 공동주택 후보 SHP, EPSG:5186)
  --kapt map/data/apts.json                            (K-apt 단지, build_geo_data.py 산출물)
출력(map/data/)
  bld/<시도코드>.json  {"n":[단지명...],"b":[[lat*1e5,lng*1e5,세대수,단지명idx,flag,apt],...]}
        apt 1 = 브이월드 세부용도 '아파트(02001)' 확정, 0 = 공동주택 후보(연립·다세대 등 포함)
        flag 0 = 동 이름까지 일치(실제 동별 세대수), 1 = 같은 번지 안에서 균등 배분(추정)
  bld_meta.json        시도별 건수·범위·기준일·연결률
  apts.json            (갱신) 동 단위로 이미 잡힌 K-apt 단지는 제외 → 이중 집계 방지

실행: python3 tools/build_bld_data.py --hub <표제부.csv> --gis <SHP폴더> [--apt 전국_아파트코드_통합속성.csv] [--kapt map/data/apts.json]  (※ 먼저 tools/build_geo_data.py 를 실행해 apts.json 원본을 만든 뒤 실행)
필요: pip install pandas pyshp pyproj
주의: 이 스크립트는 '공동주택 후보'와 '표제부'를 번지(PNU)+동이름으로 잇는다. 아파트 확정 데이터가 아니다.
"""
import argparse, glob, io, json, os, re, sys, zipfile
import pandas as pd, shapefile
from pyproj import Transformer

ap = argparse.ArgumentParser()
ap.add_argument('--hub', required=True)
ap.add_argument('--gis', required=True)
ap.add_argument('--kapt', default='map/data/apts.json')
ap.add_argument('--apt', default='', help='전국_아파트코드_통합속성.csv (세부용도 02001 아파트 확정 목록, 선택)')
ap.add_argument('--kapt-src', default='')
ap.add_argument('--out', default='map/data')
ap.add_argument('--asof', default='')
a = ap.parse_args()

# ---------- 1) 표제부 ----------
cols = ['시군구_코드', '법정동_코드', '대지_구분_코드', '번', '지', '건물_명', '동_명',
        '주_부속_구분_코드_명', '세대_수(세대)', '도로명_대지_위치']
parts = []
for c in pd.read_csv(a.hub, usecols=cols, dtype=str, chunksize=500000, encoding='utf-8-sig'):
    parts.append(c[c['주_부속_구분_코드_명'] == '주건축물'])
h = pd.concat(parts, ignore_index=True)
h['hh'] = pd.to_numeric(h['세대_수(세대)'], errors='coerce').fillna(0).astype(int)
h = h[h.hh > 0].copy()
h['pnu'] = (h['시군구_코드'] + h['법정동_코드'] + h['대지_구분_코드'].map({'0': '1', '1': '2'}).fillna('1')
            + h['번'].str.zfill(4) + h['지'].str.zfill(4))
h['dn'] = h['동_명'].fillna('').str.strip()
h['bn'] = h['건물_명'].fillna('').str.strip()
h['road'] = h['도로명_대지_위치'].fillna('').str.replace(r'\s*\(.*$', '', regex=True).str.strip()
h = h[['pnu', 'dn', 'bn', 'hh', 'road']]
print('표제부 주건축물(세대>0):', len(h), '세대합', int(h.hh.sum()))
hub_by = {k: g for k, g in h.groupby('pnu')}

# ---------- 2) GIS 건물 ----------
tr = Transformer.from_crs(5186, 4326, always_xy=True)
rows = []
for z in sorted(glob.glob(os.path.join(a.gis, '*.zip'))):
    zf = zipfile.ZipFile(z)
    base = [n for n in zf.namelist() if n.lower().endswith('.shp')][0][:-4]
    r = shapefile.Reader(shp=io.BytesIO(zf.read(base + '.shp')), shx=io.BytesIO(zf.read(base + '.shx')),
                         dbf=io.BytesIO(zf.read(base + '.dbf')), encoding='cp949')
    n0 = len(rows)
    for sr in r.iterShapeRecords():
        s = sr.shape
        if not s.points:
            continue
        x0, y0, x1, y1 = s.bbox
        rec = sr.record
        rows.append(((x0 + x1) / 2, (y0 + y1) / 2, str(rec[2])[:19], str(rec[24] or '').strip(),
                     str(rec[25] or '').strip(), str(rec[1]), str(rec[19])))
    print(os.path.basename(z), len(rows) - n0)
g = pd.DataFrame(rows, columns=['x', 'y', 'pnu', 'bn', 'dn', 'gid', 'bid'])
g = g.drop_duplicates(['pnu', 'dn', 'gid', 'bid', 'x', 'y']).reset_index(drop=True)
lng, lat = tr.transform(g.x.values, g.y.values)
g['lat'], g['lng'] = lat, lng
print('GIS 후보 건물:', len(g))
apt_gid = set()
if a.apt and os.path.exists(a.apt):
    apt_gid = set(pd.read_csv(a.apt, usecols=['GIS건물통합식별번호'], dtype=str, encoding='utf-8-sig')['GIS건물통합식별번호'])
g['apt'] = g.gid.isin(apt_gid).astype(int)
print('이 중 아파트 확정(세부용도 02001):', int(g.apt.sum()))

# ---------- 3) 번지(PNU) 단위 조인 ----------
out = []  # (sido, lat, lng, hh, name, flag, pnu)
placed_by_pnu = {}
for pnu, gg in g.groupby('pnu'):
    hb = hub_by.get(pnu)
    if hb is None:
        continue
    assign = {}  # gis index -> (hh, flag)
    hdn = hb[hb.dn != ''].groupby('dn').hh.sum()
    used_dn = set()
    left = list(gg.index)
    for dn, rows_ in gg[gg.dn != ''].groupby('dn'):
        if dn in hdn.index:
            k = len(rows_)
            for i in rows_.index:
                assign[i] = (hdn[dn] / k, 0 if k == 1 else 1)
                left.remove(i)
            used_dn.add(dn)
    rest = int(hb[~hb.dn.isin(used_dn)].hh.sum())
    if left and rest > 0:
        for i in left:
            assign[i] = (rest / len(left), 1)
    nm = next((x for x in hb.bn if x), '') or next((x for x in gg.bn if x), '')
    tot = 0
    for i, (v, fl) in assign.items():
        v = int(round(v))
        if v <= 0:
            continue
        r = gg.loc[i]
        out.append((pnu[:2], r.lat, r.lng, v, nm, fl, pnu, int(r.apt)))
        tot += v
    if tot:
        placed_by_pnu[pnu] = tot
o = pd.DataFrame(out, columns=['sido', 'lat', 'lng', 'hh', 'name', 'flag', 'pnu', 'apt'])
print('배치된 건물:', len(o), '세대합', int(o.hh.sum()), '(표제부 대비 %.1f%%)' % (100 * o.hh.sum() / h.hh.sum()))

# ---------- 4) 시도별 파일 ----------
os.makedirs(os.path.join(a.out, 'bld'), exist_ok=True)
for f in glob.glob(os.path.join(a.out, 'bld', '*.json')):
    os.remove(f)
meta = {'apt_hh': int(o[o.apt == 1].hh.sum()), 'asof': a.asof, 'sido': {}, 'total_hub_hh': int(h.hh.sum()), 'placed_hh': int(o.hh.sum()),
        'src': '국토교통부 건축HUB 표제부·브이월드 GIS건물통합정보(공동주택 후보)'}
hub_hh_by_sido = h.groupby(h.pnu.str[:2]).hh.sum()
for sd, d in o.groupby('sido'):
    names = sorted(set(d.name))
    idx = {n: i for i, n in enumerate(names)}
    d = d.sort_values('lat')
    b = [[int(round(r.lat * 1e5)), int(round(r.lng * 1e5)), int(r.hh), idx[r.name], int(r.flag), int(r.apt)] for r in d.itertuples()]
    p = os.path.join(a.out, 'bld', sd + '.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump({'n': names, 'b': b}, f, ensure_ascii=False, separators=(',', ':'))
    meta['sido'][sd] = {'n': len(b), 'hh': int(d.hh.sum()), 'hub_hh': int(hub_hh_by_sido.get(sd, 0)),
                        'bbox': [round(d.lat.min(), 4), round(d.lng.min(), 4), round(d.lat.max(), 4), round(d.lng.max(), 4)],
                        'exact_pct': round(100 * (d.flag == 0).mean(), 1), 'kb': round(os.path.getsize(p) / 1024)}
    print(sd, meta['sido'][sd])

# ---------- 5) K-apt 중복 제거 ----------
src = a.kapt_src or a.kapt   # 항상 build_geo_data.py 원본에서 읽어야 반복 실행해도 같은 결과
if os.path.exists(src):
    ka = json.load(open(src, encoding='utf-8'))
    road_pnu = {}
    for r in h[h.road != ''].itertuples():
        road_pnu.setdefault(r.road, set()).add(r.pnu)
    keep, drop, part = [], 0, 0
    for x in ka:
        pn = road_pnu.get(re.sub(r'\s*\(.*$', '', x[4]).strip(), set())
        placed = sum(placed_by_pnu.get(p, 0) for p in pn)
        if pn and placed >= 0.6 * x[2]:
            drop += 1                      # 동 단위로 충분히 잡힘 → 단지 방식 제외
        elif pn and placed > 0:            # 일부만 잡힘 → 나머지 세대·동만 단지 방식으로 남김(이중 집계 방지)
            f = 1 - placed / x[2]
            y = list(x); y[2] = max(1, x[2] - placed); y[3] = max(1, round(x[3] * f)); keep.append(y); part += 1
        else:
            keep.append(x)
    meta['kapt_total'] = len(ka)
    meta['kapt_dropped_covered'] = drop
    meta['kapt_kept'] = len(keep)
    meta['kapt_partial'] = part
    print('K-apt 단지: 전체', len(ka), '동 단위로 대체', drop, '유지', len(keep), '(일부만 잡힘 %d)' % part)
    json.dump(keep, open(a.kapt, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
json.dump(meta, open(os.path.join(a.out, 'bld_meta.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print('완료')
