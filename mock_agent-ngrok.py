import argparse
import json
import os
import socket
import subprocess
import threading
import time
from urllib import error, request

import uvicorn

from mock_agent import app


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the local mock MiniGrid agent and expose it with ngrok."
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
        "--ngrok-bin",
        default=os.environ.get("NGROK_BIN", "ngrok"),
        help="Path to the ngrok executable.",
    )
    parser.add_argument(
        "--authtoken",
        default=os.environ.get("NGROK_AUTHTOKEN"),
        help="ngrok authtoken. Defaults to NGROK_AUTHTOKEN when set.",
    )
    parser.add_argument(
        "--url",
        help="Reserved ngrok URL/domain to request, for example demo.ngrok.app.",
    )
    parser.add_argument(
        "--name",
        help="ngrok endpoint name. Defaults to a unique name for this process.",
    )
    parser.add_argument(
        "--config",
        help="Optional path to an ngrok config file.",
    )
    parser.add_argument(
        "--ngrok-api-base",
        default="http://127.0.0.1:4040/api",
        help="Base URL for the local ngrok Agent API.",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for ngrok to publish the public URL.",
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
            raise RuntimeError(f"Failed to allocate a free local port on {bind_host}: {exc}") from exc

    fallback_port = _try_bind(bind_host, 0)
    print(
        f"Local port {requested_port} is in use on {bind_host}; using free port {fallback_port} instead.",
        flush=True,
    )
    return fallback_port


def _build_ngrok_command(args, ngrok_name, local_port):
    upstream = f"{_local_upstream_host(args.host)}:{local_port}"
    command = [args.ngrok_bin, "http", upstream, "--name", ngrok_name]

    if args.authtoken:
        command.extend(["--authtoken", args.authtoken])
    if args.url:
        command.extend(["--url", args.url])
    if args.config:
        command.extend(["--config", args.config])

    return command, upstream


def _read_json(url):
    with request.urlopen(url, timeout=2.0) as response:
        return json.load(response)


def _matches_upstream(candidate, upstream):
    normalized = str(candidate).rstrip("/")
    expected_values = {
        upstream,
        f"http://{upstream}",
        f"https://{upstream}",
    }
    return normalized in expected_values


def _ngrok_output(process):
    if process.stdout is None:
        return ""
    try:
        return process.communicate(timeout=1)[0].strip()
    except subprocess.TimeoutExpired:
        return ""


def _wait_for_public_url(args, process, ngrok_name, upstream):
    api_base = args.ngrok_api_base.rstrip("/")
    deadline = time.monotonic() + args.startup_timeout
    last_error = "ngrok Agent API did not return a public URL."

    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = _ngrok_output(process)
            details = f": {output}" if output else "."
            raise RuntimeError(f"ngrok exited before the tunnel became ready{details}")

        try:
            endpoints = _read_json(f"{api_base}/endpoints").get("endpoints", [])
            for endpoint in endpoints:
                public_url = endpoint.get("url")
                endpoint_name = endpoint.get("name")
                upstream_url = endpoint.get("upstream", {}).get("url")
                if endpoint_name == ngrok_name and public_url:
                    return public_url.rstrip("/")
                if public_url and _matches_upstream(upstream_url, upstream):
                    return public_url.rstrip("/")
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)

        try:
            tunnels = _read_json(f"{api_base}/tunnels").get("tunnels", [])
            for tunnel in tunnels:
                public_url = tunnel.get("public_url")
                tunnel_name = tunnel.get("name")
                addr = tunnel.get("config", {}).get("addr")
                if tunnel_name == ngrok_name and public_url:
                    return public_url.rstrip("/")
                if public_url and _matches_upstream(addr, upstream):
                    return public_url.rstrip("/")
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)

        time.sleep(0.25)

    raise RuntimeError(
        "Timed out waiting for ngrok to publish a public URL. "
        f"Last error from the Agent API: {last_error}"
    )


def _stop_ngrok(process):
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
    ngrok_name = args.name or f"minigrid-mock-agent-{local_port}-{os.getpid()}"
    ngrok_command, upstream = _build_ngrok_command(args, ngrok_name, local_port)
    ngrok_process = None
    server = None
    server_thread = None

    try:
        server, server_thread = _start_local_server(args.host, local_port)

        try:
            ngrok_process = subprocess.Popen(
                ngrok_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"ngrok executable not found: {args.ngrok_bin}"
            ) from exc

        public_url = _wait_for_public_url(args, ngrok_process, ngrok_name, upstream)
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
        if ngrok_process is not None:
            _stop_ngrok(ngrok_process)
        if server is not None and server_thread is not None:
            _stop_local_server(server, server_thread)


if __name__ == "__main__":
    main()
