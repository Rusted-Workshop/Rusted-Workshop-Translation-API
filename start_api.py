"""
启动 FastAPI 服务器
"""

import uvicorn

if __name__ == "__main__":
    print("正在启动 Translation API 服务器...")
    print("API 将在以下地址可用:")
    print("  - http://localhost:8001")
    print("  - http://127.0.0.1:8001")
    print("  - API 文档: http://localhost:8001/docs")
    print("  - ReDoc: http://localhost:8001/redoc")
    print("\n按 Ctrl+C 停止服务器\n")

    uvicorn.run(
        "api.main:app",
        host="127.0.0.1",
        port=8001,
        reload=True,
        log_level="info",
    )
