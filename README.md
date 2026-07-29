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
python -m src.cli import-yaml
```

如果希望用 YAML 覆盖数据库中的股票池和预警规则：

```bash
python -m src.cli import-yaml --replace
```

### 4. 运行时维护股票池和预警
股票池和纯预警规则保存在 `paper_trading.db_path` 指向的 SQLite 数据库中。程序运行中修改数据库后，下一轮轮询自动生效，无需重启。

```bash
python -m src.cli list-watchlist
python -m src.cli add-stock --symbol SOXL --name 半导体ETF --market 美股
python -m src.cli disable-stock --symbol SOXL
python -m src.cli enable-stock --symbol SOXL

python -m src.cli list-alerts
python -m src.cli add-alert --symbol SOXL --field change_pct --op below --value -10
python -m src.cli disable-alert --rule-id RULE_ID
python -m src.cli enable-alert --rule-id RULE_ID
python -m src.cli list-index-snapshots --from 2026-07-28 --to 2026-07-28
```

手动更新实时快照：

```bash
# 更新当前交易时段内的观察列表股票
python -m src.cli update-snapshots --target stock

# 只更新指定股票
python -m src.cli update-snapshots --target stock --symbol SOXL

# 更新当前交易时段内的大盘指数
python -m src.cli update-snapshots --target index

# 只更新指定市场或指定指数
python -m src.cli update-snapshots --target index --market 港股
python -m src.cli update-snapshots --target index --symbol HSI

# 明确忽略交易时段过滤，强制请求行情源
python -m src.cli update-snapshots --target index --ignore-hours
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

## 命令行操作手册

所有命令默认读取 `config/config.yaml`，并使用 `paper_trading.db_path` 指向的 SQLite 数据库。`watchlist.yaml` 和 `alerts.yaml` 只作为种子/导入文件；运行时股票池和纯预警规则以 SQLite 为准。

### 查看帮助
```bash
python -m src.cli --help
python -m src.cli <command> --help
python -m src.backtest --help
```

### 启动监控服务
```bash
python -m src.main
```

启动后程序按 `monitor.interval_minutes` 周期运行。美股、港股、A股只在各自交易时段内拉取；大盘指数跟随同一轮询周期更新。

### 导入 YAML 种子数据
```bash
# 只导入 YAML 中的数据，已有数据不会被清空
python -m src.cli import-yaml

# 清空数据库中的股票池和预警规则后，再用 YAML 覆盖导入
python -m src.cli import-yaml --replace
```

### 股票池管理
```bash
# 查看启用中的股票池
python -m src.cli list-watchlist

# 查看全部股票池，包括已禁用项
python -m src.cli list-watchlist --all

# 添加启用股票
cc

# 添加时先禁用
python -m src.cli add-stock --symbol 00700 --name 腾讯控股 --market 港股 --disabled

# 禁用/启用股票
python -m src.cli disable-stock --symbol SOXL
python -m src.cli enable-stock --symbol SOXL

# 删除股票
python -m src.cli del-stock --symbol SOXL

```

### 策略管理
```bash
# 添加策略
python -m src.cli add-strategy --id soxl_drop_buy --symbol SOXL --action buy --trigger-field change_pct --trigger-op below --trigger-value -10 --amount 1000 --currency USD --lot-size 1 --cooldown-minutes 300 --max-position-amount 5000

# 删除策略
python -m src.cli del-strategy --id soxl_drop_buy


```

`--market` 取值为 `A股`、`港股`、`美股`。

### 纯通知预警管理
```bash
# 查看启用中的预警规则
python -m src.cli list-alerts

# 查看全部预警规则，包括已禁用项
python -m src.cli list-alerts --all

# 添加涨跌幅预警
python -m src.cli add-alert --symbol SOXL --field change_pct --op below --value -10

# 添加价格预警并设置冷却时间
python -m src.cli add-alert --symbol 00700 --field price --op above --value 400 --cooldown-seconds 600

# 添加时先禁用
python -m src.cli add-alert --symbol SOXL --field price --op above --value 100 --disabled

# 禁用/启用预警规则
python -m src.cli disable-alert --rule-id RULE_ID
python -m src.cli enable-alert --rule-id RULE_ID

# 删除规则
python -m src.cli del-alert --rule-id RULE_ID

```

