"""Firestore マスタ読み込みの Streamlit キャッシュ無効化."""

from __future__ import annotations


def invalidate_master_data_caches() -> None:
    """案件・職人・車両・共通設定の @st.cache_data をクリアする."""
    from services.project_service import _list_all_projects_cached
    from services.setting_service import _get_settings_cached
    from services.vehicle_service import _list_vehicles_cached
    from services.worker_service import _list_workers_cached

    _list_all_projects_cached.clear()
    _list_workers_cached.clear()
    _list_vehicles_cached.clear()
    _get_settings_cached.clear()
