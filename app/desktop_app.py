"""原生桌面工作台（PySide6）。

- 左侧：群聊 + 6 角色、协作模式、设置、停止、新会话
- 右侧：群聊/角色历史 + 底部输入
- 需求直接提给产品经理；角色视图可单独下指令
- 会话历史持久化到 session.json
"""
import asyncio
import json
import os
import queue
import shutil
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QLineEdit, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QToolButton, QDialog, QFormLayout,
    QDialogButtonBox, QComboBox, QTabWidget, QGroupBox, QCheckBox,
    QInputDialog, QMessageBox, QDoubleSpinBox,
)
import yaml

from src.pipeline import run_pipeline, run_direct
from src.paths import app_root, CONFIG_DIR
from src.models import role_override, save_model_cfg
from src.projects import (list_projects, current_project, current_workspace,
                          create_project, select_project, current_session_path)

AGENTS = [
    ("pm", "产品经理", "🧑‍💼"),
    ("arch", "架构师", "🏗️"),
    ("backend", "后端开发", "⚙️"),
    ("frontend", "前端开发", "🎨"),
    ("reviewer", "代码评审", "🔍"),
    ("qa", "QA测试", "🧪"),
]
AGENT_INFO = {k: (name, emoji) for k, name, emoji in AGENTS}
SOURCE_TO_ROLE = {
    "ProductManager": "pm", "Architect": "arch", "BackendDev": "backend",
    "FrontendDev": "frontend", "Reviewer": "reviewer", "QA": "qa", "user": "user",
}

QSS = """
QMainWindow, QWidget { background: #0f1115; color: #e6e6e6;
    font-family: "Microsoft YaHei", "Segoe UI"; font-size: 13px; }
QListWidget { background: #151922; border: none; padding: 6px; outline: none; }
QListWidget::item { padding: 10px 12px; border-radius: 8px; margin: 2px 0; }
QListWidget::item:selected { background: #26324a; color: #ffffff; }
QListWidget::item:hover { background: #1d2433; }
QLineEdit, QComboBox { background: #1b202b; border: 1px solid #2b3446; border-radius: 8px;
    padding: 8px 10px; color: #e6e6e6; }
QPushButton { background: #2f6feb; color: white; border: none; border-radius: 8px;
    padding: 8px 14px; font-weight: bold; }
QPushButton:hover { background: #3b7bf5; }
QPushButton:disabled { background: #2a3040; color: #6b7488; }
QPushButton#danger { background: #7a2f3a; }
QPushButton#danger:hover { background: #933846; }
QPushButton#ghost { background: #1b202b; border: 1px solid #2b3446; color: #c8d0dd; }
QScrollArea { border: none; background: #0f1115; }
QToolButton { border: none; color: #8b95a7; text-align: left; }
"""