`--field` 取值为 `price`、`change_pct`；`--op` 取值为 `above`、`below`。

### 手动更新实时快照
```bash
# 更新当前交易时段内的观察列表股票，写入 quote_snapshots
python -m src.cli update-snapshots --target stock

# 更新指定股票；多个 symbol 可用逗号分隔，也可重复传参
python -m src.cli update-snapshots --target stock --symbol SOXL
python -m src.cli update-snapshots --target stock --symbol SOXL,TQQQ
python -m src.cli update-snapshots --target stock --symbol SOXL --symbol TQQQ

# 按市场更新股票
python -m src.cli update-snapshots --target stock --market 美股

# 股票更新时包含已禁用股票
python -m src.cli update-snapshots --target stock --include-disabled

# 更新当前交易时段内的大盘指数，写入 index_snapshots
python -m src.cli update-snapshots --target index

# 按市场或指数代码更新指数
python -m src.cli update-snapshots --target index --market 港股
python -m src.cli update-snapshots --target index --symbol HSI

# 忽略交易时段过滤，强制请求行情源
python -m src.cli update-snapshots --target stock --ignore-hours
python -m src.cli update-snapshots --target index --ignore-hours
```

默认情况下，`update-snapshots` 和定时任务一样遵守交易时段；只有传入 `--ignore-hours` 才会在非交易时间强制拉取。`quote_snapshots` 和 `index_snapshots` 都按同一天同一代码做唯一约束，重复更新会覆盖当天最新数据，不会插入重复行。

### 大盘指数快照查询和补历史
```bash
# 只查询 SQLite 已保存的指数快照
python -m src.cli list-index-snapshots --from 2026-07-01 --to 2026-07-28

# 先补齐指定区间历史指数快照，再查询
python -m src.cli list-index-snapshots --from 2026-07-01 --to 2026-07-28 --backfill

# 单独补历史指数快照
python -m src.cli backfill-index-snapshots --from 2026-07-01 --to 2026-07-28
```

`list-index-snapshots` 不会凭空生成历史数据；不带 `--backfill` 时它只读取数据库里已经存在的记录。

### 回测
```bash
# 使用默认 SQLite 数据库回测
python -m src.backtest --from 2026-07-01 --to 2026-07-28

# 指定 SQLite 数据库回测
python -m src.backtest --from 2026-07-01 --to 2026-07-28 --db data/trading.sqlite3
```

回测基于 `quote_snapshots` 中已保存的行情快照，不会额外请求外部历史行情 API。

### 运行测试
```bash
python -m pytest tests/ -v
python -m pytest tests/ -q
```

## 大盘指数每日快照

程序默认跟随观察列表股票的同一个 `monitor.interval_minutes` 轮询周期，在各市场交易时段内更新每个市场三大指数到 SQLite `index_snapshots` 表，没有单独的指数定时器。同一指数同一交易日使用唯一约束只保留一行，交易时段内每轮会更新这行，日终自然保留当天最新值。

默认指数：

| 市场 | 指数 |
|------|------|
| A股 | 上证指数、深证成指、创业板指 |
| 港股 | 恒生指数、恒生中国企业指数、恒生科技指数 |
| 美股 | 道琼斯工业平均指数、纳斯达克综合指数、标普500指数 |

历史区间不会凭空出现在库里；`list-index-snapshots` 只查询 SQLite 已保存的数据。需要补历史时先执行：

```bash
python -m src.cli backfill-index-snapshots --from 2026-07-01 --to 2026-07-28
python -m src.cli list-index-snapshots --from 2026-07-01 --to 2026-07-28
```

也可以查询时顺手补齐：

```bash
python -m src.cli list-index-snapshots --from 2026-07-01 --to 2026-07-28 --backfill
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
