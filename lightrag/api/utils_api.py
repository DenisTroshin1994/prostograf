"""
Utility functions for the LightRAG API.
"""

import os
import argparse
from typing import Optional, List, Tuple
import sys
import time
import logging
from ascii_colors import ASCIIColors
from .._version import __api_version__ as api_version
from .._version import __version__ as core_version
from lightrag.constants import (
    DEFAULT_FORCE_LLM_SUMMARY_ON_MERGE,
)
from lightrag.api.runtime_validation import validate_runtime_target_from_env_file
from fastapi import Depends, HTTPException, Security, Request, Response, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from starlette.status import HTTP_403_FORBIDDEN
from .auth import auth_handler
from .config import ollama_server_infos, global_args, get_env_value

logger = logging.getLogger("lightrag")

# ========== Token Renewal Rate Limiting ==========
# Cache to track last renewal time per user (username as key)
# Format: {username: last_renewal_timestamp}
_token_renewal_cache: dict[str, float] = {}
_RENEWAL_MIN_INTERVAL = 60  # Minimum 60 seconds between renewals for same user

# ========== Token Renewal Path Exclusions ==========
# Paths that should NOT trigger token auto-renewal
# - /health: Health check endpoint, no login required
# - /documents/paginated: Client polls this frequently (5-30s), renewal not needed
# - /documents/pipeline_status: Client polls this very frequently (2s), renewal not needed
_TOKEN_RENEWAL_SKIP_PATHS = [
    "/health",
    "/documents/paginated",
    "/documents/pipeline_status",
]


def check_env_file():
    """
    Check if .env file exists and handle user confirmation if needed.
    Returns True if should continue, False if should exit.
    """
    env_path = ".env"

    if not os.path.exists(env_path):
        warning_msg = "Warning: Startup directory must contain .env file for multi-instance support."
        ASCIIColors.yellow(warning_msg)

        # Check if running in interactive terminal
        if sys.stdin.isatty():
            response = input("Do you want to continue? (yes/NO): ")
            if response.lower() != "yes":
                ASCIIColors.red("Server startup cancelled")
                return False
        return True

    is_valid, error_message = validate_runtime_target_from_env_file(env_path)
    if not is_valid:
        for line in error_message.splitlines():
            ASCIIColors.red(line)
        return False

    return True


# Get whitelist paths from global_args, only once during initialization
whitelist_paths = global_args.whitelist_paths.split(",")

# Pre-compile path matching patterns
whitelist_patterns: List[Tuple[str, bool]] = []
for path in whitelist_paths:
    path = path.strip()
    if path:
        # If path ends with /*, match all paths with that prefix
        if path.endswith("/*"):
            prefix = path[:-2]
            whitelist_patterns.append((prefix, True))  # (prefix, is_prefix_match)
        else:
            whitelist_patterns.append((path, False))  # (exact_path, is_prefix_match)

# Global authentication configuration
auth_configured = bool(auth_handler.accounts)


