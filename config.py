#!/usr/bin/env python3
"""全局配置"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DETAIL_DIR = OUTPUT_DIR / "detail"

# 数据源模板：用户选择的日期会替换 {date}
SOURCE_URL_TEMPLATE = "https://cp.titan007.com/buy/JingCai.aspx?typeID=101&oddstype=2&date={date}"

# HTTP 请求配置
REQUEST_TIMEOUT = 60
MAX_RESOURCE_BYTES = 5 * 1024 * 1024  # 单个资源最大 5MB
MAX_RETRIES = 3
CONCURRENCY = 3

# 复刻质量阈值
DIFF_THRESHOLD_IGNORE = 0.02   # 2% 以下忽略
DIFF_THRESHOLD_RETRY = 0.10    # 10% 以下自动重试，最多 MAX_RETRIES 次

# 单场详情页 URL 模板（从列表页提取 scheduleID 后构造）
# 对应用户定义的 L2/L3 页面：亚、欧、析、大
DETAIL_URL_TEMPLATES = {
    "analysis": "https://zq.titan007.com/analysis/{id}cn.htm",
    "asian": "https://vip.titan007.com/AsianOdds_n.aspx?id={id}",
    "europe": "https://op1.titan007.com/oddslist/{id}.htm",
    "over": "https://vip.titan007.com/OverDown_n.aspx?id={id}",
}

# 允许被复刻的域名（用于判断页面/资源是否继续抓取）
ALLOWED_HOSTS = {
    "cp.titan007.com",
    "zq.titan007.com",
    "vip.titan007.com",
    "op1.titan007.com",
    "data.titan007.com",
    "info.titan007.com",
}

# 水印配置
WATERMARK_TEXT = "复刻页面"

# 输出文件命名
LIST_PAGE_NAME = "JingCai_{date}.html"
META_FILE = "meta.json"
REPORT_FILE = "report.json"
