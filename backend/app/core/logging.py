"""
日志配置

为什么不用 Python 默认的 logging？
- 结构化日志便于机器解析（JSON 格式）
- 支持 ELK / Loki 等日志聚合系统
- 统一的日志格式和字段规范
"""

import logging
import sys
from typing import Any

import orjson


class JSONFormatter(logging.Formatter):
    """JSON 结构化日志格式化器"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # 添加额外字段（如果有）
        if hasattr(record, "extra"):
            log_data.update(record.extra)
        
        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return orjson.dumps(log_data).decode()


def setup_logging() -> None:
    """
    配置应用日志
    
    输出到 stderr（容器化环境的最佳实践）
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = [handler]
    
    # 第三方库的日志级别控制
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
