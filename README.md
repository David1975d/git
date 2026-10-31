# 析言

评论理解与反馈处理助手。将一批商品评价整理为可查看原文、核对依据、人工修正和准备回复的清单。

当前为 **v0.2.0 本机测试版**。产品需求见 [PRD](PRD-析言.md)。需求中的规划能力不等于已经实现。

## 功能

- CSV 导入与预览，保留初评、追评和来源。
- 模型提取反馈，区分一般好评、具体体验和其他评价；体验细分为口味、冲调、包装、规格。
- 查看原文与分析依据，记录人工修正、核查说明及复核状态。
- 基于规则准备回复，人工编辑后保存；不自动发送到电商平台。
- 保存和切换批次，恢复误删批次，导出 CSV／JSON。

视频专用语境分析、商家资料检索、模型个性化回复和公网服务尚未完成。降低人工处理时间是待验证目标。

## 安装与启动

以下命令用于 Windows PowerShell。需要安装带有 Tkinter 的 Python 3.10 或更新版本。

```powershell
git clone https://github.com/David1975d/git.git xiyan
cd xiyan
python -m venv backend/.venv
& ./backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-lock.txt
& ./backend/.venv/Scripts/python.exe model-pilot/configure_model.py
& ./backend/.venv/Scripts/python.exe start.py
```

在配置窗口填写兼容接口的服务地址、模型、密钥和接口类型。配置保存在 `model-pilot/config.local.json`，不应提交到仓库。配置窗口本身不调用模型；实际分析会向配置的服务发送评论并可能产生费用。

浏览器访问 <http://127.0.0.1:8771>。以后可双击 `启动析言.cmd`。仅运行单个服务进程。

## 输入与使用

1. 使用 [CSV 模板](review-import-template.csv)准备数据，或导入 [虚构示例](review-import-example.csv)。
2. 预览字段并填写商品范围，确认后开始分析。
3. 查看分类和复核状态，对需要判断的记录补充事实或修正结果。
4. 准备并保存回复，导出整批结果。

CSV 中 `main_text` 必填，其余字段可选：`review_id`、`followup_text`、`source_ref`、`flavor`、`specification`、`platform_rating`。初评和追评放在同一行。无效输入应先修正再分析。

结果保存在本机 `backend/data/`。清除浏览器会话后，不能保证从原入口找到历史批次。本项目未提供账号系统或公网部署方案。

## 结构

```text
backend/          FastAPI 接口、任务执行、SQLite 存储、规则及测试
frontend/         同源工作台 HTML、CSS、JavaScript
model-pilot/      模型适配、提示词、配置模板及测试
PRD-析言.md       产品需求定稿
start.py          本机启动器
```

## 测试

测试使用虚构输入及模拟模型，不产生模型费用。

```powershell
& ./backend/.venv/Scripts/python.exe -m unittest discover -s backend -p 'test_*.py'
& ./backend/.venv/Scripts/python.exe -m unittest discover -s model-pilot -p 'test_*.py'
& ./backend/.venv/Scripts/python.exe -m unittest test_start
```

仓库不包含真实评论、私人截图、密钥、数据库、运行日志或内部研究记录。问题与建议可通过 [Issues](https://github.com/David1975d/git/issues)反馈。
