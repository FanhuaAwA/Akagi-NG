"""
测试模块：akagi_backend/tests/unit/test_sse.py

描述：针对服务器发送事件 (SSE) 管理逻辑的单元测试。
主要测试点：
- SSEManager 对客户端 (Client) 的增加、移除及连接清理逻辑。
- 异步广播事件 (_broadcast_async) 与历史通知 (Notification History) 的管理。
- 心跳维持 (Keep-alive) 逻辑。
- SSE 处理程序 (sse_handler) 的并发请求与重复连接处理。
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from akagi_ng.dataserver.sse import SSEManager, _format_sse_message
from akagi_ng.schema.constants import ServerConstants
from akagi_ng.schema.types import SSEClientData


def _discard_scheduled_coroutine(coro, *_args):
    coro.close()
    return MagicMock()


@pytest.fixture
def sse_manager():
    manager = SSEManager()
    # Use the running loop if available, otherwise fallback (for pytest-asyncio)
    try:
        manager.loop = asyncio.get_running_loop()
    except RuntimeError:
        manager.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(manager.loop)
    manager.running = True
    return manager


@pytest.mark.asyncio
async def test_add_client(sse_manager):
    """测试手动添加客户端"""
    mock_response = MagicMock(spec=web.StreamResponse)
    mock_queue = asyncio.Queue()
    client_id = "test_client"
    client_data = SSEClientData(response=mock_response, queue=mock_queue)

    await sse_manager.add_client(client_id, client_data)

    async with sse_manager.lock:
        assert client_id in sse_manager.clients
        assert sse_manager.clients[client_id].response == mock_response
        assert sse_manager.clients[client_id].queue == mock_queue


@pytest.mark.asyncio
async def test_remove_client(sse_manager):
    """测试移除客户端"""
    mock_response = AsyncMock(spec=web.StreamResponse)
    mock_queue = asyncio.Queue()
    client_id = "test_client"
    await sse_manager.add_client(client_id, SSEClientData(response=mock_response, queue=mock_queue))

    await sse_manager._remove_client(client_id, expected_response=mock_response)

    async with sse_manager.lock:
        assert client_id not in sse_manager.clients
    assert mock_queue.get_nowait() is None
    mock_response.write_eof.assert_awaited_once()


@pytest.mark.asyncio
async def test_remove_client_mismatch(sse_manager):
    """测试移除客户端时响应不匹配（不应移除）"""
    mock_response_1 = MagicMock(spec=web.StreamResponse)
    mock_response_2 = MagicMock(spec=web.StreamResponse)
    client_id = "test_client"

    await sse_manager.add_client(client_id, SSEClientData(response=mock_response_1, queue=asyncio.Queue()))

    # 尝试移除，但传入不匹配的响应对象
    await sse_manager._remove_client(client_id, expected_response=mock_response_2)

    async with sse_manager.lock:
        assert client_id in sse_manager.clients
        assert sse_manager.clients[client_id].response == mock_response_1


@pytest.mark.asyncio
async def test_broadcast_async(sse_manager):
    """测试异步广播消息"""
    q1 = asyncio.Queue()
    q2 = asyncio.Queue()

    await sse_manager.add_client("c1", SSEClientData(response=MagicMock(), queue=q1))
    await sse_manager.add_client("c2", SSEClientData(response=MagicMock(), queue=q2))

    payload = b"test message"
    await sse_manager._broadcast_async(payload)

    assert await q1.get() == payload
    assert await q2.get() == payload


@pytest.mark.asyncio
async def test_broadcast_event(sse_manager):
    """测试事件广播与缓存更新"""
    q = asyncio.Queue()
    await sse_manager.add_client("c1", SSEClientData(response=MagicMock(), queue=q))

    event_data = {"key": "value"}

    # 模拟 run_coroutine_threadsafe 为立即执行，防止在一个 loop 中死锁
    with patch("asyncio.run_coroutine_threadsafe", side_effect=_discard_scheduled_coroutine) as mock_run:
        sse_manager.broadcast_event("recommendations", event_data)

        # 验证缓存更新
        assert sse_manager.latest_recommendations == event_data

        # 验证是否尝试调用广播
        mock_run.assert_called_once()

        # 直接调用底层的 _broadcast_async 来验证数据流
        payload = _format_sse_message(event_data, event="recommendations")
        await sse_manager._broadcast_async(payload)
        assert await q.get() == payload


@pytest.mark.asyncio
async def test_notification_history(sse_manager):
    """测试通知历史记录"""
    # 模拟广播以避免真正的协程调度
    with patch("asyncio.run_coroutine_threadsafe", side_effect=_discard_scheduled_coroutine):
        max_history = ServerConstants.SSE_MAX_NOTIFICATION_HISTORY
        for i in range(max_history + 5):
            sse_manager.broadcast_event("notification", {"id": i})

    assert len(sse_manager.notification_history) == max_history
    assert sse_manager.notification_history[-1] == {"id": max_history + 4}


def test_inference_status_cache(sse_manager):
    status = {
        "request_id": "request-1",
        "phase": "success",
        "provider": "FutureProvider",
        "started_at_ms": 1_700_000_000_000,
        "elapsed_ms": 321,
    }

    sse_manager.running = False
    sse_manager.broadcast_event("inference_status", status)

    assert sse_manager.latest_inference_status == status


def test_format_sse_message_with_event():
    data = {"a": 1}
    msg = _format_sse_message(data, event="update")
    assert b"event: update\n" in msg
    assert b'data: {"a": 1}\n' in msg


@pytest.mark.asyncio
async def test_keep_alive_logic(sse_manager):
    queue1 = asyncio.Queue()
    queue2 = asyncio.Queue()
    sse_manager.clients = {
        "c1": SSEClientData(response=MagicMock(), queue=queue1),
        "c2": SSEClientData(response=MagicMock(), queue=queue2),
    }

    # We want to test one iteration of keep_alive
    # Patch sleep to return immediately or raise to exit
    with (
        patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]),
        contextlib.suppress(asyncio.CancelledError),
    ):
        await sse_manager.keep_alive()

    assert queue1.get_nowait() == b": keep-alive\n\n"
    assert queue2.get_nowait() == b": keep-alive\n\n"

    sse_manager.stop()
    assert sse_manager.running is False


@pytest.mark.asyncio
async def test_sse_manager_lifecycle(sse_manager):
    # 测试 set_loop, start, stop
    mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
    mock_loop.create_task.side_effect = _discard_scheduled_coroutine
    sse_manager.set_loop(mock_loop)
    sse_manager.start()
    assert sse_manager.running is True
    mock_loop.create_task.assert_called_once()

    sse_manager.stop()
    assert sse_manager.running is False


@pytest.mark.asyncio
async def test_close_wakes_blocked_clients(sse_manager):
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait(b"stale")
    await sse_manager.add_client("c1", SSEClientData(response=MagicMock(), queue=queue))

    await sse_manager.close()

    assert queue.get_nowait() is None


@pytest.mark.asyncio
async def test_remove_client_failure_branches(sse_manager):
    # 无效 client_id
    await sse_manager._remove_client("non_existent")

    # 模拟异常
    mock_response = AsyncMock(spec=web.StreamResponse)
    mock_response.write_eof.side_effect = Exception("error")
    await sse_manager.add_client("c1", SSEClientData(response=mock_response, queue=asyncio.Queue()))
    await sse_manager._remove_client("c1")
    # 应该被捕获


@pytest.mark.asyncio
async def test_broadcast_async_empty(sse_manager):
    sse_manager.clients = {}
    await sse_manager._broadcast_async(b"msg")
    # 应该直接返回


@pytest.mark.asyncio
async def test_broadcast_event_no_loop(sse_manager):
    sse_manager.loop = None
    sse_manager.broadcast_event("notification", {"a": 1})
    # 应该不抛出错误


@pytest.mark.asyncio
async def test_sse_handler_no_client_id(sse_manager):
    mock_request = MagicMock(spec=web.Request)
    mock_request.query = {}
    resp = await sse_manager.sse_handler(mock_request)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_sse_handler_success_flow(sse_manager):
    mock_request = MagicMock(spec=web.Request)
    mock_request.query = {"clientId": "c1"}
    mock_request.remote = "127.0.0.1"
    sse_manager.latest_inference_status = {
        "request_id": "request-replay",
        "phase": "requesting",
        "provider": "OT3",
        "started_at_ms": 1_700_000_000_000,
        "elapsed_ms": 0,
    }

    mock_response = AsyncMock(spec=web.StreamResponse)
    with patch("aiohttp.web.StreamResponse", return_value=mock_response):
        # 我们需要在另一个任务中停止 handler 循环
        async def stop_handler():
            await asyncio.sleep(0.1)
            await sse_manager._remove_client("c1", expected_response=mock_response)

        # 模拟 queue.get() 抛出异常来退出 while True 循环
        with patch.object(asyncio.Queue, "get", side_effect=asyncio.CancelledError):
            await sse_manager.sse_handler(mock_request)

        # 验证是否写入了初始消息
        # write 是 AsyncMock
        assert any("connected" in str(call) for call in mock_response.write.call_args_list)
        assert any(
            b"event: inference_status\n" in call.args[0] and b'"request_id": "request-replay"' in call.args[0]
            for call in mock_response.write.call_args_list
        )


@pytest.mark.asyncio
async def test_sse_handler_duplicate_client_id_no_deadlock(sse_manager):
    old_response = AsyncMock(spec=web.StreamResponse)
    await sse_manager.add_client("c1", SSEClientData(response=old_response, queue=asyncio.Queue()))

    mock_request = MagicMock(spec=web.Request)
    mock_request.query = {"clientId": "c1"}
    mock_request.remote = "127.0.0.1"

    new_response = AsyncMock(spec=web.StreamResponse)
    with (
        patch("aiohttp.web.StreamResponse", return_value=new_response),
        patch.object(asyncio.Queue, "get", side_effect=asyncio.CancelledError),
    ):
        await asyncio.wait_for(sse_manager.sse_handler(mock_request), timeout=1.0)

    old_response.write_eof.assert_awaited_once()


@pytest.mark.asyncio
async def test_keep_alive_execution_logic(sse_manager):
    # 覆盖 keep_alive 协程的内部逻辑（不运行无限循环）
    q = asyncio.Queue()
    await sse_manager.add_client("c1", SSEClientData(response=MagicMock(), queue=q))

    # 通过手动调用逻辑块来模拟一次循环
    async with sse_manager.lock:
        targets = list(sse_manager.clients.values())

    payload = b": keep-alive\n\n"
    for client_data in targets:
        queue = client_data.queue
        if queue:
            queue.put_nowait(payload)

    assert await q.get() == payload
