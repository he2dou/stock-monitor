# 📈 股票价格监控

监控 A股、港股、美股实时行情，每5分钟轮询，支持100+只股票，价格预警推送。

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
                ├─→ MonitorService ─→ DataSource(akshare) ─→ Quote
alerts.yaml ────┘                  └─→ AlertEngine ─→ Notifier
```

## 数据源

| 市场 | 数据源 | 说明 |
|------|--------|------|
| A股 | akshare | 东方财富实时行情，免费 |
| 港股 | akshare | 东方财富港股实时行情 |
| 美股 | akshare + yfinance | akshare主，yfinance备用 |
