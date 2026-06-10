"""
Agentic RAG Paper QA - 一键启动脚本

用法：
    python run.py                # 默认启动（端口 8000）
    python run.py --port 8080    # 指定端口
    python run.py --reload       # 开发模式（代码改动自动重启）
    python run.py --host 0.0.0.0 # 允许局域网访问
"""

import argparse
import sys
import os

# 把当前目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Agentic RAG Paper QA 启动脚本")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="绑定端口（默认 8000）")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数（默认 1）")
    parser.add_argument("--no-frontend", action="store_true", help="不挂载前端")

    args = parser.parse_args()

    print("=" * 60)
    print("  🚀 Agentic RAG Paper QA")
    print("=" * 60)
    print(f"  地址: http://{args.host}:{args.port}")
    print(f"  文档: http://{args.host}:{args.port}/docs")
    print(f"  前端: http://{args.host}:{args.port}")
    print(f"  模式: {'热重载' if args.reload else '生产'}")
    print("=" * 60)

    if args.no_frontend:
        # 不挂载前端的话，跳过 StaticFiles mount
        os.environ["NO_FRONTEND"] = "1"

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,  # reload 模式不支持多 workers
        log_level="info",
    )


if __name__ == "__main__":
    main()
