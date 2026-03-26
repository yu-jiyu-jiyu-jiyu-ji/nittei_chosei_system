from __future__ import annotations

from typing import Dict, List, Tuple

from config.constants import (
    CONSTRUCTION_TYPE_OPTIONS,
    CONSTRUCTION_TYPE_OTHER,
    MAX_REQUIRED_VEHICLES,
    MAX_REQUIRED_WORKERS,
    MAX_WORK_DURATION_MINUTES,
    MIN_WORK_DURATION_MINUTES,
)


def validate_project_input(form_values: Dict[str, object]) -> Tuple[bool, List[str]]:
    """案件登録入力のバリデーション.

    要件・DB設計資料に基づく最低限のチェックのみ実装。
    """
    errors: List[str] = []

    project_name = (form_values.get("project_name") or "").strip()
    customer_name = (form_values.get("customer_name") or "").strip()
    address = (form_values.get("address") or "").strip()
    construction_type_raw = form_values.get("construction_type")
    if isinstance(construction_type_raw, list):
        construction_type = [str(x).strip() for x in construction_type_raw if x]
    else:
        construction_type = [str(construction_type_raw).strip()] if construction_type_raw else []
    construction_type_other = (form_values.get("construction_type_other") or "").strip()
    work_duration = form_values.get("work_duration_minutes")
    required_workers = form_values.get("required_workers")
    required_vehicle_count = form_values.get("required_vehicle_count")

    # 必須チェック
    if not project_name:
        errors.append("案件名は必須です。")
    if not customer_name:
        errors.append("顧客名は必須です。")
    if not address:
        errors.append("住所は必須です。")
    if not construction_type:
        errors.append("施工内容は1つ以上選択してください。")
    elif any(ct not in CONSTRUCTION_TYPE_OPTIONS for ct in construction_type):
        errors.append("施工内容に不正な値が含まれています。")
    elif CONSTRUCTION_TYPE_OTHER in construction_type and not construction_type_other:
        errors.append("施工内容が「その他」の場合、施工内容詳細は必須です。")

    # 作業時間
    try:
        work_duration_int = int(work_duration) if work_duration is not None else 0
    except (TypeError, ValueError):
        work_duration_int = 0
    if work_duration_int <= 0:
        errors.append("作業時間（分）は必須です。")
    elif work_duration_int < MIN_WORK_DURATION_MINUTES:
        errors.append("作業時間（分）は最小60分以上で入力してください。")
    elif work_duration_int > MAX_WORK_DURATION_MINUTES:
        errors.append("作業時間（分）は最大480分以内で入力してください。")

    # 必要人数
    try:
        required_workers_int = int(required_workers) if required_workers is not None else 0
    except (TypeError, ValueError):
        required_workers_int = 0
    if required_workers_int <= 0:
        errors.append("必要人数は1人以上で入力してください。")
    elif required_workers_int > MAX_REQUIRED_WORKERS:
        errors.append(f"必要人数は最大{MAX_REQUIRED_WORKERS}人までです。")

    # 必要車両数（任意入力だが、入力された場合のみ上限チェック）
    if required_vehicle_count is not None:
        try:
            vehicle_count_int = int(required_vehicle_count)
        except (TypeError, ValueError):
            vehicle_count_int = -1
        if vehicle_count_int < 0:
            errors.append("必要車両数は0以上の整数で入力してください。")
        elif vehicle_count_int > MAX_REQUIRED_VEHICLES:
            errors.append(f"必要車両数は最大{MAX_REQUIRED_VEHICLES}台までです。")

    is_valid = len(errors) == 0
    return is_valid, errors