def config_dialog(parent=None, first_run: bool = False) -> bool:
    """统一配置 + 按角色覆盖（模型 / API Key / Base URL）。"""
    cfg = CONFIG_DIR / "models.yaml"
    example = CONFIG_DIR / "models.example.yaml"
    if not cfg.exists() and example.exists():
        try:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(example, cfg)
        except Exception:
            pass
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    default = data.get("default") or {}
    key = str(default.get("api_key", ""))
    if first_run and key and "your-" not in key and "填入" not in key:
        return True

    dlg = QDialog(parent)
    dlg.setWindowTitle("首次配置 · 模型 API" if first_run else "模型设置")
    dlg.setMinimumSize(480 if first_run else 640, 240 if first_run else 580)
    outer = QVBoxLayout(dlg)
    tabs = QTabWidget()
    outer.addWidget(tabs)

    # ---- 统一配置 ----
    unified = QWidget()
    form = QFormLayout(unified)
    key_edit = QLineEdit(key if "your-" not in key and "填入" not in key else "")
    key_edit.setEchoMode(QLineEdit.EchoMode.Password)
    key_edit.setPlaceholderText("sk-...")
    model_edit = QLineEdit(default.get("model", "deepseek-flash"))
    base_edit = QLineEdit(default.get("base_url", "https://api.deepseek.com/v1"))
    budget_edit = QDoubleSpinBox()
    budget_edit.setRange(0, 100000)
    budget_edit.setDecimals(2)
    budget_edit.setValue(float(default.get("budget_cny", 0) or 0))
    budget_edit.setPrefix("¥ ")
    form.addRow("API Key", key_edit)
    form.addRow("模型", model_edit)
    form.addRow("Base URL", base_edit)
    form.addRow("预算（0=不限）", budget_edit)
    form.addRow(QLabel("未单独配置的角色都使用这里的设置。"))
    tabs.addTab(unified, "统一配置")

    # ---- 角色覆盖 ----
    role_edits = {}
    if not first_run:
        roles_page = QWidget()
        rv = QVBoxLayout(roles_page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        for rk, name, emoji in AGENTS:
            box = QGroupBox(f"{emoji} {name}")
            bv = QVBoxLayout(box)
            enable = QCheckBox("单独配置（不勾选则使用统一配置）")
            m = QLineEdit()
            k = QLineEdit()
            k.setEchoMode(QLineEdit.EchoMode.Password)
            b = QLineEdit()
            ov = role_override(data, rk)
            if ov:
                enable.setChecked(True)
                m.setText(str(ov.get("model", "")))
                k.setText(str(ov.get("api_key", "")))
                b.setText(str(ov.get("base_url", "")))
            m.setPlaceholderText(f"默认：{default.get('model', 'deepseek-flash')}")
            k.setPlaceholderText("留空 = 使用统一 Key")
            b.setPlaceholderText(f"默认：{default.get('base_url', 'https://api.deepseek.com/v1')}")
            f = QFormLayout()
            f.addRow("模型", m)
            f.addRow("API Key", k)
            f.addRow("Base URL", b)
            bv.addWidget(enable)
            bv.addLayout(f)
            iv.addWidget(box)
            role_edits[rk] = (enable, m, k, b)
        iv.addStretch(1)
        scroll.setWidget(inner)
        rv.addWidget(scroll)
        tabs.addTab(roles_page, "角色覆盖")

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    outer.addWidget(buttons)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False

    data.setdefault("default", {})
    data["default"].update({
        "provider": "deepseek",
        "model": model_edit.text().strip() or "deepseek-flash",
        "api_key": key_edit.text().strip(),
        "base_url": base_edit.text().strip() or "https://api.deepseek.com/v1",
        "temperature": 0.2,
        "budget_cny": float(budget_edit.value()),
    })
    if not first_run:
        roles = {}
        for rk, (enable, m, k, b) in role_edits.items():
            if not enable.isChecked():
                continue
            entry = {}
            if m.text().strip():
                entry["model"] = m.text().strip()
            if k.text().strip():
                entry["api_key"] = k.text().strip()
            if b.text().strip():
                entry["base_url"] = b.text().strip()
            if entry:
                roles[rk] = entry
        data["roles"] = roles
        data.pop("role_models", None)
    save_model_cfg(data)
    return True


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 软件团队 · 桌面工作台")
        self.resize(1180, 780)

        self.session_path = current_session_path()
        self.log = self._load_session()
        self.view = "group"
        self.running = False
        self.pending = None
        self.dm_running = set()
        self.usage = {}
        self.stop_event = threading.Event()
        self.event_q = queue.Queue()
        self.answer_q = queue.Queue()

        self._build_ui()
        self._refresh()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start(200)

    # ---------- 持久化 ----------
    def _load_session(self):
        try:
            if self.session_path.exists():
                data = json.loads(self.session_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    def _save_session(self):
        try:
            self.session_path.write_text(
                json.dumps(self.log, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # ---------- UI ----------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 侧边栏
        side = QWidget()
        side.setFixedWidth(215)
        side.setStyleSheet("background:#12151d;")
        sv = QVBoxLayout(side)
        sv.setContentsMargins(8, 10, 8, 10)
        sv.setSpacing(6)
        title = QLabel("👥 AI 软件团队")
        title.setStyleSheet("font-size:15px;font-weight:bold;padding:4px;")
        sv.addWidget(title)

        sv.addWidget(QLabel("项目"))
        self.project_combo = QComboBox()
        for p in list_projects():
            self.project_combo.addItem(p["name"])
        self.project_combo.setCurrentText(current_project()["name"])
        self.project_combo.currentTextChanged.connect(self._on_project_changed)
        sv.addWidget(self.project_combo)

        new_proj = QPushButton("＋ 新建项目")
        new_proj.setObjectName("ghost")
        new_proj.clicked.connect(self._new_project)
        sv.addWidget(new_proj)

        self.sidebar = QListWidget()
        items = [("group", "💬  群聊（全体）")] + \
                [(k, f"{emoji}  {name}") for k, name, emoji in AGENTS]
        for key, label in items:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            self.sidebar.addItem(it)
        self.sidebar.currentRowChanged.connect(self._on_view_changed)
        sv.addWidget(self.sidebar, 1)

        sv.addWidget(QLabel("协作模式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("串行接力", "roundrobin")
        self.mode_combo.addItem("自动协作", "selector")
        sv.addWidget(self.mode_combo)

        self.settings_btn = QPushButton("⚙ 设置")
        self.settings_btn.setObjectName("ghost")
        self.settings_btn.clicked.connect(self._open_settings)
        sv.addWidget(self.settings_btn)

        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.clicked.connect(self._stop_pipeline)
        self.stop_btn.setEnabled(False)
        sv.addWidget(self.stop_btn)

        self.new_btn = QPushButton("🆕 新会话")
        self.new_btn.setObjectName("ghost")
        self.new_btn.clicked.connect(self._new_session)
        sv.addWidget(self.new_btn)

        self.status = QLabel("⚪ 空闲")
        self.status.setStyleSheet("color:#8b95a7;padding:4px;")
        sv.addWidget(self.status)

        self.cost_label = QLabel("本次：0 tokens · ¥0.0000")
        self.cost_label.setStyleSheet("color:#8b95a7;font-size:11px;padding:4px;")
        self.cost_label.setWordWrap(True)
        sv.addWidget(self.cost_label)
        root.addWidget(side)

        # 右侧
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(18, 14, 18, 14)
        rlay.setSpacing(10)
        root.addWidget(right, 1)

        self.title = QLabel("💬 群聊")
        self.title.setStyleSheet("font-size: 20px; font-weight: bold;")
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(self.title)
        rlay.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.chat_host = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_host)
        self.chat_layout.setContentsMargins(4, 4, 4, 4)
        self.chat_layout.setSpacing(8)
        self.scroll.setWidget(self.chat_host)
        rlay.addWidget(self.scroll, 1)

        self.input_host = QWidget()
        self.input_layout = QHBoxLayout(self.input_host)
        self.input_layout.setContentsMargins(0, 0, 0, 0)
        self.input_layout.setSpacing(8)
        rlay.addWidget(self.input_host)

        self.sidebar.setCurrentRow(0)

    # ---------- 项目 ----------
    def _refresh_project_combo(self):
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for p in list_projects():
            self.project_combo.addItem(p["name"])
        self.project_combo.setCurrentText(current_project()["name"])
        self.project_combo.blockSignals(False)

    def _on_project_changed(self, name):
        if not name:
            return
        try:
            select_project(name)
        except Exception:
            return
        self.session_path = current_session_path()
        self.log = self._load_session()
        self.pending = None
        self.running = False
        self.dm_running = set()
        self.usage = {}
        self.stop_event = threading.Event()
        self.event_q = queue.Queue()
        self.answer_q = queue.Queue()
        self.view = "group"
        self.sidebar.setCurrentRow(0)
        self._refresh()

    def _new_project(self):
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称：")
        if not ok or not name.strip():
            return
        try:
            create_project(name.strip())
        except Exception as e:
            QMessageBox.warning(self, "新建项目失败", str(e))
            return
        self._refresh_project_combo()
        self._on_project_changed(name.strip())

    # ---------- 视图 ----------
    def _on_view_changed(self, row):
        it = self.sidebar.item(row)
        self.view = it.data(Qt.UserRole) if it else "group"
        self._refresh()

    def _visible(self):
        if self.view == "group":
            return list(self.log)
        out = []
        for m in self.log:
            src_role = SOURCE_TO_ROLE.get(m["source"], m["source"])
            if src_role == self.view or m.get("target") == self.view:
                out.append(m)
        return out

    # ---------- 渲染 ----------
    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _make_message(self, msg):
        kind, source, text = msg["kind"], msg["source"], msg["text"]
        if kind == "stage":
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color:#8b95a7; font-weight:bold; padding:8px;")
            return lbl
        if kind == "error":
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#ff8a8a; background:#2a161a; border-radius:8px; padding:8px;")
            return lbl
        if kind in ("tool", "thought"):
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            btn = QToolButton()
            btn.setText("🛠️ 工具动作" if kind == "tool" else "💭 思考过程")
            btn.setCheckable(True)
            body = QLabel(text)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setStyleSheet("color:#9aa4b2; background:#12161e; border-radius:6px; padding:6px;")
            body.setVisible(False)
            btn.toggled.connect(body.setVisible)
            v.addWidget(btn)
            v.addWidget(body)
            return box

        role = SOURCE_TO_ROLE.get(source, source)
        is_user = role == "user"
        if is_user:
            tgt = msg.get("target")
            name = ("发给 " + AGENT_INFO[tgt][0]) if tgt in AGENT_INFO else "群聊 · 你"
        else:
            name = AGENT_INFO.get(role, (source, ""))[0]

        frame = QFrame()
        frame.setMaximumWidth(820)
        frame.setStyleSheet(
            ("QFrame{background:#23406e;border-radius:10px;}"
             if is_user else "QFrame{background:#1a1f2b;border-radius:10px;}")
            + "QLabel{background:transparent;}")
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(3)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(
            "color:#bcd4ff;font-weight:bold;" if is_user else "color:#7fb3ff;font-weight:bold;")
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        v.addWidget(name_lbl)
        v.addWidget(body)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        if is_user:
            h.addStretch(1)
            h.addWidget(frame)
        else:
            h.addWidget(frame)
            h.addStretch(1)
        return row

    def _refresh_chat(self):
        self._clear_layout(self.chat_layout)
        msgs = self._visible()
        if not msgs:
            tip = QLabel("在下方输入需求，直接提给产品经理。" if self.view == "group"
                         else "该角色还没有发言。")
            tip.setStyleSheet("color:#697386; padding:20px;")
            tip.setAlignment(Qt.AlignCenter)
            self.chat_layout.addWidget(tip)
        for m in msgs:
            self.chat_layout.addWidget(self._make_message(m))
        self.chat_layout.addStretch(1)
        QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _refresh_input(self):
        self._clear_layout(self.input_layout)
        if self.pending:
            kind, prompt = self.pending
            if kind == "approve":
                lbl = QLabel(f"⏸ 需要你审批：{prompt}")
                lbl.setWordWrap(True)
                self.input_layout.addWidget(lbl, 1)
                yes = QPushButton("✅ 批准")
                no = QPushButton("❌ 驳回")
                no.setObjectName("danger")
                yes.clicked.connect(lambda: self._answer("y", "✅ 批准"))
                no.clicked.connect(lambda: self._answer("n", "❌ 驳回"))
                self.input_layout.addWidget(yes)
                self.input_layout.addWidget(no)
            else:
                lbl = QLabel(f"⏸ 产品经理在问你：{prompt}")
                lbl.setWordWrap(True)
                self.input_layout.addWidget(lbl, 1)
                self._answer_edit = QLineEdit()
                self._answer_edit.setPlaceholderText("你的答复（留空 = 按默认假设执行）")
                self.input_layout.addWidget(self._answer_edit, 2)
                btn = QPushButton("发送")
                btn.clicked.connect(lambda: self._answer(self._answer_edit.text(),
                                                         self._answer_edit.text().strip() or "按默认假设执行"))
                self.input_layout.addWidget(btn)
            return

        if self.view == "group":
            if self.running:
                self.input_layout.addWidget(QLabel("流水线正在工作，可点击左侧「⏹ 停止」…"))
                return
            if self.dm_running:
                self.input_layout.addWidget(QLabel("有角色正在单独回复，请稍候…"))
                return
            self.req_edit = QLineEdit()
            self.req_edit.setPlaceholderText("向产品经理提出需求…")
            self.req_edit.returnPressed.connect(self._submit_requirement)
            self.input_layout.addWidget(self.req_edit, 1)
            btn = QPushButton("启动团队")
            btn.clicked.connect(self._submit_requirement)
            self.input_layout.addWidget(btn)
        else:
            role = self.view
            name = AGENT_INFO[role][0]
            if role in self.dm_running:
                self.input_layout.addWidget(QLabel(f"{name} 正在回复…"))
                return
            self.dm_edit = QLineEdit()
            self.dm_edit.setPlaceholderText(f"给 {name} 单独下达指令…")
            self.dm_edit.returnPressed.connect(self._submit_dm)
            self.input_layout.addWidget(self.dm_edit, 1)
            btn = QPushButton("发送")
            btn.clicked.connect(self._submit_dm)
            self.input_layout.addWidget(btn)

    def _refresh_header(self):
        if self.view == "group":
            self.title.setText("💬 群聊")
        else:
            name, emoji = AGENT_INFO[self.view]
            self.title.setText(f"{emoji} {name}")
        if self.running:
            s = "🟢 流水线运行中"
        elif self.dm_running:
            s = "💬 角色私聊中"
        elif self.pending:
            s = "⏸ 等待你决定"
        else:
            s = "⚪ 空闲"
        self.status.setText(f"状态：{s}")
        self.stop_btn.setEnabled(self.running)
        u = self.usage or {}
        total = int(u.get("total_input", 0)) + int(u.get("total_output", 0))
        cost = float(u.get("total_cost", 0) or 0)
        budget = float(u.get("budget_cny", 0) or 0)
        text = f"本次：{total} tokens · ¥{cost:.4f}"
        if budget > 0:
            text += f" / 预算 ¥{budget:.2f}"
        self.cost_label.setText(text)

    def _refresh(self):
        self._refresh_header()
        self._refresh_chat()
        self._refresh_input()

    # ---------- 交互 ----------
    def _answer(self, value, shown):
        self.log.append({"kind": "text", "source": "user", "text": shown, "target": None})
        self.answer_q.put(value or "按默认假设执行")
        self.pending = None
        self._save_session()
        self._refresh()

    def _open_settings(self):
        if config_dialog(self, first_run=False):
            self.status.setText("设置已保存，下次启动生效")
            QTimer.singleShot(1500, self._refresh_header)

    def _stop_pipeline(self):
        if not self.running:
            return
        self.stop_event.set()
        try:
            self.answer_q.put("__STOP__")
        except Exception:
            pass
        self.status.setText("正在停止…（当前阶段结束后生效）")

    def _new_session(self):
        self.log = []
        self.pending = None
        self.running = False
        self.dm_running = set()
        self.usage = {}
        self.stop_event = threading.Event()
        self.event_q = queue.Queue()
        self.answer_q = queue.Queue()
        try:
            if self.session_path.exists():
                self.session_path.unlink()
        except Exception:
            pass
        self._refresh()

    def _submit_requirement(self):
        if self.view != "group" or self.running or self.dm_running:
            return
        edit = getattr(self, "req_edit", None)
        req = edit.text().strip() if edit else ""
        if not req:
            return
        self.log = []
        self.log.append({"kind": "text", "source": "user", "text": req, "target": None})
        self.pending = None
        self.running = True
        self.usage = {}
        self.stop_event = threading.Event()
        self.event_q = queue.Queue()
        self.answer_q = queue.Queue()
        mode = self.mode_combo.currentData() or "roundrobin"
        threading.Thread(target=self._pipeline_worker,
                         args=(req, mode, self.event_q, self.answer_q, self.stop_event),
                         daemon=True).start()
        self._save_session()
        self._refresh()

    def _submit_dm(self):
        role = self.view
        if role == "group" or role in self.dm_running:
            return
        edit = getattr(self, "dm_edit", None)
        text = edit.text().strip() if edit else ""
        if not text:
            return
        self.log.append({"kind": "text", "source": "user", "text": text, "target": role})
        self.dm_running.add(role)
        hist = self._history_text()
        threading.Thread(target=self._dm_worker,
                         args=(role, text, hist, self.event_q), daemon=True).start()
        self._save_session()
        self._refresh()

    def _history_text(self, limit=20, max_chars=400):
        lines = []
        for m in self.log[-limit:]:
            if m["kind"] != "text":
                continue
            role = SOURCE_TO_ROLE.get(m["source"], m["source"])
            name = "Leader" if role == "user" else AGENT_INFO.get(role, (role, ""))[0]
            t = m["text"]
            if len(t) > max_chars:
                t = t[:max_chars] + "…"
            lines.append(f"{name}: {t}")
        return "\n".join(lines)

    # ---------- 后台线程 ----------
    def _pipeline_worker(self, req, mode, event_q, answer_q, stop_event):
        def on_event(kind, source, text):
            event_q.put((kind, source, text))

        async def ask(prompt, kind):
            event_q.put(("ask", kind, prompt))
            return await asyncio.to_thread(answer_q.get)

        try:
            asyncio.run(run_pipeline(req, mode, on_event, ask, stop_event=stop_event))
        except Exception as e:
            event_q.put(("error", "", f"{type(e).__name__}: {e}"))
        finally:
            event_q.put(("done", "", ""))

    def _dm_worker(self, role, instruction, history, event_q):
        def on_event(kind, source, text):
            event_q.put((kind, source, text))

        try:
            asyncio.run(run_direct(role, instruction, history, on_event))
        except Exception as e:
            event_q.put(("error", "", f"{type(e).__name__}: {e}"))
        finally:
            event_q.put(("dm_done", role, ""))

    def _drain_events(self):
        changed = False
        while not self.event_q.empty():
            ev = self.event_q.get()
            kind = ev[0]
            if kind == "ask":
                self.pending = (ev[1], ev[2])
            elif kind == "done":
                self.running = False
                self.stop_event.clear()
                self._save_session()
            elif kind == "dm_done":
                self.dm_running.discard(ev[1])
                self._save_session()
            elif kind == "usage":
                try:
                    self.usage = json.loads(ev[2])
                except Exception:
                    pass
            else:
                self.log.append({"kind": ev[0], "source": ev[1],
                                 "text": ev[2], "target": None})
            changed = True
        if changed:
            self._refresh()

    def closeEvent(self, event):
        self._save_session()
        super().closeEvent(event)


def main():
    smoke = "--smoke-test" in sys.argv
    try:
        app = QApplication(sys.argv)
        app.setStyleSheet(QSS)
        if not config_dialog(first_run=True):
            return
        w = MainWindow()
        w.show()
        if smoke:
            QTimer.singleShot(1500, app.quit)
        sys.exit(app.exec())
    except Exception:
        import traceback
        try:
            (app_root() / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
