"""
全局异常处理

FastAPI 的异常处理机制：
1. HTTPException -> 返回 HTTP 错误响应
2. RequestValidationError -> 请求参数验证失败
3. 自定义业务异常 -> 统一格式返回
4. 未捕获异常 -> 500 内部错误，记录日志
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """统一错误响应格式"""
    code: str
    message: str
    detail: dict | None = None


class BusinessException(Exception):
    """
    业务异常基类
    
    所有业务逻辑错误都应该继承这个类
    避免直接用 HTTPException（那是 HTTP 层的概念）
    """
    
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: dict | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class NotFoundException(BusinessException):
    """资源不存在"""
    
    def __init__(self, resource: str, resource_id: str):
        super().__init__(
            code="NOT_FOUND",
            message=f"{resource} '{resource_id}' 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ValidationException(BusinessException):
    """数据验证失败"""
    
    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )


def setup_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器"""
    
    @app.exception_handler(BusinessException)
    async def business_exception_handler(request: Request, exc: BusinessException):
        """业务异常处理"""
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=exc.code,
                message=exc.message,
                detail=exc.detail,
            ).model_dump(),
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """请求参数验证失败"""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                code="VALIDATION_ERROR",
                message="请求参数验证失败",
                detail={"errors": exc.errors()},
            ).model_dump(),
        )
    
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """未捕获异常处理"""
        # TODO Phase 7: 接入 Langfuse 追踪异常
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("未捕获的异常", extra={"path": request.url.path})
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                code="INTERNAL_ERROR",
                message="服务器内部错误",
                detail={"request_id": "TODO"} if False else None,
            ).model_dump(),
        )
