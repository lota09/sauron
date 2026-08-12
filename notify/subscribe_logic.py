# -*- coding: utf-8 -*-
"""
notify/subscribe_logic.py — 구독 UI/역할 계산의 순수 로직(디스코드 무관, 오프라인 테스트 대상).

Discord Select 25개 한계 때문에 단과대(college)로 그룹핑하고, 한 단과대의 선택 결과만
부분 갱신한다. 역할 부여/회수는 '그 단과대 학과 집합' 안에서만 diff를 낸다.
"""
from collections import OrderedDict


def group_by_college(depts):
    """depts(list of dict) → OrderedDict{college: [dept,...]}. college 없으면 '기타'."""
    groups = OrderedDict()
    for d in depts:
        col = (d.get("college") or "").strip() or "기타"
        groups.setdefault(col, []).append(d)
    return groups


def dept_select_options(depts_in_college, subscribed_ids, max_options=25):
    """단과대 내 학과들을 Select 옵션 형태로. 현재 구독분은 default=True.
    반환: [{'label','value','default'}], (초과분은 잘라내고) 잘린 수."""
    subs = set(subscribed_ids)
    opts = []
    for d in depts_in_college[:max_options]:
        name = d.get("name_ko") or d["dept_id"]
        opts.append({"label": name[:100], "value": d["dept_id"], "default": d["dept_id"] in subs})
    dropped = max(0, len(depts_in_college) - max_options)
    return opts, dropped


def diff_for_subset(subset_dept_ids, selected_ids, current_ids):
    """한 단과대(subset) 안에서만 구독 변경 계산.
    subset 안에서 선택된 건 구독, 나머지는 해제. subset 밖은 건드리지 않음.
    반환: {'add':[dept_id], 'remove':[dept_id]}"""
    subset = set(subset_dept_ids)
    selected = set(selected_ids) & subset
    current = set(current_ids) & subset
    return {"add": sorted(selected - current), "remove": sorted(current - selected)}