def get_combined_auth_dependency(api_key: Optional[str] = None):
    """
    Create a combined authentication dependency that implements authentication logic
    based on API key, OAuth2 token, and whitelist paths.

    Args:
        api_key (Optional[str]): API key for validation

    Returns:
        Callable: A dependency function that implements the authentication logic
    """
    # Use global whitelist_patterns and auth_configured variables
    # whitelist_patterns and auth_configured are already initialized at module level

    # Only calculate api_key_configured as it depends on the function parameter
    api_key_configured = bool(api_key)

    # Create security dependencies with proper descriptions for Swagger UI
    oauth2_scheme = OAuth2PasswordBearer(
        tokenUrl="login", auto_error=False, description="OAuth2 Password Authentication"
    )

    # If API key is configured, create an API key header security
    api_key_header = None
    if api_key_configured:
        api_key_header = APIKeyHeader(
            name="X-API-Key", auto_error=False, description="API Key Authentication"
        )

    async def combined_dependency(
        request: Request,
        response: Response,  # Added: needed to return new token via response header
        token: str = Security(oauth2_scheme),
        api_key_header_value: Optional[str] = None
        if api_key_header is None
        else Security(api_key_header),
    ):
        # 1. Check if path is in whitelist
        path = request.url.path
        for pattern, is_prefix in whitelist_patterns:
            if (is_prefix and path.startswith(pattern)) or (
                not is_prefix and path == pattern
            ):
                return  # Whitelist path, allow access

        # 2. Validate token first if provided in the request (Ensure 401 error if token is invalid)
        if token:
            try:
                token_info = auth_handler.validate_token(token)

                # ========== Token Auto-Renewal Logic ==========
                from lightrag.api.config import global_args
                from datetime import datetime, timezone

                if global_args.token_auto_renew:
                    # Check if current path should skip token renewal
                    skip_renewal = any(
                        path == skip_path or path.startswith(skip_path + "/")
                        for skip_path in _TOKEN_RENEWAL_SKIP_PATHS
                    )

                    if skip_renewal:
                        logger.debug(f"Token auto-renewal skipped for path: {path}")
                    else:
                        try:
                            expire_time = token_info.get("exp")
                            if expire_time:
                                # Calculate remaining time ratio
                                now = datetime.now(timezone.utc)
                                remaining_seconds = (expire_time - now).total_seconds()

                                # Get original token expiration duration
                                role = token_info.get("role", "user")
                                total_hours = (
                                    auth_handler.guest_expire_hours
                                    if role == "guest"
                                    else auth_handler.expire_hours
                                )
                                total_seconds = total_hours * 3600

                                # Issue new token if remaining time < threshold
                                if (
                                    remaining_seconds
                                    < total_seconds * global_args.token_renew_threshold
                                ):
                                    # ========== Rate Limiting Check ==========
                                    username = token_info["username"]
                                    current_time = time.time()
                                    last_renewal = _token_renewal_cache.get(username, 0)
                                    time_since_last_renewal = (
                                        current_time - last_renewal
                                    )

                                    # Only renew if enough time has passed since last renewal
                                    if time_since_last_renewal >= _RENEWAL_MIN_INTERVAL:
                                        # Перевыпуск токена: роль/отдел берём из
                                        # users.db (источник истины), а не из старого
                                        # токена — иначе понижение в правах/смена отдела/
                                        # удаление учётки бесконечно продлевались бы со
                                        # стажалыми правами, обнуляя естественный срок.
                                        from lightrag.api.repos import users_db as _users_db

                                        fresh = _users_db.find_user(username)
                                        if fresh is None:
                                            # Пользователь удалён — не продлеваем токен.
                                            logger.info(
                                                f"Перевыпуск токена пропущен: пользователь "
                                                f"{username} больше не существует"
                                            )
                                        else:
                                            fresh_role = fresh.get("role", role)
                                            fresh_meta = {
                                                **(token_info.get("metadata") or {}),
                                                "department": fresh.get("department", ""),
                                            }
                                            new_token = auth_handler.create_token(
                                                username=username,
                                                role=fresh_role,
                                                metadata=fresh_meta,
                                            )
                                            # Return new token via response header
                                            response.headers["X-New-Token"] = new_token

                                            # Update renewal cache
                                            _token_renewal_cache[username] = current_time

                                            # Optional: log renewal
                                            logger.info(
                                                f"Token auto-renewed for user {username} "
                                                f"(role: {fresh_role}, remaining: {remaining_seconds:.0f}s)"
                                            )
                                    else:
                                        # Log skip due to rate limit
                                        logger.debug(
                                            f"Token renewal skipped for {username} "
                                            f"(rate limit: last renewal {time_since_last_renewal:.0f}s ago)"
                                        )
                                    # ========== End of Rate Limiting Check ==========
                        except Exception as e:
                            # Renewal failure should not affect normal request, just log
                            logger.warning(f"Token auto-renew failed: {e}")
                # ========== End of Token Auto-Renewal Logic ==========

                # Accept guest token if no auth is configured
                if not auth_configured and token_info.get("role") == "guest":
                    return
                # Accept non-guest token if auth is configured
                if auth_configured and token_info.get("role") != "guest":
                    return

                # Token validation failed, immediately return 401 error
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token. Please login again.",
                )
            except HTTPException as e:
                # If already a 401 error, re-raise it
                if e.status_code == status.HTTP_401_UNAUTHORIZED:
                    raise
                # For other exceptions, continue processing

        # 3. Acept all request if no API protection needed
        if not auth_configured and not api_key_configured:
            return

        # 4. Validate API key if provided and API-Key authentication is configured
        if (
            api_key_configured
            and api_key_header_value
            and api_key_header_value == api_key
        ):
            return  # API key validation successful

        ### Authentication failed ####

        # if password authentication is configured but not provided, ensure 401 error if auth_configured
        if auth_configured and not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No credentials provided. Please login.",
            )

        # if api key is provided but validation failed
        if api_key_header_value:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Invalid API Key",
            )

        # if api_key_configured but not provided
        if api_key_configured and not api_key_header_value:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="API Key required",
            )

        # Otherwise: refuse access and return 403 error
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="API Key required or login authentication required.",
        )

    return combined_dependency


