# A 股多智能体量化研究系统

面向 A 股的本地量化研究工具，组合多个分析 Agent 生成交易决策，支持行情与资讯采集、盘后计划、模拟交易、历史回测、参数优化和本地 Web 仪表盘。

项目以模拟交易为主，不会通过这些命令向券商发送实盘订单。回测和策略结果不代表未来表现，也不构成投资建议。

## 功能

- 趋势、资金、资讯、基本面、情绪、机构和风险分析
- A 股行情、板块、新闻及公开机构数据采集
- 纸面交易、历史回测、滚动验证和参数优化
- 生成盘后计划并检查风险与数据时效
- 本地 Web 仪表盘及 macOS 定时任务脚本
- 可选 DeepSeek 资讯总结；未配置时使用规则逻辑

## 环境准备

需要 Python 3.10 或更新版本。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 快速开始

内置样例不需要先下载行情数据：

```bash
python -m quant_agents.cli run-sample
python -m quant_agents.cli backtest-sample
python -m quant_agents.cli optimize-sample
```

从公开数据源采集行情并生成计划：

```bash
python -m quant_agents.cli fetch-free-snapshots --limit 100
python -m quant_agents.cli plan-free --limit 100
```

命令会将报告和运行状态写入本地 `runs/`、`data/` 目录。完整命令列表可通过以下方式查看：

```bash
python -m quant_agents.cli --help
```

## Web 仪表盘

仪表盘数据由本机的行情、计划和交易记录生成；这些运行数据不包含在 GitHub 仓库中。

```bash
python tools/build_dashboard_data.py --no-market-index
python tools/serve_dashboard.py --host 127.0.0.1 --port 8788
```

然后打开 <http://127.0.0.1:8788/>。如果本地尚无运行数据，仪表盘内容可能为空或不完整。

## 可选：DeepSeek

在项目根目录创建 `.env`，配置以下变量以启用 DeepSeek 资讯总结：

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-api-key
DEEPSEEK_MODEL=deepseek-v4-pro
```

`.env` 不会提交到 Git。不要把 API 密钥或其他凭据写入源码。

## 测试

```bash
python -m unittest discover -s tests
```

## 目录结构

```text
quant_agents/       策略、Agent、数据采集、模拟交易与回测
tools/              数据构建、仪表盘服务和自动化脚本
web_dashboard/      静态 Web 仪表盘
tests/              自动化测试
docs/               设计文档与开发计划
data/               本地行情、证据及交易状态（不提交）
runs/               本地运行报告（不提交）
```

本仓库忽略 `.env`、`data/`、`runs/` 以及仪表盘生成的数据和历史归档。克隆仓库后需自行采集或准备本地数据。
