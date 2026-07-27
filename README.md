# 📈 股票价格监控

监控 A股、港股、美股实时行情，每30分钟轮询，支持100+只股票，价格预警推送。

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
  - symbol: "AAPL"
    name: "Apple"
    market: "美股"
  - symbol: "00700"
    name: "腾讯控股"
    market: "港股"
```

### 3. 配置预警（可选）
编辑 `config/alerts.yaml`：
```yaml
rules:
  - symbol: "159995"
    field: "price"
    op: "above"
    value: 1.5
```

### 4. 启动
```bash
python -m src.main
```

### 5. 启用钉钉/飞书通知（可选）
```bash
export WEBHOOK_URL="https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
python -m src.main
```

## 配置说明

| 文件 | 说明 |
|------|------|
| `config/watchlist.yaml` | 股票池（代码、名称、市场） |
| `config/alerts.yaml` | 预警规则（价格/涨跌幅 上/下限） |

### market 取值
- `A股` — 沪深A股
- `港股` — 香港港股
- `美股` — 美国股票

### 预警规则字段
- `field`: `price`（价格）或 `change_pct`（涨跌幅%）
- `op`: `above`（高于）或 `below`（低于）
- `value`: 阈值

## 运行测试
```bash
python -m pytest tests/ -v
```

## 架构
```
watchlist.yaml ─┐
                ├─→ MonitorService ─→ DataSource(Sina/Tencent) ─→ Quote
alerts.yaml ────┘                  └─→ AlertEngine ─→ Notifier
```

## 数据源

行情通过腾讯财经（qt.gtimg.cn，主）+ 新浪财经（hq.sinajs.cn，备）双通道获取，
单次请求批量拉取整个股票池。两路均为免费、稳定、A/港/美三市场全覆盖的实时接口。

| 市场 | 主源(腾讯) | 备源(新浪) |
|------|-----------|-----------|
| A股 | sh/sz 代码 | sh/sz 代码 |
| 港股 | hk 代码 | hk 代码 |
| 美股 | usAAPL | gb_aapl |

> 说明：之前的东方财富(push2.eastmoney.com)接口在启用本地代理(Clash/V2Ray)
> 时会被拦截重置连接，导致 `RemoteDisconnected`。本实现用 `trust_env=False`
> 绕开系统代理，并改用不受影响的腾讯/新浪通道，保证稳定。
