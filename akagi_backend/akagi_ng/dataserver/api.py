import asyncio
import json
import os
import queue
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from akagi_ng.core.context import AppContext, get_app_context
from akagi_ng.core.logging import LOG_FILE, configure_logging
from akagi_ng.core.paths import ensure_dir, get_models_dir, get_settings_dir
from akagi_ng.dataserver.logger import logger
from akagi_ng.mitm_client.http_capture import http_capture_store
from akagi_ng.mjai_bot.engine import clear_resource_cache
from akagi_ng.mjai_bot.flya_service import FlyATestServiceClient, FlyATestServiceError
from akagi_ng.mjai_bot.ot3_service import OT3ServiceClient, OT3ServiceError
from akagi_ng.schema.types import (
    DebuggerDetachedMessage,
    SystemShutdownEvent,
    WebSocketClosedMessage,
    WebSocketCreatedMessage,
    WebSocketFrameMessage,
)
from akagi_ng.settings import (
    get_default_settings_dict,
    get_settings_dict,
    local_settings,
    verify_settings,
)

# CORS 响应头配置
# 桌面端仅允许本机来源访问
CORS_HEADERS = {
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _atomic_write_text(path: Path, content: str) -> None:
    """Write a UTF-8 file without exposing a partially written replacement."""
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _parse_validated_liqi_protocol(data: str) -> tuple[dict | None, str | None]:
    try:
        json_obj = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Received invalid JSON for liqi.json")
        return None, "Invalid JSON in liqi data"

    from akagi_ng.bridge.majsoul.liqi import LiqiProto

    try:
        # Building the descriptor pool validates the schema beyond JSON syntax.
        LiqiProto(json_proto=json_obj)
    except Exception as e:
        logger.warning(f"Rejected invalid liqi protocol structure: {e}")
        return None, "Invalid liqi protocol structure"

    return json_obj, None


def _is_allowed_origin(origin: str | None) -> bool:
    """检查来源是否为本机 HTTP(S) Origin。"""
    if not origin:
        return True  # 允许无 Origin 的本地请求（如 EventSource）
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


@web.middleware
async def cors_middleware(request: web.Request, handler: Callable[[web.Request], web.StreamResponse]) -> web.Response:
    """为响应添加 CORS 头，仅允许本机来源。"""
    origin = request.headers.get("Origin")

    # 仅允许 localhost/127.0.0.1 或无 Origin 的本地请求
    if not _is_allowed_origin(origin):
        logger.warning(f"Blocked CORS request from unauthorized origin: {origin}")
        return web.Response(status=403, text="Forbidden: Invalid origin")

    # 设置允许来源（有 Origin 时回显，否则使用 *）
    allow_origin = origin if origin else "*"

    if request.method == "OPTIONS":
        headers = dict(CORS_HEADERS)
        headers["Access-Control-Allow-Origin"] = allow_origin
        return web.Response(status=204, headers=headers)

    response = await handler(request)
    response.headers.update({"Access-Control-Allow-Origin": allow_origin})
    return response


def _json_response(data: object, status: int = 200) -> web.Response:
    """构造 ensure_ascii=False 的 JSON 响应。"""
    return web.json_response(
        data,
        status=status,
        dumps=lambda obj: json.dumps(obj, ensure_ascii=False),
    )


async def _request_object(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValueError("Invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request payload must be a JSON object")
    return payload


def _ot3_client(payload: dict[str, Any], *, require_key: bool = False) -> OT3ServiceClient:
    server = payload.get("server", local_settings.ot.server)
    key = payload.get("api_key", local_settings.ot.api_key)
    proxy = payload.get("proxy", local_settings.ot.effective_proxy())
    if not isinstance(server, str):
        raise ValueError("server must be a string")
    if not isinstance(key, str):
        raise ValueError("api_key must be a string")
    if not isinstance(proxy, str):
        raise ValueError("proxy must be a string")
    if require_key and not key.strip():
        raise ValueError("API key is required")
    return OT3ServiceClient(server, key, proxy)


def _flya_client(payload: dict[str, Any]) -> FlyATestServiceClient:
    server = local_settings.ot.flya_server
    requested_server = payload.get("server")
    if requested_server is not None and not isinstance(requested_server, str):
        raise ValueError("server must be a string")
    if requested_server is not None and requested_server.strip().rstrip("/") != server.strip().rstrip("/"):
        raise ValueError("server must match the configured FlyA server")
    return FlyATestServiceClient(server, local_settings.ot.flya_api_key, local_settings.ot.effective_proxy())


async def _ot3_response(operation: Callable[[], object]) -> web.Response:
    try:
        data = await asyncio.to_thread(operation)
        return _json_response({"ok": True, "data": data})
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    except OT3ServiceError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=502)
    except Exception:
        logger.exception("Unexpected OT3 service API failure")
        return _json_response({"ok": False, "error": "Internal server error"}, status=500)


async def _flya_response(operation: Callable[[], object]) -> web.Response:
    try:
        data = await asyncio.to_thread(operation)
        return _json_response({"ok": True, "data": data})
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    except FlyATestServiceError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=502)
    except Exception:
        logger.exception("Unexpected FlyA service API failure")
        return _json_response({"ok": False, "error": "Internal server error"}, status=500)


