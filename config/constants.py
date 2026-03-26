APP_TITLE = "日程調整システム"

APP_DESCRIPTION = (
    "リフォーム案件に対して、必要な職人・車両・時間を考慮した日程調整を行うシステムです。"
    " 現在は Phase1（画面 + ダミーデータ）として、案件登録・案件一覧のみ動作します。"
)

# 入力バリデーション関連（要件より）
MIN_WORK_DURATION_MINUTES = 60  # 最小1時間
MAX_WORK_DURATION_MINUTES = 480  # 最大8時間

MAX_REQUIRED_WORKERS = 9
MAX_REQUIRED_VEHICLES = 3

# 施工内容の選択肢
CONSTRUCTION_TYPE_OPTIONS = [
    "ガラス交換",
    "内窓",
    "窓交換",
    "ドア交換",
    "網戸",
    "その他",
]
CONSTRUCTION_TYPE_OTHER = "その他"

