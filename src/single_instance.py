"""单实例守护：用 QLocalServer 监听一个固定名字的本地 socket。
   - 启动时先尝试 connect，若成功说明已有实例 → 发送 "show" 请求其前台并自己退出
   - 失败则自己监听该 socket
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

_SERVER_NAME = "WorkingorFishing-singleton-v1"


def try_acquire() -> Optional["SingleInstanceServer"]:
    """返回 None 表示拿不到（已有实例并已通知其前台）；
    返回 server 实例表示当前进程拿到了单实例锁，应保留引用直到退出。"""
    # 先 ping 看是否已有实例
    sock = QLocalSocket()
    sock.connectToServer(_SERVER_NAME)
    if sock.waitForConnected(300):
        try:
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(200)
        finally:
            sock.disconnectFromServer()
        return None

    # 清掉残留的 server name（前一个进程崩溃没清理时）
    QLocalServer.removeServer(_SERVER_NAME)
    server = SingleInstanceServer()
    if not server.start():
        # 无法监听，但也不阻止程序起来——退化成"无单实例保护"
        logger.warning("failed to listen on %s; running without single-instance guard", _SERVER_NAME)
    return server


class SingleInstanceServer(QObject):
    new_instance_requested = Signal()  # 其他实例请求前台

    def __init__(self) -> None:
        super().__init__()
        self._server = QLocalServer()
        self._server.newConnection.connect(self._on_new_connection)

    def start(self) -> bool:
        return self._server.listen(_SERVER_NAME)

    def stop(self) -> None:
        try:
            self._server.close()
        finally:
            QLocalServer.removeServer(_SERVER_NAME)

    def _on_new_connection(self) -> None:
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        if sock.waitForReadyRead(200):
            _ = bytes(sock.readAll())
        sock.disconnectFromServer()
        self.new_instance_requested.emit()
