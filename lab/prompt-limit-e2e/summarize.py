"""Summarize a run log from run.sh: per turn, the requests (virtual size, status, compaction) and stdout.
Usage: python3 summarize.py <log.jsonl> [--json]"""
import json
import sys


def load(path):
    turns, cur = [], None
    for line in open(path):
        r = json.loads(line)
        if "path" in r:
            cur["requests"].append(r)
        elif "stdout" in r:
            cur["stdout"] = r["stdout"].strip()
        elif "exit" in r:
            cur["exit"] = r["exit"]
        else:
            cur = {"turn": r["turn"], "requests": []}
            turns.append(cur)
    return turns


def stats(turns):
    reqs = [q for t in turns for q in t["requests"] if q["path"] == "/v1/messages"]
    compacts = [q for q in reqs if q["compact"]]
    main = [q for q in reqs if not q["compact"]]
    first_compact_turn = next((t["turn"] for t in turns if any(q["compact"] for q in t["requests"])), None)
    pre = None
    if first_compact_turn:
        # largest successful main-loop prompt before the first compaction
        prior = [q["virtual"] for t in turns if t["turn"] < first_compact_turn
                 for q in t["requests"] if not q["compact"] and q["status"] == 200]
        pre = max(prior) if prior else None
    return {
        "turns": len(turns),
        "requests": len(reqs),
        "rejected": sum(q["status"] >= 400 for q in reqs),
        "compactions": len(compacts),
        "compaction_ok": sum(q["status"] == 200 for q in compacts),
        "first_compact_turn": first_compact_turn,
        "largest_ok_prompt_before_first_compact": pre,
        "max_ok_prompt": max((q["virtual"] for q in main if q["status"] == 200), default=None),
        "failed_turns": [t["turn"] for t in turns if t.get("exit") or any(q["status"] >= 400 for q in t["requests"]) and not t.get("stdout", "").startswith("ok")],
    }


if __name__ == "__main__":
    turns = load(sys.argv[1])
    if "--json" in sys.argv:
        print(json.dumps(stats(turns)))
        sys.exit()
    for t in turns:
        rq = " ".join(f"{q['virtual']//1000}k{'/C' if q['compact'] else ''}{'/' + str(q['status']) if q['status'] != 200 else ''}"
                      for q in t["requests"])
        print(f"t{t['turn']:>2} exit={t.get('exit', 0)} [{rq}] -> {t.get('stdout', '')[:90]!r}")
    print(json.dumps(stats(turns)))