def _effective_mitm_required(app: AppContext) -> bool:
    return bool(local_settings.mitm.enabled or (app.plugin_manager and app.plugin_manager.requires_mitm()))


async def _reconcile_mitm_client(*, previous_required: bool | None = None, force_reload: bool = True) -> str:
    """Reconcile the MITM runtime without disrupting an unchanged transport."""
    try:
        app = get_app_context()
        client = app.mitm_client
        if not client:
            return ""

        required = _effective_mitm_required(app)
        if not force_reload:
            if previous_required is None:
                raise ValueError("previous_required is required for a hot reconcile")
            if previous_required == required:
                return ""

        if force_reload or not required:
            stopped = await asyncio.to_thread(client.stop)
            if stopped is False:
                logger.error(f"MITM client did not stop cleanly: {getattr(client, 'last_error', None)}")
                return "MITM runtime reload failed; restart Akagi-NG to apply the proxy settings"

        if required:
            started = await asyncio.to_thread(client.start)
            if started is False:
                logger.error(f"MITM client did not become ready: {getattr(client, 'last_error', None)}")
                return "MITM runtime reload failed; restart Akagi-NG to apply the proxy settings"
        return ""
    except Exception:
        logger.exception("Failed to reconcile the MITM client")
        return "MITM runtime reload failed; restart Akagi-NG to apply the proxy settings"


def _plugin_snapshot() -> list[dict[str, Any]]:
    app = get_app_context()
    if app.plugin_manager is None:
        return []
    mitm_running = bool(app.mitm_client and app.mitm_client.running)
    return app.plugin_manager.list_plugins(mitm_running=mitm_running)


async def get_plugins_handler(_request: web.Request) -> web.Response:
    return _json_response({"ok": True, "data": _plugin_snapshot()})


