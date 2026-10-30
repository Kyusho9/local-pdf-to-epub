"""Defense in depth, not a substitute for OS firewall acceptance tests.

No core component is configured with remote URLs. Python network attempts fail
closed; native libraries are separately exercised under the OS network sandbox.
"""
import os
import socket


def enforce_offline():
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    if getattr(socket, "_local_epub_guard", False):
        return
    def denied(*args, **kwargs):
        raise RuntimeError("离线转换进程禁止网络连接。请在资源管理中主动准备资源。")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    socket._local_epub_guard = True
