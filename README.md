# Description Agent Manager · AI 软件团队

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6.svg)]()

**本地优先的 6 角色 AI 软件开发团队：群聊、私聊、人审、真实落盘。**

一个 Codex 风格的桌面工作台：产品经理 → 架构师 → 后端 → 前端 → 评审 → QA 在同一个群聊里协作；
你可以点开任意角色单独下指令，也可以在关键节点审批。模型 BYOK（自带 Key），只付 Token。

---

## ✨ 功能

- **6 角色流水线**：产品经理 / 架构师 / 后端 / 前端 / 代码评审 / QA
- **群聊 + 角色私聊**：群聊看全部消息；点角色看其历史并单独下指令
- **人工审批**：PRD 问题由你回答，架构方案由你批准/驳回
- **真实落盘**：开发角色通过工具把代码写入 `workspace/`
- **安全兜底**：命令白名单、执行前 Git 快照、超时、异常兜底
- **测试即用即清**：QA 完成后自动清理测试脚本
- **原生桌面**：PySide6（Qt），不依赖浏览器
- **BYOK**：DeepSeek（默认 flash），可按角色切换模型与 Key；无平台订阅费
- **Markdown 渲染**：消息、PRD、架构与代码块按 Markdown 展示
- **项目记忆**：PRD/架构/交付物按项目保存，并按角色裁剪上下文

## 🏗️ 架构

```mermaid
flowchart TD
    U[Leader / 你] --> UI[PySide6 桌面工作台]
    UI --> P[Pipeline 编排]

    P --> PM[产品经理]
    PM -->|PRD| AR[架构师]
    AR -->|已批准架构| BE[后端开发]
    BE -->|后端交付| FE[前端开发]
    FE -->|前端交付| RV[代码评审]
    RV -->|评审通过| QA[QA 测试]
    QA -->|测试通过| D[交付]

    PM -.问题.-> U
    U -.答复.-> PM
    AR -.架构审批.-> U
    U -.批准/驳回.-> AR

    BE --> T[工具层：write_file / run_cmd]
    FE --> T
    QA --> T
    RV --> R[只读工具：list_files / read_file]
    T --> WS[(workspace + Git 快照)]
    R --> WS

    P -.调度.-> M[模型 API · BYOK]
```

**交付顺序**：产品经理 → 架构师（Leader 审批）→ 后端 → 前端 → 代码评审 → QA → 交付。

> 工具层是开发/测试的执行手段，不是流程终点。后端交付后必须经过前端、代码评审与 QA；评审和 QA 通过后才算交付。

## 🚀 快速开始（源码运行）

```bash
git clone https://github.com/LiuShiW/Description_agent_manager.git
cd Description_agent_manager
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app/desktop_app.py
```

首次启动会弹出配置向导，填入你的 DeepSeek API Key 即可。

也可以手动配置：

```bash
copy config\models.example.yaml config\models.yaml
# 编辑 config\models.yaml，填入 api_key
```

## ⚙️ 配置

`config/models.yaml`：

```yaml
default:
  provider: deepseek
  model: deepseek-flash          # 或 deepseek-v4-pro
  api_key: "sk-..."
  base_url: "https://api.deepseek.com/v1"
  temperature: 0.2


# 可选：按角色单独配置（未填字段回落到 default）
roles:
  # arch:
  #   model: deepseek-v4-pro
  # backend:
  #   model: deepseek-v4-pro
  #   api_key: sk-your-other-key
  #   base_url: https://api.deepseek.com/v1
```

- DeepSeek OpenAI 兼容端点：`https://api.deepseek.com/v1`
- 若以后接 Claude Code：Anthropic 兼容端点是 `https://api.deepseek.com/anthropic`（注意不是 `/v1`）

## 🔢 Token 用量

- **只统计 token，不计算金额**。各厂商价格、缓存命中、折扣、区域差异都会导致内置价格滞后或误算，因此不内置价格表。
- 每次模型调用记录 input/output token 与实际模型名（仅用于展示/审计，不做金额换算）。
- 用量按**项目**持久化：
  - 默认项目：应用根目录 `usage.json`
  - 新项目：`projects/<名称>/usage.json`
- 每次运行/对话作为一条 `run` 记录，项目累计为所有 run 之和；「用量详情」可查看本次运行、项目累计与最近运行。

## 🧠 项目记忆与上下文

- 每个项目在项目根保存 `memory.json`：原始需求、PRD、Leader 答复、架构、后端/前端交付、评审、QA。
- 每个角色只拿到自己需要的上下文：
  - 架构师：需求 + PRD + Leader 答复
  - 后端：PRD + Leader 答复 + 架构
  - 前端：PRD + 架构 + 后端交付
  - 代码评审：架构 + 后端/前端交付
  - QA：PRD + 架构 + 评审意见
- 单个字段超过 12000 字符会截断，避免上下文膨胀。
- `session.json` 是界面聊天记录；`memory.json` 是结构化项目记忆；侧边栏「🧠 项目记忆」可查看。

## 📦 打包 Windows 可执行程序

双击 `打包.bat`（或运行 `打包.bat`）。脚本会自动：

1. 检查 `AI软件团队.exe` 是否在运行
2. 备份 dist 里的 workspace、projects、session.json、usage.json
3. 清理 dist 并执行 PyInstaller
4. 恢复配置与用户数据

产物：`dist/AI软件团队/AI软件团队.exe`（onedir，整个 `AI软件团队` 文件夹一起分发）。

> 打包前请确认 `config/models.yaml` 不含真实 Key；分发时只带 `models.example.yaml` 更安全。

## 📁 项目结构

```
├── app/
│   ├── desktop_app.py      # 原生桌面工作台（PySide6，主入口）
│   └── streamlit_app.py    # 网页原型（保留）
├── src/
│   ├── pipeline.py         # 6 角色流水线
│   ├── agents.py           # 角色定义
│   ├── teams.py            # 群聊 / 单代理阶段
│   ├── models.py           # 模型客户端工厂
│   ├── paths.py            # 开发/打包路径兼容
│   └── tools/code_exec.py  # run_cmd / write_file / read_file
├── config/
│   ├── agents.yaml         # 6 角色提示词
│   └── models.example.yaml # 模型配置模板
├── workspace/              # 运行时产出（不入库）
├── desktop_app.spec        # PyInstaller 配置
├── 打包.bat               # 中文入口（调用 build_exe.bat）
├── build_exe.bat          # 实际打包脚本（ASCII）
└── scripts/build_exe.py   # 打包逻辑
```

## 🗺️ Roadmap

- [ ] 多项目管理与会话历史
- [x] Token 用量统计
- [ ] 停止 / 重试 / 断点续跑
- [ ] 多模型管理（DeepSeek / Kimi / OpenAI / Ollama）
- [ ] 沙箱化代码执行（Docker / WSL）
- [ ] 团队协作、云端同步、审计（Pro）
- [ ] 安装包、代码签名、自动更新

## 🔒 安全说明

当前代码执行是**本机 subprocess + 白名单**，不是沙箱。请只在可信环境运行；
不要把含真实 Key 的 `config/models.yaml` 提交到仓库。

## 🤝 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 📄 License

[Apache-2.0](LICENSE)。第三方组件许可见 [NOTICE](NOTICE)。
