"""車両割当ルール（資材あり前提・4人乗りは2+3満員後のみ）."""

from __future__ import annotations

from itertools import product
from typing import Any, Dict, List, Optional


def assign_vehicles_for_crew(
    required_people: int,
    vehicles: List[Dict[str, Any]],
) -> Optional[List[str]]:
    """必要人数を満たす車両IDのリスト。不可能なら None.

    - 定員 2 / 3 / 4 の車をマスタから1台ずつ選ぶ（同定員が複数ある場合は先頭）。
    - 4人乗りは、存在する 2人乗り・3人乗りをそれぞれ定員いっぱいに使った後にのみ使用。
    - 2人乗りも3人乗りも無い場合は不可（資材あり案件前提）。
    """
    if required_people <= 0:
        return []

    pool = [
        v
        for v in vehicles
        if v.get("is_active", True)
        and str(v.get("status", "available")) == "available"
    ]
    by_cap: Dict[int, List[Dict[str, Any]]] = {}
    for v in pool:
        try:
            c = int(v.get("capacity", 0))
        except (TypeError, ValueError):
            continue
        if c in (2, 3, 4):
            by_cap.setdefault(c, []).append(v)

    def first(cap: int) -> Optional[Dict[str, Any]]:
        lst = by_cap.get(cap, [])
        return lst[0] if lst else None

    v2 = first(2)
    v3 = first(3)
    v4 = first(4)

    p2m = 2 if v2 else 0
    p3m = 3 if v3 else 0
    p4m = 4 if v4 else 0

    if required_people > 0 and p2m == 0 and p3m == 0:
        return None

    for p2 in range(0, p2m + 1):
        for p3 in range(0, p3m + 1):
            rem = required_people - p2 - p3
            if rem < 0:
                continue
            ids: List[str] = []
            if p2 > 0:
                if not v2:
                    continue
                ids.append(str(v2["vehicle_id"]))
            if p3 > 0:
                if not v3:
                    continue
                ids.append(str(v3["vehicle_id"]))
            if rem == 0:
                if p2 + p3 > 0:
                    return ids
                continue
            if rem > p4m or not v4:
                continue
            need_four = True
            if v2 and p2 < p2m:
                need_four = False
            if v3 and p3 < p3m:
                need_four = False
            if not need_four:
                continue
            ids.append(str(v4["vehicle_id"]))
            return ids
    return None


def assign_vehicle_options_for_crew(
    required_people: int,
    vehicles: List[Dict[str, Any]],
    *,
    max_options: int = 64,
) -> List[List[str]]:
    """必要人数を満たしうる車両割当候補を複数返す（先頭順依存を避ける）。"""
    if required_people <= 0:
        return [[]]

    pool = [
        v
        for v in vehicles
        if v.get("is_active", True)
        and str(v.get("status", "available")) == "available"
    ]
    by_cap: Dict[int, List[Dict[str, Any]]] = {}
    for v in pool:
        try:
            c = int(v.get("capacity", 0))
        except (TypeError, ValueError):
            continue
        if c in (2, 3, 4):
            by_cap.setdefault(c, []).append(v)

    list2 = by_cap.get(2, [])
    list3 = by_cap.get(3, [])
    list4 = by_cap.get(4, [])
    if required_people > 0 and not list2 and not list3:
        return []

    options: List[List[str]] = []
    seen: set[tuple[str, ...]] = set()

    # 旧ロジック（2/3を優先して満員活用、必要時のみ4）を維持しつつ、
    # 同定員の複数車両を候補として列挙する。
    for v2, v3, v4 in product([None] + list2, [None] + list3, [None] + list4):
        p2m = 2 if v2 else 0
        p3m = 3 if v3 else 0
        p4m = 4 if v4 else 0

        for p2 in range(0, p2m + 1):
            for p3 in range(0, p3m + 1):
                rem = required_people - p2 - p3
                if rem < 0:
                    continue
                ids: List[str] = []
                if p2 > 0:
                    if not v2:
                        continue
                    ids.append(str(v2["vehicle_id"]))
                if p3 > 0:
                    if not v3:
                        continue
                    ids.append(str(v3["vehicle_id"]))
                if rem == 0:
                    if p2 + p3 > 0:
                        key = tuple(ids)
                        if key not in seen:
                            seen.add(key)
                            options.append(ids)
                    continue
                if rem > p4m or not v4:
                    continue
                need_four = True
                if v2 and p2 < p2m:
                    need_four = False
                if v3 and p3 < p3m:
                    need_four = False
                if not need_four:
                    continue
                ids4 = ids + [str(v4["vehicle_id"])]
                key4 = tuple(ids4)
                if key4 not in seen:
                    seen.add(key4)
                    options.append(ids4)

    options.sort(key=lambda ids: (len(ids), ids))
    return options[:max_options]
