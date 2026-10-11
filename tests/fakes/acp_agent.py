"""A scripted ACP agent for the tests. Behaviour depends on the prompt text:

  contains "permission"  → a tool_call, then asks session/request_permission, then says "allowed"
                           or "denied"
  contains "slow"        → keeps working until session/cancel, then stops with "cancelled"
  contains "crash"       → exits immediately
  anything else          → a tool_call titled "desktop_elenca_finestre", then "echo <n>: <text>"
                           in two chunks (n counts the prompts of the session)
The prompt blocks are recorded in $FAKE_ACP_LOG (one JSON line per prompt), if set: text blocks as
their text, image blocks as "<image MIME BYTES>". With $FAKE_ACP_IMAGES the agent declares that it
takes images in prompts.
"""

import base64
import json
import os
import sys
import threading

lock = threading.Lock()
pending = {}            # request id → threading.Event + result
cancelled = threading.Event()
sessions = {}
next_id = 1000


def send(msg):
    msg["jsonrpc"] = "2.0"
    with lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def update(session, upd):
    send({"method": "session/update", "params": {"sessionId": session, "update": upd}})


def chunk(session, text):
    update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})


def ask(method, params):
    global next_id
    next_id += 1
    rid, done = next_id, threading.Event()
    pending[rid] = {"event": done}
    send({"id": rid, "method": method, "params": params})
    done.wait()
    return pending.pop(rid)["result"]


def prompt(rid, params):
    session = params["sessionId"]
    blocks = [b["text"] if b["type"] == "text" else f"<image {b['mimeType']} {len(base64.b64decode(b['data']))}>"
              for b in params["prompt"]]
    if os.environ.get("FAKE_ACP_LOG"):
        with open(os.environ["FAKE_ACP_LOG"], "a") as f:
            f.write(json.dumps(blocks) + "\n")
    text = [b["text"] for b in params["prompt"] if b["type"] == "text"][-1]
    sessions[session] += 1

    if "crash" in text:
        os._exit(3)
    if "slow" in text:
        cancelled.clear()
        chunk(session, "working")
        cancelled.wait(30)
        send({"id": rid, "result": {"stopReason": "cancelled"}})
        return
    if "permission" in text:
        update(session, {"sessionUpdate": "tool_call", "toolCallId": "t0", "title": "desktop_elenca_finestre",
                         "kind": "other", "status": "pending", "rawInput": {}})
        res = ask("session/request_permission", {
            "sessionId": session,
            "toolCall": {"toolCallId": "t1", "title": "Run rm -rf /tmp/x", "kind": "execute"},
            "options": [{"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "always", "name": "Always", "kind": "allow_always"},
                        {"optionId": "no", "name": "Reject", "kind": "reject_once"}]})
        outcome = res.get("outcome", {})
        ok = outcome.get("outcome") == "selected" and outcome.get("optionId") in ("yes", "always")
        chunk(session, "allowed" if ok else "denied")
        send({"id": rid, "result": {"stopReason": "end_turn"}})
        return

    update(session, {"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "desktop_elenca_finestre",
                     "kind": "other", "status": "pending", "rawInput": {}})
    chunk(session, f"echo {sessions[session]}: ")
    chunk(session, text)
    send({"id": rid, "result": {"stopReason": "end_turn"}})


def main():
    for line in sys.stdin:
        msg = json.loads(line)
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None and rid in pending:          # answer to one of our requests
            pending[rid]["result"] = msg.get("result") or {}
            pending[rid]["event"].set()
        elif method == "initialize":
            caps = {"promptCapabilities": {"image": True}} if os.environ.get("FAKE_ACP_IMAGES") else {}
            send({"id": rid, "result": {"protocolVersion": 1, "agentCapabilities": caps, "authMethods": []}})
        elif method == "session/new":
            if os.environ.get("FAKE_ACP_AUTH"):
                send({"id": rid, "error": {"code": -32000, "message": "Authentication required"}})
                continue
            sid = f"s{len(sessions) + 1}"
            sessions[sid] = 0
            send({"id": rid, "result": {"sessionId": sid}})
        elif method == "session/prompt":
            threading.Thread(target=prompt, args=(rid, params), daemon=True).start()
        elif method == "session/cancel":
            cancelled.set()


if __name__ == "__main__":
    main()