# ── Идентификация текущего пользователя (роли/отдел) ──────────────
# Используется чат-, admin- и me-роутами. Токен несёт role и
# metadata.department (выставляются в /login по записи из users.db).

_user_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="login", auto_error=False, description="OAuth2 Password Authentication"
)


async def get_current_user(token: Optional[str] = Security(_user_oauth2_scheme)) -> dict:
    """Возвращает {login, role, department, display_name} текущего пользователя.

    Бросает 401, если токен отсутствует/невалиден ИЛИ учётная запись больше не
    существует. Источник истины по правам — users.db, а НЕ payload токена:
    иначе разжалование/перевод в другой отдел/удаление пользователя не вступали
    бы в силу до истечения токена (а автопродление продлевало бы старые права
    практически бессрочно). См. аудит безопасности."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход в систему"
        )
    info = auth_handler.validate_token(token)  # бросает 401 при невалидном/истёкшем
    login = info.get("username")
    from lightrag.api.repos.users_db import find_user

    db_user = find_user(login) if login else None
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Учётная запись не найдена или отозвана",
        )
    role = db_user.get("role", "user")
    department = db_user.get("department") or ("all" if role == "admin" else "")
    return {
        "login": login,
        "role": role,
        "department": department,
        "display_name": db_user.get("display_name") or login,
    }


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Требует роль admin (для админ-роутов и управления документами)."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN, detail="Требуются права администратора"
        )
    return user


async def get_optional_user(token: Optional[str] = Security(_user_oauth2_scheme)) -> Optional[dict]:
    """Возвращает пользователя по токену или None (например, при доступе по
    API-ключу без токена). Используется в /query для применения ACL отдела:
    при доступе по токену — ограничиваем по отделу, по API-ключу — полный
    доступ (ключ эквивалентен админу).

    Права берутся из users.db (источник истины), а не из payload токена.
    Важно: если токен валиден, но пользователь отозван/удалён — возвращаем
    запись с ПУСТЫМ отделом (нет доступа к документам), а НЕ None, чтобы
    отозванный пользователь случайно не получил полный доступ (None = доступ
    по API-ключу)."""
    if not token:
        return None
    try:
        info = auth_handler.validate_token(token)
    except HTTPException:
        return None
    login = info.get("username")
    from lightrag.api.repos.users_db import find_user

    db_user = find_user(login) if login else None
    if not db_user:
        return {"login": login, "role": "user", "department": "", "display_name": login}
    role = db_user.get("role", "user")
    department = db_user.get("department") or ("all" if role == "admin" else "")
    return {
        "login": login,
        "role": role,
        "department": department,
        "display_name": db_user.get("display_name") or login,
    }


def resolve_allowed_file_paths_for_user(user: Optional[dict]) -> Optional[set]:
    """Набор разрешённых пользователю путей документов (ACL) или None (полный
    доступ): admin, отдел 'all', доступ по API-ключу (user=None)."""
    if user is None:
        return None
    if user.get("role") == "admin" or user.get("department") == "all":
        return None
    dept = user.get("department") or ""
    if not dept:
        # Пользователь без отдела — нет доступа ни к одному документу.
        return set()
    from lightrag.api.repos.dept_access import get_dept_access

    return set(get_dept_access(dept))


def apply_doc_meta_to_param(param) -> None:
    """Проставляет в QueryParam METAINFO и список «целиком»-документов из
    doc_meta. Вызывается на сервере для /query и чата; в контексте генератора
    эти данные подмешиваются к найденным документам (см. operate.py)."""
    try:
        from lightrag.api.repos import doc_meta

        param.doc_metainfo = doc_meta.metainfo_map()
        param.full_doc_paths = doc_meta.full_paths()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Не удалось загрузить doc_meta: {e}")


def display_splash_screen(args: argparse.Namespace) -> None:
    """
    Display a colorful splash screen showing LightRAG server configuration

    Args:
        args: Parsed command line arguments
    """
    # Banner
    # Banner
    top_border = "╔══════════════════════════════════════════════════════════════╗"
    bottom_border = "╚══════════════════════════════════════════════════════════════╝"
    width = len(top_border) - 4  # width inside the borders

    line1_text = f"ПростоГраф Server v{core_version}/{api_version}"
    line2_text = "Fast, Lightweight RAG Server Implementation"

    line1 = f"║ {line1_text.center(width)} ║"
    line2 = f"║ {line2_text.center(width)} ║"

    banner = f"""
    {top_border}
    {line1}
    {line2}
    {bottom_border}
    """
    ASCIIColors.cyan(banner)

    # Server Configuration
    ASCIIColors.magenta("\n📡 Server Configuration:")
    ASCIIColors.white("    ├─ Host: ", end="")
    ASCIIColors.yellow(f"{args.host}")
    ASCIIColors.white("    ├─ Port: ", end="")
    ASCIIColors.yellow(f"{args.port}")
    ASCIIColors.white("    ├─ Workers: ", end="")
    ASCIIColors.yellow(f"{args.workers}")
    ASCIIColors.white("    ├─ Timeout: ", end="")
    ASCIIColors.yellow(f"{args.timeout}")
    ASCIIColors.white("    ├─ CORS Origins: ", end="")
    ASCIIColors.yellow(f"{args.cors_origins}")
    ASCIIColors.white("    ├─ SSL Enabled: ", end="")
    ASCIIColors.yellow(f"{args.ssl}")
    if args.ssl:
        ASCIIColors.white("    ├─ SSL Cert: ", end="")
        ASCIIColors.yellow(f"{args.ssl_certfile}")
        ASCIIColors.white("    ├─ SSL Key: ", end="")
        ASCIIColors.yellow(f"{args.ssl_keyfile}")
    ASCIIColors.white("    ├─ Ollama Emulating Model: ", end="")
    ASCIIColors.yellow(f"{ollama_server_infos.LIGHTRAG_MODEL}")
    ASCIIColors.white("    ├─ Log Level: ", end="")
    ASCIIColors.yellow(f"{args.log_level}")
    ASCIIColors.white("    ├─ Verbose Debug: ", end="")
    ASCIIColors.yellow(f"{args.verbose}")
    ASCIIColors.white("    ├─ API Key: ", end="")
    ASCIIColors.yellow("Set" if args.key else "Not Set")
    ASCIIColors.white("    └─ JWT Auth: ", end="")
    ASCIIColors.yellow("Enabled" if args.auth_accounts else "Disabled")

    # Directory Configuration
    ASCIIColors.magenta("\n📂 Directory Configuration:")
    ASCIIColors.white("    ├─ Working Directory: ", end="")
    ASCIIColors.yellow(f"{args.working_dir}")
    ASCIIColors.white("    └─ Input Directory: ", end="")
    ASCIIColors.yellow(f"{args.input_dir}")
    # Embedding Configuration
    ASCIIColors.magenta("\n📊 Embedding Configuration:")
    ASCIIColors.white("    ├─ Binding: ", end="")
    ASCIIColors.yellow(f"{args.embedding_binding}")
    ASCIIColors.white("    ├─ Host: ", end="")
    ASCIIColors.yellow(f"{args.embedding_binding_host}")
    ASCIIColors.white("    ├─ Model: ", end="")
    ASCIIColors.yellow(f"{args.embedding_model}")
    ASCIIColors.white("    ├─ Dimensions: ", end="")
    ASCIIColors.yellow(f"{args.embedding_dim}")
    ASCIIColors.white("    └─ Asymmetric: ", end="")
    ASCIIColors.yellow(f"{args.embedding_asymmetric}")

    # RAG Configuration
    ASCIIColors.magenta("\n⚙️ RAG Configuration:")
    ASCIIColors.white("    ├─ Summary Language: ", end="")
    ASCIIColors.yellow(f"{args.summary_language}")
    ASCIIColors.white("    ├─ Max Parallel Insert: ", end="")
    ASCIIColors.yellow(f"{args.max_parallel_insert}")
    ASCIIColors.white("    ├─ Chunk Size: ", end="")
    ASCIIColors.yellow(f"{args.chunk_size}")
    ASCIIColors.white("    ├─ Chunk Overlap Size: ", end="")
    ASCIIColors.yellow(f"{args.chunk_overlap_size}")
    ASCIIColors.white("    ├─ Cosine Threshold: ", end="")
    ASCIIColors.yellow(f"{args.cosine_threshold}")
    ASCIIColors.white("    ├─ Top-K: ", end="")
    ASCIIColors.yellow(f"{args.top_k}")
    ASCIIColors.white("    └─ Force LLM Summary on Merge: ", end="")
    ASCIIColors.yellow(
        f"{get_env_value('FORCE_LLM_SUMMARY_ON_MERGE', DEFAULT_FORCE_LLM_SUMMARY_ON_MERGE, int)}"
    )

    # System Configuration
    ASCIIColors.magenta("\n💾 Storage Configuration:")
    ASCIIColors.white("    ├─ KV Storage: ", end="")
    ASCIIColors.yellow(f"{args.kv_storage}")
    ASCIIColors.white("    ├─ Vector Storage: ", end="")
    ASCIIColors.yellow(f"{args.vector_storage}")
    ASCIIColors.white("    ├─ Graph Storage: ", end="")
    ASCIIColors.yellow(f"{args.graph_storage}")
    ASCIIColors.white("    ├─ Document Status Storage: ", end="")
    ASCIIColors.yellow(f"{args.doc_status_storage}")
    ASCIIColors.white("    └─ Workspace: ", end="")
    ASCIIColors.yellow(f"{args.workspace if args.workspace else '-'}")

    # Server Status
    ASCIIColors.green("\n✨ Server starting up...\n")

    # Server Access Information
    protocol = "https" if args.ssl else "http"
    if args.host == "0.0.0.0":
        ASCIIColors.magenta("\n🌐 Server Access Information:")
        ASCIIColors.white("    ├─ WebUI (local): ", end="")
        ASCIIColors.yellow(f"{protocol}://localhost:{args.port}")
        ASCIIColors.white("    ├─ Remote Access: ", end="")
        ASCIIColors.yellow(f"{protocol}://<your-ip-address>:{args.port}")
        ASCIIColors.white("    ├─ API Documentation (local): ", end="")
        ASCIIColors.yellow(f"{protocol}://localhost:{args.port}/docs")
        ASCIIColors.white("    └─ Alternative Documentation (local): ", end="")
        ASCIIColors.yellow(f"{protocol}://localhost:{args.port}/redoc")

        ASCIIColors.magenta("\n📝 Note:")
        ASCIIColors.cyan("""    Since the server is running on 0.0.0.0:
    - Use 'localhost' or '127.0.0.1' for local access
    - Use your machine's IP address for remote access
    - To find your IP address:
      • Windows: Run 'ipconfig' in terminal
      • Linux/Mac: Run 'ifconfig' or 'ip addr' in terminal
    """)
    else:
        base_url = f"{protocol}://{args.host}:{args.port}"
        ASCIIColors.magenta("\n🌐 Server Access Information:")
        ASCIIColors.white("    ├─ WebUI (local): ", end="")
        ASCIIColors.yellow(f"{base_url}")
        ASCIIColors.white("    ├─ API Documentation: ", end="")
        ASCIIColors.yellow(f"{base_url}/docs")
        ASCIIColors.white("    └─ Alternative Documentation: ", end="")
        ASCIIColors.yellow(f"{base_url}/redoc")

    # Security Notice
    if args.key:
        ASCIIColors.yellow("\n⚠️  Security Notice:")
        ASCIIColors.white("""    API Key authentication is enabled.
    Make sure to include the X-API-Key header in all your requests.
    """)
    if args.auth_accounts:
        ASCIIColors.yellow("\n⚠️  Security Notice:")
        ASCIIColors.white("""    JWT authentication is enabled.
    Make sure to login before making the request, and include the 'Authorization' in the header.
    """)

    # Ensure splash output flush to system log
    sys.stdout.flush()
