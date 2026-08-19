"""WebSocket client — connect, send, receive, test CSWSH."""

import json


def ws_connect(url, timeout=10):
    """Connect to a WebSocket, receive initial message, return status."""
    try:
        import websocket

        ws = websocket.create_connection(url, timeout=timeout)
        ws.settimeout(5)
        try:
            msg = ws.recv()
            ws.close()
            return f"Connected. First message ({len(msg)} bytes): {msg[:1000]}"
        except:
            ws.close()
            return "Connected but no initial message received."
    except ImportError:
        return "websocket-client not installed. Run: pip install websocket-client"
    except Exception as e:
        return f"Connection error: {e}"


def ws_send_receive(url, message, timeout=10):
    """Send a message and receive the response."""
    try:
        import websocket

        ws = websocket.create_connection(url, timeout=timeout)
        ws.send(str(message))
        try:
            resp = ws.recv()
            ws.close()
            return f"Sent: {message[:100]}\nReceived ({len(resp)} bytes): {resp[:1000]}"
        except:
            ws.close()
            return "Message sent but no response received."
    except ImportError:
        return "websocket-client not installed. Run: pip install websocket-client"
    except Exception as e:
        return f"Error: {e}"
