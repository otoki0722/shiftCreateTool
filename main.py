# -*- coding: utf-8 -*-
"""
手動シフト作成ツール MVP（PySide6）

■ 目的
- 自動生成なし。手動入力に特化。
- 希望休入力を直感的に（専用モードでトグル）
- 見た目はQtベースで改善（ダーク系、行間/余白広め）
- 期間は「月」「前半(1-15)」「後半(16-末)」を選択
- クリックで「- → 出 → 休 → -」をトグル
- 列ヘッダにその日の『休』人数を常時表示（編集のたびに更新）
- チェックボタンでルール検証し、結果を右ペインに一覧表示
- 月跨ぎ3連休の有休チェックのために、前期間の末4日の勤務データを JSON に保存/参照

■ ステータス仕様
- "-": 未設定
- "出": 出勤
- "休": 休み（右クリックで「有休」フラグを付与できる: 表示は "休*"）

■ 希望休
- 上部の「希望休モード」トグルで切替。モード中のクリックは『希望休(×)』をON/OFF。
- 希望休は別レイヤ。チェック時に利用。

■ ルール（初期実装）
- 《3連休の時には1日有休になっているか》: 連続3つの休のブロックに少なくとも1日が「休*」であること（前期間の末4日を考慮して判定）
- 《管理職(迫/田嶋/齋藤/田中)のうち2人は同日出勤》: 列ごとに管理職の『出』人数>=2
- 《土日祝の休みは出来るだけ平等》: ここでは土日のみ対象（祝日は未対応）。各スタッフの土日『休』数を集計し、平均からの乖離が大きい人を警告。
- 《入職1ヶ月以内の新人は極力、土日祝や繁忙日は勤務を入れない》: ここでは土日の『出』を検出し警告（祝日/繁忙日は未対応）。
- 《勤務希望を1日も出していない人に4勤はつけない》: 希望休(×)が0日のスタッフに対し、4連勤(『出』x4)を検出し警告。
- 《リーダーは役職者がつける》: 日毎のリーダーをコンボで選択。役職者以外が設定されていれば警告。
- 《リーダー間隔・回数の平等》: 各人のリーダー回数を集計し、平均からの乖離が大きい人を警告。

■ データ保存
- ./data/staffs.json         : スタッフ基本情報（氏名/役職/入職日 等）
- ./data/last_tail.json      : 前期間の末4日ぶんの勤務（各人の末4セル: "-"/"出"/"休"/"休*"）
- ./data/schedule_YYYYMM_H.json : 当期間のシフト（H=1:前半, 2:後半）。希望休は同ファイル内に保持。

■ 動作に必要なパッケージ
- PySide6

    pip install PySide6

"""

import json
import os
from datetime import date, datetime, timedelta
from typing import List, Dict, Tuple

from PySide6.QtGui import QAction, QFont, QColor, QBrush, QPen
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSize, QPropertyAnimation, QEasingCurve, QSignalBlocker, QTimer, QEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QTableView, QSplitter, QTextEdit, QMessageBox,
    QCheckBox, QHeaderView, QMenu, QToolButton, QListView,
    QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QDateEdit, QSpinBox, QLineEdit, QStyledItemDelegate, QAbstractScrollArea
)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
MEMBERS_JSON        = os.path.join(DATA_DIR, 'members.json')         # メンバー管理
STAFFS_JSON         = MEMBERS_JSON
LAST_TAIL_JSON      = os.path.join(DATA_DIR, 'last_tail.json')
VACATIONS_JSON      = os.path.join(DATA_DIR, 'long_vacations.json')  # 長期休暇（期間の集合）
WEEKDAY_RULES_JSON  = os.path.join(DATA_DIR, 'weekday_rules.json')   # 曜日ごとの設定（基本）
SPECIAL_QUOTAS_JSON  = os.path.join(DATA_DIR, 'special_quota.json')   # 個別出勤数（GW・年末年始・お盆など）
# 祝日を手入力／インポートで管理（例: {"holidays": ["2025-01-01","2025-02-11", ...] }）
HOLIDAYS_JSON = os.path.join(DATA_DIR, "holidays.json")

STATUS_ORDER        = [" ", "休"]

# 役職者（固定名）
MANAGERS            = ["迫", "田嶋", "齋藤", "田中"]

DARK_STYLESHEET = """
QWidget { background-color: #101214; color: #E6E6E6; font-family: 'Meiryo UI','Segoe UI',sans-serif; }
QLabel { color: #DADCE0; }
QTableView { gridline-color: #2A2F36; alternate-background-color: #15181C; selection-background-color: #2E3A46; border: 1px solid #2A2F36; }
QHeaderView::section { background-color: #1B1F24; color: #E6E6E6; padding: 6px; border: 0px; border-bottom: 1px solid #2A2F36; border-right: 1px solid #2A2F36; font-weight: 600; }
QPushButton { background-color: #1E2530; color: #E6E6E6; border: 1px solid #2A2F36; border-radius: 10px; padding: 8px 14px; }
QPushButton:hover { background-color: #263042; }
QPushButton:pressed { background-color: #1C2632; }
QTextEdit { background-color: #0F1114; border: 1px solid #2A2F36; border-radius: 10px; }
QComboBox { background-color: #0F1114; border: 1px solid #2A2F36; padding: 6px; border-radius: 8px; }
"""

DARK_STYLESHEET += """
#SideMenu { background-color: #121418; border-right: 1px solid #2A2F36; }
#SideMenu QPushButton { text-align: left; padding: 10px 14px; border-radius: 8px; }
#SideMenu QPushButton:hover { background-color: #1E2530; }
#Hamburger { font-size: 20px; font-weight: 700; padding: 2px 10px; }
"""


