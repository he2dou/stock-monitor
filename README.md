# 📈 股票价格监控 / 模拟交易

监控 A股、港股、美股实时行情，每30分钟轮询，支持价格预警、飞书/钉钉/企业微信通知、纸面模拟交易、SQLite 历史留存和基于历史行情快照的回测。`config/watchlist.yaml`、`config/alerts.yaml`、`config/strategies.yaml`、`config/config.yaml` 会在每轮执行前动态重载，无需重启程序。

## 快速开始

### 1. 安装依赖
```bash
cd stock-monitor
pip install -r requirements.txt
```

### 2. 配置股票池
编辑 `config/watchlist.yaml`：
```yaml
stocks:
  - symbol: "159995"
    name: "芯片ETF"
    market: "A股"
  - symbol: "SOXL"
    name: "半导体ETF"
    market: "美股"
  - symbol: "00700"
    name: "腾讯控股"
    market: "港股"
```

### 3. 配置纯预警（可选）
编辑 `config/alerts.yaml`：
```yaml
rules:
  - symbol: "159995"
    field: "price"
    op: "above"
    value: 1.5
```

### 4. 配置模拟交易策略（可选）
编辑 `config/strategies.yaml`：
```yaml
strategies:
  - id: "soxl_drop_buy"
    enabled: true
    symbol: "SOXL"
    action: "buy"
    trigger:
      field: "change_pct"
      op: "below"
      value: -10.0
    sizing:
      type: "fixed_amount"
      amount: 1000
      currency: "USD"
      lot_size: 1
    constraints:
      cooldown_minutes: 300
      max_position_amount: 5000
```

### 5. 应用配置和通知
编辑 `config/config.yaml`：
```yaml
monitor:
  interval_minutes: 30

webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_TOKEN"
webhook_timeout: 10

paper_trading:
  enabled: true
  db_path: "data/trading.sqlite3"
  quote_history_enabled: true
  accounts:
    CNY: 100000
    HKD: 100000
    USD: 50000
```
环境变量 `WEBHOOK_URL` 可临时覆盖配置文件里的 webhook 地址。

### 6. 启动
```bash
python -m src.main
```

## 模拟交易说明

- 只做纸面交易，不连接实盘券商。
- 策略触发后按当前行情价立即模拟成交。
- 买入检查现金，卖出检查持仓；失败会生成 `REJECTED` 订单并通知原因。
- A股、港股、美股分别使用 CNY、HKD、USD 独立模拟账户，不做汇率换算。
- 默认 lot size：A股=100，港股=100，美股=1，可在策略中覆盖。
- 下单数量：`floor(amount / price / lot_size) * lot_size`。
- 行情快照、策略信号、订单、成交、持仓和账户余额保存在 SQLite。

## 回测

回测基于已经保存到 SQLite 的 `quote_snapshots`，不额外拉外部历史数据：
```bash
python -m src.backtest --from 2026-07-01 --to 2026-07-28
```
输出 JSON 摘要，包括回放行情数、订单数、成交数、已实现盈亏和期末现金。

## 配置文件

| 文件 | 说明 |
|------|------|
| `config/watchlist.yaml` | 股票池（代码、名称、市场），运行中动态重载 |
| `config/alerts.yaml` | 纯通知预警规则，运行中动态重载 |
| `config/strategies.yaml` | 模拟交易策略，运行中动态重载 |
| `config/config.yaml` | 应用配置（轮询周期、Webhook、模拟账户、数据库路径） |

### market 取值
- `A股` — 沪深A股
- `港股` — 香港港股
- `美股` — 美国股票

### trigger 字段
- `field`: `price` 或 `change_pct`
- `op`: `above` 或 `below`
- `value`: 阈值

## 运行测试
```bash
python -m pytest tests/ -v
```

## 数据源

行情通过腾讯财经（qt.gtimg.cn，主）+ 新浪财经（hq.sinajs.cn，备）双通道获取，单次请求批量拉取股票池。两路均为免费、稳定、A/港/美三市场全覆盖的实时接口。

| 市场 | 主源(腾讯) | 备源(新浪) |
|------|-----------|-----------|
| A股 | sh/sz 代码 | sh/sz 代码 |
| 港股 | hk 代码 | hk 代码 |
| 美股 | usAAPL | gb_aapl |

> 说明：之前的东方财富(push2.eastmoney.com)接口在启用本地代理(Clash/V2Ray)时会被拦截重置连接，导致 `RemoteDisconnected`。本实现用 `trust_env=False` 绕开系统代理，并改用不受影响的腾讯/新浪通道。
