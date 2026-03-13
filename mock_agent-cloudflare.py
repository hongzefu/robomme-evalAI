import argparse
import os
import queue
import re
import socket
import subprocess
import threading
import time

import uvicorn

from mock_agent import app


TRY_CLOUDFLARE_URL_RE = re.compile(
    r"https://[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the local mock MiniGrid agent and expose it with Cloudflare Tunnel."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind the local server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port to listen on locally.",
    )
    parser.add_argument(
        "--strict-port",
        action="store_true",
        help="Fail instead of automatically selecting a free local port.",
    )
    parser.add_argument(
        "--cloudflared-bin",
        default=os.environ.get("CLOUDFLARED_BIN", "cloudflared"),
        help="Path to the cloudflared executable.",
    )
    parser.add_argument(
        "--hostname",
        help="Optional Cloudflare hostname for a named tunnel.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("CLOUDFLARED_TOKEN"),
        help="Optional Cloudflare named tunnel token.",
    )
    parser.add_argument(
        "--config",
        help="Optional path to a cloudflared config file.",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=20.0,
        help="Seconds to wait for cloudflared to publish the public URL.",
    )
    return parser.parse_args()


def _local_upstream_host(bind_host):
    if bind_host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return bind_host


def _bindable_addresses(bind_host, port):
    host = None if bind_host in {"", "0.0.0.0", "::"} else bind_host
    return socket.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
        flags=socket.AI_PASSIVE,
    )


def _try_bind(bind_host, port):
    last_error = None
    for family, socktype, proto, _, sockaddr in _bindable_addresses(bind_host, port):
        probe = socket.socket(family, socktype, proto)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(sockaddr)
            return probe.getsockname()[1]
        except OSError as exc:
            last_error = exc
        finally:
            probe.close()

    if last_error is None:
        last_error = OSError(f"Could not resolve bind address for host {bind_host!r}")
    raise last_error


def _resolve_local_port(bind_host, requested_port, strict_port):
    try:
        return _try_bind(bind_host, requested_port)
    except OSError as exc:
        if strict_port:
            raise RuntimeError(
                f"Local port {requested_port} on {bind_host} is unavailable: {exc}"
            ) from exc
        if requested_port == 0:
            raise RuntimeError(
                f"Failed to allocate a free local port on {bind_host}: {exc}"
            ) from exc

    fallback_port = _try_bind(bind_host, 0)
    print(
        f"Local port {requested_port} is in use on {bind_host}; using free port {fallback_port} instead.",
        flush=True,
    )
    return fallback_port


def _build_cloudflared_command(args, local_port):
    upstream = f"http://{_local_upstream_host(args.host)}:{local_port}"
    command = [args.cloudflared_bin, "tunnel", "--url", upstream, "--no-autoupdate"]

    if args.config:
        command.extend(["--config", args.config])
    if args.token:
        command.extend(["run", "--token", args.token])
    elif args.hostname:
        command.extend(["--hostname", args.hostname])

    return command


def _start_output_reader(process):
    lines = queue.Queue()

    def _reader():
        if process.stdout is None:
            return
        for line in process.stdout:
            lines.put(line.rstrip())

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    return lines, thread


def _wait_for_public_url(process, output_lines, startup_timeout):
    deadline = time.monotonic() + startup_timeout
    recent_lines = []

    while time.monotonic() < deadline:
        if process.poll() is not None:
            break

        try:
            line = output_lines.get(timeout=0.25)
        except queue.Empty:
            continue

        recent_lines.append(line)
        if len(recent_lines) > 20:
            recent_lines.pop(0)

        match = TRY_CLOUDFLARE_URL_RE.search(line)
        if match:
            return match.group(0).rstrip("/")

    if process.poll() is None:
        details = "\n".join(recent_lines).strip()
        if details:
            raise RuntimeError(
                "Timed out waiting for cloudflared to publish a public URL.\n"
                f"Recent cloudflared output:\n{details}"
            )
        raise RuntimeError("Timed out waiting for cloudflared to publish a public URL.")

    details = "\n".join(recent_lines).strip()
    if details:
        raise RuntimeError(f"cloudflared exited before the tunnel became ready:\n{details}")
    raise RuntimeError("cloudflared exited before the tunnel became ready.")


def _stop_cloudflared(process):
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _start_local_server(bind_host, port):
    config = uvicorn.Config(app, host=bind_host, port=port)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if server.started:
            return server, thread
        if not thread.is_alive():
            raise RuntimeError(f"Local server failed to start on {bind_host}:{port}")
        time.sleep(0.05)

    server.should_exit = True
    thread.join(timeout=2)
    raise RuntimeError(f"Timed out waiting for local server on {bind_host}:{port}")


def _stop_local_server(server, thread):
    server.should_exit = True
    thread.join(timeout=5)


def main():
    args = parse_args()
    local_port = _resolve_local_port(args.host, args.port, args.strict_port)
    cloudflared_command = _build_cloudflared_command(args, local_port)
    cloudflared_process = None
    server = None
    server_thread = None

    try:
        server, server_thread = _start_local_server(args.host, local_port)

        try:
            cloudflared_process = subprocess.Popen(
                cloudflared_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"cloudflared executable not found: {args.cloudflared_bin}"
            ) from exc

        output_lines, _ = _start_output_reader(cloudflared_process)
        public_url = _wait_for_public_url(
            cloudflared_process,
            output_lines,
            args.startup_timeout,
        )
        print(f"Public agent URL: {public_url}", flush=True)
        print(
            f'Submission manifest: {{"agent_url": "{public_url}"}}',
            flush=True,
        )
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    except KeyboardInterrupt:
        pass
    finally:
        if cloudflared_process is not None:
            _stop_cloudflared(cloudflared_process)
        if server is not None and server_thread is not None:
            _stop_local_server(server, server_thread)


if __name__ == "__main__":
    main()