# ---------- ユーティリティ ----------

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def month_last_day(year: int, month: int) -> int:
    if month == 12:
        return 31
    first_next = date(year + (month // 12), (month % 12) + 1, 1)
    last = first_next - timedelta(days=1)
    return last.day


def is_weekend(y: int, m: int, d: int) -> bool:
    return date(y, m, d).weekday() >= 5  # 5:土,6:日

def ensure_file_with_template(path: str, template_obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        save_json(path, template_obj)

def open_json_in_explorer(path: str, template_obj):
    ensure_file_with_template(path, template_obj)
    try:
        os.startfile(path)  # Windows想定
    except Exception:
        QMessageBox.information(None, "情報", f"設定ファイルを手動で開いて編集してください:\n{path}")

def build_vacation_map(year: int, month: int, days: list[int], vacations_obj: dict, staff_names: list[str]) -> dict[str, set]:
    """長期休暇データから {メンバー名: {日,...}} を作る"""
    vac_map: dict[str, set] = {name: set() for name in staff_names}
    if not vacations_obj:
        return vac_map
    vacs = vacations_obj.get("vacations", []) if isinstance(vacations_obj, dict) else []
    for v in vacs:
        member = v.get("member", "")
        if member not in vac_map:
            continue
        try:
            d0 = date.fromisoformat(v.get("start"))
            d1 = date.fromisoformat(v.get("end"))
        except Exception:
            continue
        if d1 < d0:
            d0, d1 = d1, d0
        for d in days:
            cur = date(year, month, d)
            if d0 <= cur <= d1:
                vac_map[member].add(d)
    return vac_map


# ---------- モデル ----------

class Staff:
    def __init__(self, name: str, is_manager: bool = False, hire_date: str = None):
        self.name = name
        self.is_manager = is_manager
        self.hire_date = datetime.strptime(hire_date, '%Y-%m-%d').date() if hire_date else None

    @staticmethod
    def from_dict(d: Dict):
        return Staff(
            name=d.get('name'),
            is_manager=d.get('is_manager', False),
            hire_date=d.get('hire_date')
        )

    def to_dict(self):
        return {
            'name': self.name,
            'is_manager': self.is_manager,
            'hire_date': self.hire_date.isoformat() if self.hire_date else None
        }

class WishPaidDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        # まず通常描画
        super().paint(painter, option, index)

        model = index.model()

        # --- このデリゲートは ShiftModel 専用。属性が無ければ何もしない ---
        need_attrs = ("staffs", "days", "wishes", "wish_paid")
        if any(not hasattr(model, a) for a in need_attrs):
            return

        row, col = index.row(), index.column()
        # 範囲チェック
        if not (0 <= row < len(model.staffs) and 0 <= col < len(model.days)):
            return

        staff = model.staffs[row].name
        day   = model.days[col]

        # 希望休かつ「有給希望」なら赤枠を描画
        is_wish = model.wishes.get(staff, {}).get(day, False)
        is_paid = model.wish_paid.get(staff, {}).get(day, False)
        if is_wish and is_paid:
            painter.save()
            pen = QPen(QColor(220, 50, 47))  # 赤
            pen.setWidth(2)
            painter.setPen(pen)
            rect = option.rect.adjusted(1, 1, -1, -1)
            painter.drawRect(rect)
            painter.restore()

class ShiftModel(QAbstractTableModel):
    """スタッフ×日付の表。セルには "-", "出", "休", 休に有休フラグ(*) を付ける。"""
    def __init__(self, staffs: List[Staff], days: List[int], year: int, month: int):
        super().__init__()
        self.staffs = staffs
        self.days = days
        self.year = year
        self.month = month
        # status[name][day] = "-"/"出"/"休"/"休*"
        self.status = {s.name: {d: " " for d in days} for s in staffs}
        # wishes[name][day] = bool（希望休×）
        self.wishes: Dict[str, Dict[int, bool]] = {s.name: {d: False for d in days} for s in staffs}
        self.wish_paid: dict[str, dict[int, bool]] = {s.name: {d: False for d in self.days} for s in self.staffs}
        # leaders[day] = name or ""
        self.leaders: Dict[int, str] = {d: "" for d in days}
        self.vac_days: dict[str, set] = {s.name: set() for s in staffs}
        self.req_min_work: dict[int, int] = {d: 0 for d in self.days}
        self.sat_days: set[int] = set()  # 土曜の「日」セット
        self.hol_days: set[int] = set()  # 日祝の「日」セット（※日曜もここに含める）
        self.paid_left: dict[str, int] = {s.name: 0 for s in self.staffs}
        self.weekend_days: set[int] = set()  # 土日セット（分母用）

    # --- Qt model size ---
    def rowCount(self, parent=QModelIndex()):
        return len(self.staffs)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(getattr(self, "days", []))

    # --- ヘッダ ---
    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if section < 0 or section >= len(self.days):
                return None
            day = self.days[section]
            if role == Qt.DisplayRole:
                # 曜日表記
                import calendar
                y = getattr(self, "year", None)
                m = getattr(self, "month", None)
                youbi = ""
                if isinstance(y, int) and isinstance(m, int):
                    # ShiftModel.headerData 内（曜日表記の直前あたり）
                    yy, mm = (y, m) if day >= 16 else ((y + 1, 1) if m == 12 else (y, m + 1))
                    wd = calendar.weekday(yy, mm, day)
                    wk = "月火水木金土日"[wd]
                    youbi = f"({wk})"

                rest = self.count_rest_on_day(day)
                need = int((getattr(self, "req_min_work", {}) or {}).get(day, 0))
                work = self.count_work_on_day(day)
                status = ""
                if need > 0:
                    status = "満" if work >= need else f"あと{need - work}人"
                # 3行表示：日付(曜) / 休:N / （満 or あとX人）
                return f"{day}{youbi}\n休:{rest}" + (f"\n{status}" if status else "")
            if role == Qt.TextAlignmentRole:
                return Qt.AlignCenter

        else:  # Qt.Vertical（スタッフ名の列）
            if role == Qt.DisplayRole:
                if 0 <= section < len(self.staffs):
                    name = self.staffs[section].name
                    paid = int((getattr(self, "paid_left", {}) or {}).get(name, 0))
                    num = self.weekend_rest_count(name)
                    den = self.weekend_denominator()
                    # 1行目：氏名（残有給を名前の横に）
                    line1 = f"{name} (残有給{paid})"
                    # 2行目：土日休暇数
                    line2 = f"土日出勤{num}/{den}" if den else "0/0"
                    return f"{line1}\n{line2}"
                return ""

            if role == Qt.TextAlignmentRole:
                return Qt.AlignVCenter | Qt.AlignLeft

        return super().headerData(section, orientation, role)

    # --- データ表示 ---
    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        staff = self.staffs[index.row()].name
        day = self.days[index.column()]
        s = self.status[staff][day]
        is_wish = self.wishes[staff][day]
        is_wish_paid = self.wish_paid.get(staff, {}).get(day, False)

        if role == Qt.DisplayRole:
            if is_wish:
                return "有" if is_wish_paid else ""  # ← 有給希望は「有」、通常の希望休は空文字
            return s

        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter

        if role == Qt.BackgroundRole:
            # ① 希望休（通常のみオレンジ、有給希望はデフォルト色）
            if is_wish and not is_wish_paid:
                return QBrush(QColor(255, 140, 0))
            # ② 長期休暇（水色帯）
            if day in self.vac_days.get(staff, set()):
                return QBrush(QColor(0, 170, 255, 80))
            # ③ 週末/祝日（高コントラスト版）
            if day in getattr(self, "hol_days", set()):
                return QBrush(QColor(255, 130, 150, 160))  # 日曜・祝日：明るめピンク
            if day in getattr(self, "sat_days", set()):
                return QBrush(QColor(70, 170, 255, 160))  # 土曜：明るめシアン

        return None

    # --- 編集（クリックでトグル） ---
    def toggle_status(self, row: int, col: int):
        staff = self.staffs[row].name
        day = self.days[col]

        # 希望休 or 長期休暇のセルは編集不可
        if self.wishes[staff][day] or day in self.vac_days.get(staff, set()):
            return

        cur = self.status[staff][day]
        base = "休" if cur == "休*" else cur  # 有休は一旦「休」に揃えてから循環

        # 想定外の値でもクラッシュしないようにガード
        try:
            idx = STATUS_ORDER.index(base)
        except ValueError:
            idx = 0  # 不明値は出勤扱い（" "）に戻す

        nxt = STATUS_ORDER[(idx + 1) % len(STATUS_ORDER)]
        self.status[staff][day] = nxt  # 有休フラグはリセットされる（"休*" → "休" → ...）

        self.dataChanged.emit(self.index(row, col), self.index(row, col))
        self.headerDataChanged.emit(Qt.Horizontal, col, col)
        self.headerDataChanged.emit(Qt.Vertical, row, row)

    def toggle_paid_flag(self, row: int, col: int):
        staff = self.staffs[row].name
        day = self.days[col]

        # 希望休 or 長期休暇のセルは編集不可
        if self.wishes[staff][day] or day in self.vac_days.get(staff, set()):
            return

        cur = self.status[staff][day]
        if cur == "休":
            self.status[staff][day] = "休*"
        elif cur == "休*":
            self.status[staff][day] = "休"
        else:
            return  # 出勤（" "）などは対象外

        # 反映
        self.dataChanged.emit(self.index(row, col), self.index(row, col))
        self.headerDataChanged.emit(Qt.Horizontal, col, col)
        self.headerDataChanged.emit(Qt.Vertical, row, row)  # ★ 追加

    def toggle_wish(self, row: int, col: int):
        staff = self.staffs[row].name
        day = self.days[col]
        self.wishes[staff][day] = not self.wishes[staff][day]
        self.dataChanged.emit(self.index(row, col), self.index(row, col))

    def toggle_wish_cycle(self, row: int, col: int):
        """希望休モード：未指定 → 希望休 → 有給希望 → 解除 の三段階"""
        staff = self.staffs[row].name
        day = self.days[col]

        # 長期休暇のセルは触らない
        if day in self.vac_days.get(staff, set()):
            return

        is_wish = self.wishes[staff][day]
        is_paid = self.wish_paid[staff][day]

        if not is_wish:
            # 未指定 → 希望休（オレンジ）
            self.wishes[staff][day] = True
            self.wish_paid[staff][day] = False
        elif is_wish and not is_paid:
            # 希望休 → 有給希望（「有」＋赤枠、背景デフォルト）
            self.wish_paid[staff][day] = True
        else:
            # 有給希望 → 解除
            self.wishes[staff][day] = False
            self.wish_paid[staff][day] = False

        # 反映
        self.dataChanged.emit(self.index(row, col), self.index(row, col))
        self.headerDataChanged.emit(Qt.Horizontal, col, col)

    def count_rest_on_day(self, day: int) -> int:
        cnt = 0
        for s in self.staffs:
            v = self.status[s.name][day]
            if v in ("休", "休*"):
                cnt += 1
        return cnt

    # --- JSON入出力 ---
    def to_json(self) -> Dict:
        return {
            'year': self.year,
            'month': self.month,
            'days': self.days,
            'status': self.status,
            'wishes': {k: {str(d): v for d, v in days.items()} for k, days in self.wishes.items()},
            'wish_paid': {k: {str(d): v for d, v in days.items()} for k, days in self.wish_paid.items()},  # ★追加
            'leaders': {str(d): name for d, name in self.leaders.items()}
        }

    def from_json(self, obj: Dict):
        self.beginResetModel()
        self.status = obj.get('status', self.status)
        w = obj.get('wishes', {})
        self.wishes = {name: {int(d): v for d, v in daymap.items()} for name, daymap in w.items()}
        wp = obj.get('wish_paid', {})
        self.wish_paid = {name: {int(d): v for d, v in daymap.items()} for name, daymap in wp.items()}
        self.leaders = {int(d): nm for d, nm in obj.get('leaders', {}).items()}
        self.endResetModel()

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        name = self.staffs[index.row()].name
        day  = self.days[index.column()]
        is_wish = self.wishes[name][day]
        is_vac  = day in self.vac_days.get(name, set())
        # 希望休 or 長期休暇のセルは非活性（選択のみ可）
        if is_wish or is_vac:
            return Qt.ItemIsSelectable
        # それ以外は通常（選択＋有効）
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def count_work_on_day(self, day: int) -> int:
        return sum(1 for s in self.staffs if self.status[s.name][day] == " ")

    def weekend_rest_count(self, name: str) -> int:
        # 分子：土日で「休 or 休*」の日数
        return sum(1 for d in self.weekend_days if self.status[name][d] in ("休", "休*"))

    def weekend_denominator(self) -> int:
        # 分母：期間内の土日の日数
        return len(self.weekend_days)

    # ✅ 追加：ShiftModel 内
    def resolve_day(self, base_year: int, base_month: int, token):
        """
        token が int      -> (base_year, base_month, token)
        token が "mm:dd" -> (y', mm, dd) ※12→1跨ぎは年も進める
        """
        if isinstance(token, int):
            return base_year, base_month, int(token)
        if isinstance(token, str) and ":" in token:
            m_s, d_s = token.split(":")
            m2, d2 = int(m_s), int(d_s)
            y2 = base_year + (1 if base_month == 12 and m2 == 1 else 0)
            return y2, m2, d2
        return base_year, base_month, int(token)

    # ShiftModel に追加
    def set_period(self, year: int, month: int, days: list):
        self.beginResetModel()
        self.year = year
        self.month = month
        self.days = list(days)
        self.endResetModel()

# ---------- ビュー（テーブル） ----------

class ShiftTable(QTableView):
    def __init__(self, model: ShiftModel, wish_mode_getter):
        super().__init__()
        self.setModel(model)
        self.wish_mode_getter = wish_mode_getter
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        # スクロール設定
        self.setHorizontalScrollMode(QTableView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.horizontalHeader().setDefaultAlignment(Qt.AlignHCenter | Qt.AlignVCenter)  # 追加
        self.horizontalHeader().setFixedHeight(56)  # 2行分確保
        f = QFont()
        f.setPointSize(12)
        self.setFont(f)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QTableView.NoSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.on_context)

    def sizeHint(self):
        return QSize(1100, 600)

    def mousePressEvent(self, event):
        idx = self.indexAt(event.position().toPoint())
        if idx.isValid():
            if self.wish_mode_getter():
                self.model().toggle_wish_cycle(idx.row(), idx.column())
            else:
                staff = self.model().staffs[idx.row()].name
                day = self.model().days[idx.column()]
                if not self.model().wishes[staff][day] and event.button() == Qt.LeftButton:
                    self.model().toggle_status(idx.row(), idx.column())
        super().mousePressEvent(event)

    def on_context(self, pos):
        idx = self.indexAt(pos)
        if not idx.isValid():
            return
        m = QMenu(self)
        act_paid = QAction("この日の休みに有休フラグを付ける/外す (右クリック機能)")
        act_paid.triggered.connect(lambda: self.model().toggle_paid_flag(idx.row(), idx.column()))
        m.addAction(act_paid)
        m.exec(self.viewport().mapToGlobal(pos))

# ---------- メンバー管理ダイアログ ----------

class MembersDialog(QDialog):
    """メンバー管理（氏名 / 管理職 / 入職日）を表で編集して保存"""
    def __init__(self, parent=None, members_path=None, staffs_path=None):
        super().__init__(parent)
        self.setWindowTitle("メンバー管理")
        self.resize(640, 420)
        self.members_path = members_path
        self.staffs_path = staffs_path

        v = QVBoxLayout(self)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["氏名", "役職(管理職)", "残有給"])
        self.table.verticalHeader().setVisible(False)
        vh = self.table.verticalHeader()
        vh.setDefaultSectionSize(32)  # 30〜36程度に
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)

        v.addWidget(self.table, 1)

        hb = QHBoxLayout()
        btn_add = QPushButton("行を追加")
        btn_del = QPushButton("選択行を削除")
        hb.addWidget(btn_add); hb.addWidget(btn_del); hb.addStretch(1)
        v.addLayout(hb)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        v.addWidget(self.buttons)

        btn_add.clicked.connect(self.add_row)
        btn_del.clicked.connect(self.del_rows)
        self.buttons.accepted.connect(self.save_and_close)
        self.buttons.rejected.connect(self.reject)

        # 初期ロード（テンプレ生成→読み込み）
        # MembersDialog.__init__ の template を簡素化
        template = {"members": [
            {"name": "迫", "is_manager": True},
            {"name": "田嶋", "is_manager": True},
            {"name": "齋藤", "is_manager": True},
            {"name": "田中", "is_manager": True},
        ]}
        ensure_file_with_template(self.members_path, template)
        obj = load_json(self.members_path, template)
        for m in obj.get("members", []):
            self.add_row(m.get("name",""), bool(m.get("is_manager", False)), int(m.get("paid_left", 0)))

    def add_row(self, name="", is_manager=False, paid_left=0):
        r = self.table.rowCount()
        self.table.insertRow(r)
        # 氏名
        self.table.setItem(r, 0, QTableWidgetItem(name))
        # 管理職チェック
        item_mgr = QTableWidgetItem()
        item_mgr.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        item_mgr.setCheckState(Qt.Checked if is_manager else Qt.Unchecked)
        self.table.setItem(r, 1, item_mgr)
        # 残有給（スピン）
        sp = QSpinBox(self);
        sp.setRange(0, 99);
        sp.setValue(int(paid_left or 0))
        self.table.setCellWidget(r, 2, sp)

    def del_rows(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    # 修正後
    def save_and_close(self):
        data = []
        for r in range(self.table.rowCount()):
            name = (self.table.item(r, 0).text().strip() if self.table.item(r, 0) else "")
            is_mgr = (self.table.item(r, 1).checkState() == Qt.Checked) if self.table.item(r, 1) else False
            sp = self.table.cellWidget(r, 2)
            paid_left = int(sp.value()) if sp else 0
            if name:
                data.append({"name": name, "is_manager": is_mgr, "paid_left": paid_left})
        save_json(self.members_path, {"members": data})
        save_json(self.staffs_path, data)
        self.accept()

# ---------- 長期休暇管理ダイアログ ----------
class LongVacationDialog(QDialog):
    """長期休暇管理（最大14日・有給は50%まで）"""
    def __init__(self, parent=None, vacations_path=None, member_names=None):
        super().__init__(parent)
        self.setWindowTitle("長期休暇管理")
        self.resize(760, 460)
        self.vacations_path = vacations_path
        self.member_names = member_names or []

        v = QVBoxLayout(self)
        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(["氏名", "休暇名", "開始日", "終了日", "日数", "有給上限", "有給使用"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.table, 1)

        hb = QHBoxLayout()
        btn_add = QPushButton("行を追加")
        btn_del = QPushButton("選択行を削除")
        hb.addWidget(btn_add)
        hb.addWidget(btn_del)
        hb.addStretch(1)
        v.addLayout(hb)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        v.addWidget(self.buttons)

        btn_add.clicked.connect(self.add_row)
        btn_del.clicked.connect(self.del_rows)
        self.buttons.accepted.connect(self.save_and_close)
        self.buttons.rejected.connect(self.reject)

        template = {"vacations": []}
        ensure_file_with_template(self.vacations_path, template)
        obj = load_json(self.vacations_path, template)
        for vobj in obj.get("vacations", []):
            self.add_row(
                staff=vobj.get("member", ""),
                title=vobj.get("name", ""),
                start=vobj.get("start"),
                end=vobj.get("end"),
                paid_used=int(vobj.get("paid_used", 0)),
            )

    def _mk_member_cb(self, selected=""):
        cb = QComboBox(self)
        cb.addItems(self.member_names)
        if selected:
            idx = cb.findText(selected)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        return cb

    def _mk_date(self, iso_str: str | None):
        de = QDateEdit(self); de.setCalendarPopup(True)
        if iso_str:
            try:
                y, m, d = map(int, iso_str.split("-"))
                de.setDate(date(y, m, d))
            except Exception:
                de.setDate(date.today())
        else:
            de.setDate(date.today())
        return de

    def add_row(self, staff="", title="", start=None, end=None, paid_used=0):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setCellWidget(r, 0, self._mk_member_cb(staff))
        self.table.setItem(r, 1, QTableWidgetItem(title))
        de_start = self._mk_date(start)
        de_end = self._mk_date(end)
        self.table.setCellWidget(r, 2, de_start)
        self.table.setCellWidget(r, 3, de_end)
        self.table.setItem(r, 4, QTableWidgetItem("0"))
        self.table.setItem(r, 5, QTableWidgetItem("0"))
        sp_paid = QSpinBox(self)
        sp_paid.setRange(0, 0)
        self.table.setCellWidget(r, 6, sp_paid)

        if not hasattr(self, "_lv_warn_lock"):
            self._lv_warn_lock = False

        def on_date_changed(show_warning: bool):
            d0 = de_start.date().toPython() if hasattr(de_start.date(), 'toPython') else date.fromisoformat(de_start.date().toString('yyyy-MM-dd'))
            d1 = de_end.date().toPython()   if hasattr(de_end.date(),   'toPython') else date.fromisoformat(de_end.date().toString('yyyy-MM-dd'))

            # 終了<開始は開始日に合わせる（無警告）
            if d1 < d0:
                with QSignalBlocker(de_end):
                    de_end.setDate(d0)
                d1 = d0

            days = (d1 - d0).days + 1
            if days > 14:
                if show_warning and not self._lv_warn_lock:
                    # ここで“この操作中は1回だけ”にロック
                    self._lv_warn_lock = True
                    QMessageBox.warning(self, "エラー", "長期休暇は最大14日までです。")
                    # 終了日を開始日+13に補正（=14日間）※再発火はブロック
                    with QSignalBlocker(de_end):
                        de_end.setDate(d0 + timedelta(days=13))
                    # すぐロック解除すると再入でまた鳴ることがあるので、イベントループ1周後に解除
                    QTimer.singleShot(0, lambda: setattr(self, "_lv_warn_lock", False))
                # days を14に固定
                days = 14

            paid_cap = days // 2
            self.table.item(r, 4).setText(str(days))
            self.table.item(r, 5).setText(str(paid_cap))
            sp_paid.setMaximum(paid_cap)
            if sp_paid.value() > paid_cap:
                sp_paid.setValue(paid_cap)

        def on_start_changed():
            # 開始日を動かしたときは無警告で補正・再計算のみ
            on_date_changed(show_warning=False)

        def on_end_changed():
            # 終了日を動かしたときだけ、上限超過で警告を出す
            on_date_changed(show_warning=True)

        # 接続を分ける（※ 以前の on_date_changed 接続は削除）
        de_start.userDateChanged.connect(on_start_changed)
        de_end.userDateChanged.connect(on_end_changed)

        # 初期計算
        on_date_changed(show_warning=False)
        sp_paid.setValue(min(paid_used, sp_paid.maximum()))

    def del_rows(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    # LongVacationDialog 内
    def save_and_close(self):
        out = []
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 0)  # 氏名コンボ
            de0 = self.table.cellWidget(r, 2)  # 開始 QDateEdit
            de1 = self.table.cellWidget(r, 3)  # 終了 QDateEdit
            sp = self.table.cellWidget(r, 6)  # 有給 QSpinBox

            name = cb.currentText() if cb else ""
            title_item = self.table.item(r, 1)
            title = title_item.text().strip() if title_item else ""
            d0 = (de0.date().toPython() if hasattr(de0.date(), 'toPython')
                  else date.fromisoformat(de0.date().toString('yyyy-MM-dd')))
            d1 = (de1.date().toPython() if hasattr(de1.date(), 'toPython')
                  else date.fromisoformat(de1.date().toString('yyyy-MM-dd')))
            days = (d1 - d0).days + 1
            paid_used = sp.value() if sp else 0

            if not name or not title:
                continue
            if days <= 0 or days > 14:
                QMessageBox.warning(self, "エラー", f"{name} の長期休暇は1〜14日で指定してください。")
                return
            paid_cap = days // 2
            if paid_used > paid_cap:
                QMessageBox.warning(self, "エラー", f"{name} の有給使用日数が上限({paid_cap})を超えています。")
                return

            out.append({
                "member": name, "name": title,
                "start": d0.isoformat(), "end": d1.isoformat(),
                "days": days, "paid_cap": paid_cap, "paid_used": paid_used
            })

        # ここで確実に保存
        try:
            save_json(self.vacations_path, {"vacations": out})
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", f"長期休暇データの保存に失敗しました。\n{self.vacations_path}\n{e}")
            return

        # 保存検証：直後に読み直して件数を確認
        try:
            load_json(self.vacations_path, {"vacations": []})
        except Exception:
            # 読み直し失敗しても保存は継続
            pass

        self.accept()

class SpecialQuotaDialog(QDialog):
    """
    個別出勤数設定（期間単位・人数のみ）
    保存形式:
    {
      "periods":[
        {"name":"GW 2025","start":"2025-05-03","end":"2025-05-06","min_work":8}
      ]
    }
    """
    def __init__(self, parent=None, path=None):
        super().__init__(parent)
        self.setWindowTitle("個別出勤数設定（GW・年末年始・お盆 等）")
        self.resize(700, 320)
        self.path = path

        root = QVBoxLayout(self)

        # 期間リスト
        top = QHBoxLayout()
        self.cb_periods = QComboBox(self)
        self.btn_add = QPushButton("期間を追加")
        self.btn_del = QPushButton("期間を削除")
        top.addWidget(QLabel("期間:"))
        top.addWidget(self.cb_periods, 1)
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_del)
        root.addLayout(top)

        # 期間詳細
        form = QHBoxLayout()
        self.ed_name = QLineEdit(self); self.ed_name.setPlaceholderText("例）GW 2025")
        self.de_start = QDateEdit(self); self.de_start.setCalendarPopup(True)
        self.de_end   = QDateEdit(self); self.de_end.setCalendarPopup(True)
        self.sp_min   = QSpinBox(self);  self.sp_min.setRange(0, 99)
        form.addWidget(QLabel("名称:"));  form.addWidget(self.ed_name, 1)
        form.addWidget(QLabel("開始:"));  form.addWidget(self.de_start)
        form.addWidget(QLabel("終了:"));  form.addWidget(self.de_end)
        form.addWidget(QLabel("最低出勤数:")); form.addWidget(self.sp_min)
        root.addLayout(form)

        self.btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        root.addWidget(self.btns)
        self.btns.accepted.connect(self.save_and_close)
        self.btns.rejected.connect(self.reject)

        # データ
        self.data = self._load_or_init()
        self._rebuild_periods()

        self.cb_periods.currentIndexChanged.connect(self._on_changed)
        self.btn_add.clicked.connect(self._on_add)
        self.btn_del.clicked.connect(self._on_del)

        if self.cb_periods.count() == 0:
            self._on_add()
        else:
            self._load_ui(self.cb_periods.currentIndex())

    def _load_or_init(self):
        tmpl = {"periods": []}
        ensure_file_with_template(self.path, tmpl)
        obj = load_json(self.path, tmpl)
        return obj if isinstance(obj, dict) else tmpl

    def _rebuild_periods(self):
        self.cb_periods.blockSignals(True)
        self.cb_periods.clear()
        for p in self.data.get("periods", []):
            self.cb_periods.addItem(p.get("name", "（無題）"))
        self.cb_periods.blockSignals(False)

    def _load_ui(self, idx: int):
        if not (0 <= idx < len(self.data.get("periods", []))):
            return
        p = self.data["periods"][idx]
        self.ed_name.setText(p.get("name", ""))
        try:
            y,m,d = map(int, (p.get("start","") or date.today().isoformat()).split("-"))
            self.de_start.setDate(date(y,m,d))
        except Exception:
            self.de_start.setDate(date.today())
        try:
            y,m,d = map(int, (p.get("end","") or date.today().isoformat()).split("-"))
            self.de_end.setDate(date(y,m,d))
        except Exception:
            self.de_end.setDate(date.today())
        self.sp_min.setValue(int(p.get("min_work", 0)))

    def _read_ui(self) -> dict:
        name = self.ed_name.text().strip() or "（無題）"
        d0 = self.de_start.date().toPython() if hasattr(self.de_start.date(), 'toPython') else date.fromisoformat(self.de_start.date().toString('yyyy-MM-dd'))
        d1 = self.de_end.date().toPython()   if hasattr(self.de_end.date(),   'toPython') else date.fromisoformat(self.de_end.date().toString('yyyy-MM-dd'))
        if d1 < d0:
            d1 = d0
        return {"name": name, "start": d0.isoformat(), "end": d1.isoformat(), "min_work": int(self.sp_min.value())}

    def _on_changed(self, idx: int):
        prev = self.cb_periods.property("_prev") or 0
        if 0 <= prev < len(self.data.get("periods", [])):
            self.data["periods"][prev] = self._read_ui()
        self.cb_periods.setProperty("_prev", idx)
        self._load_ui(idx)

    def _on_add(self):
        self.data["periods"].append({
            "name": "新しい期間",
            "start": date.today().isoformat(),
            "end": date.today().isoformat(),
            "min_work": 0,
        })
        self._rebuild_periods()
        self.cb_periods.setCurrentIndex(self.cb_periods.count() - 1)

    def _on_del(self):
        idx = self.cb_periods.currentIndex()
        if 0 <= idx < len(self.data.get("periods", [])):
            del self.data["periods"][idx]
            self._rebuild_periods()
            if self.cb_periods.count():
                self.cb_periods.setCurrentIndex(0)
            else:
                self._on_add()

    def save_and_close(self):
        idx = self.cb_periods.currentIndex()
        if 0 <= idx < len(self.data.get("periods", [])):
            self.data["periods"][idx] = self._read_ui()
        save_json(self.path, self.data)
        self.accept()


# ---------- 曜日ごとの設定（基本）ダイアログ ----------
class WeekdayRulesDialog(QDialog):
    """曜日ごとの設定（基本）：最小構成のGUI"""
    def __init__(self, parent=None, rules_path=None):
        super().__init__(parent)
        self.setWindowTitle("曜日ごとの設定（基本）")
        self.resize(560, 360)
        self.rules_path = rules_path

        v = QVBoxLayout(self)
        self.table = QTableWidget(7, 4, self)
        self.table.setHorizontalHeaderLabels(["曜日", "必要出勤数", "管理職最少", "リーダー必須"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.table, 1)

        # 行ヘッダ（曜日名）
        youbi = ["月","火","水","木","金","土","日"]
        for r, yb in enumerate(youbi):
            self.table.setItem(r, 0, QTableWidgetItem(yb))
            self.table.item(r, 0).setFlags(Qt.ItemIsSelectable)  # 編集不可表示

            sp_need = QSpinBox(self); sp_need.setRange(0, 99)
            sp_mgr  = QSpinBox(self); sp_mgr.setRange(0, 10)
            cb_lead = QCheckBox(self)
            self.table.setCellWidget(r, 1, sp_need)
            self.table.setCellWidget(r, 2, sp_mgr)
            self.table.setCellWidget(r, 3, cb_lead)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        v.addWidget(self.buttons)
        self.buttons.accepted.connect(self.save_and_close)
        self.buttons.rejected.connect(self.reject)

        # ロード
        template = {
            "weekday_rules": {
                "0": {"min_work": 5, "min_managers": 1, "leader_required": True},
                "1": {"min_work": 5, "min_managers": 1, "leader_required": True},
                "2": {"min_work": 5, "min_managers": 1, "leader_required": True},
                "3": {"min_work": 5, "min_managers": 1, "leader_required": True},
                "4": {"min_work": 5, "min_managers": 1, "leader_required": True},
                "5": {"min_work": 6, "min_managers": 1, "leader_required": True},  # 土
                "6": {"min_work": 6, "min_managers": 1, "leader_required": True},  # 日
            }
        }
        ensure_file_with_template(self.rules_path, template)
        obj = load_json(self.rules_path, template)
        rules = obj.get("weekday_rules", {})

        for r in range(7):
            rule = rules.get(str(r), {})
            need = int(rule.get("min_work", 0))
            mgr  = int(rule.get("min_managers", 0))
            led  = bool(rule.get("leader_required", False))
            self.table.cellWidget(r, 1).setValue(need)
            self.table.cellWidget(r, 2).setValue(mgr)
            self.table.cellWidget(r, 3).setChecked(led)

    def save_and_close(self):
        out = {"weekday_rules": {}}
        for r in range(7):
            need = self.table.cellWidget(r, 1).value()
            mgr  = self.table.cellWidget(r, 2).value()
            led  = self.table.cellWidget(r, 3).isChecked()
            out["weekday_rules"][str(r)] = {
                "min_work": need,
                "min_managers": mgr,
                "leader_required": led,
            }
        save_json(self.rules_path, out)
        self.accept()


# ---------- メインウィンドウ ----------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_data_dir()
        self.setWindowTitle("手動シフト作成ツール (MVP)")
        self.resize(1400, 800)

        # スタッフ読み込み（初回テンプレ生成）
        self.staffs: List[Staff] = self.load_or_init_staffs()

        # --- 上部バー（既存） ---
        top = QWidget();
        top_l = QHBoxLayout(top)

        # ← ここを追加：左上にハンバーガー
        self.ham_btn = QToolButton()
        self.ham_btn.setObjectName("Hamburger")
        self.ham_btn.setText("≡")
        self.ham_btn.setMinimumHeight(36)
        top_l.addWidget(self.ham_btn)

        self.year_cb = QComboBox()
        self.month_cb = QComboBox()
        y_now = date.today().year
        for y in range(y_now - 1, y_now + 3):
            self.year_cb.addItem(str(y), y)
        self._init_month_combo()
        self.btn_load = QPushButton("更新")
        self.btn_save = QPushButton("保存")
        self.wish_mode = QCheckBox("希望休モード(×入力)")
        self.btn_check = QPushButton("チェック実行")
        self.btn_prev_tail = QPushButton("前期間末4日の読込/編集")

        top_l.addWidget(QLabel(" 年:"));
        top_l.addWidget(self.year_cb)
        top_l.addWidget(QLabel(" 月:"));
        top_l.addWidget(self.month_cb)
        top_l.addWidget(QLabel(" 期間:"))
        top_l.addWidget(self.btn_load)  # 年月期間の右に配置
        top_l.addStretch(1)
        top_l.addWidget(self.wish_mode)
        top_l.addWidget(self.btn_save)
        top_l.addWidget(self.btn_check)
        top_l.addWidget(self.btn_prev_tail)

        # --- モデル/テーブル（既存） ---
        self.model = ShiftModel(self.staffs, [], date.today().year, date.today().month)
        self.table = ShiftTable(self.model, wish_mode_getter=lambda: self.wish_mode.isChecked())
        if not isinstance(self.table.itemDelegate(), WishPaidDelegate):
            self.table.setItemDelegate(WishPaidDelegate(self.table))

        # --- 右ペイン（既存） ---
        right = QWidget();
        right_l = QVBoxLayout(right)
        right_l.addWidget(QLabel("チェック結果 / メモ"))
        self.txt_result = QTextEdit();
        self.txt_result.setReadOnly(True)
        right_l.addWidget(self.txt_result, 1)

        # --- メイン領域（トップ＋テーブル＋右）をまとめたウィジェット ---
        main_area = QWidget();
        main_v = QVBoxLayout(main_area)
        main_v.addWidget(top)
        split = QSplitter();
        split.addWidget(self.table);
        split.addWidget(right)
        split.setStretchFactor(0, 3);
        split.setStretchFactor(1, 2)
        main_v.addWidget(split, 1)

        # --- サイドメニュー（左からスライド） ---
        self.sideMenu = QWidget()
        self.sideMenu.setObjectName("SideMenu")
        side_v = QVBoxLayout(self.sideMenu)
        side_v.setContentsMargins(12, 12, 12, 12)
        side_v.addWidget(QLabel("メニュー"))

        btn_members = QPushButton("メンバー管理")
        btn_longvac = QPushButton("長期休暇管理")
        btn_weekday = QPushButton("曜日ごとの設定（基本）")
        btn_quota = QPushButton("個別出勤数設定（GW・年末年始・お盆）")
        for b in (btn_members, btn_longvac, btn_weekday, btn_quota):
            b.setMinimumHeight(36)
            side_v.addWidget(b)
        side_v.addStretch(1)

        # クリックで既存ハンドラを呼ぶ
        btn_members.clicked.connect(self.on_open_members)
        btn_longvac.clicked.connect(self.on_open_longvac)
        btn_weekday.clicked.connect(self.on_open_weekday_rules)
        btn_quota.clicked.connect(self.on_open_special_quota)

        # サイドメニューの初期幅は 0（閉じた状態）。最大幅をアニメーションで変える。
        self.sideMenu.setMaximumWidth(0)
        self._menu_open = False
        self._menu_width_target = 260  # メニューの展開幅

        # 開閉アニメーション
        self._menu_anim = QPropertyAnimation(self.sideMenu, b"maximumWidth", self)
        self._menu_anim.setDuration(220)
        self._menu_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._apply_table_layout_for_window_state()

        def toggle_menu():
            self._menu_open = not self._menu_open
            start = self.sideMenu.maximumWidth()
            end = self._menu_width_target if self._menu_open else 0
            self._menu_anim.stop()
            self._menu_anim.setStartValue(start)
            self._menu_anim.setEndValue(end)
            self._menu_anim.start()

        self.ham_btn.clicked.connect(toggle_menu)

        # --- ルートレイアウト：左に sideMenu、右に main_area ---
        central = QWidget();
        root_h = QHBoxLayout(central)
        root_h.setContentsMargins(0, 0, 0, 0);
        root_h.setSpacing(0)
        root_h.addWidget(self.sideMenu)  # ← 左
        root_h.addWidget(main_area, 1)  # ← 右（メイン）
        self.setCentralWidget(central)

        # --- シグナル ---
        self.btn_load.clicked.connect(self.on_load)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_check.clicked.connect(self.on_check)
        self.btn_prev_tail.clicked.connect(self.on_edit_prev_tail)

        # （ハンバーガー：toggle_menu を __init__ 内で定義しているなら）
        # self.ham_btn.clicked.connect(toggle_menu)
        # もしくはメソッド化していれば:
        # self.ham_btn.clicked.connect(self.toggle_menu)

        # --- 初期表示（安全に年/月/期間をセット）---
        def _set_cb_to_value(cb, value):
            """data優先で探し、なければ文字一致でフォールバック"""
            idx = cb.findData(value)
            if idx < 0:
                idx = cb.findText(str(value))
            if idx >= 0:
                cb.setCurrentIndex(idx)

        today = date.today()
        _set_cb_to_value(self.year_cb, today.year)
        _set_cb_to_value(self.month_cb, today.month)

        self.on_load()

    # どこか UI 初期化（__init__ / set_up_ui など）で月コンボを作っている箇所を置き換え
    # 例：self.month_cb を使っている前提
    def _init_month_combo(self):
        self.month_cb.clear()
        items = []
        for m in range(1, 13):
            end_m = 1 if m == 12 else m + 1
            txt = f"{m}-{end_m}"
            self.month_cb.addItem(txt, m)
            items.append(txt)

        # ▼ ここを修正：列挙値の取り方
        try:
            policy = QComboBox.SizeAdjustPolicy.AdjustToContents  # PySide6 はこちら
        except AttributeError:
            policy = QComboBox.AdjustToContents  # 互換用（PyQt等）
        self.month_cb.setSizeAdjustPolicy(policy)

        self.month_cb.setMinimumContentsLength(5)

        view = QListView(self.month_cb)
        view.setTextElideMode(Qt.ElideNone)
        fm = self.month_cb.fontMetrics()
        max_w = max(fm.horizontalAdvance(t) for t in items) + 24
        view.setMinimumWidth(max_w)
        self.month_cb.setView(view)

    # ---- スタッフ ----
    def load_or_init_staffs(self) -> List[Staff]:
        # 旧: list[{"name":..., "is_manager":..., "hire_date":...}]
        # 新: {"members": [ ... ]} も両対応にする
        default_staffs = [
            {"name": "迫", "is_manager": True, "hire_date": "2024-10-01"},
            {"name": "田嶋", "is_manager": True, "hire_date": "2023-04-01"},
            {"name": "齋藤", "is_manager": True, "hire_date": "2022-06-01"},
            {"name": "田中", "is_manager": True, "hire_date": "2024-02-01"},
        ]
        ensure_data_dir()
        obj = load_json(MEMBERS_JSON, {"members": default_staffs})
        # 後方互換：list でも dictでもOKに
        members = obj if isinstance(obj, list) else obj.get("members", default_staffs)
        # 初回生成
        if not os.path.exists(MEMBERS_JSON):
            save_json(MEMBERS_JSON, {"members": members})
        return [Staff.from_dict(s) for s in members]

    # ---- 期間 ----
    # ✅ 置き換え：MainWindow.current_period
    def current_period(self) -> tuple[int, int, list[int], str]:
        y = int(self.year_cb.currentData()) if self.year_cb.currentData() else int(self.year_cb.currentText())
        m = int(self.month_cb.currentData()) if self.month_cb.currentData() else int(self.month_cb.currentText())

        from calendar import monthrange
        last = monthrange(y, m)[1]
        days = list(range(16, last + 1)) + list(range(1, 16))

        # 翌月（12月の場合は翌年の1月）
        next_m = 1 if m == 12 else m + 1

        # 表示ラベルを「m-next_m」に
        label = f"{m}-{next_m}"

        return y, m, days, label

    # 置き換え：MainWindow.sched_path
    def sched_path(self, year: int, month: int) -> str:
        """開始年月(= その月の16日に始まる期間) 用の保存先"""
        return os.path.join(DATA_DIR, f"schedule_{year:04d}{month:02d}_16-15.json")

    # ---- 読込/表示 ----
    def on_load(self):
        """保存済みシフトデータを読み込み（無ければ新規初期化）、モデルに反映して表示まで行う。"""
        y, m, days, _ = self.current_period()
        path = self.sched_path(y, m)

        # 1) 期間（年・月・列=days）をまずモデルへ反映し、列ゼロで表示が消えるのを防ぐ
        self.model.set_period(y, m, days)

        # 2) 週末/祝日・必要出勤数などのマップを再構築（配色や分母用）
        if hasattr(self, "_rebuild_period_maps"):
            self._rebuild_period_maps(y, m, days)

        # 3) 保存ファイルの有無で分岐
        new_file = not os.path.exists(path)
        saved_obj = {} if new_file else load_json(path, {})

        # 4) 既存オブジェクト取り出し（無ければ空の辞書で受ける）
        saved_status = (saved_obj.get("status", {}) if isinstance(saved_obj, dict) else {})
        saved_wishes = (saved_obj.get("wishes", {}) if isinstance(saved_obj, dict) else {})
        saved_wish_paid = (saved_obj.get("wish_paid", {}) if isinstance(saved_obj, dict) else {})
        saved_leaders = (saved_obj.get("leaders", {}) if isinstance(saved_obj, dict) else {})

        # 5) 画面構造を再構築
        names = [s.name for s in self.staffs]
        new_status, new_wishes, new_wish_paid, new_leaders = {}, {}, {}, {}

        def _get_saved(row_obj, day):
            """文字列/整数キー両対応で安全に取得"""
            if isinstance(row_obj, dict):
                if day in row_obj:  # intキー
                    return row_obj.get(day)
                return row_obj.get(str(day))  # strキー
            return None

        for nm in names:
            row_saved_status = saved_status.get(nm, {})
            row_saved_wishes = saved_wishes.get(nm, {})
            row_saved_wish_paid = saved_wish_paid.get(nm, {})

            row_status, row_wish, row_wp = {}, {}, {}
            for d in days:
                row_status[d] = _get_saved(row_saved_status, d) or " "
                row_wish[d] = bool(_get_saved(row_saved_wishes, d))
                row_wp[d] = bool(_get_saved(row_saved_wish_paid, d))
            new_status[nm] = row_status
            new_wishes[nm] = row_wish
            new_wish_paid[nm] = row_wp

        for d in days:
            new_leaders[d] = saved_leaders.get(str(d), "-")

        # 6) モデルへ反映
        self.model.status = new_status
        self.model.wishes = new_wishes
        self.model.wish_paid = new_wish_paid
        self.model.leaders = new_leaders

        # 7) 描画更新
        self.model.layoutChanged.emit()

        # 8) 案内
        if new_file:
            QMessageBox.information(self, "新規初期化",
                                    f"保存ファイルが見つからなかったため、新規として初期化しました。\n{os.path.basename(path)}")
        else:
            QMessageBox.information(self, "読込完了", f"{os.path.basename(path)} を読み込みました。")

    # ---- 保存 ----
    def on_save(self):
        # ✅ 差し替え：MainWindow.on_save の保存先取得部分
        y, m, days, _ = self.current_period()
        path = self.sched_path(y, m)
        obj = self.model.to_json()
        save_json(path, obj)
        QMessageBox.information(self, "保存", f"保存しました。")

    # ---- 前期間末4日の編集（簡易） ----
    def on_edit_prev_tail(self):
        y, m, *_ = self.current_period()  # ← half を取らない
        # 前期間基準（今期が 2025/10 なら 2025/09 が前期基準）
        py, pm = self.prev_period_base(y, m)
        key = f"{py:04d}{pm:02d}_2"  # 末4日は「前期の後半(16-末)」を想定
        data = load_json(LAST_TAIL_JSON, {})
        if key not in data:
            data[key] = {s.name: ["-", "-", "-", "-"] for s in self.staffs}
            save_json(LAST_TAIL_JSON, data)
        if QMessageBox.question(self, "前期間末4日データ",
                                "JSON を開いて直接編集しますか？\n\nOKで開く / Cancelで閉じる"
                                ) == QMessageBox.StandardButton.Ok:
            try:
                os.startfile(LAST_TAIL_JSON)
            except Exception:
                QMessageBox.information(self, "情報", f"手動で開いてください:\n{LAST_TAIL_JSON}")

    # ✅ 置き換え：MainWindow の前後期間ベース月
    def prev_period_base(self, y: int, m: int) -> tuple[int, int]:
        # 例) 基準 2025/10 -> 前期基準は 2025/09
        return (y - 1, 12) if m == 1 else (y, m - 1)

    def next_period_base(self, y: int, m: int) -> tuple[int, int]:
        # 例) 基準 2025/10 -> 次期基準は 2025/11
        return (y + 1, 1) if m == 12 else (y, m + 1)

    # ---- チェック ----
    def on_check(self):
        y, m, days, _ = self.current_period()
        status = self.model.status
        names = [s.name for s in self.staffs]

        # 曜日ルールの読み込み（管理職数/リーダー必須の参照用）
        rules_obj = load_json(WEEKDAY_RULES_JSON, {"weekday_rules": {}})
        rules = rules_obj.get("weekday_rules", {})

        msgs = []
        import calendar

        for d in days:
            wd = calendar.weekday(y, m, d)  # 0=月 . 6=日
            r = rules.get(str(wd), {})

            # --- 必要出勤数（特別期間優先） ---
            # 1) モデルが持つ req_min_work（日ごと、特別期間を含めた最終値）
            need_work = int(getattr(self.model, "req_min_work", {}).get(d, 0) or 0)
            # 2) 無ければ曜日ルールの min_work をフォールバック
            if need_work == 0:
                need_work = int(r.get("min_work", 0))

            # --- 管理職最少/リーダー必須（曜日ルール準拠） ---
            min_managers = int(r.get("min_managers", 0))
            leader_req = bool(r.get("leader_required", False))

            # 出勤者カウント（" " が出勤）
            work_names = [nm for nm in names if status[nm][d] == " "]
            work_cnt = len(work_names)
            # 管理職の出勤者
            mgr_work = sum(1 for s in self.staffs if s.is_manager and status[s.name][d] == " ")

            # 必要出勤数チェック（特別期間を含む最終値）
            if need_work and work_cnt < need_work:
                msgs.append(f"{m}/{d}: 出勤者が不足（{work_cnt}/{need_work}）")

            # 管理職チェック（曜日ルール）
            if min_managers and mgr_work < min_managers:
                msgs.append(f"{m}/{d}: 管理職が不足（{mgr_work}/{min_managers}）")

            # リーダー必須チェック（1人もいない場合）
            if leader_req and not any(s.is_leader and status[s.name][d] == " " for s in self.staffs):
                msgs.append(f"{m}/{d}: リーダーが不在")

        if not msgs:
            QMessageBox.information(self, "チェック結果", "全ての条件を満たしています。")
        else:
            text = "\n".join(msgs)
            QMessageBox.warning(self, "チェック結果", text)

    # ---- menu handlers ----
    def on_open_members(self):
        dlg = MembersDialog(self, members_path=MEMBERS_JSON, staffs_path=STAFFS_JSON)
        if dlg.exec() == QDialog.Accepted:
            self.staffs = self.load_or_init_staffs()
            self.on_load()
            QMessageBox.information(self, "メンバー管理", "保存しました。画面を更新しました。")

    def on_open_longvac(self):
        members_obj = load_json(MEMBERS_JSON, {"members": []})
        names = [m.get("name", "") for m in
                 (members_obj.get("members", []) if isinstance(members_obj, dict) else members_obj)]
        dlg = LongVacationDialog(self, vacations_path=VACATIONS_JSON, member_names=names)  # ← ここが実際に使うパス
        if dlg.exec() == QDialog.Accepted:
            # ここは通知のみでOK（保存はダイアログで完了）
            QMessageBox.information(self, "長期休暇管理", "保存しました。")

    def on_open_weekday_rules(self):
        dlg = WeekdayRulesDialog(self, rules_path=WEEKDAY_RULES_JSON)
        if dlg.exec() == QDialog.Accepted:
            QMessageBox.information(self, "曜日ごとの設定", "保存しました。")

    def on_open_special_quota(self):
        dlg = SpecialQuotaDialog(self, path=SPECIAL_QUOTAS_JSON)
        if dlg.exec() == QDialog.Accepted:
            QMessageBox.information(self, "個別出勤数設定", "保存しました。")

    # MainWindow に追加
    def _rebuild_period_maps(self, y: int, m: int, days: list[int]) -> None:
        """土日/祝日セット、特別期間による必要出勤数、残有給をモデルに流し込む"""
        from calendar import weekday, monthrange

        # 翌月（12→1で年繰上げ）
        next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)

        # 祝日読み込み
        hol_obj = load_json(HOLIDAYS_JSON, {"holidays": []})
        hol_set = set()
        for s in hol_obj.get("holidays", []):
            try:
                hol_set.add(date.fromisoformat(s))
            except Exception:
                pass

        sat_days, hol_days, weekend_days = set(), set(), set()
        for d in days:
            # 16〜月末は (y, m, d) / 1〜15は (next_y, next_m, d)
            yy, mm = (y, m) if d >= 16 else (next_y, next_m)
            wd = weekday(yy, mm, d)  # 0=Mon ... 6=Sun
            is_sat = (wd == 5)
            is_sun = (wd == 6)
            is_holiday = (date(yy, mm, d) in hol_set)

            if is_sat: sat_days.add(d)
            if is_sun or is_holiday: hol_days.add(d)
            if is_sat or is_sun: weekend_days.add(d)

        self.model.sat_days = sat_days
        self.model.hol_days = hol_days
        self.model.weekend_days = weekend_days

        # --- 必要出勤数（基本ルール＋特別期間で上書き）---
        # 1) 曜日ごとの基本
        rules_obj = load_json(WEEKDAY_RULES_JSON, {"weekday_rules": {}})
        rules = rules_obj.get("weekday_rules", {})
        req = {d: int(rules.get(str(weekday((y if d >= 16 else next_y), (m if d >= 16 else next_m), d)), {})
                      .get("min_work", 0)) for d in days}

        # 2) 特別期間（優先）
        sp = load_json(SPECIAL_QUOTAS_JSON, {"periods": []})
        for p in sp.get("periods", []):
            try:
                d0 = date.fromisoformat(p.get("start"))
                d1 = date.fromisoformat(p.get("end"))
                mw = int(p.get("min_work", 0))
            except Exception:
                continue
            if d1 < d0:
                d0, d1 = d1, d0
            for d in days:
                yy, mm = (y, m) if d >= 16 else (next_y, next_m)
                cur = date(yy, mm, d)
                if d0 <= cur <= d1:
                    req[d] = max(req.get(d, 0), mw)  # 特別期間を優先（上書き/最大）
        self.model.req_min_work = req

        # --- 残有給（名前横表示）---
        mem = load_json(MEMBERS_JSON, {"members": []})
        paid_map = {m.get("name"): int(m.get("paid_left", 0)) for m in mem.get("members", [])}
        self.model.paid_left = {s.name: int(paid_map.get(s.name, 0)) for s in self.staffs}

    def _apply_table_layout_for_window_state(self):
        """最大化=全列伸長 / 復元=横スクロール優先。復元時はストレッチ残留幅もリセット。"""
        hh = self.table.horizontalHeader()
        default_w = 90  # 列の基準幅（必要に応じて調整）

        if (self.windowState() & Qt.WindowMaximized) == Qt.WindowMaximized:
            # 最大化：横スクロールは出さず、画面幅に全列をフィット
            hh.setStretchLastSection(False)  # 念のため False 明示
            hh.setSectionResizeMode(QHeaderView.Stretch)
            self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            # 復元：横スクロール優先
            hh.setStretchLastSection(False)
            hh.setSectionResizeMode(QHeaderView.Fixed)
            hh.setDefaultSectionSize(default_w)

            # ── 重要：ストレッチで広がった“残留幅”を明示的にリセット ──
            col_count = self.model.columnCount() if self.table.model() else 0
            for i in range(col_count):
                hh.resizeSection(i, default_w)

            # スクロールとサイズ調整方針
            self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
            self.table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)

            # ビューポート更新（見た目が残るケースの保険）
            self.table.viewport().update()

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            # イベント直後はジオメトリが安定しないので 0ms 遅延で適用
            QTimer.singleShot(0, self._apply_table_layout_for_window_state)
        super().changeEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 復元サイズ時に列がはみ出る/収まる境界で挙動が変わるため、都度再適用
        QTimer.singleShot(0, self._apply_table_layout_for_window_state)


if __name__ == '__main__':
    app = QApplication([])
    app.setStyleSheet(DARK_STYLESHEET)
    w = MainWindow()
    w.show()
    app.exec()
