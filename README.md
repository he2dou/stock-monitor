# 股票价格监控 / 模拟交易

监控 A股、港股、美股实时行情，每30分钟轮询，支持价格预警、飞书/钉钉/企业微信通知、纸面模拟交易、大盘指数每日快照、SQLite 历史留存和基于历史行情快照的回测。运行时股票池和纯预警规则存放在 SQLite，程序每轮从数据库读取，修改后无需重启即可在下一轮生效。

## 快速开始

### 1. 安装依赖
```bash
cd stock-monitor
pip install -r requirements.txt
```

### 2. 应用配置和通知
编辑 `config/config.yaml`：
```yaml
monitor:
  interval_minutes: 30

webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_TOKEN"
webhook_timeout: 10

market_indices:
  enabled: true

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

### 3. 初始化股票池和纯预警
`config/watchlist.yaml` 和 `config/alerts.yaml` 现在作为 SQLite 的首次种子/导入文件使用。程序启动时如果数据库对应表为空，会自动导入这两个文件；也可以手动导入：

```bash
python -m src.config_cli import-yaml
```

如果希望用 YAML 覆盖数据库中的股票池和预警规则：

```bash
python -m src.config_cli import-yaml --replace
```

### 4. 运行时维护股票池和预警
股票池和纯预警规则保存在 `paper_trading.db_path` 指向的 SQLite 数据库中。程序运行中修改数据库后，下一轮轮询自动生效，无需重启。

```bash
python -m src.config_cli list-watchlist
python -m src.config_cli add-stock --symbol SOXL --name 半导体ETF --market 美股
python -m src.config_cli disable-stock --symbol SOXL
python -m src.config_cli enable-stock --symbol SOXL

python -m src.config_cli list-alerts
python -m src.config_cli add-alert --symbol SOXL --field change_pct --op below --value -10
python -m src.config_cli disable-alert --rule-id RULE_ID
python -m src.config_cli enable-alert --rule-id RULE_ID
python -m src.config_cli list-index-snapshots --from 2026-07-28 --to 2026-07-28
```

### 5. 配置模拟交易策略（可选）
编辑 `config/strategies.yaml`。策略文件仍然会在每轮执行前动态重载：
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

### 6. 启动
```bash
python -m src.main
```

## 大盘指数每日快照

程序默认在各市场交易时段内，为每个市场三大指数每天保存一条快照到 SQLite `index_snapshots` 表。同一指数同一交易日使用唯一约束只保留一行，交易时段内每轮会更新这行，日终自然保留当天最新值。

默认指数：

| 市场 | 指数 |
|------|------|
| A股 | 上证指数、深证成指、创业板指 |
| 港股 | 恒生指数、恒生中国企业指数、恒生科技指数 |
| 美股 | 道琼斯工业平均指数、纳斯达克综合指数、标普500指数 |

历史区间不会凭空出现在库里；`list-index-snapshots` 只查询 SQLite 已保存的数据。需要补历史时先执行：

```bash
python -m src.config_cli backfill-index-snapshots --from 2026-07-01 --to 2026-07-28
python -m src.config_cli list-index-snapshots --from 2026-07-01 --to 2026-07-28
```

也可以查询时顺手补齐：

```bash
python -m src.config_cli list-index-snapshots --from 2026-07-01 --to 2026-07-28 --backfill
```

可在 `config/config.yaml` 中关闭：

```yaml
market_indices:
  enabled: false
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

## 配置入口

| 入口 | 说明 |
|------|------|
| SQLite `watchlist_items` | 运行时股票池，下一轮轮询自动生效 |
| SQLite `alert_rules` | 运行时纯通知预警规则，下一轮轮询自动生效 |
| `config/watchlist.yaml` | 股票池种子/导入文件，不再作为运行时直接读取来源 |
| `config/alerts.yaml` | 纯预警种子/导入文件，不再作为运行时直接读取来源 |
| `config/strategies.yaml` | 模拟交易策略，运行中动态重载 |
| `config/config.yaml` | 应用配置（轮询周期、Webhook、指数快照、模拟账户、数据库路径） |

### market 取值
- `A股` - 沪深A股
- `港股` - 香港港股
- `美股` - 美国股票

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
