"""Decision uses execution-time fields only; never the outcome label."""
from datetime import datetime
import math

VERSION = 'freshness-v1'
THRESHOLD = 15
WORKSHOPS = ('一号制氢车间', '二号装配车间', '三号动力车间', '四号综合测试区')
PILOT = ('一号制氢车间', '三号动力车间', '四号综合测试区')


def timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('时间不能为空')
    try:
        result = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('时间格式错误，请使用 ISO 8601') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('时间必须包含时区，例如 +08:00 或 Z')
    return result

def age_minutes(record):
    age = (timestamp(record.get('execute_time')) - timestamp(record.get('last_telemetry_sync'))).total_seconds() / 60
    if age < 0:
        raise ValueError('最后同步时间晚于执行时间，请核对时钟与时区')
    return age


def decide(record):
    result = {'decision': 'invalid', 'age_minutes': None, 'reasons': [],
              'next_steps': [], 'rule_version': VERSION}
    try:
        task_id = record.get('task_id')
        if isinstance(task_id, bool) or not isinstance(task_id, (str, int)) or not str(task_id).strip():
            raise ValueError('缺少工单编号')
        age = age_minutes(record)
        result['age_minutes'] = age
        if record.get('workshop') not in WORKSHOPS:
            raise ValueError('车间不在本次验证范围，请人工核对')
        # These are data validity checks, not equipment safety thresholds.
        for name in ('battery_level', 'ambient_temp'):
            value = float(record.get(name, ''))
            if not math.isfinite(value):
                raise ValueError('电量或温度不是有限数值')
            if name == 'battery_level' and not 0 <= value <= 100:
                raise ValueError('电量应在 0–100% 范围内')
    except (ValueError, TypeError, OverflowError) as exc:
        result['reasons'] = [str(exc) or '输入数据不完整']
        result['next_steps'] = ['核对时间、时区与现场数据后重新检查；按现有现场规程处理。']
        return result
    if age > THRESHOLD and record['workshop'] in PILOT:
        result.update(decision='review', reasons=[f'设备状态已 {age:g} 分钟未成功更新，超过 {THRESHOLD} 分钟复核阈值。'],
                      next_steps=['执行前重新拉取设备状态，核对设备与工单对应关系。',
                                  '同步成功后以新的执行时间和同步时间重新检查；仍异常则人工核对并记录原因。',
                                  '本原型未连接现场系统，不会远程刷新或控制设备。'])
    else:
        reason = '未触发本次时效复核规则，请继续既有巡检流程。'
        if record['workshop'] not in PILOT:
            reason = '该车间暂不纳入提示试点，记录时效并继续既有巡检流程。'
        result.update(decision='continue', reasons=[reason], next_steps=['本检查只判断数据时效提示，不代表设备安全或作业必定成功。'])
    return result