async def set_plugin_enabled_handler(request: web.Request) -> web.Response:
    plugin_id = request.match_info["plugin_id"]
    try:
        payload = await _request_object(request)
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")

        app = get_app_context()
        if app.plugin_manager is None:
            return _json_response({"ok": False, "error": "Plugin manager is unavailable"}, status=503)
        previous_required = _effective_mitm_required(app)
        app.plugin_manager.set_enabled(plugin_id, enabled)
        effective_required = _effective_mitm_required(app)
        proxy_error = await _reconcile_mitm_client(previous_required=previous_required, force_reload=False)
        plugin = app.plugin_manager.get(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        mitm_running = bool(app.mitm_client and app.mitm_client.running)
        return _json_response(
            {
                "ok": True,
                "data": plugin.to_public_dict(mitm_running=mitm_running),
                "proxyError": proxy_error,
                "proxyChanged": previous_required != effective_required,
            }
        )
    except KeyError:
        return _json_response({"ok": False, "error": "Plugin not found"}, status=404)
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        logger.exception(f"Failed to update plugin state: {plugin_id}")
        return _json_response({"ok": False, "error": "Plugin state update failed"}, status=500)


async def ot3_health_handler(request: web.Request) -> web.Response:
    try:
        payload = await _request_object(request)
        client = _ot3_client(payload)
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _ot3_response(client.health)


async def ot3_key_status_handler(request: web.Request) -> web.Response:
    try:
        payload = await _request_object(request)
        client = _ot3_client(payload, require_key=True)
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _ot3_response(client.key_status)


async def ot3_models_handler(request: web.Request) -> web.Response:
    try:
        payload = await _request_object(request)
        client = _ot3_client(payload, require_key=True)
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _ot3_response(client.models)


async def flya_health_handler(request: web.Request) -> web.Response:
    try:
        client = _flya_client(await _request_object(request))
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _flya_response(client.health)


async def flya_quota_handler(request: web.Request) -> web.Response:
    try:
        client = _flya_client(await _request_object(request))
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _flya_response(client.quota)


async def flya_models_handler(request: web.Request) -> web.Response:
    try:
        client = _flya_client(await _request_object(request))
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _flya_response(client.models)


async def ot3_redeem_handler(request: web.Request) -> web.Response:
    try:
        payload = await _request_object(request)
        client = _ot3_client(payload)
        code = payload.get("code", "")
        email = payload.get("email", "")
        renew_key = payload.get("renew_key", "")
        if not all(isinstance(value, str) for value in (code, email, renew_key)):
            raise ValueError("code, email, and renew_key must be strings")
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _ot3_response(lambda: client.redeem(code, email=email, renew_key=renew_key))


async def ot3_create_order_handler(request: web.Request) -> web.Response:
    try:
        payload = await _request_object(request)
        client = _ot3_client(payload)
        product = payload.get("product", "")
        redeem = payload.get("redeem", True)
        if not isinstance(product, str) or not isinstance(redeem, bool):
            raise ValueError("product must be a string and redeem must be a boolean")
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _ot3_response(lambda: client.create_order(product, redeem=redeem))


async def ot3_order_result_handler(request: web.Request) -> web.Response:
    try:
        payload = await _request_object(request)
        client = _ot3_client(payload)
        order_id = payload.get("order_id", "")
        claim = payload.get("claim", "")
        if not isinstance(order_id, str) or not isinstance(claim, str):
            raise ValueError("order_id and claim must be strings")
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _ot3_response(lambda: client.order_result(order_id, claim))


async def ot3_create_subscription_handler(request: web.Request) -> web.Response:
    try:
        payload = await _request_object(request)
        client = _ot3_client(payload)
        product = payload.get("product", "")
        if not isinstance(product, str):
            raise ValueError("product must be a string")
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _ot3_response(lambda: client.create_subscription(product))


async def ot3_subscription_result_handler(request: web.Request) -> web.Response:
    try:
        payload = await _request_object(request)
        client = _ot3_client(payload)
        subscription_id = payload.get("subscription_id", "")
        claim = payload.get("claim", "")
        if not isinstance(subscription_id, str) or not isinstance(claim, str):
            raise ValueError("subscription_id and claim must be strings")
    except ValueError as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
    return await _ot3_response(lambda: client.subscription_result(subscription_id, claim))


async def get_settings_handler(_request: web.Request) -> web.Response:
    return _json_response({"ok": True, "data": get_settings_dict()})


async def get_http_captures_handler(request: web.Request) -> web.Response:
    try:
        limit = int(request.query.get("limit", "200"))
    except ValueError:
        limit = 200
    return _json_response({"ok": True, "data": http_capture_store.list(limit)})


async def clear_http_captures_handler(_request: web.Request) -> web.Response:
    http_capture_store.clear()
    return _json_response({"ok": True})


async def get_log_tail_handler(request: web.Request) -> web.Response:
    try:
        limit = max(1, min(int(request.query.get("limit", "500")), 2000))
    except ValueError:
        limit = 500
    if not LOG_FILE.is_file():
        lines: list[str] = []
    else:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    return _json_response({"ok": True, "data": lines})


async def save_settings_handler(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    match payload:
        case dict():
            pass
        case _:
            return _json_response({"ok": False, "error": "Settings payload must be a JSON object"}, status=400)

    if not verify_settings(payload):
        return _json_response({"ok": False, "error": "Settings validation failed (schema mismatch)"}, status=400)

    try:
        old_settings = get_settings_dict()
        old_mitm = old_settings.get("mitm", {})
        new_mitm = payload.get("mitm", {})
        game_proxy_changed = new_mitm != old_mitm
        proxy_changed = game_proxy_changed or payload.get("mihomo", {}) != old_settings.get("mihomo", {})
        desktop_changed = payload.get("desktop", {}) != old_settings.get("desktop", {})
        try:
            local_settings.update(payload)
        except OSError:
            logger.exception("System credential storage is unavailable")
            return _json_response(
                {"ok": False, "error": "System credential storage is unavailable"},
                status=503,
            )
        local_settings.save()

        restart_required = False

        if payload.get("log_level") != old_settings.get("log_level"):
            new_level = payload.get("log_level", "INFO")
            logger.info(f"Log level changed to {new_level}, updating...")
            configure_logging(new_level)

        if (
            payload.get("platform") != old_settings.get("platform")
            or payload.get("majsoul_server") != old_settings.get("majsoul_server")
            or payload.get("server") != old_settings.get("server")
            or payload.get("model_config", {}).get("model_4p") != old_settings.get("model_config", {}).get("model_4p")
            or payload.get("model_config", {}).get("model_3p") != old_settings.get("model_config", {}).get("model_3p")
            or any(new_mitm.get(field) != old_mitm.get(field) for field in ("enabled", "host", "port"))
        ):
            restart_required = True

        proxy_error = await _reconcile_mitm_client() if new_mitm != old_mitm else ""
        resource_settings_changed = payload.get("model_config", {}) != old_settings.get(
            "model_config", {}
        ) or payload.get("ot", {}) != old_settings.get("ot", {})
        if resource_settings_changed:
            clear_resource_cache()
            logger.info("Resource cache cleared due to engine settings update.")
        return _json_response(
            {
                "ok": True,
                "data": get_settings_dict(),
                "restartRequired": restart_required,
                "proxyChanged": proxy_changed,
                "gameProxyChanged": game_proxy_changed,
                "proxyError": proxy_error,
                "desktopChanged": desktop_changed,
            }
        )
    except Exception:
        logger.exception("Failed to save settings")
        return _json_response({"ok": False, "error": "Internal server error"}, status=500)


async def reset_settings_handler(_request: web.Request) -> web.Response:
    try:
        default_settings = get_default_settings_dict()
        local_settings.update(default_settings, clear_flya_api_key=True)
        local_settings.save()

        clear_resource_cache()
        logger.info("Resource cache cleared due to settings reset.")
        return _json_response({"ok": True, "data": default_settings, "restartRequired": True})
    except Exception:
        logger.exception("Failed to reset settings")
        return _json_response({"ok": False, "error": "Internal server error"}, status=500)


async def get_models_handler(_request: web.Request) -> web.Response:
    models_dir = get_models_dir()
    if not models_dir.exists():
        return _json_response({"ok": True, "data": []})

    models = [f.name for f in models_dir.glob("*.pth") if f.is_file()]
    return _json_response({"ok": True, "data": models})


async def update_protocol_handler(request: web.Request) -> web.Response:
    """接收前端下载的 liqi.json 并保存到本地。"""
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Protocol update JSON error: {e}")
        return _json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    data = payload.get("data")
    if not data:
        return _json_response({"ok": False, "error": "Missing 'data' field"}, status=400)

    json_obj, validation_error = _parse_validated_liqi_protocol(data)
    if validation_error is not None:
        return _json_response({"ok": False, "error": validation_error}, status=400)
    assert json_obj is not None

    try:
        settings_dir = ensure_dir(get_settings_dir())
        liqi_path = settings_dir / "liqi.json"
        serialized = json.dumps(json_obj, indent=2, ensure_ascii=False)
        _atomic_write_text(liqi_path, serialized)

        # 热重载：收集所有活跃的 MajsoulBridge 实例并重置 proto
        from akagi_ng.bridge.majsoul.bridge import MajsoulBridge
        from akagi_ng.bridge.majsoul.liqi import LiqiProto

        app = get_app_context()
        bridges: list[MajsoulBridge] = []

        if app.electron_client and app.electron_client.bridge:
            bridge = app.electron_client.bridge
            if isinstance(bridge, MajsoulBridge):
                bridges.append(bridge)

        if app.mitm_client and app.mitm_client.addon:
            bridges.extend(b for b in app.mitm_client.addon.bridges.values() if isinstance(b, MajsoulBridge))

        replacement_protos = [LiqiProto(json_proto=json_obj) for _ in bridges]
        for bridge, replacement_proto in zip(bridges, replacement_protos, strict=True):
            bridge.liqi_proto = replacement_proto

        if bridges:
            logger.info(f"Hot-reloaded liqi proto in {len(bridges)} active bridge(s).")

        logger.info(f"Successfully updated liqi.json at {liqi_path}")
        return _json_response({"ok": True})

    except OSError as e:
        logger.error(f"File system error updating liqi.json: {e}")
        return _json_response({"ok": False, "error": f"File system error: {e}"}, status=500)


async def ingest_mjai_handler(request: web.Request) -> web.Response:
    """接收 Electron 发送的 MJAI 消息"""
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Ingest JSON error: {e}")
        return _json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    msg = None
    try:
        match payload:
            case {"type": "websocket_created", "requestId": request_id, "url": url}:
                msg = WebSocketCreatedMessage(request_id=request_id, url=url)
            case {"type": "websocket_closed", "requestId": request_id}:
                msg = WebSocketClosedMessage(request_id=request_id)
            case {"type": "websocket", "requestId": request_id, "direction": direction, "data": data}:
                msg = WebSocketFrameMessage(
                    request_id=request_id,
                    direction=direction,
                    data=data,
                    opcode=payload.get("opcode"),
                )
            case {"type": "debugger_detached"}:
                msg = DebuggerDetachedMessage()
            case _:
                logger.warning(f"Invalid MJAI ingest payload: {payload}")
                return _json_response({"ok": False, "error": "Invalid MJAI payload structure"}, status=400)
    except (KeyError, TypeError) as e:
        logger.warning(f"Error parsing ingest payload: {e}")
        return _json_response({"ok": False, "error": f"Payload parsing error: {e}"}, status=400)

    try:
        app = get_app_context()
        if app.electron_client:
            app.electron_client.push_message(msg)
            return _json_response({"ok": True})

        logger.warning("ElectronClient is not active")
        return _json_response({"ok": False, "error": "ElectronClient not active"}, status=503)
    except Exception as e:
        logger.error(f"Ingest handler error: {e}")
        return _json_response({"ok": False, "error": "Internal server error"}, status=500)


async def shutdown_handler(_request: web.Request) -> web.Response:
    """触发后端关闭

    通过共享消息队列发送关闭信号，由主循环统一处理。
    """
    logger.info("Received shutdown request from api.")

    try:
        app = get_app_context()

        queued = False
        queue_error: str | None = None
        if hasattr(app, "shared_queue") and app.shared_queue:
            shutdown_message = SystemShutdownEvent()
            try:
                app.shared_queue.put(shutdown_message, block=False)
                queued = True
            except queue.Full:
                logger.warning("Message queue is full, shutdown request dropped")
                queue_error = "Message queue is full"
        else:
            queue_error = "Message queue not available"

        request_shutdown = getattr(app, "request_shutdown", None)
        direct_shutdown = callable(request_shutdown)
        if direct_shutdown:
            request_shutdown()

        if queued or direct_shutdown:
            logger.info(f"Shutdown initiated (queued={queued}, direct_callback={direct_shutdown}).")
            return _json_response({"ok": True, "message": "Shutdown initiated"})

        logger.warning(f"{queue_error}, shutdown failed")
        return _json_response({"ok": False, "error": queue_error}, status=503)

    except Exception as e:
        logger.error(f"Shutdown handler error: {e}")
        return _json_response({"ok": False, "error": "Internal server error"}, status=500)


def setup_routes(app: web.Application):
    app.router.add_get("/api/settings", get_settings_handler)
    app.router.add_post("/api/settings", save_settings_handler)
    app.router.add_post("/api/settings/reset", reset_settings_handler)
    app.router.add_get("/api/logs/tail", get_log_tail_handler)
    app.router.add_get("/api/http-captures", get_http_captures_handler)
    app.router.add_delete("/api/http-captures", clear_http_captures_handler)
    app.router.add_get("/api/models", get_models_handler)
    app.router.add_get("/api/plugins", get_plugins_handler)
    app.router.add_post("/api/plugins/{plugin_id}", set_plugin_enabled_handler)
    app.router.add_post("/api/ot3/health", ot3_health_handler)
    app.router.add_post("/api/ot3/key-status", ot3_key_status_handler)
    app.router.add_post("/api/ot3/models", ot3_models_handler)
    app.router.add_post("/api/flya/health", flya_health_handler)
    app.router.add_post("/api/flya/quota", flya_quota_handler)
    app.router.add_post("/api/flya/models", flya_models_handler)
    app.router.add_post("/api/ot3/redeem", ot3_redeem_handler)
    app.router.add_post("/api/ot3/purchase/order", ot3_create_order_handler)
    app.router.add_post("/api/ot3/purchase/order/result", ot3_order_result_handler)
    app.router.add_post("/api/ot3/purchase/subscription", ot3_create_subscription_handler)
    app.router.add_post("/api/ot3/purchase/subscription/result", ot3_subscription_result_handler)
    app.router.add_post("/api/ingest", ingest_mjai_handler)
    app.router.add_post("/api/protocol/update", update_protocol_handler)
    app.router.add_post("/api/shutdown", shutdown_handler)
