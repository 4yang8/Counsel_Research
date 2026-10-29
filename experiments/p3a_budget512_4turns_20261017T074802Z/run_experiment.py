"""P3a live experiment: cap only guide-agent reasoning; keep production code unchanged."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.request import Request, urlopen

OUT = Path(__file__).resolve().parent
LAB = OUT.parent.parent
sys.path.insert(0, str(LAB))
import app_server
import paired_lab
from counselors import P3a
from counselors.P3a import runtime as p3a_runtime

STATE = OUT / "experiment.json"
BUDGET = 512

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def show(kind, **data):
    print(kind, json.dumps(data, ensure_ascii=False), flush=True)

def remote(config, route, payload):
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(config.get("api_key_env", "VLLM_API_KEY"), "")
    if key:
        headers["Authorization"] = "Bearer " + key
    base = config["vllm_url"].rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    req = Request(base + route, data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
    return urlopen(req, timeout=240)

def tokens(config, model, text):
    with remote(config, "/tokenize", {"model": model, "prompt": text, "add_special_tokens": False}) as response:
        return json.load(response)["count"]

def child_records(log, config):
    requests = {}
    result = []
    for wrapper in log.get("subagent_events", []):
        event = wrapper["event"]
        identifier = wrapper["agent_id"]
        if event["type"] == "request":
            requests[identifier] = event["payload"]
        elif event["type"] == "done":
            request = requests[identifier]
            reasoning = event.get("reasoning", "")
            output = event.get("response", "")
            result.append({
                "agent_id": identifier, "request": request,
                "reasoning": reasoning, "response": output,
                "reasoning_tokens": tokens(config, request["model"], reasoning),
                "response_tokens": tokens(config, request["model"], output),
                "finish_reason": event.get("finish_reason"), "usage": event.get("usage"),
            })
    return result

def prepare():
    previous = read(LAB / "sessions/paired/261017_024604/session.json")
    cfg = read(LAB / "config.json")
    cfg.update(previous["connection"])
    cfg["generation"] = copy.deepcopy(previous["settings"]["generation"])
    write(OUT / "config.json", cfg)
    paths = [p3a_runtime.HERE / "system.md", p3a_runtime.HERE / "guide_agent.md",
             *p3a_runtime.GUIDE_FILES.values()]
    hashes = {}
    for path in paths:
        dest = OUT / "prompt_snapshot" / path.relative_to(p3a_runtime.HERE)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "objective": "Verify whether a 512-token guide-agent thinking budget is enforced and whether truncation harms advice or four-turn counseling continuity.",
        "version": "P3a", "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "guide subagent only; main uncapped; runtime wrapper in this experiment process only",
        "budget": BUDGET, "sampling": cfg["generation"],
        "model": cfg["model"], "endpoint": cfg["vllm_url"],
        "prompt_hashes": hashes,
        "child_seed_policy": "2026101700 + public step * 10 + consultation index; paired replay uses identical seed",
        "storage_dir": str(LAB / "sessions"), "turns": [],
    }
    write(STATE, manifest)
    return previous, cfg, manifest

def server_for(cfg, manifest):
    original = P3a.run
    guide_prompt = (OUT / "prompt_snapshot/guide_agent.md").read_text()
    def run_budgeted(config, session, backend, base_build, emit, cancelled, directory):
        count = 0
        def build(config, body, tools=None, preview=False):
            nonlocal count
            payload = base_build(config, body, tools, preview)
            if body.get("system_prompt") == guide_prompt:
                count += 1
                payload["thinking_token_budget"] = BUDGET
                payload["seed"] = 2026101700 + len(session["steps"]) * 10 + count
            return payload
        return original(config, session, backend, build, emit, cancelled, directory)
    P3a.run = run_budgeted
    for path, expected in manifest["prompt_hashes"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Prompt changed during experiment: " + path)
    server = app_server.make_server(OUT / "config.json", host="127.0.0.1", port=0,
                                   storage_dir=LAB / "sessions")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:" + str(server.server_port)
    def api(route, data=None):
        req = Request(base + route,
            data=None if data is None else json.dumps(data, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "X-Gemma-Request": "1"})
        return urlopen(req, timeout=240)
    return server, api

def create():
    previous, cfg, manifest = prepare()
    server, api = server_for(cfg, manifest)
    try:
        body = copy.deepcopy(previous["settings"])
        body.pop("session_id", None)
        body["counselor_system_prompt"] = p3a_runtime.specification()["system_prompt"]
        body["speaker_modes"] = {"client": "manual", "counselor": "model"}
        body["generation"] = copy.deepcopy(cfg["generation"])
        with api("/api/paired/create", body) as response:
            session = json.load(response)
        session["experiment_metadata"] = {"name": OUT.name, "manifest": str(STATE),
            "thinking_token_budget": BUDGET, "budget_scope": "guide subagent in experiment runner"}
        paired_lab.save_session(server, session)
        manifest["session_id"] = session["session_id"]
        manifest["session_path"] = str(LAB / "sessions/paired" / session["session_id"] / "session.json")
        write(STATE, manifest)
        show("CREATED", session_id=session["session_id"], folder=str(OUT),
             initial_dialogue=session["public_dialogue"], model=cfg["model"])
    finally:
        server.shutdown()
        server.server_close()

def send(text):
    manifest = read(STATE)
    cfg = read(OUT / "config.json")
    server, api = server_for(cfg, manifest)
    try:
        sid = manifest["session_id"]
        with api("/api/paired/session?session_id=" + sid) as response:
            session = json.load(response)
        with api("/api/paired/manual", {"session_id": sid, "expected_step": len(session["steps"]),
                                       "actor": "client", "content": text}) as response:
            session = json.load(response)["session"]
        turn = len(manifest["turns"]) + 1
        show("CLIENT", turn=turn, content=text)
        last = None
        started = time.monotonic()
        with (OUT / ("turn_" + str(turn) + "_events.ndjson")).open("w") as saved:
            with api("/api/paired/step", {"session_id": sid, "expected_step": len(session["steps"])}) as response:
                for line in response:
                    if not line.strip():
                        continue
                    saved.write(line.decode())
                    saved.flush()
                    wrapper = json.loads(line)
                    outer = wrapper.get("event", wrapper)
                    child = outer.get("type") == "agent_event"
                    event = outer.get("event", {}) if child else outer
                    if event.get("type") == "request":
                        payload = event["payload"]
                        show("REQUEST", role="subagent" if child else "main",
                             iteration=event.get("iteration"), budget=payload.get("thinking_token_budget"),
                             seed=payload.get("seed"))
                    elif event.get("type") == "tool_result":
                        show("TOOL_RESULT", name=event["name"], error=event.get("is_error"))
                    elif event.get("type") in ("done", "error", "cancelled"):
                        show("CHILD_END" if child else "MAIN_END", type=event["type"],
                             finish=event.get("finish_reason"), response=event.get("response"),
                             error=event.get("error"))
                        if not child:
                            last = event
        record = {"turn": turn, "client": text, "elapsed_seconds": round(time.monotonic()-started, 2),
                  "status": last.get("type") if last else "missing_final"}
        if last and last.get("step"):
            source = last["step"]["saved_path"]
            log = read(source)
            children = child_records(log, cfg)
            for child in children:
                assert child["request"]["thinking_token_budget"] == BUDGET
            assert all("thinking_token_budget" not in c["request"] for c in log["model_calls"])
            record.update(response=last.get("response"), log_path=source,
                          subagents=children,
                          tool_errors=sum(bool(e.get("is_error")) for e in log["tool_events"] if e["type"] == "tool_result"))
        manifest["turns"].append(record)
        write(STATE, manifest)
        show("TURN_SUMMARY", **{k:v for k,v in record.items() if k!="subagents"},
             subagents=[{k:c[k] for k in ["reasoning_tokens", "response_tokens", "finish_reason"]} for c in record.get("subagents", [])])
    finally:
        server.shutdown()
        server.server_close()

def replay(turn):
    manifest = read(STATE)
    cfg = read(OUT / "config.json")
    selected = next(r for r in manifest["turns"] if r["turn"] == turn)
    for index, child in enumerate(selected.get("subagents", []), 1):
        dest = OUT / ("control_turn_" + str(turn) + "_child_" + str(index) + ".json")
        if dest.exists():
            show("CONTROL_EXISTS", path=str(dest))
            continue
        payload = copy.deepcopy(child["request"])
        payload.pop("thinking_token_budget")
        # Keep streaming and all remaining sampling fields identical.
        reasoning = output = ""
        usage = None
        finish = None
        started = time.monotonic()
        with remote(cfg, "/v1/chat/completions", payload) as response:
            for raw in response:
                line = raw.decode().strip()
                if not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if value == "[DONE]":
                    break
                chunk = json.loads(value)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    reasoning += delta.get("reasoning") or delta.get("reasoning_content") or ""
                    output += delta.get("content") or ""
                    finish = choice.get("finish_reason") or finish
        record = {"request": payload, "reasoning": reasoning, "response": output,
                  "finish_reason": finish, "usage": usage,
                  "elapsed_seconds": round(time.monotonic()-started, 2),
                  "reasoning_tokens": tokens(cfg, payload["model"], reasoning),
                  "response_tokens": tokens(cfg, payload["model"], output)}
        write(dest, record)
        show("CONTROL", turn=turn, child=index, path=str(dest),
             **{k:v for k,v in record.items() if k not in ("request", "reasoning")})

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "send", "replay"])
    parser.add_argument("--text")
    parser.add_argument("--turn", type=int)
    args = parser.parse_args()
    if args.action == "create":
        create()
    elif args.action == "send":
        if not args.text:
            parser.error("--text required")
        send(args.text)
    else:
        replay(args.turn)

