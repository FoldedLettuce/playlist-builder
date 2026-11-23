"""Builds data/sample-rows.json from the real Current_Trailers.xlsx (real DCP names/dates, placeholder runtimes)."""
import openpyxl, re, json, hashlib, datetime, sys
src = sys.argv[1]
wb = openpyxl.load_workbook(src)
cd = wb['Currently Downloaded']; pr = wb['Printout']
print_titles = []
for row in pr.iter_rows(min_row=2, max_row=29):
    for col in (1,5):
        v = row[col-1].value
        if v and 'TLR' in str(v): print_titles.append(str(v).strip())
def est(name, lo=112, hi=162):
    h = int(hashlib.md5(name.encode()).hexdigest()[:6],16)
    return round(lo + (h % ((hi-lo)*10))/10, 3)
rows = []
def row(**k):
    base = dict(role='',format='',title='',version='',rating='',release_date='',start_date='',end_date='',cpl_name='',runtime_sec='',ffec_sec='',only_for_title='',notes='SAMPLE runtime (placeholder)')
    base.update(k); rows.append(base)
for r in range(2, cd.max_row+1):
    b=cd.cell(r,2).value; d=cd.cell(r,4).value
    if not b or not d: continue
    d=str(d).strip()
    if d not in print_titles: continue
    m=re.search(r'_([FS])_', b)
    if not m: continue
    mt=re.match(r'^(.*?)\s+(TLR\S*)$', d)
    title, ver = mt.group(1), mt.group(2)
    rel=cd.cell(r,5).value
    row(role='TRAILER', format='FLAT' if m.group(1)=='F' else 'SCOPE', title=title, version=ver, rating=cd.cell(r,6).value or '',
        release_date=rel.strftime('%Y-%m-%d') if rel else '', cpl_name=b, runtime_sec=est(b))
feat = [
 dict(title='The Hunger Games: Sunrise on the Reaping', rating='PG-13', format='SCOPE', rd='2026-11-20', rt=9300,
      cpl='SunriseReaping_FTR_S_EN-EN-CCAP_US-PG13_51-HI-VI_4K_LION_20261028_DLX_SMPTE_OV'),
 dict(title='Hexed', rating='PG', format='FLAT', rd='2026-11-25', rt=5700,
      cpl='Hexed-wshr_FTR-2D_F_EN-EN-CCAP_US-PG_51-HI-VI_2K_WDAS_20261029_WDS_SMPTE_OV'),
 dict(title='The Cat in the Hat', rating='PG', format='F-200', rd='2026-11-06', rt=5100,
      cpl='CatInTheHat-wShort_FTR-2-2D_F-200_EN-EN-CCAP_US_51-HI-VI_2K_WR_20261021_EKN_SMPTE_OV'),
]
for f in feat:
    row(role='FEATURE', format=f['format'], title=f['title'], rating=f['rating'], release_date=f['rd'], cpl_name=f['cpl'],
        runtime_sec=f['rt'], ffec_sec=f['rt']-440, notes='SAMPLE runtime + FFEC (placeholder)')
def sv(week, kind, rat, tag):
    s = datetime.date(int(week[:4]),int(week[4:6]),int(week[6:])); e = s+datetime.timedelta(days=6)
    base = {'early':{'G':400,'PG':410,'PG-13':422,'R':430}, 'late':{'G':300,'PG':305,'PG-13':314,'R':320}}[kind][rat]
    row(role='SV_EARLY' if kind=='early' else 'SV_LATE', format='SCOPE', rating=rat, start_date=s.isoformat(), end_date=e.isoformat(),
        cpl_name=f'Fb-SV {kind}-{tag} {week}', runtime_sec=base)
for wk in ('20261116','20261123'):
    for rat,tag in (('G','G'),('PG','PG'),('PG-13','PG13'),('R','R')):
        sv(wk,'early',rat,tag); sv(wk,'late',rat,tag)
for fmt,c in (('SCOPE','S'),('FLAT','F')):
    row(role='COKE', format=fmt, rating='ALL', start_date='2026-10-30', end_date='2026-12-03', cpl_name=f'KO60-FLT11-GEN_ADV_{c}_EN-XX_US_51_2K_KO_20261019_CC_IOP_OV', runtime_sec=61, notes='GEN version. SAMPLE runtime')
for fmt,name,s,e in (('FLAT','TRL.GMC.0726.146_chvyhrtbeatv2_flat','2026-11-20','2026-11-25'),
                     ('SCOPE','TRL.GMC.0726.147_chvyhrtbeatv2_scope','2026-11-20','2026-11-25'),
                     ('FLAT','TRL.GMC.1126.477_chvyhol60_flat','2026-11-26','2026-12-31'),
                     ('SCOPE','TRL.GMC.1126.478_chvyhol60_scope','2026-11-26','2026-12-31')):
    row(role='CHEVY', format=fmt, rating='ALL', start_date=s, end_date=e, cpl_name=name, runtime_sec=60, notes='SAMPLE runtime')
for fmt,name in (('SCOPE','DeSimone_PreShow_2024_scope_dcp'),('FLAT','DeSimone_PreShow_2024_flat_dcp')):
    row(role='PRESHOW', format=fmt, rating='ALL', cpl_name=name, runtime_sec=31)
for rat in ('G','PG','PG-13','R'):
    for fmt,c in (('SCOPE','S'),('FLAT','F')):
        row(role='RATING_CARD', format=fmt, rating=rat, cpl_name=f'{rat}_RTG_{c}_EN-XX_2K_20251001', runtime_sec=6)
json.dump(rows, open('data/sample-rows.json','w'), indent=1)
print(len(rows), 'rows;', sum(1 for r in rows if r['role']=='TRAILER'), 'trailer rows')
print(sorted(set(r['title'] for r in rows if r['role']=='TRAILER')))
