"""Reproducible retrospective replay. Python 3.10+, standard library only."""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import timezone, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.policy import age_minutes, decide, timestamp, VERSION, THRESHOLD, PILOT, WORKSHOPS

FIELDS = ['task_id', 'execute_time', 'last_telemetry_sync', 'workshop', 'device_type', 'battery_level', 'ambient_temp', 'task_status']
TZ = timezone(timedelta(hours=8))
CANDIDATES = (5, 10, 15, 20, 30, 45, 60)


def load(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != FIELDS:
            raise ValueError('CSV 列名或顺序与题目不一致')
        raw = list(reader)
    if not raw:
        raise ValueError('CSV 没有数据记录')
    unique, duplicates = {}, []
    for line, row in enumerate(raw, 2):
        if None in row or None in row.values():
            raise ValueError(f'CSV 第 {line} 行列数错误')
        key = row['task_id']
        if key in unique:
            if row != unique[key]:
                raise ValueError(f'同一 task_id 存在冲突记录：{key}，停止评估')
            duplicates.append({'source_line': line, 'task_id': key})
            continue
        if row['task_status'] not in ('', '0', '1'):
            raise ValueError(f'未知结果值：{key}')
        decision = decide(row)
        if decision['decision'] == 'invalid':
            raise ValueError(f'工单 {key} 输入异常：{decision["reasons"]}')
        unique[key] = row
    records = []
    for row in unique.values():
        records.append({**row, 'age_minutes': age_minutes(row),
                        'date': timestamp(row['execute_time']).astimezone(TZ).date().isoformat(),
                        'failure': None if row['task_status'] == '' else row['task_status'] == '0'})
    records.sort(key=lambda r: (timestamp(r['execute_time']), r['task_id']))
    return raw, records, duplicates


def metric(records, predicate):
    known = [r for r in records if r['failure'] is not None]
    tp = sum(bool(predicate(r)) and r['failure'] for r in known)
    fp = sum(bool(predicate(r)) and not r['failure'] for r in known)
    fn = sum(not predicate(r) and r['failure'] for r in known)
    tn = len(known) - tp - fp - fn
    alerts, failures = tp + fp, tp + fn
    unknown = [r for r in records if r['failure'] is None]
    ua = sum(bool(predicate(r)) for r in unknown)
    return {'n': len(known), 'failures': failures, 'alerts': alerts, 'tp': tp, 'fp': fp,
            'fn': fn, 'tn': tn, 'precision': tp / alerts if alerts else None,
            'recall': tp / failures if failures else None,
            'alert_rate': alerts / len(known) if known else None,
            'baseline_minutes': failures * 90,
            'break_even_effectiveness': 10 * alerts / (90 * tp) if tp else None,
            'net_minutes_q50': 90 * tp * .5 - 10 * alerts,
            'net_minutes_q100_upper': 90 * tp - 10 * alerts,
            'unknown_status': len(unknown), 'unknown_alerts': ua,
            'all_alerts_including_unknown': alerts + ua,
            'net_minutes_q50_unknown_all_success': 90 * tp * .5 - 10 * (alerts + ua),
            'break_even_including_unknown_cost': 10 * (alerts + ua) / (90 * tp) if tp else None}


def group_rates(rows, key):
    output=[]
    for value in sorted({r[key] for r in rows}):
        subset=[r for r in rows if r[key]==value]
        output.append({'group':value, 'all':metric(subset, lambda r:True),
                       'stale':metric(subset, lambda r:r['age_minutes']>THRESHOLD),
                       'fresh':metric(subset, lambda r:r['age_minutes']<=THRESHOLD)})
    return output


def run(source, output):
    raw, records, duplicates = load(source)
    train=[r for r in records if r['date'] <= '2026-09-04']
    replay=[r for r in records if r['date'] >= '2026-09-05']
    # Candidate thresholds fixed before this selection; tune on development only.
    candidate_scores=[{'threshold': t, **metric(train, lambda r,t=t:r['age_minutes']>t)} for t in CANDIDATES]
    selected=max(candidate_scores, key=lambda m:(m['net_minutes_q50'], -m['alerts']))['threshold']
    workshop_selection=[]
    for workshop in WORKSHOPS:
        m=metric([r for r in train if r['workshop']==workshop], lambda r:r['age_minutes']>selected)
        workshop_selection.append({'workshop':workshop, 'include_in_pilot':m['net_minutes_q50']>0, **m})
    selected_workshops=tuple(m['workshop'] for m in workshop_selection if m['include_in_pilot'])
    if selected != THRESHOLD or selected_workshops != PILOT:
        raise ValueError('开发集选择与冻结的产品规则不一致，需要人工审阅，不自动改生产规则')
    rules={
        'no_alert':lambda r:False,
        'all_alert':lambda r:True,
        'battery_le20':lambda r:float(r['battery_level'])<=20,
        'temperature_ge60':lambda r:float(r['ambient_temp'])>=60,
        'stale_all_workshops':lambda r:r['age_minutes']>THRESHOLD,
        'pilot_policy':lambda r:decide(r)['decision']=='review',
    }
    evaluations={split:{name:metric(part,fn) for name,fn in rules.items()}
                 for split,part in [('development',train),('replay',replay),('all',records)]}
    known=[r for r in records if r['failure'] is not None]
    age_groups=[]
    for lower,upper in [(0,5),(5,10),(10,15),(15,30),(30,46),(46,66)]:
        part=[r for r in known if lower<=r['age_minutes']<upper]
        age_groups.append({'label':f'[{lower}, {upper})','n':len(part),'failures':sum(r['failure'] for r in part)})
    examples=[]
    for predicate in [lambda r:decide(r)['decision']=='review',
                      lambda r:r['age_minutes']<=THRESHOLD,
                      lambda r:r['workshop']=='二号装配车间' and r['age_minutes']>THRESHOLD]:
        row=next(r for r in replay if predicate(r))
        examples.append({k:row[k] for k in FIELDS if k!='task_status'})
    policy={'description':'一号、三号、四号车间：状态超过 15 分钟未成功更新，执行前提示数据复核；二号先影子观察。',
            'threshold_minutes':THRESHOLD,'comparison':'>','rule_version':VERSION,
            'pilot_workshops':list(PILOT),'excluded_workshops':['二号装配车间']}
    summary={'raw_rows':len(raw),'unique_tasks':len(records),'labeled_tasks':len(known),
             'failures':sum(r['failure'] for r in known),'missing_status':len(records)-len(known),
             'exact_duplicates':len(duplicates),'failure_rate':sum(r['failure'] for r in known)/len(known)}
    report={'summary':summary, 'policy':policy, 'evaluation':evaluations['replay']['pilot_policy'],
            'evaluations':evaluations, 'examples':examples,
            'source':{'file':'data/inspection_tasks.csv','sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                      'start':records[0]['execute_time'],'end':records[-1]['execute_time']},
            'method':{'development':'2026-09-01 至 2026-09-04（+08:00）','replay':'2026-09-05 至 2026-09-07（+08:00）',
                      'q_selection_assumption':.5,'failure_minutes':90,'review_minutes':10,
                      'claim':'探索性历史回放，非盲测、非因果验证；有效率 q 未观测。'},
            'threshold_selection':candidate_scores, 'workshop_selection':workshop_selection,
            'by_workshop':group_rates(records,'workshop'), 'by_device_type':group_rates(records,'device_type'),
            'by_day':group_rates(records,'date'), 'age_bins':age_groups,
            'quality':{'raw_status_counts':dict(Counter(r['task_status'] for r in raw)),
                       'conflicting_ids':0,'age_min':min(r['age_minutes'] for r in records),
                       'age_max':max(r['age_minutes'] for r in records),
                       'observed_age_minutes':sorted({r['age_minutes'] for r in records})}}
    output.mkdir(parents=True,exist_ok=True)
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (output/'task_decisions.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=FIELDS+['age_minutes','split','decision','reason']
        writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');writer.writeheader()
        for r in records:
            d=decide(r)
            writer.writerow({**{k:r[k] for k in FIELDS},'age_minutes':r['age_minutes'],
                             'split':'development' if r['date']<='2026-09-04' else 'replay',
                             'decision':d['decision'],'reason':' '.join(d['reasons'])})
    with (output/'duplicate_rows.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['source_line','task_id'],lineterminator='\n');writer.writeheader();writer.writerows(duplicates)
    print(json.dumps({'summary':summary,'replay':report['evaluation'],'source_sha256':report['source']['sha256']},ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'data/inspection_tasks.csv')
    parser.add_argument('--out',type=Path,default=ROOT/'results')
    args=parser.parse_args()
    run(args.input,args.out)
